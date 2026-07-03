#!/usr/bin/env python3
import sys, os, time, math, logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.arm_controller import ArmController
from src.turntable_controller import TurntableController
from src.extruder_controller import ExtruderController
from config_loader import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("print_circle")

# ====================  CONFIGURABLE PARAMETERS  ====================
CENTRE_X = 572.5               # turntable centre X (both arms)
LEFT_Y   = 105.0               # Y for left arm
RIGHT_Y  = 105.0 + 11          # Y for right arm (volcano offset)
Z_LEFT   = 151.8
Z_RIGHT  = 160.7

RADIUS_LEFT  = 80              # mm
RADIUS_RIGHT = 80 + 11

SPEED_LEFT  = 3                # filament speed left (mm/s) – lowered to prevent skipping
SPEED_RIGHT = 4                # filament speed right (mm/s)

LEAD_IN_DISTANCE  = 10.0       # mm
LEAD_OUT_DISTANCE = 10.0
LEAD_ANGLE        = 20.0       # degrees

ROTATION_SPEED    = 10.0       # °/s
TRAIL_SPEED = 50.0             # arm speed during approach / departure
Z_LIFT      = 0.2              # mm

SAFE_X = 400.0                 # far from centre
SAFE_Z = 190.0                 # high Z
# ===================================================================

