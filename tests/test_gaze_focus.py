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


# ------------------------------------------------------- the steadiness gate (v1.20.0)
def _gaze(x, y, valid=True, blink=False, rejected=False):
    return SimpleNamespace(x=x, y=y, valid=valid, blink=blink, rejected=rejected)


def test_a_sweeping_gaze_point_cannot_focus_a_window(app):
    """The reported bug: "my windows started flying everywhere".

    The dwell alone was 0.15 s, so it completed while the point was still travelling and focus landed
    on whatever the sweep happened to cross. The point must be STEADY before the dwell may start.
    """
    instance, focused = app
    instance.cfg.set("gaze_focus_stable_s", 0.25)
    instance.cfg.set("gaze_focus_stable_px", 40.0)
    t = 1000.0
    # a gaze point sliding steadily across the screen, one call per frame
    for i in range(60):
        t += 0.033
        instance._gaze_focus(4242, t, _gaze(1000.0 + i * 120.0, 500.0))
    assert focused == [], (
        f"a moving gaze point focused a window anyway: {focused}"
    )


def test_a_settled_gaze_point_still_focuses(app):
    """The steadiness gate must not break the feature it protects."""
    instance, focused = app
    instance.cfg.set("gaze_focus_stable_s", 0.25)
    instance.cfg.set("gaze_focus_stable_px", 40.0)
    # The look has to be held for the dwell (0.7 s) PLUS the steadiness gate (0.25 s), and the two
    # run SEQUENTIALLY, not together: the gate only starts the dwell clock once the point has been
    # still, so the worst case is 0.25 + 0.7 = 0.95 s, plus one frame of slack. Measured by tracing
    # the timers: `dwell_since` freezes while the steadiness gate is unsatisfied, which is why an
    # earlier 30-frame version stopped 10 ms short of the dwell and never fired.
    t = 1000.0
    for _ in range(40):
        t += 0.033
        instance._gaze_focus(4242, t, _gaze(1000.0, 500.0))
    assert focused == [4242], f"a steady look did not focus: {focused}"


def test_a_blink_cannot_complete_a_dwell(app):
    instance, focused = app
    instance.cfg.set("gaze_focus_stable_s", 0.0)      # isolate the blink rule
    t = 1000.0
    for _ in range(10):
        t += 0.033
        instance._gaze_focus(4242, t, _gaze(1000.0, 500.0))
    t += 0.033
    instance._gaze_focus(4242, t, _gaze(1000.0, 500.0, valid=False, blink=True))
    t += 0.8
    instance._gaze_focus(4242, t, _gaze(1000.0, 500.0))
    assert focused == [], "a blink let a dwell through"


def test_a_rejected_sample_cannot_complete_a_dwell(app):
    """A discarded sample is not a measurement, so it cannot aim a window at anything."""
    instance, focused = app
    instance.cfg.set("gaze_focus_stable_s", 0.0)
    t = 1000.0
    for _ in range(10):
        t += 0.033
        instance._gaze_focus(4242, t, _gaze(1000.0, 500.0))
    t += 0.033
    instance._gaze_focus(4242, t, _gaze(1000.0, 500.0, rejected=True))
    t += 0.8
    instance._gaze_focus(4242, t, _gaze(1000.0, 500.0))
    assert focused == [], "a rejected sample let a dwell through"


def test_after_focusing_the_point_must_leave_and_come_back(app):
    """Otherwise one long look keeps re-focusing after the user has moved on inside that window."""
    instance, focused = app
    instance.cfg.set("gaze_focus_stable_s", 0.0)
    t = 1000.0
    for _ in range(10):
        t += 0.033
        instance._gaze_focus(4242, t, _gaze(1000.0, 500.0))
    t += 0.8
    instance._gaze_focus(4242, t, _gaze(1000.0, 500.0))
    assert focused == [4242]
    # still looking at the same window, dwell runs again
    t += 0.033
    instance._gaze_focus(4242, t, _gaze(1000.0, 500.0))
    t += 0.8
    instance._gaze_focus(4242, t, _gaze(1000.0, 500.0))
    assert focused == [4242], (
        f"one continuous look re-focused the same window: {focused}"
    )
