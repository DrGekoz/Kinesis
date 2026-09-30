#!/usr/bin/env python
"""Gaze calibration across all monitors.

EyeTrax ships single-screen calibration (its get_screen_size() is the primary monitor only), which
cannot point at a window on any of the other three screens. This does the same job across the whole
virtual desktop and trains the model on virtual-desktop coordinates.

It is fully automatic: it shows a dot, waits, samples, and moves on. Sit normally, keep your head
still, and look at each dot.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from kinesis import winapi as w          # noqa: E402
from kinesis.config import Config        # noqa: E402
from kinesis.gaze import GazeEngine      # noqa: E402

DOT_R = 11
RING_R = 30

GRIDS = {
    1: [(0.5, 0.5)],
    5: [(0.12, 0.12), (0.88, 0.12), (0.5, 0.5), (0.12, 0.88), (0.88, 0.88)],
    9: [(fx, fy) for fy in (0.12, 0.5, 0.88) for fx in (0.12, 0.5, 0.88)],
}


def grid_points(monitor, count):
    return [(monitor.left + monitor.width * fx, monitor.top + monitor.height * fy)
            for fx, fy in GRIDS[count]]


def parse_args():
    ap = argparse.ArgumentParser(description="Kinesis gaze calibration")
    ap.add_argument("--camera", type=int, default=None)
    ap.add_argument("--points", type=int, default=5, choices=[1, 5, 9], help="points per monitor")
    ap.add_argument("--monitors", default="", help="e.g. 1,3 to calibrate only those")
    ap.add_argument("--sample", type=float, default=1.0, help="seconds of sampling per point")
    ap.add_argument("--settle", type=float, default=0.7, help="seconds to look before sampling")
    return ap.parse_args()


def make_camera(cfg):
    cap = cv2.VideoCapture(int(cfg["camera_index"]), cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(cfg["capture_width"]))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(cfg["capture_height"]))
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        return None
    for _ in range(12):        # let exposure settle before the first sample
        cap.read()
    return cap

def collect_point(root, canvas, hwnd, cap, engine, monitor, target, args, n, total):
    """Show one dot and sample the eyes while it is on screen."""
    tx, ty = target
    w.position_window(hwnd, monitor.left, monitor.top, monitor.width, monitor.height)
    canvas.delete("all")
    cx, cy = tx - monitor.left, ty - monitor.top
    canvas.create_oval(cx - RING_R, cy - RING_R, cx + RING_R, cy + RING_R, outline="#3a3a3a", width=2)
    canvas.create_oval(cx - DOT_R, cy - DOT_R, cx + DOT_R, cy + DOT_R, fill="#ff2a2a", outline="")
    canvas.create_text(monitor.width // 2, RING_R + 60,
                       text=f"{n}/{total}   monitor {monitor.index + 1}",
                       fill="#4a4a4a", font=("Segoe UI", 15))
    root.update()

    end = time.perf_counter() + args.settle
    while time.perf_counter() < end:
        cap.read()
        root.update()
        time.sleep(0.005)

    out, blink, noface = [], 0, 0
    end = time.perf_counter() + args.sample
    while time.perf_counter() < end:
        ok, frame = cap.read()
        if not ok:
            continue
        feats, is_blink = engine.features_for(frame)
        if feats is None:
            noface += 1
        elif is_blink:
            blink += 1
        else:
            out.append((feats, float(tx), float(ty)))
        root.update()
    return out, blink, noface


def main() -> int:
    args = parse_args()
    w.set_dpi_aware()
    cfg = Config.load()
    if args.camera is not None:
        cfg["camera_index"] = args.camera

    monitors = w.enumerate_monitors()
    if not monitors:
        print("no monitors found")
        return 2
    idx = [int(x) - 1 for x in args.monitors.split(",")] if args.monitors else list(range(len(monitors)))
    for i in idx:
        if not 0 <= i < len(monitors):
            print(f"no monitor {i + 1} (found {len(monitors)})")
            return 2

    targets = [(i, pt) for i in idx for pt in grid_points(monitors[i], args.points)]
    print(f"Kinesis gaze calibration - {len(targets)} points over monitors "
          f"{', '.join(str(i + 1) for i in idx)}")
    print("Sit exactly as you normally do. Keep your head still and look at each dot.")
    print("Do not move your head to follow it. Blink between dots, not during them.\n")

    cap = make_camera(cfg)
    if cap is None:
        print("could not open the camera - close anything else using it and retry")
        return 2
    engine = GazeEngine(cfg)

    import tkinter as tk
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, highlightthickness=0, bd=0, bg="black")
    canvas.pack(fill="both", expand=True)
    root.update()
    hwnd = w.root_hwnd(root.winfo_id())
    root.focus_force()

    samples = []
    try:
        for n, (mi, pt) in enumerate(targets, 1):
            monitor = monitors[mi]
            got, blink, noface = collect_point(root, canvas, hwnd, cap, engine, monitor, pt,
                                               args, n, len(targets))
            note = "" if len(got) >= 5 else "   <-- too few, point skipped"
            print(f"  [{n:2d}/{len(targets)}] monitor {mi + 1} at ({int(pt[0])},{int(pt[1])})  "
                  f"kept {len(got):3d}  blink {blink:3d}  no-face {noface:3d}{note}")
            samples.extend(got)
    finally:
        try:
            root.destroy()
        finally:
            cap.release()

    if len(samples) < 10:
        print("\nnot enough face samples to train on - nothing saved")
        print("check that your face is lit and visible to the camera, then run this again")
        return 1

    print(f"\ntraining on {len(samples)} samples...")
    engine.train(samples)

    X = np.array([s[0] for s in samples], dtype=np.float32)
    y = np.array([[s[1], s[2]] for s in samples], dtype=np.float32)
    pred = np.asarray(engine._estimator.predict(X))
    err = np.linalg.norm(pred - y, axis=1)
    hit = sum(1 for (px, py), (_, tx, ty) in zip(pred, samples)
              if w.monitor_at(monitors, int(px), int(py)) == w.monitor_at(monitors, int(tx), int(ty)))
    pct = 100.0 * hit / len(samples)
    print(f"in-sample error : mean {err.mean():5.0f} px   median {np.median(err):5.0f} px   "
          f"p90 {np.percentile(err, 90):5.0f} px")
    print(f"monitor hit rate: {hit}/{len(samples)} = {pct:.1f}%   <- what window targeting uses")
    if pct < 85.0:
        print("that is low - sit stiller, keep the same chair position, and try 9 points per monitor")
    print(f"\nsaved -> {engine.model_path}")
    print("run run.bat, then look at a window and use a hand gesture on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

