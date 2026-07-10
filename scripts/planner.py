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
                   (cx, cy, cz) in this frame (from calibration.yaml: the right
                   arm reads the disc centre at (575.6, 3.5), z=152.0).
                   Rotating the turntable by phi rotates plate points about the
                   axis:  world = center + Rz(phi) . plate

Turntable strategy (per path kind)
----------------------------------
For WALLS the turntable does the angular work so the arm only covers the radial
residual: for a plate point at angle theta about the axis we rotate to
phi = alpha - theta (alpha = arm's preferred azimuth), shortest direction (so an
offset/L feature spins the turntable the other way). For INFILL / SKIN the
turntable HOLDS (unless turntable_for_infill=True) and the arm traces the fill
directly - otherwise every infill point demands a large turntable swing, which
makes the motion lurch (seconds per point). With the turntable disabled entirely
it is held at a fixed home angle and the arm traces everything (Cartesian bed).

Extrusion
---------
Filament per move is volumetric: e = seg * line_width * layer_height / area,
scaled by flow_multiplier (and first_layer_flow on the first layer(s)). The
executor groups consecutive extruding moves of a path into ONE continuous
extrude at feed = total_e / path_time (see extrusion_runs).
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict
import math

from slicer import SliceResult, Layer, Path, WALL_OUTER, WALL_INNER, SKIN, INFILL

_KIND_ORDER = {WALL_OUTER: 0, WALL_INNER: 1, SKIN: 2, INFILL: 3}
_WALLS = (WALL_OUTER, WALL_INNER)


@dataclass
class PlannerConfig:
    num_arms: int = 1
    use_turntable: bool = True
    turntable_for_infill: bool = False   # False = hold turntable during infill/skin

    center: Tuple[float, float, float] = (575.6, 3.5, 152.0)  # right-arm calibration
    z_base: float = 152.0
    part_offset: Tuple[float, float] = (0.0, 0.0)

    arm_azimuths: Tuple[float, ...] = (math.radians(-45.0),)  # right zone centre
    orientation: Tuple[float, float, float] = (180.0, 45.0, 20.0)

    max_arm_speed: float = 100.0
    max_tt_speed: float = 1.5           # rad/s
    print_speed: float = 30.0           # mm/s along the part surface
    travel_speed: float = 150.0

    line_width: float = 0.4
    layer_height: float = 0.2
    filament_diameter: float = 1.75
    flow_multiplier: float = 1.0
    first_layer_flow: float = 1.2
    first_layer_count: int = 1
    extruder_tool: int = 0              # 0 = E axis (right arm's extruder)

    min_segment_length: float = 0.0     # merge points closer than this (0 = off)
    start_phi: float = 0.0

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


def _simplify(points: List[Tuple[float, float]], min_seg: float) -> List[Tuple[float, float]]:
    if min_seg <= 0 or len(points) <= 2:
        return points
    out = [points[0]]
    for p in points[1:-1]:
        if math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) >= min_seg:
            out.append(p)
    out.append(points[-1])
    return out


def _path_points(path: Path, min_seg: float) -> List[Tuple[float, float]]:
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
    lanes: List[List[Tuple[int, Path]]] = [[] for _ in range(num_arms)]
    for i, item in enumerate(ordered):
        lanes[i % num_arms].append(item)
    return lanes


def _lane_vertices(cfg: PlannerConfig, lane: List[Tuple[int, Path]], slc: SliceResult):
    """Yield (plate_xy, z_layer, extrude, seg_len, layer_idx, kind) for a lane."""
    ox, oy = cfg.part_offset
    z_by_index = {ly.index: ly.z for ly in slc.layers}
    for (layer_idx, path) in lane:
        z = z_by_index[layer_idx]
        pts = _path_points(path, cfg.min_segment_length)
        prev = None
        for j, (mx, my) in enumerate(pts):
            plate = (mx + ox, my + oy)
            if j == 0:
                yield (plate, z, False, 0.0, layer_idx, path.kind)
            else:
                seg = math.hypot(plate[0] - prev[0], plate[1] - prev[1])
                yield (plate, z, True, seg, layer_idx, path.kind)
            prev = plate


