"""User defined home positions (Settings > Home positions in the top bar).

The xArm's built in ``move_gohome`` goes to the factory zero pose, which is not
where the cell wants the arms to rest now they carry the new mounts and
extruders. This module stores a home per arm, either as joint angles or as a
Cartesian TCP pose, in ``config/home_positions.json`` and drives the arms there.
"""

import copy
import json
import logging
import os

logger = logging.getLogger("cell_studio.home")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOME_FILE = os.path.join(REPO_ROOT, "config", "home_positions.json")

_ARM_DEFAULT = {
    "enabled": True,          # False = use the xArm factory home (move_gohome)
    "mode": "joint",          # "joint" or "pose"
    "joints": [0.0, -45.0, -45.0, 0.0, 90.0, 0.0],   # deg, from config/system.yaml
    "pose": [400.0, 174.0, 250.0, 180.0, 45.0, 20.0],  # x y z (mm), roll pitch yaw (deg)
    "joint_speed": 20.0,      # deg/s
    "pose_speed": 50.0,       # mm/s
    "lift_first": True,       # pose mode: rise to safe_z before travelling
    "safe_z": 250.0,
}

DEFAULTS = {
    "left": copy.deepcopy(_ARM_DEFAULT),
    "right": copy.deepcopy(_ARM_DEFAULT),
    "sequence": "simultaneous",   # "simultaneous", "right_first", "left_first"
}

SEQUENCES = [("simultaneous", "Both together"), ("right_first", "Right, then left"),
             ("left_first", "Left, then right")]


def load():
    data = copy.deepcopy(DEFAULTS)
    try:
        with open(HOME_FILE, "r") as f:
            saved = json.load(f)
        for side in ("left", "right"):
            data[side].update(saved.get(side, {}))
        data["sequence"] = saved.get("sequence", data["sequence"])
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning("Could not read %s (%s); using defaults", HOME_FILE, e)
    return data


def save(data):
    os.makedirs(os.path.dirname(HOME_FILE), exist_ok=True)
    with open(HOME_FILE, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("Home positions saved to %s", HOME_FILE)


def _check(code, what):
    if isinstance(code, (list, tuple)):
        code = code[0]
    if code not in (0, None):
        raise RuntimeError(f"{what} failed (xArm code {code})")


def go_home(arm, cfg, wait=True):
    """Move one ArmController to its configured home."""
    if not cfg.get("enabled", True):
        arm.home(wait=wait)
        return
    if cfg.get("mode") == "joint":
        angles = [float(a) for a in cfg["joints"]]
        _check(arm.arm.set_servo_angle(angle=angles, speed=float(cfg["joint_speed"]),
                                       is_radian=False, wait=wait),
               f"{arm.name} joint home")
        logger.info("%s -> joint home %s", arm.name, angles)
        return
    x, y, z, r, p, yw = [float(v) for v in cfg["pose"]]
    spd = float(cfg["pose_speed"])
    if cfg.get("lift_first", True):
        cur = arm.get_pose()
        if cur:
            safe_z = max(float(cfg.get("safe_z", 250.0)), float(cur[2]))
            _check(arm.arm.set_position(cur[0], cur[1], safe_z, cur[3], cur[4], cur[5],
                                        speed=spd, wait=True), f"{arm.name} lift")
    _check(arm.arm.set_position(x, y, z, r, p, yw, speed=spd, wait=wait), f"{arm.name} pose home")
    logger.info("%s -> pose home %s", arm.name, [x, y, z, r, p, yw])


def home_arms(arms_by_side, data):
    """arms_by_side: {"left": ArmController|None, "right": ...}. Blocks until done."""
    seq = data.get("sequence", "simultaneous")
    order = ["right", "left"] if seq == "right_first" else ["left", "right"]
    present = [(s, arms_by_side[s]) for s in order if arms_by_side.get(s) is not None]
    if not present:
        return 0
    if seq == "simultaneous":
        for i, (side, arm) in enumerate(present):
            go_home(arm, data[side], wait=(i == len(present) - 1))
    else:
        for side, arm in present:
            go_home(arm, data[side], wait=True)
    return len(present)
