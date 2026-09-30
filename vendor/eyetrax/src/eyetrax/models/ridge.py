from __future__ import annotations

from sklearn.linear_model import Ridge

from . import register_model
from .base import BaseModel


class RidgeModel(BaseModel):
    def __init__(self, alpha: float = 1.0) -> None:
        super().__init__()
        self._init_native(alpha=alpha)

    def _init_native(self, **kw):
        self.model = Ridge(**kw)

    def _native_train(self, X, y):
        self.model.fit(X, y)

    def _native_predict(self, X):
        return self.model.predict(X)


register_model("ridge", RidgeModel)
