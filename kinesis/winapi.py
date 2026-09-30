"""Windows plumbing: monitor layout, window inspection/control, input injection.

Everything here is ctypes against user32 - no pyautogui, no keyboard library, so there is no
FAILSAFE behaviour to trip over and no per-call Python overhead in the hot path.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np          # only for the layered-window DIB view

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)     # layered-window bitmaps (desktop overlay)

# ---------------------------------------------------------------- constants
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79

MONITORINFOF_PRIMARY = 0x00000001
MONITOR_DEFAULTTONEAREST = 2

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE, SW_SHOW = 6, 3, 9, 5
SW_HIDE = 0
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOP = 0
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
GA_ROOT = 2

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002

WHEEL_DELTA = 120

VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10, "ctrl": 0x11,
    "control": 0x11, "alt": 0x12, "pause": 0x13, "capslock": 0x14, "esc": 0x1B,
    "escape": 0x1B, "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23,
    "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45, "f": 0x46, "g": 0x47,
    "h": 0x48, "i": 0x49, "j": 0x4A, "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E,
    "o": 0x4F, "p": 0x50, "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54, "u": 0x55,
    "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59, "z": 0x5A,
    "lwin": 0x5B, "rwin": 0x5C,
    # OEM punctuation, for keyboard shortcuts like Ctrl+= / Ctrl+- (zoom in/out)
    "=": 0xBB, "+": 0xBB, "plus": 0xBB, "equal": 0xBB, "equals": 0xBB,
    # punctuation and the Windows key: the OEM keys a gesture map will want for ctrl+/ or ctrl+`
    "win": 0x5B, "lwin": 0x5B, "super": 0x5B, "meta": 0x5B,
    "/": 0xBF, "slash": 0xBF, "?": 0xBF,
    "`": 0xC0, "backtick": 0xC0, "grave": 0xC0, "~": 0xC0,
    ",": 0xBC, "comma": 0xBC, "<": 0xBC,
    ".": 0xBE, "period": 0xBE, "dot": 0xBE, ">": 0xBE,
    ";": 0xBA, "semicolon": 0xBA, ":": 0xBA,
    "'": 0xDE, "quote": 0xDE, "apostrophe": 0xDE,
    "[": 0xDB, "bracketleft": 0xDB, "braceleft": 0xDB,
    "]": 0xDD, "bracketright": 0xDD, "braceright": 0xDD,
    "\\": 0xDC, "backslash": 0xDC, "pipe": 0xDC,
    "-": 0xBD, "minus": 0xBD, "hyphen": 0xBD, "_": 0xBD,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    # The multimedia keys. These are the master volume keys, which is what a gesture wants: they
    # adjust whatever the system considers the default output, and they are the same keys the
    # keyboard's volume rocker sends - so no extra dependency and no per-app focus problems.
    # The mute key deliberately has no alias mapping to "m": it is a distinct action, and aliasing
    # it would silently turn a mute request into a mute toggle with no way back.
    "volumeup": 0xAF, "volume_up": 0xAF, "volume-up": 0xAF, "volup": 0xAF, "vol_up": 0xAF,
    "vol+": 0xAF,
    "volumedown": 0xAE, "volume_down": 0xAE, "volume-down": 0xAE, "voldown": 0xAE, "vol_down": 0xAE,
    "vol-": 0xAE,
    "volumemute": 0xAD, "volume_mute": 0xAD, "volume-mute": 0xAD, "mute": 0xAD,
}


def vk_for(name: str) -> int:
    key = str(name).strip().lower()
    if key in VK:
        return VK[key]
    if len(key) == 1 and key.isalpha():
        return ord(key.upper())
    raise KeyError(f"no virtual-key mapping for {name!r}")


# ---------------------------------------------------------------- structures
class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    def as_tuple(self):
        return (self.left, self.top, self.right, self.bottom)


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", wintypes.WCHAR * 32)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT), ("ptMinPosition", POINT),
                ("ptMaxPosition", POINT), ("rcNormalPosition", RECT)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.SetCursorPos.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = (ctypes.POINTER(POINT),)
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
user32.GetSystemMetrics.restype = ctypes.c_int


# ---------------------------------------------------------------- monitors
@dataclass
class Monitor:
    handle: int
    device: str
    left: int
    top: int
    right: int
    bottom: int
    primary: bool
    work: tuple = (0, 0, 0, 0)

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    @property
    def centre(self):
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def __str__(self):
        tag = ", primary" if self.primary else ""
        return (f"{self.device} {self.width}x{self.height} at ({self.left},{self.top}){tag}")


def _monitor_callback(hmonitor, hdc, lprc, data):
    info = MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(MONITORINFOEXW)
    if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
        rect, work = info.rcMonitor, info.rcWork
        found.append(Monitor(
            handle=int(hmonitor), device=info.szDevice,
            left=rect.left, top=rect.top, right=rect.right, bottom=rect.bottom,
            primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
            work=work.as_tuple()))
    return True


MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                     ctypes.POINTER(RECT), wintypes.LPARAM)


class _DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1


def display_devices():
    """Attached displays as (gdi_device, adapter, monitor_name, monitor_id).

    The monitor name and id are what identify the actual panel ("Lenovo L27i-30",
    "MONITOR\\LEN66BF\\{...}\\0002"), which is how the EDID for each screen is located.
    """
    user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.POINTER(_DISPLAY_DEVICE), wintypes.DWORD]
    user32.EnumDisplayDevicesW.restype = wintypes.BOOL
    out = []
    index = 0
    while True:
        adapter = _DISPLAY_DEVICE()
        adapter.cb = ctypes.sizeof(_DISPLAY_DEVICE)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(adapter), 0):
            break
        index += 1
        if not (adapter.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP):
            continue
        monitor = _DISPLAY_DEVICE()
        monitor.cb = ctypes.sizeof(_DISPLAY_DEVICE)
        user32.EnumDisplayDevicesW(adapter.DeviceName, 0, ctypes.byref(monitor), 0)
        out.append((adapter.DeviceName, adapter.DeviceString,
                    monitor.DeviceString, monitor.DeviceID))
    return out


# ---------------------------------------------------------------------------- layered windows
# For the transparent desktop overlay: a per-pixel-alpha window that Windows composites for us.
# WS_EX_TRANSPARENT makes every click fall through to whatever is underneath, WS_EX_NOACTIVATE keeps
# it from ever taking focus, and UpdateLayeredWindow pushes a premultiplied BGRA bitmap into it.
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0
VK_INSERT = 0x2D


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


# ctypes passes ints as C int for unprototyped functions, and these are 64-bit handles - without
# these prototypes a handle above 2^31 raises "int too long to convert" the moment one is allocated.
class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                       ctypes.POINTER(_SIZE), wintypes.HDC,
                                       ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
                                       ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                   wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]


# ---------------------------------------------------------------------------- process + client
# Same prototype rule as the GDI block: HANDLEs are 64-bit, and an unprototyped call passes ints as
# C ints, which raises OverflowError the moment a handle exceeds 2^31.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                               wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]

_EXE_CACHE: dict = {}


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    if user32.GetClassNameW(wintypes.HWND(int(hwnd)), buf, 256):
        return buf.value
    return ""


def process_exe(pid: int) -> str:
    """Image name of a process, cached - used to tell a browser from any other Chromium window."""
    pid = int(pid)
    if pid in _EXE_CACHE:
        return _EXE_CACHE[pid]
    name = ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value)
        except Exception:
            name = ""
        finally:
            kernel32.CloseHandle(handle)
    _EXE_CACHE[pid] = name
    return name


def client_rect_on_screen(hwnd: int) -> Optional[tuple]:
    """The window's client area in screen coordinates - the frame excluded."""
    rect = wintypes.RECT()
    if not user32.GetClientRect(wintypes.HWND(int(hwnd)), ctypes.byref(rect)):
        return None
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(wintypes.HWND(int(hwnd)), ctypes.byref(origin)):
        return None
    return (origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom)


