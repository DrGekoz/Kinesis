"""The gaze metaball: dwell growth, trail shrink, iso-surface, and multi-monitor crossing.

The renderer is pure maths on numpy arrays with no window and no camera, so every claim it makes is
checkable here: that the blob MERGES rather than showing as overlapping circles, that a trail blob
reaches exactly radius 0 after `shrink_s`, that the head swells to `max_diameter_px` and no further,
and that a blob left on one monitor is still drawn on another.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis.config import Config, DEFAULTS                                  # noqa: E402
from kinesis.metaball import (Config as MBCfg, MetaballField,  # noqa: E402
                             MetaballState, _TrailBlob)


def _gaze(x, y, valid=True, blink=False, rejected=False):
    return SimpleNamespace(x=x, y=y, valid=valid, blink=blink, rejected=rejected)


def _cfg(**kw):
    return MBCfg(**kw)


def _state(**kw):
    return MetaballState(_cfg(**kw))


def _field(state, w=640, h=480, origin=(0, 0), **kw):
    cfg = state.cfg
    for k, v in kw.items():
        setattr(cfg, k, v)
    return MetaballField(cfg, state, w, h, origin=origin)


# ============================================================== dwell growth
def test_the_head_starts_at_the_minimum_and_swells_to_the_maximum():
    s = _state(max_diameter_px=80.0, min_diameter_px=10.0, grow_s=1.0)
    s.advance(_gaze(300, 200), now=100.0)
    assert round(s.head_radius(100.0) * 2) == 10, (
        f"expected the 10 px resting size, got {s.head_radius(100.0) * 2}"
    )
    s.advance(_gaze(300, 200), now=102.0)          # past grow_s
    assert round(s.head_radius(102.0) * 2) == 80, (
        f"expected the 80 px maximum, got {s.head_radius(102.0) * 2}"
    )


def test_the_head_never_exceeds_the_maximum():
    s = _state(max_diameter_px=80.0, grow_s=0.5)
    s.advance(_gaze(300, 200), now=100.0)
    for t in (101.0, 105.0, 200.0, 9999.0):
        s.advance(_gaze(300, 200), now=t)
        assert s.head_radius(t) * 2 <= 80.0 + 1e-6, (
            f"head grew past the cap: {s.head_radius(t) * 2} px"
        )


def test_moving_the_gaze_resets_the_dwell():
    """Otherwise the blob would stay huge while you are reading across the screen."""
    s = _state(max_diameter_px=80.0, grow_s=0.5, dwell_reset_px=90.0)
    s.advance(_gaze(300, 200), now=100.0)
    s.advance(_gaze(300, 200), now=101.5)         # nearly at max
    assert s.head_radius(101.5) * 2 > 60.0
    s.advance(_gaze(900, 500), now=101.6)         # looked somewhere else
    s.advance(_gaze(900, 500), now=101.7)
    assert s.head_radius(101.7) * 2 < 30.0, (
        f"the dwell did not reset on a move: {s.head_radius(101.7) * 2} px"
    )


# ============================================================== the trail
def test_moving_the_head_leaves_a_trail():
    s = _state(trail_spacing_px=26.0, trail_max=48, shrink_s=0.5, max_diameter_px=40.0)
    s.advance(_gaze(100, 100), now=100.0)
    for i in range(1, 12):
        s.advance(_gaze(100 + i * 30, 100), now=100.0 + i * 0.016)
    assert len(s.trail) >= 3, f"expected a trail, got {len(s.trail)} blobs"


def test_a_trail_blob_shrinks_to_exactly_zero_over_the_configured_time():
    s = _state(shrink_s=0.5)
    s.advance(_gaze(100, 100), now=100.0)
    for i in range(1, 8):
        s.advance(_gaze(100 + i * 40, 100), now=100.0 + i * 0.016)
    assert s.trail, "no trail to age"
    born = s.trail[0].born
    start_r = s.trail[0].radius
    # halfway through its life it should be about half the size
    s.advance(_gaze(400, 100), now=born + 0.25)
    half = [b for b in s.trail if b.born == born]
    if half:
        assert half[0].radius < start_r, "the trail blob did not shrink"
    # and past its life it is gone entirely (radius 0 == not drawn)
    s.advance(_gaze(400, 100), now=born + 0.5)
    assert all(b.born != born for b in s.trail), (
        "a trail blob outlived shrink_s instead of reaching radius 0"
    )


def test_the_trail_is_capped():
    s = _state(trail_spacing_px=5.0, trail_max=8, shrink_s=99.0)
    s.advance(_gaze(10, 10), now=100.0)
    for i in range(1, 200):
        s.advance(_gaze(10 + i * 6, 10), now=100.0 + i * 0.01)
    assert len(s.trail) <= 8, f"trail grew to {len(s.trail)} blobs"


# ============================================================== the head eases
def test_the_head_animates_rather_than_teleporting():
    """A single large jump must be spread over frames, not applied in one."""
    s = _state(follow_tau_s=0.030, max_speed_px_s=1e9)
    s.advance(_gaze(100, 100), now=100.0)
    s.advance(_gaze(1100, 100), now=100.0 + 1.0 / 180.0)
    head = s.head
    assert 100.0 < head[0] < 1100.0, (
        f"the head teleported instead of easing: {head[0]}"
    )


def test_the_speed_ceiling_stops_a_bad_sample_flinging_the_head():
    s = _state(max_speed_px_s=2000.0, follow_tau_s=0.001)
    s.advance(_gaze(100, 100), now=100.0)
    s.advance(_gaze(100_000, 100), now=100.0 + 0.05)
    head = s.head
    assert head[0] - 100.0 <= 2000.0 * 0.05 + 1.0, (
        f"the speed ceiling did not apply: moved {head[0] - 100.0} px"
    )


# ============================================================== the iso-surface
def test_overlapping_blobs_merge_into_one_shape_with_a_neck():
    """This is what makes it a metaball rather than a chain of circles.

    Two blobs whose centres are closer than the merge distance must produce ONE connected component
    whose area is greater than either circle alone - if they merely overlapped, the component count
    would still be 1 but the outline would show a pinch; so the real assertion is that the field
    between them rises above the iso-level.
    """
    cfg = _cfg(isolevel=0.5, max_diameter_px=80.0, scale=1)
    s = MetaballState(cfg)
    f = MetaballField(cfg, s, 300, 200, origin=(0, 0))
    # Two FULL-SIZE blobs 45 px apart. The separation matters and is measured, not guessed: with a
    # 40 px radius the field at the midpoint crosses the 0.5 iso-level at about 52 px separation, so
    # 45 px merges and 60 px does not. Both cases are asserted below.
    s._trail.append(_TrailBlob(100.0, 100.0, 40.0, 100.0))
    s._trail.append(_TrailBlob(145.0, 100.0, 40.0, 100.0))
    f.stamp_state(100.0)
    mask = (f.field >= cfg.isolevel).astype(np.uint8)
    assert mask.any(), "nothing was drawn"
    midpoint = float(f.field[100, 122])
    assert midpoint >= cfg.isolevel, (
        f"the two blobs did not merge: the field between them is {midpoint:.3f}, "
        f"below the iso-level {cfg.isolevel}"
    )
    # one connected component, not two
    import cv2
    n, _ = cv2.connectedComponents(mask)
    assert n == 2, f"expected 1 blob + background, found {n - 1} separate blobs"

    # ...and the merge is genuinely a THRESHOLD effect, not "they overlap because the radii said so":
    # at 60 px apart the same two blobs must NOT merge. Without this the test would pass for any
    # pair of overlapping circles, which is exactly what a metaball must not be.
    far = MetaballState(cfg)
    ff = MetaballField(cfg, far, 300, 200, origin=(0, 0))
    far._trail.append(_TrailBlob(100.0, 100.0, 40.0, 100.0))
    far._trail.append(_TrailBlob(160.0, 100.0, 40.0, 100.0))
    ff.stamp_state(100.0)
    far_mask = (ff.field >= cfg.isolevel).astype(np.uint8)
    fn, _ = cv2.connectedComponents(far_mask)
    assert fn == 3, (
        f"two blobs 60 px apart merged into {fn - 1} shapes - the kernel is too wide to be a metaball"
    )


def test_the_outline_is_only_the_rim_not_the_whole_shape():
    """Outline-only means the interior is the faint inner wash, not the full-strength line."""
    cfg = _cfg(isolevel=0.5, max_diameter_px=80.0, scale=1, outline_gain=1.0,
               glow_gain=0.34, inner_gain=0.20, min_diameter_px=80.0)
    s = MetaballState(cfg)
    f = MetaballField(cfg, s, 300, 200, origin=(0, 0))
    # min_diameter == max_diameter so the head is a known 80 px across whatever the dwell is,
    # which is what lets the rim be sampled at a fixed offset from the centre.
    s.advance(_gaze(150.0, 100.0), now=100.0)
    f.stamp_state(100.0)
    canvas = np.zeros((200, 300, 3), np.uint8)
    f.draw(canvas)
    row = canvas[100, :, 0]
    lit = np.nonzero(row)[0]
    assert lit.size, "nothing was drawn"
    span = int(lit.max() - lit.min())
    assert 74 <= span <= 86, f"expected an ~80 px blob, measured {span} px across"
    peak = int(canvas.max())
    # the rim is the bright band just inside the edge, not a fixed offset: sample the brightest
    # pixel in the outer 15% of the radius, and the middle for the interior
    # the rim is the full-strength band: take the brightest pixel of the profile itself rather than
    # guessing an offset, then compare it with the fill at the very centre
    rim = int(row.max())
    centre = int(canvas[100, 150].max())
    assert peak == 255, f"the outline did not reach full strength: {peak}"
    assert centre > 0, "the interior is empty - the blob is a hollow ring, not a filled one"
    assert rim > centre, (
        f"the rim ({rim}) must be brighter than the interior ({centre})"
    )
    # and the outline is the requested thickness, not a hairline or a slab
    thick = int((row >= 250).sum())
    assert 2 <= thick <= 6, f"the outline is {thick} px thick, expected about 3"
    # the fill must be faint: a solid disc would read as a solid object, not a translucent blob
    assert centre < 255, "the interior is fully opaque"


def test_nothing_is_drawn_without_a_gaze():
    cfg = _cfg()
    s = MetaballState(cfg)
    f = MetaballField(cfg, s, 200, 200, origin=(0, 0))
    canvas = np.zeros((200, 200, 3), np.uint8)
    f.draw(canvas)
    assert canvas.max() == 0, "an empty field drew something"


def test_the_render_is_white_so_the_overlay_alpha_is_the_intensity():
    """The overlay premultiplies canvas.max(axis=2) into alpha, so a non-white render would tint."""
    cfg = _cfg(max_diameter_px=80.0, min_diameter_px=80.0, scale=1)
    s = MetaballState(cfg)
    f = MetaballField(cfg, s, 200, 200, origin=(0, 0))
    s.advance(_gaze(100.0, 100.0), now=100.0)
    f.stamp_state(100.0)
    canvas = np.zeros((200, 200, 3), np.uint8)
    f.draw(canvas)
    lit = canvas.max(axis=2) > 0
    assert lit.any()
    px = canvas[lit]
    assert (px[..., 0] == px[..., 1]).all() and (px[..., 1] == px[..., 2]).all(), (
        "the metaball rendered a colour instead of white"
    )


# ============================================================== multi-monitor
def test_a_blob_left_on_one_monitor_is_still_drawn_on_the_next():
    """A trail has to CROSS monitors. The shared state holds desktop coordinates; each field maps
    them into its own rect and clips what falls outside."""
    cfg = _cfg(max_diameter_px=80.0, min_diameter_px=80.0, scale=1, trail_spacing_px=30.0)
    s = MetaballState(cfg)
    left = MetaballField(cfg, s, 1920, 1080, origin=(0, 0))        # the primary
    right = MetaballField(cfg, s, 1920, 1080, origin=(1920, 0))   # the screen to its right

    # x=1910 with a 40 px radius straddles the seam at x=1920, so it must appear on BOTH panels
    s.advance(_gaze(1910.0, 500.0), now=100.0)
    left.stamp_state(100.0)
    right.stamp_state(100.0)
    assert left.field.max() > 0, "the blob vanished from the screen it is on"
    assert right.field.max() > 0, (
        "the blob did not cross the seam onto the adjacent monitor"
    )


def test_a_panel_completely_off_the_blobs_stamps_nothing():
    cfg = _cfg(max_diameter_px=80.0, min_diameter_px=80.0, scale=1)
    s = MetaballState(cfg)
    far = MetaballField(cfg, s, 1920, 1080, origin=(3840, 0))    # two screens to the right
    s.advance(_gaze(100.0, 100.0), now=100.0)
    far.stamp_state(100.0)
    assert far.stamped == 0 and far.field.max() == 0.0


def test_advancing_the_state_repeatedly_moves_the_head_further():
    """The counterpart to the bug the loop avoids: advancing more often must move the head MORE,
    which is exactly why DesktopOverlay._loop advances once per tick and not once per panel.

    (This pins the mechanism. The per-tick-only guarantee is in
    test_desktop_overlay_advances_the_metaball_once_per_tick.)
    """
    cfg = _cfg(follow_tau_s=0.030, max_speed_px_s=1e9)
    s = MetaballState(cfg)
    s.advance(_gaze(100, 100), now=100.0)
    s.advance(_gaze(1100, 100), now=100.0 + 1 / 180)
    after_one = s.head[0]
    for _ in range(3):                       # three advances at the same target
        s.advance(_gaze(1100, 100), now=100.0 + 2 / 180)
    assert s.head[0] > after_one, (
        f"extra advances did not move the head further: {after_one} -> {s.head[0]}"
    )


# ============================================================== config
def test_the_metaball_config_keys_exist():
    for key in ("metaball_scale", "metaball_max_fps", "metaball_max_diameter_px",
                "metaball_min_diameter_px", "metaball_grow_s", "metaball_dwell_reset_px",
                "metaball_shrink_s", "metaball_trail_spacing_px", "metaball_trail_max",
                "metaball_trail_radius_ratio", "metaball_follow_tau_s",
                "metaball_max_speed_px_s", "metaball_outline_px", "metaball_outline_gain",
                "metaball_glow_sigma_px", "metaball_glow_gain", "metaball_inner_gain",
                "metaball_isolevel", "metaball_gain"):
        assert key in DEFAULTS, f"{key} is missing from DEFAULTS"
    assert DEFAULTS["metaball_max_diameter_px"] == 80.0
    assert DEFAULTS["metaball_outline_px"] == 3.0
    assert DEFAULTS["metaball_shrink_s"] == 0.5
    assert DEFAULTS["metaball_max_fps"] == 180.0


def test_from_cfg_reads_the_real_config():
    c = Config()
    c.set("metaball_max_diameter_px", 64.0)
    c.set("metaball_shrink_s", 0.25)
    mc = MBCfg.from_cfg(c)
    assert mc.max_diameter_px == 64.0
    assert mc.shrink_s == 0.25


def test_from_cfg_falls_back_when_a_key_is_missing():
    """A config saved before these keys existed must still build a renderer."""
    c = Config()
    stripped = {k: v for k, v in c.data.items() if not k.startswith("metaball_")}
    c.data = stripped
    mc = MBCfg.from_cfg(c)
    assert mc.max_diameter_px == 80.0
    assert mc.scale >= 1
