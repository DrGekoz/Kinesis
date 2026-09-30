"""Deterministic Kinesis tests. No camera, no cursor movement, no Windows side effects.

Landmark geometry is synthesised so the joint angles are exact:
  extended finger -> MCP/PIP/TIP collinear, PIP angle 180
  curled finger   -> the PIP segment rotated 110 degrees, PIP angle 70
So every classification the pose engine makes is being checked against a known-correct input.
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kinesis.aim import AimClassifier                                    # noqa: E402
from kinesis.config import Calibration, Config, MonitorTarget            # noqa: E402
from kinesis.filters import OneEuro                                      # noqa: E402
from kinesis.gestures import GestureEngine, Intent                       # noqa: E402
from kinesis.pose import (POSE_FIST, POSE_OPEN, POSE_POINT, POSE_SHAKA,  # noqa: E402
                          HandPose, PoseDetector)
from kinesis.winapi import Monitor                                        # noqa: E402

FRAME = (640, 480)
SCALE = 0.10          # wrist -> middle knuckle, in normalised units


# --------------------------------------------------------------------------- geometry
def _rot(v, deg):
    r = math.radians(deg)
    return (v[0] * math.cos(r) - v[1] * math.sin(r), v[0] * math.sin(r) + v[1] * math.cos(r))


def _finger(mcp, direction, extended):
    d = _norm(direction)
    pip = (mcp[0] + d[0] * 0.55 * SCALE, mcp[1] + d[1] * 0.55 * SCALE)
    v2 = d if extended else _rot(d, -110.0)      # 180 deg when extended, ~70 deg curled
    tip = (pip[0] + v2[0] * 0.55 * SCALE, pip[1] + v2[1] * 0.55 * SCALE)
    dip = ((pip[0] + tip[0]) / 2.0, (pip[1] + tip[1]) / 2.0)
    return pip, dip, tip


def _norm(v):
    n = math.hypot(*v) or 1.0
    return (v[0] / n, v[1] / n)


def build_hand(cx=0.5, cy=0.62, extended=("index", "middle", "ring", "pinky"),
               thumb="out", pinches=(), rotate=0.0, scale=SCALE):
    """21 normalised landmarks for a pose. y grows downward, fingers point up (-y)."""
    pts = {}
    wrist = (cx, cy)
    pts[0] = wrist
    mcp_dirs = {
        "index": (-0.42, -0.95), "middle": (0.0, -1.0), "ring": (0.42, -0.95),
        "pinky": (0.78, -0.80),
    }
    base = {}
    for finger, direction in mcp_dirs.items():
        d = _norm(direction)
        base[finger] = (cx + d[0] * scale * (1.0 if finger != "middle" else 1.0),
                        cy + d[1] * scale)
    base["middle"] = (cx, cy - scale)             # exactly one hand-scale from the wrist
    base["index"] = (cx - 0.42 * scale, cy - 0.95 * scale)
    base["ring"] = (cx + 0.42 * scale, cy - 0.95 * scale)
    base["pinky"] = (cx + 0.78 * scale, cy - 0.80 * scale)

    idx = {"index": (5, 6, 7, 8), "middle": (9, 10, 11, 12),
           "ring": (13, 14, 15, 16), "pinky": (17, 18, 19, 20)}
    for finger, (m, p, dpid, t) in idx.items():
        dirvec = _norm((base[finger][0] - cx, base[finger][1] - cy))
        pip, dip, tip = _finger(base[finger], dirvec, finger in extended)
        pts[m], pts[p], pts[dpid], pts[t] = base[finger], pip, dip, tip

    # thumb: out to the side, or folded across the palm for a fist
    thumb_dir = (-0.88, -0.30) if thumb == "out" else (-0.55, 0.62)
    t_cmc = (cx + thumb_dir[0] * 0.30 * scale, cy + thumb_dir[1] * 0.30 * scale)
    t_mcp = (cx + thumb_dir[0] * 0.60 * scale, cy + thumb_dir[1] * 0.60 * scale)
    v = _norm(thumb_dir)
    v2 = v if thumb == "out" else _rot(v, -110.0)
    t_ip = (t_mcp[0] + v[0] * 0.22 * scale, t_mcp[1] + v[1] * 0.22 * scale)
    t_tip = (t_ip[0] + v2[0] * 0.25 * scale, t_ip[1] + v2[1] * 0.25 * scale)
    pts[1], pts[2], pts[3], pts[4] = t_cmc, t_mcp, t_ip, t_tip

    if pinches:
        for finger in pinches:
            tip_index = idx[finger][3]
            ratio = 0.20                                   # comfortably inside pinch_on (0.34)
            base_pt = pts[tip_index]
            angle = {"index": 0.0, "middle": 30.0, "ring": 60.0, "pinky": 90.0}[finger]
            pts[4] = (base_pt[0] + ratio * scale * math.cos(math.radians(angle)),
                      base_pt[1] + ratio * scale * math.sin(math.radians(angle)))

    if rotate:
        pts = {k: _rotate_about(v, (cx, cy), rotate) for k, v in pts.items()}
    return [pts[i] for i in range(21)]


def _rotate_about(p, origin, deg):
    v = (p[0] - origin[0], p[1] - origin[1])
    r = _rot(v, deg)
    return (origin[0] + r[0], origin[1] + r[1])


def world_pointing(yaw_deg, pitch_deg=0.0, length=0.09):
    """21 world landmarks whose index MCP -> TIP vector encodes the requested angles."""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    pts = [(0.0, 0.0, 0.0) for _ in range(21)]
    depth = math.cos(pitch) * math.cos(yaw)
    dx = math.cos(pitch) * math.sin(yaw)
    dy = math.sin(pitch)
    pts[5] = (0.0, 0.0, 0.0)
    pts[8] = (dx * length, dy * length, -depth * length)     # -z is away from the camera
    return pts


@pytest.fixture
def cfg():
    return Config()


# ============================================================ pose engine
def test_open_hand_classified(cfg):
    det = PoseDetector(cfg)
    pose = det.update(build_hand(), "Right", *FRAME)
    assert pose.pose == POSE_OPEN
    assert pose.num_extended == 4
    assert not any(pose.pinches.values())


def test_fist_classified(cfg):
    det = PoseDetector(cfg)
    pose = det.update(build_hand(extended=(), thumb="in"), "Right", *FRAME)
    assert pose.pose == POSE_FIST
    assert pose.num_extended == 0


def test_shaka_classified(cfg):
    """thumb + pinky out, index/middle/ring curled - the push-to-talk pose."""
    det = PoseDetector(cfg)
    pose = det.update(build_hand(extended=("pinky",), thumb="out"), "Right", *FRAME)
    assert pose.pose == POSE_SHAKA
    assert pose.extended["thumb"] and pose.extended["pinky"]
    assert not pose.extended["index"]


def test_point_classified(cfg):
    det = PoseDetector(cfg)
    pose = det.update(build_hand(extended=("index",), thumb="in"), "Right", *FRAME)
    assert pose.pose == POSE_POINT
    assert pose.num_extended == 1


@pytest.mark.parametrize("finger", ["index", "middle", "ring", "pinky"])
def test_pinch_detected(cfg, finger):
    det = PoseDetector(cfg)
    pose = det.update(build_hand(extended=("index", "middle", "ring", "pinky"),
                                 pinches=(finger,)), "Right", *FRAME)
    assert pose.pinches[finger] is True
    assert pose.pose == f"PINCH_{finger.upper()}"
    for other in ("index", "middle", "ring", "pinky"):
        if other != finger:
            assert pose.pinches[other] is False


def test_rotation_invariance(cfg):
    """The upstream detector compared tip and knuckle heights, so a hand held sideways read as
    'all fingers curled'. Joint angles are rotation invariant, so this must still read OPEN."""
    det = PoseDetector(cfg)
    for angle in (0, 45, 90, 135, -90):
        det.forget("Right")                     # isolate the case from hysteresis
        pose = det.update(build_hand(extended=("index", "middle", "ring", "pinky"),
                                     rotate=angle), "Right", *FRAME)
        assert pose.num_extended == 4, f"hand rotated {angle} deg misread as {pose.pose}"


def test_world_pointing_angles(cfg):
    det = PoseDetector(cfg)
    right = det.update(build_hand(extended=("index",)), "Right", *FRAME,
                       world_pointing(45.0))
    assert right.yaw == pytest.approx(45.0, abs=1.0)
    det.forget("Right")
    left = det.update(build_hand(extended=("index",)), "Right", *FRAME, world_pointing(-45.0))
    assert left.yaw == pytest.approx(-45.0, abs=1.0)
    det.forget("Right")
    up = det.update(build_hand(extended=("index",)), "Right", *FRAME, world_pointing(0.0, 30.0))
    assert up.pitch == pytest.approx(30.0, abs=1.0)
    assert up.yaw == pytest.approx(0.0, abs=1.0)


def test_2d_fallback_angles(cfg):
    """Without world landmarks the same convention must hold: finger pointing right in the
    (mirrored) frame is a positive yaw."""
    det = PoseDetector(cfg)
    pts = build_hand(extended=("index",), rotate=90)      # finger points to +x (right)
    pose = det.update(pts, "Right", *FRAME)
    assert pose.yaw_2d > 60


def test_hysteresis_keeps_pose_stable(cfg):
    det = PoseDetector(cfg)
    det.update(build_hand(), "Right", *FRAME)
    # a slightly-bent open hand must not flip to curled on one frame
    bent = build_hand()
    bent[8] = (bent[8][0] + 0.01 * SCALE, bent[8][1] + 0.02 * SCALE)
    pose = det.update(bent, "Right", *FRAME)
    assert pose.num_extended == 4


# ============================================================ filters
def test_one_euro_reduces_jitter():
    import random
    rng = random.Random(7)
    f = OneEuro(min_cutoff=1.6, beta=0.05)
    raw, out = [], []
    t = 0.0
    for i in range(200):
        t += 0.033
        value = 100.0 + rng.uniform(-1.5, 1.5)
        raw.append(value)
        out.append(f(value, t))
    r_var = sum((v - sum(raw) / len(raw)) ** 2 for v in raw) / len(raw)
    o_var = sum((v - sum(out) / len(out)) ** 2 for v in out) / len(out)
    assert o_var < r_var * 0.5, f"filter did not smooth: raw var {r_var:.3f} filtered {o_var:.4f}"
    assert abs(sum(out[-50:]) / 50 - 100.0) < 1.0


def test_one_euro_tracks_fast_motion():
    f = OneEuro(min_cutoff=1.6, beta=0.05)
    t = 0.0
    f(0.0, t)
    t += 0.033
    moved = f(50.0, t)          # a big jump must move immediately, not creep
    assert moved > 10.0, f"filter lags too much on a step: {moved}"


# ============================================================ aim
MONITORS = [
    Monitor(handle=1, device="\\\\.\\DISPLAY16", left=-3840, top=0, right=-1920, bottom=1080, primary=False),
    Monitor(handle=2, device="\\\\.\\DISPLAY14", left=-1920, top=0, right=0, bottom=1080, primary=False),
    Monitor(handle=3, device="\\\\.\\DISPLAY13", left=0, top=0, right=1920, bottom=1080, primary=True),
    Monitor(handle=4, device="\\\\.\\DISPLAY15", left=1920, top=0, right=3840, bottom=1080, primary=False),
]


def make_calibration():
    return Calibration(targets=[
        MonitorTarget(index=i, name=m.device, left=m.left, top=m.top, right=m.right,
                      bottom=m.bottom, yaw=y, pitch=0.0)
        for i, (m, y) in enumerate(zip(MONITORS, (-45.0, -15.0, 15.0, 45.0)))
    ])


def test_calibrated_aim(cfg):
    aim = AimClassifier(cfg, MONITORS, make_calibration())
    assert aim.classify(-44.0, 0.0).index == 0
    assert aim.classify(-10.0, 0.0).index == 1
    assert aim.classify(20.0, 0.0).index == 2
    assert aim.classify(46.0, 0.0).index == 3
    assert aim.classify(0.0, 0.0).mode == "calibrated"


def test_aim_hysteresis_holds_current_monitor(cfg):
    aim = AimClassifier(cfg, MONITORS, make_calibration())
    assert aim.classify(20.0, 0.0).index == 2          # settle on monitor 3
    # nearest target is monitor 4 (45 deg, 13 away) vs current monitor 3 (15 deg, 17 away);
    # the 4 degree gain is inside the 7 degree hysteresis margin, so it must NOT flip
    assert aim.classify(32.0, 0.0).index == 2
    # well past it, it should
    assert aim.classify(44.0, 0.0).index == 3


def test_aim_unknown_when_far_off(cfg):
    aim = AimClassifier(cfg, MONITORS, make_calibration())
    result = aim.classify(-140.0, 0.0)
    assert result.index is None
    assert result.mode == "calibrated"


def test_aim_heuristic_without_calibration(cfg):
    aim = AimClassifier(cfg, MONITORS, Calibration())
    assert aim.classify(-70.0, 0.0).index == 0
    assert aim.classify(0.0, 0.0).index == 2
    assert aim.classify(70.0, 0.0).index == 3
    assert aim.classify(0.0, 0.0).mode == "heuristic"


def test_aim_order_check():
    assert AimClassifier.order_check([(0, -45.0), (1, 0.0), (2, 45.0)]) is True
    assert AimClassifier.order_check([(0, 45.0), (1, 0.0), (2, -45.0)]) is False
    assert AimClassifier.order_check([(0, 12.0)]) is True


def test_aim_yaw_sign_config(cfg):
    cfg.set("aim_yaw_sign", -1.0)
    aim = AimClassifier(cfg, MONITORS, Calibration())
    assert aim.classify(70.0, 0.0).index == 0


# ============================================================ gesture helpers
def make_pose(cfg, extended=("index",), pinches=(), handedness="Right", tip=(0.5, 0.5),
              palm=(0.5, 0.6), yaw=0.0, pitch=0.0, pose_name=None, scale_px=60.0):
    det = PoseDetector(cfg)
    ext = {f: (f in extended) for f in ("thumb", "index", "middle", "ring", "pinky")}
    pin = {f: (f in pinches) for f in ("index", "middle", "ring", "pinky")}
    name = pose_name or det._classify(ext, pin)
    return HandPose(handedness=handedness, pose=name, extended=ext, pinches=pin,
                    scale=SCALE, scale_px=scale_px,
                    index_tip_px=(tip[0] * FRAME[0], tip[1] * FRAME[1]),
                    index_tip_norm=tip, palm_px=(palm[0] * FRAME[0], palm[1] * FRAME[1]),
                    yaw=yaw, pitch=pitch)


class Driver:
    """Feeds frames to the gesture engine with an explicit clock."""

    def __init__(self, cfg, dt=0.033):
        self.cfg = cfg
        self.engine = GestureEngine(cfg, FRAME)
        self.now = 1000.0
        self.dt = dt
        self.intents = []
        self.stamped = []

    def feed(self, poses, steps=1, aim=None):
        for _ in range(steps):
            self.now += self.dt
            produced = self.engine.update(poses, aim, self.now, self.dt)
            self.intents.extend(produced)
            self.stamped.extend((self.now, i) for i in produced)
        return self

    def of(self, kind):
        return [i for i in self.intents if i.kind == kind]

    def times(self, kind):
        return [t for t, i in self.stamped if i.kind == kind]

    def kinds(self):
        return [i.kind for i in self.intents]

    def clear(self):
        self.intents = []
        return self


OPEN = dict(extended=("index", "middle", "ring", "pinky"))
POINT = dict(extended=("index",))
FIST = dict(extended=(), pose_name=POSE_FIST)
SHAKA = dict(extended=("pinky",), pose_name=POSE_SHAKA)


# ============================================================ gestures: flicks
def test_close_flick_minimises(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, **OPEN)], steps=6)
    d.clear().feed([make_pose(cfg, **FIST)], steps=4)
    assert d.of("window.minimise"), f"no minimise from open->fist: {d.kinds()}"
    assert len(d.of("window.minimise")) == 1, "minimise fired more than once"
    assert not d.of("mouse.click"), "the closing pinch must not also click"


def test_open_flick_maximises(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, **FIST)], steps=6)
    d.clear().feed([make_pose(cfg, **OPEN)], steps=4)
    assert len(d.of("window.maximise")) == 1


def test_slow_close_does_not_minimise(cfg):
    """Sitting in a half-closed pose for a second means it was not a flick."""
    d = Driver(cfg)
    d.feed([make_pose(cfg, **OPEN)], steps=6)
    half = make_pose(cfg, extended=("index", "middle"))
    d.clear().feed([half], steps=40)                  # ~1.3 s
    d.feed([make_pose(cfg, **FIST)], steps=4)
    assert not d.of("window.minimise")


def test_single_noisy_frame_does_not_minimise(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, **OPEN)], steps=6)
    d.clear().feed([make_pose(cfg, **FIST)], steps=1)
    d.feed([make_pose(cfg, **OPEN)], steps=4)
    assert not d.of("window.minimise"), "one frame of misclassification fired a gesture"


def test_aimed_monitor_reaches_the_action(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, **OPEN)], steps=6, aim=3)
    d.clear().feed([make_pose(cfg, **FIST)], steps=4, aim=3)
    assert d.of("window.minimise")[0].monitor == 3


# ============================================================ gestures: clicks
def test_pinch_clicks_once(cfg):
    """A pinch is committed on release (or while held past the arm time), and exactly once."""
    d = Driver(cfg)
    d.feed([make_pose(cfg, extended=("index", "middle"), pinches=("index",))], steps=4)
    d.feed([make_pose(cfg, **OPEN)], steps=8)
    clicks = d.of("mouse.click")
    assert len(clicks) == 1 and clicks[0].button == "left", d.kinds()


def test_held_pinch_clicks_while_held(cfg):
    """A deliberate pinch-and-hold clicks without needing a release."""
    d = Driver(cfg)
    d.feed([make_pose(cfg, extended=("index", "middle"), pinches=("index",))], steps=20)
    assert len(d.of("mouse.click")) == 1, d.kinds()


def test_two_quick_pinches_produce_two_clicks(cfg):
    """Two clicks - the OS turns a pair inside the double-click window into a double click,
    which is exactly how a physical mouse behaves."""
    d = Driver(cfg)
    d.feed([make_pose(cfg, extended=("index", "middle"), pinches=("index",))], steps=3)
    d.feed([make_pose(cfg, **OPEN)], steps=2)
    d.feed([make_pose(cfg, extended=("index", "middle"), pinches=("index",))], steps=3)
    d.feed([make_pose(cfg, **OPEN)], steps=6)
    clicks = d.of("mouse.click")
    assert len(clicks) == 2, d.kinds()
    assert all(c.button == "left" for c in clicks)
    times = d.times("mouse.click")
    assert times[1] - times[0] <= float(cfg["double_click_window_s"]), \
        f"the pair landed {times[1] - times[0]:.2f}s apart, outside the double-click window"


def test_fast_pinch_still_clicks(cfg):
    """A tap shorter than the arm time must not be dropped."""
    d = Driver(cfg)
    d.feed([make_pose(cfg, extended=("index", "middle"), pinches=("index",))], steps=2)
    d.feed([make_pose(cfg, **OPEN)], steps=8)
    assert len(d.of("mouse.click")) == 1, d.kinds()


def test_pinch_then_fist_cancels_click(cfg):
    """The conflict rule: closing a hand into a fist sweeps through a pinch, and that sweep
    must not fire a click on the way to the minimise."""
    d = Driver(cfg)
    d.feed([make_pose(cfg, **OPEN)], steps=6)
    d.feed([make_pose(cfg, extended=("index", "middle", "ring"), pinches=("index",))], steps=2)
    d.clear().feed([make_pose(cfg, **FIST)], steps=4)
    assert not d.of("mouse.click"), f"stray click during close flick: {d.kinds()}"
    assert d.of("window.minimise")


def test_right_click_once_and_cooldown(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, extended=("index", "middle", "ring"),
                      pinches=("middle",))], steps=10)
    rights = [i for i in d.of("mouse.click") if i.button == "right"]
    assert len(rights) == 1, d.kinds()


def test_no_right_click_while_index_pinching(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, extended=("index", "middle"), pinches=("index", "middle"))], steps=8)
    assert not [i for i in d.of("mouse.click") if i.button == "right"]


# ============================================================ gestures: scroll + drag
def test_ring_pinch_scrolls_and_freezes_cursor(cfg):
    d = Driver(cfg)
    pose = make_pose(cfg, extended=("index", "middle"), pinches=("ring",), palm=(0.5, 0.6))
    d.feed([pose], steps=3)
    assert d.engine.active == "scroll"
    d.clear()
    for y in (0.58, 0.54, 0.50, 0.46):
        d.feed([make_pose(cfg, extended=("index", "middle"), pinches=("ring",), palm=(0.5, y))],
               steps=1)
    wheels = d.of("mouse.wheel")
    assert wheels, f"no wheel events while scrolling: {d.kinds()}"
    assert all(w.amount > 0 for w in wheels), "hand moving up must scroll up"
    assert not d.of("cursor.move"), "cursor must be frozen while scrolling"
    # a scroll now survives a pinch that flickers off for a frame or two, so ending it takes a few
    d.feed([make_pose(cfg, **OPEN)], steps=int(cfg["scroll_release_frames"]) + 1)
    assert d.engine.active is None


def test_brief_pinch_flicker_does_not_end_a_scroll(cfg):
    """The old behaviour ended a scroll the instant the ring pinch missed a frame, which is most of
    what made it feel broken: the hand is never perfectly still."""
    d = Driver(cfg)
    scrolling = make_pose(cfg, extended=("index", "middle"), pinches=("ring",), palm=(0.5, 0.5))
    d.feed([scrolling], steps=4)
    assert d.engine.active == "scroll"
    d.feed([make_pose(cfg, extended=("index", "middle"), palm=(0.5, 0.50))], steps=1)   # blip
    assert d.engine.active == "scroll", "one missed pinch frame ended the scroll"
    d.feed([scrolling], steps=2)
    assert d.engine.active == "scroll"


def test_adaptive_scroll_faster_when_hand_moves_faster(cfg):
    slow = Driver(cfg)
    slow.feed([make_pose(cfg, pinches=("ring",), palm=(0.5, 0.5))], steps=3)
    slow.clear()
    fast = Driver(cfg)
    fast.feed([make_pose(cfg, pinches=("ring",), palm=(0.5, 0.5))], steps=3)
    fast.clear()

    def total(driver, step):
        driver.feed([make_pose(cfg, pinches=("ring",), palm=(0.5, 0.5 - step))], steps=1)
        return sum(w.amount for w in driver.of("mouse.wheel"))

    fast_total = 0
    slow_total = 0
    for _ in range(4):
        slow_total += total(slow, 0.01)
        fast_total += total(fast, 0.05)
    assert fast_total > slow_total, f"adaptive gain had no effect: {slow_total} vs {fast_total}"


def test_pinky_pinch_drags(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, extended=("index", "middle"), pinches=("pinky",))], steps=4)
    assert d.of("mouse.down"), d.kinds()
    assert len(d.of("mouse.down")) == 1
    d.feed([make_pose(cfg, **OPEN)], steps=3)
    assert d.of("mouse.up")
    assert d.engine.active is None


def test_drag_does_not_freeze_cursor(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, extended=("index", "middle"), pinches=("pinky",),
                      tip=(0.40, 0.40))], steps=2)
    d.clear()
    d.feed([make_pose(cfg, extended=("index", "middle"), pinches=("pinky",),
                      tip=(0.70, 0.70))], steps=2)
    assert d.of("cursor.move"), "cursor must follow during a drag"


# ============================================================ gestures: swipe
def test_swipe_right_next_tab(cfg):
    cfg.set("swipe_action", "tab")     # the swipe binding is opt-in now; this test covers it
    d = Driver(cfg)
    xs = (0.30, 0.38, 0.47, 0.56, 0.65, 0.74)
    for x in xs:
        d.feed([make_pose(cfg, **OPEN, palm=(x, 0.6), tip=(x, 0.5))], steps=1)
    taps = d.of("keys.tap")
    assert taps and taps[0].keys == ("ctrl", "tab"), d.kinds()


def test_swipe_left_previous_tab(cfg):
    cfg.set("swipe_action", "tab")     # the swipe binding is opt-in now; this test covers it
    d = Driver(cfg)
    for x in (0.74, 0.65, 0.56, 0.47, 0.38, 0.30):
        d.feed([make_pose(cfg, **OPEN, palm=(x, 0.6), tip=(x, 0.5))], steps=1)
    taps = d.of("keys.tap")
    assert taps and taps[0].keys == ("ctrl", "shift", "tab"), d.kinds()


def test_swipe_needs_an_open_hand(cfg):
    d = Driver(cfg)
    for x in (0.30, 0.45, 0.60, 0.75):
        d.feed([make_pose(cfg, extended=("index",), palm=(x, 0.6), tip=(x, 0.5))], steps=1)
    assert not d.of("keys.tap"), "a pointing hand must not switch tabs"


def test_swipe_has_a_cooldown(cfg):
    cfg.set("swipe_action", "tab")     # the swipe binding is opt-in now; this test covers it
    d = Driver(cfg)
    for sweep in range(2):
        for x in (0.30, 0.45, 0.60, 0.75):
            d.feed([make_pose(cfg, **OPEN, palm=(x, 0.6), tip=(x, 0.5))], steps=1)
    assert len(d.of("keys.tap")) == 1, f"cooldown ignored: {d.kinds()}"


def test_horizontal_swipe_ignores_vertical_travel(cfg):
    d = Driver(cfg)
    pts = [(0.30, 0.30), (0.45, 0.45), (0.60, 0.60), (0.75, 0.75)]     # diagonal
    for x, y in pts:
        d.feed([make_pose(cfg, **OPEN, palm=(x, y), tip=(x, y - 0.05))], steps=1)
    assert not d.of("keys.tap")


# ============================================================ gestures: alt-tab
def test_alt_tab_two_handed_flow(cfg):
    d = Driver(cfg)
    left_fist = make_pose(cfg, extended=(), handedness="Left", pose_name=POSE_FIST)
    right_point = make_pose(cfg, **POINT)
    d.feed([left_fist, right_point], steps=10)
    assert not d.of("keys.down"), "Alt must not engage before the hold time"

    d.feed([left_fist, right_point], steps=70)         # past the 2 s hold
    downs = [i for i in d.of("keys.down") if i.keys == ("alt",)]
    assert downs, f"Alt never held: {d.kinds()}"
    assert [i for i in d.of("keys.tap") if i.keys == ("tab",)], "switcher never opened"
    assert d.engine.alt_held

    d.clear()
    d.feed([left_fist, make_pose(cfg, extended=("index", "middle"), pinches=("index",))], steps=4)
    assert [i for i in d.of("keys.tap") if i.keys == ("tab",)], "right pinch must tap Tab"
    assert not d.of("mouse.click"), "pinch must be a Tab, not a click, while Alt is held"

    d.clear()
    d.feed([make_pose(cfg, extended=(), handedness="Left", pose_name=POSE_FIST), right_point],
           steps=1)
    d.feed([right_point], steps=2)                     # left hand gone / open
    assert [i for i in d.of("keys.up") if i.keys == ("alt",)], "Alt was never released"
    assert not d.engine.alt_held


def test_alt_tab_ignores_brief_left_fist(cfg):
    d = Driver(cfg)
    left_fist = make_pose(cfg, extended=(), handedness="Left", pose_name=POSE_FIST)
    d.feed([left_fist, make_pose(cfg, **POINT)], steps=30)   # 1 s, not 2 s
    assert not d.of("keys.down")


# ============================================================ gestures: push to talk
def test_shaka_holds_ctrl_space(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, **SHAKA)], steps=8)
    downs = [i for i in d.of("keys.down") if i.keys == ("ctrl", "space")]
    assert len(downs) == 1, d.kinds()
    d.feed([make_pose(cfg, **SHAKA)], steps=10)
    assert len([i for i in d.of("keys.down") if i.keys == ("ctrl", "space")]) == 1, \
        "hold must not repeat key down"
    d.clear().feed([make_pose(cfg, **OPEN)], steps=3)
    assert [i for i in d.of("keys.up") if i.keys == ("ctrl", "space")]


def test_shaka_needs_a_moment(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, **SHAKA)], steps=2)
    assert not d.of("keys.down"), "push-to-talk armed too eagerly"


def test_shaka_is_not_a_fist(cfg):
    """A fist would minimise; the shaka keeps the pinky out so it must not."""
    d = Driver(cfg)
    d.feed([make_pose(cfg, **OPEN)], steps=6)
    d.clear().feed([make_pose(cfg, **SHAKA)], steps=10)
    assert not d.of("window.minimise")


# ============================================================ gestures: cursor + safety
def test_cursor_snaps_to_index_tip(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, extended=("index",), tip=(0.75, 0.25))], steps=3)
    moves = d.of("cursor.move")
    assert moves, "no cursor movement from a pointing hand"
    x, y = moves[-1].x, moves[-1].y
    # frame position 0.75 across the active area maps into the right of the virtual desktop
    assert x > 0, f"expected a right-hand-side screen position, got x={x}"


def test_cursor_holds_still_when_hand_is_still(cfg):
    d = Driver(cfg)
    pose = make_pose(cfg, extended=("index",), tip=(0.5, 0.5))
    d.feed([pose], steps=2)
    d.clear().feed([pose], steps=10)
    assert not d.of("cursor.move"), "deadband did not stop idle jitter"


def test_cursor_does_not_move_without_index(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, extended=(), pose_name=POSE_FIST)], steps=4)
    assert not d.of("cursor.move"), "cursor must hold when no finger is pointing"


def test_hand_loss_releases_everything(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, extended=("index", "middle"), pinches=("pinky",))], steps=3)
    assert d.engine.active == "drag"
    d.clear().feed([], steps=2)
    assert d.of("mouse.up"), "drag left held down when the hand vanished"


def test_release_all_covers_key_holds(cfg):
    engine = GestureEngine(cfg, FRAME)
    engine.alt_held = True
    engine.ptt_held = True
    engine.active = "drag"
    out = engine.release_all(reason="test")
    kinds = {(i.kind, i.keys, i.button) for i in out}
    assert ("keys.up", ("alt",), "") in kinds
    assert ("keys.up", ("ctrl", "space"), "") in kinds
    assert ("mouse.up", (), "left") in kinds
    assert not engine.alt_held and not engine.ptt_held and engine.active is None


def test_only_one_held_action_at_a_time(cfg):
    d = Driver(cfg)
    d.feed([make_pose(cfg, extended=("index", "middle"), pinches=("ring", "pinky"))], steps=4)
    assert d.engine.active == "scroll", "scroll should win the arbiter"
    assert not d.of("mouse.down"), "drag must not start while scrolling"


# ============================================================ end-to-end chain
def test_landmarks_to_minimise_end_to_end(cfg):
    """The whole chain: synthetic landmarks -> pose detector -> gesture engine -> intent,
    including the through-the-pinch arbitration."""
    engine = GestureEngine(cfg, FRAME)
    det = PoseDetector(cfg)
    now = 1000.0
    dt = 0.033
    intents = []

    def step(points, world=None):
        nonlocal now
        now += dt
        pose = det.update(points, "Right", *FRAME, world)
        intents.extend(engine.update([pose], 2, now, dt))

    for _ in range(6):
        step(build_hand(extended=("index", "middle", "ring", "pinky"), thumb="in"))
    intents.clear()
    for _ in range(6):
        step(build_hand(extended=("index", "middle", "ring"), thumb="in", pinches=("index",)))
    for _ in range(6):
        step(build_hand(extended=(), thumb="in"))

    kinds = [i.kind for i in intents]
    assert "window.minimise" in kinds, f"end-to-end minimise failed: {kinds}"
    assert "mouse.click" not in kinds, f"stray click in the chain: {kinds}"
