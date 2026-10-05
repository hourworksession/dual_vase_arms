"""cell_forms: shapes and forms for the dual-arm turntable cell, as FullControl-style points.

A standalone module (numpy only; fullcontrol is optional) that describes the cell and
lets you draw with it. Everything is built from one idea:

    A point on the part lives in the PLATE frame (x, y on the disc, turning with it).
    The disc angle decides where that point is in the world, and so where an arm must be.
    If the disc turns while the arm only moves along ONE line (its radial axis) the
    arm draws, in the plate frame, whatever r(theta) you give it: a circle if r is
    constant, a SQUARE if r = a / max(|cos t|, |sin t|), any polygon, any lobed wall.

So a "shape" here is a list of PathPoints in the plate frame; a "form" stacks shapes
up the height (helix, cone, vase, ...) and the Cell turns the lot into a timeline:
disc angle and speed, each arm's XYZ + roll/pitch/yaw in ITS OWN base frame, and the
extrusion volume per step. The later slicer calls these same methods, so what you
bake in here (angles, two-arm offsets, speeds) is what it will print with.

Layout
    Arm, Turntable, Extruder, PrintSettings, Cell      the hardware and the settings
    PathPoint, Path                                     plate-frame geometry (+ width/height/tilt)
    Shapes      circle, square, polygon, ellipse, lobed, polar(r_of_theta), line, rect, arc,
                spiral: one closed/open loop at one height
    Forms       helix, cone, vase(profile, section), dual_helix (two arms 180 deg apart),
                stack (repeat a shape per layer), twist, bricked walls
    Cell.plan   paths -> Timeline;   Timeline.to_csv / to_json / summary
    fullcontrol bridge: to_fullcontrol(path), from_fullcontrol(points)

Conventions
    angles in DEGREES at the API, radians inside; disc angle phi positive CCW seen from above;
    plate -> world:  world = C + Rz(phi) . plate   (world = the RIGHT arm's base frame,
    as the panel's planner uses);  each arm's targets are then given in that arm's frame.
    An arm "faces" its azimuth: for a plate point at polar angle theta, the disc angle
    that brings it in front of arm k is  phi = azimuth_k - theta.

Fill in / change: the dataclass defaults (measured cell values), the Shapes/Forms methods
(add your own), and `Rules` (which arm takes what, speeds, when to hold the disc).
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field, asdict
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np

TAU = 2.0 * math.pi


# =============================================================================
# 1. The cell
# =============================================================================
@dataclass
class Arm:
    """One UFACTORY 850. Poses are xArm style: x, y, z mm in the arm's base frame,
    roll/pitch/yaw degrees, R = Rz(yaw) Ry(pitch) Rx(roll)."""
    name: str = "right"
    # Where this arm's base sits in the WORLD (= right arm base) frame, and its yaw there.
    base_xyz: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    base_yaw_deg: float = 0.0
    # Which side of the disc the arm works from: the polar angle (deg, plate/world) of the
    # arm as seen from the disc centre. Right arm sits at 180 (it is at -x of the disc),
    # the left arm at 0, so a feature at plate angle theta faces the arm at phi = az - theta.
    azimuth_deg: float = 180.0
    # Nozzle straight down, as commanded today (per-arm because of the mounts).
    roll: float = 90.0
    pitch: float = -90.0
    yaw: float = 90.0
    # nozzle tip relative to the flange (flange frame), set on the controller as TCP
    tcp: Tuple[float, float, float] = (-65.0, 0.0, 76.0)
    reach_mm: float = 850.0
    tilt_max_deg: float = 35.0          # largest lean of the nozzle from vertical
    max_speed: float = 150.0            # mm/s nozzle, printing
    travel_speed: float = 300.0         # mm/s
    tool_index: int = 0                 # extruder channel on the controller

    @property
    def azimuth(self):
        return math.radians(self.azimuth_deg)


@dataclass
class Turntable:
    centre: Tuple[float, float, float] = (508.18, -22.88, 107.4)   # world (right arm frame)
    radius_mm: float = 150.0
    max_speed: float = 1.5              # rad/s
    max_accel: float = 3.0              # rad/s^2
    # Straight lines in polar need the disc to speed up and slow down a lot; above this
    # fraction of max_accel the planner holds the disc and lets the arm draw instead.
    hold_above_accel_fraction: float = 0.8


@dataclass
class Extruder:
    name: str = "Revo High Flow 1.2"
    nozzle_mm: float = 1.2
    filament_mm: float = 1.75
    temp_c: float = 215.0
    flow: float = 1.0                   # multiplier on the volume
    max_volumetric: float = 30.0        # mm^3/s the hotend can melt
    min_width_frac: float = 0.8         # narrowest bead / nozzle
    max_width_frac: float = 1.6         # widest bead / nozzle

    def width_limits(self):
        return self.nozzle_mm * self.min_width_frac, self.nozzle_mm * self.max_width_frac

    def e_mm(self, volume_mm3):
        """Volume -> filament length (what E in G-code means)."""
        return volume_mm3 / (math.pi * (self.filament_mm / 2) ** 2)


@dataclass
class PrintSettings:
    layer_height: float = 0.6
    line_width: float = 1.3
    wall_count: int = 2
    print_speed: float = 40.0           # mm/s along the bead (plate frame)
    first_layer_speed: float = 20.0
    travel_speed: float = 150.0
    travel_lift: float = 2.0            # mm z hop when not extruding
    segment_mm: float = 1.0             # resolution of generated shapes
    cone_angle_deg: float = 15.0        # outer bead lean, "Christmas tree" build
    brick_offset: float = 0.5           # inner bead ahead of outer by this many layers
    two_arm_min_radius: float = 35.0    # below this the two tools cannot face each other
    min_nozzle_separation: float = 60.0


@dataclass
class Rules:
    """What you want baked in. The slicer reads these; an AI may propose new values."""
    outer_bead_arm: str = "right"
    inner_bead_arm: str = "left"
    turn_disc_for_round_paths: bool = True
    hold_disc_for_fill: bool = True
    face_feature_to_arm: bool = True
    order: Tuple[str, ...] = ("axis_wall", "circles", "straight", "fill")
    max_overhang_deg: float = 45.0


@dataclass
class Cell:
    arms: List[Arm] = field(default_factory=lambda: [
        Arm("right", (0.0, 0.0, 0.0), 0.0, 180.0, 90.0, -90.0, 90.0, (-65.0, 0.0, 76.0), tool_index=0),
        # left base: the disc centre is (512.1, -8.4, 110.2) in ITS frame and its base is
        # turned 180 deg to the right arm's, so base = C - Rz(180).(512.1, -8.4, 110.2)
        Arm("left", (508.18 + 512.1, -22.88 - 8.4, 107.4 - 110.2), 180.0, 0.0, 0.0, 90.0, 0.0,
            (65.0, 0.0, 76.0), tool_index=1),
    ])
    turntable: Turntable = field(default_factory=Turntable)
    extruders: List[Extruder] = field(default_factory=lambda: [Extruder(), Extruder()])
    settings: PrintSettings = field(default_factory=PrintSettings)
    rules: Rules = field(default_factory=Rules)

    def arm(self, name_or_index) -> Arm:
        if isinstance(name_or_index, int):
            return self.arms[name_or_index]
        return next(a for a in self.arms if a.name == name_or_index)

    # ---- frames ---------------------------------------------------------------
    def plate_to_world(self, p, phi):
        """Plate point (x, y, z above the disc) at disc angle phi (rad) -> world xyz."""
        c, s = math.cos(phi), math.sin(phi)
        C = self.turntable.centre
        return (C[0] + c * p[0] - s * p[1], C[1] + s * p[0] + c * p[1], C[2] + p[2])

    def world_to_arm(self, w, arm: Arm):
        """World xyz -> that arm's base frame."""
        a = math.radians(arm.base_yaw_deg)
        c, s = math.cos(-a), math.sin(-a)
        dx, dy, dz = w[0] - arm.base_xyz[0], w[1] - arm.base_xyz[1], w[2] - arm.base_xyz[2]
        return (c * dx - s * dy, s * dx + c * dy, dz)

    def facing_angle(self, theta, arm: Arm):
        """Disc angle (rad) that brings plate polar angle theta in front of the arm."""
        return arm.azimuth - theta


