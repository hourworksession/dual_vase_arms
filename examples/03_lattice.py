"""Example: hexagonal lattice annulus (non-trivial topology).

This part exercises the handoff logic — the splitter cannot do a clean
180° split because the lattice cells span across the boundary, so the
Pivot-Move handoffs will be exercised.
"""
from __future__ import annotations

import math


def build_steps():
    import fullcontrol as fc

    inner_r = 80.0
    outer_r = 180.0
    layer = 0.2
    n_layers = 20
    n_rings = 6
    n_spokes = 24

    steps: list = []
    for li in range(n_layers):
        z = (li + 1) * layer
        for ri in range(n_rings + 1):
            r = inner_r + (outer_r - inner_r) * ri / n_rings
            for s in range(n_spokes + 1):
                theta = 2 * math.pi * s / n_spokes
                steps.append(fc.Point(x=r * math.cos(theta), y=r * math.sin(theta), z=z))
        for s in range(n_spokes):
            if (s + li) % 2:
                continue
            theta = 2 * math.pi * s / n_spokes
            steps.append(fc.Point(x=inner_r * math.cos(theta), y=inner_r * math.sin(theta), z=z))
            steps.append(fc.Point(x=outer_r * math.cos(theta), y=outer_r * math.sin(theta), z=z))

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
