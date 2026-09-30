"""Camera preview overlay.

Text only, ASCII only: the preview is an OpenCV window, so nothing fancier than cv2.putText is
available, and the console side of this program must stay ASCII-safe too.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

import cv2

from .pose import HandPose

GREEN = (0, 255, 0)
AMBER = (0, 190, 255)
RED = (60, 60, 255)
DIM = (140, 140, 140)
WHITE = (235, 235, 235)

FONT = cv2.FONT_HERSHEY_SIMPLEX


class Hud:
    def __init__(self, cfg, monitors):
        self.cfg = cfg
        self.monitors = monitors
        self.width = int(cfg["preview_width"])

    def render(self, frame, poses: Sequence[HandPose], aim, active_action: str,
               state_summary: str, stats, latency_ms: float, note: str = "",
               cursor: Optional[tuple] = None):
        if frame is None:
            return None
        h, w = frame.shape[:2]
        scale = self.width / float(w)
        out = cv2.resize(frame, (self.width, int(h * scale)))

        if bool(self.cfg["show_landmarks"]):
            self._landmarks(out, poses, scale)

        lines = [
            f"pose: {poses[0].describe() if poses else 'no hand'}",
            f"aim : {self._aim_text(aim)}",
            f"act : {state_summary}",
        ]
        if bool(self.cfg["hud_detail"]) and poses:
            p = poses[0]
            lines.append(f"yaw : {p.yaw:+.1f}  pitch: {p.pitch:+.1f}  2d: {p.yaw_2d:+.1f}")
        lines.append(f"fps : cam {stats.capture_fps:4.1f}  inf {stats.inference_fps:4.1f}  "
                     f"e2e {latency_ms:4.0f}ms")
        if cursor:
            lines.append(f"cur : {int(cursor[0])},{int(cursor[1])}")
        if active_action:
            lines.append(active_action)
        if note:
            lines.append(note)

        y = 18
        for i, text in enumerate(lines):
            cv2.putText(out, text[:64], (8, y), FONT, 0.46, WHITE if i < 3 else DIM, 1, cv2.LINE_AA)
            y += 18
        cv2.putText(out, "END = quit", (8, out.shape[0] - 8), FONT, 0.42, AMBER, 1, cv2.LINE_AA)
        return out

    def _aim_text(self, aim) -> str:
        if aim is None:
            return "-"
        if aim.index is None:
            return f"unknown ({aim.mode}, yaw {aim.yaw:+.0f})"
        name = f"monitor {aim.index + 1}"
        if 0 <= aim.index < len(self.monitors):
            m = self.monitors[aim.index]
            name += f" ({m.device.split(chr(92))[-1]})"
        return f"{name}  conf {aim.confidence:.2f}  [{aim.mode}]"

    @staticmethod
    def _landmarks(out, poses: Sequence[HandPose], scale: float):
        import mediapipe as mp
        for pose in poses:
            pts = []
            for (x, y) in pose.points_norm:
                pts.append((int(x * out.shape[1]), int(y * out.shape[0])))
            colour = GREEN if pose.handedness == "Right" else AMBER
            for a, b in mp.solutions.hands.HAND_CONNECTIONS:
                cv2.line(out, pts[a], pts[b], colour, 1, cv2.LINE_AA)
            for i, p in enumerate(pts):
                r = 3 if i in (4, 8, 12, 16, 20) else 2
                cv2.circle(out, p, r, colour, -1)
            tip = pts[8]
            cv2.circle(out, tip, 6, WHITE, 1, cv2.LINE_AA)
