"""Shapes compiled into speed-and-time Programs (see cell.program).

The bed is the master for every outline here: its angle is the polar angle of the point being
laid, so the arm sits on one line out from the axis (direction ANGLE in its own frame) and only
moves radially, while the bed speed follows the outline so the bead is laid at V mm/s everywhere.
The extruder rate is K x V, K = W x L_H / (filament area x FLOW_K) mm of filament per mm of bead.
One loop of outline = one revolution, so Z climbs P per revolution (vase) or holds (flat loops).

Outlines (all symmetric, so the bed direction sign does not matter):
    circle(R)                          a cylinder
    rounded_polygon(n, side, rf)       n sides, outside side length, fillet radius (n = 4: the square)
Any function r(theta_deg) that is single valued about the axis can be compiled with `polar`.

Manual actions are one-segment programs: purge, bed_jog, arm_goto.
"""
from __future__ import annotations

import math

from cell.program import Program

FIL_D = 1.75
STEP_DEG = 2.0                      # bed-master compile step: one segment per this much bed angle
BED_DIR = -1                        # positive bed velocity turns the disc clockwise seen from above (measured 9 Nov 2026)


# ----------------------------------------------------------------------
# outlines: r(theta) in mm, theta in degrees from a side centre / any point on a circle
def circle(R):
    return lambda th: float(R)


def rounded_polygon(n, side, rf):
    """Regular n-gon with outside side length `side` (bead centreline) and fillet radius rf."""
    if n < 3:
        raise ValueError("a polygon needs at least 3 sides")
    half = math.pi / n                              # half the angle a side spans about the axis
    S = side / (2 * math.tan(half))                 # apothem (axis to side centre)
    if rf <= 0 or rf >= S:
        raise ValueError(f"fillet radius must be above 0 and below the apothem {S:.1f} mm (that would be a circle)")
    F = (S - rf) / math.cos(half)                   # axis to a fillet centre
    u1 = math.atan2((S - rf) * math.tan(half), S)   # where the straight meets the fillet

    def r(th):
        u = math.radians(th) % (2 * half)
        if u > half:
            u = 2 * half - u
        if u <= u1:
            return S / math.cos(u)
        du = half - u
        return F * math.cos(du) + math.sqrt(max(rf * rf - (F * math.sin(du)) ** 2, 0.0))
    return r


def rounded_square(side, rf):
    return rounded_polygon(4, side, rf)


def ds_dtheta(r, th, h=0.05):
    """Outline mm per radian of bed angle at th: sqrt(r^2 + r'^2)."""
    rp = (r(th + h) - r(th - h)) / math.radians(2 * h)
    return math.hypot(r(th), rp)


def outline_length(r):
    return sum(ds_dtheta(r, i * 0.5) * math.radians(0.5) for i in range(720))


# ----------------------------------------------------------------------
def bead_k(W, L_H, flow_k=1.0, fil_d=FIL_D):
    """mm of filament per mm of bead."""
    return W * L_H / (math.pi * (fil_d / 2) ** 2 * flow_k)


def arm_point(arm, r):
    """Radius along the arm's print direction -> (x, y) in that arm's frame."""
    ang = math.radians(arm["ANGLE"])
    return (arm["CX"] + r * math.cos(ang), arm["CY"] + r * math.sin(ang))


