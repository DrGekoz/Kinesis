"""Dictation focus: the click-into-the-field decision, and the ordering before Ctrl+Space."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis import focus_target as ft                               # noqa: E402
from kinesis.actions import ActionRunner                             # noqa: E402
from kinesis.config import Config                                    # noqa: E402
from kinesis.gestures import Intent                                  # noqa: E402


def hit(editable=None, ctype=0, cls="", focused=False, source="uia", reason=""):
    return ft.Hit(x=100, y=100, source=source, control_type=ctype, class_name=cls,
                  editable=editable, focused=focused, reason=reason)


def test_native_edit_control_is_clicked():
    ok, why = ft.decide("auto", hit(editable=True, ctype=ft.CT_EDIT, cls="Edit",
                                    reason="controlType 50004"))
    assert ok and "50004" in why


def test_a_button_or_link_is_never_clicked():
    for ctype in (ft.CT_BUTTON, ft.CT_HYPERLINK, ft.CT_IMAGE, ft.CT_TABITEM):
        ok, why = ft.decide("auto", hit(editable=False, ctype=ctype))
        assert not ok, f"controlType {ctype} should not be clicked ({why})"


def test_an_already_focused_field_is_left_alone():
    """Clicking a focused field would move the caret; the hotkey alone is enough."""
    ok, _ = ft.decide("auto", hit(editable=True, ctype=ft.CT_EDIT, focused=True))
    assert not ok


def test_browser_pages_are_clicked_because_they_cannot_be_read():
    """Chromium exposes no editable nodes to UIA - the page comes back as a bare pane."""
    ok, why = ft.decide("auto", hit(editable=None, ctype=ft.CT_PANE,
                                    cls="Chrome_RenderWidgetHostHWND"))
    assert ok and "web" in why


def test_a_native_text_box_class_is_clicked_without_uia():
    """With UIA unavailable the window class is the only signal - a real EDIT class still counts."""
    ok, why = ft.decide("auto", hit(editable=None, cls="Edit", source="class"))
    assert ok and "Edit" in why


def test_conservative_mode_refuses_the_unknown():
    ok, why = ft.decide("uia", hit(editable=None, ctype=ft.CT_PANE))
    assert not ok and "uia" in why


def test_always_mode_clicks_anything():
    assert ft.decide("always", hit())[0]
    assert ft.decide("always", hit(editable=False, ctype=ft.CT_BUTTON))[0]


def test_shell_surfaces_are_not_clicked():
    """The desktop and the taskbar read as bare panes; clicking them does nothing useful."""
    for cls in ("Progman", "WorkerW", "Taskbar.TaskbarFrameAutomationPeer"):
        ok, why = ft.decide("auto", hit(editable=None, ctype=ft.CT_PANE, cls=cls))
        assert not ok, f"{cls} should not be clicked ({why})"


def test_off_mode_and_no_hit():
    assert not ft.decide("off", hit(editable=True, ctype=ft.CT_EDIT))[0]
    assert not ft.decide("auto", None)[0]


def test_read_only_field_is_not_clicked():
    ok, why = ft.decide("auto", hit(editable=False, ctype=ft.CT_TEXT, cls="Static"))
    assert not ok


def _app(mode="auto", settle=40.0):
    from kinesis.app import KinesisApp
    cfg = Config()
    cfg.set("vcam_enabled", False)
    cfg.set("desktop_overlay", False)
    cfg.set("gaze_enabled", False)
    cfg.set("ptt_focus_mode", mode)
    cfg.set("ptt_focus_settle_ms", settle)
    return KinesisApp(cfg, preview=False)


def test_app_inserts_a_click_and_pause_before_the_dictation_hotkey(monkeypatch):
    app = _app()
    monkeypatch.setattr(ft, "hit_test", lambda x, y: hit(editable=True, ctype=ft.CT_EDIT, cls="Edit"))
    gaze = SimpleNamespace(x=640.0, y=300.0, valid=True)
    intents = [Intent("cursor.move", x=10, y=10), Intent("keys.down", keys=("ctrl", "space"))]

    app._prepare_ptt_focus(intents, gaze, 5.0)

    kinds = [i.kind for i in intents]
    assert kinds == ["cursor.move", "mouse.click", "pause", "keys.down"], kinds
    click = intents[1]
    assert click.warp == (640.0, 300.0)
    assert intents[2].amount == 40, "the pause should come from ptt_focus_settle_ms"
    assert "dictation focus" in click.note


def test_app_does_not_click_when_gaze_is_uncalibrated(monkeypatch):
    app = _app()
    called = []
    monkeypatch.setattr(ft, "hit_test", lambda x, y: called.append(1))
    intents = [Intent("keys.down", keys=("ctrl", "space"))]
    app._prepare_ptt_focus(intents, SimpleNamespace(x=1.0, y=1.0, valid=False), 5.0)
    assert [i.kind for i in intents] == ["keys.down"] and not called


def test_app_leaves_other_hotkeys_alone(monkeypatch):
    app = _app()
    monkeypatch.setattr(ft, "hit_test", lambda x, y: hit(editable=True, ctype=ft.CT_EDIT))
    intents = [Intent("keys.tap", keys=("tab",))]
    app._prepare_ptt_focus(intents, SimpleNamespace(x=1.0, y=1.0, valid=True), 5.0)
    assert [i.kind for i in intents] == ["keys.tap"]


def test_pause_intent_sleeps_for_the_configured_time(monkeypatch):
    import time as _time
    slept = []
    runner = ActionRunner(Config(), [], dry=False)
    monkeypatch.setattr(_time, "sleep", lambda s: slept.append(round(s, 4)))
    runner.execute([Intent("pause", amount=40)])
    assert slept == [0.04], slept


def test_pause_is_skipped_in_dry_run(monkeypatch):
    import time as _time
    slept = []
    runner = ActionRunner(Config(), [], dry=True)
    monkeypatch.setattr(_time, "sleep", lambda s: slept.append(s))
    runner.execute([Intent("pause", amount=250)])
    assert slept == []
