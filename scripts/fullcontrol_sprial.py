#!/usr/bin/env python3
"""
Print a spiral tower by continuously raising the Z axis as the turntable rotates.
"""
import sys, os, time, math, logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.arm_controller import ArmController
from src.turntable_controller import TurntableController
from src.extruder_controller import ExtruderController
from config_loader import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("spiral_tower")

# ==================== CONFIGURABLE ====================
CENTRE_X = 360.5
LEFT_Y   = 105.0
RIGHT_Y  = 105.0
Z_LEFT   = 152.2
Z_RIGHT  = 160.8

# Spiral geometry
RADIUS_LEFT_START  = 80.0
RADIUS_LEFT_END    = 70.0
RADIUS_RIGHT_START = 80.0
RADIUS_RIGHT_END   = 70.0
TURNS = 12                     # how many full rotations
POINTS_PER_TURN = 200

# Extrusion speeds (mm/s)
SPEED_LEFT  = 3.0
SPEED_RIGHT = 5.0

ROTATION_SPEED = 10.0          # °/s
TRAIL_SPEED = 50.0
SAFE_X = 400.0
SAFE_Z = 190.0

# Layer height – how much Z rises per full turn
LAYER_HEIGHT_LEFT  = 0.2      # mm per revolution
LAYER_HEIGHT_RIGHT = 0.2
# =====================================================

def generate_spiral(centre_x, centre_y, radius_start, radius_end, turns, points_per_turn):
    total_angle = turns * 2 * math.pi
    total_points = int(turns * points_per_turn)
    points = []
    for i in range(total_points):
        frac = i / (total_points - 1) if total_points > 1 else 0
        angle = frac * total_angle
        radius = radius_start + frac * (radius_end - radius_start)
        x = centre_x - radius * math.cos(angle)
        y = centre_y + radius * math.sin(angle)
        points.append((x, y))
    return points


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

    # ----- Generate spiral paths -----
    path_left = generate_spiral(CENTRE_X, 0, RADIUS_LEFT_START, RADIUS_LEFT_END,
                                TURNS, POINTS_PER_TURN)
    path_right = generate_spiral(CENTRE_X, 0, RADIUS_RIGHT_START, RADIUS_RIGHT_END,
                                 TURNS, POINTS_PER_TURN)

    logger.info("Generated %d spiral points per nozzle", len(path_left))

    # ----- Move arms to start of spirals (at base Z) -----
    logger.info("Moving to safe point...")
    left.move_to(SAFE_X, LEFT_Y, SAFE_Z, roll=180, pitch=45, yaw=0, speed=100, wait=False)
    right.move_to(SAFE_X, RIGHT_Y, SAFE_Z, roll=180, pitch=45, yaw=0, speed=100, wait=True)

    left.move_to(SAFE_X, LEFT_Y, Z_LEFT, roll=180, pitch=45, yaw=0, speed=100, wait=False)
    right.move_to(SAFE_X, RIGHT_Y, Z_RIGHT, roll=180, pitch=45, yaw=0, speed=100, wait=True)

    start_x_l, start_y_l = path_left[0]
    start_x_r, start_y_r = path_right[0]
    left.move_to(start_x_l, LEFT_Y, Z_LEFT, roll=180, pitch=45, yaw=0, speed=50, wait=False)
    right.move_to(start_x_r, RIGHT_Y, Z_RIGHT, roll=180, pitch=45, yaw=0, speed=50, wait=True)

    # ----- Total print time = turntable rotation time -----
    total_time = TURNS * 360.0 / ROTATION_SPEED
    total_z_left  = TURNS * LAYER_HEIGHT_LEFT
    total_z_right = TURNS * LAYER_HEIGHT_RIGHT

    total_len_left  = SPEED_LEFT  * total_time
    total_len_right = SPEED_RIGHT * total_time

    # Start continuous extrusion (non‑blocking)
    extruder.extrude_sync(
        length_t0=total_len_left,  speed_t0=SPEED_LEFT,
        length_t1=total_len_right, speed_t1=SPEED_RIGHT,
        wait=False
    )

    # Start turntable rotation
    turntable.rotate_absolute(TURNS * 360, ROTATION_SPEED, wait=False)

    # ----- Stream arm positions along the spirals, with Z increasing -----
    t_start = time.time()
    num_points = len(path_left)
    for i in range(num_points):
        # Time at which this point should be reached
        target_t = total_time * i / (num_points - 1) if num_points > 1 else 0
        while time.time() - t_start < target_t:
            time.sleep(0.01)

        x_l, _ = path_left[i]
        x_r, _ = path_right[i]

        # Linear interpolation of Z from base to base + total rise
        frac_done = i / (num_points - 1) if num_points > 1 else 0
        current_z_left  = Z_LEFT  + frac_done * total_z_left
        current_z_right = Z_RIGHT + frac_done * total_z_right

        left.move_to(x_l, LEFT_Y, current_z_left, roll=180, pitch=45, yaw=0, speed=100, wait=False)
        right.move_to(x_r, RIGHT_Y, current_z_right, roll=180, pitch=45, yaw=0, speed=100, wait=False)

    # Wait for turntable to stop
    while turntable.is_moving():
        time.sleep(0.1)
    turntable.wait_ok()

    # Retract
    extruder.extrude_sync(-2, 10, -2, 10)
    extruder.send_gcode("M18 E X")

    # Return to safe (at the final raised Z, then move to home)
    left.move_to(SAFE_X, LEFT_Y, Z_LEFT + total_z_left, roll=180, pitch=45, yaw=0, speed=100, wait=False)
    right.move_to(SAFE_X, RIGHT_Y, Z_RIGHT + total_z_right, roll=180, pitch=45, yaw=0, speed=100, wait=True)
    left.move_to(572.0, 202.4, 152.2, roll=180, pitch=45, yaw=0, speed=100, wait=False)
    right.move_to(573.0, 217.5, 160.9, roll=180, pitch=45, yaw=0, speed=100, wait=True)

    logger.info("✅ Spiral tower printed. Height: left=%.1f mm, right=%.1f mm", total_z_left, total_z_right)

if __name__ == "__main__":
    main()