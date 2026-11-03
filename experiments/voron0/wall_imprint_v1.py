"""wall_imprint_v1 (copy of label_cup.py, 3 Nov 2026). Emboss text (or any image) into a spiral-vase wall, so every test part carries
its own ID (Voron 0.2, FullControl).

The image is wrapped round the cylinder and read as a height map: dark = raised. The nozzle
path moves out by up to RELIEF mm where the image is dark, the same way the rope cup makes
its strands. Two rules keep it printable:
  - a layer may only step OUT by MAX_STEP_OUT mm from the one below (the lower edges of
    letters get a small chamfer instead of an overhang); stepping in is always fine
  - detail is limited by the bead: about one layer (LH) vertically and about one bead width
    (WALL_W) round the wall, so letters should be at least ~15 layers tall

    pip install fullcontrol pillow
    python3 label_cup.py                                   # LABEL text below
    python3 label_cup.py --text "RUN 12  VB0.55  K1.3"     # other text
    python3 label_cup.py --image logo.png                  # any picture (dark = raised)

WHICH FACE MOVES (--mode). A bead has two faces; the path position moves both together,
the bead width (flow) moves them apart. With outer face R + W/2 + a and inner face
R - W/2 - b:   path radius = R + (a - b) / 2,   bead width = W + a + b
    shift    a = +img, b = -img   whole wall moves: raised outside, sunk inside (any depth)
    outer    a = img,  b = 0      only the outside moves; inside stays smooth
    litho    a = b = img / 2      both faces swell: a true lithophane (thickness), held up to light
    two      a = img,  b = inner  a different picture on each face (--inner-text / --inner-image)
The width modes are limited by the nozzle: width stays between W_MIN and W_MAX.

HOW THE WIDTH IS MADE (--drive), for the width modes:
    flow     constant speed, more plastic per mm where dark. The extruder has to change rate,
             and melt pressure lags (plus pressure advance smoothing), so fine detail blurs.
    speed    constant plastic per second, the head slows down where dark:
             speed = F_WALL x WALL_W / width. The extruder never changes rate, so the
             edges stay sharp (the motion system changes speed almost at once).
"""
import argparse
import glob
import math
import os

import fullcontrol as fc
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ---- printer / material (Voron 0.2, Revo 0.4, PETG) ----
NOZZLE_T, BED_T = 240, 80
CX, CY = 60.0, 60.0
FIRST_H, LH = 0.25, 0.20
BASE_W, WALL_W = 0.45, 0.50
F_FIRST, F_BASE, F_WALL = 20, 30, 25
FAN_PCT = 40
SEG = 0.3                       # path segment round the wall (mm): the horizontal pixel size

