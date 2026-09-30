"""Browser tab strips: where they are, so a gaze-click can land on one.

A gaze-assisted click needs to know that the point you are looking at is a *tab* and not just
"somewhere near the top of the window". The strip is the top of the browser's client area: the
window frame is excluded by using the client rect, and the first few pixels are excluded because
Chromium spends them on the window drag region rather than on tabs.
"""
from __future__ import annotations

from typing import Optional, Tuple

from . import winapi as w

# Chromium and Firefox both expose a stable window class, which is a far better signal than the
# title. Electron apps share Chrome_WidgetWin_1, so the executable decides.
CHROMIUM_CLASSES = {"Chrome_WidgetWin_0", "Chrome_WidgetWin_1"}
FIREFOX_CLASSES = {"MozillaWindowClass", "MozillaWindowClassDropTarget"}
BROWSER_EXES = {
    "chrome.exe", "msedge.exe", "brave.exe", "opera.exe", "opera_gx.exe", "vivaldi.exe",
    "chromium.exe", "thorium.exe", "firefox.exe", "waterfox.exe", "librewolf.exe",
}


def is_browser(info) -> bool:
    """Is this window a browser? Class first, then the executable."""
    if info is None:
        return False
    exe = (getattr(info, "exe", "") or "").lower()
    cls = getattr(info, "class_name", "") or ""
    if exe in BROWSER_EXES:
        return True
    if exe:
        return False                      # a known non-browser exe never counts
    return cls in CHROMIUM_CLASSES or cls in FIREFOX_CLASSES


def tab_strip_band(info, top_px: float = 6.0, height_px: float = 40.0
                   ) -> Optional[Tuple[float, float]]:
    """(y0, y1) of the tab strip in screen coordinates, or None if this window has no strip.

    Heights are logical pixels scaled by the window's DPI, so a 125% display gets a proportionally
    taller band.
    """
    if not is_browser(info):
        return None
    client = w.client_rect_on_screen(int(info.hwnd))
    if client is None:
        return None
    # Chromium browsers enumerate hidden helper windows (class "IME" on Opera) with a zero-size
    # client rect. Those are not tab strips, and their "band" would sit at the top of the screen.
    if (client[2] - client[0]) < 200 or (client[3] - client[1]) < 200:
        return None
    scale = max(0.5, w.window_dpi(int(info.hwnd)) / 96.0)
    y0 = client[1] + top_px * scale
    return (y0, y0 + height_px * scale)


def in_tab_strip(info, x: float, y: float, top_px: float = 6.0, height_px: float = 40.0) -> bool:
    """Is this screen point over a browser's tab strip?"""
    band = tab_strip_band(info, top_px, height_px)
    if band is None:
        return False
    client = w.client_rect_on_screen(int(info.hwnd))
    if client is None:
        return False
    return (client[0] <= x <= client[2]) and (band[0] <= y <= band[1])
