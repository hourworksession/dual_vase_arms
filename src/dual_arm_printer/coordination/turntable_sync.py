"""Disc-frame ↔ world-frame transforms.

Whenever the splitter emits ``(segment_in_disc_frame, disc_angle)``, the
synchronizer needs to rotate that segment's endpoints into the world
frame so the arms can be commanded to those Cartesian targets.
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np


def rotate_xy(x: float, y: float, angle_rad: float) -> tuple[float, float]:
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return c * x - s * y, s * x + c * y


def transform_segment(
    start_xyz: tuple[float, float, float],
    end_xyz: tuple[float, float, float],
    disc_angle_rad: float,
    disc_centre_world: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Rotate both endpoints around the disc centre by ``disc_angle_rad``."""
    cx, cy, cz = disc_centre_world

    sx, sy = rotate_xy(start_xyz[0] - cx, start_xyz[1] - cy, disc_angle_rad)
    ex, ey = rotate_xy(end_xyz[0] - cx, end_xyz[1] - cy, disc_angle_rad)

    return ((sx + cx, sy + cy, start_xyz[2]), (ex + cx, ey + cy, end_xyz[2]))


def densify_schedule(
    schedule: Iterable[tuple[float, float]], hz: float = 200.0
) -> np.ndarray:
    """Interpolate (time, angle) waypoints to a uniform rate, ready to
    stream to the Aerotech controller."""
    pts = np.asarray(list(schedule))
    if len(pts) < 2:
        return pts
    t0, t1 = pts[0, 0], pts[-1, 0]
    n = max(int((t1 - t0) * hz), 2)
    t_uniform = np.linspace(t0, t1, n)
    angle_uniform = np.interp(t_uniform, pts[:, 0], pts[:, 1])
    return np.stack([t_uniform, angle_uniform], axis=1)
