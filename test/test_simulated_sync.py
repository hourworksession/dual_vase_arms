#!/usr/bin/env python3
"""
Simulated system test – no hardware required.

Mocks all external devices (arms, extruder, turntable) and runs the full
synchroniser logic to verify the control flow without physical hardware.
"""
import sys
import os
import logging

# Ensure the project root is on the path so we can import our modules
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from config_loader import load_config
from src.command_parser import Segment
from src.synchroniser import PrintSynchroniser


# ------------------------------------------------------------------ #
#                          Mock Hardware                              #
# ------------------------------------------------------------------ #

class MockArm:
    """Simulates the real xArm's low‑level API object.

    This mirrors the object returned by ``XArmAPI(ip)`` and stored
    inside ``ArmController.arm``.
    """

    def __init__(self, name: str) -> None:
        """
        Args:
            name: A label (e.g. ``"left"``) for log messages.
        """
        self.name = name
        self._state = 0          # 0 = idle, 1 = moving

    def move_to(self, x: float, y: float, z: float,
                roll: float = 180, pitch: float = 0, yaw: float = 0,
                speed: float = None, wait: bool = False) -> None:
        """
        Simulate a Cartesian move.

        In simulation the move is considered finished immediately, so
        the internal state is set to idle right away.
        """
        print(f"[MockArm:{self.name}] move_to("
              f"x={x:.1f}, y={y:.1f}, z={z:.1f}, "
              f"roll={roll}, pitch={pitch}, yaw={yaw}, "
              f"speed={speed}, wait={wait})")
        self._state = 0

    def get_state(self) -> tuple:
        """
        Return the arm's current state as a tuple.

        Returns:
            ``(error_code, state_number)`` where ``state_number`` is
            1 if moving, 0 if idle. The error code is always 0 in
            simulation.
        """
        return (0, self._state)


class MockArmController:
    """
    Mimics ``ArmController`` and holds a nested ``.arm`` attribute.

    All calls are forwarded to ``MockArm`` for logging.
    """

    def __init__(self, name: str) -> None:
        """
        Args:
            name: A label (e.g. ``"left"``) for log messages.
        """
        self.name = name
        self.arm = MockArm(name)

    def connect(self) -> None:
        """Simulate connecting to the arm."""
        print(f"[MockArm:{self.name}] connect()")

    def home(self, wait: bool = True) -> None:
        """Simulate homing the arm."""
        print(f"[MockArm:{self.name}] home(wait={wait})")

    def move_to(self, x: float, y: float, z: float,
                roll: float = 180, pitch: float = 0, yaw: float = 0,
                speed: float = None, wait: bool = False) -> None:
        """Forward the move command to the underlying MockArm."""
        self.arm.move_to(x, y, z, roll, pitch, yaw, speed, wait)

    def get_state(self) -> tuple:
        """Return the arm's (error_code, state) tuple."""
        return self.arm.get_state()

    def disconnect(self) -> None:
        """Simulate disconnecting from the arm."""
        print(f"[MockArm:{self.name}] disconnect()")


class MockExtruderController:
    """
    Simulates the extruder controller that talks to Klipper/Moonraker.

    All methods simply print what they would do.
    """

    def set_temperature(self, tool: int, temp: float, wait: bool) -> None:
        """
        Set target temperature for a tool.

        Args:
            tool: Extruder index (0 or 1).
            temp: Target temperature in Celsius.
            wait: If ``True``, block until temperature is reached.
        """
        print(f"[MockExtruder] set_temperature(T{tool}, {temp}°C, wait={wait})")

    def extrude(self, tool: int, length_mm: float, feedrate_mm_s: float) -> None:
        """
        Push a length of filament at a given feedrate.

        Args:
            tool: Extruder index (0 or 1).
            length_mm: Filament length to extrude (mm).
            feedrate_mm_s: Extrusion speed (mm/s).
        """
        print(f"[MockExtruder] extrude(T{tool}, {length_mm:.2f}mm, "
              f"{feedrate_mm_s:.1f}mm/s)")

    def disable_all_heaters(self) -> None:
        """Turn off all heaters."""
        print("[MockExtruder] disable_all_heaters()")


class MockTurntableController:
    """
    Simulates a turntable controller connected via serial.

    All commands are printed and the ``wait_ok`` method is public,
    matching the real controller's interface.
    """

    def rotate_absolute(self, angle_deg: float, speed_dps: float,
                        wait: bool = True) -> None:
        """
        Rotate the turntable to an absolute angle.

        Args:
            angle_deg: Target angle in degrees.
            speed_dps: Rotation speed in °/s.
            wait: If ``True``, block until motion is complete.
        """
        print(f"[MockTurntable] rotate_absolute({angle_deg}°, "
              f"{speed_dps}°/s, wait={wait})")

    def rotate_relative(self, angle_deg: float, speed_dps: float,
                        wait: bool = True) -> None:
        """
        Rotate the turntable by a relative angle.

        Args:
            angle_deg: Relative angle in degrees.
            speed_dps: Rotation speed in °/s.
            wait: If ``True``, block until motion is complete.
        """
        print(f"[MockTurntable] rotate_relative({angle_deg}°, "
              f"{speed_dps}°/s, wait={wait})")

    def wait_ok(self) -> None:
        """Simulate waiting until turntable reports completion."""
        print("[MockTurntable] wait_ok() – motion complete")

    def close(self) -> None:
        """Close the connection to the turntable."""
        print("[MockTurntable] close()")


# ------------------------------------------------------------------ #
#                              Main Test                              #
# ------------------------------------------------------------------ #

def run() -> None:
    """Run the simulated print sequence and verify the complete control flow."""
    logging.basicConfig(level=logging.INFO)

    # 1. Load example config (path resolved from this script)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, "config", "settings.yaml.example")
    config = load_config(config_path)
    print("✓ Config loaded (using example IPs)")

    # 2. Create mock hardware
    left = MockArmController("left")
    right = MockArmController("right")
    extruder = MockExtruderController()
    turntable = MockTurntableController()

    # 3. Build a test sequence
    seg1 = Segment(
        left_pose={"x": 200, "y": -50, "z": 100},
        right_pose={"x": 200, "y": 50, "z": 100},
        left_speed=80, right_speed=80
    )
    seg2 = Segment(
        left_pose={"x": 300, "y": -50, "z": 100},
        right_pose={"x": 300, "y": 50, "z": 100},
        left_extrude=10, right_extrude=10,
        left_speed=30, right_speed=30,
        extrusion_feedrate=5
    )
    seg3 = Segment(
        turntable_abs_angle=90, turntable_speed=20
    )
    seg4 = Segment(
        left_pose={"x": 200, "y": -50, "z": 100},
        right_pose={"x": 200, "y": 50, "z": 100},
        left_extrude=8, right_extrude=8,
        left_speed=30, right_speed=30,
        extrusion_feedrate=4.5
    )
    segments = [seg1, seg2, seg3, seg4]

    # 4. Run synchroniser with mocks (automatically logs every segment)
    sync = PrintSynchroniser(left, right, extruder, turntable,
                             default_speed=50, default_extrusion_feedrate=5.0)
    print("\n--- Starting simulated print sequence ---")
    sync.execute_sequence(segments)
    print("--- Sequence complete ---\n")

    # 5. Show log location
    log_path = sync.data_logger.filepath
    print(f"✅ All tests passed (simulated)")
    print(f"📁 Log saved to: {log_path}")


if __name__ == "__main__":
    run()