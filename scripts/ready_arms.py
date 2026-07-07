#!/usr/bin/env python3
import sys, os, logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from src.arm_controller import ArmController
from src.turntable_controller import TurntableController
from config_loader import load_config
import time

logging.basicConfig(level=logging.INFO)
config = load_config()


left = ArmController(config['arms']['left']['ip'], "left")
right = ArmController(config['arms']['right']['ip'], "right")
left.connect(); right.connect()

# (X axis, Y axis, Z axis, Roll (X), Pitch (Y), Yaw(Z))
# CLEAR OF TURNTABLE
left.arm.set_position(572.5, 225, 153, 180, 45, 0, speed=100, wait=False)
right.arm.set_position(572.5, 230, 165, 180, 45, 0, speed=100, wait=False)

#time.sleep(5)

# (X axis, Y axis, Z axis, Roll (X), Pitch (Y), Yaw(Z))
# READY ARM IN POSITION ON LIMIT OF TABLE
#left.arm.set_position(572, 202.4, 152.2, 180, 45, 0, speed=100, wait=False)
#right.arm.set_position(573, 217.5, 160.9, 180, 45, 0, speed=100, wait=False)


# Turntable zeroing (if supported)
#tt = TurntableController(config['turntable']['port'], config['turntable']['baudrate'])
#print("Zeroing turntable...")
#tt.rotate_absolute(0, 30, wait=True)
#tt.close()

print("Arms in position to prepare for printing.")
left.disconnect(); right.disconnect()