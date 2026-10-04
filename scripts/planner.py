#!/usr/bin/env python3
"""
Motion planner for the Gleadall multi-cell panel.

Turns a `SliceResult` (from slicer.py) into a `MotionProgram`: an ordered list
of timed steps giving, per step, the turntable target angle and each enabled
arm's Cartesian target (x, y, z, roll, pitch, yaw) plus an extrusion amount.

Frames
------
  * plate frame  - the part sits on the turntable. Origin = turntable rotation
                   axis. A model point (mx, my) maps to plate (mx+ox, my+oy)
                   where (ox, oy) = part_offset.
  * arm/world    - the frame an arm moves in. The turntable axis is at
                   (cx, cy, cz) in this frame (calibration.yaml right arm =
                   (575.6, 3.5), z=152.0). Rotating the turntable by phi rotates
                   plate points about the axis:  world = center + Rz(phi) . plate

Turntable strategy (per path kind)
----------------------------------
For WALLS the turntable does the angular work; for INFILL / SKIN it HOLDS and
the arm traces the fill (unless turntable_for_infill=True).

WHY WE SUBDIVIDE (important)
----------------------------
The slicer only emits polygon *vertices*. For a square, all four corners are at
the SAME radius, so parking the arm there and spinning the turntable draws a
CIRCLE, not a square. To draw a straight side in polar, the arm radius must dip
to the half-side distance at the side midpoint and swell to the half-diagonal at
the corners. That only happens if the side is broken into short sub-segments.
So turntable-coordinated paths are DENSIFIED to max_segment_length before
planning: each sub-point gets its own (radius, angle), the arm moves radially in
and out, and the deposited line is straight. Paths the turntable does not
coordinate (infill when held, or everything in Cartesian mode) are left alone -
straight world moves are already straight there.

Extrusion
---------
Filament per move is volumetric: e = seg * line_width * layer_height / area,
scaled by flow_multiplier (and first_layer_flow on the first layer(s)). The
executor groups consecutive extruding moves into ONE continuous extrude at
feed = total_e / path_time (see extrusion_runs).
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict
import math

from slicer import SliceResult, Layer, Path, WALL_OUTER, WALL_INNER, SKIN, INFILL, TRAVEL

_KIND_ORDER = {WALL_OUTER: 0, WALL_INNER: 1, SKIN: 2, INFILL: 3}
_WALLS = (WALL_OUTER, WALL_INNER)


@dataclass
class PlannerConfig:
    num_arms: int = 1
    use_turntable: bool = True
    turntable_for_infill: bool = False

    center: Tuple[float, float, float] = (575.6, 3.5, 152.0)
    z_base: float = 152.0
    part_offset: Tuple[float, float] = (0.0, 0.0)

    arm_azimuths: Tuple[float, ...] = (math.radians(-45.0),)
    orientation: Tuple[float, float, float] = (180.0, 45.0, 20.0)

    max_arm_speed: float = 100.0
    max_tt_speed: float = 1.5           # rad/s
    print_speed: float = 30.0
    travel_speed: float = 150.0

    line_width: float = 0.4
    layer_height: float = 0.2
    filament_diameter: float = 1.75
    flow_multiplier: float = 1.0
    first_layer_flow: float = 1.2
    first_layer_count: int = 1
    # 3D toolpaths (generators): first-layer flow applies to points at or below this
    # model z instead of by layer index. None keeps the layer-index rule.
    first_layer_z: Optional[float] = None
    extruder_tool: int = 0

    # path conditioning
    min_segment_length: float = 0.0     # coalesce points closer than this (0=off)
    max_segment_length: float = 1.0     # subdivide coordinated paths to <= this (0=off)
    start_phi: float = 0.0
    # Print rules (rules.RuleSet). When set, whether the bed turns is decided per PATH by
    # the rules (round paths around the axis turn; squares, off-centre features and fill
    # are drawn on a held bed, turned first to face the arm). None = the old per-kind rule.
    rules: Optional[object] = None
    # Per-arm frames: [(centre_x, centre_y, centre_z, base_yaw_deg), ...] for arm 0, 1, ...
    # The planner works in arm 0's frame (center / z_base above). Targets for other arms are
    # converted into THEIR frame: cell = Rz(yaw0)(p - C0); arm_i = C_i + Rz(-yaw_i) cell.
    # None = every arm gets arm-0-frame numbers (only right for a single arm).
    arm_frames: Optional[List[Tuple[float, float, float, float]]] = None
    # Each arm's own nozzle orientation (roll, pitch, yaw in ITS frame). When given, the
    # planner writes per-arm orientations into the targets, leaning the nozzle by each
    # point's tilt (6-value points) about the path tangent; the streamer sends them as is.
    arm_orientations: Optional[List[Tuple[float, float, float]]] = None
    retreat_clearance: float = 40.0      # mm outside the part's largest radius for an idle arm
    retreat_lift: float = 25.0           # mm above the current layer for an idle arm
    cross_angle: float = math.radians(100.0)   # further than this from its azimuth = in the other arm's half
    min_separation: float = 60.0         # mm between nozzles, never closer (the extruder bodies are ~45 mm wide)
    # Tool outlines per arm, in the PLANNER frame relative to each nozzle (Nx2 mm). Each arm's
    # tool keeps a fixed heading (the commanded orientation never changes), so these are fixed
    # shapes; the collision guard then tests real body clearance instead of nozzle distance.
    tool_footprints: Optional[List[object]] = None
    tool_margin: float = 20.0            # mm kept between the tool bodies
    face_limit: float = math.radians(60.0)   # held features further round than this are turned to the arm

    def filament_area(self) -> float:
        return math.pi * (self.filament_diameter / 2.0) ** 2


@dataclass
class ArmTarget:
    x: float
    y: float
    z: float
    roll: float
    pitch: float
    yaw: float
    extrude: bool
    e: float = 0.0


@dataclass
class MotionStep:
    dt: float
    tt_angle_deg: float
    arms: List[Optional[ArmTarget]]
    layer: int = -1
    kind: str = ""


@dataclass
class MotionProgram:
    steps: List[MotionStep]
    config: PlannerConfig
    decisions: Optional[object] = None      # rules.DecisionLog when planned with print rules

    def total_time(self) -> float:
        return sum(s.dt for s in self.steps)


# ----------------------------------------------------------------------------
def _wrap_to_pi(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def path_class(path: Path) -> int:
    """Print order within a layer: 0 = wall round the turntable axis (the bed turns, the
    arm hardly moves), 1 = closed round feature off the axis (posts, bores: one smooth
    loop each), 2 = other closed loops (squares, straight-sided walls), 3 = skins and
    infill (straight lines on a held bed). Also stored on the path as `_class`."""
    c = getattr(path, "_class", None)
    if c is not None:
        return c
    if path.kind in (SKIN, INFILL):
        c = 3
    elif getattr(path, "_encloses", False) or getattr(path, "_turn", False):
        c = 0
    elif path.closed and len(path.points) >= 8:
        pts = path.points
        cx = sum(q[0] for q in pts) / len(pts)
        cy = sum(q[1] for q in pts) / len(pts)
        rr = sorted(math.hypot(q[0] - cx, q[1] - cy) for q in pts)
        lo, hi = rr[len(rr) // 10], rr[(9 * len(rr)) // 10]
        mean = sum(rr) / len(rr)
        c = 1 if mean > 1e-6 and (hi - lo) / mean < 0.15 else 2
    else:
        c = 2
    path._class = c
    return c


_CLASS_NAMES = ["axis_wall", "circles", "straight", "fill"]


def order_toolpaths(slc: SliceResult, rules=None) -> List[Tuple[int, Path]]:
    """Per layer: the wall round the axis first, then the circles, then the straight
    lines (rule print.order); within a class outer walls before inner, skins before infill."""
    order = list(rules.get("print.order", _CLASS_NAMES)) if rules is not None else _CLASS_NAMES
    rank = {name: k for k, name in enumerate(order)}
    ordered: List[Tuple[int, Path]] = []
    for layer in slc.layers:
        paths = sorted(layer.paths, key=lambda p: (rank.get(_CLASS_NAMES[path_class(p)], 9),
                                                   _KIND_ORDER.get(p.kind, 9)))
        for p in paths:
            ordered.append((layer.index, p))
    return ordered


def _seg_len(a, b) -> float:
    """XY length for 2D points (sliced layers); XYZ length for 3D points."""
    if len(a) == 2:
        return math.hypot(b[0] - a[0], b[1] - a[1])
    return math.dist(a[:3], b[:3])


def _simplify(points: List[tuple], min_seg: float) -> List[tuple]:
    if min_seg <= 0 or len(points) <= 2:
        return points
    out = [points[0]]
    for p in points[1:-1]:
        if _seg_len(out[-1], p) >= min_seg:
            out.append(p)
    out.append(points[-1])
    return out


def _densify(points: List[tuple], max_seg: float) -> List[tuple]:
    """Insert points so no segment is longer than max_seg (straight-line interp).
    Extra coordinates (z, width, height) are interpolated too."""
    if max_seg <= 0 or len(points) < 2:
        return points
    out = [points[0]]
    for a, b in zip(points[:-1], points[1:]):
        d = _seg_len(a, b)
        if d > max_seg:
            nsub = int(math.ceil(d / max_seg))
            for k in range(1, nsub):
                t = k / nsub
                if len(a) == 2:
                    out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                else:
                    out.append(tuple(a[i] + (b[i] - a[i]) * t for i in range(len(a))))
        out.append(b)
    return out


def _prep_points(path: Path, min_seg: float) -> List[Tuple[float, float]]:
    pts = list(path.points)
    if path.closed and len(pts) >= 2 and pts[0] != pts[-1]:
        pts.append(pts[0])
    return _simplify(pts, min_seg)


def _world(cfg: PlannerConfig, plate_xy: Tuple[float, float], phi: float,
           z_layer: float) -> Tuple[float, float, float]:
    """Plate point -> arm 0's frame (the planner frame)."""
    px, py = plate_xy
    c, s = math.cos(phi), math.sin(phi)
    wx = cfg.center[0] + (px * c - py * s)
    wy = cfg.center[1] + (px * s + py * c)
    wz = cfg.z_base + z_layer
    return (wx, wy, wz)


