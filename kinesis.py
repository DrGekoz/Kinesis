#!/usr/bin/env python
"""Kinesis entry point.

  python kinesis.py                     run it
  python kinesis.py --dry               log gestures, inject nothing
  python kinesis.py --list-monitors     show the monitor layout and exit
  python kinesis.py --check             environment self-test and exit
  python kinesis.py --latency-report    print measured latency every 5s
  python kinesis.py --tune pinch_on=0.30 --tune filter_min_cutoff=2.0
  python kinesis.py --save-config       persist --tune values
"""
from __future__ import annotations

import argparse
import sys
import time

import cv2

from kinesis import __version__
from kinesis import winapi as w
from kinesis.config import Config, parse_tune_args
from kinesis.app import KinesisApp


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kinesis", description="Webcam human-computer use")
    p.add_argument("--version", action="version", version=f"kinesis {__version__}")
    p.add_argument("--dry", action="store_true", help="log every gesture, inject nothing")
    p.add_argument("--exclusive", action="store_true",
                   help="cursor movement only; gesture actions disabled")
    p.add_argument("--no-preview", action="store_true", help="skip the preview window (lowest latency)")
    p.add_argument("--camera", type=int, default=None, help="camera index (default 0)")
    p.add_argument("--model-complexity", type=int, choices=(0, 1), default=None,
                   help="0 = lite (faster, default), 1 = full (more accurate)")
    p.add_argument("--cursor-hand", choices=("right", "left", "auto"), default=None)
    p.add_argument("--no-filter", action="store_true", help="disable the One-Euro filter (raw landmarks)")
    p.add_argument("--no-gaze", action="store_true", help="disable eye tracking entirely")
    p.add_argument("--gaze-model", default=None, help="path to a gaze model .pkl")
    p.add_argument("--gaze-scroll", choices=("edge", "off"), default=None,
                   help="eye-driven scrolling: hold your gaze at the top/bottom edge")
    p.add_argument("--no-vcam", action="store_true", help="do not feed the virtual camera")
    p.add_argument("--vcam", choices=("passthrough", "overlay", "off"), default=None,
                   help="passthrough = webcam+overlays as a webcam; overlay = green keyable overlay")
    p.add_argument("--vcam-test", action="store_true",
                   help="send a test pattern to the virtual camera and exit")
    p.add_argument("--gaze-test", action="store_true",
                   help="print live gaze + the window it targets, and exit on END")
    p.add_argument("--latency-report", action="store_true", help="print latency stats every 5s")
    p.add_argument("--tune", action="append", metavar="KEY=VALUE",
                   help="override any setting from kinesis_config.json (repeatable)")
    p.add_argument("--save-config", action="store_true", help="save the tuned config to disk")
    p.add_argument("--enable-monitors", default="", metavar="LIST",
                   help="screens to use, e.g. 2,3 (by the numbers --list-monitors prints)")
    p.add_argument("--all-monitors", action="store_true", help="use every screen again")
    p.add_argument("--ask-monitors", action="store_true",
                   help="ask which screens to use, even if it has been answered before")
    p.add_argument("--list-monitors", action="store_true", help="print monitor layout and exit")
    p.add_argument("--vcam-style", default=argparse.SUPPRESS,
                   choices=["pointer", "comet", "path", "heatmap", "heatmap_comet", "none"],
                   help="gaze overlay look (see the Overlay styles section of the README)")
    p.add_argument("--vcam-theme", default=argparse.SUPPRESS,
                   choices=["ember", "cyan", "violet", "lime", "ice"],
                   help="overlay colour theme")
    p.add_argument("--vcam-demo", action="store_true",
                   help="drive the overlay with a synthetic gaze path so you can see the styles")
    p.add_argument("--vcam-demo-seconds", type=float, default=20.0,
                   help="how long --vcam-demo runs for")
    p.add_argument("--overlay-desktop", action="store_true",
                   help="draw the gaze overlay on the desktop itself (click-through, always on top)")
    p.add_argument("--no-overlay-desktop", action="store_true", help="disable the desktop overlay")
    p.add_argument("--desk-report", action="store_true",
                   help="print the physical desk: screens, sizes, camera FoV, seat distance")
    p.add_argument("--check", action="store_true", help="environment self-test and exit")
    return p


