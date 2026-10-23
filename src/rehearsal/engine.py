"""Rehearsal loop: agents propose, the referee judges, the world simulates.

Every proposal, verdict and model call is written to a JSONL log so a run can
be replayed and compared across models.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from .agents import PROPOSAL_SCHEMA, Agent
from .llm import LLMError
from .referee import check
from .world import World


@dataclass
class RunStats:
    proposals: int = 0
    accepted: int = 0
    first_try: int = 0
    decisions: int = 0
    refuted: int = 0
    llm_errors: int = 0
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    rule_hits: dict[str, int] = field(default_factory=dict)


class Rehearsal:
    def __init__(self, world: World, agents: list[Agent], log_path: str,
                 max_calls: int = 60, max_rounds: int = 200):
        self.world = world
        self.agents = sorted(agents, key=lambda a: a.role != "primary")  # primary asks first
        self.log_path = log_path
        self.max_calls = max_calls
        self.max_rounds = max_rounds
        self.stats = RunStats()
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        self._log = open(log_path, "w", encoding="utf-8")

    def log(self, kind: str, **data) -> None:
        rec = {"kind": kind, "t_sim_s": round(self.world.t, 2), "wall_time": time.time(), **data}
        self._log.write(json.dumps(rec) + "\n")
        self._log.flush()

    # ------------------------------------------------------------------
    def ask(self, agent: Agent) -> dict | None:
        """One decision for one agent, with refute-and-retry. Returns the accepted proposal."""
        refutations: list[str] = []
        for attempt in range(agent.max_retries + 1):
            if self.stats.calls >= self.max_calls:
                self.log("budget_exhausted", arm=agent.arm)
                return None
            msgs = agent.messages(self.world.snapshot(), refutations)
            try:
                reply = agent.client.complete_json(msgs, PROPOSAL_SCHEMA, "arm_proposal")
            except (LLMError, ValueError, KeyError) as e:
                self.stats.llm_errors += 1
                self.log("llm_error", arm=agent.arm, attempt=attempt, error=str(e)[:300])
                return None
            self.stats.calls += 1
            self.stats.prompt_tokens += reply.prompt_tokens
            self.stats.completion_tokens += reply.completion_tokens
            self.stats.cost_usd += reply.cost_usd or 0.0
            p = reply.content
            self.stats.proposals += 1
            v = check(self.world, agent.arm, p)
            self.log("proposal", arm=agent.arm, role=agent.role, attempt=attempt, model=reply.model,
                     proposal=p, ok=v.ok, refutations=v.refutations,
                     tokens=[reply.prompt_tokens, reply.completion_tokens],
                     cost_usd=reply.cost_usd, latency_s=round(reply.latency_s, 2))
            if attempt == 0:
                self.stats.decisions += 1
            if v.ok:
                self.stats.accepted += 1
                if attempt == 0:
                    self.stats.first_try += 1
                return p
            self.stats.refuted += 1
            for rule, _ in v.refutations:
                self.stats.rule_hits[rule] = self.stats.rule_hits.get(rule, 0) + 1
            refutations.append(v.text())
        return None

    def run(self) -> dict:
        w = self.world
        self.log("start", scenario=w.name, snapshot=w.snapshot())
        stalled_rounds = 0
        for rnd in range(self.max_rounds):
            if w.all_done():
                break
            acted = False
            for agent in self.agents:
                if w.state[agent.arm].status == "working":
                    continue
                p = self.ask(agent)
                if p and p["action"] in ("start_job", "join_job", "park"):
                    w.apply(agent.arm, p)
                    acted = True
                    self.log("applied", arm=agent.arm, action=p["action"], job=p.get("job_id"),
                             station_deg=p.get("station_deg"))
            finished = w.advance()
            for jid in finished:
                j = w.jobs[jid]
                self.log("job_done", job=jid, started_s=round(j.started_s or 0, 1),
                         finished_s=round(j.finished_s or 0, 1))
            if not finished and not acted:
                stalled_rounds += 1
                if stalled_rounds >= 3 or self.stats.calls >= self.max_calls:
                    self.log("stalled", reason="no progress for 3 rounds or budget spent")
                    break
            else:
                stalled_rounds = 0
        summary = self.summary()
        self.log("summary", **summary)
        self._log.close()
        return summary

    def baseline_single_arm_s(self) -> float:
        w = self.world
        revs = sum(j.height for j in w.jobs.values()) / w.pitch
        return revs * w.rev_time(w.max_surface_speed)

    def summary(self) -> dict:
        w, s = self.world, self.stats
        base = self.baseline_single_arm_s()
        return {
            "completed": w.all_done(),
            "makespan_s": round(w.t, 1),
            "single_arm_baseline_s": round(base, 1),
            "speedup": round(base / w.t, 3) if w.t and w.all_done() else None,
            "arm_busy_s": {n: round(st.busy_s, 1) for n, st in w.state.items()},
            "proposals": s.proposals, "accepted": s.accepted, "refuted": s.refuted,
            "first_try_accept_rate": round(s.first_try / s.decisions, 3) if s.decisions else None,
            "llm_errors": s.llm_errors, "calls": s.calls,
            "tokens": [s.prompt_tokens, s.completion_tokens],
            "cost_usd": round(s.cost_usd, 5),
            "rule_hits": s.rule_hits,
        }
