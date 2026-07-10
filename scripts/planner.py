#!/usr/bin/env python3
"""
Motion planner for the Gleadall multi-cell panel.

Turns a `SliceResult` (from slicer.py) into a `MotionProgram`: an ordered list
of timed steps giving, per step, the turntable target angle and each enabled
arm's Cartesian target (x, y, z, roll, pitch, yaw) plus an extrusion amount.

Two coordinate frames:

  * plate frame  - the part sits on the turntable. Origin = turntable rotation
                   axis. A model point (mx, my) maps to plate (mx+ox, my+oy)
                   where (ox, oy) = part_offset.
  * arm/world    - the frame the arms move in. The turntable axis is located at
                   (cx, cy, cz) in this frame. Rotating the turntable by phi
                   rotates every plate point about that axis:
                       world = center + Rz(phi) . plate

Turntable strategy (the heart of the request)
---------------------------------------------
With the turntable ENABLED we let the turntable do the angular work so the arm
only has to cover the *radial* residual. For a target plate point at angle
theta about the axis, we rotate the turntable to

      phi_target = alpha - theta          (alpha = arm's preferred azimuth)

so the point arrives at the arm's sweet spot and the arm barely moves in the
tangential direction. phi_target is reached along the *shortest* angular
direction, so when theta decreases (e.g. an offset/L feature that is not
centred on the axis) the turntable naturally spins the OTHER way. Segment time
expands to respect the turntable's max angular speed, so where theta jumps (a
sharp corner) the machine slows instead of demanding an impossible arm move.

With the turntable DISABLED we hold it at a fixed home angle and the part is a
static Cartesian bed: the arm traces the whole path itself. The same world()
transform is used for both, so switching modes is just a matter of whether phi
is allowed to move.
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional
import math

from slicer import SliceResult, Layer, Path, WALL_OUTER, WALL_INNER, SKIN, INFILL

# print order of path kinds within a layer (outer wall first, infill last)
_KIND_ORDER = {WALL_OUTER: 0, WALL_INNER: 1, SKIN: 2, INFILL: 3}


@dataclass
class PlannerConfig:
    num_arms: int = 1
    use_turntable: bool = True

    # turntable axis location in the arm/world frame (mm)
    center: Tuple[float, float, float] = (570.0, 0.0, 151.0)
    # world z of the model's z=0 plane (usually the turntable top face)
    z_base: float = 151.8
    # where the model origin sits on the plate relative to the axis (mm)
    part_offset: Tuple[float, float] = (0.0, 0.0)

    # preferred world azimuth (radians) for each arm's deposition point
    arm_azimuths: Tuple[float, ...] = (math.radians(135.0),)
    # fixed arm orientation while printing (deg)
    orientation: Tuple[float, float, float] = (180.0, 45.0, 20.0)

    # limits
    max_arm_speed: float = 100.0        # mm/s (Cartesian TCP speed cap)
    max_tt_speed: float = 1.5           # rad/s (turntable angular speed cap)
    print_speed: float = 20.0           # mm/s nominal speed along the part surface
    travel_speed: float = 80.0          # mm/s for non-extruding moves

    # extrusion
    line_width: float = 0.4
    layer_height: float = 0.2
    filament_diameter: float = 1.75

    start_phi: float = 0.0              # initial turntable angle (rad)

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
    extrude: bool          # True if material is laid on the move INTO this point
    e: float = 0.0         # filament length for this move (mm)


@dataclass
class MotionStep:
    dt: float                          # seconds for this segment
    tt_angle_deg: float                # absolute turntable target angle (deg)
    arms: List[Optional[ArmTarget]]    # one entry per arm (None = hold/idle)


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
    """Flatten the slice into print order: by layer, then outer->inner->infill."""
    ordered: List[Tuple[int, Path]] = []
    for layer in slc.layers:
        paths = sorted(layer.paths, key=lambda p: _KIND_ORDER.get(p.kind, 9))
        for p in paths:
            ordered.append((layer.index, p))
    return ordered


def _path_points(path: Path) -> List[Tuple[float, float]]:
    pts = list(path.points)
    if path.closed and len(pts) >= 2 and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def _world(cfg: PlannerConfig, plate_xy: Tuple[float, float], phi: float,
           z_layer: float) -> Tuple[float, float, float]:
    px, py = plate_xy
    c, s = math.cos(phi), math.sin(phi)
    wx = cfg.center[0] + (px * c - py * s)
    wy = cfg.center[1] + (px * s + py * c)
    wz = cfg.z_base + z_layer
    return (wx, wy, wz)


def _assign_paths(ordered: List[Tuple[int, Path]], num_arms: int) -> List[List[Tuple[int, Path]]]:
    """Split the ordered path list across arms (round-robin per path)."""
    lanes: List[List[Tuple[int, Path]]] = [[] for _ in range(num_arms)]
    for i, item in enumerate(ordered):
        lanes[i % num_arms].append(item)
    return lanes


def _lane_vertices(cfg: PlannerConfig, lane: List[Tuple[int, Path]],
                   slc: SliceResult):
    """Yield per-vertex records for one arm's lane.

    Each record: (plate_xy, z_layer, extrude_flag, seg_len_on_part)
    seg_len is the distance along the part from the previous vertex of the SAME
    path (0.0 for the first vertex of a path == a travel move).
    """
    ox, oy = cfg.part_offset
    z_by_index = {ly.index: ly.z for ly in slc.layers}
    for (layer_idx, path) in lane:
        z = z_by_index[layer_idx]
        pts = _path_points(path)
        prev = None
        for j, (mx, my) in enumerate(pts):
            plate = (mx + ox, my + oy)
            if j == 0:
                yield (plate, z, False, 0.0)       # travel to path start
            else:
                seg = math.hypot(plate[0] - prev[0], plate[1] - prev[1])
                yield (plate, z, True, seg)        # extrude along path
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
        # gather this step's per-arm desired vertices
        desired_phi = []
        recs = []
        for i in range(n):
            if k < len(streams[i]):
                recs.append(streams[i][k])
            else:
                recs.append(None)

        # desired turntable angle to service each active arm
        if cfg.use_turntable:
            for i, rec in enumerate(recs):
                if rec is None:
                    continue
                plate, z, extrude, seg = rec
                theta = math.atan2(plate[1], plate[0])
                alpha = cfg.arm_azimuths[i % len(cfg.arm_azimuths)]
                # nearest solution to current phi (enables reversing direction)
                target = phi + _wrap_to_pi((alpha - theta) - phi)
                desired_phi.append(target)
            if desired_phi:
                # circular mean so a shared turntable fairly serves all arms
                sx = sum(math.sin(a) for a in desired_phi)
                cx = sum(math.cos(a) for a in desired_phi)
                phi_target = phi + _wrap_to_pi(math.atan2(sx, cx) - phi)
            else:
                phi_target = phi
        else:
            phi_target = cfg.start_phi

        dphi = abs(_wrap_to_pi(phi_target - phi))

        # ---- timing: expand dt so every subsystem stays within its limits ----
        t_tt = dphi / cfg.max_tt_speed if cfg.max_tt_speed > 0 else 0.0
        t_feed = 0.0
        arm_targets_tmp = []
        for i, rec in enumerate(recs):
            if rec is None:
                arm_targets_tmp.append(None)
                continue
            plate, z, extrude, seg = rec
            speed = cfg.print_speed if extrude else cfg.travel_speed
            t_feed = max(t_feed, seg / speed if speed > 0 else 0.0)
            world = _world(cfg, plate, phi_target, z)
            arm_targets_tmp.append((world, extrude, seg, z, plate))

        # arm displacement time (given phi reaches its target)
        t_arm = 0.0
        for i, at in enumerate(arm_targets_tmp):
            if at is None or prev_world[i] is None:
                continue
            w = at[0]
            disp = math.dist(w, prev_world[i])
            t_arm = max(t_arm, disp / cfg.max_arm_speed if cfg.max_arm_speed > 0 else 0.0)

        dt = max(t_feed, t_tt, t_arm, 1e-3)

        # ---- build the step ----
        roll, pitch, yaw = cfg.orientation
        arms: List[Optional[ArmTarget]] = []
        for i, at in enumerate(arm_targets_tmp):
            if at is None:
                arms.append(None)
                continue
            world, extrude, seg, z, plate = at
            e = 0.0
            if extrude and fil_area > 0:
                e = seg * cfg.line_width * cfg.layer_height / fil_area
            arms.append(ArmTarget(round(world[0], 3), round(world[1], 3), round(world[2], 3),
                                  roll, pitch, yaw, extrude, round(e, 4)))
            prev_world[i] = world

        steps.append(MotionStep(dt=dt, tt_angle_deg=math.degrees(phi_target), arms=arms))
        phi = phi_target

    return MotionProgram(steps=steps, config=cfg)


# ----------------------------------------------------------------------------
@dataclass
class PlanStats:
    total_time: float
    arm_peak_speed: List[float]
    arm_avg_speed: List[float]
    arm_path_len: List[float]
    tt_peak_speed: float             # rad/s
    tt_travel: float                 # total |dphi| radians
    tt_reversals: int


def analyze(program: MotionProgram) -> PlanStats:
    cfg = program.config
    n = cfg.num_arms
    prev_world: List[Optional[Tuple[float, float, float]]] = [None] * n
    peak = [0.0] * n
    length = [0.0] * n
    move_time = [0.0] * n
    tt_peak = 0.0
    tt_travel = 0.0
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

    s = SliceSettings(layer_height=2.0, line_width=0.4, wall_count=2,
                      infill_density=0.15, infill_pattern="grid",
                      top_layers=1, bottom_layers=1)
    slc = slice_model(sys.argv[1] if len(sys.argv) > 1 else "cube40.3mf", s)
    print("slice:", slc.summary())

    base = dict(center=(570.0, 0.0, 151.0), z_base=151.8,
                arm_azimuths=(math.radians(135.0),), max_arm_speed=100.0,
                max_tt_speed=1.5, print_speed=20.0, line_width=0.4, layer_height=2.0)

    for use_tt in (False, True):
        cfg = PlannerConfig(num_arms=1, use_turntable=use_tt, **base)
        prog = plan(slc, cfg)
        st = analyze(prog)
        mode = "POLAR (turntable)" if use_tt else "CARTESIAN (fixed)"
        print(f"\n== {mode} ==")
        print(f"  steps={len(prog.steps)}  total_time={st.total_time:.1f}s")
        print(f"  arm avg speed={st.arm_avg_speed[0]:.1f} mm/s  peak={st.arm_peak_speed[0]:.1f} mm/s")
        print(f"  arm path length={st.arm_path_len[0]/1000:.2f} m")
        print(f"  turntable peak={st.tt_peak_speed:.2f} rad/s  travel={st.tt_travel:.1f} rad  reversals={st.tt_reversals}")
