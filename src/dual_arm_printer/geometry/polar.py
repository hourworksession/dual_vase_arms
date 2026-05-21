"""Cartesian ↔ polar conversions in the disc-local frame.

The disc rotates around the world Z axis. A point `(x, y, z)` deposited on
the disc has a disc-local `(r, theta, z)` representation; when the disc is
rotated by angle ``alpha``, the world position is
``( r*cos(theta+alpha), r*sin(theta+alpha), z )``.

These helpers are kept dependency-free so they can be used inside hot loops.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np


@dataclass(slots=True)
class PolarPoint:
    r: float
    theta_rad: float
    z: float

    def to_world(self, disc_angle_rad: float = 0.0) -> tuple[float, float, float]:
        a = self.theta_rad + disc_angle_rad
        return (self.r * math.cos(a), self.r * math.sin(a), self.z)


def cart_to_polar(x: float, y: float, z: float) -> PolarPoint:
    r = math.hypot(x, y)
    theta = math.atan2(y, x)
    return PolarPoint(r=r, theta_rad=theta, z=z)


def polar_to_cart(p: PolarPoint, disc_angle_rad: float = 0.0) -> tuple[float, float, float]:
    return p.to_world(disc_angle_rad)


def batch_cart_to_polar(xyz: np.ndarray) -> np.ndarray:
    """xyz shape (N, 3) → polar shape (N, 3) as columns (r, theta, z)."""
    r = np.hypot(xyz[:, 0], xyz[:, 1])
    theta = np.arctan2(xyz[:, 1], xyz[:, 0])
    return np.stack([r, theta, xyz[:, 2]], axis=1)


def batch_polar_to_cart(rtz: np.ndarray, disc_angle_rad: float = 0.0) -> np.ndarray:
    a = rtz[:, 1] + disc_angle_rad
    return np.stack(
        [rtz[:, 0] * np.cos(a), rtz[:, 0] * np.sin(a), rtz[:, 2]], axis=1
    )


def angular_diff(a: float, b: float) -> float:
    """Smallest signed difference (a - b), wrapped to [-pi, pi]."""
    d = (a - b + math.pi) % (2 * math.pi) - math.pi
    return d


def unwrap_thetas(thetas: Iterable[float]) -> list[float]:
    """Remove 2π discontinuities so a spiral is monotonic in theta."""
    out: list[float] = []
    last = None
    offset = 0.0
    for t in thetas:
        if last is None:
            out.append(t)
            last = t
            continue
        d = t + offset - last
        while d < -math.pi:
            offset += 2 * math.pi
            d += 2 * math.pi
        while d > math.pi:
            offset -= 2 * math.pi
            d -= 2 * math.pi
        out.append(t + offset)
        last = t + offset
    return out
