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
    "flick_window_s": 0.4,             # open<->fist transition must happen this fast
    "flick_settle_s": 0.12,            # pose must be stable this long before it counts
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
    # A right fist is also the close-flick (minimise), so this collision needs a rule:
    #   two_hands  = a right fist never minimises while the left hand is in frame (default)
    #   left_pinch = only while the left hand is actually pinching
    #   off        = the flick always wins
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
    "gaze_focus_dwell_s": 0.15,        # look at a window this long and it comes forward
    "gaze_focus_cooldown_s": 1.5,      # don't fight the user straight after focusing
    "gaze_focus_skip_fullscreen": True,
    # --- desktop overlay (click-through, over the whole desktop) ---
    "desktop_overlay": False,
    "desktop_overlay_fps": 30.0,
    "desktop_overlay_style": "",       # "" follows vcam_style
    "desktop_overlay_theme": "",       # "" follows vcam_theme
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
