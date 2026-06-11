"""
Generate a flat half-disc (semicircle) OBJ mesh for a safe-zone overlay.

The earlier zone overlay used boxes, which made the round turntable look square.
A triangulated half-disc renders as an actual round half so the disc reads as a
disc. Double-sided so it's visible from above or below.
"""

from __future__ import annotations

import math
import os

_GEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets_generated")


def half_disc_obj(name: str, radius: float, a0_deg: float, a1_deg: float,
                  segments: int = 72) -> str:
    """Write a half-disc OBJ spanning [a0_deg, a1_deg]; return its path."""
    os.makedirs(_GEN_DIR, exist_ok=True)
    path = os.path.join(_GEN_DIR, f"{name}.obj")

    verts = ["v 0 0 0"]
    for k in range(segments + 1):
        a = math.radians(a0_deg + (a1_deg - a0_deg) * k / segments)
        verts.append(f"v {radius * math.cos(a):.5f} {radius * math.sin(a):.5f} 0")

    faces = []
    for k in range(segments):
        c, b1, b2 = 1, 2 + k, 3 + k          # OBJ is 1-indexed; centre = vertex 1
        faces.append(f"f {c} {b1} {b2}")
        faces.append(f"f {c} {b2} {b1}")     # reverse winding -> double-sided

    with open(path, "w") as f:
        f.write("\n".join(verts) + "\n" + "\n".join(faces) + "\n")
    return path
