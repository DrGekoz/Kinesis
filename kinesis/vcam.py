"""Virtual camera output: webcam feed + hand tracking overlays, back out as a webcam.

Two modes:

  passthrough  the real camera frame with the hand skeleton, gaze point and status drawn on it -
               select "OBS Virtual Camera" in Discord/OBS/Zoom and your face is there with the
               tracking visible.
  overlay      EyeTrax's chroma-keyable look (flat green canvas + gaze cursor, which is what its
               own `eyetrax-virtualcam` streams so OBS can key it) with the hand overlays
               composited on top.

Sending is done from a dedicated thread with a latest-frame-only slot. pyvirtualcam paces with
`sleep_until_next_frame()`, which blocks for a whole frame interval - up to 66 ms at the 15 fps this
camera runs at - and calling that from the tracking loop would add exactly the latency this project
spent its effort removing.
"""
from __future__ import annotations

import threading
import time
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .geometry import gaze_to_canvas
from .gazevis import GazeVisualizer, text as draw_text
from .pose import HandPose
from .winapi import virtual_screen

CHROMA_GREEN = (0, 255, 0)
RIGHT_COLOR = (0, 255, 0)
LEFT_COLOR = (0, 190, 255)
TEXT_COLOR = (240, 240, 240)
FONT = cv2.FONT_HERSHEY_SIMPLEX
# gaze_to_canvas lives in geometry.py (the visualiser needs it too, and importing it from here would
# be circular); it is re-exported above via `from .geometry import gaze_to_canvas`.


