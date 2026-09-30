"""Desk geometry: what screens and camera you actually have, and how far away you are.

A webcam sees you, not your desk. This module gives Kinesis a model of the desk so the gaze
calibration is not working blind:

  * which camera is in use, and its field of view (a webcam's FoV is not exposed by Windows, so
    it comes from a table of known models, an explicit override, or an empirical measurement);
  * which monitors are attached, what they are, and their *physical* size, read from each
    monitor's EDID in the registry (byte 21/22 for the basic size, the preferred timing
    descriptor for millimetres), keyed by the vendor+product code reported per screen;
  * how they are physically arranged - a row of active areas with a bezel gap between them;
  * how far away your face is, from the apparent spacing of your eyes and the camera's focal
    length in pixels.

Everything here is pure geometry and tolerant of missing data: an unknown monitor size degrades
to an estimate, an unknown camera FoV to a documented default, and nothing raises.
"""
from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import winapi as w

# ---------------------------------------------------------------- camera field of view
# Webcam FoV is not a Windows property, so it is matched by device name. Values are the
# manufacturer's *diagonal* figure; horizontal/vertical follow the capture aspect ratio.
FOV_TABLE: Tuple[Tuple[str, float], ...] = (
    ("c920", 78.0), ("c922", 78.0), ("c925", 90.0), ("brio", 90.0), ("c930", 90.0),
    ("c270", 60.0), ("c310", 60.0), ("c615", 74.0), ("c170", 62.0),
    ("insta360", 82.0), ("k800", 78.0), ("obs virtual camera", 60.0),
    ("meta quest", 90.0), ("surface", 68.0),
)
DEFAULT_DIAGONAL_FOV = 68.0          # a middling USB webcam; documented, overridable

# Average adult anthropometry, used as the monocular scale reference.
EYE_CORNER_SPAN_MM = 90.0            # outer eye corner to outer eye corner
IPD_MM = 63.0                        # pupil to pupil
PALM_LENGTH_MM = 90.0                # wrist to middle knuckle, for hand distance

# A plausible PPI per resolution, used when EDID reports no size at all.
FALLBACK_INCH: Dict[int, float] = {7680: 32.0, 3840: 32.0, 2560: 27.0, 1920: 24.0, 1680: 22.0,
                                   1600: 22.0, 1440: 20.0, 1366: 19.0, 1280: 19.0, 1024: 15.0}


@dataclass
class CameraInfo:
    index: int = 0
    name: str = ""
    width: int = 0
    height: int = 0
    diagonal_fov_deg: float = DEFAULT_DIAGONAL_FOV
    fov_source: str = "default"

    @property
    def aspect(self) -> float:
        return (self.width / self.height) if self.height else 1.333

    @property
    def horizontal_fov_deg(self) -> float:
        """Horizontal FoV for the capture aspect, from a fixed diagonal FoV."""
        a = self.aspect
        half = math.radians(self.diagonal_fov_deg / 2.0)
        return 2.0 * math.degrees(math.atan(math.tan(half) * a / math.sqrt(1.0 + a * a)))

    @property
    def vertical_fov_deg(self) -> float:
        a = self.aspect
        half = math.radians(self.diagonal_fov_deg / 2.0)
        return 2.0 * math.degrees(math.atan(math.tan(half) / math.sqrt(1.0 + a * a)))

    @property
    def focal_px(self) -> float:
        """Pinhole focal length in pixels for this frame size."""
        if not self.width or not self.horizontal_fov_deg:
            return 0.0
        return (self.width / 2.0) / math.tan(math.radians(self.horizontal_fov_deg / 2.0))

    def describe(self) -> str:
        if not self.name:
            return f"camera {self.index} (name unknown)"
        return (f"camera {self.index}: {self.name}  {self.width}x{self.height}  "
                f"hFoV {self.horizontal_fov_deg:.1f} deg ({self.fov_source}), "
                f"focal {self.focal_px:.0f} px")


def _match_fov(name: str) -> Tuple[float, str]:
    low = (name or "").lower()
    for pattern, fov in FOV_TABLE:
        if pattern in low:
            return fov, f"table:{pattern}"
    return DEFAULT_DIAGONAL_FOV, "default"


