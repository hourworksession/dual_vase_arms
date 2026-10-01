"""Two spirals built with FullControl, one per arm, for the dual-arm vase macro (defaults for the 1.2 mm Revo High Flow). Both start on the disc at opposite sides of the part (right at -45°, left at 135°, the centres of each arm's safe zone) and rise 2 layers per turn, so each arm lays every other layer. With antiphase waves the layers interweave."""

import math

import fullcontrol as fc

NAME = "Dual-arm interweave vase (FullControl)"
AUTHOR = "Macros"
PARAMS = {
    "radius": {"default": 30.0, "unit": "mm", "min": 5, "max": 140},
    "height": {"default": 20.0, "unit": "mm", "min": 0.5, "max": 200},
    "layer_height": {"default": 0.6, "unit": "mm", "min": 0.05, "max": 0.9,
                     "help": "Height of each layer; each arm's spiral rises two layers per turn"},
    "line_width": {"default": 1.3, "unit": "mm", "min": 0.2, "max": 2.5},
    "waves": {"default": 6, "unit": "/rev", "min": 0, "max": 60},
    "amplitude": {"default": 1.0, "unit": "mm", "min": 0, "max": 10},
    "antiphase": {"default": True, "help": "Left arm's waves opposite to the right's, so the layers interweave"},
    "segments": {"default": 180, "unit": "/rev", "min": 24, "max": 720},
    "right_start": {"default": -45.0, "unit": "°", "help": "Where the right arm starts on the disc"},
    "left_start": {"default": 135.0, "unit": "°", "help": "Where the left arm starts (opposite the right)"},
}


def spiral(start_deg, radius, height, layer_height, waves, amplitude, phase, segments):
    pitch = 2 * layer_height                       # every other layer
    turns = max(height / pitch, 1 / segments)
    # FullControl helix, clockwise so a positive turntable rotation feeds it under a fixed arm
    pts = fc.helixZ(fc.Point(x=0, y=0, z=layer_height), radius, radius, math.radians(start_deg),
                    turns, pitch, max(2, int(turns * segments)), cw=True)
    if amplitude:
        out = []
        for p in pts:
            pol = fc.point_to_polar(p, fc.Point(x=0, y=0, z=p.z))
            r = radius + amplitude * math.sin(waves * pol.angle + phase)
            out.append(fc.polar_to_point(fc.Point(x=0, y=0, z=p.z), r, pol.angle))
        pts = out
    return [(p.x, p.y, p.z) for p in pts]


def generate(radius, height, layer_height, line_width, waves, amplitude, antiphase, segments,
             right_start, left_start):
    right = spiral(right_start, radius, height, layer_height, waves, amplitude, 0.0, segments)
    left = spiral(left_start, radius, height, layer_height, waves, amplitude,
                  math.pi if antiphase else 0.0, segments)
    return {"paths": [{"points": right, "arm": 0},       # arm 0 = right (primary)
                      {"points": left, "arm": 1}],       # arm 1 = left
            "width": line_width, "height": layer_height}
