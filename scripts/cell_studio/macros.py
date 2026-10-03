"""The three cell macros.

1. Alignment (AlignmentMacro): a side camera watches both nozzles. The arms
   start apart at a meeting point above the turntable axis, the macro measures
   the camera's pixel scale by moving each arm a known distance, matches the
   LEFT tip's height to the RIGHT tip (right arm = reference), then closes the
   gap in shrinking steps until the tips are `target_gap` apart. Each tip then
   gives the same physical point in its own arm frame, which corrects the left
   arm's turntable centre along the two axes the camera can see (approach
   direction and height). Then confirmation spirals can be printed.

2. Dual-arm vase (DualSpiralJob): two spirals (generators/macros/
   dual_vase_interweave.py builds them with FullControl), one per arm. Each arm
   stays at a fixed angle on its own side of the disc and follows its spiral's
   radius and height as the turntable turns; filament for both tools is sent in
   10° chunks matched to the measured turntable rotation, so Pause/Stop stop the
   flow too. Positions use the (alignment-corrected) turntable centres.

3. George's code as the slicer: handled by the Generators page (slice() contract
   in gen_runner.py); the right arm prints it through the planner.
"""

import json
import math
import os
import threading
import time
from dataclasses import dataclass, asdict, field

import numpy as np

from . import vision
from .geometry import CellGeometry
from .home_config import REPO_ROOT

ALIGN_FILE = os.path.join(REPO_ROOT, "config", "alignment.json")
CHUNK_RAD = math.radians(10.0)
SAFE_ZONE_CENTRE = {"right": -45.0, "left": 135.0}       # calibration.yaml safe_zones


class MacroAbort(Exception):
    pass


def _check(code, what):
    if isinstance(code, (list, tuple)):
        code = code[0]
    if code not in (0, None):
        raise MacroAbort(f"{what}: the arm refused the move (xArm code {code}).")


# ====================================================================== merge calibration
@dataclass
class MergeSettings:
    radius: float = 40.0          # mm, ring radius
    z: float = 0.6                # mm, nozzle height for the test layer (first layer)
    line_width: float = 1.3       # mm
    sweep: float = 2.5            # mm, left arm sweeps from radius + sweep to radius − sweep
    print_speed: float = 0.25     # rad/s of the disc while printing the rings
    scan_speed: float = 0.4       # rad/s while the camera scans the rings
    camera_angle: float = 45.0    # world angle (°) at which the camera looks along the wall
    outward_right: bool = True    # larger radius appears further RIGHT in the image
    right_angle: float = -45.0    # where each arm sits (centre of its safe half of the disc)
    left_angle: float = 135.0
    right_tool: int = 0           # extruder tool feeding the right arm (the other feeds the left)
    flow: float = 1.0
    lift: float = 10.0            # mm to lift an arm after its ring


@dataclass
class MergeResult:
    time: str
    radius: float
    right_angle: float
    left_angle: float
    camera_angle: float
    scale_px_per_mm: float
    merge_offset_mm: float        # add to the left arm's radial offset so its bead lands on the right's
    height_diff_mm: float         # left bead top − right bead top (subtract from Centre Z · left)
    fit_rms_mm: float
    points: int
    radial_offset_left_before: float
    radial_offset_left_after: float
    centre_z_left_before: float
    centre_z_left_after: float
    applied: bool = False
    notes: list = field(default_factory=list)