# ---- cup and label ----
R = 30.0
HEIGHT = 30.0                   # wall height above the base (mm)
LABEL = "RUN 01  3 NOV 26  PETG 240"
BAND_Z0 = 2.0                   # label band starts this far up the wall (mm)
BAND_H = 7.0                    # label band height (mm): text is ~70 % of it
WRAP = 0.75                     # fraction of the way round the cup the label may use
RELIEF = 0.6                    # how far the dark parts stand out (mm), shift mode
W_MIN, W_MAX = 0.40, 0.90       # bead width limits for the width modes (0.4 mm nozzle)
MAX_STEP_OUT = 0.15             # largest outward step from one layer to the next (mm)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def make_canvas(text=None, image=None, mirror=False):
    """Grey-scale height map, 0..1, one column per SEG mm round the wall, one row per layer."""
    cols = int(2 * math.pi * R / SEG)
    rows = int(round(BAND_H / LH))
    if image:
        img = ImageOps.grayscale(Image.open(image))
        w = int(cols * WRAP)
        h = rows
        img.thumbnail((w, h * 1000))                  # keep aspect: real mm, not pixels
        sx, sy = img.size
        sy_mm = sy * SEG                              # one pixel = SEG mm wide
        rows_needed = int(round(sy_mm / LH))
        img = img.resize((sx, min(rows, rows_needed)))
        art = ImageOps.invert(img)                    # dark = raised
    else:
        # draw the text at the band's real mm proportions, then squash rows to layers
        px_mm = 10                                    # drawing resolution: 10 px per mm
        hw, hh = int(2 * math.pi * R * WRAP * px_mm), int(BAND_H * px_mm)
        size = int(hh * 0.72)
        try:
            font = ImageFont.truetype(FONT, size)
        except OSError:
            font = ImageFont.load_default(size=size)
        while True:
            box = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), text, font=font)
            if box[2] - box[0] <= hw or size < 10:
                break
            size -= 2
            font = ImageFont.truetype(FONT, size) if os.path.exists(FONT) else ImageFont.load_default(size=size)
        tw, th = box[2] - box[0], box[3] - box[1]
        art = Image.new("L", (tw + 4 * px_mm, hh), 0)
        ImageDraw.Draw(art).text((2 * px_mm - box[0], (hh - th) // 2 - box[1]), text, fill=255, font=font)
        art = art.resize((int(art.size[0] / px_mm / SEG), rows), Image.BILINEAR)
    if mirror:                                        # read from inside the cup: mirror it
        art = ImageOps.mirror(art)
    canvas = Image.new("L", (cols, rows), 0)
    canvas.paste(art, ((cols - art.size[0]) // 2, (rows - art.size[1]) // 2))
    return canvas, cols, rows


def spiral_disc(z, r_out, pitch, inward):
    pts, r_in = [], pitch / 2
    turns = max(round((r_out - r_in) / pitch), 1)
    pitch = (r_out - r_in) / turns
    n = max(int(2 * math.pi * r_out * turns / 1.0), 50)
    for i in range(n + 1):
        f = i / n
        r = r_out - f * (r_out - r_in) if inward else r_in + f * (r_out - r_in)
        a = 2 * math.pi * turns * f
        pts.append(fc.Point(x=CX + r * math.cos(a), y=CY + r * math.sin(a), z=z))
    return pts


def build(canvas, cols, rows, mode="shift", inner=None, drive="flow", out="wall_imprint_v1"):
    px = canvas.load()
    pxi = inner.load() if inner is not None else None
    room = W_MAX - WALL_W                       # width the width modes may add
    steps = [fc.ManualGcode(text="M140 S%d\nM104 S170\nG28" % BED_T),
             fc.Buildplate(temp=BED_T, wait=True), fc.Hotend(temp=NOZZLE_T, wait=True), fc.Fan(speed_percent=0),
             fc.Extruder(on=False), fc.Point(x=5, y=10, z=5), fc.Point(z=FIRST_H),
             fc.ExtrusionGeometry(area_model="rectangle", width=0.6, height=FIRST_H),
             fc.Printer(print_speed=F_FIRST * 60, travel_speed=150 * 60),
             fc.Extruder(on=True), fc.Point(x=5, y=80), fc.Point(x=5.6, y=80), fc.Point(x=5.6, y=10),
             fc.Extruder(on=False)]
    z = FIRST_H
    b1 = spiral_disc(z, R, BASE_W, True)
    steps += [fc.Point(z=z + 1), fc.Point(x=b1[0].x, y=b1[0].y, z=z + 1), fc.Point(z=z),
              fc.ExtrusionGeometry(area_model="rectangle", width=BASE_W, height=FIRST_H), fc.Extruder(on=True)] + b1
    z += LH
    steps += [fc.Printer(print_speed=F_BASE * 60),
              fc.ExtrusionGeometry(area_model="rectangle", width=BASE_W, height=LH), fc.Point(z=z)]
    steps += spiral_disc(z, R, BASE_W, False)
    z += LH
    steps += [fc.Fan(speed_percent=FAN_PCT), fc.Printer(print_speed=F_WALL * 60),
              fc.ExtrusionGeometry(area_model="rectangle", width=WALL_W, height=LH), fc.Point(z=z)]
    for i in range(cols + 1):
        a = 2 * math.pi * i / cols
        steps.append(fc.Point(x=CX + R * math.cos(a), y=CY + R * math.sin(a), z=z))
    z0 = z
    prev_a = [0.0] * cols                       # each column's outer / inner face offsets one layer down
    prev_b = [0.0] * cols
    n = int(HEIGHT / LH * cols)
    raised = 0
    last_w = WALL_W
    for i in range(1, n + 1):
        t = i / cols
        c = i % cols
        zw = t * LH
        row = rows - 1 - int((zw - BAND_Z0) / LH)  # image row 0 = top of the band
        g = px[c, row] / 255 if 0 <= row < rows else 0.0
        gi = (pxi[c, row] / 255 if pxi is not None and 0 <= row < rows else 0.0)
        if mode == "shift":
            ta, tb = RELIEF * g, -RELIEF * g
        elif mode == "outer":
            ta, tb = room * g, 0.0
        elif mode == "litho":
            ta = tb = room * g / 2
        else:  # two
            ta, tb = room / 2 * g, room / 2 * gi
        # each face may only step outward (away from the wall) by MAX_STEP_OUT per layer
        fa = min(ta, prev_a[c] + MAX_STEP_OUT)
        fb = min(tb, prev_b[c] + MAX_STEP_OUT)
        prev_a[c], prev_b[c] = fa, fb
        raised += (fa + fb) > 0.05 or fa > 0.05
        w = min(max(WALL_W + fa + fb, W_MIN), W_MAX)
        if abs(w - last_w) > 1e-3:
            steps.append(fc.ExtrusionGeometry(width=w))
            if drive == "speed":                    # same mm3/s: slower where wider
                steps.append(fc.Printer(print_speed=F_WALL * 60 * WALL_W / w))
            last_w = w
        a = 2 * math.pi * t
        r = R + (fa - fb) / 2
        steps.append(fc.Point(x=CX + r * math.cos(a), y=CY + r * math.sin(a), z=z0 + zw))
    steps += [fc.Extruder(on=False), fc.Point(z=min(z0 + HEIGHT + 10, 115)), fc.Point(x=CX, y=115),
              fc.ManualGcode(text="M104 S0\nM140 S0\nM107\nM84")]
    fc.transform(steps, "gcode", fc.GcodeControls(
        printer_name="generic", save_as=out,
        initialization_data={"primer": "no_primer", "dia_feed": 1.75, "relative_e": True,
                             "area_model": "rectangle", "extrusion_width": WALL_W, "extrusion_height": LH,
                             "print_speed": F_WALL * 60, "travel_speed": 150 * 60}), show_tips=False)
    for f in glob.glob(out + "__*.gcode"):
        os.replace(f, out + ".gcode")
    return raised


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=LABEL)
    ap.add_argument("--image")
    ap.add_argument("--mode", default="shift", choices=["shift", "outer", "litho", "two"])
    ap.add_argument("--inner-text", default="INSIDE")
    ap.add_argument("--inner-image")
    ap.add_argument("--drive", default="flow", choices=["flow", "speed"])
    args = ap.parse_args()
    canvas, cols, rows = make_canvas(text=None if args.image else args.text, image=args.image)
    canvas.save("label_canvas.png")                   # what will be embossed (unrolled)
    inner = None
    if args.mode == "two":
        inner = make_canvas(text=None if args.inner_image else args.inner_text,
                            image=args.inner_image, mirror=True)[0]
        inner.save("label_canvas_inner.png")
    raised = build(canvas, cols, rows, mode=args.mode, inner=inner, drive=args.drive)
    print(f"Label band {BAND_Z0:g}-{BAND_Z0 + BAND_H:g} mm up the wall, {rows} layers x {cols} columns "
          f"({SEG} mm each); mode {args.mode}. Wrote wall_imprint_v1.gcode and label_canvas.png")
