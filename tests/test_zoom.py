"""Two-hand pinch zoom: both index pinches, apart to zoom in, together to zoom out."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kinesis.config import Config                               # noqa: E402
from kinesis.gestures import GestureEngine, Intent               # noqa: E402
from test_kinesis import Driver, make_pose, OPEN                 # noqa: E402

DT = 1.0 / 15.0
W, H = 640.0, 480.0


def cfg_with(**kw) -> Config:
    cfg = Config()
    cfg.set("frame_width", int(W))
    cfg.set("frame_height", int(H))
    for k, v in kw.items():
        cfg.set(k, v)
    return cfg


def two_pinch(cfg, span_px: float):
    """Both hands pinching index+thumb, `span_px` apart horizontally."""
    left_tip = (W * 0.5 - span_px / 2.0) / W
    right_tip = (W * 0.5 + span_px / 2.0) / W
    left = make_pose(cfg, extended=("middle", "ring", "pinky"), pinches=("index",),
                     tip=(left_tip, 0.5), handedness="Left")
    right = make_pose(cfg, extended=("middle", "ring", "pinky"), pinches=("index",),
                      tip=(right_tip, 0.5), handedness="Right")
    return [left, right]


def start_zoom(d, span=200.0):
    d.feed(two_pinch(d.cfg, span), steps=3)
    assert d.engine._zoom_active, "the two-hand pinch did not start a zoom"
    return d


def taps(d):
    return [i for i in d.of("keys.tap")]


def test_hands_apart_zooms_in():
    d = Driver(cfg_with())
    start_zoom(d, 200.0)
    d.clear()
    d.feed(two_pinch(d.cfg, 230.0), steps=1)         # +30px, step is 22px
    assert taps(d), d.kinds()
    assert all(i.keys == ("ctrl", "=") for i in taps(d)), [i.keys for i in taps(d)]


def test_hands_together_zooms_out():
    d = Driver(cfg_with())
    start_zoom(d, 300.0)
    d.clear()
    d.feed(two_pinch(d.cfg, 270.0), steps=1)         # -30px
    assert taps(d), d.kinds()
    assert all(i.keys == ("ctrl", "-") for i in taps(d)), [i.keys for i in taps(d)]


def test_jitter_and_short_moves_do_not_zoom():
    d = Driver(cfg_with())
    start_zoom(d, 200.0)
    d.clear()
    d.feed(two_pinch(d.cfg, 202.0), steps=3)         # inside the deadband
    assert not taps(d), f"jitter zoomed: {d.kinds()}"


def test_a_fast_sweep_does_not_flood_the_keys():
    d = Driver(cfg_with())
    start_zoom(d, 200.0)
    d.clear()
    d.feed(two_pinch(d.cfg, 1400.0), steps=1)        # 1200px in one frame
    assert len(taps(d)) <= int(d.cfg["zoom_max_steps_per_frame"]), d.kinds()


def test_one_pinch_still_clicks_and_does_not_zoom():
    """The right hand's index pinch is the click. It must not be eaten by the zoom."""
    d = Driver(cfg_with())
    single = [make_pose(d.cfg, extended=("index", "middle"), pinches=("index",),
                        handedness="Right")]
    d.feed(single, steps=4)
    assert not d.engine._zoom_active, "one hand cannot be a two-hand pinch"
    assert not taps(d), f"a single pinch zoomed: {d.kinds()}"
    d.feed([make_pose(d.cfg, **OPEN, handedness="Right")], steps=8)
    assert d.of("mouse.click"), f"the click stopped working: {d.kinds()}"


def test_zoom_suppresses_the_click_entirely():
    d = Driver(cfg_with())
    start_zoom(d, 200.0)
    d.clear()
    d.feed(two_pinch(d.cfg, 200.0), steps=3)
    assert not d.of("mouse.click"), f"both pinches still clicked: {d.kinds()}"
    assert not [i for i in d.of("cursor.move")], "the cursor moved during a zoom"


def test_zoom_owns_the_hand():
    d = Driver(cfg_with())
    start_zoom(d, 200.0)
    assert d.engine.lock == "zoom"
    # a drag or a scroll must not be readable while the zoom owns the hand
    d.clear()
    d.feed(two_pinch(d.cfg, 240.0), steps=2)
    assert not d.of("mouse.down"), f"a drag started mid-zoom: {d.kinds()}"
    assert d.engine.active is None


def test_zoom_survives_a_dropped_frame_and_ends_cleanly():
    d = Driver(cfg_with())
    start_zoom(d, 200.0)
    d.clear()
    d.feed(two_pinch(d.cfg, 200.0)[:1], steps=2)      # one hand lost for two frames
    assert d.engine._zoom_active, "a two-frame dropout ended the zoom"
    d.feed(two_pinch(d.cfg, 200.0)[:1], steps=int(d.cfg["scroll_release_frames"]) + 1)
    assert not d.engine._zoom_active
    assert d.engine.lock is None, "the zoom held the lock after ending"


def test_zoom_keys_are_configurable():
    d = Driver(cfg_with(zoom_keys_in=["ctrl", "plus"], zoom_keys_out=["ctrl", "minus"]))
    start_zoom(d, 200.0)
    d.clear()
    d.feed(two_pinch(d.cfg, 240.0), steps=1)
    assert taps(d) and all(i.keys == ("ctrl", "plus") for i in taps(d)), d.kinds()


def test_zoom_can_be_disabled():
    d = Driver(cfg_with(zoom_pinch_enabled=False))
    d.feed(two_pinch(d.cfg, 200.0), steps=3)
    assert not d.engine._zoom_active
    d.clear()
    d.feed(two_pinch(d.cfg, 260.0), steps=1)
    assert not taps(d), f"disabled zoom still fired keys: {d.kinds()}"


def test_release_all_clears_a_zoom_and_does_not_forget_the_ptt_keys():
    """The zoom taps Ctrl+= rather than holding it, so there is no key of its own to release - but
    release_all must still clear it, and must still release the dictation keys."""
    d = Driver(cfg_with())
    start_zoom(d, 200.0)
    d.engine.ptt_held = True                    # pretend dictation is mid-hold
    out = d.engine.release_all(now=10.0, reason="test")
    kinds = {(i.kind, tuple(i.keys or ())) for i in out}
    assert ("keys.up", tuple(d.cfg["ptt_keys"])) in kinds, \
        f"the dictation keys were not released: {kinds}"
    assert not d.engine._zoom_active, "release_all left the zoom running"
    assert d.engine.lock is None