# =============================================================================
# 2. Plate-frame geometry
# =============================================================================
@dataclass
class PathPoint:
    x: float
    y: float
    z: float
    width: float = 1.3           # bead width (mm); the extruder pushes less for narrower
    height: float = 0.6
    tilt: float = 0.0            # nozzle lean (deg) about the bead tangent, + = outward
    extrude: bool = True
    arm: int = 0                 # which arm lays it (index into Cell.arms)
    tag: str = ""                # free text: "outer", "inner", "post 2", ...

    @property
    def r(self):
        return math.hypot(self.x, self.y)

    @property
    def theta(self):
        return math.atan2(self.y, self.x)

    def xyz(self):
        return (self.x, self.y, self.z)


class Path(list):
    """A list of PathPoints with a few helpers. Open unless you close it yourself."""

    def length(self) -> float:
        return sum(math.dist(a.xyz(), b.xyz()) for a, b in zip(self[:-1], self[1:]))

    def volume(self) -> float:
        return sum(math.dist(a.xyz(), b.xyz()) * b.width * b.height for a, b in zip(self[:-1], self[1:]) if b.extrude)

    def closed(self) -> "Path":
        if self and math.dist(self[0].xyz(), self[-1].xyz()) > 1e-6:
            p = Path(self)
            q = PathPoint(**asdict(self[0]))
            p.append(q)
            return p
        return self

    def moved(self, dx=0.0, dy=0.0, dz=0.0) -> "Path":
        return Path([PathPoint(p.x + dx, p.y + dy, p.z + dz, p.width, p.height, p.tilt, p.extrude, p.arm, p.tag) for p in self])

    def rotated(self, deg: float) -> "Path":
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        return Path([PathPoint(c * p.x - s * p.y, s * p.x + c * p.y, p.z, p.width, p.height, p.tilt, p.extrude, p.arm, p.tag)
                     for p in self])

    def for_arm(self, arm: int, tag: Optional[str] = None) -> "Path":
        for p in self:
            p.arm = arm
            if tag is not None:
                p.tag = tag
        return self

    def with_bead(self, width=None, height=None, tilt=None) -> "Path":
        for p in self:
            if width is not None:
                p.width = width
            if height is not None:
                p.height = height
            if tilt is not None:
                p.tilt = tilt
        return self

    def ramp_z(self, dz: float) -> "Path":
        """Climb dz linearly along the path (FullControl ramp_xyz): a loop becomes one turn
        of a helix, which is how ANY outline turns into a continuous spiral."""
        n = max(1, len(self) - 1)
        return Path([PathPoint(p.x, p.y, p.z + dz * k / n, p.width, p.height, p.tilt, p.extrude, p.arm, p.tag)
                     for k, p in enumerate(self)])

    def ramp_radius(self, dr: float) -> "Path":
        """Scale the radius linearly along the path (FullControl ramp_polar): a cone."""
        n = max(1, len(self) - 1)
        out = Path()
        for k, p in enumerate(self):
            r = p.r + dr * k / n
            t = p.theta
            out.append(PathPoint(r * math.cos(t), r * math.sin(t), p.z, p.width, p.height, p.tilt, p.extrude, p.arm, p.tag))
        return out

    def resampled(self, step: float) -> "Path":
        """Points every `step` mm along the path (bead values interpolated)."""
        if len(self) < 2:
            return Path(self)
        out = Path([PathPoint(**asdict(self[0]))])
        carry = 0.0
        for a, b in zip(self[:-1], self[1:]):
            d = math.dist(a.xyz(), b.xyz())
            if d < 1e-9:
                continue
            s = step - carry
            while s < d:
                t = s / d
                out.append(PathPoint(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t,
                                     a.width + (b.width - a.width) * t, a.height + (b.height - a.height) * t,
                                     a.tilt + (b.tilt - a.tilt) * t, b.extrude, b.arm, b.tag))
                s += step
            carry = d - (s - step)
        out.append(PathPoint(**asdict(self[-1])))
        return out


