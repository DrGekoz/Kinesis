"""The pointer follows the eyes (v1.11.0): one decision point, and never a dead mouse."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis.app import KinesisApp                                # noqa: E402
from kinesis.config import Config                                 # noqa: E402
from kinesis.gaze import GazeState                                # noqa: E402


def _app(**cfg_overrides):
    cfg = Config()
    for key, value in cfg_overrides.items():
        cfg.set(key, value)
    app = KinesisApp(cfg, dry=True, preview=False)
    app.gaze._estimator = object()          # a "model is loaded" stand-in
    return app


def _gaze(x, y, valid=True):
    return GazeState(x=x, y=y, valid=valid, age=0.0)


def test_the_eyes_drive_the_pointer():
    app = _app()
    moves = app._gaze_cursor(_gaze(-1000.0, 400.0))
    assert moves and moves[0].kind == "cursor.move"
    assert (moves[0].x, moves[0].y) == (-1000, 400)
    assert app.gestures.cursor_from_hand is False, "the hand must be locked out of the pointer"


def test_the_first_gaze_sample_is_taken_as_is():
    """No smoothing on the first sample, or the pointer would crawl in from nowhere."""
    app = _app()
    first = app._gaze_cursor(_gaze(500.0, 300.0))[0]
    assert (first.x, first.y) == (500, 300)


def test_a_resting_eye_does_not_jitter_the_pointer():
    app = _app()
    app._gaze_cursor(_gaze(1000.0, 500.0))
    assert app._gaze_cursor(_gaze(1001.0, 501.0)) == [], "1 px of eye noise moved the pointer"


def test_a_real_look_moves_it():
    app = _app()
    app._gaze_cursor(_gaze(1000.0, 500.0))
    moves = app._gaze_cursor(_gaze(1400.0, 500.0))
    assert moves, "a 400 px look did not move the pointer"


def test_smoothing_slows_the_pointer_without_stopping_it():
    app = _app(gaze_cursor_smoothing=0.8, gaze_cursor_deadband_px=0.0)
    app._gaze_cursor(_gaze(0.0, 0.0))
    first = app._gaze_cursor(_gaze(1000.0, 0.0))[0]
    assert 0 < first.x < 1000, f"expected a smoothed step, got {first.x}"


def test_looking_away_holds_the_pointer_by_default():
    """Gaze loss must never snap the pointer back to a hand - there is no hand pointer any more."""
    app = _app(cursor_fallback="hold")
    app._gaze_cursor(_gaze(600.0, 400.0))
    assert app._gaze_cursor(_gaze(600.0, 400.0, valid=False)) == []
    assert app.gestures.cursor_from_hand is False


def test_before_calibration_the_hand_can_still_drive_it():
    """A mouse that cannot move is worse than a calibration nag, so the fallback exists."""
    app = _app(cursor_fallback="hand")
    app.gaze._estimator = None              # no model yet
    assert app._gaze_cursor(_gaze(0.0, 0.0)) == []
    assert app.gestures.cursor_from_hand is True


def test_the_hand_takes_over_when_the_pointer_is_begged_to_come_from_it():
    app = _app(cursor_source="hand")
    app._gaze_cursor(_gaze(700.0, 700.0))
    assert app.gestures.cursor_from_hand is True


def test_gaze_still_owns_the_pointer_after_a_fallback_frame():
    """Calibration completes mid-session: the eyes must take back over without a restart."""
    app = _app(cursor_fallback="hand")
    app.gaze._estimator = None
    app._gaze_cursor(None)
    assert app.gestures.cursor_from_hand is True
    app.gaze._estimator = object()
    app._gaze_cursor(_gaze(300.0, 200.0))
    assert app.gestures.cursor_from_hand is False