def window_dpi(hwnd: int) -> int:
    """Window DPI, so logical tab-strip heights scale. 96 = 100%."""
    try:
        return int(user32.GetDpiForWindow(wintypes.HWND(int(hwnd)))) or 96
    except Exception:                                       # pragma: no cover
        return 96


def create_overlay_window(x: int, y: int, w: int, h: int, title: str = "Kinesis overlay") -> int:
    """A borderless, click-through, topmost, never-activating window covering the given rect."""
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, wintypes.LPVOID]
    ex = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    hwnd = user32.CreateWindowExW(ex, "Static", title, WS_POPUP, x, y, w, h, 0, 0, 0, None)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE | SWP_SHOWWINDOW)
    return hwnd


def dib_buffer(w: int, h: int):
    """(hdc, bitmap, numpy BGRA view) - top-down 32-bit DIB we can write straight into."""
    screen_dc = user32.GetDC(0)
    hdc = gdi32.CreateCompatibleDC(screen_dc)
    user32.ReleaseDC(0, screen_dc)
    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = int(w)
    info.bmiHeader.biHeight = -int(h)                 # negative = top-down rows
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = DIB_RGB_COLORS
    bits = ctypes.c_void_p()
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    bitmap = gdi32.CreateDIBSection(hdc, ctypes.byref(info), DIB_RGB_COLORS,
                                    ctypes.byref(bits), None, 0)
    gdi32.SelectObject(hdc, bitmap)
    view = np.ctypeslib.as_array(ctypes.cast(bits, ctypes.POINTER(ctypes.c_ubyte)),
                                 shape=(int(h), int(w), 4))
    return hdc, bitmap, view


