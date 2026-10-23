"""Intake test: does a model catch a European/American duct mismatch before
anything is made?

Each case gives a correct-looking drawing plus whatever else the workshop
received (order message, site survey). The model returns a part
specification and says whether the part fits what it must fit. Answers are
scored check by check against ``config/intake/duct_cases.yaml``.

Two scripted baselines run offline: ``technician`` builds exactly to the
drawing and never checks the interface (the traditional route as the
scenario assumes it), and ``ideal`` is the target behaviour.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import yaml

from .llm import LLMError, MockClient

SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "quantity": {"type": ["integer", "null"]},
        "length_mm": {"type": ["number", "null"]},
        "outer_diameter_mm": {"type": ["number", "null"], "description": "as drawn, converted to mm"},
        "mating_part": {"type": "string", "description": "what the part must fit, as stated in the documents, or 'not stated'"},
        "interface_status": {"type": "string", "enum": ["consistent", "conflict", "unknown"]},
        "fit_problem": {"type": "string",
                        "enum": ["none", "too_small_loose", "too_large_will_not_insert", "unknown"],
                        "description": "outlet OD compared with the duct: too small (slides in loose) or too large (cannot be inserted)"},
        "recommended_outer_diameter_mm": {"type": ["number", "null"],
                                          "description": "only if there is a conflict"},
        "questions_for_requester": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": ["quantity", "length_mm", "outer_diameter_mm", "mating_part", "interface_status",
                 "fit_problem", "recommended_outer_diameter_mm", "questions_for_requester", "reasoning"],
}

SYSTEM = """You are the order-intake engineer for a manufacturing cell.
You receive a drawing summary and every other document that came with the order.
Produce the part specification in millimetres.

Then check the part against whatever the documents say it must fit or connect to:
- conflict: the part as drawn does not suit what it connects to; say whether it is too small
  (loose) or too large (cannot be inserted), and recommend the outside diameter that would suit;
- consistent: it fits;
- unknown: the documents do not say what it connects to (ask).
A spigot outlet slides inside its duct, so its outside diameter must be slightly under the
duct's nominal size. Reply with one JSON object matching the schema."""


def load_cases(path: str) -> dict:
    return yaml.safe_load(open(path, encoding="utf-8"))


def build_messages(pack: dict, case: dict) -> list[dict]:
    text = [f"Request {case['id']}.", "", pack["drawings"][case["drawing"]]]
    for d in case.get("documents", []):
        text += ["", pack["documents"][d]]
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(text)}]


def score(expect: dict, out: dict) -> list[tuple[str, bool, str]]:
    res = []
    for k, want in expect.items():
        got = out.get(k)
        if isinstance(want, list):                     # [value, tolerance]
            ok = isinstance(got, (int, float)) and abs(got - want[0]) <= want[1]
            res.append((k, ok, f"want {want[0]}±{want[1]}, got {got}"))
        else:
            res.append((k, got == want, f"want {want}, got {got}"))
    return res


# ----------------------------------------------------------------------
# Offline baselines
def _text(messages: list[dict]) -> str:
    return messages[1]["content"]


def technician_policy(messages: list[dict]) -> dict:
    """Builds exactly to the drawing; does not cross-check the order or survey."""
    t = _text(messages)
    imperial = "Outlet OD: 9.94 in." in t
    return {"quantity": 10, "length_mm": 304.8 if imperial else 305,
            "outer_diameter_mm": 252.5 if imperial else 248.5, "mating_part": "not checked",
            "interface_status": "unknown", "fit_problem": "unknown",
            "recommended_outer_diameter_mm": None, "questions_for_requester": [],
            "reasoning": "made to drawing"}


