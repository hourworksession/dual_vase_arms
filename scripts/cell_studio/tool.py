"""Tool and nozzle settings: one place for what is mounted on the arms.

Settings ▸ Tool and nozzle. Saved in config/tool.json. Used by the cylinder
print, Prepare to print, the planner (Model print, Generators) and the macros,
so a mount or nozzle change is entered once.

Defaults match the current cell (Oct 2026): E3D Revo High Flow 1.2 mm nozzles,
pointing straight down (roll 180, pitch 0). The old 45° mount used pitch 45.
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
    "roll": 180.0,
    "pitch": 0.0,               # 0 = straight down (new mount); 45 = old tilted mount
    "yaw": 20.0,
}


def load():
    data = dict(DEFAULTS)
    try:
        with open(TOOL_FILE) as f:
            saved = json.load(f)
        for k in DEFAULTS:
            if k in saved:
                data[k] = type(DEFAULTS[k])(saved[k])
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
    return out
