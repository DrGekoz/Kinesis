"""Probe what hardware detail is actually available: camera device names, per-monitor EDID
(physical size, model, serial) and how GDI monitor indices map to monitor hardware IDs."""
import ctypes
import re
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kinesis import winapi as w

user32 = ctypes.windll.user32


class DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


def enum_adapters():
    out = []
    i = 0
    while True:
        dd = DISPLAY_DEVICE()
        dd.cb = ctypes.sizeof(DISPLAY_DEVICE)
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        out.append(dd)
        i += 1
    return out


print("=== adapters and their monitors ===")
for a in enum_adapters():
    attached = bool(a.StateFlags & 0x1)      # DISPLAY_DEVICE_ATTACHED_TO_DESKTOP
    if not attached:
        continue
    mon = DISPLAY_DEVICE()
    mon.cb = ctypes.sizeof(DISPLAY_DEVICE)
    user32.EnumDisplayDevicesW(a.DeviceName, 0, ctypes.byref(mon), 0)
    print(f"  {a.DeviceName:14} adapter={a.DeviceString[:28]:30} monitor={mon.DeviceString[:26]:28} id={mon.DeviceID}")

print("\n=== monitors as Kinesis sees them ===")
for i, m in enumerate(w.enumerate_monitors()):
    print(f"  {i + 1}. {m}")

print("\n=== EDID via registry ===")
import winreg

base = r"SYSTEM\CurrentControlSet\Enum\DISPLAY"
try:
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
        n_sub = winreg.QueryInfoKey(k)[0]
        for i in range(n_sub):
            vendor = winreg.EnumKey(k, i)
            with winreg.OpenKey(k, vendor) as vk:
                n_inst = winreg.QueryInfoKey(vk)[0]
                for j in range(n_inst):
                    inst = winreg.EnumKey(vk, j)
                    try:
                        with winreg.OpenKey(vk, inst + r"\Device Parameters") as dk:
                            edid, _ = winreg.QueryValueEx(dk, "EDID")
                    except OSError:
                        continue
                    if len(edid) < 128:
                        continue
                    mfg = (edid[8] << 8 | edid[9])
                    letters = "".join(chr(((mfg >> s) & 0x1F) + 64) for s in (10, 5, 0))
                    prod = f"{edid[11]:02X}{edid[10]:02X}"
                    h_cm, v_cm = edid[21], edid[22]
                    # preferred detailed timing descriptor: image size in mm (bytes 12,13 of DTD)
                    dtd = edid[54:72]
                    h_mm = dtd[12] | ((dtd[13] >> 4) << 8)
                    v_mm = dtd[13] & 0x0F | ((dtd[14] >> 4) << 8)
                    h_px = dtd[2] | ((dtd[4] >> 4) << 8)
                    v_px = dtd[5] | ((dtd[6] >> 4) << 8)
                    print(f"  {vendor}\\{inst[:38]:40} mfg={letters} prod={prod} "
                          f"basic={h_cm}x{v_cm}cm dtd={h_mm}x{v_mm}mm {h_px}x{v_px}px")
except OSError as e:
    print(f"  registry read failed: {e}")

print("\n=== cameras via ffmpeg dshow ===")
try:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                       capture_output=True, text=True, timeout=30)
    for line in (r.stderr or "").splitlines():
        if "(video)" in line or "(audio)" in line:
            print("  " + line.strip()[:130])
except Exception as e:
    print(f"  ffmpeg listing failed: {e}")

print("\n=== camera names via PnP ===")
try:
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPClass -eq 'Camera' -or $_.PNPClass -eq 'Image' } | Select-Object -ExpandProperty Name"],
                       capture_output=True, text=True, timeout=60)
    for line in (r.stdout or "").splitlines():
        if line.strip():
            print("  " + line.strip()[:120])
except Exception as e:
    print(f"  pnp query failed: {e}")
