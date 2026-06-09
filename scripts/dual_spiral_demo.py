#!/usr/bin/env python3
"""
Phased dual‑arm spiral demo – safe and synchronised.

Phase 1 : RIGHT extruder prints a cylinder base (helix up).
Phase 2 : RIGHT retracts and parks.
Phase 3 : LEFT extruder continues the same tower upward.
Phase 4 : BOTH extruders co‑print 180° apart to the top.
"""
import sys, os, time, math, logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.arm_controller import ArmController
from src.turntable_controller import TurntableController
from src.extruder_controller import ExtruderController
from config_loader import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("demo_spiral")

# ==================== CONFIGURABLE PARAMETERS ====================
# Turntable centre per arm (from your calibration)
TT_CX_LEFT  = 575.3
TT_CY_LEFT  = -10.6
TT_CX_RIGHT = 573.1
TT_CY_RIGHT = 3.0

# Radii – both print at the same physical radius on the turntable
RADIUS = 100.0                  # mm

# Z heights (left is the reference; right must be adjusted so both
# nozzles touch the bed at the same time)
Z_LEFT   = 151.8                # left nozzle base height
Z_RIGHT  = 151.8                # right nozzle base height
Z_STEP_PER_REV = 0.4            # vertical advance per full turn (layer height)

# Extrusion parameters (both nozzles are 0.4 mm)
LINE_WIDTH        = 0.4         # mm
LAYER_HEIGHT      = Z_STEP_PER_REV   # bead thickness = vertical advance
FILAMENT_DIAMETER = 1.75        # mm

# Phases (full revolutions)
RIGHT_REVS = 3                  # revs right prints alone
LEFT_REVS  = 3                  # revs left prints alone
COMBINED_REVS = 4               # revs both print together 180° apart

# Turntable speed during phases (adjusted automatically to match extrusion)
ROTATION_SPEED = 10.0           # °/s – will be recalculated per phase

# Extrusion feed rate (mm/s of filament) – used as F in G1
FEED_RATE = 4.0

# Pre‑extrude before each phase (mm)
PRE_EXTRUDE = 8.0
PRE_EXTRUDE_SPEED = 3.0         # mm/s

# Parking position for idle arm
PARK_X = 220.0
PARK_Y = 150.0
PARK_Z = 220.0

# Safe approach / stand‑by
SAFE_X = 220.0
SAFE_Z = 250.0
# ===================================================================

FILAMENT_AREA = math.pi * (FILAMENT_DIAMETER / 2) ** 2


# ---------- Helper: compute filament length for a full revolution ----------
def filament_per_rev(radius, line_width, layer_height):
    circ = 2 * math.pi * radius
    vol = circ * line_width * layer_height
    return vol / FILAMENT_AREA


