"""Probe 5: cursor shape over a topmost EDIT vs a blank area.

Probes 3 and 4 both read the cursor while Opera was covering the test window, so they measured the
wrong window. This creates a WS_EX_TOPMOST frame and verifies WindowFromPoint really lands on the EDIT
before trusting the cursor reading.
"""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis import winapi as w                        # noqa: E402

w.set_dpi_aware()
u = ctypes.WinDLL("user32", use_last_error=True)
u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
u.CreateWindowExW.restype = wintypes.HWND
u.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
u.LoadCursorW.restype = wintypes.HANDLE
u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
u.WindowFromPoint.argtypes = [wintypes.POINT]
u.WindowFromPoint.restype = wintypes.HWND
u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int, ctypes.c_int, wintypes.UINT]
u.SetWindowPos.restype = wintypes.BOOL


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hCursor", wintypes.HANDLE),
                ("ptScreenPos", wintypes.POINT)]


u.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
STOCK = {32512: "arrow", 32513: "I-BEAM", 32649: "hand", 32515: "cross"}
LOADED = {int(u.LoadCursorW(None, ctypes.c_void_p(v)) or 0): n for v, n in STOCK.items()}


def cname():
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    u.GetCursorInfo(ctypes.byref(ci))
    return LOADED.get(int(ci.hCursor or 0), f"other:{hex(int(ci.hCursor or 0))}")


def cls_at(x, y):
    hwnd = u.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    buf = ctypes.create_unicode_buffer(64)
    u.GetClassNameW(hwnd, buf, 64)
    return buf.value or "(none)", hwnd


WS_POPUP, WS_VISIBLE, WS_CHILD, WS_BORDER = 0x80000000, 0x10000000, 0x40000000, 0x00800000
WS_EX_TOPMOST, WS_EX_TOOLWINDOW = 0x00000008, 0x00000080
ES_AUTOHSCROLL = 0x0080
HWND_TOPMOST, SWP_SHOWWINDOW = -1, 0x0040

frame = u.CreateWindowExW(WS_EX_TOPMOST | WS_EX_TOOLWINDOW, "Static", "kinesis probe",
                          WS_POPUP | WS_VISIBLE, 600, 200, 620, 300, 0, 0, 0, None)
u.SetWindowPos(frame, HWND_TOPMOST, 600, 200, 620, 300, SWP_SHOWWINDOW)
edit = u.CreateWindowExW(0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER | ES_AUTOHSCROLL,
                         50, 60, 500, 26, frame, 0, 0, None)
u.SetForegroundWindow(frame)
time.sleep(0.6)
origin = wintypes.POINT(0, 0)
u.ClientToScreen(frame, ctypes.byref(origin))
ox, oy = origin.x, origin.y

print("point                       class under it              cursor")
for label, x, y in [("EDIT", ox + 260, oy + 73), ("blank frame", ox + 260, oy + 200)]:
    u.SetCursorPos(int(x - 150), int(y))
    time.sleep(0.08)
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.35)
    klass, _ = cls_at(x, y)
    print(f"  {label:14} ({x},{y})  {klass:26}  {cname()}")

u.DestroyWindow(frame)
print("done")
