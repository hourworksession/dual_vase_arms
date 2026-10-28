"""Parametric hopper case-study models (P1 ring, P2 tube, P3 tube with 35 deg collar).

Geometry is the thesis case study: 123.65 mm body radius (OD 248.5 mm, the spigot
for a 250 mm metric duct), single 1.2 mm bead wall,
12 in tall, collar root at 8 in rising at 35 deg to the horizontal over the last
4 in (a 55 deg overhang from vertical). All dimensions in mm; `scale` shrinks
everything uniformly (0.5 = campaign scale).

Usage:
    python cad/hopper.py                # writes build/cad/P1..P3 at scale 1.0 and 0.5
    python cad/hopper.py --scale 0.5    # one scale
Outputs STEP (for CAD), STL (for the twin / overlay viewer) and a JSON of the
derived numbers so the plan tables and the model never disagree.
"""
import argparse, json, math, os
import cadquery as cq

IN = 25.4
P = dict(
    radius=123.65,         # bead centreline; outside diameter 248.5 mm = spigot for a 250 mm (EN 1506) duct
    bead=1.2,              # wall thickness = one bead
    h1=12.0,               # P1 height, the misread "12"
    h2=12 * IN,            # P2 height, 12 inches
    root=8 * IN,           # collar root height
    rise=4 * IN,           # collar vertical rise
    angle_deg=35.0,        # collar angle to the HORIZONTAL (55 deg overhang)
)


def derived(scale=1.0):
    s = {k: (v * scale if k not in ("angle_deg", "bead") else v) for k, v in P.items()}  # bead is the nozzle, never scaled
    a = math.radians(s["angle_deg"])
    s["dr"] = s["rise"] / math.tan(a)           # radial growth of the collar
    s["rim_radius"] = s["radius"] + s["dr"]
    s["slant"] = s["rise"] / math.sin(a)
    s["overhang_deg"] = 90 - s["angle_deg"]
    s["scale"] = scale
    return s


def shell_tube(r_mid, h, t):
    """Vertical tube wall of thickness t centred on radius r_mid."""
    return (cq.Workplane("XY").circle(r_mid + t / 2).circle(r_mid - t / 2).extrude(h))


def shell_cone(r0, r1, z0, h, t):
    """Conical wall of thickness t (measured normal to the surface) from radius r0 at z0 to r1 at z0+h."""
    # Build a revolved profile: the wall as a parallelogram offset normal to the slant.
    a = math.atan2(h, r1 - r0)                  # slant angle to horizontal
    nx, nz = -math.sin(a) * t / 2, math.cos(a) * t / 2   # half-thickness normal offset
    pts = [(r0 + nx, z0 + nz), (r1 + nx, z0 + h + nz), (r1 - nx, z0 + h - nz), (r0 - nx, z0 - nz)]
    return (cq.Workplane("XZ").polyline(pts).close().revolve(360, (0, 0, 0), (0, 1, 0)))


def build(scale=1.0):
    s = derived(scale)
    r, t = s["radius"], s["bead"]
    p1 = shell_tube(r, s["h1"], t)
    p2 = shell_tube(r, s["h2"], t)
    p3 = shell_tube(r, s["root"], t).union(shell_cone(r, s["rim_radius"], s["root"], s["rise"], t))
    return s, {"P1_ring": p1, "P2_tube": p2, "P3_collar": p3}


def export(scale, outdir):
    s, parts = build(scale)
    tag = f"s{scale:g}".replace(".", "_")
    os.makedirs(outdir, exist_ok=True)
    for name, solid in parts.items():
        cq.exporters.export(solid, os.path.join(outdir, f"{name}_{tag}.step"))
        cq.exporters.export(solid, os.path.join(outdir, f"{name}_{tag}.stl"), tolerance=0.05, angularTolerance=0.05)
    with open(os.path.join(outdir, f"hopper_{tag}.json"), "w") as f:
        json.dump({k: round(v, 3) if isinstance(v, float) else v for k, v in s.items()}, f, indent=2)
    return s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=None)
    ap.add_argument("--out", default="build/cad")
    a = ap.parse_args()
    for sc in ([a.scale] if a.scale else [1.0, 0.5]):
        s = export(sc, a.out)
        print(f"scale {sc}: body R {s['radius']:.1f}, rim R {s['rim_radius']:.1f}, H2 {s['h2']:.1f}, "
              f"root {s['root']:.1f}, slant {s['slant']:.1f}, overhang {s['overhang_deg']:.0f} deg -> {a.out}")
