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
import math
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


def leave_one_point_out(engine, samples, point_ids, model_factory=None):
    """Honest accuracy: refit without each target dot, then predict that dot back.

    The in-sample error is scored on the samples the model was fitted to, so it flatters a fit that
    has merely memorised them. Leaving out a whole DOT - not a sample - is what predicts behaviour on
    a position it has never seen, which is every position a user actually looks at.

    With `model_factory`, each fold uses a fresh candidate model and the engine is left alone; without
    it the engine's own model is refitted per fold, so retrain on everything afterwards.

    Returns (per-sample errors, one (id, median_x, median_y, target_x, target_y) per dot).
    """
    X = np.array([s[0] for s in samples], dtype=np.float32)
    y = np.array([[float(s[1]), float(s[2])] for s in samples], dtype=np.float32)
    ids = np.array(point_ids)
    errors, per_point = [], []
    for pid in sorted(set(point_ids)):
        test = ids == pid
        train = ~test
        if train.sum() < 8 or test.sum() == 0:
            continue
        if model_factory is None:
            engine._estimator.train(X[train], y[train])
            pred = np.asarray(engine._estimator.predict(X[test]), dtype=np.float64)
        else:
            model = model_factory()
            model.train(X[train], y[train])
            pred = np.asarray(model.predict(X[test]), dtype=np.float64)
        errors.extend(float(e) for e in np.linalg.norm(pred - y[test], axis=1))
        per_point.append((int(pid), float(np.median(pred[:, 0])), float(np.median(pred[:, 1])),
                          float(y[test][0][0]), float(y[test][0][1])))
    return errors, per_point


# Ridge strength. EyeTrax defaults to alpha=1.0 over 486 standardised features, which with a few
# hundred samples memorises the calibration dots: measured on held-out dots it was 1543 px out while
# claiming 28 px. Sweeping this is what turns a calibration that "looks perfect" into one that works.
MODEL_CANDIDATES = [
    ("ridge", {"alpha": a})
    for a in (1.0, 10.0, 100.0, 1_000.0, 10_000.0, 100_000.0, 1_000_000.0)
]


def select_model(samples, point_ids, mon_index):
    """Score every candidate on dots it was not fitted on; best held-out accuracy wins.

    Returns a list of (name, kwargs, median_error_px, hit_rate_pct, n_dots), best first. Empty when
    there are too few dots for a held-out estimate, in which case the default model is kept.
    """
    from eyetrax.models import create_model
    results = []
    for name, kwargs in MODEL_CANDIDATES:
        errors, per_point = leave_one_point_out(
            None, samples, point_ids, model_factory=lambda: create_model(name, **kwargs))
        if not errors:
            continue
        hits = [mon_index(px, py) == mon_index(tx, ty) for _, px, py, tx, ty in per_point]
        results.append((name, kwargs, float(np.median(errors)),
                        100.0 * sum(hits) / max(len(hits), 1), len(per_point)))
    # Rank by targeting, not by precision. Gaze in Kinesis picks the window you are looking at and
    # clicks the tab you are looking at; on this desk alpha=1 scored the best median error (712 px)
    # while only putting 60% of held-out dots on the right monitor, against 100% at alpha>=100.
    results.sort(key=lambda r: (-r[3], r[2]))          # hit rate first, then median error
    return results


MIN_GATE_SAMPLES = 20            # below this the gate gives up rather than starve the model


