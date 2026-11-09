"""Toolpaths from code: FullControl designs, G-code and plain Python points.

Every generator, whoever wrote it, ends up as the same thing: an ordered list of
paths. Each path is a list of points (x, y, z, width, height) in mm, in the
model frame, and is either extruding or a travel. That is what the planner
takes (planner.py accepts 3D/5-value points), so a FullControl vase, a G-code
file from another slicer and a list of points from someone's own script all
plan, preview and print the same way as a sliced model.

    toolpath = {"paths": [{"p": [[x, y, z, w, h], ...], "e": True, "arm": None}, ...],
                "source": "fullcontrol" | "gcode" | "points",
                "notes": ["..."]}

Nothing here touches the machine. adapt() runs inside the generator subprocess
(gen_runner.py); validate() and to_slice_result() run in the panel.
"""

import math
import re

ROUND = 4
DISC_RADIUS = 150.0          # ADRS acrylic disc, 300 mm diameter
MAX_POINTS = 3_000_000
WARN_POINTS = 300_000


class GeneratorError(Exception):
    """A problem with what a generator returned, explained for the person who wrote it."""


# ---------------------------------------------------------------- helpers
def _is_fc_object(x):
    return type(x).__module__.startswith("fullcontrol")


def _contains_fc(obj, depth=0):
    if depth > 6:
        return False
    if _is_fc_object(obj):
        return True
    if isinstance(obj, (list, tuple)):
        return any(_contains_fc(o, depth + 1) for o in obj[:2000])
    return False


def _flatten(obj):
    out = []
    stack = [iter([obj])]
    while stack:
        try:
            x = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if isinstance(x, (list, tuple)):
            stack.append(iter(x))
        else:
            out.append(x)
    return out


def _finite(*vals):
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


def _path(points, extrude=True, arm=None):
    return {"p": [[round(float(c), ROUND) for c in pt] for pt in points], "e": bool(extrude),
            "arm": None if arm is None else int(arm)}


# ---------------------------------------------------------------- FullControl
def from_fullcontrol(steps, controls=None, transform=None):
    """Use FullControl's own plot transform, so its state rules (extruder on/off,
    ExtrusionGeometry, relative points...) are applied exactly as in its preview."""
    import fullcontrol as fc
    transform = transform or fc.transform
    flat = _flatten(steps)
    if not flat:
        raise GeneratorError("The FullControl step list is empty.")
    pc = fc.PlotControls(raw_data=True)
    notes = []
    if controls is not None:
        for attr in ("printer_name", "initialization_data"):
            val = getattr(controls, attr, None)
            if val is not None:
                setattr(pc, attr, val)
    area_model = "rectangle"
    if pc.initialization_data and pc.initialization_data.get("area_model"):
        area_model = pc.initialization_data["area_model"]
    for st in flat:
        if type(st).__name__ == "ExtrusionGeometry" and getattr(st, "area_model", None):
            area_model = st.area_model
    pd = transform(flat, "plot", pc, show_tips=False)
    paths = []
    for p in pd.paths:
        n = len(p.xvals)
        if n < 2:
            continue
        ws = list(p.widths) if getattr(p, "widths", None) else [None] * n
        hs = list(p.heights) if getattr(p, "heights", None) else [None] * n
        pts = []
        for i in range(n):
            w, h = ws[i] if i < len(ws) else None, hs[i] if i < len(hs) else None
            w = 0.4 if w is None else float(w)
            h = 0.2 if h is None else float(h)
            if area_model == "stadium" and h > 0:          # same area, as a rectangle of height h
                w = ((w - h) * h + math.pi * (h / 2) ** 2) / h
            pts.append((p.xvals[i], p.yvals[i], p.zvals[i], w, h))
        paths.append(_path(pts, extrude=bool(p.extruder.on)))
    if area_model != "rectangle":
        notes.append(f"FullControl area model '{area_model}' converted to an equivalent rectangle.")
    return {"paths": paths, "source": "fullcontrol", "notes": notes}


# ---------------------------------------------------------------- G-code
_WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