# =============================================================================
# 3. Shapes: one loop or line at one height, in the plate frame
# =============================================================================
class Shapes:
    """Each method returns a Path. `polar` is the general one: the arm stays on its radial
    line, the disc turns, and r(theta) draws the outline."""

    def __init__(self, cell: Cell):
        self.cell = cell
        self.s = cell.settings

    def _pt(self, x, y, z, w=None, h=None, tilt=0.0, arm=0, tag=""):
        return PathPoint(x, y, z, w if w is not None else self.s.line_width,
                         h if h is not None else self.s.layer_height, tilt, True, arm, tag)

    # ---- polar: one-axis arm motion + disc turn ------------------------------
    def polar(self, r_of_theta: Callable[[float], float], z: float, start_deg: float = 0.0,
              turns: float = 1.0, step_deg: Optional[float] = None, width=None, arm=0, tag="",
              n: Optional[int] = None) -> Path:
        """Outline r(theta) (theta in RADIANS inside the callable) walked round `turns` times.
        step_deg defaults to one settings.segment_mm of arc at the start radius; n fixes the
        point count instead (two lock-step arms must share one n)."""
        r0 = r_of_theta(math.radians(start_deg))
        if step_deg is None:
            step_deg = math.degrees(self.s.segment_mm / max(r0, 1.0))
        if n is None:
            n = max(8, int(round(abs(turns) * 360.0 / step_deg)))
        out = Path()
        for k in range(n + 1):
            t = math.radians(start_deg) + TAU * turns * k / n
            r = r_of_theta(t)
            out.append(self._pt(r * math.cos(t), r * math.sin(t), z, width, arm=arm, tag=tag))
        return out

    def circle(self, radius: float, z: float, start_deg=0.0, width=None, arm=0, tag="circle") -> Path:
        return self.polar(lambda t: radius, z, start_deg, 1.0, width=width, arm=arm, tag=tag)

    def square(self, side: float, z: float, start_deg=45.0, corner_r: float = 0.0, width=None, arm=0, tag="square") -> Path:
        """A square round the axis drawn with the arm on ONE axis:  r = a / max(|cos|,|sin|).
        corner_r > 0 rounds the corners (the disc cannot stop dead at a sharp corner)."""
        a = side / 2.0

        def r(t):
            c, s_ = abs(math.cos(t)), abs(math.sin(t))
            r_sq = a / max(c, s_)
            if corner_r <= 0:
                return r_sq
            # rounded square: inset square of a - corner_r, plus a circle of corner_r
            m = a - corner_r
            # distance from centre to the rounded outline along direction t
            # (solve on the quadrant: outline = square(m) Minkowski circle(corner_r))
            x, y = c, s_
            if x * m >= y * m and y <= x * (m / max(m, 1e-9)):
                pass
            # closed form: project onto the inset square corner (m, m) when in the corner sector
            if x > 0 and y > 0 and y / x > m / (m + corner_r) and y / x < (m + corner_r) / m:
                # ray meets the corner arc: |t*(x,y) - (m,m)| = corner_r
                b = -2 * (m * x + m * y)
                cc = 2 * m * m - corner_r ** 2
                disc = b * b - 4 * cc
                return (-b + math.sqrt(max(disc, 0.0))) / 2.0
            return (m + corner_r) / max(c, s_)
        return self.polar(r, z, start_deg, 1.0, width=width, arm=arm, tag=tag)

    def polygon(self, sides: int, enclosing_radius: float, z: float, start_deg=0.0, width=None, arm=0, tag="polygon") -> Path:
        """Regular polygon round the axis: r = R cos(pi/n) / cos(((t - t0) mod 2pi/n) - pi/n)."""
        half = math.pi / sides
        apothem = enclosing_radius * math.cos(half)

        def r(t):
            u = (t - math.radians(start_deg)) % (2 * half)
            return apothem / math.cos(u - half)
        return self.polar(r, z, start_deg, 1.0, width=width, arm=arm, tag=tag)

    def ellipse(self, a: float, b: float, z: float, start_deg=0.0, width=None, arm=0, tag="ellipse") -> Path:
        return self.polar(lambda t: (a * b) / math.hypot(b * math.cos(t), a * math.sin(t)), z, start_deg, 1.0,
                          width=width, arm=arm, tag=tag)

    def lobed(self, radius: float, lobes: int, amplitude: float, z: float, start_deg=0.0, width=None, arm=0, tag="lobed") -> Path:
        """A round wall with a sine ripple: r = R + A sin(n t)."""
        return self.polar(lambda t: radius + amplitude * math.sin(lobes * t), z, start_deg, 1.0, width=width, arm=arm, tag=tag)

    def offset_circle(self, cx: float, cy: float, radius: float, z: float, start_deg=0.0, width=None, arm=0, tag="post") -> Path:
        """A circle NOT on the axis (a post, a bore). Drawn with the disc held and faced to the arm."""
        n = max(8, int(TAU * radius / self.s.segment_mm))
        out = Path()
        for k in range(n + 1):
            t = math.radians(start_deg) + TAU * k / n
            out.append(self._pt(cx + radius * math.cos(t), cy + radius * math.sin(t), z, width, arm=arm, tag=tag))
        return out

    # ---- held-disc cartesian shapes ------------------------------------------
    def line(self, p0: Tuple[float, float], p1: Tuple[float, float], z: float, width=None, arm=0, tag="line") -> Path:
        out = Path([self._pt(p0[0], p0[1], z, width, arm=arm, tag=tag), self._pt(p1[0], p1[1], z, width, arm=arm, tag=tag)])
        return out.resampled(self.s.segment_mm)

    def rect(self, corner: Tuple[float, float], w: float, h: float, z: float, width=None, arm=0, tag="rect") -> Path:
        x, y = corner
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
        out = Path([self._pt(px, py, z, width, arm=arm, tag=tag) for px, py in pts])
        return out.resampled(self.s.segment_mm)

    def arc(self, cx, cy, radius, start_deg, arc_deg, z, width=None, arm=0, tag="arc") -> Path:
        n = max(4, int(abs(math.radians(arc_deg)) * radius / self.s.segment_mm))
        out = Path()
        for k in range(n + 1):
            t = math.radians(start_deg + arc_deg * k / n)
            out.append(self._pt(cx + radius * math.cos(t), cy + radius * math.sin(t), z, width, arm=arm, tag=tag))
        return out

    def spiral_flat(self, r_start: float, r_end: float, z: float, turns: float, width=None, arm=0, tag="spiral") -> Path:
        """Flat Archimedean spiral (a bottom skin that never lifts the nozzle)."""
        return self.polar(lambda t: r_start + (r_end - r_start) * (t / (TAU * turns)), z, 0.0, turns,
                          width=width, arm=arm, tag=tag)

    def zigzag_fill(self, region_w: float, region_h: float, corner: Tuple[float, float], z: float,
                    spacing: float, angle_deg: float = 0.0, width=None, arm=0, tag="fill") -> Path:
        """Square-wave fill of a rectangle (FullControl squarewaveXY), disc held."""
        x0, y0 = corner
        n = max(1, int(region_h / spacing))
        pts = []
        for k in range(n + 1):
            y = y0 + k * spacing
            row = [(x0, y), (x0 + region_w, y)] if k % 2 == 0 else [(x0 + region_w, y), (x0, y)]
            pts.extend(row)
        out = Path([self._pt(px, py, z, width, arm=arm, tag=tag) for px, py in pts])
        return out.rotated(angle_deg) if angle_deg else out


