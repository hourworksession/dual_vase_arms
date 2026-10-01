# Generators

Python (or G-code) that makes a toolpath. Everything in this folder appears in
Cell Studio ▸ **Generators**, where you set its parameters, preview it in 3D,
and then plan, dry-run and print it with the same planner as Model print.

Put each person's work in their own folder (`george/`, `wil/`, ...). You can also
add a folder that lives elsewhere (e.g. a cloned repo) with **Add folder…**.

## Four ways to write one

1. **`generate()` function** (recommended, see `TEMPLATE.py`)

   ```python
   NAME = "My vase"
   PARAMS = {"radius": {"default": 30.0, "unit": "mm", "min": 5, "max": 140}}

   def generate(radius):
       return steps   # FullControl steps, OR a list of paths, OR a G-code string
   ```

   `PARAMS` entries become fields in the panel. Shorthand works too:
   `"layers": 50` or `"radius": (30.0, "mm")`. Without `PARAMS`, the defaults of
   `generate()`'s arguments are used.

2. **An ordinary FullControl script.** Build `steps` and call
   `fc.transform(steps, 'gcode')` or `'plot'` as usual. Cell Studio catches the
   steps (no browser window opens). Top-level numbers such as `RADIUS = 25` or
   `layers = 40` become editable parameters automatically.

3. **`build_steps()`** returning `(steps, settings)`. This is the older convention in
   `src/dual_arm_printer/slicing/fullcontrol_runner.py`.

4. **A `.gcode` file** from any slicer or script. Line widths are worked out from
   the E values.

## Plain points (no FullControl needed)

A path is a list of points `(x, y, z)` or `(x, y, z, width, height)` in mm.
Return one path, a list of paths, or a dict:

```python
return {"paths": [
            {"points": [(x, y, z), ...], "arm": 0},       # "arm" pins a path to an arm
            {"points": [...], "extrude": False},          # a travel (keeps z-hops)
        ],
        "width": 0.45, "height": 0.2}                     # defaults for points without w, h
```

## Coordinates

Millimetres, model frame, with z = 0 on the disc surface. FullControl designs
usually start at z = layer height. **Centre the part on the turntable axis** is on
by default, so a design centred at (50, 50) still lands in the middle of the disc.
The disc radius is 150 mm.

## Safety

Generators run in a separate process with a time limit, and never get access to
the arms. They do run with your user's rights on this PC, so only add code you
trust.
