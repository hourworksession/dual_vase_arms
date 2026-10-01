"""A FullControl script written the usual way: build steps, then call fc.transform. Cell Studio runs it unchanged; its top-level numbers appear as parameters."""

import fullcontrol as fc

RADIUS = 25.0
SIDES = 5
LAYERS = 60
LAYER_HEIGHT = 0.25
LINE_WIDTH = 0.5
TWIST_PER_LAYER = 1.5            # degrees

steps = [fc.ExtrusionGeometry(area_model="rectangle", width=LINE_WIDTH, height=LAYER_HEIGHT)]
for layer in range(LAYERS):
    z = LAYER_HEIGHT * (layer + 1)
    start = layer * TWIST_PER_LAYER * 3.141592653589793 / 180
    steps += fc.polygonXY(fc.Point(x=50, y=50, z=z), RADIUS, start, SIDES)

fc.transform(steps, "plot")       # in Cell Studio this hands the steps over instead of plotting
