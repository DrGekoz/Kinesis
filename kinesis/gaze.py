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

import pickle
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from .filters import GazeStabiliser
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
    eye_span_px: float = 0.0    # outer eye corners, apparent width in pixels
    distance_mm: float = 0.0    # estimated distance from the screen (see geometry)
    distance_ok: bool = True    # within range and close to the calibration distance
    distance_note: str = ""
    rejected: bool = False      # this sample was discarded as an excursion

    def as_point(self) -> Optional[Tuple[int, int]]:
        return (int(self.x), int(self.y)) if self.valid else None


class _LandmarkTap:
    """Records the face-landmarker result EyeTrax already computed.

    EyETrax normalises its features by the outer-eye-corner distance, which is exactly the
    monocular scale reference needed to estimate how far away the face is - but it does not return
    it. Intercepting `detect_for_video` gets that quantity from the inference that was happening
    anyway, instead of running the landmarker a second time every frame.

    Deliberately duck-typed around the model: if EyeTrax changes its internals the tap is simply
    not installed (see GazeEngine._install_landmark_tap) and distance reporting degrades to
    "unknown" rather than breaking gaze.
    """

    def __init__(self, landmarker):
        self._landmarker = landmarker
        self.last = None

    def __getattr__(self, item):
        return getattr(self._landmarker, item)

    def detect_for_video(self, image, timestamp_ms):
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        self.last = result
        return result


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
        self._tap = None
        self._camera = None
        self._span_ema = 0.0
        # Outlier rejection on the predicted point. Measured necessity: see GazeStabiliser - the
        # gaze model is rank-deficient and a 0.2% landmark wobble throws the pointer a million
        # pixels, so the excursion has to be rejected rather than smoothed.
        self._stab = GazeStabiliser(
            reject_px=float(self.cfg.get("gaze_reject_px", 260.0)),
            window=int(self.cfg.get("gaze_stabilise_window", 5)),
            speed_max_px_s=float(self.cfg.get("gaze_reject_speed_px_s", 9000.0)),
            deadband_px=float(self.cfg.get("gaze_stabilise_deadband_px", 3.0)),
            deadband_gain=float(self.cfg.get("gaze_stabilise_deadband_gain", 2.0)),
            slew_px=float(self.cfg.get("gaze_slew_px", 900.0)),
        )
        # The last point the eyes were NOT blinked at. A blink must not move the pointer, and it
        # must not leave it parked at the last pre-blink value either - that is how a blink ends up
        # reading as "looked down" (the eyes roll, then the decay holds the old y).
        self._last_open: Optional[Tuple[float, float]] = None
        self._blink_since = 0.0
        self._blink_count_reported = 0
        # is_calibrated unpickles the model, so the verdict is memoised. Cleared by
        # set_model_path(), by train() and by stop()-then-start(), so a freshly written model
        # is never reported as the old (or a stale True) answer.
        self._loadable_cache = False
        self.error: Optional[str] = None
        # stats
        self.frames = 0
        self.faces = 0
        self.blinks = 0
        self.misses = 0
        self.predict_ms = 0.0

    # ------------------------------------------------------------------ lifecycle
    def set_model_path(self, path) -> None:
        """Point at a different model file and forget the memoised verdict for the old one.

        Tests and the retrain tool both swap the path; without clearing the cache the first
        verdict would be reported for every later file, which is the same class of lie as the
        original `.exists()`.
        """
        self.model_path = Path(str(path))
        if not self.model_path.is_absolute():
            from .config import ROOT
            self.model_path = ROOT / self.model_path
        self._loadable_cache = False

    @property
    def is_calibrated(self) -> bool:
        """Is there a USABLE model? A file that exists but cannot be loaded is not calibrated.

        This used to be a bare `.exists()`, and that turned out to be a lie that broke every
        diagnosis downstream. A truncated or non-pickle file exists, so the app printed
        "gaze: model gaze_model.pkl" and the calibration prompt was skipped - then
        `start()` failed with `invalid load key, 'x'` and the main loop measured nothing while
        every status line claimed eye tracking was live.

        The check is now a REAL load, not a magic byte. A magic byte was tried first and is not
        enough: a 4-byte truncated pickle (`\\x80\\x04\\x95\\x01` - the protocol and frame header
        with no model in it) passes any byte check and then raises on unpickling. Unpickling the
        file is the only test that matches what `start()` will actually do, it costs a few
        milliseconds, and it happens once at startup.

        Unpickling executes the file's opcodes, so this only ever runs against a path from the
        user's own config on their own machine - the same trust `start()` already places in it.
        """
        if not self._loadable_cache:
            try:
                if not self.model_path.is_file():
                    return False
                with self.model_path.open("rb") as fh:
                    if fh.read(1) != b"\x80":            # cheap reject first: not a pickle at all
                        return False
                    fh.seek(0)
                    obj = pickle.load(fh)
            except Exception:
                return False
            self._loadable_cache = bool(obj) or obj is not None
        return self._loadable_cache

    def start(self) -> bool:
        if not self.enabled:
            return False
        if not self.is_calibrated:
            if self.model_path.exists():
                self.error = (f"{self.model_path.name} is not a usable gaze model "
                              f"(corrupt or not a pickle) - re-fit it with "
                              f"tools\\retrain_gaze_model.py, or recalibrate with "
                              f"calibrate_gaze.bat")
            else:
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
        self._install_landmark_tap()
        print(f"[gaze] model {self.model_path.name} loaded, smoother={kind}"
              + (f", seat distance at calibration {self.calibration_distance_mm:.0f} mm"
                 if self.calibration_distance_mm else ""))
        return True

    # ------------------------------------------------------------------ distance
    @property
    def metadata_path(self) -> Path:
        return self.model_path.with_suffix(".json")

    def load_metadata(self) -> dict:
        """Calibration sidecar: seat distance, camera and layout the model was trained with."""
        try:
            import json
            if self.metadata_path.exists():
                return json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def save_metadata(self, data: dict) -> None:
        try:
            import json
            self.metadata_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:                      # pragma: no cover
            print(f"[gaze] could not write {self.metadata_path.name}: {exc}")

    @property
    def calibration_distance_mm(self) -> float:
        return float(self.load_metadata().get("distance_mm", 0.0) or 0.0)

    def set_camera(self, camera) -> None:
        """The geometry.CameraInfo for the active camera (focal length in pixels)."""
        self._camera = camera

    @property
    def state(self) -> GazeState:
        return self._state

    def measure_distance(self, frame) -> float:
        """Seat distance from the frame the last feature extraction used.

        Public entry point for the calibration wizard, which drives feature extraction directly
        rather than through update(). Returns millimetres, or 0 when it cannot tell.
        """
        self._update_distance(frame)
        return self._state.distance_mm

    def _install_landmark_tap(self) -> None:
        """See _LandmarkTap. Silent no-op if EyeTrax's internals do not match."""
        try:
            landmarker = getattr(self._estimator, "_face_landmarker", None)
            if landmarker is None or isinstance(landmarker, _LandmarkTap):
                return
            self._tap = _LandmarkTap(landmarker)
            self._estimator._face_landmarker = self._tap
        except Exception:
            self._tap = None

    def _update_distance(self, frame) -> None:
        """Estimate seat distance from the eye span the landmarker just measured.

        Zero extra inference: the landmarks come from the tap on EyeTrax's own detect call.
        """
        if self._tap is None or self._tap.last is None or self._camera is None:
            return
        faces = getattr(self._tap.last, "face_landmarks", None)
        if not faces:
            return
        try:
            from .geometry import estimate_distance_mm
            lm = faces[0]
            h, w = frame.shape[:2]
            # landmarks 33 / 263 are the outer eye corners in MediaPipe's face mesh, normalised
            # against different axes, so the span has to be rebuilt in pixels
            dx = (lm[263].x - lm[33].x) * w
            dy = (lm[263].y - lm[33].y) * h
            span = (dx * dx + dy * dy) ** 0.5
            if span <= 1.0:
                return
            self._span_ema = span if not self._span_ema else 0.75 * self._span_ema + 0.25 * span
            ref = (float(self.cfg["eye_corner_mm"])
                   if str(self.cfg.get("scale_reference", "eye_corners")) == "eye_corners"
                   else float(self.cfg["ipd_mm"]))
            dist = estimate_distance_mm(self._span_ema, self._camera, ref)
            if dist > 0:
                self._state.eye_span_px = self._span_ema
                self._state.distance_mm = (
                    dist if not self._state.distance_mm
                    else 0.7 * self._state.distance_mm + 0.3 * dist)
                self._state.distance_ok, self._state.distance_note = self._distance_verdict(
                    self._state.distance_mm)
        except Exception:
            return

    def _distance_verdict(self, distance_mm: float) -> Tuple[bool, str]:
        lo = float(self.cfg["distance_min_mm"])
        hi = float(self.cfg["distance_max_mm"])
        if distance_mm < lo:
            return False, f"very close ({distance_mm:.0f} mm) - gaze gets noisy"
        if distance_mm > hi:
            return False, f"far away ({distance_mm:.0f} mm) - gaze degrades with distance"
        cal = self.calibration_distance_mm
        if cal > 0:
            drift = abs(distance_mm - cal) / cal
            limit = float(self.cfg["distance_warn_fraction"])
            if drift > limit:
                return False, (f"seat moved {drift * 100:.0f}% since calibration "
                               f"({cal:.0f} -> {distance_mm:.0f} mm) - recalibrate gaze")
        return True, ""

    def stop(self):
        if self._estimator is not None:
            try:
                self._estimator.close()
            except Exception:
                pass
            self._estimator = None
        # The calibration wizard may have replaced the model while we were not looking, so the
        # memoised verdict is dropped here: a later start() must re-read the file rather than
        # trust a verdict computed before the wizard ran.
        self._loadable_cache = False
        # The stabiliser's history belongs to the old model. Keeping it would let the first sample
        # from a newly calibrated model be judged against the previous one's points.
        self._stab.reset()
        self._last_open = None
        self._blink_since = 0.0

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
        self._update_distance(frame)     # free: reads the landmarks that call just produced
        if features is None:
            self.misses += 1
            self._state.face = False
            return self._decay(now)

        self.faces += 1
        self._state.face = True
        if blink:
            # A BLINK IS NOT A GAZE. Reported as the report made: while the eyelids close the eyes
            # roll, and the old code simply decayed the point, so the pointer inherited the last
            # pre-blink vertical value and slid down with the roll. Worse, `raw` kept feeding the
            # eye-cursor smoothing from a stale point. So a blink now:
            #   * invalidates the estimate outright (no stale x/y to inherit),
            #   * is never used to steer the cursor, and
            #   * resumes from the last OPEN point rather than from wherever the roll left it.
            self.blinks += 1
            if not self._blink_since:
                self._blink_since = now
            return self._decay(now, blink=True)
        self._blink_since = 0.0

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

        # Reject the excursion. Returns None when this sample is physically impossible, in which
        # case the last good point stands: the pointer freezes for one frame instead of being thrown
        # across the desk, and the next good sample is accepted on the frame it arrives.
        stable = self._stab(x, y, now)
        if stable is None:
            self._last_valid = now
            prev = self._state
            self._state = GazeState(x=prev.x, y=prev.y, valid=True, blink=False, age=0.0,
                                    raw=prev.raw, face=True, rejected=True)
            return self._state
        x, y = stable
        self._last_open = (x, y)
        self._last_valid = now
        self._state = GazeState(x=x, y=y, valid=True, blink=False, age=0.0, raw=(x, y), face=True)
        return self._state

    def _decay(self, now: float, blink: bool = False) -> GazeState:
        """Keep reporting the last point for a short grace period (a blink or a dropped frame is
        not "looked away"), then mark it invalid.

        A BLINK IS EXCLUDED ON PURPOSE. Reporting the previous point as `valid` while the eyelids
        are shut is what made a blink read as "looked down": the roll during the blink changed the
        eye geometry, and the pointer inherited a stale vertical value that the eye-cursor smoothing
        then carried on with. While blinking, the state is invalid so nothing downstream - the
        cursor, the gaze scroller, the window target, the focus dwell - can act on it. The last OPEN
        point is what the pointer falls back to when the eyes come back up, not a blink-era value.
        """
        age = now - self._last_valid if self._last_valid else 999.0
        if blink:
            self._state = GazeState(x=self._state.x, y=self._state.y, valid=False,
                                    blink=True, age=age, face=self._state.face)
        elif age > float(self.cfg["gaze_max_age_s"]):
            self._state = GazeState(x=self._state.x, y=self._state.y, valid=False,
                                    blink=False, age=age, face=self._state.face)
        else:
            self._state = GazeState(x=self._state.x, y=self._state.y, valid=True,
                                    blink=False, age=age, raw=self._state.raw,
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
        self._loadable_cache = False       # a new file: the old verdict is meaningless now

    def features_for(self, frame):
        """Raw feature extraction, for the calibration wizard."""
        if self._estimator is None:
            from eyetrax import GazeEstimator
            self._estimator = GazeEstimator()
            self._install_landmark_tap()
        return self._estimator.extract_features(frame)

    def status_line(self) -> str:
        if not self.enabled:
            return "gaze: off"
        if self._estimator is None:
            return f"gaze: not running ({self.error or 'not started'})"
        state = self._state
        dist = ""
        if state.distance_mm > 0:
            dist = f" {state.distance_mm / 10:.0f}cm away"
            if not state.distance_ok:
                dist += " (!)"
        if state.valid:
            return (f"gaze: {int(state.x)},{int(state.y)} age {state.age * 1000:.0f}ms"
                    f" face {'yes' if state.face else 'no'}{dist}")
        return (f"gaze: stale ({state.age:.1f}s) face {'yes' if state.face else 'no'}{dist}")
