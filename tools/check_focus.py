"""Live check: what does the dictation-focus hit test see at real points, and would it click?

Run it with a browser open and look at the printed verdicts. It does NOT move the pointer or click.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis import focus_target as ft                               # noqa: E402
from kinesis import winapi as w                                      # noqa: E402

w.set_dpi_aware()
t0 = time.perf_counter()
ready = ft.prewarm()
print(f"UIA client ready={ready} in {(time.perf_counter() - t0) * 1000:.0f} ms")
if not ready:
    print("  (comtypes + UIAutomationCore are needed for the editable-element check)")

monitors = w.enumerate_monitors()
points = []
# somewhere inside each monitor, plus the taskbar strip
for m in monitors:
    points.append((f"monitor {m.device[-2:]} centre", m.left + m.width // 2, m.top + m.height // 2))
points.append(("taskbar area", monitors[0].left + 400, monitors[0].bottom - 12))

for label, x, y in points:
    t = time.perf_counter()
    hit = ft.hit_test(x, y)
    ms = (time.perf_counter() - t) * 1000
    ok, why = ft.decide("auto", hit)
    win_title = ""
    try:
        hwnd = w.topmost_window_at(x, y, monitors)
        if hwnd:
            info = w.window_info(hwnd, monitors)
            win_title = (info.exe or info.title[:26]) if info else ""
    except Exception:
        pass
    print(f"\n({x},{y}) {label}  [{ms:.1f} ms]")
    if hit is None:
        print("  hit: none")
    else:
        print(f"  hit: source={hit.source} controlType={hit.control_type} "
              f"class={hit.class_name!r} editable={hit.editable} focused={hit.focused}")
        print(f"  window under it: {win_title}")
    print(f"  auto mode -> {'CLICK' if ok else 'no click'} ({why})")
