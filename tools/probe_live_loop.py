"""Live probe of the EXACT main-loop gaze path, stage by stage, with numbers.

`tools/check_gaze.py` opens its own camera and drives the estimator directly. That proves the
gaze stack works, but NOT that the app's main loop measures anything - and "gaze was started but
did not measure" is a main-loop bug, not a stack bug. This drives the real objects the app
uses, in the real order, and says which stage stops producing data:

    TrackingEngine.start -> engine.update -> engine.raw_frame
      -> GazeEngine.update -> features / blink / predict -> GazeState.valid
      -> KinesisApp._gaze_cursor -> cursor.move intents

Everything is printed per second so a failure says WHICH stage went quiet.

    .venv\\Scripts\\python.exe -u tools\\probe_live_loop.py --seconds 8
    .venv\\Scripts\\python.exe -u tools\\probe_live_loop.py --seconds 8 --move-cursor
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis import winapi as w                                  # noqa: E402
from kinesis.app import KinesisApp                               # noqa: E402
from kinesis.config import Config                                # noqa: E402
from kinesis.gaze import GazeEngine                              # noqa: E402
from kinesis.tracking import TrackingEngine                      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="probe the live main-loop gaze path")
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--move-cursor", action="store_true",
                    help="actually let the pointer follow the gaze (needs a valid model)")
    args = ap.parse_args()

    w.set_dpi_aware()
    cfg = Config.load()
    print("=" * 74)
    print(" live main-loop gaze probe")
    print("=" * 74)
    print(f"gaze_enabled      {cfg['gaze_enabled']}")
    print(f"gaze_hz           {cfg['gaze_hz']}   (interval {1.0 / max(float(cfg['gaze_hz']), 1.0) * 1000:.0f} ms)")
    print(f"gaze_smoother     {cfg['gaze_smoother']}  ema_alpha {cfg['gaze_ema_alpha']}")
    print(f"gaze_max_age_s    {cfg['gaze_max_age_s']}")
    print(f"cursor_source     {cfg['cursor_source']}   cursor_fallback {cfg['cursor_fallback']}")
    print(f"virtual screen    {w.virtual_screen()}")

    # --- the app's own wiring, without starting the whole app -------------------------
    import os
    app = KinesisApp(cfg)
    engine: TrackingEngine = app.engine
    gaze: GazeEngine = app.gaze
    print(f"cwd after KinesisApp  {os.getcwd()}")
    print(f"sys.path[0]          {sys.path[0] if sys.path else None}")

    if not engine.start():
        print(f"\n[FAIL] camera: {engine.camera.error}")
        return 1
    frame_w, frame_h = engine.frame_size
    print(f"camera            {frame_w}x{frame_h}")
    # print the exact task path mediapipe is about to be handed
    try:
        import eyetrax.gaze as egz
        tp = egz._ensure_face_landmarker_task(None)
        print(f"landmarker task   {tp!r}  exists={tp.exists()}  isabs={tp.is_absolute()}")
    except Exception as exc:
        print(f"landmarker task   could not resolve: {exc}")
    print(f"cwd before start  {os.getcwd()}")
    started = gaze.start()
    print(f"gaze.start()      {started}  estimator={'live' if gaze._estimator else 'NONE'}"
          f"  is_calibrated={gaze.is_calibrated}  error={gaze.error}")
    if not started:
        print("\n[FAIL] gaze did not start - this is why the main loop measures nothing.")
        engine.stop()
        return 1
    app._build_geometry(frame_w, frame_h)

    print(f"\nwatching {args.seconds:.0f}s (one line per second)")
    print("-" * 74)
    last = time.perf_counter()
    raw_seen = infer_seen = 0
    prev_inferred = 0
    prev_gaze_frames = 0
    try:
        end = last + args.seconds
        while time.perf_counter() < end:
            now = time.perf_counter()
            poses, frame, latency = engine.update()
            raw = engine.raw_frame()
            if raw is not None:
                raw_seen += 1
            if engine.inference is not None and engine.inference.inferred != prev_inferred:
                infer_seen += 1
                prev_inferred = engine.inference.inferred

            gaze_state = gaze.update(raw, now)
            intents = app._gaze_cursor(gaze_state)
            moved = [i for i in intents if i.kind == "cursor.move"]

            if gaze.frames != prev_gaze_frames:
                prev_gaze_frames = gaze.frames
            elapsed = now - last
            if elapsed >= 1.0:
                last = now
                st = gaze.state
                print(f"t+{end - now:4.0f}s  cap {engine.camera.fps:4.1f}fps  "
                      f"inf {engine.stats.inference_fps:4.1f}fps  "
                      f"raw {'Y' if raw_seen else 'n'}  "
                      f"gazeRuns {gaze.frames:3d}  faces {gaze.faces:3d}  "
                      f"miss {gaze.misses:3d}  blink {gaze.blinks:3d}  "
                      f"valid {'Y' if st.valid else 'n'}  age {st.age * 1000:5.0f}ms  "
                      f"face {'Y' if st.face else 'n'}  "
                      f"pt ({int(st.x):5d},{int(st.y):5d})  "
                      f"moves {len(moved):3d}  pred {gaze.predict_ms:4.1f}ms"
                      + ("  [BLINK]" if st.blink else ""))
                if moved:
                    if args.move_cursor:
                        from kinesis.actions import ActionRunner
                        ActionRunner(cfg, app.monitors, dry=False).execute(moved)
                    else:
                        print(f"          first move -> {moved[0]}")
    finally:
        engine.stop()

    print("-" * 74)
    st = gaze.state
    print(f"TOTAL  gaze runs {gaze.frames}  faces {gaze.faces}  misses {gaze.misses}  "
          f"blinks {gaze.blinks}  predict {gaze.predict_ms:.1f} ms")
    print(f"       final valid={st.valid} age={st.age:.2f}s point=({st.x:.0f},{st.y:.0f})")
    print(f"       gaze.error = {gaze.error!r}")
    print(f"       gestures.cursor_from_hand = {app.gestures.cursor_from_hand} "
          f"(True = the HAND is driving the pointer, not the eyes)")

    ok = gaze.frames > 0 and gaze.faces > 0 and gaze.misses < gaze.frames * 0.5
    if gaze.frames == 0:
        print("\n[FAIL] GazeEngine.update() never ran inference. The frame is not reaching it.")
    elif gaze.faces == 0:
        print("\n[FAIL] inference ran but no face was found. Camera/lighting/face-in-frame.")
    elif gaze.misses > gaze.frames * 0.5:
        print(f"\n[FAIL] {gaze.misses}/{gaze.frames} frames had no face - intermittent detection")
    elif not st.valid:
        print(f"\n[FAIL] predictions exist but the state is stale (age {st.age:.2f}s > "
              f"gaze_max_age_s {cfg['gaze_max_age_s']}) - every blink is throwing the point away")
    else:
        print("\n[ok] the main loop is measuring eye gaze.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
