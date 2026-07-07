"""
Generate a placeholder UF850 (xArm 850) URDF with the REAL kinematics.

The joint origins, rpy and axes below are taken verbatim from UFACTORY's
official ``xarm_description`` (uf850.urdf.xacro, the commented default
``<origin>`` values), so the chain matches the physical robot:

    joint1  xyz 0 0 0.364     rpy 0 0 0           axis Z
    joint2  xyz 0 0 0         rpy 1.5708 -1.5708 0 axis Z
    joint3  xyz 0.39 0 0      rpy -3.1416 0 -1.5708 axis Z
    joint4  xyz 0.15 0.426 0  rpy -1.5708 0 0      axis Z
    joint5  xyz 0 0 0         rpy -1.5708 0 0      axis Z
    joint6  xyz 0 -0.09 0     rpy 1.5708 0 0       axis Z

Every joint rotates about its local Z, exactly like the SDK's
``get_servo_angle`` output -- so feeding real joint angles into this model
(drive_mode="joints") reproduces the arm's true configuration. Because meshes
aren't exported yet, links are drawn as cylinders that bridge each joint to the
next; swap in the official STL meshes later for photoreal geometry without
touching the kinematics.

A Hemera-style extruder is mounted on the flange at a fixed 45 deg
(``TOOL_MOUNT_RPY``); joint6 (wrist roll) rotates the angled tool to set the
print direction, as on the hardware. Limits are the official UF850 values.

Sources:
  https://github.com/xArm-Developer/xarm_ros2 (xarm_description/urdf/uf850)
"""

from __future__ import annotations

import math
import os
from typing import List, Tuple

PI = math.pi

# (name, child, xyz, rpy, lower, upper)  -- official UF850 defaults
JOINTS = [
    ("joint1", "link1", (0.0, 0.0, 0.364), (0.0, 0.0, 0.0), -PI * 0.99, PI * 0.99),
    ("joint2", "link2", (0.0, 0.0, 0.0), (1.5708, -1.5708, 0.0), -2.3038346, 2.3038346),
    ("joint3", "link3", (0.39, 0.0, 0.0), (-3.1416, 0.0, -1.5708), -4.2236968, 0.061087),
    ("joint4", "link4", (0.15, 0.426, 0.0), (-1.5708, 0.0, 0.0), -PI * 0.99, PI * 0.99),
    ("joint5", "link5", (0.0, 0.0, 0.0), (-1.5708, 0.0, 0.0), -2.1642, 2.1642),
    ("joint6", "link6", (0.0, -0.09, 0.0), (1.5708, 0.0, 0.0), -PI * 0.99, PI * 0.99),
]

# Extruder tool mounted INLINE with the flange (no baked tilt). On the real
# machine the 45-deg print angle comes from the COMMANDED orientation
# (roll 180, pitch 45) -- the arm's TCP convention -- not from a hidden geometry
# offset. So the tool stays aligned with the flange here and the commanded pitch
# drives the angle 1:1, matching the hardware (pitch 45 -> correct position).
TOOL_MOUNT_RPY_LEFT = (0.0, 0.0, 0.0)
TOOL_MOUNT_RPY_RIGHT = (0.0, 0.0, 0.0)
TOOL_MOUNT_RPY = TOOL_MOUNT_RPY_LEFT   # default (back-compat)
TOOL_BODY_LEN = 0.10

# Null-space IK rest pose (rad). Mechanical zero is valid; a slight elbow bend
# gives a natural, stable posture for the offline/commanded (IK) path.
REST_POSE = [0.0, 0.30, -0.60, 0.0, 0.30, 0.0]

EE_LINK_NAME = "tool_tip"
_GEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets_generated")
_GEN_PATH = os.path.join(_GEN_DIR, "primitive_xarm850.urdf")


def _fmt_rpy(rpy):
    return f"{rpy[0]:.4f} {rpy[1]:.4f} {rpy[2]:.4f}"


def _connector(target, radius, rgba):
    """Cylinder from this link's origin to ``target`` (x,y,z) in the link frame."""
    x, y, z = target
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-6:
        return ""
    pitch = math.atan2(math.hypot(x, y), z)
    yaw = math.atan2(y, x)
    mid = (x / 2.0, y / 2.0, z / 2.0)
    return (
        f'    <visual><origin xyz="{mid[0]:.4f} {mid[1]:.4f} {mid[2]:.4f}" '
        f'rpy="0 {pitch:.4f} {yaw:.4f}"/>'
        f'<geometry><cylinder radius="{radius:.4f}" length="{length:.4f}"/></geometry>'
        f'<material name="seg"><color rgba="{rgba}"/></material></visual>\n'
        f'    <collision><origin xyz="{mid[0]:.4f} {mid[1]:.4f} {mid[2]:.4f}" '
        f'rpy="0 {pitch:.4f} {yaw:.4f}"/>'
        f'<geometry><cylinder radius="{radius:.4f}" length="{length:.4f}"/></geometry></collision>\n'
    )