def blit_layered(hwnd: int, hdc: int, w: int, h: int) -> bool:
    """Composite the DIB onto the window (the bitmap must already be premultiplied BGRA)."""
    dst = wintypes.POINT(0, 0)
    size = _SIZE(int(w), int(h))
    src = wintypes.POINT(0, 0)
    blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    screen_dc = user32.GetDC(0)
    ok = user32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(dst), ctypes.byref(size),
                                    hdc, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
    user32.ReleaseDC(0, screen_dc)
    return bool(ok)


def destroy_overlay_window(hwnd: int, hdc: int = 0, bitmap: int = 0) -> None:
    try:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if hdc:
            gdi32.DeleteDC(hdc)
        if hwnd:
            user32.DestroyWindow(hwnd)
    except Exception:
        pass


def enumerate_monitors(by_position: bool = True) -> List[Monitor]:
    """Live monitor list. Sorted left-to-right by default so index 1 = leftmost,
    which is how a person counting screens thinks about them."""
    global found
    found = []
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_monitor_callback), 0)
    if by_position:
        found.sort(key=lambda m: (m.left, m.top))
    return list(found)


_VIRTUAL_OVERRIDE: Optional[tuple] = None


def set_virtual_screen_override(rect: Optional[tuple]) -> None:
    """Define "the desktop" as a subset of the real virtual screen.

    Every consumer - the aim classifier, gaze calibration, the canvas mapping, the overlays - asks
    for the virtual screen rather than enumerating monitors, so narrowing it here is what makes the
    monitor selection apply everywhere at once, instead of in the few places that were remembered.
    Pass None to go back to the whole desktop.
    """
    global _VIRTUAL_OVERRIDE
    if rect is not None and (int(rect[2]) <= 0 or int(rect[3]) <= 0):
        rect = None
    _VIRTUAL_OVERRIDE = tuple(int(v) for v in rect) if rect is not None else None


def virtual_screen() -> tuple:
    if _VIRTUAL_OVERRIDE is not None:
        return _VIRTUAL_OVERRIDE
    return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


def monitor_at(x: int, y: int, monitors: Optional[List[Monitor]] = None) -> Optional[Monitor]:
    monitors = monitors if monitors is not None else enumerate_monitors()
    for m in monitors:
        if m.contains(x, y):
            return m
    return None


