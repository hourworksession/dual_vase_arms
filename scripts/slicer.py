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
    _MESH_IMPORT_ERROR = None
except ImportError as _e:  # pragma: no cover
    trimesh = Polygon = LineString = MultiLineString = GeometryCollection = affinity = None
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


def _section_polygons(mesh: trimesh.Trimesh, z: float) -> List[Polygon]:
    """Return the filled cross-section polygons (with holes) at height z, in model XY.

    Built with shapely from the section's closed loops (even-odd rule: XOR of all loops),
    so a part with holes or several islands needs no extra packages (trimesh's own
    polygons_full needs `rtree`), and the coordinates are the model's own XY."""
    section = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
    if section is None:
        return []
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
        return []
    loops.sort(key=lambda g: -g.area)
    region = loops[0]
    for g in loops[1:]:
        region = region.symmetric_difference(g)
    return list(_iter_polys(region))


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
    zmin, zmax = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    height = zmax - zmin
    lh = settings.layer_height
    n_layers = max(1, int(math.floor(height / lh + 1e-6)))

    layers: List[Layer] = []
    failures = []
    thin = 0                            # cross-sections too thin for even one line
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
    res = SliceResult(layers=layers, settings=settings, bounds=mesh.bounds)
    res.failures = failures
    res.thin = thin
    return res


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
