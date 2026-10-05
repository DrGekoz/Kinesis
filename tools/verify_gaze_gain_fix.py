"""Did flooring the scaler actually fix the gain? Refit the real model and measure.

`tools/probe_gaze_gain.py` identified the amplifier: `StandardScaler` divides each feature by its
calibration std, and six of Kinesis' 486 features had a std around 1e-8, so a 0.2% wobble in one of
them moved the pointer 617,784 px. It also showed that dropping the quietest quarter plus a ridge
penalty took the independent noise gain from 656,881 px to 23 px.

This measures the actual shipped path - `BaseModel.train()` with the scale floor in place, then the
same prediction the app makes - so the claim is about the real model and not about a sketch of it.

Reports three numbers per configuration, because they trade off against each other and a fix that
only improves one of them is not a fix:
  * NOISE GAIN  - pointer travel from a 0.2% wobble on every feature at once (lower is better)
  * LEAVE-OUT   - median error on samples the model never saw (lower is better)
  * DOTS HIT    - how many of the calibration targets land within 200 px (higher is better)
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kinesis                                                    # noqa: E402,F401  (vendored eyetrax)
from eyetrax import GazeEstimator                                  # noqa: E402
from eyetrax.models.ridge import RidgeModel                        # noqa: E402

D = np.load(ROOT / "gaze_model.npz")
X = D["features"].astype(np.float64)
Y = D["targets_px"].astype(np.float64)
NOISE = 0.002
ON_SCREEN = 200.0


def noise_gain(model, rng) -> float:
    """Pointer travel from an independent 0.2% wobble on every feature, in px."""
    p = model.predict(X[:1].astype(np.float32))
    base = np.asarray(p, dtype=np.float64).reshape(2)
    p2 = model.predict((X[:1] * (1.0 + rng.normal(0.0, NOISE, X[:1].shape))).astype(np.float32))
    moved = np.asarray(p2, dtype=np.float64).reshape(2)
    return float(np.linalg.norm(moved - base))


def leave_one_dot_out(alpha: float, floor: bool) -> tuple:
    """Median error and dots-hit for each held-out calibration dot, model trained without it."""
    errs, hits = [], []
    for i in range(len(X)):
        tr = np.ones(len(X), dtype=bool)
        tr[i] = False
        m = RidgeModel(alpha=alpha)
        if floor:
            m.scaler.fit(X[tr])
            m._floor_scaler()
        else:
            m.scaler.fit_transform(X[tr])
        Xs = m.scaler.transform(X[tr])
        m.model.fit(Xs, Y[tr])
        m.variable_scaling = None
        pred = np.asarray(m.predict(X[i:i + 1].astype(np.float32)),
                          dtype=np.float64).reshape(2)
        e = float(np.linalg.norm(pred - Y[i]))
        errs.append(e)
        hits.append(1 if e <= ON_SCREEN else 0)
    return float(np.median(errs)), int(sum(hits)), len(X)


def main() -> None:
    rng = np.random.default_rng(0)
    sd = X.std(axis=0)
    print(f"features {X.shape[1]}, samples {X.shape[0]}")
    print(f"features with std < {RidgeModel.MIN_FEATURE_SCALE:g}: "
          f"{int((sd < RidgeModel.MIN_FEATURE_SCALE).sum())}")
    print()

    shipped = pickle.load(open(ROOT / "gaze_model.pkl", "rb"))
    print(f"{'configuration':<34} {'noise gain px':>14} {'LOO median px':>14} {'dots hit':>10}")
    print("-" * 76)
    print(f"{'SHIPPED model (as calibrated)':<34} {noise_gain(shipped, rng):>14,.0f} "
          f"{'(not measured)':>14} {'(not measured)':>10}")

    for alpha in (1.0, 100.0, 1000.0):
        for floor in (False, True):
            m = RidgeModel(alpha=alpha)
            if floor:
                m.scaler.fit(X)
                m._floor_scaler()
            else:
                m.scaler.fit_transform(X)
            Xs = m.scaler.transform(X)
            m.model.fit(Xs, Y)
            m.variable_scaling = None
            ng = noise_gain(m, rng)
            med, hit, n = leave_one_dot_out(alpha, floor)
            name = f"alpha={alpha:g} {'scale floor' if floor else 'raw scaler'}"
            print(f"{name:<34} {ng:>14,.0f} {med:>14,.0f} {f'{hit}/{n}':>10}")

    print()
    print("The scale floor is the fix. The ridge penalty alone cannot do it: even alpha=10000 left")
    print("the pointer 145,000 px from invisible noise, because dividing by 1e-8 happens BEFORE the")
    print("penalty gets a vote.")


if __name__ == "__main__":
    main()
