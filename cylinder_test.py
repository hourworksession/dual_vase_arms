"""One-arm cylinder test: prime, circle, spiral, final circle. Extruder on/off control test.

Macro-style values, run from the repo root (test Klipper config loaded, hot end already hot):

    python3 cylinder_test.py T=0 R=60 MAX_VT=100 MAX_VB=3.1416 E=12 L_H=0.8 P=0.8 --dry-run
    python3 cylinder_test.py T=0 R=60 MAX_VT=100 MAX_VB=3.1416 E=12 L_H=0.8 P=0.8 --sim
    python3 cylinder_test.py T=0 R=60 MAX_VT=100 MAX_VB=3.1416 E=12 L_H=0.8 P=0.8
    python3 cylinder_test.py T=0 R=60 E_MIN=8 E_MAX=16 WAVE_T=1 L_H=0.8 P=0.8 REVS=10

Values (KEY=value, any order):
    T       tool / arm: 0 = right, 1 = left                                  (required)
    R       cylinder radius, mm                                              (required)
    E       filament speed while printing, mm/s       (required, unless E_MIN and E_MAX are given)
    L_H     layer height used for the flow calculation, mm                   (required)
    P       pitch: Z rise per revolution in the spiral, mm/rev               (required)
    MAX_VB  bed speed ceiling, rad/s. The bed runs at the speed that lays a
            W x L_H bead at E, capped at this.                              (default 3.1416)
    MAX_VT  arm speed ceiling, % of 100 mm/s (approach and retreat moves)    (default 100)
    W       line width, mm                       (default 1.1 for T=0, 0.9 for T=1)
    Z0      zero height: nozzle touching the disc  (default 105.9 for T=0, 110.2 for T=1)
    FIRST   first layer above Z0, mm                                         (default 0.6)
    SPIRAL  spiral revolutions                                               (default 3)
    REVS    total counted revolutions (circle + spiral + final circle); sets SPIRAL = REVS - 2
    HEIGHT  wall height above the first layer, mm; sets SPIRAL = HEIGHT / P  (wins over REVS)
    E_MIN, E_MAX  sine throttle: the flow swings between these, mm/s; E becomes their middle
    WAVE_T  sine period, s                                                   (default 1)
    WAVES_PER_REV  alternative to WAVE_T: whole waves per bed revolution, locked to the bed
            angle so the waves stack up the wall
    PRIME_MM  prime travel inward to R, mm                                   (default 15)
    PRIME_S   prime time, s (bed at half speed, extruder at half E)          (default 5)
    PRIME_Z   prime start height above Z0, mm                                (default 2)
    FIL     filament diameter, mm                                            (default 1.75)
    MIN_TEMP  refuse to start below this hot-end temperature, C              (default 190)

Sequence:
  1. Prime: approach to R + PRIME_MM at Z0 + PRIME_Z; bed at half speed, extruder at E/2;
     over PRIME_S the nozzle moves in to R and down to Z0 + FIRST.
  2. Circle: bed to full speed, extruder to E; 1 revolution at Z0 + FIRST.
  3. Spiral: Z rises P per revolution for SPIRAL revolutions.
  4. Final circle: 1 revolution at the top height.
  5. Extruder off at the end angle. 6. Lift 10 mm and park. 7. Stop the bed.
Revolutions are counted from the turntable's measured angle, not a timer.
Nothing is ever commanded below Z0. Ctrl+C aborts: extruder off, bed stop, arm lifts.
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
DEFAULTS = {  # per tool; centres and start angles from cell studio's cylinder defaults
    0: {"W": 1.1, "Z0": 105.9, "CX": 508.18, "CY": -22.88, "ANGLE": 135.0},
    1: {"W": 0.9, "Z0": 110.2, "CX": 512.1, "CY": -8.4, "ANGLE": 140.0},
}
COMMON = {"MAX_VB": math.pi, "MAX_VT": 100.0, "FIRST": 0.6, "SPIRAL": 3, "PRIME_MM": 15.0,
          "PRIME_S": 5.0, "PRIME_Z": 2.0, "FIL": 1.75, "MIN_TEMP": 190.0}
REQUIRED = ("T", "R", "L_H", "P")
BASE_ARM_SPEED = 100.0            # mm/s = MAX_VT 100 %
PARK = (400.0, 174.0, 180.0)      # cell studio's park pose
SAFE_Z = 250.0
WATCHDOG_S = 5.0


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
            vals[k.strip().upper()] = float(v)
        else:
            raise SystemExit(f"Not understood: {a!r} (use KEY=value, e.g. R=60)")
    if "E" not in vals and "E_MIN" in vals and "E_MAX" in vals:
        vals["E"] = (vals["E_MIN"] + vals["E_MAX"]) / 2
    missing = [k for k in REQUIRED + ("E",) if k not in vals]
    if missing:
        raise SystemExit("Missing: " + ", ".join(missing) + "\n\n" + __doc__.split("Values")[0])
    t = int(vals["T"])
    if t not in SIDE:
        raise SystemExit("T must be 0 (right) or 1 (left)")
    p = {**COMMON, **DEFAULTS[t], **vals, "T": t}
    if "HEIGHT" in vals:
        p["SPIRAL"] = vals["HEIGHT"] / p["P"]
    elif "REVS" in vals:
        if vals["REVS"] < 2:
            raise SystemExit("REVS must be at least 2 (one circle and one final circle)")
        p["SPIRAL"] = vals["REVS"] - 2
    p["WAVE"] = "E_MIN" in vals and "E_MAX" in vals
    if p["WAVE"]:
        if not 0 < p["E_MIN"] <= p["E_MAX"]:
            raise SystemExit("Need 0 < E_MIN <= E_MAX")
        p.setdefault("WAVE_T", 1.0)
    return p, flags


def plan(p):
    """Every number the run will use, worked out before anything moves."""
    fil_area = math.pi * (p["FIL"] / 2) ** 2
    flow = p["E"] * fil_area                                  # mm3/s
    v_surface = flow / (p["W"] * p["L_H"])                    # mm/s the bead needs
    omega_needed = v_surface / p["R"]
    omega = min(omega_needed, p["MAX_VB"])
    z_first = p["Z0"] + p["FIRST"]
    z_top = z_first + p["SPIRAL"] * p["P"]
    rev_s = 2 * math.pi / omega
    total_revs = 2 + p["SPIRAL"]
    a = math.radians(p["ANGLE"])
    pt = lambda r: (p["CX"] + r * math.cos(a), p["CY"] + r * math.sin(a))
    prime_xy, print_xy = pt(p["R"] + p["PRIME_MM"]), pt(p["R"])
    prime_dist = math.hypot(p["PRIME_MM"], p["PRIME_Z"] - p["FIRST"])
    warn = []
    if omega < omega_needed:
        warn.append(f"Bed capped at MAX_VB {p['MAX_VB']:.3f} rad/s; the bead needs {omega_needed:.3f}. "
                    f"Over-extruded by {omega_needed / omega:.2f}x.")
    if p.get("WAVE") and p["E_MAX"] * fil_area > 30:
        warn.append(f"Wave peak E_MAX {p['E_MAX']:g} mm/s is {p['E_MAX'] * fil_area:.0f} mm3/s, above what a Revo HF "
                    "usually melts (about 30): the peaks may under-extrude (that is part of what this shows).")
    if flow > 30:
        warn.append(f"{flow:.0f} mm3/s is above what a Revo HF usually melts (about 30); expect under-extrusion.")
    if p["L_H"] > 0.8 * (1.0 if p["T"] == 0 else 0.8):
        warn.append(f"L_H {p['L_H']} mm is over 80 % of the nozzle.")
    if p["FIRST"] <= 0:
        raise SystemExit("FIRST must be above 0: nothing may run at or below Z0.")
    return {"fil_area": fil_area, "flow": flow, "v_surface": v_surface, "omega": omega,
            "omega_needed": omega_needed, "z_first": z_first, "z_top": z_top, "rev_s": rev_s,
            "total_revs": total_revs, "prime_xy": prime_xy, "print_xy": print_xy,
            "prime_z": p["Z0"] + p["PRIME_Z"], "prime_speed": prime_dist / p["PRIME_S"],
            "arm_speed": BASE_ARM_SPEED * p["MAX_VT"] / 100, "warnings": warn,
            "filament_mm": p["E"] * total_revs * rev_s + p["E"] / 2 * p["PRIME_S"]}


def show(p, k):
    side = SIDE[p["T"]]
    print(f"\nCylinder test, {side} arm (T={p['T']})")
    print(f"  Bead {p['W']} x {p['L_H']} mm at E={p['E']} mm/s -> {k['flow']:.1f} mm3/s, "
          f"{k['v_surface']:.1f} mm/s at the nozzle")
    print(f"  Bed {k['omega']:.3f} rad/s ({math.degrees(k['omega']):.1f} deg/s), {k['rev_s']:.1f} s per rev"
          f"   [ceiling {p['MAX_VB']:.3f}]")
    print(f"  Z0 {p['Z0']:.2f}   first layer {k['z_first']:.2f}   top {k['z_top']:.2f}")
    print(f"  1. Prime: from R {p['R'] + p['PRIME_MM']:g} at Z {k['prime_z']:.2f} -> R {p['R']:g} at "
          f"Z {k['z_first']:.2f} over {p['PRIME_S']:g} s ({k['prime_speed']:.1f} mm/s); "
          f"bed {k['omega'] / 2:.3f} rad/s, E {p['E'] / 2:g} mm/s")
    print(f"  2. Circle: 1 rev at Z {k['z_first']:.2f}, E {p['E']:g} mm/s")
    print(f"  3/4. Spiral: {p['SPIRAL']:g} rev, Z {k['z_first']:.2f} -> {k['z_top']:.2f} (P {p['P']:g} mm/rev)")
    print(f"  5. Final circle: 1 rev at Z {k['z_top']:.2f}")
    if p.get("WAVE"):
        how = (f"{p['WAVES_PER_REV']:g} waves per rev, locked to the bed" if "WAVES_PER_REV" in p
               else f"period {p['WAVE_T']:g} s ({k['rev_s'] / p['WAVE_T']:.1f} waves per rev)")
        print(f"  Throttle: sine {p['E_MIN']:g} to {p['E_MAX']:g} mm/s around {p['E']:g}, {how}; "
              f"from the circle to the end (prime is steady)")
    print(f"  6-8. Extruder off at {k['total_revs']} rev, lift 10 mm, park, stop bed")
    print(f"  Time about {k['total_revs'] * k['rev_s'] + p['PRIME_S']:.0f} s, filament about "
          f"{k['filament_mm']:.0f} mm")
    print(f"  Print point X {k['print_xy'][0]:.1f} Y {k['print_xy'][1]:.1f} (centre {p['CX']}, {p['CY']}, "
          f"angle {p['ANGLE']:g} deg)   arm moves {k['arm_speed']:.0f} mm/s")
    for w in k["warnings"]:
        print("  CHECK: " + w)


# ----------------------------------------------------------------------
class Cell:
    """The three devices. Real ones from config/settings.yaml, or cell studio's simulators."""

    def __init__(self, p, sim):
        from cell_studio import tool as toolmod
        self.side, self.sim = SIDE[p["T"]], sim
        self.orient = toolmod.orientation(toolmod.load(), self.side)
        if sim:
            from cell_studio.sim_hardware import SimArm, SimExtruder, SimTurntable
            self.arm, self.tt, self.ext = SimArm("sim", self.side), SimTurntable(), SimExtruder()
        else:
            from config_loader import load_config
            from arm_controller import ArmController
            from extruder_controller_manual import ManualExtruderController
            from turntable_controller import TurntableController
            cfg = load_config()
            self.arm = ArmController(cfg["arms"][self.side]["ip"], self.side)
            self.tt = TurntableController(host=cfg["turntable"]["controller_ip"], axis=cfg["turntable"]["axis"])
            self.ext = ManualExtruderController(cfg["moonraker"]["host"], cfg["moonraker"]["port"])
        self.stop_keep = threading.Event()      # ends the extruder keepalive and throttle (also on abort)
        self.lock = threading.Lock()
        self.arm.connect()
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

    def pose(self):
        return self.arm.get_pose()

    def move(self, x, y, z, speed, wait):
        r, pt, yw = self.orient
        self.arm.arm.set_position(x, y, z, r, pt, yw, speed=speed, wait=wait)


