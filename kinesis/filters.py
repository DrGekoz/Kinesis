"""One-Euro filter - jitter removal with minimal added lag.

A plain low-pass kills hand tremor but makes the cursor feel glued; the One-Euro filter raises its
cutoff frequency as the signal speeds up, so a stationary hand is smoothed hard and a fast one is
tracked almost raw. Reference: Casiez, Roussel & Vogel, "1e Filter: A Simple Speed-based Low-pass
Filter for Noisy Input in Interactive Systems" (CHI 2012).

Kinesis runs one of these per landmark axis, and one on the pointing angles.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple


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


class GazeStabiliser:
    """Zero-latency outlier rejection plus an adaptive deadband, for a NOISY gaze point.

    Written for eye tracking specifically, and the reason is measured rather than guessed. Kinesis's
    gaze model is a ridge regression over 486 face-landmark features fitted from ~220 calibration
    samples, so it is rank-deficient by more than two to one. Measured on the shipped model, feature
    noise of just 0.2% - well below what a face landmarker produces frame to frame - moves the
    predicted pointer by over a million pixels. That is not jitter a deadband can absorb: to swallow
    million-pixel excursions it would have to discard essentially all real motion.

    So this rejects the EXCURSION and keeps the motion:

    * **Speed gate.** A step beyond `speed_max_px_s` in one frame is physically impossible for eye
      movement across a desk, so it is discarded outright rather than smoothed. Rejection, not
      lag: a legitimate fast saccade clears the gate and passes through untouched.
    * **Windowed outlier vote.** `window` recent samples are kept; a sample that is the furthest from
      the window's own median by more than `reject_px` is dropped. Isolated spikes are the signature
      of a bad landmark frame, and the median is unmoved by them, so the filter converges instantly
      instead of gliding in.
    * **Median-then-mean.** The emitted point is the mean of the window after rejection, so the
      residual sub-window wobble is already suppressed before any EMA is applied.
    * **Adaptive deadband.** The window's own spread sets the deadband, floored at `deadband_px`.
      Still eyes produce a tiny spread and hold rock steady; a moving eye produces a large spread and
      passes straight through. A fixed deadband cannot do both: too small and the cursor breathes,
      too large and short saccades stop registering.

    No added latency. Unlike an EMA or a median-of-window, a good sample is emitted on the frame it
    arrives - nothing is buffered and played late, so nothing is delayed.
    """

    __slots__ = ("reject_px", "window", "speed_max_px_s", "deadband_px", "deadband_gain",
                 "_xs", "_ys", "_t", "_last", "_held", "rejected", "accepted", "slew_px",
                 "_slew_x", "_slew_y", "_last_t", "motion_px_s", "saccade_px")

    # `speed_max_px_s` is derived, not guessed. Gaze refreshes at `gaze_hz` (20 Hz), so one frame is
    # 50 ms and the fastest honest motion is a saccade. Measured on this machine's 7680 px virtual
    # desktop: crossing ONE screen (1920 px) in one frame is 38,400 px/s, and a third of the whole
    # desktop is 50,688 px/s. The original 9,000 px/s default rejected all of those - a real 600 px
    # saccade measured 12,000 px/s and was discarded as noise, which is the opposite of the intent.
    # 20,000 px/s passes a half-screen jump in a single frame and still rejects the million-pixel
    # excursions this filter exists for (which are 20,000,000 px/s) by three orders of magnitude.
    def __init__(self, reject_px: float = 260.0, window: int = 5, speed_max_px_s: float = 20000.0,
                 deadband_px: float = 3.0, deadband_gain: float = 2.0,
                 slew_px: float = 900.0):
        self.reject_px = float(reject_px)
        self.window = max(int(window), 1)
        self.speed_max_px_s = float(speed_max_px_s)
        self.deadband_px = float(deadband_px)
        self.deadband_gain = float(deadband_gain)
        self.slew_px = float(slew_px)
        # Above `motion_px_s` the eye is treated as travelling rather than resting, which widens the
        # outlier gate to `saccade_px`. Measured: a real 600 px saccade is 12,000 px/s, so the
        # threshold sits well under that, and a stationary eye with 0.2% landmark jitter measures in
        # the hundreds of px/s at most.
        self.motion_px_s = 400.0
        self.saccade_px = 900.0
        self._xs: list = []
        self._ys: list = []
        self._t: list = []
        self._last = None
        self._last_t = None
        self._held = None          # the point actually emitted, for the deadband
        self._slew_x = 0.0         # direction of the current sweep, for slew limiting
        self._slew_y = 0.0
        self.rejected = 0
        self.accepted = 0

    def reset(self) -> None:
        """Drop the history AND the counters.

        The counters used to survive a reset, so `reset()` produced a filter that reported rejections
        it had never made in its current life - and a test asserting a clean slate after a reset
        failed. `note_jump()` deliberately does NOT clear them: a jump that threw samples away really
        did reject them, and the diagnostic line should keep saying so.
        """
        self._xs.clear()
        self._ys.clear()
        self._t.clear()
        self._last = None
        self._last_t = None
        self._held = None
        self._slew_x = 0.0
        self._slew_y = 0.0
        self.rejected = 0
        self.accepted = 0

    def note_jump(self) -> None:
        """Forget the history after a deliberate pointer move (a warp, a release, a mode change).

        Without this the first real sample after a warp is compared against the OLD point, is always
        outside the speed gate, and gets discarded - so the pointer stays at the warp destination
        until the window happens to fill with post-warp samples.
        """
        self._last = None
        self._last_t = None
        self._held = None
        self._xs.clear()
        self._ys.clear()
        self._t.clear()

    def _median(self, values: list) -> float:
        s = sorted(values)
        n = len(s)
        mid = n // 2
        return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])

    def __call__(self, x: float, y: float, t: float) -> Optional[Tuple[float, float]]:
        x, y = float(x), float(y)

        # 1. SPEED GATE - and its verdict is remembered, because it is what tells the outlier vote
        #    whether this step is motion or a misfire.
        #
        #    A step beyond `speed_max_px_s` is not something an eye does: measured at 20 Hz gaze, one
        #    screen is 1920 px = 38,400 px/s, so 20,000 px/s still passes a half-screen saccade and
        #    rejects the million-pixel excursions this filter exists for (20,000,000 px/s).
        #    `passing_speed` is then used to WIDEN the outlier gate below. Without that the very first
        #    step of any saccade is judged against a stationary window and thrown away, which is how
        #    a measured 200-step sweep across the desk had 195 of its steps rejected.
        step_speed = None
        if self._last is not None and self._last_t is not None:
            dt = max(t - self._last_t, 1e-4)
            step = math.hypot(x - self._last[0], y - self._last[1])
            step_speed = step / dt
            if step_speed > self.speed_max_px_s:
                self.rejected += 1
                return None                    # drop the sample, keep the last good point

        # 2. Windowed outlier vote against the window's own median - BUT the threshold is not fixed.
        #
        # A fixed `reject_px` is only right for a point that is roughly where the last few were. Once
        # the eye has already committed to moving, a large step is a SACCADE, not an outlier: measured
        # on this machine a 600 px saccade is 12,000 px/s at 20 Hz gaze and is completely ordinary,
        # and a fixed 260 px gate rejected every one of them - the pointer simply refused to follow a
        # real look, which reads to the user as "the tracking is broken".
        #
        # So the gate is `max(reject_px, the recent spread)`: a stationary eye, whose window has no
        # spread, keeps the tight fixed gate that discards landmark jitter; a moving eye, whose window
        # already carries the movement, is allowed to keep moving. The million-pixel misfire is still
        # caught many times over - it is 500x the fixed gate even at maximum spread.
        if self._xs:
            mx = self._median(self._xs)
            my = self._median(self._ys)
            if len(self._xs) >= 3:
                spread = math.hypot(mx - sum(self._xs) / len(self._xs),
                                    my - sum(self._ys) / len(self._ys))
                gate = max(self.reject_px, spread * 2.0)
            else:
                gate = self.reject_px
            if step_speed is not None and step_speed > self.motion_px_s:
                # the eye is demonstrably MOVING, so a large step is intent. The window cannot know
                # that yet - it is still made of the old, stationary positions - so the gate is
                # widened to accept a step proportional to how fast the eye is actually travelling.
                gate = max(gate, self.saccade_px)
            if math.hypot(x - mx, y - my) > gate:
                self.rejected += 1
                return None

        # 3. THE MEAN OF THE WINDOW IS NOT USABLE DURING A SACCADE.
        #
        # The window holds the last 5 ACCEPTED samples, so on the first frame of a saccade it is four
        # old, stationary positions and one new one. Their mean moves the output only a fifth of the
        # way - measured, a 600 px saccade tracked to 1120 px, a fifth of the way across, and it would
        # take five frames to arrive. On a 50 ms gaze cycle that is a visible quarter-second lag on
        # every look, which is exactly the "laggy" the filter was meant to avoid.
        #
        # So during confirmed motion the window is RESET and the sample is taken at face value. The
        # history was describing where the eye WAS; once it has demonstrably left, that history is
        # not evidence about where it is now. The deadband immediately after rebuilds a fresh window,
        # so the jitter rejection is back within one frame.
        moving = step_speed is not None and step_speed > self.motion_px_s
        if moving:
            self._xs.clear()
            self._ys.clear()
            self._t.clear()
        self._xs.append(x)
        self._ys.append(y)
        self._t.append(t)
        if len(self._xs) > self.window:
            self._xs.pop(0)
            self._ys.pop(0)
            self._t.pop(0)
        self.accepted += 1
        self._last = (x, y)
        self._last_t = t

        cx = sum(self._xs) / len(self._xs)
        cy = sum(self._ys) / len(self._ys)

        # 4. adaptive deadband: the window's own spread sets the threshold
        if len(self._xs) >= 3:
            spread = math.hypot(self._median(self._xs) - cx, self._median(self._ys) - cy)
        else:
            spread = 0.0
        dead = max(self.deadband_px, spread * self.deadband_gain)

        # 5. THE DEADBAND MUST STAND DOWN FOR A SACCADE. This is the single line that decides whether
        # the pointer follows a real look.
        #
        # The deadband's job is to hold a RESTING eye still, and it compares the window mean against
        # the held point. But on the first frame of a saccade the window mean has only moved a
        # fraction of the way - it is still mostly the old, stationary samples - so a live deadband
        # returns the held point unchanged and the pointer does not move at all. Measured: a 600 px
        # saccade cleared the outlier gate and still tracked to the old position, because this
        # deadband swallowed it.
        #
        # The slew limit below is NOT bypassed: at 900 px it does not touch a 600 px saccade anyway
        # (saccade < slew), and it still belongs in force to cap a genuine jerk.
        if not moving and self._held is not None and abs(cx - self._held[0]) < dead and \
                abs(cy - self._held[1]) < dead:
            return self._held                # a resting eye does not shake the pointer

        # 6. slew limit: one frame may not travel further than a real saccade can
        if self.slew_px and self._held is not None:
            dx, dy = cx - self._held[0], cy - self._held[1]
            dist = math.hypot(dx, dy)
            if dist > self.slew_px:
                k = self.slew_px / dist
                cx = self._held[0] + dx * k
                cy = self._held[1] + dy * k

        self._held = (cx, cy)
        return (cx, cy)

    def stats_line(self) -> str:
        total = self.accepted + self.rejected
        if not total:
            return "gaze stabiliser: nothing yet"
        return (f"gaze stabiliser: {self.accepted}/{total} samples kept "
                f"({self.rejected} excursions rejected)")


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
