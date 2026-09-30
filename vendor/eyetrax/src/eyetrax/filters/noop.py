from __future__ import annotations

from typing import Tuple

from .base import BaseSmoother


class NoSmoother(BaseSmoother):

    def step(self, x: int, y: int) -> Tuple[int, int]:
        return x, y
