"""Three bugs that each made eye tracking report success while measuring nothing.

Every one of these is a "silent failure dressed as a working feature", which is the failure mode
this project keeps producing. Each test pins the specific behaviour so the bug cannot come back
as an `is_calibrated` that says True, a `start()` that returns True, or a camera that opens with
no frames.

1. `is_calibrated` was a bare `.exists()`, so a 1-byte non-pickle file "gaze_model.pkl" reported
   calibrated, skipped the calibration wizard, and then `start()` died with
   `invalid load key, 'x'`.
2. A unit test wrote that exact 1-byte file, at a RELATIVE path, with a cleanup that only ran
   when the file did not already exist - so running the suite against a calibrated project
   destroyed the trained model.
3. `mp.solutions.hands.Hands()` (hand tracking) permanently breaks the modern
   `mediapipe.tasks` file loader, so FaceLandmarker could never load its task file again in that
   process - and eye tracking and hand tracking MUST share a process.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis.config import Config                      # noqa: E402
from kinesis.gaze import GazeEngine                   # noqa: E402


# ------------------------------------------------------------------ 1. the is_calibrated lie
def test_a_one_byte_file_is_not_a_calibrated_model(tmp_path):
    """The exact damage: gaze_model.pkl was one byte containing 'x' and reported calibrated."""
    p = tmp_path / "gaze_model.pkl"
    p.write_text("x", encoding="utf-8")
    eng = GazeEngine(Config())
    eng.set_model_path(p)
    assert eng.is_calibrated is False, \
        "a 1-byte stub reported as calibrated - that is the lie that hid the failure"


def test_a_truncated_pickle_is_not_calibrated(tmp_path):
    p = tmp_path / "gaze_model.pkl"
    p.write_bytes(b"\x80\x04\x95\x01")            # pickle header, nothing else
    eng = GazeEngine(Config())
    eng.set_model_path(p)
    assert eng.is_calibrated is False, "a header with no model data is not a model"


def test_a_real_pickle_is_calibrated(tmp_path):
    """A REAL model must still pass. The magic byte is 0x80 alone; the version is a second byte.

    Comparing two bytes against b'\\x80' silently rejects every genuine model, which looks exactly
    like "my model is corrupt" - so the positive case is pinned here on purpose.
    """
    import pickle
    p = tmp_path / "gaze_model.pkl"
    p.write_bytes(pickle.dumps({"marker": "a model"}))
    eng = GazeEngine(Config())
    eng.set_model_path(p)
    assert eng.is_calibrated is True, \
        f"a genuine pickle was rejected; first byte is {p.read_bytes()[:1]!r}"


def test_a_missing_model_is_not_calibrated(tmp_path):
    eng = GazeEngine(Config())
    eng.set_model_path(tmp_path / "nope.pkl")
    assert eng.is_calibrated is False


def test_start_refuses_a_corrupt_model_and_says_why(tmp_path):
    """It must not raise, and the message must name the actual problem."""
    p = tmp_path / "gaze_model.pkl"
    p.write_text("x", encoding="utf-8")
    eng = GazeEngine(Config())
    eng.set_model_path(p)
    assert eng.start() is False
    assert "not a usable gaze model" in (eng.error or ""), eng.error


# ------------------------------------------------------------------ 2. the test that ate the model
def test_the_calibration_fixture_cannot_touch_the_real_model(tmp_path):
    """No test may be able to write to the project's gaze_model.pkl.

    The regression: `tests/test_inapp_calibration.py` wrote to the RELATIVE path
    `gaze_model.pkl` and restored only `if model_exists and not existed`. On a real, calibrated
    project the file DID exist, so the one-byte stub was left behind and the trained model was
    gone. A unit test that can destroy a calibration artifact is a data-loss bug, not a fixture.
    """
    root = Path(__file__).resolve().parent.parent
    source = (root / "tests" / "test_inapp_calibration.py").read_text(encoding="utf-8")
    assert 'Path("gaze_model.pkl")' not in source or "tmp_path" in source, \
        "test_inapp_calibration.py can still name the real model file by a relative path"
    assert "model.write_text" in source, "the fixture should still create a stub, but in tmp_path"
    # the model file the project actually uses must be a real pickle if it exists at all
    real = root / "gaze_model.pkl"
    if real.is_file():
        assert real.stat().st_size > 64, \
            f"{real.name} is {real.stat().st_size} bytes - it is a stub, not a trained model"


# ------------------------------------------------------------------ 3. hands vs face landmarker
def test_the_face_landmarker_loads_from_a_buffer_not_a_path():
    """Hand tracking and eye tracking must be able to share one process.

    `mp.solutions.hands.Hands()` permanently breaks mediapipe's task FILE loader: every later
    FaceLandmarker resolves an absolute path as if it were relative and raises
    `Unable to open file at <site-packages>/C:/Users/.../face_landmarker.task  errno=22`.
    Measured to survive close(), chdir, a new thread and every path spelling. Only
    `model_asset_buffer` is unaffected, because it never asks the loader to resolve anything.

    This builds the legacy hand model FIRST - reproducing the poison - and then requires the
    FaceLandmarker to still work.
    """
    import numpy as np
    import mediapipe as mp
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core.base_options import BaseOptions

    from kinesis.vendored import ensure_eyetrax
    if not ensure_eyetrax():
        pytest.skip("no eyetrax source and none installed")
    from eyetrax.gaze import _ensure_face_landmarker_task

    task = _ensure_face_landmarker_task(None)
    if not task.is_file():
        pytest.skip("the mediapipe face_landmarker.task is not downloaded")

    # poison the process exactly as the app does
    hands = mp.solutions.hands.Hands(static_image_mode=False, max_num_hands=2,
                                    model_complexity=0)
    try:
        # a path would fail here - assert the fix uses the buffer
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=task.read_bytes()),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1)
        lm = vision.FaceLandmarker.create_from_options(options)
        img = mp.Image(image_format=mp.ImageFormat.SRGB,
                       data=np.ascontiguousarray(np.zeros((480, 640, 3), np.uint8)))
        lm.detect_for_video(img, 1)          # a real load + inference
        lm.close()
    finally:
        try:
            hands.close()
        except Exception:
            pass


def test_the_vendored_eyetrax_passes_bytes_to_the_landmarker():
    """Pin the fix in the source itself: a path here is the bug that broke eye tracking."""
    root = Path(__file__).resolve().parent.parent
    src = (root / "vendor" / "eyetrax" / "src" / "eyetrax" / "gaze.py").read_text(encoding="utf-8")
    assert "model_asset_buffer" in src, \
        "vendor/eyetrax must load the face task from bytes; a path breaks once Hands() is built"
    assert "model_asset_path=str(task_path)" not in src, \
        "the path form is back - the landmarker will fail as soon as hand tracking is enabled"
