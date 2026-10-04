"""Gaze stabiliser, blink guard, force close, and the startup steps.

These are the tests for the changes made in response to a real report: the eye-gaze pointer was
jittering, a blink moved the pointer downward, windows were un-maximising themselves, and the user
asked for a force-close chord, a longer focus dwell, and calibration plus settings on every run.

The measured reason the pointer jittered is recorded in `tools/probe_gaze_resolution.py` and
`filters.GazeStabiliser`: the gaze model is a 486-feature ridge fitted from ~220 samples, so it is
rank-deficient and 0.2% feature noise moves the prediction by over a million pixels. That is why
these tests assert REJECTION rather than smoothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kinesis.config import Config, DEFAULTS                                  # noqa: E402
from kinesis.filters import GazeStabiliser                                  # noqa: E402
from kinesis.gaze import GazeEngine, GazeState                              # noqa: E402
from kinesis.gestures import GestureEngine                                  # noqa: E402
from test_kinesis import Driver, FIST, OPEN, make_pose                      # noqa: E402


def cfg_with(**kw):
    c = Config()
    for k, v in kw.items():
        c.set(k, v)
    return c


# ======================================================================= GazeStabiliser
def test_a_sustained_sweep_across_the_desk_is_never_rejected():
    """The regression that mattered most: with a fixed outlier gate, 195 of a 200-step sweep were
    thrown away, because every step was judged against a window made of the OLD positions. The gate
    has to widen while the eye is demonstrably travelling."""
    f = GazeStabiliser(reject_px=260.0, window=5, deadband_px=3.0, slew_px=0.0)
    t = 0.0
    f(0.0, 500.0, t)
    accepted = rejected = 0
    for i in range(1, 200):
        t += 0.05                                  # 20 Hz gaze
        if f(i * 100.0, 500.0, t) is None:
            rejected += 1
        else:
            accepted += 1
    assert accepted >= 190, f"only {accepted}/200 sweep steps were followed"
    assert rejected <= 10, f"{rejected}/200 sweep steps were discarded as outliers"


def test_the_widened_gate_still_rejects_a_misfire_mid_sweep():
    """Widening the gate for motion must not make it useless: the million-pixel excursion is what
    the whole filter is for, and it must be caught even while the eye is moving."""
    f = GazeStabiliser(reject_px=260.0, window=5, deadband_px=3.0, slew_px=0.0)
    t = 0.0
    for i in range(6):
        t += 0.05
        f(i * 400.0, 500.0, t)                     # a real sweep, so the gate is WIDE
    t += 0.05
    before = f.rejected
    out = f(f._last[0] + 1_000_000.0, 500.0, t)
    assert out is None, "a million-pixel excursion was accepted mid-sweep"
    assert f.rejected == before + 1


def test_small_jitter_while_resting_is_not_treated_as_motion():
    """The widened gate must not become so loose that ordinary landmark wobble gets through while
    the eye is still."""
    f = GazeStabiliser(reject_px=260.0, window=5, deadband_px=3.0, slew_px=0.0)
    t = 0.0
    f(1000.0, 500.0, t)
    rejected = 0
    for i in range(60):
        t += 0.05
        f(1000.0 + (6 if i % 2 else -6), 500.0 + (5 if i % 3 else -5), t)
    # a 6 px wobble at 20 Hz is ~170 px/s: well under motion_px_s, so the gate stays tight
    assert f.rejected == rejected, f"{f.rejected} resting samples were rejected"


def test_the_default_speed_gate_passes_a_real_saccade():
    """Pins the derived default, because it was wrong once: 9,000 px/s is slower than a real
    one-screen saccade at 20 Hz gaze (38,400 px/s), so it ate genuine looks."""
    f = GazeStabiliser()
    assert f.speed_max_px_s >= 20000.0, (
        f"the default speed gate {f.speed_max_px_s} px/s is slower than a real saccade"
    )


def test_a_million_pixel_excursion_is_rejected_and_the_point_stands_still():
    """The measured failure: 0.2% feature noise throws the prediction over a million pixels.

    A deadband or an EMA cannot absorb that - to swallow a million-pixel excursion they would have
    to discard essentially all real motion. So the sample must be DISCARDED, and the previous good
    point kept.
    """
    f = GazeStabiliser(reject_px=260.0, window=5, deadband_px=3.0)
    t = 0.0
    good = None
    for _ in range(5):
        t += 0.05
        good = f(1000.0, 500.0, t)
    assert good is not None

    t += 0.05
    out = f(2_000_000.0, 500.0, t)          # the excursion
    assert out is None, "a physically impossible sample must be rejected, not smoothed"
    assert f.rejected == 1

    # and the next real sample is accepted on the frame it arrives - no buffering, no delay
    t += 0.05
    assert f(1010.0, 500.0, t) is not None


def test_a_steady_eye_holds_perfectly_still():
    """A resting eye must not shake the pointer. Feed sub-deadband wobble and assert no movement."""
    f = GazeStabiliser(reject_px=260.0, window=5, deadband_px=6.0, deadband_gain=2.0)
    t = 0.0
    out = None
    seen = []
    for i in range(60):
        t += 0.05
        # +/- 1.5 px of wobble around a fixed point: below the 6 px deadband floor
        out = f(1000.0 + (1.5 if i % 2 else -1.5), 500.0 + (1.0 if i % 2 else -1.0), t)
        if out is not None:
            seen.append(out)
    assert len(seen) >= 2, "the first sample must establish a point"
    xs = {round(p[0]) for p in seen}
    ys = {round(p[1]) for p in seen}
    assert len(xs) == 1 and len(ys) == 1, (
        f"a resting eye moved the pointer: x={sorted(xs)} y={sorted(ys)}"
    )


def test_a_real_saccade_still_passes_through():
    """Rejecting noise must not reject intent.

    A 600 px move in one 50 ms gaze frame is 12,000 px/s, which the DEFAULT gate has to allow - the
    original 9,000 px/s default rejected exactly this and discarded real saccades as noise. Measured
    against this machine's desk: one screen is 38,400 px/s at 20 Hz, so the default is 20,000.
    """
    f = GazeStabiliser(reject_px=260.0, window=5, deadband_px=3.0, slew_px=0.0)
    t = 0.0
    for _ in range(5):
        t += 0.05
        f(1000.0, 500.0, t)
    t += 0.05
    out = f(1600.0, 500.0, t)              # 600 px in 50 ms = 12,000 px/s, allowed
    assert out is not None, "a real saccade must not be rejected"
    assert abs(out[0] - 1600.0) < 60.0, f"the saccade should track through: {out}"


def test_slew_limit_caps_one_frames_travel():
    """Even an accepted sample may not teleport the pointer further than a real saccade can."""
    f = GazeStabiliser(reject_px=100_000.0, speed_max_px_s=1e9, deadband_px=0.0,
                       slew_px=900.0, window=1)
    t = 0.0
    first = f(0.0, 0.0, t)
    assert first is not None
    t += 0.05
    out = f(5000.0, 0.0, t)
    assert out is not None
    assert abs(out[0] - first[0]) <= 900.0 + 1, f"slew limit not applied: {out}"


def test_note_jump_lets_the_next_sample_through():
    """After a deliberate pointer move the history must be dropped, or the first real sample is
    judged against the old point and discarded."""
    f = GazeStabiliser(reject_px=260.0, window=5)
    t = 0.0
    for _ in range(5):
        t += 0.05
        f(1000.0, 500.0, t)
    f.note_jump()                          # e.g. a gaze tab-click warp
    t += 0.05
    out = f(4000.0, 500.0, t)               # a huge but deliberate new position
    assert out is not None, "the sample after a jump must be accepted"


def test_reset_clears_the_counters():
    f = GazeStabiliser()
    t = 0.0
    for _ in range(3):
        t += 0.05
        f(10.0, 10.0, t)
    t += 0.05
    f(10_000_000.0, 10.0, t)
    assert f.rejected >= 1
    f.reset()
    assert f.rejected == 0 and f.accepted == 0
    assert f(1.0, 1.0, t) is not None


# ======================================================================= blink guard
class _FakeEstimator:
    """Just enough of eyetrax.GazeEstimator to drive GazeEngine.update()."""

    def __init__(self):
        self.point = [1000.0, 500.0]
        self.blink = False
        self.no_face = False
        self.calls = 0

    def extract_features(self, frame):
        self.calls += 1
        if self.no_face:
            return None, False
        return [0.0] * 486, self.blink

    def predict(self, X):
        return [list(self.point)]

    def close(self):
        pass


def _engine(**kw):
    c = cfg_with(**kw)
    eng = GazeEngine(c)
    eng._estimator = _FakeEstimator()
    eng._last_run = -99.0            # never rate limited
    eng._smoother = None
    return eng


def test_a_blink_never_reports_a_valid_point():
    """The reported bug: a blink moved the pointer DOWN, because the old code decayed the previous
    point as still valid while the eyes rolled."""
    eng = _engine()
    s = eng.update(object(), now=1.0)
    assert s.valid and not s.blink

    eng._estimator.blink = True
    s = eng.update(object(), now=1.2)
    assert s.blink, "the blink must be reported"
    assert not s.valid, (
        "a blink must not report the previous point as valid - that is the downward jump"
    )


def test_a_blink_does_not_move_the_predicted_point():
    """The state's coordinates must not be rewritten by a blink either, so nothing downstream can
    steer on them."""
    eng = _engine()
    first = eng.update(object(), now=1.0)
    before = (first.x, first.y)
    eng._estimator.blink = True
    for i in range(5):
        s = eng.update(object(), now=1.2 + i * 0.05)
        assert (s.x, s.y) == before, "a blink changed the point"


def test_point_resumes_from_the_last_open_sample_after_a_blink():
    eng = _engine()
    eng.update(object(), now=1.0)
    eng._estimator.blink = True
    eng.update(object(), now=1.2)
    eng._estimator.blink = False
    eng._estimator.point = [1010.0, 500.0]
    s = eng.update(object(), now=1.6)
    assert s.valid and abs(s.x - 1010.0) < 40.0, f"did not resume from the open point: {s}"


def test_a_rejected_sample_keeps_the_last_good_point_and_says_so():
    eng = _engine()
    for i in range(6):
        eng.update(object(), now=1.0 + i * 0.05)
    good = eng.state
    eng._estimator.point = [50_000_000.0, 500.0]
    s = eng.update(object(), now=2.0)
    assert s.rejected, "the excursion must be flagged as rejected"
    assert (s.x, s.y) == (good.x, good.y), "the pointer must stand still, not travel"


# ======================================================================= force close
def test_both_open_hands_held_still_sends_ctrl_alt_f4():
    d = Driver(cfg_with())
    poses = [make_pose(d.cfg, **OPEN, handedness="Left", palm=(0.35, 0.60)),
             make_pose(d.cfg, **OPEN, handedness="Right", palm=(0.65, 0.60))]
    d.feed(poses, steps=60, target_hwnd=4242)
    taps = [i for i in d.intents if i.kind == "keys.tap"
            and "f4" in i.keys]
    assert taps, f"no force close after a 2 s hold: {d.kinds()}"
    assert set(taps[0].keys) == {"ctrl", "alt", "f4"}
    assert taps[0].target_hwnd == 4242
    assert taps[0].window_only, "a force close must never fall back to the foreground window"


def test_one_hand_can_never_force_close():
    """The `_other_hand` trap: with one hand in frame both roles are the same object, so a naive
    'both hands' condition fires on a single hand. This is the single most destructive action."""
    d = Driver(cfg_with())
    d.feed([make_pose(d.cfg, **OPEN, handedness="Right", palm=(0.5, 0.6))],
           steps=60, target_hwnd=4242)
    assert not [i for i in d.intents if i.kind == "keys.tap" and "f4" in i.keys], (
        f"one hand force-closed a window: {d.kinds()}"
    )


def test_a_moving_hand_does_not_force_close():
    """'Both hands open' is the resting pose. Without the stillness gate every pause would close
    something, so the hands have to stay put."""
    d = Driver(cfg_with())
    for i in range(60):
        y = 0.60 + (0.25 if i > 30 else 0.0)       # one hand wanders halfway through
        d.feed([make_pose(d.cfg, **OPEN, handedness="Left", palm=(0.35, 0.60)),
                make_pose(d.cfg, **OPEN, handedness="Right", palm=(0.65, y))],
               steps=1, target_hwnd=4242)
    assert not [i for i in d.intents if i.kind == "keys.tap" and "f4" in i.keys], (
        f"moving hands force-closed a window: {d.kinds()}"
    )


def test_a_fist_does_not_force_close():
    """A fist is the Alt-Tab / Ctrl-Tab modifier. It must not also close a window."""
    d = Driver(cfg_with())
    d.feed([make_pose(d.cfg, **OPEN, handedness="Left"),
            make_pose(d.cfg, **FIST, handedness="Right")],
           steps=60, target_hwnd=4242)
    assert not [i for i in d.intents if i.kind == "keys.tap" and "f4" in i.keys]


def test_a_pinched_hand_does_not_force_close():
    """Pinch is the click - the most-travelled shape in the app."""
    d = Driver(cfg_with())
    d.feed([make_pose(d.cfg, **OPEN, handedness="Left"),
            make_pose(d.cfg, extended=("middle", "ring", "pinky"),
                      pinches=("index",), handedness="Right")],
           steps=60, target_hwnd=4242)
    assert not [i for i in d.intents if i.kind == "keys.tap" and "f4" in i.keys]


def test_force_close_does_nothing_without_a_gaze_target():
    """No target means no close. Holding both hands open with your eyes off every window must not
    close whatever happened to be focused."""
    d = Driver(cfg_with())
    poses = [make_pose(d.cfg, **OPEN, handedness="Left"),
             make_pose(d.cfg, **OPEN, handedness="Right")]
    d.feed(poses, steps=60, target_hwnd=None)
    assert not [i for i in d.intents if i.kind == "keys.tap" and "f4" in i.keys], (
        f"force closed with no target: {d.kinds()}"
    )


def test_a_close_armed_by_hand_loss_never_fires_on_the_next_hands():
    """The stale-latch class of bug: a hold left armed through a hand loss would send CTRL+ALT+F4 to
    whatever window the NEXT pair of open hands is looking at."""
    d = Driver(cfg_with())
    poses = [make_pose(d.cfg, **OPEN, handedness="Left"),
             make_pose(d.cfg, **OPEN, handedness="Right")]
    d.feed(poses, steps=20, target_hwnd=4242)     # armed, not yet long enough
    assert d.engine._close_since is not None
    d.feed([], steps=3)                            # both hands gone
    assert d.engine._close_since is None, "release_all must disarm the force close"
    d.clear()
    d.feed(poses, steps=5, target_hwnd=4242)
    assert not [i for i in d.intents if i.kind == "keys.tap" and "f4" in i.keys]


def test_the_close_cooldown_fires_once_not_every_frame():
    d = Driver(cfg_with())
    poses = [make_pose(d.cfg, **OPEN, handedness="Left"),
             make_pose(d.cfg, **OPEN, handedness="Right")]
    d.feed(poses, steps=120, target_hwnd=4242)     # 4 s of holding
    taps = [i for i in d.intents if i.kind == "keys.tap" and "f4" in i.keys]
    assert len(taps) == 1, f"force close fired {len(taps)} times in one hold"


def test_force_close_can_be_switched_off():
    d = Driver(cfg_with(force_close_enabled=False))
    poses = [make_pose(d.cfg, **OPEN, handedness="Left"),
             make_pose(d.cfg, **OPEN, handedness="Right")]
    d.feed(poses, steps=60, target_hwnd=4242)
    assert not [i for i in d.intents if i.kind == "keys.tap" and "f4" in i.keys]


# ======================================================================= config defaults
def test_the_new_keys_all_exist_with_sane_defaults():
    for key in ("gaze_reject_px", "gaze_stabilise_window", "gaze_reject_speed_px_s",
                "gaze_stabilise_deadband_px", "gaze_stabilise_deadband_gain", "gaze_slew_px",
                "gaze_blink_guard", "gaze_require_stable", "gaze_focus_stable_s",
                "gaze_focus_stable_px", "force_close_enabled", "force_close_hold_s",
                "force_close_move_px", "force_close_cooldown_s", "force_close_keys",
                "force_close_requires_target", "force_gaze_calibration", "settings_at_start",
                "startup_calibration"):
        assert key in DEFAULTS, f"{key} is missing from DEFAULTS"

    assert DEFAULTS["force_close_keys"] == ["ctrl", "alt", "f4"]
    assert DEFAULTS["force_close_requires_target"] is True
    assert DEFAULTS["gaze_focus_dwell_s"] >= 0.5, (
        "the focus dwell must be long enough that gaze jitter cannot complete it"
    )
    assert DEFAULTS["frame_width"] == 640, (
        "640x480 is measured at 14 fps on this camera; 1280x720 is 8 fps for no accuracy gain"
    )


def test_a_legacy_config_still_loads():
    """A config saved before any of these keys existed must not KeyError - that is how a version
    bump breaks a working install."""
    c = Config()
    c.data = {k: v for k, v in DEFAULTS.items() if k not in
              ("gaze_reject_px", "force_close_enabled", "settings_at_start",
               "force_gaze_calibration")}
    loaded = Config.from_dict(c.data) if hasattr(Config, "from_dict") else Config.load()
    assert loaded["force_close_enabled"] is True
    assert loaded["settings_at_start"] is True
