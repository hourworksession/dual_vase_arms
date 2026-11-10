"""Rounded-square test on one or both arms: prime, flat loop, spiral, final flat loop.

Same bead and cell link as cylinder_test.py (which this imports), but the outline is a square with
filleted corners, and the EXTRUDER FOLLOWS WHATEVER IS MOVING:
  * straights: bed STOPPED, the arm moves along the side at V_LINE; the extruder runs at the rate
               that lays a W x L_H bead at V_LINE
  * corners:   bed ON at VB (rad/s) while the arm makes the compensating move that keeps the fillet
               about the corner's own centre; the extruder rate is set every tick in proportion to
               the MEASURED bed speed (rate = bead per mm x RF x omega), so it ramps with the bed and
               stops when the bed stops. Same bead on the corner as on the straight, whatever VB is.
Every corner turns the bed 90 deg, so after each corner the next side of the square sits exactly
where the last one was in the arm's frame: the arm draws the SAME straight line and the SAME corner
move every segment, the bed indexes the part under it.

Run from the repo root (test Klipper config loaded, hot ends hot):

    python3 square_test.py T=0 SIDE=80 RF=10 L_H=0.6 LOOPS=20 --dry-run                 # right arm, defaults
    python3 square_test.py T=0 SIDE=80 RF=10 L_H=0.6 V_LINE=8 VB=0.4 LOOPS=20            # slower corners
    python3 square_test.py T=2 SIDE=80 RF=10 L_H=0.6 V_LINE=8 VB=0.55 FLOW_K=1.3 LOOPS=20  # both arms
    python3 square_test.py T=0 SIDE=80 RF=10 LOOPS=2 --cold                               # moves only

Values (KEY=value, any order). In T=2 the plain keys are the RIGHT arm; *_L keys override the left.
    T       0 = right, 1 = left, 2 = both                                    (required)
    SIDE    outside side length of the square, mm (bead centreline)          (required)
    RF      fillet (corner) radius, mm, bead centreline                      (default 10)
    L_H     bead height (layer height), mm                                   (default 0.5)
    W       line width, mm                                                   (default 1.0, both arms)
    FLOW_K  measured / planned plastic from a test print
    V_LINE  arm speed on the straights, mm/s. Pick one the arm starts and stops cleanly at (default 8)
    VB      bed speed on the corners, rad/s (default V_LINE / RF: the fillet moves under the nozzle
            at the same speed as the straights). Lower it to let the bed index cleanly; the extruder
            slows with it, so the bead stays the same
    The filament rate is never set directly: it is W x L_H / (filament area x FLOW_K) mm of filament
    per mm of bead, times the speed of whatever is moving (arm on the straights, bed x RF on the
    corners, measured). E=... is accepted only as a way of giving V_LINE (the speed that E would lay
    the bead at).
    P       Z rise per loop of the outline, mm                     (default n x L_H, n = arms used)
    Z0      zero height, nozzle touching the disc            (defaults as cylinder_test.py)
    W_L, FLOW_K_L, Z0_L   left arm in T=2 (same nozzle: same W and FLOW_K as the right unless set)
    MAX_VB  bed speed ceiling on the corners, rad/s                           (default 3.1416)
    MAX_VT  arm speed ceiling, % of 100 mm/s                                 (default 100)
    FIRST   first layer above Z0, mm                                         (default L_H)
    SPIRAL  spiral loops (default 3);  LOOPS  total loops;  HEIGHT  wall height above the first
            layer, mm (sets SPIRAL = HEIGHT / P, wins over LOOPS)
    RAMP_STEPS, RAMP_EVERY   ease into the spiral exactly as cylinder_test.py (5 steps every 0.125 loop)
    PRIME_MM, PRIME_S, PRIME_Z   prime move-in mm, time s, start height above Z0 mm (15, 5, 2)
            The prime runs in along the line of the first side at half the straight-line rate.
    FIL     filament diameter, mm (1.75);  MIN_TEMP  refuse below this, C (190)
    ANGLE, CX, CY (and _L)   print direction and turntable centre in each arm's frame (as cylinder):
            the arm's side of the square sits at SIDE/2 from the axis in direction ANGLE
    DR (and DR_L)  outline trim, mm: offsets the whole outline outwards (side and fillet both grow).
            Left default 1.0 as in cylinder_test.py
    BED_DIR -1 if a positive bed velocity turns the disc CLOCKWISE seen from above (xArm frames are
            z up, x towards y), +1 if anticlockwise.  Default -1: measured on the cell, 9 Nov 2026.
            Wrong sign: every corner swings the nozzle across to the opposite corner, and the print
            comes out as a circle of radius (S - RF) sqrt2 with the square inside it.
    CORNER_LEAD_S  starting lead: how far ahead of the measured bed angle the arm is sent on corners,
            s (0.15). Then closed loop: each tick the arm's REPORTED position is placed on the corner
            path and the lead is corrected by LEAD_GAIN x (bed angle - arm angle), so the nozzle sits
            on the bed's angle whatever the arm's latency. A lagging arm draws the fillet rotated about
            the axis, i.e. shifted into the next side.
    LEAD_GAIN  that correction gain per tick (0.5). 0 = fixed lead.
    SERVO   1: drive the arms in xArm servo mode, a pose every 10 ms (set_servo_cartesian, firmware
            1.6+), with the outline walked by TIME: the nozzle's progress along the outline runs at
            V_LINE everywhere (sides and fillets alike) and each pose is the outline point for this
            instant turned by the bed's MEASURED angle. The bed makes its 90 deg index as its own move
            (ramps included) during each fillet; the arm starts the fillet tangentially while the bed
            is still at rest, sweeps while it turns, and is back on the tangent of the next side as
            it stops, so there is no reversal and no stop at the hand-over. The extruder rate is
            constant. VB only sets how fast the index is made (default: fast enough to finish inside
            the fillet's time). 0 (default): queued set_position moves at 20 Hz, the fillet tied to
            the bed's progress, as before. Poses are rate limited to 2 mm per tick; the arms go back
            to position mode at the end or on abort.
    SERVO_LEAD_S  lead ahead of the measured bed angle in servo mode, s (0.03)
    CORNER_FLOOR  the corner extruder rate never drops below this fraction of the straight-line rate
            (0.3), so the flow does not have to restart from nothing at the hand-over to the side.
    BLEND   blend radius for the streamed corner moves, mm                   (default 2)
    STOP_LEAD  stop the bed this many degrees before the corner's 90 (its deceleration), deg (0);
            the arm finishes any part of the fillet the bed did not give, at V_LINE
    RATE_MIN  below this filament rate (mm/s) the tool is switched off rather than crawled (0.1)
    RATE_HZ   how often the extruder rate may be re-sent to Klipper, per second (10). Extruder
            commands run on their own thread, so the motion loop never waits on Moonraker.
    BED_MOVE  1 (default): each corner is ONE 90 deg incremental move (the controller plans the
            accel, cruise and decel and lands on 90 exactly). 0: free-run at VB and stop on the
            measured angle, as before (the simulator only has this one).
    BED_ACC   bed acceleration/deceleration to ask the Aerotech for, deg/s2 (0 = leave the axis as
            configured). The spin-up from rest is the pause you see at each corner start: at
            45 deg/s, 500 deg/s2 is 0.09 s, the default ramp may be ten times that.

Flags: --dry-run (plan only), --sim (cell studio's simulators), --yes (no prompt),
       --cold (no extrusion, hot-end check skipped: arm and bed moves only).
Calibration (--find-z0, --find-centre) is in cylinder_test.py.

Geometry (per arm, in that arm's frame; C = turntable centre, n = unit vector at ANGLE, t = n turned
+90 deg, S = SIDE/2 + DR, R = RF + DR, d = S - R, dir = -BED_DIR = traversal sense of the outline):
    straight:  C + S n + x t,  x from -dir d to +dir d           (bed still)
    corner:    C + R n + Rot(BED_DIR x beta) (d n + dir d t),  beta 0..90 deg = measured bed angle
               since the corner began. A quarter circle of radius d sqrt2 about C + R n; in the BED
               frame it is the fillet of radius R about the corner centre.
    Arm speed on the corner = d sqrt2 x VB; capped by MAX_VT (then the bed is slowed to match).
Sequence, with n arms:  1. Prime (bed still).  2. Flat: 1/n loop at the first layer (two arms:
together the full outline).  3. Spiral: Z rises P per loop.  4. Final flat 1/n loop.
5. Extruders off.  6. Lift 10 mm and park.  7. Bed stopped.
Corner to straight: the bed is stopped one tick early and the arm keeps following the measured
bed angle through the bed's deceleration, the extruder slowing with it; the fillet's last part (if
the bed stops short) and the next side are then queued behind it with a blend radius.
Extruder commands are EXTRUDE_ON T= RATE= (re-sent whenever the rate changes, and at least every
2 s for the watchdog) and EXTRUDE_OFF. EXTRUDE_SCALE is reset to 100 for both tools at the start
of every run (it is sticky in Klipper) and never used otherwise.
Nothing is commanded below an arm's Z0. Ctrl+C aborts: extruders off, bed stop, arms lift.
Every run logs to runs/square_test_*.csv (commanded rate per arm included).
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))   # repo root
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "ui")]

from cell.runs.cylinder import (Abort, BASE_ARM_SPEED, COMMON, DEFAULTS, NOZZLE, PARK, SAFE_Z,  # noqa: E402
                           SIDE, WATCHDOG_S, Cell, abort, ramp_loss, report_deviation, spiral_dz)

SQUARE_COMMON = {**COMMON, "RF": 10.0, "BED_DIR": -1.0, "CORNER_LEAD_S": 0.15, "BLEND": 2.0, "STOP_LEAD": 0.0,
                 "V_LINE": 8.0, "RATE_MIN": 0.1, "BED_ACC": 0.0, "BED_MOVE": 1.0, "RATE_HZ": 10.0,
                 "CORNER_FLOOR": 0.3, "LEAD_GAIN": 0.5, "SERVO": 0.0, "SERVO_LEAD_S": 0.03}
TICK = 0.05                      # main loop period, s (position mode; servo mode runs at 0.01)
SERVO_STEP = 2.0                 # mm a servo pose may move the nozzle per tick
SETTLE_S = 3.0                   # max wait for the bed to come to rest after a corner
RATE_TOL = 0.05                  # re-send EXTRUDE_ON when the rate moves by more than this fraction


# ----------------------------------------------------------------------
def parse(argv):
    vals, flags = {}, set()
    for a in argv:
        if a.startswith("--"):
            flags.add(a)
        elif "=" in a:
            k, v = a.split("=", 1)
            vals[k.strip().upper()] = float(v)
        else:
            raise SystemExit(f"Not understood: {a!r} (use KEY=value, e.g. SIDE=80)")
    known = {"T", "SIDE", "RF", "L_H", "E", "P", "W", "FLOW_K", "Z0", "W_L", "FLOW_K_L", "Z0_L", "VB", "V_LINE",
             "MAX_VB", "MAX_VT", "FIRST", "SPIRAL", "LOOPS", "HEIGHT", "PRIME_MM", "PRIME_S", "PRIME_Z", "FIL",
             "MIN_TEMP", "ANGLE", "CX", "CY", "ANGLE_L", "CX_L", "CY_L", "DR", "DR_L", "RAMP_STEPS", "RAMP_EVERY",
             "BED_DIR", "CORNER_LEAD_S", "BLEND", "STOP_LEAD", "RATE_MIN", "BED_ACC", "BED_MOVE", "RATE_HZ",
             "CORNER_FLOOR", "LEAD_GAIN", "SERVO", "SERVO_LEAD_S"}
    unknown = sorted(set(vals) - known)
    if unknown:
        hint = ""
        if {"E_MIN", "E_MAX", "WAVE_T", "WAVES_PER_LOOP", "SHAPE", "E_L"} & set(unknown):
            hint = "  (the extruder rate follows the motion here; waves and E_L are cylinder_test.py settings)"
        raise SystemExit("Unknown setting(s): " + ", ".join(unknown) + hint)
    missing = [k for k in ("T", "SIDE") if k not in vals]
    if missing:
        raise SystemExit("Missing: " + ", ".join(missing) + "\n\n" + __doc__.split("Values")[0])
    vals.setdefault("L_H", 0.5)
    t = int(vals["T"])
    if t not in (0, 1, 2):
        raise SystemExit("T must be 0 (right), 1 (left) or 2 (both)")
    tools = [0, 1] if t == 2 else [t]
    g = {**SQUARE_COMMON, **vals, "T": t, "N": len(tools)}
    if g["RF"] <= 0 or g["RF"] >= g["SIDE"] / 2:
        raise SystemExit("RF must be above 0 and below SIDE / 2 (SIDE / 2 is a circle: use cylinder_test.py)")
    if g["BED_DIR"] not in (1.0, -1.0):
        raise SystemExit("BED_DIR must be +1 or -1")
    g["BED_DIR"] = int(g["BED_DIR"])
    g["DIR"] = -g["BED_DIR"]                               # outline traversal sense (bed turns the other way)
    g["NL"] = g["N"]                                       # beads per outline height: layered only
    g.setdefault("FIRST", g["L_H"])
    g.setdefault("P", g["NL"] * g["L_H"])
    g.setdefault("RAMP_STEPS", 5)
    g.setdefault("RAMP_EVERY", 0.125)
    if g["RAMP_STEPS"] < 1 or g["RAMP_EVERY"] <= 0:
        raise SystemExit("RAMP_STEPS must be 1 or more and RAMP_EVERY above 0")
    g["RAMP_STEPS"] = int(g["RAMP_STEPS"])
    g["C0"] = g["C1"] = 1.0 / g["NL"]
    if "HEIGHT" in vals:
        g["SPIRAL"] = (vals["HEIGHT"] + ramp_loss(g)) / g["P"]
    elif "LOOPS" in vals:
        if vals["LOOPS"] < g["C0"] + g["C1"]:
            raise SystemExit("LOOPS is too small for the opening and closing flat loops")
        g["SPIRAL"] = vals["LOOPS"] - g["C0"] - g["C1"]

    arms = {}
    first = tools[0]
    fil_area = math.pi * (g["FIL"] / 2) ** 2
    for tl in tools:
        sfx = "_L" if (t == 2 and tl == 1) else ""
        a = dict(DEFAULTS[tl])
        if sfx == "":
            for key in ("W", "Z0", "CX", "CY", "ANGLE", "DR"):
                if key in vals:
                    a[key] = vals[key]
            a["FLOW_K"] = g["FLOW_K"]
        else:
            for key in ("W", "Z0", "CX", "CY", "ANGLE", "FLOW_K", "DR"):
                if key + "_L" in vals:
                    a[key] = vals[key + "_L"]
            for key in ("W", "FLOW_K"):
                if key + "_L" not in vals:
                    a[key] = arms[first][key]
        dr = a.get("DR", 0.0)
        a["S"] = g["SIDE"] / 2 + dr                       # half side, bead centreline
        a["R"] = g["RF"] + dr                             # fillet radius, bead centreline
        a["D"] = a["S"] - a["R"]                          # half length of a straight
        a["K"] = a["W"] * g["L_H"] / (fil_area * a["FLOW_K"])   # mm of filament per mm of bead
        a["T"], a["side"], a["nozzle"] = tl, SIDE[tl], NOZZLE[tl]
        arms[tl] = a
    g["arms"] = arms
    lead = arms[first]
    if "E" in vals:                                        # E given: read it as the straight-line speed
        if "V_LINE" in vals:
            raise SystemExit("Give V_LINE or E, not both (E only sets the speed the bead would be laid at)")
        g["V_LINE"] = vals["E"] / lead["K"]
    if g["V_LINE"] <= 0:
        raise SystemExit("V_LINE must be above 0")
    g.setdefault("VB", g["V_LINE"] / lead["R"])           # same bead speed on the corners unless told otherwise
    if g["VB"] <= 0:
        raise SystemExit("VB must be above 0")
    g["L"] = 8 * lead["D"] + 2 * math.pi * lead["R"]     # outline length per loop (lead arm)
    return g, flags


# ----------------------------------------------------------------------
def frame(a):
    """(centre, n, t) for an arm: n points from the axis to the arm's side of the square."""
    ang = math.radians(a["ANGLE"])
    n = (math.cos(ang), math.sin(ang))
    t = (-math.sin(ang), math.cos(ang))
    return (a["CX"], a["CY"]), n, t


