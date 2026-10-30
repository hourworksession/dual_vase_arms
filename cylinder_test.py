"""Cylinder test on one or both arms: prime, circle, spiral, final circle.

Macro-style values, run from the repo root (test Klipper config loaded, hot ends hot):

    python3 cylinder_test.py T=0 R=60 E=12 L_H=0.8 P=0.8 --dry-run           # right arm
    python3 cylinder_test.py T=1 R=60 E=12 L_H=0.6 P=0.6 --dry-run           # left arm
    python3 cylinder_test.py T=2 R=60 E=12 L_H=0.6 --dry-run                 # both arms
    python3 cylinder_test.py T=0 R=60 E_MIN=8 E_MAX=16 WAVE_T=1 L_H=0.8 P=0.8 REVS=10
    python3 cylinder_test.py T=0 R=60 E_MIN=8 E_MAX=16 WAVE_T=1 SHAPE=square L_H=0.8 P=0.8 REVS=10
    python3 cylinder_test.py T=1 R=60 --find-z0                              # touch-off
    python3 cylinder_test.py T=1 --find-centre                               # jog to the turntable centre

Values (KEY=value, any order). In T=2 the plain keys are the RIGHT arm; *_L keys override the left.
    T       0 = right, 1 = left, 2 = both                                    (required)
    R       cylinder radius, mm                                              (required)
    L_H     bead height (layer height), mm                                   (default 0.5)
    E       filament speed, mm/s                  (required, unless E_MIN and E_MAX are given)
    P       Z rise per bed revolution, mm/rev                 (default n x L_H, n = arms used)
    W       line width, mm                                   (default 1.0, both arms)
    FLOW_K  measured / planned plastic from a test print (1.5 / 1.1 = 1.36)
    Z0      zero height, nozzle touching the disc            (default 105.9 right, 109.7 left)
    E_L, W_L, FLOW_K_L, Z0_L   left arm in T=2. Both nozzles are 1.0 mm, so the left takes the
            same E, W and FLOW_K as the right unless you set these; Z0_L defaults to 109.7
    MAX_VB  bed speed ceiling, rad/s; the bed runs at the speed that lays the right arm's
            W x L_H bead at E x FLOW_K, capped at this                       (default 3.1416)
    VB      bed speed, rad/s, instead of E: E is worked out so every arm still lays its
            W x L_H bead (E = VB x R x W x L_H / (filament area x FLOW_K)). Same VB = same bed
            speed in one- and two-arm runs.
    MAX_VT  arm speed ceiling, % of 100 mm/s                                 (default 100)
    FIRST   first layer above Z0, mm                                         (default L_H)
    SPIRAL  spiral revolutions                                               (default 3)
    MODE    T=2 only.  layered (default): the arms' beads stack, each arm on top of the other's,
            P = 2 x L_H.  side: both arms at the same height, beads side by side (a wall two
            beads thick), left ring SPACING mm outside the right, P = L_H. For tuning the
            arms' relative position before layering.
            merge: start side by side (full first ring, SIDE_REVS revs), then over MERGE_REVS
            revs the left ring slides in onto the right one and the rise grows to the layered
            P = 2 x L_H; layered from then on. A first layer that always goes down, then stacked.
    SIDE_REVS, MERGE_REVS   merge mode: revs side by side, revs to slide in      (2, 4)
    SPACING side and merge modes: left bead centre minus right bead centre, mm (default W; negative =
            left inside). The left arm's E is scaled by R_left / R so its bead matches.
    AS_DUAL=1  (T=0 or T=1) give the TWO-arm profile (its L_H, P, REVS) and run ONE arm with the
            same beads: P becomes P/2 and REVS 2 x REVS, same bed speed and E, same height.
            e.g. the 2-arm L_H=0.6 P=0.8 REVS=100 -> one arm at P 0.4 for 200 revs
    RAMP_STEPS, RAMP_EVERY   ease into the spiral: the rise per rev starts at P / RAMP_STEPS and
            goes up by that much every RAMP_EVERY rev until it is P. Default 5 steps every
            0.125 rev (L_H 0.5: 0.1, 0.2 ... 0.5 mm per bead). RAMP_STEPS=1 turns it off.
    REVS    total counted revolutions (sets SPIRAL);  HEIGHT  wall height above the first
            layer, mm (sets SPIRAL = HEIGHT / P, wins over REVS)
    E_MIN, E_MAX, WAVE_T or WAVES_PER_REV   throttle between E_MIN and E_MAX (every arm used)
    SHAPE   throttle waveform: sine, square or triangle                      (default sine)
    PRIME_MM, PRIME_S, PRIME_Z   prime move-in mm, time s, start height above Z0 mm (15, 5, 2)
    FIL     filament diameter, mm (1.75);  MIN_TEMP  refuse below this, C (190)
    ANGLE, CX, CY (and _L)   print angle and turntable centre in each arm's frame
    DR (and DR_L)  radius trim, mm: add to that arm's print radius in every mode, e.g. DR_L=1
            when the left lays its bead 1 mm inside the right's (left centre slightly off)

Sequence, with n arms (each on its own side of the disc):
  1. Prime: every arm approaches R + PRIME_MM at Z0 + PRIME_Z; bed at half speed, extruders
     at half E; over PRIME_S each nozzle moves in to R and down to Z0 + FIRST.
  2. Circle: bed to full speed, extruders to E; 1/n revolution flat at the first layer
     (with two arms, each lays half the circle, together the full ring).
  3. Spiral: Z rises P per revolution for SPIRAL revolutions (starts at rev 1/n).
  4. Final circle: 1/n revolution flat at the top.
  5. Extruders off at the end angle. 6. Lift 10 mm and park. 7. Stop the bed.
Revolutions are counted on the turntable's measured angle. Nothing is commanded below an
arm's Z0. Ctrl+C aborts: extruders off, bed stop, arms lift. Every run logs to runs/*.csv.
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]

SIDE = {0: "right", 1: "left"}
NOZZLE = {0: 1.0, 1: 1.0}         # Revo HF 1.0 mm on both arms, 29 Oct 2026
DEFAULTS = {  # per tool; centres and start angles from cell studio's cylinder defaults
    0: {"W": 1.0, "Z0": 105.80, "CX": 510.08, "CY": -18.98, "ANGLE": 135.0},   # centre cali_jog 29 Oct, Z0 touch-off at R 60
    1: {"W": 1.0, "Z0": 109.60, "CX": 511.60, "CY": -10.11, "ANGLE": 135.0},   # centre cali_jog 29 Oct, Z0 touch-off at R 60
}
COMMON = {"FLOW_K": 1.0, "MAX_VB": math.pi, "MAX_VT": 100.0, "SPIRAL": 3,
          "PRIME_MM": 15.0, "PRIME_S": 5.0, "PRIME_Z": 2.0, "FIL": 1.75, "MIN_TEMP": 190.0}
BASE_ARM_SPEED = 100.0            # mm/s = MAX_VT 100 %
PARK = (400.0, 174.0, 180.0)      # cell studio's park pose (each arm's own frame)
SAFE_Z = 250.0
WATCHDOG_S = 5.0


SHAPES = {   # phase in radians -> -1 .. +1
    "sine": math.sin,
    "square": lambda ph: 1.0 if math.sin(ph) >= 0 else -1.0,
    "triangle": lambda ph: 2 / math.pi * math.asin(math.sin(ph)),
}


class Abort(Exception):
    pass


# ----------------------------------------------------------------------
def parse(argv):
    vals, flags = {}, set()
    for a in argv:
        if a.startswith("--"):
            flags.add(a)
        elif "=" in a:
            k, v = a.split("=", 1)
            k = k.strip().upper()
            if k == "MODE":
                v = {"layered": "layered", "layer": "layered", "side": "side", "side-by-side": "side",
                     "sbs": "side", "merge": "merge"}.get(v.strip().lower())
                if v is None:
                    raise SystemExit("MODE must be layered, side or merge")
                vals[k] = v
            elif k == "SHAPE":
                v = v.strip().lower()
                if v not in SHAPES:
                    raise SystemExit(f"SHAPE must be one of {', '.join(SHAPES)}")
                vals[k] = v
            else:
                vals[k] = float(v)
        else:
            raise SystemExit(f"Not understood: {a!r} (use KEY=value, e.g. R=60)")
    if "--find-z0" in flags or "--find-centre" in flags:   # only the arm (and radius) needed
        for k_, v_ in (("E", 1.0), ("L_H", 0.5), ("R", 60.0)):
            vals.setdefault(k_, v_)
    if "E" not in vals and "E_MIN" in vals and "E_MAX" in vals:
        vals["E"] = (vals["E_MIN"] + vals["E_MAX"]) / 2
    vals.setdefault("L_H", 0.5)                            # 1.0 mm nozzle: 1.0 x 0.5 mm bead
    if "VB" in vals:                                       # bed speed given: work out E from it
        if "E" in vals or "E_MIN" in vals:
            raise SystemExit("Give VB or E (or E_MIN/E_MAX), not both")
        if "R" not in vals or "T" not in vals:
            raise SystemExit("VB needs T and R")
        lead = 0 if int(vals["T"]) in (0, 2) else 1
        w = vals.get("W", DEFAULTS[lead]["W"])
        fil = math.pi * (vals.get("FIL", COMMON["FIL"]) / 2) ** 2
        vals["E"] = vals["VB"] * vals["R"] * w * vals["L_H"] / (fil * vals.get("FLOW_K", COMMON["FLOW_K"]))
    missing = [k for k in ("T", "R", "E") if k not in vals]
    if missing:
        raise SystemExit("Missing: " + ", ".join(missing) + "\n\n" + __doc__.split("Values")[0])
    t = int(vals["T"])
    if t not in (0, 1, 2):
        raise SystemExit("T must be 0 (right), 1 (left) or 2 (both)")
    if t == 2 and ("--find-z0" in flags or "--find-centre" in flags):
        raise SystemExit("Calibrate one arm at a time: T=0 or T=1")
    tools = [0, 1] if t == 2 else [t]
    g = {**COMMON, **vals, "T": t, "N": len(tools)}
    g.setdefault("MODE", "layered")
    if g["MODE"] in ("side", "merge") and t != 2:
        raise SystemExit(f"MODE={g['MODE']} needs both arms (T=2)")
    g["NL"] = 1 if g["MODE"] == "side" else g["N"]         # beads per ring height: 2 stacked, 1 side by side
    g.setdefault("SIDE_REVS", 2.0)
    g.setdefault("MERGE_REVS", 4.0)
    g.setdefault("FIRST", g["L_H"])                        # first bead the same height as the rest
    if vals.get("AS_DUAL"):
        if t == 2:
            raise SystemExit("AS_DUAL is for one arm (T=0 or T=1): it runs that arm as if the other were there")
        g["P"] = vals.get("P", 2 * g["L_H"]) / 2          # each arm's beads sat P/2 apart in the 2-arm run
        if "REVS" in vals:
            vals["REVS"] = g["REVS"] = vals["REVS"] * 2     # same height at half the rise per rev
    g.setdefault("P", g["NL"] * g["L_H"])
    g.setdefault("RAMP_STEPS", 5)
    g.setdefault("RAMP_EVERY", 0.125)
    if g["RAMP_STEPS"] < 1 or g["RAMP_EVERY"] <= 0:
        raise SystemExit("RAMP_STEPS must be 1 or more and RAMP_EVERY above 0")
    g["RAMP_STEPS"] = int(g["RAMP_STEPS"])
    # opening circle: a full ring when the arms lay rings of their own, 1/n when they share one
    g["C0"] = 1.0 if g["MODE"] == "merge" else 1.0 / g["NL"]
    g["C1"] = 1.0 / g["NL"]
    if "HEIGHT" in vals:
        if g["MODE"] == "merge":
            g["SPIRAL"] = 0.0                                  # set once the arms (and W) are known
        else:
            g["SPIRAL"] = (vals["HEIGHT"] + ramp_loss(g)) / g["P"]
    elif "REVS" in vals:
        if vals["REVS"] < g["C0"] + g["C1"]:
            raise SystemExit("REVS is too small for the opening and closing circles")
        g["SPIRAL"] = vals["REVS"] - g["C0"] - g["C1"]
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
        if sfx == "":                                     # plain keys = this arm
            for key in ("W", "Z0", "CX", "CY", "ANGLE", "DR"):
                if key in vals:
                    a[key] = vals[key]
            a["FLOW_K"] = g["FLOW_K"]
            a["E"] = g["E"]
        else:                                             # left arm in T=2
            for key in ("W", "Z0", "CX", "CY", "ANGLE", "FLOW_K", "E", "DR"):
                if key + "_L" in vals:
                    a[key] = vals[key + "_L"]
            for key in ("W", "FLOW_K", "E"):              # same nozzle: same as the right
                if key + "_L" not in vals:
                    a[key] = arms[first][key]
        a["R"] = g["R"] + a.get("DR", 0.0)
        if g["MODE"] == "merge" and sfx:                  # left ends on the right's ring; starts SPACING out
            g["S"] = vals.get("SPACING", arms[first]["W"])
            if g["R"] + g["S"] <= 0:
                raise SystemExit("SPACING puts the left ring at or inside the centre")
        if g["MODE"] == "side" and sfx:                   # left ring beside the right one
            a["R"] += vals.get("SPACING", arms[first]["W"])
            if a["R"] <= 0:
                raise SystemExit("SPACING puts the left ring at or inside the centre")
            if "E_L" not in vals:                         # same bead on a longer path: more filament
                a["E"] = arms[first]["E"] * (g["R"] + vals.get("SPACING", arms[first]["W"])) / g["R"]
        a["T"], a["side"], a["nozzle"] = tl, SIDE[tl], NOZZLE[tl]
        arms[tl] = a
    g["arms"] = arms
    g.setdefault("S", 0.0)
    if g["MODE"] == "merge":
        g["WM"] = arms[first]["W"]
        lead_in = g["SIDE_REVS"] + g["MERGE_REVS"]
        if "HEIGHT" in vals:
            h_in = spiral(g, lead_in)[0]
            g["SPIRAL"] = lead_in + max(0.0, vals["HEIGHT"] - h_in) / g["P"]
        if g["SPIRAL"] < lead_in:
            raise SystemExit(f"merge needs at least {lead_in + g['C0'] + g['C1']:g} REVS "
                             f"({g['C0']:g} ring + {g['SIDE_REVS']:g} side by side + {g['MERGE_REVS']:g} merging "
                             f"+ {g['C1']:g} final circle)")
    return g, flags


def ramp_loss(g):
    """Height the ramp gives up compared with climbing at full P from the start, mm."""
    n, every = g["RAMP_STEPS"], g["RAMP_EVERY"]
    return g["P"] * every * (n - 1) / 2


def spiral_dz(g, u, P=None):
    """Height above the first layer after u revolutions of spiral at pitch P, with the ramp-in."""
    n, every = g["RAMP_STEPS"], g["RAMP_EVERY"]
    P = g["P"] if P is None else P
    if u >= n * every:
        return P * u - P * every * (n - 1) / 2
    k = int(u // every)                         # whole steps done; step j climbs at P (j+1)/n
    return P * every * k * (k + 1) / (2 * n) + P * (k + 1) / n * (u - k * every)


def spiral(g, u):
    """(height above the first layer, left arm's extra radius) after u revs of spiral.

    layered / side / one arm: the plain spiral, no extra radius. merge: side by side at
    L_H per rev, then the left ring slides in from S to 0 over MERGE_REVS while the rise
    grows so the plastic still fills the narrowing wall (2 W L_H per rev over a wall
    W + S(1-f) wide), then layered at P."""
    if g["MODE"] != "merge":
        return spiral_dz(g, u), 0.0
    L, W, S = g["L_H"], g["WM"], g["S"]
    sb, m = g["SIDE_REVS"], g["MERGE_REVS"]
    if u < sb:
        return spiral_dz(g, u, L), S
    h0 = spiral_dz(g, sb, L)

    def merged(f):                     # height gained over the first f of the merge
        if abs(S) < 1e-9:
            return 2 * L * m * f
        return 2 * W * L * m / S * math.log((W + S) / (W + S * (1 - f)))
    if u < sb + m:
        f = (u - sb) / m
        return h0 + merged(f), S * (1 - f)
    return h0 + merged(1.0) + g["P"] * (u - sb - m), 0.0


def where(g, rev):
    """Phase, height above the first layer and the left arm's extra radius at a bed revolution."""
    c0, n_sp = g["C0"], g["SPIRAL"]
    if rev < c0:
        return "circle", 0.0, g["S"] if g["MODE"] == "merge" else 0.0
    if rev < c0 + n_sp:
        dz, off = spiral(g, rev - c0)
        ph = "spiral"
        if g["MODE"] == "merge":
            u = rev - c0
            ph = "side" if u < g["SIDE_REVS"] else "merge" if u < g["SIDE_REVS"] + g["MERGE_REVS"] else "spiral"
        return ph, dz, off
    dz, off = spiral(g, n_sp)
    return "final circle", dz, off


def plan(g):
    """Every number the run will use, worked out before anything moves."""
    fil_area = math.pi * (g["FIL"] / 2) ** 2
    lead = g["arms"][min(g["arms"])]
    flow = lead["E"] * fil_area * lead["FLOW_K"]              # mm3/s laid by the lead arm
    v_surface = flow / (lead["W"] * g["L_H"])
    omega_needed = v_surface / g["R"]
    omega = min(omega_needed, g["MAX_VB"])
    n = g["NL"]
    circle = g["C1"]
    total_revs = g["C0"] + g["SPIRAL"] + g["C1"]
    rev_s = 2 * math.pi / omega
    warn = []
    if omega < omega_needed:
        warn.append(f"Bed capped at MAX_VB {g['MAX_VB']:.3f} rad/s; the bead needs {omega_needed:.3f}. "
                    f"Over-extruded by {omega_needed / omega:.2f}x.")
    if abs(g["P"] - n * g["L_H"]) > 1e-6:
        warn.append(f"P {g['P']:g} is not n x L_H = {n * g['L_H']:g}: beads will be "
                    f"{g['P'] / n:.2f} mm tall but the flow is sized for {g['L_H']:g} mm.")
    if g.get("AS_DUAL"):
        warn.append(f"AS_DUAL: one arm laying the two-arm wall's beads ({g['P']:g} mm apart, "
                    f"{total_revs:g} revs); the arm climbs half as fast as in the two-arm run.")
    if g["FIRST"] <= 0:
        raise SystemExit("FIRST must be above 0: nothing may run at or below Z0.")
    per = {}
    for tl, a in g["arms"].items():
        ang = math.radians(a["ANGLE"])
        pt = lambda r, a=a, ang=ang: (a["CX"] + r * math.cos(ang), a["CY"] + r * math.sin(ang))
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
        prime_dist = math.hypot(g["PRIME_MM"], g["PRIME_Z"] - g["FIRST"])
        r0 = a["R"] + (g["S"] if (g["MODE"] == "merge" and tl != min(g["arms"])) else 0.0)
        per[tl] = {"z_first": z_first, "z_top": z_first + spiral(g, g["SPIRAL"])[0], "pt": pt,
                   "prime_xy": pt(r0 + g["PRIME_MM"]), "print_xy": pt(r0),
                   "prime_z": a["Z0"] + g["PRIME_Z"], "prime_speed": prime_dist / g["PRIME_S"],
                   "flow": a["E"] * fil_area * a["FLOW_K"]}
    return {"fil_area": fil_area, "flow": flow, "v_surface": v_surface, "omega": omega,
            "omega_needed": omega_needed, "rev_s": rev_s, "circle": circle, "total_revs": total_revs,
            "arm_speed": BASE_ARM_SPEED * g["MAX_VT"] / 100, "warnings": warn, "per": per,
            "filament_mm": sum(a["E"] for a in g["arms"].values()) * (total_revs * rev_s + g["PRIME_S"] / 2)}


def show(g, k):
    names = " + ".join(f"{a['side']} ({a['nozzle']:g} mm)" for a in g["arms"].values())
    mode = {"layered": "layered", "side": "side by side", "merge": "side by side, then merging into layered"}[g["MODE"]] if g["N"] == 2 else "one arm"
    print(f"\nCylinder test: {names}, {mode}")
    print(f"  Bed {k['omega']:.3f} rad/s ({math.degrees(k['omega']):.1f} deg/s), {k['rev_s']:.1f} s per rev, "
          f"{k['v_surface']:.1f} mm/s at R {g['R']:g}   [ceiling {g['MAX_VB']:.3f}]")
    for tl, a in g["arms"].items():
        q = k["per"][tl]
        kk = f" x FLOW_K {a['FLOW_K']:g}" if a["FLOW_K"] != 1 else ""
        print(f"  {a['side']:5}: E {a['E']:.2f} mm/s{kk} -> {q['flow']:.1f} mm3/s, bead {a['W']:g} x {g['L_H']:g} mm;"
              f"  Z0 {a['Z0']:.2f}, first {q['z_first']:.2f}, top {q['z_top']:.2f};"
              f"  R {a['R'] + (g['S'] if g['MODE'] == 'merge' and tl == max(g['arms']) else 0):g}"
              + (f" -> {a['R']:g}" if g["MODE"] == "merge" and tl == max(g["arms"]) else "")
              + f" at X {q['print_xy'][0]:.1f} Y {q['print_xy'][1]:.1f}")
    print(f"  1. Prime: {g['PRIME_MM']:g} mm move-in over {g['PRIME_S']:g} s from {g['PRIME_Z']:g} mm above Z0; "
          f"bed {k['omega'] / 2:.3f} rad/s, extruders at half E")
    print(f"  2. Circle: {g['C0']:g} rev flat at the first layer"
          + (" (each arm its own full ring, side by side)" if g["MODE"] in ("side", "merge") else
             " (each arm half the circle)" if g["NL"] == 2 else ""))
    if g["MODE"] == "merge":
        sb, m = g["SIDE_REVS"], g["MERGE_REVS"]
        h_sb, h_m = spiral(g, sb)[0], spiral(g, sb + m)[0]
        print(f"  3a. Side by side: {sb:g} rev at {g['L_H']:g} mm/rev, left ring {g['S']:g} mm outside"
              f"  (to {h_sb:.2f} mm)")
        print(f"  3b. Merge: {m:g} rev, left ring slides in to the right's, rise grows to {g['P']:g} mm/rev"
              f"  (to {h_m:.2f} mm)")
    ramp = (f", easing in over {g['RAMP_STEPS'] * g['RAMP_EVERY']:g} rev ({g['P'] / g['RAMP_STEPS']:.2f} mm/rev "
            f"more every {g['RAMP_EVERY']:g} rev)" if g["RAMP_STEPS"] > 1 else "")
    if g["MODE"] == "merge":
        rest = g["SPIRAL"] - g["SIDE_REVS"] - g["MERGE_REVS"]
        print(f"  3c/4. Layered spiral: {rest:g} rev at {g['P']:g} mm/rev")
    else:
        print(f"  3/4. Spiral: {g['SPIRAL']:g} rev from rev {g['C0']:g}, rising {g['P']:g} mm/rev{ramp}")
    print(f"  5. Final circle: {g['C1']:g} rev flat at the top")
    if g["WAVE"]:
        how = (f"{g['WAVES_PER_REV']:g} waves per rev, locked to the bed" if "WAVES_PER_REV" in g
               else f"period {g['WAVE_T']:g} s ({k['rev_s'] / g['WAVE_T']:.1f} waves per rev)")
        print(f"  Throttle: {g['SHAPE']}, E x {g['E_MIN'] / g['E']:.2f} to x {g['E_MAX'] / g['E']:.2f}, {how}")
    print(f"  6-8. Extruders off at {k['total_revs']:g} rev, lift 10 mm, park, stop bed")
    print(f"  Time about {k['total_revs'] * k['rev_s'] + g['PRIME_S']:.0f} s, filament about "
          f"{k['filament_mm']:.0f} mm in total;  arm moves {k['arm_speed']:.0f} mm/s")
    for w in k["warnings"]:
        print("  CHECK: " + w)


# ----------------------------------------------------------------------
class Cell:
    """Turntable, extruder link and the arms in use. Real ones, or cell studio's simulators."""

    def __init__(self, g, sim):
        from cell_studio import tool as toolmod
        self.sim = sim
        td = toolmod.load()
        self.orient = {tl: toolmod.orientation(td, SIDE[tl]) for tl in g["arms"]}
        if sim:
            from cell_studio.sim_hardware import SimArm, SimExtruder, SimTurntable
            self.arms = {tl: SimArm("sim", SIDE[tl]) for tl in g["arms"]}
            self.tt, self.ext = SimTurntable(), SimExtruder()
        else:
            from config_loader import load_config
            from arm_controller import ArmController
            from extruder_controller_manual import ManualExtruderController
            from turntable_controller import TurntableController
            cfg = load_config()
            self.arms = {tl: ArmController(cfg["arms"][SIDE[tl]]["ip"], SIDE[tl]) for tl in g["arms"]}
            self.tt = TurntableController(host=cfg["turntable"]["controller_ip"], axis=cfg["turntable"]["axis"])
            self.ext = ManualExtruderController(cfg["moonraker"]["host"], cfg["moonraker"]["port"])
        self.stop_keep = threading.Event()      # ends keepalive and throttle (also on abort)
        self.lock = threading.Lock()
        for a in self.arms.values():
            a.connect()
        self.tt.connect()

    def gcode(self, s):
        with self.lock:                          # keepalive, throttle and main loop share one link
            return self.ext.send_gcode(s, timeout=5)

    def hot_end_temp(self, t):
        if self.sim:
            return 999.0
        st = self.ext.get_printer_status()
        name = "heater_generic " + ("right_hotend" if t == 0 else "left_hotend")
        if name not in st:
            raise Abort("The test Klipper config is not loaded (no right_hotend/left_hotend). "
                        "Load 'Klipper Configurations/non_extruder_control_test.cfg'.")
        return st[name].get("temperature", 0.0)

    def pose(self, tl):
        return self.arms[tl].get_pose()

    def move(self, tl, x, y, z, speed, wait):
        r, pt, yw = self.orient[tl]
        self.arms[tl].arm.set_position(x, y, z, r, pt, yw, speed=speed, wait=wait)


def run(g, k, cell):
    arms, per = g["arms"], k["per"]
    rate = {tl: a["E"] / 2 for tl, a in arms.items()}

    def guard(tl, z):
        if z < arms[tl]["Z0"] - 1e-6:
            raise Abort(f"Refused: {arms[tl]['side']} Z {z:.2f} is below its zero height {arms[tl]['Z0']:.2f}")
        return z

    def keepalive():
        while not cell.stop_keep.wait(WATCHDOG_S / 2.5):
            for tl in arms:
                cell.gcode(f"EXTRUDE_ON T={tl} RATE={rate[tl]:.3f} TIMEOUT={WATCHDOG_S}")

    for tl, a in arms.items():
        temp = cell.hot_end_temp(tl)
        if temp < g["MIN_TEMP"]:
            raise Abort(f"{a['side']} hot end is at {temp:.0f} C, below MIN_TEMP {g['MIN_TEMP']:.0f}. Heat it first "
                        f"(python3 extrude.py heat {a['side']} 215 --wait).")
    cell.gcode("EXTRUDE_OFF")
    v = k["arm_speed"]
    print("Approach: each arm to park, safe height, above its prime point, down to the prime start")
    for tl in arms:
        px, py = per[tl]["prime_xy"]
        cell.move(tl, *PARK, speed=v, wait=True)
        cell.move(tl, PARK[0], PARK[1], SAFE_Z, speed=v, wait=True)
        cell.move(tl, px, py, guard(tl, per[tl]["prime_z"] + 20), speed=v, wait=True)
        cell.move(tl, px, py, guard(tl, per[tl]["prime_z"]), speed=min(v, 20), wait=True)

    # 1. Prime (all arms together)
    print(f"1. Prime: bed {k['omega'] / 2:.3f} rad/s, extruders at half E, moving in over {g['PRIME_S']:g} s")
    cell.tt.rotate_velocity(math.degrees(k["omega"] / 2))
    for tl in arms:
        cell.gcode(f"EXTRUDE_ON T={tl} RATE={rate[tl]:.3f} TIMEOUT={WATCHDOG_S}")
    threading.Thread(target=keepalive, daemon=True).start()
    for tl in arms:
        qx, qy = per[tl]["print_xy"]
        cell.move(tl, qx, qy, guard(tl, per[tl]["z_first"]), speed=per[tl]["prime_speed"], wait=False)
    t_start = time.monotonic()
    t_end = t_start + g["PRIME_S"] + 3
    while time.monotonic() < t_end:
        arrived = all(math.dist(cell.pose(tl)[:3], (*per[tl]["print_xy"], per[tl]["z_first"])) < 0.3 for tl in arms)
        if arrived and time.monotonic() - t_start >= g["PRIME_S"]:
            break
        time.sleep(0.05)
    else:
        print("  (an arm did not report arrival within prime time + 3 s; carrying on)")

    # 2-5. Circle, spiral, final circle, counted on the turntable angle
    cell.tt.rotate_velocity(math.degrees(k["omega"]))
    for tl, a in arms.items():
        rate[tl] = a["E"]
        cell.gcode(f"EXTRUDE_ON T={tl} RATE={rate[tl]:.3f} TIMEOUT={WATCHDOG_S}")
    a0 = cell.tt.get_angle()
    c = g["C0"]
    print(f"2. Circle: {c:g} rev, bed {k['omega']:.3f} rad/s, extruders at E, start angle {a0:.1f} deg")
    live = {"rev": 0.0, "z": {tl: per[tl]["z_first"] for tl in arms},
            "xy": {tl: per[tl]["print_xy"] for tl in arms}, "dev": {tl: [] for tl in arms},
            "efac": {tl: 1.0 for tl in arms}}
    os.makedirs(os.path.join(ROOT, "runs"), exist_ok=True)
    log_path = os.path.join(ROOT, "runs", time.strftime("cylinder_test_%Y%m%d_%H%M%S.csv"))
    threading.Thread(target=throttle, args=(g, cell, live, log_path), daemon=True).start()
    print(f"   logging to {log_path}")
    n_sp, phase = g["SPIRAL"], "circle"
    last_z = dict(live["z"])
    last_xy = dict(live["xy"])
    left = max(arms) if len(arms) == 2 else None
    while True:
        rev = (cell.tt.get_angle() - a0) / 360.0
        live["rev"] = rev
        if rev >= k["total_revs"]:
            break
        ph, dz, off = where(g, rev)
        if ph != phase:
            print({"side": f"3a. Side by side: {g['SIDE_REVS']:g} rev",
                   "merge": f"3b. Merge: left ring sliding in over {g['MERGE_REVS']:g} rev",
                   "spiral": f"3. Spiral at P {g['P']:g} mm/rev",
                   "final circle": f"5. Final circle: {g['C1']:g} rev at the top"}[ph] + f"  (rev {rev:.2f})")
            phase = ph
        for tl in arms:
            z = per[tl]["z_first"] + dz
            xy = per[tl]["print_xy"]
            if g["MODE"] == "merge" and tl == left:
                r = arms[tl]["R"] + off
                xy = per[tl]["pt"](r)
                live["efac"][tl] = r / arms[tl]["R"]          # longer ring: a little more filament
            if abs(z - last_z[tl]) >= 0.02 or math.dist(xy, last_xy[tl]) >= 0.02:
                cell.move(tl, *xy, guard(tl, z), speed=10, wait=False)
                last_z[tl], last_xy[tl] = z, xy
                live["z"][tl], live["xy"][tl] = z, xy
        time.sleep(0.05)

    # 6-8
    cell.stop_keep.set()
    cell.gcode("EXTRUDE_OFF")
    for tl in arms:
        cell.gcode(f"EXTRUDE_SCALE T={tl} PERCENT=100")
    print(f"6. Extruders off at rev {(cell.tt.get_angle() - a0) / 360:.3f}")
    report_deviation(g, live)
    print("7. Move away")
    for tl in arms:
        cell.move(tl, *last_xy[tl], guard(tl, last_z[tl] + 10), speed=20, wait=True)
    for tl in arms:
        cell.move(tl, *PARK, speed=v, wait=True)
    cell.tt.stop_rotation()
    print("8. Bed stopped. Done.")


def throttle(g, cell, live, log_path):
    """10 Hz: set the flow scale for the sine (if any) and log time, rev, Z and commanded flow."""
    arms = g["arms"]
    amp = (g["E_MAX"] - g["E_MIN"]) / 2 / g["E"] if g["WAVE"] else 0.0     # as a fraction of E
    t0 = time.monotonic()
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("t_s,rev,scale_pct," + ",".join(
            f"z_cmd_{s},x_act_{s},y_act_{s},z_act_{s},dz_{s},dxy_{s},e_{s}"
            for s in (arms[t]["side"] for t in arms))
            + "," + ",".join(f"temp_{arms[t]['side']}" for t in arms) + "\n")
        temps = {t: "" for t in arms}
        tick = 0
        while not cell.stop_keep.is_set():
            now = time.monotonic() - t0
            if amp:
                ph = (2 * math.pi * g["WAVES_PER_REV"] * live["rev"] if "WAVES_PER_REV" in g
                      else 2 * math.pi * now / g["WAVE_T"])
                scale = 1 + amp * SHAPES[g["SHAPE"]](ph)
            else:
                scale = 1.0
            if amp or g["MODE"] == "merge":
                for tl in arms:
                    try:
                        cell.gcode(f"EXTRUDE_SCALE T={tl} PERCENT={100 * scale * live['efac'][tl]:.1f}")
                    except Exception as ex:
                        print(f"  throttle command failed: {ex}")
            cols = []
            for t in arms:
                try:
                    p = cell.pose(t)                  # what the arm reports (its own encoders)
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
            if tick % 10 == 0:                         # hot-end temperature once a second
                for t in arms:
                    try:
                        temps[t] = f"{cell.hot_end_temp(t):.1f}"
                    except Exception:
                        temps[t] = ""
            cols = ",".join(cols) + "," + ",".join(temps[t] for t in arms)
            f.write(f"{now:.3f},{live['rev']:.4f},{100 * scale:.1f},{cols}\n")
            tick += 1
            delay = t0 + tick * 0.1 - time.monotonic()
            if delay > 0:
                cell.stop_keep.wait(delay)


def report_deviation(g, live):
    """Commanded vs reported arm position over the spiral (skips the first 0.5 rev of settling)."""
    print("   Arm position vs command (from the arm's own reports; physical sag or slip won't show here):")
    for t, rows in live["dev"].items():
        rows = [r for r in rows if r[0] >= 0.5]
        if not rows:
            continue
        dz = [abs(r[1]) for r in rows]
        dxy = [r[2] for r in rows]
        rms = lambda v: math.sqrt(sum(x * x for x in v) / len(v))
        print(f"   {g['arms'][t]['side']:5}: Z off by max {max(dz):.3f} / rms {rms(dz):.3f} mm;  "
              f"XY off by max {max(dxy):.3f} / rms {rms(dxy):.3f} mm  ({len(rows)} samples)")


def find_z0(g, k, cell):
    """Touch-off: hover over the print point, step down by hand, read Z when paper drags."""
    tl = next(iter(g["arms"]))
    a, q = g["arms"][tl], k["per"][tl]
    z0_guess = a["Z0"]
    floor = z0_guess - 25.0                     # never below this, even by hand
    qx, qy = q["print_xy"]
    v = k["arm_speed"]
    print(f"\nTouch-off for the {a['side']} arm at R {g['R']:g} (X {qx:.1f} Y {qy:.1f}). "
          f"Current zero height {z0_guess:.2f}; hand limit {floor:.2f}.")
    cell.move(tl, *PARK, speed=v, wait=True)
    cell.move(tl, PARK[0], PARK[1], SAFE_Z, speed=v, wait=True)
    cell.move(tl, qx, qy, z0_guess + 20, speed=v, wait=True)
    z = z0_guess + 5
    cell.move(tl, qx, qy, z, speed=10, wait=True)
    help_ = ("Type a step and Enter:  -1  -0.5  -0.1  -0.05  (down)   +1  +0.1 (up)\n"
             "  r = turn the bed 90 deg (check another spot)   ok = paper drags here   q = quit\n"
             "Slide a sheet of paper (about 0.1 mm) under the nozzle between steps.")
    print(help_)
    found = []
    while True:
        try:
            pose_z = cell.pose(tl)[2]
        except Exception:
            pose_z = z
        ans = input(f"Z {z:.2f} (arm reports {pose_z:.2f}) > ").strip().lower()
        if ans == "":
            continue
        if ans in ("q", "quit"):
            break
        if ans == "ok":
            found.append(z - 0.1)                 # paper thickness
            print(f"  zero height here = {z - 0.1:.2f} (Z {z:.2f} minus 0.1 mm paper)")
            continue
        if ans == "r":
            cell.move(tl, qx, qy, z + 2, speed=10, wait=True)
            cell.tt.rotate_velocity(30.0)
            time.sleep(3.0)                      # 90 deg at 30 deg/s
            cell.tt.stop_rotation()
            time.sleep(0.5)
            cell.move(tl, qx, qy, z, speed=5, wait=True)
            print("  bed turned 90 deg")
            continue
        try:
            step = float(ans)
        except ValueError:
            print(help_)
            continue
        if abs(step) > 5:
            print("  steps are limited to 5 mm")
            continue
        new = z + step
        if new < floor:
            print(f"  refused: {new:.2f} is below the hand limit {floor:.2f}")
            continue
        cell.move(tl, qx, qy, new, speed=5, wait=True)
        z = new
    cell.move(tl, qx, qy, z + 10, speed=10, wait=True)
    cell.move(tl, *PARK, speed=v, wait=True)
    if found:
        mean = sum(found) / len(found)
        spread = max(found) - min(found)
        print(f"\nZero heights found: {', '.join(f'{f:.2f}' for f in found)}  "
              f"(mean {mean:.2f}, spread {spread:.2f} mm)")
        print(f"Use it with:  Z0={mean:.2f}   (e.g. python3 cylinder_test.py T={tl} Z0={mean:.2f} ...)")
        if spread > 0.3:
            print("The bed is not flat or level at this radius by more than 0.3 mm: worth fixing before printing.")


def find_centre(g, k, cell):
    """Jog the nozzle onto the turntable axis in X, Y, Z; record CX, CY, Z0 for that arm."""
    tl = next(iter(g["arms"]))
    a = g["arms"][tl]
    cx0, cy0, z0 = a["CX"], a["CY"], a["Z0"]
    v = k["arm_speed"]
    lim_xy, floor = 30.0, z0 - 25.0
    print(f"\nCentre jog for the {a['side']} arm. Current centre X {cx0:.2f} Y {cy0:.2f}, zero height {z0:.2f}.")
    cell.move(tl, *PARK, speed=v, wait=True)
    cell.move(tl, PARK[0], PARK[1], SAFE_Z, speed=v, wait=True)
    cell.move(tl, cx0, cy0, z0 + 20, speed=v, wait=True)
    x, y, z = cx0, cy0, z0 + 5
    cell.move(tl, x, y, z, speed=10, wait=True)
    help_ = ("Type a move and Enter (any axis, any step up to 5 mm):\n"
             "  x+1  x-0.5  y+0.1  y-2  z-1  z+0.1     (or just -0.1 / +0.1 for Z)\n"
             "  r = lift 2 mm, turn the bed 180 deg, come back down    ok = record here    q = quit\n"
             "Finding the axis: put a pen dot on the disc under the nozzle, press r. If the dot stays\n"
             "under the nozzle you are on the axis; if not, jog HALF way towards where it went, repeat.\n"
             "Bring Z down to paper-drag before 'ok' to record the zero height too.")
    print(help_)
    found = []
    while True:
        try:
            px, py, pz = cell.pose(tl)[:3]
        except Exception:
            px, py, pz = x, y, z
        ans = input(f"X {x:.2f}  Y {y:.2f}  Z {z:.2f}   (arm reports {px:.2f} {py:.2f} {pz:.2f}) > ").strip().lower()
        if ans == "":
            continue
        if ans in ("q", "quit"):
            break
        if ans == "ok":
            found.append((x, y, z - 0.1))
            print(f"  recorded: CX {x:.2f}  CY {y:.2f}  Z0 {z - 0.1:.2f} (Z minus 0.1 mm paper; ignore Z0 if you "
                  "were not at paper-drag)")
            continue
        if ans == "r":
            cell.move(tl, x, y, z + 2, speed=10, wait=True)
            cell.tt.rotate_velocity(60.0)
            time.sleep(3.0)                      # 180 deg at 60 deg/s
            cell.tt.stop_rotation()
            time.sleep(0.5)
            cell.move(tl, x, y, z, speed=5, wait=True)
            print("  bed turned 180 deg")
            continue
        axis, rest = (ans[0], ans[1:]) if ans[0] in "xyz" else ("z", ans)
        try:
            step = float(rest)
        except ValueError:
            print(help_)
            continue
        if abs(step) > 5:
            print("  steps are limited to 5 mm")
            continue
        nx, ny, nz = x + (step if axis == "x" else 0), y + (step if axis == "y" else 0), z + (step if axis == "z" else 0)
        if math.hypot(nx - cx0, ny - cy0) > lim_xy:
            print(f"  refused: more than {lim_xy:g} mm from the starting centre")
            continue
        if nz < floor:
            print(f"  refused: Z {nz:.2f} is below the hand limit {floor:.2f}")
            continue
        cell.move(tl, nx, ny, nz, speed=5, wait=True)
        x, y, z = nx, ny, nz
    cell.move(tl, x, y, z + 10, speed=10, wait=True)
    cell.move(tl, *PARK, speed=v, wait=True)
    if found:
        mx = sum(f[0] for f in found) / len(found)
        my = sum(f[1] for f in found) / len(found)
        mz = sum(f[2] for f in found) / len(found)
        print(f"\nTurntable centre for the {a['side']} arm: CX {mx:.2f}  CY {my:.2f}  (was {cx0:.2f}, {cy0:.2f}; "
              f"moved {math.hypot(mx - cx0, my - cy0):.2f} mm)   Z0 {mz:.2f}")
        print(f"Use it with:  T={tl} CX={mx:.2f} CY={my:.2f} Z0={mz:.2f}"
              + ("   (in T=2 runs: CX_L, CY_L, Z0_L)" if tl == 1 else ""))
        print("In cell studio: Cylinder page > Turntable centre (calibration) > "
              f"Centre X/Y/Z · {a['side']}.")


def abort(cell, why):
    print(f"\nABORT: {why}")
    cell.stop_keep.set()
    steps = [("extruders off", lambda: cell.gcode("EXTRUDE_OFF")), ("bed stop", lambda: cell.tt.stop_rotation())]
    for tl in cell.arms:
        steps.append((f"{SIDE[tl]} arm lift",
                      lambda tl=tl: cell.move(tl, *cell.pose(tl)[:2], cell.pose(tl)[2] + 10, speed=20, wait=True)))
    for name, fn in steps:
        try:
            fn()
            print(f"  {name}: done")
        except Exception as e:
            print(f"  {name}: FAILED ({e})")


def main(argv=None):
    g, flags = parse(sys.argv[1:] if argv is None else argv)
    k = plan(g)
    sim = "--sim" in flags
    if "--find-z0" in flags or "--find-centre" in flags:
        cell = Cell(g, sim)
        try:
            (find_centre if "--find-centre" in flags else find_z0)(g, k, cell)
        except KeyboardInterrupt:
            tl = next(iter(cell.arms))
            cell.move(tl, *cell.pose(tl)[:2], cell.pose(tl)[2] + 10, speed=10, wait=True)
            print("\nStopped; arm lifted 10 mm.")
        return 0
    show(g, k)
    if "--dry-run" in flags:
        print("\nDry run: nothing moved.")
        return 0
    if not sim and "--yes" not in flags:
        if input("\nRun this on the cell? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Not started.")
            return 1
    cell = Cell(g, sim)
    try:
        run(g, k, cell)
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
