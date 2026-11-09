"""Rounded-square test on one or both arms: prime, flat loop, spiral, final flat loop.

Same bead, flow and bed relationship as cylinder_test.py (which this imports), but the outline
is a square with filleted corners:
  * straights: bed STOPPED, the arm moves along the side at the bead's surface speed
  * corners:   bed ON at the cylinder relationship with R = the fillet radius (omega = v / RF),
               while the arm makes a small compensating move so the fillet is laid about the
               corner's own centre (the fillet centre is not on the turntable axis, so the arm
               cannot simply hold still; see "Geometry" below).
Every corner turns the bed 90 deg, so after each corner the next side of the square sits exactly
where the last one was in the arm's frame: the arm draws the SAME straight line and the SAME
corner move every segment, the bed indexes the part under it.

Run from the repo root (test Klipper config loaded, hot ends hot):

    python3 square_test.py T=0 SIDE=80 RF=10 E=12 L_H=0.8 P=0.8 --dry-run           # right arm
    python3 square_test.py T=1 SIDE=80 RF=10 E=12 L_H=0.6 --dry-run                 # left arm
    python3 square_test.py T=2 SIDE=80 RF=10 VB=0.55 L_H=0.6 LOOPS=20 --dry-run     # both arms
    python3 square_test.py T=0 SIDE=80 RF=10 E=12 LOOPS=2 --cold                     # moves only, no plastic:
                                                                                     # check BED_DIR first!
    python3 square_test.py T=0 SIDE=80 RF=10 E_MIN=8 E_MAX=16 WAVE_T=1 SHAPE=square LOOPS=10

Values (KEY=value, any order). In T=2 the plain keys are the RIGHT arm; *_L keys override the left.
    T       0 = right, 1 = left, 2 = both                                    (required)
    SIDE    outside side length of the square, mm (bead centreline)          (required)
    RF      fillet (corner) radius, mm, bead centreline                      (default 10)
    L_H     bead height (layer height), mm                                   (default 0.5)
    E       filament speed, mm/s                  (required, unless E_MIN and E_MAX, or VB, are given)
    VB      bed speed on the corners, rad/s, instead of E: E is worked out so the bead is laid at
            that speed on a fillet of radius RF (E = VB x RF x W x L_H / (filament area x FLOW_K))
    P       Z rise per loop of the outline, mm                     (default n x L_H, n = arms used)
    W       line width, mm                                   (default 1.0, both arms)
    FLOW_K  measured / planned plastic from a test print
    Z0      zero height, nozzle touching the disc            (defaults as cylinder_test.py)
    E_L, W_L, FLOW_K_L, Z0_L   left arm in T=2 (same nozzle: same E, W, FLOW_K as the right unless set)
    MAX_VB  bed speed ceiling on the corners, rad/s                           (default 3.1416)
    MAX_VT  arm speed ceiling, % of 100 mm/s                                 (default 100)
    FIRST   first layer above Z0, mm                                         (default L_H)
    SPIRAL  spiral loops (default 3);  LOOPS  total loops;  HEIGHT  wall height above the first
            layer, mm (sets SPIRAL = HEIGHT / P, wins over LOOPS)
    RAMP_STEPS, RAMP_EVERY   ease into the spiral exactly as cylinder_test.py (5 steps every 0.125 loop)
    E_MIN, E_MAX, WAVE_T or WAVES_PER_LOOP   throttle between E_MIN and E_MAX (every arm used)
    SHAPE   throttle waveform: sine, square or triangle                      (default sine)
    PRIME_MM, PRIME_S, PRIME_Z   prime move-in mm, time s, start height above Z0 mm (15, 5, 2)
            The prime runs in along the line of the first side (a tangent tail outside the fillet).
    FIL     filament diameter, mm (1.75);  MIN_TEMP  refuse below this, C (190)
    ANGLE, CX, CY (and _L)   print direction and turntable centre in each arm's frame (as cylinder):
            the arm's side of the square sits at SIDE/2 from the axis in direction ANGLE
    DR (and DR_L)  outline trim, mm: offsets the whole outline outwards (side and fillet both grow).
            Left default 1.0 as in cylinder_test.py
    BED_DIR +1 if a positive bed velocity turns the disc ANTICLOCKWISE seen from above (xArm frames
            are z up, x towards y), -1 if clockwise                           (default +1)
            Wrong sign: the second side is laid on the far side of the disc instead of on top of
            the first. Check with a --cold run (LOOPS=1) before any plastic goes down.
    CORNER_LEAD_S  how far ahead of the measured bed angle the arm is sent on corners, s (0.15)
    BLEND   blend radius for the streamed corner moves, mm                   (default 2)
    STOP_LEAD  stop the bed this many degrees before the corner's 90 (its deceleration), deg (0);
            the arm finishes any part of the fillet the bed did not give

Flags: --dry-run (plan only), --sim (cell studio's simulators), --yes (no prompt),
       --cold (no extrusion, hot-end check skipped: arm and bed moves only).
Calibration (--find-z0, --find-centre) is in cylinder_test.py.

Geometry (per arm, in that arm's frame; C = turntable centre, n = unit vector at ANGLE, t = n turned
+90 deg, S = SIDE/2 + DR, R = RF + DR, d = S - R, dir = -BED_DIR = traversal sense of the outline):
    straight:  C + S n + x t,  x from -dir d to +dir d           (bed still)
    corner:    C + R n + Rot(BED_DIR x beta) (d n + dir d t),  beta 0..90 deg = measured bed angle
               since the corner began. A quarter circle of radius d sqrt2 about C + R n; in the BED
               frame it is the fillet of radius R about the corner centre.
    Arm speed on the corner = d sqrt2 x omega with omega = v / R; capped by MAX_VT (then the bed is
    slowed to match and the extruders scaled down on the corners so the bead stays the same).
Sequence, with n arms:  1. Prime (bed still).  2. Flat: 1/n loop at the first layer (two arms:
together the full outline).  3. Spiral: Z rises P per loop.  4. Final flat 1/n loop.
5. Extruders off.  6. Lift 10 mm and park.  7. Bed stopped.
Nothing is commanded below an arm's Z0. Ctrl+C aborts: extruders off, bed stop, arms lift.
Every run logs to runs/square_test_*.csv.
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]

from cylinder_test import (Abort, BASE_ARM_SPEED, COMMON, DEFAULTS, NOZZLE, PARK, SAFE_Z, SHAPES,  # noqa: E402
                           SIDE, WATCHDOG_S, Cell, abort, ramp_loss, report_deviation, spiral_dz)

SQUARE_COMMON = {**COMMON, "RF": 10.0, "BED_DIR": 1.0, "CORNER_LEAD_S": 0.15, "BLEND": 2.0, "STOP_LEAD": 0.0}
TICK = 0.05                      # main loop period, s
SETTLE_S = 3.0                   # max wait for the bed to come to rest after a corner


# ----------------------------------------------------------------------
def parse(argv):
    vals, flags = {}, set()
    for a in argv:
        if a.startswith("--"):
            flags.add(a)
        elif "=" in a:
            k, v = a.split("=", 1)
            k = k.strip().upper()
            if k == "SHAPE":
                v = v.strip().lower()
                if v not in SHAPES:
                    raise SystemExit(f"SHAPE must be one of {', '.join(SHAPES)}")
                vals[k] = v
            else:
                vals[k] = float(v)
        else:
            raise SystemExit(f"Not understood: {a!r} (use KEY=value, e.g. SIDE=80)")
    known = {"T", "SIDE", "RF", "L_H", "E", "P", "W", "FLOW_K", "Z0", "E_L", "W_L", "FLOW_K_L", "Z0_L", "VB",
             "MAX_VB", "MAX_VT", "FIRST", "SPIRAL", "LOOPS", "HEIGHT", "E_MIN", "E_MAX", "WAVE_T", "WAVES_PER_LOOP",
             "SHAPE", "PRIME_MM", "PRIME_S", "PRIME_Z", "FIL", "MIN_TEMP", "ANGLE", "CX", "CY", "ANGLE_L", "CX_L",
             "CY_L", "DR", "DR_L", "RAMP_STEPS", "RAMP_EVERY", "BED_DIR", "CORNER_LEAD_S", "BLEND", "STOP_LEAD"}
    unknown = sorted(set(vals) - known)
    if unknown:
        raise SystemExit("Unknown setting(s): " + ", ".join(unknown) + "  (check the spelling; see the list at the top)")
    if "E" not in vals and "E_MIN" in vals and "E_MAX" in vals:
        vals["E"] = (vals["E_MIN"] + vals["E_MAX"]) / 2
    vals.setdefault("L_H", 0.5)
    rf = vals.get("RF", SQUARE_COMMON["RF"])
    if "VB" in vals:                                       # corner bed speed given: work out E from it
        if "E" in vals or "E_MIN" in vals:
            raise SystemExit("Give VB or E (or E_MIN/E_MAX), not both")
        if "T" not in vals:
            raise SystemExit("VB needs T")
        lead = 0 if int(vals["T"]) in (0, 2) else 1
        w = vals.get("W", DEFAULTS[lead]["W"])
        fil = math.pi * (vals.get("FIL", COMMON["FIL"]) / 2) ** 2
        vals["E"] = vals["VB"] * rf * w * vals["L_H"] / (fil * vals.get("FLOW_K", COMMON["FLOW_K"]))
    missing = [k for k in ("T", "SIDE", "E") if k not in vals]
    if missing:
        raise SystemExit("Missing: " + ", ".join(missing) + "\n\n" + __doc__.split("Values")[0])
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
    g["WAVE"] = "E_MIN" in vals and "E_MAX" in vals
    if g["WAVE"]:
        if not 0 < g["E_MIN"] <= g["E_MAX"]:
            raise SystemExit("Need 0 < E_MIN <= E_MAX")
        g.setdefault("WAVE_T", 1.0)
        g.setdefault("SHAPE", "sine")

    arms = {}
    first = tools[0]
    for tl in tools:
        sfx = "_L" if (t == 2 and tl == 1) else ""
        a = dict(DEFAULTS[tl])
        if sfx == "":
            for key in ("W", "Z0", "CX", "CY", "ANGLE", "DR"):
                if key in vals:
                    a[key] = vals[key]
            a["FLOW_K"] = g["FLOW_K"]
            a["E"] = g["E"]
        else:
            for key in ("W", "Z0", "CX", "CY", "ANGLE", "FLOW_K", "E", "DR"):
                if key + "_L" in vals:
                    a[key] = vals[key + "_L"]
            for key in ("W", "FLOW_K", "E"):
                if key + "_L" not in vals:
                    a[key] = arms[first][key]
        dr = a.get("DR", 0.0)
        a["S"] = g["SIDE"] / 2 + dr                       # half side, bead centreline
        a["R"] = g["RF"] + dr                             # fillet radius, bead centreline
        a["D"] = a["S"] - a["R"]                          # half length of a straight
        a["T"], a["side"], a["nozzle"] = tl, SIDE[tl], NOZZLE[tl]
        arms[tl] = a
    g["arms"] = arms
    lead = arms[first]
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
    flow = lead["E"] * fil_area * lead["FLOW_K"]
    v_surface = flow / (lead["W"] * g["L_H"])                 # bead speed, straights and corners
    arm_speed = BASE_ARM_SPEED * g["MAX_VT"] / 100
    warn = []
    if v_surface > arm_speed:
        raise SystemExit(f"The straights need the arm at {v_surface:.1f} mm/s, above the arm ceiling "
                         f"{arm_speed:.0f} mm/s (MAX_VT). Lower E or raise MAX_VT.")
    omega_needed = v_surface / lead["R"]                      # cylinder relationship at R = fillet radius
    corner_arm_per_rad = max(a["D"] for a in g["arms"].values()) * math.sqrt(2)
    omega = min(omega_needed, g["MAX_VB"], arm_speed / corner_arm_per_rad)
    if omega < omega_needed - 1e-9:
        why = "MAX_VB" if omega == g["MAX_VB"] else f"the arm's {arm_speed:.0f} mm/s ceiling on the corner move"
        warn.append(f"Corners: bed capped at {omega:.3f} rad/s by {why}; the bead needs {omega_needed:.3f}. "
                    f"Extruders are scaled to x {omega / omega_needed:.2f} on the corners to match.")
    if abs(g["P"] - g["NL"] * g["L_H"]) > 1e-6:
        warn.append(f"P {g['P']:g} is not n x L_H = {g['NL'] * g['L_H']:g}: beads will be "
                    f"{g['P'] / g['NL']:.2f} mm tall but the flow is sized for {g['L_H']:g} mm.")
    if g["FIRST"] <= 0:
        raise SystemExit("FIRST must be above 0: nothing may run at or below Z0.")
    total_loops = g["C0"] + g["SPIRAL"] + g["C1"]
    t_straight = 2 * lead["D"] / v_surface
    t_corner = (math.pi / 2) / omega
    loop_s = 4 * (t_straight + t_corner)
    per = {}
    for tl, a in g["arms"].items():
        z_first = a["Z0"] + g["FIRST"]
        e_peak = (g["E_MAX"] / g["E"]) * a["E"] if g["WAVE"] else a["E"]
        if e_peak * fil_area > 30:
            warn.append(f"{a['side']}: {e_peak * fil_area:.0f} mm3/s (E {e_peak:.1f}) is above what a Revo HF "
                        "usually melts (about 30); expect under-extrusion there.")
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
                   "flow": a["E"] * fil_area * a["FLOW_K"], "corner_speed": a["D"] * math.sqrt(2) * omega}
    return {"fil_area": fil_area, "flow": flow, "v_surface": v_surface, "omega": omega, "omega_needed": omega_needed,
            "corner_efac": omega / omega_needed, "t_straight": t_straight, "t_corner": t_corner, "loop_s": loop_s,
            "total_loops": total_loops, "arm_speed": arm_speed, "warnings": warn, "per": per,
            "filament_mm": sum(a["E"] for a in g["arms"].values()) * (total_loops * loop_s + g["PRIME_S"] / 2)}


def show(g, k):
    names = " + ".join(f"{a['side']} ({a['nozzle']:g} mm)" for a in g["arms"].values())
    lead = g["arms"][min(g["arms"])]
    print(f"\nRounded square test: {names}, {'layered' if g['N'] == 2 else 'one arm'}")
    print(f"  Outline {g['SIDE']:g} mm square, fillet R {g['RF']:g}: straights {2 * lead['D']:.1f} mm, "
          f"corners {math.pi / 2 * lead['R']:.1f} mm, loop {g['L']:.1f} mm")
    print(f"  Bead {k['v_surface']:.1f} mm/s: straights = arm speed, bed still;  corners = bed "
          f"{k['omega']:.3f} rad/s ({math.degrees(k['omega']):.1f} deg/s) at R {lead['R']:g}"
          f"  [ceiling {g['MAX_VB']:.3f}], {k['t_corner']:.1f} s per corner, {k['t_straight']:.1f} s per side")
    for tl, a in g["arms"].items():
        q = k["per"][tl]
        kk = f" x FLOW_K {a['FLOW_K']:g}" if a["FLOW_K"] != 1 else ""
        print(f"  {a['side']:5}: E {a['E']:.2f} mm/s{kk} -> {q['flow']:.1f} mm3/s, bead {a['W']:g} x {g['L_H']:g} mm;"
              f"  Z0 {a['Z0']:.2f}, first {q['z_first']:.2f}, top {q['z_top']:.2f}")
        print(f"         side X {q['start'][0]:.1f} Y {q['start'][1]:.1f} -> X {q['end'][0]:.1f} Y {q['end'][1]:.1f};"
              f"  corner move out to X {q['corner_far'][0]:.1f} Y {q['corner_far'][1]:.1f} at {q['corner_speed']:.0f} mm/s")
    print(f"  1. Prime: {g['PRIME_MM']:g} mm move-in along the first side over {g['PRIME_S']:g} s from "
          f"{g['PRIME_Z']:g} mm above Z0; bed still, extruders at half E")
    print(f"  2. Flat: {g['C0']:g} loop at the first layer" + (" (each arm half the outline)" if g["N"] == 2 else ""))
    ramp = (f", easing in over {g['RAMP_STEPS'] * g['RAMP_EVERY']:g} loop ({g['P'] / g['RAMP_STEPS']:.2f} mm/loop "
            f"more every {g['RAMP_EVERY']:g} loop)" if g["RAMP_STEPS"] > 1 else "")
    print(f"  3. Spiral: {g['SPIRAL']:g} loops, rising {g['P']:g} mm/loop{ramp}")
    print(f"  4. Final flat: {g['C1']:g} loop at the top")
    if g["WAVE"]:
        how = (f"{g['WAVES_PER_LOOP']:g} waves per loop, locked to the outline" if "WAVES_PER_LOOP" in g
               else f"period {g['WAVE_T']:g} s ({k['loop_s'] / g['WAVE_T']:.1f} waves per loop)")
        print(f"  Throttle: {g['SHAPE']}, E x {g['E_MIN'] / g['E']:.2f} to x {g['E_MAX'] / g['E']:.2f}, {how}")
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
    rate = {tl: a["E"] / 2 for tl, a in arms.items()}
    v = k["arm_speed"]
    lead = arms[min(arms)]
    L = g["L"]

    def gcode(s):
        if not cold:
            return cell.gcode(s)

    def guard(tl, z):
        if z < arms[tl]["Z0"] - 1e-6:
            raise Abort(f"Refused: {arms[tl]['side']} Z {z:.2f} is below its zero height {arms[tl]['Z0']:.2f}")
        return z

    def keepalive():
        while not cell.stop_keep.wait(WATCHDOG_S / 2.5):
            for tl in arms:
                gcode(f"EXTRUDE_ON T={tl} RATE={rate[tl]:.3f} TIMEOUT={WATCHDOG_S}")

    def move(tl, xy, z, speed, wait, blend=None):
        r, pt, yw = cell.orient[tl]
        kw = {"speed": speed, "wait": wait}
        if blend is not None:
            kw["radius"] = blend
        cell.arms[tl].arm.set_position(xy[0], xy[1], guard(tl, z), r, pt, yw, **kw)

    def z_at(tl, s):
        return per[tl]["z_first"] + height(g, s / L)

    if not cold:
        for tl, a in arms.items():
            temp = cell.hot_end_temp(tl)
            if temp < g["MIN_TEMP"]:
                raise Abort(f"{a['side']} hot end is at {temp:.0f} C, below MIN_TEMP {g['MIN_TEMP']:.0f}. Heat it first "
                            f"(python3 extrude.py heat {a['side']} 215 --wait).")
    gcode("EXTRUDE_OFF")
    cell.tt.stop_rotation()
    print("Approach: each arm to park, safe height, above its prime point, down to the prime start")
    for tl in arms:
        px, py = per[tl]["prime_xy"]
        cell.move(tl, *PARK, speed=v, wait=True)
        cell.move(tl, PARK[0], PARK[1], SAFE_Z, speed=v, wait=True)
        cell.move(tl, px, py, guard(tl, per[tl]["prime_z"] + 20), speed=v, wait=True)
        cell.move(tl, px, py, guard(tl, per[tl]["prime_z"]), speed=min(v, 20), wait=True)

    # 1. Prime: bed still, half E, run in along the line of the first side
    print(f"1. Prime: bed still, extruders at half E, moving in over {g['PRIME_S']:g} s")
    for tl in arms:
        gcode(f"EXTRUDE_ON T={tl} RATE={rate[tl]:.3f} TIMEOUT={WATCHDOG_S}")
    threading.Thread(target=keepalive, daemon=True).start()
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
    for tl, a in arms.items():
        rate[tl] = a["E"]
        gcode(f"EXTRUDE_ON T={tl} RATE={rate[tl]:.3f} TIMEOUT={WATCHDOG_S}")
    a0 = cell.tt.get_angle()
    live = {"rev": 0.0, "seg": "", "bed": 0.0, "z": {tl: per[tl]["z_first"] for tl in arms},
            "xy": {tl: per[tl]["start"] for tl in arms}, "dev": {tl: [] for tl in arms},
            "efac": {tl: 1.0 for tl in arms}}
    os.makedirs(os.path.join(ROOT, "runs"), exist_ok=True)
    log_path = os.path.join(ROOT, "runs", time.strftime("square_test_%Y%m%d_%H%M%S.csv"))
    threading.Thread(target=throttle, args=(g, cell, live, log_path, cold), daemon=True).start()
    print(f"2. Flat loop: start angle {a0:.1f} deg, bead {k['v_surface']:.1f} mm/s;  logging to {log_path}")
    s_end = k["total_loops"] * L
    omega_dps = math.degrees(k["omega"])
    s = 0.0
    seg = 0
    total = 0.0                            # bed angle since the start, deg (measured)
    phase = "flat"
    while s < s_end - 1e-6:
        kq = seg // 2                      # quarter turns the bed has made before this segment
        ph = phase_name(g, s / L)
        if ph != phase:
            print({"spiral": f"3. Spiral at P {g['P']:g} mm/loop", "final flat": "4. Final flat loop"}[ph]
                  + f"  (loop {s / L:.2f})")
            phase = ph
        if seg % 2 == 0:
            # ---- straight: bed still, one move along the side, Z climbing linearly
            length = min(2 * lead["D"], s_end - s)
            frac = length / (2 * lead["D"])
            live["seg"] = f"side {kq % 4}"
            targets = {}
            for tl, a in arms.items():
                xy = straight_pt(g, a, frac * 2 * a["D"], kq, total)
                targets[tl] = (xy, z_at(tl, s + length))
                move(tl, xy, targets[tl][1], k["v_surface"], wait=False)
            t0 = time.monotonic()
            dur = length / k["v_surface"]
            while True:
                el = time.monotonic() - t0
                f = min(1.0, el / dur) if dur > 0 else 1.0
                live["rev"] = (s + f * length) / L
                for tl, a in arms.items():
                    live["xy"][tl] = straight_pt(g, a, f * frac * 2 * a["D"], kq, total)
                    live["z"][tl] = z_at(tl, s + f * length)
                arrived = all(math.dist(cell.pose(tl)[:3], (*targets[tl][0], targets[tl][1])) < 0.3 for tl in arms)
                if arrived and el >= dur - 0.05:
                    break
                if el > dur + 3:
                    print(f"  (an arm did not report arrival on side {kq % 4} within time + 3 s; carrying on)")
                    break
                time.sleep(TICK)
            s += length
        else:
            # ---- corner: bed on; the arm follows the MEASURED bed angle round the corner move
            arc = math.pi / 2 * lead["R"]
            b_end = 90.0 * min(1.0, (s_end - s) / arc)       # a partial corner if the print ends in it
            live["seg"] = f"corner {kq % 4}"
            for tl in arms:
                live["efac"][tl] = k["corner_efac"]
            t_begin = total                                   # where the bed is as this corner begins
            cell.tt.rotate_velocity(omega_dps)
            lead_deg = omega_dps * g["CORNER_LEAD_S"]
            v_cmd = min(v, max(10.0, 1.5 * max(q["corner_speed"] for q in per.values())))
            last = {tl: None for tl in arms}
            while True:
                total = cell.tt.get_angle() - a0
                b = max(0.0, total - t_begin)                  # fillet progress = bed progress
                live["bed"] = total
                if b + omega_dps * TICK >= b_end - g["STOP_LEAD"]:
                    cell.tt.stop_rotation()
                    break
                bl = min(b + lead_deg, b_end)
                for tl, a in arms.items():
                    xy = corner_pt(g, a, bl, kq, total + (bl - b))
                    z = z_at(tl, s + arc * bl / 90.0)
                    if last[tl] is None or math.dist(xy, last[tl][0]) >= 0.05 or abs(z - last[tl][1]) >= 0.02:
                        move(tl, xy, z, v_cmd, wait=False, blend=g["BLEND"])
                        last[tl] = (xy, z)
                    live["xy"][tl], live["z"][tl] = corner_pt(g, a, b, kq, total), z_at(tl, s + arc * b / 90.0)
                live["rev"] = (s + arc * b / 90.0) / L
                time.sleep(TICK)
            # let the bed come to rest, then read where it actually stopped
            t_settle = time.monotonic()
            prev = cell.tt.get_angle()
            while time.monotonic() - t_settle < SETTLE_S:
                time.sleep(0.1)
                now = cell.tt.get_angle()
                if abs(now - prev) < 0.02:
                    break
                prev = now
            total = cell.tt.get_angle() - a0
            live["bed"] = total
            b_bed = max(0.0, min(b_end, total - t_begin))
            if b_end - b_bed > 0.05:                          # bed stopped short: the arm finishes the fillet alone
                print(f"  bed stopped {b_end - b_bed:.2f} deg short on corner {kq % 4}; the arm finishes the fillet")
                for tl in arms:
                    live["efac"][tl] = 1.0
                n_pts = max(1, int(math.ceil((b_end - b_bed) / 90.0 * arc / 0.5)))
                for i in range(1, n_pts + 1):
                    b = b_bed + (b_end - b_bed) * i / n_pts
                    for tl, a in arms.items():
                        move(tl, corner_pt(g, a, b, kq, total), z_at(tl, s + arc * b / 90.0), k["v_surface"],
                             wait=(i == n_pts), blend=g["BLEND"])
                    live["rev"] = (s + arc * b / 90.0) / L
            else:
                for tl, a in arms.items():                   # exact end of the corner move, waited
                    move(tl, corner_pt(g, a, b_end, kq, total), z_at(tl, s + arc * b_end / 90.0), v_cmd, wait=True)
            for tl, a in arms.items():
                live["efac"][tl] = 1.0
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
    for tl in arms:
        gcode(f"EXTRUDE_SCALE T={tl} PERCENT=100")
    print(f"5. Extruders off after {s / L:.3f} loops ({seg} segments)")
    report_deviation(g, live)
    print("6. Move away")
    for tl in arms:
        cell.move(tl, *live["xy"][tl], guard(tl, live["z"][tl] + 10), speed=20, wait=True)
    for tl in arms:
        cell.move(tl, *PARK, speed=v, wait=True)
    cell.tt.stop_rotation()
    print("7. Bed stopped. Done.")


def throttle(g, cell, live, log_path, cold):
    """10 Hz: flow scale for the wave and the corner cap; log time, loop, segment, bed angle, Z and flow."""
    arms = g["arms"]
    amp = (g["E_MAX"] - g["E_MIN"]) / 2 / g["E"] if g["WAVE"] else 0.0
    t0 = time.monotonic()
    sent = {tl: None for tl in arms}
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("t_s,loop,segment,bed_deg,scale_pct," + ",".join(
            f"z_cmd_{s},x_act_{s},y_act_{s},z_act_{s},dz_{s},dxy_{s},e_{s}"
            for s in (arms[t]["side"] for t in arms))
            + "," + ",".join(f"temp_{arms[t]['side']}" for t in arms) + "\n")
        temps = {t: "" for t in arms}
        tick = 0
        while not cell.stop_keep.is_set():
            now = time.monotonic() - t0
            if amp:
                ph = (2 * math.pi * g["WAVES_PER_LOOP"] * live["rev"] if "WAVES_PER_LOOP" in g
                      else 2 * math.pi * now / g["WAVE_T"])
                scale = 1 + amp * SHAPES[g["SHAPE"]](ph)
            else:
                scale = 1.0
            if not cold:
                for tl in arms:
                    pct = round(100 * scale * live["efac"][tl], 1)
                    if pct != sent[tl]:
                        try:
                            cell.gcode(f"EXTRUDE_SCALE T={tl} PERCENT={pct:.1f}")
                            sent[tl] = pct
                        except Exception as ex:
                            print(f"  throttle command failed: {ex}")
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
                                f"{arms[t]['E'] * scale * live['efac'][t]:.3f}")
                else:
                    cols.append(f"{live['z'][t]:.3f},,,,,,{arms[t]['E'] * scale * live['efac'][t]:.3f}")
            if tick % 10 == 0 and not cold:
                for t in arms:
                    try:
                        temps[t] = f"{cell.hot_end_temp(t):.1f}"
                    except Exception:
                        temps[t] = ""
            cols = ",".join(cols) + "," + ",".join(temps[t] for t in arms)
            f.write(f"{now:.3f},{live['rev']:.4f},{live['seg']},{live['bed']:.2f},{100 * scale:.1f},{cols}\n")
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
    try:
        run(g, k, cell, cold=cold)
        return 0
    except KeyboardInterrupt:
        abort(cell, "Ctrl+C")
    except Abort as e:
        abort(cell, str(e))
    except Exception as e:
        abort(cell, f"error: {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
