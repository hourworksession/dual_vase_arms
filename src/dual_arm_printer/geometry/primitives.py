"""Lightweight geometric primitives used by the splitter and simulator."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Cylinder:
    centre_xyz: tuple[float, float, float]
    radius_mm: float
    height_mm: float


@dataclass(slots=True)
class AxisAlignedBox:
    min_xyz: tuple[float, float, float]
    max_xyz: tuple[float, float, float]


@dataclass(slots=True)
class Disc:
    centre_xyz: tuple[float, float, float]
    radius_mm: float
    thickness_mm: float
