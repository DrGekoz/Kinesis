from __future__ import annotations

import pickle
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
from sklearn.preprocessing import StandardScaler


class BaseModel(ABC):
    """
    Common interface every gaze-prediction model must implement
    """

    # A feature whose standard deviation across the calibration samples is below this is treated as
    # having NO scale, and is passed through as-is instead of being divided by its own noise.
    #
    # This is not a precaution, it is the measured cause of the pointer jumping. `StandardScaler`
    # divides each feature by its calibration std, and it only rescues an EXACTLY zero variance - a
    # std of 1e-8 sails straight through. Kinesis' calibration includes dots where the head barely
    # moved, so several features ended up with std around 1e-8, and dividing real-world landmark
    # wobble by 1e-8 amplifies it a hundred million-fold.
    #
    # Measured on the shipped 222-sample model: feature 461 had std 1.068e-08 and a pointer gain of
    # 617,784 px from a 0.2% wobble; five more sat above 8,000 px. The independent sum over all 486
    # features was 656,881 px of pointer travel caused by noise no one could see. That is why the
    # pointer appeared to jitter everywhere, and no amount of smoothing could remove it, because the
    # excursions were real output of the model rather than something added afterwards.
    #
    # Flooring the scale does not discard the feature - it keeps it, and simply stops dividing by a
    # quantity that is itself noise. The ridge penalty then has something stable to shrink.
    MIN_FEATURE_SCALE = 1e-4

    def __init__(self) -> None:
        self.scaler = StandardScaler()

    @abstractmethod
    def _init_native(self, **kwargs): ...
    @abstractmethod
    def _native_train(self, X: np.ndarray, y: np.ndarray): ...
    @abstractmethod
    def _native_predict(self, X: np.ndarray) -> np.ndarray: ...

    def _floor_scaler(self) -> int:
        """Replace degenerate scale factors with 1.0, in place, and report how many were changed.

        Called after `fit` and before any transform, so the fit itself does not bake the bad scaling
        into the stored `mean_`/`var_`. `scale_` is what `transform` divides by, and it is also what
        `inverse_transform` and any later `fit` of the same object would rely on, so it is corrected
        at the source rather than worked around at each call site.
        """
        scale = self.scaler.scale_
        if scale is None:
            return 0
        dead = scale < self.MIN_FEATURE_SCALE
        n = int(dead.sum())
        if n:
            # 1.0 means "leave this feature alone", which is the correct behaviour for a feature that
            # did not move during calibration: it carries no scale information either way.
            scale[dead] = 1.0
        return n

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        variable_scaling: np.ndarray | None = None,
    ) -> None:
        self.variable_scaling = variable_scaling
        self.scaler.fit(X)
        floored = self._floor_scaler()
        Xs = self.scaler.transform(X)
        if variable_scaling is not None:
            Xs *= variable_scaling
        if floored:
            print(f"[model] {floored} feature(s) had no usable scale during calibration "
                  f"(std < {self.MIN_FEATURE_SCALE:g}) and were left unscaled; without this the "
                  f"pointer gain from landmark noise is enormous")
        self._native_train(Xs, y)

    def predict(self, X: np.ndarray) -> np.ndarray:
        Xs = self.scaler.transform(X)
        if getattr(self, "variable_scaling", None) is not None:
            Xs *= self.variable_scaling
        return self._native_predict(Xs)

    def save(self, path: str | Path) -> None:
        with Path(path).open("wb") as fh:
            pickle.dump(self, fh)

    @classmethod
    def load(cls, path: str | Path) -> "BaseModel":
        with Path(path).open("rb") as fh:
            return pickle.load(fh)
