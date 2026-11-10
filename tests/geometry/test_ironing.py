"""Nino's ironing motifs and the zone compiler: pure maths."""
import math

from cell import ironing as ir, program as prg
from cell.runs.cylinder import DEFAULTS


def test_motifs_end_on_the_line_at_p_end():
    assert ir.line((0, 5), (10, 5), 1)[-1] == (10, 5)
    zz = ir.zigzag((0, 5), (10, 5), amp=2, pitch=0.5)
    assert zz[-1] == (10, 5) and max(z for _, z in zz) == 7
    sp = ir.spiral((0, 5), (10, 5), radius=1, pitch=2, n_seg=12)
    assert abs(sp[-1][0] - 10) < 1e-9 and abs(sp[-1][1] - 5) < 1e-9
    assert ir.motif_height(dict(motif="spiral", r=1)) == 2


def test_zone_compiles_to_bed_rotation_and_held_radius():
    arm = dict(DEFAULTS[1], T=1)
    r_face = 40 + arm["DR"]
    zones = [dict(pressure=0.3, x_start=-20, x_end=20, z_start=10, passes=range(0, 21, 10), motif="line", step=1)]
    p = ir.compile_zones(arm, r_face, zones, mode="bed", iron_speed=5)
    rows = prg.simulate(p, tick=0.1)
    radii = [math.hypot(r["arm1"][0] - arm["CX"], r["arm1"][1] - arm["CY"]) for r in rows if r["arm1"]]
    assert abs(min(radii) - (r_face - 0.3)) < 0.05           # pressing
    assert abs(max(radii) - (r_face + 5)) < 0.05             # safe distance
    span = max(r["bed_deg"] for r in rows) - min(r["bed_deg"] for r in rows)
    assert abs(span - math.degrees(40 / r_face)) < 1.0        # the zone's 40 mm as bed angle
    zs = [r["arm1"][2] - arm["Z0"] for r in rows if r["arm1"]]
    assert abs(min(zs) - 10) < 1e-6 and abs(max(zs) - 30) < 1e-6
    assert ir.check_zones(zones, 0.6, 25) == ["zone 1: ironing goes up to 30 mm, above the top of the wall (25 mm)"]


def test_arm_mode_moves_the_arm_along_the_arc_with_the_bed_still():
    arm = dict(DEFAULTS[1], T=1)
    r_face = 40 + arm["DR"]
    zones = [dict(pressure=0.3, x_start=-20, x_end=20, z_start=10, passes=[0], motif="line", step=1)]
    p = ir.compile_zones(arm, r_face, zones, mode="arm", iron_speed=5)
    assert "bed" not in p.tracks
    rows = prg.simulate(p, tick=0.1)
    pts = [r["arm1"] for r in rows if r["arm1"]]
    angs = [math.degrees(math.atan2(q[1] - arm["CY"], q[0] - arm["CX"])) for q in pts]
    assert abs((max(angs) - min(angs)) - math.degrees(40 / r_face)) < 1.0
    radii = [math.hypot(q[0] - arm["CX"], q[1] - arm["CY"]) for q in pts]
    assert abs(min(radii) - (r_face - 0.3)) < 0.05


def test_flat_face_is_a_straight_line_along_the_side_at_apothem_minus_pressure():
    arm = dict(DEFAULTS[1], T=1)
    apothem = 40 + arm["DR"]
    zones = [dict(pressure=0.3, x_start=-20, x_end=20, z_start=10, passes=[0], motif="line", step=1)]
    p = ir.compile_zones(arm, apothem, zones, face="flat", flat_half_width=30, iron_speed=5)
    rows = prg.simulate(p, tick=0.1)
    ang = math.radians(arm["ANGLE"])
    pts = [r["arm1"] for r in rows if r["arm1"]]
    outs = [(q[0] - arm["CX"]) * math.cos(ang) + (q[1] - arm["CY"]) * math.sin(ang) for q in pts]
    sides = [-(q[0] - arm["CX"]) * math.sin(ang) + (q[1] - arm["CY"]) * math.cos(ang) for q in pts]
    pressing = [sd for o, sd in zip(outs, sides) if abs(o - (apothem - 0.3)) < 1e-6]     # while on the side
    assert len(pressing) > 10
    assert abs(max(pressing) - 20) < 0.5 and abs(min(pressing) + 20) < 0.5               # straight along it, the zone's 40 mm
    assert abs(max(outs) - (apothem + 5)) < 1e-6                                          # safe distance between zones
    import pytest
    with pytest.raises(ValueError):
        ir.compile_zones(arm, apothem, zones, face="flat", flat_half_width=15)         # past the flat part
    with pytest.raises(ValueError):
        ir.compile_zones(arm, apothem, zones, face="flat", mode="bed")
