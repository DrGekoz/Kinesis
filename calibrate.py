#!/usr/bin/env python
"""Aim calibration: teach Kinesis which hand angle points at each monitor.

For each monitor: point at it, hold still, press Enter. Kinesis samples the hand's pointing
angles for ~2 seconds and stores the average. Afterwards it checks whether the angles were
monotonic across the screens and reports it.

    python calibrate.py
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import datetime

from kinesis import winapi as w
from kinesis.aim import AimClassifier
from kinesis.config import Calibration, Config, MonitorTarget, save_calibration
from kinesis.tracking import TrackingEngine

SAMPLE_SECONDS = 2.0
MIN_SAMPLES = 12
LINE_WIDTH = 90


def _clear_line():
    print("\r" + " " * LINE_WIDTH + "\r", end="", flush=True)


def collect(engine: TrackingEngine, seconds: float):
    """Sample the right hand's pointing angles. The first third is dropped - that is where the
    hand is still moving into position."""
    ys, ps = [], []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        poses, _, _ = engine.update()
        primary = next((p for p in poses if p.handedness == "Right"),
                       poses[0] if poses else None)
        if primary is not None:
            ys.append(primary.yaw)
            ps.append(primary.pitch)
        time.sleep(0.005)
    if len(ys) < MIN_SAMPLES:
        return [], []
    cut = len(ys) // 3
    return ys[cut:], ps[cut:]


def wait_for_key(engine: TrackingEngine, prompt: str) -> str:
    """Returns 'sample', 'skip' or 'quit'. Live-view only when attached to a terminal."""
    if not sys.stdin.isatty():
        print(f"{prompt}  [Enter = sample, s = skip, q = quit]")
        line = sys.stdin.readline()
        if not line:
            return "quit"
        token = line.strip().lower()
        return "skip" if token == "s" else ("quit" if token == "q" else "sample")

    import msvcrt
    while True:
        poses, _, _ = engine.update()
        seen = ", ".join(p.describe() for p in poses) if poses else "no hand"
        print(f"\r{prompt}   (seeing: {seen})", end="", flush=True)
        if msvcrt.kbhit():
            ch = msvcrt.getwch().lower()
            if ch == "q":
                _clear_line()
                return "quit"
            if ch == "s":
                _clear_line()
                return "skip"
            if ch in ("\r", "\n"):
                _clear_line()
                return "sample"
        time.sleep(0.01)


def main() -> int:
    ap = argparse.ArgumentParser(description="Kinesis aim calibration")
    ap.add_argument("--monitors", default="",
                    help="only these screens, e.g. 2,3 (default: the ones enabled for tracking)")
    args = ap.parse_args()
    cfg = Config.load()
    monitors = w.enumerate_monitors()
    if not monitors:
        print("no monitors detected")
        return 1

    if args.monitors:
        wanted = [int(x) - 1 for x in args.monitors.replace(" ", "").split(",") if x.strip()]
    else:
        # same default as the gaze wizard: never put a calibration screen on one that was switched
        # off for tracking - a TV that is not on the desk is exactly what people disable
        from kinesis import monitor_set as _ms
        kept, dropped = _ms.select_monitors(monitors, cfg["enabled_monitors"])
        wanted = [i for i, m in enumerate(monitors) if m in kept] if dropped \
            else list(range(len(monitors)))
    wanted = [i for i in wanted if 0 <= i < len(monitors)]
    if not wanted:
        print("no screens selected to calibrate")
        return 2

    print("Kinesis aim calibration")
    print("=======================")
    print(f"{len(monitors)} monitors, indexed left to right:\n")
    for i, m in enumerate(monitors):
        mark = "" if i in wanted else "   (not calibrated)"
        print(f"  {i + 1}. {m}{mark}")
    print("\nPoint if needed, then hold your hand still and press Enter for each screen.")
    print("'s' skips a screen, 'q' quits without saving.\n")

    engine = TrackingEngine(cfg)
    if not engine.start():
        print(f"camera error: {engine.camera.error}")
        return 2
    time.sleep(0.5)
    print("camera running, waiting for a hand...\n")

    targets = []
    quit_requested = False
    try:
        for i, monitor in enumerate(monitors):
            if i not in wanted:
                continue
            name = monitor.device.split("\\")[-1]
            while True:
                action = wait_for_key(engine, f"[monitor {i + 1}] {name}: point at this screen")
                if action == "quit":
                    quit_requested = True
                    break
                if action == "skip":
                    print(f"[monitor {i + 1}] skipped")
                    break
                ys, ps = collect(engine, SAMPLE_SECONDS)
                if not ys:
                    print(f"[monitor {i + 1}] no hand seen during the sample - try again")
                    continue
                yaw = statistics.mean(ys)
                pitch = statistics.mean(ps)
                spread = statistics.pstdev(ys) if len(ys) > 1 else 0.0
                targets.append(MonitorTarget(
                    index=i, name=monitor.device, left=monitor.left, top=monitor.top,
                    right=monitor.right, bottom=monitor.bottom,
                    yaw=yaw, pitch=pitch, yaw_std=spread,
                    pitch_std=statistics.pstdev(ps) if len(ps) > 1 else 0.0))
                print(f"[monitor {i + 1}] yaw {yaw:+.1f}  pitch {pitch:+.1f}  "
                      f"(spread +/-{spread:.1f}, {len(ys)} samples)")
                break
            if quit_requested:
                break
    except KeyboardInterrupt:
        quit_requested = True
    finally:
        engine.stop()

    if quit_requested and not targets:
        print("cancelled - nothing written")
        return 1
    if not targets:
        print("nothing captured - no changes written")
        return 1

    ordered = sorted(((t.index, t.yaw) for t in targets), key=lambda p: p[0])
    looks_right = AimClassifier.order_check(ordered)
    print("\nangle ordering across screens:")
    for idx, yaw in ordered:
        print(f"  monitor {idx + 1}: yaw {yaw:+7.1f}")
    if len(ordered) > 1:
        if looks_right:
            print("  increasing left to right: pointing right gives a larger yaw, as expected")
        else:
            print("  decreasing left to right: your yaw axis is mirrored. That is fine - matching "
                  "uses the recorded angles, so the sign convention does not matter.")

    calibration = Calibration(targets=targets,
                              captured_at=datetime.now().isoformat(timespec="seconds"),
                              yaw_flipped=not looks_right)
    save_calibration(calibration)
    print(f"\nsaved kinesis_calibration.json with {len(targets)} monitor target(s)")
    print("run kinesis.py - the HUD shows which monitor it thinks you are aiming at")
    return 0


if __name__ == "__main__":
    sys.exit(main())
