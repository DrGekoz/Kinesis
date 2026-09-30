"""Gaze visualisation: the pretty layer.

Everything is rendered into one float32 accumulation buffer and composited additively, which is what
gives trails, phosphor decay and bloom for almost nothing:

    decay the buffer  ->  stamp this frame's contribution  ->  blur the buffer into itself (glow)
    ->  add the buffer to the canvas

Styles pick which layers get stamped and how fast the buffer fades:

    pointer        a crisp reticle, nothing persists. The functional look.
    comet          a tapered tail behind the reticle, fading like a phosphor trace.
    path           the last N seconds of travel drawn as a tapering polyline - the gaze-plot look.
    heatmap        accumulated dwell rendered through a colormap; where you have been looking.
    heatmap_comet  both.
    none           nothing but the hand skeleton.

Text uses a real TTF through Pillow (OpenCV's Hershey fonts are why the old HUD looked like a 1994
demo). Tiles are rendered once per string+size+colour and cached, so the per-frame cost is a blit.
"""
from __future__ import annotations

import collections
import math
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import cv2
import numpy as np

# themes are declared in RGB (how humans read colour) and converted on use
THEMES: Dict[str, Dict[str, object]] = {
    "ember": {"head": (255, 178, 92), "tail": (255, 96, 20), "heat": cv2.COLORMAP_TURBO},
    "cyan": {"head": (152, 240, 255), "tail": (0, 168, 224), "heat": cv2.COLORMAP_TURBO},
    "violet": {"head": (214, 170, 255), "tail": (146, 78, 232), "heat": cv2.COLORMAP_MAGMA},
    "lime": {"head": (196, 255, 150), "tail": (56, 208, 92), "heat": cv2.COLORMAP_VIRIDIS},
    "ice": {"head": (240, 250, 255), "tail": (120, 180, 250), "heat": cv2.COLORMAP_WINTER},
}
DEFAULT_THEME = "ember"

STYLES = ("pointer", "comet", "path", "heatmap", "heatmap_comet", "none")


def _bgr(rgb: Sequence[int]) -> Tuple[int, int, int]:
    return (int(rgb[2]), int(rgb[1]), int(rgb[0]))


# --------------------------------------------------------------------------- text
_FONTS: Dict[Tuple[int, bool], object] = {}
_TILES: Dict[tuple, np.ndarray] = {}
TILE_CACHE_MAX = 160
_FONT_FILES = ("bahnschrift.ttf", "seguisb.ttf", "segoeuib.ttf", "ariblk.ttf", "DejaVuSans-Bold.ttf")


def _font(size: int, bold: bool = True):
    key = (size, bold)
    if key not in _FONTS:
        font = None
        for name in _FONT_FILES:
            path = Path("C:/Windows/Fonts") / name
            if path.exists():
                try:
                    from PIL import ImageFont
                    font = ImageFont.truetype(str(path), size)
                    break
                except Exception:
                    font = None
        if font is None:
            from PIL import ImageFont
            font = ImageFont.load_default()
        _FONTS[key] = font
    return _FONTS[key]


def _render_tile(text: str, size: int, rgb, glow_rgb, glow: int) -> np.ndarray:
    """One text string as an RGBA tile, with an optional bloom behind it."""
    from PIL import Image, ImageDraw, ImageFilter

    font = _font(size)
    probe = Image.new("RGBA", (8, 8))
    left, top, right, bottom = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
    pad = glow * 2 + 4
    w = max(1, right - left + pad * 2)
    h = max(1, bottom - top + pad * 2)
    tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    if glow_rgb is not None and glow > 0:
        halo = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(halo).text((pad - left, pad - top), text, font=font,
                                  fill=tuple(glow_rgb) + (255,))
        tile = Image.alpha_composite(tile, halo.filter(ImageFilter.GaussianBlur(glow)))
        draw = ImageDraw.Draw(tile)
    draw.text((pad - left, pad - top), text, font=font, fill=tuple(rgb) + (255,))
    return np.array(tile)          # RGBA, PIL order


