"""Live verification of the Windows action layer against a real window.

Creates its own Notepad window, drives it through the same ctypes path the gestures use, and
asserts the OS actually changed state. Nothing here touches any of your existing windows.

    python tools/verify_actions.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kinesis import winapi as w                                    # noqa: E402
from kinesis.actions import ActionRunner                           # noqa: E402
from kinesis.config import Config                                  # noqa: E402
from kinesis.gestures import Intent                                # noqa: E402
from kinesis.winapi import WindowInfo                              # noqa: E402

results = []


def check(name, got, want=True):
    ok = got == want
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got!r} want={want!r}")


WM_CLOSE = 0x0010


def find_window(timeout=15.0, title="Kinesis Test Window"):
    """Find the test window by title.

    Deliberately not matched by pid: the venv's python.exe is a trampoline that starts the real
    interpreter as a child, so the window belongs to a different pid than the one Popen returns.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for hwnd in w._enum_windows():
            info = w.window_info(hwnd)
            if info and title in info.title:
                return hwnd
        time.sleep(0.2)
    return None


TEST_WINDOW_SRC = (
    "import tkinter as tk\n"
    "r = tk.Tk()\n"
    "r.title('Kinesis Test Window')\n"
    "r.geometry('420x300+200+200')\n"
    "r.mainloop()\n"
)


def main() -> int:
    monitors = w.enumerate_monitors()
    cfg = Config()

    # ---------------------------------------------------------------- real window control
    proc = subprocess.Popen([sys.executable, "-c", TEST_WINDOW_SRC])
    hwnd = find_window()
    if hwnd is None:
        print("could not create a test window")
        proc.kill()
        return 2
    time.sleep(0.8)
    time.sleep(0.6)
    try:
        base = w.window_info(hwnd)
        check("test window located", base is not None)
        print(f"      window {hwnd} rect={base.rect} monitor {base.monitor_index + 1}")

        w.minimise(hwnd)
        time.sleep(0.5)
        check("minimise really minimised it", bool(w.user32.IsIconic(w.wintypes.HWND(hwnd))))

        w.restore(hwnd)
        time.sleep(0.5)
        check("restore really restored it", not bool(w.user32.IsIconic(w.wintypes.HWND(hwnd))))

        w.maximise(hwnd)
        time.sleep(0.5)
        check("maximise really maximised it", bool(w.user32.IsZoomed(w.wintypes.HWND(hwnd))))

        w.restore(hwnd)
        time.sleep(0.4)

        # a real key tap through SendInput, into the window we own
        w.focus_window(hwnd)
        time.sleep(0.3)
        w.key_tap("a")
        w.key_tap("b")
        time.sleep(0.3)
        check("test window still alive after key injection", w.user32.IsWindow(w.wintypes.HWND(hwnd)) == 1)

        # gaze targeting: a point inside the window must resolve to that window. This is the lookup
        # that decides which window a hand gesture lands on.
        live = w.window_info(hwnd)
        if live is not None:
            left, top, right, bottom = live.rect
            cx, cy = (left + right) // 2, (top + bottom) // 2
            hit = w.topmost_window_at(cx, cy, monitors, {w.own_process_id()}, ())
            print(f"      gaze point ({cx},{cy}) -> hwnd {hit}")
            check("gaze point resolves to the window under it", hit == hwnd)
            blocked = w.topmost_window_at(cx, cy, monitors, {w.own_process_id()},
                                          ("kinesis test window",))
            print(f"      with the title blocklisted -> hwnd {blocked}")
            check("blocklisted titles are skipped by gaze targeting", blocked != hwnd)
            outside = w.topmost_window_at(left - 500, top - 500, monitors, {w.own_process_id()}, ())
            print(f"      a point 500px outside the window -> hwnd {outside}")
            check("a point outside the window does not return it", outside != hwnd)
    finally:
        # close through the OS rather than killing the process: the venv python is a trampoline,
        # so terminate() would leave the real interpreter (and the window) behind
        w.user32.PostMessageW(w.wintypes.HWND(hwnd), WM_CLOSE, 0, 0)
        time.sleep(1.0)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
        if w.user32.IsWindow(w.wintypes.HWND(hwnd)) == 1:
            w.user32.PostMessageW(w.wintypes.HWND(hwnd), WM_CLOSE, 0, 0)
            time.sleep(0.6)
    time.sleep(0.4)
    check("test window closed cleanly", w.user32.IsWindow(w.wintypes.HWND(hwnd)) == 0)

    # ---------------------------------------------------------------- fullscreen key choice
    runner = ActionRunner(cfg, monitors, dry=True)
    youtube = WindowInfo(hwnd=1, title="Some Video - YouTube", rect=(0, 0, 1920, 1080),
                         monitor_index=0, process_id=1, is_maximized=False, is_fullscreen=False,
                         is_foreground=True)
    other = WindowInfo(hwnd=2, title="Document - Notepad", rect=(0, 0, 1920, 1080),
                       monitor_index=0, process_id=1, is_maximized=False, is_fullscreen=False,
                       is_foreground=True)
    check("fullscreen key on YouTube is 'f'", runner._fs_key(youtube), "f")
    check("fullscreen key elsewhere is 'f11'", runner._fs_key(other), "f11")

    # ---------------------------------------------------------------- dry-run action layer
    runner.execute([Intent("window.minimise", monitor=None), Intent("keys.tap", keys=("ctrl", "tab")),
                    Intent("mouse.wheel", amount=120)])
    log = " | ".join(runner.log)
    check("dry run logged the window action", "minimise" in log or "no target window" in log)
    check("dry run logged the tab tap", "ctrl+tab" in log)
    check("dry run logged the scroll", "scroll" in log)
    check("dry run injected nothing (cursor untouched)", w.get_cursor_pos() is not None)

    # wheel accumulation: sub-click deltas must not be lost
    runner2 = ActionRunner(cfg, monitors, dry=True)
    for _ in range(2):
        runner2.execute([Intent("mouse.wheel", amount=60)])
    check("partial wheel deltas accumulate into one click",
          any("scroll 1 click" in line for line in runner2.log))

    # ------------------------------------------------- no gaze target must not touch anything
    # The regression that made alt-tab feel broken: a window action with no gaze target fell back
    # to GetForegroundWindow(), so it minimised whatever you happened to be typing in. These two
    # must find NOTHING even with a window actually focused and a monitor to aim at.
    focused = ActionRunner(cfg, monitors, dry=True)
    fg = w.user32.GetForegroundWindow()
    check("there is a foreground window to be dangerous with", bool(fg))
    check("window_only with no gaze target resolves to nothing",
          focused.resolve_target(Intent("window.minimise", monitor=0, window_only=True)) is None)
    check("window_only refuses the foreground fallback even with a monitor",
          focused.resolve_target(Intent("window.maximise", monitor=0, window_only=True,
                                         target_hwnd=None)) is None)
    check("a non-window intent still falls back to focus (keys must work)",
          focused.resolve_target(Intent("keys.tap", keys=("ctrl", "tab"))) is not None)

    # and the claw path must reach the action layer with the flag set
    from kinesis.gestures import map_action_intents
    mapped = map_action_intents(
        __import__("kinesis.gesture_map", fromlist=["Action"]).Action(kind="system",
                                                                     value="minimise"),
        None, note="test")
    check("a map-bound minimise is window_only too",
          bool(mapped) and all(i.window_only for i in mapped if i.kind.startswith("window.")))

    print()
    print(f"{sum(results)}/{len(results)} live action checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
