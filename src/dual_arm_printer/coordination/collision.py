"""Lightweight, fast collision checks.

Two complementary checks live here:

1. Coarse: closest-distance between the two arm-TCP positions in the
   world frame. Used inside the splitter for sanity, and inside the
   simulator at every visualization step.

2. Fine: PyBullet-based mesh-mesh queries between the actual arm links
   plus the disc surface and the previously-deposited material. The fine
   check is invoked from ``simulation/pybullet_world.py``.
"""
from __future__ import annotations

import math


def tcp_tcp_distance(
    a_xyz: tuple[float, float, float], b_xyz: tuple[float, float, float]
) -> float:
    return math.sqrt(
        (a_xyz[0] - b_xyz[0]) ** 2
        + (a_xyz[1] - b_xyz[1]) ** 2
        + (a_xyz[2] - b_xyz[2]) ** 2
    )


def nozzle_above_disc(
    nozzle_z_mm: float, disc_top_z_mm: float, min_clearance_mm: float
) -> bool:
    return (nozzle_z_mm - disc_top_z_mm) >= min_clearance_mm
