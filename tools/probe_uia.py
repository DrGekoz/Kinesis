"""Probe: can UI Automation identify an editable text box at an arbitrary screen point?

Uses a real window with a real Entry. The first cut of this probe crashed on null UIA properties and
reported the wrong element, so it now prints raw values and checks the focused element first.
"""
import ctypes
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import comtypes.client
from comtypes import CLSCTX_ALL

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA     # noqa: E402

from kinesis import winapi as w                        # noqa: E402

w.set_dpi_aware()
CTRL = {UIA.UIA_EditControlTypeId: "Edit", UIA.UIA_DocumentControlTypeId: "Document",
        UIA.UIA_TextControlTypeId: "Text", UIA.UIA_PaneControlTypeId: "Pane",
        UIA.UIA_WindowControlTypeId: "Window", UIA.UIA_CustomControlTypeId: "Custom"}


def name_of(ct):
    return CTRL.get(ct, f"controlType={ct}")


def point(x, y):
    pt = UIA.tagPOINT()
    pt.x, pt.y = int(x), int(y)
    return pt


def describe(el):
    if el is None:
        return "None"
    bits = []
    for attr in ("CurrentControlType", "CurrentClassName", "CurrentLocalizedControlType",
                 "CurrentIsEnabled", "CurrentIsKeyboardFocusable", "CurrentHasKeyboardFocus"):
        try:
            val = getattr(el, attr)
        except Exception as exc:
            val = f"<{type(exc).__name__}>"
        if attr == "CurrentControlType" and isinstance(val, int):
            val = name_of(val)
        bits.append(f"{attr.replace('Current', '')}={val!r}")
    return " ".join(bits)


def patterns(el):
    out = []
    if el is None:
        return out
    for pid, label in ((UIA.UIA_ValuePatternId, "Value"), (UIA.UIA_TextPatternId, "Text"),
                       (UIA.UIA_LegacyIAccessiblePatternId, "Legacy")):
        try:
            if el.GetCurrentPattern(pid):
                out.append(label)
        except Exception:
            pass
    return out


root = tk.Tk()
root.title("Kinesis UIA probe")
root.geometry("460x240+300+300")
root.attributes("-topmost", True)
entry = tk.Entry(root, font=("Consolas", 13), width=34)
entry.pack(pady=40)
tk.Label(root, text="not a text box", font=("Consolas", 11)).pack(pady=10)
root.update()
entry.focus_force()
root.update()
time.sleep(0.5)

uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation, clsctx=CLSCTX_ALL)
print("UIA hit tests (screen coordinates):")

ex = entry.winfo_rootx() + entry.winfo_width() // 2
ey = entry.winfo_rooty() + entry.winfo_height() // 2
print(f"\n  tk Entry centre ({ex},{ey})")
for attempt in range(3):
    try:
        el = uia.ElementFromPoint(point(ex, ey))
    except Exception as exc:
        print(f"    ElementFromPoint raised: {exc}")
        el = None
    print(f"    attempt {attempt + 1}: {describe(el)} patterns={patterns(el)}")
    if el is not None:
        break
    time.sleep(0.4)

print("\n  focused element (independent of hit testing):")
try:
    foc = uia.GetFocusedElement()
    print(f"    {describe(foc)} patterns={patterns(foc)}")
except Exception as exc:
    print(f"    GetFocusedElement raised: {exc}")

print("\n  a point over the tk window background:")
try:
    el = uia.ElementFromPoint(point(root.winfo_rootx() + 8, root.winfo_rooty() + 6))
    print(f"    {describe(el)} patterns={patterns(el)}")
except Exception as exc:
    print(f"    raised: {exc}")

root.destroy()
print("\ndone")
