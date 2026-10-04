"""Does 1280x720 buy ANY gaze accuracy, or only cost fps? - measured on ONE frame.

An earlier version of this probe grabbed a separate live frame at each resolution, so any difference
it reported was HEAD MOTION rather than resolution. This one captures a single 1280x720 frame and
derives the 640x480 version from that same frame by resampling, so the face is in exactly the same
place in both. Whatever difference survives is caused by pixel resolution alone.

Also measured: how far the pointer jumps for a given amount of feature noise, because that sets the
scale for "how much smoothing is worth it".
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kinesis                                                    # noqa: E402,F401
from eyetrax import GazeEstimator                                  # noqa: E402

MODEL = ROOT / "gaze_model.pkl"


def grab(w, h, settle=0.8):
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, 30)
    time.sleep(settle)
    frame = None
    for _ in range(40):
        ok, f = cap.read()
        if ok and f is not None:
            frame = f
    cap.release()
    return frame


cap_frame = grab(1280, 720)
if cap_frame is None:
    raise SystemExit("no frame from the camera")
print(f"one live frame captured: {cap_frame.shape}")

# the SAME instant, two resolutions
small = cv2.resize(cap_frame, (640, 480), interpolation=cv2.INTER_AREA)
small_bilin = cv2.resize(cap_frame, (640, 480), interpolation=cv2.INTER_LINEAR)

est = GazeEstimator()
est.load_model(MODEL)

print()
for label, img in (("640x480 INTER_AREA", small),
                   ("640x480 INTER_LINEAR", small_bilin),
                   ("1280x720 native", cap_frame)):
    feats, blink = est.extract_features(img)
    if feats is None:
        print(f"  {label:22s} no face detected")
        continue
    point = est.predict(np.array([feats]))[0]
    print(f"  {label:22s} face found, blink={blink}, predicted {np.round(point, 1)}")

fs, _ = est.extract_features(small)
fb, _ = est.extract_features(cap_frame)
if fs is None or fb is None:
    raise SystemExit("no face in the frame - sit in view and rerun")

a, b = np.asarray(fs, np.float64), np.asarray(fb, np.float64)
d = np.abs(a - b)
pa = np.asarray(est.predict(np.array([fs]))[0], dtype=np.float64)
pb = np.asarray(est.predict(np.array([fb]))[0], dtype=np.float64)
print()
print(f"same frame, 640x480 vs 1280x720, {d.size} feature values:")
print(f"  mean diff {d.mean():.6f}   median {np.median(d):.6f}   max {d.max():.6f}")
print(f"  identical to within 1e-6: {(d < 1e-6).mean() * 100:.1f}%")
print(f"  predicted point moved {np.round(np.abs(pa - pb), 1)} px on the desktop")

# How much does the pointer move per unit of feature noise? That is what a smoother is fighting.
print()
print("pointer movement per unit of feature noise (the scale that matters for smoothing):")
rng = np.random.default_rng(0)
base = np.asarray(fs, np.float64)
for sigma in (0.0, 0.002, 0.005, 0.01, 0.02, 0.05):
    pts = []
    for _ in range(8):
        noisy = base + rng.normal(0, sigma, base.shape)
        pts.append(np.asarray(est.predict(np.array([noisy.astype(np.float32)]))[0],
                              dtype=np.float64))
    pts = np.array(pts)
    print(f"  sigma={sigma:<6} -> spread {np.round(pts.max(axis=0) - pts.min(axis=0), 0)} px"
          f"   std {np.round(pts.std(axis=0), 0)} px")
