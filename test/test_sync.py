import sys, os, logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from src import *
from config_loader import load_config

logging.basicConfig(level=logging.INFO)
config = load_config()

left = ArmController(config['arms']['left']['ip'], "left")
right = ArmController(config['arms']['right']['ip'], "right")
ext = ExtruderController(config['moonraker']['host'], config['moonraker']['port'])
tt = TurntableController(config['turntable']['port'], config['turntable']['baudrate'])

left.connect(); right.connect()
left.home(); right.home()

ext.set_temperature(0, 200, wait=True)
ext.set_temperature(1, 200, wait=True)

sync = PrintSynchroniser(left, right, ext, tt)
seg = Segment(left_pose={"x": 300, "y": -100, "z": 100},
              right_pose={"x": 300, "y": 100, "z": 100},
              left_extrude=5, right_extrude=5)
sync.execute_sequence([seg])

ext.disable_all_heaters()
left.disconnect(); right.disconnect(); tt.close()