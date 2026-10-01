"""The in-app calibration wizard has to be handed the camera, not fight the app for it.

`_maybe_calibrate` runs `calibrate_gaze.py` as a SUBPROCESS while the app is already up and
tracking. A webcam can only be opened by one process at a time, so the wizard died with

    could not open the camera - close anything else using it and retry

- a message aimed at the user, when the thing holding the camera was the app that asked the
question. Selecting "F"ull sweep from the prompt therefore never worked.

These tests pin the ORDER: engine.stop() before the wizard is launched, engine.start() after it
returns on every path, including failure. The engine is faked so no camera is needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kinesis.app as app                                     # noqa: E402


class FakeEngine:
    """Records the order of stop/start, and whether the camera is held between them."""

    def __init__(self, fail_on_restart: bool = False):
        self.calls: list = []
        self.held = True
        self.fail_on_restart = fail_on_restart
        self.held_during_wizard = None

    def stop(self):
        self.calls.append("stop")
        self.held = False

    def start(self):
        self.calls.append("start")
        if self.fail_on_restart:
            return False
        self.held = True
        return True


class FakeGaze:
    def __init__(self, model_exists: bool = True):
        self.model_path = Path("gaze_model.pkl")
        self._exists = model_exists

    def exists(self):                                          # pragma: no cover - unused
        return self._exists

    def __getattr__(self, name):
        # model_path.exists() is the only thing _maybe_calibrate asks of the gaze object
        if name == "model_path":
            return self.model_path
        raise AttributeError(name)


@pytest.fixture
def wizard(monkeypatch):
    """Answer the prompt with `answer`, and capture whether the camera was free at wizard time."""
    def run(answer: str, engine, model_exists: bool = True, rc: int = 0):
        calls = {"order": [], "camera_free": None}
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)

        import builtins
        real_input = builtins.input
        monkeypatch.setattr(builtins, "input", lambda _prompt="": answer)

        def fake_call(cmd, *a, **kw):
            calls["order"].append("wizard")
            calls["camera_free"] = not engine.held
            return rc

        monkeypatch.setattr(app.subprocess, "call", fake_call)
        # a temp model file so the "did it produce a model" check can be satisfied
        model = Path("gaze_model.pkl")
        existed = model.exists()
        if model_exists:
            model.write_text("x", encoding="utf-8")
        try:
            result = app._maybe_calibrate({}, FakeGaze(), engine)
        finally:
            if model_exists and not existed:
                model.unlink(missing_ok=True)
        return result, calls

    return run


def test_the_camera_is_released_before_the_wizard_starts(wizard):
    engine = FakeEngine()
    result, calls = wizard("F", engine)
    assert calls["camera_free"] is True, "the wizard ran while the app still held the camera"
    # two separate logs: engine.calls is stop/start, calls["order"] is where the wizard landed
    assert engine.calls == ["stop", "start"], f"wrong engine sequence: {engine.calls}"
    assert calls["order"] == ["wizard"], f"wizard ran {len(calls['order'])} times"


def test_the_camera_comes_back_after_a_successful_calibration(wizard):
    engine = FakeEngine()
    wizard("F", engine, model_exists=True)
    assert engine.held is True, "the app never reconnected the camera"
    assert engine.calls == ["stop", "start"]


def test_the_camera_comes_back_even_when_the_wizard_fails(wizard):
    """The important one: a failed calibration must not leave the app with no camera."""
    engine = FakeEngine()
    result, _ = wizard("F", engine, model_exists=False, rc=1)
    assert engine.held is True, "a failed wizard left the app with no camera"
    assert engine.calls == ["stop", "start"]


def test_quick_calibration_also_hands_the_camera_over(wizard):
    engine = FakeEngine()
    _, calls = wizard("Q", engine)
    assert calls["camera_free"] is True


def test_declining_calibration_never_touches_the_camera(wizard):
    engine = FakeEngine()
    result, calls = wizard("N", engine)
    assert result is False
    assert engine.calls == [], "the camera was released for a calibration that was declined"


def test_an_empty_answer_is_a_no(wizard):
    engine = FakeEngine()
    result, _ = wizard("", engine)
    assert result is False
    assert engine.calls == []
