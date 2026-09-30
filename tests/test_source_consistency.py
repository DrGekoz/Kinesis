"""Guards against attribute slips on the shared dataclasses.

`calibrate_gaze.py` called `monitor.index`, which does not exist, and crashed the gaze wizard the
first time it ran. Nothing caught it because the geometry tests built monitors as
`SimpleNamespace` fakes, so a wrong attribute name still passed.

Two guards replace that hole:
  * the field set of Monitor is asserted explicitly, so a rename fails here;
  * the real dataclass is exercised through the helpers that consume it, so an attribute the rest
    of the code assumes is actually read out of a real instance.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kinesis import geometry as g                                        # noqa: E402
from kinesis import winapi as w                                          # noqa: E402
from kinesis.config import Config                                        # noqa: E402


def real_monitor(device, left, top, width=1920, height=1080, primary=False):
    return w.Monitor(handle=0, device=device, left=left, top=top,
                     right=left + width, bottom=top + height, primary=primary,
                     work=(left, top, left + width, top + height))


def test_monitor_exposes_the_fields_the_code_uses():
    fields = set(getattr(w.Monitor, "__annotations__", {})) | set(dir(w.Monitor))
    for name in ("handle", "device", "left", "top", "right", "bottom", "primary", "work",
                 "width", "height", "centre", "contains"):
        assert name in fields, f"Monitor lost {name}"


def test_helpers_read_a_real_monitor_without_slips():
    """The paths that actually consume Monitor: hit-testing, physical layout, geometry."""
    monitors = [real_monitor("\\\\.\\DISPLAY1", 0, 0), real_monitor("\\\\.\\DISPLAY2", 1920, 0)]
    assert w.monitor_at(100, 100, monitors).device == "\\\\.\\DISPLAY1"
    assert w.monitor_at(2000, 100, monitors).device == "\\\\.\\DISPLAY2"
    assert w.monitor_at(-500, 100, monitors) is None      # off the array entirely

    hardware = [g.MonitorHardware(model="A", mm_w=600, mm_h=340),
                g.MonitorHardware(model="B", mm_w=600, mm_h=340)]
    layout = g.physical_layout(monitors, hardware, bezel_mm=10.0)
    assert layout.panels[0].px_w == 1920
    assert layout.total_width_mm == 1210.0

    cfg = Config()
    cfg.set("camera_name", "HD Pro Webcam C920")
    geo = g.build_geometry(cfg, monitors, 640, 480, distance_mm=600.0, distance_source="test")
    assert len(geo.layout.panels) == 2
    assert geo.camera.focal_px > 0
    assert geo.panel_for_angle(0.0) in (0, 1)
