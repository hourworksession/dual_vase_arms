"""xArm 850 kinematic model: load DH params, evaluate forward kinematics.

We use the manufacturer DH parameters from ``config/arms/xarm850.yaml``.
Forward kinematics is computed directly so the simulator can work without
a network connection to a real arm.

Inverse kinematics is delegated to the xArm SDK or PyBullet (see ``ik.py``)
because hand-coding a numerically stable closed-form IK for the 850 is
error-prone and unnecessary while we have those libraries.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import yaml


@dataclass(slots=True)
class DHRow:
    alpha: float
    a: float
    d: float
    theta_offset: float


def _dh_transform(alpha_deg: float, a: float, d: float, theta_deg: float) -> np.ndarray:
    """Modified DH transformation matrix."""
    alpha = np.deg2rad(alpha_deg)
    theta = np.deg2rad(theta_deg)
    ca, sa = np.cos(alpha), np.sin(alpha)
    ct, st = np.cos(theta), np.sin(theta)
    return np.array(
        [
            [ct, -st, 0.0, a],
            [st * ca, ct * ca, -sa, -sa * d],
            [st * sa, ct * sa, ca, ca * d],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )


class XArm850Model:
    def __init__(self, yaml_path: str | Path):
        raw = yaml.safe_load(Path(yaml_path).read_text())
        self.dh = [DHRow(**row) for row in raw["dh"]]
        self.tool_offset = np.asarray(raw.get("tool_offset_mm", [0, 0, 0]), dtype=float)

    def fk(self, joints_deg: Sequence[float]) -> np.ndarray:
        """Return the 4x4 base→flange transform for the given joint angles."""
        T = np.eye(4)
        for joint, row in zip(joints_deg, self.dh):
            T = T @ _dh_transform(row.alpha, row.a, row.d, joint + row.theta_offset)
        # Apply tool offset along the flange Z.
        tool = np.eye(4)
        tool[:3, 3] = self.tool_offset
        return T @ tool

    def nozzle_world(
        self,
        joints_deg: Sequence[float],
        base_world_xyz: tuple[float, float, float],
        base_yaw_deg: float,
    ) -> tuple[float, float, float]:
        """Convenience: nozzle position in world coordinates."""
        T = self.fk(joints_deg)
        yaw = np.deg2rad(base_yaw_deg)
        Rz = np.array(
            [[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]]
        )
        p = Rz @ T[:3, 3] + np.asarray(base_world_xyz)
        return float(p[0]), float(p[1]), float(p[2])