def run(p, k, cell):
    t, z0 = p["T"], p["Z0"]
    stop_keep = cell.stop_keep
    rate = {"E": p["E"] / 2}

    def guard(z):
        if z < z0 - 1e-6:
            raise Abort(f"Refused: Z {z:.2f} is below the zero height {z0:.2f}")
        return z

    def keepalive():
        while not stop_keep.wait(WATCHDOG_S / 2.5):
            cell.gcode(f"EXTRUDE_ON T={t} RATE={rate['E']:.3f} TIMEOUT={WATCHDOG_S}")

    temp = cell.hot_end_temp(t)
    if temp < p["MIN_TEMP"]:
        raise Abort(f"{cell.side} hot end is at {temp:.0f} C, below MIN_TEMP {p['MIN_TEMP']:.0f}. Heat it first "
                    f"(python3 extrude.py heat {cell.side} 225 --wait).")
    cell.gcode(f"EXTRUDE_OFF T={t}")
    v = k["arm_speed"]
    px, py = k["prime_xy"]
    print("Approach: park, safe height, above the prime point, down to the prime start")
    cell.move(*PARK, speed=v, wait=True)
    cell.move(PARK[0], PARK[1], SAFE_Z, speed=v, wait=True)
    cell.move(px, py, guard(k["prime_z"] + 20), speed=v, wait=True)
    cell.move(px, py, guard(k["prime_z"]), speed=min(v, 20), wait=True)

    # 1. Prime
    print(f"1. Prime: bed {k['omega'] / 2:.3f} rad/s, E {rate['E']:g} mm/s, moving in over {p['PRIME_S']:g} s")
    cell.tt.rotate_velocity(math.degrees(k["omega"] / 2))
    cell.gcode(f"EXTRUDE_ON T={t} RATE={rate['E']:.3f} TIMEOUT={WATCHDOG_S}")
    threading.Thread(target=keepalive, daemon=True).start()
    qx, qy = k["print_xy"]
    cell.move(qx, qy, guard(k["z_first"]), speed=k["prime_speed"], wait=False)
    t_start = time.monotonic()
    t_end = t_start + p["PRIME_S"] + 3
    while time.monotonic() < t_end:
        x, y, z = cell.pose()[:3]
        arrived = math.dist((x, y, z), (qx, qy, k["z_first"])) < 0.3
        if arrived and time.monotonic() - t_start >= p["PRIME_S"]:
            break
        time.sleep(0.05)
    else:
        print("  (did not report arrival within prime time + 3 s; carrying on)")

    # 2-5. Circle, spiral, final circle, counted on the turntable angle
    cell.tt.rotate_velocity(math.degrees(k["omega"]))
    rate["E"] = p["E"]
    cell.gcode(f"EXTRUDE_ON T={t} RATE={rate['E']:.3f} TIMEOUT={WATCHDOG_S}")
    a0 = cell.tt.get_angle()
    print(f"2. Circle: bed {k['omega']:.3f} rad/s, E {p['E']:g} mm/s, start angle {a0:.1f} deg")
    live = {"rev": 0.0, "z": k["z_first"]}
    os.makedirs(os.path.join(ROOT, "runs"), exist_ok=True)
    log_path = os.path.join(ROOT, "runs", time.strftime("cylinder_test_%Y%m%d_%H%M%S.csv"))
    threading.Thread(target=throttle, args=(p, cell, live, log_path), daemon=True).start()
    print(f"   logging to {log_path}")
    n, last_z, phase = p["SPIRAL"], k["z_first"], "circle"
    while True:
        rev = (cell.tt.get_angle() - a0) / 360.0
        live["rev"] = rev
        if rev >= k["total_revs"]:
            break
        if rev < 1:
            z, ph = k["z_first"], "circle"
        elif rev < 1 + n:
            z, ph = k["z_first"] + (rev - 1) * p["P"], "spiral"
        else:
            z, ph = k["z_top"], "final circle"
        if ph != phase:
            print({"spiral": f"3. Spiral: {n} rev at P {p['P']:g} mm/rev",
                   "final circle": f"5. Final circle at Z {k['z_top']:.2f}"}[ph] + f"  (rev {rev:.2f})")
            phase = ph
        if abs(z - last_z) >= 0.02:
            cell.move(qx, qy, guard(z), speed=10, wait=False)
            last_z = z
            live["z"] = z
        time.sleep(0.05)

    # 6-8
    stop_keep.set()
    cell.gcode(f"EXTRUDE_OFF T={t}")
    cell.gcode(f"EXTRUDE_SCALE T={t} PERCENT=100")
    print(f"6. Extruder off at rev {(cell.tt.get_angle() - a0) / 360:.3f}")
    print("7. Move away")
    cell.move(qx, qy, guard(k["z_top"] + 10), speed=20, wait=True)
    cell.move(*PARK, speed=v, wait=True)
    cell.tt.stop_rotation()
    print("8. Bed stopped. Done.")