def apply_args(args, cfg: Config) -> Config:
    if args.camera is not None:
        cfg.set("camera_index", args.camera)
    if args.model_complexity is not None:
        cfg.set("model_complexity", args.model_complexity)
    if args.cursor_hand:
        cfg.set("cursor_hand", args.cursor_hand)
    if args.no_filter:
        cfg.set("filter", False)
    if args.no_gaze:
        cfg.set("gaze_enabled", False)
    if args.gaze_model:
        cfg.set("gaze_model_path", args.gaze_model)
    if args.gaze_scroll:
        cfg.set("gaze_scroll_mode", args.gaze_scroll)
    style = getattr(args, "vcam_style", None)
    if style:
        cfg.set("vcam_style", style)
    theme = getattr(args, "vcam_theme", None)
    if theme:
        cfg.set("vcam_theme", theme)
    if getattr(args, "no_overlay_desktop", False):
        cfg.set("desktop_overlay", False)
    if getattr(args, "overlay_desktop", False):
        cfg.set("desktop_overlay", True)
    if args.no_vcam:
        cfg.set("vcam_enabled", False)
    if args.vcam:
        cfg.set("vcam_mode", args.vcam)
        cfg.set("vcam_enabled", args.vcam != "off")
    if args.latency_report:
        cfg.set("latency_report", True)
    if args.exclusive:
        cfg.set("exclusive", True)
    if args.dry:
        cfg.set("dry_run", True)
    if args.no_preview:
        cfg.set("preview", False)
    if args.tune:
        cfg.apply_overrides(parse_tune_args(args.tune))
    return cfg


def cmd_list_monitors() -> int:
    monitors = w.enumerate_monitors()
    left, top, width, height = w.virtual_screen()
    print(f"virtual desktop: {width}x{height} at ({left},{top})\n")
    for i, m in enumerate(monitors):
        print(f"  {i + 1}. {m}")
        print(f"     work area {m.work}")
    print("\nKinesis indexes monitors left-to-right, matching the numbering above.")
    return 0


def cmd_vcam_test(cfg: Config) -> int:
    """Prove the virtual camera actually accepts frames (OBS device present and working)."""
    import numpy as np
    from kinesis.vcam import VirtualCamera
    cam = VirtualCamera(cfg, dry=False)
    if not cam.enabled:
        print("virtual camera is disabled (vcam_enabled=false or --no-vcam)")
        return 1
    if not cam.start(30.0):
        print(f"[FAIL] {cam.error}")
        return 1
    w_, h_ = cam.size
    print(f"[ok]   virtual camera: {cam.device_info()} {w_}x{h_} mode={cam.mode}")
    for i in range(45):
        frame = np.zeros((h_, w_, 3), np.uint8)
        frame[:, :, 1] = 40
        frame[10:60, 10:10 + i * 4] = (0, 200, 255)
        cv2.putText(frame, f"kinesis vcam test {i:02d}", (12, h_ - 16), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (255, 255, 255), 1, cv2.LINE_AA)
        cam.publish_direct(frame)
        time.sleep(1.0 / 30.0)
    print(f"[ok]   sent {cam.sent} frames at {cam.current_fps():.1f} fps")
    # second phase: the real path the app uses (publish -> sender thread -> device)
    before = cam.sent
    for i in range(30):
        frame = np.zeros((h_, w_, 3), np.uint8)
        frame[:, :, 2] = 60
        cam.publish(frame)
        time.sleep(1.0 / 30.0)
    print(f"[ok]   threaded publish path sent {cam.sent - before} frames "
          f"(dropped {cam.dropped})")
    cam.stop()
    ok = (cam.sent - before) >= 10 and cam.error is None
    if not ok:
        print(f"[FAIL] threaded send path: sent={cam.sent - before} error={cam.error}")
        return 1
    print("open OBS (or any app) and select 'OBS Virtual Camera' to see it")
    return 0


