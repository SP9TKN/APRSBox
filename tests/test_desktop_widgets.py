"""Reusable desktop widgets (activation schedule editor, ASCII field, table helpers)."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_parse_form_datetime_accepts_web_formats() -> None:
    from app.desktop.widgets.activation import parse_form_datetime

    assert parse_form_datetime("2026-05-01 18:30").toString("yyyy-MM-dd HH:mm") == "2026-05-01 18:30"
    assert parse_form_datetime("2026-05-01T18:30").toString("yyyy-MM-dd HH:mm") == "2026-05-01 18:30"
    assert parse_form_datetime("2026-05-01").toString("yyyy-MM-dd HH:mm") == "2026-05-01 23:59"
    assert parse_form_datetime("") is None and parse_form_datetime("not a date") is None


def test_activation_editor_posts_like_the_web_form(qapp) -> None:
    from app.desktop.widgets.activation import ActivationScheduleEditor
    from app.desktop.widgets.common import set_combo_value

    editor = ActivationScheduleEditor(lambda: "WIDE2-2", lambda: "30")
    assert editor.values()["activation_mode"] == "manual"
    assert editor.values()["active_from_utc"] == ""
    editor.active_from.set_value("2026-01-01 10:00")
    editor.active_until.set_value("2026-01-02 10:00")
    # Manual mode ignores "active from" (disabled field) but keeps "valid until".
    assert editor.values()["active_from_utc"] == "" and editor.values()["active_until_utc"] == "2026-01-02 10:00"
    set_combo_value(editor.mode, "scheduled")
    assert editor.values()["active_from_utc"] == "2026-01-01 10:00"
    assert editor.values()["recurrence_duration_minutes"] == ""
    set_combo_value(editor.mode, "recurring")
    editor.duration_hours.setValue(3)
    editor.interval.setValue(2)
    set_combo_value(editor.unit, "day")
    values = editor.values()
    assert values["recurrence_duration_minutes"] == "180"
    assert values["recurrence_interval_value"] == "2" and values["recurrence_interval_unit"] == "day"
    assert "WIDE2-2" in editor.warnings.text()
    editor.active_until.clear_value()
    assert editor.active_until.is_empty() and editor.values()["active_until_utc"] == ""


def test_ascii_field_strips_national_characters_and_limits_length(qapp) -> None:
    from app.desktop.widgets.crud import AsciiTextField

    field = AsciiTextField(10)
    field.edit.setPlainText("Zażółć gęślą jaźń")
    assert field.text() == "Za gl ja"
    assert not field.error.isHidden()
    field.set_text("0123456789ABC")
    assert field.text() == "0123456789" and field.counter.text() == "10 / 10"


def test_data_table_filters_and_keeps_selection(qapp) -> None:
    from app.desktop.widgets.table import Column, DataTable

    table = DataTable([Column("name", "Name", stretch=True)])
    table.set_rows([{"id": 1, "name": "SP9ABC"}, {"id": 2, "name": "SQ9XYZ"}])
    assert table.select_key(2)
    table.set_rows([{"id": 2, "name": "SQ9XYZ"}, {"id": 3, "name": "SR9XSR"}])
    assert table.current_row()["id"] == 2
    table.set_text_filter("sr9")
    assert table.visible_count() == 1
    table.set_text_filter("")
    table.set_filter(lambda row: row["id"] > 2)
    assert table.visible_count() == 1


def test_station_symbol_program_icon(qapp) -> None:
    from PySide6.QtCore import QSize

    from app.desktop.icons import PROGRAM_ICON_SIZES, app_icon, program_icon, station_symbol_icon

    icon = station_symbol_icon("/", "#", "", symbol_set="modern")
    available = {size.width() for size in icon.availableSizes()}
    assert set(PROGRAM_ICON_SIZES) <= available
    image = icon.pixmap(QSize(32, 32)).toImage()
    assert len({image.pixelColor(x, y).name() for x in range(32) for y in range(32)}) > 4  # not empty
    # The overlay character belongs to the alternate table only.
    plain = station_symbol_icon("/", "#", "D", symbol_set="modern").pixmap(QSize(48, 48)).toImage()
    assert plain == station_symbol_icon("/", "#", "", symbol_set="modern").pixmap(QSize(48, 48)).toImage()
    with_overlay = station_symbol_icon("\\", "#", "D", symbol_set="modern").pixmap(QSize(48, 48)).toImage()
    assert with_overlay != station_symbol_icon("\\", "#", "", symbol_set="modern").pixmap(QSize(48, 48)).toImage()
    # No callsign yet, or the option switched off: the APRSBox logo.
    logo = app_icon().pixmap(QSize(32, 32)).toImage()
    assert program_icon({"callsign": "", "symbol_table": "/", "symbol_code": ">"}).pixmap(QSize(32, 32)).toImage() == logo
    station = {"callsign": "SP9TKN", "symbol_table": "/", "symbol_code": "-"}
    assert program_icon(station, use_station_symbol=False).pixmap(QSize(32, 32)).toImage() == logo
    assert program_icon(station).pixmap(QSize(32, 32)).toImage() != logo


def test_classic_symbols_lose_their_white_tile(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtGui import QImage

    from app.desktop import icons
    from app.desktop.icons import _symbol_base_pixmap, static_path, symbol_icon_for

    def opaque(image: QImage, x: int, y: int) -> bool:
        return image.pixelColor(x, y).alpha() == 255

    original = QImage(str(static_path("icons/verG/29.gif")))  # car on a white tile with a dark frame
    assert not original.hasAlphaChannel() and opaque(original, 0, 0)
    car = _symbol_base_pixmap("icons/verG/29.gif").toImage()
    width, height = car.width(), car.height()
    for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1), (1, 1), (width - 2, height - 2)):
        assert car.pixelColor(x, y).alpha() == 0, (x, y)
    assert sum(opaque(car, x, y) for x in range(width) for y in range(height)) > width * height // 5  # the car stays
    # White enclosed by the symbol's outline stays (the white "6" on the blue square).
    six = _symbol_base_pixmap("icons/verG/21.gif").toImage()
    assert any(
        opaque(six, x, y) and min(six.pixelColor(x, y).red(), six.pixelColor(x, y).green(), six.pixelColor(x, y).blue()) >= 225
        for x in range(six.width()) for y in range(six.height())
    )
    # The modern PNG set is transparent already and left untouched.
    modern_path = symbol_icon_for("/", ">", symbol_set="modern")
    assert QImage(str(static_path(modern_path))).hasAlphaChannel()
    calls: list[object] = []
    monkeypatch.setattr(icons, "without_tile_background", lambda image: calls.append(image) or image)
    _symbol_base_pixmap.cache_clear()
    try:
        assert not _symbol_base_pixmap(modern_path).isNull() and calls == []
    finally:
        _symbol_base_pixmap.cache_clear()


def test_classic_map_symbol_is_as_tall_as_the_callsign_bubble(qapp) -> None:
    from PySide6.QtCore import QPointF

    from app.desktop.widgets.map_view import LEGACY_SYMBOL_ART_PX, LEGACY_SYMBOL_FILE_PX, MODERN_ICON_PX, MapView

    view = MapView()
    view.set_modern_symbols(False)
    size = view._icon_size()
    assert abs(size * LEGACY_SYMBOL_ART_PX / LEGACY_SYMBOL_FILE_PX - view._label_height()) <= 1
    label = view._label_pixmap("SP9TKN", False, False)
    origin = view._label_origin(QPointF(100, 100), label, size)
    # Centred on the symbol and just right of its drawing.
    assert abs(origin.y() + label.height() / label.devicePixelRatio() / 2 - 100) < 0.01
    assert 100 + size * LEGACY_SYMBOL_ART_PX / LEGACY_SYMBOL_FILE_PX / 2 < origin.x() < 100 + size / 2 + 3
    view.set_modern_symbols(True)
    assert view._icon_size() == MODERN_ICON_PX
    view.deleteLater()


def test_map_callsign_bubble_and_letters_let_the_map_show_through(qapp) -> None:
    from PySide6.QtGui import QColor

    from app.desktop.theme import contrast_ratio
    from app.desktop.widgets.map_view import LABEL_BACKGROUND, LABEL_TEXT, MapView

    view = MapView()
    pixmap = view._label_pixmap("SP9TKN", False, False)
    image = pixmap.toImage()
    background = image.pixelColor(image.width() // 2, round(2.5 * pixmap.devicePixelRatio()))  # above the letters
    assert 120 <= background.alpha() <= 165  # light grey bubble, clearly see-through
    assert all(abs(channel - 223) <= 4 for channel in (background.red(), background.green(), background.blue()))
    row = [image.pixelColor(x, image.height() // 2) for x in range(image.width())]
    darkest = min((color for color in row if color.alpha() >= 150), key=lambda color: color.lightness())
    assert darkest.lightness() < 100 and abs(darkest.red() - darkest.blue()) <= 8  # dark grey letters ...
    assert max(color.alpha() for color in row) < 245  # ... the map shows through them as well

    def over(top: QColor, base: QColor) -> QColor:
        alpha = top.alphaF()
        return QColor(*(round(a * alpha + b * (1 - alpha))
                        for a, b in ((top.red(), base.red()), (top.green(), base.green()), (top.blue(), base.blue()))))

    # Still readable on the darkened map of the dark theme.
    bubble = over(LABEL_BACKGROUND, QColor(90, 90, 90))
    assert contrast_ratio(over(LABEL_TEXT, bubble), bubble) >= 3.0
    view.deleteLater()


def test_selected_table_row_stands_out_in_every_palette(qapp) -> None:
    from app.desktop.palettes import PALETTES
    from app.desktop.theme import (
        ROW_SELECTION_MIN_CONTRAST,
        TEXT_MIN_CONTRAST,
        contrast_ratio,
        row_selection_color,
        theme_colors,
    )

    for name in PALETTES:
        for mode in ("dark", "light"):
            colors = theme_colors(name, mode)
            selected = row_selection_color(colors)
            # Against both zebra rows (accent-soft alone was ~1.0-1.24 against panel-alt).
            assert min(contrast_ratio(selected, colors.color(key)) for key in ("panel", "panel-alt")) >= ROW_SELECTION_MIN_CONTRAST, (name, mode)
            assert contrast_ratio(selected, colors.color("text")) >= TEXT_MIN_CONTRAST, (name, mode)


def test_own_station_marker_until_the_station_is_on_the_map(qapp) -> None:
    from app.desktop.map_layers import OWN_SETTINGS_ORIGIN, own_station_marker

    station = {
        "callsign": "sp9tkn", "ssid": "0", "latitude": "50.094513", "longitude": "19.675835",
        "symbol_table": "\\", "symbol_code": "#", "symbol_overlay": "I", "beacon_comment": "Igate&Digi 144.800",
    }
    marker = own_station_marker(station, symbol_icon="icons/verG/x.gif", present_keys=set())
    assert marker is not None and marker.own and marker.label == "SP9TKN" and marker.overlay == "I"
    assert (marker.latitude, marker.longitude) == (50.094513, 19.675835)
    assert marker.payload["origin"] == OWN_SETTINGS_ORIGIN and "Igate&amp;Digi 144.800" in marker.tooltip
    # Once a frame of the station put it on the map, its regular marker takes over.
    assert own_station_marker(station, symbol_icon="", present_keys={"SP9TKN"}) is None
    assert own_station_marker({**station, "ssid": "7"}, symbol_icon="", present_keys={"SP9TKN"}).label == "SP9TKN-7"
    assert own_station_marker({**station, "symbol_table": "/"}, symbol_icon="", present_keys=set()).overlay == ""
    assert own_station_marker({**station, "latitude": ""}, symbol_icon="", present_keys=set()) is None
    assert own_station_marker({**station, "callsign": ""}, symbol_icon="", present_keys=set()) is None


def test_track_dots_show_who_received_each_frame(qapp) -> None:
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QContextMenuEvent

    from app.desktop.map_layers import track_shapes
    from app.desktop.widgets.map_view import MapView

    track = {"display_callsign": "SQ9XAA-7", "points": [
        {"latitude": 50.10, "longitude": 19.60, "interface_id": 3, "heard_at": "2026-10-08T17:20:00+00:00", "frame_id": 11,
         "source_kind": "rf", "line": "SQ9XAA-7>APRS,SP9XDG*,WIDE2*:!5006.00N/01936.00E>"},
        {"latitude": 50.11, "longitude": 19.62, "interface_id": 1, "heard_at": "2026-10-08T17:21:00+00:00", "frame_id": 12,
         "source_kind": "aprsis", "line": "SQ9XAA-7>APRS,SR9XNW*,WIDE2-1,qAR,SP9XDG-10:!5006.60N/01937.20E>"},
    ]}
    shape = track_shapes([track], own_key="SP9TKN", interface_names={3: "DIREWOLF 290R2", 1: "APRS-IS"})[0]
    assert "SP9TKN" in shape.point_tooltips[0] and "DIREWOLF 290R2" in shape.point_tooltips[0] and "SP9XDG" in shape.point_tooltips[0]
    assert "SP9XDG-10" in shape.point_tooltips[1] and "qAR" in shape.point_tooltips[1] and "SR9XNW" in shape.point_tooltips[1]
    assert shape.point_payloads[0]["frame_id"] == 11 and shape.point_payloads[1]["interface_name"] == "APRS-IS"

    view = MapView()
    view.resize(400, 300)
    view.set_view(50.105, 19.61, 14)
    view.set_layer("tracks", [shape])
    origin = view._origin()
    x, y = view._world_point(50.10, 19.60)
    dot = QPointF(x - origin[0], y - origin[1])
    hits = view._hit_points(dot)
    assert hits and hits[0][1] == 0
    assert view._hit_points(QPointF(dot.x() + 40, dot.y() + 40)) == []
    received: list[object] = []
    view.track_points_selected.connect(lambda payloads, _pos: received.append(payloads))
    view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, dot.toPoint(), QPoint(0, 0)))
    assert received and received[0][0]["frame_id"] == 11
    view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(0, 0)))
    assert received[-1] == []  # right click elsewhere: nothing picked
    view.deleteLater()


def test_right_click_on_a_track_dot_draws_a_line_to_the_receiver(qapp) -> None:
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QContextMenuEvent, QKeyEvent

    from app.desktop.i18n import t
    from app.desktop.map_layers import TrackReceptionLines, path_segment_color, track_shapes
    from app.desktop.rf_paths import KIND_APRSIS, KIND_RF_DIGI, KIND_RF_DIRECT
    from app.desktop.widgets.map_view import MapView

    positions = {"SP9TKN": (50.0945, 19.6758), "SP9XDG": (50.15, 20.05), "SR9XNW": (49.45, 20.99), "SP9XDG-10": (49.90, 20.20)}
    track = {"display_callsign": "SQ9XAA-7", "points": [
        {"latitude": 50.10, "longitude": 19.60, "heard_at": "2026-10-08T17:20:00+00:00", "frame_id": 11,
         "source_kind": "rf", "line": "SQ9XAA-7>APRS,SP9XDG*,WIDE2*:!5006.00N/01936.00E>"},
        {"latitude": 50.11, "longitude": 19.62, "heard_at": "2026-10-08T17:21:00+00:00", "frame_id": 12,
         "source_kind": "aprsis", "line": "SQ9XAA-7>APRS,SR9XNW*,WIDE2-1,qAR,SP9XDG-10:!5006.60N/01937.20E>"},
        {"latitude": 50.12, "longitude": 19.64, "heard_at": "2026-10-08T17:22:00+00:00", "frame_id": 13,
         "source_kind": "aprsis", "line": "SQ9XAA-7>APRS,TCPIP*,qAC,T2POLAND:!5007.20N/01938.40E>"},
        {"latitude": 50.13, "longitude": 19.66, "heard_at": "2026-10-08T17:23:00+00:00", "frame_id": 14,
         "source_kind": "rf", "line": "SQ9XAA-7>APRS,WIDE1-1:!5007.80N/01939.60E>"},
    ]}
    shape = track_shapes([track], own_key="SP9TKN", pick_hint=True)[0]
    hint = t("Right click: line to the station that received it")
    assert hint in shape.point_tooltips[0] and hint in shape.point_tooltips[1]
    assert hint not in shape.point_tooltips[2]  # Internet only: no line to draw
    assert hint not in track_shapes([track], own_key="SP9TKN")[0].point_tooltips[0]

    view = MapView()
    view.resize(400, 300)
    view.set_view(50.105, 19.61, 14)
    view.set_layer("tracks", [shape])
    lines = TrackReceptionLines(view, positions=lambda: positions, own_key=lambda: "SP9TKN")
    origin = view._origin()
    x, y = view._world_point(50.10, 19.60)
    dot = QPoint(round(x - origin[0]), round(y - origin[1]))
    view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, dot, QPoint(0, 0)))
    drawn = view.layers["reception"]
    assert [line.points for line in drawn] == [[(50.10, 19.60), positions["SP9XDG"]], [positions["SP9XDG"], positions["SP9TKN"]]]
    assert [line.color.name() for line in drawn] == [path_segment_color(KIND_RF_DIGI).name(), path_segment_color(KIND_RF_DIRECT).name()]
    assert all(line.arrows and not line.dashed for line in drawn)
    assert view._hit_shape(QPointF(*_screen_midpoint(view, drawn[0]))) is drawn[0]  # the line has its own tooltip
    view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, dot, QPoint(0, 0)))
    assert view.layers["reception"] == [] and lines.payloads == []  # the same dot again hides it

    lines.select([shape.point_payloads[1]])
    assert [line.points[-1] for line in view.layers["reception"]] == [positions["SR9XNW"], positions["SP9XDG-10"]]
    assert {line.color.name() for line in view.layers["reception"]} == {path_segment_color(KIND_APRSIS).name()}
    view.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert view.layers["reception"] == [] and lines.payloads == []

    notes = lines.select([shape.point_payloads[2]])
    assert view.layers["reception"] == [] and lines.payloads == [] and notes == [
        t("Received over the Internet ({detail}): nobody heard it on RF").format(detail="qAC, T2POLAND")]
    assert lines.select([shape.point_payloads[2]]) == notes  # nothing drawn: the next right click explains it again
    lines.select([shape.point_payloads[3]])
    assert [line.points for line in view.layers["reception"]] == [[(50.13, 19.66), positions["SP9TKN"]]]
    del positions["SP9TKN"]  # own position not set yet
    assert lines.refresh() == [t("Position unknown on the map: {callsigns}").format(callsigns="SP9TKN")]
    assert view.layers["reception"] == []
    view.deleteLater()


def test_set_view_at_puts_the_point_at_the_anchor(qapp) -> None:
    from PySide6.QtCore import QPointF

    from app.desktop.widgets.map_view import MapView

    view = MapView()
    view.resize(1000, 600)
    view.set_view_at(50.0945, 19.6758, 12, anchor=QPointF(340, 300))
    origin = view._origin()
    x, y = view._world_point(50.0945, 19.6758)
    assert view.zoom == 12 and abs(x - origin[0] - 340) < 0.5 and abs(y - origin[1] - 300) < 0.5
    view.set_view_at(50.0945, 19.6758, 12)  # no anchor: the middle, like set_view
    assert (round(view.center_lat, 6), round(view.center_lon, 6)) == (50.0945, 19.6758)
    view.deleteLater()


def test_reception_lines_go_out_by_themselves_after_ten_seconds(qapp) -> None:
    from app.desktop.map_layers import RECEPTION_FADE_SECONDS, RECEPTION_LINES_SECONDS, TrackReceptionLines, track_shapes
    from app.desktop.widgets.map_view import MapView

    positions = {"SP9TKN": (50.0945, 19.6758)}
    track = {"display_callsign": "SQ9XAA-7", "points": [
        {"latitude": 50.10, "longitude": 19.60, "frame_id": 21, "source_kind": "rf", "line": "SQ9XAA-7>APRS,WIDE1-1:>a"},
        {"latitude": 50.11, "longitude": 19.62, "frame_id": 22, "source_kind": "rf", "line": "SQ9XAA-7>APRS,WIDE1-1:>b"},
    ]}
    payload = track_shapes([track], own_key="SP9TKN")[0].point_payloads[0]
    view = MapView()
    lines = TrackReceptionLines(view, positions=lambda: positions, own_key=lambda: "SP9TKN")
    lines.select([payload])
    assert len(view.layers["reception"]) == 1 and view.layers["reception"][0].opacity == 1.0
    assert lines._timer.isActive() and lines._timer.interval() == round((RECEPTION_LINES_SECONDS - RECEPTION_FADE_SECONDS) * 1000)
    lines._shown_at -= RECEPTION_LINES_SECONDS - RECEPTION_FADE_SECONDS / 2  # half way through the dimming
    lines._tick()
    line = view.layers["reception"][0]
    assert abs(line.opacity - 0.5) < 0.05 and abs(line.halo.alphaF() - 0.85 * 0.5) < 0.05 and lines._timer.isActive()
    lines.refresh()  # a page refresh keeps it dimmed
    assert abs(view.layers["reception"][0].opacity - 0.5) < 0.05
    lines._shown_at -= RECEPTION_FADE_SECONDS
    lines._tick()
    assert view.layers["reception"] == [] and lines.payloads == [] and not lines._timer.isActive()
    lines.select([payload])  # gone by itself: the same dot draws it again
    assert len(view.layers["reception"]) == 1
    lines.clear()
    assert not lines._timer.isActive()
    view.deleteLater()


def test_right_click_on_a_station_draws_the_way_of_its_latest_frame(qapp) -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent

    from app.desktop.i18n import t
    from app.desktop.map_layers import OWN_SETTINGS_ORIGIN, TrackReceptionLines, station_markers
    from app.desktop.widgets.map_view import MapView

    positions = {"SP9TKN": (50.0945, 19.6758), "SP9XDG": (50.15, 20.05), "SR9XSR": (50.179, 19.901)}
    stations = [
        {"display_callsign": "SQ9XAA-7", "latitude": "50.10000", "longitude": "19.60000", "source_kind": "rf", "is_rf": True,
         "origin": "heard", "last_heard_at": "2026-10-08T17:20:00+00:00",
         "raw_text": "SQ9XAA-7>APRS,SP9XDG*,WIDE2*:!5006.00N/01936.00E>"},
        {"display_callsign": "MEETING", "latitude": "50.0950", "longitude": "19.6650", "source_kind": "rf", "is_rf": True,
         "origin": "heard", "raw_text": "SR9XSR>APNU19,WIDE2-1:;MEETING  *071200z5005.70N/01939.90EEClub meeting"},
        {"display_callsign": "SP9NET", "latitude": "49.86", "longitude": "20.94", "source_kind": "aprsis",
         "origin": "heard", "raw_text": "SP9NET>APRS,TCPIP*,qAC,T2POLAND:>x"},
    ]
    markers = station_markers(stations, own_key="SP9TKN", pick_hint=True)
    hint = t("Right click: line to the station that received it")
    assert [hint in marker.tooltip for marker in markers] == [True, True, False]  # SP9NET: Internet only
    assert all(hint not in marker.tooltip for marker in station_markers(stations, own_key="SP9TKN"))

    view = MapView()
    view.resize(500, 400)
    view.set_view(50.10, 19.60, 13)
    view.set_markers(markers[:1])
    lines = TrackReceptionLines(view, positions=lambda: positions, own_key=lambda: "SP9TKN")
    on_station = QPoint(250, 200)  # SQ9XAA-7 is in the middle of the view
    view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, on_station, QPoint(0, 0)))
    assert [line.points for line in view.layers["reception"]] == [
        [(50.10, 19.60), positions["SP9XDG"]], [positions["SP9XDG"], positions["SP9TKN"]]]
    view.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Reason.Mouse, on_station, QPoint(0, 0)))
    assert view.layers["reception"] == []  # the same station again hides it

    # An object: the line starts at the station that sent it, not at the object itself.
    lines._select_station(stations[1])
    assert [line.points for line in view.layers["reception"]] == [[positions["SR9XSR"], positions["SP9TKN"]]]
    lines.clear()
    del positions["SR9XSR"]
    assert lines._select_station(stations[1]) == [t("Position unknown on the map: {callsigns}").format(callsigns="SR9XSR")]
    assert lines._select_station(stations[2]) == [
        t("Received over the Internet ({detail}): nobody heard it on RF").format(detail="qAC, T2POLAND")]
    assert lines._select_station({"origin": OWN_SETTINGS_ORIGIN}) == [] and view.layers["reception"] == []
    view.deleteLater()


def _screen_midpoint(view, line) -> tuple[float, float]:
    origin = view._origin()
    (ax, ay), (bx, by) = (view._world_point(*point) for point in line.points)
    return ((ax + bx) / 2 - origin[0], (ay + by) / 2 - origin[1])


def test_station_tooltip_keeps_weather_on_a_few_short_lines(qapp) -> None:
    import re

    from app.desktop.map_layers import TOOLTIP_MAX_CHARS, station_data_lines, station_tooltip

    weather = [
        ("Kierunek wiatru", "220°"), ("Prędkość wiatru", "13 km/h"), ("Porywy", "19 km/h"),
        ("Temperatura", "13.3°C"), ("Deszcz 1h", "0.0 mm"), ("Deszcz 24h", "0.0 mm"),
        ("Deszcz od północy", "0.0 mm"), ("Wilgotność", "70%"), ("Ciśnienie", "980.8 hPa"),
        ("Nasłonecznienie", "153 W/m²"),
    ]
    station = {
        "display_callsign": "GW7536", "aprs_device_short": "Unknown", "source": "CWOP · cwop.aprs.net",
        "path": "TCPIP*,qAC,CWOP-7", "distance_km": 14.2,
        "comment": "L153 WX de SP9SSM Sig:-105.0dBm UV:1.6 Bat:3.95V " * 3,  # a long one wraps too
        "data": [{"icon": "x.svg", "label": label, "value": value} for label, value in weather],
    }
    lines = station_data_lines(station["data"])
    assert len(lines) == 3  # the user: weather on 2-3 lines, not one across the map
    assert lines[0] == "Temperatura: 13.3°C · Wilgotność: 70% · Ciśnienie: 980.8 hPa"
    assert lines[1].endswith("220°, 13 km/h, gusts 19 km/h · Nasłonecznienie: 153 W/m²")
    assert "1h 0.0 mm, 24h 0.0 mm, since midnight 0.0 mm" in lines[2]
    rows = [re.sub(r"<[^>]+>", "", row).replace("&amp;", "&") for row in station_tooltip(station).split("<br>")]
    assert max(len(row) for row in rows) <= TOOLTIP_MAX_CHARS  # as wide as the other station tooltips
    # Other decoded data (not weather) still shows, wrapped to the same width.
    other = station_data_lines([{"label": "Kurs", "value": "260°"}, {"label": "Prędkość", "value": "57 km/h"}])
    assert other == ["Kurs: 260° · Prędkość: 57 km/h"]


def test_station_info_shows_when_the_radio_and_aprs_is_last_had_it(qapp) -> None:
    """The user: a station kept on the map by APRS-IS showed only the radio's time, hours old."""
    import re
    from datetime import datetime, timedelta, timezone

    from app.datetime_utils import format_display_datetime
    from app.desktop.map_layers import station_tooltip

    now = datetime.now(timezone.utc).replace(microsecond=0)
    radio = (now - timedelta(hours=4, minutes=44, seconds=10)).isoformat()
    aprsis = (now - timedelta(minutes=58, seconds=10)).isoformat()

    def rows(station: dict) -> list[str]:
        return [re.sub(r"<[^>]+>", "", row) for row in station_tooltip(station).split("<br>")]

    both = rows({"display_callsign": "SP9XG-6", "last_heard_at": radio, "last_heard_age_s": 17050,
                 "activity_label": "Last heard", "last_heard_rf_at": radio, "last_seen_aprsis_at": aprsis})
    assert f"Radio: {format_display_datetime(radio)} (4 h 44 min ago)" in both
    assert f"APRS-IS: {format_display_datetime(aprsis)} (58 min ago)" in both
    assert not any(row.startswith("Last heard:") for row in both)  # no third, ambiguous time
    radio_only = rows({"display_callsign": "SP9XGP-1", "last_heard_rf_at": radio})
    assert [row for row in radio_only if row.startswith(("Radio:", "APRS-IS:"))] == [
        f"Radio: {format_display_datetime(radio)} (4 h 44 min ago)"]
    neither = rows({"display_callsign": "MEETING", "last_heard_at": aprsis, "activity_label": "Last heard"})
    assert f"Last heard: {format_display_datetime(aprsis)}" in neither  # nothing to split: as before


