"""Why is the gaze pointer jittering? Sweep the ridge penalty on the SAVED samples.

Measured already, and it is not a smoothing problem:

    feature noise sigma=0.002 -> pointer spread 1,367,037 px
    feature noise sigma=0.005 -> pointer spread 3,093,144 px

Those are the numbers EyeTrax's own RidgeModel produces with its default `alpha=1.0` over 486
features fitted from 222 samples. 486 free weights against 222 points is rank-deficient, so the
model has an enormous gain in directions no sample ever constrained. Landmark jitter of 0.2% is
enough to fling the pointer across the desk a million times over. No deadband, One-Euro filter or
median window can fix that - they would have to discard almost all real motion.

The fix is to lower the model's gain, and the skill's own note says so: "rank models by dots
targeted, not pixel error... ridge alpha=1 gave the worst targeting (60%); alpha>=100 hit 100%".
The model that shipped is alpha=1, i.e. the option measured to be worst. This re-runs that sweep
against the real saved samples so the choice is made on current evidence, not an old note.
"""
import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import kinesis                                                    # noqa: E402,F401

from eyetrax.constants import (LEFT_EYE_INDICES, RIGHT_EYE_INDICES,  # noqa: E402
                               MUTUAL_INDICES)
from eyetrax.models.ridge import RidgeModel                        # noqa: E402

d = np.load(ROOT / "gaze_model.npz")
X = d["features"].astype(np.float64)
y = d["targets_px"].astype(np.float64)
dots = d["dot_ids"]
mons = d["monitors"]
print(f"saved samples: {X.shape[0]}  features: {X.shape[1]}  "
      f"dots: {len(set(dots.tolist()))}  monitors: {sorted(set(mons.tolist()))}")
print(f"target range: x {y[:, 0].min():.0f}..{y[:, 0].max():.0f}  "
      f"y {y[:, 1].min():.0f}..{y[:, 1].max():.0f}")

shipped = pickle.load(open(ROOT / "gaze_model.pkl", "rb"))
print(f"\nshipped model: {type(shipped).__name__}  alpha={getattr(shipped, 'alpha', '?')}")

# desk geometry, so "targeted" means a real screen not a virtual-desktop guess
MONS = {1: (-3840, 0, 1920, 1080), 2: (-1920, 0, 1920, 1080),
        3: (0, 0, 1920, 1080), 4: (1920, 0, 1920, 1080)}


def on_any_screen(pts, tol=200):
    for px, py in pts:
        if any(l - tol <= px <= l + w + tol and t - tol <= py <= t + h + tol
               for l, t, w, h in MONS.values()):
            return True
    return False


def evaluate(pred, yte, tol=200):
    """Score already-computed predictions. The predictions are (n, 2) screen points, so this must
    NOT hand them back to model.predict - that is a 2-column array where the scaler wants 486
    features, and it is how the first two runs of this sweep died instead of printing numbers."""
    pred = np.asarray(pred, dtype=np.float64).reshape(len(yte), -1)
    yte = np.asarray(yte, dtype=np.float64).reshape(len(yte), -1)
    err = np.linalg.norm(pred - yte, axis=1)
    return float(np.median(err)), float(err.mean()), int(on_any_screen(pred)), len(pred)


def predict2(model, X):
    """Eyetrax models scale their input, so a (1, 486) matrix must stay 2-D.

    `np.array([row])` on a 2-column target builds (1, n, 2) and silently flattens to 2 features
    downstream - which is why the first run of this sweep died in StandardScaler rather than
    reporting a wrong number.
    """
    return np.asarray(model.predict(np.asarray(X, dtype=np.float32)),
                      dtype=np.float64).reshape(len(X), -1)


rng = np.random.default_rng(0)

print()
print(f"{'alpha':>8} {'holdout median px':>18} {'holdout mean px':>17} "
      f"{'dots hit':>10} {'gain: 0.2% noise':>19}")
print("-" * 78)
for alpha in (1, 10, 30, 100, 300, 1000, 3000, 10000):
    # LEAVE-ONE-DOT-OUT: hold out every sample from a dot the model has never seen.
    held_pred, held_true = [], []
    for dot in sorted(set(dots.tolist())):
        te = dots == dot
        tr = ~te
        m = RidgeModel(alpha=float(alpha))
        m.train(X[tr], y[tr])
        held_pred.append(predict2(m, X[te]))
        held_true.append(y[te])
    med, mean, hit, n = evaluate(np.vstack(held_pred), np.vstack(held_true))

    # noise response: how far does 0.2% feature noise throw the pointer?
    full = RidgeModel(alpha=float(alpha))
    full.train(X, y)
    base = X[0]
    pts = []
    for _ in range(40):
        noisy = base + rng.normal(0, 0.002, base.shape)
        pts.append(predict2(full, noisy.reshape(1, -1)).ravel())
    pts = np.array(pts)
    spread = float(np.median(np.linalg.norm(pts - pts.mean(axis=0), axis=1)))
    print(f"{alpha:>8} {med:>18.0f} {mean:>17.0f} {hit:>6}/{n:<3} {spread:>15,.0f} px")