def cmd_vcam_demo(cfg: Config, seconds: float = 20.0) -> int:
    """Drive the overlay with a synthetic gaze path and send it to the virtual camera.

    Without this you cannot see the overlay styles without a face in frame; with it you can open any
    app that takes a camera, pick "OBS Virtual Camera", and watch each style.
    """
    import math
    from types import SimpleNamespace

    from kinesis import winapi as w
    from kinesis.vcam import VirtualCamera

    vcam = VirtualCamera(cfg, dry=False)
    if not vcam.enabled:
        print("virtual camera is off (vcam_enabled / vcam_mode)")
        return 1
    if not vcam.start(fps=float(cfg["vcam_fps"])):
        print(f"virtual camera failed: {vcam.error}")
        return 1
    print(f"virtual camera: {vcam.device_info()} at {vcam.size[0]}x{vcam.size[1]}")
    print(f"style: {vcam.visual.status() if vcam.visual else 'none'}")
    print(f"running {seconds:.0f}s - select 'OBS Virtual Camera' in OBS, Discord or the Camera app")
    left, top, vw, vh = w.virtual_screen()
    monitors = w.enumerate_monitors()
    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < seconds:
            t = time.perf_counter() - t0
            fx = 0.5 + 0.40 * math.sin(t * 0.9) * math.cos(t * 0.23)
            fy = 0.5 + 0.33 * math.sin(t * 1.7)
            gx, gy = left + fx * vw, top + fy * vh
            gaze = SimpleNamespace(x=gx, y=gy, valid=True, age=0.0)
            mon = w.monitor_at(int(gx), int(gy), monitors)
            label = f"monitor {monitors.index(mon) + 1}" if mon in monitors else "between screens"
            lines = [
                "Kinesis - overlay demo",
                f"style {vcam.visual.style}  theme {vcam.visual.theme_name}" if vcam.visual else "off",
                f"gaze {int(gx)},{int(gy)}  {label}",
                f"seat {float(cfg['assumed_distance_mm']):.0f} mm  target: Chrome",
            ]
            out = vcam.composite(None, poses=(), gaze=gaze, lines=lines)
            if out is not None:
                vcam.publish_direct(out)
    except KeyboardInterrupt:
        pass
    finally:
        vcam.stop()
    print(f"demo done ({vcam.sent} frames sent)")
    return 0


def cmd_desk_report(cfg: Config) -> int:
    """Print what the desk actually is: screens, their physical sizes, the camera, the seat."""
    from kinesis import winapi as w
    from kinesis.geometry import build_geometry
    from kinesis.gaze import GazeEngine

    monitors = w.enumerate_monitors()
    gaze = GazeEngine(cfg)
    distance = gaze.calibration_distance_mm
    geo = build_geometry(cfg, monitors, int(cfg["frame_width"]), int(cfg["frame_height"]),
                         distance_mm=distance or float(cfg["assumed_distance_mm"]),
                         distance_source="gaze calibration" if distance else "assumed")
    print(geo.report())
    if not distance:
        print("\n  (no gaze calibration yet, so the seat distance is a guess - "
              "run calibrate_gaze.bat)")
    print("\nwhat the array looks like from that seat:")
    for p in geo.layout.panels:
        lo, hi = geo_span(geo, p)
        print(f"  screen {p.index + 1}: {p.model[:26]:28} "
              f"{g_centre(geo, p):+6.1f} deg centre, {lo:+6.1f} to {hi:+6.1f} deg")
    return 0


def geo_span(geo, panel):
    from kinesis.geometry import angular_span_deg
    return angular_span_deg(panel, geo.eye_offset_mm, geo.distance_mm)


def g_centre(geo, panel):
    from kinesis.geometry import centre_angle_deg
    return centre_angle_deg(panel, geo.eye_offset_mm, geo.distance_mm)


