#!/usr/bin/env python3
"""
Lightweight built-in slicer for the Gleadall multi-cell panel.

Parses a 3mf (or any trimesh-loadable mesh), slices it into layers, and
generates per-layer toolpaths grouped into:

    WALL_OUTER   - outermost perimeter loop
    WALL_INNER   - subsequent perimeter loops (inside the outer)
    SKIN         - solid infill on the bottom/top layers
    INFILL       - sparse infill on the interior layers

Coordinates are in the *model's own XY frame* (millimetres). Placement of the
part on the turntable / plate is handled later by the motion planner, not here.

This is intentionally simple and correct for prismatic parts such as the test
cube. Top/bottom "skin" is decided by layer index (first `bottom_layers` and
last `top_layers`), which is exact for prismatic solids; true neighbour-based
skin detection is a later enhancement.
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional
import math

import numpy as np
try:                      # only needed to slice meshes; generators and the planner work without them
    import trimesh
    from shapely.geometry import Polygon, LineString, MultiLineString, GeometryCollection
    from shapely import affinity
    from shapely.ops import unary_union
    _MESH_IMPORT_ERROR = None
except ImportError as _e:  # pragma: no cover
    trimesh = Polygon = LineString = MultiLineString = GeometryCollection = affinity = unary_union = None
    _MESH_IMPORT_ERROR = _e

Point = Tuple[float, float]

# Path type constants
WALL_OUTER = "WALL_OUTER"
WALL_INNER = "WALL_INNER"
TRAVEL = "TRAVEL"          # non-extruding move kept as a path (generators: z-hops, ordered travels)
SKIN = "SKIN"
INFILL = "INFILL"


@dataclass
class Path:
    """A single toolpath. If `closed` the last point connects back to the first.

    Points are (x, y) for sliced layers, or (x, y, z) / (x, y, z, width, height)
    for 3D toolpaths from generators (per point z, line width and layer height).
    An optional `arm` attribute pins the path to one arm when planning for two.
    """
    kind: str
    points: List[Point]
    closed: bool = False

    def length(self) -> float:
        pts = self.points
        if len(pts) < 2:
            return 0.0
        total = 0.0
        for a, b in zip(pts[:-1], pts[1:]):
            total += math.hypot(b[0] - a[0], b[1] - a[1])
        if self.closed:
            a, b = pts[-1], pts[0]
            total += math.hypot(b[0] - a[0], b[1] - a[1])
        return total


@dataclass
class Layer:
    index: int
    z: float           # z height of this layer (mm, model frame)
    solid: bool
    paths: List[Path] = field(default_factory=list)


@dataclass
class SliceSettings:
    layer_height: float = 0.2
    line_width: float = 0.4
    wall_count: int = 2
    infill_density: float = 0.20     # fraction 0..1 for interior layers
    infill_pattern: str = "grid"     # 'lines' or 'grid'
    top_layers: int = 3
    bottom_layers: int = 3
    # Parts narrower than one line: "centreline" prints them as a single line down their
    # middle, as wide as the feature (less plastic per mm), never narrower than
    # thin_min_width (0 = 0.75 x line width); features narrower than thin_skip_below are
    # dropped. "skip" = the old behaviour (not printed).
    thin_mode: str = "centreline"
    thin_min_width: float = 0.0
    thin_skip_below: float = 0.0
    # Layer strategy (see strategies.py):
    #   planar   - flat layers
    #   spiral   - flat layers, but walls that go around the turntable axis climb
    #              continuously (one helix, no layer seam), where layer_style allows
    #   cone_out - conical layers, apex up ("Christmas tree"): outward overhangs
    #   cone_in  - conical layers, apex down: inward overhangs
    # Cones follow Wüthrich et al. 2021 (RotBot): transform the mesh so the cones
    # become planes, slice flat, transform the paths back.
    strategy: str = "planar"
    cone_angle_deg: float = 15.0
    # optional: callable(section_stats) -> per-layer style ("planar"/"spiral"/...),
    # from the print rules' non-planar bands; None = spiral everywhere it can.
    layer_style: object = None


@dataclass
class SliceResult:
    layers: List[Layer]
    settings: SliceSettings
    bounds: np.ndarray               # 2x3 (min/max xyz) of the source mesh

    def summary(self) -> str:
        counts = {WALL_OUTER: 0, WALL_INNER: 0, SKIN: 0, INFILL: 0}
        total_len = 0.0
        for layer in self.layers:
            for p in layer.paths:
                counts[p.kind] = counts.get(p.kind, 0) + 1
                total_len += p.length()
        return (f"{len(self.layers)} layers | "
                f"outer={counts[WALL_OUTER]} inner={counts[WALL_INNER]} "
                f"skin={counts[SKIN]} infill={counts[INFILL]} | "
                f"extrude path length ~= {total_len/1000:.2f} m"
                + (f" | {self.thin} feature cross-section(s) thinner than the {self.settings.line_width} mm line "
                   "were skipped" if getattr(self, "thin", 0) else "")
                + (f" | {len(self.narrow)} layer(s) have parts narrower than the line (not printed)"
                   if getattr(self, "narrow", None) else "")
                + (f" | {len(self.failures)} layer(s) FAILED to slice (first: layer "
                   f"{self.failures[0][0]}, {self.failures[0][1]})" if getattr(self, "failures", None) else ""))


# ----------------------------------------------------------------------------
def _load_mesh(mesh_path: str) -> trimesh.Trimesh:
    mesh = trimesh.load(mesh_path, force="mesh")
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.to_geometry()
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"Could not load a single mesh from {mesh_path!r}")
    return mesh


def _bodies(mesh: trimesh.Trimesh) -> List[trimesh.Trimesh]:
    """Separate solids in the file. Tinkercad and other CAD exports often contain shapes
    that overlap without being merged; each must be filled on its own, then unioned."""
    cached = getattr(mesh, "_cell_bodies", None)
    if cached is None:
        try:
            cached = list(mesh.split(only_watertight=False)) or [mesh]
        except Exception:
            cached = [mesh]
        mesh._cell_bodies = cached
    return cached


def _body_region(body: trimesh.Trimesh, z: float):
    """Filled cross-section of ONE solid: even-odd over its loops, so its own holes stay holes."""
    if not (body.bounds[0][2] <= z <= body.bounds[1][2]):
        return None
    section = body.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if section is None:
        return None
    loops = []
    for d in section.discrete:
        pts = np.asarray(d)[:, :2]
        if len(pts) < 4:
            continue
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if not poly.is_empty and poly.area > 1e-6:
            loops.append(poly)
    if not loops:
        return None
    loops.sort(key=lambda g: -g.area)
    region = loops[0]
    for g in loops[1:]:
        region = region.symmetric_difference(g)
    return region


def _section_polygons(mesh: trimesh.Trimesh, z: float) -> List[Polygon]:
    """Return the filled cross-section polygons (with holes) at height z, in model XY.

    Each separate solid is filled with the even-odd rule (its own holes stay holes), then
    all solids are UNIONED, so overlapping, unmerged shapes print as one part instead of
    the overlap being cut out. Uses shapely only (trimesh's polygons_full needs `rtree`)."""
    regions = [r for r in (_body_region(b, z) for b in _bodies(mesh)) if r is not None and not r.is_empty]
    if not regions:
        return []
    # 0.02 mm simplification: far below what the nozzle can show, and it keeps refined
    # (cone-space) outlines from having thousands of vertices per loop.
    out = []
    for q in _iter_polys(unary_union(regions)):
        q2 = q.simplify(0.02, preserve_topology=True)
        out.append(q2 if (not q2.is_empty and q2.geom_type == "Polygon") else q)
    return out


def _ring_points(ring) -> List[Point]:
    return [(float(x), float(y)) for x, y in ring.coords]


def _iter_polys(geom):
    """Yield shapely Polygons from a Polygon/MultiPolygon/collection result."""
    if geom.is_empty:
        return
    gtype = geom.geom_type
    if gtype == "Polygon":
        yield geom
    elif gtype in ("MultiPolygon", "GeometryCollection"):
        for g in geom.geoms:
            if g.geom_type == "Polygon" and not g.is_empty:
                yield g


def _offset(poly: Polygon, distance: float) -> List[Polygon]:
    """Inward offset (distance>0 shrinks) keeping mitred (square) corners."""
    result = poly.buffer(-distance, join_style=2, mitre_limit=5.0)
    return list(_iter_polys(result))


def _walls_for_polygon(poly: Polygon, settings: SliceSettings) -> Tuple[List[Path], List[Polygon]]:
    """Generate wall loops for one polygon; return (paths, infill_regions)."""
    paths: List[Path] = []
    lw = settings.line_width
    for k in range(settings.wall_count):
        inset = lw * (k + 0.5)
        for wpoly in _offset(poly, inset):
            kind = WALL_OUTER if k == 0 else WALL_INNER
            paths.append(Path(kind, _ring_points(wpoly.exterior), closed=True))
            for hole in wpoly.interiors:
                paths.append(Path(kind, _ring_points(hole), closed=True))
    # Region left for infill sits inside all the walls.
    infill_regions = _offset(poly, lw * settings.wall_count)
    return paths, infill_regions


def _scanline_infill(region: Polygon, spacing: float, angle_deg: float,
                     kind: str) -> List[Path]:
    """Fill `region` with parallel line segments at `angle_deg`, spacing apart."""
    if region.is_empty or spacing <= 0:
        return []
    cx, cy = region.centroid.x, region.centroid.y
    # Rotate region so the fill lines become horizontal, fill, then rotate back.
    rot = affinity.rotate(region, -angle_deg, origin=(cx, cy), use_radians=False)
    minx, miny, maxx, maxy = rot.bounds
    paths: List[Path] = []
    y = miny + spacing * 0.5
    while y <= maxy:
        scan = LineString([(minx - 1.0, y), (maxx + 1.0, y)])
        inter = rot.intersection(scan)
        segments = []
        if inter.is_empty:
            pass
        elif inter.geom_type == "LineString":
            segments = [inter]
        elif inter.geom_type in ("MultiLineString", "GeometryCollection"):
            segments = [g for g in inter.geoms if g.geom_type == "LineString"]
        for seg in segments:
            coords = list(seg.coords)
            if len(coords) >= 2:
                line = LineString(coords)
                line = affinity.rotate(line, angle_deg, origin=(cx, cy), use_radians=False)
                paths.append(Path(kind, [(float(x), float(yy)) for x, yy in line.coords]))
        y += spacing
    return paths


def _infill_for_region(region: Polygon, layer_index: int, solid: bool,
                       settings: SliceSettings) -> List[Path]:
    lw = settings.line_width
    if solid:
        # Solid skin: fully packed lines, alternate 45/135 per layer to bond.
        angle = 45.0 if (layer_index % 2 == 0) else 135.0
        return _scanline_infill(region, lw, angle, SKIN)

    density = max(0.0, min(1.0, settings.infill_density))
    if density <= 0.0:
        return []
    spacing = lw / density
    if settings.infill_pattern == "grid":
        base = 45.0 if (layer_index % 2 == 0) else 135.0
        out = _scanline_infill(region, spacing, base, INFILL)
        out += _scanline_infill(region, spacing, base + 90.0, INFILL)
        return out
    else:  # 'lines'
        angle = 0.0 if (layer_index % 2 == 0) else 90.0
        return _scanline_infill(region, spacing, angle, INFILL)


# ----------------------------------------------------------------------------
def slice_model(mesh_path: str, settings: SliceSettings, progress=None) -> SliceResult:
    """progress(done, total) is called every few layers if given."""
    if _MESH_IMPORT_ERROR is not None:
        raise ImportError(f"Slicing needs trimesh and shapely ({_MESH_IMPORT_ERROR}). "
                          "pip install trimesh shapely")
    mesh = _load_mesh(mesh_path)
    bounds0 = mesh.bounds.copy()
    cone = None
    if settings.strategy in ("cone_out", "cone_in"):
        cone = (math.radians(settings.cone_angle_deg), 1.0 if settings.strategy == "cone_out" else -1.0,
                float(bounds0[0][2]))
        mesh = cone_transform_mesh(mesh, *cone[:2])
    zmin, zmax = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    height = zmax - zmin
    lh = settings.layer_height
    n_layers = max(1, int(math.floor(height / lh + 1e-6)))

    layers: List[Layer] = []
    failures = []
    thin = 0                            # cross-sections too thin for even one line
    all_polys = []                      # per layer cross-sections, for the per-region features
    thin_filled = []                    # layers where narrow parts were printed as centrelines
    section_stats = []                  # per layer: parts, holes, area, perimeter (non-planar bands)
    narrow = []                         # (layer, z, mm² narrower than one line, % of the section)
    worst = (0.0, None)                 # (lost %, polygons) of the layer that loses the most
    for i in range(n_layers):
        if progress is not None and i % 5 == 0:
            progress(i, n_layers)
        z = zmin + lh * (i + 0.5)
        solid = (i < settings.bottom_layers) or (i >= n_layers - settings.top_layers)
        layer = Layer(index=i, z=z, solid=solid)
        try:
            polys = _section_polygons(mesh, z)
        except Exception as e:          # keep going, but never silently: reported below
            polys = []
            failures.append((i, f"{type(e).__name__}: {e}"))
        section_stats.append({"polys": len(polys), "holes": sum(len(q.interiors) for q in polys),
                              "area": sum(q.area for q in polys), "perimeter": sum(q.length for q in polys)})
        lost = narrower_than(polys, settings.line_width)
        total = sum(p_.area for p_ in polys)
        all_polys.append(polys)
        if total > 0 and lost / total > 0.005:
            pct = 100.0 * lost / total
            narrow.append((i, z, lost, pct))
            if pct > worst[0]:
                worst = (pct, polys)
        thin_here = 0
        if settings.thin_mode == "centreline" and lost > 0:
            for poly in polys:
                tp = thin_feature_paths(poly, settings, z)
                thin_here += len(tp)
                layer.paths.extend(tp)
        if thin_here:
            thin_filled.append(i)
        for poly in polys:
            if poly.is_empty or poly.area <= 0:
                continue
            wall_paths, infill_regions = _walls_for_polygon(poly, settings)
            if not wall_paths:
                thin += 1
            layer.paths.extend(wall_paths)
            for region in infill_regions:
                layer.paths.extend(_infill_for_region(region, i, solid, settings))
        layers.append(layer)

    if failures and len(failures) == n_layers:
        raise RuntimeError(f"Every layer failed to slice. First error: {failures[0][1]}")
    if not any(layer.paths for layer in layers):
        raise RuntimeError("The model sliced into layers but produced no toolpaths. It may be too small for "
                           f"the {settings.line_width} mm line width, or not a closed solid.")
    # Strategy: which layers climb as a spiral (from the rules' bands when given), and the
    # conical back-transform. Cones are applied last so spiralled z is bent with them.
    res_spiral = 0
    styles = settings.layer_style(section_stats) if settings.layer_style else None
    if settings.strategy == "spiral" or (styles and any(st in ("spiral", "spiral_brick") for st in styles)):
        res_spiral = spiral_walls(layers, settings, styles if settings.strategy != "spiral" else None)
    if cone is not None:
        cone_back_transform(layers, *cone)
        if not any(layer.paths for layer in layers):
            raise RuntimeError("Nothing left above the bed after the conical transform. Try a smaller cone angle.")
    res = SliceResult(layers=layers, settings=settings, bounds=bounds0)
    res.spiral_layers = res_spiral
    res.failures = failures
    res.thin = thin
    res.narrow = narrow
    res.section_stats = section_stats
    res.thin_filled = thin_filled
    try:
        res.region_features = region_features(all_polys, settings)
    except Exception as e:              # features feed reports and the AI only; never block a slice
        res.region_features = []
        failures.append((-1, f"region features: {type(e).__name__}: {e}"))
    res.narrow_worst_polys = worst[1]
    return res


def thin_feature_paths(poly: Polygon, settings: SliceSettings, z: float) -> List[Path]:
    """Single lines down the middle of the parts of `poly` narrower than one line width.

    Uses the medial axis (centres of the largest circles that fit), found from the
    Voronoi diagram of the densified outline. Each point carries its own width = the
    local feature width (clamped to thin_min_width .. line width), so the planner
    extrudes less plastic where the feature is narrower. Points are (x, y, z, w, h)."""
    from scipy.spatial import Voronoi
    import shapely
    lw = settings.line_width
    w_min = settings.thin_min_width or 0.75 * lw
    w_skip = settings.thin_skip_below or 0.4 * lw
    step = lw / 4.0
    chunks = []
    for ring in [poly.exterior] + list(poly.interiors):
        L = ring.length
        n = max(8, int(L / step))
        sampled = shapely.line_interpolate_point(ring, np.linspace(0.0, L, n, endpoint=False))
        chunks.append(shapely.get_coordinates(sampled))
    pts = np.concatenate(chunks) if chunks else np.zeros((0, 2))
    if len(pts) < 8:
        return []
    try:
        vor = Voronoi(pts)
    except Exception:
        return []
    V = vor.vertices
    inside = shapely.contains_xy(poly, V[:, 0], V[:, 1])
    r = np.full(len(V), np.inf)
    if inside.any():
        r[inside] = shapely.distance(poly.boundary, shapely.points(V[inside]))
    keep = inside & (r < lw / 2.0) & (2 * r >= w_skip)
    adj = {}
    for a, b in vor.ridge_vertices:
        if a < 0 or b < 0 or not (keep[a] and keep[b]):
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    if not adj:
        return []
    # walk the graph into chains (split at junctions and ends)
    seen_edges = set()
    chains = []
    starts = [v for v, nb in adj.items() if len(nb) != 2] or [next(iter(adj))]
    for s0 in starts + list(adj):
        for nb in list(adj[s0]):
            e = (min(s0, nb), max(s0, nb))
            if e in seen_edges:
                continue
            chain = [s0]
            prev, cur = s0, nb
            seen_edges.add(e)
            while True:
                chain.append(cur)
                if len(adj[cur]) != 2 or cur == s0:
                    break
                nxt = [q for q in adj[cur] if q != prev][0]
                e = (min(cur, nxt), max(cur, nxt))
                if e in seen_edges:
                    break
                seen_edges.add(e)
                prev, cur = cur, nxt
            chains.append(chain)
    out = []
    h = settings.layer_height
    for ch in chains:
        xy = V[ch]
        seg = np.hypot(*np.diff(xy, axis=0).T).sum() if len(xy) > 1 else 0.0
        if seg < lw:                      # stubs at corners and junctions
            continue
        closed = len(ch) > 2 and ch[0] == ch[-1]
        if closed:
            xy, ch = xy[:-1], ch[:-1]
        # thin out to ~step spacing
        keep_i = [0]
        acc = 0.0
        for i in range(1, len(xy)):
            acc += float(np.hypot(*(xy[i] - xy[i - 1])))
            if acc >= step or i == len(xy) - 1:
                keep_i.append(i)
                acc = 0.0
        p5 = [(float(xy[i][0]), float(xy[i][1]), float(z),
               float(min(lw, max(w_min, 2.0 * r[ch[i]]))), float(h)) for i in keep_i]
        out.append(Path(WALL_OUTER, p5, closed=closed))
    return out


# ---------------------------------------------------------------- strategies
def cone_transform_mesh(mesh, angle, sign, max_edge: float = 1.5):
    """Mesh -> the space where conical layers are flat (RotBot transform):
    x' = x / cos a, y' = y / cos a, z' = z + sign * r * tan a   (r about the turntable axis).
    The mesh is refined first so straight triangle edges bend correctly."""
    import trimesh as _tm
    v, f = mesh.vertices, mesh.faces
    try:
        v, f = _tm.remesh.subdivide_to_size(v, f, max_edge=max_edge, max_iter=12)
    except Exception:
        pass
    v = np.array(v, dtype=float)
    r = np.hypot(v[:, 0], v[:, 1])
    c, t = math.cos(angle), math.tan(angle)
    out = np.empty_like(v)
    out[:, 0] = v[:, 0] / c
    out[:, 1] = v[:, 1] / c
    out[:, 2] = v[:, 2] + sign * r * t
    return _tm.Trimesh(out, f, process=False)


def _densify_pts(pts, closed, step):
    out = []
    n = len(pts)
    m = n if closed else n - 1
    for i in range(m):
        a, b = pts[i], pts[(i + 1) % n]
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        k = max(1, int(math.ceil(L / step)))
        for j in range(k):
            t = j / k
            out.append(tuple(a[q] + (b[q] - a[q]) * t if q < 3 else a[q] for q in range(len(a))))
    if not closed:
        out.append(tuple(pts[-1]))
    return out


def cone_back_transform(layers, angle, sign, bed_z, step: float = 1.0):
    """Paths sliced in cone space -> real 3D points (x, y, z[, w, h]) on the cones.
    Points that would be under the bed are dropped (paths split there); points just
    above it are lifted onto it."""
    c, t = math.cos(angle), math.tan(angle)
    for layer in layers:
        new_paths = []
        for path in layer.paths:
            pts = [(p[0], p[1], p[2] if len(p) >= 3 else layer.z) + tuple(p[3:]) for p in path.points]
            if len(pts) < 2:
                continue
            pts = _densify_pts(pts, path.closed, step / c)
            run = []
            for p in pts:
                x, y = p[0] * c, p[1] * c
                z = p[2] - sign * math.hypot(x, y) * t
                if z < bed_z - 1e-6:
                    if len(run) > 1:
                        new_paths.append(Path(path.kind, run, closed=False))
                    run = []
                    continue
                run.append((x, y, max(z, bed_z + 0.05)) + tuple(p[3:]))
            if len(run) > 1:
                whole = len(run) == len(pts) and path.closed
                new_paths.append(Path(path.kind, run, closed=whole))
        layer.paths = new_paths


def spiral_walls(layers, settings: SliceSettings, styles=None) -> int:
    """Turn wall loops that go around the turntable axis into one continuous climb:
    over the loop, z rises from (layer z - h/2) to (layer z + h/2), starting at the
    +X side and running anticlockwise, so each layer's loop ends where the next one
    starts (no layer-change seam). Other paths stay flat. Layers whose style is not
    'spiral' are left flat. Returns how many layers were spiralled."""
    from shapely.geometry import Point as _P, Polygon as _Poly
    h = settings.layer_height
    n_done = 0
    for li, layer in enumerate(layers):
        if styles is not None and (li >= len(styles) or styles[li] not in ("spiral", "spiral_brick")):
            continue
        changed = False
        for path in layer.paths:
            if path.kind not in (WALL_OUTER, WALL_INNER) or not path.closed or len(path.points) < 3:
                continue
            xy = [(p[0], p[1]) for p in path.points]
            try:
                if not _Poly(xy).contains(_P(0.0, 0.0)):
                    continue
            except Exception:
                continue
            area2 = sum(xy[i][0] * xy[(i + 1) % len(xy)][1] - xy[(i + 1) % len(xy)][0] * xy[i][1]
                        for i in range(len(xy)))
            pts = list(path.points) if area2 > 0 else list(reversed(path.points))
            # start at the point closest to the +X ray
            k0 = min(range(len(pts)), key=lambda i: abs(math.atan2(pts[i][1], pts[i][0])))
            pts = pts[k0:] + pts[:k0]
            pts = pts + [pts[0]]
            seg = [0.0] + [math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
                           for i in range(1, len(pts))]
            total = sum(seg) or 1.0
            acc, out = 0.0, []
            base = layer.z
            for p, d in zip(pts, seg):
                acc += d
                z = base - h / 2 + h * acc / total
                out.append((p[0], p[1], z) + tuple(p[3:]) if len(p) >= 5 else (p[0], p[1], z))
            path.points = out
            path.closed = False
            changed = True
        n_done += changed
    return n_done


def region_features(all_polys, settings: SliceSettings):
    """Measurements of every connected region of every layer, for choosing a tactic per
    region (print rules) and for the AI to learn from. Model frame; the turntable axis is
    at the model origin (as placed for printing). Returns [layer][region] dicts."""
    from shapely.geometry import Point as _P
    from shapely.ops import unary_union as _uu
    lh, lw = settings.layer_height, settings.line_width
    unions = [(_uu(ps) if ps else None) for ps in all_polys]
    out = []
    origin = _P(0.0, 0.0)
    for i, polys in enumerate(all_polys):
        below = unions[i - 1] if i > 0 else None
        above = unions[i + 1] if i + 1 < len(unions) else None
        feats = []
        for q in polys:
            if q.is_empty or q.area <= 0:
                continue
            c = q.centroid
            ext = list(q.exterior.coords)
            radii = sorted(math.hypot(x, y) for x, y in ext)
            mean_r = sum(radii) / len(radii)
            r10, r90 = radii[len(radii) // 10], radii[(len(radii) * 9) // 10]
            overhang = 0.0
            if below is not None and not below.is_empty:
                diff = q.simplify(0.1).difference(below.simplify(0.1))
                if not diff.is_empty and diff.area > 0.5 * lw * lw:
                    pts = [_P(x, y) for g in getattr(diff, "geoms", [diff]) if g.geom_type == "Polygon"
                           for x, y in g.exterior.coords[::max(1, len(g.exterior.coords) // 60)]]
                    if pts:
                        overhang = max(below.distance(pp) for pp in pts)
            # grows outward = the new material lies further from the axis than the layer below
            grows_out = True
            if overhang > 0 and below is not None and not below.is_empty:
                try:
                    dc = diff.centroid
                    bc = below.centroid
                    grows_out = math.hypot(dc.x, dc.y) >= math.hypot(bc.x, bc.y) - 1e-6
                except Exception:
                    pass
            top = 0.0
            if above is None or above.is_empty:
                top = 1.0
            else:
                top = max(0.0, q.difference(above).area / q.area)
            feats.append({
                "layer": i, "area": round(q.area, 2), "holes": len(q.interiors),
                "perimeter": round(q.length, 2),
                "wall_mm": round(2.0 * q.area / q.length, 3) if q.length > 0 else 0.0,
                "centroid": (round(c.x, 2), round(c.y, 2)),
                "encloses_axis": bool(Polygon(q.exterior).contains(origin)),
                "radial_variation": round((r90 - r10) / mean_r, 3) if mean_r > 0 else 1.0,
                "narrow_pct": round(100.0 * narrower_than([q], lw) / q.area, 2),
                "overhang_mm": round(overhang, 3),
                "overhang_deg": round(math.degrees(math.atan2(overhang, lh)), 1),
                "top_fraction": round(top, 3),
                "grows_outward": bool(grows_out),
            })
        # mirror partners: same area, centroid mirrored through the axis
        for a in feats:
            a["has_mirror_partner"] = any(
                b is not a and abs(b["area"] - a["area"]) <= 0.05 * a["area"] and
                math.hypot(a["centroid"][0] + b["centroid"][0], a["centroid"][1] + b["centroid"][1]) <= 1.0
                for b in feats) and math.hypot(*a["centroid"]) > 1.0
        out.append(feats)
    return out


def narrower_than(polys, width: float) -> float:
    """Area (mm²) of these cross-sections that is narrower than `width`, i.e. that a
    line that wide cannot reach (morphological opening)."""
    lost = 0.0
    for poly in polys:
        if poly.is_empty or poly.area <= 0:
            continue
        opened = poly.buffer(-width / 2.0).buffer(width / 2.0)
        lost += max(0.0, poly.area - opened.area)
    return lost


def suggest_line_width(polys, current: float, minimum: float, step: float = 0.05,
                       tolerance_pct: float = 0.5) -> Optional[float]:
    """Widest line (≤ current, ≥ minimum) that keeps all but tolerance_pct of these
    cross-sections printable. None if even the minimum loses more than that."""
    total = sum(p.area for p in polys if not p.is_empty)
    if total <= 0:
        return None
    w = round(current - step, 3)
    while w >= minimum - 1e-9:
        if 100.0 * narrower_than(polys, w) / total <= tolerance_pct:
            return round(w, 2)
        w = round(w - step, 3)
    return None


# ----------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "cube40.3mf"
    s = SliceSettings(layer_height=2.0, line_width=0.4, wall_count=3,
                      infill_density=0.20, infill_pattern="grid",
                      top_layers=1, bottom_layers=1)
    res = slice_model(path, s)
    print(res.summary())
    mid = res.layers[len(res.layers) // 2]
    print(f"mid layer {mid.index} z={mid.z:.2f} solid={mid.solid} "
          f"paths={len(mid.paths)}")