def to_world(a, p, phi_deg=0.0):
    """Local (n, t) coordinates p, turned by phi_deg about the axis, to the arm's frame."""
    (cx, cy), n, t = frame(a)
    ph = math.radians(phi_deg)
    u = p[0] * math.cos(ph) - p[1] * math.sin(ph)
    v = p[0] * math.sin(ph) + p[1] * math.cos(ph)
    return (cx + u * n[0] + v * t[0], cy + u * n[1] + v * t[1])


def straight_pt(g, a, s_local, k=0, total=None):
    """Point s_local (0..2d) along side k of the outline, with the bed at total deg since the start.

    The side sits where side 0 sat whenever the bed has made exactly k quarter turns; a bed
    residual (over- or undershoot) turns it to match. total=None means exactly k x 90."""
    x = g["DIR"] * (s_local - a["D"])
    return to_world(a, (a["S"], x), _phi(g, k, total))


def corner_pt(g, a, b_deg, k=0, total=None):
    """Point of fillet k at fillet progress b_deg (0..90, about the corner's own centre, in the bed
    frame), with the bed at total deg since the start. While the bed turns at the fillet rate
    this traces the compensating quarter circle; with the bed at rest it traces the fillet itself."""
    d, r, dr = a["D"], a["R"], g["DIR"]
    ph = math.radians(dr * b_deg)
    return to_world(a, (d + r * math.cos(ph), dr * d + r * math.sin(ph)), _phi(g, k, total))


