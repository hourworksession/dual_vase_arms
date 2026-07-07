#!/usr/bin/env python3
"""
First dual-arm print with turntable.
Prints a simple cross pattern: two lines intersecting at the centre.
"""
import sys, os, time, logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.arm_controller import ArmController
from src.extruder_controller import ExtruderController
from src.turntable_controller import TurntableController
from src.synchroniser import PrintSynchroniser
from src.command_parser import Segment
from src.logger import DataLogger
from config_loader import load_config

logging.basicConfig(level=logging.INFO)
config = load_config()

# Initialise hardware
left = ArmController(config['arms']['left']['ip'], "left")
right = ArmController(config['arms']['right']['ip'], "right")
ext = ExtruderController(config['moonraker']['host'], config['moonraker']['port'])
tt = TurntableController(config['turntable']['port'], config['turntable']['baudrate'])

left.connect(); right.connect()
left.home(wait=True); right.home(wait=True)

# Heat up
print("Heating extruders...")
ext.set_temperature(0, config['defaults']['temperature']['tool0'], wait=True)
ext.set_temperature(1, config['defaults']['temperature']['tool1'], wait=True)

sync = PrintSynchroniser(left, right, ext, tt,
                         default_speed=config['defaults']['print_speed'],
                         default_extrusion_feedrate=config['defaults']['extrusion_feedrate'])
logger = DataLogger("demo_log.csv")

# Define a cross pattern: move to start, extrude line, rotate 90°, extrude second line
# Coordinates are just examples – adjust to your setup.
start_left = {"x": 300, "y": -80, "z": 80, "roll": 180, "pitch": 0, "yaw": 0}
start_right = {"x": 300, "y": 80, "z": 80, "roll": 180, "pitch": 0, "yaw": 0}

line1_end_left = {"x": 500, "y": -80, "z": 80, "roll": 180, "pitch": 0, "yaw": 0}
line1_end_right = {"x": 500, "y": 80, "z": 80, "roll": 180, "pitch": 0, "yaw": 0}

line2_end_left = {"x": 300, "y": -50, "z": 80, "roll": 180, "pitch": 0, "yaw": 0}
line2_end_right = {"x": 300, "y": 50, "z": 80, "roll": 180, "pitch": 0, "yaw": 0}

segments = [
    Segment(left_pose=start_left, right_pose=start_right, left_speed=100, right_speed=100),
    Segment(left_pose=line1_end_left, right_pose=line1_end_right,
            left_extrude=20, right_extrude=20,
            left_speed=30, right_speed=30),
    Segment(turntable_abs_angle=90, turntable_speed=30),
    Segment(left_pose=line2_end_left, right_pose=line2_end_right,
            left_extrude=15, right_extrude=15,
            left_speed=30, right_speed=30),
]

sync.execute_sequence(segments)

# Shutdown
ext.disable_all_heaters()
left.disconnect(); right.disconnect()
tt.close()
logger.close()
print("Demo complete. Log saved to demo_log.csv")