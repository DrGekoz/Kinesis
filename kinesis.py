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
    p.add_argument("--latency-report", action="store_true", help="print latency stats every 5s")
    p.add_argument("--tune", action="append", metavar="KEY=VALUE",
                   help="override any setting from kinesis_config.json (repeatable)")
    p.add_argument("--save-config", action="store_true", help="save the tuned config to disk")
    p.add_argument("--list-monitors", action="store_true", help="print monitor layout and exit")
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

    print("\nRESULT: " + ("all checks passed" if ok else "problems found above"))
    return 0 if ok else 1


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_monitors:
        return cmd_list_monitors()

    cfg = apply_args(args, Config.load())

    if args.check:
        return cmd_check(args, cfg)

    if args.save_config:
        cfg.save()
        changed = cfg.diff_from_defaults()
        print(f"saved kinesis_config.json ({len(changed)} non-default settings)")
        for k, v in sorted(changed.items()):
            print(f"  {k} = {v}")

    app = KinesisApp(cfg)
    app.describe_environment()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
