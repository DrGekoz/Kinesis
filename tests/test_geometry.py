"""Geometry tests: EDID parsing, physical layout, distance, angles, and the drift gating.

Deterministic and hardware-free. The EDID block below is synthetic but laid out to the real spec,
so the parser is tested against byte offsets rather than against whatever this machine happens to
be plugged into.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis import geometry as g                                      # noqa: E402
from kinesis.config import Config                                      # noqa: E402
from kinesis.gaze import GazeEngine, _LandmarkTap                       # noqa: E402
from kinesis.winapi import Monitor as RealMonitor                       # noqa: E402


# ---------------------------------------------------------------- synthetic EDID
def make_edid(vendor="LEN", product=(0xBF, 0x66), name="L27i-30", basic=(60, 34),
              px=(1920, 1080), mm=(598, 336)):
    e = bytearray(128)
    v = [ord(c) - 64 for c in vendor]
    code = (v[0] << 10) | (v[1] << 5) | v[2]
    e[8], e[9] = (code >> 8) & 0xFF, code & 0xFF
    e[10], e[11] = product
    e[21], e[22] = basic
    dtd = bytearray(18)
    dtd[0], dtd[1] = 0x8C, 0x01                       # non-zero pixel clock
    dtd[2] = px[0] & 0xFF
    dtd[4] = ((px[0] >> 8) & 0x0F) << 4
    dtd[5] = px[1] & 0xFF
    dtd[6] = ((px[1] >> 8) & 0x0F) << 4
    dtd[12] = mm[0] & 0xFF
    dtd[14] = ((mm[0] >> 8) & 0x0F) << 4
    dtd[13] = mm[1] & 0xFF
    dtd[14] |= (mm[1] >> 8) & 0x0F
    e[54:72] = dtd
    blk = bytearray(18)
    blk[3] = 0xFC
    blk[5:5 + len(name)] = name.encode()
    e[72:90] = blk
    return bytes(e)


def test_parse_edid_reads_vendor_product_name_and_sizes():
    info = g.parse_edid(make_edid())
    assert info["vendor"] == "LEN"
    assert info["product"] == "66BF"
    assert info["edid_name"] == "L27i-30"
    assert info["basic_mm"] == (600.0, 340.0)
    assert info["timing_px"] == (1920, 1080)
    assert info["timing_mm"] == (598.0, 336.0)


def test_parse_edid_rejects_rubbish():
    assert g.parse_edid(b"") == {}
    assert g.parse_edid(b"\x00" * 40) == {}


# ---------------------------------------------------------------- camera
def test_camera_fov_from_table_and_aspect():
    cam = g.camera_info(0, 640, 480, names=["HD Pro Webcam C920"])
    assert cam.diagonal_fov_deg == 78.0
    assert cam.fov_source == "table:c920"
    # 78 deg diagonal on a 4:3 frame is ~66 deg across, not 78
    assert 64.0 < cam.horizontal_fov_deg < 68.0
    wide = g.camera_info(0, 1280, 720, names=["HD Pro Webcam C920"])
    assert 69.0 < wide.horizontal_fov_deg < 72.0
    assert wide.focal_px > cam.focal_px


def test_camera_fov_override_wins():
    cam = g.camera_info(0, 640, 480, names=["HD Pro Webcam C920"], override_fov=120.0)
    assert cam.diagonal_fov_deg == 120.0
    assert cam.fov_source == "config"


def test_unknown_camera_falls_back_and_says_so():
    cam = g.camera_info(0, 640, 480, names=["Some Random Cam"])
    assert cam.fov_source == "default"
    assert cam.diagonal_fov_deg == g.DEFAULT_DIAGONAL_FOV


def test_camera_index_selects_the_right_name():
    names = ["HD Pro Webcam C920", "Meta Quest 3", "OBS Virtual Camera"]
    assert g.camera_info(2, 640, 480, names=names).name == "OBS Virtual Camera"


# ---------------------------------------------------------------- distance
def test_distance_round_trip():
    cam = g.camera_info(0, 640, 480, names=["HD Pro Webcam C920"])
    for true_mm in (400.0, 600.0, 900.0):
        span_px = cam.focal_px * 90.0 / true_mm          # what the camera would see
        assert g.estimate_distance_mm(span_px, cam, 90.0) == pytest.approx(true_mm, rel=1e-6)


def test_distance_needs_a_scale():
    cam = g.camera_info(0, 640, 480, names=["HD Pro Webcam C920"])
    assert g.estimate_distance_mm(0.0, cam, 90.0) == 0.0
    assert g.estimate_distance_mm(50.0, cam, 0.0) == 0.0


# ---------------------------------------------------------------- layout
def _monitor(device, left, top, w, h, primary=False):
    """A real Monitor, not a stand-in: using SimpleNamespace here is what let `monitor.index`
    (an attribute that does not exist) survive into a wizard that crashed on its first run."""
    return RealMonitor(handle=0, device=device, left=left, top=top, right=left + w,
                       bottom=top + h, primary=primary, work=(left, top, left + w, top + h))


def _hw(mm_w, mm_h, model="X", source="edid-basic"):
    return g.MonitorHardware(model=model, mm_w=mm_w, mm_h=mm_h, size_source=source)


def test_layout_places_panels_with_bezels():
    monitors = [_monitor("\\\\.\\DISPLAY1", 0, 0, 1920, 1080),
                _monitor("\\\\.\\DISPLAY2", 1920, 0, 1920, 1080)]
    layout = g.physical_layout(monitors, [_hw(600, 340), _hw(930, 530)], bezel_mm=10.0)
    assert layout.panels[0].x_mm == 0.0
    assert layout.panels[1].x_mm == 610.0            # 600 + 10 mm bezel
    assert layout.total_width_mm == 1540.0
    assert layout.vertical_misalignment_mm == 0.0


def test_layout_notices_vertical_misalignment():
    monitors = [_monitor("a", 0, 0, 1920, 1080), _monitor("b", 1920, 120, 1920, 1080)]
    layout = g.physical_layout(monitors, [_hw(600, 340), _hw(600, 340)], bezel_mm=0.0)
    # 120 px of offset on a 600 mm wide 1920 px panel
    assert layout.panels[1].top_mm == pytest.approx(120 * 600 / 1920, rel=0.01)
    assert layout.vertical_misalignment_mm > 8.0


def test_ppi_is_physical_not_guessed():
    layout = g.physical_layout([_monitor("a", 0, 0, 1920, 1080)], [_hw(598, 336)])
    assert 81.0 < layout.panels[0].ppi < 82.0


def test_fallback_size_when_edid_has_nothing():
    monitors = [_monitor("a", 0, 0, 1920, 1080)]
    hw = g.monitor_hardware(monitors) if False else g.MonitorHardware()
    mm_w, mm_h = g._fallback_mm(1920, 1080)
    assert 500 < mm_w < 700 and 280 < mm_h < 400


# ---------------------------------------------------------------- angles
def test_angular_span_and_centre_of_a_centred_panel():
    panel = g.Panel(index=0, x_mm=0, width_mm=600, height_mm=340)
    lo, hi = g.angular_span_deg(panel, 300.0, 600.0)      # eye 300 mm in front of centre
    assert lo == pytest.approx(-26.57, abs=0.05)
    assert hi == pytest.approx(26.57, abs=0.05)
    assert g.centre_angle_deg(panel, 300.0, 600.0) == pytest.approx(0.0, abs=0.01)


def test_panel_for_angle_picks_the_screen_you_are_pointing_at():
    layout = g.DeskLayout(panels=[g.Panel(index=0, x_mm=0, width_mm=600, height_mm=340),
                                  g.Panel(index=1, x_mm=610, width_mm=600, height_mm=340),
                                  g.Panel(index=2, x_mm=1220, width_mm=600, height_mm=340)],
                          bezel_mm=10.0)
    eye, dist = 910.0, 600.0                              # seated at the middle screen
    for idx, angle in ((0, -45.0), (1, 0.0), (2, 45.0)):
        assert g.monitor_for_angle_deg(layout, angle, eye, dist) == idx
    assert g.monitor_for_angle_deg(layout, -89.0, eye, dist) == 0     # way off to the left
    assert g.monitor_for_angle_deg(layout, 89.0, eye, dist) == 2


def test_example_known_layout_gives_sane_separations():
    """Four 1920x1080 screens, 27/24/27/27 inch, seated at the second: the middle two are close
    in angle and the outer one is far, which is exactly the case a linear model gets wrong."""
    monitors = [_monitor("a", 0, 0, 1920, 1080), _monitor("b", 1920, 0, 1920, 1080),
                _monitor("c", 3840, 0, 1920, 1080), _monitor("d", 5760, 0, 1920, 1080)]
    hw = [_hw(930, 530), _hw(600, 340), _hw(600, 340), _hw(600, 340)]
    layout = g.physical_layout(monitors, hw, bezel_mm=10.0)
    geo = g.DeskGeometry(camera=g.camera_info(0, 640, 480, names=["HD Pro Webcam C920"]),
                         layout=layout, hardware=hw, distance_mm=700.0)
    geo.eye_offset_mm = layout.panels[1].centre_mm
    seps = geo.separation_deg()
    assert len(seps) == 3
    assert all(s > 8.0 for s in seps)                     # distinguishable at that distance
    lo, hi = geo.angular_coverage_deg()
    assert lo < -40.0 and hi > 40.0                       # a wide array, as expected
    assert geo.panel_for_angle(g.centre_angle_deg(layout.panels[3], geo.eye_offset_mm, 700.0)) == 3


def test_tight_seating_gets_flagged():
    """Same array from much further back: the screens crowd into each other's angles, and the
    report says so, because that is when gaze targeting starts picking the wrong window."""
    monitors = [_monitor("a", 0, 0, 1920, 1080), _monitor("b", 1920, 0, 1920, 1080),
                _monitor("c", 3840, 0, 1920, 1080), _monitor("d", 5760, 0, 1920, 1080)]
    hw = [_hw(600, 340)] * 4
    layout = g.physical_layout(monitors, hw, bezel_mm=10.0)
    geo = g.DeskGeometry(layout=layout, distance_mm=4500.0)
    geo.eye_offset_mm = layout.panels[1].centre_mm
    assert min(geo.separation_deg()) < 8.0
    assert geo.report().count("\n") > 3


# ---------------------------------------------------------------- gaze + tap
def _cfg(**kw):
    c = Config()
    for k, v in kw.items():
        c.set(k, v)
    return c


def test_landmark_tap_records_and_delegates():
    class Fake:
        def __init__(self):
            self.calls = 0

        def detect_for_video(self, image, ts):
            self.calls += 1
            return SimpleNamespace(face_landmarks=[[1]])

        def close(self):
            return "closed"

    tap = _LandmarkTap(Fake())
    assert tap.last is None
    res = tap.detect_for_video("img", 123)
    assert tap.last is res and res.face_landmarks == [[1]]
    assert tap.close() == "closed"            # everything else delegates


def test_eye_span_turns_into_a_distance():
    """The whole point of the tap: eye corners in pixels -> how far away the face is."""
    engine = GazeEngine(_cfg(gaze_enabled=False))
    cam = g.camera_info(0, 640, 480, names=["HD Pro Webcam C920"])
    engine.set_camera(cam)
    landmarks = [SimpleNamespace(x=0.4, y=0.5, z=0.0) for _ in range(300)]
    landmarks[33] = SimpleNamespace(x=0.40, y=0.50, z=0.0)
    landmarks[263] = SimpleNamespace(x=0.50, y=0.50, z=0.0)     # 0.10 * 640 = 64 px apart
    engine._tap = SimpleNamespace(last=SimpleNamespace(face_landmarks=[landmarks]))
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    mm = engine.measure_distance(frame)
    expected = cam.focal_px * 90.0 / 64.0
    assert mm == pytest.approx(expected, rel=0.02)
    assert 600 < mm < 800                       # a plausible seated distance


def test_distance_verdict_covers_range_and_drift(tmp_path):
    c = _cfg(gaze_enabled=False, distance_min_mm=400.0, distance_max_mm=1000.0,
             distance_warn_fraction=0.25)
    engine = GazeEngine(c)
    engine.model_path = tmp_path / "m.pkl"
    ok, note = engine._distance_verdict(350.0)
    assert not ok and "close" in note
    ok, note = engine._distance_verdict(1500.0)
    assert not ok and "far" in note
    ok, note = engine._distance_verdict(700.0)
    assert ok and note == ""                    # no calibration yet: no drift opinion

    engine.save_metadata({"distance_mm": 600.0})
    assert engine.calibration_distance_mm == 600.0
    ok, note = engine._distance_verdict(650.0)
    assert ok
    ok, note = engine._distance_verdict(900.0)  # 50% further away than calibrated
    assert not ok and "recalibrate" in note


def test_geometry_aim_prefers_physical_triangulation():
    from kinesis.aim import AimClassifier
    layout = g.DeskLayout(panels=[g.Panel(index=0, x_mm=0, width_mm=600, height_mm=340),
                                  g.Panel(index=1, x_mm=610, width_mm=600, height_mm=340)],
                          bezel_mm=10.0)
    geo = g.DeskGeometry(layout=layout, distance_mm=600.0, eye_offset_mm=910.0)
    assert geo.known
    monitors = [_monitor("a", 0, 0, 1920, 1080), _monitor("b", 1920, 0, 1920, 1080)]
    aim = AimClassifier(Config(), monitors)
    aim.set_geometry(geo)
    left = aim.classify(-30.0, 0.0)
    right = aim.classify(30.0, 0.0)
    assert left.index == 0 and right.index == 1
    assert left.mode == "geometry"
