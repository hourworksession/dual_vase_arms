"""
Turn UFACTORY's 850 CAD (STEP) into per-link meshes for the digital twin.

    pip install cadquery-ocp trimesh fast_simplification
    python -m simulation.step_to_link_meshes "UFACTORY 850(FX8510)-20260626.STEP"

Writes simulation/assets/uf850/link_base.stl ... link6.stl, each in its own URDF
link frame (metres), which primitive_arm.py then uses for the arm visuals.

How the parts are placed
------------------------
The STEP is an assembly of seven parts, FX85_P01 ... FX85_P07 = link_base,
link1 ... link6, exported in millimetres with Y up and every joint at zero.
So each link's frame is just the base frame (below) followed by the official
UF850 joint chain at q = 0 (primitive_arm.JOINTS). Each part is moved into its
link frame with the inverse of that.

This placement was checked by registering every part against UFACTORY's own
visual meshes (xarm_ros2/xarm_description/meshes/uf850): all six joints come
out at 0 deg (within 0.06 deg), the joint origins agree within 0.3 mm, and the
surfaces agree to a median of 0.005-0.16 mm.

Only needed again if UFACTORY publish a new STEP; the meshes are in the repo.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "assets", "uf850")
LINKS = ["link_base", "link1", "link2", "link3", "link4", "link5", "link6"]
MAX_FACES = 9000            # per link, keeps the repo and PyBullet light

# Base frame in the STEP's coordinates (mm): origin at the centre of the base
# plate's underside; link X = -CAD Z, link Y = -CAD X, link Z = +CAD Y (up).
BASE_IN_CAD = np.array([
    [0.0, -1.0, 0.0, 37.88],
    [0.0, 0.0, 1.0, 114.48],
    [-1.0, 0.0, 0.0, 64.60],
    [0.0, 0.0, 0.0, 1.0],
])


def _rpy(r, p, y):
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def link_frames_zero():
    """4x4 pose (mm) of every link frame in the base frame at q = 0."""
    sys.path.insert(0, os.path.dirname(HERE))
    from simulation.primitive_arm import JOINTS
    frames = {"link_base": np.eye(4)}
    T = np.eye(4)
    for (_, child, xyz, rpy, _, _) in JOINTS:
        J = np.eye(4)
        J[:3, :3] = _rpy(*rpy)
        J[:3, 3] = np.array(xyz) * 1000.0
        T = T @ J
        frames[child] = T.copy()
    return frames


def read_parts(step_path):
    """Tessellate the STEP's top-level parts, in file order, as trimesh meshes (mm)."""
    import trimesh
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopoDS import TopoDS_Iterator, TopoDS
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location

    r = STEPControl_Reader()
    if r.ReadFile(step_path) != 1:
        raise RuntimeError(f"Could not read {step_path}")
    r.TransferRoots()
    shape = r.OneShape()
    parts = []
    it = TopoDS_Iterator(shape)
    while it.More():
        s = it.Value()
        BRepMesh_IncrementalMesh(s, 0.3, False, 0.35, True)
        V, F, off = [], [], 0
        e = TopExp_Explorer(s, TopAbs_FACE)
        while e.More():
            f = TopoDS.Face(e.Current())
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(f, loc)
            if tri is not None:
                tr = loc.Transformation()
                for i in range(1, tri.NbNodes() + 1):
                    q = tri.Node(i).Transformed(tr)
                    V.append((q.X(), q.Y(), q.Z()))
                rev = f.Orientation() == TopAbs_REVERSED
                for i in range(1, tri.NbTriangles() + 1):
                    a, b, c = tri.Triangle(i).Get()
                    F.append((a - 1 + off, c - 1 + off, b - 1 + off) if rev else (a - 1 + off, b - 1 + off, c - 1 + off))
                off += tri.NbNodes()
            e.Next()
        parts.append(trimesh.Trimesh(np.array(V), np.array(F), process=True))
        it.Next()
    return parts


def main(step_path):
    parts = read_parts(step_path)
    if len(parts) != len(LINKS):
        raise SystemExit(f"Expected {len(LINKS)} parts (FX85_P01..P07), found {len(parts)}.")
    frames = link_frames_zero()
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, mesh in zip(LINKS, parts):
        mesh.apply_transform(np.linalg.inv(BASE_IN_CAD @ frames[name]))
        mesh.apply_scale(0.001)
        if len(mesh.faces) > MAX_FACES:
            mesh = mesh.simplify_quadric_decimation(face_count=MAX_FACES)
        path = os.path.join(OUT_DIR, f"{name}.stl")
        mesh.export(path)
        lo, hi = (mesh.bounds * 1000).round(1)
        print(f"{name:9s} {len(mesh.faces):6d} faces  bounds mm {lo.tolist()} .. {hi.tolist()}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    main(sys.argv[1])
