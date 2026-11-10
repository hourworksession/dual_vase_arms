"""Ironing the outer face of a printed column with the second arm: Nino's zones and motifs.

Nino's method (FullControl, for a flat wall on a 5-axis machine): the face is split into ZONES,
each with its own range along the wall, start height, pressure, list of passes and motion motif
(line, zigzag or spiral). For each zone the nozzle moves in front of it a safe distance away,
approaches the wall straight on until it presses by `pressure`, sweeps back and forth, one pass
per entry in `passes` (each shifted in z), then backs away before the next zone.

On the cell the column is round and the bed can turn, so "along the wall" is BED ROTATION:
arc length s on the face becomes a bed angle s / R_face, the nozzle holds its radius
(R_face - pressure) and only does the z part of the motif. The pattern functions below are
Nino's, with (x, y, z) replaced by (s, z): s = mm along the face, z = height.

Zone dict (Nino's format):
    pressure        mm the nozzle sits INSIDE the nominal face (negative = clear of it)
    x_start, x_end  mm along the face, start and end of the zone (direction of the 1st pass)
    z_start         mm, height of the 1st pass (above the disc; the arm's Z0 is added)
    passes          z offsets (mm) relative to z_start, done back and forth
    motif           "line"   -> step  (mm, segment length)
                    "zigzag" -> amp   (mm, amplitude in z), pitch (mm, advance between vertices)
                    "spiral" -> r     (mm, loop radius, total height 2 r), pitch (mm per turn), seg (points per turn)

compile_zones() turns a list of zones into a cell.program.Program: a bed track (deg/s, s) and an
arm track ((x, y, z), s), both from IRON_SPEED along the pattern. Preview it with
cell.program.simulate, run it with cell.program.Player, like any other program.
"""
from __future__ import annotations

import math

from cell.program import Program
from cell.shapes import arm_point

tau = 2 * math.pi


def dist(a, b):
    return math.dist(a, b)


# ---------------------------------------------------------------- Nino's pattern functions, (s, z)
def segmenter(p_start, p_end, step):
    """Split the segment p_start -> p_end into pieces of ~step mm. Returns the points without
    p_start, with p_end."""
    n = max(1, round(dist(p_start, p_end) / step))
    return [tuple(s + (e - s) * j / n for s, e in zip(p_start, p_end)) for j in range(1, n + 1)]


def line(p_start, p_end, step):
    """Straight line from p_start to p_end (s, z) at constant z, in pieces of ~step mm."""
    return [(s, p_start[1]) for s, _ in segmenter(p_start, p_end, step)]


def zigzag(p_start, p_end, amp, pitch):
    """Zigzag from p_start to p_end (s, z); odd vertices are raised by amp in z. Even n -> ends on
    the line, at p_end."""
    n = 2 * max(1, round(dist(p_start, p_end) / (2 * pitch)))
    pts = []
    for j in range(1, n + 1):
        s, z = (a + (b - a) * j / n for a, b in zip(p_start, p_end))
        if j % 2:
            z += amp
        pts.append((s, z))
    return pts


def spiral(p_start, p_end, radius, pitch, n_seg=36):
    """Spiral line from p_start to p_end (s, z): the nozzle draws circles of radius `radius` in
    the (along the face, z) plane, advancing by `pitch` per turn. The loops go from the line up
    to line + 2 radius. n_seg = points per turn."""
    L = dist(p_start, p_end)
    n_turns = max(1, round(L / pitch))
    u_dir = 1.0 if p_end[0] >= p_start[0] else -1.0
    n = n_turns * n_seg
    pts = []
    for j in range(1, n + 1):
        t = j / n
        theta = tau * n_turns * t
        u = radius * math.sin(theta)
        w = radius * (1 - math.cos(theta))
        s, z = (a + (b - a) * t for a, b in zip(p_start, p_end))
        pts.append((s + u * u_dir, z + w))
    return pts


def iron_pass(cfg, p_start, p_end):
    """One ironing pass from p_start to p_end (s, z) using the zone's motif and settings."""
    if cfg["motif"] == "line":
        return line(p_start, p_end, cfg["step"])
    if cfg["motif"] == "zigzag":
        return zigzag(p_start, p_end, cfg["amp"], cfg["pitch"])
    if cfg["motif"] == "spiral":
        return spiral(p_start, p_end, cfg["r"], cfg["pitch"], n_seg=cfg.get("seg", 36))
    raise ValueError(f"unknown motif: {cfg['motif']}")


