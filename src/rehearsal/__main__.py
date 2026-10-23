"""Rehearsal test bed CLI.

    python -m rehearsal doctor                       # check key, credit and structured-output models
    python -m rehearsal run --mock                   # offline run, no key needed
    python -m rehearsal run --mock --mistakes 0.4    # offline, agents make mistakes the referee must catch
    python -m rehearsal run                          # live, models from config/rehearsal.yaml
    python -m rehearsal run --models a/b c/d         # live, override the model list
    python -m rehearsal report runs/<file>.jsonl     # table + timeline PNG for a run

Run from the repo root (with ``src`` on PYTHONPATH, or after ``pip install -e .``).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import yaml

from .agents import Agent, mock_policy
from .engine import Rehearsal
from .llm import MockClient, OpenRouterClient, load_dotenv
from .world import World


def _cfg(path: str) -> dict:
    return yaml.safe_load(open(path, encoding="utf-8"))


def cmd_doctor(a) -> int:
    load_dotenv()
    cfg = _cfg(a.config)["llm"]
    try:
        c = OpenRouterClient(models=cfg["models"], base_url=cfg.get("base_url", "https://openrouter.ai/api/v1"))
    except Exception as e:
        print("FAIL:", e)
        return 1
    try:
        key = c.get("/key").get("data", {})
        print(f"Key OK. label={key.get('label')} usage=${key.get('usage')} limit={key.get('limit')} "
              f"free_tier={key.get('is_free_tier')}")
    except Exception as e:
        print("Could not read key info:", e)
    try:
        models = c.get("/models?supported_parameters=structured_outputs").get("data", [])
        ids = {m["id"]: m for m in models}
        print(f"{len(ids)} models support structured outputs.")
        for m in cfg["models"]:
            print(f"  {m}: {'OK' if m in ids or m == 'openrouter/auto' else 'NOT in structured-output list'}")
        free = sorted(i for i in ids if i.endswith(":free"))
        if free:
            print("  Free structured-output models right now:", ", ".join(free[:12]))
    except Exception as e:
        print("Could not list models:", e)
    return 0


def cmd_run(a) -> int:
    load_dotenv()
    cfg = _cfg(a.config)
    world = World.from_yaml(a.scenario, a.system)
    llm_cfg = cfg["llm"]
    agents = []
    for name, spec in world.arms.items():
        if a.mock:
            client = MockClient(policy=mock_policy(a.mistakes, seed=a.seed + len(agents)), name="mock")
        else:
            models = a.models or llm_cfg["models"]
            client = OpenRouterClient(models=models, base_url=llm_cfg.get("base_url", "https://openrouter.ai/api/v1"),
                                      temperature=llm_cfg.get("temperature", 0.2),
                                      max_tokens=llm_cfg.get("max_tokens", 800))
        agents.append(Agent(arm=name, role=spec.role, station_deg=spec.default_station_deg,
                            client=client, max_retries=cfg["engine"].get("max_retries", 2)))
    tag = "mock" if a.mock else "live"
    log = a.out or os.path.join("runs", f"{world.name}_{tag}_{time.strftime('%Y%m%d_%H%M%S')}.jsonl")
    r = Rehearsal(world, agents, log, max_calls=a.max_calls or cfg["engine"]["max_calls"],
                  max_rounds=cfg["engine"].get("max_rounds", 200))
    summary = r.run()
    print(json.dumps(summary, indent=2))
    print("log:", log)
    if a.report:
        return cmd_report(argparse.Namespace(log=log, png=None))
    return 0 if summary["completed"] else 2


def cmd_report(a) -> int:
    recs = [json.loads(l) for l in open(a.log, encoding="utf-8")]
    props = [r for r in recs if r["kind"] == "proposal"]
    print(f"{'t_sim':>8}  {'arm':6} {'try':>3}  {'action':10} {'job':5} {'ok':3}  model / refutation")
    for r in props:
        p = r["proposal"]
        why = "; ".join(f"[{x}] {m}" for x, m in r["refutations"]) or r.get("model", "")
        print(f"{r['t_sim_s']:>8.1f}  {r['arm']:6} {r['attempt']:>3}  {p.get('action',''):10} "
              f"{str(p.get('job_id') or ''):5} {'Y' if r['ok'] else 'N':3}  {why[:90]}")
    summ = next((r for r in recs if r["kind"] == "summary"), None)
    if summ:
        keep = {k: v for k, v in summ.items() if k not in ("kind", "wall_time")}
        print(json.dumps(keep, indent=2))
    try:
        _timeline_png(recs, a.png or a.log.replace(".jsonl", ".png"))
    except Exception as e:  # matplotlib missing or headless issue
        print("timeline plot skipped:", e)
    return 0


def _timeline_png(recs: list[dict], path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    start = next(r for r in recs if r["kind"] == "start")
    arms = list(start["snapshot"]["arms"].keys())
    applied = [r for r in recs if r["kind"] == "applied" and r["action"] in ("start_job", "join_job")]
    done = {r["job"]: r["finished_s"] for r in recs if r["kind"] == "job_done"}
    fig, ax = plt.subplots(figsize=(10, 1.2 + 0.8 * len(arms)))
    cmap = plt.get_cmap("tab20")
    jobs = sorted({r["job"] for r in applied})
    for r in applied:
        y = arms.index(r["arm"])
        t0, t1 = r["t_sim_s"], done.get(r["job"], r["t_sim_s"])
        ax.barh(y, t1 - t0, left=t0, color=cmap(jobs.index(r["job"]) % 20), edgecolor="black", linewidth=0.4)
        ax.text(t0 + (t1 - t0) / 2, y, r["job"], ha="center", va="center", fontsize=7)
    refs = [r for r in recs if r["kind"] == "proposal" and not r["ok"]]
    for r in refs:
        ax.plot(r["t_sim_s"], arms.index(r["arm"]) + 0.38, "x", color="crimson", ms=5)
    ax.set_yticks(range(len(arms)), arms)
    ax.set_xlabel("simulated time (s)   (x = refuted proposal)")
    ax.set_title(start["scenario"])
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    print("timeline:", path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rehearsal")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("doctor", "run"):
        s = sub.add_parser(name)
        s.add_argument("--config", default="config/rehearsal.yaml")
    r = sub.choices["run"]
    r.add_argument("--scenario", default="config/rehearsal/hopper_P2_s0.5.yaml")
    r.add_argument("--system", default="config/system.yaml")
    r.add_argument("--mock", action="store_true", help="offline scripted agents")
    r.add_argument("--mistakes", type=float, default=0.0, help="mock only: chance of a deliberate error")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--models", nargs="+", help="override model fallback list")
    r.add_argument("--max-calls", type=int, default=None)
    r.add_argument("--out", default=None)
    r.add_argument("--report", action="store_true")
    rep = sub.add_parser("report")
    rep.add_argument("log")
    rep.add_argument("--png", default=None)
    a = ap.parse_args(argv)
    return {"doctor": cmd_doctor, "run": cmd_run, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