def text(canvas: np.ndarray, s: str, xy: Tuple[int, int], size: int = 26,
         rgb=(255, 255, 255), glow_rgb: Optional[Sequence[int]] = None, glow: int = 8) -> None:
    """Blit `s` onto a BGR canvas with alpha, using a real font. Cached."""
    if not s:
        return
    key = (s, int(size), tuple(rgb), tuple(glow_rgb) if glow_rgb else None, int(glow))
    tile = _TILES.get(key)
    if tile is None:
        tile = _render_tile(s, int(size), rgb, glow_rgb, int(glow))
        if len(_TILES) > TILE_CACHE_MAX:
            _TILES.clear()
        _TILES[key] = tile
    th, tw = tile.shape[:2]
    x, y = int(xy[0]), int(xy[1])
    ch, cw = canvas.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(cw, x + tw), min(ch, y + th)
    if x1 <= x0 or y1 <= y0:
        return
    sub = tile[y0 - y:y1 - y, x0 - x:x1 - x]
    alpha = sub[..., 3:4].astype(np.float32) / 255.0
    bgr = sub[..., 2::-1].astype(np.float32)          # RGBA -> BGR
    region = canvas[y0:y1, x0:x1].astype(np.float32)
    canvas[y0:y1, x0:x1] = (region * (1.0 - alpha) + bgr * alpha).astype(np.uint8)


def text_width(s: str, size: int = 26) -> int:
    from PIL import Image, ImageDraw
    if not s:
        return 0
    probe = Image.new("RGBA", (8, 8))
    left, _, right, _ = ImageDraw.Draw(probe).textbbox((0, 0), s, font=_font(size))
    return right - left