_RPY_CACHE: Dict[tuple, Tuple[float, float, float]] = {}


def arm_rpy(cfg: PlannerConfig, i: int, world, tang, tilt_deg: float) -> Tuple[float, float, float]:
    """Roll/pitch/yaw for arm i at this point: its base orientation, leaned by tilt_deg about
    the bead tangent `tang` (planner frame) so the nozzle tip points down-and-inward
    (positive tilt = tool leans outward, laying the bead on the shoulder of the one
    inside/below it)."""
    base = tuple(cfg.arm_orientations[i])
    if abs(tilt_deg) < 0.05 or tang is None or (abs(tang[0]) + abs(tang[1])) < 1e-9:
        return base
    key = (i, round(tilt_deg, 1), round(math.atan2(tang[1], tang[0]), 2),
           round(math.atan2(world[1] - cfg.center[1], world[0] - cfg.center[0]), 2))
    hit = _RPY_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        import numpy as np
        from cell_studio.arm_model import rpy_matrix, matrix_to_pose
    except Exception:
        return base
    # directions in the planner frame, then into arm i's frame (a yaw)
    t = np.array([tang[0], tang[1], 0.0])
    t /= np.linalg.norm(t)
    rad = np.array([world[0] - cfg.center[0], world[1] - cfg.center[1], 0.0])
    rad = rad / (np.linalg.norm(rad) or 1.0)
    if cfg.arm_frames and i < len(cfg.arm_frames):
        a = math.radians(cfg.arm_frames[0][3] - cfg.arm_frames[i][3])
        c, s_ = math.cos(a), math.sin(a)
        R = np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1.0]])
        t, rad = R @ t, R @ rad
    R0 = rpy_matrix(*[math.radians(v) for v in base])
    down = np.array([0.0, 0.0, -1.0])
    ang = math.radians(tilt_deg)
    best = None
    for sign in (1.0, -1.0):
        k = t * sign
        K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
        Rt = np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * (K @ K)
        d = Rt @ down                      # new nozzle direction
        score = float(d @ (-rad))          # should gain an inward component
        if best is None or score > best[0]:
            best = (score, Rt)
    out = tuple(matrix_to_pose(np.block([[best[1] @ R0, np.zeros((3, 1))], [np.zeros((1, 3)), 1.0]]))[3:])
    out = tuple(round(v, 3) for v in out)
    _RPY_CACHE[key] = out
    return out