def motif_height(cfg):
    """Height covered by the motif above the pass line."""
    return {"line": 0, "zigzag": cfg.get("amp", 0), "spiral": 2 * cfg.get("r", 0)}[cfg["motif"]]


def zone_points(cfg):
    """All (s, z) points of one zone, pass after pass, back and forth. z relative to the disc."""
    pts = []
    x_a, x_b, z = cfg["x_start"], cfg["x_end"], cfg["z_start"]
    for p, dz in enumerate(cfg["passes"]):
        z_pass = z + dz
        p0, p1 = (x_a, x_b) if p % 2 == 0 else (x_b, x_a)
        pts += iron_pass(cfg, (p0, z_pass), (p1, z_pass))
    return pts


def check_zones(zones, z_bottom, z_top):
    """Nino's checks: each zone must stay between the bottom and the top of the wall."""
    notes = []
    for k, cfg in enumerate(zones, start=1):
        z_max = cfg["z_start"] + max(cfg["passes"]) + motif_height(cfg)
        if z_max > z_top:
            notes.append(f"zone {k}: ironing goes up to {z_max:g} mm, above the top of the wall ({z_top:g} mm)")
        if cfg["z_start"] < z_bottom:
            notes.append(f"zone {k}: z_start = {cfg['z_start']:g} mm, below the 1st layer ({z_bottom:g} mm)")
    return notes


# ---------------------------------------------------------------- the cell: bed turns, arm holds radius
def compile_zones(arm, r_face, zones, *, iron_speed=5.0, safe_distance=5.0, approach_speed=2.0,
                  travel_speed=20.0, bed_dir=-1, name="ironing"):
    """Nino's zone sequence as a Program for one arm pressing the face of a round column.

    arm:      the ironing arm's DEFAULTS entry (CX, CY, ANGLE, Z0)
    r_face:   radius of the face the nozzle sees (column radius + that arm's DR), mm
    zones:    list of zone dicts (see module docstring); x along the face = arc length, mm
    iron_speed: mm/s along the pattern; approach/back-off at approach_speed; between zones at travel_speed
    bed_dir:  sign that makes +s turn the face past the nozzle the same way as +x did on Nino's wall
              (only mirrors the pattern; -1 as measured on the cell)
    Bed angles: s / r_face, so the bed track is the sequence of (deg/s, seconds) that walks the
    pattern; the arm track holds radius r_face - pressure and follows z.
    """
    prog = Program(name, f"{len(zones)} zones, {iron_speed:g} mm/s, safe {safe_distance:g} mm")
    bed = prog.track("bed")
    tr = prog.track(f"arm{arm['T']}")
    z0 = arm["Z0"]

    def deg(s):
        return bed_dir * math.degrees(s / r_face)

    def seg(r, s_from, z_from, s_to, z_to, speed):
        """One segment: bed from angle(s_from) to angle(s_to), nozzle from (r, z_from) to (r, z_to)."""
        length = math.hypot(s_to - s_from, z_to - z_from)
        dt = max(0.05, length / speed)
        bed.add((deg(s_to) - deg(s_from)) / dt, dt)
        x, y = arm_point(arm, r)
        tr.add((x, y, z0 + z_to), dt)
        return dt

    s_cur, z_cur = None, None
    for cfg in zones:
        r_press = r_face - cfg["pressure"]
        r_safe = r_face + safe_distance
        x_a, z_a = cfg["x_start"], cfg["z_start"]
        # move in front of the zone's start, backed off (bed turns to the zone, nozzle at safe radius)
        if s_cur is None:
            s_cur, z_cur = x_a, z_a
            x, y = arm_point(arm, r_safe)
            tr.add((x, y, z0 + z_a), 1.0)
            bed.add(0.0, 1.0)
        else:
            seg(r_safe, s_cur, z_cur, x_a, z_a, travel_speed)
        # perpendicular approach to the face: only the radius changes
        x, y = arm_point(arm, r_press)
        dt = max(0.2, (r_safe - r_press) / approach_speed)
        tr.add((x, y, z0 + z_a), dt)
        bed.add(0.0, dt)
        # the passes
        s_cur, z_cur = x_a, z_a
        for s_to, z_to in zone_points(cfg):
            seg(r_press, s_cur, z_cur, s_to, z_to, iron_speed)
            s_cur, z_cur = s_to, z_to
        # back off perpendicular to the face, where the nozzle finished
        x, y = arm_point(arm, r_safe)
        tr.add((x, y, z0 + z_cur), dt)
        bed.add(0.0, dt)
    return prog
