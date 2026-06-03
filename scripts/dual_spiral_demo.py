#!/usr/bin/env python3
"""
Phased cooperative dual-arm spiral, frame-stepped on the TURNTABLE clock.

Print sequence
--------------
  Phase 1  (revs 0..N1)         RIGHT arm.  Rev 0 = flat circle, then spiral up.
  Phase 2                       RIGHT arm parks at a holding pose (no extrusion).
  Phase 3  (revs N1..N1+N2)     LEFT arm continues the SAME tower, spiralling up
                                from exactly where the right arm stopped.
  Phase 4  (revs N1+N2..NTOTAL) LEFT arm keeps printing the tower.  Half a turn
                                into the re-join revolution the RIGHT arm drops
                                onto the point directly above the current bead
                                and both arms co-print (physically 180° apart)
                                to the top.

Why this version is smooth
--------------------------
The previous script fired ONE bulk `extrude_sync` and ONE bulk `rotate_absolute`,
then streamed arm moves on an independent `time.time()` loop.  Three open-loop
clocks drift apart -> choppy deposition.

Here the TURNTABLE is the single master clock.  Each frame:
    1. advance the bed by a fixed angle with a BLOCKING `rotate_relative`,
    2. command the arm move for the new disc angle (non-blocking),
    3. push the extrusion increment for *this frame's* arc length.
Extrusion is its own per-frame command (decoupled from the arm move) sized to
the bead actually laid down, so flow always tracks motion.

Extruder wiring (from extruder_controller.py)
---------------------------------------------
    tool 0  -> E axis   (one Hemera)
    tool 1  -> X axis   (the "X-motor" second extruder)
Map each ARM to whichever tool physically feeds it via ARM_TOOL below.

Extrusion volume (correct basis)
--------------------------------
    bead_volume      = path_length * LINE_WIDTH * LAYER_HEIGHT
    filament_length  = bead_volume / filament_cross_section_area
This is the volumetric basis used in cylinder_incremental.py, not
circumference*revs (which ignores line width / layer height / filament area).
The per-frame filament increment is computed from the arc length printed in
that frame, so the TOTAL fed over a phase equals the true toolpath length:
the base circle plus the rising spiral up to the final circle.
"""

import sys, os, time, math, logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.arm_controller import ArmController
from src.turntable_controller import TurntableController
from src.extruder_controller import ExtruderController
from config_loader import load_config

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dual_spiral_phased")

# ==================== CONFIGURABLE ====================
# ---------- Turntable centre (measured per arm) ----------
TT_CX_LEFT  = 575.1
TT_CY_LEFT  = -11.1
TT_CX_RIGHT = 575.6
TT_CY_RIGHT = 3.5

# ---------- Print geometry ----------
RADIUS          = 120.0      # base printed radius (mm)
LEFT_R_OFFSET   = 3.0        # radial offset for left arm
RIGHT_R_OFFSET  = 5.2        # radial offset for right arm
START_ANGLE_DEG = 135.0      # global start angle (degrees)

# ---------- Heights ----------
Z_LEFT   = 151.7             # left nozzle base height
Z_RIGHT  = 151.9             # right nozzle base height
Z_STEP_PER_REV = 0.4         # mm vertical advance per full turn (layer height)

# ---------- Bead / filament ----------
LINE_WIDTH        = 0.4      # mm
LAYER_HEIGHT      = 0.4      # mm (bead thickness == Z_STEP_PER_REV here)
FILAMENT_DIAMETER = 1.75     # mm

# ---------- Phases (in full revolutions) ----------
RIGHT_PHASE1_REVS = 3        # revs the right arm prints first (circle + spiral)
LEFT_REVS         = 3        # revs the left arm prints next
TOTAL_REVS        = 110      # total revolutions to final height
REJOIN_LAYER      = 7        # 1-indexed rev on which the right arm re-joins
REJOIN_AT_FRAC    = 0.5      # half-way through that rev's rotation

# ---------- Frame-stepped turntable control ----------
ROTATION_SPEED  = 10.0       # °/s (bed speed within each frame step)
STEP_DEG        = 5.0        # bed advance per frame (toolpath resolution)

