"""Slice a model both ways (planar layers and the spiral fit), plan both, and report the delta.

    from compare_strategies import compare
    out = compare(path, settings, cfg)      # settings: SliceSettings, cfg: PlannerConfig
    print(out["text"])

Coverage delta: every extruding bead is sampled every ~1 mm and dropped into 1 mm cells of a
3D grid (cell = line width across, layer height up). Cells that only one method fills are
where the two differ; the report lists them per height band so you can see WHERE, not
just how much.
"""

from __future__ import annotations

import math
import time
from dataclasses import replace

import numpy as np


def _beads(res, step=1.0):
    """Sampled points along every extruding bead: Nx3 array (model frame)."""
    out = []
    for L in res.layers:
        for p in L.paths:
            if p.kind == "TRAVEL" or len(p.points) < 2:
                continue
            pts = p.points + ([p.points[0]] if p.closed else [])
            for a, b in zip(pts[:-1], pts[1:]):
                za, zb = (a[2] if len(a) >= 3 else L.z), (b[2] if len(b) >= 3 else L.z)
                d = math.dist((a[0], a[1], za), (b[0], b[1], zb))
                n = max(1, int(d / step))
                for k in range(n):
                    t = k / n
                    out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, za + (zb - za) * t))
    return np.array(out) if out else np.zeros((0, 3))


BAND = 5.0      # mm of height per coverage band


def _cells(pts, lw, lh):
    """(x cell, y cell, 5 mm band): where material goes in plan view, per band of height."""
    if len(pts) == 0:
        return set()
    ix = np.floor(pts[:, 0] / lw).astype(int)
    iy = np.floor(pts[:, 1] / lw).astype(int)
    iz = np.floor(pts[:, 2] / BAND).astype(int)
    return set(zip(ix.tolist(), iy.tolist(), iz.tolist()))


def compare(mesh_path, settings, cfg, progress=None):
    from slicer import slice_model
    from planner import plan, analyze
    out = {}
    lw, lh = settings.line_width, settings.layer_height
    for name, strat in (("planar", "planar"), ("spiral", "spiral")):
        if progress:
            progress(f"Slicing {name}…")
        t = time.time()
        res = slice_model(mesh_path, replace(settings, strategy=strat, layer_style=None))
        t_slice = time.time() - t
        if progress:
            progress(f"Planning {name}…")
        t = time.time()
        try:
            prog = plan(res, cfg)
        except RuntimeError as e:
            if "deadlocked" not in str(e):
                raise
            prog = plan(res, replace(cfg, num_arms=1))
        t_plan = time.time() - t
        st = analyze(prog)
        pts = _beads(res)
        length = sum(p.length() for L in res.layers for p in L.paths if p.kind != "TRAVEL")
        both = sum(1 for s_ in prog.steps if len(s_.arms) > 1 and all(a is not None and a.extrude for a in s_.arms))
        left = sum(1 for s_ in prog.steps if len(s_.arms) > 1 and s_.arms[1] is not None and s_.arms[1].extrude)
        air = sum(a for _, a in getattr(getattr(res, "region_features", None), "unsupported", []) or [])
        out[name] = dict(res=res, prog=prog, pts=pts, cells=_cells(pts, lw, lh), length_m=length / 1000.0,
                         minutes=prog.total_time() / 60.0, steps=len(prog.steps), reversals=st.tt_reversals,
                         turns=st.tt_travel / (2 * math.pi), both=both, left=left, air=air,
                         paths=sum(len(L.paths) for L in res.layers), t_slice=t_slice, t_plan=t_plan)
    P, S = out["planar"], out["spiral"]
    only_p = P["cells"] - S["cells"]
    only_s = S["cells"] - P["cells"]
    common = P["cells"] & S["cells"]
    zmin = float(min(P["pts"][:, 2].min() if len(P["pts"]) else 0, S["pts"][:, 2].min() if len(S["pts"]) else 0))
    zmax = float(max(P["pts"][:, 2].max() if len(P["pts"]) else 1, S["pts"][:, 2].max() if len(S["pts"]) else 1))
    # per 5 mm band
    bands = []
    nb = max(1, int(math.ceil((zmax - zmin) / 5.0)))
    for b in range(nb):
        lo, hi = zmin + BAND * b, zmin + BAND * (b + 1)
        kb = int(math.floor(lo / BAND))
        op = sum(1 for c in only_p if c[2] == kb)
        os_ = sum(1 for c in only_s if c[2] == kb)
        cm = sum(1 for c in common if c[2] == kb)
        bands.append((lo, hi, op, os_, cm))
    vol = lw * lw * BAND / 1000.0         # cm³ per cell column (rough)
    lines = [f"{'':14s}{'planar':>12s}{'spiral':>12s}",
             f"{'paths':14s}{P['paths']:>12d}{S['paths']:>12d}",
             f"{'bead length':14s}{P['length_m']:>10.1f} m{S['length_m']:>10.1f} m",
             f"{'est. time':14s}{P['minutes']:>8.1f} min{S['minutes']:>8.1f} min",
             f"{'both arms busy':14s}{100.0 * P['both'] / max(1, P['steps']):>10.0f} %{100.0 * S['both'] / max(1, S['steps']):>10.0f} %",
             f"{'left arm busy':14s}{100.0 * P['left'] / max(1, P['steps']):>10.0f} %{100.0 * S['left'] / max(1, S['steps']):>10.0f} %",
             f"{'disc reversals':14s}{P['reversals']:>12d}{S['reversals']:>12d}",
             f"{'disc turns':14s}{P['turns']:>12.0f}{S['turns']:>12.0f}",
             f"{'in the air':14s}{P['air']:>8.0f} mm²{'  (not measured)':>12s}",
             f"{'slice + plan':14s}{P['t_slice'] + P['t_plan']:>10.1f} s{S['t_slice'] + S['t_plan']:>10.1f} s",
             "",
             f"Plan-view coverage per {BAND:g} mm band ({lw:g} mm cells): both {len(common)}  ·  planar only "
             f"{len(only_p)}  ·  spiral only {len(only_s)}",
             "By height (mm): planar-only / spiral-only / both   (<- marks bands where they differ by >10 %)"]
    for lo, hi, op, os_, cm in bands:
        mark = "  <- differs" if (op + os_) > 0.10 * max(1, cm) else ""
        lines.append(f"  {lo:5.1f}–{hi:5.1f}: {op:6d} / {os_:6d} / {cm:6d}{mark}")
    return dict(text="\n".join(lines), zmin=zmin, zmax=zmax, lw=lw, planar_pts=P["pts"], spiral_pts=S["pts"],
                planar=P["res"], spiral=S["res"], planar_prog=P["prog"], spiral_prog=S["prog"], bands=bands)
