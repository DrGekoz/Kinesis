from __future__ import annotations

from sklearn.neural_network import MLPRegressor

from . import register_model
from .base import BaseModel


class TinyMLPModel(BaseModel):
    def __init__(
        self,
        *,
        hidden_layer_sizes: tuple[int, ...] = (64, 32),
        activation: str = "relu",
        alpha: float = 1e-4,
        learning_rate_init: float = 1e-3,
        max_iter: int = 500,
        early_stopping: bool = True,
    ) -> None:
        super().__init__()
        self._init_native(
            hidden_layer_sizes=hidden_layer_sizes,
            activation=activation,
            alpha=alpha,
            learning_rate_init=learning_rate_init,
            max_iter=max_iter,
            early_stopping=early_stopping,
        )

    def _init_native(self, **kw):
        self.model = MLPRegressor(
            solver="adam",
            batch_size="auto",
            random_state=0,
            verbose=False,
            **kw,
        )

    def _native_train(self, X, y):
        self.model.fit(X, y)

    def _native_predict(self, X):
        return self.model.predict(X)


register_model("tiny_mlp", TinyMLPModel)
