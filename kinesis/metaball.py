"""The gaze metaball: a white outlined blob that swells where you look and leaves a trail.

What it draws, per the spec:
  * ONE blob whose radius grows with how long you have been looking in one place, to a maximum.
  * A TRAIL of blobs left behind, each shrinking to nothing over `shrink_s` (0.5 s).
  * The head animates to a new gaze position at native speed instead of teleporting.
  * Transparent, WHITE OUTLINE ONLY (3 px), with a faint white glow both inside and outside it.

HOW A METABALL IS ACTUALLY DRAWN IN 2D
A blob is not a circle. It is a scalar FIELD plus an ISO-SURFACE: each blob adds a smooth kernel to
a float buffer, blobs that overlap sum above the threshold, and the union is one merged shape with a
neck between them - which is the whole visual signature. Drawing circles instead gives you a chain
of circles, not a blob. So this keeps a field, thresholds it, and takes the outline from the band
between the mask and its erosion.

WHY THE FIELD IS RENDERED SMALL AND UPSCALED
The overlay renders at `scale` (default 3) and upscales once on composite, because a 1080p float
field per monitor is ~8 MB and the glow blur alone measured 59-140 ms at full resolution - three
orders of magnitude over the frame budget. At scale 3 a 1920x1080 panel is 640x360, which is what
makes 180 fps reachable. The outline thickness is therefore specified in SCREEN pixels and divided
by the scale, so "3 px" stays 3 px on the desktop.

WHY THE KERNEL IS RESIZED RATHER THAN RECOMPUTED
The kernel is a fixed radial falloff. Computing `exp(-(d/R)^2)` per blob is thousands of exp() calls
for something that is the same shape at a different size, so one kernel is built at the maximum
radius and each blob gets a cv2.resize of it - a SIMD blit instead of an exponential.

The result is pure white intensity in a single channel. That is deliberate: the overlay pipeline
takes `canvas.max(axis=2)` as the alpha channel and premultiplies, so a white-canvas render becomes
exactly a white translucent overlay with no colour handling at all.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

# The kernel's support, in radii. Past this a Gaussian is visually zero, so the field is only
# computed inside a bounded box - which is what keeps stamping cheap.
_KERNEL_RADII = 2.6


@dataclass
class Blob:
    """One metaball. `born` is when it was emitted; `anchor` is where the head was when it left."""
    x: float
    y: float
    radius: float
    born: float
    dying: bool = False
    anchor: Optional[Tuple[float, float]] = None


@dataclass
class _TrailBlob:
    x: float
    y: float
    radius: float
    born: float


@dataclass
class Config:
    """Every knob, resolved from kinesis config in DesktopOverlay's constructor."""
    scale: int = 3
    max_fps: float = 180.0
    max_diameter_px: float = 80.0        # the head's largest size, across
    min_diameter_px: float = 10.0        # ... and its resting size
    grow_s: float = 1.2                  # dwell time to reach max_diameter_px
    dwell_reset_px: float = 90.0        # moving this far counts as a new dwell
    shrink_s: float = 0.5                # a trail blob's life; ends at radius 0
    trail_spacing_px: float = 26.0       # how far the head must travel to leave a blob
    trail_max: int = 48                  # hard cap, so a long trail cannot grow without bound
    trail_radius_ratio: float = 0.55     # a trail blob starts smaller than the head
    follow_tau_s: float = 0.030          # head easing time constant -> ~"native speed"
    # 20000 px/s, matching the gaze stabiliser: at 20 Hz gaze a one-screen saccade is 38,400 px/s,
    # so a tighter ceiling would make the head visibly lag a real look.
    max_speed_px_s: float = 20000.0      # ceiling so a bad sample cannot fling the head
    outline_px: float = 3.0              # SCREEN pixels
    outline_gain: float = 1.0
    glow_sigma_px: float = 9.0           # SCREEN pixels
    glow_gain: float = 0.34
    inner_gain: float = 0.20             # the faint wash inside the outline
    isolevel: float = 0.5
    gain: float = 1.0                    # master alpha multiplier
    gain_max: float = 1.0

    @classmethod
    def from_cfg(cls, cfg) -> "Config":
        def num(key, default):
            try:
                return float(cfg[key])
            except Exception:
                return float(default)
        return cls(
            scale=max(1, int(num("metaball_scale", 3))),
            max_fps=num("metaball_max_fps", 180.0),
            max_diameter_px=num("metaball_max_diameter_px", 80.0),
            min_diameter_px=num("metaball_min_diameter_px", 10.0),
            grow_s=num("metaball_grow_s", 1.2),
            dwell_reset_px=num("metaball_dwell_reset_px", 90.0),
            shrink_s=num("metaball_shrink_s", 0.5),
            trail_spacing_px=num("metaball_trail_spacing_px", 26.0),
            trail_max=int(num("metaball_trail_max", 48)),
            trail_radius_ratio=num("metaball_trail_radius_ratio", 0.55),
            follow_tau_s=num("metaball_follow_tau_s", 0.030),
            max_speed_px_s=num("metaball_max_speed_px_s", 20000.0),
            outline_px=num("metaball_outline_px", 3.0),
            outline_gain=num("metaball_outline_gain", 1.0),
            glow_sigma_px=num("metaball_glow_sigma_px", 9.0),
            glow_gain=num("metaball_glow_gain", 0.34),
            inner_gain=num("metaball_inner_gain", 0.20),
            isolevel=num("metaball_isolevel", 0.5),
            gain=num("metaball_gain", 1.0),
        )


