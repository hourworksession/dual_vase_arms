"""Typed configuration loader.

Reads the YAML files under ``config/`` into validated dataclasses so that
typos or missing fields fail fast at startup instead of mid-print.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class DiscConfig:
    radius_mm: float
    centre_world_xyz: tuple[float, float, float]
    surface_thickness_mm: float


@dataclass
class ArmCellConfig:
    base_world_xyz: tuple[float, float, float]
    base_yaw_deg: float
    reach_mm: float
    home_joint_deg: list[float]


@dataclass
class WorkspaceConfig:
    inner_radius_mm: float
    outer_radius_mm: float
    max_height_mm: float


@dataclass
class SafetyConfig:
    arm_arm_min_distance_mm: float
    arm_disc_min_clearance_mm: float
    nozzle_to_disc_z_min_mm: float


@dataclass
class SystemConfig:
    disc: DiscConfig
    arms: dict[str, ArmCellConfig]
    workspace: WorkspaceConfig
    safety: SafetyConfig

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SystemConfig":
        raw = yaml.safe_load(Path(path).read_text())["cell"]
        return cls(
            disc=DiscConfig(**raw["disc"]),
            arms={k: ArmCellConfig(**v) for k, v in raw["arms"].items()},
            workspace=WorkspaceConfig(**raw["workspace"]),
            safety=SafetyConfig(**raw["safety"]),
        )


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Generic YAML loader for the strategy / driver configs."""
    return yaml.safe_load(Path(path).read_text())