# =============================================================================
# 4. Forms: shapes stacked or ramped up the height
# =============================================================================
class Forms:
    def __init__(self, cell: Cell):
        self.cell = cell
        self.s = cell.settings
        self.shapes = Shapes(cell)

    def helix(self, r_of_theta: Callable[[float], float], z0: float, z1: float, pitch: Optional[float] = None,
              start_deg=0.0, width=None, tilt=0.0, arm=0, tag="helix", n: Optional[int] = None) -> Path:
        """One continuous helix from z0 to z1 following the outline r(theta): THE primitive.
        pitch defaults to one layer height per turn."""
        pitch = pitch or self.s.layer_height
        turns = (z1 - z0) / pitch
        p = self.shapes.polar(r_of_theta, z0, start_deg, turns, width=width, arm=arm, tag=tag, n=n)
        return p.ramp_z(z1 - z0).with_bead(tilt=tilt)

    def cylinder(self, radius, z0, z1, **kw) -> Path:
        return self.helix(lambda t: radius, z0, z1, **kw)

    def cone(self, r0: float, r1: float, z0: float, z1: float, pitch=None, width=None, arm=0, tag="cone") -> Path:
        """Radius ramps r0 -> r1 up the height; the nozzle leans with the wall (within tilt_max)."""
        lean = math.degrees(math.atan2(r1 - r0, z1 - z0))
        lean = max(-self.cell.arm(arm).tilt_max_deg, min(self.cell.arm(arm).tilt_max_deg, lean))
        p = self.helix(lambda t: r0, z0, z1, pitch, width=width, arm=arm, tag=tag)
        return p.ramp_radius(r1 - r0).with_bead(tilt=lean)

    def vase(self, profile: Callable[[float], float], section: Callable[[float], float], z0: float, z1: float,
             pitch=None, width=None, arm=0, tag="vase") -> Path:
        """r(theta, z) = profile(z) * section(theta): any surface of revolution times any outline.
        profile(z) is the radius scale at height z (1.0 = the section as given)."""
        pitch = pitch or self.s.layer_height
        turns = (z1 - z0) / pitch
        n = max(8, int(turns * 360.0 / math.degrees(self.s.segment_mm / max(profile(z0) * section(0.0), 1.0))))
        out = Path()
        prev = None
        for k in range(n + 1):
            u = k / n
            z = z0 + (z1 - z0) * u
            t = TAU * turns * u
            r = profile(z) * section(t)
            tilt = 0.0
            if prev is not None:
                dr, dz = r - prev[0], z - prev[1]
                tilt = math.degrees(math.atan2(dr, dz)) if dz > 1e-9 else 0.0
                tilt = max(-self.cell.arm(arm).tilt_max_deg, min(self.cell.arm(arm).tilt_max_deg, tilt))
            out.append(self.shapes._pt(r * math.cos(t), r * math.sin(t), z, width, tilt=tilt, arm=arm, tag=tag))
            prev = (r, z)
        return out

    def dual_helix(self, r_of_theta: Callable[[float], float], z0: float, z1: float, beads: int = 2,
                   width=None, start_deg=0.0) -> Tuple[Path, Path]:
        """Two arms, 180 degrees apart, lock-step (same point count), as proven on the cylinder.
        beads=1: both arms lay the SAME bead, interleaved, pitch 2 layers each.
        beads=2: right arm outer bead leaning outward by the cone angle, left arm the inner
                 bead (one line width in) running AHEAD by brick_offset layers + lw tan(cone)."""
        s = self.s
        lw = width or s.line_width
        right, left = self.cell.arm(self.cell.rules.outer_bead_arm), self.cell.arm(self.cell.rules.inner_bead_arm)
        ir, il = self.cell.arms.index(right), self.cell.arms.index(left)
        # one point count for both arms, so index k is the same moment on both helices
        if beads == 1:
            pitch = 2 * s.layer_height
            a = self.helix(r_of_theta, z0, z1, pitch, start_deg, lw, arm=ir, tag="bead right")
            b = self.helix(r_of_theta, z0, z1, pitch, start_deg + 180.0, lw, arm=il, tag="bead left", n=len(a) - 1)
            return a, b
        lead = s.brick_offset * s.layer_height + lw * math.tan(math.radians(s.cone_angle_deg))
        outer = self.helix(lambda t: r_of_theta(t) - 0.5 * lw, z0, z1, s.layer_height, start_deg, lw,
                           tilt=s.cone_angle_deg, arm=ir, tag="outer")
        inner = self.helix(lambda t: r_of_theta(t) - 1.5 * lw, z0 + lead, z1 + lead, s.layer_height,
                           start_deg + 180.0, lw, tilt=0.0, arm=il, tag="inner", n=len(outer) - 1)
        return outer, inner

    def stack(self, shape: Path, z0: float, z1: float, continuous: bool = True) -> Path:
        """Repeat a closed shape per layer. continuous=True ramps each lap (one helix, no
        seam, no start/stop); False gives flat laps with a lift between them."""
        lh = self.s.layer_height
        n = max(1, int(round((z1 - z0) / lh)))
        out = Path()
        base = shape.closed()
        for k in range(n):
            lap = base.moved(dz=z0 + k * lh - base[0].z)
            if continuous:
                lap = lap.ramp_z(lh)
                if out:
                    lap = Path(lap[1:])
            out.extend(lap)
        return out

    def twist(self, path: Path, deg_per_mm: float) -> Path:
        """Turn the outline with height (a twisted vase)."""
        out = Path()
        z0 = path[0].z if path else 0.0
        for p in path:
            a = math.radians(deg_per_mm * (p.z - z0))
            c, s_ = math.cos(a), math.sin(a)
            out.append(PathPoint(c * p.x - s_ * p.y, s_ * p.x + c * p.y, p.z, p.width, p.height, p.tilt, p.extrude, p.arm, p.tag))
        return out

    def post(self, cx, cy, radius, z0, z1, arm=0, tag="post") -> Path:
        """An off-axis feature as one continuous helix (disc held, faced to the arm)."""
        lh = self.s.layer_height
        n = max(1, int(round((z1 - z0) / lh)))
        out = Path()
        for k in range(n):
            lap = self.shapes.offset_circle(cx, cy, radius, z0 + k * lh, arm=arm, tag=tag).ramp_z(lh)
            out.extend(lap if not out else lap[1:])
        return out


