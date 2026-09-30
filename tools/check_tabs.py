"""Check tab-strip detection against whatever browsers are actually open right now."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis import tabs                                              # noqa: E402
from kinesis import winapi as w                                       # noqa: E402

w.set_dpi_aware()
monitors = w.enumerate_monitors()
found = 0
for hwnd in w._enum_windows():
    info = w.window_info(hwnd, monitors)
    if info is None or not info.title.strip():
        continue
    if not tabs.is_browser(info):
        continue
    found += 1
    client = w.client_rect_on_screen(info.hwnd)
    band = tabs.tab_strip_band(info)
    dpi = w.window_dpi(info.hwnd)
    print(f"\n{info.exe or '(no exe)'}  class={info.class_name}  dpi={dpi}")
    print(f"  title:  {info.title[:60]}")
    print(f"  client: {client}")
    print(f"  strip:  {band}")
    if band:
        mid_x = (client[0] + client[2]) // 2
        y_mid = int((band[0] + band[1]) / 2)
        print(f"  point at the middle of the strip ({mid_x}, {y_mid}): "
              f"in_tab_strip={tabs.in_tab_strip(info, mid_x, y_mid)}")
        y_page = int(band[1] + 60)
        print(f"  point 60px below the strip      ({mid_x}, {y_page}): "
              f"in_tab_strip={tabs.in_tab_strip(info, mid_x, y_page)}")

print(f"\n{found} browser window(s) found")
if not found:
    print("(open Chrome or Edge to test against a real tab strip)")
