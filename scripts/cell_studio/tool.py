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
    # Nozzle tip (TCP) relative to the flange, in the flange frame, mm. ESTIMATED from the
    # mount model (revo_mount_new.3mf): the extruder sits INSIDE the bracket, between the
    # backbone plate and the side bracket, so its nozzle is about on the flange axis sideways,
    # 76 mm out along the flange axis and ~65 mm below the flange centre line. Must match
    # set_tcp_offset on each controller; measure and correct these.
    "tcp_right": [-65.0, 0.0, 76.0],
    "tcp_left": [65.0, 0.0, 76.0],
    # Approach: how the whole tool is turned about the vertical, per arm. 0 = the flange
    # points at the disc (the mount seen edge-on from the disc). +90 / -90 = the flange
    # points along the arm's Y (towards / away across the cell) so the mount lies sideways
    # to the disc: a slimmer body towards the other arm, and the extruder can get in
    # beside a tall feature. Added to the commanded yaw; the tool outline the collision
    # guard uses turns with it.
    "approach_right": 0.0,
    "approach_left": 0.0,
}

SIDES = ("right", "left")


def tcp(data, side):
    """Nozzle tip offset from the flange (flange frame, mm) for that arm."""
    v = data.get(f"tcp_{side}") or [0.0, 0.0, 0.0]
    return (float(v[0]), float(v[1]), float(v[2]))


# The mount model's axes -> the flange frame, for each arm: the mount is turned on the flange
# so the extruder hangs DOWN (flange -X for the right arm, +X for the left, given the mount
# orientations above). Columns = where model x, y, z go.
MOUNT_TO_FLANGE = {
    "right": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "left": ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
}
# The mount MESH is turned this much about the flange axis on top of MOUNT_TO_FLANGE (the
# extruder body and nozzle stay where they are: hanging down, inside the bracket).
MOUNT_MESH_YAW_DEG = 180.0
# extruder body (box, mm) in MODEL coordinates relative to the flange centre: INSIDE the
# bracket, against the backbone plate's inner face (plate at x -35..-23, side bracket from
# x +20), hanging down from the mount pattern (z 76) to the nozzle
EXTRUDER_BOX = {"x": (-23.0, 19.0), "y": (-60.0, 12.0), "z": (58.0, 94.0)}


def approach(data, side):
    """Turn of the whole tool about the vertical (deg) for that arm: 0 = flange at the disc,
    ±90 = flange along the arm's Y (mount sideways to the disc)."""
    return float(data.get(f"approach_{side}", 0.0) or 0.0)


def orientation(data, side):
    """(roll, pitch, yaw) commanded to that arm, with the approach turn in the yaw (a turn
    of the whole tool about the base Z is a change of yaw whatever the pitch)."""
    return (float(data[f"roll_{side}"]), float(data[f"pitch_{side}"]),
            float(data[f"yaw_{side}"]) + approach(data, side))


def load():
    data = dict(DEFAULTS)
    try:
        with open(TOOL_FILE) as f:
            saved = json.load(f)
        for k in DEFAULTS:
            if k in saved:
                if isinstance(DEFAULTS[k], list):
                    data[k] = [float(v) for v in saved[k]][:3]
                else:
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
        a = approach(data, side)
        if a not in (0.0, 90.0, -90.0):
            out.append(f"{side.capitalize()} approach {a:g}° is unusual: 0 faces the disc, ±90 lies sideways.")
    return out


def mesh_yaw():
    """Rotation applied to the mount mesh about the flange axis (model Z)."""
    import numpy as np
    import math
    a = math.radians(MOUNT_MESH_YAW_DEG)
    c, s_ = math.cos(a), math.sin(a)
    return np.array([[c, -s_, 0.0], [s_, c, 0.0], [0.0, 0.0, 1.0]])


def footprint(data, side):
    """Horizontal outline of the tool around the nozzle as a shapely Polygon in (approach,
    tangential) mm: approach = along the flange axis (toward the disc), tangential = flange Y,
    turned by the arm's approach setting (so a sideways mount is sideways in the guard too).
    The real (non-convex) outline: mount mesh triangles + extruder box, projected flat and
    unioned. The bodies are at the same height, so the flat test is the safe one.
    None without the mount mesh or shapely."""
    import numpy as np
    from . import arm_model as am
    mt = am.tool_mesh()
    if mt is None:
        return None
    try:
        from shapely.geometry import Polygon, box as _box
        from shapely.ops import unary_union
    except Exception:
        return None
    R = np.array(MOUNT_TO_FLANGE[side]).T
    off = np.array(tcp(data, side))
    tri = (mt.reshape(-1, 3) @ mesh_yaw().T @ R.T - off).reshape(-1, 3, 3)   # flange frame, nozzle at 0
    polys = []
    for t in tri:
        pts = [(float(q[2]), float(q[1])) for q in t]                   # (approach, tangential)
        pg = Polygon(pts)
        if pg.area > 0.5:
            polys.append(pg.buffer(0.5))
    bx = EXTRUDER_BOX
    c = np.array([[x, y, z] for x in bx["x"] for y in bx["y"] for z in bx["z"]]) @ R.T - off
    polys.append(_box(c[:, 2].min(), c[:, 1].min(), c[:, 2].max(), c[:, 1].max()))
    u = unary_union(polys).simplify(1.0)
    a = approach(data, side)
    if a:
        from shapely.affinity import rotate
        u = rotate(u, a, origin=(0, 0))
    return u
