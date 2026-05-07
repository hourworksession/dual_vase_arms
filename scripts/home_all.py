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

left.home(wait=True)
right.home(wait=True)

# Turntable zeroing (if supported)
tt = TurntableController(config['turntable']['port'], config['turntable']['baudrate'])
print("Zeroing turntable...")
tt.rotate_absolute(0, 30, wait=True)
tt.close()

print("All axes homed/zeroed")
left.disconnect(); right.disconnect()