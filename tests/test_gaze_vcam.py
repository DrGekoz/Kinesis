"""Tests for the gaze-driven scrolling, gaze targeting, and the virtual camera compositor.

Same rules as the other suite: no camera, no OS side effects, deterministic. The virtual-camera
tests build frames in memory - they never touch the OBS device (that is tools/verify_actions.py's
job).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis.actions import ActionRunner                          # noqa: E402
from kinesis.config import Config                                  # noqa: E402
from kinesis.gestures import GazeScroller, GestureEngine, Intent   # noqa: E402
from kinesis.pose import HandPose                                  # noqa: E402
from kinesis.vcam import VirtualCamera, gaze_to_canvas             # noqa: E402
from kinesis.winapi import enumerate_monitors, virtual_screen     # noqa: E402

OPEN = dict(extended=("index", "middle", "ring", "pinky"), pinches=(), yaw=0.0, pitch=0.0)


class FakeGaze:
    def __init__(self, x=0.0, y=540.0, valid=True, age=0.0, face=True):
        self.x, self.y, self.valid, self.age, self.face = x, y, valid, age, face


def make_pose(extended=("index", "middle", "ring", "pinky"), pinches=(), yaw=0.0, pitch=0.0,
              pose_name="", handedness="Right"):
    ext = {f: (f in extended) for f in ("thumb", "index", "middle", "ring", "pinky")}
    pin = {f: (f in pinches) for f in ("index", "middle", "ring", "pinky")}
    return HandPose(handedness=handedness, pose=pose_name, extended=ext, pinches=pin,
                    scale=0.1, scale_px=60.0,
                    points_px=[(320.0, 240.0) for _ in range(21)],
                    points_norm=[(0.5, 0.5) for _ in range(21)],
                    index_tip_px=(320.0, 240.0), index_tip_norm=(0.5, 0.5),
                    palm_px=(320.0, 300.0),
                    yaw=yaw, pitch=pitch, yaw_2d=yaw, confidence=0.95)


def cfg_with(**kw):
    c = Config()
    for k, v in kw.items():
        c.set(k, v)
    return c


# ---------------------------------------------------------------- gaze scrolling
def test_gaze_scroll_off_by_default_when_mode_off():
    sc = GazeScroller(cfg_with(gaze_scroll_mode="off"))
    assert sc.update(FakeGaze(y=50.0), 100.0, 0.05) == []


def test_gaze_scroll_needs_the_band():
    sc = GazeScroller(cfg_with(gaze_scroll_mode="edge"))
    assert sc.update(FakeGaze(y=540.0), 100.0, 0.05) == []      # middle of the screen


def test_gaze_scroll_needs_dwell_before_firing():
    cfg = cfg_with(gaze_scroll_mode="edge", gaze_scroll_dwell_s=0.25, gaze_scroll_warp_cursor=False)
    sc = GazeScroller(cfg)
    now = 100.0
    assert sc.update(FakeGaze(y=40.0), now, 0.05) == []          # arrives in the band
    now += 0.10
    assert sc.update(FakeGaze(y=40.0), now, 0.05) == []          # still inside the dwell
    now += 0.20
    out = sc.update(FakeGaze(y=40.0), now, 0.05)                 # dwell satisfied
    assert any(i.kind == "mouse.wheel" for i in out)


def test_gaze_scroll_direction_top_is_up_bottom_is_down():
    cfg = cfg_with(gaze_scroll_mode="edge", gaze_scroll_warp_cursor=False)
    _l, top, _w, h = virtual_screen()
    top_band, bottom_band = top + 20, top + h - 20

    up = GazeScroller(cfg)
    now = 100.0
    for _ in range(12):
        now += 0.05
        out = up.update(FakeGaze(y=top_band), now, 0.05)
        up_units = sum(i.amount for i in out if i.kind == "mouse.wheel")
    assert up_units > 0, "looking at the top band must scroll up (positive units)"

    down = GazeScroller(cfg)
    now = 100.0
    for _ in range(12):
        now += 0.05
        out = down.update(FakeGaze(y=bottom_band), now, 0.05)
        down_units = sum(i.amount for i in out if i.kind == "mouse.wheel")
    assert down_units < 0, "looking at the bottom band must scroll down (negative units)"


def test_gaze_scroll_ramps_up_over_time():
    cfg = cfg_with(gaze_scroll_mode="edge", gaze_scroll_ramp_s=1.0, gaze_scroll_warp_cursor=False)
    sc = GazeScroller(cfg)
    now = 100.0
    first = 0
    for i in range(30):
        now += 0.05
        out = sc.update(FakeGaze(y=40.0), now, 0.05)
        units = [i.amount for i in out if i.kind == "mouse.wheel"]
        if i == 10:
            first = sum(units)
        last = sum(units)
    assert last > first, "scrolling should accelerate towards full speed"


def test_gaze_scroll_stops_when_the_gaze_leaves_the_band():
    cfg = cfg_with(gaze_scroll_mode="edge", gaze_scroll_warp_cursor=False)
    sc = GazeScroller(cfg)
    now = 100.0
    for _ in range(10):
        now += 0.05
        sc.update(FakeGaze(y=40.0), now, 0.05)
    assert sc.active
    sc.update(FakeGaze(y=540.0), now + 0.05, 0.05)
    assert not sc.active


def test_gaze_scroll_ignores_stale_gaze():
    sc = GazeScroller(cfg_with(gaze_scroll_mode="edge"))
    assert sc.update(FakeGaze(y=40.0, valid=False), 100.0, 0.05) == []


def test_gaze_scroll_warps_the_cursor_so_the_wheel_lands_on_the_target():
    cfg = cfg_with(gaze_scroll_mode="edge", gaze_scroll_warp_cursor=True)
    sc = GazeScroller(cfg)
    now = 100.0
    out = []
    for _ in range(10):
        now += 0.05
        out = sc.update(FakeGaze(x=-100.0, y=40.0), now, 0.05)
    warps = [i for i in out if i.kind == "cursor.warp"]
    assert warps and warps[0].x == -100.0 and warps[0].y == 40.0

    off = GazeScroller(cfg_with(gaze_scroll_mode="edge", gaze_scroll_warp_cursor=False))
    now = 100.0
    out = []
    for _ in range(10):
        now += 0.05
        out = off.update(FakeGaze(y=40.0), now, 0.05)
    assert not [i for i in out if i.kind == "cursor.warp"]


# ---------------------------------------------------------------- gaze targeting
def test_window_gesture_carries_the_gaze_target():
    """The close flick must act on the window the gaze resolved, not on whatever has focus."""
    engine = GestureEngine(Config())
    now = 1000.0
    engine.update([make_pose(**OPEN)], None, now, 0.033, target_hwnd=555)
    engine.update([make_pose(**OPEN)], None, now + 0.033, 0.033, target_hwnd=555)
    out = []
    for i in range(6):
        now += 0.033
        out += engine.update([make_pose(extended=(), pose_name="FIST")], None, now, 0.033,
                             target_hwnd=555)
    mins = [i for i in out if i.kind == "window.minimise"]
    assert mins and mins[0].target_hwnd == 555


def test_tab_swipe_carries_a_focus_target():
    engine = GestureEngine(cfg_with(swipe_min_fraction=0.10, swipe_window_s=0.25,
                                    swipe_action="tab"))   # opt back into the replaced binding
    now = 1000.0
    out = []
    # an open hand travelling right fast enough to count as a swipe
    for step in range(6):
        pose = make_pose(**OPEN)
        pose.palm_px = (100.0 + step * 60.0, 300.0)
        now += 0.033
        out += engine.update([pose], None, now, 0.033, target_hwnd=777)
    taps = [i for i in out if i.kind == "keys.tap" and "tab" in i.keys]
    assert taps, "a fast open-hand lateral move should switch tabs"
    assert taps[0].focus_hwnd == 777


def test_alt_tab_focuses_the_gaze_target_before_switching():
    """Alt-Tab is two-handed: the left fist holds Alt, so the target has to be focused first."""
    engine = GestureEngine(cfg_with(alt_tab_hold_s=0.3, cursor_hand="right"))
    now = 1000.0
    out = []
    for _ in range(20):
        now += 0.05
        left = make_pose(extended=(), pose_name="FIST", handedness="Left")
        right = make_pose(**OPEN)
        out += engine.update([left, right], None, now, 0.05, target_hwnd=999)
    downs = [i for i in out if i.kind == "keys.down" and "alt" in i.keys]
    assert downs, "a left fist held past the delay should open Alt-Tab"
    assert downs[0].focus_hwnd == 999


# ---------------------------------------------------------------- virtual camera
def test_gaze_maps_monotonically_left_to_right():
    _l, _t, vw, vh = virtual_screen()
    left_x, _ = gaze_to_canvas(0.0, 0.0, 1920, 1080)
    right_x, _ = gaze_to_canvas(float(vw * 0.9), 0.0, 1920, 1080)
    assert left_x < right_x
    _cx, cy = gaze_to_canvas(0.0, float(vh * 0.5), 1920, 1080)
    assert cy == pytest.approx(540.0, abs=2.0)


def test_passthrough_is_the_camera_frame_plus_overlays():
    cfg = cfg_with(vcam_mode="passthrough", vcam_enabled=True, vcam_width=320, vcam_height=240,
                   vcam_show_hud=False, vcam_show_landmarks=True)
    cam = VirtualCamera(cfg)
    frame = np.full((240, 320, 3), 90, np.uint8)

    quiet = cam.composite(frame, [], None, [])
    assert quiet.shape == (240, 240 * 240 // 240, 3) or quiet.shape[:2] == (240, 320)
    assert np.array_equal(quiet, frame), "with nothing to draw the frame should pass through"

    busy = cam.composite(frame, [make_pose(**OPEN)], FakeGaze(x=0.0, y=540.0), [])
    assert not np.array_equal(busy, frame), "hand landmarks should be drawn onto the frame"


def test_overlay_is_a_keyable_green_canvas():
    cfg = cfg_with(vcam_mode="overlay", vcam_enabled=True, vcam_overlay_size=[640, 360],
                   vcam_show_hud=False, vcam_show_landmarks=False)
    cam = VirtualCamera(cfg)
    out = cam.composite(None, [], None, [])
    assert out.shape[:2] == (360, 640)
    assert tuple(int(v) for v in out[5, 5]) == (0, 255, 0), "overlay background must be chroma green"


def test_overlay_draws_hands_and_gaze():
    cfg = cfg_with(vcam_mode="overlay", vcam_enabled=True, vcam_overlay_size=[640, 360],
                   vcam_show_hud=False, vcam_show_landmarks=True)
    cam = VirtualCamera(cfg)
    base = cam.composite(None, [], None, [])
    with_hand = cam.composite(None, [make_pose(**OPEN)], None, [])
    assert not np.array_equal(base, with_hand), "hand skeleton must appear on the overlay"
    with_gaze = cam.composite(None, [], FakeGaze(x=0.0, y=540.0), [])
    assert not np.array_equal(base, with_gaze), "gaze cursor must appear on the overlay"


def test_overlay_hud_text_is_optional():
    cfg = cfg_with(vcam_mode="overlay", vcam_enabled=True, vcam_overlay_size=[640, 360],
                   vcam_show_hud=True, vcam_show_landmarks=False)
    cam = VirtualCamera(cfg)
    out = cam.composite(None, [], None, ["target: Lemonade"])
    assert not np.array_equal(out, cam.composite(None, [], None, []))


def test_vcam_disabled_never_starts():
    cam = VirtualCamera(cfg_with(vcam_enabled=False))
    assert cam.enabled is False
    assert cam.start(30.0) is False


# ---------------------------------------------------------------- dictation (Handy)
def test_ptt_hotkey_is_configurable():
    """The dictation hotkey must come from config: ctrl+space is Handy's Windows default, but a
    remap there has to be mirrored here and released as exactly what was pressed."""
    engine = GestureEngine(cfg_with(ptt_keys=["ctrl", "shift", "d"]))
    now = 1000.0
    out = []
    shaka = make_pose(pose_name="SHAKA", extended=("thumb", "pinky"), pinches=())
    for _ in range(8):
        now += 0.05
        out += engine.update([shaka], None, now, 0.05)
    downs = [i for i in out if i.kind == "keys.down"]
    assert downs and downs[0].keys == ("ctrl", "shift", "d"), downs
    ups = [i for i in engine.release_all(now) if i.kind == "keys.up"]
    assert ups and ups[0].keys == ("ctrl", "shift", "d"), ups


def test_ptt_defaults_to_handys_windows_binding():
    engine = GestureEngine(Config())
    assert engine.ptt_keys == ("ctrl", "space")


def test_release_all_releases_whatever_was_pressed():
    """Safety path: the runner must not guess key names. A remapped hotkey released as a hardcoded
    ctrl+space would leave the real keys stuck down."""
    runner = ActionRunner(Config(), enumerate_monitors(), dry=True)
    runner.execute([Intent("keys.down", keys=("ctrl", "shift", "d"))])
    assert runner._held_keys == ["ctrl", "shift", "d"]
    released = []
    original = runner._keys_up
    runner._keys_up = lambda keys: (released.append(tuple(keys)), original(keys))[1]
    runner.release_all()
    # press order, which _keys_up reverses internally so the last key pressed comes up first
    assert ("ctrl", "shift", "d") in released, f"released {released}"
    assert runner._held_keys == []
    assert runner._alt_held is False
