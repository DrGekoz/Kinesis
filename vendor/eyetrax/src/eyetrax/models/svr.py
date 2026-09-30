from __future__ import annotations

import numpy as np
from sklearn.svm import LinearSVR

from . import register_model
from .base import BaseModel


class LinearSVRModel(BaseModel):
    def __init__(
        self,
        *,
        C: float = 5.0,
        epsilon: float = 5.0,
        loss: str = "epsilon_insensitive",
        fit_intercept: bool = True,
    ) -> None:
        super().__init__()
        self._init_native(
            C=C,
            epsilon=epsilon,
            loss=loss,
            fit_intercept=fit_intercept,
        )

    def _init_native(self, **kwargs):
        self._template = LinearSVR(**kwargs)

    def _native_train(self, X: np.ndarray, y: np.ndarray):
        y = y.reshape(-1, 2)

        self.model_x = LinearSVR(**self._template.get_params())
        self.model_y = LinearSVR(**self._template.get_params())

        self.model_x.fit(X, y[:, 0])
        self.model_y.fit(X, y[:, 1])

    def _native_predict(self, X: np.ndarray) -> np.ndarray:
        x_pred = self.model_x.predict(X)
        y_pred = self.model_y.predict(X)
        return np.column_stack((x_pred, y_pred))


register_model("linear_svr", LinearSVRModel)
register_model("svr", LinearSVRModel)
