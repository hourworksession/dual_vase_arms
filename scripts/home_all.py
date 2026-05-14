#!/usr/bin/env python3
"""Home both arms and zero the turntable."""
import sys, os, logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.arm_controller import ArmController
from src.turntable_controller import TurntableController
from config_loader import load_config

logging.basicConfig(level=logging.INFO)
config = load_config()

# Arms
left = ArmController(config['arms']['left']['ip'], "left")
right = ArmController(config['arms']['right']['ip'], "right")
left.connect()
right.connect()

left.home(wait=False)
right.home(wait=True)

# Turntable (Aerotech iXC4)
tt = TurntableController(
    host=config['turntable']['controller_ip'],
    axis=config['turntable']['axis']
)
tt.connect()
print("Zeroing turntable...")
tt.rotate_absolute(0, 30, wait=True)
tt.disconnect()

print("All axes homed/zeroed")
left.disconnect()
right.disconnect()