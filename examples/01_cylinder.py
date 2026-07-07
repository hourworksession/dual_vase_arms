"""Example FullControl design: thin-walled cylinder on the disc.

Usage:
    python -m dual_arm_printer slice examples/01_cylinder.py -o build/cylinder.gcode
"""
from __future__ import annotations

import math


def build_steps():
    """Return (steps, settings) for FullControl.

    This function deliberately uses only the fullcontrol API and is the
    *only* place geometry is defined for this example. The rest of the
    pipeline never re-derives geometry — it always works from the G-code.
    """
    import fullcontrol as fc

    radius = 60.0       # mm — cylinder radius (centred on disc origin)
    height = 30.0       # mm — total wall height
    layer = 0.2         # mm — layer height
    seg_per_rev = 120   # discretisation of circle

    steps: list = []
    n_layers = int(height / layer)
    for li in range(n_layers):
        z = (li + 1) * layer
        for s in range(seg_per_rev + 1):
            theta = 2 * math.pi * s / seg_per_rev
            steps.append(
                fc.Point(x=radius * math.cos(theta), y=radius * math.sin(theta), z=z)
            )

    settings = {
        "extrusion_width": 0.45,
        "extrusion_height": layer,
        "nozzle_temp": 215,
        "bed_temp": 0,
        "print_speed": 1500,
        "travel_speed": 6000,
        "dia_feed": 1.75,
        "primer": "no_primer",
        "printer_name": "generic",
    }
    return steps, settings
