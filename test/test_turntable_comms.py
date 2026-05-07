import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from src.turntable_controller import TurntableController
from config_loader import load_config

config = load_config()
tt = TurntableController(config['turntable']['port'], config['turntable']['baudrate'])
print("Rotating 90 degrees relative...")
tt.rotate_relative(90, 30)
print("Done")
tt.close()