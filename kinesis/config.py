"""Config + calibration store for Kinesis.

Two JSON files next to the project root:
  kinesis_config.json       tunables (thresholds, camera, filter, latency options)
  kinesis_calibration.json  per-monitor pointing angles captured by calibrate.py
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "kinesis_config.json"
CALIBRATION_PATH = ROOT / "kinesis_calibration.json"

DEFAULTS: Dict[str, Any] = {
    # --- camera / capture ---
    "camera_index": 0,
    # 640x480 is NOT a default by accident. Measured on this machine's C920 over DSHOW:
    #   640x480  -> 14.0 fps      1280x720 -> 8.1 fps      1920x1080 -> 4.0 fps
    # MJPG is ignored by the driver (it reports YUY2 either way), so there is no way to get 720p
    # back to 15 fps. And a 720p frame bought no measurable gaze accuracy: MediaPipe's face
    # landmarker rescales its input to a fixed square, so the extra pixels only change sub-pixel
    # sampling. Higher resolution is available (frame_width/frame_height) but costs 6 fps.
    "frame_width": 640,
    "frame_height": 480,
    "camera_backend": "dshow",        # dshow | msmf | default
    "mirror": True,                    # selfie view; landmark handedness depends on this

    # --- inference ---
    "model_complexity": 0,             # 0 = lite (fastest), 1 = full
    "max_hands": 2,
    "min_detection_confidence": 0.6,
    "min_tracking_confidence": 0.6,
    "smooth_landmarks": True,

    # --- latency / jitter ---
    "filter": True,                    # One-Euro on landmark positions
    "filter_min_cutoff": 1.6,          # lower = smoother at rest, more lag
    "filter_beta": 0.05,               # higher = less lag on fast motion
    "filter_derivative_cutoff": 1.0,
    "deadband_px": 1.5,                # ignore sub-pixel residue => truly still cursor
    # --- the pointer follows the eyes (cursor_source = gaze) ---
    "cursor_source": "gaze",           # gaze | hand
    "cursor_fallback": "hand",         # hand | hold - what to do before gaze is calibrated
    "gaze_cursor_deadband_px": 3.0,    # a resting eye must not jitter the pointer
    "gaze_cursor_smoothing": 0.55,     # 0 = raw, 1 = never moves: EMA on the eye cursor
    # --- gaze outlier rejection (see filters.GazeStabiliser) ---
    # The gaze model is a 486-feature ridge fitted from ~220 samples, so it is rank-deficient and
    # 0.2% landmark noise moves the prediction by over a million pixels. Rejecting the excursion is
    # the only thing that works: a deadband large enough to swallow that would eat real motion.
    "gaze_reject_px": 260.0,           # drop a sample this far from the window median
    "gaze_stabilise_window": 5,        # samples in the median/mean window
    # 20000 px/s, not 9000: gaze runs at 20 Hz, so a 1920 px one-screen saccade is 38,400 px/s and
    # the old value discarded real saccades as noise. Still three orders below the million-pixel
    # excursions the stabiliser exists to reject.
    "gaze_reject_speed_px_s": 20000.0,
    "gaze_stabilise_deadband_px": 3.0, # floor for the adaptive deadband
    "gaze_stabilise_deadband_gain": 2.0,  # deadband = max(floor, gain x window spread)
    "gaze_slew_px": 900.0,             # max px the pointer may travel in one frame
    "gaze_blink_guard": True,          # a blink must never steer the pointer
    "gaze_require_stable": True,       # do not aim a gesture at a rejected/jerking sample
    "cursor_gain": 1.0,                # 1.0 = snap 1:1
    "active_margin": 0.10,             # frame edge trimmed from the tracking area

    # --- hands / cursor ---
    "cursor_hand": "right",            # right | left | auto
    "handedness_mirror": False,        # flip if mediapipe labels look swapped

    # --- pose detection ---
    "finger_extended_deg": 155.0,
    "finger_curled_deg": 110.0,
    "pinch_on": 0.34,                  # thumb->fingertip distance / hand size
    "pinch_off": 0.55,
    "thumb_extended_deg": 150.0,
    "thumb_curled_deg": 120.0,

    # --- gesture timing (seconds) ---
    "click_arm_s": 0.35,               # pinch held this long clicks while still pinched; shorter
                                       # pinches click on release instead (see release grace)
    "click_release_grace_s": 0.12,     # short pinch: allow this long after release before it counts
                                       # (a fist forming inside this window cancels the click)
    "double_click_window_s": 0.45,
    "right_click_cooldown_s": 0.6,
    "swipe_window_s": 0.28,
    "swipe_min_fraction": 0.16,        # of frame width
    "swipe_max_vertical_fraction": 0.14,
    "swipe_cooldown_s": 0.7,
    "alt_tab_hold_s": 2.0,             # left fist held this long opens the switcher
    # --- Ctrl-Tab: right-hand fist holds Ctrl, left hand taps through browser tabs ---
    # The mirror of Alt-Tab; which hand holds the modifier is the only thing that tells them apart.
    "ctrl_tab_enabled": True,
    "ctrl_tab_hold_s": 0.6,            # right fist held this long opens the session
    "ctrl_tab_repeat_s": 0.35,         # re-tap rate while the left pinch stays held
    "ctrl_tab_session_timeout_s": 30.0,
    # Historical: this guard existed only because a right fist was ALSO the close-flick (minimise).
    # The flick is gone, so a right fist means Ctrl whenever Ctrl-Tab is on. The key is kept so an
    # existing kinesis_config.json still loads; it no longer changes behaviour.
    #   two_hands  = (legacy default, now inert)
    #   left_pinch = (legacy, now inert)
    #   off        = (legacy, now inert)
    "ctrl_tab_flick_guard": "two_hands",
    # The open-hand lateral swipe no longer switches tabs - the two-hand gesture replaced it.
    # "tab" restores the old binding; "none" leaves the swipe detected but silent.
    "swipe_action": "none",
    "alt_tab_repeat_s": 0.35,          # held right pinch repeats Tab
    "ptt_arm_s": 0.15,                 # shaka must hold this long before the dictation hotkey
    # The push-to-talk hotkey the shaka gesture holds. ctrl+space is the Windows default binding
    # in Handy (https://github.com/cjpais/Handy), so dictation works with no setup. Mirror any
    # remap here.
    "ptt_keys": ["ctrl", "space"],
    "scroll_gain": 1.0,
    "scroll_adaptive": True,
    "scroll_adaptive_k": 1.4,
    "scroll_max_per_frame": 240,

    # --- windows ---
    "window_title_blocklist": [
        "nvidia geforce overlay",
        "windows input experience",
        "program manager",
        "microsoft text input application",
        "default ime",
        "windows shell experience host",
    ],

    # --- fullscreen ---
    "youtube_title_hint": "youtube",
    "youtube_fs_key": "f",
    "generic_fs_key": "f11",
    # --- force close: CTRL+ALT+F4 on the window you are looking at ---
    # The shape has to be one the other gestures cannot make, and both fists are already spoken for
    # (left = hold Alt, right = hold Ctrl) - which is why an earlier draft reached for a pinch and had
    # to be abandoned: thumb+index is the left click and thumb+middle the right click, so a pinch is
    # the single most-travelled shape in the app.
    # BOTH HANDS OPEN AND STILL is free: the screenshot gesture needs both hands open AND MOVING
    # (screenshot_fist_px), and a hold requires stillness, so the two can never both be true.
    "force_close_enabled": True,
    "force_close_hold_s": 1.2,         # long: closing a window is not a reflex
    "force_close_move_px": 60.0,       # both hands must stay this close to where they started
    "force_close_cooldown_s": 2.5,
    "force_close_keys": ["ctrl", "alt", "f4"],
    "force_close_requires_target": True,   # never fall back to the foreground window

    # --- desk geometry ---
    "geometry_enabled": True,          # use camera/monitor physical data in gaze calibration
    "assumed_distance_mm": 700.0,      # used before a gaze calibration says otherwise
    "camera_name": "",                 # override the detected camera name (for the FoV table)
    "camera_fov_deg": 0.0,             # 0 = from the table, else your own diagonal FoV
    "bezel_mm": 10.0,                  # physical gap between active areas (Windows hides bezels)
    "monitor_mm_overrides": [],        # [[width_mm, height_mm], ...] per monitor, left to right
    # Which screens Kinesis may use, by device name. Empty = every screen. Set on first run.
    "enabled_monitors": [],
    "monitors_configured": False,
    # F2 opens the settings window while running; "none" disables it. open_settings is the
    # --settings launch flag and is not meant to be saved.
    "settings_hotkey": "f2",
    "open_settings": False,
    # Startup steps. Both default ON because both were asked for as run.bat behaviour, and both
    # are cheap to turn off with --no-calibrate / --no-settings from the launcher.
    #   force_gaze_calibration  run the gaze wizard before the loop starts, every run. A model
    #                           fitted at one seat position degrades when you move, and a stale
    #                           model is exactly what produces the "jittery pointer" report.
    #   settings_at_start      open the settings window on every run so the screens can be picked
    #                           per session (which displays to track changes with how you sit).
    "force_gaze_calibration": True,
    "settings_at_start": True,
    "startup_calibration": "full",     # full | quick - which sweep to run at startup
    # Gesture-Maps: where the user's map is saved, and the marketplace endpoint (set by deploy.py)
    "gesture_map_path": "",
    "marketplace_api": "",
    "map_confirm_frames": 2,
    "scale_reference": "eye_corners",  # eye_corners | ipd - the physical span used for distance
    "eye_corner_mm": 90.0,             # outer eye corner span; measure yours for better distance
    "ipd_mm": 63.0,
    "palm_mm": 90.0,                   # wrist to middle knuckle, for hand distance
    "distance_warn_fraction": 0.25,    # warn when the seat distance drifts this much
    "distance_min_mm": 300.0,          # closer than this, gaze is unreliable
    "distance_max_mm": 1400.0,         # further than this, distance (and gaze) degrade

    # --- gaze (EyeTrax) ---
    "gaze_enabled": True,
    "gaze_model_path": "gaze_model.pkl",
    "gaze_smoother": "kalman_ema",      # kalman_ema | kalman | none
    "gaze_ema_alpha": 0.25,             # higher = smoother, more lag (eyetrax convention)
    "gaze_hz": 20.0,                    # face landmarking is rate limited; hands stay at camera rate
    "gaze_max_age_s": 0.6,              # a stale point stops counting as "looking at"
    "gaze_target_enabled": True,        # gaze selects the target window for gestures
    "gaze_target_refresh_s": 0.1,
    "gaze_scroll_mode": "edge",         # edge | off
    "gaze_scroll_edge": 0.12,           # fraction of screen height at top/bottom
    "gaze_scroll_dwell_s": 0.25,        # how long to hold the gaze there before it engages
    "gaze_scroll_ramp_s": 0.5,          # ramp to full speed over this long
    "gaze_scroll_speed": 480,           # wheel units per second at full strength
    "gaze_scroll_cooldown_s": 0.4,
    "gaze_scroll_warp_cursor": True,    # park the cursor on the gaze point so the wheel lands there

    # --- virtual camera ---
    "vcam_enabled": True,
    "vcam_mode": "passthrough",         # passthrough | overlay | off
    "vcam_width": 640,
    "vcam_height": 480,
    "vcam_fps": 30,
    "vcam_overlay_size": [1920, 1080],
    "vcam_backend": "",                 # obs | unitycapture | "" = auto
    "vcam_show_landmarks": True,
    "vcam_show_hud": True,

    # --- gaze overlay look (see gazevis.py) ---
    "vcam_style": "comet",             # pointer|comet|path|heatmap|heatmap_comet|none
    "vcam_theme": "ember",             # ember|cyan|violet|lime|ice
    "vcam_trail_decay": 0.86,          # buffer multiplier per frame: higher = longer trail
    "vcam_tail_points": 48,            # how many samples the plotted path keeps
    "vcam_glow": 1.15,                 # bloom strength; 0 disables the blur pass
    "vcam_heat_radius": 26,            # heatmap stamp radius in canvas pixels
    "vcam_heat_gain": 1.35,            # heatmap contrast
    "vcam_visual_scale": 3,            # render layers at 1/N resolution, upscale on composite
                                       # (1 = full quality and full cost; 3 = ~12x cheaper)

    # --- gesture exclusivity ---
    "gesture_lock": True,              # one gesture owns the hand until the hand opens again
    "gesture_lock_timeout_s": 6.0,     # safety: never stay locked longer than this
    "gesture_lock_open_fingers": 4,    # "open again" = at least this many fingers extended
    # --- what the held pinches do ---
    # Thumb+ring was the pinch scroll; eye-gaze scrolling replaced it, so the ring pinch now carries
    # the standard drag (text selection, moving files) and the pinky pinch is unbound.
    "pinch_ring_action": "drag",       # drag | scroll | none
    "pinch_pinky_action": "none",      # drag | none
    # Minimise / maximise: pinch everything (all four fingertips to the thumb) and drag down to
    # minimise, up to maximise. The old open->fist flick is gone - the fist is the Alt-Tab modifier
    # and was minimising whatever had focus every time either tab gesture was attempted.
    "claw_minimise": True,
    "claw_travel_px": 140.0,           # how far the hand must travel for it to count
    "claw_window_s": 1.2,              # and how quickly, before it was just a slow move
    # Maximise / fullscreen is the second exit from the SAME five-digit pinch: instead of dragging
    # it, SPREAD it - all four fingertips leaving the thumb together.
    #
    # Measured, not guessed: a relaxed release of a claw and a deliberate five-finger spread end in
    # geometrically the same place (the slowest fingertip sits 1.58 hand-scales from the thumb when
    # you let go, 1.63 when you spread), and mean fingertip width does not separate them either
    # (2.08 relaxed, 1.57-2.62 depending on spread radius). There is NO static threshold that tells
    # them apart, because letting go of a claw IS opening your hand.
    #
    # So the discriminator is not the ending, it is the START: the claw must have been HELD first.
    # `claw_settle_s` requires the five-digit pinch to be steady for that long before a spread means
    # anything, and `claw_spread_frames` requires the spread itself to be a single decisive event
    # rather than a drift. A hand that was mid-gesture - swiping, clicking, coming out of a
    # transition - cannot pass through a settled claw on the way somewhere else.
    "claw_spread": True,
    "claw_spread_margin": 0.45,        # extra hand-scales past pinch_off every tip must clear
    "claw_spread_frames": 2,           # the spread must read across this many frames, not one
    "claw_settle_s": 0.18,             # hold the claw this long first, or it was never a claw
    # System volume: index + pinky out (middle and ring curled, so it is NOT the shaka, which needs
    # the thumb out too), held, then the hand moves up or down to raise or lower it.
    #
    # "Soft" is three separate things, because a naive tap-per-frame is unusable on a real machine:
    #   volume_deadband_px   movement below this does nothing at all
    #   volume_step_px       palm travel that buys ONE step, so the hand sets the rate
    #   volume_max_steps     a hard ceiling per frame, so a fast sweep cannot flood the key queue
    #   volume_smooth        low-pass on the palm, so noise does not become steps
    # The Windows master volume is 100 steps of 2% each, so one step is ~2% - deliberately gentle.
    "volume_enabled": True,
    "volume_deadband_px": 4.0,
    "volume_step_px": 26.0,
    "volume_max_steps": 2,
    "volume_smooth": 0.35,
    "volume_release_frames": 3,
    "volume_keys_up": ("volumeup",),
    "volume_keys_down": ("volumedown",),
    # Screenshot: BOTH hands open -> both fists -> both open, fast. The two-hand requirement is the
    # whole safety story - a single left fist is the Alt-Tab modifier, so one hand can never take a
    # screenshot, and a two-hand fist is the only shape that can.
    #
    # The Alt-Tab collision is real and is handled by `screenshot_fist_px`: a fist held still is a
    # modifier, a fist that travels is a screenshot. BOTH hands must move, in the same direction, by
    # that much, inside `screenshot_window_s`. So a deliberate slow two-hand clench (the thing that
    # looks like it wants to be Alt-Tab) is far too slow and far too still to fire this.
    "screenshot_enabled": True,
    "screenshot_window_s": 0.45,          # the whole open->fist->open must happen this fast
    "screenshot_fist_px": 45.0,           # and BOTH fists must travel this far while closed
    "screenshot_fist_frames": 2,          # a fist must be seen this many frames before it counts
    "screenshot_cooldown_s": 1.2,         # so one flourish cannot fire repeatedly
    "screenshot_keys": ("printscreen",),
    "ring_confirm_frames": 2,          # ring pinch must hold this long before whatever it is bound
                                       # to starts (was scroll_confirm_frames)
    "drag_confirm_frames": 2,          # thumb-pinky drag must hold this long too (also keeps
                                       # the scroll-vs-drag arbiter deterministic)
    "scroll_release_frames": 3,        # pinch flicker must not end a scroll in progress
    "scroll_deadband_px": 1.5,         # per-frame hand movement treated as jitter
    "scroll_smooth": 0.45,             # EMA on the scrolling hand position (0 = raw)
    "alt_tab_session_timeout_s": 30.0, # Alt is force-released after this long
    # --- gaze-assisted clicking ---
    "gaze_click_tabs": True,           # click while looking at a browser tab -> switch to that tab
    "gaze_click_warp_delay_ms": 1.0,   # move the pointer to the gaze point, wait, then click
    "tab_strip_top_px": 6.0,           # logical px of drag region above the tabs (Chromium)
    "tab_strip_height_px": 40.0,       # logical height of the tab strip
    # --- dictation focus: click into the field you are looking at before the dictation hotkey ---
    "ptt_focus_mode": "auto",          # auto | uia | always | off  (auto clicks pages we cannot read)
    "ptt_focus_settle_ms": 40.0,       # gap between clicking the field and Ctrl+Space going out
    # --- two-hand pinch zoom: both index pinches, hands apart = in, together = out ---
    "zoom_pinch_enabled": True,
    "zoom_keys_in": ["ctrl", "="],
    "zoom_keys_out": ["ctrl", "-"],
    "zoom_step_px": 22.0,              # pinch-point travel that earns one zoom step
    "zoom_deadband_px": 3.0,           # per-frame jitter floor
    "zoom_max_steps_per_frame": 3,     # a fast sweep must not flood the key queue
    "zoom_confirm_frames": 2,
    "zoom_session_timeout_s": 20.0,
    # --- gaze focus: the window you look at becomes the focused window ---
    "gaze_focus_enabled": True,
    # 0.15 s was far too short. Gaze jitters, so a 150 ms dwell completed while the point was
    # still sweeping across the screen and focus landed on whatever the jitter happened to cross.
    # 0.9 s is long enough that the point has to settle on a window on purpose.
    "gaze_focus_dwell_s": 0.9,
    "gaze_focus_cooldown_s": 1.5,      # don't fight the user straight after focusing
    "gaze_focus_skip_fullscreen": True,
    # The point must also be STEADY before it counts as "looking at" something: a window only
    # becomes the dwell candidate once the gaze has stopped sweeping.
    "gaze_focus_stable_s": 0.25,       # the point must stay within tolerance for this long
    "gaze_focus_stable_px": 40.0,      # ... within this many px of where it was
    # --- desktop overlay (click-through, over the whole desktop) ---
    "desktop_overlay": False,
    "desktop_overlay_fps": 30.0,
    "desktop_overlay_style": "",       # "" follows vcam_style
    "desktop_overlay_theme": "",       # "" follows vcam_theme
    # --- gaze metaball (kinesis/metaball.py) ---
    # A white outlined blob that swells with dwell, leaves a shrinking trail, and animates to new
    # positions at up to 180 fps. Enabled through desktop_overlay_style = "metaball", which reuses
    # the existing click-through overlay windows (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
    # | WS_EX_NOACTIVATE) rather than adding a second overlay path.
    "metaball_scale": 3,               # field render scale; 3 is what makes 180 fps reachable
    "metaball_max_fps": 180.0,
    "metaball_max_diameter_px": 80.0,  # the head's largest size
    "metaball_min_diameter_px": 10.0,  # its resting size
    "metaball_grow_s": 1.2,            # dwell time to reach the maximum
    "metaball_dwell_reset_px": 90.0,   # moving this far starts a new dwell
    "metaball_shrink_s": 0.5,          # a trail blob's life, shrinking to radius 0
    "metaball_trail_spacing_px": 26.0, # head travel between trail blobs
    "metaball_trail_max": 48,
    "metaball_trail_radius_ratio": 0.55,
    "metaball_follow_tau_s": 0.030,    # head easing time constant -> "native speed"
    "metaball_max_speed_px_s": 20000.0,
    "metaball_outline_px": 3.0,        # SCREEN pixels, not field pixels
    "metaball_outline_gain": 1.0,
    "metaball_glow_sigma_px": 9.0,
    "metaball_glow_gain": 0.34,        # the faint white glow, inside and outside
    "metaball_inner_gain": 0.20,       # the wash inside the outline
    "metaball_isolevel": 0.5,
    "metaball_gain": 1.0,
    "desktop_overlay_scale": 6,        # internal render scale (cheaper than the vcam path)
    "desktop_overlay_alpha_gain": 1.25,
    "desktop_overlay_hotkey": "insert",  # toggles it at runtime
    "desktop_overlay_panels_per_tick": 2,  # monitors redrawn per tick (others keep decaying)
    # --- aim ---
    "aim_enabled": True,
    "aim_hysteresis_deg": 7.0,         # must beat the current monitor by this margin
    "aim_max_distance_deg": 42.0,      # beyond this, aim is unknown
    "aim_smooth_min_cutoff": 0.9,
    "aim_smooth_beta": 0.02,
    "aim_yaw_sign": 1.0,               # heuristic fallback only; calibration is sign-agnostic

    # --- ui ---
    "preview": True,
    "preview_width": 480,
    "show_landmarks": True,
    "hud_detail": True,
    "latency_report": False,
    "dry_run": False,
    "exclusive": False,                # True = cursor only, no gesture actions
}


@dataclass
class MonitorTarget:
    """One monitor's calibrated pointing angles."""
    index: int
    name: str
    left: int
    top: int
    right: int
    bottom: int
    yaw: float
    pitch: float
    yaw_std: float = 0.0
    pitch_std: float = 0.0

    @property
    def centre(self):
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


