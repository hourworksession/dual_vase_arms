"""PyBullet scene for the dual-arm + turntable cell.

Loads the xArm 850 URDFs (placed at the configured base poses), the
ADRS turntable + disc URDF, and provides hooks to drive each from an
ExecutionPlan. The scene also draws the deposited material as line
segments so the user can see the print evolve.

PyBullet was chosen because it is pip-installable, scriptable, and good
enough for collision-distance queries at the rate this cell needs.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class SceneHandles:
    physics_client: int
    left_arm_id: int
    right_arm_id: int
    disc_id: int


def build_scene(
    urdf_dir: str | Path,
    left_base_world_xyz_m: tuple[float, float, float],
    right_base_world_xyz_m: tuple[float, float, float],
    gui: bool = True,
) -> SceneHandles:
    """Construct the cell. Heights are in metres because PyBullet uses SI."""
    import pybullet as p
    import pybullet_data

    cid = p.connect(p.GUI if gui else p.DIRECT)
    p.setAdditionalSearchPath(pybullet_data.getDataPath())
    p.setGravity(0, 0, -9.81)
    p.loadURDF("plane.urdf")

    urdf_dir = Path(urdf_dir)
    left = p.loadURDF(
        str(urdf_dir / "xarm850.urdf"),
        basePosition=list(left_base_world_xyz_m),
        useFixedBase=True,
    )
    right = p.loadURDF(
        str(urdf_dir / "xarm850.urdf"),
        basePosition=list(right_base_world_xyz_m),
        useFixedBase=True,
    )
    disc = p.loadURDF(
        str(urdf_dir / "adrs_turntable.urdf"),
        basePosition=[0, 0, 0],
        useFixedBase=True,
    )
    log.info("PyBullet scene built (client=%d)", cid)
    return SceneHandles(cid, left, right, disc)
