"""Spiral vase with twisting radial ripples, built with FullControl (Gleadall, 2021). Shows the generate() style returning FullControl steps."""

import math

import fullcontrol as fc

NAME = "Ripple vase (FullControl)"
AUTHOR = "Examples"
PARAMS = {
    "radius": {"default": 30.0, "unit": "mm", "min": 5, "max": 140, "help": "Mean radius"},
    "height": {"default": 30.0, "unit": "mm", "min": 1, "max": 200},
    "layer_height": {"default": 0.25, "unit": "mm", "min": 0.05, "max": 0.6},
    "line_width": {"default": 0.5, "unit": "mm", "min": 0.2, "max": 1.5},
    "ripples": {"default": 12, "unit": "/rev", "min": 0, "max": 60},
    "ripple_depth": {"default": 1.5, "unit": "mm", "min": 0, "max": 10},
    "twist": {"default": 90.0, "unit": "°", "help": "How far the ripples twist over the full height"},
    "segments": {"default": 128, "unit": "/rev", "min": 16, "max": 720},
}


def generate(radius, height, layer_height, line_width, ripples, ripple_depth, twist, segments):
    steps = [fc.ExtrusionGeometry(area_model="rectangle", width=line_width, height=layer_height)]
    turns = height / layer_height
    for i in range(int(turns * segments) + 1):
        t = i / segments                                   # revolutions so far
        a = 2 * math.pi * t
        z = layer_height * (1 + t)
        tw = math.radians(twist) * (z / height)
        r = radius + ripple_depth * math.sin(ripples * a + tw)
        steps.append(fc.Point(x=r * math.cos(a), y=r * math.sin(a), z=z))
    return steps