def corner_progress(g, a, xy, k, t_begin):
    """Where on corner k's path the arm actually is: fillet progress (deg) from the corner's start,
    for a corner that began with the bed at t_begin. Inverse of corner_pt.

    corner_pt(b, k, t_begin + b) = C + Rot(phi) F0 + R e(BED_DIR x res), res = t_begin - 90 k, so the
    arm runs on a circle of radius |F0| about Cc = C + R e(BED_DIR res); its angle there is phi."""
    (cx, cy), n, t = frame(a)
    res = t_begin - 90.0 * k
    e = math.radians(g["BED_DIR"] * res)
    cc = (cx + a["R"] * (math.cos(e) * n[0] + math.sin(e) * t[0]),
          cy + a["R"] * (math.cos(e) * n[1] + math.sin(e) * t[1]))
    dx, dy = xy[0] - cc[0], xy[1] - cc[1]
    u, v = dx * n[0] + dy * n[1], dx * t[0] + dy * t[1]         # in (n, t) coordinates
    ang = math.degrees(math.atan2(v, u))
    ang0 = math.degrees(math.atan2(g["DIR"] * a["D"], a["D"]))  # angle of F0 at phi = 0
    total_arm = g["BED_DIR"] * (ang - ang0 - g["DIR"] * 90.0 * k)   # phi = BED_DIR total + DIR 90 k
    b_arm = (total_arm - t_begin + 180.0) % 360.0 - 180.0
    return b_arm


def _phi(g, k, total):
    """Turn (deg) taking outline segment k's local coordinates into the arm's frame: the bed's
    measured turn plus the k quarter turns of the outline (which cancel when the bed is on 90 k)."""
    bed = 90.0 * k if total is None else total
    return g["BED_DIR"] * bed + g["DIR"] * 90.0 * k


def height(g, u):
    """Height above the first layer after u loops of outline (flat, spiral with ramp, flat)."""
    c0, n_sp = g["C0"], g["SPIRAL"]
    if u < c0:
        return 0.0
    if u < c0 + n_sp:
        return spiral_dz(g, u - c0)
    return spiral_dz(g, n_sp)


