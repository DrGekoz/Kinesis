"""Gaze focus tests: looking at a window for a moment makes it the focused window."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis import winapi as w          # noqa: E402
from kinesis.app import KinesisApp       # noqa: E402
from kinesis.config import Config        # noqa: E402


@pytest.fixture
def app(monkeypatch):
    cfg = Config()
    for key, value in (("vcam_enabled", False), ("desktop_overlay", False),
                       ("gaze_enabled", False), ("gaze_focus_enabled", True),
                       ("gaze_focus_dwell_s", 0.7), ("gaze_focus_cooldown_s", 1.5)):
        cfg.set(key, value)
    instance = KinesisApp(cfg, preview=False)
    focused = []
    monkeypatch.setattr(w, "focus_and_verify",
                        lambda hwnd, allow_alt_trick=True: (focused.append(hwnd), True)[1])
    monkeypatch.setattr(w, "own_process_id", lambda: 1)
    monkeypatch.setattr(w, "window_info", lambda hwnd, monitors: SimpleNamespace(
        process_id=999, is_fullscreen=False, title=f"window {hwnd}"))
    monkeypatch.setattr(w.user32, "GetForegroundWindow", lambda: 0)
    return instance, focused


def test_no_focus_before_the_dwell_elapses(app):
    instance, focused = app
    instance._gaze_focus(4242, 1000.0)          # first sighting starts the dwell
    instance._gaze_focus(4242, 1000.3)          # still inside the dwell
    assert focused == [], f"focused too early: {focused}"


def test_focuses_after_the_dwell(app):
    instance, focused = app
    instance._gaze_focus(4242, 1000.0)
    instance._gaze_focus(4242, 1000.8)          # past gaze_focus_dwell_s
    assert focused == [4242]


def test_changing_window_restarts_the_dwell(app):
    instance, focused = app
    instance._gaze_focus(4242, 1000.0)
    instance._gaze_focus(7777, 1001.0)          # looked elsewhere: dwell restarts
    assert focused == []
    instance._gaze_focus(7777, 1001.8)
    assert focused == [7777]


def test_cooldown_stops_it_thrashing(app):
    instance, focused = app
    instance._gaze_focus(4242, 1000.0)
    instance._gaze_focus(4242, 1000.8)
    instance._gaze_focus(4242, 1001.0)          # inside the cooldown after focusing
    assert focused == [4242], f"focused twice back to back: {focused}"


def test_already_focused_window_is_not_refocused(app, monkeypatch):
    instance, focused = app
    monkeypatch.setattr(w.user32, "GetForegroundWindow", lambda: 4242)
    instance._gaze_focus(4242, 1000.0)
    instance._gaze_focus(4242, 1000.9)
    assert focused == [], "refocused a window that already had focus"


def test_no_target_clears_the_dwell(app):
    instance, focused = app
    instance._gaze_focus(4242, 1000.0)
    instance._gaze_focus(None, 1000.5)          # gaze left the screen
    instance._gaze_focus(4242, 1000.6)          # so this is a fresh dwell, not a continuation
    assert focused == [], "dwell survived losing the gaze target"
