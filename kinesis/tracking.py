"""Camera + inference pipeline.

Two background threads and a newest-frame-only policy:

  capture thread   cap.read() in a tight loop, keeping only the most recent frame
  inference thread takes the newest frame (never a queued stale one) and runs MediaPipe

The main thread never blocks on the camera or the model - it reads whatever the inference thread
last produced. If inference is slower than the camera, frames are *dropped*, not queued, which is
what keeps end-to-end latency flat instead of growing with the backlog.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import mediapipe as mp

from .filters import HandFilter
from .pose import PoseDetector, HandPose

BACKENDS = {
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "default": cv2.CAP_ANY,
}


@dataclass
class RawHand:
    handedness: str
    points_norm: List[Tuple[float, float]]
    world_points: Optional[List[Tuple[float, float, float]]]
    score: float


@dataclass
class TrackingStats:
    frames_captured: int = 0
    frames_dropped: int = 0
    frames_inferred: int = 0
    capture_fps: float = 0.0
    inference_fps: float = 0.0
    inference_ms: float = 0.0
    end_to_end_ms: float = 0.0
    last_timings: dict = field(default_factory=dict)


class CameraThread(threading.Thread):
    """Keeps only the newest frame. Anything the consumer does not pick up is counted as dropped."""

    def __init__(self, index: int = 0, width: int = 640, height: int = 480, fps: int = 30,
                 backend: str = "dshow"):
        super().__init__(daemon=True, name="kinesis-capture")
        self.index, self.width, self.height = index, width, height
        self.target_fps = fps
        self.backend_name = backend
        self._lock = threading.Lock()
        self._frame = None
        self._seq = 0
        self._consumed = 0
        # NAMED _halt, NOT _stop - `Thread` has a private `self._stop()` METHOD that its own start()
        # calls, and shadowing it breaks every restart. See the note in InferenceThread.
        self._halt = threading.Event()
        self.captured = 0
        self.opened = False
        self.error: Optional[str] = None
        self._fps_times: List[float] = []

    def open(self) -> bool:
        api = BACKENDS.get(self.backend_name, cv2.CAP_ANY)
        cap = cv2.VideoCapture(self.index, api) if api != cv2.CAP_ANY else cv2.VideoCapture(self.index)
        if not cap.isOpened() and api != cv2.CAP_ANY:
            cap.release()
            cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            self.error = f"could not open camera {self.index}"
            return False
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.target_fps)
        self._cap = cap
        self.opened = True
        return True

    def verify_streaming(self, seconds: float = 3.0) -> bool:
        """IS A REAL FRAME FLOWING? `isOpened()` does not mean yes, and believing it is a lie.

        Measured failure: with VRChat running, the C920 still enumerates, `VideoCapture`
        still reports `isOpened() == True` and even hands back a plausible 640x480 size - and
        then every single `read()` returns `(False, None)`. ffmpeg, a completely separate
        stack, says "Could not run graph (sometimes caused by a device already in use by other
        application)". A webcam is a single-client device.

        The consequence of not checking this is the exact bug class this project keeps hitting:
        the app looks alive (camera open, loop spinning, HUD drawing) while producing nothing,
        and every status line blames the wrong layer. So the app PROVES frames arrive before
        it claims to be running.

        Must be called BEFORE the capture thread is started, because `run()` is what consumes
        frames.
        """
        if not self.opened:
            return False
        deadline = time.perf_counter() + max(seconds, 0.1)
        got = 0
        while time.perf_counter() < deadline:
            ok, frame = self._cap.read()
            if ok and frame is not None:
                got += 1
                if got >= 3:
                    self.error = ""
                    return True
            time.sleep(0.03)
        self.error = (
            f"camera {self.index} ({self.backend_name}) opened but delivered NO frames - "
            f"another program is holding the webcam. Close it (VRChat, Discord camera, Zoom, "
            f"OBS, Teams, the Windows Camera app) and start Kinesis again. "
            f"Get-PnpDevice -Class Camera shows the device itself is fine.")
        return False

    @property
    def actual_size(self):
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)) if self.opened else self.width
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) if self.opened else self.height
        return w or self.width, h or self.height

    def run(self):
        while not self._halt.is_set():
            ok, frame = self._cap.read()
            if not ok or frame is None:
                time.sleep(0.005)
                continue
            now = time.perf_counter()
            with self._lock:
                self._frame = frame
                self._seq += 1
            self.captured += 1
            self._fps_times.append(now)
            if len(self._fps_times) > 60:
                self._fps_times.pop(0)

    def latest(self):
        """Returns (frame, seq, captured_at) or (None, seq, 0)."""
        with self._lock:
            if self._frame is None:
                return None, self._seq, 0.0
            if self._seq == self._consumed:
                return None, self._seq, 0.0
            self._consumed = self._seq
            return self._frame, self._seq, time.perf_counter()

    def stop(self):
        self._halt.set()
        if self.opened:
            try:
                self._cap.release()
            except Exception:
                pass

    @property
    def fps(self) -> float:
        if len(self._fps_times) < 2:
            return 0.0
        span = self._fps_times[-1] - self._fps_times[0]
        return (len(self._fps_times) - 1) / span if span > 0 else 0.0


class InferenceThread(threading.Thread):
    def __init__(self, camera: CameraThread, cfg):
        super().__init__(daemon=True, name="kinesis-inference")
        self.camera = camera
        self.cfg = cfg
        self._lock = threading.Lock()
        self._hands: List[RawHand] = []
        self._frame = None
        self._raw_frame = None
        self._stamp = 0.0
        self._seq = -1
        # NAMED _halt, NOT _stop. `Thread` has a private `self._stop()` METHOD that its own
        # `Thread.start()` calls. Assigning an Event to `self._stop` shadows it, and every restart
        # dies with `TypeError: 'Event' object is not callable` from inside CPython's threading.py.
        # Cost one real crash to find; keeping the name distinct so it cannot come back.
        self._halt = threading.Event()
        self.inferred = 0
        self.dropped = 0
        self.inference_ms = 0.0
        self.error: Optional[str] = None
        self.fatal = False           # model never started: the engine must not fail silently
        self.process_failures = 0
        self._mp_hands = None

    def _make_model(self):
        """Build the MediaPipe hand model, passing only the kwargs this mediapipe version
        actually accepts (parameter names have drifted between releases)."""
        import inspect
        cfg = self.cfg
        wanted = dict(
            static_image_mode=False,
            max_num_hands=int(cfg["max_hands"]),
            model_complexity=int(cfg["model_complexity"]),
            min_detection_confidence=float(cfg["min_detection_confidence"]),
            min_tracking_confidence=float(cfg["min_tracking_confidence"]),
            smooth_landmarks=bool(cfg["smooth_landmarks"]),
        )
        try:
            params = inspect.signature(mp.solutions.hands.Hands.__init__).parameters
            if not any(p.kind == p.VAR_KEYWORD for p in params.values()):
                wanted = {k: v for k, v in wanted.items() if k in params}
        except (TypeError, ValueError):
            wanted.pop("smooth_landmarks", None)
        return mp.solutions.hands.Hands(**wanted)

    def run(self):
        try:
            self._mp_hands = self._make_model()
        except Exception as exc:              # pragma: no cover - environment failure
            self.error = f"mediapipe init failed: {exc}"
            self.fatal = True
            return
        mirror = bool(self.cfg["mirror"])
        while not self._halt.is_set():
            frame, seq, captured = self.camera.latest()
            if frame is None:
                time.sleep(0.001)
                continue
            raw_frame = frame          # keep the unmirrored reference: cv2.flip returns a new array
            if mirror:
                frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            t0 = time.perf_counter()
            try:
                result = self._mp_hands.process(rgb)
            except Exception as exc:          # pragma: no cover
                self.error = f"mediapipe process failed: {exc}"
                self.process_failures += 1
                if self.process_failures == 1:
                    print(f"[kinesis] warning: {self.error}")
                time.sleep(0.05)
                continue
            t1 = time.perf_counter()

            hands: List[RawHand] = []
            if result.multi_hand_landmarks:
                labelled = list(zip(result.multi_hand_landmarks, result.multi_handedness or [None] * len(result.multi_hand_landmarks)))
                world_list = list(result.multi_hand_world_landmarks or [])
                for i, (lms, handed) in enumerate(labelled):
                    label = ""
                    score = 0.0
                    if handed is not None and handed.classification:
                        label = handed.classification[0].label
                        score = handed.classification[0].score
                    pts = [(lm.x, lm.y) for lm in lms.landmark]
                    world = ([(lm.x, lm.y, lm.z) for lm in world_list[i].landmark]
                             if i < len(world_list) else None)
                    hands.append(RawHand(label, pts, world, float(score)))

            with self._lock:
                self._hands = hands
                self._frame = frame
                self._raw_frame = raw_frame
                self._stamp = captured
                self._seq = seq
            self.inferred += 1
            self.inference_ms = (t1 - t0) * 1000.0

    def latest(self):
        with self._lock:
            return list(self._hands), self._frame, self._stamp, self._seq

    def latest_raw(self):
        """The unmirrored frame, for gaze (calibrated on raw frames). Not consumed - it is a
        second view of the same inference result."""
        with self._lock:
            return self._raw_frame, self._stamp

    def stop(self):
        self._halt.set()
        if self._mp_hands is not None:
            try:
                self._mp_hands.close()
            except Exception:
                pass


class TrackingEngine:
    """Owns both threads and turns raw landmarks into filtered HandPose objects."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.camera = CameraThread(int(cfg["camera_index"]), int(cfg["frame_width"]),
                                   int(cfg["frame_height"]), backend=str(cfg["camera_backend"]))
        self.inference: Optional[InferenceThread] = None
        self.detector = PoseDetector(cfg)
        self._filters: Dict[str, HandFilter] = {}
        self._missing: Dict[str, float] = {}
        self.stats = TrackingStats()
        self._last_seq = -1
        self._last_infer_time = 0.0
        self._int_prev = 0.0
        self._int_times: List[float] = []
        self._fatal_reported = False
        self._fps_sample = None
        self._latency_accum = 0.0
        self._latency_count = 0

    def start(self) -> bool:
        """Open the camera and start both threads.

        RESTARTABLE, because the in-app gaze calibration has to take the camera away and hand it
        back: it stops this engine, runs `calibrate_gaze.py` as a separate process, then starts it
        again. Two things make a naive restart fail, and both were a real crash:

        1. the `_halt` Event is SET by `stop()` and never cleared, so a restarted camera thread
           would return immediately from its own `while not self._halt.is_set()`.
        2. A `Thread` object cannot be started twice - it raises `RuntimeError: threads can only be
           started once`. The old one is genuinely dead, so it has to be replaced, not reused.

        So both objects are rebuilt from scratch. Everything they own is per-run state anyway, and
        the MediaPipe hands model is re-created with them.
        """
        if self.camera.opened and self.inference is not None and self.inference.is_alive():
            return True                              # already running; nothing to do
        self.camera = CameraThread(int(self.cfg["camera_index"]), int(self.cfg["frame_width"]),
                                   int(self.cfg["frame_height"]),
                                   backend=str(self.cfg["camera_backend"]))
        if not self.camera.open():
            return False
        # Prove frames actually arrive. `isOpened()` was reporting success on a camera another
        # program was holding, and the app then ran happily with nothing to track - which is how
        # "eye-gaze was allegedly started but nothing was measured" happened. A silent camera is
        # the single most expensive failure to debug, so the app refuses to start without frames.
        if not self.camera.verify_streaming():
            self.camera.stop()
            return False
        self.camera.start()
        self.inference = InferenceThread(self.camera, self.cfg)
        self.inference.start()
        return True

    def stop(self):
        if self.inference:
            self.inference.stop()
        self.camera.stop()
        # Join so the threads are genuinely finished before anyone tries to reopen the device.
        # Without this, a restart can race the old capture thread for the camera - and the symptom
        # is the "could not open camera" message pointing at the user, when the app caused it.
        if self.inference is not None and self.inference.is_alive():
            self.inference.join(timeout=2.0)
        if self.camera.is_alive():
            self.camera.join(timeout=2.0)

    def _filter_for(self, handedness: str) -> HandFilter:
        f = self._filters.get(handedness)
        if f is None:
            f = HandFilter(min_cutoff=float(self.cfg["filter_min_cutoff"]),
                           beta=float(self.cfg["filter_beta"]),
                           d_cutoff=float(self.cfg["filter_derivative_cutoff"]))
            self._filters[handedness] = f
        return f

    @property
    def frame_size(self):
        return self.camera.actual_size

    def update(self) -> Tuple[List[HandPose], Optional[object], float]:
        """Returns (poses, preview_frame, end_to_end_latency_seconds)."""
        if self.inference is not None and self.inference.fatal and not self._fatal_reported:
            self._fatal_reported = True
            raise RuntimeError(self.inference.error)
        hands, frame, stamp, seq = self.inference.latest() if self.inference else ([], None, 0.0, -1)
        now = time.perf_counter()

        if seq != self._last_seq:
            delta = now - self._last_infer_time if self._last_infer_time else 0.0
            self._last_infer_time = now
            self._last_seq = seq
            if delta:
                self._int_times.append(delta)
                if len(self._int_times) > 60:
                    self._int_times.pop(0)

        w, h = self.frame_size
        mirror = bool(self.cfg["mirror"])
        poses: List[HandPose] = []
        seen = set()
        for raw in hands:
            label = raw.handedness or "Unknown"
            if bool(self.cfg["handedness_mirror"]) and mirror:
                label = {"Left": "Right", "Right": "Left"}.get(label, label)
            seen.add(label)
            pts = raw.points_norm
            if bool(self.cfg["filter"]):
                pts = self._filter_for(label).filter(pts, now)
            pose = self.detector.update(pts, label, w, h, raw.world_points)
            pose.confidence = raw.score
            self.detector.note_seen(label, now)
            self._missing.pop(label, None)
            poses.append(pose)

        for label in list(self._filters):
            if label not in seen:
                self._missing.setdefault(label, now)
                if now - self._missing[label] > 0.35:
                    self._filters[label].reset()
        self.detector.age_out(now)

        # stats
        self.stats.frames_captured = self.camera.captured
        self.stats.frames_inferred = self.inference.inferred if self.inference else 0
        self.stats.capture_fps = self.camera.fps
        if self.inference is not None:
            # rate sampled from the thread's own counter over a fixed window, so the figure is
            # right no matter how often update() happens to be called
            if self._fps_sample is None:
                self._fps_sample = (now, self.inference.inferred)
            else:
                t_prev, n_prev = self._fps_sample
                elapsed = now - t_prev
                if elapsed >= 0.5:
                    self.stats.inference_fps = (self.inference.inferred - n_prev) / elapsed
                    self._fps_sample = (now, self.inference.inferred)
                    self._lat_sample = self._latency_accum / max(self._latency_count, 1)
                    self._latency_accum = 0.0
                    self._latency_count = 0
        self.stats.inference_ms = self.inference.inference_ms if self.inference else 0.0
        self.stats.end_to_end_ms = (now - stamp) * 1000.0 if stamp else 0.0
        if stamp:
            self._latency_accum += (now - stamp) * 1000.0
            self._latency_count += 1
        latency = (now - stamp) if stamp else 0.0
        return poses, frame, latency

    def handedness_report(self, poses: Sequence[HandPose]) -> str:
        if not poses:
            return "no hands"
        return ", ".join(f"{p.handedness}({p.confidence:.2f})" for p in poses)

    def raw_frame(self):
        """The unmirrored camera frame the gaze estimator must be fed."""
        return self.inference.latest_raw()[0] if self.inference else None