def main():
    cfg = load_config()

    left  = ArmController(cfg['arms']['left']['ip'], "left")
    right = ArmController(cfg['arms']['right']['ip'], "right")
    turntable = TurntableController(host=cfg['turntable']['controller_ip'],
                                    axis=cfg['turntable']['axis'])
    extruder = ExtruderController(cfg['moonraker']['host'],
                                  cfg['moonraker']['port'])

    left.connect()
    right.connect()
    turntable.connect()

    # Derived radii
    start_radius_left  = RADIUS_LEFT  + LEAD_IN_DISTANCE
    start_radius_right = RADIUS_RIGHT + LEAD_IN_DISTANCE
    end_radius_left    = RADIUS_LEFT  + LEAD_OUT_DISTANCE
    end_radius_right   = RADIUS_RIGHT + LEAD_OUT_DISTANCE

    start_x_left  = CENTRE_X - start_radius_left
    start_x_right = CENTRE_X - start_radius_right
    end_x_left    = CENTRE_X - end_radius_left
    end_x_right   = CENTRE_X - end_radius_right

    lead_in_time  = LEAD_ANGLE / ROTATION_SPEED
    lead_out_time = LEAD_ANGLE / ROTATION_SPEED
    circle_time   = (360.0 - 2 * LEAD_ANGLE) / ROTATION_SPEED

    trail_on_dist  = abs(SAFE_X - start_x_left)    # same for both arms
    trail_on_time  = trail_on_dist / TRAIL_SPEED
    trail_off_dist = abs(end_x_left - SAFE_X)
    trail_off_time = trail_off_dist / TRAIL_SPEED

    total_time = trail_on_time + lead_in_time + circle_time + lead_out_time + trail_off_time

    total_len_left  = SPEED_LEFT  * total_time
    total_len_right = SPEED_RIGHT * total_time

    current_z_left  = Z_LEFT
    current_z_right = Z_RIGHT

    def x_from_radius(r):
        return CENTRE_X - r

    # ---------- 1. Safe approach (high Z) ----------
    logger.info("Moving to safe point (%.1f, %.1f)", SAFE_X, LEFT_Y)
    left.move_to(SAFE_X, LEFT_Y, SAFE_Z,
                 roll=180, pitch=45, yaw=0, speed=100, wait=False)
    right.move_to(SAFE_X, RIGHT_Y, SAFE_Z,
                  roll=180, pitch=45, yaw=0, speed=100, wait=True)

    # Lower to print Z
    logger.info("Lowering arms to print Z...")
    left.move_to(SAFE_X, LEFT_Y, Z_LEFT,
                 roll=180, pitch=45, yaw=0, speed=100, wait=False)
    right.move_to(SAFE_X, RIGHT_Y, Z_RIGHT,
                  roll=180, pitch=45, yaw=0, speed=100, wait=True)

    # ---------- 2. Start continuous extrusion (non‑blocking) ----------
    logger.info("Starting continuous extrusion (%.1f s)...", total_time)
    extruder.extrude_sync(
        length_t0=total_len_left,  speed_t0=SPEED_LEFT,
        length_t1=total_len_right, speed_t1=SPEED_RIGHT,
        wait=False
    )

    # ---------- 3. Trail‑on: move from SAFE_X to start_x, extruding ----------
    logger.info("Trail‑on...")
    t_start = time.time()
    while time.time() - t_start < trail_on_time:
        frac = (time.time() - t_start) / trail_on_time
        cur_x_left  = SAFE_X + frac * (start_x_left  - SAFE_X)
        cur_x_right = SAFE_X + frac * (start_x_right - SAFE_X)
        left.move_to(cur_x_left, LEFT_Y, current_z_left,
                     roll=180, pitch=45, yaw=0, speed=TRAIL_SPEED, wait=False)
        right.move_to(cur_x_right, RIGHT_Y, current_z_right,
                      roll=180, pitch=45, yaw=0, speed=TRAIL_SPEED, wait=False)
        time.sleep(0.1)

    # ---------- 4. Start turntable ----------
    logger.info("Starting turntable rotation...")
    turntable.rotate_absolute(360, ROTATION_SPEED, wait=False)

    # ---------- 5. Circle + lead‑in/out ----------
    logger.info("Printing circle...")
    circle_start = time.time()
    z_lifted = False

    while True:
        elapsed = time.time() - circle_start
        if elapsed >= lead_in_time + circle_time + lead_out_time:
            break

        if elapsed < lead_in_time:
            frac = elapsed / lead_in_time
            r_left  = start_radius_left  + frac * (RADIUS_LEFT  - start_radius_left)
            r_right = start_radius_right + frac * (RADIUS_RIGHT - start_radius_right)
        elif elapsed < lead_in_time + circle_time:
            r_left  = RADIUS_LEFT
            r_right = RADIUS_RIGHT
            if not z_lifted and elapsed >= lead_in_time + circle_time / 2.0:
                current_z_left  += Z_LIFT
                current_z_right += Z_LIFT
                z_lifted = True
                logger.info("Z lift +%.2f mm", Z_LIFT)
        else:
            t_out = elapsed - (lead_in_time + circle_time)
            frac = t_out / lead_out_time
            r_left  = RADIUS_LEFT  + frac * (end_radius_left  - RADIUS_LEFT)
            r_right = RADIUS_RIGHT + frac * (end_radius_right - RADIUS_RIGHT)

        left.move_to(x_from_radius(r_left), LEFT_Y, current_z_left,
                     roll=180, pitch=45, yaw=0, speed=100, wait=False)
        right.move_to(x_from_radius(r_right), RIGHT_Y, current_z_right,
                      roll=180, pitch=45, yaw=0, speed=100, wait=False)
        time.sleep(0.1)

    # Wait for turntable to physically stop (using task status, no axes attribute)
    while turntable.is_moving():
        time.sleep(0.1)
    turntable.wait_ok()

    # ---------- 6. Trail‑off: back to SAFE_X, still extruding ----------
    logger.info("Trail‑off...")
    t_start = time.time()
    while time.time() - t_start < trail_off_time:
        frac = (time.time() - t_start) / trail_off_time
        cur_x_left  = end_x_left  + frac * (SAFE_X - end_x_left)
        cur_x_right = end_x_right + frac * (SAFE_X - end_x_right)
        left.move_to(cur_x_left, LEFT_Y, current_z_left,
                     roll=180, pitch=45, yaw=0, speed=TRAIL_SPEED, wait=False)
        right.move_to(cur_x_right, RIGHT_Y, current_z_right,
                      roll=180, pitch=45, yaw=0, speed=TRAIL_SPEED, wait=False)
        time.sleep(0.1)

    # ---------- 7. Retract & disable ----------
    extruder.extrude_sync(length_t0=-2, speed_t0=10, length_t1=-2, speed_t1=10)
    extruder.send_gcode("M18 E X")

    # ---------- 8. Return to ready positions ----------
    logger.info("Returning arms to standby...")
    left.move_to(SAFE_X, LEFT_Y, SAFE_Z,
                 roll=180, pitch=45, yaw=0, speed=100, wait=False)
    right.move_to(SAFE_X, RIGHT_Y, SAFE_Z,
                  roll=180, pitch=45, yaw=0, speed=100, wait=True)

    left.move_to(572.0, 202.4, 152.2, roll=180, pitch=45, yaw=0, speed=100, wait=False)
    right.move_to(573.0, 217.5, 160.9, roll=180, pitch=45, yaw=0, speed=100, wait=True)

    logger.info("Calibration circles printed.")
    logger.info("Left radius = %.0f mm, Right radius = %.0f mm (commanded)", RADIUS_LEFT, RADIUS_RIGHT)

if __name__ == "__main__":
    main()