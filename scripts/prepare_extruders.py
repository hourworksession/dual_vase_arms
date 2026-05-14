#!/usr/bin/env python3
"""Prepare both extruders: heat, load filament, purge."""
import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.extruder_controller import ExtruderController
from config_loader import load_config

config = load_config()
ext = ExtruderController(config['moonraker']['host'], config['moonraker']['port'])

TARGET = 200.0

# Start heating both immediately
ext.set_temperature(0, TARGET, wait=False)
ext.set_temperature(1, TARGET, wait=False)

# Now wait for each one in turn (could also poll, but M109 blocks)
print(f"Heating both extruders to {TARGET}°C...")
ext.heat_and_wait(0, TARGET)   # blocks until T0 ready
ext.heat_and_wait(1, TARGET)   # blocks until T1 ready

print("Heaters ready. Loading filament...")
ext.load_filament(0, length_mm=20, feedrate_mm_s=5)
ext.load_filament(1, length_mm=20, feedrate_mm_s=5)

print("Extruders prepared. You can now start the print.")