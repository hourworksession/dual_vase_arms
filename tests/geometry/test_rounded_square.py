"""The rounded-square geometry, both ways of laying it: pure maths, no hardware."""
import math

from cell.runs import square_indexing as sq
from cell.runs import square_polar as sp


def _off_outline(p, S, R, d):
    x, y = abs(p[0]), abs(p[1])
    return abs(math.hypot(x - d, y - d) - R) if (x > d and y > d) else abs(max(x, y) - S)


def test_indexing_path_is_the_rounded_square_in_the_bed_frame():
    for bed_dir in (1, -1):
        g, _ = sq.parse(["T=0", "SIDE=80", "RF=10", f"BED_DIR={bed_dir}", "DR=0"])
        a = g["arms"][0]
        (cx, cy), n, t = sq.frame(a)
        S, R, d = a["S"], a["R"], a["D"]

        def to_bed(xy, total):
            dx, dy = xy[0] - cx, xy[1] - cy
            u, v = dx * n[0] + dy * n[1], dx * t[0] + dy * t[1]
            ph = -math.radians(bed_dir * total)
            return (u * math.cos(ph) - v * math.sin(ph), u * math.sin(ph) + v * math.cos(ph))

        pts, total = [], 0.0
        for k in range(4):
            for i in range(31):
                pts.append(to_bed(sq.straight_pt(g, a, 2 * d * i / 30, k, total), total))
            for i in range(0, 91, 3):
                pts.append(to_bed(sq.corner_pt(g, a, i, k, total + i), total + i))
            total += 90
        assert max(_off_outline(p, S, R, d) for p in pts) < 1e-9
        assert max(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) < 3.0
        assert math.dist(pts[0], pts[-1]) < 1e-9


def test_corner_progress_inverts_corner_pt_with_a_bed_residual():
    g, _ = sq.parse(["T=0", "SIDE=80", "RF=10", "DR=0"])
    a = g["arms"][0]
    for k in range(5):
        for res in (0.0, 3.7, -2.1):
            t_begin = 90 * k + res
            for b in (0.0, 10.0, 45.0, 80.0, 90.0):
                xy = sq.corner_pt(g, a, b, k, t_begin + b)
                assert abs(sq.corner_progress(g, a, xy, k, t_begin) - b) < 1e-9


def test_polar_radius_is_the_rounded_square():
    g, _ = sp.parse(["T=0", "SIDE=80", "RF=10", "DR=0"])
    a = g["arms"][0]
    S, R, d = a["S"], a["R"], a["D"]
    worst = 0.0
    for i in range(3600):
        th = i / 10
        r = sp.radius(a, th)
        worst = max(worst, _off_outline((r * math.cos(math.radians(th)), r * math.sin(math.radians(th))), S, R, d))
    assert worst < 1e-9


def test_polar_speed_profile_is_continuous_and_never_zero():
    g, _ = sp.parse(["T=0", "SIDE=80", "RF=10", "V=8"])
    a = g["arms"][0]
    w = [g["V"] / sp.ds_dtheta(a, i / 10) for i in range(900)]
    assert min(w) > 0.1 and max(w) < 0.25
    assert max(abs(w[i + 1] - w[i]) for i in range(len(w) - 1)) < 0.002