# ---------- Arm speeds ----------
PRINT_ARM_SPEED = 100        # mm/s arm move while printing
TRAIL_SPEED     = 50         # mm/s arm move during approach
RAPID_SPEED     = 100        # mm/s arm rapid

# ---------- Filament feed rate cap (mm/s of filament) ----------
FEED_RATE = 5.0              # used as the F for per-frame extrude moves

# ---------- Which extruder tool feeds which arm ----------
# tool 0 = E axis, tool 1 = X axis (the "X-motor" extruder)
ARM_TOOL = {"right": 0, "left": 1}

# ---------- Holding pose for the idle right arm ----------
HOLD_X = 450.0
HOLD_Y = 150.0
HOLD_Z = 220.0

# ---------- Safe approach ----------
SAFE_X = 420.0
SAFE_Z = 250.0
PRE_EXTRUDE = 8.0            # mm filament primed before first frame
PRE_EXTRUDE_FEED = 3.0      # mm/s
# =====================================================

FILAMENT_AREA = math.pi * (FILAMENT_DIAMETER / 2.0) ** 2
START_RAD     = math.radians(START_ANGLE_DEG)
EFF_R_LEFT    = RADIUS + LEFT_R_OFFSET
EFF_R_RIGHT   = RADIUS + RIGHT_R_OFFSET


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------
def disc_point(angle_rad, radius, cx, cy):
    """World XY of a bead at (radius, angle) on the disc as seen by an arm."""
    return cx + radius * math.cos(angle_rad), cy + radius * math.sin(angle_rad)


def path_z(base_z, rev):
    """
    Z for a given absolute revolution count.
    Rev 0..1 is a FLAT circle (constant Z); the spiral rise begins at rev 1.
    """
    if rev <= 1.0:
        return base_z
    return base_z + (rev - 1.0) * Z_STEP_PER_REV


def filament_for_arc(arc_len_mm):
    """Convert a deposited arc length to filament length via bead volume."""
    bead_volume = arc_len_mm * LINE_WIDTH * LAYER_HEIGHT
    return bead_volume / FILAMENT_AREA


def steps_for(revs):
    """Number of fixed-angle frames to cover `revs` revolutions."""
    return max(1, int(math.ceil(revs * 360.0 / STEP_DEG)))


