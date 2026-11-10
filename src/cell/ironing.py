"""Ironing the outer face of a printed column with the second arm: Nino's zones and motifs.

Nino's method (FullControl, for a flat wall on a 5-axis machine): the face is split into ZONES,
each with its own range along the wall, start height, pressure, list of passes and motion motif
(line, zigzag or spiral). For each zone the nozzle moves in front of it a safe distance away,
approaches the wall straight on until it presses by `pressure`, sweeps back and forth, one pass
per entry in `passes` (each shifted in z), then backs away before the next zone.

On the cell the face can be FLAT (a square column's side) or ROUND (a cylinder), and the motion
along it can come from the arm or from the bed (face= and mode= in compile_zones):
    face "flat"  Nino's wall as she wrote it: a straight line along the side at apothem - pressure
                 from the axis, the arm moving in its own XYZ with the bed still (mode "arm" only)
    face "round" "along the wall" is an arc about the axis at R - pressure:
        mode "arm"  the bed stays still, the arm moves along the arc in XYZ (the default)
        mode "bed"  the bed turns the face past the nozzle (arc length / R as bed angle) and the
                    arm holds its radius and does only the z part of the motif
The pattern functions below are Nino's, with (x, y, z) replaced by (s, z): s = mm along the
face, z = height.

Zone dict (Nino's format):
    pressure        mm the nozzle sits INSIDE the nominal face (negative = clear of it)
    x_start, x_end  mm along the face, start and end of the zone (direction of the 1st pass)
    z_start         mm, height of the 1st pass (above the disc; the arm's Z0 is added)
    passes          z offsets (mm) relative to z_start, done back and forth
    motif           "line"   -> step  (mm, segment length)
                    "zigzag" -> amp   (mm, amplitude in z), pitch (mm, advance between vertices)
                    "spiral" -> r     (mm, loop radius, total height 2 r), pitch (mm per turn), seg (points per turn)

compile_zones() turns a list of zones into a cell.program.Program: an arm track ((x, y, z), s) and,
in "bed" mode, a bed track (deg/s, s), both from IRON_SPEED along the pattern. Preview it with
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
def compile_zones(arm, r_face, zones, *, face="round", mode="arm", flat_half_width=None, iron_speed=5.0,
                  safe_distance=5.0, approach_speed=2.0, travel_speed=20.0, bed_dir=-1, name="ironing"):
    """Nino's zone sequence as a Program for one arm pressing the face of a column.

    arm:      the ironing arm's DEFAULTS entry (CX, CY, ANGLE, Z0)
    r_face:   distance from the axis to the face the nozzle sees: the cylinder's radius, or a square
              column's apothem (half side), plus that arm's DR, mm
    zones:    list of zone dicts (see module docstring); x along the face in mm (arc length on a
              round face, straight distance from the face centre on a flat one)
    face:     "round" or "flat"
    mode:     "arm" (bed still, the arm moves along the face in XYZ) or "bed" (round faces only:
              the bed turns the face past the nozzle, the arm holds its radius and does z)
    flat_half_width: flat faces: how far the flat part extends from the face centre (half side
              minus the fillet radius); zones beyond it would run onto the fillet and are refused
    iron_speed: mm/s along the pattern; approach/back-off at approach_speed; between zones at travel_speed
    bed_dir:  "bed" mode only: sign that makes +s turn the face past the nozzle the same way as +x
              did on Nino's wall (only mirrors the pattern; -1 as measured on the cell)
    """
    if mode not in ("arm", "bed"):
        raise ValueError("mode must be 'arm' or 'bed'")
    if face not in ("round", "flat"):
        raise ValueError("face must be 'round' or 'flat'")
    if face == "flat" and mode == "bed":
        raise ValueError("a flat face cannot be ironed by turning the bed (its distance from the axis changes); use mode='arm'")
    if face == "flat" and flat_half_width is not None:
        for k, cfg in enumerate(zones, start=1):
            reach = max(abs(cfg["x_start"]), abs(cfg["x_end"])) + (cfg.get("r", 0.0) if cfg["motif"] == "spiral" else 0.0)
            if reach > flat_half_width + 1e-9:
                raise ValueError(f"zone {k} reaches {reach:g} mm from the face centre, past the flat part ({flat_half_width:g} mm)")
    prog = Program(name, f"{len(zones)} zones, {face} face, {mode} moves, {iron_speed:g} mm/s, safe {safe_distance:g} mm")
    bed = prog.track("bed") if mode == "bed" else None
    tr = prog.track(f"arm{arm['T']}")
    z0 = arm["Z0"]
    ang0 = math.radians(arm["ANGLE"])

    def deg(s):
        return bed_dir * math.degrees(s / r_face)

    def point(r, s):
        """Nozzle (x, y) in the arm's frame at distance r from the axis and s along the face."""
        if mode == "bed":
            return arm_point(arm, r)                              # fixed direction; the bed brings s round
        if face == "flat":                                        # straight along the side: r out, s sideways
            return (arm["CX"] + r * math.cos(ang0) - s * math.sin(ang0),
                    arm["CY"] + r * math.sin(ang0) + s * math.cos(ang0))
        phi = ang0 + s / r_face                                   # round: s along the face = an angle about the axis
        return (arm["CX"] + r * math.cos(phi), arm["CY"] + r * math.sin(phi))

    def seg(r, s_from, z_from, s_to, z_to, speed):
        """One segment of the pattern from (s_from, z_from) to (s_to, z_to) at radius r."""
        length = math.hypot(s_to - s_from, z_to - z_from)
        dt = max(0.05, length / speed)
        if bed is not None:
            bed.add((deg(s_to) - deg(s_from)) / dt, dt)
        x, y = point(r, s_to)
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
            x, y = point(r_safe, x_a)
            tr.add((x, y, z0 + z_a), 1.0)
            if bed is not None:
                bed.add(0.0, 1.0)
        else:
            seg(r_safe, s_cur, z_cur, x_a, z_a, travel_speed)
        # perpendicular approach to the face: only the radius changes
        x, y = point(r_press, x_a)
        dt = max(0.2, (r_safe - r_press) / approach_speed)
        tr.add((x, y, z0 + z_a), dt)
        if bed is not None:
            bed.add(0.0, dt)
        # the passes
        s_cur, z_cur = x_a, z_a
        for s_to, z_to in zone_points(cfg):
            seg(r_press, s_cur, z_cur, s_to, z_to, iron_speed)
            s_cur, z_cur = s_to, z_to
        # back off perpendicular to the face, where the nozzle finished
        x, y = point(r_safe, s_cur)
        tr.add((x, y, z0 + z_cur), dt)
        if bed is not None:
            bed.add(0.0, dt)
    return prog