# ----------------------------------------------------------------------
def polar(arms, r, *, V, W, L_H, flow_k=1.0, pitch=None, loops=3.0, flat_first=1.0, flat_last=1.0,
          first=None, prime_mm=15.0, prime_s=5.0, prime_z=2.0, max_bed_dps=180.0, step_deg=STEP_DEG,
          name="polar"):
    """Compile a bed-master outline into a Program for one or two arms.

    arms: {tool: {"CX", "CY", "ANGLE", "Z0", "DR"(opt)}}  (cell.runs.cylinder.DEFAULTS entries)
    r: outline function r(theta_deg) for the lead arm; a second arm uses the same outline offset by
       its DR (outline trim), 180 deg round, which the symmetric outlines make identical.
    pitch: Z per loop of outline (default n_arms x L_H); loops: spiral loops; flat_first/flat_last:
       flat loops at the first layer and at the top (each arm does 1/n of them). first: first layer
       height above Z0 (default L_H).
    """
    n = len(arms)
    pitch = n * L_H if pitch is None else pitch
    first = L_H if first is None else first
    K = bead_k(W, L_H, flow_k)
    prog = Program(name, f"V {V} mm/s, bead {W} x {L_H}, K {K:.3f}, pitch {pitch}, loops {flat_first}+{loops}+{flat_last}")
    tools = sorted(arms)
    rs = {t: (lambda th, dr=arms[t].get("DR", 0.0): r(th) + dr) for t in tools}
    r0 = rs[tools[0]]

    # prime: bed at half the side-centre speed, extruder at half rate, nozzle runs in from outside
    w_start = math.degrees(V / ds_dtheta(r0, 0.0))
    bed = prog.track("bed")
    bed.add(w_start / 2, prime_s)
    n_pr = max(1, int(prime_s / 0.25))
    for t in tools:
        prog.track(f"ext{t}").add(K * V / 2, prime_s)
        a = arms[t]
        z_first, zp = a["Z0"] + first, a["Z0"] + prime_z
        tr = prog.track(f"arm{t}")
        th = 0.0
        for i in range(1, n_pr + 1):
            f = i / n_pr
            th += (w_start / 2) * (prime_s / n_pr)
            x, y = arm_point(a, rs[t](th) + prime_mm * (1 - f))
            tr.add((x, y, zp + (z_first - zp) * f), prime_s / n_pr)

    # the outline: one segment per step_deg of bed
    total_loops = flat_first / n + loops + flat_last / n
    th_end = 360.0 * total_loops
    th = (w_start / 2) * prime_s                        # the bed already turned this much in the prime

    def z_at(t, th_now):
        u = th_now / 360.0
        c0 = flat_first / n
        if u < c0:
            dz = 0.0
        elif u < c0 + loops:
            dz = pitch * (u - c0)
        else:
            dz = pitch * loops
        return arms[t]["Z0"] + first + dz

    th_rel = 0.0
    while th_rel < th_end - 1e-9:
        d = min(step_deg, th_end - th_rel)
        w = min(math.degrees(V / ds_dtheta(r0, th + d / 2)), max_bed_dps)
        dt = d / w
        bed.add(w, dt)
        for t in tools:
            prog.track(f"ext{t}").add(K * V, dt)
            x, y = arm_point(arms[t], rs[t](th + d))
            prog.track(f"arm{t}").add((x, y, z_at(t, th_rel + d)), dt)
        th += d
        th_rel += d
    # finish: extruders off, bed stops (ends of tracks), arms lift 10 mm over 2 s
    for t in tools:
        a = arms[t]
        x, y = arm_point(a, rs[t](th))
        prog.track(f"arm{t}").add((x, y, z_at(t, th_rel) + 10.0), 2.0)
    return prog


def cylinder(arms, R, **kw):
    kw.setdefault("name", f"cylinder R{R:g}")
    return polar(arms, circle(R), **kw)


def rounded_square_program(arms, side, rf, **kw):
    kw.setdefault("name", f"rounded square {side:g}/{rf:g}")
    return polar(arms, rounded_square(side, rf), **kw)


def rounded_polygon_program(arms, n, side, rf, **kw):
    kw.setdefault("name", f"{n}-gon {side:g}/{rf:g}")
    return polar(arms, rounded_polygon(n, side, rf), **kw)


# ----------------------------------------------------------------------
# manual actions as one-segment programs
def purge(tool, rate, seconds):
    p = Program(f"purge T{tool}", f"{rate} mm/s for {seconds} s")
    p.track(f"ext{tool}").add(rate, seconds)
    return p


def bed_jog(deg_per_s, seconds):
    p = Program("bed", f"{deg_per_s} deg/s for {seconds} s")
    p.track("bed").add(deg_per_s, seconds)
    return p


def arm_goto(tool, xyz, seconds):
    p = Program(f"arm{tool} goto", f"to {xyz} in {seconds} s")
    p.track(f"arm{tool}").add(tuple(xyz), seconds)
    return p


# ----------------------------------------------------------------------
def bed_frame_path(rows, arm, tool, bed_dir=BED_DIR):
    """Where the nozzle of `tool` was on the DISC: simulate() rows -> list of (x, y, z) in the bed
    frame (turntable axis at the origin, x along the arm's ANGLE at bed angle 0)."""
    ang = math.radians(arm["ANGLE"])
    n, t = (math.cos(ang), math.sin(ang)), (-math.sin(ang), math.cos(ang))
    out = []
    for row in rows:
        p = row.get(f"arm{tool}")
        if p is None:
            continue
        dx, dy = p[0] - arm["CX"], p[1] - arm["CY"]
        u, v = dx * n[0] + dy * n[1], dx * t[0] + dy * t[1]
        ph = -math.radians(bed_dir * row["bed_deg"])
        out.append((u * math.cos(ph) - v * math.sin(ph), u * math.sin(ph) + v * math.cos(ph), p[2]))
    return out