def test_memory_caches_stay_small(qapp) -> None:
    """The user: APRSBox at 587 MB after 5 days. Callsign bubbles and RAM tiles are capped low, LRU."""
    from app.desktop.tiles import TileManager
    from app.desktop.widgets import map_view
    from app.desktop.widgets.map_view import MapView

    assert TileManager.MEMORY_TILES <= 96
    view = MapView(show_zoom_control=False, show_attribution=False)
    first = view._label_pixmap("SP9TKN", False, True)
    for index in range(map_view.LABEL_CACHE_MAX + 200):
        view._label_pixmap(f"SP9{index:04d}", False, False)
        if index % 100 == 0:
            assert view._label_pixmap("SP9TKN", False, True) is first  # used often: kept, not redrawn
    assert len(view._label_cache) == map_view.LABEL_CACHE_MAX
    assert ("SP90000", False, False) not in {key[:3] for key in view._label_cache}  # the oldest went first
    view.deleteLater()


def test_maps_off_the_screen_give_their_memory_back(qapp) -> None:
    """The user: a light app. A hidden map drops its labels, and the RAM tiles go once no map is shown."""
    from PySide6.QtGui import QPixmap

    from app.desktop.tiles import TileManager
    from app.desktop.widgets.map_view import MapView

    tiles = TileManager()
    first = MapView(tile_manager=tiles, show_zoom_control=False, show_attribution=False)
    second = MapView(tile_manager=tiles, show_zoom_control=False, show_attribution=False)
    first.show()
    second.show()
    tiles._memory[("x", 1, 1, 1)] = QPixmap(4, 4)
    first._label_pixmap("SP9TKN", False, True)
    first.hide()
    assert first._release_timer.isActive()
    first._release_memory_if_unseen()
    assert not first._label_cache and tiles._memory  # the other map is still on the screen
    second.hide()
    second._release_memory_if_unseen()
    assert not tiles._memory  # no map on the screen: the tiles are back on the disk only
    second.show()
    assert not second._release_timer.isActive()
    tiles.set_fast(True)  # "Fast map": nothing is given back
    tiles._memory[("x", 1, 1, 1)] = QPixmap(4, 4)
    second.hide()
    second._release_memory_if_unseen()
    assert tiles._memory and tiles.memory_tiles == TileManager.MEMORY_TILES_FAST
    for view in (first, second):
        view.deleteLater()


