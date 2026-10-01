"""Re-fit the gaze model from the saved calibration samples - no new calibration needed.

`calibrate_gaze.py` already writes every sample it collected to `gaze_model.npz`
(features, target pixels, dot ids, monitors). The trained estimator is a separate pickle
(`gaze_model.pkl`), and that is the file that can be lost or corrupted - and when it is,
`GazeEstimator.load_model` raises `invalid load key` on a file that is not a pickle, while
`GazeEngine.is_calibrated` (a bare `.exists()`) still reports True. The result is the worst
possible failure mode: the app claims eye tracking is live and measures nothing.

Re-fitting is cheap and deterministic: the samples are the data, the model was chosen by a
swept alpha, so re-running the same fit on the same rows reproduces the same model. It reports
the same in-sample and held-out numbers the wizard reported, so you can tell whether the
recovery is sound BEFORE trusting the pointer.

    .venv\\Scripts\\python.exe -u tools\\retrain_gaze_model.py
    .venv\\Scripts\\python.exe -u tools\\retrain_gaze_model.py --alpha 1000 --dry
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis.config import ROOT                      # noqa: E402


def _monitor_of(points, virtual) -> int:
    """Index of the monitor a point lands on, given the virtual screen rect."""
    left, top, width, height = virtual
    for i, (mx, mw) in enumerate(points):
        if mx <= points[i][0] < mx + mw:
            return i
    return -1


def monitor_hits(pred, target, monitor_edges):
    """Fraction of predictions landing on the target's monitor, left-to-right.

    monitor_edges is a sorted list of x-offsets dividing the virtual desktop into monitors.
    """
    def idx(x):
        i = 0
        while i + 1 < len(monitor_edges) and x >= monitor_edges[i + 1]:
            i += 1
        return i
    return 100.0 * sum(1 for p, t in zip(pred, target) if idx(p[0]) == idx(t[0])) / max(len(target), 1)


def main() -> int:
    ap = argparse.ArgumentParser(description="re-fit the gaze model from saved samples")
    ap.add_argument("--npz", default=str(ROOT / "gaze_model.npz"), help="the saved samples")
    ap.add_argument("--out", default=str(ROOT / "gaze_model.pkl"), help="where to write the model")
    ap.add_argument("--alpha", type=float, default=None,
                    help="ridge alpha (default: whatever gaze_model.json recorded as chosen)")
    ap.add_argument("--dry", action="store_true", help="report the fit, write nothing")
    args = ap.parse_args()

    npz = Path(args.npz)
    if not npz.is_file():
        print(f"[FAIL] no sample file at {npz}")
        print("       the samples are only written by a calibration run, so the full wizard")
        print("       (calibrate_gaze.bat) is the only way to get them back.")
        return 1
    data = np.load(npz)
    X = np.asarray(data["features"], dtype=np.float32)
    y = np.asarray(data["targets_px"], dtype=np.float32)
    dot_ids = np.asarray(data["dot_ids"])
    if X.size == 0 or y.size == 0:
        print(f"[FAIL] {npz.name} holds no samples")
        return 1
    print(f"samples   {len(X)}   features {X.shape[1]}   dots {len(set(dot_ids.tolist()))}")

    # alpha: the one the wizard chose, so the recovery is the same model it scored
    alpha = args.alpha
    if alpha is None:
        try:
            import json
            meta = json.loads((npz.with_suffix(".json")).read_text(encoding="utf-8"))
            alpha = float(meta.get("model", {}).get("kwargs", {}).get("alpha", 1000.0))
            print(f"alpha     {alpha:g}  (from gaze_model.json - the model that was chosen)")
        except Exception:
            alpha = 1000.0
            print(f"alpha     {alpha:g}  (default; gaze_model.json unreadable)")
    elif args.dry:
        print(f"alpha     {alpha:g}  (from --alpha)")

    from eyetrax.models import create_model
    model = create_model("ridge", alpha=alpha)

    # leave-one-DOT-out, the honest number, exactly as the wizard computes it
    dots = sorted(set(dot_ids.tolist()))
    if len(dots) >= 3:
        held, held_n = [], 0
        for d in dots:
            te = dot_ids == d
            tr = ~te
            if tr.sum() < 10 or te.sum() == 0:
                continue
            m = create_model("ridge", alpha=alpha)
            m.train(X[tr], y[tr])
            p = np.asarray(m.predict(X[te]))
            t = y[te]
            err = np.linalg.norm(p - t, axis=1)
            held.extend(err.tolist())
            held_n += int(te.sum())
        if held:
            held = np.array(held)
            print(f"held-out  median {np.median(held):.0f} px   p90 {np.percentile(held, 90):.0f} px"
                  f"   over {held_n} samples on unseen dots")
            hp = np.array([m.predict(X[dot_ids == d])[0] for d in dots])
    model.train(X, y)
    pred = np.asarray(model.predict(X))
    err = np.linalg.norm(pred - y, axis=1)
    print(f"in-sample median {np.median(err):.0f} px   mean {err.mean():.0f} px   p90 "
          f"{np.percentile(err, 90):.0f} px")
    px = pred
    print(f"predicted x range {px[:, 0].min():.0f} .. {px[:, 0].max():.0f}   "
          f"y range {px[:, 1].min():.0f} .. {px[:, 1].max():.0f}")
    if px[:, 0].std() < 1.0 and px[:, 1].std() < 1.0:
        print("\n[FAIL] the fit is constant - the samples are degenerate, recalibrate properly.")
        return 1

    if args.dry:
        print("\ndry run: nothing written.")
        return 0

    out = Path(args.out)
    backup = out.with_suffix(".pkl.bak")
    if out.is_file() and out.stat().st_size > 64:
        backup.write_bytes(out.read_bytes())
        print(f"backed up the existing model -> {backup.name}")
    with out.open("wb") as fh:
        pickle.dump(model, fh)
    print(f"\nwrote {out} ({out.stat().st_size} bytes)")

    # prove it loads exactly the way GazeEngine loads it
    from eyetrax import GazeEstimator
    est = GazeEstimator()
    est.load_model(out)
    x, y2 = est.predict(X[:1])[0]
    print(f"[ok] loads through GazeEstimator: first sample predicts ({x:.0f}, {y2:.0f})")
    print("run check.bat (or run.bat) - the main loop will pick this up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
