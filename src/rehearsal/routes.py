"""Traditional (extrude, then modify in a second cell) against the robotic cell
(one job order, modified in place) for the European / American duct mismatch.

Each route has two branches: the spec error is caught at intake, or it is
missed and found at installation. The expected result weights them by
``p_catch``. QA cannot catch this error on either route: parts are inspected
against the drawing, and the drawing is what is wrong (verification passes,
validation fails).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import yaml

DAY_H = 24.0


@dataclass
class Outcome:
    lead_time_h: float = 0.0        # order received -> working installation
    machine_h: float = 0.0
    labour_h: float = 0.0
    material_kg: float = 0.0
    scrap_kg: float = 0.0
    job_orders: int = 0
    handovers: int = 0
    cost_gbp: float = 0.0

    def scaled(self, k: float) -> "Outcome":
        return Outcome(**{f: v * k for f, v in asdict(self).items()})

    def __add__(self, o: "Outcome") -> "Outcome":
        return Outcome(**{f: getattr(self, f) + getattr(o, f) for f in asdict(self)})


def tube_mass_kg(od_mm, wall_mm, length_mm, rho):
    r_o, r_i = od_mm / 2, od_mm / 2 - wall_mm
    return math.pi * (r_o ** 2 - r_i ** 2) * length_mm * rho / 1e6


# ----------------------------------------------------------------------
def _ods(cfg: dict) -> tuple[float, float]:
    drawn, fits = cfg["part"]["od_mm"][cfg["direction"]]
    return drawn, fits


def traditional(cfg: dict, n: int, caught: bool) -> Outcome:
    p, t = cfg["part"], cfg["traditional"]
    drawn, fits = _ods(cfg)
    od = fits if caught else drawn
    m = tube_mass_kg(od, t["wall_mm"], p["length_mm"], p["density_g_cm3"])
    m_fit = tube_mass_kg(fits, t["wall_mm"], p["length_mm"], p["density_g_cm3"])
    run_h = lambda k: k * p["length_mm"] / 1000 / t["line_speed_m_per_min"] / 60 + k * t["cut_s_per_part"] / 3600
    qa_h = n * t["qa_min_per_part"][t["qa_policy"]] / 60
    o = Outcome(job_orders=1, handovers=0)
    o.machine_h = t["die_setup_h"] + run_h(n)
    o.labour_h = t["drawing_to_shop_h"] + o.machine_h + qa_h
    o.material_kg = n * m
    o.lead_time_h = t["drawing_to_shop_h"] + o.machine_h + qa_h + t["ship_days"] * DAY_H + n * t["install_h_per_part"]
    if not caught:
        # QA passed them (they match the drawing); found at install, sent back
        o.job_orders += 1
        if cfg["direction"] == "loose":
            o.handovers += 1                                  # to the forming cell
            fix_h = n * t["expand_min_per_part"] / 60
            k = math.ceil(n * t["expand_scrap"])
            remake_h = t["remake_setup_h"] + run_h(k) if k else 0.0
        else:                                                 # will_not_fit: remake all
            fix_h, k = 0.0, n
            remake_h = t["remake_setup_h"] + run_h(n)
        o.machine_h += fix_h + remake_h
        o.labour_h += fix_h + remake_h + qa_h + n * t["install_h_per_part"]
        o.material_kg += k * m_fit
        o.scrap_kg += k * m
        o.lead_time_h += t["return_days"] * DAY_H + fix_h + remake_h + qa_h + t["ship_days"] * DAY_H \
            + n * t["install_h_per_part"]
    o.cost_gbp = o.machine_h * t["machine_gbp_h"] + o.labour_h * t["labour_gbp_h"] + o.material_kg * t["material_gbp_kg"]
    return o


def cell_print_h(c: dict, od_mm: float, height_mm: float, n: int) -> float:
    r_mid = od_mm / 2 - c["wall_mm"] / 2
    rev_s = 2 * math.pi * r_mid / c["surface_speed_mm_s"]
    return n * height_mm / c["pitch_mm"] * rev_s / 3600 / c["dual_arm_speedup"]


def cell(cfg: dict, n: int, caught: bool) -> Outcome:
    p, c = cfg["part"], cfg["cell"]
    drawn, fits = _ods(cfg)
    od = fits if caught else drawn
    rho, L, ins = p["density_g_cm3"], p["length_mm"], p["spigot_insert_mm"]
    print_h = cell_print_h(c, od, L, n)
    load_h = n * c["load_unload_min_per_part"] / 60
    ship_h = 0.0 if c["on_site"] else c["ship_days"] * DAY_H
    o = Outcome(job_orders=1, handovers=0)
    o.machine_h = print_h + load_h
    o.labour_h = (c["intake_min"] + c["approval_min"]) / 60 + load_h + print_h * c["labour_fraction_while_printing"]
    o.material_kg = n * tube_mass_kg(od, c["wall_mm"], L, rho)
    o.lead_time_h = (c["intake_min"] + c["approval_min"]) / 60 + o.machine_h + ship_h + n * c["install_h_per_part"]
    if not caught:
        # found at install: the same job order gains a job per part
        back_h = 0.0 if c["on_site"] else c["return_days"] * DAY_H
        reloc_h = n * c["relocate_min_per_part"] / 60
        if cfg["direction"] == "loose":                       # add outer beads round the spigot
            beads = math.ceil((fits - od) / (2 * c["wall_mm"]) - 1e-9)
            fix_h = added = 0.0
            for b in range(1, beads + 1):
                d = od + 2 * b * c["wall_mm"]
                fix_h += cell_print_h(c, d, ins, n) * c["dual_arm_speedup"]   # one arm per bead ring
                added += n * tube_mass_kg(d, c["wall_mm"], ins, rho)
            cut_h, scrap = 0.0, 0.0
        else:                                                 # cut the spigot off, reprint it smaller
            cut_h = n * c["cut_min_per_part"] / 60
            fix_h = cell_print_h(c, fits, ins, n)
            added = n * tube_mass_kg(fits, c["wall_mm"], ins, rho)
            scrap = n * tube_mass_kg(od, c["wall_mm"], ins, rho)
        o.machine_h += fix_h + reloc_h
        o.labour_h += cut_h + reloc_h + fix_h * c["labour_fraction_while_printing"] + n * c["install_h_per_part"]
        o.material_kg += added
        o.scrap_kg += scrap
        o.lead_time_h += back_h + cut_h + reloc_h + fix_h + ship_h + n * c["install_h_per_part"]
    o.cost_gbp = o.machine_h * c["machine_gbp_h"] + o.labour_h * c["labour_gbp_h"] + o.material_kg * c["material_gbp_kg"]
    return o


ROUTES = {"traditional": traditional, "cell": cell}


def evaluate(cfg: dict, n: int | None = None, p_catch: dict | None = None) -> dict:
    n = n or cfg["batch"]
    pc = {**cfg["p_catch"], **(p_catch or {})}
    out = {}
    for name, fn in ROUTES.items():
        hit, miss = fn(cfg, n, True), fn(cfg, n, False)
        exp = hit.scaled(pc[name]) + miss.scaled(1 - pc[name])
        out[name] = {"p_catch": pc[name], "caught": asdict(hit), "missed": asdict(miss), "expected": asdict(exp)}
    return out


def sweep(cfg: dict, sizes=range(1, 31), p_catch: dict | None = None) -> list[dict]:
    rows = []
    for n in sizes:
        e = evaluate(cfg, n, p_catch)
        rows.append({"n": n, **{f"{r}_{k}": e[r]["expected"][k] for r in ROUTES for k in ("lead_time_h", "cost_gbp")}})
    return rows


def load(path: str) -> dict:
    return yaml.safe_load(open(path, encoding="utf-8"))


def print_table(res: dict) -> None:
    keys = ["lead_time_h", "machine_h", "labour_h", "material_kg", "scrap_kg", "job_orders", "handovers", "cost_gbp"]
    cols = [(r, b) for r in ROUTES for b in ("caught", "missed", "expected")]
    print(f"{'':14}" + "".join(f"{(r[:4] + ' ' + b):>16}" for r, b in cols))
    for k in keys:
        print(f"{k:14}" + "".join(f"{res[r][b][k]:>16.2f}" for r, b in cols))
    print("p_catch:", {r: res[r]["p_catch"] for r in ROUTES})


def sweep_png(rows: list[dict], path: str, title: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    n = [r["n"] for r in rows]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
    for route, style in (("traditional", "-"), ("cell", "--")):
        a1.plot(n, [r[f"{route}_lead_time_h"] / 24 for r in rows], style, label=route)
        a2.plot(n, [r[f"{route}_cost_gbp"] for r in rows], style, label=route)
    a1.set_xlabel("batch size"); a1.set_ylabel("expected lead time (days)")
    a2.set_xlabel("batch size"); a2.set_ylabel("expected cost (GBP)")
    for a in (a1, a2):
        a.grid(alpha=0.3); a.legend()
    fig.suptitle(title + "  (placeholder assumptions: config/routes/duct_case.yaml)", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
