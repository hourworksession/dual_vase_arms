"""G-code parser + reconstructor tests."""
from __future__ import annotations

from pathlib import Path

from dual_arm_printer.slicing.gcode_parser import parse_gcode
from dual_arm_printer.slicing.reconstructor import reconstruct

FIX = Path(__file__).parent / "fixtures"


def test_parses_basic_moves(tmp_path):
    src = tmp_path / "tiny.gcode"
    src.write_text(
        """
        ; tiny test
        G21
        G90
        G1 X0 Y0 Z0.2 F1500
        G1 X10 Y0 E0.5
        G1 X10 Y10 E1.0
        G0 X0 Y0 Z5
        """.strip()
    )
    prog = parse_gcode(src)
    assert len(prog.moves) == 4
    g = reconstruct(prog)
    assert len(g.segments) >= 2
    # First two extrusion segments
    extrudes = [s for s in g.segments if not s.is_travel]
    assert len(extrudes) >= 2
    assert extrudes[0].length_mm > 0


def test_layer_partition(tmp_path):
    src = tmp_path / "two_layers.gcode"
    src.write_text(
        """
G1 X0 Y0 Z0.2 F1500
G1 X5 Y0 E0.1
G1 X5 Y5 E0.2
G1 X0 Y5 Z0.4 E0.3
G1 X0 Y0 E0.4
        """.strip()
    )
    prog = parse_gcode(src)
    g = reconstruct(prog)
    assert len(g.layers) >= 2
