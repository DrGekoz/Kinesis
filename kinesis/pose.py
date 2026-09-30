"""Landmarks -> finger states, pose classification, and pointing angles.

Finger states are decided by the joint angle at the PIP joint rather than by comparing tip and
knuckle heights. Heights break the moment the hand is rotated (a hand held sideways reads as "all
fingers curled"); angles are invariant under in-plane rotation, so pointing at a monitor to your
left still reads correctly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

# (mcp, pip, tip) for the four long fingers, and (mcp, ip, tip) for the thumb
FINGER_JOINTS = {
    "thumb": (THUMB_MCP, THUMB_IP, THUMB_TIP),
    "index": (INDEX_MCP, INDEX_PIP, INDEX_TIP),
    "middle": (MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP),
    "ring": (RING_MCP, RING_PIP, RING_TIP),
    "pinky": (PINKY_MCP, PINKY_PIP, PINKY_TIP),
}
LONG_FINGERS = ("index", "middle", "ring", "pinky")

POSE_OPEN = "OPEN"
POSE_FIST = "FIST"
POSE_SHAKA = "SHAKA"
POSE_POINT = "POINT"
POSE_MIXED = "MIXED"
POSE_NONE = "NONE"


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _dist3(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def angle_at(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> float:
    """Angle ABC in degrees, 0-180. Uses only x/y, so it is rotation-invariant in the image
    plane - which is the property that makes gesture detection survive a tilted hand."""
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 180.0
    cosang = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosang))))


def hand_scale(points: Sequence[Sequence[float]]) -> float:
    """Wrist to middle-finger knuckle, in the same units as the input points.
    Every distance threshold is expressed as a fraction of this, so gestures work at any
    distance from the camera."""
    return max(_dist(points[WRIST], points[MIDDLE_MCP]), 1e-6)


@dataclass
class HandPose:
    handedness: str = ""
    pose: str = POSE_NONE
    extended: Dict[str, bool] = field(default_factory=dict)
    pinches: Dict[str, bool] = field(default_factory=dict)
    scale: float = 1.0
    scale_px: float = 1.0
    points_px: List[Tuple[float, float]] = field(default_factory=list)
    points_norm: List[Tuple[float, float]] = field(default_factory=list)
    index_tip_px: Tuple[float, float] = (0.0, 0.0)
    index_tip_norm: Tuple[float, float] = (0.0, 0.0)
    palm_px: Tuple[float, float] = (0.0, 0.0)
    yaw: float = 0.0
    pitch: float = 0.0
    yaw_2d: float = 0.0
    confidence: float = 1.0

    def pinch_point_px(self, finger: str = "index") -> Tuple[float, float]:
        """The midpoint between the thumb tip and a finger tip, in pixels.

        This is the point a pinch actually happens at, which is what a two-hand pinch-zoom should
        measure between: the hands' centres of mass move when they rotate, the pinch points do not.
        """
        if not self.points_px:
            return self.index_tip_px
        finger_tip = FINGER_JOINTS.get(finger, FINGER_JOINTS["index"])[2]
        thumb = self.points_px[THUMB_TIP]
        tip = self.points_px[finger_tip]
        return ((thumb[0] + tip[0]) / 2.0, (thumb[1] + tip[1]) / 2.0)

    @property
    def num_extended(self) -> int:
        return sum(1 for f in LONG_FINGERS if self.extended.get(f))

    def holds(self, *fingers: str) -> bool:
        return all(self.extended.get(f) for f in fingers)

    def pinching(self, *fingers: str) -> bool:
        return any(self.pinches.get(f) for f in fingers)

    def describe(self) -> str:
        pinched = [f for f in ("index", "middle", "ring", "pinky") if self.pinches.get(f)]
        extra = f" pinch:{'+'.join(pinched)}" if pinched else ""
        return f"{self.handedness} {self.pose}{extra}"


class PoseDetector:
    """Stateful per-hand classifier (the state exists for hysteresis only)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._finger_state: Dict[str, Dict[str, bool]] = {}
        self._pinch_state: Dict[str, Dict[str, bool]] = {}
        self._last_seen: Dict[str, float] = {}

    def forget(self, handedness: str):
        self._finger_state.pop(handedness, None)
        self._pinch_state.pop(handedness, None)
        self._last_seen.pop(handedness, None)

    def note_seen(self, handedness: str, now: float):
        self._last_seen[handedness] = now

    def age_out(self, now: float, timeout: float = 0.7):
        """Drop hysteresis state for hands that have been gone a while, so a hand that
        reappears in a different pose is classified from scratch."""
        for h in list(self._last_seen):
            if now - self._last_seen[h] > timeout:
                self.forget(h)

    def update(self, norm_points: Sequence[Sequence[float]], handedness: str,
               frame_w: float, frame_h: float, world_points: Optional[Sequence[Sequence[float]]] = None
               ) -> HandPose:
        """norm_points: 21 (x, y) in normalised image space (already mirrored)."""
        px = [(p[0] * frame_w, p[1] * frame_h) for p in norm_points]
        scale = hand_scale(norm_points)          # in normalised units
        scale_px = hand_scale(px)
        ext_cfg_hi = float(self.cfg["finger_extended_deg"])
        ext_cfg_lo = float(self.cfg["finger_curled_deg"])
        thumb_hi = float(self.cfg["thumb_extended_deg"])
        thumb_lo = float(self.cfg["thumb_curled_deg"])

        prev_ext = self._finger_state.setdefault(handedness, {f: False for f in FINGER_JOINTS})
        extended: Dict[str, bool] = {}
        for finger, (mcp, pip, tip) in FINGER_JOINTS.items():
            ang = angle_at(norm_points[mcp], norm_points[pip], norm_points[tip])
            hi, lo = (thumb_hi, thumb_lo) if finger == "thumb" else (ext_cfg_hi, ext_cfg_lo)
            # hysteresis: a finger that is already extended needs to fall further to be "curled"
            threshold = lo if prev_ext.get(finger) else hi
            extended[finger] = ang > threshold
            prev_ext[finger] = extended[finger]

        # pinches: thumb tip to fingertip, relative to hand size, with separate on/off thresholds
        prev_pinch = self._pinch_state.setdefault(
            handedness, {f: False for f in LONG_FINGERS})
        pinch_on = float(self.cfg["pinch_on"])
        pinch_off = float(self.cfg["pinch_off"])
        pinches: Dict[str, bool] = {}
        for finger in LONG_FINGERS:
            ratio = _dist(norm_points[THUMB_TIP], norm_points[FINGER_JOINTS[finger][2]]) / scale
            threshold = pinch_off if prev_pinch.get(finger) else pinch_on
            pinches[finger] = ratio < threshold
            prev_pinch[finger] = pinches[finger]

        pose = self._classify(extended, pinches)
        yaw, pitch, yaw2d = self._pointing(norm_points, world_points, extended)

        palm = (norm_points[WRIST][0] + norm_points[MIDDLE_MCP][0]) / 2.0, \
               (norm_points[WRIST][1] + norm_points[MIDDLE_MCP][1]) / 2.0

        return HandPose(
            handedness=handedness,
            pose=pose,
            extended=extended,
            pinches=pinches,
            scale=scale,
            scale_px=scale_px,
            points_px=px,
            points_norm=list(norm_points),
            index_tip_px=(norm_points[INDEX_TIP][0] * frame_w, norm_points[INDEX_TIP][1] * frame_h),
            index_tip_norm=(norm_points[INDEX_TIP][0], norm_points[INDEX_TIP][1]),
            palm_px=(palm[0] * frame_w, palm[1] * frame_h),
            yaw=yaw, pitch=pitch, yaw_2d=yaw2d,
        )

    @staticmethod
    def _classify(extended: Dict[str, bool], pinches: Dict[str, bool]) -> str:
        long_ext = [f for f in LONG_FINGERS if extended[f]]
        # shaka = thumb + pinky out, index/middle/ring curled. Checked before FIST because a
        # fist requires the pinky curled, so the two cannot be confused.
        if extended["thumb"] and extended["pinky"] and not extended["index"] \
                and not extended["middle"] and not extended["ring"]:
            return POSE_SHAKA
        if not long_ext:
            return POSE_FIST
        for finger in ("index", "middle", "ring", "pinky"):
            if pinches[finger]:
                return f"PINCH_{finger.upper()}"
        if len(long_ext) >= 4:
            return POSE_OPEN
        if long_ext == ["index"]:
            return POSE_POINT
        return POSE_MIXED

    @staticmethod
    def _pointing(norm_points, world_points, extended):
        """Direction the hand is indicating, in degrees.

        yaw:   0 = pointing at the monitor the camera sits on, +90 = hard right, -90 = hard left.
        pitch: 0 = level, +ve = pointing up.

        Uses MediaPipe's metric world landmarks when available; the 2D fallback uses the same
        convention (finger up in frame = pointing at the camera's screen), so calibration captured
        with one path stays meaningful with the other.
        """
        if extended.get("index"):
            base_i, tip_i = INDEX_MCP, INDEX_TIP
        else:
            base_i, tip_i = WRIST, MIDDLE_MCP

        if world_points is not None and len(world_points) == 21:
            b, t = world_points[base_i], world_points[tip_i]
            dx = t[0] - b[0]
            dy = t[1] - b[1]                 # world y is up-positive
            dz = t[2] - b[2]
            depth_away = -dz                 # mediapipe z is negative toward the camera
            yaw = math.degrees(math.atan2(dx, depth_away))
            pitch = math.degrees(math.atan2(dy, depth_away))
        else:
            yaw = pitch = 0.0

        b2, t2 = norm_points[base_i], norm_points[tip_i]
        dx2 = t2[0] - b2[0]
        dy2 = t2[1] - b2[1]                  # image y is down-positive
        yaw2d = math.degrees(math.atan2(dx2, -dy2)) if (dx2 or dy2) else 0.0
        pitch2d = math.degrees(math.atan2(-dy2, math.hypot(dx2, 1e-9)))
        if world_points is None:
            yaw, pitch = yaw2d, pitch2d
        return yaw, pitch, yaw2d
