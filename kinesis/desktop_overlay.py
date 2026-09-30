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


class _Panel:
    """One monitor's window, DIB and renderer."""

    def __init__(self, cfg, monitor, scale: int, style: str, theme: str):
        self.monitor = monitor
        small = (max(8, monitor.width // scale), max(8, monitor.height // scale))
        mon = monitor

        def to_canvas(x, y, cw, ch, mon=mon):
            fx = (x - mon.left) / max(mon.width - 1, 1)
            fy = (y - mon.top) / max(mon.height - 1, 1)
            return fx * (cw - 1), fy * (ch - 1)

        sub = cfg.copy() if hasattr(cfg, "copy") else cfg
        vis = GazeVisualizer(sub, small[0], small[1], to_canvas=to_canvas,
                             style=style, theme=theme, scale=scale)
        self.vis = vis
        self.canvas = np.zeros((small[1], small[0], 3), np.uint8)
        self.hwnd = w.create_overlay_window(monitor.left, monitor.top, monitor.width,
                                            monitor.height)
        self.hdc, self.bitmap, self.view = w.dib_buffer(monitor.width, monitor.height)

    def render(self, gaze, gain: float) -> None:
        self.canvas[:] = 0
        inside = (gaze is not None and getattr(gaze, "valid", False)
                  and self.monitor.contains(int(gaze.x), int(gaze.y)))
        self.vis.advance(gaze, present=inside)
        self.vis.draw(self.canvas)
        alpha = self.canvas.max(axis=2)
        a = np.clip(alpha.astype(np.float32) * gain, 0, 255).astype(np.uint8)
        bgra = np.empty((self.canvas.shape[0], self.canvas.shape[1], 4), np.uint8)
        for i in range(3):                      # premultiply: Windows expects colour * alpha
            bgra[..., i] = (self.canvas[..., i].astype(np.uint16) * a) // 255
        bgra[..., 3] = a
        cv2.resize(bgra, (self.monitor.width, self.monitor.height), dst=self.view,
                   interpolation=cv2.INTER_LINEAR)

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
        self.fps = float(cfg["desktop_overlay_fps"])
        self.gain = float(cfg["desktop_overlay_alpha_gain"])
        self.style = str(cfg["desktop_overlay_style"] or cfg["vcam_style"])
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

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if not self.enabled:
            return False
        try:
            for mon in self.monitors:
                self._panels.append(_Panel(self.cfg, mon, self.scale, self.style, self.theme))
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
                if gaze is not None:
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
        return (f"overlay: {self.style}/{self.theme} {self.fps:.0f}fps {self.tick_ms:.0f}ms "
                f"{'visible' if self.visible else 'hidden'} ({self.frames} frames)")