# --------------------------------------------------------------------------- visualiser
class GazeVisualizer:
    """Accumulation-buffer renderer for the gaze layer."""

    def __init__(self, cfg, width: int, height: int, to_canvas=None):
        self.style = str(cfg["vcam_style"]).lower()
        if self.style not in STYLES:
            self.style = "comet"
        theme = THEMES.get(str(cfg["vcam_theme"]).lower()) or THEMES[DEFAULT_THEME]
        self.theme = theme
        self.theme_name = str(cfg["vcam_theme"]).lower()
        self.head_bgr = _bgr(theme["head"])
        self.tail_bgr = _bgr(theme["tail"])
        self._decay_base = float(np.clip(float(cfg["vcam_trail_decay"]), 0.0, 0.99))
        self.tail_points = max(2, int(cfg["vcam_tail_points"]))
        self.glow_strength = float(cfg["vcam_glow"])
        self.heat_radius = max(1, int(cfg["vcam_heat_radius"]))
        self.heat_gain = float(cfg["vcam_heat_gain"])
        self._w, self._h = int(width), int(height)
        # All layers are rendered at a fraction of the canvas and upscaled once on composite. A
        # soft trail does not need 1080p precision, and full-resolution float buffers meant ~74 MB
        # of temporaries per frame (measured at 59-140 ms/frame, far over the frame budget).
        self._scale = max(1, int(cfg["vcam_visual_scale"]))
        self._sw = max(8, self._w // self._scale)
        self._sh = max(8, self._h // self._scale)
        self._r_core = max(2, int(self._h * 0.006 / self._scale))
        self._r_halo = max(self._r_core + 2, int(self._h * 0.015 / self._scale))
        self._r_ring = max(self._r_core + 1, int(self._h * 0.012 / self._scale))
        self._heat_r = max(1, self.heat_radius // self._scale)
        self._trail = np.zeros((self._sh, self._sw, 3), np.float32)
        self._heat = np.zeros((self._sh, self._sw), np.float32)
        self._points = collections.deque(maxlen=self.tail_points)
        self._to_canvas = to_canvas
        self._last_pt: Optional[Tuple[int, int]] = None
        self._layer = np.empty((self._h, self._w, 3), np.uint8)
        self.frames = 0

    # ---------------------------------------------------------------- geometry
    def point(self, gaze) -> Tuple[int, int]:
        if self._to_canvas is not None:
            x, y = self._to_canvas(gaze.x, gaze.y, self._sw, self._sh)
        else:
            x, y = gaze.x / self._scale, gaze.y / self._scale
        return (int(min(max(x, 0), self._sw - 1)), int(min(max(y, 0), self._sh - 1)))

    # ---------------------------------------------------------------- per frame
    @property
    def _decay(self) -> float:
        if self.style == "pointer":
            return 0.0                       # nothing persists: the reticle is the whole point
        if self.style == "path":
            return math.sqrt(self._decay_base)    # long memory for the plotted path
        return self._decay_base

    def advance(self, gaze) -> None:
        """Called once per composed frame: decay, then stamp this frame's contribution.

        All mutation happens here; draw() only composites. Keeping the two separate means a frame
        that is drawn twice does not double-expose the reticle.
        """
        self.frames += 1
        decay = self._decay
        if decay <= 0.0:
            self._trail.fill(0.0)
            self._heat.fill(0.0)
        else:
            self._trail *= decay
            self._heat *= decay

        if gaze is None or not getattr(gaze, "valid", False):
            return
        pt = self.point(gaze)
        self._points.append(pt)
        if self.style in ("comet", "heatmap_comet") and self._last_pt is not None:
            cv2.line(self._trail, self._last_pt, pt, self.tail_bgr, max(1, 3 // self._scale),
                     cv2.LINE_AA)
        if self.style in ("heatmap", "heatmap_comet"):
            cv2.circle(self._heat, pt, self._heat_r, 1.0, -1, cv2.LINE_AA)
        if self.style in ("pointer", "comet", "path", "heatmap_comet"):
            self._stamp_head()
        if self.style == "path":
            self._stamp_path()
        self._last_pt = pt

    # ---------------------------------------------------------------- rendering
    def draw(self, canvas: np.ndarray) -> None:
        if self.style == "none":
            return
        if self.style in ("heatmap", "heatmap_comet"):
            self._add_heat(canvas)
        layer = self._trail
        if self.glow_strength > 0 and layer.any():
            layer = layer + self._glow(layer) * self.glow_strength
            np.clip(layer, 0, 255, out=layer)
        small = layer.astype(np.uint8)
        if (self._sw, self._sh) == (self._w, self._h):
            cv2.add(canvas, small, dst=canvas)
            return
        cv2.resize(small, (self._w, self._h), dst=self._layer, interpolation=cv2.INTER_LINEAR)
        cv2.add(canvas, self._layer, dst=canvas)

    def _stamp_head(self) -> None:
        if not self._points:
            return
        pt = self._points[-1]
        cv2.circle(self._trail, pt, self._r_halo,
                   tuple(c * 0.22 for c in self.head_bgr), -1, cv2.LINE_AA)
        cv2.circle(self._trail, pt, self._r_core, self.head_bgr, -1, cv2.LINE_AA)
        cv2.circle(self._trail, pt, self._r_ring, self.head_bgr, 1, cv2.LINE_AA)

    def _stamp_path(self) -> None:
        """The gaze-plot look: the recent travel as a tapering line."""
        pts = list(self._points)
        n = len(pts)
        if n < 2:
            return
        thick = max(1, 2 // self._scale)
        for i in range(n - 1):
            alpha = ((i + 1) / n) ** 2.5
            colour = tuple(c * alpha for c in self.tail_bgr)
            cv2.line(self._trail, pts[i], pts[i + 1], colour, thick, cv2.LINE_AA)
        for i, pt in enumerate(pts[:-1]):
            r = max(1, (2 if (i + 1) / n > 0.5 else 1) // self._scale)
            cv2.circle(self._trail, pt, r, tuple(c * ((i + 1) / n) for c in self.tail_bgr), -1,
                       cv2.LINE_AA)

    def _glow(self, layer: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(layer, (0, 0), max(1.5, 7.0 / self._scale))

    def _add_heat(self, canvas: np.ndarray) -> None:
        peak = float(self._heat.max())
        if peak < 1e-4:
            return
        norm = np.clip(self._heat / peak * self.heat_gain, 0.0, 1.0)
        u8 = (norm * 255.0).astype(np.uint8)
        colour = cv2.applyColorMap(u8, self.theme["heat"])
        colour[norm <= 0.06] = 0                     # never tint untouched areas
        big = cv2.resize(colour, (self._w, self._h), interpolation=cv2.INTER_LINEAR)
        cv2.addWeighted(canvas, 1.0, big, 0.55, 0.0, dst=canvas)

    # ---------------------------------------------------------------- telemetry
    def status(self) -> str:
        bits = [self.style, self.theme_name]
        if self.glow_strength > 0:
            bits.append(f"glow {self.glow_strength:.1f}")
        return " ".join(bits)