class VirtualCamera:
    def __init__(self, cfg, dry: bool = False):
        self.cfg = cfg
        self.mode = str(cfg["vcam_mode"]).lower()
        self.enabled = bool(cfg["vcam_enabled"]) and self.mode in ("passthrough", "overlay")
        self.dry = dry
        self.error: Optional[str] = None
        self.sent = 0
        self.dropped = 0
        self.device = ""
        self.backend = ""
        self._cam = None
        self._lock = threading.Lock()
        self._frame = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._w = int(cfg["vcam_width"])
        self._h = int(cfg["vcam_height"])
        if self.mode == "overlay":
            # overlay is a screen map, not a camera frame: the canvas is the overlay size, and it
            # must be known before start() so composite() is correct on its own
            self._w, self._h = (int(x) for x in cfg["vcam_overlay_size"])
        from .gazevis import GazeVisualizer
        self.visual = (None if str(cfg["vcam_style"]).lower() == "none"
                       else GazeVisualizer(cfg, self._w, self._h, to_canvas=gaze_to_canvas))
        self._fps = float(cfg["vcam_fps"])
        self._last_compose_ms = 0.0
        self._own_green = None

    # ------------------------------------------------------------------ lifecycle
    def start(self, fps: Optional[float] = None) -> bool:
        if not self.enabled:
            return False
        try:
            import pyvirtualcam
        except Exception as exc:                     # pragma: no cover
            self.error = f"pyvirtualcam import failed: {exc}"
            print(f"[vcam] {self.error}")
            self.enabled = False
            return False
        if fps:
            self._fps = max(float(fps), 1.0)
        if self.mode == "overlay":
            # the overlay canvas is a screen map, not a camera frame: use the configured canvas
            self._w, self._h = (int(x) for x in self.cfg["vcam_overlay_size"])
        try:
            kwargs = {}
            if self.cfg.get("vcam_backend"):
                kwargs["backend"] = str(self.cfg["vcam_backend"])
            self._cam = pyvirtualcam.Camera(width=self._w, height=self._h, fps=self._fps,
                                            fmt=pyvirtualcam.PixelFormat.BGR,
                                            print_fps=False, **kwargs)
            self.device = getattr(self._cam, "device", "?")
            self.backend = getattr(self._cam, "backend", "?")
        except Exception as exc:
            self.error = str(exc)
            print(f"[vcam] could not open a virtual camera: {exc}")
            print("[vcam] on Windows this needs OBS Studio installed (its virtual camera driver "
                  "provides the 'OBS Virtual Camera' device). Run OBS once and click Start Virtual "
                  "Camera if the device is missing.")
            self._cam = None
            self.enabled = False
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name="kinesis-vcam")
        self._thread.start()
        print(f"[vcam] {self.mode} mode -> {self.device} ({self.backend}) "
              f"{self._w}x{self._h} @ {self._fps:.0f}fps")
        return True

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._cam is not None:
            try:
                self._cam.close()
            except Exception:
                pass
            self._cam = None
        self.enabled = False

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                frame = self._frame
                self._frame = None
            if frame is None:
                time.sleep(0.002)
                continue
            try:
                self._cam.send(frame)
                self.sent += 1
                self._cam.sleep_until_next_frame()
            except Exception as exc:                 # pragma: no cover
                self.error = f"send failed: {exc}"
                print(f"[vcam] {self.error}")
                break

    # ------------------------------------------------------------------ publishing
    def publish(self, frame) -> None:
        """Hand the newest composed frame to the sender; a frame nobody collected is dropped."""
        if not self.enabled or self.dry or frame is None:
            return
        with self._lock:
            if self._frame is not None:
                self.dropped += 1
            self._frame = frame

    # ------------------------------------------------------------------ compositing
    def composite(self, frame, poses: Sequence[HandPose] = (), gaze=None,
                  lines: Sequence[str] = ()) -> Optional[np.ndarray]:
        t0 = time.perf_counter()
        if self.mode == "overlay":
            canvas = self._green_canvas()
        elif frame is not None:
            canvas = cv2.resize(frame, (self._w, self._h)) if (
                frame.shape[1] != self._w or frame.shape[0] != self._h) else frame.copy()
        else:
            canvas = (np.zeros((self._h, self._w, 3), np.uint8) if self._own_green is None
                      else self._own_green.copy())

        if bool(self.cfg["vcam_show_landmarks"]):
            self._draw_hands(canvas, poses)
        if self.visual is not None:
            self.visual.advance(gaze)      # decay + record this frame's gaze sample
            self.visual.draw(canvas)       # trails, heat, reticle, bloom
        if bool(self.cfg["vcam_show_hud"]) and lines:
            self._draw_hud(canvas, lines)
        self._last_compose_ms = (time.perf_counter() - t0) * 1000.0
        return canvas

    def _green_canvas(self):
        if self._own_green is None or self._own_green.shape[:2] != (self._h, self._w):
            self._own_green = np.zeros((self._h, self._w, 3), np.uint8)
            self._own_green[:] = CHROMA_GREEN
        return self._own_green.copy()

    def _draw_hands(self, canvas, poses: Sequence[HandPose]) -> None:
        import mediapipe as mp
        h, w = canvas.shape[:2]
        for pose in poses:
            colour = RIGHT_COLOR if pose.handedness == "Right" else LEFT_COLOR
            pts = [(int(x * w), int(y * h)) for (x, y) in pose.points_norm]
            if len(pts) != 21:
                continue
            for a, b in mp.solutions.hands.HAND_CONNECTIONS:
                cv2.line(canvas, pts[a], pts[b], colour, 2, cv2.LINE_AA)
            for i, p in enumerate(pts):
                cv2.circle(canvas, p, 4 if i in (4, 8, 12, 16, 20) else 3, colour, -1, cv2.LINE_AA)
            if self.mode == "overlay":
                cv2.circle(canvas, pts[8], 9, (255, 255, 255), 2, cv2.LINE_AA)
                cv2.putText(canvas, pose.describe().split()[0], (pts[0][0] - 20, pts[0][1] + 24),
                            FONT, 0.6, colour, 2, cv2.LINE_AA)

    def _draw_hud(self, canvas, lines: Sequence[str]) -> None:
        """Telemetry with a real font and a soft glow, sized to the canvas."""
        h, _w = canvas.shape[:2]
        size = max(13, int(h * 0.030))
        pad = int(h * 0.022)
        y = pad
        for line in list(lines)[:6]:
            draw_text(canvas, str(line)[:80], (pad, y), size=size,
                      rgb=(238, 240, 244), glow_rgb=(255, 150, 40), glow=max(4, size // 4))
            y += int(size * 1.30)

    @property
    def size(self) -> Tuple[int, int]:
        return self._w, self._h

    def device_info(self) -> str:
        return f"{self.device or '?'} ({self.backend or '?'})"

    def current_fps(self) -> float:
        try:
            return float(getattr(self._cam, "current_fps", 0.0) or 0.0)
        except Exception:
            return 0.0

    def publish_direct(self, frame) -> None:
        """Send synchronously, bypassing the sender thread - used by --vcam-test so the frame
        pacing in the test is deterministic."""
        if self._cam is None or frame is None:
            return
        self._cam.send(frame)
        self.sent += 1
        self._cam.sleep_until_next_frame()

    def status_line(self) -> str:
        if not self.enabled:
            return f"vcam: off ({self.error})" if self.error else "vcam: off"
        return (f"vcam: {self.mode} -> {self.device} {self._w}x{self._h} "
                f"sent {self.sent} dropped {self.dropped}")
