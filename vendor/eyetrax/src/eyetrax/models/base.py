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

    def __init__(self) -> None:
        self.scaler = StandardScaler()

    @abstractmethod
    def _init_native(self, **kwargs): ...
    @abstractmethod
    def _native_train(self, X: np.ndarray, y: np.ndarray): ...
    @abstractmethod
    def _native_predict(self, X: np.ndarray) -> np.ndarray: ...

    def train(
        self,
        X: np.ndarray,
        y: np.ndarray,
        variable_scaling: np.ndarray | None = None,
    ) -> None:
        self.variable_scaling = variable_scaling
        Xs = self.scaler.fit_transform(X)
        if variable_scaling is not None:
            Xs *= variable_scaling
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