# ---------- Safe arm move (collision avoidance) ----------
def safe_move(left_arm, right_arm,
              lx, ly, lz, roll_l, pitch_l, yaw_l, speed_l,
              rx, ry, rz, roll_r, pitch_r, yaw_r, speed_r):
    """
    Move both arms. If *either* target Y is in the range -50..50 mm,
    move sequentially (left first, then right). Otherwise move together.
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


# ---------- Cartesian point on disc for an arm ----------
def disc_point(angle_rad, radius, cx, cy):
    """Return (x, y) world coordinates for the bead at angle_rad on the
    turntable as seen by an arm whose turntable centre is (cx, cy)."""
    x = cx + radius * math.cos(angle_rad)
    y = cy + radius * math.sin(angle_rad)
    return x, y


# ---------- Z height for a given absolute revolution (0‑based) ----------
def path_z(base_z, rev):
    """Rev 0 = flat circle at base_z; rev > 0 = base_z + (rev-1)*Z_STEP_PER_REV."""
    if rev <= 1.0:
        return base_z
    return base_z + (rev - 1.0) * Z_STEP_PER_REV


# ---------- Run one phase of the print ----------
def run_phase(arm_ctrl, tool, eff_radius, cx, cy, base_z,
              start_rev, end_rev, turntable, extruder,
              second_arm_ctrl=None, second_tool=None,
              second_cx=None, second_cy=None, second_base_z=None,
              angle_offset_second=0.0):
    """
    Print a spiral section from start_rev to end_rev using the given arm.
    If second_arm_ctrl is provided, that arm co‑prints at the same radius,
    180° offset (or angle_offset_second) from the main arm.

    The turntable rotation speed is calculated so that one revolution takes
    exactly the time needed by the slower extruder at the desired FEED_RATE.
    A single continuous extrusion command is used for each active extruder.
    """
    # Number of revolutions in this phase
    revs = end_rev - start_rev
    if revs <= 0:
        return end_rev

    # Filament length per revolution for each active nozzle
    len_per_rev_main = filament_per_rev(eff_radius, LINE_WIDTH, LAYER_HEIGHT)
    # Time for one rev at FEED_RATE (main)
    t_per_rev_main = len_per_rev_main / FEED_RATE

    if second_arm_ctrl is not None:
        len_per_rev_second = filament_per_rev(eff_radius, LINE_WIDTH, LAYER_HEIGHT)
        t_per_rev_second = len_per_rev_second / FEED_RATE
        # Use the longer time so both finish together
        rev_time = max(t_per_rev_main, t_per_rev_second)
    else:
        rev_time = t_per_rev_main

    # Turntable speed (°/s) to make one rev in rev_time seconds
    turntable_speed = 360.0 / rev_time
    total_rev_time = rev_time * revs

    # Total filament lengths for the whole phase
    total_len_main = len_per_rev_main * revs
    if second_arm_ctrl is not None:
        total_len_second = len_per_rev_second * revs

    logger.info("Phase revs %.1f–%.1f, turntable %.1f °/s, %.1f s",
                start_rev, end_rev, turntable_speed, total_rev_time)
    logger.info("Main filament: %.0f mm @ %.1f mm/s",
                total_len_main, FEED_RATE)
    if second_arm_ctrl:
        logger.info("Second filament: %.0f mm @ %.1f mm/s",
                    total_len_second, FEED_RATE)

    # Move arms to start positions (angle = 0 rad for start of phase)
    # We keep the same global start angle for all phases (0 rad for simplicity).
    start_angle = 0.0   # can be adjusted if needed

    main_x, main_y = disc_point(start_angle, eff_radius, cx, cy)
    main_z = path_z(base_z, start_rev)

    # For co‑printing, second arm is offset by angle_offset_second (should be π)
    if second_arm_ctrl is not None:
        second_angle = start_angle + angle_offset_second
        second_x, second_y = disc_point(second_angle, eff_radius, second_cx, second_cy)
        second_z = path_z(second_base_z, start_rev)
        safe_move(arm_ctrl.arm, second_arm_ctrl.arm,
                  main_x, main_y, main_z, 180, 45, 0, 100,
                  second_x, second_y, second_z, 180, 45, 0, 100)
    else:
        arm_ctrl.move_to(main_x, main_y, main_z, roll=180, pitch=45, yaw=0,
                         speed=100, wait=True)

    # Pre‑extrude for the main arm
    extruder.extrude(tool, PRE_EXTRUDE, PRE_EXTRUDE_SPEED, wait=True)

    # Start continuous extrusion for the main arm (non‑blocking)
    extruder.extrude(tool, total_len_main, FEED_RATE, wait=False)

    # Start second arm extrusion if present (non‑blocking)
    if second_arm_ctrl is not None:
        extruder.extrude(second_tool, total_len_second, FEED_RATE, wait=False)

    # Start turntable rotation (non‑blocking)
    turntable.rotate_absolute(revs * 360, turntable_speed, wait=False)

    # Arm motion loop – keep both arms at the correct disc position during rotation.
    # This ensures the nozzles follow the spiral.
    t_start = time.time()
    while time.time() - t_start < total_rev_time:
        elapsed = time.time() - t_start
        frac = elapsed / total_rev_time   # fraction of phase completed
        cur_rev = start_rev + frac * revs
        cur_angle = start_angle + cur_rev * 2 * math.pi   # absolute disc angle

        main_x, main_y = disc_point(cur_angle, eff_radius, cx, cy)
        main_z = path_z(base_z, cur_rev)

        if second_arm_ctrl is not None:
            second_angle = cur_angle + angle_offset_second
            second_x, second_y = disc_point(second_angle, eff_radius,
                                            second_cx, second_cy)
            second_z = path_z(second_base_z, cur_rev)
            # Use safe_move to update both simultaneously (they should be far apart)
            safe_move(arm_ctrl.arm, second_arm_ctrl.arm,
                      main_x, main_y, main_z, 180, 45, 0, 100,
                      second_x, second_y, second_z, 180, 45, 0, 100)
        else:
            arm_ctrl.move_to(main_x, main_y, main_z,
                             roll=180, pitch=45, yaw=0, speed=100, wait=False)
        time.sleep(0.2)

    # Wait for turntable to stop
    while turntable.is_moving():
        time.sleep(0.1)
    turntable.wait_ok()

    # Small retract after phase
    extruder.extrude(tool, -2, 10, wait=True)
    if second_arm_ctrl is not None:
        extruder.extrude(second_tool, -2, 10, wait=True)

    return end_rev


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
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
    extruder.set_relative_extrusion()

    # Tool mapping (from earlier: tool 0 = E axis, tool 1 = X axis)
    TOOL_RIGHT = 0   # right arm uses E (tool 0)
    TOOL_LEFT  = 1   # left arm uses X (tool 1)

    # Effective radii (same for both since they print at the same physical radius)
    EFF_RADIUS = RADIUS

    # ---- Initial parking and approach ----
    # Park both arms safely
    logger.info("Parking both arms...")
    right.arm.set_position(PARK_X, PARK_Y, PARK_Z, 180, 45, 0, speed=100, wait=True)
    left.arm.set_position(PARK_X, -PARK_Y, PARK_Z, 180, 45, 0, speed=100, wait=True)

    # Phase 1: Right arm prints the base (revs 0 to RIGHT_REVS)
    logger.info("=== PHASE 1: Right arm base (%.0f revs) ===", RIGHT_REVS)
    run_phase(right, TOOL_RIGHT, EFF_RADIUS, TT_CX_RIGHT, TT_CY_RIGHT,
              Z_RIGHT, 0.0, RIGHT_REVS, turntable, extruder)

    # Phase 2: Right arm retracts and parks
    logger.info("=== PHASE 2: Right arm parks ===")
    right.arm.set_position(PARK_X, PARK_Y, PARK_Z, 180, 45, 0, speed=100, wait=True)

    # Phase 3: Left arm continues (revs RIGHT_REVS to RIGHT_REVS+LEFT_REVS)
    logger.info("=== PHASE 3: Left arm continues (%.0f revs) ===", LEFT_REVS)
    # Move left arm to starting point at the end of right's phase
    start_angle = 0.0
    start_x, start_y = disc_point(start_angle, EFF_RADIUS, TT_CX_LEFT, TT_CY_LEFT)
    start_z = path_z(Z_LEFT, RIGHT_REVS)
    left.move_to(SAFE_X, start_y, SAFE_Z, 180, 45, 0, speed=100, wait=True)
    left.move_to(start_x, start_y, start_z, 180, 45, 0, speed=100, wait=True)

    run_phase(left, TOOL_LEFT, EFF_RADIUS, TT_CX_LEFT, TT_CY_LEFT,
              Z_LEFT, RIGHT_REVS, RIGHT_REVS + LEFT_REVS, turntable, extruder)

    # Phase 4: Both arms together 180° apart (revs RIGHT_REVS+LEFT_REVS to total)
    total_revs = RIGHT_REVS + LEFT_REVS + COMBINED_REVS
    logger.info("=== PHASE 4: Both arms co‑printing 180° apart (%.0f revs) ===", COMBINED_REVS)

    # Position right arm opposite to left arm (offset π)
    run_phase(left, TOOL_LEFT, EFF_RADIUS, TT_CX_LEFT, TT_CY_LEFT,
              Z_LEFT, RIGHT_REVS + LEFT_REVS, total_revs, turntable, extruder,
              second_arm_ctrl=right,
              second_tool=TOOL_RIGHT,
              second_cx=TT_CX_RIGHT,
              second_cy=TT_CY_RIGHT,
              second_base_z=Z_RIGHT,
              angle_offset_second=math.pi)

    # ---- Finish: retract, disable, park ----
    logger.info("Print finished. Retracting and parking.")
    extruder.extrude(TOOL_LEFT, -2, 10, wait=True)
    extruder.extrude(TOOL_RIGHT, -2, 10, wait=True)
    extruder.send_gcode("M18 E X")

    # Move to standby positions safely
    final_z = path_z(Z_LEFT, total_revs)
    safe_z = max(SAFE_Z, final_z + 20)
    right.arm.set_position(SAFE_X, PARK_Y, safe_z, 180, 45, 0, speed=100, wait=True)
    left.arm.set_position(SAFE_X, -PARK_Y, safe_z, 180, 45, 0, speed=100, wait=True)

    # Return to your usual ready positions
    left.move_to(572.0, 202.4, 152.2, roll=180, pitch=45, yaw=0, speed=100, wait=False)
    right.move_to(573.0, 217.5, 160.9, roll=180, pitch=45, yaw=0, speed=100, wait=True)

    logger.info("✅ Demo spiral complete.")


if __name__ == "__main__":
    main()