# =============================================================================
# 5. From paths to a timeline the machine can run
# =============================================================================
@dataclass
class Step:
    t: float
    dt: float
    phi_deg: float
    tt_speed: float                         # rad/s during this step
    arms: List[Optional[dict]]              # per arm: {x,y,z,roll,pitch,yaw,extrude,e_mm3,width,height,tilt} in ITS frame
    held: bool = False


class Timeline(list):
    def duration(self):
        return self[-1].t + self[-1].dt if self else 0.0

    def summary(self) -> str:
        if not self:
            return "empty"
        vol = [sum((a or {}).get("e_mm3", 0.0) for a in s.arms) for s in self]
        tt = sum(abs(s.tt_speed) * s.dt for s in self) / TAU
        busy = [sum(1 for s in self if s.arms[k] and s.arms[k]["extrude"]) / len(self) for k in range(len(self[0].arms))]
        return (f"{len(self)} steps, {self.duration() / 60:.1f} min, {sum(vol):.0f} mm^3, disc {tt:.1f} turns, "
                f"arm busy " + " / ".join(f"{b * 100:.0f}%" for b in busy))

    def to_csv(self, path: str):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            n = len(self[0].arms) if self else 0
            head = ["t", "dt", "phi_deg", "tt_speed"]
            for k in range(n):
                head += [f"a{k}_{c}" for c in ("x", "y", "z", "roll", "pitch", "yaw", "extrude", "e_mm3", "width", "height", "tilt")]
            w.writerow(head)
            for s in self:
                row = [f"{s.t:.4f}", f"{s.dt:.4f}", f"{s.phi_deg:.3f}", f"{s.tt_speed:.4f}"]
                for a in s.arms:
                    row += ["" for _ in range(11)] if a is None else [
                        f"{a['x']:.3f}", f"{a['y']:.3f}", f"{a['z']:.3f}", f"{a['roll']:.2f}", f"{a['pitch']:.2f}",
                        f"{a['yaw']:.2f}", int(a["extrude"]), f"{a['e_mm3']:.4f}", f"{a['width']:.2f}",
                        f"{a['height']:.2f}", f"{a['tilt']:.1f}"]
                w.writerow(row)

    def to_json(self, path: str):
        with open(path, "w") as f:
            json.dump([asdict(s) for s in self], f)


