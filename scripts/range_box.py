#!/usr/bin/env python3
"""Test the reachable box volume with both arms."""
import sys, os, logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from src.arm_controller import ArmController
from config_loader import load_config
import time

logging.basicConfig(level=logging.INFO)
config = load_config()

left  = ArmController(config['arms']['left']['ip'], "left")
right = ArmController(config['arms']['right']['ip'], "right")
left.connect()
right.connect()

loop = 0

# ======== SAFE BOX (taken from your proven range) ========
X_MIN, X_MAX = 280.0, 550.0      # mm
Y_MIN, Y_MAX = 0.0, 210.0
Z_MIN, Z_MAX = 200, 600.0      # safe Z window
# =========================================================

corners = [
    (X_MIN, Y_MIN, Z_MIN),   # 1
    (X_MIN, Y_MAX, Z_MIN),   # 2
    (X_MAX, Y_MAX, Z_MIN),   # 3
    (X_MIN, Y_MAX, Z_MIN),   # 4
    (X_MIN, Y_MIN, Z_MAX),   # 5
    (X_MIN, Y_MAX, Z_MAX),   # 6
    (X_MAX, Y_MAX, Z_MAX),   # 7
    (X_MIN, Y_MAX, Z_MAX),   # 8
]

# Base tool orientation (roll, pitch, yaw) in degrees
BASE_ROLL  = 180.0
BASE_PITCH = 45.0
BASE_YAW   = 0.0

# Orientation variations
orientations = [
    (BASE_ROLL, BASE_PITCH, BASE_YAW),                # base
    (BASE_ROLL + 15, BASE_PITCH, BASE_YAW),           # +roll
    (BASE_ROLL - 15, BASE_PITCH, BASE_YAW),           # -roll
    (BASE_ROLL, BASE_PITCH + 15, BASE_YAW),           # +pitch
    (BASE_ROLL, BASE_PITCH - 15, BASE_YAW),           # -pitch
    (BASE_ROLL, BASE_PITCH, BASE_YAW + 15),           # +yaw
    (BASE_ROLL, BASE_PITCH, BASE_YAW - 15),           # -yaw
]

print("Testing box corners with varied nozzle angles")
total_cycles = 1   # repeat the whole sequence N times
cycle = 0
while cycle < total_cycles:
    for corner_idx, (x, y, z) in enumerate(corners):
        for ori_idx, (roll, pitch, yaw) in enumerate(orientations):
            print(f"Cycle {cycle+1}/{total_cycles}, Corner {corner_idx+1}, "
                  f"Ori {ori_idx+1}: ({x:.1f},{y:.1f},{z:.1f}) "
                  f"R/P/Y=({roll:.0f},{pitch:.0f},{yaw:.0f})")
            left.arm.set_position(x, y, z, roll, pitch, yaw,
                                  speed=100, wait=False)
            right.arm.set_position(x, y, z, roll, pitch, yaw,
                                   speed=100, wait=True)
            time.sleep(0.1)   # small breathing room
    cycle += 1

print("Box iteration complete – all corners reached safely.")
left.disconnect()
right.disconnect()