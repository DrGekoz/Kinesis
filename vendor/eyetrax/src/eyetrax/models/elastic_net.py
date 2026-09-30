from __future__ import annotations

from sklearn.linear_model import ElasticNet

from . import register_model
from .base import BaseModel


class ElasticNetModel(BaseModel):
    def __init__(self, *, alpha: float = 1.0, l1_ratio: float = 0.5) -> None:
        super().__init__()
        self._init_native(alpha=alpha, l1_ratio=l1_ratio)

    def _init_native(self, **kw):
        self.model = ElasticNet(**kw)

    def _native_train(self, X, y):
        self.model.fit(X, y)

    def _native_predict(self, X):
        return self.model.predict(X)


register_model("elastic_net", ElasticNetModel)
