#!/usr/bin/env python3
import sys, os, time, logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.arm_controller import ArmController
from src.turntable_controller import TurntableController
from src.extruder_controller import ExtruderController
from config_loader import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("prepare")

def main():
    cfg = load_config()

    left = ArmController(cfg['arms']['left']['ip'], "left")
    right = ArmController(cfg['arms']['right']['ip'], "right")
    turntable = TurntableController(host=cfg['turntable']['controller_ip'], axis=cfg['turntable']['axis'])
    extruder = ExtruderController(cfg['moonraker']['host'], cfg['moonraker']['port'])

    left.connect()
    right.connect()
    turntable.connect()

    left.home(wait=True)
    right.home(wait=True)

    temp = cfg['defaults']['temperature']['tool0']
    logger.info(f"Heating both extruders to {temp}°C")
    extruder.set_temperature(0, temp, wait=False)
    extruder.set_temperature(1, temp, wait=False)

    logger.info("Moving arms to safe position while heating...")
    left.arm.set_position(572.5, 225, 153, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(572.5, 230, 165, 180, 45, 0, speed=100, wait=False)

    extruder.heat_and_wait(0, temp)
    extruder.heat_and_wait(1, temp)

    logger.info("Moving arms to print-ready positions...")
    left.arm.set_position(572, 202.4, 152.2, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(573, 217.5, 160.9, 180, 45, 0, speed=100, wait=False)
    time.sleep(5)

    logger.info("Purging nozzles (25 mm each)...")
    extruder.extrude(0, 25, feedrate_mm_s=5)
    extruder.extrude(1, 25, feedrate_mm_s=5)

    logger.info("✅ Ready to print. Hardware remains connected.")

if __name__ == "__main__":
    main()