"""Rounded square with NOTHING stopping: the bed is the master, the arm only moves in and out.

The bed's angle is always the polar angle of the point being printed, so the nozzle sits on a fixed
line out from the turntable axis (direction ANGLE in the arm's frame) and slides radially between
SIDE/2 (side centres) and the fillet apex. The bed speed follows the outline,
    omega(theta) = V / sqrt(r^2 + r'^2)        (slow where the outline is far out and oblique,
                                                fast at the side centres; never zero)
so the bead is laid at V mm/s everywhere, and the extruder rate follows the MEASURED bed speed:
    rate = K x omega_measured x sqrt(r^2 + r'^2),   K = W x L_H / (filament area x FLOW_K).
One loop of the outline = one bed revolution, so Z rises exactly as cylinder_test.py: a flat
first loop, the spiral at P per loop (with the ramp-in), a flat closing loop.

For an 80 mm square with R 10 at V = 8 mm/s the bed runs between about 0.13 and 0.2 rad/s and the
arm moves radially at under 5 mm/s. BED_DIR does not matter here: the outline is symmetric.

    python3 square_polar_test.py T=0 SIDE=80 RF=10 L_H=0.6 FLOW_K=1.3 V=8 LOOPS=10 --dry-run
    python3 square_polar_test.py T=2 SIDE=80 RF=10 L_H=0.6 V=8 LOOPS=20 --dry-run
    python3 square_polar_test.py T=0 SIDE=80 RF=10 LOOPS=2 --cold

Values (KEY=value). In T=2 the plain keys are the RIGHT arm; *_L keys override the left.
    T, SIDE (required);  RF (10);  L_H (0.5);  W (1.0);  FLOW_K;  Z0, Z0_L;  W_L, FLOW_K_L
    V       bead speed along the outline, mm/s                                  (default 8)
    P       Z rise per loop (default n x L_H);  FIRST;  SPIRAL / LOOPS / HEIGHT;  RAMP_STEPS, RAMP_EVERY
    MAX_VB  bed speed ceiling, rad/s (3.1416);  MAX_VT arm ceiling, % of 100 mm/s (100)
    PRIME_MM, PRIME_S, PRIME_Z   prime: nozzle starts PRIME_MM outside the outline, bed at half speed,
            extruder at half rate, moves in over PRIME_S s from PRIME_Z above Z0 (15, 5, 2)
    ANGLE, CX, CY (and _L);  DR, DR_L (outline offset, left default 1.0);  FIL;  MIN_TEMP
    LEAD_S  how far ahead of the measured bed angle the arm's radial target is sent, s (0.2)
    BLEND   blend radius for the streamed arm moves, mm (2)
    BED_ACC bed ramp to ask the Aerotech for, deg/s2 (0 = leave as configured). The bed speed
            changes continuously here, so a quick ramp keeps it on the profile.
    RATE_HZ extruder rate updates per second (10);  RATE_MIN (0.1)
Flags: --dry-run, --sim, --yes, --cold.
Nothing is commanded below an arm's Z0. Ctrl+C aborts: extruders off, bed stop, arms lift.
Logs to runs/square_polar_*.csv (bed angle, measured bed speed, r command/actual, rate).
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
from cell.runs.square_indexing import set_bed_ramp  # noqa: E402

POLAR_COMMON = {**COMMON, "RF": 10.0, "V": 8.0, "LEAD_S": 0.2, "BLEND": 2.0, "BED_ACC": 0.0, "RATE_HZ": 10.0,
                "RATE_MIN": 0.1}
TICK = 0.05
RATE_TOL = 0.05


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
    known = {"T", "SIDE", "RF", "L_H", "W", "FLOW_K", "Z0", "W_L", "FLOW_K_L", "Z0_L", "V", "P", "FIRST", "SPIRAL",
             "LOOPS", "HEIGHT", "RAMP_STEPS", "RAMP_EVERY", "MAX_VB", "MAX_VT", "PRIME_MM", "PRIME_S", "PRIME_Z",
             "ANGLE", "CX", "CY", "ANGLE_L", "CX_L", "CY_L", "DR", "DR_L", "FIL", "MIN_TEMP", "LEAD_S", "BLEND",
             "BED_ACC", "RATE_HZ", "RATE_MIN"}
    unknown = sorted(set(vals) - known)
    if unknown:
        raise SystemExit("Unknown setting(s): " + ", ".join(unknown))
    missing = [k for k in ("T", "SIDE") if k not in vals]
    if missing:
        raise SystemExit("Missing: " + ", ".join(missing) + "\n\n" + __doc__.split("Values")[0])
    vals.setdefault("L_H", 0.5)
    t = int(vals["T"])
    if t not in (0, 1, 2):
        raise SystemExit("T must be 0 (right), 1 (left) or 2 (both)")
    tools = [0, 1] if t == 2 else [t]
    g = {**POLAR_COMMON, **vals, "T": t, "N": len(tools)}
    if g["RF"] <= 0 or g["RF"] >= g["SIDE"] / 2:
        raise SystemExit("RF must be above 0 and below SIDE / 2 (SIDE / 2 is a circle: use cylinder_test.py)")
    if g["V"] <= 0:
        raise SystemExit("V must be above 0")
    g["NL"] = g["N"]
    g.setdefault("FIRST", g["L_H"])
    g.setdefault("P", g["NL"] * g["L_H"])
    g.setdefault("RAMP_STEPS", 5)
    g.setdefault("RAMP_EVERY", 0.125)
    g["RAMP_STEPS"] = int(g["RAMP_STEPS"])
    g["C0"] = g["C1"] = 1.0 / g["NL"]
    if "HEIGHT" in vals:
        g["SPIRAL"] = (vals["HEIGHT"] + ramp_loss(g)) / g["P"]
    elif "LOOPS" in vals:
        if vals["LOOPS"] < g["C0"] + g["C1"]:
            raise SystemExit("LOOPS is too small for the opening and closing flat loops")
        g["SPIRAL"] = vals["LOOPS"] - g["C0"] - g["C1"]
    fil_area = math.pi * (g["FIL"] / 2) ** 2
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
        else:
            for key in ("W", "Z0", "CX", "CY", "ANGLE", "FLOW_K", "DR"):
                if key + "_L" in vals:
                    a[key] = vals[key + "_L"]
            for key in ("W", "FLOW_K"):
                if key + "_L" not in vals:
                    a[key] = arms[first][key]
        dr = a.get("DR", 0.0)
        a["S"], a["R"] = g["SIDE"] / 2 + dr, g["RF"] + dr
        a["D"] = a["S"] - a["R"]
        a["K"] = a["W"] * g["L_H"] / (fil_area * a["FLOW_K"])
        a["T"], a["side"], a["nozzle"] = tl, SIDE[tl], NOZZLE[tl]
        arms[tl] = a
    g["arms"] = arms
    return g, flags


# ----------------------------------------------------------------------
def radius(a, theta_deg):
    """Outline radius about the axis at polar angle theta (deg from a side centre). 4-fold and
    mirror symmetric, so theta is folded into 0..45."""
    u = theta_deg % 90.0
    if u > 45.0:
        u = 90.0 - u
    u = math.radians(u)
    S, R, d = a["S"], a["R"], a["D"]
    if u <= math.atan2(d, S):                                   # on the straight: x = S, y = S tan u
        return S / math.cos(u)
    f = d * math.sqrt(2)                                        # fillet centre at (d, d), angle 45 deg
    du = math.pi / 4 - u
    return f * math.cos(du) + math.sqrt(max(R * R - (f * math.sin(du)) ** 2, 0.0))


def ds_dtheta(a, theta_deg):
    """Outline length per radian of bed angle: sqrt(r^2 + r'^2), r' numerical."""
    h = 0.05
    r = radius(a, theta_deg)
    rp = (radius(a, theta_deg + h) - radius(a, theta_deg - h)) / math.radians(2 * h)
    return math.hypot(r, rp)


def height(g, u):
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
    fil_area = math.pi * (g["FIL"] / 2) ** 2
    lead = g["arms"][min(g["arms"])]
    arm_speed = BASE_ARM_SPEED * g["MAX_VT"] / 100
    warn = []
    thetas = [i * 0.5 for i in range(0, 91)]
    omegas = [g["V"] / ds_dtheta(lead, th) for th in thetas]
    w_max, w_min = max(omegas), min(omegas)
    if w_max > g["MAX_VB"]:
        raise SystemExit(f"The side centres need the bed at {w_max:.3f} rad/s, above MAX_VB {g['MAX_VB']:.3f}. Lower V.")
    rdot = max(abs(g["V"] * (radius(lead, th + 0.05) - radius(lead, th - 0.05)) / (ds_dtheta(lead, th) * math.radians(0.1)))
               for th in thetas)
    if rdot > arm_speed:
        raise SystemExit(f"The arm would need {rdot:.1f} mm/s radially, above its {arm_speed:.0f} mm/s ceiling.")
    loop_len = sum(ds_dtheta(lead, th) * math.radians(0.5) for th in thetas[:-1]) * 8   # 0..45 is an eighth
    loop_s = loop_len / g["V"]
    if abs(g["P"] - g["NL"] * g["L_H"]) > 1e-6:
        warn.append(f"P {g['P']:g} is not n x L_H = {g['NL'] * g['L_H']:g}: beads will be {g['P'] / g['NL']:.2f} mm tall "
                    f"but the flow is sized for {g['L_H']:g} mm.")
    if g["FIRST"] <= 0:
        raise SystemExit("FIRST must be above 0.")
    total_loops = g["C0"] + g["SPIRAL"] + g["C1"]
    per = {}
    for tl, a in g["arms"].items():
        rate = a["K"] * g["V"]
        if rate * fil_area * a["FLOW_K"] > 30:
            warn.append(f"{a['side']}: {rate * fil_area * a['FLOW_K']:.0f} mm3/s is above what a Revo HF usually melts.")
        if rate < 0.5:
            warn.append(f"{a['side']}: filament rate {rate:.2f} mm/s is very low; raise V or the bead.")
        if g["L_H"] > 0.75 * a["nozzle"] + 1e-9:
            warn.append(f"{a['side']}: L_H {g['L_H']:g} is over 75 % of its {a['nozzle']:g} mm nozzle.")
        if not 0.95 * a["nozzle"] <= a["W"] <= 1.6 * a["nozzle"]:
            warn.append(f"{a['side']}: line width {a['W']:g} is outside about 1 to 1.5 x its {a['nozzle']:g} mm nozzle.")
        per[tl] = {"z_first": a["Z0"] + g["FIRST"], "z_top": a["Z0"] + g["FIRST"] + height(g, total_loops),
                   "rate": rate, "r_min": radius(a, 0.0), "r_max": radius(a, 45.0),
                   "prime_z": a["Z0"] + g["PRIME_Z"]}
    return {"w_max": w_max, "w_min": w_min, "rdot": rdot, "loop_len": loop_len, "loop_s": loop_s,
            "total_loops": total_loops, "arm_speed": arm_speed, "warnings": warn, "per": per,
            "filament_mm": sum(q["rate"] for q in per.values()) * (total_loops * loop_s + g["PRIME_S"] / 2)}


def show(g, k):
    names = " + ".join(f"{a['side']} ({a['nozzle']:g} mm)" for a in g["arms"].values())
    print(f"\nRounded square, polar (bed never stops): {names}, {'layered' if g['N'] == 2 else 'one arm'}")
    print(f"  Outline {g['SIDE']:g} mm square, fillet R {g['RF']:g}, loop {k['loop_len']:.1f} mm, {k['loop_s']:.1f} s per loop")
    print(f"  Bead {g['V']:g} mm/s everywhere.  Bed {k['w_min']:.3f} to {k['w_max']:.3f} rad/s "
          f"({math.degrees(k['w_min']):.1f} to {math.degrees(k['w_max']):.1f} deg/s), fastest at the side centres.  "
          f"Arm radial only, up to {k['rdot']:.1f} mm/s")
    for tl, a in g["arms"].items():
        q = k["per"][tl]
        kk = f" x FLOW_K {a['FLOW_K']:g}" if a["FLOW_K"] != 1 else ""
        print(f"  {a['side']:5}: bead {a['W']:g} x {g['L_H']:g} mm{kk} -> {q['rate']:.2f} mm/s filament (follows the measured "
              f"bed speed);  r {q['r_min']:.1f} to {q['r_max']:.1f} mm along ANGLE {a['ANGLE']:g};  "
              f"Z0 {a['Z0']:.2f}, first {q['z_first']:.2f}, top {q['z_top']:.2f}")
    print(f"  1. Prime: from {g['PRIME_MM']:g} mm outside over {g['PRIME_S']:g} s, bed and extruders at half")
    print(f"  2. Flat {g['C0']:g} loop;  3. Spiral {g['SPIRAL']:g} loops at P {g['P']:g} mm/loop"
          + (f" easing in over {g['RAMP_STEPS'] * g['RAMP_EVERY']:g} loop" if g["RAMP_STEPS"] > 1 else "")
          + f";  4. Final flat {g['C1']:g} loop;  5-7. extruders off, lift, park, bed stop")
    print(f"  Time about {k['total_loops'] * k['loop_s'] + g['PRIME_S']:.0f} s, filament about {k['filament_mm']:.0f} mm")
    for w in k["warnings"]:
        print("  CHECK: " + w)


# ----------------------------------------------------------------------
def run(g, k, cell, cold=False):
    arms, per = g["arms"], k["per"]
    v_arm = k["arm_speed"]
    lead = arms[min(arms)]
    rate = {tl: 0.0 for tl in arms}
    sent = {tl: None for tl in arms}
    sent_at = {tl: 0.0 for tl in arms}

    def gcode(s):
        if not cold:
            return cell.gcode(s)

    def extruder_thread():
        period = 1.0 / g["RATE_HZ"]
        while not cell.stop_keep.wait(period / 2):
            now = time.monotonic()
            for tl in arms:
                r = rate[tl]
                try:
                    if r < g["RATE_MIN"]:
                        if sent[tl] is not None:
                            gcode(f"EXTRUDE_OFF T={tl}")
                            sent[tl], sent_at[tl] = None, now
                        continue
                    due = (sent[tl] is None or abs(r - sent[tl]) > RATE_TOL * sent[tl]) and now - sent_at[tl] >= period
                    if due or (sent[tl] is not None and now - sent_at[tl] >= WATCHDOG_S / 2.5):
                        gcode(f"EXTRUDE_ON T={tl} RATE={r:.3f} TIMEOUT={WATCHDOG_S}")
                        sent[tl], sent_at[tl] = r, now
                except Exception as ex:
                    print(f"  extruder command failed: {ex}")

    def guard(tl, z):
        if z < arms[tl]["Z0"] - 1e-6:
            raise Abort(f"Refused: {arms[tl]['side']} Z {z:.2f} is below its zero height {arms[tl]['Z0']:.2f}")
        return z

    def pt(a, r):
        ang = math.radians(a["ANGLE"])
        return (a["CX"] + r * math.cos(ang), a["CY"] + r * math.sin(ang))

    def move(tl, r, z, speed, wait, blend=None):
        ro, pi_, yw = cell.orient[tl]
        x, y = pt(arms[tl], r)
        kw = {"speed": speed, "wait": wait}
        if blend is not None:
            kw["radius"] = blend
        cell.arms[tl].arm.set_position(x, y, guard(tl, z), ro, pi_, yw, **kw)

    if not cold:
        for tl, a in arms.items():
            temp = cell.hot_end_temp(tl)
            if temp < g["MIN_TEMP"]:
                raise Abort(f"{a['side']} hot end is at {temp:.0f} C, below MIN_TEMP {g['MIN_TEMP']:.0f}.")
    gcode("EXTRUDE_OFF")
    for tl in (0, 1):
        gcode(f"EXTRUDE_SCALE T={tl} PERCENT=100")
    cell.tt.stop_rotation()
    if g["BED_ACC"] > 0:
        set_bed_ramp(cell.tt, g["BED_ACC"])
    print("Approach: each arm to park, safe height, above its prime point, down to the prime start")
    for tl, a in arms.items():
        x, y = pt(a, per[tl]["r_min"] + g["PRIME_MM"])
        cell.move(tl, *PARK, speed=v_arm, wait=True)
        cell.move(tl, PARK[0], PARK[1], SAFE_Z, speed=v_arm, wait=True)
        cell.move(tl, x, y, guard(tl, per[tl]["prime_z"] + 20), speed=v_arm, wait=True)
        cell.move(tl, x, y, guard(tl, per[tl]["prime_z"]), speed=min(v_arm, 20), wait=True)

    # 1. Prime: bed at half the side-centre speed, extruders at half rate, nozzle runs in radially
    a0 = cell.tt.get_angle()                                    # theta = 0 is a side centre, facing the arm
    w0 = g["V"] / ds_dtheta(lead, 0.0)
    print(f"1. Prime: bed at {math.degrees(w0) / 2:.1f} deg/s, extruders at half rate, moving in over {g['PRIME_S']:g} s")
    cell.tt.rotate_velocity(math.degrees(w0) / 2)
    for tl in arms:
        rate[tl] = per[tl]["rate"] / 2
    threading.Thread(target=extruder_thread, daemon=True).start()
    t_start = time.monotonic()
    while time.monotonic() - t_start < g["PRIME_S"]:
        f = (time.monotonic() - t_start) / g["PRIME_S"]
        th = cell.tt.get_angle() - a0
        for tl, a in arms.items():
            r = radius(a, th) + g["PRIME_MM"] * (1 - f)
            z = per[tl]["prime_z"] + (per[tl]["z_first"] - per[tl]["prime_z"]) * f
            move(tl, r, z, max(10.0, 2 * per[tl]["rate"] / a["K"]), wait=False, blend=g["BLEND"])
        time.sleep(TICK)

    # 2-4. The outline: bed speed on the profile, arm radial, extruder on the measured bed speed
    live = {"rev": 0.0, "seg": "", "bed": 0.0, "w": 0.0, "z": {tl: per[tl]["z_first"] for tl in arms},
            "xy": {tl: pt(arms[tl], per[tl]["r_min"]) for tl in arms}, "dev": {tl: [] for tl in arms}, "rate": rate}
    os.makedirs(os.path.join(ROOT, "runs"), exist_ok=True)
    log_path = os.path.join(ROOT, "runs", time.strftime("square_polar_%Y%m%d_%H%M%S.csv"))
    threading.Thread(target=logger, args=(g, cell, live, log_path, cold), daemon=True).start()
    print(f"2. Flat loop: start angle {a0:.1f} deg;  logging to {log_path}")
    th_end = 360.0 * k["total_loops"]
    prev_th, prev_t = cell.tt.get_angle() - a0, time.monotonic()
    w_filt = w0
    phase = "flat"
    last = {tl: None for tl in arms}
    while True:
        now = time.monotonic()
        th = cell.tt.get_angle() - a0                           # deg since the start, a loop = 360
        if th >= th_end:
            break
        u = th / 360.0
        live["rev"], live["bed"] = u, th
        ph = phase_name(g, u)
        if ph != phase:
            print({"spiral": f"3. Spiral at P {g['P']:g} mm/loop", "final flat": "4. Final flat loop"}[ph] + f"  (loop {u:.2f})")
            phase = ph
        w_raw = math.radians((th - prev_th) / max(now - prev_t, 1e-3))
        w_filt = 0.5 * w_filt + 0.5 * max(0.0, w_raw)
        live["w"] = w_filt
        # bed: the profile speed a little ahead of where it is, so it is at speed when it gets there
        th_cmd = th + math.degrees(w_filt) * g["LEAD_S"]
        w_cmd = min(g["V"] / ds_dtheta(lead, th_cmd), g["MAX_VB"])
        cell.tt.rotate_velocity(math.degrees(w_cmd))
        for tl, a in arms.items():
            rate[tl] = a["K"] * w_filt * ds_dtheta(a, th)       # extruder follows the measured bed speed
            th_arm = th + math.degrees(w_filt) * g["LEAD_S"]
            r, z = radius(a, th_arm), per[tl]["z_first"] + height(g, th_arm / 360.0)
            if last[tl] is None or abs(r - last[tl][0]) >= 0.05 or abs(z - last[tl][1]) >= 0.02:
                move(tl, r, z, max(10.0, 3 * k["rdot"]), wait=False, blend=g["BLEND"])
                last[tl] = (r, z)
            live["xy"][tl], live["z"][tl] = pt(a, radius(a, th)), per[tl]["z_first"] + height(g, u)
        live["seg"] = f"theta {th % 90:.0f}"
        prev_th, prev_t = th, now
        time.sleep(TICK)

    # 5-7
    cell.stop_keep.set()
    gcode("EXTRUDE_OFF")
    cell.tt.stop_rotation()
    print(f"5. Extruders off after {th / 360.0:.3f} loops")
    report_deviation(g, live)
    print("6. Move away")
    for tl in arms:
        cell.move(tl, *live["xy"][tl], guard(tl, live["z"][tl] + 10), speed=20, wait=True)
    for tl in arms:
        cell.move(tl, *PARK, speed=v_arm, wait=True)
    # square up: a face centre faces the arm at every 90 deg from the start angle, so turn the bed to the
    # nearest one (the ironing arm sits 180 deg round, which is also a face). Then the next step, e.g. the
    # ironing notebook, starts square to a side without a jog.
    square = a0 + 90.0 * round((cell.tt.get_angle() - a0) / 90.0)
    square_up(cell.tt, square)
    print(f"7. Bed stopped at {cell.tt.get_angle():.2f} deg (a face centre faces the arm). Done.")


def square_up(tt, angle_deg, speed_dps=15.0):
    """Turn the bed to angle_deg (absolute, continuous angle) and wait. Falls back to a timed
    free-run on a turntable without rotate_absolute (the simulator)."""
    if hasattr(tt, "rotate_absolute"):
        tt.rotate_absolute(angle_deg, speed_dps, wait=True)
        return
    delta = angle_deg - tt.get_angle()
    if abs(delta) > 0.05:
        tt.rotate_velocity(speed_dps if delta > 0 else -speed_dps)
        time.sleep(abs(delta) / speed_dps)
        tt.stop_rotation()


def logger(g, cell, live, log_path, cold):
    arms = g["arms"]
    t0 = time.monotonic()
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("t_s,loop,bed_deg,bed_rad_s," + ",".join(
            f"z_cmd_{s},x_act_{s},y_act_{s},z_act_{s},dz_{s},dxy_{s},rate_{s}" for s in (arms[t]["side"] for t in arms))
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
                    cols.append(f"{live['z'][t]:.3f},{p[0]:.3f},{p[1]:.3f},{p[2]:.3f},{dz:.3f},{dxy:.3f},{live['rate'][t]:.3f}")
                else:
                    cols.append(f"{live['z'][t]:.3f},,,,,,{live['rate'][t]:.3f}")
            if tick % 10 == 0 and not cold:
                for t in arms:
                    try:
                        temps[t] = f"{cell.hot_end_temp(t):.1f}"
                    except Exception:
                        temps[t] = ""
            f.write(f"{now:.3f},{live['rev']:.4f},{live['bed']:.2f},{live['w']:.4f}," + ",".join(cols) + ","
                    + ",".join(temps[t] for t in arms) + "\n")
            tick += 1
            delay = t0 + tick * 0.1 - time.monotonic()
            if delay > 0:
                cell.stop_keep.wait(delay)


def main(argv=None):
    g, flags = parse(sys.argv[1:] if argv is None else argv)
    k = plan(g)
    sim, cold = "--sim" in flags, "--cold" in flags
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
