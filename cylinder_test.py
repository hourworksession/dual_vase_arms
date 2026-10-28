"""Cylinder test on one or both arms: prime, circle, spiral, final circle.

Macro-style values, run from the repo root (test Klipper config loaded, hot ends hot):

    python3 cylinder_test.py T=0 R=60 E=12 L_H=0.8 P=0.8 --dry-run           # right arm
    python3 cylinder_test.py T=1 R=60 E=12 L_H=0.6 P=0.6 --dry-run           # left arm
    python3 cylinder_test.py T=2 R=60 E=12 L_H=0.6 --dry-run                 # both arms
    python3 cylinder_test.py T=0 R=60 E_MIN=8 E_MAX=16 WAVE_T=1 L_H=0.8 P=0.8 REVS=10
    python3 cylinder_test.py T=0 R=60 E_MIN=8 E_MAX=16 WAVE_T=1 SHAPE=square L_H=0.8 P=0.8 REVS=10
    python3 cylinder_test.py T=1 R=60 --find-z0                              # touch-off

Values (KEY=value, any order). In T=2 the plain keys are the RIGHT arm; *_L keys override the left.
    T       0 = right, 1 = left, 2 = both                                    (required)
    R       cylinder radius, mm                                              (required)
    L_H     bead height (layer height), mm                                   (required)
    E       filament speed, mm/s                  (required, unless E_MIN and E_MAX are given)
    P       Z rise per bed revolution, mm/rev                 (default n x L_H, n = arms used)
    W       line width, mm                                   (default 1.1 right, 0.9 left)
    FLOW_K  measured / planned plastic from a test print (1.5 / 1.1 = 1.36 for the right)
    Z0      zero height, nozzle touching the disc            (default 105.9 right, 109.7 left)
    E_L, W_L, FLOW_K_L, Z0_L   left arm in T=2. Defaults: W_L = W (same line width),
            FLOW_K_L = FLOW_K, Z0_L = 109.7, and E_L from  E_L = E x (W_L / W) x (FLOW_K / FLOW_K_L)
    MAX_VB  bed speed ceiling, rad/s; the bed runs at the speed that lays the right arm's
            W x L_H bead at E x FLOW_K, capped at this                       (default 3.1416)
    MAX_VT  arm speed ceiling, % of 100 mm/s                                 (default 100)
    FIRST   first layer above Z0, mm                                         (default 0.6)
    SPIRAL  spiral revolutions                                               (default 3)
    REVS    total counted revolutions (sets SPIRAL);  HEIGHT  wall height above the first
            layer, mm (sets SPIRAL = HEIGHT / P, wins over REVS)
    E_MIN, E_MAX, WAVE_T or WAVES_PER_REV   throttle between E_MIN and E_MAX (every arm used)
    SHAPE   throttle waveform: sine, square or triangle                      (default sine)
    PRIME_MM, PRIME_S, PRIME_Z   prime move-in mm, time s, start height above Z0 mm (15, 5, 2)
    FIL     filament diameter, mm (1.75);  MIN_TEMP  refuse below this, C (190)
    ANGLE, CX, CY (and _L)   print angle and turntable centre in each arm's frame

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
NOZZLE = {0: 1.0, 1: 0.8}         # Revo HF, 28 Oct 2026
DEFAULTS = {  # per tool; centres and start angles from cell studio's cylinder defaults
    0: {"W": 1.1, "Z0": 105.9, "CX": 508.18, "CY": -22.88, "ANGLE": 135.0},
    1: {"W": 0.9, "Z0": 109.7, "CX": 512.1, "CY": -8.4, "ANGLE": 140.0},
}
COMMON = {"FLOW_K": 1.0, "MAX_VB": math.pi, "MAX_VT": 100.0, "FIRST": 0.6, "SPIRAL": 3,
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
            if k == "SHAPE":
                v = v.strip().lower()
                if v not in SHAPES:
                    raise SystemExit(f"SHAPE must be one of {', '.join(SHAPES)}")
                vals[k] = v
            else:
                vals[k] = float(v)
        else:
            raise SystemExit(f"Not understood: {a!r} (use KEY=value, e.g. R=60)")
    if "--find-z0" in flags:                    # touch-off needs only the arm and the radius
        for k_, v_ in (("E", 1.0), ("L_H", 0.5)):
            vals.setdefault(k_, v_)
    if "E" not in vals and "E_MIN" in vals and "E_MAX" in vals:
        vals["E"] = (vals["E_MIN"] + vals["E_MAX"]) / 2
    missing = [k for k in ("T", "R", "L_H", "E") if k not in vals]
    if missing:
        raise SystemExit("Missing: " + ", ".join(missing) + "\n\n" + __doc__.split("Values")[0])
    t = int(vals["T"])
    if t not in (0, 1, 2):
        raise SystemExit("T must be 0 (right), 1 (left) or 2 (both)")
    if t == 2 and "--find-z0" in flags:
        raise SystemExit("Touch-off one arm at a time: T=0 or T=1")
    tools = [0, 1] if t == 2 else [t]
    g = {**COMMON, **vals, "T": t, "N": len(tools)}
    g.setdefault("P", g["N"] * g["L_H"])
    if "HEIGHT" in vals:
        g["SPIRAL"] = vals["HEIGHT"] / g["P"]
    elif "REVS" in vals:
        if vals["REVS"] < 2 / g["N"]:
            raise SystemExit("REVS is too small for the opening and closing circles")
        g["SPIRAL"] = vals["REVS"] - 2 / g["N"]
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
            for key in ("W", "Z0", "CX", "CY", "ANGLE"):
                if key in vals:
                    a[key] = vals[key]
            a["FLOW_K"] = g["FLOW_K"]
            a["E"] = g["E"]
        else:                                             # left arm in T=2
            for key in ("W", "Z0", "CX", "CY", "ANGLE", "FLOW_K", "E"):
                if key + "_L" in vals:
                    a[key] = vals[key + "_L"]
            a.setdefault("FLOW_K", g["FLOW_K"])
            if "W_L" not in vals:
                a["W"] = arms[first]["W"]                 # same line width as the right
            if "E_L" not in vals:                         # E_L = E (W_L/W)(K/K_L)
                a["E"] = g["E"] * (a["W"] / arms[first]["W"]) * (g["FLOW_K"] / a["FLOW_K"])
        a["T"], a["side"], a["nozzle"] = tl, SIDE[tl], NOZZLE[tl]
        arms[tl] = a
    g["arms"] = arms
    return g, flags


def plan(g):
    """Every number the run will use, worked out before anything moves."""
    fil_area = math.pi * (g["FIL"] / 2) ** 2
    lead = g["arms"][min(g["arms"])]
    flow = lead["E"] * fil_area * lead["FLOW_K"]              # mm3/s laid by the lead arm
    v_surface = flow / (lead["W"] * g["L_H"])
    omega_needed = v_surface / g["R"]
    omega = min(omega_needed, g["MAX_VB"])
    n = g["N"]
    circle = 1.0 / n
    total_revs = 2 * circle + g["SPIRAL"]
    rev_s = 2 * math.pi / omega
    warn = []
    if omega < omega_needed:
        warn.append(f"Bed capped at MAX_VB {g['MAX_VB']:.3f} rad/s; the bead needs {omega_needed:.3f}. "
                    f"Over-extruded by {omega_needed / omega:.2f}x.")
    if abs(g["P"] - n * g["L_H"]) > 1e-6:
        warn.append(f"P {g['P']:g} is not n x L_H = {n * g['L_H']:g}: beads will be "
                    f"{g['P'] / n:.2f} mm tall but the flow is sized for {g['L_H']:g} mm.")
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
        per[tl] = {"z_first": z_first, "z_top": z_first + g["SPIRAL"] * g["P"],
                   "prime_xy": pt(g["R"] + g["PRIME_MM"]), "print_xy": pt(g["R"]),
                   "prime_z": a["Z0"] + g["PRIME_Z"], "prime_speed": prime_dist / g["PRIME_S"],
                   "flow": a["E"] * fil_area * a["FLOW_K"]}
    return {"fil_area": fil_area, "flow": flow, "v_surface": v_surface, "omega": omega,
            "omega_needed": omega_needed, "rev_s": rev_s, "circle": circle, "total_revs": total_revs,
            "arm_speed": BASE_ARM_SPEED * g["MAX_VT"] / 100, "warnings": warn, "per": per,
            "filament_mm": sum(a["E"] for a in g["arms"].values()) * (total_revs * rev_s + g["PRIME_S"] / 2)}


def show(g, k):
    names = " + ".join(f"{a['side']} ({a['nozzle']:g} mm)" for a in g["arms"].values())
    print(f"\nCylinder test: {names}, n = {g['N']}")
    print(f"  Bed {k['omega']:.3f} rad/s ({math.degrees(k['omega']):.1f} deg/s), {k['rev_s']:.1f} s per rev, "
          f"{k['v_surface']:.1f} mm/s at R {g['R']:g}   [ceiling {g['MAX_VB']:.3f}]")
    for tl, a in g["arms"].items():
        q = k["per"][tl]
        kk = f" x FLOW_K {a['FLOW_K']:g}" if a["FLOW_K"] != 1 else ""
        print(f"  {a['side']:5}: E {a['E']:.2f} mm/s{kk} -> {q['flow']:.1f} mm3/s, bead {a['W']:g} x {g['L_H']:g} mm;"
              f"  Z0 {a['Z0']:.2f}, first {q['z_first']:.2f}, top {q['z_top']:.2f};"
              f"  print point X {q['print_xy'][0]:.1f} Y {q['print_xy'][1]:.1f}")
    print(f"  1. Prime: {g['PRIME_MM']:g} mm move-in over {g['PRIME_S']:g} s from {g['PRIME_Z']:g} mm above Z0; "
          f"bed {k['omega'] / 2:.3f} rad/s, extruders at half E")
    print(f"  2. Circle: {k['circle']:g} rev flat at the first layer"
          + (" (each arm half the circle)" if g["N"] == 2 else ""))
    print(f"  3/4. Spiral: {g['SPIRAL']:g} rev from rev {k['circle']:g}, rising {g['P']:g} mm/rev")
    print(f"  5. Final circle: {k['circle']:g} rev flat at the top")
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
                        f"(python3 extrude.py heat {a['side']} 225 --wait).")
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
    c = k["circle"]
    print(f"2. Circle: {c:g} rev, bed {k['omega']:.3f} rad/s, extruders at E, start angle {a0:.1f} deg")
    live = {"rev": 0.0, "z": {tl: per[tl]["z_first"] for tl in arms}}
    os.makedirs(os.path.join(ROOT, "runs"), exist_ok=True)
    log_path = os.path.join(ROOT, "runs", time.strftime("cylinder_test_%Y%m%d_%H%M%S.csv"))
    threading.Thread(target=throttle, args=(g, cell, live, log_path), daemon=True).start()
    print(f"   logging to {log_path}")
    n_sp, phase = g["SPIRAL"], "circle"
    last_z = dict(live["z"])
    while True:
        rev = (cell.tt.get_angle() - a0) / 360.0
        live["rev"] = rev
        if rev >= k["total_revs"]:
            break
        if rev < c:
            dz, ph = 0.0, "circle"
        elif rev < c + n_sp:
            dz, ph = (rev - c) * g["P"], "spiral"
        else:
            dz, ph = n_sp * g["P"], "final circle"
        if ph != phase:
            print({"spiral": f"3. Spiral: {n_sp:g} rev at P {g['P']:g} mm/rev",
                   "final circle": f"5. Final circle: {c:g} rev at the top"}[ph] + f"  (rev {rev:.2f})")
            phase = ph
        for tl in arms:
            z = per[tl]["z_first"] + dz
            if abs(z - last_z[tl]) >= 0.02:
                cell.move(tl, *per[tl]["print_xy"], guard(tl, z), speed=10, wait=False)
                last_z[tl] = z
                live["z"][tl] = z
        time.sleep(0.05)

    # 6-8
    cell.stop_keep.set()
    cell.gcode("EXTRUDE_OFF")
    for tl in arms:
        cell.gcode(f"EXTRUDE_SCALE T={tl} PERCENT=100")
    print(f"6. Extruders off at rev {(cell.tt.get_angle() - a0) / 360:.3f}")
    print("7. Move away")
    for tl in arms:
        cell.move(tl, *per[tl]["print_xy"], guard(tl, last_z[tl] + 10), speed=20, wait=True)
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
        f.write("t_s,rev,scale_pct," + ",".join(f"z_{arms[t]['side']},e_{arms[t]['side']}" for t in arms) + "\n")
        tick = 0
        while not cell.stop_keep.is_set():
            now = time.monotonic() - t0
            if amp:
                ph = (2 * math.pi * g["WAVES_PER_REV"] * live["rev"] if "WAVES_PER_REV" in g
                      else 2 * math.pi * now / g["WAVE_T"])
                scale = 1 + amp * SHAPES[g["SHAPE"]](ph)
                for tl in arms:
                    try:
                        cell.gcode(f"EXTRUDE_SCALE T={tl} PERCENT={100 * scale:.1f}")
                    except Exception as ex:
                        print(f"  throttle command failed: {ex}")
            else:
                scale = 1.0
            cols = ",".join(f"{live['z'][t]:.3f},{arms[t]['E'] * scale:.3f}" for t in arms)
            f.write(f"{now:.3f},{live['rev']:.4f},{100 * scale:.1f},{cols}\n")
            tick += 1
            delay = t0 + tick * 0.1 - time.monotonic()
            if delay > 0:
                cell.stop_keep.wait(delay)


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
    if "--find-z0" in flags:
        cell = Cell(g, sim)
        try:
            find_z0(g, k, cell)
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
