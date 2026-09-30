"""Diagnostic: what does window enumeration see per monitor?"""
import sys
sys.path.insert(0, "F:/aaaaaVIBECODING/Kinesis")

from kinesis import winapi as w

monitors = w.enumerate_monitors()
print(f"{len(monitors)} monitors")
for i, m in enumerate(monitors):
    print(f"  {i+1}. {m}")

print("\nall visible titled top-level windows:")
ENUM = w.ctypes.WINFUNCTYPE(w.wintypes.BOOL, w.wintypes.HWND, w.wintypes.LPARAM)


def cb(hwnd, lparam):
    h = int(hwnd)
    if not w.user32.IsWindowVisible(w.wintypes.HWND(h)):
        return True
    if w.user32.GetWindowTextLengthW(w.wintypes.HWND(h)) == 0:
        return True
    info = w.window_info(h, monitors)
    if info is None:
        return True
    print(f"  hwnd={h:<10} mon={info.monitor_index + 1} pid={info.process_id:<8} "
          f"max={int(info.is_maximized)} fs={int(info.is_fullscreen)} "
          f"iconic={int(bool(w.user32.IsIconic(w.wintypes.HWND(h))))} "
          f"rect={info.rect} title={info.title[:44]!r}")
    return True


w.user32.EnumWindows(ENUM(cb), 0)

print("\ntopmost per monitor (skipping our own pid):")
for i in range(len(monitors)):
    hwnd = w.topmost_window_on_monitor(i, monitors, {w.own_process_id()})
    if hwnd:
        info = w.window_info(hwnd, monitors)
        print(f"  monitor {i+1}: {info.title[:50]!r}")
    else:
        print(f"  monitor {i+1}: none")
