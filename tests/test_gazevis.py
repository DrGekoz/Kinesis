"""Tests for the gaze overlay renderer: styles, decay, heat, bloom, text, theme sanity.

No camera and no virtual-camera device: the visualiser is fed synthetic gaze states and its output
is inspected directly.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis.config import Config                                    # noqa: E402
from kinesis.gazevis import THEMES, STYLES, GazeVisualizer, text      # noqa: E402

W, H = 640, 360


def cfg_with(**kw):
    c = Config()
    for k, v in kw.items():
        c.set(k, v)
    return c


def vis(**kw):
    return GazeVisualizer(cfg_with(**kw), W, H)


def gaze(x, y, valid=True, age=0.0):
    return SimpleNamespace(x=x, y=y, valid=valid, age=age)


def feed(v, pts, frames_each=1):
    """Advance the visualiser through a list of gaze points; returns the total brightness."""
    for (x, y) in pts:
        for _ in range(frames_each):
            v.advance(gaze(x, y))
    return float(v._trail.sum())


def test_layers_render_below_canvas_resolution():
    """Full-resolution float layers measured 59-140 ms per frame at 1080p. The reduction to an
    internal resolution is load-bearing, so pin it."""
    v = vis(vcam_style="comet", vcam_visual_scale=3)
    assert v._sw < W and v._sh < H
    assert v._trail.dtype == np.float32
    assert v._layer.dtype == np.uint8
    assert v._layer.shape == (H, W, 3)
    full = vis(vcam_style="comet", vcam_visual_scale=1)     # allowed, just costly
    assert full._sw == W and full._sh == H


def test_scale_is_clamped_to_something_sane():
    v = vis(vcam_style="comet", vcam_visual_scale=0)        # nonsense input
    assert v._scale >= 1
    assert v._sw >= 8 and v._sh >= 8


def test_every_theme_colormap_exists():
    """cv2.COLORMAP_ICE does not exist - an invented name was caught by exactly this test."""
    for name, theme in THEMES.items():
        cmap = theme["heat"]
        assert isinstance(cmap, int), f"{name}: colormap is not an int"
        assert 0 <= cmap <= 21, f"{name}: colormap id {cmap} is not a real OpenCV colormap"
        for key in ("head", "tail"):
            assert len(theme[key]) == 3, f"{name}.{key} must be an RGB triple"


def test_unknown_style_and_theme_fall_back():
    v = vis(vcam_style="nonsense", vcam_theme="nonsense")
    assert v.style in STYLES
    assert v.theme_name == "nonsense"          # recorded as asked, but themed safely
    assert v.head_bgr and len(v.head_bgr) == 3


def test_pointer_leaves_no_trail():
    v = vis(vcam_style="pointer")
    feed(v, [(100, 100), (200, 120), (300, 140)])
    # the reticle is stamped, but nothing persists from earlier frames
    assert v._trail.sum() > 0
    before = v._trail.copy()
    v.advance(gaze(400, 160))
    assert v._trail.sum() > 0
    assert not np.allclose(before, v._trail)   # cleared and restamped at the new point


def test_comet_accumulates_a_tail():
    v = vis(vcam_style="comet", vcam_trail_decay=0.9)
    strength = feed(v, [(50 + i * 8, 180) for i in range(30)])
    v.advance(gaze(300, 180))                  # one more frame without stamping the tail
    assert strength > 0
    assert v._trail.sum() > 0


def test_trail_decay_fades_over_frames():
    v = vis(vcam_style="comet", vcam_trail_decay=0.8)
    feed(v, [(200, 180)])
    first = v._trail.sum()
    for _ in range(6):
        v.advance(gaze(200, 180, valid=False))    # gaze lost: no new stamp, just decay
    assert v._trail.sum() < first * 0.5


def test_lower_decay_persists_less():
    strong = vis(vcam_style="comet", vcam_trail_decay=0.95)
    weak = vis(vcam_style="comet", vcam_trail_decay=0.6)
    for v in (strong, weak):
        v.advance(gaze(200, 180))
    for _ in range(4):
        strong.advance(gaze(200, 180, valid=False))
        weak.advance(gaze(200, 180, valid=False))
    assert strong._trail.sum() > weak._trail.sum()


def test_heatmap_only_draws_where_gaze_has_been():
    """Layer buffers are internal-resolution, so assert against the buffer in ITS space and the
    painted result in canvas space."""
    v = vis(vcam_style="heatmap", vcam_heat_radius=30)
    feed(v, [(100, 100)] * 3)
    bx, by = v.point(gaze(100, 100))
    assert v._heat[by, bx] > 0                       # stamped where the gaze was
    assert v._heat[min(by + 30, v._sh - 1), min(bx + 30, v._sw - 1)] == 0   # nowhere else
    canvas = np.zeros((H, W, 3), np.uint8)
    v.draw(canvas)
    assert canvas[100, 100].max() > 0                # painted on the canvas
    assert canvas[300, 300].max() == 0               # and not painted elsewhere


def test_draw_is_additive_and_bounds_safe():
    v = vis(vcam_style="comet", vcam_glow=1.2)
    feed(v, [(10, 10), (320, 180), (630, 350)])   # corners: must not raise
    canvas = np.zeros((H, W, 3), np.uint8)
    v.draw(canvas)
    assert canvas.max() > 0
    assert canvas.shape == (H, W, 3)


def test_none_style_draws_nothing():
    v = vis(vcam_style="none")
    canvas = np.zeros((H, W, 3), np.uint8)
    v.advance(gaze(100, 100))
    v.draw(canvas)
    assert canvas.max() == 0


def test_gaze_outside_the_desktop_is_clamped_into_the_canvas():
    v = vis(vcam_style="comet")
    v.advance(gaze(-50000, 50000))
    x, y = v._points[-1]
    assert 0 <= x < W and 0 <= y < H


def test_glow_brightens_the_layer():
    plain = vis(vcam_style="comet", vcam_glow=0.0)
    glowing = vis(vcam_style="comet", vcam_glow=1.5)
    for v in (plain, glowing):
        feed(v, [(200, 180)])
        canvas = np.zeros((H, W, 3), np.uint8)
        v.draw(canvas)
        v._drawn = float(canvas.sum())
    assert glowing._drawn > plain._drawn


def test_text_draws_pixels_and_caches():
    canvas = np.zeros((H, W, 3), np.uint8)
    text(canvas, "seat 700 mm  target: Chrome", (12, 12), size=20)
    assert canvas.max() > 0
    from kinesis import gazevis
    assert len(gazevis._TILES) >= 1
    n = len(gazevis._TILES)
    text(canvas, "seat 700 mm  target: Chrome", (12, 12), size=20)   # same key
    assert len(gazevis._TILES) == n                                  # served from cache


def test_text_handles_empty_and_overlong_strings():
    canvas = np.zeros((H, W, 3), np.uint8)
    text(canvas, "", (0, 0))
    text(canvas, "x" * 500, (0, 0), size=14)      # clipped, must not raise
    assert canvas.shape == (H, W, 3)


def test_status_reports_style_and_theme():
    v = vis(vcam_style="path", vcam_theme="cyan", vcam_glow=1.0)
    s = v.status()
    assert "path" in s and "cyan" in s


@pytest.mark.parametrize("style", STYLES)
def test_every_style_survives_a_frame(style):
    v = vis(vcam_style=style)
    feed(v, [(100, 100), (300, 200)])
    canvas = np.zeros((H, W, 3), np.uint8)
    v.draw(canvas)
    assert canvas.shape == (H, W, 3)

