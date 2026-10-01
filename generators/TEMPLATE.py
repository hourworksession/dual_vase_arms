"""Copy this file, rename it, and edit generate(). The first paragraph of this
docstring is shown as the description in Cell Studio."""

import math

NAME = "Template: wavy cylinder"
AUTHOR = ""                      # defaults to the folder name
PARAMS = {
    # name: default, or (default, unit), or a dict like these
    "radius": {"default": 30.0, "unit": "mm", "min": 5, "max": 140, "help": "Mean radius"},
    "height": {"default": 20.0, "unit": "mm", "min": 0.5, "max": 200},
    "layer_height": {"default": 0.2, "unit": "mm", "min": 0.05, "max": 0.6},
    "waves": {"default": 6, "unit": "/rev", "min": 0, "max": 60},
    "amplitude": {"default": 2.0, "unit": "mm", "min": 0, "max": 20},
    "spiral": {"default": True, "help": "One continuous spiral (vase mode) instead of separate layers"},
}


def generate(radius, height, layer_height, waves, amplitude, spiral):
    """Return a list of paths; each path is a list of (x, y, z) points in mm.
    (You can also return FullControl steps or a G-code string.)"""
    segments = 180
    layers = max(1, int(round(height / layer_height)))
    if spiral:
        pts = []
        for i in range(layers * segments + 1):
            t = i / segments                         # revolutions so far
            a = 2 * math.pi * t
            r = radius + amplitude * math.sin(waves * a)
            pts.append((r * math.cos(a), r * math.sin(a), layer_height * (1 + t)))
        return [pts]
    paths = []
    for layer in range(layers):
        z = layer_height * (layer + 1)
        pts = []
        for i in range(segments + 1):
            a = 2 * math.pi * i / segments
            r = radius + amplitude * math.sin(waves * a)
            pts.append((r * math.cos(a), r * math.sin(a), z))
        paths.append(pts)
    return paths