def enumerate_cameras() -> List[str]:
    """DirectShow video device names, in enumeration order.

    Order matters: OpenCV's DSHOW index follows this enumeration, which is how index 0 gets a name.
    Uses ffmpeg because Windows exposes no API for it that does not need a DirectShow COM client.
    """
    try:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-list_devices", "true",
                               "-f", "dshow", "-i", "dummy"],
                              capture_output=True, text=True, timeout=25)
    except Exception:
        return []
    names: List[str] = []
    for line in (proc.stderr or "").splitlines():
        if "(video)" not in line:
            continue
        parts = line.split('"')
        if len(parts) >= 2:
            names.append(parts[1])
    return names


def camera_info(index: int, width: int, height: int, names: Optional[Sequence[str]] = None,
                override_name: str = "", override_fov: float = 0.0) -> CameraInfo:
    names = list(names) if names is not None else enumerate_cameras()
    name = override_name or ""
    if not name:
        if 0 <= index < len(names):
            name = names[index]
        elif len(names) == 1:
            name = names[0]
    if override_fov:
        return CameraInfo(index=index, name=name or f"camera {index}", width=width, height=height,
                          diagonal_fov_deg=float(override_fov), fov_source="config")
    fov, source = _match_fov(name)
    return CameraInfo(index=index, name=name or f"camera {index}", width=width, height=height,
                      diagonal_fov_deg=fov, fov_source=source)


# ---------------------------------------------------------------- monitor hardware (EDID)
@dataclass
class MonitorHardware:
    gdi_device: str = ""
    hardware_id: str = ""            # MONITOR\LEN66BF\{...}\0002
    vendor: str = ""
    product: str = ""
    model: str = ""                  # from EnumDisplayDevices, or the EDID name descriptor
    edid_name: str = ""
    serial: str = ""
    mm_w: float = 0.0
    mm_h: float = 0.0
    size_source: str = "unknown"     # edid-basic | edid-timing | estimate | unknown


def parse_edid(edid: bytes) -> Dict[str, object]:
    """Pull the vendor, product, name, serial and physical size out of an EDID block."""
    out: Dict[str, object] = {}
    if not edid or len(edid) < 128:
        return out
    code = (edid[8] << 8) | edid[9]
    out["vendor"] = "".join(chr(((code >> s) & 0x1F) + 64) for s in (10, 5, 0)).strip()
    out["product"] = f"{edid[11]:02X}{edid[10]:02X}"
    out["serial_number"] = int.from_bytes(edid[12:16], "little")
    out["basic_mm"] = (float(edid[21]) * 10.0, float(edid[22]) * 10.0)

    # preferred timing descriptor: 18 bytes at offset 54
    dtd = edid[54:72]
    if len(dtd) == 18 and (dtd[0] or dtd[1]):
        h_px = dtd[2] | ((dtd[4] & 0xF0) << 4)
        v_px = dtd[5] | ((dtd[6] & 0xF0) << 4)
        h_mm = dtd[12] | ((dtd[14] & 0xF0) << 4)
        v_mm = dtd[13] | ((dtd[14] & 0x0F) << 8)
        out["timing_px"] = (h_px, v_px)
        out["timing_mm"] = (float(h_mm), float(v_mm))

    for off in (72, 90, 108, 126):        # descriptor slots 2..4 (and the 4th for safety)
        if off + 18 > len(edid):
            break
        blk = edid[off:off + 18]
        if blk[0:3] == b"\x00\x00\x00" and blk[3] in (0xFC, 0xFF):
            text = blk[5:18].decode("ascii", "ignore").replace("\n", "").replace("\x00", "").strip()
            if blk[3] == 0xFC and text:
                out["edid_name"] = text
            elif blk[3] == 0xFF and text:
                out["edid_serial"] = text
    return out


