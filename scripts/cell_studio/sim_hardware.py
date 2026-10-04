"""Simulated hardware with the same interface the panel uses on the real cell.

Tick "Simulated hardware" in Connections to rehearse a job, test the UI or
check a new home position without anything moving. Motion is instantaneous,
the turntable integrates its commanded velocity, heaters ramp towards target.

The simulator also behaves like the real devices when things go wrong, so the
scenario tests in tests/test_cell_studio_scenarios.py can exercise faults:

* an xArm in E-stop rejects motion with a non-zero code until it is reconnected
  (the real SDK returns codes rather than raising);
* the turntable raises "not connected" after disconnect, as TurntableController does;
* extrude_sync rejects non-positive speeds, as ExtruderController does;
* FAULTS lets a test drop a device mid-job or refuse a connection.

Every command is recorded in LOG so tests can check what the machine was told.
"""

import logging
import threading
import time

logger = logging.getLogger("cell_studio.sim")

# Fault injection, set by tests. Keys:
#   refuse_connect: set of device names ("left", "right", "turntable") that fail to connect
#   drop_after_moves: {"left": n} -> that arm raises after n more moves
#   arm_code: {"right": 9} -> set_position returns this code (xArm error) without raising
FAULTS = {"refuse_connect": set(), "drop_after_moves": {}, "arm_code": {},
          "frame_error": {}}     # {"left": (dx, dy, dz)}: where that arm's frame REALLY is vs its calibration
LOG = []          # (time, device, command, detail)
_log_lock = threading.Lock()


