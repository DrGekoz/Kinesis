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
import datetime
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
    ap.add_argument("--points", type=int, default=0, choices=[0, 1, 5, 9],
                    help="points per monitor; 0 = pick per screen from its angular size")
    ap.add_argument("--monitors", default="", help="e.g. 1,3 to calibrate only those")
    ap.add_argument("--sample", type=float, default=1.0, help="seconds of sampling per point")
    ap.add_argument("--settle", type=float, default=0.7, help="seconds to look before sampling")
    return ap.parse_args()


def make_camera(cfg):
    cap = cv2.VideoCapture(int(cfg["camera_index"]), cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(cfg["frame_width"]))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(cfg["frame_height"]))
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        return None
    for _ in range(12):        # let exposure settle before the first sample
        cap.read()
    return cap

def collect_point(root, canvas, hwnd, cap, engine, monitor, mi, target, args, n, total):
    """Show one dot and sample the eyes while it is on screen.

    `mi` is the monitor's position in the left-to-right list: Monitor carries no index of its own.
    """
    tx, ty = target
    w.position_window(hwnd, monitor.left, monitor.top, monitor.width, monitor.height)
    canvas.delete("all")
    cx, cy = tx - monitor.left, ty - monitor.top
    canvas.create_oval(cx - RING_R, cy - RING_R, cx + RING_R, cy + RING_R, outline="#3a3a3a", width=2)
    canvas.create_oval(cx - DOT_R, cy - DOT_R, cx + DOT_R, cy + DOT_R, fill="#ff2a2a", outline="")
    canvas.create_text(monitor.width // 2, RING_R + 60,
                       text=f"{n}/{total}   monitor {mi + 1}",
                       fill="#4a4a4a", font=("Segoe UI", 15))
    root.update()

    end = time.perf_counter() + args.settle
    while time.perf_counter() < end:
        cap.read()
        root.update()
        time.sleep(0.005)

    out, blink, noface, dists = [], 0, 0, []
    end = time.perf_counter() + args.sample
    while time.perf_counter() < end:
        ok, frame = cap.read()
        if not ok:
            continue
        feats, is_blink = engine.features_for(frame)
        engine.measure_distance(frame)          # uses the landmarks that call just produced
        if engine.state.distance_mm > 0:
            dists.append(engine.state.distance_mm)
        if feats is None:
            noface += 1
        elif is_blink:
            blink += 1
        else:
            out.append((feats, float(tx), float(ty), mi))
        root.update()
    return out, blink, noface, dists


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

    # ---- desk geometry: which camera, which screens, how big, how far away -------------
    from kinesis.geometry import build_geometry, angular_span_deg
    geo = build_geometry(cfg, monitors, int(cfg["frame_width"]), int(cfg["frame_height"]),
                         distance_mm=float(cfg["assumed_distance_mm"]), distance_source="assumed")
    print(geo.report())
    print()

    # more sample points where a screen subtends a wider angle: those are the ones a linear model
    # gets wrong, and on a 4-screen array they are the outer screens
    def points_for(i: int) -> int:
        if args.points:
            return args.points
        if geo.distance_mm > 0:
            lo, hi = angular_span_deg(geo.layout.panels[i], geo.eye_offset_mm, geo.distance_mm)
            return 9 if (hi - lo) > 30.0 else 5
        return 5

    targets = [(i, pt) for i in idx for pt in grid_points(monitors[i], points_for(i))]
    counts = ", ".join(f"{i + 1}:{points_for(i)}pt" for i in idx)
    print(f"Kinesis gaze calibration - {len(targets)} points ({counts})")
    print("Sit exactly as you normally do. Keep your head still and look at each dot.")
    print("Do not move your head to follow it. Blink between dots, not during them.\n")

    cap = make_camera(cfg)
    if cap is None:
        print("could not open the camera - close anything else using it and retry")
        return 2
    engine = GazeEngine(cfg)
    engine.set_camera(geo.camera)      # so the seat distance can be measured while sampling

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
    seat_dists = []
    try:
        for n, (mi, pt) in enumerate(targets, 1):
            monitor = monitors[mi]
            got, blink, noface, dists = collect_point(root, canvas, hwnd, cap, engine, monitor, mi, pt,
                                                     args, n, len(targets))
            seat_dists.extend(dists)
            note = "" if len(got) >= 5 else "   <-- too few, point skipped"
            seen = f"  {sum(dists) / len(dists) / 10:.0f}cm" if dists else ""
            print(f"  [{n:2d}/{len(targets)}] monitor {mi + 1} at ({int(pt[0])},{int(pt[1])})  "
                  f"kept {len(got):3d}  blink {blink:3d}  no-face {noface:3d}{seen}{note}")
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

    monitor_of = [int(s[3]) for s in samples]
    train_samples = [(s[0], s[1], s[2]) for s in samples]
    print(f"\ntraining on {len(train_samples)} samples...")
    engine.train(train_samples)

    X = np.array([s[0] for s in train_samples], dtype=np.float32)
    y = np.array([[s[1], s[2]] for s in train_samples], dtype=np.float32)
    pred = np.asarray(engine._estimator.predict(X))
    err = np.linalg.norm(pred - y, axis=1)
    def mon_index(x: float, y: float):
        hit = w.monitor_at(int(x), int(y), monitors)
        if hit is None:
            return None
        return next((i for i, mm in enumerate(monitors) if mm.device == hit.device), None)

    hits = [mon_index(px, py) == mon_index(tx, ty)
            for (px, py), (_, tx, ty) in zip(pred, train_samples)]
    hit = sum(hits)
    pct = 100.0 * hit / len(train_samples)
    print(f"in-sample error : mean {err.mean():5.0f} px   median {np.median(err):5.0f} px   "
          f"p90 {np.percentile(err, 90):5.0f} px")
    print(f"monitor hit rate: {hit}/{len(train_samples)} = {pct:.1f}%   "
          f"<- what window targeting uses")

    # per-screen, because one bad screen is what ruins targeting in practice
    per_monitor = {}
    for mi in sorted(set(monitor_of)):
        sel = [h for h, m in zip(hits, monitor_of) if m == mi]
        got = sum(sel)
        per_monitor[mi] = round(100.0 * got / max(len(sel), 1), 1)
        print(f"  monitor {mi + 1}: {got}/{len(sel)} = {per_monitor[mi]:.0f}%")

    seat = float(np.median(seat_dists)) if seat_dists else 0.0
    if seat:
        print(f"seat distance   : {seat:.0f} mm ({seat / 10:.0f} cm) - recorded so Kinesis can "
              f"tell you if you move")
        if geo.distance_mm > 0 and abs(seat - geo.distance_mm) / geo.distance_mm > 0.35:
            print(f"  (that differs a lot from the {geo.distance_mm:.0f} mm I assumed from the "
                  f"monitors - the measured value is the one being saved)")

    if pct < 85.0:
        print("\nthat is low. In order of what usually helps:")
        print("  1. sit stiller and keep your head level; do not follow the dot with your head")
        print("  2. sit closer: your screens are only "
              + (f"{min(geo.separation_deg()):.1f}" if geo.separation_deg() else "a few")
              + " degrees apart from that seat, which is tight for gaze")
        print("  3. light your face evenly - a window or lamp behind you wrecks accuracy")

    engine.save_metadata({
        "trained_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "samples": len(train_samples),
        "monitor_hit_rate": round(pct, 1),
        "per_monitor_hit_rate": {str(k + 1): v for k, v in per_monitor.items()},
        "distance_mm": round(seat, 1),
        "distance_source": "measured with the face scale at calibration",
        "camera": {
            "index": geo.camera.index, "name": geo.camera.name,
            "resolution": [geo.camera.width, geo.camera.height],
            "fov_diagonal_deg": round(geo.camera.diagonal_fov_deg, 1),
            "fov_horizontal_deg": round(geo.camera.horizontal_fov_deg, 1),
            "fov_source": geo.camera.fov_source, "focal_px": round(geo.camera.focal_px, 1),
        },
        "monitors": [{
            "index": p.index + 1, "model": p.model, "mm": [round(p.width_mm), round(p.height_mm)],
            "px": [p.px_w, p.px_h], "size_source": p.size_source,
            "rect": [monitors[p.index].left, monitors[p.index].top,
                     monitors[p.index].right, monitors[p.index].bottom],
        } for p in geo.layout.panels],
        "layout": {
            "total_width_mm": round(geo.layout.total_width_mm),
            "bezel_mm": geo.layout.bezel_mm,
            "angular_span_deg": [round(v, 1) for v in geo.angular_coverage_deg()],
            "separation_deg": [round(v, 1) for v in geo.separation_deg()],
            "vertical_misalignment_mm": round(geo.layout.vertical_misalignment_mm, 1),
        },
    })
    print(f"\nsaved -> {engine.model_path}")
    print(f"saved -> {engine.metadata_path}   (seat distance, camera and layout)")
    print("run run.bat, then look at a window and use a hand gesture on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

