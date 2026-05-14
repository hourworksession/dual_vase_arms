#!/usr/bin/env python3
import sys, os, logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from src.arm_controller import ArmController
from src.turntable_controller import TurntableController
from config_loader import load_config

logging.basicConfig(level=logging.INFO)
config = load_config()


left = ArmController(config['arms']['left']['ip'], "left")
right = ArmController(config['arms']['right']['ip'], "right")
left.connect(); right.connect()
n = 0

while n < 5:
#normal side
    left.arm.set_position(577, 210, 180, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(577, 210, 180, 180, 45, 0, speed=100, wait=True)

    left.arm.set_position(350, 210, 180, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(350, 210, 180, 180, 45, 0, speed=100, wait=True)

    left.arm.set_position(350, -210, 180, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(350, -210, 180, 180, 45, 0, speed=100, wait=True)
#flipped
    left.arm.set_position(577, -210, 180, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(577, -210, 180, 180, 45, 0, speed=100, wait=True)

    left.arm.set_position(350, -210, 180, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(350, -210, 180, 180, 45, 0, speed=100, wait=True)

    left.arm.set_position(350, 210, 180, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(350, 210, 180, 180, 45, 0, speed=100, wait=True)
#back to normal
    left.arm.set_position(577, 210, 180, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(577, 210, 180, 180, 45, 0, speed=100, wait=True)

    n = n+1



# Turntable zeroing (if supported)
#tt = TurntableController(config['turntable']['port'], config['turntable']['baudrate'])
#print("Zeroing turntable...")
#tt.rotate_absolute(0, 30, wait=True)
#tt.close()

print("Arms in position to prepare for printing.")
left.disconnect(); right.disconnect()