def throttle(p, cell, live, log_path):
    """10 Hz: set the flow scale for the sine (if any) and log time, rev, Z and commanded flow."""
    t, mid = p["T"], p["E"]
    amp = (p["E_MAX"] - p["E_MIN"]) / 2 if p.get("WAVE") else 0.0
    t0 = time.monotonic()
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("t_s,rev,z_mm,e_cmd_mm_s,scale_pct\n")
        tick = 0
        while not cell.stop_keep.is_set():
            now = time.monotonic() - t0
            if amp:
                if "WAVES_PER_REV" in p:
                    phase = 2 * math.pi * p["WAVES_PER_REV"] * live["rev"]
                else:
                    phase = 2 * math.pi * now / p["WAVE_T"]
                e = mid + amp * math.sin(phase)
            else:
                e = mid
            pct = 100.0 * e / mid
            if amp:
                try:
                    cell.gcode(f"EXTRUDE_SCALE T={t} PERCENT={pct:.1f}")
                except Exception as ex:
                    print(f"  throttle command failed: {ex}")
            f.write(f"{now:.3f},{live['rev']:.4f},{live['z']:.3f},{e:.3f},{pct:.1f}\n")
            tick += 1
            delay = t0 + tick * 0.1 - time.monotonic()
            if delay > 0:
                cell.stop_keep.wait(delay)


def abort(cell, p, why):
    print(f"\nABORT: {why}")
    cell.stop_keep.set()
    for name, fn in (("extruder off", lambda: cell.gcode("EXTRUDE_OFF")),
                     ("bed stop", lambda: cell.tt.stop_rotation()),
                     ("arm lift", lambda: cell.move(*cell.pose()[:2], cell.pose()[2] + 10, speed=20, wait=True))):
        try:
            fn()
            print(f"  {name}: done")
        except Exception as e:
            print(f"  {name}: FAILED ({e})")


def main(argv=None):
    p, flags = parse(sys.argv[1:] if argv is None else argv)
    k = plan(p)
    show(p, k)
    if "--dry-run" in flags:
        print("\nDry run: nothing moved.")
        return 0
    sim = "--sim" in flags
    if not sim and "--yes" not in flags:
        if input("\nRun this on the cell? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Not started.")
            return 1
    cell = Cell(p, sim)
    try:
        run(p, k, cell)
        return 0
    except KeyboardInterrupt:
        abort(cell, p, "Ctrl+C")
    except Abort as e:
        abort(cell, p, str(e))
    except Exception as e:
        abort(cell, p, f"error: {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