def from_gcode(text, layer_height=0.2, filament_diameter=1.75, arc_seg=0.5):
    """Parse G0/G1 (and G2/G3 arcs) with absolute or relative XYZ (G90/G91) and
    E (M82/M83), G92 resets. Width per segment comes from the filament used:
    w = dE * filament_area / (length * h), where h is the file's own layer height
    (from its Z steps) or `layer_height` for spiral G-code with no distinct layers."""
    area = math.pi * (filament_diameter / 2) ** 2
    x = y = z = 0.0
    e = 0.0
    rel_xyz = False
    rel_e = False
    segs = []                                   # (start, end, de)
    seen_move = False

    def seg_to(nx, ny, nz, de):
        nonlocal x, y, z
        segs.append(((x, y, z), (nx, ny, nz), de))
        x, y, z = nx, ny, nz

    for raw in text.splitlines():
        line = raw.split(";", 1)[0].split("(", 1)[0].strip()
        if not line:
            continue
        words = {}
        cmd = None
        for letter, val in _WORD.findall(line):
            L = letter.upper()
            if cmd is None and L in "GM":
                cmd = f"{L}{int(float(val))}"
            else:
                words[L] = float(val)
        if cmd is None:
            continue
        if cmd == "G90":
            rel_xyz = False
        elif cmd == "G91":
            rel_xyz = True
        elif cmd == "M82":
            rel_e = False
        elif cmd == "M83":
            rel_e = True
        elif cmd == "G92":
            if "E" in words:
                e = words["E"]
            x, y, z = words.get("X", x), words.get("Y", y), words.get("Z", z)
        elif cmd in ("G0", "G1", "G2", "G3"):
            def target(axis, cur_v):
                if axis not in words:
                    return cur_v
                return cur_v + words[axis] if rel_xyz else words[axis]
            nx, ny, nz = target("X", x), target("Y", y), target("Z", z)
            if "E" in words:
                de = words["E"] if rel_e else words["E"] - e
                e = e + words["E"] if rel_e else words["E"]
            else:
                de = 0.0
            if cmd in ("G2", "G3") and ("I" in words or "J" in words):
                cx, cy = x + words.get("I", 0.0), y + words.get("J", 0.0)
                r = math.hypot(x - cx, y - cy)
                a0 = math.atan2(y - cy, x - cx)
                a1 = math.atan2(ny - cy, nx - cx)
                sweep = a1 - a0
                if cmd == "G2" and sweep >= 0:
                    sweep -= 2 * math.pi
                if cmd == "G3" and sweep <= 0:
                    sweep += 2 * math.pi
                n = max(2, int(abs(sweep) * r / arc_seg))
                z0 = z
                for k in range(1, n + 1):
                    t = k / n
                    a = a0 + sweep * t
                    seg_to(cx + r * math.cos(a), cy + r * math.sin(a), z0 + (nz - z0) * t, de / n)
            else:
                if (nx, ny, nz) == (x, y, z):
                    continue          # stationary extrusion (priming): no path
                seg_to(nx, ny, nz, de)
            seen_move = True
    if not seen_move:
        raise GeneratorError("No G0/G1 moves found in this G-code.")

    # layer heights from the distinct Z levels of extruding moves
    ext_z = [round(b[2], 4) for a, b, de in segs if de > 1e-7 and abs(a[2] - b[2]) < 1e-6]
    levels = sorted(set(ext_z))
    notes = []
    h_of = {}
    if levels and len(levels) <= 0.2 * max(1, len(ext_z)):
        steps = [b - a for a, b in zip(levels[:-1], levels[1:])]
        h_main = sorted(steps)[len(steps) // 2] if steps else (levels[0] if levels[0] > 0 else layer_height)
        for i, zl in enumerate(levels):
            h = (zl - levels[i - 1]) if i else zl
            h_of[zl] = h if 0.03 <= h <= 2.0 else h_main
        notes.append(f"Line widths estimated from E with the file's layer height ({h_main:.3f} mm) and "
                     f"{filament_diameter} mm filament.")
    else:
        notes.append(f"No distinct layers (spiral G-code?): widths estimated from E with a {layer_height} mm "
                     f"layer height (Defaults) and {filament_diameter} mm filament.")

    paths = []
    cur, cur_ext = [], None
    for a, b, de in segs:
        length = math.dist(a, b)
        extruding = de > 1e-7 and length > 1e-6
        h = h_of.get(round(b[2], 4), layer_height)
        w = (de * area / (length * h)) if extruding else 0.4
        if cur_ext is None or extruding != cur_ext:
            if len(cur) >= 2:
                paths.append(_path(cur, cur_ext))
            cur = [cur[-1]] if cur else [(a[0], a[1], a[2], w, h)]
            cur_ext = extruding
        cur.append((b[0], b[1], b[2], w, h))
    if len(cur) >= 2:
        paths.append(_path(cur, cur_ext))
    return {"paths": paths, "source": "gcode", "notes": notes}


def looks_like_gcode(text):
    return bool(re.search(r"^\s*G0?[0-3]\b", text, re.M | re.I))


# ---------------------------------------------------------------- plain points
def _point(pt, default_w, default_h, where):
    if hasattr(pt, "tolist"):
        pt = pt.tolist()
    if isinstance(pt, dict):
        pt = (pt.get("x"), pt.get("y"), pt.get("z"), pt.get("w", pt.get("width")), pt.get("h", pt.get("height")))
    if not isinstance(pt, (list, tuple)) or len(pt) < 3:
        raise GeneratorError(f"{where}: each point needs x, y and z (got {pt!r}).")
    x, y, z = pt[0], pt[1], pt[2]
    w = pt[3] if len(pt) > 3 and pt[3] is not None else default_w
    h = pt[4] if len(pt) > 4 and pt[4] is not None else default_h
    if not _finite(x, y, z, w, h):
        raise GeneratorError(f"{where}: point {pt!r} is not a set of finite numbers.")
    return (x, y, z, w, h)


def from_points(obj, default_w=0.4, default_h=0.2):
    """Accepts:
      * {"paths": [...], "width": .., "height": ..}
      * a list of paths, where a path is a list of (x, y, z[, w, h]) points, a numpy
        N×3 / N×5 array, or a dict {"points": [...], "extrude": True, "arm": 0,
        "width": .., "height": ..}
      * a single path (list of points)
    """
    if hasattr(obj, "tolist") and not isinstance(obj, (list, dict)):
        obj = obj.tolist()
    if isinstance(obj, dict):
        default_w = obj.get("width", default_w)
        default_h = obj.get("height", default_h)
        obj = obj.get("paths", obj.get("points"))
        if obj is None:
            raise GeneratorError("The dict returned has no 'paths' (or 'points') entry.")
    if not isinstance(obj, (list, tuple)) or not obj:
        raise GeneratorError("generate() returned nothing to print (an empty list or None).")
    first = obj[0]
    if hasattr(first, "tolist"):
        first = first.tolist()
    single = isinstance(first, (list, tuple)) and len(first) in (3, 5) and all(
        isinstance(c, (int, float)) for c in first)
    if single or isinstance(first, dict) and "x" in first:
        obj = [obj]
    paths = []
    for i, p in enumerate(obj):
        extrude, arm, w, h = True, None, default_w, default_h
        if isinstance(p, dict):
            extrude = p.get("extrude", True)
            arm = p.get("arm")
            w, h = p.get("width", w), p.get("height", h)
            p = p.get("points", p.get("p"))
        if hasattr(p, "tolist"):
            p = p.tolist()
        if not isinstance(p, (list, tuple)):
            raise GeneratorError(f"path {i}: expected a list of points, got {type(p).__name__}.")
        pts = [_point(pt, w, h, f"path {i}") for pt in p]
        if len(pts) >= 2:
            paths.append(_path(pts, extrude, arm))
    return {"paths": paths, "source": "points", "notes": []}


# ---------------------------------------------------------------- dispatch
def adapt(result, defaults, fc_transform=None, fc_controls=None):
    """Turn whatever a generator returned into a toolpath dict."""
    w, h = defaults.get("line_width", 0.4), defaults.get("layer_height", 0.2)
    if result is None:
        raise GeneratorError("generate() returned None. Return the FullControl steps, a list of "
                             "paths, or a G-code string.")
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict) and _contains_fc(result[0]):
        import fullcontrol as fc
        ctl = fc.GcodeControls(initialization_data=result[1])
        return from_fullcontrol(result[0], ctl, fc_transform)
    if isinstance(result, str):
        if looks_like_gcode(result):
            return from_gcode(result, h, defaults.get("filament_diameter", 1.75))
        raise GeneratorError("generate() returned text that is not G-code.")
    if _contains_fc(result):
        return from_fullcontrol(result, fc_controls, fc_transform)
    return from_points(result, w, h)


