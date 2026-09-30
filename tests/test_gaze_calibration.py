"""Vendored EyeTrax, and the calibration scoring that has to tell the truth.

The LOO harness is exercised with a stub estimator so it needs no camera and no mediapipe: what is
being tested is the fold construction (leave out a whole DOT, not a sample) and the arithmetic.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                            # noqa: E402

import calibrate_gaze as cg                                   # noqa: E402
from kinesis.vendored import ensure_eyetrax, eyetrax_source_dir   # noqa: E402


# --------------------------------------------------------------------------- vendoring
def test_vendored_eyetrax_is_in_the_repo():
    """Users must not have to install eyetrax: the source has to be in vendor/."""
    src = eyetrax_source_dir()
    assert src is not None, "vendor/eyetrax/src is missing - a fresh clone would not import"
    assert (src / "eyetrax" / "__init__.py").is_file()
    assert (src / "eyetrax" / "gaze.py").is_file()


def test_ensure_eyetrax_makes_the_import_work():
    assert ensure_eyetrax() is True
    import eyetrax
    assert eyetrax.__file__.replace("\\", "/").endswith("eyetrax/__init__.py")
    from eyetrax import GazeEstimator                    # the two things kinesis/gaze.py needs
    from eyetrax.filters import KalmanEMASmoother        # noqa: F401
    assert GazeEstimator is not None


def test_vendored_licence_is_kept():
    """Redistributing MIT code means shipping its licence."""
    licence = eyetrax_source_dir().parent / "LICENSE"
    assert licence.is_file(), "vendor/eyetrax/LICENSE is missing"
    assert "MIT License" in licence.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- LOO scoring
class _StubEstimator:
    """Least-squares stand-in for EyeTrax's estimator: only train/predict are used by the harness."""

    def __init__(self):
        self.weights = None

    def train(self, X, y):
        A = np.hstack([X, np.ones((len(X), 1))])
        self.weights, *_ = np.linalg.lstsq(A, y, rcond=None)

    def predict(self, X):
        if self.weights is None:
            raise RuntimeError("not trained")
        return np.hstack([X, np.ones((len(X), 1))]) @ self.weights


class _StubEngine:
    def __init__(self):
        self._estimator = _StubEstimator()


def _samples(dots=6, per_dot=4, noise=0.0, seed=0):
    """Feature -> screen is linear, so a correct harness reports a near-zero held-out error."""
    rng = np.random.default_rng(seed)
    samples, ids = [], []
    for d in range(dots):
        fx = np.zeros(3)
        fx[0] = d / max(dots - 1, 1)
        tx, ty = 200.0 + 400.0 * fx[0], 300.0 - 100.0 * fx[0]
        for _ in range(per_dot):
            feats = fx + (rng.normal(0, noise, 3) if noise else 0)
            samples.append((feats.astype(np.float32), tx, ty, 0, d))
            ids.append(d)
    return samples, ids


def _to_xy(samples):
    return [(s[0], s[1], s[2]) for s in samples]


def test_loo_scores_an_unseen_dot_and_is_small_for_a_fit_that_generalises():
    samples, ids = _samples()
    errors, per_point = cg.leave_one_point_out(_StubEngine(), _to_xy(samples), ids)
    assert per_point, "no folds were scored"
    assert len(per_point) <= len(set(ids))
    assert np.mean(errors) < 5.0, "a linear mapping should predict held-out dots almost exactly"


def test_loo_leaves_out_a_whole_dot_not_a_single_sample():
    """If folds leaked samples from the same dot, a memorising model would score as perfect."""
    samples, ids = _samples(dots=4, per_dot=5)
    engine = _StubEngine()
    seen = []
    real_train = engine._estimator.train

    def spy(X, y):
        seen.append(len(X))
        return real_train(X, y)

    engine._estimator.train = spy
    cg.leave_one_point_out(engine, _to_xy(samples), ids)
    assert seen and all(n == 15 for n in seen), f"expected 15 of 20 training samples, got {seen}"


def test_loo_reports_a_large_error_for_a_model_that_cannot_generalise():
    """A constant model must not be able to claim accuracy."""
    rng = np.random.default_rng(3)
    samples, ids = [], []
    for d in range(6):
        for _ in range(4):
            samples.append((rng.normal(0, 1, 3).astype(np.float32),
                            400.0 * d, 300.0 * (d % 2), 0, d))
            ids.append(d)
    errors, _ = cg.leave_one_point_out(_StubEngine(), _to_xy(samples), ids)
    assert np.mean(errors) > 50.0, "a signal-free mapping must score badly"


def test_loo_skips_folds_with_too_little_data():
    samples, ids = _samples(dots=2, per_dot=3)
    errors, per_point = cg.leave_one_point_out(_StubEngine(), _to_xy(samples), ids)
    assert errors == [] and per_point == [], "tiny folds must be skipped, not scored"


# --------------------------------------------------------------------------- quick mode
def test_quick_points_are_the_monitor_centre():
    class Mon:
        left, top, width, height = -1920, 0, 1920, 1080

    pts = cg.grid_points(Mon(), 1)
    assert pts == [(Mon.left + Mon.width * 0.5, Mon.top + Mon.height * 0.5)]


def test_quick_and_dry_run_flags_exist(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["calibrate_gaze.py", "--quick", "--dry-run"])
    args = cg.parse_args()
    assert args.quick is True and args.dry_run is True


def test_dots_per_monitor_defaults_to_five_and_quick_overrides(monkeypatch):
    """5 dots per screen on every monitor is the default; --quick drops to the centre dot only."""
    monkeypatch.setattr(sys, "argv", ["calibrate_gaze.py"])
    assert cg.parse_args().points == 5
    monkeypatch.setattr(sys, "argv", ["calibrate_gaze.py", "--quick"])
    assert cg.parse_args().points == 1
    monkeypatch.setattr(sys, "argv", ["calibrate_gaze.py", "--points", "9"])
    assert cg.parse_args().points == 9
