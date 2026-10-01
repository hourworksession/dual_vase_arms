"""Polygon tower that twists layer by layer, as plain Python points (no FullControl needed). The planner keeps the sides straight while the turntable turns."""

import math

NAME = "Twisted polygon (plain Python)"
AUTHOR = "Examples"
PARAMS = {
    "sides": {"default": 6, "min": 3, "max": 24},
    "radius": {"default": 35.0, "unit": "mm", "min": 5, "max": 140, "help": "Corner radius"},
    "height": {"default": 25.0, "unit": "mm", "min": 0.5, "max": 200},
    "layer_height": {"default": 0.25, "unit": "mm", "min": 0.05, "max": 0.6},
    "twist": {"default": 120.0, "unit": "°", "help": "Total twist from bottom to top"},
}


def generate(sides, radius, height, layer_height, twist):
    layers = max(1, int(round(height / layer_height)))
    paths = []
    for layer in range(layers):
        z = layer_height * (layer + 1)
        rot = math.radians(twist) * layer / max(1, layers - 1)
        loop = [(radius * math.cos(2 * math.pi * k / sides + rot),
                 radius * math.sin(2 * math.pi * k / sides + rot), z) for k in range(sides + 1)]
        paths.append(loop)
    return {"paths": paths, "width": 0.45, "height": layer_height}