@dataclass
class Calibration:
    targets: List[MonitorTarget] = field(default_factory=list)
    captured_at: str = ""
    yaw_flipped: bool = False

    def to_json(self) -> dict:
        return {
            "captured_at": self.captured_at,
            "yaw_flipped": self.yaw_flipped,
            "targets": [asdict(t) for t in self.targets],
        }

    @classmethod
    def from_json(cls, data: dict) -> "Calibration":
        return cls(
            targets=[MonitorTarget(**t) for t in data.get("targets", [])],
            captured_at=data.get("captured_at", ""),
            yaw_flipped=data.get("yaw_flipped", False),
        )


class Config:
    def __init__(self, data: Optional[dict] = None):
        self.data = dict(DEFAULTS)
        if data:
            self.data.update(data)

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def apply_overrides(self, overrides: Dict[str, str]):
        """--tune key=value pairs, coerced to the type of the existing default."""
        for key, raw in overrides.items():
            if key not in DEFAULTS:
                raise KeyError(f"unknown setting: {key}")
            want = type(DEFAULTS[key])
            if want is bool:
                value = str(raw).strip().lower() in ("1", "true", "yes", "on")
            elif want is int:
                value = int(float(raw))
            elif want is float:
                value = float(raw)
            else:
                value = str(raw)
            self.data[key] = value

    def save(self, path: Path = CONFIG_PATH):
        path.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if path.exists():
            try:
                return cls(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return cls()

    def diff_from_defaults(self) -> Dict[str, Any]:
        return {k: v for k, v in self.data.items() if DEFAULTS.get(k) != v}


def load_calibration(path: Path = CALIBRATION_PATH) -> Calibration:
    if path.exists():
        try:
            return Calibration.from_json(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, TypeError):
            pass
    return Calibration()


def save_calibration(cal: Calibration, path: Path = CALIBRATION_PATH):
    path.write_text(json.dumps(cal.to_json(), indent=2) + "\n", encoding="utf-8")


def parse_tune_args(pairs: List[str]) -> Dict[str, str]:
    out = {}
    for item in pairs or []:
        if "=" not in item:
            raise ValueError(f"--tune expects key=value, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out
