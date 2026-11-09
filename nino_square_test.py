# Nino's square code for Orange Storm Giga printer setup (3 axis + 2 axis)




x0, y0 = CX - SIZE / 2, CY - SIZE / 2
x1, y1 = CX + SIZE / 2, CY + SIZE / 2

start = (CX, y0)                            # start in the middle of the bottom side → C = 0
loop = []                                   # one loop (x, y, c), excluding the start point
loop += side(start, (x1 - R, y0), 0)                      # right half of the bottom side
loop += corner((x1 - R, y0 + R), -90, 0)                  # bottom-right corner: C 0 → 90
loop += side((x1, y0 + R), (x1, y1 - R), 90)              # right side
loop += corner((x1 - R, y1 - R), 0, 90)                   # top-right corner   : C 90 → 180
loop += side((x1 - R, y1), (x0 + R, y1), 180)             # top side
loop += corner((x0 + R, y1 - R), 90, 180)                 # top-left corner    : C 180 → 270
loop += side((x0, y1 - R), (x0, y0 + R), 270)             # left side
loop += corner((x0 + R, y0 + R), 180, 270)                # bottom-left corner : C 270 → 360
loop += side((x0 + R, y0), start, 360)                    # left half of the bottom side
n_loop = len(loop)

# spiral from the first layer: z rises by LAYER_H per loop, C by 360° per loop (only in the corners)
steps = [Point(x=start[0], y=start[1], z=FIRST_LAYER_Z, c=0)]
for k, (x, y, c) in enumerate(loop * N_LAYERS, start=1):
    steps.append(Point(x=x, y=y, z=FIRST_LAYER_Z + LAYER_H * k / n_loop,
                       c=c + 360 * ((k - 1) // n_loop)))

# end: extrusion off, then lift 10 mm → rows with E = 0 tell the robot to stop
end_length = 10
n_end = int(end_length / STEP)
last = fc.last_point(steps)
steps.append(fc.Extruder(on=False))
for i in range(1, n_end + 1):
    steps.append(fc.relative_point(last, 0, 0, STEP * i))

print(f"Points per loop: {n_loop}, total: {len(steps)}, duration: {len(steps) * DT / 60:.1f} min")

# preview of the first 3 layers only (the full plot is too heavy)
#preview = steps[:3 * n_loop + 1] #remplacer steps par preview si tu veux observer que la partie du debut 
for step in steps:
    if isinstance(step, fc.Point):  # skip fc.Extruder(on=False), which has no C
        step.color = [(step.c % 360) / 360, 0, 1 - (step.c % 360) / 360]
fci.transform(steps, 'plot', fc.PlotControls(style='line', color_type='manual'))