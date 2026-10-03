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


def order_toolpaths(slc: SliceResult) -> List[Tuple[int, Path]]:
    ordered: List[Tuple[int, Path]] = []
    for layer in slc.layers:
        paths = sorted(layer.paths, key=lambda p: _KIND_ORDER.get(p.kind, 9))
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
    px, py = plate_xy
    c, s = math.cos(phi), math.sin(phi)
    wx = cfg.center[0] + (px * c - py * s)
    wy = cfg.center[1] + (px * s + py * c)
    wz = cfg.z_base + z_layer
    return (wx, wy, wz)


def _assign_paths(ordered: List[Tuple[int, Path]], num_arms: int) -> List[List[Tuple[int, Path]]]:
    """Round robin over arms. A path with an `arm` attribute goes to that arm; travel
    paths go with the extruding path that follows them."""
    lanes: List[List[Tuple[int, Path]]] = [[] for _ in range(num_arms)]
    pending: List[Tuple[int, Path]] = []
    rr = 0
    last = 0
    for item in ordered:
        path = item[1]
        if path.kind == TRAVEL:
            pending.append(item)
            continue
        arm = getattr(path, "arm", None)
        if arm is None:
            lane = rr % num_arms
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
        for attr in ("_turn", "_anchor"):
            if hasattr(path, attr):
                setattr(q, attr, getattr(path, attr))
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
            elif getattr(path, "_turn", False) and path.closed and getattr(path, "_encloses", False):
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
            if j == 0:
                yield (plate, pz, False, 0.0, layer_idx, path.kind, w, h, coord, anchor)
            else:
                if len(pt) == 2:
                    seg = math.hypot(plate[0] - prev[0][0], plate[1] - prev[0][1])
                else:
                    seg = math.dist((plate[0], plate[1], pz), (prev[0][0], prev[0][1], prev[1]))
                yield (plate, pz, not travel, seg, layer_idx, path.kind, w, h, coord, anchor)
            prev = (plate, pz)


def plan(slc: SliceResult, cfg: PlannerConfig) -> MotionProgram:
    decisions = apply_rules(slc, cfg)
    ordered = order_toolpaths(slc)
    n = max(1, cfg.num_arms)
    pinned = any(getattr(p, "arm", None) is not None for _, p in ordered)
    if n == 2 and cfg.rules is not None and not pinned:
        # slot-based split (both arms on one layer at once); each slot's two vertex lists
        # are padded with None so the arms stay step-aligned
        streams = [[], []]
        for slot in assign_slots(ordered, cfg):
            parts = [list(_lane_vertices(cfg, [it], slc)) if it is not None else [] for it in slot]
            m = max(len(q) for q in parts)
            for i in range(2):
                streams[i].extend(parts[i] + [None] * (m - len(parts[i])))
    else:
        lanes = _assign_paths(ordered, n)
        streams = [list(_lane_vertices(cfg, lanes[i], slc)) for i in range(n)]

    fil_area = cfg.filament_area()
    phi = cfg.start_phi
    prev_world: List[Optional[Tuple[float, float, float]]] = [None] * n

    steps: List[MotionStep] = []
    max_len = max((len(s) for s in streams), default=0)

    for k in range(max_len):
        recs = [streams[i][k] if k < len(streams[i]) else None for i in range(n)]

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
            speed = cfg.print_speed if extrude else cfg.travel_speed
            t_feed = max(t_feed, seg / speed if speed > 0 else 0.0)
            world = _world(cfg, plate, phi_target, z)
            tmp.append((world, extrude, seg, layer_idx, kind, z, w, h))

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
            world, extrude, seg, layer_idx, kind, z, w, h = at
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
            arms.append(ArmTarget(round(world[0], 3), round(world[1], 3), round(world[2], 3),
                                  roll, pitch, yaw, extrude, round(e, 4)))
            prev_world[i] = world

        steps.append(MotionStep(dt=dt, tt_angle_deg=math.degrees(phi_target),
                                arms=arms, layer=step_layer, kind=step_kind))
        phi = phi_target

    return MotionProgram(steps=steps, config=cfg, decisions=decisions)


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
        radius = round(math.hypot(at.x - cx, at.y - cy), 3) if at else None
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
