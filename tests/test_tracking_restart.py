"""The tracking engine must be restartable, because the in-app calibration hands the camera back.

v1.17.0 added the camera handoff: the engine is stopped so `calibrate_gaze.py` can have the webcam,
then started again afterwards. It worked, and the app **exited** on the way out - because
`TrackingEngine` was never built to be restarted:

1. `CameraThread._stop` is a `threading.Event` that `stop()` SETS and never clears, so a restarted
   camera thread returns immediately from `while not self._stop.is_set()` and never captures.
2. A `Thread` cannot be started twice (`RuntimeError: threads can only be started once`), and the
   old one is genuinely dead.

So `start()` now rebuilds both. These tests use a stub camera so no webcam is needed, and they
assert the two properties that actually broke: a fresh stop-event, and a fresh thread object.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis.config import Config                               # noqa: E402
import kinesis.tracking as tracking                             # noqa: E402


class StubCamera:
    """Behaves like CameraThread for the things `start`/`stop` depend on."""

    instances: list = []

    def __init__(self, index, width, height, backend=""):
        self.index, self.width, self.height, self.backend = index, width, height, backend
        self.opened = False
        self._stop = threading.Event()
        self._alive = False
        self.actual_size = (width, height)
        self.error = None
        # flipped by the tests that exercise the "opened but no frames" failure
        self.frames_arriving = True
        StubCamera.instances.append(self)

    def open(self) -> bool:
        self.opened = True
        return True

    def verify_streaming(self, seconds: float = 3.0) -> bool:
        # Mirrors the real contract: on failure it SETS self.error, because that message is the
        # only thing the user sees. A stub that returned False silently would let the test pass
        # while the app printed nothing - which is the bug this whole check exists to prevent.
        if self.frames_arriving:
            self.error = None
            return True
        self.error = (f"camera {self.index} ({self.backend}) opened but delivered NO frames - "
                      f"another program is holding the webcam.")
        return False

    def start(self):
        self._stop.clear()          # what the real one cannot do - the point of the fix
        self._alive = True

    def stop(self):
        self._stop.set()
        self.opened = False
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive


class StubInference:
    instances: list = []

    def __init__(self, camera, cfg):
        self.camera, self.cfg = camera, cfg
        self._alive = False
        self._stop = threading.Event()
        StubInference.instances.append(self)

    def start(self):
        self._stop.clear()
        self._alive = True

    def stop(self):
        self._stop.set()
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout=None):
        self._alive = False

    def close(self):
        pass


@pytest.fixture
def engine(monkeypatch):
    StubCamera.instances.clear()
    StubInference.instances.clear()
    monkeypatch.setattr(tracking, "CameraThread", StubCamera)
    monkeypatch.setattr(tracking, "InferenceThread", StubInference)
    return tracking.TrackingEngine(Config())


def test_the_engine_starts(engine):
    assert engine.start() is True
    assert engine.inference.is_alive()


def test_the_engine_starts_again_after_a_stop(engine):
    """The v1.17.0 crash: a restart must not raise, and must leave a live thread."""
    assert engine.start() is True
    first_camera, first_inference = engine.camera, engine.inference
    engine.stop()
    assert engine.start() is True, "restart returned False - the app would exit here"
    assert engine.inference.is_alive(), "the restarted inference thread is not running"
    assert engine.camera is not first_camera, \
        "the camera object was reused; its _stop Event is already set so it cannot capture"
    assert engine.inference is not first_inference, \
        "the inference thread was reused; a Thread cannot be started twice"


def test_a_restarted_camera_is_not_forever_stopped(engine):
    """The concrete symptom of a stale stop-event: the loop returns immediately and never runs."""
    assert engine.start() is True
    engine.stop()
    assert engine.start() is True
    assert not engine.camera._stop.is_set(), \
        "the restarted camera still has its stop flag set, so its run loop exits immediately"


def test_stop_is_safe_to_call_twice(engine):
    assert engine.start() is True
    engine.stop()
    engine.stop()          # must not raise


def test_start_is_idempotent_while_already_running(engine):
    """Starting twice without stopping must not leak a camera handle."""
    assert engine.start() is True
    before = len(StubCamera.instances)
    assert engine.start() is True
    assert len(StubCamera.instances) == before, "a second camera was opened while already running"


def test_a_camera_that_opens_but_delivers_nothing_stops_the_engine(monkeypatch):
    """The real-world failure: VRChat held the C920, `isOpened()` said True, frames were zero.

    Before this, the app started normally with nothing to track - camera "open", loop spinning,
    HUD drawing - and every status line implied eye tracking was live. `start()` must now
    refuse, so the user gets a sentence about the camera instead of a mystery.
    """
    monkeypatch.setattr(tracking, "CameraThread", StubCamera)
    monkeypatch.setattr(tracking, "InferenceThread", StubInference)
    engine = tracking.TrackingEngine(Config())

    real_init = StubCamera.__init__

    def silent_camera(*a, **kw):
        real_init(*a, **kw)
        StubCamera.instances[-1].frames_arriving = False   # opens, then delivers nothing

    monkeypatch.setattr(StubCamera, "__init__", silent_camera)
    assert engine.start() is False, "the engine started on a camera that never produces frames"
    assert "NO frames" in (engine.camera.error or ""), \
        f"the error must name the real problem, got: {engine.camera.error!r}"
    assert engine.inference is None, "the inference thread must not start without frames"


def test_a_real_camera_survives_a_stop_start_cycle():
    """The genuine test, on real hardware: open, stop, start, and read a frame again.

    Skipped when the camera is unavailable, but on Joe's machine this is the whole bug - the stub
    proves the wiring, this proves the device actually comes back.
    """
    import cv2

    cfg = Config()
    engine = tracking.TrackingEngine(cfg)
    if not engine.start():
        pytest.skip("camera not available in this environment")
    try:
        engine.stop()
        time.sleep(1.0)                    # DSHOW needs a moment to release the device
        assert engine.start() is True, "the camera would not reopen after a stop/start cycle"
        deadline = time.time() + 6.0
        got_frame = False
        while time.time() < deadline and not got_frame:
            _poses, frame, _lat = engine.update()
            if frame is not None:
                got_frame = True
            else:
                time.sleep(0.1)
        assert got_frame, "the restarted engine produced no frames - the threads are not running"
    finally:
        engine.stop()