def read_monitor_edid(hardware_id: str) -> Optional[bytes]:
    """Read the EDID blob for a monitor hardware id from the registry."""
    import winreg
    if not hardware_id:
        return None
    parts = hardware_id.split("\\")
    if len(parts) < 2 or parts[0].upper() != "MONITOR":
        return None
    vendor = parts[1]
    base = rf"SYSTEM\CurrentControlSet\Enum\DISPLAY\{vendor}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as key:
            count = winreg.QueryInfoKey(key)[0]
            for i in range(count):
                instance = winreg.EnumKey(key, i)
                try:
                    with winreg.OpenKey(key, instance + r"\Device Parameters") as dk:
                        edid, _ = winreg.QueryValueEx(dk, "EDID")
                        if edid and len(edid) >= 128:
                            return bytes(edid)
                except OSError:
                    continue
    except OSError:
        return None
    return None


def _fallback_mm(width_px: int, height_px: int) -> Tuple[float, float]:
    inch = FALLBACK_INCH.get(width_px)
    if not inch:
        inch = max(19.0, width_px / 96.0)
    mm_w = inch * 25.4
    aspect = (height_px / width_px) if width_px else 0.5625
    return mm_w, mm_w * aspect


def monitor_hardware(monitors: Sequence[w.Monitor]) -> List[MonitorHardware]:
    """Per-monitor hardware detail, matched to the same left-to-right order Kinesis indexes by."""
    devices = w.display_devices()          # [(gdi_name, adapter, monitor_name, monitor_id)]
    by_gdi = {d[0].upper(): d for d in devices}
    out: List[MonitorHardware] = []
    for m in monitors:
        hw = MonitorHardware(gdi_device=m.device)
        rec = by_gdi.get(m.device.upper())
        if rec:
            hw.model = rec[2]
            hw.hardware_id = rec[3]
        edid = read_monitor_edid(hw.hardware_id)
        info = parse_edid(edid) if edid else {}
        if info:
            hw.vendor = str(info.get("vendor", ""))
            hw.product = str(info.get("product", ""))
            hw.edid_name = str(info.get("edid_name", ""))
            hw.serial = str(info.get("edid_serial", "") or info.get("serial_number", ""))
        basic = info.get("basic_mm")
        timing = info.get("timing_mm")
        if isinstance(basic, tuple) and basic[0] > 50 and basic[1] > 50:
            hw.mm_w, hw.mm_h, hw.size_source = basic[0], basic[1], "edid-basic"
        elif isinstance(timing, tuple) and timing[0] > 50 and timing[1] > 50:
            hw.mm_w, hw.mm_h, hw.size_source = timing[0], timing[1], "edid-timing"
        else:
            hw.mm_w, hw.mm_h = _fallback_mm(m.width, m.height)
            hw.size_source = "estimate"
        # Windows reports "Generic PnP Monitor" for plenty of screens; the EDID name is better
        if hw.edid_name and (not hw.model or "generic" in hw.model.lower()):
            hw.model = hw.edid_name
        out.append(hw)
    return out


# ---------------------------------------------------------------- physical layout
@dataclass
class Panel:
    index: int = 0
    device: str = ""
    model: str = ""
    x_mm: float = 0.0                # left edge, physical, relative to the desk's left edge
    width_mm: float = 0.0
    height_mm: float = 0.0
    top_mm: float = 0.0
    px_w: int = 0
    px_h: int = 0
    size_source: str = "unknown"

    @property
    def centre_mm(self) -> float:
        return self.x_mm + self.width_mm / 2.0

    @property
    def ppi(self) -> float:
        if self.width_mm <= 0 or not self.px_w:
            return 0.0
        diag_mm = math.hypot(self.width_mm, self.height_mm)
        return (math.hypot(self.px_w, self.px_h) / (diag_mm / 25.4)) if diag_mm else 0.0


@dataclass
class DeskLayout:
    panels: List[Panel] = field(default_factory=list)
    bezel_mm: float = 10.0

    @property
    def total_width_mm(self) -> float:
        return self.panels[-1].x_mm + self.panels[-1].width_mm if self.panels else 0.0

    @property
    def vertical_misalignment_mm(self) -> float:
        if not self.panels:
            return 0.0
        tops = [p.top_mm for p in self.panels]
        return max(tops) - min(tops)


