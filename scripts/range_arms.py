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
m = 0
print("Testing box motion")
while n < 3:
#normal side
    print(f"Point 1 {n}")
    left.arm.set_position(577, 210, 190, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(577, 210, 190, 180, 45, 0, speed=100, wait=True)
    print(f"Point 2 {n}")
    left.arm.set_position(350, 210, 190, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(350, 210, 190, 180, 45, 0, speed=100, wait=True)
    print(f"Point 3 {n}")
    left.arm.set_position(350, -210, 190, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(350, -210, 190, 180, 45, 0, speed=100, wait=True)
#flipped
    print(f"Point 4 {n}")
    left.arm.set_position(577, -210, 190, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(577, -210, 190, 180, 45, 0, speed=100, wait=True)
    print(f"Point 3 {n}")
    left.arm.set_position(350, -210, 190, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(350, -210, 190, 180, 45, 0, speed=100, wait=True)
    print(f"Point 2 {n}")
    left.arm.set_position(350, 210, 190, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(350, 210, 190, 180, 45, 0, speed=100, wait=True)
#back to normal
    print(f"Point 1 {n}")
    left.arm.set_position(577, 210, 190, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(577, 210, 190, 180, 45, 0, speed=100, wait=True)

    n = n+1

print("Testing quarter slice motion")
while m < 3:
    print(f"Point 1 {m}")
    left.arm.set_position(577, 210, 190, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(577, 210, 190, 180, 45, 0, speed=100, wait=True)
    print(f"Point 2 {m}")
    left.arm.set_position(350, 210, 190, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(350, 210, 190, 180, 45, 0, speed=100, wait=True)
    print(f"Point 3 {m}")
    left.arm.set_position(350, 0, 190, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(350, 0, 190, 180, 45, 0, speed=100, wait=True)
    print(f"Point 2 {m}")
    left.arm.set_position(350, 210, 190, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(350, 210, 190, 180, 45, 0, speed=100, wait=True)
    print(f"Point 1 {m}")
    left.arm.set_position(577, 210, 190, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(577, 210, 190, 180, 45, 0, speed=100, wait=True)

    m = m + 1

# Turntable zeroing (if supported)
#tt = TurntableController(config['turntable']['port'], config['turntable']['baudrate'])
#print("Zeroing turntable...")
#tt.rotate_absolute(0, 30, wait=True)
#tt.close()

print("Arms in position to prepare for printing.")
left.disconnect(); right.disconnect()