def _rpy_matrix(r, p, y):
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def _matrix_rpy(R):
    pitch = math.asin(max(-1.0, min(1.0, -R[2, 0])))
    if abs(R[2, 0]) < 0.99999:
        return math.atan2(R[2, 1], R[2, 2]), pitch, math.atan2(R[1, 0], R[0, 0])
    roll = math.atan2(-R[0, 1], R[1, 1]) if R[2, 0] < 0 else math.atan2(R[0, 1], R[1, 1])
    return roll, pitch, 0.0


def _axis_angle(axis, ang):
    axis = np.asarray(axis, float)
    n = np.linalg.norm(axis)
    if n < 1e-9 or abs(ang) < 1e-9:
        return np.eye(3)
    x, y, z = axis / n
    c, s, C = math.cos(ang), math.sin(ang), 1 - math.cos(ang)
    return np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                     [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                     [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


class Planner:
    """Paths (plate frame) -> Timeline. One path per arm, lock-step by index (pad a shorter
    one with None). mode "polar": the disc turns so arm 0's point faces arm 0 (the other
    arm's point must then lie at its own azimuth: build it 180 deg apart). mode "held":
    the disc is turned once so the path's centre faces the arm, then held."""

    def __init__(self, cell: Cell):
        self.cell = cell

    def _orientation(self, arm: Arm, tangent_world, tilt_deg):
        R = _rpy_matrix(math.radians(arm.roll), math.radians(arm.pitch), math.radians(arm.yaw))
        if abs(tilt_deg) > 1e-6:
            # lean about the bead tangent (expressed in the arm's frame)
            a = math.radians(arm.base_yaw_deg)
            c, s = math.cos(-a), math.sin(-a)
            tx, ty = c * tangent_world[0] - s * tangent_world[1], s * tangent_world[0] + c * tangent_world[1]
            R = _axis_angle((tx, ty, 0.0), math.radians(tilt_deg)) @ R
        r, p, y = _matrix_rpy(R)
        return math.degrees(r), math.degrees(p), math.degrees(y)

    def plan(self, paths: Sequence[Optional[Path]], mode: str = "polar") -> Timeline:
        cell, s, tt = self.cell, self.cell.settings, self.cell.turntable
        n = max(len(p) for p in paths if p)
        lanes = [list(p) + [None] * (n - len(p)) if p else [None] * n for p in paths]
        tl = Timeline()
        t = 0.0
        phi_prev = None
        prev_world = [None] * len(lanes)
        for k in range(n):
            pts = [lane[k] for lane in lanes]
            lead = next((i for i, p in enumerate(pts) if p is not None), None)
            if lead is None:
                continue
            arm0 = cell.arms[pts[lead].arm]
            if mode == "polar":
                phi = cell.facing_angle(pts[lead].theta, arm0)
            else:
                if phi_prev is None:
                    cx = sum(p.x for p in lanes[lead] if p) / max(1, sum(1 for p in lanes[lead] if p))
                    cy = sum(p.y for p in lanes[lead] if p) / max(1, sum(1 for p in lanes[lead] if p))
                    phi = cell.facing_angle(math.atan2(cy, cx), arm0) if math.hypot(cx, cy) > 1 else 0.0
                else:
                    phi = phi_prev
            if phi_prev is not None:                    # shortest way round
                phi = phi_prev + (phi - phi_prev + math.pi) % TAU - math.pi
            # time for this step: the slowest of (each arm's nozzle move at its speed, the disc)
            dphi = 0.0 if phi_prev is None else phi - phi_prev
            dt = abs(dphi) / tt.max_speed
            arms_out = []
            for i, p in enumerate(pts):
                if p is None:
                    arms_out.append(None)
                    continue
                arm = cell.arms[p.arm]
                w = cell.plate_to_world(p.xyz(), phi)
                if prev_world[i] is not None:
                    d = math.dist(w, prev_world[i])
                    speed = (s.print_speed if p.extrude else s.travel_speed)
                    dt = max(dt, d / max(speed, 1e-6), d / arm.max_speed)
                arms_out.append((arm, w, p))
            dt = max(dt, 1e-3)
            rows = []
            for i, item in enumerate(arms_out):
                if item is None:
                    rows.append(None)
                    continue
                arm, w, p = item
                # bead length in the PLATE frame decides the volume (what lands on the part)
                prev_p = lanes[i][k - 1] if k > 0 else None
                seg = math.dist(p.xyz(), prev_p.xyz()) if prev_p is not None else 0.0
                vol = seg * p.width * p.height * cell.extruders[arm.tool_index].flow if p.extrude and prev_p is not None else 0.0
                tang = (w[0] - prev_world[i][0], w[1] - prev_world[i][1]) if prev_world[i] is not None else (1.0, 0.0)
                roll, pitch, yaw = self._orientation(arm, tang, p.tilt)
                x, y, z = cell.world_to_arm(w, arm)
                rows.append({"x": x, "y": y, "z": z + (0.0 if p.extrude else s.travel_lift), "roll": roll, "pitch": pitch,
                             "yaw": yaw, "extrude": bool(p.extrude), "e_mm3": vol, "width": p.width, "height": p.height,
                             "tilt": p.tilt})
                prev_world[i] = w
            tl.append(Step(t, dt, math.degrees(phi), dphi / dt, rows, held=(mode != "polar")))
            t += dt
            phi_prev = phi
        return tl

    def check(self, tl: Timeline) -> List[str]:
        """Things the hardware would refuse: reach, tilt, nozzle separation, disc speed."""
        out = []
        cell = self.cell
        for s in tl:
            if abs(s.tt_speed) > cell.turntable.max_speed * 1.01:
                out.append(f"t {s.t:.1f}: disc {s.tt_speed:.2f} rad/s over the limit")
                break
        worlds = []
        for s in tl:
            ws = []
            for k, a in enumerate(s.arms):
                if a is None:
                    ws.append(None)
                    continue
                arm = cell.arms[k]
                if math.hypot(a["x"], a["y"]) > arm.reach_mm:
                    out.append(f"t {s.t:.1f}: {arm.name} arm out of reach ({math.hypot(a['x'], a['y']):.0f} mm)")
                    return out
                if abs(a["tilt"]) > arm.tilt_max_deg + 1e-6:
                    out.append(f"t {s.t:.1f}: {arm.name} tilt {a['tilt']:.0f} over {arm.tilt_max_deg:.0f}")
                    return out
                ang = math.radians(arm.base_yaw_deg)
                c, sn = math.cos(ang), math.sin(ang)
                ws.append((arm.base_xyz[0] + c * a["x"] - sn * a["y"], arm.base_xyz[1] + sn * a["x"] + c * a["y"]))
            if len(ws) == 2 and ws[0] and ws[1] and math.dist(ws[0], ws[1]) < cell.settings.min_nozzle_separation:
                out.append(f"t {s.t:.1f}: nozzles {math.dist(ws[0], ws[1]):.0f} mm apart (< {cell.settings.min_nozzle_separation:.0f})")
                return out
        return out


# =============================================================================
# 6. FullControl bridge
# =============================================================================
def to_fullcontrol(path: Path):
    """Path -> list of fullcontrol steps (Points with Extruder on/off and ExtrusionGeometry
    changes where the bead changes). Needs fullcontrol installed."""
    import fullcontrol as fc
    steps = []
    ext_on, geom = None, None
    for p in path:
        if p.extrude != ext_on:
            steps.append(fc.Extruder(on=p.extrude))
            ext_on = p.extrude
        if (p.width, p.height) != geom:
            steps.append(fc.ExtrusionGeometry(area_model="rectangle", width=p.width, height=p.height))
            geom = (p.width, p.height)
        steps.append(fc.Point(x=p.x, y=p.y, z=p.z))
    return steps


def from_fullcontrol(points, width=1.3, height=0.6, arm=0, tag="fc") -> Path:
    """A list of fullcontrol Points (e.g. fc.helixZ(...)) -> Path. Non-point steps are skipped;
    an Extruder(on=False) step turns the following points into travels."""
    out = Path()
    extrude = True
    for q in points:
        if hasattr(q, "on") and not hasattr(q, "x"):
            extrude = bool(q.on)
            continue
        if hasattr(q, "x") and q.x is not None:
            out.append(PathPoint(float(q.x), float(q.y), float(q.z or 0.0), width, height, 0.0, extrude, arm, tag))
    return out


# =============================================================================
# 7. Example
# =============================================================================
if __name__ == "__main__":
    cell = Cell()
    shapes, forms, planner = Shapes(cell), Forms(cell), Planner(cell)

    # a square drawn with the right arm on its radial axis while the disc turns
    sq = shapes.square(60.0, z=0.3, corner_r=4.0)
    tl = planner.plan([sq], mode="polar")
    print("square (polar, one-axis arm motion):", tl.summary())
    print("  first rows:", [(round(s.phi_deg, 1), round(s.arms[0]["x"], 1), round(s.arms[0]["y"], 1)) for s in tl[:4]])
    print("  checks:", planner.check(tl) or "ok")

    # the proven two-arm interwoven spiral on a 50 mm cylinder, 2 beads, 20 mm tall
    right, left = forms.dual_helix(lambda t: 50.0, z0=0.3, z1=20.3, beads=2)
    tl2 = planner.plan([right, left], mode="polar")
    print("dual helix cylinder:", tl2.summary())
    print("  checks:", planner.check(tl2) or "ok")

    # a lobed vase that flares, as one continuous helix with the nozzle leaning
    vase = forms.vase(profile=lambda z: 1.0 + 0.4 * z / 60.0, section=lambda t: 40.0 + 3.0 * math.sin(6 * t),
                      z0=0.3, z1=60.3)
    tl3 = planner.plan([vase], mode="polar")
    print("lobed flared vase:", tl3.summary(), "| checks:", planner.check(tl3) or "ok")

    # a post off the axis: disc faced to the arm and held, one continuous helix
    post = forms.post(30.0, 0.0, 5.0, 0.3, 12.3)
    tl4 = planner.plan([post], mode="held")
    print("post (held):", tl4.summary())

    tl2.to_csv("dual_helix_timeline.csv")
    print("wrote dual_helix_timeline.csv")