def test_a_removed_map_leaves_the_shared_tiles_without_errors(qapp) -> None:
    """Pages are rebuilt (settings saved) and their maps go; the tile manager stays. Its next status change
    must not reach the removed map ("Signal source has been deleted" opened the error window)."""
    import sys

    from PySide6.QtCore import QCoreApplication, QEvent

    from app.desktop.tiles import TileManager
    from app.desktop.widgets.map_view import MapView

    tiles = TileManager()
    kept = MapView(tile_manager=tiles, show_zoom_control=False, show_attribution=False)
    removed = MapView(tile_manager=tiles, show_zoom_control=False, show_attribution=False)
    statuses: list[str] = []
    kept.tile_status_changed.connect(statuses.append)
    removed.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    errors: list[str] = []
    saved_hook = sys.excepthook
    sys.excepthook = lambda kind, error, _tb: errors.append(f"{kind.__name__}: {error}")
    try:
        tiles.status_changed.emit("recovered")
        tiles.source_changed.emit()
        tiles.tile_ready.emit()
        qapp.processEvents()
    finally:
        sys.excepthook = saved_hook
    assert errors == []
    assert statuses == ["recovered"]  # the map still on a page gets it
    kept.deleteLater()
    tiles.shutdown()


def test_super_zoom_enlarges_the_last_tiles_of_the_source(qapp) -> None:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QColor, QPixmap

    from app.desktop.tiles import TileSource
    from app.desktop.widgets.map_view import MapView

    class FakeTiles(QObject):
        tile_ready = Signal()
        status_changed = Signal(str)
        source_changed = Signal()

        def __init__(self) -> None:
            super().__init__()
            self.source = TileSource(source_id=1, name="OSM", url_template="http://127.0.0.1:9/{z}/{x}/{y}.png", max_zoom=19)
            self.requests: list[tuple[int, int, int]] = []
            self.pixmap = QPixmap(256, 256)
            self.pixmap.fill(QColor("#4080c0"))

        def cached(self, z: int, x: int, y: int) -> QPixmap | None:
            return self.pixmap if z == 19 else None

        def request(self, z: int, x: int, y: int) -> None:
            self.requests.append((z, x, y))

    tiles = FakeTiles()
    view = MapView(tile_manager=tiles, show_zoom_control=False, show_attribution=False)
    view.resize(400, 300)
    assert (view.native_max_zoom, view.max_zoom) == (19, MapView.MAX_ZOOM) == (19, 25)  # as on the CWOP weather map
    view.set_view(50.0945, 19.6758, 25)
    assert view.zoom == 25
    image = view.grab().toImage()
    assert tiles.requests == []  # past zoom 19 nothing new is downloaded: the zoom-19 tile is enlarged
    assert image.pixelColor(200, 150) == QColor("#4080c0")
    view.set_view(50.0945, 19.6758, 21)
    tiles.cached = lambda z, x, y: None  # nothing in memory: zoom-19 tiles are what it asks for
    view.grab()
    assert tiles.requests and {z for z, _x, _y in tiles.requests} == {19}
    # Markers group (and spread) by the tiles' own max zoom, not by the enlarged range.
    view.cluster_enabled = view.spiderfy_enabled = True
    assert view.spiderfy_activation_zoom() == 17
    view.set_view(50.0945, 19.6758, 16)
    assert view._clustering_active()
    view.set_view(50.0945, 19.6758, 17)
    assert not view._clustering_active()
    tiles.source = TileSource(source_id=2, name="CARTO", url_template="http://127.0.0.1:9/{z}/{x}/{y}.png", max_zoom=20)
    tiles.source_changed.emit()  # the map bar picked another source
    assert view.native_max_zoom == 20 and view.max_zoom == 25
    view.deleteLater()