def phase_name(g, u):
    if u < g["C0"]:
        return "flat"
    if u < g["C0"] + g["SPIRAL"]:
        return "spiral"
    return "final flat"


def plan(g):
    """Every number the run will use, worked out before anything moves."""
    fil_area = math.pi * (g["FIL"] / 2) ** 2
    lead = g["arms"][min(g["arms"])]
    arm_speed = BASE_ARM_SPEED * g["MAX_VT"] / 100
    warn = []
    v_line = g["V_LINE"]
    if v_line > arm_speed:
        raise SystemExit(f"V_LINE {v_line:.1f} mm/s is above the arm ceiling {arm_speed:.0f} mm/s (MAX_VT).")
    corner_arm_per_rad = max(a["D"] for a in g["arms"].values()) * math.sqrt(2)
    omega = min(g["VB"], g["MAX_VB"], arm_speed / corner_arm_per_rad)
    if omega < g["VB"] - 1e-9:
        why = "MAX_VB" if omega == g["MAX_VB"] else f"the arm's {arm_speed:.0f} mm/s ceiling on the corner move"
        warn.append(f"Corners: bed capped at {omega:.3f} rad/s by {why} (asked {g['VB']:.3f}). "
                    "The extruder follows the bed, so the bead is unchanged; the corners just take longer.")
    v_corner = omega * lead["R"]                              # bead speed on the fillet at full bed speed
    if abs(g["P"] - g["NL"] * g["L_H"]) > 1e-6:
        warn.append(f"P {g['P']:g} is not n x L_H = {g['NL'] * g['L_H']:g}: beads will be "
                    f"{g['P'] / g['NL']:.2f} mm tall but the flow is sized for {g['L_H']:g} mm.")
    if g["FIRST"] <= 0:
        raise SystemExit("FIRST must be above 0: nothing may run at or below Z0.")
    total_loops = g["C0"] + g["SPIRAL"] + g["C1"]
    t_straight = 2 * lead["D"] / v_line
    t_corner = (math.pi / 2) / omega
    loop_s = 4 * (t_straight + t_corner)
    per = {}
    for tl, a in g["arms"].items():
        z_first = a["Z0"] + g["FIRST"]
        rate_line = a["K"] * v_line                           # filament mm/s on the straights
        rate_corner = a["K"] * a["R"] * omega                 # ... on the corners at full bed speed
        peak = max(rate_line, rate_corner) * fil_area * a["FLOW_K"]
        if rate_line < 0.5 or rate_corner < 0.5:
            warn.append(f"{a['side']}: filament rate {min(rate_line, rate_corner):.2f} mm/s is very low; the extruder "
                        "may not deliver evenly below about 0.5 mm/s. Raise V_LINE / VB or the bead.")
        if peak > 30:
            warn.append(f"{a['side']}: {peak:.0f} mm3/s is above what a Revo HF usually melts (about 30); "
                        "expect under-extrusion. Lower V_LINE (and VB).")
        if g["L_H"] > 0.75 * a["nozzle"] + 1e-9:
            warn.append(f"{a['side']}: L_H {g['L_H']:g} mm is over 75 % of its {a['nozzle']:g} mm nozzle "
                        f"(max {0.75 * a['nozzle']:.2f}).")
        if not 0.95 * a["nozzle"] <= a["W"] <= 1.6 * a["nozzle"]:
            warn.append(f"{a['side']}: line width {a['W']:g} mm is outside about 1 to 1.5 x its "
                        f"{a['nozzle']:g} mm nozzle.")
        start = straight_pt(g, a, 0.0)
        end = straight_pt(g, a, 2 * a["D"])
        prime_xy = straight_pt(g, a, -g["PRIME_MM"])          # back along the line of the side
        prime_dist = math.hypot(g["PRIME_MM"], g["PRIME_Z"] - g["FIRST"])
        per[tl] = {"z_first": z_first, "z_top": z_first + height(g, total_loops), "start": start, "end": end,
                   "corner_far": corner_pt(g, a, 45.0, 0, 45.0), "prime_xy": prime_xy,
                   "prime_z": a["Z0"] + g["PRIME_Z"], "prime_speed": prime_dist / g["PRIME_S"],
                   "rate_line": rate_line, "rate_corner": rate_corner,
                   "flow": rate_line * fil_area * a["FLOW_K"], "corner_speed": a["D"] * math.sqrt(2) * omega}
    fil_mm = sum(q["rate_line"] * t_straight * 4 + q["rate_corner"] * t_corner * 4 for q in per.values())
    return {"fil_area": fil_area, "v_line": v_line, "v_corner": v_corner, "omega": omega,
            "t_straight": t_straight, "t_corner": t_corner, "loop_s": loop_s, "total_loops": total_loops,
            "arm_speed": arm_speed, "warnings": warn, "per": per,
            "filament_mm": fil_mm * total_loops + sum(q["rate_line"] / 2 for q in per.values()) * g["PRIME_S"]}


