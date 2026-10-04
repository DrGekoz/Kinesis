"""Gaze overlay on the real desktop: transparent, click-through, always on top.

Windows composites per-pixel alpha for us (WS_EX_LAYERED + UpdateLayeredWindow with premultiplied
BGRA), and WS_EX_TRANSPARENT lets every click fall through - so this is a real overlay, not a window
that steals the mouse.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

import cv2
import numpy as np

from . import winapi as w
from .gazevis import THEMES, GazeVisualizer
from .metaball import Config as MetaballConfig, MetaballField, MetaballState

METABALL = "metaball"


class _Panel:
    """One monitor's window, DIB and renderer.

    Two renderers, chosen by style. The metaball is NOT a GazeVisualizer style because it needs a
    shared, monitor-independent state (see MetaballState) and because it writes WHITE intensity
    rather than a theme colour - both of which the accumulation-buffer path cannot express.
    """

    def __init__(self, cfg, monitor, scale: int, style: str, theme: str,
                 metaball_state: Optional[MetaballState] = None):
        self.monitor = monitor
        self.is_metaball = str(style).lower() == METABALL
        small = (max(8, monitor.width // scale), max(8, monitor.height // scale))
        mon = monitor

        def to_canvas(x, y, cw, ch, mon=mon):
            fx = (x - mon.left) / max(mon.width - 1, 1)
            fy = (y - mon.top) / max(mon.height - 1, 1)
            return fx * (cw - 1), fy * (ch - 1)

        sub = cfg.copy() if hasattr(cfg, "copy") else cfg
        if self.is_metaball:
            self.vis = None
            self.blob = MetaballField(MetaballConfig.from_cfg(cfg), metaball_state,
                                      monitor.width, monitor.height,
                                      origin=(monitor.left, monitor.top))
            # the metaball composites at FULL panel resolution, so its canvas is full size
            self.canvas = np.zeros((monitor.height, monitor.width, 3), np.uint8)
        else:
            self.blob = None
            vis = GazeVisualizer(sub, small[0], small[1], to_canvas=to_canvas,
                                 style=style, theme=theme, scale=scale)
            self.vis = vis
            self.canvas = np.zeros((small[1], small[0], 3), np.uint8)
        self.hwnd = w.create_overlay_window(monitor.left, monitor.top, monitor.width,
                                            monitor.height)
        self.hdc, self.bitmap, self.view = w.dib_buffer(monitor.width, monitor.height)
        # the rectangle cleared on the NEXT frame, so a shrinking trail does not leave ghosts
        self._cleared: Optional[tuple] = None

    def render(self, gaze, gain: float, now: Optional[float] = None) -> None:
        # The metaball only redraws its DIRTY RECT, so the panel has to clear the previous frame's
        # rect rather than the whole canvas - clearing 2 M pixels every frame to erase a 220x220 blob
        # is most of the cost this change exists to remove. The trail shrinks in place, so the old
        # rect has to go even when the new one is smaller.
        if self.is_metaball:
            if self._cleared is None:
                self.canvas[:] = 0
            else:
                px, py, pw, ph = self._cleared
                self.canvas[py:py + ph, px:px + pw] = 0
            self._cleared = None
        else:
            self.canvas[:] = 0

        inside = (gaze is not None and getattr(gaze, "valid", False)
                  and self.monitor.contains(int(gaze.x), int(gaze.y)))
        if self.is_metaball:
            # the shared state was already advanced once for this tick by DesktopOverlay._loop;
            # this panel only stamps its own slice of it
            stamp_now = now if now is not None else time.perf_counter()
            self.blob.stamp_state(stamp_now)
            self.blob.draw(self.canvas)
            self._cleared = self.blob._last_rect
        else:
            self.vis.advance(gaze, present=inside)
            self.vis.draw(self.canvas)

        bgra = np.empty((self.canvas.shape[0], self.canvas.shape[1], 4), np.uint8)
        if self.is_metaball:
            # TWO THINGS MAKE THIS PATH CHEAP, AND BOTH ARE MEASURED.
            #
            # 1. It never reads `canvas.max(axis=2)`. That reduction measured 60 ms on a 1080p panel
            #    - a numpy reduce across three channels - on a canvas that is white in R, G and B by
            #    construction. One channel IS the alpha.
            # 2. It writes only the dirty rect, through `cvtColor` into a VIEW of the DIB. Writing the
            #    four planes of a full 1080p BGRA buffer measured 7.5 ms; the same write confined to
            #    the rect is 0.19 ms, and `cvtColor` does it in 0.04 ms.
            #
            # The rest of the panel is cleared to zero on the NEXT frame, so the premultiplied
            # channels stay zero - which is exactly right, because premultiplied colour == alpha and
            # alpha is 0 there.
            rect = self._cleared
            if rect is not None:
                px, py, pw, ph = rect
                pw = max(1, min(pw, self.canvas.shape[1] - px))
                ph = max(1, min(ph, self.canvas.shape[0] - py))
                view = bgra[py:py + ph, px:px + pw]
                a = cv2.convertScaleAbs(self.canvas[py:py + ph, px:px + pw, 0],
                                        alpha=max(0.0, float(gain)))
                cv2.cvtColor(a, cv2.COLOR_GRAY2BGRA, dst=view)
        else:
            # Every other style writes a THEME COLOUR into the canvas, so the alpha really does have
            # to be the max across channels and the colour really does have to be premultiplied.
            alpha = self.canvas.max(axis=2)
            a = np.clip(alpha.astype(np.float32) * gain, 0, 255).astype(np.uint8)
            for i in range(3):                  # premultiply: Windows expects colour * alpha
                bgra[..., i] = (self.canvas[..., i].astype(np.uint16) * a) // 255
            bgra[..., 3] = a

        if self.is_metaball:
            # UpdateLayeredWindow needs the WHOLE buffer every frame, so a stale pixel outside the
            # rect would persist on screen. Zero the rest of it - a memset, not a per-plane copy.
            if self._cleared is not None:
                px, py, pw, ph = self._cleared
                pw = max(1, min(pw, self.canvas.shape[1] - px))
                ph = max(1, min(ph, self.canvas.shape[0] - py))
                if px > 0:
                    bgra[:, :px] = 0
                if px + pw < bgra.shape[1]:
                    bgra[:, px + pw:] = 0
                if py > 0:
                    bgra[:py] = 0
                if py + ph < bgra.shape[0]:
                    bgra[py + ph:] = 0

        if bgra.shape[:2] != (self.monitor.height, self.monitor.width):
            cv2.resize(bgra, (self.monitor.width, self.monitor.height), dst=self.view,
                       interpolation=cv2.INTER_LINEAR)
        else:
            self.view[:] = bgra

    def blit(self) -> bool:
        return w.blit_layered(self.hwnd, self.hdc, self.monitor.width, self.monitor.height)

    def show(self, visible: bool) -> None:
        w.user32.ShowWindow(self.hwnd, w.SW_SHOW if visible else w.SW_HIDE)

    def destroy(self) -> None:
        w.destroy_overlay_window(self.hwnd, self.hdc, self.bitmap)


class DesktopOverlay:
    """Draws the gaze layer on the desktop itself: one click-through window per monitor."""

    def __init__(self, cfg, monitors: List[w.Monitor], gaze_getter: Callable):
        self.cfg = cfg
        self.monitors = monitors
        self.gaze_getter = gaze_getter
        self.enabled = bool(cfg["desktop_overlay"])
        self.style = str(cfg["desktop_overlay_style"] or cfg["vcam_style"]).lower()
        self.is_metaball = self.style == METABALL
        # The metaball animates to new gaze positions and has to stay smooth while doing it, so it
        # runs on its own fps cap (180) rather than the overlay's default 30. Round-robin panel
        # updates are also disabled for it: at 180 fps a 3-panel round robin would leave each screen
        # at 60 fps, and the trail is shared state that would visibly tear between panels.
        self.fps = float(cfg["metaball_max_fps"]) if self.is_metaball \
            else float(cfg["desktop_overlay_fps"])
        self.gain = float(cfg["desktop_overlay_alpha_gain"])
        self.theme = str(cfg["desktop_overlay_theme"] or cfg["vcam_theme"])
        self.scale = max(1, int(cfg["desktop_overlay_scale"]))
        self.per_tick = max(1, int(cfg["desktop_overlay_panels_per_tick"]))
        self.visible = True
        self.error: Optional[str] = None
        self.frames = 0
        self.tick_ms = 0.0
        self._panels: List[_Panel] = []
        self._thread: Optional[threading.Thread] = None
        self._stop = False
        self._hotkey_down = False
        self._metaball: Optional[MetaballState] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if not self.enabled:
            return False
        try:
            if self.is_metaball:
                self._metaball = MetaballState(MetaballConfig.from_cfg(self.cfg))
            for mon in self.monitors:
                self._panels.append(_Panel(self.cfg, mon, self.scale, self.style, self.theme,
                                           metaball_state=self._metaball))
        except Exception as exc:
            self.error = f"could not create overlay windows ({exc})"
            self.stop()
            return False
        self._stop = False
        self._thread = threading.Thread(target=self._loop, name="kinesis-overlay", daemon=True)
        self._thread.start()
        return True

    def _loop(self) -> None:
        period = 1.0 / max(1.0, self.fps)
        cursor = 0
        while not self._stop:
            t0 = time.perf_counter()
            self._check_hotkey()
            if self.visible and self._panels:
                gaze = self.gaze_getter()
                now = time.perf_counter()
                if self.is_metaball:
                    # ONE advance for the whole tick, then every panel stamps the same state. If
                    # the state were advanced per panel the head would move n times per tick and a
                    # multi-monitor setup would show the blob at a different place on each screen.
                    if self._metaball is not None:
                        self._metaball.advance(gaze, present=True, now=now)
                    for panel in self._panels:
                        try:
                            panel.render(gaze, self.gain, now=now)
                            panel.blit()
                        except Exception as exc:           # pragma: no cover
                            self.error = f"metaball render failed ({exc})"
                    self.frames += 1
                elif gaze is not None:
                    n = len(self._panels)
                    # a few panels per tick: the trail decays on the others anyway, and this keeps
                    # the whole overlay inside a fraction of a frame
                    count = min(self.per_tick, n)
                    for k in range(count):
                        panel = self._panels[(cursor + k) % n]
                        try:
                            panel.render(gaze, self.gain)
                            panel.blit()
                        except Exception as exc:           # pragma: no cover
                            self.error = f"overlay render failed ({exc})"
                    cursor = (cursor + count) % n
                    self.frames += 1
            self.tick_ms = (time.perf_counter() - t0) * 1000.0
            slack = period - (time.perf_counter() - t0)
            if slack > 0:
                time.sleep(slack)

    def _check_hotkey(self) -> None:
        names = {"insert": w.VK_INSERT, "delete": 0x2E, "home": 0x24, "end": 0x23}
        vk = names.get(str(self.cfg["desktop_overlay_hotkey"]).lower())
        if vk is None:
            return
        down = bool(w.user32.GetAsyncKeyState(vk) & 0x8000)
        if down and not self._hotkey_down:
            self.toggle()
        self._hotkey_down = down

    def toggle(self) -> bool:
        self.visible = not self.visible
        for panel in self._panels:
            try:
                panel.show(self.visible)
            except Exception:                              # pragma: no cover
                pass
        return self.visible

    def stop(self) -> None:
        self._stop = True
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        for panel in self._panels:
            panel.destroy()
        self._panels = []

    def status_line(self) -> str:
        if not self.enabled:
            return "overlay: off"
        if self.error:
            return f"overlay: {self.error}"
        if not self.running:
            return "overlay: not running"
        if self.is_metaball and self._metaball is not None:
            now = self._metaball._last_t or 0.0
            head = self._metaball.head_radius(now) * 2.0
            return (f"overlay: metaball {head:.0f}px head, {len(self._metaball.trail)} trail, "
                    f"{self.fps:.0f}fps {self.tick_ms:.1f}ms "
                    f"{'visible' if self.visible else 'hidden'} ({self.frames} frames)")
        return (f"overlay: {self.style}/{self.theme} {self.fps:.0f}fps {self.tick_ms:.0f}ms "
                f"{'visible' if self.visible else 'hidden'} ({self.frames} frames)")

