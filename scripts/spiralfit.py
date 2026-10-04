"""Spiral-first toolpaths: the model as spiral segments for two arms and a turntable.

The idea (Wil's): generate a spiral that follows the model's wall, run it up until it
leaves the wall (a slot, a hole, the wall ending) — that intersection sets the top of
the segment — then start the next segment where the wall exists again, and repeat
until the model is covered. Both arms print at once, 180° apart on the same wall:

  * one bead across the wall  -> two interleaved helices of pitch 2·lh (the proven
    dual-vase interweave): together they make the wall at the normal layer height;
  * two beads                 -> the right arm spirals the outer bead, the left arm the
    inner bead, half a layer up (bricked), both at pitch lh;
  * thicker walls             -> extra beads in further passes.

The nozzle leans with the wall (dr/dz), within the hardware tilt limit, so the bead is
laid along the surface instead of stepping out into air.

What the spiral cannot cover is left to flat layers: the bottom and top skins, and
closed regions off the axis (the trug's posts) which get their own local helix
around their centre, by the right arm with the disc holding.

Output: a SliceResult-compatible object whose paths carry 6-value points
(x, y, z, width, height, tilt_deg) and an `arm` attribute (0 right, 1 left). Arm 0 and
arm 1 paths are emitted in lock-step (same number of points per turn, gaps filled with
lifted TRAVEL hops) so the planner streams them together with one disc motion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from slicer import (SliceSettings, SliceResult, Layer, Path, WALL_OUTER, WALL_INNER, SKIN, TRAVEL,
                    _section_polygons, _load_mesh, _walls_for_polygon, _infill_for_region)

N_THETA = 360                      # ray samples per turn (1° -> ~0.9 mm at r 50)
FAR = 2000.0


@dataclass
class Segment:
    arm: int
    bead: int
    z0: float
    z1: float
    turns: float
    why_end: str


@dataclass
class SpiralReport:
    segments: List[Segment] = field(default_factory=list)
    planar_layers: List[int] = field(default_factory=list)
    local_features: int = 0
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.segments:
            return "no spiral segments"
        by_arm = {}
        for s in self.segments:
            by_arm.setdefault(s.arm, []).append(s)
        lines = [f"{len(self.segments)} spiral segments "
                 f"({', '.join(f'arm {a}: {len(v)}' for a, v in sorted(by_arm.items()))}); "
                 f"{len(self.planar_layers)} flat layers; {self.local_features} local helices"]
        ends = {}
        for s in self.segments:
            ends[s.why_end] = ends.get(s.why_end, 0) + 1
        lines.append("  segment ends: " + ", ".join(f"{n} × {w}" for w, n in sorted(ends.items(), key=lambda kv: -kv[1])))
        for s in self.segments[:8]:
            lines.append(f"  arm {s.arm} bead {s.bead}: z {s.z0:.1f} → {s.z1:.1f} mm, {s.turns:.1f} turns, ends: {s.why_end}")
        if len(self.segments) > 8:
            lines.append(f"  … {len(self.segments) - 8} more")
        return "\n".join(lines)


# ---------------------------------------------------------------- bead lines by ray
def _main_polygon(polys, cx=0.0, cy=0.0):
    """The section polygon whose outer ring goes round the axis, or None."""
    from shapely.geometry import Polygon, Point
    for q in polys:
        try:
            if Polygon(q.exterior).contains(Point(cx, cy)):
                return q
        except Exception:
            continue
    return None


def _bead_lines(poly, lw, beads):
    """Rings the beads follow. Bead 0 (outer): the outside ring of the section shrunk by
    half a line width. Bead 1 (inner): the ring of that shrunk section that goes round the
    axis on the INSIDE (continuous round posts and bumps); when the layer is C-shaped
    (slots) there is no such ring and bead 1 follows the inside part of the outer ring.
    Beads further in: outlines shrunk by 1.5, 2.5 ... line widths. Returns a list of
    (ring LineString, same_ring_as_outer: bool) per bead."""
    from shapely.geometry import Polygon, Point, LineString
    out = []
    g0 = None
    for k in range(beads):
        depth = 0.5 if k < 2 else (k - 0.5)
        try:
            g = poly.buffer(-depth * lw)
        except Exception:
            g = None
        if g is None or g.is_empty:
            out.append((None, False))
            continue
        polys = list(getattr(g, "geoms", [g]))
        main = _main_polygon(polys) or max(polys, key=lambda q: q.area)
        if k == 0:
            g0 = main
            out.append((LineString(main.exterior.coords), False))
        elif k == 1:
            inner = None
            for ring in main.interiors:
                if Polygon(ring).contains(Point(0, 0)):
                    inner = LineString(ring.coords)
                    break
            if inner is not None:
                out.append((inner, False))
            else:
                out.append((LineString(main.exterior.coords), True))
        else:
            out.append((LineString(main.exterior.coords), False))
    return out


def _ray_hits(line, cx, cy, th):
    """Radii where the ray from (cx, cy) at angle th crosses `line` (sorted)."""
    from shapely.geometry import LineString
    if line is None:
        return []
    ray = LineString([(cx, cy), (cx + FAR * math.cos(th), cy + FAR * math.sin(th))])
    inter = line.intersection(ray)
    pts = []
    for g in getattr(inter, "geoms", [inter]):
        if g.is_empty:
            continue
        if g.geom_type == "Point":
            pts.append(math.hypot(g.x - cx, g.y - cy))
        elif g.geom_type == "LineString":
            xs, ys = g.xy
            pts.extend(math.hypot(x - cx, y - cy) for x, y in zip(xs, ys))
    return sorted(pts)


# ---------------------------------------------------------------- the fit
def build(mesh_path: str, settings: SliceSettings, tilt_max_deg: float = 30.0, progress=None,
          cone_angle_deg: float = 15.0, min_two_arm_radius: float = 0.0):
    """min_two_arm_radius: below this wall radius the two tools cannot face each other across
    the part, so one arm prints both beads (alternating laps)."""
    mesh = _load_mesh(mesh_path)
    lh, lw = settings.layer_height, settings.line_width
    zmin, zmax = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    n_layers = max(1, int(math.floor((zmax - zmin) / lh + 1e-6)))
    thetas = np.linspace(0.0, 2 * math.pi, N_THETA, endpoint=False)
    rep = SpiralReport()

    # 1. sections; for the main wall (polygon round the axis) the bead lines and their ray
    #    hits per angle, so a bead can follow its surface continuously
    beads_wanted = max(1, settings.wall_count)
    sections, mains, hits = [], [], []
    trace_rings = []                  # (z, [outer bead ring coords, inner bead ring coords]) per layer
    for i in range(n_layers):
        if progress and i % 5 == 0:
            progress(i, n_layers)
        z = zmin + lh * (i + 0.5)
        polys = _section_polygons(mesh, z)
        sections.append(polys)
        P = _main_polygon(polys)
        mains.append(P)
        if P is None:
            hits.append(None)
            continue
        lines = _bead_lines(P, lw, beads_wanted)
        hits.append([([_ray_hits(lines[k][0], 0.0, 0.0, th) for th in thetas], lines[k][1])
                     for k in range(beads_wanted)])
        trace_rings.append((z, [list(ln[0].coords) if ln[0] is not None else None for ln in lines[:2]]))

    layers = [Layer(index=i, z=zmin + lh * (i + 0.5), solid=False) for i in range(n_layers)]
    bottom = settings.bottom_layers
    top = settings.top_layers

    # 2. flat skins where the spiral cannot go: bottom / top layers
    for i in list(range(min(bottom, n_layers))) + list(range(max(0, n_layers - top), n_layers)):
        L = layers[i]
        L.solid = True
        for poly in sections[i]:
            if poly.is_empty or poly.area <= 0:
                continue
            wall_paths, regions = _walls_for_polygon(poly, settings)
            L.paths.extend(wall_paths)
            for reg in regions:
                L.paths.extend(_infill_for_region(reg, i, True, settings))
        rep.planar_layers.append(i)

    # 3. the main wall as spirals between the skins
    i0, i1 = min(bottom, n_layers), max(0, n_layers - top)
    JUMP = 3.0 * lw                 # a bigger radial jump than this between samples = a different surface

    def pick(cands, prev, bead, shared=False):
        """Which of the ray hits the bead follows: nearest the previous radius (the ring is
        continuous, so any jump along it is real geometry); on a fresh start the outermost
        for the outer bead, innermost for the inner one. When the inner bead has to share
        the outer ring (C-shaped layer), it takes the inner hits with a jump guard, so it
        does not wander onto the outside."""
        if not cands:
            return None
        if shared:
            if len(cands) < 2:
                return None
            cands = cands[:-1]
        if prev is None:
            return cands[-1] if bead == 0 else cands[0]
        r = min(cands, key=lambda c: abs(c - prev))
        if shared and abs(r - prev) > JUMP:
            return None
        return r

    def radius(i, frac, j, bead, prev):
        """Bead radius at angle j between layer i (frac=0) and i+1 (frac=1); None in a gap."""
        i2 = min(i + 1, n_layers - 1)
        if hits[i] is None or hits[i2] is None:
            return None, None
        ha, sa = hits[i][bead]
        hb, sb = hits[i2][bead]
        ra = pick(ha[j], prev, bead, sa)
        rb = pick(hb[j], ra if ra is not None else prev, bead, sb)
        if ra is None or rb is None:
            return None, None
        r = ra + (rb - ra) * frac
        drdz = (rb - ra) / lh if i2 != i else 0.0
        return r, drdz

    if i1 > i0 and any(h is not None for h in hits[i0:i1]):
        # typical wall radius: the outer bead's median hit
        rr = [h[0][0][j][-1] for h in hits[i0:i1] if h is not None for j in range(0, N_THETA, 30) if h[0][0][j]]
        mean_r = float(np.median(rr)) if rr else 0.0
        two_arms = mean_r >= min_two_arm_radius
        if not two_arms:
            rep.notes.append(f"Wall radius {mean_r:.0f} mm is too small for two tools to face each other "
                             f"(needs {min_two_arm_radius:.0f} mm): the right arm prints both beads.")
        z_start = layers[i0].z
        z_end = layers[i1 - 1].z
        height = z_end - z_start
        if beads_wanted == 1:
            # interleaved: both arms pitch 2lh, same height at any moment, 180° apart
            pitch, offsets = 2 * lh, [(0, 0.0, 0.0), (1, math.pi, 0.0)]       # (arm, angle offset, z offset)
            bead_of = {0: 0, 1: 0}
        else:
            # Conical build, axis outward: the left arm runs the INNER bead ahead (higher) by
            # half a layer (bricked) plus the cone lead lw·tan(a), the right arm lays the
            # outer bead on its shoulder. Pitch lh each, 180° apart.
            lead = 0.5 * lh + lw * math.tan(math.radians(cone_angle_deg))
            pitch, offsets = lh, [(0, 0.0, 0.0), (1, math.pi, lead)]
            bead_of = {0: 0, 1: 1}
        turns = height / pitch
        n_u = int(math.ceil(turns * N_THETA))
        runs = {0: [], 1: []}                     # per arm: list of (kind, points)
        cur = {0: None, 1: None}
        seg_start = {0: None, 1: None}
        prev_r = {0: None, 1: None}
        for s in range(n_u + 1):
            u = s / N_THETA                        # turns
            for arm, ang_off, z_off in offsets:
                th = 2 * math.pi * u + ang_off
                z = z_start + pitch * u + z_off
                if z > z_end + 1e-9:
                    r = None
                else:
                    fi = (z - layers[0].z) / lh
                    i = int(max(0, min(n_layers - 2, math.floor(fi))))
                    frac = max(0.0, min(1.0, fi - i))
                    j = int(round((th % (2 * math.pi)) / (2 * math.pi) * N_THETA)) % N_THETA
                    r, drdz = radius(i, frac, j, bead_of[arm], prev_r[arm])
                prev_r[arm] = r
                kind = WALL_OUTER if r is not None else TRAVEL
                if r is None:
                    # gap: hover over the last radius, lifted, no extrusion
                    last = runs[arm][-1][1][-1] if runs[arm] and runs[arm][-1][1] else None
                    rr = math.hypot(last[0], last[1]) if last else 0.0
                    pt = (rr * math.cos(th), rr * math.sin(th), min(z, z_end) + 2.0, lw, lh, 0.0)
                else:
                    # lean with the wall slope, plus the cone angle outward for the outer bead
                    lean = math.degrees(math.atan(drdz)) + (cone_angle_deg if bead_of[arm] == 0 and beads_wanted > 1 else 0.0)
                    tilt = max(-tilt_max_deg, min(tilt_max_deg, lean))
                    pt = (r * math.cos(th), r * math.sin(th), z, lw, lh, tilt)
                if cur[arm] != kind:
                    if cur[arm] == WALL_OUTER and seg_start[arm] is not None:
                        rep.segments.append(Segment(arm, bead_of[arm], seg_start[arm][0], runs[arm][-1][1][-1][2],
                                                    (u - seg_start[arm][1]), "left the wall" if r is None else "top"))
                    runs[arm].append((kind, []))
                    cur[arm] = kind
                    if kind == WALL_OUTER:
                        seg_start[arm] = (z, u)
                runs[arm][-1][1].append(pt)
        for arm in (0, 1):
            if cur[arm] == WALL_OUTER and seg_start[arm] is not None and runs[arm][-1][1]:
                rep.segments.append(Segment(arm, bead_of[arm], seg_start[arm][0], runs[arm][-1][1][-1][2],
                                            (n_u / N_THETA - seg_start[arm][1]), "top"))
        # hand the runs to one layer (the planner streams by index, not by layer); keep
        # the arms in lock-step: run lists already have equal point counts per u
        host = layers[i0]
        if two_arms:
            for arm in (0, 1):
                for kind, pts in runs[arm]:
                    if len(pts) < 2:
                        continue
                    p = Path(kind, pts, closed=False)
                    p.arm = arm
                    host.paths.append(p)
        else:
            # one arm: the two beads lap by lap (outer lap, then inner lap), right arm
            lap = N_THETA
            flat = {a: [pt for kind, pts in runs[a] for pt in pts if kind == WALL_OUTER] for a in (0, 1)}
            for a in (0, 1):
                for sg in rep.segments:
                    sg.arm = 0
            k = 0
            while k * lap < max(len(flat[0]), len(flat[1])):
                for a in (0, 1):
                    pts = flat[a][k * lap:(k + 1) * lap + 1]
                    if len(pts) > 1:
                        p = Path(WALL_OUTER, pts, closed=False)
                        p.arm = 0
                        host.paths.append(p)
                k += 1
        # further beads (walls thicker than 2 beads): right arm, pitch lh, after the pair
        for bead in range(2, beads_wanted):
            pts = []
            pr = None
            for s in range(int(math.ceil(height / lh * N_THETA)) + 1):
                u = s / N_THETA
                th = 2 * math.pi * u
                z = z_start + lh * u
                fi = (z - layers[0].z) / lh
                i = int(max(0, min(n_layers - 2, math.floor(fi))))
                r, drdz = radius(i, max(0.0, min(1.0, fi - i)), int(round(u * N_THETA)) % N_THETA, bead, pr)
                pr = r
                if r is None:
                    if len(pts) > 1:
                        p = Path(WALL_INNER, pts, closed=False); p.arm = 0; host.paths.append(p)
                    pts = []
                    continue
                pts.append((r * math.cos(th), r * math.sin(th), z, lw, lh, 0.0))
            if len(pts) > 1:
                p = Path(WALL_INNER, pts, closed=False); p.arm = 0; host.paths.append(p)
    else:
        rep.notes.append("No wall round the axis between the skins: nothing to spiral.")

    # 4. everything else that is a closed ring: features off the axis and the bores of
    #    posts. Each is walked round by arc length as a one-lap helix per layer (right arm,
    #    disc facing it), so they are spirals too, just local ones.
    from shapely.geometry import Polygon as _Poly, Point as _Pt, LineString as _LS
    for i in range(i0, i1):
        main = mains[i]
        rings = []
        for q in sections[i]:
            if q.is_empty or q.area <= 0:
                continue
            try:
                g = q.buffer(-0.5 * lw)
            except Exception:
                continue
            for gq in getattr(g, "geoms", [g]):
                if gq.is_empty:
                    continue
                if q is not main:
                    rings.append(gq.exterior)
                for ring in gq.interiors:
                    if not _Poly(ring).contains(_Pt(0, 0)):
                        rings.append(ring)
        for ring in rings:
            L = _LS(ring.coords)
            length = L.length
            if length < 3 * lw:
                continue
            n = max(8, int(length / 1.0))
            pts = []
            for k in range(n + 1):
                sd = length * k / n
                pnt = L.interpolate(sd)
                pts.append((pnt.x, pnt.y, layers[i].z - lh / 2 + lh * k / n, lw, lh, 0.0))
            p = Path(WALL_OUTER, pts, closed=False)
            p.arm = 0
            layers[i].paths.append(p)
            rep.local_features += 1

    res = SliceResult(layers=layers, settings=settings, bounds=mesh.bounds)
    res.failures = []
    res.thin = 0
    res.narrow = []
    res.thin_filled = []
    res.narrow_worst_polys = None
    from slicer import _RegionFeatures
    res.region_features = _RegionFeatures([], [])
    res.spiral_report = rep
    res.spiral_trace = {"rings": trace_rings, "n_theta": N_THETA, "lw": lw, "lh": lh}
    res.spiral_layers = len(rep.segments)
    res.section_stats = [{"polys": len(p), "holes": sum(len(q.interiors) for q in p),
                          "area": sum(q.area for q in p), "perimeter": sum(q.length for q in p)} for p in sections]
    return res
