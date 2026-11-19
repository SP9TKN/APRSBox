"""Run blocking service calls off the GUI thread.

Most page data comes from SQLite read-models that can take tens or hundreds of
milliseconds; running them on a worker keeps the window responsive.  Results
are delivered back on the GUI thread through Qt signals.
"""

from __future__ import annotations

import itertools
import traceback
import weakref
from typing import Any, Callable

from PySide6.QtCore import QCoreApplication, QObject, QRunnable, QThreadPool, Signal, Slot

from app.db import log_event


class _Dispatcher(QObject):
    """The one object that carries every job's result back to the GUI thread. Created once, never deleted.

    APRSBox crashed twice (10-17, 10-23) with an access violation inside Qt's signal emission (doActivate,
    reading the receiver's thread data through a dangling pointer). Each job used to have its own QObject
    whose signal the worker emitted; the GUI thread could deliver the result, deleteLater() and destroy that
    object while the worker was still inside emit(). Qt does not allow deleting a sender/receiver while
    another thread emits on it. One long-lived dispatcher (like the tile manager's signals) removes that,
    and the per-job objects and their memory with it.
    """

    finished = Signal(int, object, object)  # (job id, result, error)

    def __init__(self) -> None:
        super().__init__()
        self._jobs: dict[int, tuple[Callable[[Any], None] | None, Callable[[BaseException], None] | None,
                                    Callable[[], QObject | None] | None]] = {}
        self._ids = itertools.count(1)
        self.finished.connect(self._deliver)

    def add(self, on_done: Callable[[Any], None] | None, on_error: Callable[[BaseException], None] | None,
            owner_ref: Callable[[], QObject | None] | None) -> int:
        job_id = next(self._ids)
        self._jobs[job_id] = (on_done, on_error, owner_ref)
        return job_id

    @Slot(int, object, object)
    def _deliver(self, job_id: int, result: Any, error: Any) -> None:
        on_done, on_error, owner_ref = self._jobs.pop(job_id, (None, None, None))
        if owner_ref is not None:
            alive = owner_ref()
            if alive is None:
                return
            try:
                from shiboken6 import isValid

                if not isValid(alive):
                    return
            except Exception:
                pass
        if error is not None:
            if on_error is not None:
                on_error(error)
            else:
                details = getattr(error, "__aprsbox_traceback__", "") or str(error)
                try:
                    log_event("ERROR", "desktop", f"Background task failed: {details}")
                except Exception:
                    pass
            return
        if on_done is not None:
            on_done(result)


class _Job(QRunnable):
    def __init__(self, function: Callable[[], Any], dispatcher: _Dispatcher, job_id: int) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._function = function
        self._dispatcher = dispatcher
        self._job_id = job_id

    def run(self) -> None:  # pragma: no cover - executed on worker threads
        try:
            result = self._function()
        except BaseException as exc:  # noqa: BLE001 - forwarded to the GUI thread
            exc.__aprsbox_traceback__ = traceback.format_exc()  # type: ignore[attr-defined]
            self._dispatcher.finished.emit(self._job_id, None, exc)
            return
        self._dispatcher.finished.emit(self._job_id, result, None)


_POOL: QThreadPool | None = None
_DISPATCHER: _Dispatcher | None = None


def pool() -> QThreadPool:
    global _POOL
    if _POOL is None:
        _POOL = QThreadPool()
        _POOL.setMaxThreadCount(4)
        _POOL.setExpiryTimeout(30000)
    return _POOL


def dispatcher() -> _Dispatcher:
    """The dispatcher, living in the GUI (application) thread whoever asks first."""
    global _DISPATCHER
    if _DISPATCHER is None:
        _DISPATCHER = _Dispatcher()
        app = QCoreApplication.instance()
        if app is not None and _DISPATCHER.thread() is not app.thread():
            _DISPATCHER.moveToThread(app.thread())
    return _DISPATCHER


def pending_jobs() -> int:
    """Jobs started and not delivered yet (tests)."""
    return len(dispatcher()._jobs)


def run_async(
    function: Callable[[], Any],
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[BaseException], None] | None = None,
    *,
    owner: QObject | None = None,
) -> None:
    """Run ``function`` on a worker; call ``on_done``/``on_error`` on the GUI thread.

    When ``owner`` is given and gets destroyed before the job finishes, the
    callbacks are skipped (the page is gone).
    """
    hub = dispatcher()
    job_id = hub.add(on_done, on_error, weakref.ref(owner) if owner is not None else None)
    pool().start(_Job(function, hub, job_id))


def wait_for_idle(timeout_ms: int = 10000) -> bool:
    """Block until all queued jobs are done (used by tests and on shutdown)."""
    return pool().waitForDone(timeout_ms)


class SerialLoader(QObject):
    """Coalesces refresh requests: at most one job in flight, one pending."""

    def __init__(self, function: Callable[[], Any], on_done: Callable[[Any], None], parent: QObject,
                 on_error: Callable[[BaseException], None] | None = None) -> None:
        super().__init__(parent)
        self._function = function
        self._on_done = on_done
        self._on_error = on_error
        self._running = False
        self._pending = False

    @property
    def busy(self) -> bool:
        return self._running

    def request(self) -> None:
        if self._running:
            self._pending = True
            return
        self._running = True
        run_async(self._function, self._finished, self._failed, owner=self)

    def _finished(self, result: Any) -> None:
        self._running = False
        try:
            self._on_done(result)
        finally:
            if self._pending:
                self._pending = False
                self.request()

    def _failed(self, error: BaseException) -> None:
        self._running = False
        if self._on_error is not None:
            self._on_error(error)
        else:
            details = getattr(error, "__aprsbox_traceback__", "") or str(error)
            try:
                log_event("ERROR", "desktop", f"Background refresh failed: {details}")
            except Exception:
                pass
        if self._pending:
            self._pending = False
            self.request()
