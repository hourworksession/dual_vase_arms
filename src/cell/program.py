"""Speed-and-time programs for the cell: G-code reduced to "this fast, for this long".

A Program is a set of parallel TRACKS. Each track is a list of SEGMENTS played back to back in
time. When a segment's time is up the player applies the next one.

    bed track       segment = (deg_per_s, seconds)        the disc turns at that speed for that long
    ext0 / ext1     segment = (mm_per_s, seconds)         filament rate for that long (0 = off)
    arm0 / arm1     segment = ((x, y, z), seconds)        the nozzle reaches that point (in the arm's
                                                          own frame) by the end of the segment, so its
                                                          speed is distance / seconds

Tracks are independent: a cylinder is one long bed segment, one long extruder segment, and an arm
track of small Z steps. A rounded square with the bed as master is a bed track whose speed changes
every couple of degrees. Anything can be previewed before it is run, because a program is just data.

Players: `Player` drives a connected Cell (real or simulated) from a Program; `simulate()` walks a
Program in pure maths and returns where every device is at each tick, for previews and tests.

Two ways to drive the arms (Player(arm_mode=...)):
    "position"  (default) each arm segment is one queued set_position with a blend radius, 20 Hz.
                Robust, but each call is a planned move, so the path is a chain of short moves.
    "servo"     xArm servo mode (set_mode(1), set_servo_cartesian): the Player interpolates along
                the segment and streams a pose every 10 ms; the controller goes straight to each
                one with no planning, so the motion is as smooth as the stream. Needs firmware
                1.6 or later. The Player refuses a segment that would ask for more than
                SERVO_MAX_STEP mm between two poses, and puts the arm back in position mode at
                the end or on abort.
"""
from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, field

TICK = 0.05                         # player period in position mode, s (20 Hz)
SERVO_TICK = 0.01                   # player period in servo mode, s (100 Hz)
ARM_BLEND = 2.0                     # blend radius for queued arm moves, mm (never more than half a segment)
SERVO_MAX_STEP = 2.0                # mm the nozzle may be asked to jump between two servo poses
APPROACH_MM = 20.0                  # a first segment farther than this gets a planned approach first
EXTRUDE_TIMEOUT_MARGIN = 1.0        # EXTRUDE_ON TIMEOUT = segment time + this, so the watchdog never cuts a segment
KINDS = ("bed", "ext0", "ext1", "arm0", "arm1")


@dataclass
class Seg:
    value: object                   # float for bed/ext, (x, y, z) for arms
    dur: float                      # seconds

    def end_of(self, t0):
        return t0 + self.dur


@dataclass
class Track:
    kind: str                       # one of KINDS
    segs: list = field(default_factory=list)

    def add(self, value, dur):
        if dur <= 0:
            raise ValueError(f"{self.kind}: a segment needs a positive time (got {dur})")
        self.segs.append(Seg(value, float(dur)))
        return self

    def total(self):
        return sum(s.dur for s in self.segs)

    def at(self, t):
        """(segment index, segment, time into it) at time t, or (None, None, None) after the end."""
        acc = 0.0
        for i, s in enumerate(self.segs):
            if t < acc + s.dur:
                return i, s, t - acc
            acc += s.dur
        return None, None, None


@dataclass
class Program:
    name: str = "untitled"
    notes: str = ""
    tracks: dict = field(default_factory=dict)     # kind -> Track

    def track(self, kind):
        if kind not in KINDS:
            raise ValueError(f"unknown track {kind!r}; use one of {KINDS}")
        if kind not in self.tracks:
            self.tracks[kind] = Track(kind)
        return self.tracks[kind]

    def total(self):
        return max((t.total() for t in self.tracks.values()), default=0.0)

    def to_dict(self):
        return {"name": self.name, "notes": self.notes,
                "tracks": {k: [{"value": s.value, "dur": s.dur} for s in t.segs] for k, t in self.tracks.items()}}

    def to_json(self):
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d):
        p = cls(d.get("name", "untitled"), d.get("notes", ""))
        for k, segs in d.get("tracks", {}).items():
            tr = p.track(k)
            for s in segs:
                v = s["value"]
                tr.add(tuple(v) if isinstance(v, (list, tuple)) else float(v), s["dur"])
        return p

    def summary(self):
        lines = [f"{self.name}: {self.total():.1f} s"]
        for k in KINDS:
            if k in self.tracks:
                t = self.tracks[k]
                lines.append(f"  {k}: {len(t.segs)} segments, {t.total():.1f} s")
        return "\n".join(lines)


