"""Is gaze actually working? Live probe of every stage, with numbers.

Run it while sitting in front of the camera:

    .venv\\Scripts\\python.exe -u tools\\check_gaze.py
    .venv\\Scripts\\python.exe -u tools\\check_gaze.py --seconds 10 --save-preview

It reports each stage separately so a failure says *which* stage broke:

    camera -> face detected -> landmarks -> features -> blink -> model loaded -> prediction

Exit code is 0 when the pipeline produced gaze points (or when there is simply no model yet and
everything upstream works), 1 when a stage is broken.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                        # noqa: E402

from kinesis.config import Config                          # noqa: E402
from kinesis.gaze import GazeEngine                        # noqa: E402
from kinesis.vendored import ensure_eyetrax, eyetrax_source_dir   # noqa: E402


def open_camera(index: int, width: int = 640, height: int = 480):
    import cv2
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


def main() -> int:
    ap = argparse.ArgumentParser(description="Live gaze pipeline check")
    ap.add_argument("--seconds", type=float, default=6.0, help="how long to watch")
    ap.add_argument("--camera", type=int, default=-1, help="override camera index")
    ap.add_argument("--save-preview", action="store_true", help="write a preview PNG of the last frame")
    args = ap.parse_args()

    cfg = Config()
    index = args.camera if args.camera >= 0 else int(cfg["camera_index"])

    print("=" * 68)
    print(" Kinesis gaze check")
    print("=" * 68)

    # ---------------------------------------------------------------- stage 0: vendored source
    print(f"[1/6] vendored eyetrax .... ", end="", flush=True)
    src = eyetrax_source_dir()
    ok = ensure_eyetrax()
    if not ok:
        print("FAIL - no vendor/eyetrax source and nothing installed")
        return 1
    import eyetrax
    print(f"OK  v{eyetrax.__version__}  {src}")

    # ---------------------------------------------------------------- stage 1: camera
    print(f"[2/6] camera {index} ......... ", end="", flush=True)
    cap = open_camera(index)
    if cap is None:
        print("FAIL - could not open it (is another app holding it?)")
        return 1
    for _ in range(10):                                    # let exposure settle
        cap.read()
    ok, frame = cap.read()
    if not ok or frame is None:
        print("FAIL - opened but returned no frames")
        cap.release()
        return 1
    h, w = frame.shape[:2]
    print(f"OK  {w}x{h}")

    # ---------------------------------------------------------------- stage 2: landmarks + features
    engine = GazeEngine(cfg)
    print(f"[3/6] landmarker .......... ", end="", flush=True)
    try:
        estimator = engine._estimator or __import__(
            "eyetrax", fromlist=["GazeEstimator"]).GazeEstimator()
        engine._estimator = estimator
        engine._install_landmark_tap()
    except Exception as exc:
        print(f"FAIL - could not build GazeEstimator: {exc}")
        cap.release()
        return 1
    print("OK  (mediapipe face landmarker)")

    print(f"[4/6] watching {args.seconds:.0f}s ...")
    faces = blinks = misses = 0
    feat_len = 0
    t0 = time.perf_counter()
    last_pred = None
    predictions = []
    while time.perf_counter() - t0 < args.seconds:
        ok, frame = cap.read()
        if not ok:
            continue
        try:
            feats, blink = estimator.extract_features(frame)
        except Exception as exc:
            print(f"      extract_features raised: {exc}")
            misses += 1
            continue
        engine._update_distance(frame)                     # free, from the same landmarks
        if feats is None:
            misses += 1
            continue
        faces += 1
        feat_len = int(np.asarray(feats).size)
        if blink:
            blinks += 1
            continue
        if engine.is_calibrated:
            try:
                x, y = estimator.predict(np.array([feats]))[0]
                predictions.append((float(x), float(y)))
                last_pred = (float(x), float(y))
            except Exception as exc:
                print(f"      predict raised: {exc}")
        time.sleep(0.03)
    elapsed = time.perf_counter() - t0
    cap.release()

    st = engine.state
    print(f"      frames with a face .. {faces}")
    print(f"      no face ............. {misses}")
    print(f"      blinks .............. {blinks}")
    print(f"      feature length ...... {feat_len}")
    if st.distance_mm > 0:
        print(f"      seat distance ....... {st.distance_mm / 10:.1f} cm"
              f"{'' if st.distance_ok else '  (!) ' + st.distance_note}")

    # ---------------------------------------------------------------- stage 3: model
    print(f"[5/6] calibration ......... ", end="", flush=True)
    if engine.is_calibrated:
        print(f"OK  {engine.model_path.name}")
    else:
        print("NONE - gaze cannot point yet (run calibrate_gaze.bat)")

    # ---------------------------------------------------------------- stage 4: verdict
    print(f"[6/6] verdict ............. ", end="", flush=True)
    if faces == 0:
        print(f"FAIL - no face detected in {elapsed:.1f}s.")
        print("      Check: camera not blocked, good light, face within ~1m, whole face in frame.")
        return 1
    if feat_len == 0:
        print("FAIL - landmarks found but features empty.")
        return 1
    if not engine.is_calibrated:
        print("PIPELINE OK, not calibrated.")
        print(f"      Eye tracking works up to the model: {faces} faces, {blinks} blinks, "
              f"{feat_len} features.")
        print("      Next: .\\calibrate_gaze.bat  (~40 s of looking at dots)")
        return 0
    if not predictions:
        print("FAIL - model loaded but no prediction came out.")
        return 1

    arr = np.array(predictions)
    print(f"OK  {len(predictions)} points")
    print(f"      x range {arr[:, 0].min():.0f} .. {arr[:, 0].max():.0f}"
          f"   y range {arr[:, 1].min():.0f} .. {arr[:, 1].max():.0f}")
    print(f"      last point ({last_pred[0]:.0f}, {last_pred[1]:.0f})")
    spread_x = arr[:, 0].max() - arr[:, 0].min()
    spread_y = arr[:, 1].max() - arr[:, 1].min()
    if spread_x < 1 and spread_y < 1:
        print("      WARNING: the point never moved - the model is returning a constant.")
        return 1
    print("      Point moves with your eyes: gaze is live.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
