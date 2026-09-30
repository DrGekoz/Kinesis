from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Tuple


class BaseSmoother(ABC):

    def __init__(self) -> None:
        self.debug: dict = {}

    @abstractmethod
    def step(self, x: int, y: int) -> Tuple[int, int]: ...