def _knuckle(radius, rgba):
    return (
        f'    <visual><geometry><sphere radius="{radius:.4f}"/></geometry>'
        f'<material name="knk"><color rgba="{rgba}"/></material></visual>\n'
    )


def _inertial():
    return ('    <inertial><mass value="0.5"/>'
            '<inertia ixx="0.002" iyy="0.002" izz="0.002" ixy="0" ixz="0" iyz="0"/>'
            '</inertial>\n')


def generate_urdf(path: str = _GEN_PATH, tool_mount_rpy=TOOL_MOUNT_RPY) -> Tuple[str, str]:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out: List[str] = ['<?xml version="1.0"?>\n<robot name="uf850_primitive">\n']

    # child-joint origin in each parent link's frame -> draws the bridging segment
    child_origin = {
        "link_base": JOINTS[0][2],   # base column up to joint1
        "link1": JOINTS[1][2],
        "link2": JOINTS[2][2],
        "link3": JOINTS[3][2],
        "link4": JOINTS[4][2],
        "link5": JOINTS[5][2],
    }
    seg_rgba = {
        "link_base": "0.35 0.35 0.38 1",
        "link2": "0.30 0.65 0.35 1",
        "link3": "0.30 0.45 0.80 1",
        "link5": "0.65 0.35 0.80 1",
    }

    # base link with the vertical column up to joint1
    out.append('  <link name="link_base">\n')
    out.append(_connector(child_origin["link_base"], 0.055, seg_rgba["link_base"]))
    out.append(_inertial())
    out.append("  </link>\n")

    parent = "link_base"
    for name, child, xyz, rpy, lo, hi in JOINTS:
        out.append(
            f'  <joint name="{name}" type="revolute">\n'
            f'    <parent link="{parent}"/><child link="{child}"/>\n'
            f'    <origin xyz="{xyz[0]:.4f} {xyz[1]:.4f} {xyz[2]:.4f}" rpy="{_fmt_rpy(rpy)}"/>\n'
            f'    <axis xyz="0 0 1"/>\n'
            f'    <limit lower="{lo:.5f}" upper="{hi:.5f}" effort="200" velocity="3.14"/>\n'
            f'  </joint>\n'
        )
        out.append(f'  <link name="{child}">\n')
        out.append(_knuckle(0.05, "0.2 0.2 0.22 1"))
        seg = child_origin.get(child)
        if seg is not None and child in seg_rgba:
            out.append(_connector(seg, 0.045, seg_rgba[child]))
        out.append(_inertial())
        out.append("  </link>\n")
        parent = child

    # ---- angled extruder tool on the flange (link6) ----
    out.append(
        f'  <joint name="tool_mount" type="fixed">\n'
        f'    <parent link="link6"/><child link="extruder"/>\n'
        f'    <origin xyz="0 0 0" rpy="{_fmt_rpy(tool_mount_rpy)}"/>\n'
        f'  </joint>\n'
        f'  <link name="extruder">\n'
        f'    <visual><origin xyz="0 0 {TOOL_BODY_LEN*0.45:.4f}"/>'
        f'<geometry><box size="0.05 0.045 {TOOL_BODY_LEN*0.9:.4f}"/></geometry>'
        f'<material name="ext"><color rgba="0.15 0.15 0.15 1"/></material></visual>\n'
        f'{_inertial()}'
        f'  </link>\n'
    )
    out.append(
        f'  <joint name="nozzle" type="fixed">\n'
        f'    <parent link="extruder"/><child link="{EE_LINK_NAME}"/>\n'
        f'    <origin xyz="0 0 {TOOL_BODY_LEN:.4f}" rpy="0 0 0"/>\n'
        f'  </joint>\n'
        f'  <link name="{EE_LINK_NAME}">\n'
        f'    <visual><origin xyz="0 0 -0.012"/>'
        f'<geometry><cylinder radius="0.006" length="0.024"/></geometry>'
        f'<material name="noz"><color rgba="0.9 0.9 0.2 1"/></material></visual>\n'
        f'    <inertial><mass value="0.05"/>'
        f'<inertia ixx="1e-5" iyy="1e-5" izz="1e-5" ixy="0" ixz="0" iyz="0"/></inertial>\n'
        f'  </link>\n'
    )

    out.append("</robot>\n")
    with open(path, "w") as f:
        f.write("".join(out))
    return path, EE_LINK_NAME


def urdf_for_arm(arm: str) -> Tuple[str, str]:
    """Generate (or regenerate) the URDF for a given arm with the correct tool
    tilt, and return (path, ee_link). The right arm's extruder is mirrored so it
    points the same way in the world as the left's."""
    rpy = TOOL_MOUNT_RPY_RIGHT if arm == "right" else TOOL_MOUNT_RPY_LEFT
    path = os.path.join(_GEN_DIR, f"primitive_xarm850_{arm}.urdf")
    return generate_urdf(path, tool_mount_rpy=rpy)


if __name__ == "__main__":
    for a in ("left", "right"):
        pth, ee = urdf_for_arm(a)
        print(f"wrote {pth} ({a} arm, UF850 kinematics, ee link: {ee})")
