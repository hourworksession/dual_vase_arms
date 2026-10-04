"""UFACTORY 850 kinematics and meshes for the 3D cell view.

The joint chain is UFACTORY's official one (simulation/primitive_arm.JOINTS, from
xarm_description). Meshes are the low-poly versions of the real 850 CAD in
simulation/assets/uf850/lowpoly (link frames, metres).

Poses follow the xArm SDK: x, y, z in mm in the arm's base frame (origin at the
centre of the base underside), roll/pitch/yaw in degrees with
R = Rz(yaw) · Ry(pitch) · Rx(roll). The flange (link6) frame is what the pose
describes when the TCP offset is zero.

FK gives every link's 4x4 pose (mm) from six joint angles. IK finds joint angles
for a pose (damped least squares, warm-started from the last answer) when only the
pose is known, e.g. on simulated hardware. Real arms report their joint angles,
which are used directly.
"""

from __future__ import annotations

import math
import os
import struct

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MESH_DIR = os.path.join(REPO_ROOT, "simulation", "assets", "uf850", "lowpoly")
LINKS = ["link_base", "link1", "link2", "link3", "link4", "link5", "link6"]
HOME_JOINTS_DEG = [0.0, -45.0, -45.0, 0.0, 90.0, 0.0]

# official UF850 chain: (xyz m, rpy rad, lower, upper) -- same as simulation/primitive_arm.JOINTS
_PI = math.pi
# limits: xArm 850 data sheet (J1, J4, J6 ±360°), the rest from xarm_description
CHAIN = [
    ((0.0, 0.0, 0.364), (0.0, 0.0, 0.0), -2 * _PI, 2 * _PI),
    ((0.0, 0.0, 0.0), (1.5708, -1.5708, 0.0), -2.3038346, 2.3038346),
    ((0.39, 0.0, 0.0), (-3.1416, 0.0, -1.5708), -4.2236968, 0.061087),
    ((0.15, 0.426, 0.0), (-1.5708, 0.0, 0.0), -2 * _PI, 2 * _PI),
    ((0.0, 0.0, 0.0), (-1.5708, 0.0, 0.0), -2.1642, 2.1642),
    ((0.0, -0.09, 0.0), (1.5708, 0.0, 0.0), -2 * _PI, 2 * _PI),
]


def rpy_matrix(r, p, y):
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def _origin(xyz, rpy):
    T = np.eye(4)
    T[:3, :3] = rpy_matrix(*rpy)
    T[:3, 3] = np.array(xyz) * 1000.0
    return T


_ORIGINS = [_origin(x, r) for x, r, _, _ in CHAIN]
LIMITS = np.array([(lo, hi) for _, _, lo, hi in CHAIN])
try:                                   # the real 850 limits (config/arms/xarm850.yaml) win
    from .home_config import JOINT_LIMITS as _JL
    LIMITS = np.radians(np.array(_JL, dtype=float))
except Exception:
    pass


def _rz(q):
    c, s = math.cos(q), math.sin(q)
    return np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1.0]])


def fk(q):
    """Poses (4x4, mm) of link_base, link1 … link6 in the arm base frame for joints q (rad)."""
    T = np.eye(4)
    out = [T]
    for O, qi in zip(_ORIGINS, q):
        T = T @ O @ _rz(qi)
        out.append(T)
    return out


def pose_matrix(pose):
    """xArm pose [x, y, z, roll, pitch, yaw] (mm, deg) -> 4x4."""
    T = np.eye(4)
    T[:3, :3] = rpy_matrix(*(math.radians(a) for a in pose[3:6]))
    T[:3, 3] = pose[:3]
    return T


def matrix_to_pose(T):
    """4x4 -> xArm pose [x, y, z, roll, pitch, yaw] (mm, deg)."""
    R = T[:3, :3]
    pitch = math.asin(max(-1.0, min(1.0, -R[2, 0])))
    if abs(R[2, 0]) < 0.99999:
        roll = math.atan2(R[2, 1], R[2, 2])
        yaw = math.atan2(R[1, 0], R[0, 0])
    else:                                    # gimbal lock (pitch ±90): put it all in roll
        yaw = 0.0
        roll = math.atan2(-R[0, 1], R[1, 1]) if R[2, 0] < 0 else math.atan2(R[0, 1], R[1, 1])
    return [float(T[0, 3]), float(T[1, 3]), float(T[2, 3]),
            math.degrees(roll), math.degrees(pitch), math.degrees(yaw)]


