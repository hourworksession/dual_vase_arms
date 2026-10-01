"""Two interleaved helices, one per arm, on opposite sides of the part: each arm lays every other layer and the waves are in antiphase. Plan it with Arms = 2. Printing with two arms is not wired into the streamer yet, so use the preview and dry run."""

import math

NAME = "Interweave, two arms"
AUTHOR = "Examples"
PARAMS = {
    "radius": {"default": 40.0, "unit": "mm", "min": 10, "max": 140},
    "height": {"default": 15.0, "unit": "mm", "min": 1, "max": 200},
    "layer_height": {"default": 0.3, "unit": "mm", "min": 0.05, "max": 0.6},
    "waves": {"default": 8, "unit": "/rev", "min": 0, "max": 60},
    "amplitude": {"default": 1.2, "unit": "mm", "min": 0, "max": 10},
    "segments": {"default": 180, "unit": "/rev", "min": 24, "max": 720},
}


def generate(radius, height, layer_height, waves, amplitude, segments):
    pitch = 2 * layer_height                    # each arm prints every other layer
    turns = height / pitch
    paths = []
    for arm in (0, 1):
        pts = []
        for i in range(int(turns * segments) + 1):
            t = i / segments
            a = 2 * math.pi * t + arm * math.pi      # start on opposite sides
            z = layer_height * (1 + arm) + pitch * t
            r = radius + amplitude * math.sin(waves * a + arm * math.pi)
            pts.append((r * math.cos(a), r * math.sin(a), z))
        paths.append({"points": pts, "arm": arm})
    return {"paths": paths, "width": 0.5, "height": layer_height}
