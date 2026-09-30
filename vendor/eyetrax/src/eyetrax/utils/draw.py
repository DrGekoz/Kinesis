from __future__ import annotations

import cv2
import numpy as np


def draw_cursor(
    canvas,
    x: int,
    y: int,
    alpha: float,
    *,
    radius_outer: int = 30,
    radius_inner: int = 25,
    color_outer: tuple[int, int, int] = (0, 0, 255),
    color_inner: tuple[int, int, int] = (255, 255, 255),
):
    if alpha <= 0.0:
        return canvas

    overlay = canvas.copy()
    cv2.circle(overlay, (int(x), int(y)), radius_outer, color_outer, -1)
    if radius_inner > 0:
        cv2.circle(overlay, (int(x), int(y)), radius_inner, color_inner, -1)

    cv2.addWeighted(overlay, alpha * 0.6, canvas, 1 - alpha * 0.6, 0, canvas)
    return canvas


def make_thumbnail(
    frame,
    *,
    size: tuple[int, int] = (320, 240),
    border: int = 2,
    border_color: tuple[int, int, int] = (255, 255, 255),
):
    img = cv2.resize(frame, size)
    return cv2.copyMakeBorder(
        img,
        border,
        border,
        border,
        border,
        cv2.BORDER_CONSTANT,
        value=border_color,
    )
