"""Example: hollow cone, base on the disc, apex 80 mm up.

This is the part type the dual-spiral splitter most benefits from —
each layer is a circle, so the 180° offset is perfectly balanced.
"""
from __future__ import annotations

import math


def build_steps():
    import fullcontrol as fc

    base_r = 100.0
    apex_r = 5.0
    height = 80.0
    layer = 0.2
    seg_per_rev = 180

    steps: list = []
    n_layers = int(height / layer)
    for li in range(n_layers):
        z = (li + 1) * layer
        frac = z / height
        r = base_r * (1 - frac) + apex_r * frac
        for s in range(seg_per_rev + 1):
            theta = 2 * math.pi * s / seg_per_rev
            steps.append(fc.Point(x=r * math.cos(theta), y=r * math.sin(theta), z=z))

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