# --------------------------------------------------------------------------
# Core per-phase printing loop (turntable = master clock)
# --------------------------------------------------------------------------
def print_phase(arm_ctrl, tool, eff_radius, cx, cy, base_z,
                rev_start, rev_end, turntable, extruder,
                second_cfg=None):
    """
    Print one phase by stepping the turntable in fixed angular increments.

    `arm_ctrl` is an ArmController (NOT the raw XArmAPI); we call its
    `move_to(...)` wrapper, which forwards to XArmAPI.set_position.

    For each frame:
      * advance the bed STEP_DEG (blocking)  -> the master clock tick,
      * move the arm to the new disc angle (non-blocking),
      * extrude the filament for the arc swept this frame on `tool`.

    `second_cfg` optionally co-prints a second arm in the same frames (used in
    phase 4 once the right arm has re-joined). It is a dict:
    {arm_ctrl, tool, eff_radius, cx, cy, base_z, active_from_rev}.
    """
    n = steps_for(rev_end - rev_start)
    rev_span = rev_end - rev_start
    prev_rev = rev_start

    # Move arm to the start point of this phase before stepping.
    sx, sy = disc_point(START_RAD, eff_radius, cx, cy)
    arm_ctrl.move_to(sx, sy, path_z(base_z, rev_start),
                     roll=180, pitch=45, yaw=0, speed=TRAIL_SPEED, wait=True)

    second_started = False
    for i in range(n):
        # ---- 1. master clock tick: advance the bed a fixed angle ----
        # Last frame may be a partial step so the phase ends exactly on rev_end.
        remaining_deg = (rev_end - prev_rev) * 360.0
        step = min(STEP_DEG, remaining_deg)
        if step <= 0:
            break
        turntable.rotate_relative(step, ROTATION_SPEED, wait=True)

        cur_rev = prev_rev + step / 360.0
        # Disc angle the spiral has reached (start angle + cumulative rotation).
        angle = START_RAD + cur_rev * 2.0 * math.pi

        # ---- 2. arm move for the new disc angle (non-blocking) ----
        x, y = disc_point(angle, eff_radius, cx, cy)
        z = path_z(base_z, cur_rev)
        arm_ctrl.move_to(x, y, z, roll=180, pitch=45, yaw=0,
                         speed=PRINT_ARM_SPEED, wait=False)

        # ---- 2b. optional second (re-joining) arm, co-printing ----
        if second_cfg is not None:
            if cur_rev >= second_cfg["active_from_rev"]:
                s = second_cfg
                # Re-join ON TOP of the active bead: identical commanded XY/Z,
                # 180° physical separation handled by arm geometry.
                if not second_started:
                    s["arm_ctrl"].move_to(x, y, z, roll=180, pitch=45, yaw=0,
                                          speed=RAPID_SPEED, wait=True)
                    second_started = True
                else:
                    s["arm_ctrl"].move_to(x, y, z, roll=180, pitch=45, yaw=0,
                                          speed=PRINT_ARM_SPEED, wait=False)

        # ---- 3. extrusion increment for THIS frame's arc (decoupled) ----
        # Arc length on the primary path this frame.
        d_rev = cur_rev - prev_rev
        arc_primary = eff_radius * (d_rev * 2.0 * math.pi)
        extruder.extrude(tool, filament_for_arc(arc_primary),
                         FEED_RATE, wait=False)

        # Second arm extrusion (only while it is co-printing).
        if second_cfg is not None and second_started:
            s = second_cfg
            arc_second = s["eff_radius"] * (d_rev * 2.0 * math.pi)
            extruder.extrude(s["tool"], filament_for_arc(arc_second),
                             FEED_RATE, wait=False)

        prev_rev = cur_rev

    return prev_rev


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

    right_tool = ARM_TOOL["right"]
    left_tool  = ARM_TOOL["left"]

    rejoin_rev = (REJOIN_LAYER - 1) + REJOIN_AT_FRAC   # absolute rev for re-join

    logger.info("Effective radii: left %.1f mm, right %.1f mm",
                EFF_R_LEFT, EFF_R_RIGHT)
    logger.info("Filament/rev: right %.2f mm, left %.2f mm",
                filament_for_arc(2 * math.pi * EFF_R_RIGHT),
                filament_for_arc(2 * math.pi * EFF_R_LEFT))

    # ---- Approach: high safe point, then prime the right tool ----
    sx_r, sy_r = disc_point(START_RAD, EFF_R_RIGHT, TT_CX_RIGHT, TT_CY_RIGHT)
    right.move_to(SAFE_X, sy_r, SAFE_Z, 180, 45, 0, speed=RAPID_SPEED, wait=True)
    right.move_to(sx_r, sy_r, Z_RIGHT, 180, 45, 0, speed=TRAIL_SPEED, wait=True)
    extruder.extrude(right_tool, PRE_EXTRUDE, PRE_EXTRUDE_FEED, wait=True)

    # =========================================================
    # PHASE 1 — RIGHT arm: circle (rev 0) then spiral up
    # =========================================================
    logger.info("PHASE 1: right arm, revs 0..%d", RIGHT_PHASE1_REVS)
    rev_now = print_phase(right, right_tool, EFF_R_RIGHT,
                          TT_CX_RIGHT, TT_CY_RIGHT, Z_RIGHT,
                          rev_start=0.0, rev_end=float(RIGHT_PHASE1_REVS),
                          turntable=turntable, extruder=extruder)

    # =========================================================
    # PHASE 2 — RIGHT arm parks (no extrusion)
    # =========================================================
    logger.info("PHASE 2: right arm to holding pose")
    extruder.extrude(right_tool, -2.0, 10.0, wait=True)          # small retract
    right.arm.set_position(HOLD_X, HOLD_Y, HOLD_Z, 180, 45, 0,
                           speed=RAPID_SPEED, wait=True)

    # =========================================================
    # PHASE 3 — LEFT arm continues the SAME tower upward
    # =========================================================
    logger.info("PHASE 3: left arm, revs %d..%d",
                RIGHT_PHASE1_REVS, RIGHT_PHASE1_REVS + LEFT_REVS)
    sx_l, sy_l = disc_point(START_RAD, EFF_R_LEFT, TT_CX_LEFT, TT_CY_LEFT)
    left.move_to(SAFE_X, sy_l, SAFE_Z, 180, 45, 0, speed=RAPID_SPEED, wait=True)
    left.move_to(sx_l, sy_l, path_z(Z_LEFT, RIGHT_PHASE1_REVS),
                 180, 45, 0, speed=TRAIL_SPEED, wait=True)
    extruder.extrude(left_tool, PRE_EXTRUDE, PRE_EXTRUDE_FEED, wait=True)

    rev_now = print_phase(left, left_tool, EFF_R_LEFT,
                          TT_CX_LEFT, TT_CY_LEFT, Z_LEFT,
                          rev_start=float(RIGHT_PHASE1_REVS),
                          rev_end=float(RIGHT_PHASE1_REVS + LEFT_REVS),
                          turntable=turntable, extruder=extruder)

    # =========================================================
    # PHASE 4 — LEFT prints to the top; RIGHT re-joins half-way
    #           through the re-join rev, co-printing 180° opposite.
    # =========================================================
    logger.info("PHASE 4: left to top (rev %d), right re-joins at rev %.1f",
                TOTAL_REVS, rejoin_rev)
    # Right arm pre-positions near the tower (above current bead height) so its
    # drop-in is short; it does NOT extrude until print_phase activates it.
    right.arm.set_position(SAFE_X, sy_r, SAFE_Z, 180, 45, 0,
                           speed=RAPID_SPEED, wait=True)
    extruder.extrude(right_tool, PRE_EXTRUDE, PRE_EXTRUDE_FEED, wait=True)

    right_cfg = {
        "arm_ctrl": right,
        "tool": right_tool,
        "eff_radius": EFF_R_RIGHT,
        "cx": TT_CX_RIGHT,
        "cy": TT_CY_RIGHT,
        "base_z": Z_RIGHT,
        "active_from_rev": rejoin_rev,
    }

    rev_now = print_phase(left, left_tool, EFF_R_LEFT,
                          TT_CX_LEFT, TT_CY_LEFT, Z_LEFT,
                          rev_start=float(RIGHT_PHASE1_REVS + LEFT_REVS),
                          rev_end=float(TOTAL_REVS),
                          turntable=turntable, extruder=extruder,
                          second_cfg=right_cfg)

    # ---- Finish: retract, disable feeds, raise and stand by ----
    logger.info("Finishing: retract + park")
    extruder.extrude(left_tool, -2.0, 10.0, wait=True)
    extruder.extrude(right_tool, -2.0, 10.0, wait=True)
    extruder.send_gcode("M18 E X")

    while turntable.is_moving():
        time.sleep(0.1)
    turntable.wait_ok()

    final_z = path_z(Z_LEFT, TOTAL_REVS)
    left.arm.set_position(SAFE_X, sy_l, max(SAFE_Z, final_z + 20),
                          180, 45, 0, speed=RAPID_SPEED, wait=False)
    right.arm.set_position(SAFE_X, sy_r, max(SAFE_Z, final_z + 20),
                           180, 45, 0, speed=RAPID_SPEED, wait=True)
    left.arm.set_position(572.0, 202.4, 152.2, 180, 45, 0, speed=RAPID_SPEED, wait=False)
    right.arm.set_position(573.0, 217.5, 160.9, 180, 45, 0, speed=RAPID_SPEED, wait=True)

    logger.info("Phased dual-arm spiral complete. Final height %.1f mm", final_z)


if __name__ == "__main__":
    main()
