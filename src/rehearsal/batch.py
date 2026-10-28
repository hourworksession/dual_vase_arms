"""Repeat the intake test many times and write every result to an Excel workbook.

    python -m rehearsal batch --models openai/gpt-4o-mini google/gemini-2.5-flash --repeats 30

One "repeat" sends all five cases once. Every call is one row on the Answers
sheet; the Summary, By case and Direct vs extract sheets are Excel formulas
over those rows, so they recalculate if rows are deleted or added.
Ctrl+C stops early and still saves what has finished.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import intake

HEAD = ["Model", "Mode", "Repeat", "Case", "Expected status", "Passed", "Conflict case", "Flagged conflict",
        "Right fix", "False alarm", "Unreadable", "Status said", "Direction said", "OD read (mm)",
        "Recommended OD (mm)", "Duct size read (mm)", "Cost (USD)", "Latency (s)", "Reasoning", "Error"]


def answer_row(model: str, mode: str, repeat: int, r: dict) -> list:
    exp, a = r["expect"], r["answer"]
    is_conf = exp.get("interface_status") == "conflict"
    right = is_conf and r["error"] is None and all(
        ok for k, ok, _ in r["checks"] if k in ("interface_status", "fit_problem", "recommended_outer_diameter_mm"))
    false_alarm = (not is_conf) and a.get("interface_status") == "conflict"
    return [model, mode, repeat, r["case"], exp.get("interface_status"), int(r["passed"]), int(is_conf),
            int(a.get("interface_status") == "conflict"), int(right), int(false_alarm), int(r["error"] is not None),
            a.get("interface_status"), a.get("fit_problem"), a.get("outer_diameter_mm"),
            a.get("recommended_outer_diameter_mm"), a.get("mating_nominal_mm"), r["cost_usd"], r["latency_s"],
            (a.get("reasoning") or "")[:500], r["error"]]


def run_batch(jobs: list[tuple[str, str, object]], cases_path: str, repeats: int, workers: int,
              out_xlsx: str, settings: dict) -> str:
    """jobs = [(model label, mode, client)]."""
    pack = intake.load_cases(cases_path)
    tasks = [(label, mode, client, rep, case) for label, mode, client in jobs
             for rep in range(1, repeats + 1) for case in pack["cases"]]
    total, done, lock = len(tasks), 0, threading.Lock()
    rows: list[list] = []
    raw_path = out_xlsx.replace(".xlsx", ".jsonl")
    os.makedirs(os.path.dirname(out_xlsx) or ".", exist_ok=True)
    print(f"{total} calls planned ({len(jobs)} model/mode pairs x {repeats} repeats x {len(pack['cases'])} cases), "
          f"{workers} at a time. Ctrl+C stops and saves.")
    t0 = time.monotonic()
    raw = open(raw_path, "w", encoding="utf-8")
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futs = {pool.submit(intake.evaluate_case, client, pack, case, mode, label): (label, mode, rep)
                for label, mode, client, rep, case in tasks}
        for f in as_completed(futs):
            label, mode, rep = futs[f]
            r = f.result()
            with lock:
                rows.append(answer_row(label, mode, rep, r))
                raw.write(json.dumps({"model": label, "mode": mode, "repeat": rep, **r}) + "\n")
                done += 1
                flag = "ERR " if r["error"] else ("PASS" if r["passed"] else "fail")
                print(f"[{done:>4}/{total}] {label:32} {mode:7} r{rep:<3} {r['case']} {flag}", flush=True)
    except KeyboardInterrupt:
        print("\nStopped: saving the finished calls.")
        pool.shutdown(wait=False, cancel_futures=True)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        raw.close()
    rows.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    settings = {**settings, "calls completed": f"{len(rows)} of {total}",
                "wall time (min)": round((time.monotonic() - t0) / 60, 1), "raw log": raw_path}
    write_workbook(out_xlsx, rows, pack, settings, sorted({(j[0], j[1]) for j in jobs}))
    return out_xlsx


# ----------------------------------------------------------------------
def write_workbook(path: str, rows: list[list], pack: dict, settings: dict, pairs: list[tuple[str, str]]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    F = "Arial"
    hfont, hfill = Font(name=F, bold=True, color="FFFFFF"), PatternFill("solid", fgColor="2C4F8A")
    body, blue = Font(name=F), Font(name=F, color="0000FF")
    wb = Workbook()

    def header(ws, cols, widths=None):
        for j, h in enumerate(cols, 1):
            c = ws.cell(1, j, h)
            c.font, c.fill = hfont, hfill
            c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A2"
        for j, w in enumerate(widths or [], 1):
            ws.column_dimensions[get_column_letter(j)].width = w

    # Settings (inputs referenced by formulas)
    st = wb.active
    st.title = "Settings"
    header(st, ["Setting", "Value", "Note"], [30, 60, 70])
    fixed = [("z (95% interval)", 1.96, "Wilson score interval, used on Summary and By case"),
             ("Good: p_fix at least", 0.95, "Proposed threshold; agree with supervisors"),
             ("Good: false-alarm rate at most", 0.05, "Proposed threshold"),
             ("Acceptable: p_fix at least", 0.67, "Proposed threshold"),
             ("Acceptable: false-alarm rate at most", 0.5, "Proposed threshold")]
    for i, (k, v, n) in enumerate(fixed, 2):
        st.cell(i, 1, k).font = body
        st.cell(i, 2, v).font = blue
        st.cell(i, 3, n).font = body
    r = len(fixed) + 2
    for k, v in settings.items():
        st.cell(r, 1, k).font = body
        st.cell(r, 2, v if isinstance(v, (int, float)) else str(v)).font = body
        st.cell(r, 2).alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    Z, GP, GF, AP, AF = "Settings!$B$2", "Settings!$B$3", "Settings!$B$4", "Settings!$B$5", "Settings!$B$6"

    # Answers (raw data, one row per call)
    an = wb.create_sheet("Answers")
    header(an, HEAD, [26, 9, 8, 6, 12, 7, 8, 9, 7, 8, 9, 12, 22, 10, 12, 11, 10, 9, 70, 40])
    for i, row in enumerate(rows, 2):
        for j, v in enumerate(row, 1):
            an.cell(i, j, v).font = body
    last = max(len(rows) + 1, 2)
    col = {h: get_column_letter(j) for j, h in enumerate(HEAD, 1)}
    rng = lambda h: f"Answers!${col[h]}$2:${col[h]}${last}"
    an.auto_filter.ref = f"A1:{get_column_letter(len(HEAD))}{last}"

    def wilson(p, n, side):
        sign = "-" if side == "low" else "+"
        return (f"=IFERROR(({p}+{Z}^2/(2*{n}){sign}{Z}*SQRT({p}*(1-{p})/{n}+{Z}^2/(4*{n}^2)))"
                f"/(1+{Z}^2/{n}),\"\")")

    # Summary (formulas over Answers)
    sm = wb.create_sheet("Summary", 0)
    cols = ["Model", "Mode", "Repeats", "Calls", "Unreadable", "Conflict trials", "Flagged", "Right fixes",
            "Clean trials", "False alarms", "Cases passed", "p_catch", "p_fix", "p_fix 95% low", "p_fix 95% high",
            "False-alarm rate", "Case accuracy", "Cost (USD)", "Cost per right fix (USD)", "Mean latency (s)", "Grade"]
    header(sm, cols, [28, 9, 9, 7, 10, 9, 8, 8, 8, 8, 8, 9, 8, 9, 9, 10, 10, 10, 12, 10, 11])
    for i, (m, md) in enumerate(pairs, 2):
        k = f'{rng("Model")},$A{i},{rng("Mode")},$B{i}'
        f = {
            "A": m, "B": md,
            "C": f'=COUNTIFS({k},{rng("Case")},"C1")',
            "D": f"=COUNTIFS({k})",
            "E": f'=SUMIFS({rng("Unreadable")},{k})',
            "F": f'=SUMIFS({rng("Conflict case")},{k})',
            "G": f'=SUMIFS({rng("Flagged conflict")},{k},{rng("Conflict case")},1)',
            "H": f'=SUMIFS({rng("Right fix")},{k})',
            "I": f"=D{i}-F{i}",
            "J": f'=SUMIFS({rng("False alarm")},{k})',
            "K": f'=SUMIFS({rng("Passed")},{k})',
            "L": f'=IFERROR(G{i}/F{i},"")',
            "M": f'=IFERROR(H{i}/F{i},"")',
            "N": wilson(f"M{i}", f"F{i}", "low"),
            "O": wilson(f"M{i}", f"F{i}", "high"),
            "P": f'=IFERROR(J{i}/I{i},"")',
            "Q": f'=IFERROR(K{i}/D{i},"")',
            "R": f'=SUMIFS({rng("Cost (USD)")},{k})',
            "S": f'=IFERROR(R{i}/H{i},"")',
            "T": f'=IFERROR(AVERAGEIFS({rng("Latency (s)")},{k}),"")',
            "U": f'=IF(D{i}=0,"",IF(AND(M{i}>={GP},P{i}<={GF}),"Good",IF(AND(M{i}>={AP},P{i}<={AF}),"Acceptable","Fail")))',
        }
        for c, v in f.items():
            cell = sm[f"{c}{i}"]
            cell.value, cell.font = v, body
        for c in "LMNOPQ":
            sm[f"{c}{i}"].number_format = "0.0%"
        for c in "RS":
            sm[f"{c}{i}"].number_format = "0.00000"
        sm[f"T{i}"].number_format = "0.00"
    n_pairs = len(pairs) + 1
    note = n_pairs + 2
    for t in ("p_fix = conflicts flagged with the right direction and diameter / conflict trials (C1, C4, C5). "
              "This is the number for the route model's --p-catch.",
              "False-alarm rate = conflicts flagged on C2 and C3 / clean trials. Unreadable replies count as wrong.",
              "Repeats measure how consistent a model is on these five fixed cases, not how it handles new cases.",
              "Grades use the thresholds on the Settings sheet; they are proposed, not taken from a standard."):
        sm.cell(note, 1, t).font = Font(name=F, italic=True)
        note += 1

    # By case
    bc = wb.create_sheet("By case", 1)
    header(bc, ["Model", "Mode", "Case", "Expected status", "Trials", "Passed", "Pass rate", "95% low", "95% high",
                "Flagged conflict", "Flag rate", "Unreadable"], [28, 9, 6, 14, 7, 7, 9, 9, 9, 9, 9, 10])
    i = 2
    for m, md in pairs:
        for case in pack["cases"]:
            k = f'{rng("Model")},$A{i},{rng("Mode")},$B{i},{rng("Case")},$C{i}'
            vals = [m, md, case["id"], case["expect"].get("interface_status"),
                    f"=COUNTIFS({k})", f'=SUMIFS({rng("Passed")},{k})', f'=IFERROR(F{i}/E{i},"")',
                    wilson(f"G{i}", f"E{i}", "low"), wilson(f"G{i}", f"E{i}", "high"),
                    f'=SUMIFS({rng("Flagged conflict")},{k})', f'=IFERROR(J{i}/E{i},"")',
                    f'=SUMIFS({rng("Unreadable")},{k})']
            for j, v in enumerate(vals, 1):
                bc.cell(i, j, v).font = body
            for c in "GHIK":
                bc[f"{c}{i}"].number_format = "0.0%"
            i += 1

    # Direct vs extract, per model
    dv = wb.create_sheet("Direct vs extract", 2)
    header(dv, ["Model", "p_fix direct", "p_fix extract", "Change in p_fix", "False-alarm rate direct",
                "False-alarm rate extract", "Change in false-alarm rate"], [28, 12, 12, 12, 14, 14, 14])
    models = sorted({m for m, _ in pairs})
    S = f"Summary!$A$2:$A${n_pairs}"
    for i, m in enumerate(models, 2):
        def pick(colL, mode):
            return (f'=IFERROR(AVERAGEIFS(Summary!${colL}$2:${colL}${n_pairs},{S},$A{i},'
                    f'Summary!$B$2:$B${n_pairs},"{mode}"),"")')
        vals = [m, pick("M", "direct"), pick("M", "extract"), f'=IFERROR(C{i}-B{i},"")',
                pick("P", "direct"), pick("P", "extract"), f'=IFERROR(F{i}-E{i},"")']
        for j, v in enumerate(vals, 1):
            dv.cell(i, j, v).font = body
            if j > 1:
                dv.cell(i, j).number_format = "0.0%"

    # Prompts, for the record
    pr = wb.create_sheet("Prompts and cases")
    header(pr, ["Item", "Text"], [22, 120])
    items = [("Direct prompt", intake.SYSTEM), ("Extract prompt", intake.EXTRACT_SYSTEM)]
    items += [(f"Drawing: {k}", v) for k, v in pack["drawings"].items()]
    items += [(f"Document: {k}", v) for k, v in pack["documents"].items()]
    items += [(f"{c['id']} expected", json.dumps(c["expect"])) for c in pack["cases"]]
    for i, (k, v) in enumerate(items, 2):
        pr.cell(i, 1, k).font = body
        pr.cell(i, 2, v).font = body
        pr.cell(i, 2).alignment = Alignment(wrap_text=True, vertical="top")

    wb.save(path)
