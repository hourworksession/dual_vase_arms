#!/usr/bin/env python3
"""
Print an interleaved two‑nozzle cylinder WITH COLLISION AVOIDANCE,
using the same turntable‑centre / angle / radial‑offset method
that works in your demo.
"""
import sys, os, time, math, logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.arm_controller import ArmController
from src.turntable_controller import TurntableController
from src.extruder_controller import ExtruderController
from config_loader import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cylinder_safe")

# ==================== CONFIGURABLE ====================
# ---------- Turntable centre (measured per arm) ----------
TT_CX_LEFT  = 575.1
TT_CY_LEFT  = -11.1
TT_CX_RIGHT = 575.6
TT_CY_RIGHT = 3.5

# ---------- Print geometry ----------
RADIUS          = 120.0               # base printed radius (mm)
LEFT_R_OFFSET   = 5.0                 # radial offset for left arm
RIGHT_R_OFFSET  = 8.5                 # radial offset for right arm

START_ANGLE_DEG = 135.0               # global start angle (degrees)

Z_LEFT   = 151.6                      # left nozzle height – master reference
Z_RIGHT  = 151.8                    # right nozzle, adjust to match left

LINE_WIDTH      = 0.4                 # mm
LAYER_HEIGHT    = 0.2                 # mm (bead thickness)
Z_STEP_PER_REV  = 0.4                 # mm vertical advance per full turn

TOTAL_REVOLUTIONS = 100

SPEED_LEFT  = 3                     # mm/s
SPEED_RIGHT = 3

FILAMENT_DIAMETER = 1.75              # mm

PRE_EXTRUDE_TIME = 2.8                # seconds

SAFE_X = 420
SAFE_Z = 250.0                        # high enough to clear everything
# ======================================================

FILAMENT_AREA = math.pi * (FILAMENT_DIAMETER / 2) ** 2

# ---------- safe move helper (unchanged) ----------
def safe_move(left_arm, right_arm,
              lx, ly, lz, roll_l, pitch_l, yaw_l, speed_l,
              rx, ry, rz, roll_r, pitch_r, yaw_r, speed_r):
    """
    Move both arms to given poses. If *either* target Y is in the
    collision band (-50 .. 50), move sequentially (left first, then right).
    Otherwise move simultaneously (left non‑blocking, right blocking).
    """
    in_danger = (abs(ly) < 50.0) or (abs(ry) < 50.0)

    if in_danger:
        left_arm.set_position(lx, ly, lz, roll_l, pitch_l, yaw_l,
                              speed=speed_l, wait=True)
        right_arm.set_position(rx, ry, rz, roll_r, pitch_r, yaw_r,
                               speed=speed_r, wait=True)
    else:
        left_arm.set_position(lx, ly, lz, roll_l, pitch_l, yaw_l,
                              speed=speed_l, wait=False)
        right_arm.set_position(rx, ry, rz, roll_r, pitch_r, yaw_r,
                               speed=speed_r, wait=True)