def test_tile_cache_limit_removes_tiles_outside_the_listening_area_first(tmp_path) -> None:
    import os
    import time

    from app.desktop import geo
    from app.desktop.tiles import prune_tile_cache, tile_distance_km

    station = (50.094513, 19.675835)  # SP9TKN, listening radius 50 km (APRS-IS m/50)

    def tile_of(latitude: float, longitude: float, z: int) -> tuple[int, int, int]:
        x, y = geo.project(latitude, longitude, z)
        return z, int(x // geo.TILE_SIZE), int(y // geo.TILE_SIZE)

    def write(name: str, tile: tuple[int, int, int], days_old: float) -> str:
        z, x, y = tile
        path = tmp_path / "1-OpenStreetMap" / str(z) / str(x) / f"{y}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 1000)
        stamp = time.time() - days_old * 86400
        os.utime(path, (stamp, stamp))
        return name

    tiles = {
        "home old": tile_of(*station, 15), "home new": tile_of(50.09, 19.66, 16),
        "Katowice": tile_of(50.06, 20.02, 15),  # 25 km away: inside
        "Warszawa newest": tile_of(52.23, 21.01, 15), "Krakow": tile_of(49.86, 20.94, 15),  # 93 km: outside
        "Poland z5": tile_of(52.0, 19.0, 5),  # a big tile covering the station: inside
    }
    ages = {"home old": 30, "home new": 1, "Katowice": 10, "Warszawa newest": 0.1, "Krakow": 20, "Poland z5": 40}
    for name, tile in tiles.items():
        write(name, tile, ages[name])
    assert tile_distance_km(*tiles["home old"], *station) == 0
    assert tile_distance_km(*tiles["Krakow"], *station) > 50

    # 6 kB on disk, limit 2.5 kB -> down to 90 % = 2.25 kB: four tiles go.
    result = prune_tile_cache(tmp_path, limit_bytes=2500, center=station, radius_km=50)
    left = {name for name, (z, x, y) in tiles.items() if (tmp_path / "1-OpenStreetMap" / str(z) / str(x) / f"{y}.png").exists()}
    # Outside the area first (even the newest one), then the oldest inside it.
    assert left == {"home new", "Katowice"}
    assert result["removed"] == 4 and result["removed_outside"] == 2
    assert prune_tile_cache(tmp_path, limit_bytes=2500, center=station, radius_km=50)["removed"] == 0  # under the limit


def test_elevation_tiles_are_coloured_by_height_and_kept_for_good(qapp, tmp_path, monkeypatch) -> None:
    import os

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    from app.desktop import tiles

    def encoded(colour: QColor, fmt: str) -> bytes:
        image = QImage(256, 256, QImage.Format.Format_RGB32)
        image.fill(colour)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, fmt)
        return bytes(data.data())

    def centre(data: bytes) -> tuple[int, int, int]:
        colour = QImage.fromData(data).pixelColor(128, 128)
        return colour.red(), colour.green(), colour.blue()

    # Terrarium: 300 m = R*256 + G + B/256 - 32768 -> R 129, G 44.
    terrarium = encoded(QColor(129, 44, 0), "PNG")
    expected = tiles.elevation_colour(300)
    assert expected == (186, 208, 108)  # the same height has the same colour on every tile
    assert all(abs(a - b) <= 6 for a, b in zip(centre(tiles.render_elevation_tile(terrarium, None)), expected))
    shade = encoded(QColor(128, 128, 128), "JPG")  # Esri's relief shading multiplies the colours
    darker = centre(tiles.render_elevation_tile(terrarium, shade))
    assert all(abs(a - b * 128 / 255) <= 8 for a, b in zip(darker, expected))
    assert tiles.elevation_colour(50) == tiles.elevation_colour(100) and tiles.elevation_colour(1800) == (240, 240, 240)

    monkeypatch.setattr(tiles, "tile_cache_root", lambda: tmp_path)
    downloads: list[str] = []
    monkeypatch.setattr(tiles, "_download", lambda url: downloads.append(url) or (terrarium if "terrarium" in url else shade))
    source = tiles.TileSource(source_id=6, name="Wysokości (AWS)", max_zoom=15,
                              url_template="https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png")
    assert tiles.is_elevation_source(source)
    first = tiles.fetch_tile_bytes(source, 10, 568, 350)
    assert len(downloads) == 2 and "terrarium/10/568/350" in downloads[0] and "World_Hillshade" in downloads[1]
    painted = list(tmp_path.rglob("*.jpg"))
    assert len(painted) == 1
    os.utime(painted[0], (1, 1))  # very old: still no refresh, the terrain does not change
    downloads.clear()
    assert tiles.fetch_tile_bytes(source, 10, 568, 350) == first and downloads == []