# ----------------------------------------------------------------------
def simulate(prog, tick=TICK, start_pose=None):
    """Walk a Program in pure maths. Returns a list of rows, one per tick:
    {t, bed_deg, bed_dps, ext0, ext1, arm0: (x, y, z) or None, arm1: ...}.
    Arms move in a straight line to each segment's target; the bed integrates its speed."""
    start_pose = start_pose or {}
    rows, bed = [], 0.0
    arm_from = {k: start_pose.get(k) for k in ("arm0", "arm1")}
    arm_seg_start = {k: None for k in ("arm0", "arm1")}
    n = int(math.ceil(prog.total() / tick)) + 1
    for i in range(n):
        t = i * tick
        row = {"t": round(t, 3), "bed_deg": bed, "bed_dps": 0.0, "ext0": 0.0, "ext1": 0.0, "arm0": None, "arm1": None}
        if "bed" in prog.tracks:
            _, s, _ = prog.tracks["bed"].at(t)
            if s is not None:
                row["bed_dps"] = s.value
                bed += s.value * tick
        for k in ("ext0", "ext1"):
            if k in prog.tracks:
                _, s, _ = prog.tracks[k].at(t)
                row[k] = s.value if s is not None else 0.0
        for k in ("arm0", "arm1"):
            if k in prog.tracks:
                idx, s, into = prog.tracks[k].at(t)
                if s is not None:
                    if arm_seg_start[k] != idx:
                        arm_seg_start[k] = idx
                        arm_from[k] = arm_from[k] or s.value
                    f = min(1.0, into / s.dur)
                    a, b = arm_from[k], s.value
                    row[k] = tuple(a[j] + (b[j] - a[j]) * f for j in range(3))
                    if f >= 1.0 - 1e-9:
                        arm_from[k] = b
                else:
                    row[k] = arm_from[k]
                if row[k] is not None and idx is not None and into + tick >= s.dur:
                    arm_from[k] = s.value
        rows.append(row)
    return rows


