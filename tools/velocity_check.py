#!/usr/bin/env python3
"""Cold check of the arm's Cartesian velocity mode, stage by stage, before anything prints with it.

    python3 tools/velocity_check.py            # arm T=0 (right), live
    python3 tools/velocity_check.py T=1        # the left arm
    python3 tools/velocity_check.py --sim      # simulators (proves the maths only)
    python3 tools/velocity_check.py --sim LAG=0.15   # simulated arm with a 150 ms velocity lag
    python3 tools/velocity_check.py STAGES=deadman,step   KP=4   Z=60   V=8

No bed, no extruder. The nozzle works around PARK, Z mm ABOVE that arm's Z0 (default 60), so a
runaway has room and nothing is in the way. Each stage logs planned vs measured at 50 Hz to
runs/velocity_check_<time>.csv and prints a verdict; a failed stage stops the sequence.

Stages, in order:
  deadman   one command, +X at 10 mm/s with the 0.1 s dead-man, then NOTHING for 1 s. The arm
            must travel ~1 mm and stop on its own. This is the safety property everything else
            relies on: if it keeps going, velocity mode is not usable here.
  step      stream +X at 20 mm/s for 1 s, then -X back. Measures latency (time to half speed),
            steady speed vs commanded, and overshoot after the stream goes to zero.
  square_ff square of 40 mm at V mm/s with FEEDFORWARD ONLY (Kp = 0). Shows the open-loop drift
            of the controller's own integration (expected: a few tenths of a mm per side).
  square    the same square through the Player in velocity mode, Kp on. Max error to the
            planned point and the end error. This is what printing would use.
  circle    circle R 20 at V mm/s through the Player: constant |v| with continuously turning
            direction, the closest cold analogue to a corner.

Pass marks: deadman travel 0.5-3 mm and stopped within 0.5 s; step latency < 0.1 s, steady speed
within 10 %, overshoot < 1 mm; square/circle max error < 1 mm, end error < 0.5 mm."""
from __future__ import annotations

import csv
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "ui")]

from cell import program as prg  # noqa: E402
from cell.runs.cylinder import DEFAULTS, PARK, SIDE, Cell  # noqa: E402

RATE = 50.0
ALL = ["deadman", "step", "square_ff", "square", "circle"]


def parse(argv):
    g = {"T": 0, "Z": 60.0, "V": 8.0, "KP": prg.VEL_KP, "STAGES": ALL, "sim": False, "LAG": 0.0}
    for a in argv:
        if a == "--sim":
            g["sim"] = True
        elif "=" in a:
            k, v = a.split("=", 1)
            g[k] = v.split(",") if k == "STAGES" else (int(v) if k == "T" else float(v))
    return g


