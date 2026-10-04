"""WHY is the gaze gain a million? The StandardScaler, not the ridge penalty.

The ridge sweep showed even alpha=10000 still threw the pointer 145,000 px for 0.2% feature noise,
which is not something a penalty can fix. The suspicion is the StandardScaler EyeTrax wraps every
model in: it divides each feature by that feature's standard deviation across the calibration
samples. A landmark that barely moved during calibration gets divided by a near-zero std, so an
invisible wobble in real use is amplified enormously before the regression ever sees it.

Measure the scale factors. If the ratio is huge, that is the amplifier, and the fix is to stop
feeding the model features whose calibration variance was noise.
"""
import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import kinesis                                                    # noqa: E402,F401

d = np.load(ROOT / "gaze_model.npz")
X = d["features"].astype(np.float64)
m = pickle.load(open(ROOT / "gaze_model.pkl", "rb"))

scale = np.asarray(m.scaler.scale_, dtype=np.float64)
mean = np.asarray(m.scaler.mean_, dtype=np.float64)
coef = np.asarray(m.model.coef_, dtype=np.float64)          # (2, 486)
print(f"features {X.shape[1]}, samples {X.shape[0]}")
print(f"scaler.scale_  min {scale.min():.4g}  median {np.median(scale):.4g}  "
      f"max {scale.max():.4g}")
print(f"ratio max/min  {scale.max() / max(scale.min(), 1e-12):,.0f}x")
print(f"scale_ > 100:  {int((scale > 100).sum())} features")
print(f"scale_ > 1000: {int((scale > 1000).sum())} features")

sd = X.std(axis=0)
print(f"\nraw per-feature std across calibration samples:")
print(f"  min {sd.min():.3e}  median {np.median(sd):.3e}  max {sd.max():.3e}")
dead = np.argsort(sd)[:12]
print(f"  12 smallest-variance features (index, std, 1/std):")
for i in dead:
    print(f"    {i:3d}  std {sd[i]:.3e}  scale_ {scale[i]:.4g}")

# Per-feature gain: how many output pixels does a 0.2% wobble in THIS feature alone produce?
gain = np.abs(coef).max(axis=0) * 0.002 / np.maximum(scale, 1e-12)
print(f"\nper-feature pointer gain from a 0.2% wobble (px):")
print(f"  median {np.median(gain):,.0f}   p90 {np.percentile(gain, 90):,.0f}   "
      f"max {gain.max():,.0f}")
order = np.argsort(gain)[::-1][:10]
print("  worst 10 features (index, gain px):")
for i in order:
    print(f"    {i:3d}  {gain[i]:,.0f} px")

# Total: independent 0.2% wobble on every feature at once.
rng = np.random.default_rng(0)
total = np.sqrt(np.sum(gain ** 2))
print(f"\nRSS of all 486 independent contributions: {total:,.0f} px")
print("That is the order of the excursion the stabiliser has to reject.")

# Does dropping the dead features fix it? Refit without the lowest-variance quarter.
keep = sd > np.percentile(sd, 25)
print(f"\nrefit check: keeping {int(keep.sum())}/{len(keep)} features "
      f"(dropping the quietest quarter)")
try:
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    for alpha in (1.0, 100.0, 1000.0):
        sc = StandardScaler().fit(X[:, keep])
        r = Ridge(alpha=alpha).fit(sc.transform(X[:, keep]), d["targets_px"])
        c = np.abs(r.coef_).max(axis=0)
        g = np.sqrt(np.sum((c * 0.002 / sc.scale_) ** 2))
        print(f"  alpha {alpha:>7}: RSS gain {g:>12,.0f} px")
except Exception as exc:
    print("  refit failed:", exc)
