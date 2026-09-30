"""Probe 3: is the cursor shape a reliable "there is a text box here" signal?

Every app that accepts text shows an I-beam over the editable area. If that holds, Kinesis can ask the
app instead of guessing, with no accessibility dependencies at all. Tested against a real Win32 EDIT
control and against a plain window background.
"""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis import winapi as w                        # noqa: E402

w.set_dpi_aware()
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hCursor", wintypes.HANDLE),
                ("ptScreenPos", wintypes.POINT)]


user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]

IDC_ARROW, IDC_IBEAM, IDC_HAND, IDC_SIZEALL, IDC_CROSS = 32512, 32513, 32649, 32646, 32515
NAMES = {IDC_ARROW: "arrow", IDC_IBEAM: "I-BEAM", IDC_HAND: "hand", IDC_SIZEALL: "move",
         IDC_CROSS: "cross"}
MAKEINTRESOURCE = ctypes.c_void_p


def cursor_handles():
    out = {}
    for vk, name in NAMES.items():
        out[int(user32.LoadCursorW(None, MAKEINTRESOURCE(vk)) or 0)] = name
    return out


HANDLES = cursor_handles()


def cursor_now():
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(ci)):
        return None, "GetCursorInfo failed"
    return int(ci.hCursor or 0), HANDLES.get(int(ci.hCursor or 0), "unknown/custom")


WS_OVERLAPPEDWINDOW, WS_VISIBLE, WS_CHILD, WS_BORDER = 0x00CF0000, 0x10000000, 0x40000000, 0x00800000
ES_AUTOHSCROLL, ES_MULTILINE = 0x0080, 0x0004

frame = user32.CreateWindowExW(0, "Static", "Kinesis cursor probe", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                               500, 300, 560, 260, 0, 0, 0, None)
edit = user32.CreateWindowExW(0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER | ES_AUTOHSCROLL,
                              40, 60, 460, 24, frame, 0, 0, None)
blank = user32.CreateWindowExW(0, "STATIC", "", WS_CHILD | WS_VISIBLE, 40, 120, 460, 80, frame, 0, 0, None)
user32.SetForegroundWindow(frame)
time.sleep(0.7)

# client origin of the frame
origin = wintypes.POINT(0, 0)
user32.ClientToScreen(frame, ctypes.byref(origin))
ox, oy = origin.x, origin.y

tests = [("over the EDIT control", ox + 240, oy + 72),
         ("over a blank static", ox + 240, oy + 160),
         ("over the desktop", 20, 20)]

print("cursor shape at each point (the pointer IS warped there, as Kinesis does):")
for label, x, y in tests:
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.12)
    handle, name = cursor_now()
    print(f"  {label:22} ({x},{y}) -> {name}")

user32.DestroyWindow(frame)
print("\ndone")