# ---------------------------------------------------------------- windows
@dataclass
class WindowInfo:
    hwnd: int
    title: str
    rect: tuple            # (left, top, right, bottom)
    monitor_index: int
    process_id: int
    is_maximized: bool
    is_fullscreen: bool
    is_foreground: bool
    class_name: str = ""   # window class: identifies a browser without guessing from the title
    exe: str = ""          # process image name, e.g. chrome.exe


class MONITORINFOPLAIN(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD)]


def _window_rect(hwnd) -> Optional[tuple]:
    r = RECT()
    if user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
        return r.as_tuple()
    return None


def _is_maximized(hwnd) -> bool:
    return bool(user32.IsZoomed(wintypes.HWND(hwnd)))


def _monitor_rect_for_window(hwnd) -> Optional[tuple]:
    hmon = user32.MonitorFromWindow(wintypes.HWND(hwnd), MONITOR_DEFAULTTONEAREST)
    if not hmon:
        return None
    info = MONITORINFOPLAIN()
    info.cbSize = ctypes.sizeof(MONITORINFOPLAIN)
    if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        return info.rcMonitor.as_tuple()
    return None


def window_info(hwnd: int, monitors: Optional[List[Monitor]] = None) -> Optional[WindowInfo]:
    rect = _window_rect(hwnd)
    if rect is None:
        return None
    monitors = monitors if monitors is not None else enumerate_monitors()
    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    idx = 0
    for i, m in enumerate(monitors):
        if m.contains(cx, cy):
            idx = i
            break
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    mrect = _monitor_rect_for_window(hwnd)
    tolerance = 2
    fullscreen = False
    if mrect is not None:
        fullscreen = (abs(rect[0] - mrect[0]) <= tolerance and abs(rect[1] - mrect[1]) <= tolerance
                      and abs(rect[2] - mrect[2]) <= tolerance and abs(rect[3] - mrect[3]) <= tolerance
                      and not _is_maximized(hwnd))
    length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(wintypes.HWND(hwnd), buf, length + 1)
    return WindowInfo(hwnd=hwnd, title=buf.value, rect=rect, monitor_index=idx,
                      process_id=int(pid.value), is_maximized=_is_maximized(hwnd),
                      is_fullscreen=fullscreen,
                      is_foreground=(user32.GetForegroundWindow() == hwnd),
                      class_name=class_name(hwnd), exe=process_exe(int(pid.value)))


def foreground_window(monitors: Optional[List[Monitor]] = None) -> Optional[WindowInfo]:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    return window_info(hwnd, monitors)


def _enum_windows():
    handles = []
    ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lparam):
        handles.append(int(hwnd))
        return True

    user32.EnumWindows(ENUMPROC(cb), 0)
    return handles


def topmost_window_on_monitor(monitor_index: int, monitors: Optional[List[Monitor]] = None,
                              skip_pids: Optional[set] = None,
                              blocked_titles: Optional[Sequence[str]] = None) -> Optional[int]:
    """Topmost focusable window whose centre sits on the given monitor.
    EnumWindows walks top-level windows in z-order, so the first match is the top one.
    Overlay and shell windows are excluded - they are usually fullscreen and would otherwise
    swallow window gestures."""
    monitors = monitors if monitors is not None else enumerate_monitors()
    if not monitors:
        return None
    skip_pids = skip_pids or set()
    blocked = tuple(t.lower() for t in (blocked_titles or ()))
    for hwnd in _enum_windows():
        if not user32.IsWindowVisible(wintypes.HWND(hwnd)):
            continue
        if user32.IsIconic(wintypes.HWND(hwnd)):
            continue
        length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
        if length == 0:
            continue
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(wintypes.HWND(hwnd), buf, length + 1)
        title = buf.value
        if blocked and any(token in title.lower() for token in blocked):
            continue
        ex = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)
        if ex & WS_EX_TOOLWINDOW and not (ex & WS_EX_APPWINDOW):
            continue
        info = window_info(hwnd, monitors)
        if info is None or info.process_id in skip_pids:
            continue
        if info.monitor_index == monitor_index:
            return hwnd
    return None


def is_fullscreen_anywhere(hwnd: int) -> bool:
    info = window_info(hwnd)
    return bool(info and info.is_fullscreen)


