"""Replay a saved plan in PyBullet at user-controlled speed.

Useful for debugging the splitter's output without committing to a real
print. Reads the plan JSON written by ``execution.output_writer``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..utils.logging import get_logger
from .pybullet_world import SceneHandles, build_scene

log = get_logger(__name__)


def replay_plan(
    plan_json: str | Path,
    urdf_dir: str | Path,
    left_base_xyz_m: tuple[float, float, float] = (-0.7, 0.0, 0.0),
    right_base_xyz_m: tuple[float, float, float] = (0.7, 0.0, 0.0),
    speed: float = 1.0,
    gui: bool = True,
) -> None:
    import pybullet as p

    data = json.loads(Path(plan_json).read_text())
    scene = build_scene(urdf_dir, left_base_xyz_m, right_base_xyz_m, gui=gui)

    left_wps = data["left_waypoints"]
    right_wps = data["right_waypoints"]
    disc = data["turntable_schedule"]

    t_start = time.monotonic()
    li = ri = di = 0
    while li < len(left_wps) or ri < len(right_wps) or di < len(disc):
        sim_t = (time.monotonic() - t_start) * speed
        if li < len(left_wps) and left_wps[li]["t_s"] <= sim_t:
            _set_tcp(scene.physics_client, scene.left_arm_id, left_wps[li]["xyz_mm"])
            li += 1
        if ri < len(right_wps) and right_wps[ri]["t_s"] <= sim_t:
            _set_tcp(scene.physics_client, scene.right_arm_id, right_wps[ri]["xyz_mm"])
            ri += 1
        if di < len(disc) and disc[di][0] <= sim_t:
            p.resetJointState(scene.disc_id, 0, disc[di][1], physicsClientId=scene.physics_client)
            di += 1
        p.stepSimulation(physicsClientId=scene.physics_client)
        time.sleep(1.0 / 240.0)


def _set_tcp(client: int, body_id: int, xyz_mm: tuple[float, float, float]) -> None:
    import pybullet as p

    target = [xyz_mm[0] / 1000.0, xyz_mm[1] / 1000.0, xyz_mm[2] / 1000.0]
    n_joints = p.getNumJoints(body_id, physicsClientId=client)
    ee_link = n_joints - 1
    joints = p.calculateInverseKinematics(body_id, ee_link, target, physicsClientId=client)
    for i, q in enumerate(joints):
        p.resetJointState(body_id, i, q, physicsClientId=client)