def gate_by_distance(samples, tolerance):
    """Drop the samples taken at a seat distance far from the median one.

    The seat distance comes out of the same geometry `--desk-report` uses (eye-corner span through the
    camera's focal length, so it is per-frame and free). A sample taken mid-lean is a sample of a
    DIFFERENT geometry: it drags the fit toward the midpoint of two seats, which is the same effect
    that needs a full recalibration once someone moves their chair. Catching it here keeps it out of
    the model, at the cost of a few samples that were not usable anyway.

    Returns (kept_samples, info). Unmeasurable samples are always kept - no distance, no opinion.
    """
    info = {"dropped": 0, "kept": len(samples), "median_mm": 0.0, "tolerance": float(tolerance)}
    if len(samples) < MIN_GATE_SAMPLES:
        return samples, info
    measured = np.array([s[5] for s in samples if s[5] > 0], dtype=np.float64)
    if measured.size < 8:
        return samples, info
    med = float(np.median(measured))
    mad = float(np.median(np.abs(measured - med)))
    # Never tighter than the estimator's own noise: 8% of 600 mm is 48 mm, which is about the
    # spread of the eye-span distance measurement itself, so a fixed percentage alone threw away
    # 29% of a real calibration. The bound is the wider of the tolerance and 3 median deviations.
    spread = max(float(tolerance) * med, 3.0 * mad)
    lo, hi = med - spread, med + spread
    kept = [s for s in samples if not (s[5] > 0) or lo <= s[5] <= hi]
    if len(kept) < MIN_GATE_SAMPLES:
        return samples, info               # the gate would eat the calibration: ignore it
    info.update(median_mm=med, spread_mm=round(spread, 1),
                dropped=len(samples) - len(kept), kept=len(kept),
                bounds_mm=(round(lo, 1), round(hi, 1)))
    return kept, info


def save_calibration_data(engine, samples, point_ids, monitor_of, pred, geo, monitors):
    """Write out every sample, with the desk geometry each dot came from.

    Nothing here should have to be re-performed to re-fit a model later, and keeping the desk data
    with the calibration is what makes a later layout change detectable instead of mysterious.

    Returns the path written, or None if it could not be written.
    """
    data_path = engine.model_path.with_suffix(".npz")
    try:
        panels = geo.layout.panels
        azimuth = np.array([
            math.degrees(math.atan2(
                panels[int(s[3])].x_mm
                + ((s[1] - monitors[int(s[3])].left) / max(monitors[int(s[3])].width, 1))
                * panels[int(s[3])].width_mm
                - geo.eye_offset_mm,
                geo.distance_mm or 1.0))
            for s in samples], dtype=np.float32)
        np.savez_compressed(
            data_path,
            features=np.array([s[0] for s in samples], dtype=np.float32),
            targets_px=np.array([[s[1], s[2]] for s in samples], dtype=np.float32),
            azimuth_deg=azimuth,
            dot_ids=np.array(point_ids, dtype=np.int32),
            monitors=np.array(monitor_of, dtype=np.int32),
            distances_mm=np.array([s[5] for s in samples], dtype=np.float32),
            eye_span_px=np.array([s[6] for s in samples], dtype=np.float32),
            in_sample_pred_px=np.asarray(pred, dtype=np.float32),
        )
    except Exception as exc:
        print(f"could not write {data_path.name}: {exc}")
        return None
    return data_path


