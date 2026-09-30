"""Turns gesture intents into real Windows actions.

Window actions are aimed: the gesture carries the monitor the hand was pointing at, and the
topmost normal window on that monitor is the target (falling back to the foreground window when
aim is unknown). Anything that needs the window to react first - exiting fullscreen before
minimising - goes through a small deferred queue so the tracking loop never blocks.
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional, Sequence, Tuple

from . import winapi as w
from .gestures import Intent
from .winapi import Monitor, WindowInfo

FULLSCREEN_EXIT_DELAY = 0.45


class ActionRunner:
    def __init__(self, cfg, monitors: Sequence[Monitor], dry: bool = False):
        self.cfg = cfg
        self.monitors = list(monitors)
        self.dry = bool(dry)
        self.log: List[str] = []
        self._deferred: List[Tuple[float, str, Callable[[], None]]] = []
        self._skip_pids = {w.own_process_id()}
        self._wheel_residual = 0
        self.last_action = ""

    # ------------------------------------------------------------------ logging
    def _note(self, text: str):
        self.last_action = text
        self.log.append(f"{time.strftime('%H:%M:%S')} {text}")
        if len(self.log) > 200:
            self.log.pop(0)

    # ------------------------------------------------------------------ primitives
    def _set_cursor(self, x: float, y: float):
        if self.dry:
            return
        w.set_cursor_pos(int(round(x)), int(round(y)))

    def _click(self, button: str):
        self._note(f"mouse click {button}")
        if not self.dry:
            w.mouse_click(button)

    def _double(self):
        self._note("mouse double click")
        if not self.dry:
            w.mouse_click("left")
            w.mouse_click("left")

    def _down(self, button: str):
        self._note(f"mouse down {button}")
        if not self.dry:
            w.mouse_down(button)

    def _up(self, button: str):
        self._note(f"mouse up {button}")
        if not self.dry:
            w.mouse_up(button)

    def _wheel(self, units: int):
        """Accumulate wheel units and only send whole clicks, carrying the remainder, so slow
        scrolls stay smooth instead of being rounded to zero."""
        self._wheel_residual += int(units)
        clicks = int(self._wheel_residual / w.WHEEL_DELTA)
        if not clicks:
            return
        self._wheel_residual -= clicks * w.WHEEL_DELTA
        self._note(f"scroll {clicks} click(s)")
        if not self.dry:
            w.scroll_wheel(clicks)

    def _keys_down(self, keys: Sequence[str]):
        self._note("key down " + "+".join(keys))
        if not self.dry:
            for k in keys:
                w.key_down(k)

    def _keys_up(self, keys: Sequence[str]):
        self._note("key up " + "+".join(keys))
        if not self.dry:
            for k in reversed(keys):
                w.key_up(k)

    def _keys_tap(self, keys: Sequence[str]):
        self._note("key tap " + "+".join(keys))
        if self.dry:
            return
        for k in keys:
            w.key_down(k)
        for k in reversed(keys):
            w.key_up(k)

    # ------------------------------------------------------------------ windows
    def target_window(self, monitor_index: Optional[int]) -> Optional[WindowInfo]:
        hwnd = None
        if monitor_index is not None:
            hwnd = w.topmost_window_on_monitor(monitor_index, self.monitors, self._skip_pids,
                                               self.cfg.get("window_title_blocklist") or ())
        if hwnd is None:
            hwnd = w.user32.GetForegroundWindow()
        if not hwnd:
            return None
        info = w.window_info(hwnd, self.monitors)
        if info and info.process_id in self._skip_pids:
            hwnd = w.user32.GetForegroundWindow()
            info = w.window_info(hwnd, self.monitors)
        return info

    def _fs_key(self, info: Optional[WindowInfo]) -> str:
        hint = str(self.cfg["youtube_title_hint"]).lower()
        if info and hint and hint in info.title.lower():
            return str(self.cfg["youtube_fs_key"])
        return str(self.cfg["generic_fs_key"])

    def _toggle_fullscreen(self, info: Optional[WindowInfo], what: str):
        key = self._fs_key(info)
        title = (info.title[:40] if info else "foreground window")
        self._note(f"{what}: send {key.upper()} to {title!r}")
        self._keys_tap((key,))

    def minimise_aimed(self, monitor_index: Optional[int]):
        info = self.target_window(monitor_index)
        if info is None:
            self._note("minimise: no target window")
            return
        if info.is_fullscreen:
            # exit fullscreen first, then minimise - otherwise Windows minimises the fullscreen
            # surface and the app is left in a weird state
            self._toggle_fullscreen(info, "exit fullscreen")
            hwnd = info.hwnd
            self._defer(FULLSCREEN_EXIT_DELAY, f"minimise {info.title[:30]!r}",
                        lambda: None if self.dry else w.minimise(hwnd))
        else:
            self._note(f"minimise {info.title[:40]!r}")
            if not self.dry:
                w.minimise(info.hwnd)

    def maximise_aimed(self, monitor_index: Optional[int]):
        info = self.target_window(monitor_index)
        if info is None:
            self._note("maximise: no target window")
            return
        if info.is_maximized or info.is_fullscreen:
            self._toggle_fullscreen(info, "already maximised -> fullscreen")
            return
        self._note(f"maximise {info.title[:40]!r}")
        if not self.dry:
            w.focus_window(info.hwnd)
            w.maximise(info.hwnd)

    # ------------------------------------------------------------------ queue
    def _defer(self, delay: float, label: str, fn: Callable[[], None]):
        self._deferred.append((time.perf_counter() + delay, label, fn))

    def tick(self, now: Optional[float] = None):
        now = now if now is not None else time.perf_counter()
        due = [item for item in self._deferred if item[0] <= now]
        self._deferred = [item for item in self._deferred if item[0] > now]
        for _, label, fn in due:
            self._note(label)
            try:
                fn()
            except Exception as exc:              # pragma: no cover - defensive
                self._note(f"deferred action failed: {exc}")

    # ------------------------------------------------------------------ main
    def execute(self, intents: Sequence[Intent]):
        for intent in intents:
            kind = intent.kind
            if kind == "cursor.move":
                self._set_cursor(intent.x, intent.y)
            elif kind == "mouse.click":
                self._click(intent.button or "left")
            elif kind == "mouse.double":
                self._double()
            elif kind == "mouse.down":
                self._down(intent.button or "left")
            elif kind == "mouse.up":
                self._up(intent.button or "left")
            elif kind == "mouse.wheel":
                self._wheel(intent.amount)
            elif kind == "keys.down":
                self._keys_down(intent.keys)
            elif kind == "keys.up":
                self._keys_up(intent.keys)
            elif kind == "keys.tap":
                self._keys_tap(intent.keys)
            elif kind == "window.minimise":
                self.minimise_aimed(intent.monitor)
            elif kind == "window.maximise":
                self.maximise_aimed(intent.monitor)
            else:
                self._note(f"ignored intent {kind}")

    def release_all(self):
        """Safety: nothing may stay held down when the program stops."""
        self._keys_up(("alt",))
        self._keys_up(("ctrl", "space"))
        self._up("left")
        self._up("right")
        self._up("middle")