def to_arm_frame(cfg: PlannerConfig, arm_index: int, p: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """Planner-frame (arm 0) point -> arm `arm_index`'s own frame, using cfg.arm_frames."""
    if arm_index == 0 or not cfg.arm_frames or arm_index >= len(cfg.arm_frames):
        return p
    c0x, c0y, c0z, yaw0 = cfg.arm_frames[0]
    cix, ciy, ciz, yawi = cfg.arm_frames[arm_index]
    dx, dy = p[0] - c0x, p[1] - c0y
    a = math.radians(yaw0 - yawi)
    c, s = math.cos(a), math.sin(a)
    return (cix + c * dx - s * dy, ciy + s * dx + c * dy, ciz + (p[2] - c0z))


def from_arm_frame(cfg: PlannerConfig, arm_index: int, p: Tuple[float, float, float]) -> Tuple[float, float, float]:
    """Inverse of to_arm_frame: arm i's own frame -> planner (arm 0) frame."""
    if arm_index == 0 or not cfg.arm_frames or arm_index >= len(cfg.arm_frames):
        return p
    c0x, c0y, c0z, yaw0 = cfg.arm_frames[0]
    cix, ciy, ciz, yawi = cfg.arm_frames[arm_index]
    dx, dy = p[0] - cix, p[1] - ciy
    a = math.radians(yawi - yaw0)
    c, s = math.cos(a), math.sin(a)
    return (c0x + c * dx - s * dy, c0y + s * dx + c * dy, c0z + (p[2] - ciz))


def _assign_paths(ordered: List[Tuple[int, Path]], num_arms: int) -> List[List[Tuple[int, Path]]]:
    """Round robin over arms. A path with an `arm` attribute goes to that arm; travel
    paths go with the extruding path that follows them."""
    lanes: List[List[Tuple[int, Path]]] = [[] for _ in range(num_arms)]
    pending: List[Tuple[int, Path]] = []
    rr = 0
    last = 0
    pinned_any = any(getattr(p, "arm", None) is not None for _, p in ordered)
    for item in ordered:
        path = item[1]
        if path.kind == TRAVEL:
            pending.append(item)
            continue
        arm = getattr(path, "arm", None)
        if arm is None:
            # with pinned paths in the mix (spiral fit), unpinned work (skins, local helices)
            # stays with the right arm; the left is only ever sent where it was planned
            lane = 0 if pinned_any else rr % num_arms
            rr += 1
        else:
            lane = int(arm) % num_arms
        lanes[lane].extend(pending)
        pending = []
        lanes[lane].append(item)
        last = lane
    lanes[last].extend(pending)
    return lanes


def _split_loop(path: Path, ox: float, oy: float) -> Optional[Tuple[Path, Path]]:
    """Cut a closed loop around the axis into two halves of equal angular span, starting at
    the +X side, both running the same way round. Each half ends where the other starts,
    so two arms on opposite sides finish it together in half a disc turn."""
    pts = list(path.points)
    if len(pts) < 6:
        return None
    ang = [math.atan2(p[1] + oy, p[0] + ox) for p in pts]
    # orient anticlockwise
    area2 = sum((pts[i][0] + ox) * (pts[(i + 1) % len(pts)][1] + oy) - (pts[(i + 1) % len(pts)][0] + ox) * (pts[i][1] + oy)
                for i in range(len(pts)))
    if area2 < 0:
        pts.reverse()
        ang.reverse()
    k0 = min(range(len(pts)), key=lambda i: abs(ang[i]))              # near +X
    k1 = min(range(len(pts)), key=lambda i: abs(_wrap_to_pi(ang[i] - math.pi)))   # near -X
    if k0 == k1:
        return None
    if k0 < k1:
        a, b = pts[k0:k1 + 1], pts[k1:] + pts[:k0 + 1]
    else:
        a, b = pts[k0:] + pts[:k1 + 1], pts[k1:k0 + 1]
    if len(a) < 2 or len(b) < 2:
        return None
    pa, pb = Path(path.kind, a, closed=False), Path(path.kind, b, closed=False)
    for q in (pa, pb):
        q._turn = getattr(path, "_turn", False)
        q._anchor = None
        if not q._turn:                       # held half: face its own centre to its arm
            cx = sum(p[0] + ox for p in q.points) / len(q.points)
            cy = sum(p[1] + oy for p in q.points) / len(q.points)
            q._anchor = math.atan2(cy, cx)
    return pa, pb


def _mirror_pairs(items, ox, oy, tol=1.5):
    """Indices of held paths that are the same shape on the opposite side of the axis."""
    feats = []
    for idx, (li, path) in items:
        pts = [(p[0] + ox, p[1] + oy) for p in path.points]
        cx = sum(x for x, _ in pts) / len(pts)
        cy = sum(y for _, y in pts) / len(pts)
        feats.append((idx, cx, cy, path.length()))
    pairs, used = {}, set()
    for i, (ia, ax, ay, la) in enumerate(feats):
        if ia in used or math.hypot(ax, ay) < 3.0:
            continue
        for ib, bx, by, lb in feats[i + 1:]:
            if ib in used:
                continue
            if math.hypot(ax + bx, ay + by) <= tol and abs(la - lb) <= 0.05 * max(la, lb, 1e-6):
                pairs[ia] = ib
                used.update((ia, ib))
                break
    return pairs


def assign_slots(ordered: List[Tuple[int, Path]], cfg: PlannerConfig):
    """Two-arm work split, one SLOT per step group: [item for arm 0, item for arm 1] (None =
    that arm idles). Walls that go round the axis are cut in half, one half per arm, so both
    arms print the loop at once while the disc turns. Held features that are mirrored
    through the axis are printed together, one per arm. Everything else goes to arm 0 (the
    right arm) with arm 1 waiting. Needs the arms on opposite sides of the disc."""
    ox, oy = cfg.part_offset
    flip = False
    slots: List[List[Optional[Tuple[int, Path]]]] = []
    by_layer: Dict[int, List[Tuple[int, Tuple[int, Path]]]] = {}
    order = []
    for n, item in enumerate(ordered):
        by_layer.setdefault(item[0], []).append((n, item))
        if item[0] not in order:
            order.append(item[0])
    for li in order:
        items = by_layer[li]
        held = [(n, it) for n, it in items if it[1].kind != TRAVEL and not getattr(it[1], "_turn", False)]
        pairs = _mirror_pairs(held, ox, oy)
        partner_of = {b: a for a, b in pairs.items()}
        done = set()
        for n, (lidx, path) in items:
            if n in done:
                continue
            if path.kind == TRAVEL:
                slots.append([(lidx, path), None])
            elif path.closed and getattr(path, "_encloses", False):
                # a loop round the axis is shared whether the bed turns for it or not: with the
                # bed held, each half lies on its own arm's side once the disc faces arm 0
                halves = _split_loop(path, ox, oy)
                if halves:
                    # alternate which arm takes which half: after half a turn each arm is over
                    # the other half's start, so the next loop carries on without a swing back
                    if flip:
                        halves = (halves[1], halves[0])
                    flip = not flip
                    slots.append([(lidx, halves[0]), (lidx, halves[1])])
                else:
                    slots.append([(lidx, path), None])
            elif n in pairs:
                m = pairs[n]
                other = next(it for nn, it in items if nn == m)
                slots.append([(lidx, path), other])
                done.add(m)
            elif n in partner_of:
                continue
            else:
                slots.append([(lidx, path), None])
            done.add(n)
    return slots


def _clash_tester(cfg: PlannerConfig):
    """Returns clash(j, i, p_j, p_i) -> True when tool j at p_j and tool i at p_i would come
    within tool_margin (planner-frame points), from the fixed tool outlines (shapely
    polygons in the planner frame, relative to each nozzle); None without outlines."""
    fps = cfg.tool_footprints
    if not fps or len(fps) < 2 or any(f is None for f in fps):
        return None
    try:
        from shapely.affinity import translate
        from shapely.prepared import prep
    except Exception:
        return None
    grown = [prep(f.buffer(cfg.tool_margin)) for f in fps]

    def clash(j, i, pj, pi):
        moved = translate(fps[i], pi[0] - pj[0], pi[1] - pj[1])     # tool i in tool j's nozzle frame
        return grown[j].intersects(moved)
    return clash


def _is_coordinated(cfg: PlannerConfig, kind: str, path: Optional[Path] = None) -> bool:
    """True if the turntable rotates while printing this path."""
    if kind == TRAVEL:
        return False
    if not cfg.use_turntable:
        return False
    if cfg.rules is not None and path is not None and getattr(path, "_turn", None) is not None:
        return path._turn
    return kind in _WALLS or cfg.turntable_for_infill


def apply_rules(slc: SliceResult, cfg: PlannerConfig):
    """Decide, per path, whether the bed turns (cfg.rules). Marks each path with _turn and,
    for held paths, _anchor (plate angle of its centre, to face the arm). Returns a
    rules.DecisionLog. No-op (None) without rules."""
    if cfg.rules is None or not cfg.use_turntable:
        return None
    from rules import path_turntable_decision, layer_is_symmetric, DecisionLog
    rs = cfg.rules
    log = DecisionLog()
    ox, oy = cfg.part_offset
    face = bool(rs.get("turntable.face_arm", True))
    cfg.face_limit = math.radians(float(rs.get("turntable.face_limit_deg", 60.0)))
    cfg.min_separation = float(rs.get("hardware.min_nozzle_separation", cfg.min_separation))
    cfg.tool_margin = float(rs.get("hardware.tool_margin", cfg.tool_margin))
    for layer in slc.layers:
        plate_paths = [[(p[0] + ox, p[1] + oy) for p in path.points] for path in layer.paths
                       if path.kind != TRAVEL]
        sym = layer_is_symmetric(plate_paths)
        for path in layer.paths:
            if path.kind == TRAVEL:
                continue
            pts = [(p[0] + ox, p[1] + oy) for p in path.points]
            turn, rid, why = path_turntable_decision(pts, path.kind, rs, sym)
            path._turn = turn
            path._anchor = None
            from rules import _encloses_axis
            path._encloses = bool(path.closed and len(pts) >= 3 and _encloses_axis(pts))
            if not turn and face and pts:
                cx = sum(p[0] for p in pts) / len(pts)
                cy = sum(p[1] for p in pts) / len(pts)
                if math.hypot(cx, cy) > 5.0:
                    path._anchor = math.atan2(cy, cx)
            log.add(rid, why, f"layer {layer.index + 1} {path.kind.lower()}")
    return log


def _lane_vertices(cfg: PlannerConfig, lane: List[Tuple[int, Path]], slc: SliceResult):
    """Yield (plate_xy, z, extrude, seg_len, layer_idx, kind, width, height) for a lane.

    2D points take z from their layer and width/height from the config (None).
    3D points carry their own z, and 5-value points their own width and height."""
    ox, oy = cfg.part_offset
    z_by_index = {ly.index: ly.z for ly in slc.layers}
    for (layer_idx, path) in lane:
        z = z_by_index[layer_idx]
        travel = path.kind == TRAVEL
        pts = _prep_points(path, cfg.min_segment_length)
        # Subdivide only paths the turntable coordinates, so straight sides stay
        # straight in polar. Others (held infill, or Cartesian) need no extra pts.
        coord = _is_coordinated(cfg, path.kind, path)
        anchor = getattr(path, "_anchor", None) if (not coord and cfg.rules is not None) else None
        if coord and cfg.max_segment_length > 0:
            pts = _densify(pts, cfg.max_segment_length)
        prev = None
        for j, pt in enumerate(pts):
            plate = (pt[0] + ox, pt[1] + oy)
            if len(pt) == 2:
                pz, w, h = z, None, None
            else:
                pz = pt[2]
                w, h = (pt[3], pt[4]) if len(pt) >= 5 else (None, None)
            tilt = float(pt[5]) if len(pt) >= 6 else 0.0
            if j == 0:
                yield (plate, pz, False, 0.0, layer_idx, path.kind, w, h, coord, anchor, tilt)
            else:
                if len(pt) == 2:
                    seg = math.hypot(plate[0] - prev[0][0], plate[1] - prev[0][1])
                else:
                    seg = math.dist((plate[0], plate[1], pz), (prev[0][0], prev[0][1], prev[1]))
                yield (plate, pz, not travel, seg, layer_idx, path.kind, w, h, coord, anchor, tilt)
            prev = (plate, pz)


def plan(slc: SliceResult, cfg: PlannerConfig) -> MotionProgram:
    decisions = apply_rules(slc, cfg)
    ordered = order_toolpaths(slc, cfg.rules)
    n = max(1, cfg.num_arms)
    pinned = any(getattr(p, "arm", None) is not None for _, p in ordered)
    if n == 2 and cfg.rules is not None and not pinned:
        # slot-based split (both arms on one layer at once); each slot's two vertex lists
        # are padded with None so the arms stay step-aligned
        jobs = []
        for slot in assign_slots(ordered, cfg):
            jobs.append([list(_lane_vertices(cfg, [it], slc)) if it is not None else [] for it in slot])
    else:
        lanes = _assign_paths(ordered, n)
        jobs = [[list(_lane_vertices(cfg, lanes[i], slc)) for i in range(n)]]
    streams = [sum((job[i] for job in jobs), []) for i in range(n)]   # for callers that inspect them

    fil_area = cfg.filament_area()
    phi = cfg.start_phi
    prev_world: List[Optional[Tuple[float, float, float]]] = [None] * n
    prev_plate: List[Optional[Tuple[float, float]]] = [None] * n

    # Retreat pose per arm ("two carpenters on a log"): when an arm has nothing to do, or the
    # other arm's job takes it across the axis into this arm's half, this arm backs off to its
    # own side, outside the part and above it. Planner (arm 0) frame, fixed in the cell.
    ox, oy = cfg.part_offset
    part_r = max((math.hypot(p[0] + ox, p[1] + oy) for _, path in ordered for p in path.points), default=50.0)
    top_z = max((ly.z for ly in slc.layers), default=0.0)
    retreat_r = part_r + cfg.retreat_clearance
    cur_z = [0.0] * n

    def retreat_pose(i, z):
        a = cfg.arm_azimuths[i % len(cfg.arm_azimuths)]
        return (cfg.center[0] + retreat_r * math.cos(a), cfg.center[1] + retreat_r * math.sin(a),
                cfg.z_base + z + cfg.retreat_lift)

    retreated = [False] * n
    held_last = [False] * n
    crossings: List[int] = []
    clash = _clash_tester(cfg)

    steps: List[MotionStep] = []
    held_for = [0] * n
    k = -1
    # Jobs run one after another; inside a job the arms step together (so shared loops stay
    # in step) unless the collision guard holds one back. Every job starts re-synchronised.
    for job in jobs:
        cursor = [0] * n
        streams = job
        while any(cursor[i] < len(streams[i]) for i in range(n)):
          k += 1
          recs = [streams[i][cursor[i]] if cursor[i] < len(streams[i]) else None for i in range(n)]
          advance = [True] * n
          if n > 1:
              # collision guard: never let two nozzles come within min_separation. Arm 0 (the
              # primary) always goes; a later arm that would get too close WAITS this step.
              targets = [None] * n
              for i, rec in enumerate(recs):
                  if rec is not None:
                      targets[i] = _world(cfg, rec[0], phi, rec[1])     # at the current disc angle
              for i in range(1, n):
                  if targets[i] is None:
                      continue
                  for j in range(i):
                      # an idle arm never blocks: it is (or is about to be) at its retreat pose,
                      # outside the part, which is further than the part edge from any target
                      other = targets[j]
                      if other is not None and (clash(j, i, other, targets[i]) if clash else
                                                math.dist(targets[i][:2], other[:2]) < cfg.min_separation):
                          recs[i] = None
                          advance[i] = False
                          held_last[i] = True
                          held_for[i] += 1
                          if held_for[i] > 20000:
                              raise RuntimeError("Two-arm plan deadlocked: the left arm cannot get to its "
                                                 "path without hitting the right arm.")
                          break
                  else:
                      held_for[i] = 0
          for i in range(n):
              if advance[i] and cursor[i] < len(streams[i]):
                  cursor[i] += 1

          if cfg.use_turntable:
              desired = []
              for i, rec in enumerate(recs):
                  if rec is None:
                      continue
                  coord, anchor = rec[8], rec[9]
                  alpha = cfg.arm_azimuths[i % len(cfg.arm_azimuths)]
                  if coord:
                      plate = rec[0]
                      theta = math.atan2(plate[1], plate[0])
                  elif anchor is not None:
                      # Held path: if its centre is more than face_limit away from this arm,
                      # turn it to face the arm first; otherwise leave the bed where it is.
                      off = _wrap_to_pi(anchor + phi - alpha)
                      if abs(off) <= cfg.face_limit:
                          continue
                      theta = anchor
                  else:
                      continue
                  desired.append(phi + _wrap_to_pi((alpha - theta) - phi))
              if desired:
                  sx = sum(math.sin(a) for a in desired)
                  cxx = sum(math.cos(a) for a in desired)
                  phi_target = phi + _wrap_to_pi(math.atan2(sx, cxx) - phi)
              else:
                  phi_target = phi
          else:
              phi_target = cfg.start_phi

          dphi = abs(_wrap_to_pi(phi_target - phi))
          t_tt = dphi / cfg.max_tt_speed if cfg.max_tt_speed > 0 else 0.0

          t_feed = 0.0
          tmp = []
          for i, rec in enumerate(recs):
              if rec is None:
                  tmp.append(None)
                  continue
              plate, z, extrude, seg, layer_idx, kind, w, h = rec[:8]
              tilt = rec[10] if len(rec) > 10 else 0.0
              if retreated[i] or held_last[i]:
                  extrude, seg = False, 0.0        # coming back from a hold / retreat: a travel, not a bead
              speed = cfg.print_speed if extrude else cfg.travel_speed
              t_feed = max(t_feed, seg / speed if speed > 0 else 0.0)
              world = _world(cfg, plate, phi_target, z)
              # bead direction on the plate, turned into the planner frame by the disc angle
              pp = prev_plate[i]
              if pp is not None:
                  c_, s__ = math.cos(phi_target), math.sin(phi_target)
                  tx, ty = plate[0] - pp[0], plate[1] - pp[1]
                  tang = (tx * c_ - ty * s__, tx * s__ + ty * c_)
              else:
                  tang = None
              prev_plate[i] = plate
              tmp.append((world, extrude, seg, layer_idx, kind, z, w, h, tilt, tang))
              held_last[i] = False
              cur_z[i] = z
              retreated[i] = False
          if n > 1:
              # which arms are working in the OTHER arm's half (angle from the axis vs its azimuth)?
              crossing = [False] * n
              for i, at in enumerate(tmp):
                  if at is None:
                      continue
                  a = cfg.arm_azimuths[i % len(cfg.arm_azimuths)]
                  ang = math.atan2(at[0][1] - cfg.center[1], at[0][0] - cfg.center[0])
                  crossing[i] = abs(_wrap_to_pi(ang - a)) > cfg.cross_angle
              for i in range(n):
                  if tmp[i] is not None:
                      continue
                  other_busy = any(tmp[j] is not None for j in range(n) if j != i)
                  if retreated[i] or not other_busy:
                      continue
                  z = max(cur_z)
                  tmp[i] = (retreat_pose(i, z), False, 0.0, -1, "RETREAT", z, None, None, 0.0, None)
                  retreated[i] = True
              # an arm crossing into the other's half must find that arm already retreated;
              # the planner flags it so the report can say so (the arms are opposite by
              # construction for shared loops and mirrored pairs; this covers the rest)
              for i in range(n):
                  if crossing[i]:
                      for j in range(n):
                          if j != i and tmp[j] is not None and not (tmp[j][4] == "RETREAT"):
                              crossings.append(k)
                              break

          t_arm = 0.0
          for i, at in enumerate(tmp):
              if at is None or prev_world[i] is None:
                  continue
              t_arm = max(t_arm, math.dist(at[0], prev_world[i]) / cfg.max_arm_speed
                          if cfg.max_arm_speed > 0 else 0.0)

          dt = max(t_feed, t_tt, t_arm, 1e-3)

          roll, pitch, yaw = cfg.orientation
          arms: List[Optional[ArmTarget]] = []
          step_layer, step_kind = -1, ""
          for i, at in enumerate(tmp):
              if at is None:
                  arms.append(None)
                  continue
              world, extrude, seg, layer_idx, kind, z, w, h = at[:8]
              tilt = at[8] if len(at) > 8 else 0.0
              tang = at[9] if len(at) > 9 else None
              if step_layer < 0:
                  step_layer, step_kind = layer_idx, kind
              e = 0.0
              if extrude and fil_area > 0:
                  flow = cfg.flow_multiplier
                  if cfg.first_layer_z is not None:
                      first = z <= cfg.first_layer_z
                  else:
                      first = layer_idx < cfg.first_layer_count
                  if first:
                      flow *= cfg.first_layer_flow
                  lw = cfg.line_width if w is None else w
                  lh = cfg.layer_height if h is None else h
                  e = seg * lw * lh / fil_area * flow
              own = to_arm_frame(cfg, i, world)
              r_, p_, y_ = arm_rpy(cfg, i, world, tang, tilt) if cfg.arm_orientations else (roll, pitch, yaw)
              arms.append(ArmTarget(round(own[0], 3), round(own[1], 3), round(own[2], 3),
                                    r_, p_, y_, extrude, round(e, 4)))
              prev_world[i] = world

          steps.append(MotionStep(dt=dt, tt_angle_deg=math.degrees(phi_target),
                                  arms=arms, layer=step_layer, kind=step_kind))
          phi = phi_target

    if n > 1 and steps:
        z = max(cur_z)
        arms = []
        for i in range(n):
            w = retreat_pose(i, z)
            own = to_arm_frame(cfg, i, w)
            roll, pitch, yaw = cfg.arm_orientations[i] if cfg.arm_orientations else cfg.orientation
            arms.append(ArmTarget(round(own[0], 3), round(own[1], 3), round(own[2], 3), roll, pitch, yaw, False, 0.0))
        d = max((math.dist(retreat_pose(i, z), prev_world[i]) for i in range(n) if prev_world[i]), default=50.0)
        steps.append(MotionStep(dt=max(d / cfg.max_arm_speed, 0.2), tt_angle_deg=math.degrees(phi), arms=arms,
                                layer=-1, kind="RETREAT"))
    prog = MotionProgram(steps=steps, config=cfg, decisions=decisions)
    prog.crossings = crossings
    return prog


# ----------------------------------------------------------------------------
def extrusion_runs(program: MotionProgram, arm_index: int = 0) -> Dict[int, Tuple[float, float]]:
    runs: Dict[int, Tuple[float, float]] = {}
    steps = program.steps
    i, nsteps = 0, len(steps)
    while i < nsteps:
        at = steps[i].arms[arm_index] if arm_index < len(steps[i].arms) else None
        if at is None or not at.extrude:
            i += 1
            continue
        j = i
        total_e = duration = 0.0
        while j < nsteps:
            a = steps[j].arms[arm_index] if arm_index < len(steps[j].arms) else None
            if a is None or not a.extrude:
                break
            total_e += a.e
            duration += steps[j].dt
            j += 1
        runs[i] = (round(total_e, 4), round(total_e / duration if duration > 0 else 0.0, 4))
        i = j
    return runs


def debug_rows(program: MotionProgram, arm_index: int = 0) -> List[dict]:
    rows = []
    t = 0.0
    cx, cy = program.config.center[0], program.config.center[1]
    for i, step in enumerate(program.steps):
        at = step.arms[arm_index] if arm_index < len(step.arms) else None
        if at is not None:
            px_, py_, _ = from_arm_frame(program.config, arm_index, (at.x, at.y, at.z))
        radius = round(math.hypot(px_ - cx, py_ - cy), 3) if at else None
        rows.append(dict(
            i=i, t=round(t, 4), dt_ms=round(step.dt * 1000, 2),
            layer=step.layer, kind=step.kind,
            move=("EXTRUDE" if (at and at.extrude) else "travel"),
            x=(at.x if at else None), y=(at.y if at else None), z=(at.z if at else None),
            r=radius, yaw=(at.yaw if at else None), tt_deg=round(step.tt_angle_deg, 3),
            e=(at.e if at else 0.0),
        ))
        t += step.dt
    return rows


def dt_stats(program: MotionProgram, arm_index: int = 0) -> dict:
    steps = program.steps
    if not steps:
        return dict(steps=0)
    dts = [s.dt for s in steps]
    seg_lens = []
    prev = None
    for s in steps:
        at = s.arms[arm_index] if arm_index < len(s.arms) else None
        if at is not None:
            w = (at.x, at.y, at.z)
            if prev is not None:
                seg_lens.append(math.dist(w, prev))
            prev = w
    return dict(
        steps=len(steps),
        total_time_s=round(sum(dts), 2),
        dt_ms_min=round(min(dts) * 1000, 2),
        dt_ms_avg=round(sum(dts) / len(dts) * 1000, 2),
        dt_ms_max=round(max(dts) * 1000, 2),
        steps_under_5ms=sum(1 for d in dts if d < 0.005),
        seg_mm_avg=round(sum(seg_lens) / len(seg_lens), 3) if seg_lens else 0.0,
        seg_mm_max=round(max(seg_lens), 3) if seg_lens else 0.0,
    )


# ----------------------------------------------------------------------------
@dataclass
class PlanStats:
    total_time: float
    arm_peak_speed: List[float]
    arm_avg_speed: List[float]
    arm_path_len: List[float]
    tt_peak_speed: float
    tt_travel: float
    tt_reversals: int


def analyze(program: MotionProgram) -> PlanStats:
    cfg = program.config
    n = cfg.num_arms
    prev_world: List[Optional[Tuple[float, float, float]]] = [None] * n
    peak = [0.0] * n
    length = [0.0] * n
    move_time = [0.0] * n
    tt_peak = tt_travel = 0.0
    reversals = 0
    prev_angle = None
    prev_dir = 0

    for step in program.steps:
        if prev_angle is not None:
            d = _wrap_to_pi(math.radians(step.tt_angle_deg) - math.radians(prev_angle))
            tt_travel += abs(d)
            if step.dt > 0:
                tt_peak = max(tt_peak, abs(d) / step.dt)
            cur_dir = 1 if d > 1e-9 else (-1 if d < -1e-9 else prev_dir)
            if cur_dir != 0 and prev_dir != 0 and cur_dir != prev_dir:
                reversals += 1
            if cur_dir != 0:
                prev_dir = cur_dir
        prev_angle = step.tt_angle_deg

        for i, at in enumerate(step.arms):
            if at is None:
                continue
            w = (at.x, at.y, at.z)
            if prev_world[i] is not None:
                disp = math.dist(w, prev_world[i])
                length[i] += disp
                move_time[i] += step.dt
                if step.dt > 0:
                    peak[i] = max(peak[i], disp / step.dt)
            prev_world[i] = w

    avg = [length[i] / move_time[i] if move_time[i] > 0 else 0.0 for i in range(n)]
    return PlanStats(total_time=program.total_time(),
                     arm_peak_speed=peak, arm_avg_speed=avg, arm_path_len=length,
                     tt_peak_speed=tt_peak, tt_travel=tt_travel, tt_reversals=reversals)


# ----------------------------------------------------------------------------
if __name__ == "__main__":
    import warnings, sys
    warnings.filterwarnings("ignore")
    from slicer import slice_model, SliceSettings

    s = SliceSettings(layer_height=0.2, line_width=0.4, wall_count=1,
                      infill_density=0.0, infill_pattern="grid",
                      top_layers=0, bottom_layers=0)
    slc = slice_model(sys.argv[1] if len(sys.argv) > 1 else "cube40.3mf", s)

    # One outer-wall loop, polar, densified -> radius should vary corner<->mid.
    from slicer import WALL_OUTER
    one = [p for p in slc.layers[10].paths if p.kind == WALL_OUTER][:1]
    from slicer import SliceResult, Layer
    sub = SliceResult(layers=[Layer(index=10, z=slc.layers[10].z, solid=False, paths=one)],
                      settings=slc.settings, bounds=slc.bounds)

    for mseg in (0.0, 1.0):
        cfg = PlannerConfig(num_arms=1, use_turntable=True, max_segment_length=mseg)
        prog = plan(sub, cfg)
        rows = debug_rows(prog)
        radii = [r["r"] for r in rows if r["r"] is not None]
        print(f"\nmax_segment_length={mseg}: {len(rows)} points, "
              f"radius min={min(radii):.2f} max={max(radii):.2f} spread={max(radii)-min(radii):.2f} mm")
        print("  (spread ~0 => circle;  spread ~8 => square corners vs midpoints)")