def physical_layout(monitors: Sequence[w.Monitor], hardware: Sequence[MonitorHardware],
                    bezel_mm: float = 10.0) -> DeskLayout:
    """Lay the screens out in physical millimetres, left to right.

    Windows snaps monitors edge-to-edge in the virtual desktop, so pixel positions say nothing
    about bezels. The layout therefore assumes the active areas are adjacent with `bezel_mm`
    between them, and keeps the pixel-derived vertical offsets as the best available evidence of
    how they are aligned.
    """
    layout = DeskLayout(bezel_mm=bezel_mm)
    if not monitors:
        return layout
    base_top = min(m.top for m in monitors)
    ordered = list(monitors)
    x = 0.0
    for i, m in enumerate(ordered):
        hw = hardware[i] if i < len(hardware) else MonitorHardware()
        mm_w = hw.mm_w or _fallback_mm(m.width, m.height)[0]
        mm_h = hw.mm_h or _fallback_mm(m.width, m.height)[1]
        mm_per_px = (mm_w / m.width) if m.width else 0.03
        layout.panels.append(Panel(
            index=i, device=m.device,
            model=hw.model or hw.edid_name or "unknown",
            x_mm=x, width_mm=mm_w, height_mm=mm_h,
            top_mm=(m.top - base_top) * mm_per_px,
            px_w=m.width, px_h=m.height, size_source=hw.size_source))
        x += mm_w + bezel_mm
    return layout


# ---------------------------------------------------------------- distance and angles
def estimate_distance_mm(span_px: float, camera: CameraInfo, span_mm: float) -> float:
    """Monocular distance from a known physical span and its apparent size in pixels.

    pinhole: span_px = focal_px * span_mm / distance  =>  distance = focal_px * span_mm / span_px
    """
    if span_px <= 1.0 or span_mm <= 0 or camera.focal_px <= 0:
        return 0.0
    return camera.focal_px * span_mm / span_px


def angular_span_deg(panel: Panel, eye_x_mm: float, distance_mm: float) -> Tuple[float, float]:
    """Horizontal angles (degrees, negative = left) to a panel's left and right edges."""
    if distance_mm <= 0:
        return 0.0, 0.0
    left = math.degrees(math.atan2(panel.x_mm - eye_x_mm, distance_mm))
    right = math.degrees(math.atan2(panel.x_mm + panel.width_mm - eye_x_mm, distance_mm))
    return left, right


def centre_angle_deg(panel: Panel, eye_x_mm: float, distance_mm: float) -> float:
    if distance_mm <= 0:
        return 0.0
    return math.degrees(math.atan2(panel.centre_mm - eye_x_mm, distance_mm))


def monitor_for_angle_deg(layout: DeskLayout, angle_deg: float, eye_x_mm: float,
                          distance_mm: float) -> Optional[int]:
    """Which panel does a gaze/pointing angle land on? None if it falls in the gaps."""
    for panel in layout.panels:
        left, right = angular_span_deg(panel, eye_x_mm, distance_mm)
        if left <= angle_deg <= right:
            return panel.index
    # outside every panel: snap to the nearest edge rather than reporting nothing
    if not layout.panels or distance_mm <= 0:
        return None
    spans = [angular_span_deg(p, eye_x_mm, distance_mm) for p in layout.panels]
    if angle_deg < spans[0][0]:
        return 0
    if angle_deg > spans[-1][1]:
        return len(layout.panels) - 1
    return None


