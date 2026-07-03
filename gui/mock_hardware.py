"""
Mock controllers for offline testing of the GUI.
They print actions to the console and fake status data.
"""
import time
import logging

logger = logging.getLogger(__name__)


class MockArmController:
    def __init__(self, ip, name):
        self.name = name
        self.ip = ip
        self._connected = False
        self._pose = (200.0, 0.0, 100.0)  # fake home position

    def connect(self):
        print(f"[MOCK] {self.name} arm connected at {self.ip}")
        self._connected = True

    def disconnect(self):
        print(f"[MOCK] {self.name} arm disconnected")
        self._connected = False

    def home(self, wait=True):
        print(f"[MOCK] {self.name} homing...")
        if wait:
            time.sleep(0.5)
        self._pose = (200.0, 0.0, 100.0)
        print(f"[MOCK] {self.name} homed")

    def move_to(self, x, y, z, roll=180, pitch=0, yaw=0, speed=None, wait=False):
        print(f"[MOCK] {self.name} move to ({x:.1f}, {y:.1f}, {z:.1f}) speed={speed}")
        self._pose = (x, y, z)
        if wait:
            time.sleep(0.1)

    def get_pose(self):
        return self._pose

    def emergency_stop(self):
        print(f"[MOCK] {self.name} EMERGENCY STOP")
        self._connected = False

    # needed by synchroniser
    class MockArm:
        def get_state(self):
            return (0, 0)   # idle
    def __init__(self, ip, name):
        self.arm = self.MockArm()
        self.name = name
        self.ip = ip
        self._connected = False
        self._pose = (200.0, 0.0, 100.0)


class MockTurntableController:
    def __init__(self, host, axis):
        self.host = host
        self.axis = axis
        self._connected = False
        self._angle = 0.0

    def connect(self):
        print(f"[MOCK] Turntable {self.axis} connected at {self.host}")
        self._connected = True

    def disconnect(self):
        print("[MOCK] Turntable disconnected")
        self._connected = False

    def rotate_absolute(self, angle_deg, speed_dps, wait=True):
        print(f"[MOCK] Turntable rotate absolute to {angle_deg}° at {speed_dps}°/s")
        self._angle = angle_deg
        if wait:
            time.sleep(0.3)

    def rotate_relative(self, angle_deg, speed_dps, wait=True):
        new_angle = self._angle + angle_deg
        print(f"[MOCK] Turntable rotate relative by {angle_deg}° → {new_angle}°")
        self._angle = new_angle
        if wait:
            time.sleep(0.3)

    def get_angle(self):
        return self._angle


class MockExtruderController:
    def __init__(self, host, port=7125):
        self.host = host
        self.port = port
        self._connected = False
        self._temps = {0: 20.0, "heater_bed": 20.0}

    def connect(self):
        print(f"[MOCK] Extruder connected to {self.host}:{self.port}")
        self._connected = True

    def set_temperature(self, tool, temp, wait=False):
        if tool == 0:
            self._temps[0] = temp
            print(f"[MOCK] T0 target set to {temp}°C")
        elif tool == "heater_bed":
            self._temps["heater_bed"] = temp
            print(f"[MOCK] heater_bed target set to {temp}°C")

    def send_gcode(self, script, timeout=30):
        print(f"[MOCK] G-Code: {script[:80]}...")
        if "M109" in script or "M104" in script:
            pass
        elif "SET_HEATER_TEMPERATURE" in script:
            # crude parse
            if "heater_bed" in script:
                self._temps["heater_bed"] = 200
        return {"result": "ok"}

    def heat_and_wait(self, tool, temp, timeout=120):
        print(f"[MOCK] Heating T{tool} to {temp}°C...")
        self._temps[tool] = temp
        time.sleep(0.5)
        print(f"[MOCK] T{tool} at {temp}°C")

    def get_temperature(self, tool):
        return self._temps.get(tool, 0.0)

    def get_printer_status(self):
        return {
            "extruder": {"temperature": self._temps[0]},
            "heater_bed": {"temperature": self._temps.get("heater_bed", 0.0)}
        }

    def extrude(self, tool, length_mm, feedrate_mm_s):
        print(f"[MOCK] Extrude T{tool}: {length_mm}mm @ {feedrate_mm_s}mm/s")

    def disable_all_heaters(self):
        print("[MOCK] All heaters disabled")
        self._temps = {0: 20.0, "heater_bed": 20.0}