class SimWorld:
    """Time history of the simulated cell (disc rotation, nozzle positions, extrusion), so a
    simulated camera can show the beads that were actually laid down."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.tt = []                                   # (t, angle_deg, vel_dps)
        self.poses = {"left": [], "right": []}         # (t, pose6) in the arm's own frame
        self.chunks = []                               # (t0, t1, tool, length)
        self.tool_for_arm = {"right": 0, "left": 1}    # how the cell is really wired

    def angle_at(self, t):
        a, last_t, vel = 0.0, None, 0.0
        for (te, ang, v) in self.tt:
            if te > t:
                break
            a, last_t, vel = ang, te, v
        return a if last_t is None else a + vel * (t - last_t)

    def extruding(self, tool, t0, t1):
        """Fraction of [t0, t1] during which `tool` was pushing filament."""
        tot = 0.0
        for (c0, c1, tl, ln) in self.chunks:
            if tl == tool and ln > 0 and c1 > t0 and c0 < t1:
                tot += min(c1, t1) - max(c0, t0)
        return tot / max(t1 - t0, 1e-9)


WORLD = SimWorld()


def record(dev, cmd, detail=None):
    with _log_lock:
        LOG.append((time.time(), dev, cmd, detail))


def reset():
    FAULTS["refuse_connect"] = set()
    FAULTS["drop_after_moves"] = {}
    FAULTS["arm_code"] = {}
    FAULTS["frame_error"] = {}
    WORLD.reset()
    with _log_lock:
        LOG.clear()


class _SimXArm:
    """Stand in for xarm.wrapper.XArmAPI (only the calls the panel makes)."""

    def __init__(self, owner):
        self.o = owner

    def _gate(self, what):
        o = self.o
        n = FAULTS["drop_after_moves"].get(o.name)
        if n is not None:
            if n <= 0:
                raise ConnectionError(f"{o.name} arm: connection lost")
            FAULTS["drop_after_moves"][o.name] = n - 1
        if o.estopped:
            record(o.name, what + "_rejected", "estop")
            return 1   # xArm reports an error code while stopped
        code = FAULTS["arm_code"].get(o.name, 0)
        if code:
            record(o.name, what + "_rejected", code)
        return code

    def set_position(self, x=None, y=None, z=None, roll=None, pitch=None, yaw=None,
                     speed=None, wait=False, radius=None, **_):
        code = self._gate("move")
        if code:
            return code
        cur = list(self.o._pose)
        for i, v in enumerate((x, y, z, roll, pitch, yaw)):
            if v is not None:
                cur[i] = float(v)
        self.o._pose = cur
        WORLD.poses.setdefault(self.o.name, []).append((time.time(), tuple(cur)))
        record(self.o.name, "move", (tuple(round(c, 2) for c in cur), speed))
        if wait:
            time.sleep(0.01)
        return 0

    def set_servo_angle(self, angle=None, speed=None, is_radian=False, wait=False, **_):
        code = self._gate("joint_move")
        if code:
            return code
        self.o._joints = [float(a) for a in angle]
        j = self.o._joints
        self.o._pose = _flange_pose(j, self.o.name)
        record(self.o.name, "joint_move", (tuple(self.o._joints), speed))
        if wait:
            time.sleep(0.01)
        return 0

    def get_position(self):
        return 0, list(self.o._pose)

    def get_servo_angle(self, is_radian=False):
        return 0, list(self.o._joints)

    def get_state(self):
        return 0, 4 if self.o.estopped else 2

    def emergency_stop(self):
        self.o.estopped = True
        return 0

    def disconnect(self):
        return 0


def _flange_pose(joints, name=None):
    """Where the real UF850 puts its NOZZLE for these joints: flange from the joint chain
    (cell_studio.arm_model) plus the tool's TCP offset, as a controller with set_tcp_offset
    reports it."""
    try:
        import numpy as np
        from .arm_model import fk, matrix_to_pose
        from . import tool as toolmod
        T = fk(np.radians(joints))[-1]
        if name in ("left", "right"):
            off = np.array(toolmod.tcp(toolmod.load(), name))
            T = T.copy()
            T[:3, 3] = T[:3, 3] + T[:3, :3] @ off
        return [round(v, 3) for v in matrix_to_pose(T)]
    except Exception:
        return [400.0, 174.0, 250.0, 180.0, 45.0, 20.0]


class SimArm:
    def __init__(self, ip, name):
        self.ip, self.name = ip, name
        try:                                   # start at that arm's home (its own side of the disc)
            from .home_config import DEFAULTS as _HOME
            self._joints = list(_HOME[name]["joints"])
        except Exception:
            self._joints = [0.0, -45.0, -45.0, 0.0, 90.0, 0.0]
        self._pose = _flange_pose(self._joints, name)
        self.arm = None
        self.estopped = False
        self.online = False

    def connect(self):
        if self.name in FAULTS["refuse_connect"]:
            raise ConnectionError(f"{self.name} arm at {self.ip} did not respond")
        self.arm = _SimXArm(self)
        self.estopped = False       # a fresh connection clears the stop (motion_enable + set_state 0)
        self.online = True
        record(self.name, "connect")
        logger.info("[SIM] %s arm connected", self.name)

    def home(self, wait=True):
        code = self.arm._gate("factory_home")
        if code:
            raise RuntimeError(f"{self.name} home failed (code {code})")
        self._joints = [0.0] * 6
        self._pose = [207.0, 0.0, 112.0, 180.0, 0.0, 0.0]
        record(self.name, "factory_home")

    def move_to(self, x, y, z, roll=180, pitch=0, yaw=0, speed=None, wait=False):
        # the real ArmController.move_to ignores the return code, so do we
        self.arm.set_position(x, y, z, roll, pitch, yaw, speed=speed, wait=wait)

    def get_pose(self):
        if FAULTS["drop_after_moves"].get(self.name) == 0:
            raise ConnectionError(f"{self.name} arm: connection lost")
        return list(self._pose)

    def get_joints(self):
        return list(self._joints)

    def emergency_stop(self):
        if self.arm:
            self.arm.emergency_stop()
        record(self.name, "estop")
        logger.warning("[SIM] %s EMERGENCY STOP", self.name)

    def disconnect(self):
        self.online = False
        record(self.name, "disconnect")
        logger.info("[SIM] %s arm disconnected", self.name)


class SimTurntable:
    def __init__(self, host=None, axis="U"):
        self._lock = threading.Lock()
        self._angle = 0.0
        self._vel = 0.0
        self._t = time.time()
        self._connected = False

    def _advance(self):
        now = time.time()
        self._angle += self._vel * (now - self._t)
        self._t = now

    def connect(self):
        if "turntable" in FAULTS["refuse_connect"]:
            raise ConnectionError("Turntable controller did not respond")
        self._connected = True
        record("turntable", "connect")

    def disconnect(self):
        if not self._connected:
            return
        with self._lock:
            self._advance()
            self._vel = 0.0         # the real controller disables the axis
        self._connected = False
        record("turntable", "disconnect")

    def get_angle(self):
        with self._lock:
            self._advance()
            return self._angle

    @property
    def velocity(self):
        return self._vel

    def rotate_velocity(self, speed_dps):
        if not self._connected:
            raise RuntimeError("Turntable not connected.")
        with self._lock:
            self._advance()
            if float(speed_dps) != self._vel:
                record("turntable", "velocity", round(float(speed_dps), 3))
                WORLD.tt.append((self._t, self._angle, float(speed_dps)))
            self._vel = float(speed_dps)

    def stop_rotation(self):
        self.rotate_velocity(0.0)


class SimExtruder:
    def __init__(self, host=None, port=None):
        self._target = [0.0, 0.0]
        self._temp = [22.0, 22.0]
        self._t = time.time()
        self.busy_until = 0.0       # queued extrusion finishes at this time
        self.gcode = []

    def _advance(self):
        now = time.time()
        dt = now - self._t
        self._t = now
        for i in (0, 1):
            self._temp[i] += (self._target[i] - self._temp[i]) * min(1.0, dt * 0.8)

    def send_gcode(self, script, **_):
        self.gcode.append(script)
        record("extruder", "gcode", script)
        if script.strip().upper().startswith("CANCEL_PRINT"):
            self.busy_until = time.time()   # Klipper aborts the queued move
        return {}

    def set_temperature(self, tool, temp, wait=False):
        self._target[tool] = float(temp)
        record("extruder", "set_temp", (tool, temp))

    def heat_and_wait(self, tool, temp, **_):
        self.set_temperature(tool, temp)
        time.sleep(0.05)
        self._temp[tool] = float(temp)

    def _queue(self, seconds, amounts=None):
        start = max(time.time(), self.busy_until)
        self.busy_until = start + seconds
        for tool, ln in (amounts or {}).items():
            WORLD.chunks.append((start, start + seconds, tool, ln))

    def extrude(self, tool, length_mm, feedrate_mm_s, wait=False):
        if tool not in (0, 1):
            raise ValueError(f"Invalid tool index: {tool}")
        record("extruder", "extrude", (tool, round(length_mm, 3), feedrate_mm_s))
        if feedrate_mm_s > 0:
            self._queue(abs(length_mm) / feedrate_mm_s, {tool: length_mm})

    def extrude_sync(self, l0, s0, l1, s1, wait=False):
        if s0 <= 0 or s1 <= 0:
            raise ValueError("Speeds must be positive.")
        record("extruder", "extrude_sync", (round(l0, 2), s0, round(l1, 2), s1))
        self._queue(max(abs(l0) / s0, abs(l1) / s1), {0: l0, 1: l1})

    def extruding(self):
        return time.time() < self.busy_until

    def get_printer_status(self):
        self._advance()
        return {"extruder": {"temperature": self._temp[0]},
                "heater_bed": {"temperature": self._temp[1]}}

    def disable_all_heaters(self):
        self._target = [0.0, 0.0]
        record("extruder", "heaters_off")
