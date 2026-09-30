"""Gaze via EyeTrax: our frames in, a smoothed virtual-desktop point out.

Deliberately not EyeTrax's own entry points. They open their own `VideoCapture` (a second camera
client, and frames that are not the ones the hands were tracked from) and their coordinate space is
the primary monitor only (`get_screen_size()` returns `get_monitors()[0]`), which on this machine
would leave three of four displays unpickable. Kinesis therefore feeds `GazeEstimator` the frame it
already captured and calibrates against the full virtual desktop.

Frame orientation matters: calibration and runtime must both see the *raw* camera frame. Kinesis
mirrors frames for display and for hand handedness, and mirroring changes the face yaw feature, so
the mirrored frame is never fed here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from .winapi import virtual_screen


@dataclass
class GazeState:
    x: float = 0.0
    y: float = 0.0
    valid: bool = False
    blink: bool = False
    age: float = 999.0          # seconds since the last valid estimate
    raw: Optional[Tuple[float, float]] = None
    face: bool = False          # a face was seen this frame

    def as_point(self) -> Optional[Tuple[int, int]]:
        return (int(self.x), int(self.y)) if self.valid else None


class GazeEngine:
    """Wraps eyetrax.GazeEstimator with rate limiting, smoothing and staleness handling."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = bool(cfg["gaze_enabled"])
        self.model_path = Path(str(cfg["gaze_model_path"]))
        if not self.model_path.is_absolute():
            from .config import ROOT
            self.model_path = ROOT / self.model_path
        self._estimator = None
        self._smoother = None
        self._state = GazeState()
        self._last_valid = 0.0
        self._last_run = 0.0
        self._interval = 1.0 / max(float(cfg["gaze_hz"]), 1.0)
        self._owner = None
        self.error: Optional[str] = None
        # stats
        self.frames = 0
        self.faces = 0
        self.blinks = 0
        self.misses = 0
        self.predict_ms = 0.0

    # ------------------------------------------------------------------ lifecycle
    @property
    def is_calibrated(self) -> bool:
        return self.model_path.exists()

    def start(self) -> bool:
        if not self.enabled:
            return False
        if not self.is_calibrated:
            self.error = (f"no gaze model at {self.model_path} - run calibrate_gaze.bat")
            print(f"[gaze] {self.error}")
            return False
        try:
            from eyetrax import GazeEstimator
            from eyetrax.filters import KalmanEMASmoother, KalmanSmoother, NoSmoother, make_kalman
        except Exception as exc:                     # pragma: no cover - environment failure
            self.error = f"eyetrax import failed: {exc}"
            print(f"[gaze] {self.error}")
            return False
        try:
            self._estimator = GazeEstimator()
            self._estimator.load_model(self.model_path)
        except Exception as exc:
            self.error = f"gaze model load failed: {exc}"
            print(f"[gaze] {self.error}")
            return False

        kind = str(self.cfg["gaze_smoother"]).lower()
        if kind == "kalman":
            self._smoother = KalmanSmoother(make_kalman())
        elif kind == "none":
            self._smoother = NoSmoother()
        else:
            # note: KalmanSmoother.tune() opens its own camera and a fullscreen window, so it is
            # never called from here - the EMA variant needs no tuning pass
            self._smoother = KalmanEMASmoother(make_kalman(),
                                               ema_alpha=float(self.cfg["gaze_ema_alpha"]))
        print(f"[gaze] model {self.model_path.name} loaded, smoother={kind}")
        return True

    def stop(self):
        if self._estimator is not None:
            try:
                self._estimator.close()
            except Exception:
                pass
            self._estimator = None

    # ------------------------------------------------------------------ per frame
    def update(self, frame, now: Optional[float] = None) -> GazeState:
        """frame must be the RAW (unmirrored) BGR camera frame."""
        now = now if now is not None else time.perf_counter()
        if self._estimator is None or frame is None:
            return self._decay(now)

        # gaze does not need camera rate: rate limiting keeps the hand loop fast
        if now - self._last_run < self._interval:
            return self._decay(now)
        self._last_run = now
        self.frames += 1

        t0 = time.perf_counter()
        try:
            features, blink = self._estimator.extract_features(frame)
        except Exception as exc:                     # pragma: no cover
            self.error = f"extract_features failed: {exc}"
            return self._decay(now)
        if features is None:
            self.misses += 1
            self._state.face = False
            return self._decay(now)

        self.faces += 1
        self._state.face = True
        if blink:
            self.blinks += 1
            return self._decay(now, blink=True)

        try:
            x, y = self._estimator.predict(np.array([features]))[0]
        except Exception as exc:                     # pragma: no cover
            self.error = f"predict failed: {exc}"
            return self._decay(now)
        self.predict_ms = (time.perf_counter() - t0) * 1000.0

        if self._smoother is not None:
            x, y = self._smoother.step(int(x), int(y))
        left, top, width, height = virtual_screen()
        x = min(max(float(x), left), left + width - 1)
        y = min(max(float(y), top), top + height - 1)
        self._last_valid = now
        self._state = GazeState(x=x, y=y, valid=True, blink=False, age=0.0, raw=(x, y), face=True)
        return self._state

    def _decay(self, now: float, blink: bool = False) -> GazeState:
        """Keep reporting the last point for a short grace period (a blink or a dropped frame is
        not "looked away"), then mark it invalid."""
        age = now - self._last_valid if self._last_valid else 999.0
        if age > float(self.cfg["gaze_max_age_s"]):
            self._state = GazeState(x=self._state.x, y=self._state.y, valid=False,
                                    blink=blink, age=age, face=self._state.face)
        else:
            self._state = GazeState(x=self._state.x, y=self._state.y, valid=True,
                                    blink=blink, age=age, raw=self._state.raw,
                                    face=self._state.face)
        return self._state

    # ------------------------------------------------------------------ calibration support
    def train(self, samples) -> None:
        """samples: list of (features, screen_x, screen_y). Used by calibrate_gaze.py."""
        from eyetrax import GazeEstimator
        if self._estimator is None:
            self._estimator = GazeEstimator()
        X = np.array([s[0] for s in samples], dtype=np.float32)
        y = np.array([[float(s[1]), float(s[2])] for s in samples], dtype=np.float32)
        self._estimator.train(X, y)
        self._estimator.save_model(self.model_path)

    def features_for(self, frame):
        """Raw feature extraction, for the calibration wizard."""
        if self._estimator is None:
            from eyetrax import GazeEstimator
            self._estimator = GazeEstimator()
        return self._estimator.extract_features(frame)

    def status_line(self) -> str:
        if not self.enabled:
            return "gaze: off"
        if self._estimator is None:
            return f"gaze: not running ({self.error or 'not started'})"
        state = self._state
        if state.valid:
            return (f"gaze: {int(state.x)},{int(state.y)} age {state.age * 1000:.0f}ms "
                    f"face {'yes' if state.face else 'no'}")
        return f"gaze: stale ({state.age:.1f}s) face {'yes' if state.face else 'no'}"
