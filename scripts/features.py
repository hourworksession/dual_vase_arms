"""Feature classification: what in the model is a primitive the toolpath can be built from.

Two passes, both working from sections of the mesh:

1. Prisms (Z direction). Every solid piece and every hole of every layer is matched to the
   layer below; where its outline is unchanged (symmetric difference under `tol` of the
   area) the run grows. A run over several layers is a prism: a post, a bore, a straight
   wall, a handle's tube. Each is classified as the FullControl primitive that draws it,
   with the coordinates where it happens:
       circle     -> fullcontrol.circleXY(centre, radius)   / helixZ over the run
       ring       -> two circles (outer bead, bore)
       rectangle  -> fullcontrol.rectangleXY(corner, w, h) turned by its angle
       polygon    -> fullcontrol.polygonXY(centre, radius, sides) when regular,
                     else the simplified vertex list
   A continuous run means the nozzle can climb it as one helix (continuous extrusion)
   instead of a lap per layer.

2. Revolved (polar). The mesh is cut by half-planes through the turntable axis every
   `step_mm` of arc at the part's largest radius (0.6 mm there = one bead), giving the
   (r, z) profile at each angle. Where the profile is unchanged from one angle to the next
   the part is a surface of revolution: that is where the spiral prints it continuously,
   both arms 180 degrees apart. Angle ranges where the profile changes are local features
   (handles, lugs) with their angle and height range, for the arm that faces them.

The result is data for the generator (and the rules AI): which parts of the model are
which primitive, where, and over what height or angle, so the spiral and the local
helices can be laid out as point-to-point FullControl paths.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from shapely.geometry import Polygon, Point, LineString
from shapely.ops import unary_union


@dataclass
class Feature:
    kind: str                     # circle | ring | rectangle | polygon | revolved | local
    role: str                     # solid | hole | wall
    z0: float
    z1: float
    layers: Tuple[int, int]       # first, last layer index (inclusive)
    centre: Tuple[float, float]   # plate frame, mm
    params: dict = field(default_factory=dict)
    theta: Optional[Tuple[float, float]] = None   # degrees, polar runs only
    primitive: str = ""           # the FullControl call that draws it
    coords: List[Tuple[float, float]] = field(default_factory=list)   # anchor points (plate frame)

    @property
    def height(self):
        return self.z1 - self.z0

    def encloses_axis(self, cx=0.0, cy=0.0) -> bool:
        """Whether the outline goes round the turntable axis (the main wall or bore)."""
        if self.kind in ("circle", "ring"):
            return math.hypot(self.centre[0] - cx, self.centre[1] - cy) < self.params["radius"]
        if len(self.coords) >= 3:
            try:
                return Polygon(self.coords).buffer(1.0).contains(Point(cx, cy))
            except Exception:
                return False
        return False

    def describe(self) -> str:
        c = f"({self.centre[0]:.1f}, {self.centre[1]:.1f})"
        if self.kind in ("circle", "ring"):
            s = f"{self.kind} r {self.params['radius']:.1f}" + (f" bore {self.params['bore']:.1f}" if "bore" in self.params else "")
        elif self.kind == "rectangle":
            s = f"rectangle {self.params['w']:.1f}x{self.params['h']:.1f} at {self.params['angle']:.0f}°"
        elif self.kind == "polygon":
            s = f"polygon {self.params['sides']} sides r {self.params['radius']:.1f}"
        elif self.kind == "revolved":
            return (f"revolved wall θ {self.theta[0]:.0f}–{self.theta[1]:.0f}° · r {self.params['r_min']:.0f}–"
                    f"{self.params['r_max']:.0f} · z {self.z0:.1f}–{self.z1:.1f}")
        elif self.kind == "local":
            return (f"local feature θ {self.theta[0]:.0f}–{self.theta[1]:.0f}° · r to {self.params['r_max']:.0f}"
                    f" · z {self.z0:.1f}–{self.z1:.1f}")
        else:
            s = self.kind
        return f"{s} {self.role} at {c} · z {self.z0:.1f}–{self.z1:.1f} ({self.layers[1] - self.layers[0] + 1} layers)"


@dataclass
class FeatureMap:
    prisms: List[Feature] = field(default_factory=list)
    revolved: List[Feature] = field(default_factory=list)
    local: List[Feature] = field(default_factory=list)
    step_deg: float = 0.0
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        out = []
        helix = [f for f in self.prisms if f.layers[1] > f.layers[0]]
        out.append(f"Features: {len(self.prisms)} prisms unchanged in Z ({len(helix)} over more than one layer, "
                   f"printable as continuous helices), {len(self.revolved)} revolved runs, "
                   f"{len(self.local)} local angle ranges (polar step {self.step_deg:.2f}°).")
        for f in sorted(self.prisms, key=lambda f: -f.height)[:12]:
            out.append("  " + f.describe() + "  -> " + f.primitive)
        for f in self.revolved[:8]:
            out.append("  " + f.describe())
        for f in self.local[:8]:
            out.append("  " + f.describe())
        out.extend("  " + n for n in self.notes)
        return "\n".join(out)

    def fullcontrol(self) -> str:
        """The primitives as FullControl calls, one per feature, with their coordinates."""
        lines = ["import fullcontrol as fc", "steps = []"]
        for f in self.prisms:
            lines.append(f"# {f.describe()}")
            lines.append(f"steps.extend({f.primitive})")
        for f in self.revolved:
            lines.append(f"# {f.describe()}: spiral (helix) following the profile, both arms")
        return "\n".join(lines)

    def prism_runs(self, role="hole"):
        """(first layer, last layer, centre, radius) of circular runs for the local helices."""
        return [(f.layers[0], f.layers[1], f.centre, f.params.get("radius"))
                for f in self.prisms if f.role == role and f.kind in ("circle", "ring")]


# ------------------------------------------------------------------ shape tests
def classify_ring(ring_coords) -> Tuple[str, dict, Tuple[float, float], list]:
    """Which primitive a closed outline is. Returns (kind, params, centre, anchor coords)."""
    P = Polygon(ring_coords)
    if P.area <= 0:
        P = P.buffer(0)
    c = P.centroid
    cx, cy = float(c.x), float(c.y)
    pts = np.array(P.exterior.coords[:-1])
    rr = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
    r_mean = float(rr.mean())
    # circle: radius spread small and area close to pi r^2
    if r_mean > 0 and (rr.max() - rr.min()) / r_mean < 0.06 and abs(P.area - math.pi * r_mean ** 2) < 0.08 * P.area:
        return "circle", {"radius": r_mean}, (cx, cy), [(cx + r_mean, cy)]
    mrr = P.minimum_rotated_rectangle
    if mrr.area > 0 and P.area / mrr.area > 0.95:
        q = np.array(mrr.exterior.coords[:-1])
        e1, e2 = q[1] - q[0], q[2] - q[1]
        w, h = float(np.linalg.norm(e1)), float(np.linalg.norm(e2))
        ang = math.degrees(math.atan2(e1[1], e1[0])) % 180.0
        return "rectangle", {"w": w, "h": h, "angle": ang}, (cx, cy), [tuple(map(float, v)) for v in q]
    simp = P.simplify(0.15, preserve_topology=True)
    verts = np.array(simp.exterior.coords[:-1])
    n = len(verts)
    rv = np.hypot(verts[:, 0] - cx, verts[:, 1] - cy)
    regular = n >= 3 and rv.mean() > 0 and (rv.max() - rv.min()) / rv.mean() < 0.05
    return "polygon", {"sides": int(n), "radius": float(rv.mean()), "regular": bool(regular)}, (cx, cy), \
        [tuple(map(float, v)) for v in verts]


def _primitive(kind, role, params, centre, coords, z0, z1, lh):
    cx, cy = centre
    laps = max(1, int(round((z1 - z0) / lh)))
    if kind == "circle":
        return (f"fc.helixZ(fc.Point(x={cx:.2f}, y={cy:.2f}, z={z0:.2f}), start_radius={params['radius']:.2f}, "
                f"end_radius={params['radius']:.2f}, start_angle=0, n_turns={laps}, pitch_z={lh:.2f}, segments={64 * laps})")
    if kind == "ring":
        return (f"fc.helixZ(fc.Point(x={cx:.2f}, y={cy:.2f}, z={z0:.2f}), start_radius={params['radius']:.2f}, "
                f"end_radius={params['radius']:.2f}, start_angle=0, n_turns={laps}, pitch_z={lh:.2f}, segments={64 * laps})"
                f"  # and the bore: helixZ radius {params['bore']:.2f}")
    if kind == "rectangle":
        x0, y0 = coords[0]
        return (f"fc.rectangleXY(fc.Point(x={x0:.2f}, y={y0:.2f}, z={z0:.2f}), x_size={params['w']:.2f}, "
                f"y_size={params['h']:.2f})  # turned {params['angle']:.1f}°, repeated {laps} layers")
    if kind == "polygon" and params.get("regular"):
        return (f"fc.polygonXY(fc.Point(x={cx:.2f}, y={cy:.2f}, z={z0:.2f}), enclosing_radius={params['radius']:.2f}, "
                f"start_angle=0, sides={params['sides']})  # repeated {laps} layers")
    pts = ", ".join(f"fc.Point(x={x:.1f}, y={y:.1f}, z={z0:.2f})" for x, y in coords[:12])
    return f"[{pts}{', ...' if len(coords) > 12 else ''}]  # {params['sides']}-vertex polygon, repeated {laps} layers"


# ------------------------------------------------------------------ pass 1: prisms
def prisms(sections, zs, lh, tol=0.03, min_area=2.0) -> List[Feature]:
    """sections: per layer, list of shapely Polygons (plate frame). zs: layer centre heights."""
    items_prev = []           # (role, polygon, run)
    runs = []                 # dicts: role, i0, i1, polys
    for i, polys in enumerate(sections):
        items = []
        for q in polys or []:
            if q.is_empty or q.area < min_area:
                continue
            items.append(("solid", q))
            for ring in q.interiors:
                hp = Polygon(ring)
                if hp.area >= min_area:
                    items.append(("hole", hp))
        cur = []
        used = set()
        for role, g in items:
            run = None
            b = g.bounds
            for k, (role_p, gp, run_p) in enumerate(items_prev):
                if k in used or role_p != role:
                    continue
                bp = gp.bounds
                if abs(b[0] - bp[0]) > 1 or abs(b[1] - bp[1]) > 1 or abs(b[2] - bp[2]) > 1 or abs(b[3] - bp[3]) > 1:
                    continue
                try:
                    if g.symmetric_difference(gp).area <= tol * max(g.area, gp.area):
                        run, used_k = run_p, k
                        break
                except Exception:
                    continue
            if run is None:
                run = {"role": role, "i0": i, "i1": i, "polys": [g]}
                runs.append(run)
            else:
                used.add(used_k)
                run["i1"] = i
                run["polys"].append(g)
            cur.append((role, g, run))
        items_prev = cur
    feats = []
    for run in runs:
        g = run["polys"][len(run["polys"]) // 2]
        kind, params, centre, coords = classify_ring(list(g.exterior.coords))
        role = run["role"]
        if role == "solid" and kind == "circle" and len(g.interiors) == 1:
            hk, hp, hc, _ = classify_ring(list(g.interiors[0].coords))
            if hk == "circle" and math.hypot(hc[0] - centre[0], hc[1] - centre[1]) < 1.0:
                kind, params = "ring", {"radius": params["radius"], "bore": hp["radius"]}
        z0, z1 = zs[run["i0"]] - lh / 2, zs[run["i1"]] + lh / 2
        f = Feature(kind, role, z0, z1, (run["i0"], run["i1"]), centre, params, None, "", coords)
        f.primitive = _primitive(kind, role, params, centre, coords, z0, z1, lh)
        feats.append(f)
    return feats


# ------------------------------------------------------------------ pass 2: polar
def polar_profiles(mesh, step_mm=0.6, max_steps=3600, cx=0.0, cy=0.0):
    """(thetas_deg, profiles) where each profile is the union of the (r, z) polygons of the
    half-plane section at that angle (r >= 0 side)."""
    import trimesh  # noqa: F401
    b = mesh.bounds
    r_max = float(max(np.hypot(b[:, 0] - cx, b[:, 1] - cy).max(), 1.0))
    n = int(min(max_steps, max(36, math.ceil(2 * math.pi * r_max / step_mm))))
    thetas = np.linspace(0.0, 360.0, n, endpoint=False)
    profiles = []
    for th in thetas:
        a = math.radians(th)
        d = np.array([math.cos(a), math.sin(a), 0.0])
        normal = np.array([-math.sin(a), math.cos(a), 0.0])
        sec = mesh.section(plane_origin=[cx, cy, 0.0], plane_normal=normal)
        if sec is None:
            profiles.append(None)
            continue
        polys = []
        for ent in sec.entities:
            pts = sec.vertices[ent.points]
            if len(pts) < 3:
                continue
            r = (pts[:, 0] - cx) * d[0] + (pts[:, 1] - cy) * d[1]
            rz = np.column_stack([r, pts[:, 2]])
            try:
                pg = Polygon(rz)
                if not pg.is_valid:
                    pg = pg.buffer(0)
                if pg.area > 0.5:
                    polys.append(pg)
            except Exception:
                continue
        if not polys:
            profiles.append(None)
            continue
        u = unary_union(polys)
        half = u.intersection(Polygon([(0, b[0][2] - 1), (r_max + 1, b[0][2] - 1), (r_max + 1, b[1][2] + 1), (0, b[1][2] + 1)]))
        profiles.append(half if not half.is_empty else None)
    return thetas, profiles


def revolved_runs(thetas, profiles, tol=0.03) -> Tuple[List[Feature], List[Feature]]:
    """Angle runs where the (r, z) profile is unchanged (revolved) and where it changes (local)."""
    n = len(thetas)
    same = np.zeros(n, dtype=bool)
    for k in range(n):
        a, b = profiles[k], profiles[(k + 1) % n]
        if a is None or b is None:
            same[k] = a is None and b is None
            continue
        try:
            same[k] = a.symmetric_difference(b).area <= tol * max(a.area, b.area)
        except Exception:
            same[k] = False
    # runs of consecutive "same" steps
    runs = []
    k = 0
    # start at a change so runs do not wrap awkwardly
    start = int(np.argmin(same)) if not same.all() else 0
    order = [(start + j) % n for j in range(n)]
    cur = None
    for j in order:
        s = bool(same[j])
        if cur is None or cur[0] != s:
            cur = [s, j, j]
            runs.append(cur)
        else:
            cur[2] = j
    step = 360.0 / n
    # a "revolved" run narrower than 5 degrees is noise between two features: call it local,
    # then merge neighbouring local runs
    for r in runs:
        if r[0] and ((r[2] - r[1]) % n + 1) * step < 5.0:
            r[0] = False
    merged = []
    for r in runs:
        if merged and not merged[-1][0] and not r[0]:
            merged[-1][2] = r[2]
        else:
            merged.append(r)
    if len(merged) > 1 and not merged[0][0] and not merged[-1][0]:
        merged[-1][2] = merged[0][2]
        merged.pop(0)
    runs = merged
    rev, loc = [], []
    for s, j0, j1 in runs:
        span = ((j1 - j0) % n + 1) * step
        pr = [profiles[(j0 + m) % n] for m in range(0, (j1 - j0) % n + 1, max(1, ((j1 - j0) % n + 1) // 8 or 1))]
        pr = [p for p in pr if p is not None]
        if not pr:
            continue
        bb = np.array([p.bounds for p in pr])
        r_min, r_max = float(bb[:, 0].min()), float(bb[:, 2].max())
        z0, z1 = float(bb[:, 1].min()), float(bb[:, 3].max())
        th0, th1 = float(thetas[j0]), float(thetas[j1]) + step
        f = Feature("revolved" if s else "local", "wall", z0, z1, (0, 0), (0.0, 0.0),
                    {"r_min": r_min, "r_max": r_max, "span_deg": span}, (th0, th1))
        (rev if s else loc).append(f)
    return rev, loc


# ------------------------------------------------------------------ entry
def classify(mesh, sections, zs, lh, step_mm=0.6, polar=True, progress=None) -> FeatureMap:
    fm = FeatureMap()
    fm.prisms = prisms(sections, zs, lh)
    if polar:
        thetas, profiles = polar_profiles(mesh, step_mm=step_mm)
        fm.step_deg = 360.0 / len(thetas)
        fm.revolved, fm.local = revolved_runs(thetas, profiles)
        rev = sum(f.params["span_deg"] for f in fm.revolved)
        fm.notes.append(f"{rev:.0f}° of the part is a surface of revolution (spiral, both arms); "
                        f"{360 - rev:.0f}° has local features (held, faced to one arm).")
    return fm


def classify_mesh(mesh_path, lh, step_mm=0.6):
    from slicer import _load_mesh, _section_polygons
    mesh = _load_mesh(mesh_path)
    zmin, zmax = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    n = max(1, int(math.floor((zmax - zmin) / lh + 1e-6)))
    zs = [zmin + lh * (i + 0.5) for i in range(n)]
    sections = [_section_polygons(mesh, z) for z in zs]
    return classify(mesh, sections, zs, lh, step_mm)


if __name__ == "__main__":
    import sys
    fm = classify_mesh(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 0.6)
    print(fm.summary())
    print()
    print(fm.fullcontrol())