# ----------------------------------------------------------------------
class Player:
    """Plays a Program on a Cell (cell.runs.cylinder.Cell, real or simulated) in a thread.

    Every TICK: for each track, if the segment changed since the last tick, apply it:
      bed   -> tt.rotate_velocity(speed)              (0 -> stop_rotation)
      extN  -> EXTRUDE_ON T=N RATE=rate TIMEOUT=dur+1 (0 -> EXTRUDE_OFF T=N); re-sent every 2 s for the watchdog
      armN  -> set_position(target, speed = distance / dur, wait=False, radius=ARM_BLEND)
    At the end: bed stopped, extruders off. stop() aborts the same way at once.
    Progress and the last applied values are in .status()."""

    def __init__(self, cell, prog, z_floor=None, on_log=None, arm_mode="position", tick=None, approach_z=None):
        if arm_mode not in ("position", "servo"):
            raise ValueError("arm_mode must be 'position' or 'servo'")
        self.cell, self.prog = cell, prog
        self.arm_mode = arm_mode
        self.tick = tick or (SERVO_TICK if arm_mode == "servo" else TICK)
        self.z_floor = z_floor or {}                 # {"arm0": z0, "arm1": z0}: refuse targets below these
        self.log = on_log or (lambda s: None)
        self._seg_from = {}                          # servo: where each arm segment started
        self._servo_arms = []                        # tools switched into servo mode
        self.approach_z = approach_z                 # safe height for the approach to a far first segment
        self._stop = threading.Event()
        self._thread = None
        self.t0 = None
        self.error = None
        self.applied = {k: None for k in KINDS}       # last value applied per track
        self.idx = {k: None for k in KINDS}
        self._ext_sent_at = {}

    # -- public
    def start(self):
        if self._thread and self._thread.is_alive():
            raise RuntimeError("already running")
        self._stop.clear()
        self.error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._all_off()

    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def status(self):
        t = (time.monotonic() - self.t0) if (self.t0 and self.running()) else (self.prog.total() if self.t0 else 0.0)
        return {"running": self.running(), "t": round(min(t, self.prog.total()), 2), "total": round(self.prog.total(), 2),
                "applied": {k: v for k, v in self.applied.items() if v is not None}, "error": self.error}

    # -- internals
    def _tool(self, kind):
        return int(kind[-1])

    def _all_off(self):
        for fn in (lambda: self.cell.gcode("EXTRUDE_OFF"), self.cell.tt.stop_rotation):
            try:
                fn()
            except Exception as ex:
                self.log(f"stop: {ex}")
        for t in list(self._servo_arms):                 # back to position mode, always
            try:
                a = self.cell.arms[t].arm
                a.set_mode(0)
                a.set_state(0)
            except Exception as ex:
                self.log(f"arm{t}: could not leave servo mode: {ex}")
        self._servo_arms = []

    def _approach(self):
        """An arm whose first segment is far from where it is now (more than APPROACH_MM) is taken there
        first with planned, waited moves: up to approach_z, across, down. Programs are written as if the
        arm is already at their first point, as the scripts' own approach does."""
        for kind, track in self.prog.tracks.items():
            if not kind.startswith("arm") or not track.segs:
                continue
            t = self._tool(kind)
            cur = tuple(self.cell.pose(t)[:3])
            first = track.segs[0].value
            if math.dist(cur, first) <= APPROACH_MM:
                continue
            r, p, yw = self.cell.orient[t]
            a = self.cell.arms[t].arm
            if self.approach_z is not None:
                up = max(cur[2], first[2], self.approach_z)
                a.set_position(cur[0], cur[1], up, r, p, yw, speed=60, wait=True)
                a.set_position(first[0], first[1], up, r, p, yw, speed=60, wait=True)
            a.set_position(first[0], first[1], first[2], r, p, yw, speed=20, wait=True)
            self.log(f"{kind}: approached the start ({math.dist(cur, first):.0f} mm away)")

    def _servo_on(self):
        for kind in self.prog.tracks:
            if not kind.startswith("arm"):
                continue
            t = self._tool(kind)
            a = self.cell.arms[t].arm
            if not hasattr(a, "set_servo_cartesian"):
                raise RuntimeError(f"arm{t}: this SDK/firmware has no set_servo_cartesian (needs 1.6+)")
            a.set_mode(1)
            a.set_state(0)
            time.sleep(0.1)
            self._servo_arms.append(t)
            self._seg_from[kind] = tuple(self.cell.pose(t)[:3])

    def _servo_pose(self, kind, seg, idx, into):
        """Pose on the current segment at time `into`, and the check that the step is small."""
        if self.idx[kind] != idx:                         # segment changed: it starts where the last one ended
            self.idx[kind] = idx
            self.applied[kind] = seg.value
        a, b = self._seg_from[kind], seg.value
        f = min(1.0, into / seg.dur)
        pt = tuple(a[j] + (b[j] - a[j]) * f for j in range(3))
        step = math.dist(a, b) * self.tick / seg.dur
        if step > SERVO_MAX_STEP:
            raise RuntimeError(f"{kind}: segment {idx} asks for {step:.2f} mm per {self.tick * 1000:.0f} ms tick "
                               f"(limit {SERVO_MAX_STEP} mm): slow it down or split it")
        return pt, f

    def _apply_servo(self, kind, pt):
        t = self._tool(kind)
        floor = self.z_floor.get(kind)
        if floor is not None and pt[2] < floor - 1e-6:
            raise RuntimeError(f"{kind}: pose Z {pt[2]:.2f} is below its Z0 {floor:.2f}")
        r, p, yw = self.cell.orient[t]
        self.cell.arms[t].arm.set_servo_cartesian([pt[0], pt[1], pt[2], r, p, yw], speed=100, mvacc=2000)

    def _apply_bed(self, v):
        if abs(v) < 1e-9:
            self.cell.tt.stop_rotation()
        else:
            self.cell.tt.rotate_velocity(float(v))

    def _apply_ext(self, kind, v, dur):
        t = self._tool(kind)
        if v <= 0:
            self.cell.gcode(f"EXTRUDE_OFF T={t}")
        else:
            self.cell.gcode(f"EXTRUDE_ON T={t} RATE={v:.3f} TIMEOUT={dur + EXTRUDE_TIMEOUT_MARGIN:.1f}")
        self._ext_sent_at[kind] = time.monotonic()

    def _apply_arm(self, kind, target, dur):
        t = self._tool(kind)
        x, y, z = target
        floor = self.z_floor.get(kind)
        if floor is not None and z < floor - 1e-6:
            raise RuntimeError(f"{kind}: target Z {z:.2f} is below its Z0 {floor:.2f}")
        cur = self.cell.pose(t)[:3]
        dist = math.dist(cur, target)
        speed = max(1.0, min(100.0, dist / max(dur, 1e-3)))
        r, p, yw = self.cell.orient[t]
        blend = min(ARM_BLEND, dist / 2)                 # never round off more than half the segment
        self.cell.arms[t].arm.set_position(x, y, z, r, p, yw, speed=speed, wait=False, radius=blend)

    def _run(self):
        total = self.prog.total()
        n = 0
        try:
            self._approach()
            if self.arm_mode == "servo":
                self._servo_on()
            self.t0 = time.monotonic()
            while not self._stop.is_set():
                t = time.monotonic() - self.t0
                if t >= total:
                    break
                for kind, track in self.prog.tracks.items():
                    idx, seg, into = track.at(t)
                    if idx is None:                                   # this track has finished
                        if self.idx[kind] is not None:
                            self.idx[kind] = None
                            if kind == "bed":
                                self._apply_bed(0.0)
                            elif kind.startswith("ext"):
                                self._apply_ext(kind, 0.0, 0.0)
                        continue
                    if kind.startswith("arm") and self.arm_mode == "servo":
                        prev_idx = self.idx[kind]
                        if prev_idx is not None and prev_idx != idx:  # new segment starts where the last one ended
                            self._seg_from[kind] = self.prog.tracks[kind].segs[prev_idx].value
                        pt, f = self._servo_pose(kind, seg, idx, into)
                        self._apply_servo(kind, pt)
                        continue
                    if idx != self.idx[kind]:                        # new segment: apply it
                        self.idx[kind] = idx
                        self.applied[kind] = seg.value
                        if kind == "bed":
                            self._apply_bed(seg.value)
                        elif kind.startswith("ext"):
                            self._apply_ext(kind, seg.value, seg.dur)
                        else:
                            self._apply_arm(kind, seg.value, seg.dur)
                    elif kind.startswith("ext") and seg.value > 0 and time.monotonic() - self._ext_sent_at.get(kind, 0) > 2.0:
                        self._apply_ext(kind, seg.value, seg.dur - into)   # keep the watchdog fed
                n += 1
                delay = self.t0 + n * self.tick - time.monotonic()   # absolute schedule: no drift at 100 Hz
                if delay > 0:
                    time.sleep(delay)
        except Exception as ex:
            self.error = str(ex)
            self.log(f"program stopped: {ex}")
        finally:
            self._all_off()