def ideal_policy(messages: list[dict]) -> dict:
    t = _text(messages)
    imperial = "Outlet OD: 9.94 in." in t
    od = 252.5 if imperial else 248.5
    out = {"quantity": 10, "length_mm": 304.8 if imperial else 305, "outer_diameter_mm": od,
           "mating_part": "not stated", "interface_status": "unknown", "fit_problem": "unknown",
           "recommended_outer_diameter_mm": None, "questions_for_requester": [], "reasoning": "ideal"}
    us_duct = '10 in. spiral duct' in t or '10" GALV' in t
    eu_duct = "250 mm spiral duct" in t
    if us_duct:
        out.update(mating_part="10 in. spiral duct", interface_status="conflict" if od < 251 else "consistent",
                   fit_problem="too_small_loose" if od < 251 else "none",
                   recommended_outer_diameter_mm=252.5 if od < 251 else None)
    elif eu_duct:
        bad = od > 249.5
        out.update(mating_part="250 mm spiral duct", interface_status="conflict" if bad else "consistent",
                   fit_problem="too_large_will_not_insert" if bad else "none",
                   recommended_outer_diameter_mm=248.5 if bad else None)
    else:
        out["questions_for_requester"] = ["What duct does the outlet fit, and to which standard?"]
    return out


BASELINES = {"technician": technician_policy, "ideal": ideal_policy}


def baseline_client(name: str) -> MockClient:
    return MockClient(policy=BASELINES[name], name=name)


def run(client, pack_path: str, out_path: str, label: str) -> dict:
    pack = load_cases(pack_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    rows, n_checks, n_pass = [], 0, 0
    with open(out_path, "w", encoding="utf-8") as log:
        for case in pack["cases"]:
            t0 = time.monotonic()
            try:
                reply = client.complete_json(build_messages(pack, case), SPEC_SCHEMA, "part_spec")
                out, model, err, cost = reply.content, reply.model, None, reply.cost_usd
            except (LLMError, ValueError, KeyError) as e:
                out, model, err, cost = {}, label, str(e)[:300], None
            checks = score(case["expect"], out)
            ok = err is None and all(c[1] for c in checks)
            n_checks += len(checks)
            n_pass += sum(c[1] for c in checks)
            row = {"case": case["id"], "purpose": case["purpose"], "expect": case["expect"], "model": model, "passed": ok,
                   "checks": checks, "answer": out, "error": err, "cost_usd": cost,
                   "latency_s": round(time.monotonic() - t0, 2)}
            rows.append(row)
            log.write(json.dumps(row) + "\n")
        conflicts = [r for r in rows if r["expect"].get("interface_status") == "conflict"]
        caught = sum(1 for r in conflicts if r["answer"].get("interface_status") == "conflict")
        # caught AND the right direction AND a usable recommended diameter
        right = sum(1 for r in conflicts if all(ok for k, ok, _ in r["checks"]
                                                 if k in ("interface_status", "fit_problem", "recommended_outer_diameter_mm")))
        clean = [r for r in rows if r["expect"].get("interface_status") != "conflict"]
        false_alarms = sum(1 for r in clean if r["answer"].get("interface_status") == "conflict")
        summary = {"label": label, "cases_passed": sum(r["passed"] for r in rows), "cases": len(rows),
                   "checks_passed": n_pass, "checks": n_checks,
                   "conflicts_caught": f"{caught}/{len(conflicts)}",
                   "p_catch": round(caught / len(conflicts), 3) if conflicts else None,
                   "correct_fixes": f"{right}/{len(conflicts)}",
                   "p_correct_fix": round(right / len(conflicts), 3) if conflicts else None,
                   "false_alarms": f"{false_alarms}/{len(clean)}",
                   "cost_usd": round(sum(r["cost_usd"] or 0 for r in rows), 5), "log": out_path}
        log.write(json.dumps({"summary": summary}) + "\n")
    return {"summary": summary, "rows": rows}


def print_report(result: dict) -> None:
    s = result["summary"]
    print(f"\n{s['label']}: {s['cases_passed']}/{s['cases']} cases, {s['checks_passed']}/{s['checks']} checks, "
          f"${s['cost_usd']}")
    print(f"  conflicts flagged {s['conflicts_caught']}, flagged with the right direction and fix "
          f"{s['correct_fixes']}, false alarms {s['false_alarms']}")
    for r in result["rows"]:
        print(f"  {r['case']} {'PASS' if r['passed'] else 'FAIL':4}  {r['purpose']}")
        if r["error"]:
            print(f"        error: {r['error']}")
        for k, ok, d in r["checks"]:
            if not ok:
                print(f"        {k}: {d}")
    print("log:", s["log"])