# ----------------------------------------

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

    # ---- Compute effective radii ----
    eff_radius_left  = RADIUS + LEFT_R_OFFSET
    eff_radius_right = RADIUS + RIGHT_R_OFFSET

    # ---- Extrusion calculations ----
    circ_left  = 2 * math.pi * eff_radius_left
    circ_right = 2 * math.pi * eff_radius_right

    vol_left  = circ_left  * LINE_WIDTH * LAYER_HEIGHT
    vol_right = circ_right * LINE_WIDTH * LAYER_HEIGHT

    filament_len_left_per_rev  = vol_left  / FILAMENT_AREA
    filament_len_right_per_rev = vol_right / FILAMENT_AREA

    time_left  = filament_len_left_per_rev  / SPEED_LEFT
    time_right = filament_len_right_per_rev / SPEED_RIGHT
    rev_time = max(time_left, time_right)
    turntable_speed = 360.0 / rev_time
    total_rotation_time = rev_time * TOTAL_REVOLUTIONS

    filament_len_left  = filament_len_left_per_rev  * TOTAL_REVOLUTIONS
    filament_len_right = filament_len_right_per_rev * TOTAL_REVOLUTIONS

    logger.info("Effective radii: left %.1f mm, right %.1f mm", eff_radius_left, eff_radius_right)
    logger.info("Rev time: %.2f s, turntable speed: %.2f °/s", rev_time, turntable_speed)
    logger.info("Total filament: left %.0f mm, right %.0f mm", filament_len_left, filament_len_right)

    # ---- Approach: go to high safe point (collision zone => sequential) ----
    #  (We use the start Y coordinates as the "safe Y" for approach, they are >50)
    start_angle_rad = math.radians(START_ANGLE_DEG)
    safe_ly = TT_CY_LEFT  + eff_radius_left  * math.sin(start_angle_rad)
    safe_ry = TT_CY_RIGHT + eff_radius_right * math.sin(start_angle_rad)

    logger.info("Moving to high safe point...")
    safe_move(left.arm, right.arm,
              SAFE_X, safe_ly, SAFE_Z, 180, 45, 0, 100,
              SAFE_X, safe_ry, SAFE_Z, 180, 45, 0, 100)

    # ---- Lower to print Z at the safe X (still sequential) ----
    logger.info("Lowering to print Z at safe X...")
    safe_move(left.arm, right.arm,
              SAFE_X, safe_ly, Z_LEFT, 180, 45, 0, 100,
              SAFE_X, safe_ry, Z_RIGHT + LAYER_HEIGHT, 180, 45, 0, 100)

    # ---- Move to the exact start point on the circle ----
    start_x_left  = TT_CX_LEFT  + eff_radius_left  * math.cos(start_angle_rad)
    start_y_left  = TT_CY_LEFT  + eff_radius_left  * math.sin(start_angle_rad)
    start_x_right = TT_CX_RIGHT + eff_radius_right * math.cos(start_angle_rad)
    start_y_right = TT_CY_RIGHT + eff_radius_right * math.sin(start_angle_rad)

    logger.info("Moving to start point (angle %.1f°)...", START_ANGLE_DEG)
    safe_move(left.arm, right.arm,
              start_x_left, start_y_left, Z_LEFT, 180, 45, 0, 50,
              start_x_right, start_y_right, Z_RIGHT + LAYER_HEIGHT, 180, 45, 0, 50)

    # ---- Continuous extrusion (non‑blocking) ----
    logger.info("Starting continuous extrusion...")
    extruder.extrude_sync(
        length_t0=filament_len_left,  speed_t0=SPEED_LEFT,
        length_t1=filament_len_right, speed_t1=SPEED_RIGHT,
        wait=False
    )

    # ---- Pre‑extrude ----
    time.sleep(PRE_EXTRUDE_TIME)

    # ---- Start turntable ----
    logger.info("Starting turntable rotation...")
    turntable.rotate_absolute(TOTAL_REVOLUTIONS * 360, turntable_speed, wait=False)

    # ---- Main spiral loop with collision‑aware moves ----
    z_rise_total = TOTAL_REVOLUTIONS * Z_STEP_PER_REV
    t_start = time.time()

    while time.time() - t_start < total_rotation_time:
        elapsed = time.time() - t_start
        frac = elapsed / total_rotation_time

        # Current angle = start angle + total spiral rotation so far
        angle = start_angle_rad + frac * TOTAL_REVOLUTIONS * 2 * math.pi

        # Compute new XY positions exactly like the demo's nozzle_pos
        cur_x_left  = TT_CX_LEFT  + eff_radius_left  * math.cos(angle)
        cur_y_left  = TT_CY_LEFT  + eff_radius_left  * math.sin(angle)
        cur_x_right = TT_CX_RIGHT + eff_radius_right * math.cos(angle)
        cur_y_right = TT_CY_RIGHT + eff_radius_right * math.sin(angle)

        # Z rises linearly
        new_z_left  = Z_LEFT  + frac * z_rise_total
        new_z_right = Z_RIGHT + LAYER_HEIGHT + frac * z_rise_total

        safe_move(left.arm, right.arm,
                  cur_x_left, cur_y_left, new_z_left, 180, 45, 0, 100,
                  cur_x_right, cur_y_right, new_z_right, 180, 45, 0, 100)

        time.sleep(0.2)

    # ---- Wait for turntable to stop ----
    while turntable.is_moving():
        time.sleep(0.1)
    turntable.wait_ok()

    # ---- Retract, raise, home ----
    extruder.extrude_sync(-2, 10, -2, 10)
    extruder.send_gcode("M18 E X")

    logger.info("Raising to safe Z...")
    safe_move(left.arm, right.arm,
              cur_x_left, cur_y_left, SAFE_Z, 180, 45, 0, 100,
              cur_x_right, cur_y_right, SAFE_Z, 180, 45, 0, 100)

    # Go to far safe X
    safe_move(left.arm, right.arm,
              SAFE_X, cur_y_left, SAFE_Z, 180, 45, 0, 100,
              SAFE_X, cur_y_right, SAFE_Z, 180, 45, 0, 100)

    # Final standby position (your existing safe positions)
    left.arm.set_position(572.0, 202.4, 152.2, 180, 45, 0, speed=100, wait=False)
    right.arm.set_position(573.0, 217.5, 160.9, 180, 45, 0, speed=100, wait=True)

    logger.info("✅ Interleaved cylinder printed safely.")

if __name__ == "__main__":
    main()