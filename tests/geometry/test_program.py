"""Speed-and-time programs: pure maths, no hardware."""
import math

from cell import program as prg, shapes
from cell.runs.cylinder import DEFAULTS


def test_tracks_step_by_time():
    p = prg.Program("t")
    p.track("bed").add(10, 2).add(20, 3)
    assert p.total() == 5
    assert p.tracks["bed"].at(1.0)[1].value == 10
    assert p.tracks["bed"].at(2.5)[1].value == 20
    assert p.tracks["bed"].at(5.1)[0] is None
    assert prg.Program.from_dict(p.to_dict()).to_dict() == p.to_dict()


def test_rounded_square_compiles_to_the_outline_in_the_bed_frame():
    arm = dict(DEFAULTS[0], DR=0.0)
    p = shapes.rounded_square_program({0: arm}, 80, 10, V=8, W=1.0, L_H=0.6, loops=2)
    rows = prg.simulate(p)
    path = shapes.bed_frame_path(rows, arm, 0)
    S, R, d = 40, 10, 30

    def off(q):
        x, y = abs(q[0]), abs(q[1])
        return abs(math.hypot(x - d, y - d) - R) if (x > d and y > d) else abs(max(x, y) - S)

    body = [q for q, r in zip(path, [r for r in rows if r["arm0"]]) if 6 < r["t"] < p.total() - 2.5]
    assert max(off(q) for q in body) < 0.1
    assert abs(shapes.outline_length(shapes.rounded_square(80, 10)) - 302.8) < 0.2


def test_polygon_n4_is_the_square_and_bed_never_stops():
    sq, p4 = shapes.rounded_square(80, 10), shapes.rounded_polygon(4, 80, 10)
    assert max(abs(sq(i / 10) - p4(i / 10)) for i in range(3600)) < 1e-9
    p = shapes.rounded_polygon_program({0: dict(DEFAULTS[0])}, 6, 60, 8, V=8, W=1.0, L_H=0.6, loops=1)
    assert min(s.value for s in p.tracks["bed"].segs) > 0