def parse_args():
    ap = argparse.ArgumentParser(description="Kinesis gaze calibration")
    ap.add_argument("--camera", type=int, default=None)
    ap.add_argument("--points", type=int, default=5, choices=[0, 1, 5, 9],
                    help="dots per monitor (default 5); 0 = pick per screen from its angular size")
    ap.add_argument("--quick", action="store_true",
                    help="one dot per monitor (its centre): ~15 s, enough to pick a monitor")
    ap.add_argument("--dry-run", action="store_true",
                    help="sample and score but do not save the model")
    ap.add_argument("--distance-tolerance", type=float, default=0.08,
                    help="drop samples taken more than this fraction off the median seat distance")
    ap.add_argument("--monitors", default="", help="e.g. 1,3 to calibrate only those")
    ap.add_argument("--sample", type=float, default=1.0, help="seconds of sampling per point")
    ap.add_argument("--settle", type=float, default=0.7, help="seconds to look before sampling")
    ap.add_argument("--retries", type=int, default=2,
                    help="extra sampling passes for a dot that yielded almost nothing")
    ap.add_argument("--min-kept", type=int, default=5,
                    help="samples a dot needs before it counts as collected")
    args = ap.parse_args()
    if args.quick:
        args.points = 1                    # one dot per monitor: its centre
    return args


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
        ok, frame = cap.read()
        if ok:
            # Feed the landmarker during the settle, not just during sampling. EyeTrax decides what
            # counts as a blink from a rolling history of eye-aspect ratios; if that history is cold
            # the first frames of a dot get called blinks, which is how dots end up with no samples.
            engine.features_for(frame)
        root.update()
        time.sleep(0.005)

    def sample_once():
        out, blink, noface, dists = [], 0, 0, []
        end = time.perf_counter() + args.sample
        while time.perf_counter() < end:
            ok, frame = cap.read()
            if not ok:
                continue
            feats, is_blink = engine.features_for(frame)
            engine.measure_distance(frame)      # uses the landmarks that call just produced
            dist = engine.state.distance_mm
            span = engine.state.eye_span_px
            if dist > 0:
                dists.append(dist)
            if feats is None:
                noface += 1
            elif is_blink:
                blink += 1
            else:
                # the seat distance is kept with every sample so the fit can be hardened against the
                # samples taken while leaning in or back - see gate_by_distance()
                out.append((feats, float(tx), float(ty), mi, int(n), float(dist), float(span)))
            root.update()
        return out, blink, noface, dists

    out, blink, noface, dists = sample_once()
    # A dot looked at from the edge of the desk can be called a blink for its whole window. Retrying
    # costs a second and saves the monitor from being missing from the model altogether.
    for _ in range(max(int(args.retries), 0)):
        if len(out) >= int(args.min_kept):
            break
        extra, blink2, noface2, dists2 = sample_once()
        out.extend(extra)
        blink += blink2
        noface += noface2
        dists.extend(dists2)
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
    if args.monitors:
        idx = [int(x) - 1 for x in args.monitors.split(",")]
    else:
        # default to the screens the user enabled for tracking, so a calibration never starts by
        # putting dots on a screen they deliberately left out
        from kinesis import monitor_set as _ms
        kept, dropped_count = _ms.select_monitors(monitors, cfg["enabled_monitors"])
        if dropped_count:
            idx = [i for i, m in enumerate(monitors) if m in kept]
            print(f"calibrating the {len(kept)} enabled screen(s); "
                  f"{len(dropped_count)} left out of tracking")
            print(f"  (all screens: --monitors 1,2,3,4)")
        else:
            idx = list(range(len(monitors)))
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

    # ---- harden with the desk data: bin the samples taken while leaning in or back ------------
    samples, gating = gate_by_distance(samples, args.distance_tolerance)
    if gating["median_mm"]:
        lo, hi = gating.get("bounds_mm", (0.0, 0.0))
        print(f"seat distance  : {gating['median_mm']:.0f} mm "
              f"({gating['median_mm'] / 10:.0f} cm) measured while sampling")
        if gating["dropped"]:
            print(f"gating         : dropped {gating['dropped']} samples taken outside "
                  f"{lo:.0f}-{hi:.0f} mm (leaning in or back), kept {gating['kept']}")
        else:
            print("gating         : seat held steady, no samples dropped")

    monitor_of = [int(s[3]) for s in samples]
    point_ids = [int(s[4]) for s in samples]
    train_samples = [(s[0], s[1], s[2]) for s in samples]
    X = np.array([s[0] for s in train_samples], dtype=np.float32)
    y = np.array([[s[1], s[2]] for s in train_samples], dtype=np.float32)

    def mon_index(x: float, y: float):
        hit = w.monitor_at(int(x), int(y), monitors)
        if hit is None:
            return None
        return next((i for i, mm in enumerate(monitors) if mm.device == hit.device), None)

    n_points = len(set(point_ids))
    print(f"\n{len(train_samples)} samples from {n_points} dots")

    # ---- pick the model on dots it has not seen, then fit the winner on everything ---------
    chosen = None
    held_out = None
    sweep = []
    if n_points >= 5:
        sweep = select_model(train_samples, point_ids, mon_index)
        if sweep:
            print("model selection (leave-one-dot-out, ranked by dots targeted correctly):")
            for name, kwargs, med, hr, nd in sweep:
                print(f"   {name:<6} alpha={kwargs.get('alpha', 0):>10,.0f}  "
                      f"median {med:6.0f} px   monitor {hr:5.1f}%")
            name, kwargs, med, hr, _nd = sweep[0]
            chosen = (name, kwargs)
            held_out = hr
            default = next((m for n, k, m, _h, _d in sweep if k.get("alpha") == 1.0), None)
            extra = f" vs {default:.0f} px at EyeTrax's default alpha=1.0" if default else ""
            print(f"   -> {name} alpha={kwargs.get('alpha', 0):,.0f}: median {med:.0f} px, "
                  f"{hr:.0f}% of dots targeted correctly{extra}")

    if chosen is not None:
        from eyetrax.models import create_model
        engine._estimator.model = create_model(chosen[0], **chosen[1])

    if args.dry_run:
        engine._estimator.train(X, y)       # in memory only: a dry run must not overwrite a model
        print("(dry run - nothing will be saved)")
    else:
        engine.train(train_samples)
    pred = np.asarray(engine._estimator.predict(X))
    err = np.linalg.norm(pred - y, axis=1)
    hits = [mon_index(px, py) == mon_index(tx, ty)
            for (px, py), (_, tx, ty) in zip(pred, train_samples)]
    hit = sum(hits)
    pct = 100.0 * hit / len(train_samples)
    print(f"in-sample error : mean {err.mean():5.0f} px   median {np.median(err):5.0f} px   "
          f"p90 {np.percentile(err, 90):5.0f} px")
    print(f"monitor hit rate: {hit}/{len(train_samples)} = {pct:.1f}%   "
          f"<- what window targeting uses")

    # ---- the honest number: dots the model was never fitted on ---------------------------
    if held_out is not None:
        print(f"held-out hit rate: {held_out:.1f}%   <- the number to trust, not the one above")
    else:
        print(f"only {n_points} dots, so there is no unseen position to score on and the hit rate")
        print("above is in-sample. Run the full sweep (no --quick) for a real number.")

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

    # judge the fit on the held-out number when there is one: the in-sample figure is flattering
    verdict = held_out if held_out is not None else pct
    if verdict < 85.0:
        print(f"\nthat is low ({verdict:.0f}%). In order of what usually helps:")
        print("  1. sit stiller and keep your head level; do not follow the dot with your head")
        print("  2. sit closer: your screens are only "
              + (f"{min(geo.separation_deg()):.1f}" if geo.separation_deg() else "a few")
              + " degrees apart from that seat, which is tight for gaze")
        print("  3. light your face evenly - a window or lamp behind you wrecks accuracy")

    if args.dry_run:
        print("\ndry run: no model and no metadata written.")
        print("re-run without --dry-run to save it, then run run.bat")
        return 0

    engine.save_metadata({
        "trained_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "samples": len(train_samples),
        "dots": len(set(point_ids)),
        "quick": bool(args.quick),
        "monitor_hit_rate": round(pct, 1),
        "held_out_monitor_hit_rate": round(held_out, 1) if held_out is not None else None,
        "model": {"name": chosen[0], "kwargs": chosen[1]} if chosen else "eyetrax default",
        "model_selection": [
            {"name": n, "kwargs": k, "median_error_px": round(m, 1), "hit_rate": round(h, 1)}
            for n, k, m, h, _d in sweep
        ],
        "gating": gating,
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

    # ---- every sample, and the desk geometry each dot came from -----------------------------
    data_path = save_calibration_data(engine, samples, point_ids, monitor_of, pred, geo, monitors)
    if data_path is not None:
        print(f"saved -> {data_path}   {len(samples)} samples: features, targets, dot ids, "
              f"monitors, seat distance, eye span, desk azimuth")

    print(f"saved -> {engine.metadata_path}   (seat distance, camera and layout)")
    print("run run.bat, then look at a window and use a hand gesture on it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