def flange_pose(joints_deg):
    """Joint angles (deg) -> xArm flange pose (TCP offset zero)."""
    return matrix_to_pose(fk(np.radians(joints_deg))[-1])


def _err(T, Td, w_rot):
    e = np.empty(6)
    e[:3] = Td[:3, 3] - T[:3, 3]
    e[3:] = _rotvec(Td[:3, :3] @ T[:3, :3].T) * w_rot   # rotation still to go
    return e


def _rotvec(R):
    """Rotation matrix -> rotation vector (axis * angle), correct up to 180°."""
    c = max(-1.0, min(1.0, (np.trace(R) - 1.0) / 2.0))
    ang = math.acos(c)
    v = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    if ang < 1e-6:
        return 0.5 * v
    if ang < math.pi - 1e-3:
        return v * (ang / (2.0 * math.sin(ang)))
    # near 180°: axis from the diagonal
    axis = np.sqrt(np.maximum((np.diag(R) + 1.0) / 2.0, 0.0))
    k = int(np.argmax(axis))
    if k == 0:
        axis[1] = math.copysign(axis[1], R[0, 1]); axis[2] = math.copysign(axis[2], R[0, 2])
    elif k == 1:
        axis[0] = math.copysign(axis[0], R[0, 1]); axis[2] = math.copysign(axis[2], R[1, 2])
    else:
        axis[0] = math.copysign(axis[0], R[0, 2]); axis[1] = math.copysign(axis[1], R[1, 2])
    return axis / max(np.linalg.norm(axis), 1e-9) * ang


def ik(pose, seed, iters=40, w_rot=200.0):
    """Joint angles (rad) putting the flange at `pose`, starting from `seed`.
    Returns (q, position error mm, converged)."""
    Td = pose_matrix(pose)
    q = np.array(seed, dtype=float)
    lam = 5.0
    for _ in range(iters):
        T = fk(q)[-1]
        e = _err(T, Td, w_rot)
        if np.linalg.norm(e[:3]) < 0.2 and np.linalg.norm(e[3:]) < 0.2:
            return q, float(np.linalg.norm(e[:3])), True
        J = np.empty((6, 6))
        h = 1e-5
        for j in range(6):
            dq = q.copy()
            dq[j] += h
            J[:, j] = (_err(T, Td, w_rot) - _err(fk(dq)[-1], Td, w_rot)) / h
        step = J.T @ np.linalg.solve(J @ J.T + lam * lam * np.eye(6), e)
        q = np.clip(q + step, LIMITS[:, 0], LIMITS[:, 1])
    T = fk(q)[-1]
    e = _err(T, Td, w_rot)
    pe = float(np.linalg.norm(e[:3]))
    return q, pe, pe < 2.0 and float(np.linalg.norm(e[3:])) / w_rot < math.radians(2)


def read_stl(path):
    """Binary or ASCII STL -> (triangles N x 3 x 3) in the file's units."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:5].lower() == b"solid" and b"facet" in data[:1024]:
        vals = [list(map(float, line.split()[1:4])) for line in data.decode("utf-8", "replace").splitlines()
                if line.strip().startswith("vertex")]
        return np.array(vals).reshape(-1, 3, 3)
    n = struct.unpack("<I", data[80:84])[0]
    rec = np.frombuffer(data, dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]), count=n, offset=84)
    return rec["v"].reshape(-1, 3, 3).astype(float)


_MESHES = None
_TOOL = None
TOOL_MESH = os.path.join(REPO_ROOT, "simulation", "assets", "tool", "revo_mount.stl")


def tool_mesh():
    """Mount triangles (mm, model frame centred on the flange), or None."""
    global _TOOL
    if _TOOL is None:
        try:
            _TOOL = read_stl(TOOL_MESH)
        except OSError:
            _TOOL = False
    return _TOOL if _TOOL is not False else None


def box_tris(x, y, z):
    """12 triangles of an axis-aligned box given (lo, hi) per axis."""
    import itertools
    c = np.array(list(itertools.product(x, y, z)))          # 8 corners
    f = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1),
         (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    return c[np.array(f)]


def meshes():
    """Per link triangles in mm (link frame), or None if the mesh files are missing."""
    global _MESHES
    if _MESHES is None:
        try:
            _MESHES = [read_stl(os.path.join(MESH_DIR, f"{n}.stl")) * 1000.0 for n in LINKS]
        except OSError:
            _MESHES = []
    return _MESHES or None