def cmd_check(args, cfg: Config) -> int:
    ok = True
    print(f"kinesis {__version__} environment check\n")

    try:
        import mediapipe as mp
        print(f"[ok]   mediapipe {mp.__version__}")
        print(f"[ok]   mp.solutions.hands available: {hasattr(mp.solutions, 'hands')}")
    except Exception as exc:
        ok = False
        print(f"[FAIL] mediapipe: {exc}")

    try:
        import cv2
        print(f"[ok]   opencv {cv2.__version__}")
    except Exception as exc:
        ok = False
        print(f"[FAIL] opencv: {exc}")

    monitors = w.enumerate_monitors()
    print(f"[ok]   monitors: {len(monitors)}")
    for m in monitors:
        print(f"         {m}")
    print(f"[ok]   virtual desktop: {w.virtual_screen()}")
    print(f"[ok]   cursor now at: {w.get_cursor_pos()}")

    vk = w.vk_for("f11")
    print(f"[ok]   virtual-key map: f11=0x{vk:02X} tab=0x{w.vk_for('tab'):02X} "
          f"space=0x{w.vk_for('space'):02X} alt=0x{w.vk_for('alt'):02X}")

    fg = w.foreground_window(monitors)
    if fg:
        print(f"[ok]   foreground window: {fg.title[:50]!r} monitor {fg.monitor_index + 1} "
              f"maximised={fg.is_maximized} fullscreen={fg.is_fullscreen}")
    else:
        print("[warn] no foreground window detected")

    target = w.topmost_window_on_monitor(0, monitors, {w.own_process_id()})
    print(f"[{'ok' if target else 'warn'}]   topmost window on monitor 1: {target}")

    try:
        from kinesis.tracking import TrackingEngine
        engine = TrackingEngine(cfg)
        if engine.start():
            # wait for the first inference result rather than guessing at a sleep: the first
            # mediapipe frame carries graph initialisation and can take seconds
            deadline = time.time() + 8.0
            while time.time() < deadline and engine.inference.inferred == 0:
                if engine.inference.fatal:
                    break
                engine.update()
                time.sleep(0.1)
            time.sleep(0.8)
            poses, frame, latency = engine.update()
            w_, h_ = engine.frame_size
            print(f"[ok]   camera {cfg['camera_index']}: {w_}x{h_}, "
                  f"{engine.stats.capture_fps:.1f} capture fps, "
                  f"{engine.stats.inference_fps:.1f} inference fps, "
                  f"{engine.stats.inference_ms:.1f} ms/frame")
            if engine.inference.fatal or engine.inference.process_failures:
                ok = False
                print(f"[FAIL] inference: {engine.inference.error}")
            print(f"[ok]   hands: {engine.handedness_report(poses)}")
            print(f"[ok]   frame received: {frame is not None}, end-to-end {latency * 1000:.0f} ms")
            engine.stop()
        else:
            ok = False
            print(f"[FAIL] camera: {engine.camera.error}")
    except Exception as exc:
        ok = False
        print(f"[FAIL] tracking pipeline: {exc}")

    # --- gaze + virtual camera ---
    try:
        import pyvirtualcam
        print(f"[ok]   pyvirtualcam {pyvirtualcam.__version__}")
    except Exception as exc:
        ok = False
        print(f"[FAIL] pyvirtualcam: {exc}")

    try:
        from kinesis.gaze import GazeEngine
        g = GazeEngine(cfg)
        from eyetrax import GazeEstimator  # noqa: F401
        print(f"[ok]   eyetrax importable; gaze model: "
              f"{'present' if g.is_calibrated else 'NOT CALIBRATED - run calibrate_gaze.bat'}")
        if g.is_calibrated:
            if g.start():
                print(f"[ok]   gaze estimator loaded ({g.model_path.name})")
                g.stop()
            else:
                ok = False
                print(f"[FAIL] gaze: {g.error}")
    except Exception as exc:
        ok = False
        print(f"[FAIL] gaze stack: {exc}")

    print("\nRESULT: " + ("all checks passed" if ok else "problems found above"))
    return 0 if ok else 1


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    w.set_dpi_aware()          # physical pixels before anything reads a monitor rect

    if args.list_monitors:
        return cmd_list_monitors()

    cfg = apply_args(args, Config.load())

    if args.desk_report:
        return cmd_desk_report(cfg)
    if args.check:
        return cmd_check(args, cfg)

    if args.vcam_test:
        return cmd_vcam_test(cfg)

    if args.vcam_demo:
        return cmd_vcam_demo(cfg, args.vcam_demo_seconds)

    if args.save_config:
        cfg.save()
        changed = cfg.diff_from_defaults()
        print(f"saved kinesis_config.json ({len(changed)} non-default settings)")
        for k, v in sorted(changed.items()):
            print(f"  {k} = {v}")

    if args.all_monitors or args.enable_monitors or args.ask_monitors:
        from kinesis import monitor_set as _ms
        from kinesis import winapi as _w
        monitors = _w.enumerate_monitors()
        chosen = None
        if args.ask_monitors:
            labels = {}
            try:
                from kinesis.geometry import monitor_hardware
                labels = _ms.labels_from_hardware(monitor_hardware(monitors), monitors)
            except Exception:
                labels = {}
            chosen = _ms.ask(monitors, labels=labels)
        elif args.all_monitors:
            chosen = [str(getattr(m, "device", "") or "") for m in monitors]
            print("using every screen")
        else:
            picked = _ms.parse_selection(args.enable_monitors, len(monitors))
            if picked is None:
                print(f"could not read --enable-monitors {args.enable_monitors!r} - the screens are "
                      f"numbered 1..{len(monitors)} as --list-monitors prints them")
                return 2
            chosen = [str(getattr(monitors[i - 1], "device", "") or "") for i in picked]
            kept, dropped = _ms.select_monitors(monitors, chosen)
            print(f"using {len(kept)} of {len(monitors)} screens")
            for m in dropped:
                print(f"  left out: {m}")
        if chosen is not None:
            cfg["enabled_monitors"] = chosen
            cfg["monitors_configured"] = True
            cfg.save()

    app = KinesisApp(cfg)
    app.describe_environment()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
