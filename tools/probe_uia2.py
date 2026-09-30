"""Probe 2: a real Win32 EDIT control, and what a browser exposes to UIA.

Tk reports every control as a Pane, so it cannot tell us whether the mechanism works. This creates a
genuine Win32 EDIT window and hit-tests it, then walks the UIA tree under the running browser to see
whether editable nodes are exposed there at all.
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
uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation, clsctx=CLSCTX_ALL)

EDIT_CT = UIA.UIA_EditControlTypeId
DOC_CT = UIA.UIA_DocumentControlTypeId


def pt(x, y):
    p = UIA.tagPOINT()
    p.x, p.y = int(x), int(y)
    return p


def ct(el):
    try:
        return el.CurrentControlType
    except Exception:
        return -1


def editable(el):
    """Edit/Document, or a writable ValuePattern."""
    if el is None:
        return False, "no element"
    if ct(el) in (EDIT_CT, DOC_CT):
        return True, f"controlType {ct(el)}"
    try:
        vp = el.GetCurrentPattern(UIA.UIA_ValuePatternId)
        if vp:
            try:
                if not vp.CurrentIsReadOnly:
                    return True, "writable ValuePattern"
                return False, "read-only ValuePattern"
            except Exception:
                return True, "ValuePattern (readonly unknown)"
    except Exception:
        pass
    try:
        if el.CurrentClassName in ("Edit", "RichEdit20W", "RichEdit50W", "Scintilla"):
            return True, f"class {el.CurrentClassName}"
    except Exception:
        pass
    return False, "not editable"


# ---------------------------------------------------------------- real Win32 EDIT
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND
WS_OVERLAPPEDWINDOW, WS_VISIBLE, WS_CHILD, WS_BORDER = 0x00CF0000, 0x10000000, 0x40000000, 0x00800000
ES_AUTOHSCROLL = 0x0080

top = user32.CreateWindowExW(0, "Static", "Kinesis UIA probe", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                             400, 420, 420, 150, 0, 0, 0, None)
edit = user32.CreateWindowExW(0, "EDIT", "click me", WS_CHILD | WS_VISIBLE | WS_BORDER | ES_AUTOHSCROLL,
                              40, 40, 320, 26, top, 0, 0, None)
user32.SetForegroundWindow(top)
user32.SetFocus(edit)
time.sleep(0.6)

print("1. real Win32 EDIT control")
ex, ey = 400 + 200, 420 + 52
try:
    el = uia.ElementFromPoint(pt(ex, ey))
    ok, why = editable(el)
    print(f"   hit ({ex},{ey}) -> class={el.CurrentClassName!r} focusable={bool(el.CurrentIsKeyboardFocusable)}")
    print(f"   editable={ok} ({why})")
except Exception as exc:
    print(f"   raised: {exc}")
try:
    foc = uia.GetFocusedElement()
    ok, why = editable(foc)
    print(f"   focused element -> class={foc.CurrentClassName!r} editable={ok} ({why})")
except Exception as exc:
    print(f"   focused raised: {exc}")

user32.DestroyWindow(top)

print("\n2. what a browser exposes to UIA")
monitors = w.enumerate_monitors()
target = None
for hwnd in w._enum_windows():
    info = w.window_info(hwnd, monitors)
    if info and info.exe in ("opera.exe", "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"):
        if (info.rect[2] - info.rect[0]) > 400:
            target = info
            break
if target is None:
    print("   no browser window open")
else:
    print(f"   {target.exe} / {target.class_name}: {target.title[:40]!r}")
    try:
        root_el = uia.ElementFromHandle(wintypes.HWND(int(target.hwnd)))
        cond = uia.CreateTrueCondition()
        walker = uia.RawViewWalker
        counts = {}
        edits = []

        def visit(el, depth):
            if depth > 6 or len(counts) > 4000:
                return
            c = ct(el)
            counts[c] = counts.get(c, 0) + 1
            if c in (EDIT_CT, DOC_CT) and len(edits) < 6:
                try:
                    edits.append((c, el.CurrentClassName, bool(el.CurrentIsKeyboardFocusable),
                                  (el.CurrentName or "")[:28]))
                except Exception:
                    pass
            try:
                child = walker.GetFirstChildElement(el)
            except Exception:
                return
            n = 0
            while child is not None and n < 60:
                visit(child, depth + 1)
                n += 1
                try:
                    child = walker.GetNextSiblingElement(child)
                except Exception:
                    break

        visit(root_el, 0)
        named = {20004: "Edit", 20030: "Document", 20026: "Pane", 20033: "Group",
                 20020: "Text", 20000: "Button", 20016: "CheckBox", 20021: "TitleBar"}
        found = {named.get(k, k): v for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:10]}
        print(f"   UIA node types under the window: {found}")
        print(f"   editable nodes found: {edits if edits else 'NONE'}")
    except Exception as exc:
        print(f"   tree walk failed: {exc}")

print("\ndone")
