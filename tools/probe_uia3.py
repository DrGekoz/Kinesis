"""Probe 4: cursor shape with a real move, and Chromium's UIA tree after a warm-up.

Probe 3 warped once and read the cursor immediately; the window may not have processed WM_SETCURSOR
yet. Probe 2 asked Chromium for its tree the instant the UIA client existed, and Chromium only builds
its accessibility tree once it notices an assistive client. This does both properly.
"""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import comtypes.client
from comtypes import CLSCTX_ALL

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA     # noqa: E402

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
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hCursor", wintypes.HANDLE),
                ("ptScreenPos", wintypes.POINT)]


user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
STOCK = {32512: "arrow", 32513: "I-BEAM", 32649: "hand", 32515: "cross", 32516: "text"}
LOADED = {int(user32.LoadCursorW(None, ctypes.c_void_p(v)) or 0): n for v, n in STOCK.items()}
print("stock cursor handles:", {hex(k): v for k, v in LOADED.items()})


def cursor_name():
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    user32.GetCursorInfo(ctypes.byref(ci))
    h = int(ci.hCursor or 0)
    return h, LOADED.get(h, f"other:{hex(h)}")


def class_at(x, y):
    hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, buf, 64)
    return buf.value


WS_OVERLAPPEDWINDOW, WS_VISIBLE, WS_CHILD, WS_BORDER, ES_AUTOHSCROLL = (
    0x00CF0000, 0x10000000, 0x40000000, 0x00800000, 0x0080)
frame = user32.CreateWindowExW(0, "Static", "Kinesis cursor probe", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                               500, 300, 560, 260, 0, 0, 0, None)
edit = user32.CreateWindowExW(0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER | ES_AUTOHSCROLL,
                              40, 60, 460, 24, frame, 0, 0, None)
user32.SetForegroundWindow(frame)
time.sleep(0.5)
origin = wintypes.POINT(0, 0)
user32.ClientToScreen(frame, ctypes.byref(origin))
ox, oy = origin.x, origin.y

print("\ncursor test (approach from a distance so a real WM_MOUSEMOVE fires):")
for label, x, y in [("EDIT control", ox + 240, oy + 72), ("blank area", ox + 240, oy + 160)]:
    user32.SetCursorPos(int(x - 120), int(y))          # arrive from the left
    time.sleep(0.05)
    user32.SetCursorPos(int(x - 4), int(y))            # step onto the target
    time.sleep(0.05)
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.3)
    h, name = cursor_name()
    print(f"  {label:14} ({x},{y}) over class={class_at(x, y)!r} -> cursor={name}")

user32.DestroyWindow(frame)

print("\nChromium UIA warm-up (accessibility activates a second or so after a client appears):")
uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation, clsctx=CLSCTX_ALL)
monitors = w.enumerate_monitors()
targets = []
for hwnd in w._enum_windows():
    info = w.window_info(hwnd, monitors)
    if info and info.exe in ("opera.exe", "chrome.exe", "msedge.exe", "brave.exe"):
        if (info.rect[2] - info.rect[0]) > 400 and (info.rect[3] - info.rect[1]) > 300:
            targets.append(info)
if not targets:
    print("  no browser window open")
else:
    t = targets[0]
    print(f"  watching {t.exe} {t.title[:34]!r} for 6 s")
    for wait in (0.5, 2.0, 4.0):
        time.sleep(wait)
        counts, edits = {}, []
        try:
            root_el = uia.ElementFromHandle(wintypes.HWND(int(t.hwnd)))
            walker = uia.RawViewWalker

            def visit(el, depth):
                if depth > 7 or sum(counts.values()) > 6000:
                    return
                try:
                    c = el.CurrentControlType
                except Exception:
                    c = -1
                counts[c] = counts.get(c, 0) + 1
                if c in (UIA.UIA_EditControlTypeId, UIA.UIA_DocumentControlTypeId) and len(edits) < 5:
                    try:
                        edits.append((c, (el.CurrentName or "")[:24]))
                    except Exception:
                        pass
                try:
                    child = walker.GetFirstChildElement(el)
                except Exception:
                    return
                n = 0
                while child is not None and n < 120:
                    visit(child, depth + 1)
                    n += 1
                    try:
                        child = walker.GetNextSiblingElement(child)
                    except Exception:
                        break

            visit(root_el, 0)
            named = {50004: "Edit", 50030: "Document", 50033: "Pane", 50026: "Group", 50020: "Text",
                     50000: "Button", 50006: "Image", 50005: "Hyperlink", 50032: "Window"}
            top = {named.get(k, k): v for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:8]}
            print(f"    +{wait}s: {top}")
            print(f"           editable nodes: {edits if edits else 'none'}")
        except Exception as exc:
            print(f"    +{wait}s: walk failed: {exc}")

print("\ndone")
