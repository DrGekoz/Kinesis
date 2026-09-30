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
        self._alt_held = False
        self._held_keys: List[str] = []      # press order; released in reverse
        self.last_action = ""

    # ------------------------------------------------------------------ logging
    @property
    def alt_held(self) -> bool:
        return self._alt_held

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

    def _click(self, button: str, warp: Optional[tuple] = None):
        """Click, optionally after parking the pointer somewhere first.

        Gaze-assisted tab switching: the pointer has to be ON the tab for the click to land there, so
        it is moved to the gaze point and given `gaze_click_warp_delay_ms` to settle before the click
        is sent.
        """
        if warp is not None:
            self._set_cursor(warp[0], warp[1])
            delay = max(0.0, float(self.cfg["gaze_click_warp_delay_ms"])) / 1000.0
            if delay:
                time.sleep(delay)
            self._note(f"gaze warp -> {int(warp[0])},{int(warp[1])} ({delay * 1000:.1f} ms before click)")
        self._note(f"mouse click {button}")
        if not self.dry:
            w.mouse_click(button)

    def _double(self, warp: Optional[tuple] = None):
        if warp is not None:
            self._set_cursor(warp[0], warp[1])
            delay = max(0.0, float(self.cfg["gaze_click_warp_delay_ms"])) / 1000.0
            if delay:
                time.sleep(delay)
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
        for k in keys:
            if k == "alt":
                self._alt_held = True
            if k not in self._held_keys:
                self._held_keys.append(k)
        if not self.dry:
            for k in keys:
                w.key_down(k)

    def _keys_up(self, keys: Sequence[str]):
        self._note("key up " + "+".join(keys))
        for k in keys:
            if k == "alt":
                self._alt_held = False
            if k in self._held_keys:
                self._held_keys.remove(k)
        if not self.dry:
            for k in reversed(keys):
                w.key_up(k)

    def _focus_first(self, intent: Intent) -> bool:
        """Keyboard gestures only work on a focused window (a browser will not switch tabs in an
        unfocused one), so focus the target and verify the OS actually agreed before sending."""
        hwnd = intent.focus_hwnd
        if not hwnd or not self._usable(hwnd):
            return False
        if w.user32.GetForegroundWindow() == hwnd:
            return True
        if self.dry:
            self._note(f"would focus hwnd {hwnd}")
            return True
        ok = w.focus_and_verify(hwnd, allow_alt_trick=not self._alt_held)
        self._note(f"focus {'ok' if ok else 'FAILED'} hwnd {hwnd}")
        return ok

    def _keys_tap(self, keys: Sequence[str]):
        self._note("key tap " + "+".join(keys))
        if self.dry:
            return
        for k in keys:
            w.key_down(k)
        for k in reversed(keys):
            w.key_up(k)

    # ------------------------------------------------------------------ windows
    @staticmethod
    def _usable(hwnd: int) -> bool:
        if not hwnd:
            return False
        handle = w.wintypes.HWND(hwnd)
        return bool(w.user32.IsWindow(handle) and w.user32.IsWindowVisible(handle)
                    and not w.user32.IsIconic(handle))

    def resolve_target(self, intent: Intent) -> Optional[WindowInfo]:
        """The window an action applies to: the gaze-resolved window first (that is the point of
        the eye tracking), then the monitor the hand was pointing at, then whatever has focus."""
        if intent.target_hwnd and self._usable(intent.target_hwnd):
            info = w.window_info(intent.target_hwnd, self.monitors)
            if info is not None and info.process_id not in self._skip_pids:
                return info
        if intent.monitor is not None:
            hwnd = w.topmost_window_on_monitor(intent.monitor, self.monitors, self._skip_pids,
                                               self.cfg.get("window_title_blocklist") or ())
            if hwnd:
                info = w.window_info(hwnd, self.monitors)
                if info is not None:
                    return info
        hwnd = w.user32.GetForegroundWindow()
        if not hwnd:
            return None
        info = w.window_info(hwnd, self.monitors)
        if info and info.process_id in self._skip_pids:
            return None
        return info

    def target_window(self, monitor_index: Optional[int]) -> Optional[WindowInfo]:
        """Kept for callers that only have a monitor index."""
        return self.resolve_target(Intent("window.target", monitor=monitor_index))

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

    def minimise_aimed(self, intent: Intent):
        info = self.resolve_target(intent)
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

    def maximise_aimed(self, intent: Intent):
        info = self.resolve_target(intent)
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
                self._click(intent.button or "left", intent.warp)
            elif kind == "mouse.double":
                self._double(intent.warp)
            elif kind == "mouse.down":
                self._down(intent.button or "left")
            elif kind == "mouse.up":
                self._up(intent.button or "left")
            elif kind == "mouse.wheel":
                self._wheel(intent.amount)
            elif kind == "keys.down":
                self._focus_first(intent)
                self._keys_down(intent.keys)
            elif kind == "keys.up":
                self._keys_up(intent.keys)
            elif kind == "keys.tap":
                self._focus_first(intent)
                self._keys_tap(intent.keys)
            elif kind == "window.minimise":
                self.minimise_aimed(intent)
            elif kind == "window.maximise":
                self.maximise_aimed(intent)
            elif kind == "cursor.warp":
                self._set_cursor(intent.x, intent.y)
            else:
                self._note(f"ignored intent {kind}")

    def release_all(self):
        """Safety: nothing may stay held down when the program stops.

        Releases whatever was actually pressed rather than a hardcoded list - if the dictation
        hotkey is remapped (`ptt_keys`), a guess here would leave the real keys stuck down.
        """
        if self._held_keys:
            # passed in press order - _keys_up reverses, so the key pressed last is released first
            self._keys_up(tuple(self._held_keys))
        self._held_keys.clear()
        self._alt_held = False
        self._up("left")
        self._up("right")
        self._up("middle")