def test_tile_cache_limit_comes_from_the_settings(qapp, monkeypatch) -> None:
    from app.desktop import tiles

    checks: list[float] = []
    monkeypatch.setattr(tiles, "start_tile_cache_check", lambda delay_s=0.0: checks.append(delay_s))
    tiles.set_tile_cache_limit_mb(5120)
    assert tiles.tile_cache_limit_mb() == 5120 and checks == [0.0]  # a new limit is checked right away
    tiles.set_tile_cache_limit_mb(tiles.DEFAULT_TILE_CACHE_LIMIT_MB)
    assert tiles.DEFAULT_TILE_CACHE_LIMIT_MB == 2048  # the user's choice: 2 GB
    import pytest

    with pytest.raises(ValueError):
        tiles.set_tile_cache_limit_mb(777)


def test_no_lines_between_map_tiles(qapp) -> None:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QColor, QPixmap

    from app.desktop.tiles import TileSource
    from app.desktop.widgets.map_view import MapView

    class Tiles(QObject):
        tile_ready = Signal()
        status_changed = Signal(str)
        source_changed = Signal()

        def __init__(self) -> None:
            super().__init__()
            self.source = TileSource(source_id=1, name="t", url_template="http://127.0.0.1:9/{z}/{x}/{y}.png", max_zoom=19)
            self.pixmap = QPixmap(256, 256)
            self.pixmap.fill(QColor(200, 200, 200))

        def cached(self, z: int, x: int, y: int) -> QPixmap:
            return self.pixmap

        def request(self, z: int, x: int, y: int) -> None:
            pass

    view = MapView(tile_manager=Tiles(), show_zoom_control=False, show_attribution=False)
    view.resize(700, 500)
    for zoom in (12, 21):  # native and super zoom
        for step in range(4):  # centres at fractions of a pixel
            view.set_view(50.0945 + step * 0.00037, 19.6758 + step * 0.00041, zoom)
            image = view.grab().toImage()
            columns = {image.pixelColor(x, y).red() for x in range(700) for y in (100, 400)}
            rows = {image.pixelColor(x, y).red() for y in range(500) for x in (100, 600)}
            assert columns | rows == {200}  # the background never shows between two tiles
    view.deleteLater()