class MergeCalibration:
    """Find where the left arm's bead lands on the right arm's ('merge point') from the deposited filament.

    1. Right arm prints a reference ring at `radius`.
    2. Left arm prints the same layer on the opposite side while its radius sweeps from
       radius + sweep to radius − sweep over one turn, so it crosses the right bead (overshoot is
       expected: it is how the crossing is found).
    3. Both lift and the disc turns once past the side camera. Each frame shows the bead
       cross-section at one disc angle; from the turntable angle we know what radius offset the
       left arm had when it laid THAT piece of bead (the 'go back' in time to the deposit).
    4. Where the two beads lie apart, their separation is a straight line in the offset; it crosses
       zero at the merge offset. The slope gives px/mm, and the bead tops give the height error.
    """

    def __init__(self, ctl, camera, settings: MergeSettings, vs: vision.VisionSettings, log=print, on_frame=None):
        self.c, self.cam, self.s, self.vs = ctl, camera, settings, vs
        self.geom = CellGeometry(ctl)
        self.log = log
        self.on_frame = on_frame or (lambda img, det: None)

    def _abort_if_stopped(self):
        if self.c.estopped:
            raise MacroAbort("EMERGENCY STOP.")
        if self.c.stop_requested:
            raise MacroAbort("Stopped by user.")

    def _pose(self, side, r, z):
        ang = math.radians(self.s.right_angle if side == "right" else self.s.left_angle)
        r = r + self.c.safe_get(self.c.param_vars[f"radial_offset_{side}"], f"radial_offset_{side}")
        x, y, zz = self.geom.to_arm(side, (r * math.cos(ang), r * math.sin(ang), z))
        return (x, y, zz) + self.c.orientation(side)

    def _move(self, arm, p, speed=40, wait=True):
        self._abort_if_stopped()
        _check(arm.arm.set_position(*p, speed=speed, wait=wait), f"{arm.name} arm")

    def _world_angle(self):
        return math.radians(self.c.turntable.get_angle()) * self.geom.tt_direction

    def _ring(self, side, radius_fn, tool):
        """One turn with one arm, radius_fn(progress 0..1). Returns the world disc angle at the start."""
        c, s = self.c, self.s
        arm = c.left if side == "left" else c.right
        tt, ext = c.turntable, c.extruder
        p0 = self._pose(side, radius_fn(0.0), s.z)
        cur = arm.get_pose() or p0
        safe_z = max(cur[2], p0[2] + 15.0)
        self._move(arm, (cur[0], cur[1], safe_z) + p0[3:])
        self._move(arm, (p0[0], p0[1], safe_z) + p0[3:])
        self._move(arm, p0, speed=10)
        area = math.pi * (c.param_vars["filament_diameter"].get() / 2) ** 2
        per_mm = s.line_width * s.z / area * s.flow
        cmd0 = tt.get_angle()
        phi0 = math.radians(cmd0) * self.geom.tt_direction
        total = 2 * math.pi + (math.radians(10) if side == "right" else 0.0)   # reference ring overlaps its start
        tt.rotate_velocity(math.degrees(s.print_speed))
        done = 0.0
        try:
            while True:
                self._abort_if_stopped()
                prog = math.radians(tt.get_angle() - cmd0)
                if prog >= total:
                    break
                if done - prog < CHUNK_RAD / 2 and done < total:
                    target = min(total, max(done, prog) + CHUNK_RAD)
                    r_mid = radius_fn(min(1.0, (done + target) / 2 / (2 * math.pi)))
                    amount = r_mid * (target - max(done, prog)) * per_mm
                    secs = max((target - max(done, prog)) / s.print_speed, 0.05)
                    ext.extrude(tool, amount, amount / secs, wait=False)
                    done = target
                p = self._pose(side, radius_fn(min(1.0, prog / (2 * math.pi))), s.z)
                arm.move_to(*p[:3], roll=p[3], pitch=p[4], yaw=p[5], speed=20, wait=False)
                time.sleep(0.05)
        finally:
            tt.stop_rotation()
        cp = arm.get_pose() or p0
        self._move(arm, (cp[0], cp[1], cp[2] + s.lift) + p0[3:], speed=20)
        return phi0

    def run(self):
        c, s = self.c, self.s
        for dev, name in ((c.left, "left arm"), (c.right, "right arm"), (c.turntable, "turntable"),
                          (c.extruder, "extruder")):
            if dev is None:
                raise MacroAbort(f"The {name} is not connected.")
        left_tool = 1 - s.right_tool
        self.log("Checking the disc is clear under the camera")
        tt = c.turntable
        cmd0 = tt.get_angle()
        tt.rotate_velocity(math.degrees(max(1.0, s.scan_speed)))
        seen = 0
        try:
            while math.radians(tt.get_angle() - cmd0) < 2 * math.pi:
                self._abort_if_stopped()
                img = self.cam.read()
                det = vision.detect_beads(img, self.vs)
                self.on_frame(img, det)
                seen += 1 if det.ok else 0
        finally:
            tt.stop_rotation()
        if seen >= 3:
            raise MacroAbort("The camera already sees material on the disc at the calibration radius. Clear the "
                             "disc (or change the ring radius) and run the calibration again.")
        self.log("1/3 Right arm: reference ring")
        self._ring("right", lambda f: s.radius, s.right_tool)
        self.log("2/3 Left arm: sweeping across the reference ring")
        phi2 = self._ring("left", lambda f: s.radius + s.sweep - 2 * s.sweep * f, left_tool)
        self.log("3/3 Scanning the rings with the camera")
        frames = []
        tt = c.turntable
        cmd0 = tt.get_angle()
        tt.rotate_velocity(math.degrees(s.scan_speed))
        try:
            while math.radians(tt.get_angle() - cmd0) < 2 * math.pi:
                self._abort_if_stopped()
                a0 = self._world_angle()
                img = self.cam.read()
                a1 = self._world_angle()
                det = vision.detect_beads(img, self.vs)
                self.on_frame(img, det)
                frames.append(((a0 + a1) / 2, det))
        finally:
            tt.stop_rotation()
        return self.analyse(frames, phi2)

    def analyse(self, frames, phi2):
        s = self.s
        d = self.geom.tt_direction
        th_cam = math.radians(s.camera_angle)
        a_l = math.radians(s.left_angle)
        sgn = 1.0 if s.outward_right else -1.0
        samples = []
        singles = 0
        for phi, det in frames:
            if not det.ok:
                continue
            theta_view = th_cam - phi
            q = (a_l - theta_view - phi2) * d          # disc turn since the left ring began, when the
            q = q % (2 * math.pi)                      # left arm laid the bead now under the camera
            frac = q / (2 * math.pi)
            if frac < 0.02 or frac > 0.98:
                continue                               # start/end of the sweep ring: ambiguous
            o = s.sweep - 2 * s.sweep * frac
            if len(det.beads) == 1:
                singles += 1
                samples.append((o, None))
            else:
                samples.append((o, det.beads[:2]))
        two = [(o, b) for o, b in samples if b is not None]
        if len(two) < 12:
            raise MacroAbort(f"The camera saw two separate beads in only {len(two)} frames. Check the camera "
                             "aim (Camera angle), the ROI and the backdrop, and that BOTH arms extruded. If one arm "
                             "printed nothing, the extruder tools may be the other way round (Right arm tool).")
        # Which bead is the right arm's? It stays put in the image while the left's moves from one side
        # of it to the other, so it is the position seen both early (left far outside) and late (inside).
        far_out = [b for o, b in two if o > 0.5 * s.sweep]
        far_in = [b for o, b in two if o < -0.5 * s.sweep]
        if len(far_out) < 3 or len(far_in) < 3:
            raise MacroAbort("The camera did not see both ends of the sweep. Increase the sweep range or "
                             "check the camera angle.")
        A = np.array([bb[0] for b in far_out for bb in b])
        B = np.array([bb[0] for b in far_in for bb in b])
        dA = np.array([np.min(np.abs(B - a)) for a in A])
        u_r = float(np.median(A[dA <= np.median(dA)]))
        u_out = float(np.median([max(b, key=lambda bb: abs(bb[0] - u_r))[0] for b in far_out]))
        u_in = float(np.median([max(b, key=lambda bb: abs(bb[0] - u_r))[0] for b in far_in]))
        sgn = 1.0 if u_out > u_in else -1.0
        notes = []
        if (sgn > 0) != s.outward_right:
            notes.append("The camera shows larger radius to the " + ("right" if sgn > 0 else "left") +
                         ", unlike the 'Larger radius appears to the right' setting. Used what the camera showed; "
                         "update the setting.")
        o_star = 0.0
        for _ in range(4):
            pts = []
            for o, b in two:
                if abs(o - o_star) < 0.6 * s.line_width:
                    continue
                left = max(b[:2], key=lambda bb: abs(bb[0] - u_r))      # the bead away from the right's
                right = min(b[:2], key=lambda bb: abs(bb[0] - u_r))
                pts.append((o, sgn * (left[0] - right[0]), left[1], right[1]))
            if len(pts) < 10:
                raise MacroAbort("Too few clear frames either side of the merge point.")
            arr = np.array(pts)
            k, b0 = np.polyfit(arr[:, 0], arr[:, 1], 1)
            if k <= 0:
                raise MacroAbort("The bead positions do not follow the left arm's sweep. Check that the camera "
                                 "looks along the wall at the configured Camera angle.")
            o_star = -b0 / k
        above = int((arr[:, 0] > o_star).sum())
        below = len(arr) - above
        if min(above, below) < 5:
            raise MacroAbort(f"The sweep did not cross the right arm's bead clearly (offset {o_star:+.2f} mm). "
                             "Increase the sweep range.")
        rms = float(np.std(arr[:, 1] / k - (arr[:, 0] - o_star)))
        dz = float(np.median((arr[:, 3] - arr[:, 2]) / k))      # left top higher → positive
        if rms > 0.1:
            notes.append(f"The fit is loose ({rms:.2f} mm RMS): the beads may be uneven, or the camera "
                         "is not looking straight along the wall.")
        notes.append(f"{singles} frames showed one merged bead, as expected near the merge point.")
        notes.append("The radial correction is valid at these arm positions on the disc "
                     f"(right {s.right_angle:g}°, left {s.left_angle:g}°): print the dual vase from the same angles.")
        rol = self.c.param_vars["radial_offset_left"].get()
        czl = self.c.param_vars["tt_cz_left"].get()
        return MergeResult(time=time.strftime("%Y-%m-%d %H:%M:%S"), radius=s.radius,
                           right_angle=s.right_angle, left_angle=s.left_angle, camera_angle=s.camera_angle,
                           scale_px_per_mm=round(float(k), 2), merge_offset_mm=round(float(o_star), 3),
                           height_diff_mm=round(dz, 3), fit_rms_mm=round(rms, 3), points=len(arr),
                           radial_offset_left_before=round(rol, 3),
                           radial_offset_left_after=round(rol + float(o_star), 3),
                           centre_z_left_before=round(czl, 3), centre_z_left_after=round(czl - dz, 3),
                           notes=notes)