def plan(slc: SliceResult, cfg: PlannerConfig) -> MotionProgram:
    ordered = order_toolpaths(slc)
    n = max(1, cfg.num_arms)
    lanes = _assign_paths(ordered, n)
    streams = [list(_lane_vertices(cfg, lanes[i], slc)) for i in range(n)]

    fil_area = cfg.filament_area()
    phi = cfg.start_phi
    prev_world: List[Optional[Tuple[float, float, float]]] = [None] * n

    steps: List[MotionStep] = []
    max_len = max((len(s) for s in streams), default=0)

    for k in range(max_len):
        recs = [streams[i][k] if k < len(streams[i]) else None for i in range(n)]

        # ---- decide the turntable target for this step ----
        if cfg.use_turntable:
            desired = []
            for i, rec in enumerate(recs):
                if rec is None:
                    continue
                kind = rec[5]
                # Only coordinate the turntable for walls (unless asked to also
                # coordinate infill). Infill/skin -> hold the turntable steady.
                if not cfg.turntable_for_infill and kind not in _WALLS:
                    continue
                plate = rec[0]
                theta = math.atan2(plate[1], plate[0])
                alpha = cfg.arm_azimuths[i % len(cfg.arm_azimuths)]
                desired.append(phi + _wrap_to_pi((alpha - theta) - phi))
            if desired:
                sx = sum(math.sin(a) for a in desired)
                cxx = sum(math.cos(a) for a in desired)
                phi_target = phi + _wrap_to_pi(math.atan2(sx, cxx) - phi)
            else:
                phi_target = phi            # hold current angle
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
            plate, z, extrude, seg, layer_idx, kind = rec
            speed = cfg.print_speed if extrude else cfg.travel_speed
            t_feed = max(t_feed, seg / speed if speed > 0 else 0.0)
            world = _world(cfg, plate, phi_target, z)
            tmp.append((world, extrude, seg, layer_idx, kind))

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
            world, extrude, seg, layer_idx, kind = at
            if step_layer < 0:
                step_layer, step_kind = layer_idx, kind
            e = 0.0
            if extrude and fil_area > 0:
                flow = cfg.flow_multiplier
                if layer_idx < cfg.first_layer_count:
                    flow *= cfg.first_layer_flow
                e = seg * cfg.line_width * cfg.layer_height / fil_area * flow
            arms.append(ArmTarget(round(world[0], 3), round(world[1], 3), round(world[2], 3),
                                  roll, pitch, yaw, extrude, round(e, 4)))
            prev_world[i] = world

        steps.append(MotionStep(dt=dt, tt_angle_deg=math.degrees(phi_target),
                                arms=arms, layer=step_layer, kind=step_kind))
        phi = phi_target

    return MotionProgram(steps=steps, config=cfg)


# ----------------------------------------------------------------------------
# Extrusion grouping + debugging helpers
# ----------------------------------------------------------------------------
def extrusion_runs(program: MotionProgram, arm_index: int = 0) -> Dict[int, Tuple[float, float]]:
    """Group consecutive extruding steps into runs -> {start_index:(total_e, feed)}."""
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
    for i, step in enumerate(program.steps):
        at = step.arms[arm_index] if arm_index < len(step.arms) else None
        rows.append(dict(
            i=i, t=round(t, 4), dt_ms=round(step.dt * 1000, 2),
            layer=step.layer, kind=step.kind,
            move=("EXTRUDE" if (at and at.extrude) else "travel"),
            x=(at.x if at else None), y=(at.y if at else None), z=(at.z if at else None),
            yaw=(at.yaw if at else None), tt_deg=round(step.tt_angle_deg, 3),
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

    s = SliceSettings(layer_height=0.2, line_width=0.4, wall_count=3,
                      infill_density=0.20, infill_pattern="grid",
                      top_layers=3, bottom_layers=3)
    slc = slice_model(sys.argv[1] if len(sys.argv) > 1 else "cube40.3mf", s)
    print("slice:", slc.summary())
    for label, tfi in [("walls-only turntable (default)", False), ("turntable for infill too", True)]:
        cfg = PlannerConfig(num_arms=1, use_turntable=True, turntable_for_infill=tfi)
        prog = plan(slc, cfg)
        st = analyze(prog)
        print(f"\n== {label} ==")
        print("  dt_stats:", dt_stats(prog))
        print(f"  arm avg={st.arm_avg_speed[0]:.1f} peak={st.arm_peak_speed[0]:.1f} mm/s  "
              f"len={st.arm_path_len[0]/1000:.2f}m  tt_turns={st.tt_travel/(2*math.pi):.1f} rev={st.tt_reversals}")
        print(f"  extrusion runs: {len(extrusion_runs(prog))}")