@dataclass
class DeskGeometry:
    """Everything Kinesis knows about the physical desk."""
    camera: CameraInfo = field(default_factory=CameraInfo)
    layout: DeskLayout = field(default_factory=DeskLayout)
    hardware: List[MonitorHardware] = field(default_factory=list)
    eye_offset_mm: float = 0.0        # eye position, relative to the desk's left edge
    distance_mm: float = 0.0
    distance_source: str = "none"
    notes: List[str] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return bool(self.layout.panels) and self.distance_mm > 0

    def panel_for_angle(self, angle_deg: float) -> Optional[int]:
        return monitor_for_angle_deg(self.layout, angle_deg, self.eye_offset_mm, self.distance_mm)

    def angular_coverage_deg(self) -> Tuple[float, float]:
        if not self.layout.panels or self.distance_mm <= 0:
            return 0.0, 0.0
        left, _ = angular_span_deg(self.layout.panels[0], self.eye_offset_mm, self.distance_mm)
        _, right = angular_span_deg(self.layout.panels[-1], self.eye_offset_mm, self.distance_mm)
        return left, right

    def separation_deg(self) -> List[float]:
        """Angle between adjacent panel centres - how distinguishable the screens are."""
        if self.distance_mm <= 0:
            return []
        centres = [centre_angle_deg(p, self.eye_offset_mm, self.distance_mm)
                   for p in self.layout.panels]
        return [abs(centres[i + 1] - centres[i]) for i in range(len(centres) - 1)]

    def report(self) -> str:
        lines = ["desk geometry:"]
        lines.append("  " + self.camera.describe())
        if not self.layout.panels:
            lines.append("  no monitors detected")
            return "\n".join(lines)
        lines.append(f"  {len(self.layout.panels)} monitors, "
                     f"{self.layout.total_width_mm / 10:.0f} cm of active area "
                     f"(+{self.layout.bezel_mm:.0f} mm bezel allowance between them)")
        for p in self.layout.panels:
            size = f"{p.width_mm / 10:.1f}x{p.height_mm / 10:.1f} cm"
            diag_in = math.hypot(p.width_mm, p.height_mm) / 25.4
            ppi = f"{p.ppi:.0f} ppi" if p.ppi else "ppi unknown"
            lines.append(f"   {p.index + 1}. {p.model[:26]:28} {size:14} {diag_in:4.1f}\"  {ppi:11} "
                         f"{p.px_w}x{p.px_h} ({p.size_source})")
        if self.distance_mm > 0:
            lines.append(f"  your eyes: {self.distance_mm:.0f} mm from the screen "
                         f"({self.distance_mm / 10:.0f} cm), {self.distance_source}")
        lo, hi = self.angular_coverage_deg()
        if lo or hi:
            lines.append(f"  angular span of the array from your seat: {lo:+.1f} to {hi:+.1f} deg")
            seps = self.separation_deg()
            if seps:
                lines.append("  angle between adjacent screen centres: "
                             + ", ".join(f"{s:.1f}" for s in seps) + " deg")
        mis = self.layout.vertical_misalignment_mm
        lines.append(f"  vertical alignment: {'aligned' if mis < 8 else f'{mis:.0f} mm out'}")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


def build_geometry(cfg, monitors: Sequence[w.Monitor], camera_width: int, camera_height: int,
                   distance_mm: float = 0.0, distance_source: str = "none") -> DeskGeometry:
    """Assemble the desk model from config plus live hardware queries."""
    hw = monitor_hardware(monitors)
    layout = physical_layout(monitors, hw, bezel_mm=float(cfg.get("bezel_mm", 10.0)))
    cam = camera_info(int(cfg["camera_index"]), camera_width, camera_height,
                      override_name=str(cfg.get("camera_name", "") or ""),
                      override_fov=float(cfg.get("camera_fov_deg", 0.0) or 0.0))
    geo = DeskGeometry(camera=cam, layout=layout, hardware=hw,
                       distance_mm=distance_mm, distance_source=distance_source)
    # sit in front of the primary screen when there is one, else the middle of the array
    primary = next((p for p in layout.panels
                    if any(m.primary for m in monitors if m.device == p.device)), None)
    anchor = primary or (layout.panels[len(layout.panels) // 2] if layout.panels else None)
    geo.eye_offset_mm = anchor.centre_mm if anchor else 0.0
    unknown = [p.index + 1 for p in layout.panels if p.size_source in ("estimate", "unknown")]
    if unknown:
        geo.notes.append("physical size guessed for screen(s) "
                         + ", ".join(str(i) for i in unknown)
                         + " (no usable EDID size) - set monitor_mm_overrides if that matters")
    if geo.camera.fov_source == "default":
        geo.notes.append("camera FoV unknown, assuming "
                         f"{geo.camera.diagonal_fov_deg:.0f} deg diagonal "
                         "- set camera_fov_deg if you know it")
    seps = geo.separation_deg()
    if seps and min(seps) < 8.0:
        geo.notes.append(f"screens are only {min(seps):.1f} deg apart from your seat, which is "
                         "tight for gaze discrimination - sitting closer helps")
    return geo