def apply_merge(ctl, res: MergeResult):
    ctl.param_vars["radial_offset_left"].set(res.radial_offset_left_after)
    ctl.param_vars["tt_cz_left"].set(res.centre_z_left_after)
    res.applied = True
    save_alignment(res)


def save_alignment(res):
    hist = load_alignments()
    hist.append(asdict(res))
    os.makedirs(os.path.dirname(ALIGN_FILE), exist_ok=True)
    with open(ALIGN_FILE, "w") as f:
        json.dump(hist[-30:], f, indent=1)


def load_alignments():
    try:
        with open(ALIGN_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


# ====================================================================== dual-arm spirals
@dataclass
class SpiralArm:
    side: str
    theta: np.ndarray        # unwrapped plate angle per point (rad)
    r: np.ndarray
    z: np.ndarray
    e: np.ndarray            # cumulative filament (mm)
    prog: np.ndarray         # progress (rad of turntable rotation) per point, from 0
    end: float
    start_deg: float


def prepare_spirals(tp, filament_diameter=1.75, flow=1.0, disc_radius=150.0):
    """Check a two-arm toolpath and turn each arm's path into functions of turntable progress.
    Returns (arms, direction, problems, warnings)."""
    problems, warnings = [], []
    ext = [p for p in tp.get("paths", []) if p["e"]]
    by_arm = {}
    for p in ext:
        if p.get("arm") in (0, 1):
            by_arm.setdefault(p["arm"], []).append(p)
    if set(by_arm) != {0, 1} or any(len(v) != 1 for v in by_arm.values()):
        problems.append("The dual-arm macro needs exactly one extruding path for arm 0 (right) and one for "
                        "arm 1 (left).")
        return None, 0, problems, warnings
    area = math.pi * (filament_diameter / 2) ** 2
    arms, dirs = {}, {}
    for idx, side in ((0, "right"), (1, "left")):
        a = np.asarray(by_arm[idx][0]["p"], dtype=float)
        th = np.unwrap(np.arctan2(a[:, 1], a[:, 0]))
        r = np.hypot(a[:, 0], a[:, 1])
        d = np.sign(th[-1] - th[0])
        steps = np.diff(th) * d
        if d == 0 or (steps < -1e-6).any():
            problems.append(f"The {side} arm's path does not wind steadily around the turntable axis.")
            continue
        if (r < 2.0).any():
            problems.append(f"The {side} arm's path passes within 2 mm of the axis.")
        if r.max() > disc_radius:
            problems.append(f"The {side} arm's path goes beyond the disc ({r.max():.0f} mm).")
        if a[:, 2].min() <= 0:
            problems.append(f"The {side} arm's path goes down to z = {a[:, 2].min():.2f} mm.")
        seg = np.linalg.norm(np.diff(a[:, :3], axis=0), axis=1)
        e = np.concatenate([[0.0], np.cumsum(seg * a[1:, 3] * a[1:, 4] / area * flow)])
        prog = (th - th[0]) * d
        start = math.degrees(math.atan2(a[0, 1], a[0, 0]))
        arms[side] = SpiralArm(side, th, r, a[:, 2], e, prog, float(prog[-1]), start)
        dirs[side] = d
    if problems:
        return None, 0, problems, warnings
    if dirs["left"] != dirs["right"]:
        problems.append("The two spirals wind in opposite directions; they must turn the same way.")
        return None, 0, problems, warnings
    for side, arm in arms.items():
        off = (arm.start_deg - SAFE_ZONE_CENTRE[side] + 180) % 360 - 180
        if abs(off) > 90:
            warnings.append(f"The {side} arm starts at {arm.start_deg:.0f}°, outside its half of the disc "
                            f"(centred on {SAFE_ZONE_CENTRE[side]:.0f}°).")
    sep = abs((arms["left"].start_deg - arms["right"].start_deg + 180) % 360 - 180)
    if sep < 90:
        problems.append(f"The arms would start only {sep:.0f}° apart on the disc. Start them on opposite sides.")
    if abs(arms["left"].end - arms["right"].end) > math.radians(5):
        warnings.append("The two spirals have different lengths; the shorter arm will lift off early.")
    return arms, int(dirs["right"]), problems, warnings


class DualSpiralJob:
    """Streams two spirals, one per arm, around the turning disc. Runs on a worker thread
    and uses the controller's job flags (printing / paused / stop / E-stop)."""

    def __init__(self, ctl, arms, direction, right_tool=0, name="Dual vase", log=print, on_done=None):
        self.c, self.arms, self.d = ctl, arms, direction
        self.geom = CellGeometry(ctl)
        self.right_tool = right_tool
        self.name = name
        self.log = log
        self.on_done = on_done or (lambda ok, msg: None)

    def start(self):
        c = self.c
        if c._job_running_message(self.name):
            return False
        if not c._require("left", "right", "turntable", "extruder"):
            return False
        c.printing = True
        c.stop_requested = False
        c.paused = False
        c.job_name.set(self.name)
        c.elapsed_time_var.set("00:00")
        c.job_state.set("running")
        threading.Thread(target=self._thread, daemon=True).start()
        return True

    def _pose(self, side, prog):
        arm = self.arms[side]
        p = min(max(prog, 0.0), arm.end)
        r = float(np.interp(p, arm.prog, arm.r)) + self.c.safe_get(self.c.param_vars[f"radial_offset_{side}"],
                                                                 f"radial_offset_{side}")
        z = float(np.interp(p, arm.prog, arm.z))
        ang = math.radians(arm.start_deg)
        x, y, zz = self.geom.to_arm(side, (r * math.cos(ang), r * math.sin(ang), z))
        o = [self.c.safe_get(self.c.offset_vars[f"{side}_{ax}"], f"{side}_{ax}") for ax in self.c.axes]
        R0, P0, Y0 = self.c.orientation(side)
        return (x + o[0], y + o[1], zz + o[2], R0 + o[3], P0 + o[4], Y0 + o[5])

    def _e(self, side, prog):
        arm = self.arms[side]
        return float(np.interp(min(max(prog, 0.0), arm.end), arm.prog, arm.e))

    def _thread(self):
        c = self.c
        left, right, tt, ext = c.left, c.right, c.turntable, c.extruder
        handles = {"left": left, "right": right}
        # A plate point under a fixed arm moves to smaller plate angles as the disc turns CCW, so the
        # disc must turn opposite to the path's winding. calibration.yaml says which way + turns it.
        cmd_sign = -self.d * self.geom.tt_direction
        try:
            # approach: lift, over the start, down. Right (primary) first.
            for side in ("right", "left"):
                arm = handles[side]
                p0 = self._pose(side, 0.0)
                cur = arm.get_pose() or p0
                safe_z = max(cur[2], p0[2] + 15.0)
                for target in ((cur[0], cur[1], safe_z), (p0[0], p0[1], safe_z), p0[:3]):
                    if c.stop_requested:
                        break
                    _check(arm.arm.set_position(*target, p0[3], p0[4], p0[5], speed=50, wait=True), f"{side} arm")
            if c.stop_requested:
                raise MacroAbort("Stopped before printing started.")
            phi0 = math.radians(tt.get_angle())
            speed = c.safe_get(c.turntable_speed_var, "turntable_speed")
            tt.rotate_velocity(math.degrees(speed) * cmd_sign)
            done = {s: False for s in self.arms}
            extruded = 0.0
            end = max(a.end for a in self.arms.values())
            t0 = time.time()
            last = 0.0
            while not c.stop_requested:
                if c.paused:
                    tt.stop_rotation()
                    while c.paused and not c.stop_requested:
                        time.sleep(0.1)
                    if c.stop_requested:
                        break
                    tt.rotate_velocity(math.degrees(speed) * cmd_sign)
                new_speed = c.safe_get(c.turntable_speed_var, "turntable_speed")
                if new_speed != speed:
                    speed = new_speed
                    tt.rotate_velocity(math.degrees(speed) * cmd_sign)
                if time.time() - last < 0.1:
                    time.sleep(0.01)
                    continue
                last = time.time()
                prog = (math.radians(tt.get_angle()) - phi0) * cmd_sign
                if prog >= end:
                    break
                # filament for the next 10°, both tools in one move, at the rate the disc is turning
                if speed > 0 and extruded - prog < CHUNK_RAD / 2:
                    target = min(end, max(prog, extruded) + CHUNK_RAD)
                    secs = max((target - max(prog, extruded)) / speed, 0.05)
                    amt = {s: self._e(s, target) - self._e(s, extruded) for s in self.arms}
                    l0, l1 = (amt["right"], amt["left"]) if self.right_tool == 0 else (amt["left"], amt["right"])
                    ext.extrude_sync(l0, max(l0 / secs, 1e-3), l1, max(l1 / secs, 1e-3), wait=False)
                    extruded = target
                for side, arm in handles.items():
                    if done[side]:
                        continue
                    if prog >= self.arms[side].end:
                        done[side] = True
                        p = arm.get_pose()
                        if p:
                            arm.arm.set_position(p[0], p[1], p[2] + 3, p[3], p[4], p[5], speed=20, wait=False)
                        continue
                    pose = self._pose(side, prog)
                    rps = speed / (2 * math.pi)
                    need = 2 * math.pi * float(np.interp(prog, self.arms[side].prog, self.arms[side].r)) * rps
                    arm.move_to(*pose[:3], roll=pose[3], pitch=pose[4], yaw=pose[5],
                                speed=max(c.base_arm_speed_var.get(), need * 1.5), wait=False)
                el = time.time() - t0
                c.ui.post(lambda e=el: c.elapsed_time_var.set(f"{int(e) // 60:02d}:{int(e) % 60:02d}"))
                if prog > 0:
                    rem = el / prog * (end - prog)
                    c.ui.post(lambda r=rem: c.remaining_time_var.set(f"{int(r) // 60:02d}:{int(r) % 60:02d}"))
            if c.estopped:
                c._job_finished()
                return
            tt.stop_rotation()
            for side, arm in handles.items():
                p = arm.get_pose()
                if p:
                    arm.arm.set_position(p[0], p[1], p[2] + 10, p[3], p[4], p[5], speed=30, wait=True)
            stopped = c.stop_requested
            c._job_finished()
            self.log(f"{self.name}: {'stopped' if stopped else 'finished'}.")
            self.on_done(not stopped, "stopped" if stopped else "finished")
        except Exception as e:
            c._job_finished()
            if c.estopped:
                return
            try:
                tt.stop_rotation()
            except Exception:
                pass
            msg = str(e)
            self.log(f"{self.name} failed: {msg}")
            c.ui.error(self.name, msg)
            self.on_done(False, msg)


def sim_bead_camera_for(ctl, settings: MergeSettings):
    """A simulated side camera at settings.camera_angle that shows the beads the simulated arms
    really laid down (hidden frame errors and the real tool wiring included)."""
    from .sim_hardware import WORLD
    geom = CellGeometry(ctl)

    def deposits():
        now = time.time()
        theta_view = math.radians(settings.camera_angle) - math.radians(WORLD.angle_at(now)) * geom.tt_direction
        out = []
        for side in ("left", "right"):
            arm = ctl.left if side == "left" else ctl.right
            if arm is None or not hasattr(arm, "true_centre"):
                continue
            tool = WORLD.tool_for_arm[side]
            hist = WORLD.poses.get(side, [])
            for i, (t0, pose) in enumerate(hist):
                t1 = hist[i + 1][0] if i + 1 < len(hist) else now
                if t1 <= t0:
                    continue
                w = geom.to_world(side, pose, centre=arm.true_centre)
                if not 0 < w[2] < 3.0 or WORLD.extruding(tool, t0, t1) < 0.5:
                    continue
                th = math.atan2(w[1], w[0])
                a0 = th - math.radians(WORLD.angle_at(t0)) * geom.tt_direction
                a1 = th - math.radians(WORLD.angle_at(t1)) * geom.tt_direction
                lo, hi = min(a0, a1), max(a0, a1)
                k = math.floor((theta_view - lo) / (2 * math.pi))
                tv = theta_view - k * 2 * math.pi
                if lo <= tv <= hi or lo <= tv - 2 * math.pi <= hi:
                    out.append((math.hypot(w[0], w[1]), w[2], settings.line_width))
        return out
    return vision.SimBeadCamera(deposits, settings.radius)
