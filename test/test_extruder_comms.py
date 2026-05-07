import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from src.extruder_controller import ExtruderController
from config_loader import load_config

config = load_config()
ext = ExtruderController(config['moonraker']['host'], config['moonraker']['port'])
print("Heating T0 to 200°C...")
ext.set_temperature(0, 200, wait=True)
print("Extruding 10 mm on T0")
ext.extrude(0, 10, 5)
print("Disabling heater")
ext.disable_all_heaters()