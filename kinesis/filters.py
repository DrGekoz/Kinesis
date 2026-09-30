"""One-Euro filter - jitter removal with minimal added lag.

A plain low-pass kills hand tremor but makes the cursor feel glued; the One-Euro filter raises its
cutoff frequency as the signal speeds up, so a stationary hand is smoothed hard and a fast one is
tracked almost raw. Reference: Casiez, Roussel & Vogel, "1e Filter: A Simple Speed-based Low-pass
Filter for Noisy Input in Interactive Systems" (CHI 2012).

Kinesis runs one of these per landmark axis, and one on the pointing angles.
"""
from __future__ import annotations

import math


class LowPass:
    __slots__ = ("y", "initialised")

    def __init__(self):
        self.y = 0.0
        self.initialised = False

    def __call__(self, x: float, alpha: float) -> float:
        if not self.initialised:
            self.y = x
            self.initialised = True
        else:
            self.y = alpha * x + (1.0 - alpha) * self.y
        return self.y


class OneEuro:
    """Scalar One-Euro filter."""

    __slots__ = ("min_cutoff", "beta", "d_cutoff", "_x", "_dx", "_t_prev", "value")

    def __init__(self, min_cutoff: float = 1.6, beta: float = 0.05, d_cutoff: float = 1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._x = LowPass()
        self._dx = LowPass()
        self._t_prev = None
        self.value = 0.0

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * max(cutoff, 1e-6))
        return 1.0 / (1.0 + tau / max(dt, 1e-6))

    def reset(self):
        self._x = LowPass()
        self._dx = LowPass()
        self._t_prev = None

    def __call__(self, x: float, t: float) -> float:
        if self._t_prev is None:
            self._t_prev = t
            self.value = self._x(x, 1.0)
            return self.value
        dt = max(t - self._t_prev, 1e-4)
        self._t_prev = t

        dx = (x - self._x.y) / dt if self._x.initialised else 0.0
        dx_hat = self._dx(dx, self._alpha(self.d_cutoff, dt))
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        self.value = self._x(x, self._alpha(cutoff, dt))
        return self.value

    def configure(self, min_cutoff=None, beta=None, d_cutoff=None):
        if min_cutoff is not None:
            self.min_cutoff = float(min_cutoff)
        if beta is not None:
            self.beta = float(beta)
        if d_cutoff is not None:
            self.d_cutoff = float(d_cutoff)


class OneEuroPoint:
    """Independent One-Euro filters per axis of a 2D point."""

    def __init__(self, min_cutoff: float = 1.6, beta: float = 0.05, d_cutoff: float = 1.0):
        self.fx = OneEuro(min_cutoff, beta, d_cutoff)
        self.fy = OneEuro(min_cutoff, beta, d_cutoff)

    def __call__(self, x: float, y: float, t: float):
        return (self.fx(x, t), self.fy(y, t))

    def configure(self, min_cutoff=None, beta=None, d_cutoff=None):
        self.fx.configure(min_cutoff, beta, d_cutoff)
        self.fy.configure(min_cutoff, beta, d_cutoff)

    def reset(self):
        self.fx.reset()
        self.fy.reset()


class HandFilter:
    """Per-landmark 2D One-Euro filters for one hand.

    Landmarks jump around a pixel or two frame to frame even when the hand is still; filtering in
    normalised image space (rather than screen space) keeps the smoothing independent of display
    resolution, and the result is projected to the desktop afterwards.
    """

    def __init__(self, count: int = 21, min_cutoff: float = 1.6, beta: float = 0.05,
                 d_cutoff: float = 1.0):
        self.filters = [OneEuroPoint(min_cutoff, beta, d_cutoff) for _ in range(count)]

    def filter(self, points, t: float):
        """points: sequence of (x, y) in normalised image space."""
        out = []
        for i, (x, y) in enumerate(points):
            out.append(self.filters[i](x, y, t))
        return out

    def configure(self, min_cutoff=None, beta=None, d_cutoff=None):
        for f in self.filters:
            f.configure(min_cutoff, beta, d_cutoff)

    def reset(self):
        for f in self.filters:
            f.reset()