def _downscale(mask: np.ndarray, scale: int, full) -> np.ndarray:
    """A full-resolution mask back down to field resolution, for the (soft) glow blur.

    The glow is the one part of the render that tolerates being soft, so it is computed at a third
    of the size and upscaled with everything else. Returns the input unchanged when the field is
    already at full resolution.
    """
    if scale <= 1 or mask.shape[:2] == (full[1] // scale, full[0] // scale):
        return mask
    return cv2.resize(mask, (max(8, full[0] // scale), max(8, full[1] // scale)),
                      interpolation=cv2.INTER_AREA)


def outline_small(outline: np.ndarray, scale: int, full) -> np.ndarray:
    return _downscale(outline, scale, full).astype(np.float32)


def eroded_small(eroded: np.ndarray, scale: int, full) -> np.ndarray:
    return _downscale(eroded, scale, full).astype(np.float32)


class MetaballState:
    """The shared, monitor-INDEPENDENT half: where the head is, how big, and the trail.

    Split from the field on purpose. A trail has to CROSS monitors - a blob left on the left screen
    must still be drawn while the head is on the right one - so the head position and the trail
    cannot live inside a per-monitor renderer. There is exactly one of these per overlay, and one
    MetaballField per panel.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.full_w = self.full_h = 0
        self._hx: Optional[float] = None
        self._hy: Optional[float] = None
        self._target: Optional[Tuple[float, float]] = None
        self._dwell_anchor: Optional[Tuple[float, float]] = None
        self._dwell_since = 0.0
        self._last_trail_at: Optional[Tuple[float, float]] = None
        self._trail: List[_TrailBlob] = []
        self._last_t = 0.0
        self.frames = 0
        self.last_ms = 0.0

    @property
    def head(self) -> Optional[Tuple[float, float]]:
        return None if self._hx is None else (self._hx, self._hy)

    @property
    def trail(self) -> List[_TrailBlob]:
        return self._trail

    def advance(self, gaze, present: bool = True, now: Optional[float] = None) -> None:
        """Move the head, age the trail. Call once per tick, before every panel's stamp."""
        t0 = time.perf_counter()
        now = now if now is not None else time.perf_counter()
        self.frames += 1

        usable = (gaze is not None and getattr(gaze, "valid", False)
                  and not getattr(gaze, "blink", False)
                  and not getattr(gaze, "rejected", False)
                  and present)
        self._target = (float(gaze.x), float(gaze.y)) if usable else None

        self._step_head(now)
        self._age_trail(now)
        self.last_ms = (time.perf_counter() - t0) * 1000.0

    def _step_head(self, now: float) -> None:
        """Ease the head toward the target; decide whether it has settled (dwell) or moved."""
        if self._target is None:
            # no gaze: the head coasts to a stop and the dwell is dropped, but the trail stays
            # alive so it finishes shrinking rather than vanishing
            self._dwell_anchor = None
            self._dwell_since = 0.0
            return
        tx, ty = self._target
        if self._hx is None or self._hy is None:
            self._hx, self._hy = tx, ty
            self._dwell_anchor = (tx, ty)
            self._dwell_since = now
            self._last_trail_at = (tx, ty)
            return

        # framerate-independent exponential ease, with a hard speed ceiling
        dt = self._dt(now)
        k = 1.0 - math.exp(-dt / max(self.cfg.follow_tau_s, 1e-3))
        nx = self._hx + (tx - self._hx) * k
        ny = self._hy + (ty - self._hy) * k
        step = math.hypot(nx - self._hx, ny - self._hy)
        limit = self.cfg.max_speed_px_s * dt
        if step > limit > 0:
            scale = limit / step
            nx = self._hx + (nx - self._hx) * scale
            ny = self._hy + (ny - self._hy) * scale
        self._hx, self._hy = nx, ny

        # dwell: reset when the head has travelled far enough to count as looking somewhere new
        if self._dwell_anchor is None:
            self._dwell_anchor = (nx, ny)
            self._dwell_since = now
        elif math.hypot(nx - self._dwell_anchor[0], ny - self._dwell_anchor[1]) \
                >= self.cfg.dwell_reset_px:
            self._dwell_anchor = (nx, ny)
            self._dwell_since = now

        # trail: drop a blob once the head has moved far enough from the last one
        if self._last_trail_at is None:
            self._last_trail_at = (nx, ny)
        elif math.hypot(nx - self._last_trail_at[0], ny - self._last_trail_at[1]) \
                >= self.cfg.trail_spacing_px:
            self._trail.append(_TrailBlob(nx, ny,
                                          self.head_radius(now) * self.cfg.trail_radius_ratio,
                                          now))
            self._last_trail_at = (nx, ny)

    def _dt(self, now: float) -> float:
        """Seconds since the last advance, clamped so a stalled frame cannot teleport the head."""
        if not self._last_t:
            self._last_t = now
            return 1.0 / max(self.cfg.max_fps, 1.0)
        dt = now - self._last_t
        self._last_t = now
        return min(max(dt, 1e-4), 0.1)

    def head_radius(self, now: float) -> float:
        """Head radius in FULL-RESOLUTION desktop pixels, growing with dwell."""
        if self._hx is None or self._dwell_anchor is None:
            return 0.0
        held = max(0.0, now - self._dwell_since)
        t = min(1.0, held / max(self.cfg.grow_s, 1e-3))
        t = t * t * (3.0 - 2.0 * t)          # smoothstep: eases in rather than ramping linearly
        lo = self.cfg.min_diameter_px * 0.5
        hi = self.cfg.max_diameter_px * 0.5
        return lo + (hi - lo) * t

    def _age_trail(self, now: float) -> None:
        life = max(self.cfg.shrink_s, 1e-3)
        kept: List[_TrailBlob] = []
        for blob in self._trail:
            age = now - blob.born
            if age >= life:
                continue                        # reached radius 0: gone
            # linear shrink to exactly 0 over `shrink_s`
            kept.append(_TrailBlob(blob.x, blob.y, blob.radius * (1.0 - age / life), blob.born))
        if len(kept) > self.cfg.trail_max:
            kept = kept[-self.cfg.trail_max:]
        self._trail = kept

    def reset(self) -> None:
        self._trail.clear()
        self._hx = self._hy = None
        self._target = None
        self._dwell_anchor = None
        self._dwell_since = 0.0
        self._last_trail_at = None
        self._last_t = 0.0


class MetaballField:
    """One panel's scalar field and its iso-surface. Clips the shared state to one monitor."""

    def __init__(self, cfg: Config, state: MetaballState, width: int, height: int,
                 origin: Tuple[int, int] = (0, 0)):
        self.cfg = cfg
        self.state = state
        self.origin_x, self.origin_y = int(origin[0]), int(origin[1])
        self.full_w, self.full_h = int(width), int(height)
        self.scale = max(1, cfg.scale)
        self.w = max(16, self.full_w // self.scale)
        self.h = max(16, self.full_h // self.scale)
        self.field = np.zeros((self.h, self.w), np.float32)
        self._out = np.empty((self.full_h, self.full_w), np.uint8)
        self._kernel = self._build_kernel()
        self.peak = 0.0
        self.stamped = 0
        # THE DIRTY RECTANGLE. A blob is at most 80 px across plus a ~27 px glow halo, so the region
        # that can possibly be non-transparent is roughly 220x220 - about 1% of a 1080p panel.
        # Processing the whole panel anyway is what held this at 28-66 fps: every erode, blur,
        # upscale and channel write was over 2 M pixels to fill about 48,000 of them. Only the dirty
        # rect is processed, so the per-frame cost tracks the blob, not the monitor.
        self._dx0 = self._dy0 = self._wx = self._hy = 0     # dirty rect in FIELD coords
        self._last_rect: Optional[Tuple[int, int, int, int]] = None   # in CANVAS coords

    # ------------------------------------------------------------------ geometry
    @property
    def _max_r(self) -> float:
        """Maximum blob radius, in FIELD pixels."""
        return max(1.0, (self.cfg.max_diameter_px * 0.5) / self.scale)

    def _build_kernel(self) -> np.ndarray:
        """One radial falloff at the maximum radius, reused for every blob by resizing."""
        r = int(math.ceil(self._max_r * _KERNEL_RADII)) + 2
        r = max(4, r)
        ys, xs = np.mgrid[-r:r + 1, -r:r + 1].astype(np.float32)
        d2 = xs * xs + ys * ys
        k = np.exp(-d2 / (2.0 * (self._max_r * 0.42) ** 2)).astype(np.float32)
        k[r, r] = 1.0
        return k

    def stamp(self, sx: float, sy: float, radius_px: float, now: float) -> None:
        """Add one blob's kernel into this panel's field, only inside its bounded box.

        `sx, sy` are DESKTOP coordinates, so the same blob appears on every panel whose rect
        contains it - which is what makes a trail cross monitors.
        """
        if radius_px <= 0.5:
            return
        fx = (sx - self.origin_x) / self.scale
        fy = (sy - self.origin_y) / self.scale
        r_field = radius_px / self.scale
        r_px = int(math.ceil(r_field * _KERNEL_RADII)) + 1
        r_px = max(2, r_px)
        # cheap reject before any slicing: the blob's box cannot touch this panel
        if (fx + r_px < 0 or fx - r_px > self.w
                or fy + r_px < 0 or fy - r_px > self.h):
            return
        cx, cy = int(round(fx)), int(round(fy))
        side = r_px * 2 + 1
        if side == self._kernel.shape[0]:
            k = self._kernel
        else:
            k = cv2.resize(self._kernel, (side, side), interpolation=cv2.INTER_AREA)
        x0, y0 = cx - r_px, cy - r_px
        x1, y1 = cx + r_px + 1, cy + r_px + 1
        sx0, sy0 = max(0, -x0), max(0, -y0)
        sx1, sy1 = min(side, self.w - x0), min(side, self.h - y0)
        if sx1 <= sx0 or sy1 <= sy0:
            return
        dx0, dy0 = max(0, x0), max(0, y0)
        dx1, dy1 = min(self.w, x1), min(self.h, y1)
        self.field[dy0:dy1, dx0:dx1] += k[sy0:sy1, sx0:sx1]
        self.stamped += 1

    def _stamp_measuring(self, sx: float, sy: float, radius_px: float) -> bool:
        """`stamp()`, but returns whether it landed and does not touch the field.

        `stamp_state` needs to know which blobs actually landed BEFORE it can compute the bounding
        box, and stamping twice per blob just to find out would double the cost of the cheap half of
        the render. This answers the same off-panel question without writing.
        """
        if radius_px <= 0.5:
            return False
        fx = (sx - self.origin_x) / self.scale
        fy = (sy - self.origin_y) / self.scale
        r_field = radius_px / self.scale
        r_px = int(math.ceil(r_field * _KERNEL_RADII)) + 1
        r_px = max(2, r_px)
        if (fx + r_px < 0 or fx - r_px > self.w
                or fy + r_px < 0 or fy - r_px > self.h):
            return False
        return True

    def stamp_state(self, now: float) -> None:
        """Rebuild this panel's field from the shared state: trail first, head last.

        Also records the bounding box of everything stamped, which is the only part of the panel that
        can be non-transparent next frame. `draw()` works on that rectangle alone.
        """
        self.field.fill(0.0)
        self.stamped = 0
        x0 = y0 = 1 << 30
        x1 = y1 = -(1 << 30)
        for blob in self.state.trail:
            if self._stamp_measuring(blob.x, blob.y, blob.radius):
                pad = int(math.ceil(blob.radius / self.scale * _KERNEL_RADII)) + 2
                cx = int(round((blob.x - self.origin_x) / self.scale))
                cy = int(round((blob.y - self.origin_y) / self.scale))
                x0, y0 = min(x0, cx - pad), min(y0, cy - pad)
                x1, y1 = max(x1, cx + pad), max(y1, cy + pad)
                self.stamp(blob.x, blob.y, blob.radius, now)
        head = self.state.head
        if head is not None:
            radius = self.state.head_radius(now)
            if self._stamp_measuring(head[0], head[1], radius):
                pad = int(math.ceil(radius / self.scale * _KERNEL_RADII)) + 2
                cx = int(round((head[0] - self.origin_x) / self.scale))
                cy = int(round((head[1] - self.origin_y) / self.scale))
                x0, y0 = min(x0, cx - pad), min(y0, cy - pad)
                x1, y1 = max(x1, cx + pad), max(y1, cy + pad)
                self.stamp(head[0], head[1], radius, now)

        if x1 < x0 or y1 < y0:
            self._wx = self._hy = 0
            self._last_rect = None
            return
        # clamp to the panel, then widen to cover the glow halo, which spills outside the kernels
        halo = int(math.ceil(self.cfg.glow_sigma_px / self.scale * 3.0)) + 2
        x0 = max(0, min(self.w - 1, x0 - halo))
        y0 = max(0, min(self.h - 1, y0 - halo))
        x1 = max(0, min(self.w - 1, x1 + halo))
        y1 = max(0, min(self.h - 1, y1 + halo))
        self._dx0, self._dy0 = x0, y0
        self._wx, self._hy = x1 - x0 + 1, y1 - y0 + 1
        self._last_rect = (x0 * self.scale, y0 * self.scale,
                           self._wx * self.scale, self._hy * self.scale)

    # ------------------------------------------------------------------ rendering
    def draw(self, canvas: np.ndarray) -> None:
        """Composite the field into a WHITE canvas (the overlay makes it translucent).

        White on all three channels is not a shortcut, it is the contract: `DesktopOverlay` takes
        `canvas.max(axis=2)` as alpha and premultiplies, so a white render needs no colour path at
        all.

        TWO STRUCTURAL RULES, BOTH MEASURED:

        1. **Everything happens at field resolution, and there is exactly one upscale.** Deriving the
           outline at full resolution put three full-resolution float passes in the hot path, at
           8-18 ms each on a 2 M-element array - the entire frame budget, several times over. It is
           not necessary: at `scale` = 3 one field pixel IS three screen pixels, so a one-field-pixel
           band upscales to exactly the 3 px outline the design calls for.

        2. **Only the dirty rectangle is touched.** A blob is at most 80 px across plus a ~27 px glow
           halo, so at most ~220x220 of a 1080p panel can be non-transparent - about 1%. Every
           erode, blur, upscale and channel write over the whole panel instead is the difference
           between 28 fps and 180.

        Two unit traps had to be true at once, and neither raises an error:
          * the layer is a 0.0-1.0 float, so it must be multiplied by 255 EXACTLY ONCE before the
            uint8 cast - clipping to 0-255 without that rounds every sub-1.0 value to 1 and the whole
            blob renders as a 1/255 smear;
          * the mask, the glow and the result must not share a buffer. `convertScaleAbs` writing into
            the array it was still blurring from produced exactly that smear.
        """
        if self._last_rect is None or self._wx <= 0 or self._hy <= 0:
            self.peak = 0.0
            return
        fx0, fy0, fx1, fy1 = self._dx0, self._dy0, self._dx0 + self._wx, self._dy0 + self._hy
        sub = self.field[fy0:fy1, fx0:fx1]
        peak = float(sub.max()) if sub.size else 0.0
        self.peak = peak
        if peak < 1e-3:
            return

        mask = (sub >= self.cfg.isolevel).astype(np.uint8)
        if not mask.any():
            return

        # outline: the band between the mask and its erosion. The kernel is ODD and at least 3, so
        # the band is one field pixel wide (a 1x1 kernel is the identity, which erases the outline).
        band = max(1, int(round(self.cfg.outline_px / self.scale)))
        kernel_size = band * 2 + 1
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
        eroded = cv2.erode(mask, k)
        # cv2.subtract, not numpy int16 arithmetic: byte-identical output at a fraction of the cost
        outline = cv2.subtract(mask, eroded)

        # glow: a blur of the OUTLINE spans the rim, a blur of the eroded interior gives the wash
        # inside it. Blurring the full mask instead lets the rim's two edges overlap in the blur and
        # come out brighter than the rim itself.
        sigma = max(0.8, self.cfg.glow_sigma_px / self.scale)
        glow = cv2.GaussianBlur(outline.astype(np.float32), (0, 0), sigma)
        inside = cv2.GaussianBlur(eroded.astype(np.float32), (0, 0), sigma)

        layer = (glow * self.cfg.glow_gain
                 + inside * self.cfg.inner_gain
                 + outline.astype(np.float32) * self.cfg.outline_gain)

        # the ONE upscale, of the ONE rect, then the ONE 0-255 conversion
        cx, cy, cw, ch = self._last_rect
        cw = max(1, min(cw, canvas.shape[1] - cx))
        ch = max(1, min(ch, canvas.shape[0] - cy))
        quantised = np.clip(layer * 255.0, 0.0, 255.0).astype(np.uint8)
        rect = np.empty((ch, cw), np.uint8)
        if (layer.shape[1], layer.shape[0]) != (cw, ch):
            cv2.resize(quantised, (cw, ch), dst=rect, interpolation=cv2.INTER_LINEAR)
        else:
            rect[:] = quantised
        white = cv2.convertScaleAbs(rect, alpha=max(0.0, self.cfg.gain))

        # WHITE EVERYWHERE, IN ONE CALL, INTO THE RECT ONLY. The alpha channel IS the intensity, so
        # the panel needs one plane replicated - but three `np.maximum` writes into a 2 M-element
        # canvas measured 9.1 ms, and `merge` without a destination another 4.3 ms. `cvtColor` into
        # the destination view costs 2.4 ms on the full panel, and a fraction of that on a rect.
        view = canvas[cy:cy + ch, cx:cx + cw]
        cv2.cvtColor(white, cv2.COLOR_GRAY2BGR, dst=view)

    def reset(self) -> None:
        self.field.fill(0.0)
        self.stamped = 0
