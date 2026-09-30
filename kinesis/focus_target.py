"""What is under a screen point, and is it somewhere you can type?

Used to decide whether the dictation gesture should click into a text field first. Three sources, in
order of trust:

1. **UI Automation** - definitive for native controls (an EDIT reports controlType 50004). Chromium
   puts its page content behind accessibility, which is not exposed on this machine even after the
   client has been waiting seconds, so browsers come back inconclusive.
2. **Window class under the point** - catches Edit / RichEdit / Scintilla windows even without UIA.
3. **Nothing** - inconclusive. `decide()` says what to do about that per mode.

The decision logic is deliberately separate from the COM calls so it can be tested without a desktop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from . import winapi as w

# UIA control types (UIA_*ControlTypeId)
CT_BUTTON = 50000
CT_COMBOBOX = 50003
CT_EDIT = 50004
CT_HYPERLINK = 50005
CT_IMAGE = 50006
CT_LISTITEM = 50007
CT_MENUITEM = 50010
CT_TAB = 50017
CT_TABITEM = 50018
CT_TEXT = 50020
CT_CUSTOM = 50025
CT_DOCUMENT = 50030
CT_WINDOW = 50032
CT_PANE = 50033

EDITABLE_CONTROLS = {CT_EDIT, CT_DOCUMENT, CT_COMBOBOX}
# Things that are definitely not somewhere to type: clicking them does something else instead.
NOT_TEXT_CONTROLS = {CT_BUTTON, CT_HYPERLINK, CT_IMAGE, CT_MENUITEM, CT_TAB, CT_TABITEM,
                     CT_LISTITEM}
EDITABLE_CLASSES = {"edit", "richedit20w", "richedit50w", "richedit20a", "scintilla",
                    "textbox", "textarea"}
# Renderer windows that hold a whole web page. Not a text box by themselves, but we cannot read
# inside them (Chromium keeps its accessibility tree to itself), so in `auto` they count as
# somewhere you meant to type.
WEB_CLASSES = {"chrome_renderwidgethosthwnd", "mozillawindowclass"}
# Shell/desktop surfaces: clicking them does nothing useful, so never treat them as a field.
SHELL_CLASSES = {"progman", "workerw", "shell_traywnd", "shell_secondarytraywnd", "dv2controlhost",
                 "windows.ui.core.corewindow"}

# Modes
MODES = ("auto", "uia", "always", "off")


@dataclass
class Hit:
    """What a hit test found at a point."""
    x: int = 0
    y: int = 0
    source: str = "none"          # uia | class | none
    control_type: int = 0
    class_name: str = ""
    name: str = ""
    editable: Optional[bool] = None     # None = could not tell
    focused: bool = False
    reason: str = ""


def decide(mode: str, hit: Optional[Hit]) -> Tuple[bool, str]:
    """Should the pointer be parked here and clicked before dictating?

    `auto` clicks when the point is definitely a text box, and also when the point is a browser
    page (inconclusive): you are looking at a field and made the dictation gesture, so clicking is
    the intent. It never clicks a control that obviously does something else.
    """
    mode = (mode or "auto").lower()
    if mode == "off":
        return False, "mode=off"
    if hit is None:
        return False, "no hit test"
    if mode == "always":
        return True, "mode=always"
    if hit.control_type in NOT_TEXT_CONTROLS:
        return False, f"{hit.class_name or 'control'} is not a text box"
    if hit.focused and hit.editable:
        return False, "already focused here"
    if hit.editable is True:
        return True, hit.reason or "editable element"
    if hit.editable is False:
        return False, hit.reason or "not editable"
    # inconclusive
    if mode == "uia":
        return False, "inconclusive and mode=uia"
    cls = (hit.class_name or "").lower()
    name = (hit.name or "").lower()
    if cls in SHELL_CLASSES or "taskbar" in cls or "taskbar" in name:
        return False, f"{hit.class_name or hit.name} is a shell surface"
    if cls in EDITABLE_CLASSES:
        return True, f"class {hit.class_name}"
    if cls in WEB_CLASSES:
        return True, "web content (unreadable, so assume a field)"
    if hit.source == "uia":
        return True, "browser/web content (inconclusive)"
    return False, "nothing identified under the gaze point"


# ------------------------------------------------------------------ UIA (optional, warm client)
_uia = None
_uia_failed = False


def _client():
    """A cached IUIAutomation instance, or None. Built once: COM setup is the slow part."""
    global _uia, _uia_failed
    if _uia is not None or _uia_failed:
        return _uia
    try:
        import comtypes.client
        from comtypes import CLSCTX_ALL
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA
        _uia = (comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation,
                                             clsctx=CLSCTX_ALL), UIA)
    except Exception:
        _uia_failed = True
        _uia = None
    return _uia


def prewarm() -> bool:
    """Build the UIA client up front so the first gesture is not paying for COM startup."""
    return _client() is not None


def _from_uia(x: int, y: int) -> Optional[Hit]:
    got = _client()
    if got is None:
        return None
    uia, UIA = got
    try:
        pt = UIA.tagPOINT()
        pt.x, pt.y = int(x), int(y)
        el = uia.ElementFromPoint(pt)
        if el is None:
            return None
        hit = Hit(x=int(x), y=int(y), source="uia")
        try:
            hit.control_type = int(el.CurrentControlType)
        except Exception:
            hit.control_type = 0
        try:
            hit.class_name = el.CurrentClassName or ""
        except Exception:
            hit.class_name = ""
        try:
            hit.name = (el.CurrentName or "")[:60]
        except Exception:
            hit.name = ""
        try:
            hit.focused = bool(el.CurrentHasKeyboardFocus)
        except Exception:
            hit.focused = False
        if hit.control_type in EDITABLE_CONTROLS:
            hit.editable, hit.reason = True, f"controlType {hit.control_type}"
        elif hit.control_type in NOT_TEXT_CONTROLS:
            hit.editable, hit.reason = False, f"controlType {hit.control_type}"
        else:
            try:
                vp = el.GetCurrentPattern(UIA.UIA_ValuePatternId)
                if vp is not None:
                    ro = bool(vp.CurrentIsReadOnly)
                    hit.editable = not ro
                    hit.reason = "read-only value" if ro else "writable value"
            except Exception:
                pass
        return hit
    except Exception:
        return None


def hit_test(x: int, y: int) -> Optional[Hit]:
    """What is at this screen point, using the best source available."""
    hit = _from_uia(x, y)
    if hit is not None and hit.editable is not None and hit.control_type:
        return hit
    # window class fallback (and a class check even when UIA answered inconclusively)
    try:
        hwnd = None
        try:
            import ctypes
            from ctypes import wintypes
            hwnd = w.user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        except Exception:
            hwnd = None
        cls = w.class_name(int(hwnd)) if hwnd else ""
        if cls.lower() in EDITABLE_CLASSES and cls.lower() != "chrome_renderwidgethosthwnd":
            return Hit(x=int(x), y=int(y), source="class", class_name=cls, editable=True,
                       focused=(hit.focused if hit else False), reason=f"class {cls}")
        if hit is None:
            return Hit(x=int(x), y=int(y), source="class", class_name=cls)
        if not hit.class_name and cls:
            hit.class_name = cls
        return hit
    except Exception:
        return hit
