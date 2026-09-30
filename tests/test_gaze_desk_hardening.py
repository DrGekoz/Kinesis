"""The desk-informed parts of calibration: distance gating and model ranking.

Both are pure functions so they can be tested without a camera, a face, or mediapipe.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                            # noqa: E402

import calibrate_gaze as cg                                   # noqa: E402


def _samples_with_distances(distances):
    """Minimum viable sample tuples: (features, tx, ty, monitor, dot, distance, span)."""
    return [(np.zeros(3, dtype=np.float32), 100.0 * i, 50.0 * i, 0, i, d, 120.0)
            for i, d in enumerate(distances)]


# ------------------------------------------------------------------ distance gating
def test_gating_drops_the_samples_taken_mid_lean():
    distances = [700.0] * 24 + [1200.0] * 6
    kept, info = cg.gate_by_distance(_samples_with_distances(distances), 0.08)
    assert info["dropped"] == 6 and info["kept"] == 24
    assert all(s[5] == 700.0 for s in kept)
    assert info["median_mm"] == 700.0


def test_gating_keeps_everything_when_the_seat_held_steady():
    kept, info = cg.gate_by_distance(_samples_with_distances([650.0] * 25), 0.08)
    assert len(kept) == 25 and info["dropped"] == 0
    assert info["median_mm"] == 650.0


def test_gating_ignores_a_gate_that_would_eat_the_calibration():
    """If dropping outliers leaves too little to train on, keep them: a bad fit beats no fit."""
    kept, info = cg.gate_by_distance(_samples_with_distances([700.0] * 19 + [2000.0] * 11), 0.08)
    assert len(kept) == 30, "should have given up on gating rather than train on 19 samples"
    assert info["dropped"] == 0 and info["median_mm"] == 0.0


def test_gating_keeps_samples_whose_distance_is_unknown():
    """No measurement is not evidence of a lean."""
    samples = _samples_with_distances([700.0] * 24)
    samples += [(np.zeros(3, dtype=np.float32), 0.0, 0.0, 0, 99, 0.0, 0.0)]
    kept, info = cg.gate_by_distance(samples, 0.08)
    assert len(kept) == 25 and info["dropped"] == 0


def test_gating_needs_enough_measurements_to_have_an_opinion():
    """Five measurements are not a median - the gate must stand down, not guess."""
    distances = [700.0] * 5 + [0.0] * 20          # 0.0 = distance could not be measured
    kept, info = cg.gate_by_distance(_samples_with_distances(distances), 0.01)
    assert len(kept) == 25 and info["median_mm"] == 0.0


# ------------------------------------------------------------------ model ranking
class _RankStub:
    """A model whose predictions are a function of the dot id encoded in feature 0.

    `per_dot` maps dot id -> (x, y) prediction, so a test can build a model with a small error that
    targets badly, and another with a larger error that targets well.
    """

    def __init__(self, per_dot):
        self.per_dot = per_dot

    def train(self, X, y):
        return None

    def predict(self, X):
        return np.array([self.per_dot[int(round(float(row[0])))] for row in X], dtype=np.float64)


def _two_monitor_dots(per_monitor=3, reps=4):
    """Dots on two monitors, with the dot id encoded as feature 0.

    `reps` samples per dot: leave-one-dot-out needs more than a handful of training samples to score
    a fold at all, which is exactly why a real calibration samples ~1 s per dot.
    """
    samples, ids = [], []
    for mon in range(2):
        for k in range(per_monitor):
            dot = mon * per_monitor + k
            tx = (mon * 1920) + 300 + 500 * k
            ty = 540.0
            for _ in range(reps):
                samples.append((np.array([dot, 0, 0], dtype=np.float32), tx, ty, mon, dot,
                                700.0, 120.0))
                ids.append(dot)
    return samples, ids


def _mon_index(x, y):
    """Monitors are 1920 wide starting at 0, so the index is just the integer division."""
    if x is None or y is None:
        return None
    return int(x // 1920)


def test_model_ranking_prefers_targeting_over_precision(monkeypatch):
    """The live case: alpha=1 won on median error (712 px) but only targeted 60% of dots, while
    alpha=1000 was slightly less precise (777 px) and targeted 100%. Targeting has to win."""
    samples, ids = _two_monitor_dots()
    truth = {int(s[4]): (s[1], s[2]) for s in samples}

    precise_but_blind = {}
    for dot, (tx, ty) in truth.items():
        # dots 0-2 predicted 40 px off (excellent), 3-5 predicted a whole desk away (wrong monitor)
        precise_but_blind[dot] = (tx + 40, ty) if dot <= 3 else (tx - 2500, ty)
    # pulled to the centre of the screen that dot is on: never precise, but never on the wrong one
    targeting_but_loose = {dot: (((dot // 3) * 1920) + 960.0, ty) for dot, (tx, ty) in truth.items()}

    stubs = {"blind": _RankStub(precise_but_blind), "targeting": _RankStub(targeting_but_loose)}
    monkeypatch.setattr(cg, "MODEL_CANDIDATES", [("blind", {}), ("targeting", {})])
    import eyetrax.models as em
    monkeypatch.setattr(em, "create_model", lambda name, **kw: stubs[name])

    results = cg.select_model([(s[0], s[1], s[2], s[3], s[4]) for s in samples], ids, _mon_index)
    assert results, "the sweep produced nothing"
    assert results[0][0] == "targeting", f"ranked {results[0][0]} first: {results}"
    blind = next(r for r in results if r[0] == "blind")
    assert blind[2] < results[0][2], "the precision model really did have the lower median error"
    assert results[0][3] == 100.0 and blind[3] < 100.0


def test_model_ranking_reports_every_candidate(monkeypatch):
    samples, ids = _two_monitor_dots()
    monkeypatch.setattr(cg, "MODEL_CANDIDATES", [("a", {}), ("b", {}), ("c", {})])
    import eyetrax.models as em
    monkeypatch.setattr(em, "create_model",
                        lambda name, **kw: _RankStub({int(s[4]): (s[1], s[2]) for s in samples}))
    results = cg.select_model([(s[0], s[1], s[2], s[3], s[4]) for s in samples], ids, _mon_index)
    assert {r[0] for r in results} == {"a", "b", "c"}
    assert all(r[3] == 100.0 for r in results), "a perfect model must score 100%"


# ------------------------------------------------------------------ saving everything
class _SaveEngine:
    """Just enough of GazeEngine for the writer: a model path plus a metadata writer."""

    def __init__(self, model_path):
        self.model_path = Path(model_path)
        self.metadata_path = self.model_path.with_suffix(".json")
        self.metadata = None

    def save_metadata(self, data):
        self.metadata = data
        self.metadata_path.write_text("{}", encoding="utf-8")


def _fake_geo_and_monitors():
    from types import SimpleNamespace
    panels = [SimpleNamespace(x_mm=0.0, width_mm=600.0),
              SimpleNamespace(x_mm=600.0, width_mm=600.0)]
    geo = SimpleNamespace(layout=SimpleNamespace(panels=panels), eye_offset_mm=300.0,
                          distance_mm=700.0)
    monitors = [SimpleNamespace(left=0, width=1920), SimpleNamespace(left=1920, width=1920)]
    return geo, monitors


def test_saving_keeps_every_field_the_calibration_produced(tmp_path):
    """The saved .npz has to hold the samples AND the desk azimuth of each dot, not just a model."""
    samples = [(np.zeros(486, dtype=np.float32) + i, 100.0 * i, 50.0 + i, i % 2, i,
                700.0 + i, 120.0 + i) for i in range(4)]
    pred = np.array([[10.0, 20.0]] * 4, dtype=np.float32)
    engine = _SaveEngine(tmp_path / "gaze_model.pkl")
    geo, monitors = _fake_geo_and_monitors()

    path = cg.save_calibration_data(engine, samples, [s[4] for s in samples],
                                    [s[3] for s in samples], pred, geo, monitors)
    assert path is not None and Path(path).is_file()
    with np.load(path) as z:
        assert set(z.files) >= {"features", "targets_px", "azimuth_deg", "dot_ids", "monitors",
                                "distances_mm", "eye_span_px", "in_sample_pred_px"}
        assert z["features"].shape == (4, 486)
        assert z["targets_px"].shape == (4, 2)
        assert z["dot_ids"].tolist() == [0, 1, 2, 3]
        assert z["monitors"].tolist() == [0, 1, 0, 1]
        assert z["distances_mm"].tolist() == [700.0, 701.0, 702.0, 703.0]


def test_saved_azimuth_follows_the_desk_left_to_right(tmp_path):
    """Desk data in the file: a dot further right must have a larger azimuth, measured from the eyes
    rather than from the screen edge, so the left screen sits at a negative angle."""
    samples = [(np.zeros(3, dtype=np.float32), tx, 100.0, mi, i, 700.0, 120.0)
               for i, (mi, tx) in enumerate([(0, 100.0), (0, 1700.0), (1, 2000.0), (1, 3700.0)])]
    engine = _SaveEngine(tmp_path / "gaze_model.pkl")
    geo, monitors = _fake_geo_and_monitors()
    path = cg.save_calibration_data(engine, samples, [s[4] for s in samples],
                                    [s[3] for s in samples], np.zeros((4, 2), dtype=np.float32),
                                    geo, monitors)
    with np.load(path) as z:
        az = z["azimuth_deg"].tolist()
    assert az == sorted(az), f"azimuth must increase left to right, got {az}"
    assert az[0] < 0 < az[-1], "the left hand screen should sit left of straight ahead"
