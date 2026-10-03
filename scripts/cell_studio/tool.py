"""Tool and nozzle settings: one place for what is mounted on the arms.

Settings ▸ Tool and nozzle. Saved in config/tool.json. Used by the cylinder
print, Prepare to print, the planner (Model print, Generators) and the macros,
so a mount or nozzle change is entered once.

Defaults match the current cell (Oct 2026): E3D Revo High Flow 1.2 mm nozzles on
the new mounts. Each arm's flange points horizontally at the disc and the extruder
hangs off it pointing straight down, so each arm has its OWN orientation:
    right arm: roll 90, pitch -90, yaw 90
    left arm:  roll 0,  pitch  90, yaw 0
(from the cell's current config, R_P_OFFSET / L_P_OFFSET etc.). The old tilted
mount used roll 180, pitch 45, yaw 20 on both arms.

Pitch ±90 is the xArm's roll/yaw "gimbal lock": there only (yaw - roll) or
(yaw + roll) matters, and the arm may REPORT a different but equivalent roll/yaw
pair from the one commanded. That is normal; the panel always commands the
values set here.

The orientation is what the panel commands. The xArm's own TCP offset (set on
the arm controller) must match the mount for the nozzle tip to be where the
panel thinks it is.
"""

import json
import os

from .home_config import REPO_ROOT

TOOL_FILE = os.path.join(REPO_ROOT, "config", "tool.json")

DEFAULTS = {
    "nozzle": "E3D Revo High Flow",
    "nozzle_diameter": 1.2,
    "line_width": 1.3,          # mm, default extrusion width
    "layer_height": 0.6,        # mm, default layer height (≤ ~75 % of the nozzle)
    "roll_right": 90.0,
    "pitch_right": -90.0,
    "yaw_right": 90.0,
    "roll_left": 0.0,
    "pitch_left": 90.0,
    "yaw_left": 0.0,
}

SIDES = ("right", "left")


def orientation(data, side):
    """(roll, pitch, yaw) commanded to that arm."""
    return (float(data[f"roll_{side}"]), float(data[f"pitch_{side}"]), float(data[f"yaw_{side}"]))


def load():
    data = dict(DEFAULTS)
    try:
        with open(TOOL_FILE) as f:
            saved = json.load(f)
        for k in DEFAULTS:
            if k in saved:
                data[k] = type(DEFAULTS[k])(saved[k])
        # A file from before the per-arm orientation had one roll/pitch/yaw for both arms.
        # Those values were for the old mounts, so the new per-arm defaults are kept.
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return data


def save(data):
    os.makedirs(os.path.dirname(TOOL_FILE), exist_ok=True)
    with open(TOOL_FILE, "w") as f:
        json.dump({k: data[k] for k in DEFAULTS}, f, indent=2)


def problems(data):
    out = []
    d = data["nozzle_diameter"]
    if not 0.1 <= d <= 3.0:
        out.append(f"Nozzle diameter {d} mm looks wrong.")
    if not 0.5 * d <= data["line_width"] <= 2.5 * d:
        out.append(f"Line width {data['line_width']} mm is far from the {d} mm nozzle.")
    if not 0.05 <= data["layer_height"] <= 0.9 * d:
        out.append(f"Layer height {data['layer_height']} mm should be under about {0.8 * d:.2f} mm for a {d} mm nozzle.")
    for side in SIDES:
        p = data[f"pitch_{side}"]
        if not -90.0 <= p <= 90.0:
            out.append(f"{side.capitalize()} arm pitch {p:g}° is outside the xArm's -90…90° range.")
    return out
