"""Which monitor is the hand pointing at.

Two modes:
  * calibrated - nearest calibrated (yaw, pitch) centroid per monitor, with hysteresis and a
    distance gate. Sign-convention agnostic: whatever angles the tracker reports for each monitor
    at calibration time are what it matches at runtime.
  * heuristic - before calibration, yaw is mapped monotonically across the virtual desktop, so
    aiming is approximately right on first run.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .config import Calibration, MonitorTarget
from .winapi import Monitor, virtual_screen

HEURISTIC_SPAN_DEG = 70.0


@dataclass
class AimResult:
    index: Optional[int]          # monitor index (left-to-right), None = unknown
    confidence: float
    mode: str                     # calibrated | heuristic | none
    yaw: float = 0.0
    pitch: float = 0.0


class AimClassifier:
    def __init__(self, cfg, monitors: Sequence[Monitor], calibration: Optional[Calibration] = None):
        self.cfg = cfg
        self.monitors = list(monitors)
        self.calibration = calibration or Calibration()
        self._current: Optional[int] = None

    # ------------------------------------------------------------------ setup
    @property
    def is_calibrated(self) -> bool:
        return bool(self.calibration.targets)

    def set_calibration(self, calibration: Calibration):
        self.calibration = calibration

    # ------------------------------------------------------------------ lookup
    def aimed_monitor(self, index: Optional[int]) -> Optional[Monitor]:
        if index is None or not (0 <= index < len(self.monitors)):
            return None
        return self.monitors[index]

    def _angles(self, yaw: float, pitch: float):
        """Apply the configured yaw sign so both modes share one convention."""
        return yaw * float(self.cfg["aim_yaw_sign"]), pitch

    def _heuristic(self, yaw: float, pitch: float) -> AimResult:
        left, top, width, height = virtual_screen()
        if width <= 0:
            return AimResult(None, 0.0, "none", yaw, pitch)
        fraction = (yaw + HEURISTIC_SPAN_DEG) / (2.0 * HEURISTIC_SPAN_DEG)
        fraction = min(max(fraction, 0.0), 1.0)
        x = left + fraction * width
        idx = None
        for i, m in enumerate(self.monitors):
            if m.left <= x < m.right:
                idx = i
                break
        if idx is None:
            # outside every monitor's x-range: take the nearest by centre distance
            idx = min(range(len(self.monitors)),
                      key=lambda i: abs(self.monitors[i].centre[0] - x)) if self.monitors else None
        confidence = max(0.0, 1.0 - abs(yaw) / 120.0)
        return AimResult(idx, confidence, "heuristic", yaw, pitch)

    def _calibrated(self, yaw: float, pitch: float) -> AimResult:
        targets: List[MonitorTarget] = self.calibration.targets
        if not targets:
            return self._heuristic(yaw, pitch)
        scored = []
        for t in targets:
            d = math.hypot(yaw - t.yaw, (pitch - t.pitch) * 0.45)
            scored.append((d, t))
        scored.sort(key=lambda pair: pair[0])
        best_d, best = scored[0]
        max_d = float(self.cfg["aim_max_distance_deg"])
        if best_d > max_d:
            # too far from any calibrated direction: fall back to geometric mapping, but keep
            # the mode honest about it
            return AimResult(None, 0.0, "calibrated", yaw, pitch)
        # hysteresis: stick with the current monitor unless the challenger is clearly closer
        if self._current is not None and self._current != best.index:
            current = next((t for t in targets if t.index == self._current), None)
            if current is not None:
                d_current = math.hypot(yaw - current.yaw, (pitch - current.pitch) * 0.45)
                margin = float(self.cfg["aim_hysteresis_deg"])
                if d_current - best_d < margin:
                    best_d, best = d_current, current
        confidence = max(0.0, min(1.0, 1.0 - best_d / max_d))
        return AimResult(best.index, confidence, "calibrated", yaw, pitch)

    def classify(self, yaw: float, pitch: float) -> AimResult:
        yaw, pitch = self._angles(yaw, pitch)
        result = self._calibrated(yaw, pitch) if self.is_calibrated else self._heuristic(yaw, pitch)
        if result.index is not None:
            self._current = result.index
        return result

    # ------------------------------------------------------------------ calibration helper
    @staticmethod
    def order_check(angle_pairs: Sequence[tuple]) -> bool:
        """Given (monitor_index_in_left_to_right_order, mean_yaw) pairs, is yaw increasing
        with screen position? Used by the wizard to detect a reversed yaw axis and say so."""
        pairs = sorted(angle_pairs, key=lambda p: p[0])
        if len(pairs) < 2:
            return True
        increasing = sum(1 for a, b in zip(pairs, pairs[1:]) if b[1] > a[1])
        decreasing = sum(1 for a, b in zip(pairs, pairs[1:]) if b[1] < a[1])
        return increasing >= decreasing
