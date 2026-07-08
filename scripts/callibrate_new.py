import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import time

from src.arm_controller import ArmController
from src.turntable_controller import TurntableController
from src.extruder_controller import ExtruderController
from config_loader import load_config

cfg = load_config()

# ------------------------------------------------------------------
# Connect Hardware
# ------------------------------------------------------------------

right = ArmController(cfg['arms']['right']['ip'], "right")
right.connect()

left = ArmController(cfg['arms']['left']['ip'], "left")
left.connect()

turntable = TurntableController(
    host=cfg['turntable']['controller_ip'],
    axis=cfg['turntable']['axis']
)
turntable.connect()

extruder = ExtruderController(
    cfg['moonraker']['host'],
    cfg['moonraker']['port']
)

#Heat Extruders
temp = cfg['defaults']['temperature']['tool0']
extruder.heat_and_wait(0, temp)
extruder.heat_and_wait(1, temp)

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

R_OUTER = 140
R_CENTRE = 138
R_INNER = 136

TT_CX_RIGHT = 567.7
TT_CY_RIGHT = 9.7
TT_CX_LEFT  = 574.1
TT_CY_LEFT  = -5.4

RIGHT_YAW = 20.0
ROLL = 180.0
PITCH = 45.0
Z_RIGHT = 152.0
CENTRE_Z_OFFSET = 0.8

ARM_SPEED = 100
TABLE_SPEED = 20
SPEED_RIGHT = 3.0


# ------------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------------

def move_and_rotate(arm, extruder_id, x, y, z_offset = 0):
    """
    Move to a point, extrude, rotate turntable 360°, retract.
    """
    arm.arm.set_position(
        x, y, Z_RIGHT + z_offset,
        ROLL, PITCH, RIGHT_YAW,
        speed=ARM_SPEED,
        wait=True
    )

    extruder.extrude(extruder_id, 52.5, SPEED_RIGHT, wait=False)

    turntable.rotate_linear(360,TABLE_SPEED)

    #extruder.send_gcode("CANCEL_PRINT\n")

    arm.arm.set_position(
        x, y, Z_RIGHT + 5,
        ROLL, PITCH, RIGHT_YAW,
        speed=ARM_SPEED,
        wait=True
    )


def draw_x_circle(arm, extruder_id, cx, cy, radius, positive=False, z_offset = 0):
    if positive:
        x = cx + radius
    else:
        x = cx - radius
    move_and_rotate(arm, extruder_id, x, cy, z_offset)

def draw_y_circle(arm, extruder_id, cx, cy, radius, positive=False, z_offset = 0):
    if positive:
        y = cy + radius
    else:
        y = cy - radius
    move_and_rotate(arm, extruder_id, cx, y, z_offset)


def draw_reference_x(arm, extruder_id, cx, cy):
    draw_x_circle(arm, extruder_id, cx, cy, R_OUTER)
    draw_x_circle(arm, extruder_id, cx, cy, R_INNER)


def draw_reference_y(arm, extruder_id, cx, cy):
    draw_y_circle(arm, extruder_id, cx, cy, R_OUTER, z_offset=0.8)
    draw_y_circle(arm, extruder_id, cx, cy, R_INNER, z_offset=0.8)


def draw_centre_x(arm, extruder_id, cx, cy):
    draw_x_circle(arm, extruder_id, cx, cy, R_CENTRE, positive=False)
    draw_x_circle(arm, extruder_id, cx, cy, R_CENTRE, positive=True,  z_offset=1.8)


def draw_centre_y(arm, extruder_id, cx, cy):
    draw_y_circle(arm, extruder_id, cx, cy, R_CENTRE, positive=False, z_offset = 0.8)
    draw_y_circle(arm, extruder_id, cx, cy, R_CENTRE, positive=True, z_offset = 0.8)

# ------------------------------------------------------------------
# Calibration
# ------------------------------------------------------------------

def run_calibration(arm_name, axis):
    if arm_name == "right":
        arm = right
        extruder_id = 0
        cx = TT_CX_RIGHT
        cy = TT_CY_RIGHT
    elif arm_name == "left":
        arm = left
        extruder_id = 1
        cx = TT_CX_LEFT
        cy = TT_CY_LEFT
    else:
        raise ValueError("Arm must be left or right")

    #Purge
    extruder.extrude(extruder_id, 20, SPEED_RIGHT, wait=True)
    time.sleep(5)

    if axis == "x":
        draw_reference_x(arm, extruder_id, cx, cy)
        draw_centre_x(arm, extruder_id, cx, cy)
    elif axis == "y":
        draw_reference_y(arm, extruder_id, cx, cy)
        draw_centre_y(arm, extruder_id, cx, cy)
    else:
        raise ValueError("Axis must be x or y")

    while True:
        state = input("Middle / Inner / Outer ? ").strip().lower()

        if state == "middle":
            print("Calibration Complete")
            print(f"TT_CX = {cx}")
            print(f"TT_CY = {cy}")
            arm.home(wait=True)
            return

        if state not in ("inner", "outer"):
            print("Invalid input")
            continue

        scale = float(input("Percentage correction: ")) / 100.0
        offset = (R_OUTER - R_INNER) * scale

        if axis == "x":
            if state == "inner":
                cx += offset
            else:
                cx -= offset
            draw_centre_x(arm, extruder_id, cx, cy)

        else:
            if state == "inner":
                cy += offset
            else:
                cy -= offset
            draw_centre_y(arm, extruder_id, cx, cy)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
arm_name = input("Calibrate Left or Right arm? ").strip().lower()
axis = input("Calibrate X or Y? ").strip().lower()

run_calibration(arm_name, axis)
