"""The desktop overlay's metaball wiring, with the Win32 layer faked out.

`DesktopOverlay.start()` creates real topmost layered windows and DIBs, which cannot run in a test.
So the loop is driven directly with stub panels, and what is being pinned is the ORCHESTRATION -
one shared advance per tick, every panel rendered per tick - not the pixels.

Note on driving the loop: `_loop()` is `while not self._stop`, so a test that sets `_stop = True`
before calling it exercises nothing at all. Each test here enters the loop, and the stubs set the
flag once the assertion point has been reached.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kinesis.desktop_overlay as do                                   # noqa: E402
from kinesis.config import Config                                     # noqa: E402
from kinesis.metaball import Config as MBCfg, MetaballState           # noqa: E402


def _monitor(index, left, top=0, width=1920, height=1080):
    return SimpleNamespace(index=index, left=left, top=top, width=width, height=height,
                           device=f"\\\\.\\DISPLAY{index}",
                           contains=lambda x, y: (left <= x < left + width
                                                  and top <= y < top + height))


def _cfg(**kw):
    c = Config()
    c.set("desktop_overlay", True)
    c.set("desktop_overlay_style", "metaball")
    for k, v in kw.items():
        c.set(k, v)
    return c


def _gaze(x, y):
    return SimpleNamespace(x=x, y=y, valid=True, blink=False, rejected=False, age=0.0)


def _overlay(cfg, monitors, gaze):
    o = do.DesktopOverlay(cfg, monitors, lambda: gaze)
    o._metaball = MetaballState(MBCfg.from_cfg(cfg))
    return o


def _stub_panels(overlay, monitors):
    """Panels with everything real except the window, the DIB and the blit."""
    panels = []
    for mon in monitors:
        panel = object.__new__(do._Panel)
        panel.monitor = mon
        panel.is_metaball = True
        panel.vis = None
        panel.blob = None
        panel.canvas = np.zeros((4, 4, 3), np.uint8)
        panel._cleared = None
        panel.rendered = []
        panel.blit = lambda: True
        panels.append(panel)
    overlay._panels = panels
    return panels


def test_the_metaball_style_raises_the_fps_cap_to_180():
    """The blob animates to new positions; at the 30 fps default it would visibly step."""
    cfg = _cfg(metaball_max_fps=180.0)
    o = _overlay(cfg, [_monitor(0, 0)], _gaze(100, 100))
    assert o.is_metaball is True
    assert o.fps == 180.0


def test_a_non_metaball_style_keeps_the_normal_fps():
    cfg = _cfg(desktop_overlay_style="comet")
    o = _overlay(cfg, [_monitor(0, 0)], _gaze(100, 100))
    assert o.is_metaball is False
    assert o.fps == float(cfg["desktop_overlay_fps"])


def test_the_loop_advances_the_metaball_state_exactly_once_per_tick():
    """The regression this pins: advancing per PANEL would move the head n times per frame, so with
    three monitors the blob would sit in a different place on each screen."""
    monitors = [_monitor(0, 0), _monitor(1, 1920), _monitor(2, 3840)]
    o = _overlay(_cfg(), monitors, _gaze(100, 100))
    panels = _stub_panels(o, monitors)

    calls = {"advance": 0}
    real_advance = o._metaball.advance
    seen = {"n": 0}

    def counting_advance(*a, **k):
        calls["advance"] += 1
        return real_advance(*a, **k)

    o._metaball.advance = counting_advance

    def render(*a, **k):
        panels[seen["n"]].rendered.append(seen["n"])
        seen["n"] += 1
        if seen["n"] >= len(panels):
            o._stop = True                    # exactly one tick

    for panel in panels:
        panel.render = render

    o._check_hotkey = lambda: None
    o._stop = False
    o._loop()

    assert calls["advance"] == 1, (
        f"the shared state advanced {calls['advance']} times for {len(panels)} panels in one tick"
    )


def test_every_panel_is_rendered_per_tick_for_the_metaball():
    """Round-robin panel updates are disabled for the metaball: at 180 fps a 3-way round robin would
    leave each screen at 60 fps, and the shared trail would visibly tear between panels."""
    cfg = _cfg(desktop_overlay_panels_per_tick=2)
    monitors = [_monitor(0, 0), _monitor(1, 1920), _monitor(2, 3840)]
    o = _overlay(cfg, monitors, _gaze(100, 100))
    panels = _stub_panels(o, monitors)

    seen = {"n": 0}

    def render(*a, **k):
        panels[seen["n"]].rendered.append(seen["n"])
        seen["n"] += 1
        if seen["n"] >= len(panels):
            o._stop = True

    for panel in panels:
        panel.render = render

    o._check_hotkey = lambda: None
    o._stop = False
    o._loop()

    rendered = sorted(n for p in panels for n in p.rendered)
    assert rendered == [0, 1, 2], (
        f"only panels {rendered} were rendered - the metaball must not round-robin"
    )


def test_a_gaze_that_is_blinking_does_not_drive_the_metaball():
    """A blink is not a measurement; the blob must not chase it."""
    o = _overlay(_cfg(), [_monitor(0, 0)], _gaze(100, 100))
    state = o._metaball
    state.advance(_gaze(500, 500), now=100.0)
    before = state.head[0]
    blinked = SimpleNamespace(x=900, y=900, valid=True, blink=True, rejected=False, age=0.0)
    state.advance(blinked, now=100.1)
    assert state.head[0] < 900.0, "the blob followed a blink all the way to it"
    assert state.head[0] <= before + 1.0, (
        f"the blob moved on a blink: {before} -> {state.head[0]}"
    )


def test_a_rejected_sample_does_not_drive_the_metaball():
    o = _overlay(_cfg(), [_monitor(0, 0)], _gaze(100, 100))
    state = o._metaball
    state.advance(_gaze(500, 500), now=100.0)
    before = state.head[0]
    rejected = SimpleNamespace(x=900, y=900, valid=True, blink=False, rejected=True, age=0.0)
    state.advance(rejected, now=100.1)
    assert state.head[0] < 900.0, "the blob followed a discarded sample all the way to it"
    assert state.head[0] <= before + 1.0, (
        f"the blob moved on a discarded sample: {before} -> {state.head[0]}"
    )


def test_the_metaball_style_is_not_a_second_overlay_path():
    """--metaball must set the EXISTING desktop_overlay switch, not add a new one, so a user who
    turned the overlay off does not get it back from the metaball."""
    cfg = _cfg(desktop_overlay=False, desktop_overlay_style="metaball")
    o = do.DesktopOverlay(cfg, [_monitor(0, 0)], lambda: None)
    assert o.enabled is False
    assert o.start() is False
