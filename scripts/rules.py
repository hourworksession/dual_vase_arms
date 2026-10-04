"""Print rules: load, check, explain and apply config/print_rules.yaml.

The rules file holds the decisions about HOW a part is printed (when the bed
turns, line widths, non-planar preferences per layer). It is also the contract
for the rule-based AI layer:

    rs = RuleSet.load()
    rs.get("turntable.max_radial_variation")       # 0.15
    rs.explain()                                   # plain text for an AI prompt
    ok, rejected = rs.propose({"turntable.max_radial_variation": 0.3})
    rs.save()

Every rule has an id, a `why` and (numbers) a `range`. propose() only accepts
values inside the range, or one of the `choices`. Decisions made with the rules
are logged as (rule id, what, where) in a DecisionLog so a print can be
explained afterwards.
"""

from __future__ import annotations

import copy
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_FILE = os.path.join(REPO_ROOT, "config", "print_rules.yaml")

# Used when the file is missing or a rule is absent, so the planner always has a value.
DEFAULTS = {
    "turntable.enabled": True,
    "turntable.round_paths": True,
    "turntable.max_radial_variation": 0.15,
    "turntable.min_radius_mm": 8.0,
    "turntable.kinds_never_turned": ["SKIN", "INFILL"],
    "turntable.face_arm": True,
    "turntable.face_limit_deg": 60.0,
    "turntable.symmetric_parts": True,
    "lines.min_width": 0.8,
    "lines.max_width": 1.6,
    "lines.warn_lost_percent": 0.5,
    "lines.max_overhang_deg": 45.0,
    "nonplanar.enabled": False,
    "hardware.min_nozzle_separation": 60.0,
    "hardware.tool_margin": 20.0,
    "strategy.default": "auto",
    "strategy.cone_angle": 15.0,
    "strategy.auto_overhang": 35.0,
    "strategy.auto_overhang_fraction": 0.15,
    "nonplanar.max_tilt": 15,
    "nonplanar.max_rise": 1.2,
}


@dataclass
class Rule:
    id: str
    value: Any
    why: str = ""
    range: Optional[Tuple[float, float]] = None
    choices: Optional[List[Any]] = None
    node: Optional[dict] = None          # the dict in the YAML tree, so edits save back

    def check(self, value) -> Optional[str]:
        """None if value is allowed, else the reason it is not."""
        if self.choices is not None:
            vals = value if isinstance(value, list) else [value]
            bad = [v for v in vals if v not in self.choices]
            return f"{bad} not in {self.choices}" if bad else None
        if isinstance(self.value, bool):
            return None if isinstance(value, bool) else "must be true or false"
        if isinstance(self.value, (int, float)):
            try:
                v = float(value)
            except (TypeError, ValueError):
                return "must be a number"
            if not math.isfinite(v):
                return "must be a finite number"
            if self.range is not None and not (self.range[0] <= v <= self.range[1]):
                return f"{v:g} is outside {self.range[0]:g}…{self.range[1]:g}"
        return None


@dataclass
class Band:
    id: str
    when: dict
    style: str
    why: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Tactic:
    id: str
    status: str                 # in_use | preview | planned
    when: dict
    uses: List[str]
    why: str = ""
    node: Optional[dict] = None


@dataclass
class DecisionLog:
    rows: List[Tuple[str, str, str]] = field(default_factory=list)   # (rule id, decision, where)

    def add(self, rule_id, decision, where=""):
        self.rows.append((rule_id, decision, where))

    def counts(self) -> Dict[Tuple[str, str], int]:
        out: Dict[Tuple[str, str], int] = {}
        for rid, dec, _ in self.rows:
            out[(rid, dec)] = out.get((rid, dec), 0) + 1
        return out

    def summary(self) -> str:
        return "\n".join(f"  {n:5d} × {dec}  [{rid}]" for (rid, dec), n in
                         sorted(self.counts().items(), key=lambda kv: -kv[1]))