def show(g, k):
    names = " + ".join(f"{a['side']} ({a['nozzle']:g} mm)" for a in g["arms"].values())
    lead = g["arms"][min(g["arms"])]
    print(f"\nRounded square test: {names}, {'layered' if g['N'] == 2 else 'one arm'}")
    print(f"  Outline {g['SIDE']:g} mm square, fillet R {g['RF']:g}: straights {2 * lead['D']:.1f} mm, "
          f"corners {math.pi / 2 * lead['R']:.1f} mm, loop {g['L']:.1f} mm")
    print(f"  Straights: arm {k['v_line']:.1f} mm/s, bed still, {k['t_straight']:.1f} s per side.  "
          f"Corners: bed {k['omega']:.3f} rad/s ({math.degrees(k['omega']):.1f} deg/s) [ceiling {g['MAX_VB']:.3f}], "
          f"bead {k['v_corner']:.1f} mm/s at R {lead['R']:g}, {k['t_corner']:.1f} s per corner")
    print("  Extruder follows the motion: rate = bead per mm x arm speed on the straights, x RF x measured bed "
          "speed on the corners")
    for tl, a in g["arms"].items():
        q = k["per"][tl]
        kk = f" x FLOW_K {a['FLOW_K']:g}" if a["FLOW_K"] != 1 else ""
        print(f"  {a['side']:5}: bead {a['W']:g} x {g['L_H']:g} mm{kk} = {a['K']:.3f} mm filament per mm;"
              f"  rate {q['rate_line']:.2f} mm/s on the straights ({q['flow']:.1f} mm3/s), "
              f"{q['rate_corner']:.2f} on the corners at full bed speed;"
              f"  Z0 {a['Z0']:.2f}, first {q['z_first']:.2f}, top {q['z_top']:.2f}")
        print(f"         side X {q['start'][0]:.1f} Y {q['start'][1]:.1f} -> X {q['end'][0]:.1f} Y {q['end'][1]:.1f};"
              f"  corner move out to X {q['corner_far'][0]:.1f} Y {q['corner_far'][1]:.1f} at {q['corner_speed']:.0f} mm/s")
    print(f"  1. Prime: {g['PRIME_MM']:g} mm move-in along the first side over {g['PRIME_S']:g} s from "
          f"{g['PRIME_Z']:g} mm above Z0; bed still, extruders at half the straight-line rate")
    print(f"  2. Flat: {g['C0']:g} loop at the first layer" + (" (each arm half the outline)" if g["N"] == 2 else ""))
    ramp = (f", easing in over {g['RAMP_STEPS'] * g['RAMP_EVERY']:g} loop ({g['P'] / g['RAMP_STEPS']:.2f} mm/loop "
            f"more every {g['RAMP_EVERY']:g} loop)" if g["RAMP_STEPS"] > 1 else "")
    print(f"  3. Spiral: {g['SPIRAL']:g} loops, rising {g['P']:g} mm/loop{ramp}")
    print(f"  4. Final flat: {g['C1']:g} loop at the top")
    print(f"  5-7. Extruders off after {k['total_loops']:g} loops, lift 10 mm, park, bed stopped")
    print(f"  Bed turns +{math.degrees(k['omega']):.1f} deg/s on corners; BED_DIR {g['BED_DIR']:+d} "
          f"(outline traversed {'anticlockwise' if g['DIR'] > 0 else 'clockwise'} in the arm's frame)")
    print(f"  Time about {k['total_loops'] * k['loop_s'] + g['PRIME_S']:.0f} s, filament about "
          f"{k['filament_mm']:.0f} mm in total;  arm moves {k['arm_speed']:.0f} mm/s")
    for w in k["warnings"]:
        print("  CHECK: " + w)