def topmost_window_at(x: int, y: int, monitors: Optional[List[Monitor]] = None,
                      skip_pids: Optional[set] = None,
                      blocked_titles: Optional[Sequence[str]] = None) -> Optional[int]:
    """Topmost window containing the given screen point.

    Same filters as topmost_window_on_monitor (visible, not minimised, titled, not an overlay or
    shell window, not our own process), but the test is whether the window's own rect contains the
    point rather than whether the window's centre sits on a monitor. This is what turns a gaze
    point into "the window I am looking at"."""
    monitors = monitors if monitors is not None else enumerate_monitors()
    skip_pids = skip_pids or set()
    blocked = tuple(t.lower() for t in (blocked_titles or ()))
    for hwnd in _enum_windows():
        if not user32.IsWindowVisible(wintypes.HWND(hwnd)):
            continue
        if user32.IsIconic(wintypes.HWND(hwnd)):
            continue
        rect = _window_rect(hwnd)
        if rect is None or not (rect[0] <= x < rect[2] and rect[1] <= y < rect[3]):
            continue
        length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
        if length == 0:
            continue
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(wintypes.HWND(hwnd), buf, length + 1)
        if blocked and any(token in buf.value.lower() for token in blocked):
            continue
        ex = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE)
        if ex & WS_EX_TOOLWINDOW and not (ex & WS_EX_APPWINDOW):
            continue
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        if int(pid.value) in skip_pids:
            continue
        return hwnd
    return None


def focus_and_verify(hwnd: int, attempts: int = 3, settle: float = 0.08,
                     allow_alt_trick: bool = True) -> bool:
    """Focus a window and confirm the OS actually gave it foreground.

    SetForegroundWindow is refused when the calling process is not itself foreground, which is the
    normal case here (Kinesis sits in the background). AttachThreadInput usually gets around it; if
    not, the documented fallback is to tap Alt, which clears the foreground lock for the next call.
    The Alt tap is skipped when Alt is already being held by the Alt-Tab gesture.
    """
    import time as _time
    for attempt in range(max(attempts, 1)):
        focus_window(hwnd)
        _time.sleep(settle)
        if user32.GetForegroundWindow() == hwnd:
            return True
        if allow_alt_trick and attempt == attempts - 2:
            _send(_key_input(0x12, False), _key_input(0x12, True))     # VK_MENU tap
    return user32.GetForegroundWindow() == hwnd


# ---------------------------------------------------------------- input injection
def _send(*inputs):
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    return user32.SendInput(n, arr, ctypes.sizeof(INPUT))


def _key_input(vk: int, up: bool) -> INPUT:
    return INPUT(type=INPUT_KEYBOARD,
                 u=_INPUTUNION(ki=KEYBDINPUT(wVk=vk, wScan=0,
                                             dwFlags=KEYEVENTF_KEYUP if up else 0,
                                             time=0, dwExtraInfo=None)))


def _mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE,
                 u=_INPUTUNION(mi=MOUSEINPUT(dx=dx, dy=dy, mouseData=data & 0xFFFFFFFF,
                                             dwFlags=flags, time=0, dwExtraInfo=None)))


def get_cursor_pos() -> tuple:
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


