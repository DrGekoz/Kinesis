from __future__ import annotations

import time
from collections import deque
from typing import Deque, Tuple

import cv2
import numpy as np
from scipy.stats import gaussian_kde

from .base import BaseSmoother


class KDESmoother(BaseSmoother):

    def __init__(
        self,
        screen_w: int,
        screen_h: int,
        *,
        time_window: float = 0.5,
        confidence: float = 0.5,
        grid: Tuple[int, int] = (320, 200),
    ) -> None:
        super().__init__()
        self.sw, self.sh = screen_w, screen_h
        self.window = time_window
        self.conf = confidence
        self.grid = grid
        self.hist: Deque[Tuple[float, int, int]] = deque()

    def step(self, x: int, y: int) -> Tuple[int, int]:
        now = time.time()

        self.hist.append((now, x, y))
        while self.hist and now - self.hist[0][0] > self.window:
            self.hist.popleft()

        pts = np.asarray([(hx, hy) for (_, hx, hy) in self.hist])
        if pts.shape[0] < 2:
            self.debug.clear()
            return x, y

        try:
            kde = gaussian_kde(pts.T)
            xi, yi = np.mgrid[
                0 : self.sw : complex(self.grid[0]),
                0 : self.sh : complex(self.grid[1]),
            ]
            zi = kde(np.vstack([xi.ravel(), yi.ravel()])).reshape(xi.shape).T

            flat = zi.ravel()
            idx = np.argsort(flat)[::-1]
            cdf = np.cumsum(flat[idx]) / flat.sum()
            thr = flat[idx[np.searchsorted(cdf, self.conf)]]

            mask = (zi >= thr).astype(np.uint8)
            mask = cv2.resize(mask, (self.sw, self.sh))

            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )

            self.debug["mask"] = mask
            self.debug["contours"] = contours

            sx, sy = pts.mean(axis=0).astype(int)
            return int(sx), int(sy)

        except np.linalg.LinAlgError:
            self.debug.clear()
            return x, y