class RuleSet:
    def __init__(self, tree: Optional[dict] = None, path: str = RULES_FILE):
        self.path = path
        self.tree = tree if tree is not None else {}
        self.rules: Dict[str, Rule] = {}
        self.bands: List[Band] = []
        self.tactics: List[Tactic] = []
        self.load_error: Optional[str] = None
        self._index(self.tree)
        for t in (self.tree.get("tactics") or []):
            if isinstance(t, dict) and "id" in t:
                self.tactics.append(Tactic(t["id"], str(t.get("status", "planned")), t.get("when") or {},
                                           list(t.get("uses") or []), str(t.get("why", "")).strip(), t))
        for rid, v in DEFAULTS.items():
            self.rules.setdefault(rid, Rule(rid, copy.deepcopy(v), "(default: not in the rules file)"))

    # ------------------------------------------------------------ load / save
    @classmethod
    def load(cls, path: str = RULES_FILE) -> "RuleSet":
        import yaml
        try:
            with open(path) as f:
                tree = yaml.safe_load(f) or {}
            if not isinstance(tree, dict):
                raise ValueError("not a rules file")
            rs = cls(tree, path)
        except FileNotFoundError:
            rs = cls({}, path)
        except Exception as e:                    # keep printing with defaults, but say so
            rs = cls({}, path)
            rs.load_error = f"{type(e).__name__}: {e}"
        return rs

    @classmethod
    def from_text(cls, text: str, path: str = RULES_FILE) -> "RuleSet":
        import yaml
        tree = yaml.safe_load(text) or {}
        if not isinstance(tree, dict):
            raise ValueError("The rules file must be a YAML mapping.")
        rs = cls(tree, path)
        problems = rs.problems()
        if problems:
            raise ValueError("\n".join(problems))
        return rs

    def save(self, path: Optional[str] = None):
        import yaml
        path = path or self.path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(self.tree, f, sort_keys=False, allow_unicode=True, width=100)

    def _index(self, node):
        if isinstance(node, dict):
            if "id" in node and "value" in node:
                rng = node.get("range")
                self.rules[node["id"]] = Rule(node["id"], node["value"], str(node.get("why", "")).strip(),
                                              tuple(rng) if rng else None, node.get("choices"), node)
            for k, v in node.items():
                if k == "bands" and isinstance(v, list):
                    for b in v:
                        if isinstance(b, dict) and "style" in b:
                            extra = {kk: vv for kk, vv in b.items() if kk not in ("id", "when", "style", "why")}
                            self.bands.append(Band(b.get("id", f"band{len(self.bands)}"), b.get("when") or {},
                                                   b["style"], str(b.get("why", "")).strip(), extra))
                            self._index(b)
                elif k == "tactics" and isinstance(v, list):
                    for t in v:                     # index their parameters (tilt etc.), not the tactic
                        if isinstance(t, dict):
                            for kk, vv in t.items():
                                if isinstance(vv, dict):
                                    self._index(vv)
                elif isinstance(v, (dict, list)):
                    self._index(v)
        elif isinstance(node, list):
            for v in node:
                self._index(v)

    # ------------------------------------------------------------ access
    def get(self, rule_id, default=None):
        r = self.rules.get(rule_id)
        if r is None:
            return default
        return r.value

    def problems(self) -> List[str]:
        out = []
        for r in self.rules.values():
            msg = r.check(r.value)
            if msg:
                out.append(f"{r.id}: {msg}")
        for b in self.bands:
            if b.style not in ("planar", "spiral", "spiral_brick"):
                out.append(f"{b.id}: unknown style '{b.style}' (planar, spiral, spiral_brick)")
        tilt_max = float(self.get("hardware.tilt_max", 30))
        for rid, r in self.rules.items():
            if (rid.endswith(".tilt") or rid.endswith(".max_tilt")) and isinstance(r.value, (int, float)) \
                    and float(r.value) > tilt_max:
                out.append(f"{rid}: {r.value} deg is more than the hardware allows (hardware.tilt_max {tilt_max:g})")
        for t in self.tactics:
            if t.status not in ("in_use", "preview", "planned"):
                out.append(f"{t.id}: status must be in_use, preview or planned")
            unknown = [k for k in t.when if k not in TACTIC_TESTS]
            if unknown:
                out.append(f"{t.id}: unknown test(s) {unknown}; known: {sorted(TACTIC_TESTS)}")
        return out

    def propose(self, changes: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """Apply the changes that are allowed. Returns (accepted, rejected-with-reason).
        This is the only way the AI layer changes a rule."""
        accepted, rejected = {}, {}
        for rid, val in changes.items():
            r = self.rules.get(rid)
            if r is None:
                rejected[rid] = "no such rule"
                continue
            msg = r.check(val)
            if msg:
                rejected[rid] = msg
                continue
            if isinstance(r.value, bool):
                val = bool(val)
            elif isinstance(r.value, int) and not isinstance(r.value, bool) and float(val).is_integer():
                val = int(val)
            elif isinstance(r.value, (int, float)) and not isinstance(r.value, bool):
                val = float(val)
            r.value = val
            if r.node is not None:
                r.node["value"] = val
            accepted[rid] = val
        return accepted, rejected

    def explain(self) -> str:
        """Plain-text list of the rules, for people and for an AI prompt."""
        lines = ["Print rules (id = value  [allowed]  why):"]
        for r in sorted(self.rules.values(), key=lambda r: r.id):
            allowed = (f"[{r.range[0]:g}…{r.range[1]:g}]" if r.range else
                       f"[{', '.join(map(str, r.choices))}]" if r.choices else "")
            lines.append(f"- {r.id} = {r.value!r} {allowed}  {r.why}".rstrip())
        if self.tactics:
            lines.append("Tactics (per region of the part; first match wins):")
            for t in self.tactics:
                lines.append(f"- {t.id} [{t.status}] when {t.when or 'always'} uses {'+'.join(t.uses)}. {t.why}")
        if self.bands:
            lines.append("Non-planar bands (first match wins):")
            for b in self.bands:
                lines.append(f"- {b.id}: when {b.when} -> {b.style}. {b.why}")
        return "\n".join(lines)


# ======================================================================== turntable
def path_turntable_decision(points, kind: str, rs: RuleSet, symmetric_layer: bool = False):
    """Should the bed turn while this path is printed?

    points: plate-frame (x, y[, ...]) about the turntable axis.
    Returns (turn: bool, rule id that decided, short reason)."""
    if not rs.get("turntable.enabled", True):
        return False, "turntable.enabled", "turntable off"
    if kind in (rs.get("turntable.kinds_never_turned") or []):
        return False, "turntable.kinds_never_turned", f"{kind.lower()} on a still bed"
    if not rs.get("turntable.round_paths", True):
        return True, "turntable.round_paths", "all walls turn (rule off)"
    pts = [(p[0], p[1]) for p in points]
    if len(pts) < 3:
        return False, "turntable.round_paths", "too short to turn for"
    radii = _radii_by_length(pts)
    mean_r = sum(radii) / len(radii)
    if mean_r < float(rs.get("turntable.min_radius_mm", 8.0)):
        return False, "turntable.min_radius_mm", "too close to the axis"
    if not _encloses_axis(pts):
        return False, "turntable.round_paths", "off-centre feature: bed held, arm draws it"
    # Spread of the radius along the path, ignoring the extreme 10 % at each end, so a
    # round wall with a couple of small bumps (posts, clips) still counts as round.
    r10, r90 = radii[len(radii) // 10], radii[(len(radii) * 9) // 10]
    variation = (r90 - r10) / mean_r if mean_r > 0 else 1.0
    if variation <= float(rs.get("turntable.max_radial_variation", 0.15)):
        return True, "turntable.max_radial_variation", "round path around the axis: bed turns"
    if symmetric_layer and rs.get("turntable.symmetric_parts", True):
        return True, "turntable.symmetric_parts", "not round but symmetric: bed turns"
    return False, "turntable.max_radial_variation", "corners / straight sides: bed held, arm draws it"


def _radii_by_length(pts, step: float = 1.0):
    """Radii sampled every `step` mm along the closed path (so long sides count by length,
    not by how many vertices they have), sorted."""
    out = []
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        L = math.hypot(x1 - x0, y1 - y0)
        k = max(1, int(L / step))
        for j in range(k):
            t = j / k
            out.append(math.hypot(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return sorted(out)


def _encloses_axis(pts) -> bool:
    """Winding number of the closed path about the origin is not zero."""
    total = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a0, a1 = math.atan2(y0, x0), math.atan2(y1, x1)
        d = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        total += d
    return abs(total) > math.pi        # ~±2π when it goes round once


def layer_is_symmetric(paths_pts, tol_mm: float = 1.0) -> bool:
    """Two-fold symmetric about the axis: every path has a partner (or itself) that is
    the same shape turned 180° (same length, centroid mirrored through the axis)."""
    feats = []
    for pts in paths_pts:
        if len(pts) < 2:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        length = sum(math.dist(pts[i][:2], pts[i + 1][:2]) for i in range(len(pts) - 1))
        feats.append((cx, cy, length))
    used = set()
    for i, (cx, cy, L) in enumerate(feats):
        if i in used:
            continue
        if math.hypot(cx, cy) <= tol_mm:          # centred on the axis: its own partner
            used.add(i)
            continue
        match = None
        for j, (dx, dy, M) in enumerate(feats):
            if j != i and j not in used and math.hypot(cx + dx, cy + dy) <= tol_mm and abs(L - M) <= tol_mm * 2:
                match = j
                break
        if match is None:
            return False
        used.update((i, match))
    return bool(feats)


# ======================================================================== non-planar
def classify_layers(stats, rs: RuleSet):
    """Which non-planar band each layer falls in.

    stats: per layer dicts {polys, holes, area, perimeter} from the slicer.
    Returns a list of (band id, style, reason) per layer, and a run-length summary text."""
    n = len(stats)
    out = []
    for i, s in enumerate(stats):
        prev = stats[i - 1] if i > 0 else None
        change = 0.0
        topo = False
        if prev is not None and prev["area"] > 0:
            change = 100.0 * abs(s["area"] - prev["area"]) / prev["area"]
            topo = (s["polys"], s["holes"]) != (prev["polys"], prev["holes"])
        wall = 2.0 * s["area"] / s["perimeter"] if s["perimeter"] > 0 else 0.0
        single = s["polys"] == 1 and s["holes"] <= 1
        chosen = None
        for b in rs.bands:
            w = b.when
            ok = True
            if "first_layers" in w:
                ok &= i < int(w["first_layers"])
            if "last_layers" in w:
                ok &= i >= n - int(w["last_layers"])
            if "section_change_percent_over" in w:
                ok &= topo or change > float(w["section_change_percent_over"])
            if "single_loop" in w:
                ok &= single == bool(w["single_loop"])
            if "wall_mm_at_least" in w:
                ok &= wall >= float(w["wall_mm_at_least"])
            if ok:
                chosen = b
                break
        if chosen is None:
            why = ("not one loop: " + (f"{s['polys']} separate parts" if s["polys"] != 1 else
                                       f"{s['holes']} holes")) if not single else "no band matched"
            out.append(("nonplanar.default", "planar", why))
        else:
            out.append((chosen.id, chosen.style, chosen.why.split(".")[0]))
    # run-length summary
    lines, start = [], 0
    for i in range(1, n + 1):
        if i == n or out[i][:2] != out[start][:2]:
            a, b = start + 1, i
            rid, style, why = out[start]
            span = f"layer {a}" if a == b else f"layers {a}–{b}"
            lines.append(f"  {span}: {style} [{rid}] {why}")
            start = i
    return out, "\n".join(lines)


# ======================================================================== tactics per region
# Tests a tactic's `when` can use, on one region's measurements (slicer.region_features).
TACTIC_TESTS = {
    "narrower_than_line": lambda f, v: (f["narrow_pct"] > 50.0) == bool(v),
    "encloses_axis": lambda f, v: f["encloses_axis"] == bool(v),
    "round": lambda f, v: (f["radial_variation"] <= f.get("_round_limit", 0.15)) == bool(v),
    "has_mirror_partner": lambda f, v: f["has_mirror_partner"] == bool(v),
    "single_loop": lambda f, v: (f["holes"] <= 1) == bool(v),
    "wall_mm_below": lambda f, v: f["wall_mm"] < float(v),
    "wall_mm_at_least": lambda f, v: f["wall_mm"] >= float(v),
    "overhang_deg_over": lambda f, v: f["overhang_deg"] > float(v),
    "top_surface": lambda f, v: (f["top_fraction"] > 0.5) == bool(v),
    "curved": lambda f, v: not bool(v),          # not measured yet: never matches "curved: true"
}


def choose_tactic(feat: dict, rs: RuleSet, statuses=("in_use", "preview")):
    """First tactic (in file order) whose tests all pass. Returns (tactic, [tests passed])."""
    f = dict(feat)
    f["_round_limit"] = float(rs.get("turntable.max_radial_variation", 0.15))
    for t in rs.tactics:
        if t.status not in statuses:
            continue
        if all(TACTIC_TESTS[k](f, v) for k, v in t.when.items() if k in TACTIC_TESTS):
            return t, list(t.when)
    return None, []


def build_cells(region_features, rs: RuleSet):
    """Group regions into CELLS: the same region carried up through consecutive layers
    with the same tactic. Returns a list of cell dicts (id, layers, tactic, status,
    centroid, mean measurements), the unit the AI chooses and learns per."""
    cells = []
    open_cells = []                          # cells that reached the previous layer
    for i, feats in enumerate(region_features):
        now = []
        for f in feats:
            t, _ = choose_tactic(f, rs)
            tid = t.id if t else "none"
            match = None
            for c in open_cells:
                if c["tactic"] == tid and math.hypot(c["_c"][0] - f["centroid"][0], c["_c"][1] - f["centroid"][1]) < 3.0 \
                        and abs(c["_area"] - f["area"]) <= 0.25 * max(c["_area"], 1e-6):
                    match = c
                    break
            if match is None:
                match = {"id": f"cell{len(cells) + 1}", "tactic": tid, "status": t.status if t else "",
                         "first_layer": i + 1, "last_layer": i + 1, "regions": 0, "_sum": {}}
                cells.append(match)
            match["last_layer"] = i + 1
            match["regions"] += 1
            match["_c"], match["_area"] = f["centroid"], f["area"]
            for k in ("area", "wall_mm", "radial_variation", "overhang_deg", "narrow_pct"):
                match["_sum"][k] = match["_sum"].get(k, 0.0) + float(f[k])
            match["centroid"] = f["centroid"]
            match["encloses_axis"] = f["encloses_axis"]
            match["has_mirror_partner"] = f["has_mirror_partner"]
            now.append(match)
        open_cells = now
    for c in cells:
        n = max(1, c["regions"])
        c["mean"] = {k: round(v / n, 3) for k, v in c.pop("_sum").items()}
        c.pop("_c", None)
        c.pop("_area", None)
    return cells


def cells_summary(cells, limit: int = 12) -> str:
    """Short text table of the cells, biggest first."""
    rows = sorted(cells, key=lambda c: -(c["last_layer"] - c["first_layer"] + 1) * c["mean"].get("area", 0))
    out = []
    by_tactic: Dict[str, int] = {}
    for c in cells:
        by_tactic[c["tactic"]] = by_tactic.get(c["tactic"], 0) + 1
    out.append("  " + ", ".join(f"{n} × {t}" for t, n in sorted(by_tactic.items(), key=lambda kv: -kv[1])))
    for c in rows[:limit]:
        m = c["mean"]
        out.append(f"  {c['id']:>7}: layers {c['first_layer']}–{c['last_layer']} at {c['centroid']} -> {c['tactic']} "
                   f"({c['status']}); wall {m['wall_mm']:.2f} mm, round {m['radial_variation']:.2f}, "
                   f"overhang {m['overhang_deg']:.0f}°" + (", mirror pair" if c["has_mirror_partner"] else ""))
    if len(rows) > limit:
        out.append(f"  … {len(rows) - limit} more")
    return "\n".join(out)


# ======================================================================== strategy
def choose_strategy_by_trial(slice_fn, rs: RuleSet):
    """Try each layer strategy on a quick coarse slice and keep the one that leaves the least
    material in the air (ties -> planar). slice_fn(strategy, cone_angle) -> SliceResult.
    Returns (strategy, cone_angle, table: [(strategy, angle, unsupported mm², layers)])."""
    tilt_max = float(rs.get("hardware.tilt_max", 30.0))
    # Spiral-first: if the model has a wall round the axis, the two-arm spiral is the plan.
    try:
        res = slice_fn("spiral", float(rs.get("strategy.cone_angle", 15.0)))
        segs = getattr(getattr(res, "spiral_report", None), "segments", [])
        turns = sum(s_.turns for s_ in segs)
        if segs and turns >= 2.0:
            return "spiral", float(rs.get("strategy.cone_angle", 15.0)), [("spiral", 0.0, 0.0, len(res.layers))]
    except Exception:
        pass
    angles = [a for a in (15.0, 25.0, 35.0) if a <= tilt_max + 1e-9] or [tilt_max]
    table = []
    trials = [("planar", 0.0)] + [(s_, a) for a in angles for s_ in ("cone_out", "cone_in")]
    for strat, ang in trials:
        try:
            res = slice_fn(strat, ang)
            air = sum(a for _, a in getattr(res.region_features, "unsupported", []))
            table.append((strat, ang, air, len(res.layers)))
        except Exception as e:
            table.append((strat, ang, float("inf"), 0))
    # least material in the air wins; among near-equal results prefer planar, then the
    # shallowest cone (less nozzle tilt, fewer layers)
    best = min(table, key=lambda r: (round(r[2] / 25.0), 0 if r[0] == "planar" else 1, r[1]))
    strat, ang = best[0], best[1]
    if strat == "planar":
        # nothing in the air to fix: a single loop around the axis prints better as a spiral
        res = slice_fn("planar", 0.0)
        s2, _ = recommend_strategy(res.region_features, rs)
        if s2 == "spiral":
            strat = "spiral"
    return strat, ang, table


def recommend_strategy(region_features, rs: RuleSet):
    """Pick a layer strategy from a coarse planar slice's region features.
    Returns (strategy, reason)."""
    if not region_features:
        return "planar", "no cross-sections measured"
    limit = float(rs.get("strategy.auto_overhang", 35.0))
    need = float(rs.get("strategy.auto_overhang_fraction", 0.15))
    n = len(region_features)
    out_layers = in_layers = loop_layers = 0
    for feats in region_features:
        if not feats:
            continue
        big = max(feats, key=lambda f: f["area"])
        if big["overhang_deg"] > limit:
            # outward if the region grew away from the axis: compare mean radius trend via area
            out_layers += 1 if big.get("grows_outward", True) else 0
            in_layers += 0 if big.get("grows_outward", True) else 1
        if big["encloses_axis"] and big["holes"] <= 1 and len(feats) == 1:
            loop_layers += 1
    if out_layers / n >= need:
        return "cone_out", f"{out_layers} of {n} layers overhang outward by more than {limit:g}°"
    if in_layers / n >= need:
        return "cone_in", f"{in_layers} of {n} layers overhang inward by more than {limit:g}°"
    if loop_layers / n >= 0.6:
        return "spiral", f"{loop_layers} of {n} layers are a single loop around the axis"
    return "planar", "no large overhangs; mixed cross-sections"
