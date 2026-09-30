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
    "alt_tab_repeat_s": 0.35,          # held right pinch repeats Tab
    "ptt_arm_s": 0.15,                 # shaka must hold this long before Ctrl+Space
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

    # --- gaze (EyeTrax) ---
    "gaze_enabled": True,
    "gaze_model_path": "gaze_model.pkl",
    "gaze_smoother": "kalman_ema",      # kalman_ema | kalman | none
    "gaze_ema_alpha": 0.25,             # higher = smoother, more lag (eyetrax convention)
    "gaze_hz": 10.0,                    # face landmarking is rate limited; hands stay at camera rate
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