def set_cursor_pos(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def mouse_click(button: str = "left") -> None:
    if button == "left":
        down, up = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
    elif button == "right":
        down, up = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    else:
        down, up = MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
    _send(_mouse_input(down), _mouse_input(up))


def mouse_down(button: str = "left") -> None:
    flag = {"left": MOUSEEVENTF_LEFTDOWN, "right": MOUSEEVENTF_RIGHTDOWN,
            "middle": MOUSEEVENTF_MIDDLEDOWN}[button]
    _send(_mouse_input(flag))


def mouse_up(button: str = "left") -> None:
    flag = {"left": MOUSEEVENTF_LEFTUP, "right": MOUSEEVENTF_RIGHTUP,
            "middle": MOUSEEVENTF_MIDDLEUP}[button]
    _send(_mouse_input(flag))


def scroll_wheel(clicks: int) -> None:
    """Positive = up/away (Windows convention, same as the physical wheel)."""
    if not clicks:
        return
    _send(_mouse_input(MOUSEEVENTF_WHEEL, data=int(clicks) * WHEEL_DELTA))


def scroll_horizontal(clicks: int) -> None:
    if not clicks:
        return
    _send(_mouse_input(MOUSEEVENTF_HWHEEL, data=int(clicks) * WHEEL_DELTA))


def key_down(name: str) -> None:
    _send(_key_input(vk_for(name), False))


def key_up(name: str) -> None:
    _send(_key_input(vk_for(name), True))


def key_tap(name: str) -> None:
    vk = vk_for(name)
    _send(_key_input(vk, False), _key_input(vk, True))


def master_volume() -> Optional[int]:
    """The master output volume as 0-65535, or None if the device cannot be read.

    Read-only and used by the verification tools, so a volume gesture can be proven against the
    real mixer rather than asserted. `waveOutGetVolume(NULL)` is the classic device-independent
    master-volume read; a NULL device handle means "the default output".
    """
    try:
        wave = ctypes.WinDLL("winmm")
    except OSError:                                     # pragma: no cover - non-Windows
        return None
    value = wintypes.DWORD()
    if wave.waveOutGetVolume(None, ctypes.byref(value)) != 0:
        return None
    return int(value.value)


def set_master_volume(value: int) -> bool:
    """Set the master output volume, 0-65535. Only the verification tools call this."""
    try:
        wave = ctypes.WinDLL("winmm")
    except OSError:                                     # pragma: no cover - non-Windows
        return False
    clamped = max(0, min(65535, int(value)))
    return wave.waveOutSetVolume(None, wintypes.DWORD(clamped)) == 0


# ---------------------------------------------------------------- window control
def show_window(hwnd: int, cmd: int) -> None:
    user32.ShowWindow(wintypes.HWND(hwnd), cmd)


def minimise(hwnd: int) -> None:
    show_window(hwnd, SW_MINIMIZE)


def maximise(hwnd: int) -> None:
    show_window(hwnd, SW_MAXIMIZE)


def restore(hwnd: int) -> None:
    show_window(hwnd, SW_RESTORE)


def focus_window(hwnd: int) -> None:
    """Bring a window forward. SetForegroundWindow is rate-limited by Windows when the
    calling process does not own the foreground window, so fall back to the
    AttachThreadInput trick used by the Nucleus work."""
    if user32.GetForegroundWindow() == hwnd:
        return
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(wintypes.HWND(fg), None) if fg else 0
    cur_thread = kernel32.GetCurrentThreadId()
    attached = False
    if fg_thread and fg_thread != cur_thread:
        attached = bool(user32.AttachThreadInput(fg_thread, cur_thread, True))
    try:
        user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(fg_thread, cur_thread, False)


def set_dpi_aware():
    """Physical pixels everywhere. Without this, at anything above 100% display scaling
    GetSystemMetrics hands back virtualised numbers and every cursor/gaze coordinate is wrong."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # per-monitor v2
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def root_hwnd(hwnd: int) -> int:
    """Top-level window for a child handle (e.g. Tk's winfo_id())."""
    return int(user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT))


def position_window(hwnd: int, x: int, y: int, width: int, height: int, topmost: bool = True):
    """Place a window exactly, including on monitors left of the primary (negative x). Tk's own
    geometry strings cannot express a negative origin - a leading '-' means 'from the right edge'."""
    flags = SWP_SHOWWINDOW | SWP_NOACTIVATE
    insert_after = wintypes.HWND(HWND_TOPMOST) if topmost else wintypes.HWND(HWND_NOTOPMOST)
    user32.SetWindowPos(wintypes.HWND(int(hwnd)), insert_after, int(x), int(y),
                        int(width), int(height), flags)


def own_process_id() -> int:
    return os.getpid()


def key_pressed(vk: int) -> bool:
    """Physical key state, for the panic key. GetAsyncKeyState returns a SHORT with the
    high bit set while the key is down."""
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


VK_END = 0x23


def panic_pressed() -> bool:
    return key_pressed(VK_END)