# ----------------------------------------------------------------------
def run(g, k, cell, cold=False):
    arms, per = g["arms"], k["per"]
    v = k["arm_speed"]
    lead = arms[min(arms)]
    L = g["L"]
    rate = {tl: 0.0 for tl in arms}        # filament mm/s wanted per tool (set by the motion loop)
    sent = {tl: None for tl in arms}       # what Klipper was last told (None = off)
    sent_at = {tl: 0.0 for tl in arms}

    def gcode(s):
        if not cold:
            return cell.gcode(s)

    def set_rate(tl, r):
        """Ask for r mm/s of filament; the extruder thread sends it. Never blocks the motion loop."""
        rate[tl] = r

    def extruder_thread():
        """EXTRUDE_ON at the wanted rate (EXTRUDE_OFF below RATE_MIN) when it changes by RATE_TOL,
        at most RATE_HZ times a second, and at least every WATCHDOG_S / 2.5 for the watchdog."""
        period = 1.0 / g["RATE_HZ"]
        while not cell.stop_keep.wait(period / 2):
            now = time.monotonic()
            for tl in arms:
                r = rate[tl]
                if r < g["RATE_MIN"]:
                    if sent[tl] is not None:
                        try:
                            gcode(f"EXTRUDE_OFF T={tl}")
                        except Exception as ex:
                            print(f"  extruder command failed: {ex}")
                        sent[tl], sent_at[tl] = None, now
                    continue
                due = (sent[tl] is None or abs(r - sent[tl]) > RATE_TOL * sent[tl]) and now - sent_at[tl] >= period
                due = due or (sent[tl] is not None and now - sent_at[tl] >= WATCHDOG_S / 2.5)
                if due:
                    try:
                        gcode(f"EXTRUDE_ON T={tl} RATE={r:.3f} TIMEOUT={WATCHDOG_S}")
                        sent[tl], sent_at[tl] = r, now
                    except Exception as ex:
                        print(f"  extruder command failed: {ex}")

    def guard(tl, z):
        if z < arms[tl]["Z0"] - 1e-6:
            raise Abort(f"Refused: {arms[tl]['side']} Z {z:.2f} is below its zero height {arms[tl]['Z0']:.2f}")
        return z

    def move(tl, xy, z, speed, wait, blend=None):
        r, pt, yw = cell.orient[tl]
        kw = {"speed": speed, "wait": wait}
        if blend is not None:
            kw["radius"] = blend
        cell.arms[tl].arm.set_position(xy[0], xy[1], guard(tl, z), r, pt, yw, **kw)

    def z_at(tl, s):
        return per[tl]["z_first"] + height(g, s / L)

    servo = bool(g["SERVO"])
    tick = 0.01 if servo else TICK
    last_pose = {}

    def stream(tl, xy, z, speed=None, blend=None):
        """Send the arm towards (xy, z): a servo pose (rate limited to SERVO_STEP per tick) or a queued move."""
        if not servo:
            move(tl, xy, z, speed if speed is not None else v, wait=False, blend=blend)
            return
        tgt = (xy[0], xy[1], guard(tl, z))
        cur = last_pose.get(tl) or tuple(cell.pose(tl)[:3])
        d = math.dist(cur, tgt)
        if d > SERVO_STEP:                                    # never ask for a jump: walk towards it
            f = SERVO_STEP / d
            tgt = tuple(c + (t - c) * f for c, t in zip(cur, tgt))
        r, pt, yw = cell.orient[tl]
        cell.arms[tl].arm.set_servo_cartesian([tgt[0], tgt[1], tgt[2], r, pt, yw], speed=100, mvacc=2000)
        last_pose[tl] = tgt

    def servo_on():
        for tl in arms:
            a = cell.arms[tl].arm
            if not hasattr(a, "set_servo_cartesian"):
                raise Abort(f"{arms[tl]['side']}: this SDK/firmware has no servo mode (needs 1.6+); run without SERVO=1")
            a.set_mode(1)
            a.set_state(0)
            last_pose[tl] = tuple(cell.pose(tl)[:3])
        time.sleep(0.1)

    def servo_off():
        for tl in arms:
            try:
                cell.arms[tl].arm.set_mode(0)
                cell.arms[tl].arm.set_state(0)
            except Exception as ex:
                print(f"  {arms[tl]['side']}: could not leave servo mode: {ex}")
        time.sleep(0.1)

    if not cold:
        for tl, a in arms.items():
            temp = cell.hot_end_temp(tl)
            if temp < g["MIN_TEMP"]:
                raise Abort(f"{a['side']} hot end is at {temp:.0f} C, below MIN_TEMP {g['MIN_TEMP']:.0f}. Heat it first "
                            f"(python3 tools/extrude.py heat {a['side']} 215 --wait).")
    gcode("EXTRUDE_OFF")
    for tl in (0, 1):                      # a stale EXTRUDE_SCALE from an earlier run would multiply every rate
        gcode(f"EXTRUDE_SCALE T={tl} PERCENT=100")
    cell.tt.stop_rotation()
    incremental = bool(g["BED_MOVE"]) and hasattr(cell.tt, "rotate_relative")
    if g["BED_ACC"] > 0:
        set_bed_ramp(cell.tt, g["BED_ACC"])
    print("Approach: each arm to park, safe height, above its prime point, down to the prime start")
    for tl in arms:
        px, py = per[tl]["prime_xy"]
        cell.move(tl, *PARK, speed=v, wait=True)
        cell.move(tl, PARK[0], PARK[1], SAFE_Z, speed=v, wait=True)
        cell.move(tl, px, py, guard(tl, per[tl]["prime_z"] + 20), speed=v, wait=True)
        cell.move(tl, px, py, guard(tl, per[tl]["prime_z"]), speed=min(v, 20), wait=True)

    # 1. Prime: bed still, half the straight-line rate, run in along the line of the first side
    print(f"1. Prime: bed still, extruders at half the straight-line rate, moving in over {g['PRIME_S']:g} s")
    for tl in arms:
        set_rate(tl, per[tl]["rate_line"] / 2)
    threading.Thread(target=extruder_thread, daemon=True).start()
    for tl in arms:
        move(tl, per[tl]["start"], per[tl]["z_first"], per[tl]["prime_speed"], wait=False)
    t_start = time.monotonic()
    t_end = t_start + g["PRIME_S"] + 3
    while time.monotonic() < t_end:
        arrived = all(math.dist(cell.pose(tl)[:3], (*per[tl]["start"], per[tl]["z_first"])) < 0.3 for tl in arms)
        if arrived and time.monotonic() - t_start >= g["PRIME_S"]:
            break
        time.sleep(TICK)
    else:
        print("  (an arm did not report arrival within prime time + 3 s; carrying on)")

    # 2-4. The outline, segment by segment: straight (bed still), corner (bed on), x4 per loop
    if servo:
        servo_on()
    a0 = cell.tt.get_angle()
    live = {"rev": 0.0, "seg": "", "bed": 0.0, "z": {tl: per[tl]["z_first"] for tl in arms},
            "xy": {tl: per[tl]["start"] for tl in arms}, "dev": {tl: [] for tl in arms}, "rate": rate}
    os.makedirs(os.path.join(ROOT, "runs"), exist_ok=True)
    log_path = os.path.join(ROOT, "runs", time.strftime("square_test_%Y%m%d_%H%M%S.csv"))
    threading.Thread(target=logger, args=(g, cell, live, log_path, cold), daemon=True).start()
    print(f"2. Flat loop: start angle {a0:.1f} deg, straights {k['v_line']:.1f} mm/s;  logging to {log_path}")
    s_end = k["total_loops"] * L
    omega_dps = math.degrees(k["omega"])
    s = 0.0
    seg = 0
    total = 0.0                            # bed angle since the start, deg (measured)
    phase = "flat"
    lead_adj = {tl: 0.0 for tl in arms}    # learned extra lead per arm, deg (carried corner to corner)
    if servo:
        s, seg, total = stream_outline(g, k, cell, arms, per, lead, L, s_end, a0, live, stream, set_rate, z_at, incremental)
    while s < s_end - 1e-6:
        kq = seg // 2                      # quarter turns the bed has made before this segment
        ph = phase_name(g, s / L)
        if ph != phase:
            print({"spiral": f"3. Spiral at P {g['P']:g} mm/loop", "final flat": "4. Final flat loop"}[ph]
                  + f"  (loop {s / L:.2f})")
            phase = ph
        if seg % 2 == 0:
            # ---- straight: bed still, one move along the side at V_LINE, extruder at the line rate
            length = min(2 * lead["D"], s_end - s)
            frac = length / (2 * lead["D"])
            live["seg"] = f"side {kq % 4}"
            targets = {}
            for tl, a in arms.items():
                set_rate(tl, per[tl]["rate_line"])
                xy = straight_pt(g, a, frac * 2 * a["D"], kq, total)
                targets[tl] = (xy, z_at(tl, s + length))
                if not servo:
                    move(tl, xy, targets[tl][1], k["v_line"], wait=False)
            t0 = time.monotonic()
            dur = length / k["v_line"]
            n_tick = 0
            while True:
                el = time.monotonic() - t0
                f = min(1.0, el / dur) if dur > 0 else 1.0
                live["rev"] = (s + f * length) / L
                for tl, a in arms.items():
                    live["xy"][tl] = straight_pt(g, a, f * frac * 2 * a["D"], kq, total)
                    live["z"][tl] = z_at(tl, s + f * length)
                    if servo:                                  # streamed: the pose for this instant
                        stream(tl, live["xy"][tl], live["z"][tl])
                if servo:
                    if f >= 1.0:
                        break
                else:
                    arrived = all(math.dist(cell.pose(tl)[:3], (*targets[tl][0], targets[tl][1])) < 0.3 for tl in arms)
                    if arrived and el >= dur - 0.05:
                        break
                    if el > dur + 3:
                        print(f"  (an arm did not report arrival on side {kq % 4} within time + 3 s; carrying on)")
                        break
                n_tick += 1
                delay = t0 + n_tick * tick - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            s += length
        else:
            # ---- corner: bed on; the arm follows the MEASURED bed angle round the corner move and
            #      the extruder rate follows the MEASURED bed speed (ramps up, ramps down, stops)
            arc = math.pi / 2 * lead["R"]
            b_end = 90.0 * min(1.0, (s_end - s) / arc)       # a partial corner if the print ends in it
            live["seg"] = f"corner {kq % 4}"
            t_begin = total                                   # where the bed is as this corner begins
            if incremental:                                   # one planned move: ramps and lands on b_end
                cell.tt.rotate_relative(b_end - g["STOP_LEAD"], omega_dps, wait=False)
            else:
                cell.tt.rotate_velocity(omega_dps)
            v_cmd = min(v, max(10.0, 1.5 * max(q["corner_speed"] for q in per.values())))
            last = {tl: None for tl in arms}
            lag = {tl: [] for tl in arms}
            stopped, t_stop = False, None
            prev_total, prev_t = total, time.monotonic()
            t_begin_t = prev_t
            w_filt = 0.0                                      # measured bed speed, deg/s, lightly smoothed
            while True:
                now_t = time.monotonic()
                total = cell.tt.get_angle() - a0
                b = max(0.0, total - t_begin)                  # fillet progress = bed progress
                live["bed"] = total
                w_raw = (total - prev_total) / max(now_t - prev_t, 1e-3)
                w_filt = 0.5 * w_filt + 0.5 * max(0.0, w_raw)
                for tl, a in arms.items():                    # extruder proportional to the bed, floored
                    set_rate(tl, max(a["K"] * a["R"] * math.radians(w_filt),
                                     g["CORNER_FLOOR"] * per[tl]["rate_line"]))
                if not stopped and not incremental and b + omega_dps * tick >= b_end - g["STOP_LEAD"]:
                    cell.tt.stop_rotation()                    # from here the arm keeps following the bed
                    stopped, t_stop = True, now_t              # through its deceleration, no parking
                if incremental and not stopped and b >= b_end - g["STOP_LEAD"] - max(2.0, omega_dps * tick):
                    stopped, t_stop = True, now_t              # in its own deceleration now
                if stopped and (abs(total - prev_total) < 0.01 or now_t - t_stop > SETTLE_S):
                    break                                      # bed at rest (or SETTLE_S is up)
                if incremental and now_t - t_begin_t > (b_end / omega_dps) * 3 + SETTLE_S:
                    raise Abort("the bed did not complete its corner move")
                if b > 0.5 and g["LEAD_GAIN"] > 0:            # close the loop on where the arm really is
                    for tl, a in arms.items():
                        try:
                            pose = cell.pose(tl)
                        except Exception:
                            pose = None
                        if pose:
                            b_arm = corner_progress(g, a, pose[:2], kq, t_begin)
                            lag[tl].append(b - b_arm)
                            lead_adj[tl] += g["LEAD_GAIN"] * (b - b_arm)
                            lead_adj[tl] = max(-10.0, min(45.0, lead_adj[tl]))
                for tl, a in arms.items():
                    lead_s = g["SERVO_LEAD_S"] if servo else g["CORNER_LEAD_S"]
                    lead_deg = (w_filt if stopped else omega_dps) * lead_s + lead_adj[tl]
                    bl = min(b + max(0.0, lead_deg), b_end)
                    xy = corner_pt(g, a, bl, kq, total + (bl - b))
                    z = z_at(tl, s + arc * bl / 90.0)
                    if servo or last[tl] is None or math.dist(xy, last[tl][0]) >= 0.05 or abs(z - last[tl][1]) >= 0.02:
                        stream(tl, xy, z, speed=v_cmd, blend=g["BLEND"])
                        last[tl] = (xy, z)
                    live["xy"][tl], live["z"][tl] = corner_pt(g, a, b, kq, total), z_at(tl, s + arc * b / 90.0)
                live["rev"] = (s + arc * b / 90.0) / L
                prev_total, prev_t = total, now_t
                time.sleep(tick if not stopped else min(tick, 0.02))
            live["bed"] = total
            for tl in arms:                                   # bed at rest: straight-line rate from here
                set_rate(tl, per[tl]["rate_line"])
            if any(lag.values()):
                worst = max(abs(x) for v in lag.values() for x in v[len(v) // 3:] or v)
                if worst > 3.0:
                    print(f"  corner {kq % 4}: arm lagged the bed by up to {worst:.1f} deg after settling in "
                          f"(lead now {', '.join(f'{lead_adj[t]:+.1f}' for t in arms)} deg)")
            b_bed = max(0.0, min(b_end, total - t_begin))
            if b_end - b_bed > 0.05:                          # bed stopped short: the arm finishes the fillet alone
                print(f"  bed stopped {b_end - b_bed:.2f} deg short on corner {kq % 4}; the arm finishes the fillet")
                for tl in arms:
                    set_rate(tl, per[tl]["rate_line"])        # arm moving at V_LINE: line rate
                rest = (b_end - b_bed) / 90.0 * arc            # mm of fillet left
                n_pts = max(1, int(math.ceil(rest / (k["v_line"] * tick if servo else 0.5))))
                for i in range(1, n_pts + 1):
                    b = b_bed + (b_end - b_bed) * i / n_pts
                    for tl, a in arms.items():
                        stream(tl, corner_pt(g, a, b, kq, total), z_at(tl, s + arc * b / 90.0), speed=k["v_line"], blend=g["BLEND"])
                    live["rev"] = (s + arc * b / 90.0) / L
                    if servo:
                        time.sleep(tick)
            else:
                for tl, a in arms.items():                   # exact end of the corner move, queued and blended:
                    stream(tl, corner_pt(g, a, b_end, kq, total), z_at(tl, s + arc * b_end / 90.0), speed=v_cmd,
                           blend=g["BLEND"])                 # the next side's move follows at once, no stop
            for tl, a in arms.items():
                live["xy"][tl], live["z"][tl] = corner_pt(g, a, b_end, kq, total), z_at(tl, s + arc * b_end / 90.0)
            residual = total - t_begin - b_end
            if residual > 0.5:
                print(f"  bed {residual:+.2f} deg past the corner's {b_end:g} (corner {kq % 4}); the next side is turned to match")
            s += arc * b_end / 90.0
        seg += 1
        live["rev"] = s / L

    # 5-7
    cell.stop_keep.set()
    gcode("EXTRUDE_OFF")
    if servo:
        servo_off()
    print(f"5. Extruders off after {s / L:.3f} loops ({seg} segments)")
    report_deviation(g, live)
    print("6. Move away")
    for tl in arms:
        cell.move(tl, *live["xy"][tl], guard(tl, live["z"][tl] + 10), speed=20, wait=True)
    for tl in arms:
        cell.move(tl, *PARK, speed=v, wait=True)
    cell.tt.stop_rotation()
    print("7. Bed stopped. Done.")


def stream_outline(g, k, cell, arms, per, lead, L, s_end, a0, live, stream, set_rate, z_at, incremental):
    """Servo mode: walk the outline by time at V_LINE, the bed indexing 90 deg on its own during each
    fillet, every pose = outline point for this instant turned by the measured bed angle.

    Returns (s, seg, total) at the end so the caller's bookkeeping carries on."""
    v = k["v_line"]
    tick = 0.01
    d2 = 2 * lead["D"]                                   # a side, mm
    arc = math.pi / 2 * lead["R"]                        # a fillet, mm
    t_corner = arc / v
    # bed index speed: VB if given, else fast enough to do 90 deg in ~70 % of the fillet's time (ramps)
    bed_dps = math.degrees(g["VB"]) if "VB" in g and g["VB"] else 90.0 / (0.7 * t_corner)
    bed_dps = min(bed_dps, math.degrees(g["MAX_VB"]))
    for tl in arms:
        set_rate(tl, per[tl]["rate_line"])               # constant: the bead speed is constant everywhere
    print(f"   servo: outline at {v:g} mm/s everywhere, poses every {tick * 1000:.0f} ms; bed indexes 90 deg at "
          f"{bed_dps:.1f} deg/s during each {t_corner:.1f} s fillet")
    s = 0.0
    seg = 0
    corners_started = 0
    total = cell.tt.get_angle() - a0
    w_filt = 0.0
    prev_total, prev_t = total, time.monotonic()
    t0 = time.monotonic()
    n = 0
    phase = "flat"
    while s < s_end - 1e-6:
        now = time.monotonic()
        total = cell.tt.get_angle() - a0
        w_raw = (total - prev_total) / max(now - prev_t, 1e-3)
        w_filt = 0.5 * w_filt + 0.5 * w_raw
        prev_total, prev_t = total, now
        total_lead = total + w_filt * g["SERVO_LEAD_S"]   # where the bed will be when the pose lands
        s = min(s_end, v * (now - t0))
        seg = int(s // (d2 + arc)) * 2                    # side k then corner k, per quarter
        s_in = s - (seg // 2) * (d2 + arc)
        kq = seg // 2
        if s_in >= d2:                                    # in the fillet
            seg += 1
            b = 90.0 * (s_in - d2) / arc
            if corners_started <= kq:                     # first tick of this fillet: start the bed's index
                corners_started = kq + 1
                if incremental:
                    cell.tt.rotate_relative(90.0, bed_dps, wait=False)
                else:
                    cell.tt.rotate_velocity(bed_dps)      # free-run fallback: stopped when the fillet ends
            live["seg"] = f"corner {kq % 4}"
            for tl, a in arms.items():
                xy = corner_pt(g, a, b, kq, total_lead)
                stream(tl, xy, z_at(tl, s))
                live["xy"][tl], live["z"][tl] = corner_pt(g, a, b, kq, total), z_at(tl, s)
        else:
            if not incremental and kq > 0 and corners_started == kq and total - 90.0 * (kq - 1) >= 90.0:
                cell.tt.stop_rotation()                   # free-run fallback: the index is complete
            live["seg"] = f"side {kq % 4}"
            for tl, a in arms.items():
                xy = straight_pt(g, a, s_in * a["D"] / lead["D"], kq, total_lead)
                stream(tl, xy, z_at(tl, s))
                live["xy"][tl], live["z"][tl] = straight_pt(g, a, s_in * a["D"] / lead["D"], kq, total), z_at(tl, s)
        live["bed"], live["rev"] = total, s / L
        ph = phase_name(g, s / L)
        if ph != phase:
            print({"spiral": f"3. Spiral at P {g['P']:g} mm/loop", "final flat": "4. Final flat loop"}[ph] + f"  (loop {s / L:.2f})")
            phase = ph
        n += 1
        delay = t0 + n * tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)
    if not incremental:
        cell.tt.stop_rotation()
    # how far is the bed from where the outline thinks it is? (it should be on a multiple of 90)
    off = total - 90.0 * round(total / 90.0)
    print(f"   servo: done; bed {off:+.2f} deg off its nearest index (absorbed by the measured-angle compensation)")
    return s_end, seg + 1, total


def set_bed_ramp(tt, acc_dps2):
    """Ask the Aerotech for a linear ramp at acc_dps2 (accel and decel). Best effort: the
    Automation1 call names differ between versions, so a failure only prints a note."""
    try:
        import automation1 as a1
        ms = tt.controller.runtime.commands.motion_setup
        ms.setupaxisramptype([tt.axis], a1.RampType.Linear)
        ms.setupaxisrampvalue([tt.axis], a1.RampMode.Rate, acc_dps2)
        print(f"  bed ramp set to {acc_dps2:g} deg/s2 (linear)")
    except Exception as ex:
        print(f"  could not set the bed ramp ({ex}); the axis keeps its configured ramp")


def logger(g, cell, live, log_path, cold):
    """10 Hz: log time, loop, segment, bed angle, commanded Z and rate, reported arm position."""
    arms = g["arms"]
    t0 = time.monotonic()
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("t_s,loop,segment,bed_deg," + ",".join(
            f"z_cmd_{s},x_act_{s},y_act_{s},z_act_{s},dz_{s},dxy_{s},rate_{s}"
            for s in (arms[t]["side"] for t in arms))
            + "," + ",".join(f"temp_{arms[t]['side']}" for t in arms) + "\n")
        temps = {t: "" for t in arms}
        tick = 0
        while not cell.stop_keep.is_set():
            now = time.monotonic() - t0
            cols = []
            for t in arms:
                try:
                    p = cell.pose(t)
                except Exception:
                    p = None
                if p:
                    dz = p[2] - live["z"][t]
                    dxy = math.dist(p[:2], live["xy"][t])
                    live["dev"][t].append((live["rev"], dz, dxy))
                    cols.append(f"{live['z'][t]:.3f},{p[0]:.3f},{p[1]:.3f},{p[2]:.3f},{dz:.3f},{dxy:.3f},"
                                f"{live['rate'][t]:.3f}")
                else:
                    cols.append(f"{live['z'][t]:.3f},,,,,,{live['rate'][t]:.3f}")
            if tick % 10 == 0 and not cold:
                for t in arms:
                    try:
                        temps[t] = f"{cell.hot_end_temp(t):.1f}"
                    except Exception:
                        temps[t] = ""
            cols = ",".join(cols) + "," + ",".join(temps[t] for t in arms)
            f.write(f"{now:.3f},{live['rev']:.4f},{live['seg']},{live['bed']:.2f},{cols}\n")
            tick += 1
            delay = t0 + tick * 0.1 - time.monotonic()
            if delay > 0:
                cell.stop_keep.wait(delay)


def main(argv=None):
    g, flags = parse(sys.argv[1:] if argv is None else argv)
    k = plan(g)
    sim = "--sim" in flags
    cold = "--cold" in flags
    show(g, k)
    if cold:
        print("\nCOLD RUN: no extrusion, hot-end check skipped. Arm and bed moves only.")
    if "--dry-run" in flags:
        print("\nDry run: nothing moved.")
        return 0
    if not sim and "--yes" not in flags:
        if input("\nRun this on the cell? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Not started.")
            return 1
    cell = Cell(g, sim)

    def leave_servo():
        if g["SERVO"]:
            for tl in g["arms"]:
                try:
                    cell.arms[tl].arm.set_mode(0)
                    cell.arms[tl].arm.set_state(0)
                except Exception:
                    pass
    try:
        run(g, k, cell, cold=cold)
        return 0
    except KeyboardInterrupt:
        leave_servo()
        abort(cell, "Ctrl+C")
    except Abort as e:
        leave_servo()
        abort(cell, str(e))
    except Exception as e:
        leave_servo()
        abort(cell, f"error: {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
