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

def _arm_default(joints, xyz, rpy):
    d = copy.deepcopy(_ARM_DEFAULT)
    d["joints"] = list(joints)
    d["pose"] = list(xyz) + list(rpy)            # each arm's own mount orientation (see tool.py)
    return d


# Default home (Oct 2026 lab calibration): both arms at the same TCP pose,
# X 298.3, Y 0, Z 298.9, pitch 90, yaw 180, roll +90 left / -90 right — nozzle down the mount
# axis, 150 mm proud of the disc. Pose mode drives straight to this; the
# joint lists remain as a fallback for "joint" mode. A saved
# config/home_positions.json takes precedence over these defaults.
_HOME_POSE_XYZ = (298.3, 0.0, 298.9)
_HOME_POSE_RPY_LEFT = (90.0, 90.0, 180.0)
_HOME_POSE_RPY_RIGHT = (-90.0, 90.0, 180.0)   # mirrored extruder mount: opposite roll

def _pose_default(joints, rpy):
    d = _arm_default(joints, _HOME_POSE_XYZ, rpy)
    d["mode"] = "pose"
    return d

DEFAULTS = {
    "left": _pose_default((-2.8, -26.9, -7.0, -3.0, 109.9, 179.0), _HOME_POSE_RPY_LEFT),
    "right": _pose_default((-7.7, -27.8, -6.9, -8.3, 110.8, -3.0), _HOME_POSE_RPY_RIGHT),
    "sequence": "simultaneous",   # "simultaneous", "right_first", "left_first"
}

LOAD_ERROR = None        # set when home_positions.json exists but could not be read

# xArm 850 joint limits (config/arms/xarm850.yaml), used to check a home before it is saved
JOINT_LIMITS = [(-360, 360), (-118, 120), (-225, 11), (-360, 360), (-97, 180), (-360, 360)]
try:
    import yaml as _yaml
    with open(os.path.join(REPO_ROOT, "config", "arms", "xarm850.yaml")) as _f:
        _lim = _yaml.safe_load(_f).get("joint_limits_deg", {})
    JOINT_LIMITS = [tuple(_lim[f"q{i + 1}"]) for i in range(6)]
except Exception:
    pass

SEQUENCES = [("simultaneous", "Both together"), ("right_first", "Right, then left"),
             ("left_first", "Left, then right")]


def load():
    """Saved homes, or the defaults. A file that exists but cannot be read sets LOAD_ERROR
    and disables custom home for both arms, so Home never drives to a pose nobody chose."""
    global LOAD_ERROR
    LOAD_ERROR = None
    data = copy.deepcopy(DEFAULTS)
    try:
        with open(HOME_FILE, "r") as f:
            saved = json.load(f)
        for side in ("left", "right"):
            data[side].update(saved.get(side, {}))
        data["sequence"] = saved.get("sequence", data["sequence"])
        problems = validate(data)
        if problems:
            raise ValueError("; ".join(problems))
    except FileNotFoundError:
        pass
    except Exception as e:
        LOAD_ERROR = f"{HOME_FILE} could not be used ({e})."
        logger.warning(LOAD_ERROR)
        data = copy.deepcopy(DEFAULTS)
        data["left"]["enabled"] = data["right"]["enabled"] = False
    return data


def validate(data, sides=("left", "right")):
    """Return a list of problems (empty when the homes are safe to use)."""
    out = []
    for side in sides:
        a = data.get(side, {})
        if not a.get("enabled", True):
            continue
        if a.get("mode") == "joint":
            j = a.get("joints", [])
            if len(j) != 6:
                out.append(f"{side} arm: need 6 joint angles")
                continue
            for i, (v, (lo, hi)) in enumerate(zip(j, JOINT_LIMITS)):
                if not lo <= float(v) <= hi:
                    out.append(f"{side} arm: J{i + 1} = {v:g}° is outside the xArm 850 range {lo}° to {hi}°")
            if not 0 < float(a.get("joint_speed", 0)) <= 180:
                out.append(f"{side} arm: joint speed must be between 0 and 180 °/s")
        else:
            if len(a.get("pose", [])) != 6:
                out.append(f"{side} arm: need x y z roll pitch yaw")
            if not 0 < float(a.get("pose_speed", 0)) <= 500:
                out.append(f"{side} arm: pose speed must be between 0 and 500 mm/s")
    return out


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
    problems = validate({"arm": cfg}, sides=("arm",))
    if problems:
        raise ValueError("; ".join(p.replace("arm arm", arm.name + " arm") for p in problems))
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
        if cur and len(cur) >= 6:
            safe_z = max(float(cfg.get("safe_z", 250.0)), float(cur[2]))
            _check(arm.arm.set_position(cur[0], cur[1], safe_z, cur[3], cur[4], cur[5],
                                        speed=spd, wait=True), f"{arm.name} lift")
        else:
            logger.warning("%s: no current pose readable, skipping lift-first", arm.name)
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
