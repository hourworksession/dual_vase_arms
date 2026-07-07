"""Inverse kinematics.

Production: call ``XArmAPI.set_position`` and let the on-board controller
solve IK. The xArm controller is robust and aware of singularities and
joint limits in a way our Python code is not.

Simulation: use PyBullet's ``calculateInverseKinematics``, which is fine
for visualisation and collision checks.

This module is intentionally a thin shim so we don't carry our own IK.
"""
from __future__ import annotations

from typing import Sequence


def ik_pybullet(
    physics_client_id: int,
    body_id: int,
    end_effector_link_index: int,
    target_pos_world_m: Sequence[float],
    target_orn_quat: Sequence[float] | None = None,
) -> list[float]:
    """Compute joint angles (radians) for a target end-effector pose.

    Wrapper around ``pybullet.calculateInverseKinematics`` so the rest of
    the codebase doesn't import PyBullet directly.
    """
    import pybullet as p

    if target_orn_quat is None:
        return list(
            p.calculateInverseKinematics(
                body_id,
                end_effector_link_index,
                list(target_pos_world_m),
                physicsClientId=physics_client_id,
            )
        )
    return list(
        p.calculateInverseKinematics(
            body_id,
            end_effector_link_index,
            list(target_pos_world_m),
            list(target_orn_quat),
            physicsClientId=physics_client_id,
        )
    )
