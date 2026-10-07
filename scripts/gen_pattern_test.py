#!/usr/bin/env python3
"""Generate the construction-pattern test job: side by side -> bricklaying
-> interweaved spiral on one cylinder wall.

The cell consumes coordinate streams rather than classic gcode: this writes
a D dataset (t, x0, y0, z0, x1, y1, z1, rotary_speed_rev_s, extruder_0_mm_s,
extruder_1_mm_s) that the web Job Runner and cell_server execute directly.
Azimuths are fixed (nozzle 0 at 0 deg, nozzle 1 at 180 deg); the table sweeps.

  lead-in   : flat half circle, both extruders, pattern 1 radii
  phase 1   : SIDE BY SIDE   n0 at r-b/2, n1 at r+b/2, z0 = z1, pitch/rev
  phase 2   : BRICKLAYING    n0 at r-b/4, n1 at r+b/4, z1 = z0 + p/2, pitch/rev
  phase 3   : INTERWEAVE     both at r, z0 = z1, 2 x pitch/rev (dual start)
  lead-out  : flat half circle at h, both extruders
Half-revolution transitions run extruders-off while radii morph and the
half-layer stagger opens (T1) and closes (T2).
"""
import argparse, math, os

TAU = 2 * math.pi

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--r", type=float, default=30.0, help="wall centreline radius mm")
    ap.add_argument("--h", type=float, default=12.0, help="total height mm")
    ap.add_argument("--pitch", type=float, default=0.2)
    ap.add_argument("--bead", type=float, default=1.2)
    ap.add_argument("--surface", type=float, default=30.0, help="surface speed mm/s")
    ap.add_argument("--freq", type=float, default=20.0, help="rows per second")
    ap.add_argument("--fil", type=float, default=1.75, help="filament diameter mm")
    ap.add_argument("--out", default="jobs/D_pattern_test.csv")
    a = ap.parse_args()

    r, h, p, b = a.r, a.h, a.pitch, a.bead
    om = a.surface / (TAU * r)                       # rev/s, held constant
    K = (b * p) / (math.pi * a.fil * a.fil / 4)      # extruder mm/s per mm/s surface
    z1, z2 = h / 3.0, 2 * h / 3.0

    # (name, revolutions, fn(frac 0..1) -> (r0, r1, z0, z1, extrude))
    segs = [
        ("LEADIN",     0.5,            lambda u: (r-b/2, r+b/2, p, p, True)),
        ("SIDEBYSIDE", (z1-p)/p,       lambda u: (r-b/2, r+b/2, p+u*(z1-p), p+u*(z1-p), True)),
        ("T1",         0.5,            lambda u: (r-b/2+u*b/4, r+b/2-u*b/4, z1, z1+u*p/2, False)),
        ("BRICKLAY",   (z2-z1)/p,      lambda u: (r-b/4, r+b/4, z1+u*(z2-z1), z1+p/2+u*(z2-z1), True)),
        ("T2",         0.5,            lambda u: (r-b/4+u*b/4, r+b/4-u*b/4, z2, z2+(p/2)*(1-u), False)),
        ("INTERWEAVE", (h-z2)/(2*p),   lambda u: (r, r, z2+u*(h-z2), z2+u*(h-z2), True)),
        ("LEADOUT",    0.5,            lambda u: (r, r, h, h, True)),
    ]

    rows, t, dt = [], 0.0, 1.0 / a.freq
    for name, revs, fn in segs:
        n = max(1, int(revs / om * a.freq))
        for i in range(n):
            r0, r1, zz0, zz1, on = fn(i / n)
            e0 = K * TAU * r0 * om if on else 0.0
            e1 = K * TAU * r1 * om if on else 0.0
            rows.append((t, r0, 0.0, zz0, -r1, 0.0, zz1, om, e0, e1))
            t += dt

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        fh.write("t,x0,y0,z0,x1,y1,z1,rotary_speed_rev_s,extruder_0_mm_s,extruder_1_mm_s\n")
        for row in rows:
            fh.write("%.4f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.5f,%.3f,%.3f\n" % row)
    print("wrote %s: %d rows, %.0f s print, omega %.4f rev/s" % (a.out, len(rows), t, om))
    for name, revs, _ in segs:
        print("  %-11s %6.1f rev" % (name, revs))

if __name__ == "__main__":
    main()
