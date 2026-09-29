"""Simulated hardware with the same interface the panel uses on the real cell.

Tick "Simulated hardware" in Connections to rehearse a job, test the UI or
check a new home position without anything moving. Motion is instantaneous,
the turntable integrates its commanded velocity, heaters ramp towards target.
"""

import logging
import math
import threading
import time

logger = logging.getLogger("cell_studio.sim")


class _SimXArm:
    """Stand in for xarm.wrapper.XArmAPI (only the calls the panel makes)."""

    def __init__(self, owner):
        self.o = owner

    def set_position(self, x=None, y=None, z=None, roll=None, pitch=None, yaw=None,
                     speed=None, wait=False, radius=None, **_):
        cur = list(self.o._pose)
        for i, v in enumerate((x, y, z, roll, pitch, yaw)):
            if v is not None:
                cur[i] = float(v)
        self.o._pose = cur
        if wait:
            time.sleep(0.05)
        return 0

    def set_servo_angle(self, angle=None, speed=None, is_radian=False, wait=False, **_):
        self.o._joints = [float(a) for a in angle]
        # crude forward kinematics stand in so the pose readout changes visibly
        j = self.o._joints
        self.o._pose = [400 + 2 * j[0], 170 + j[1], 250 + j[2], 180.0, 45.0, 20 + j[5]]
        if wait:
            time.sleep(0.05)
        return 0

    def get_position(self):
        return 0, list(self.o._pose)

    def get_servo_angle(self, is_radian=False):
        return 0, list(self.o._joints)

    def get_state(self):
        return 0, 2

    def emergency_stop(self):
        return 0

    def disconnect(self):
        return 0


class SimArm:
    def __init__(self, ip, name):
        self.ip, self.name = ip, name
        self._pose = [400.0, 174.0, 250.0, 180.0, 45.0, 20.0]
        self._joints = [0.0, -45.0, -45.0, 0.0, 90.0, 0.0]
        self.arm = None

    def connect(self):
        self.arm = _SimXArm(self)
        logger.info("[SIM] %s arm connected", self.name)

    def home(self, wait=True):
        self._joints = [0.0] * 6
        self._pose = [207.0, 0.0, 112.0, 180.0, 0.0, 0.0]
        logger.info("[SIM] %s factory home", self.name)

    def move_to(self, x, y, z, roll=180, pitch=0, yaw=0, speed=None, wait=False):
        self.arm.set_position(x, y, z, roll, pitch, yaw, speed=speed, wait=wait)

    def get_pose(self):
        return list(self._pose)

    def get_joints(self):
        return list(self._joints)

    def emergency_stop(self):
        logger.warning("[SIM] %s EMERGENCY STOP", self.name)

    def disconnect(self):
        logger.info("[SIM] %s arm disconnected", self.name)


class SimTurntable:
    def __init__(self, host=None, axis="U"):
        self._lock = threading.Lock()
        self._angle = 0.0
        self._vel = 0.0
        self._t = time.time()

    def _advance(self):
        now = time.time()
        self._angle += self._vel * (now - self._t)
        self._t = now

    def connect(self):
        logger.info("[SIM] turntable connected")

    def disconnect(self):
        with self._lock:
            self._advance()
            self._vel = 0.0

    def get_angle(self):
        with self._lock:
            self._advance()
            return self._angle

    def rotate_velocity(self, speed_dps):
        with self._lock:
            self._advance()
            self._vel = float(speed_dps)

    def stop_rotation(self):
        self.rotate_velocity(0.0)


class SimExtruder:
    def __init__(self, host=None, port=None):
        self._target = [0.0, 0.0]
        self._temp = [22.0, 22.0]
        self._t = time.time()

    def _advance(self):
        now = time.time()
        dt = now - self._t
        self._t = now
        for i in (0, 1):
            self._temp[i] += (self._target[i] - self._temp[i]) * min(1.0, dt * 0.8)

    def send_gcode(self, script, **_):
        logger.info("[SIM] gcode: %s", script)
        return {}

    def set_temperature(self, tool, temp, wait=False):
        self._target[tool] = float(temp)

    def heat_and_wait(self, tool, temp, **_):
        self.set_temperature(tool, temp)
        t0 = time.time()
        while time.time() - t0 < 3:
            self._advance()
            time.sleep(0.2)
        self._temp[tool] = float(temp)

    def extrude(self, tool, length_mm, feedrate_mm_s, wait=False):
        logger.debug("[SIM] extrude T%d %.2f mm @ %.2f", tool, length_mm, feedrate_mm_s)

    def extrude_sync(self, l0, s0, l1, s1, wait=False):
        logger.info("[SIM] extrude_sync %.1f mm / %.1f mm", l0, l1)

    def get_printer_status(self):
        self._advance()
        return {"extruder": {"temperature": self._temp[0]},
                "heater_bed": {"temperature": self._temp[1]}}

    def disable_all_heaters(self):
        self._target = [0.0, 0.0]
        logger.warning("[SIM] heaters off")