# ---------------------------------------------------------------- checks
def bounds(tp, extruding_only=True):
    xs, ys, zs = [], [], []
    for p in tp["paths"]:
        if extruding_only and not p["e"]:
            continue
        for pt in p["p"]:
            xs.append(pt[0]); ys.append(pt[1]); zs.append(pt[2])
    if not xs:
        return None
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def placement_shift(tp, centre):
    """XY shift that puts the middle of the part on the turntable axis (if centre)."""
    b = bounds(tp)
    if not centre or b is None:
        return 0.0, 0.0
    return -(b[0][0] + b[1][0]) / 2, -(b[0][1] + b[1][1]) / 2


def validate(tp, shift=(0.0, 0.0), offset=(0.0, 0.0), filament_diameter=1.75,
             disc_radius=DISC_RADIUS, first_layer_h=0.2):
    """Return (errors, warnings, stats). Errors block planning."""
    errors, warnings = [], []
    paths = tp.get("paths", [])
    n_pts = sum(len(p["p"]) for p in paths)
    ext = [p for p in paths if p["e"]]
    stats = {"paths": len(ext), "travels": len(paths) - len(ext), "points": n_pts}
    if n_pts > MAX_POINTS:
        errors.append(f"{n_pts:,} points is more than the {MAX_POINTS:,} limit. Use fewer segments.")
        return errors, warnings, stats
    if not ext:
        errors.append("Nothing is extruded: every move is a travel (is the extruder ever on?).")
        return errors, warnings, stats
    length = 0.0
    fil = 0.0
    area = math.pi * (filament_diameter / 2) ** 2
    rmax = 0.0
    wmin = hmin = 1e9
    wmax = hmax = 0.0
    dx, dy = shift[0] + offset[0], shift[1] + offset[1]
    for p in ext:
        pts = p["p"]
        for a, b in zip(pts[:-1], pts[1:]):
            s = math.dist(a[:3], b[:3])
            length += s
            fil += s * b[3] * b[4] / area
        for pt in pts:
            rmax = max(rmax, math.hypot(pt[0] + dx, pt[1] + dy))
            wmin, wmax = min(wmin, pt[3]), max(wmax, pt[3])
            hmin, hmax = min(hmin, pt[4]), max(hmax, pt[4])
    (x0, y0, z0), (x1, y1, z1) = bounds(tp)
    size = max(x1 - x0, y1 - y0, z1 - z0)
    stats.update(length_m=length / 1000, filament_m=fil / 1000,
                 grams=fil * area * 1.24e-3,            # PLA, 1.24 g/cm³

                 size=(x1 - x0, y1 - y0, z1 - z0), zmin=z0, zmax=z1, rmax=rmax,
                 width=(wmin, wmax), height=(hmin, hmax))
    if size < 2:
        warnings.append(f"The whole part is only {size:.3f} mm across. Are the units metres instead of mm?")
    if size > 1000:
        errors.append(f"The part is {size:.0f} mm across, far bigger than the cell. Check the units.")
    if z0 < 0:
        errors.append(f"Extrusion goes down to z = {z0:.2f} mm, below the turntable surface.")
    elif z0 < 0.5 * min(first_layer_h, hmin if hmin < 1e8 else first_layer_h):
        warnings.append(f"The first extrusion is at z = {z0:.2f} mm: the nozzle would almost touch the "
                        "disc. FullControl designs normally start at z = layer height.")
    if rmax > disc_radius:
        errors.append(f"The toolpath reaches {rmax:.0f} mm from the turntable axis; the disc radius is "
                      f"{disc_radius:.0f} mm. Turn on 'Centre on turntable', or move or shrink the part.")
    if wmin < 0.1 or wmax > 3.0:
        warnings.append(f"Line width ranges {wmin:.2f} to {wmax:.2f} mm (expected about 0.3 to 1.5 mm).")
    if hmin < 0.03 or hmax > 2.0:
        warnings.append(f"Layer height ranges {hmin:.2f} to {hmax:.2f} mm.")
    if n_pts > WARN_POINTS:
        warnings.append(f"{n_pts:,} points: planning and streaming will be slow.")
    return errors, warnings, stats


def to_slice_result(tp, shift=(0.0, 0.0)):
    """Planner input: one Layer per path, in the order the generator emitted them
    (so a part printed object by object is never re-ordered by height)."""
    import numpy as np
    from slicer import SliceResult, SliceSettings, Layer, Path, WALL_OUTER, TRAVEL
    layers = []
    dx, dy = shift
    for i, p in enumerate(tp["paths"]):
        pts = [(pt[0] + dx, pt[1] + dy, pt[2], pt[3], pt[4]) for pt in p["p"]]
        path = Path(kind=WALL_OUTER if p["e"] else TRAVEL, points=pts, closed=False)
        path.arm = p.get("arm")
        layers.append(Layer(index=i, z=pts[0][2], solid=False, paths=[path]))
    b = bounds(tp)
    arr = np.array([[b[0][0] + dx, b[0][1] + dy, b[0][2]], [b[1][0] + dx, b[1][1] + dy, b[1][2]]])
    return SliceResult(layers=layers, settings=SliceSettings(), bounds=arr)