class Log:
    def __init__(self, path):
        self.f = open(path, "w", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(["stage", "t", "px", "py", "pz", "mx", "my", "mz", "vx", "vy", "vz"])
        self.path = path

    def row(self, stage, t, p, m, v):
        self.w.writerow([stage, f"{t:.3f}"] + [f"{c:.3f}" for c in (*p, *m, *v)])

    def close(self):
        self.f.close()


class Check:
    def __init__(self, g):
        self.g = g
        self.t = g["T"]
        self.a = dict(DEFAULTS[self.t], T=self.t, side=SIDE[self.t])
        if g["sim"] and g["LAG"]:
            from cell_studio.sim_hardware import FAULTS
            FAULTS["vel_lag_s"] = g["LAG"]
        self.cell = Cell({"arms": [self.t]}, g["sim"])
        self.arm = self.cell.arms[self.t].arm
        self.home = (PARK[0], PARK[1], self.a["Z0"] + g["Z"])
        os.makedirs(os.path.join(ROOT, "runs"), exist_ok=True)
        self.log = Log(os.path.join(ROOT, "runs", time.strftime("velocity_check_%Y%m%d_%H%M%S.csv")))
        self.results = {}

    # -- helpers
    def pose(self):
        return tuple(self.cell.pose(self.t)[:3])

    def goto_home(self):
        self.arm.set_mode(0)
        self.arm.set_state(0)
        time.sleep(0.1)
        self.cell.move(self.t, *self.home, speed=40, wait=True)
        time.sleep(0.3)

    def vel_on(self):
        self.arm.set_mode(5)
        self.arm.set_state(0)
        time.sleep(0.1)

    def send(self, v, duration=prg.VEL_DEADMAN):
        self.arm.vc_set_cartesian_velocity([v[0], v[1], v[2], 0.0, 0.0, 0.0], duration=duration)

    def vel_off(self):
        try:
            self.send((0, 0, 0), 0.01)
        finally:
            self.arm.set_mode(0)
            self.arm.set_state(0)

    def watch(self, stage, seconds, v_of_t, planned_of_t=None, send=True):
        """Stream v_of_t(t) at RATE for `seconds` (or just watch if send=False), logging measured."""
        rows = []
        t0 = time.monotonic()
        n = 0
        while True:
            t = time.monotonic() - t0
            if t >= seconds:
                break
            v = v_of_t(t)
            if send:
                self.send(v)
            m = self.pose()
            p = planned_of_t(t) if planned_of_t else m
            self.log.row(stage, t, p, m, v)
            rows.append((t, p, m, v))
            n += 1
            d = t0 + n / RATE - time.monotonic()
            if d > 0:
                time.sleep(d)
        return rows

    def verdict(self, stage, ok, text):
        self.results[stage] = ok
        print(f"   {'PASS' if ok else 'FAIL'}  {stage}: {text}")
        return ok

    # -- stages
    def deadman(self):
        print("1. dead-man: one +X 10 mm/s command, then silence for 1 s")
        self.goto_home()
        p0 = self.pose()
        self.vel_on()
        self.send((10.0, 0, 0))
        rows = self.watch("deadman", 1.0, lambda t: (0, 0, 0), send=False)
        self.vel_off()
        dist = [math.dist(m, p0) for _, _, m, _ in rows]
        travel = dist[-1]
        moving_until = max((t for t, d in zip((r[0] for r in rows), dist) if d < travel - 0.05), default=0.0)
        ok = 0.5 <= travel <= 3.0 and moving_until < 0.5
        return self.verdict("deadman", ok, f"travelled {travel:.2f} mm, still moving until {moving_until:.2f} s "
                                           f"(want 0.5-3 mm, stopped < 0.5 s)")

    def step(self):
        print("2. step: +X 20 mm/s for 1 s, zero, then -X back")
        self.goto_home()
        p0 = self.pose()
        self.vel_on()
        rows = self.watch("step", 1.6, lambda t: (20.0, 0, 0) if t < 1.0 else (0, 0, 0))
        time.sleep(0.2)
        self.watch("step_back", 1.6, lambda t: (-20.0, 0, 0) if t < 1.0 else (0, 0, 0))
        self.vel_off()
        xs = [(t, m[0] - p0[0]) for t, _, m, _ in rows]
        # speed from finite differences over 2 samples (40 ms), stamped at the window's centre
        sp = [((xs[i][1] - xs[i - 2][1]) / (xs[i][0] - xs[i - 2][0]), 0.5 * (xs[i][0] + xs[i - 2][0]))
              for i in range(2, len(xs))]
        lat = next((t for v, t in sp if v >= 10.0), None)
        steady = [v for v, t in sp if 0.4 <= t <= 0.95]
        v_ss = sum(steady) / len(steady) if steady else 0.0
        x_at_stop = next((x for t, x in xs if t >= 1.0), xs[-1][1])
        overshoot = xs[-1][1] - x_at_stop
        ok = lat is not None and lat < 0.1 and abs(v_ss - 20.0) <= 2.0 and abs(overshoot) < 1.0
        return self.verdict("step", ok, f"latency {lat if lat is None else f'{lat:.3f}'} s, steady {v_ss:.1f} mm/s "
                                        f"(cmd 20), overshoot after stop {overshoot:.2f} mm")

    def _square(self):
        side, v = 40.0, self.g["V"]
        h = self.home
        pts = [h, (h[0] + side, h[1], h[2]), (h[0] + side, h[1] + side, h[2]), (h[0], h[1] + side, h[2]), h]
        return pts, side / v

    def square_ff(self):
        print("3. square 40 mm, feed-forward only (no correction)")
        self.goto_home()
        pts, T = self._square()

        def plan(t):
            i = min(3, int(t // T))
            f = min(1.0, (t - i * T) / T)
            return tuple(pts[i][j] + (pts[i + 1][j] - pts[i][j]) * f for j in range(3))

        def vel(t):
            i = min(3, int(t // T))
            return tuple((pts[i + 1][j] - pts[i][j]) / T for j in range(3))

        self.vel_on()
        rows = self.watch("square_ff", 4 * T, vel, plan)
        self.send((0, 0, 0), 0.01)
        time.sleep(0.3)
        end = self.pose()
        self.vel_off()
        errs = [math.dist(p, m) for _, p, m, _ in rows]
        e_end = math.dist(end, pts[-1])
        return self.verdict("square_ff", e_end < 2.0, f"open-loop: max error {max(errs):.2f} mm, end drift {e_end:.2f} mm "
                                                     "(informational; < 2 mm to continue)")

    def _player(self, stage, prog):
        self.goto_home()
        kind = f"arm{self.t}"
        tr = prog.track(kind)
        pl = prg.Player(self.cell, prog, z_floor={kind: self.a["Z0"]}, on_log=lambda s: print("     ", s),
                        arm_mode="velocity", approach_z=self.home[2])
        rows = []
        pl.start()
        while pl.running():
            if pl.t0:
                t = time.monotonic() - pl.t0
                i, seg, into = tr.at(t)
                if i is not None:
                    a = tr.segs[i - 1].value if i > 0 else self.home
                    f = into / seg.dur
                    p = tuple(a[j] + (seg.value[j] - a[j]) * f for j in range(3))
                    m = self.pose()
                    self.log.row(stage, t, p, m, (0, 0, 0))
                    rows.append((t, p, m))
            time.sleep(1 / RATE)
        if pl.error:
            return self.verdict(stage, False, f"player error: {pl.error}")
        time.sleep(0.3)
        e_end = math.dist(self.pose(), tr.segs[-1].value)
        errs = [math.dist(p, m) for _, p, m in rows]
        ok = max(errs) < 1.0 and e_end < 0.5
        return self.verdict(stage, ok, f"Kp {self.g['KP']:g}: max error {max(errs):.2f} mm, end error {e_end:.2f} mm "
                                       "(want < 1 / < 0.5)")

    def square(self):
        print("4. square 40 mm through the Player, velocity mode with correction")
        pts, T = self._square()
        p = prg.Program("velocity check square")
        tr = p.track(f"arm{self.t}")
        tr.add(self.home, 0.2)                       # a Program starts AT its first point
        for q in pts[1:]:
            tr.add(q, T)
        return self._player("square", p)

    def circle(self):
        print("5. circle R 20 through the Player")
        R, v = 20.0, self.g["V"]
        h = self.home
        c = (h[0] + R, h[1], h[2])
        n = 72
        p = prg.Program("velocity check circle")
        tr = p.track(f"arm{self.t}")
        dT = (2 * math.pi * R / n) / v
        tr.add(self.home, 0.2)
        for i in range(1, n + 1):
            th = math.pi + 2 * math.pi * i / n          # start at home (angle pi from the centre)
            tr.add((c[0] + R * math.cos(th), c[1] + R * math.sin(th), h[2]), dT)
        return self._player("circle", p)

    def run(self):
        prg.VEL_KP = self.g["KP"]
        print(f"velocity check: arm T={self.t} ({SIDE[self.t]}), around {self.home}, Z0+{self.g['Z']:g}; "
              f"log {os.path.relpath(self.log.path, ROOT)}")
        if not hasattr(self.arm, "vc_set_cartesian_velocity"):
            print("   this SDK has no vc_set_cartesian_velocity: stop here")
            return 2
        try:
            for st in self.g["STAGES"]:
                if not getattr(self, st)():
                    print("   stopping: fix this before the next stage")
                    break
        except KeyboardInterrupt:
            print("   interrupted")
        finally:
            try:
                self.vel_off()
            except Exception:
                pass
            self.log.close()
        print("summary: " + ", ".join(f"{k} {'ok' if v else 'FAIL'}" for k, v in self.results.items()))
        return 0 if all(self.results.values()) else 1


if __name__ == "__main__":
    sys.exit(Check(parse(sys.argv[1:])).run())
