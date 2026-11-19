"""APRS runtime hosted inside the desktop process.

This is the in-process replacement of ``app.core_main`` (the ``aprsbox-core``
service of the Linux build): the same services are created, wired and started
in the same order, but on a private asyncio loop running in a daemon thread.
The window never touches these objects directly except through the small,
thread-safe API below; everything else goes through SQLite, exactly like the
web GUI did.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from typing import Any, Awaitable, Callable

from app.db import init_db, log_event


class CoreRuntimeError(RuntimeError):
    pass


class CoreRuntime:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._services: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._started_at: float | None = None
        self.restart_count = 0

    # ------------------------------------------------------------------ state
    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive() and self._ready.is_set() and self._startup_error is None)

    @property
    def started_at(self) -> float | None:
        return self._started_at

    @property
    def startup_error(self) -> BaseException | None:
        return self._startup_error

    def service(self, name: str) -> Any:
        return self._services.get(name)

    # -------------------------------------------------------------- lifecycle
    def start(self, *, timeout: float = 30.0) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._startup_error = None
            self._thread = threading.Thread(target=self._thread_main, name="aprsbox-core", daemon=True)
            self._thread.start()
        if not self._ready.wait(timeout):
            raise CoreRuntimeError("APRS runtime did not start in time.")
        if self._startup_error is not None:
            raise CoreRuntimeError(f"APRS runtime failed to start: {self._startup_error}") from self._startup_error

    def stop(self, *, timeout: float = 20.0) -> None:
        with self._lock:
            loop = self._loop
            thread = self._thread
            if loop is None or thread is None:
                return
            if loop.is_running():
                future = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
                try:
                    future.result(timeout=timeout)
                except Exception as exc:  # pragma: no cover - defensive
                    log_event("WARNING", "system", f"APRS runtime shutdown error: {exc}")
                loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=timeout)
            self._thread = None
            self._loop = None
            self._services = {}
            self._started_at = None

    def restart(self) -> None:
        self.stop()
        self.restart_count += 1
        self.start()

    # ------------------------------------------------------------- execution
    def submit(self, coroutine_factory: Callable[[], Awaitable[Any]]) -> concurrent.futures.Future[Any]:
        loop = self._loop
        if loop is None or not loop.is_running():
            raise CoreRuntimeError("APRS runtime is not running.")
        return asyncio.run_coroutine_threadsafe(_call(coroutine_factory), loop)

    def call(self, coroutine_factory: Callable[[], Awaitable[Any]], *, timeout: float = 15.0) -> Any:
        return self.submit(coroutine_factory).result(timeout=timeout)

    # ------------------------------------------------------- core API mirror
    def restart_traffic_monitor(self) -> dict[str, Any]:
        """Equivalent of ``POST /api/traffic/restart`` of the core service."""
        traffic_monitor = self.service("traffic_monitor")
        if traffic_monitor is None:
            return {"ok": False, "error": "APRS runtime is not running."}
        try:
            self.call(traffic_monitor.restart, timeout=30.0)
        except Exception as exc:
            return {"ok": False, "error": str(exc) or exc.__class__.__name__}
        log_event("INFO", "traffic", "Traffic monitor runtime restarted by request")
        return {"ok": True}

    def traffic_runtime_snapshot(self) -> dict[str, Any]:
        """Equivalent of ``GET /api/traffic`` of the core service."""
        traffic_monitor = self.service("traffic_monitor")
        if traffic_monitor is None:
            return {}
        return self.call(lambda: _sync(traffic_monitor.snapshot), timeout=5.0)

    def digi_flow_latency_snapshot(self) -> dict[str, Any]:
        """Equivalent of ``GET /api/digi-flows/latency`` of the core service."""
        from app.services.notifications import radar_notification_dispatcher_snapshot

        digi_flow_runtime = self.service("digi_flow_runtime")
        aprsis_uplink = self.service("aprsis_uplink")
        if digi_flow_runtime is None or aprsis_uplink is None:
            return {}

        def collect() -> dict[str, Any]:
            snapshot = digi_flow_runtime.latency_snapshot()
            rx_side_effect_snapshot = aprsis_uplink.rx_side_effect_snapshot()
            radar_snapshot = radar_notification_dispatcher_snapshot()
            rx_side_effect_snapshot["radar_dispatcher"] = radar_snapshot
            rx_side_effect_snapshot["radar_breakdown_ms"] = radar_snapshot["radar_breakdown_ms"]
            snapshot["rx_side_effect_dispatcher"] = rx_side_effect_snapshot
            return snapshot

        return self.call(lambda: _sync(collect), timeout=5.0)

    def digi_flow_test_inject(self, *, source_ref: str, raw_payload: str, frame_uid: str | None = None) -> dict[str, Any]:
        """Equivalent of ``POST /api/digi-flows/test-inject`` of the core service."""
        digi_flow_runtime = self.service("digi_flow_runtime")
        if digi_flow_runtime is None:
            raise CoreRuntimeError("APRS runtime is not running.")
        source_ref = str(source_ref or "").strip()
        raw_payload = str(raw_payload or "").strip()
        if not source_ref:
            raise ValueError("source_ref is required.")
        if not raw_payload:
            raise ValueError("raw_payload is required.")
        return self.call(
            lambda: _sync(
                lambda: digi_flow_runtime.enqueue_tnc2_frame(
                    source_kind="receiver_rf",
                    source_ref=source_ref,
                    raw_payload=raw_payload,
                    frame_uid=frame_uid,
                )
            ),
            timeout=5.0,
        )

    # ------------------------------------------------------------ internals
    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._startup())
        except BaseException as exc:  # noqa: BLE001 - reported to the caller of start()
            self._startup_error = exc
            self._ready.set()
            try:
                loop.run_until_complete(self._shutdown())
            except BaseException:
                pass
            loop.close()
            return
        self._started_at = time.time()
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    async def _startup(self) -> None:
        # Mirrors app.core_main.lifespan (same order, same wiring).
        from app.services.alerts import expire_aprs_alerts
        from app.services.aprsis import AprsisClientService
        from app.services.cwop import CwopReceiverService
        from app.services.aprsis_tx_dispatcher import AprsIsTxDispatcher
        from app.services.beacon_scheduler import BeaconSchedulerService
        from app.services.bulletin_scheduler import BulletinSchedulerService
        from app.services.digi_flow_runtime import DigiFlowRuntimeService
        from app.services.maintenance_scheduler import MaintenanceSchedulerService
        from app.services.notifications import start_radar_notification_dispatcher
        from app.services.object_scheduler import ObjectSchedulerService
        from app.services.outbound_runtime import OutboundService
        from app.services.own_alert_scheduler import OwnAlertSchedulerService
        from app.services.radio_activity import RadioActivityAggregatorService
        from app.services.rf_tx_dispatcher import RfTxDispatcher
        from app.services.traffic import TrafficMonitorService
        from app.services.wx_scheduler import WxSchedulerService

        init_db()
        expire_aprs_alerts()
        aprsis_uplink = AprsisClientService()
        aprsis_tx_dispatcher = AprsIsTxDispatcher(client=aprsis_uplink)
        traffic_monitor = TrafficMonitorService()
        rf_tx_dispatcher = RfTxDispatcher(traffic_monitor=traffic_monitor)
        digi_flow_runtime = DigiFlowRuntimeService(
            aprsis_client=aprsis_uplink,
            aprsis_tx_dispatcher=aprsis_tx_dispatcher,
            rf_tx_dispatcher=rf_tx_dispatcher,
        )
        aprsis_uplink.set_frame_consumer(digi_flow_runtime.enqueue_aprsis_tnc2_frame)
        traffic_monitor.set_frame_consumer(digi_flow_runtime.enqueue_rx_tnc2_frame)
        outbound_service = OutboundService(traffic_monitor=traffic_monitor, digi_flow_runtime=digi_flow_runtime)
        services: dict[str, Any] = {
            "aprsis_uplink": aprsis_uplink,
            "cwop_receiver": CwopReceiverService(),  # receive only, switched on in Settings
            "aprsis_tx_dispatcher": aprsis_tx_dispatcher,
            "traffic_monitor": traffic_monitor,
            "rf_tx_dispatcher": rf_tx_dispatcher,
            "digi_flow_runtime": digi_flow_runtime,
            "outbound_service": outbound_service,
            "beacon_scheduler": BeaconSchedulerService(),
            "bulletin_scheduler": BulletinSchedulerService(),
            "maintenance_scheduler": MaintenanceSchedulerService(poll_interval=30.0),
            "object_scheduler": ObjectSchedulerService(),
            "own_alert_scheduler": OwnAlertSchedulerService(),
            "wx_scheduler": WxSchedulerService(),
            "radio_activity_aggregator": RadioActivityAggregatorService(),
        }
        self._services = services
        await start_radar_notification_dispatcher()
        self._services["_radar_started"] = True
        for name in (
            "aprsis_uplink",
            "cwop_receiver",
            "aprsis_tx_dispatcher",
            "rf_tx_dispatcher",
            "digi_flow_runtime",
            "traffic_monitor",
            "outbound_service",
            "beacon_scheduler",
            "bulletin_scheduler",
            "maintenance_scheduler",
            "object_scheduler",
            "own_alert_scheduler",
            "wx_scheduler",
            "radio_activity_aggregator",
        ):
            await services[name].start()
            services[f"_{name}_started"] = True
        log_event("INFO", "system", "APRSBox core started")

    async def _shutdown(self) -> None:
        from app.services.notifications import stop_radar_notification_dispatcher

        services = self._services

        async def stop(name: str) -> None:
            if not services.get(f"_{name}_started"):
                return
            try:
                await services[name].stop()
            except Exception as exc:
                log_event("WARNING", "system", f"Stopping {name} failed: {exc}")
            services[f"_{name}_started"] = False

        # Same order as app.core_main.lifespan.
        for name in (
            "radio_activity_aggregator",
            "wx_scheduler",
            "object_scheduler",
            "own_alert_scheduler",
            "maintenance_scheduler",
            "bulletin_scheduler",
            "beacon_scheduler",
            "outbound_service",
            "traffic_monitor",
        ):
            await stop(name)
        digi_flow_runtime = services.get("digi_flow_runtime")
        if digi_flow_runtime is not None and services.get("_digi_flow_runtime_started"):
            try:
                await asyncio.wait_for(digi_flow_runtime.wait_until_idle(), timeout=10.0)
            except Exception:
                pass
            await stop("digi_flow_runtime")
        await stop("aprsis_tx_dispatcher")
        await stop("cwop_receiver")
        await stop("aprsis_uplink")
        if services.get("_radar_started"):
            try:
                await stop_radar_notification_dispatcher()
            except Exception:
                pass
            services["_radar_started"] = False
        await stop("rf_tx_dispatcher")
        log_event("INFO", "system", "APRSBox core stopped")


async def _call(coroutine_factory: Callable[[], Awaitable[Any]]) -> Any:
    return await coroutine_factory()


async def _sync(function: Callable[[], Any]) -> Any:
    return function()


_ACTIVE_RUNTIME: CoreRuntime | None = None


def set_active_runtime(runtime: CoreRuntime | None) -> None:
    """Route ``app.services.core_client`` calls to the in-process runtime."""
    global _ACTIVE_RUNTIME
    _ACTIVE_RUNTIME = runtime
    from app.services import core_client

    core_client.set_local_core(runtime)


def get_active_runtime() -> CoreRuntime | None:
    return _ACTIVE_RUNTIME
