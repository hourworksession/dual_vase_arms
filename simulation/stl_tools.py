"""
Pure-Python STL utilities (no CAD kernel needed).

The sandbox can't tessellate STEP, but SolidWorks exports STL easily and STL is
trivial to parse. These helpers read binary/ASCII STL, split a combined export
into its separate solid bodies (by connected components over shared vertices),
report each body, and write per-body STLs -- the input side of turning the real
UFACTORY 850 CAD into twin link meshes.

CLI:
    python -m simulation.stl_tools inspect path/to/arm.stl
    python -m simulation.stl_tools split   path/to/arm.stl  out_dir/
"""

from __future__ import annotations

import os
import struct
import sys
from typing import List, Tuple

import numpy as np

Mesh = Tuple[np.ndarray, np.ndarray]   # (vertices Nx3 float, faces Mx3 int)


# ---------------------------------------------------------------- read / write
def read_stl(path: str) -> Mesh:
    with open(path, "rb") as f:
        head = f.read(5)
        f.seek(0)
        if head[:5].lower() == b"solid":
            # might still be binary with a "solid" header -> sniff for ascii
            data = f.read()
            if b"facet" in data[:2048] or b"vertex" in data[:4096]:
                return _read_ascii(data.decode("utf-8", "replace"))
            f.seek(0)
        return _read_binary(f)


def _read_binary(f) -> Mesh:
    f.read(80)
    (n,) = struct.unpack("<I", f.read(4))
    verts = np.empty((n * 3, 3), dtype=np.float64)
    for i in range(n):
        f.read(12)  # normal
        tri = struct.unpack("<9f", f.read(36))
        verts[i * 3 + 0] = tri[0:3]
        verts[i * 3 + 1] = tri[3:6]
        verts[i * 3 + 2] = tri[6:9]
        f.read(2)   # attribute byte count
    faces = np.arange(n * 3, dtype=np.int64).reshape(n, 3)
    return _weld(verts, faces)


def _read_ascii(text: str) -> Mesh:
    pts = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("vertex"):
            _, x, y, z = line.split()[:4]
            pts.append((float(x), float(y), float(z)))
    verts = np.array(pts, dtype=np.float64)
    faces = np.arange(len(pts), dtype=np.int64).reshape(-1, 3)
    return _weld(verts, faces)


def _weld(verts: np.ndarray, faces: np.ndarray, decimals: int = 4) -> Mesh:
    """Merge duplicate vertices so faces share indices (needed for splitting)."""
    keys = np.round(verts, decimals)
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    return uniq, inv[faces]


def write_binary_stl(path: str, verts: np.ndarray, faces: np.ndarray) -> None:
    tri = verts[faces]                              # (M,3,3)
    n = len(faces)
    with open(path, "wb") as f:
        f.write(b"\x00" * 80)
        f.write(struct.pack("<I", n))
        for t in tri:
            v0, v1, v2 = t
            nrm = np.cross(v1 - v0, v2 - v0)
            ln = np.linalg.norm(nrm)
            nrm = nrm / ln if ln else nrm
            f.write(struct.pack("<3f", *nrm))
            f.write(struct.pack("<9f", *v0, *v1, *v2))
            f.write(b"\x00\x00")


# ---------------------------------------------------------------- split bodies
def split_connected(verts: np.ndarray, faces: np.ndarray) -> List[Mesh]:
    """Split into connected components via union-find over shared vertices."""
    parent = np.arange(len(verts))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for f in faces:
        union(f[0], f[1])
        union(f[1], f[2])

    roots = np.array([find(i) for i in range(len(verts))])
    bodies = []
    for r in np.unique(roots):
        vmask = roots == r
        if not vmask.any():
            continue
        fmask = vmask[faces[:, 0]]
        sub_faces = faces[fmask]
        if len(sub_faces) == 0:
            continue
        used = np.unique(sub_faces)
        remap = -np.ones(len(verts), dtype=np.int64)
        remap[used] = np.arange(len(used))
        bodies.append((verts[used], remap[sub_faces]))
    bodies.sort(key=lambda m: -len(m[1]))           # largest first
    return bodies


def describe(mesh: Mesh) -> str:
    v, f = mesh
    lo = v.min(axis=0)
    hi = v.max(axis=0)
    c = v.mean(axis=0)
    return (f"tris={len(f):6d}  size(mm)={np.round(hi - lo, 1)}  "
            f"centre={np.round(c, 1)}  zspan=[{lo[2]:.1f},{hi[2]:.1f}]")


# ---------------------------------------------------------------- CLI
def _main(argv):
    if len(argv) < 2:
        print(__doc__)
        return
    cmd, path = argv[0], argv[1]
    verts, faces = read_stl(path)
    bodies = split_connected(verts, faces)
    print(f"{path}: {len(faces)} triangles, {len(bodies)} bodies")
    for i, b in enumerate(bodies):
        print(f"  body {i:2d}: {describe(b)}")
    if cmd == "split":
        out = argv[2] if len(argv) > 2 else "stl_bodies"
        os.makedirs(out, exist_ok=True)
        for i, (v, f) in enumerate(bodies):
            p = os.path.join(out, f"body_{i:02d}.stl")
            write_binary_stl(p, v, f)
        print(f"wrote {len(bodies)} bodies to {out}/")


if __name__ == "__main__":
    _main(sys.argv[1:])
