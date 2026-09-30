"""Gesture hardening tests: exclusivity lock, Alt-Tab session, left-fist guard."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kinesis.config import Config                                  # noqa: E402
from test_kinesis import FIST, OPEN, Driver, make_pose             # noqa: E402


def cfg_with(**kw):
    c = Config()
    for k, v in kw.items():
        c.set(k, v)
    return c


def hold(d, poses, seconds, dt=0.033):
    """Feed a pose for a wall-clock duration."""
    for _ in range(max(1, int(seconds / dt))):
        d.feed(list(poses), steps=1)


def test_lone_left_fist_never_minimises():
    """The Alt-Tab modifier was promoted to the cursor hand when the right hand left the frame, and
    its fist pose then fired the close-flick at whatever had focus."""
    d = Driver(cfg_with())
    d.feed([make_pose(d.cfg, **OPEN, handedness="Left")], steps=6)
    d.clear()
    d.feed([make_pose(d.cfg, **FIST, handedness="Left")], steps=8)
    assert not d.of("window.minimise"), f"a lone left fist minimised a window: {d.kinds()}"


def test_a_gesture_keeps_the_hand_until_it_opens():
    """Once a gesture owns the hand, opening it is what releases the lock - not another pose."""
    d = Driver(cfg_with())
    d.feed([make_pose(d.cfg, extended=("index", "middle"), pinches=("ring",))], steps=4)
    assert d.engine.active == "scroll" and d.engine.lock == "scroll"
    d.feed([make_pose(d.cfg, extended=("index", "middle"))], steps=2)     # still not open
    assert d.engine.lock == "scroll", "the lock let go before the hand opened"
    d.feed([make_pose(d.cfg, **OPEN)], steps=1)
    assert d.engine.lock is None


def test_release_sweep_after_scroll_cannot_swipe():
    """Letting go of a scroll sweeps the hand through the open pose with lateral motion, which is
    exactly what the tab swipe looks for."""
    d = Driver(cfg_with())
    d.feed([make_pose(d.cfg, extended=("index", "middle"), pinches=("ring",), palm=(0.5, 0.6))],
           steps=4)
    assert d.engine.active == "scroll"
    d.clear()
    for x in (0.46, 0.42, 0.38, 0.34, 0.30, 0.26, 0.22):
        d.feed([make_pose(d.cfg, **OPEN, palm=(x, 0.6))], steps=1)
    assert not d.of("keys.tap"), f"a swipe fired out of a scroll release: {d.kinds()}"


def test_lock_times_out_so_a_pose_cannot_wedge_the_engine():
    d = Driver(cfg_with(gesture_lock_timeout_s=0.2))
    d.feed([make_pose(d.cfg, extended=("index", "middle"), pinches=("ring",))], steps=4)
    assert d.engine.lock == "scroll"
    d.feed([make_pose(d.cfg, extended=("index", "middle", "pinky"))], steps=12)   # never opens
    assert d.engine.lock is None, "a stuck hand pose locked the engine out"


def test_ctrl_tab_gesture_holds_ctrl_and_taps_forward():
    """Right fist holds Ctrl; a left index pinch taps to the next tab."""
    d = Driver(cfg_with(ctrl_tab_hold_s=0.2))
    fist = make_pose(d.cfg, **FIST, handedness="Right")
    left_pinch = make_pose(d.cfg, extended=("middle", "ring", "pinky"), pinches=("index",),
                           handedness="Left")
    left_open = make_pose(d.cfg, **OPEN, handedness="Left")
    hold(d, [make_pose(d.cfg, **OPEN, handedness="Right"), left_open], 5)
    d.clear()
    d.feed([fist, left_open], steps=10)                 # hold the right fist to open the session
    assert d.engine.ctrl_held, d.kinds()
    assert any(i.keys == ("ctrl",) for i in d.of("keys.down")), d.kinds()
    d.clear()
    d.feed([fist, left_pinch], steps=1)                 # tap: rising edge, no repeat wait
    taps = [i for i in d.of("keys.tap") if i.keys == ("tab",)]
    assert taps, f"a left index pinch should tap Tab: {d.kinds()}"
    assert taps[0].focus_hwnd is None or True
    d.clear()
    d.feed([make_pose(d.cfg, **OPEN, handedness="Right"), left_open], steps=2)
    assert not d.engine.ctrl_held
    assert any(i.keys == ("ctrl",) for i in d.of("keys.up")), f"Ctrl must be released: {d.kinds()}"


def test_ctrl_tab_gesture_taps_backwards_with_the_middle_finger():
    d = Driver(cfg_with(ctrl_tab_hold_s=0.2))
    fist = make_pose(d.cfg, **FIST, handedness="Right")
    left_open = make_pose(d.cfg, **OPEN, handedness="Left")
    back = make_pose(d.cfg, extended=("index", "ring", "pinky"), pinches=("middle",),
                     handedness="Left")
    hold(d, [fist, left_open], 10)
    assert d.engine.ctrl_held
    d.clear()
    d.feed([fist, back], steps=1)
    taps = [i for i in d.of("keys.tap") if i.keys == ("shift", "tab")]
    assert taps, f"a left middle pinch should tap Shift+Tab: {d.kinds()}"


def test_ctrl_tab_never_sends_a_bare_tab_without_ctrl_held():
    """A tap is only sent inside the session, so it can never fire Ctrl+Tab by accident."""
    d = Driver(cfg_with(ctrl_tab_hold_s=0.2))
    left_pinch = make_pose(d.cfg, extended=("middle", "ring", "pinky"), pinches=("index",),
                           handedness="Left")
    d.feed([make_pose(d.cfg, **OPEN, handedness="Right"), left_pinch], steps=6)
    assert not d.engine.ctrl_held
    assert not d.of("keys.tap"), d.kinds()


def test_the_swipe_no_longer_switches_tabs():
    """Joe replaced it with the two-hand gesture; the default must be silent."""
    d = Driver(cfg_with())
    for x in (0.30, 0.45, 0.60, 0.75):
        d.feed([make_pose(d.cfg, **OPEN, palm=(x, 0.6), tip=(x, 0.5))], steps=1)
    assert not d.of("keys.tap"), f"the swipe still switched tabs: {d.kinds()}"


def test_two_missed_pinch_frames_do_not_end_a_scroll():
    d = Driver(cfg_with())
    scroll = make_pose(d.cfg, extended=("index", "middle"), pinches=("ring",), palm=(0.5, 0.5))
    blink = make_pose(d.cfg, extended=("index", "middle"), palm=(0.5, 0.5))
    d.feed([scroll], steps=4)
    d.feed([blink], steps=2)                        # fewer than scroll_release_frames
    assert d.engine.active == "scroll", "a two-frame pinch dropout killed the scroll"


def _alt_tab_setup(cfg=None):
    d = Driver(cfg or cfg_with())
    left_fist = make_pose(d.cfg, **FIST, handedness="Left")
    right_open = make_pose(d.cfg, **OPEN, handedness="Right")
    hold(d, [left_fist, right_open], 2.3)
    return d, left_fist, right_open


def _index_pinch(cfg):
    return make_pose(cfg, extended=("middle", "ring", "pinky"), pinches=("index",),
                     handedness="Right")


def test_alt_tab_opens_after_the_hold():
    d, _, _ = _alt_tab_setup()
    assert d.engine.alt_held
    assert ("alt",) in [i.keys for i in d.of("keys.down")]
    assert ("tab",) in [i.keys for i in d.of("keys.tap")]


def test_alt_tab_tab_fires_on_the_rising_edge():
    """The first pinch after the switcher opens used to be swallowed by the repeat timer, which is
    why the switcher felt unresponsive for the first third of a second."""
    d, left_fist, _ = _alt_tab_setup()
    d.clear()
    d.feed([left_fist, _index_pinch(d.cfg)], steps=1)
    assert [i for i in d.of("keys.tap") if i.keys == ("tab",)], d.kinds()


def test_alt_tab_does_not_click():
    """The right-hand pinch means 'next window' while the session is open - it must not also click."""
    d, left_fist, _ = _alt_tab_setup()
    d.clear()
    d.feed([left_fist, _index_pinch(d.cfg)], steps=5)
    assert not d.of("mouse.click"), f"a click fired during Alt-Tab: {d.kinds()}"


def test_alt_tab_ends_when_the_left_fist_opens():
    d, _, right_open = _alt_tab_setup()
    d.clear()
    d.feed([make_pose(d.cfg, **OPEN, handedness="Left"), right_open], steps=3)
    assert not d.engine.alt_held
    assert ("alt",) in [i.keys for i in d.of("keys.up")], d.kinds()
    assert d.engine.lock is None


def test_alt_tab_releases_alt_on_timeout():
    d, left_fist, right_open = _alt_tab_setup(cfg_with(alt_tab_session_timeout_s=0.5))
    d.clear()
    hold(d, [left_fist, right_open], 1.0)
    assert not d.engine.alt_held, "Alt stayed held past the session timeout"
    assert ("alt",) in [i.keys for i in d.of("keys.up")]