def test_station_info_on_a_click_and_the_station_page_on_a_double_click(qapp, monkeypatch) -> None:
    """The user: nothing on hover, one left click = the info window, a quick double click = the
    station page (the right click keeps drawing the RF path)."""
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from app.desktop.app import configure_tooltips
    from app.desktop.widgets import map_view
    from app.desktop.widgets.map_view import MapMarker, MapView

    QApplication.setEffectEnabled(Qt.UIEffect.UI_AnimateTooltip, True)  # Windows' tooltip animation was on
    configure_tooltips()
    assert not QApplication.isEffectEnabled(Qt.UIEffect.UI_AnimateTooltip)
    assert not QApplication.isEffectEnabled(Qt.UIEffect.UI_FadeTooltip)

    calls: list[tuple[object, ...]] = []

    class Tip:
        text_now = ""

        @classmethod
        def isVisible(cls) -> bool:  # noqa: N802 - Qt API
            return bool(cls.text_now)

        @classmethod
        def text(cls) -> str:
            return cls.text_now

        @classmethod
        def showText(cls, _pos: QPoint, text: str, _widget: object = None, _rect: object = None,  # noqa: N802
                     msec: int = -1) -> None:
            calls.append(("show", text, msec))
            cls.text_now = text

        @classmethod
        def hideText(cls) -> None:  # noqa: N802 - Qt API
            calls.append(("hide",))
            cls.text_now = ""

    monkeypatch.setattr(map_view, "QToolTip", Tip)
    view = MapView(show_zoom_control=False, show_attribution=False)
    view.resize(500, 400)
    view.set_view(50.10, 19.60, 13)
    payload = {"callsign": "SP9XBF-2"}
    view.set_markers([MapMarker("SP9XBF-2", 50.10, 19.60, label="SP9XBF-2", tooltip="<b>SP9XBF-2</b>", payload=payload)])
    view.grab()  # painted once, as on screen
    clicked: list[object] = []
    opened: list[object] = []
    view.marker_clicked.connect(clicked.append)
    view.marker_double_clicked.connect(opened.append)
    on_station, empty = QPointF(250, 200), QPointF(60, 60)  # the station is in the middle of the view

    def mouse(kind: QEvent.Type, pos: QPointF) -> None:
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
        held = Qt.MouseButton.LeftButton if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick) \
            else Qt.MouseButton.NoButton
        QApplication.sendEvent(view, QMouseEvent(kind, pos, view.mapToGlobal(pos.toPoint()).toPointF(), button, held,
                                                 Qt.KeyboardModifier.NoModifier))

    def click(pos: QPointF) -> None:
        mouse(QEvent.Type.MouseButtonPress, pos)
        mouse(QEvent.Type.MouseButtonRelease, pos)

    def double_click(pos: QPointF) -> None:
        click(pos)
        mouse(QEvent.Type.MouseButtonDblClick, pos)
        mouse(QEvent.Type.MouseButtonRelease, pos)

    for _move in range(5):
        mouse(QEvent.Type.MouseMove, on_station)
    assert calls == [] and view.cursor().shape() == Qt.CursorShape.PointingHandCursor  # hover: only the cursor

    click(on_station)
    assert calls == [("show", "<b>SP9XBF-2</b>", map_view.INFO_WINDOW_MS)]
    assert clicked == [payload] and opened == []
    mouse(QEvent.Type.MouseMove, empty)
    view._show_tooltip(QPoint(60, 60), "<b>a track dot</b>")
    assert Tip.text_now == "<b>SP9XBF-2</b>"  # the info stays while the mouse moves on

    calls.clear()
    double_click(on_station)
    assert opened == [payload]  # the station page, once
    assert calls == [("show", "<b>SP9XBF-2</b>", map_view.INFO_WINDOW_MS), ("hide",)]  # the info goes with it

    zoom = view.zoom
    double_click(empty)
    assert view.zoom == zoom + 1 and opened == [payload]  # empty map: zoom in, as before
    view.set_view(50.10, 19.60, 13)
    view.grab()
    click(on_station)
    assert calls[-1] == ("show", "<b>SP9XBF-2</b>", map_view.INFO_WINDOW_MS)  # the next click is a click again

    # Tooltips that do come on hover (groups, track dots) are shown once, not started again on every move.
    Tip.text_now = ""
    calls.clear()
    view._info_text = ""
    for _move in range(20):
        view._show_tooltip(QPoint(10, 10), "<b>3</b>")
    assert calls == [("show", "<b>3</b>", -1)]
    view.deleteLater()


