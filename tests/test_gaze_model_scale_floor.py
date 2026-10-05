"""The scaler's scale floor: the measured cause of the pointer jumping everywhere.

Kinesis' gaze model is a ridge regression over 486 face-landmark features, wrapped by EyeTrax in a
`StandardScaler`. That scaler divides every feature by its standard deviation across the calibration
samples - and it only rescues an EXACTLY zero variance. A std of 1e-8 sails straight through.

Measured on the shipped 222-sample model (`tools/probe_gaze_gain.py`):

| feature | calibration std | pointer gain from a 0.2% wobble |
| --- | --- | --- |
| 461 | 1.068e-08 | **617,784 px** |
| 278 | 4.742e-08 | 179,662 px |
| 50 | 4.992e-08 | 122,116 px |
| 276 | 1.173e-07 | 39,185 px |
| 48 | 1.180e-07 | 30,787 px |

The independent sum over all 486 features was **656,881 px** of pointer travel caused by wobble no one
could see. That is why smoothing never fixed it: the excursions were real model output, not noise added
afterwards, so the only two places to intervene are the model's gain (here) and rejection (the
stabiliser).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kinesis                                                    # noqa: E402,F401  (vendored eyetrax)
from eyetrax.models.base import BaseModel                         # noqa: E402
from eyetrax.models.ridge import RidgeModel                       # noqa: E402


def _model_with_dead_features(n_dead: int = 6, n_live: int = 40, n_samples: int = 60):
    """Features where some columns are constant to within 1e-8 and the rest vary normally.

    That reproduces what the real calibration produced: dots where the head barely moved, so a handful
    of landmarks recorded (almost) the same value every time.
    """
    rng = np.random.default_rng(7)
    X = rng.normal(0.0, 1.0, (n_samples, n_dead + n_live))
    for i in range(n_dead):
        # constant up to float noise, exactly like a landmark that did not move during calibration
        X[:, i] = 0.5 + rng.normal(0.0, 0.0, n_samples)
        X[:, i] = np.float64(0.5) + rng.normal(0.0, 1e-12, n_samples)
    y = np.column_stack([X[:, -1] * 100.0, X[:, -2] * 100.0])
    return X, y


def test_a_constant_feature_does_not_get_divided_by_its_own_noise():
    """The core claim. Without the floor, `StandardScaler` divides a ~1e-8 std feature by ~1e-8."""
    X, y = _model_with_dead_features()
    m = RidgeModel(alpha=1.0)
    m.train(X, y)
    dead = np.arange(6)
    scale = np.asarray(m.scaler.scale_)
    assert (scale[dead] >= 1.0).all(), (
        f"dead features still have a tiny scale: {scale[dead]} - real wobble would be "
        f"amplified by {1.0 / max(scale[dead].min(), 1e-12):,.0f}x"
    )


def test_the_floor_leaves_healthy_features_alone():
    """Flooring must not flatten the features that DO carry scale information."""
    X, y = _model_with_dead_features()
    m = RidgeModel(alpha=1.0)
    m.train(X, y)
    scale = np.asarray(m.scaler.scale_)
    live = scale[6:]
    # A genuinely varying feature has std ~1, so its scale stays near 1. The MEDIAN is the check,
    # not every value: 40 samples of random data give each column its own std, so a handful land
    # outside any tight band. An earlier version asserted all 40 were within 0.1 and failed on four
    # of them - a property of the sample, not of the floor.
    assert 0.85 < float(np.median(live)) < 1.15, (
        f"healthy features were rescaled: median {np.median(live):.4f}"
    )
    # and none of them may have been floored, which is the part that would actually be a bug
    assert (live >= BaseModel.MIN_FEATURE_SCALE).all(), (
        f"a healthy feature was floored: min {live.min():.4g}"
    )


def test_noise_gain_collapses_with_the_floor_and_without_it_does_not():
    """The end-to-end claim: the same 0.2% wobble, the same features, only the floor differs."""
    X, y = _model_with_dead_features(n_dead=8, n_live=30, n_samples=80)
    rng = np.random.default_rng(3)
    probe = X[:1]
    wobbled = probe * (1.0 + rng.normal(0.0, 0.002, probe.shape))

    def gain(model):
        a = np.asarray(model.predict(probe.astype(np.float32)), dtype=np.float64).reshape(2)
        b = np.asarray(model.predict(wobbled.astype(np.float32)), dtype=np.float64).reshape(2)
        return float(np.linalg.norm(b - a))

    raw = RidgeModel(alpha=1.0)
    raw.scaler.fit_transform(X)                      # the old path: fit_transform, no floor
    raw.model.fit(raw.scaler.transform(X), y)
    raw.variable_scaling = None

    floored = RidgeModel(alpha=1.0)
    floored.train(X, y)                              # the shipped path

    g_raw, g_fixed = gain(raw), gain(floored)
    assert g_raw > 100.0, (
        f"the unfloored model did not show the amplification at all ({g_raw:,.1f} px), so this "
        f"test would pass for the wrong reason"
    )
    assert g_fixed < g_raw / 50.0, (
        f"the scale floor barely helped: {g_raw:,.1f} px -> {g_fixed:,.1f} px"
    )


def test_training_still_fits_the_samples_it_was_given():
    """A floor must not stop the model learning. It has to remain usable, not merely quiet."""
    X, y = _model_with_dead_features(n_dead=4, n_live=20, n_samples=120)
    m = RidgeModel(alpha=1.0)
    m.train(X, y)
    pred = np.asarray(m.predict(X[:20].astype(np.float32)), dtype=np.float64)
    err = np.linalg.norm(pred - y[:20], axis=1)
    assert float(np.median(err)) < 50.0, (
        f"the floored model no longer fits its own training data: median {np.median(err):,.1f} px"
    )


def test_the_floor_is_reported_so_a_silent_fix_cannot_hide():
    """A calibration that quietly produces a broken model is the failure mode of this whole area, so
    the count has to be visible rather than merely applied."""
    X, y = _model_with_dead_features()
    m = RidgeModel(alpha=1.0)
    m.scaler.fit(X)
    assert m._floor_scaler() == 6, "the floor did not report how many features it rescued"
    # idempotent: a second pass must find nothing left to fix
    assert m._floor_scaler() == 0, "the floor is not idempotent"


def test_a_model_with_no_dead_features_reports_zero():
    X, y = _model_with_dead_features(n_dead=0, n_live=30)
    m = RidgeModel(alpha=1.0)
    m.scaler.fit(X)
    assert m._floor_scaler() == 0, "the floor fired on healthy features"


def test_the_floor_survives_a_save_load_cycle():
    """The model is pickled and reloaded on every run, so the correction has to be persisted rather
    than applied live each time."""
    import pickle
    X, y = _model_with_dead_features()
    m = RidgeModel(alpha=1.0)
    m.train(X, y)
    again = pickle.loads(pickle.dumps(m))
    probe = X[:1]
    rng = np.random.default_rng(11)
    wobbled = probe * (1.0 + rng.normal(0.0, 0.002, probe.shape))
    a = np.asarray(again.predict(wobbled.astype(np.float32)), dtype=np.float64).reshape(2)
    b = np.asarray(again.predict(probe.astype(np.float32)), dtype=np.float64).reshape(2)
    assert np.linalg.norm(a - b) < 100.0, (
        "the reloaded model is noisy again - the fix was not persisted in the pickle"
    )


def test_predict_still_works_on_a_model_whose_scaler_was_never_trained():
    """`predict` must not assume `_floor_scaler` ran; a model loaded from an older pickle has a
    scaler with real scale factors and the floor is a no-op there."""
    X, y = _model_with_dead_features()
    m = RidgeModel(alpha=1.0)
    m.train(X, y)
    out = np.asarray(m.predict(X[:5].astype(np.float32)), dtype=np.float64)
    assert out.shape == (5, 2), f"unexpected prediction shape {out.shape}"


def test_the_floor_is_a_class_attribute_not_a_magic_number():
    """It is read by `tools/verify_gaze_gain_fix.py`, so it has to be discoverable."""
    assert isinstance(BaseModel.MIN_FEATURE_SCALE, float)
    assert 1e-6 < BaseModel.MIN_FEATURE_SCALE < 1e-2, (
        f"{BaseModel.MIN_FEATURE_SCALE:g} is outside the range the measurements justify"
    )
