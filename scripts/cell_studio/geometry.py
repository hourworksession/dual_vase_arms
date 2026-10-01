"""Shared world frame for the two arms (config/calibration.yaml).

World: origin at the centre of the disc top surface, +Z up. Each arm reports
poses in its own base frame. For arm a with turntable centre C_a = (cx, cy, cz)
in its frame (the panel's "Centre X/Y/Z · left/right", cz = disc surface) and
base yaw ψ_a (left 0°, right 180° in calibration.yaml):

    world_xy = Rz(ψ_a) · (arm_xy − C_a.xy)        world_z = arm_z − C_a.z
    arm_xy   = C_a.xy + Rz(−ψ_a) · world_xy        arm_z   = C_a.z + world_z
"""

import math
import os

from .home_config import REPO_ROOT

DEFAULT_YAW = {"left": 0.0, "right": 180.0}
ARM_SIDE_SIGN = {"left": -1.0, "right": 1.0}     # base_world_xyz x sign in system.yaml


def load_tt_direction():
    try:
        import yaml
        with open(os.path.join(REPO_ROOT, "config", "calibration.yaml")) as f:
            return 1 if float(yaml.safe_load(f)["turntable"].get("direction", 1)) >= 0 else -1
    except Exception:
        return 1


def load_yaws():
    yaws = dict(DEFAULT_YAW)
    try:
        import yaml
        with open(os.path.join(REPO_ROOT, "config", "calibration.yaml")) as f:
            cal = yaml.safe_load(f)
        for side in ("left", "right"):
            yaws[side] = float(cal["arms"][side]["base_yaw_deg"])
    except Exception:
        pass
    return yaws


class CellGeometry:
    def __init__(self, ctl, yaws=None):
        self.c = ctl
        self.yaw = yaws or load_yaws()
        self.tt_direction = load_tt_direction()

    def centre(self, side):
        p = self.c.param_vars
        return (p[f"tt_cx_{side}"].get(), p[f"tt_cy_{side}"].get(), p[f"tt_cz_{side}"].get())

    def to_arm(self, side, w):
        cx, cy, cz = self.centre(side)
        a = math.radians(-self.yaw[side])
        c, s = math.cos(a), math.sin(a)
        return (cx + c * w[0] - s * w[1], cy + s * w[0] + c * w[1], cz + w[2])

    def to_world(self, side, p, centre=None):
        cx, cy, cz = centre or self.centre(side)
        a = math.radians(self.yaw[side])
        c, s = math.cos(a), math.sin(a)
        dx, dy = p[0] - cx, p[1] - cy
        return (c * dx - s * dy, s * dx + c * dy, p[2] - cz)

    def approach(self, side):
        """Unit vector, in this arm's frame, pointing from its side of the disc towards the axis."""
        wdir = (-ARM_SIDE_SIGN[side], 0.0)
        a = math.radians(-self.yaw[side])
        c, s = math.cos(a), math.sin(a)
        return (c * wdir[0] - s * wdir[1], s * wdir[0] + c * wdir[1], 0.0)