def test_route_map_panel_draws_layers_that_can_be_switched(qapp) -> None:
    from app.desktop.widgets.route_map import RouteMapPanel, edge_width

    opened = []
    nodes = {call: {"call": call, "lat": lat, "lon": lon, "station": None}
             for call, lat, lon in (("SP9TKN", 50.09, 19.68), ("SR9XSR", 50.02, 19.15), ("SP9ABC", 50.1, 18.5),
                                    ("SP9XYZ", 50.17, 18.83))}
    edges = [
        {"layer": "network", "a": "SR9XSR", "b": "SP9TKN", "n": 40, "uncertain": False, "direct": True, "bands": {"APRS 2M": 40}, "last": 0},
        {"layer": "digi", "a": "SP9XYZ", "b": "SP9TKN", "n": 3, "uncertain": False, "direct": False, "bands": {}, "last": 0},
        {"layer": "own", "a": "SP9TKN", "b": "SR9XSR", "n": 1, "uncertain": True, "direct": False, "bands": {}, "last": 0},
    ]
    route_map = {"focus": "SP9TKN", "own": "SP9TKN", "receivers": ["SP9TKN"], "nodes": nodes, "edges": edges,
                 "missing": ["SQ9NOPOS"], "layers": {"network": 1, "digi": 1, "aprsis": 0, "own": 1}}
    panel = RouteMapPanel(route_map, on_open_station=opened.append)
    assert set(panel.layer_checks) == {"own", "digi", "network"}  # no APRS-IS switch without its arrows
    panel.resize(1000, 1400)
    panel.show()
    for _ in range(5):
        qapp.processEvents()
    width = panel.view.width()
    assert width > 900 and panel.view.height() == round(width * 4 / 5)  # the card's width, height = 4/5 of it
    panel.resize(1200, 1600)
    for _ in range(5):
        qapp.processEvents()
    assert panel.view.width() > width and panel.view.height() == round(panel.view.width() * 4 / 5)
    shapes = panel.view.layers["paths"]
    assert len(shapes) == 3 and all(shape.arrows and shape.arrow_at > 0.5 for shape in shapes)
    assert shapes[0].width > shapes[1].width  # 40 frames draw a thicker arrow than 3
    assert shapes[2].dashed  # bridged hop
    assert {marker.key for marker in panel.view.markers} == {"SP9TKN", "SR9XSR", "SP9XYZ"}
    panel.layer_checks["network"].setChecked(False)
    assert len(panel.view.layers["paths"]) == 2
    panel.layer_checks["digi"].setChecked(False)
    assert {marker.key for marker in panel.view.markers} == {"SP9TKN", "SR9XSR"}
    panel._open({"display_callsign": "SR9XSR"})  # double click on a station
    assert opened == ["SR9XSR"]
    assert edge_width(0) < edge_width(10) < edge_width(10_000) == 7.0
