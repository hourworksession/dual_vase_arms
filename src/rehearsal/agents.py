"""Extruder agents: each arm is roleplayed by one LLM agent.

The agent sees the blackboard snapshot and proposes one action. If the referee
refutes it, the refutation is appended to the conversation and the agent tries
again (up to ``max_retries``), otherwise it waits this round.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any

PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["start_job", "join_job", "wait", "park"]},
        "job_id": {"type": ["string", "null"]},
        "station_deg": {"type": ["number", "null"],
                        "description": "world angle of the nozzle on the wall, 0 = +X"},
        "surface_speed_mm_s": {"type": ["number", "null"]},
        "interweave": {"type": "boolean"},
        "rationale": {"type": "string", "description": "one or two sentences"},
        "confidence": {"type": "number", "description": "0 to 1"},
    },
    "required": ["action", "job_id", "station_deg", "surface_speed_mm_s",
                 "interweave", "rationale", "confidence"],
}

ROLE_TEXT = {
    "primary": (
        "You are the PRIMARY arm. Your duty is to keep the part growing: whenever you are idle, "
        "start the next job whose dependencies are done, from your default station."
    ),
    "greedy": (
        "You are the GREEDY arm. You have no fixed duty. Whenever you are idle, look for work that "
        "finishes the part sooner: join an active job that has a free arm slot (interweaving with "
        "your partner), or start a pending job that is ready. If nothing helps, wait or park."
    ),
}

SYSTEM_TEMPLATE = """You control one extruder arm in a dual-arm robotic 3D printing cell.
A disc turns continuously under two nozzles; each nozzle sits at a fixed world angle (its station)
on the wall radius and lays a spiral bead as the disc turns. The part is built as a list of jobs.

{role}

Your arm: {arm}. Default station: {station} deg.

Actions:
- start_job: begin a pending job (its dependencies must be done and it must start at the as-built top).
- join_job: help on an active job with a free slot; set interweave=true and sit about 180 deg from your partner.
- wait: do nothing this round.  park: retreat (only when idle).

Rules a deterministic referee enforces (a refuted proposal comes back to you with the rule id):
reach from your base, minimum gap to the other nozzle, surface speed at or below the extruder limit,
dependencies, as-built height, free arm slots, interweave geometry.

Reply with ONE JSON object matching the schema. Use null for fields that do not apply."""


@dataclass
class Agent:
    arm: str
    role: str
    station_deg: float
    client: Any
    max_retries: int = 2
    history: list[dict] = field(default_factory=list)

    def system_prompt(self) -> str:
        return SYSTEM_TEMPLATE.format(role=ROLE_TEXT[self.role], arm=self.arm,
                                      station=self.station_deg)

    def messages(self, snapshot: dict, refutations: list[str]) -> list[dict]:
        msgs = [{"role": "system", "content": self.system_prompt()},
                {"role": "user", "content": "Cell state:\n" + json.dumps(snapshot)}]
        for r in refutations:
            msgs.append({"role": "user", "content": "Refuted by the referee: " + r +
                         "\nPropose a corrected action."})
        return msgs


# ----------------------------------------------------------------------
# Offline policy for MockClient: a sensible baseline, optionally error-prone
def _snapshot_from(messages: list[dict]) -> dict:
    for m in messages:
        if m["role"] == "user" and m["content"].startswith("Cell state:"):
            return json.loads(m["content"].split("\n", 1)[1])
    raise ValueError("no snapshot in messages")


def _arm_from(messages: list[dict]) -> str:
    sys = messages[0]["content"]
    return sys.split("Your arm: ", 1)[1].split(".", 1)[0]


def mock_policy(mistake_rate: float = 0.0, seed: int = 0):
    rng = random.Random(seed)

    def policy(messages: list[dict]) -> dict:
        snap = _snapshot_from(messages)
        arm = _arm_from(messages)
        me = snap["arms"][arm]
        refuted = any(m["content"].startswith("Refuted") for m in messages)
        speed = snap["part"]["max_surface_speed_mm_s"]
        base = {"job_id": None, "station_deg": None, "surface_speed_mm_s": None,
                "interweave": False, "confidence": 0.8}
        if me["status"] == "working":
            return {**base, "action": "wait", "rationale": "busy"}
        jobs = snap["jobs"]
        done = {j["id"] for j in jobs if j["status"] == "done"}
        top = snap["as_built_top_mm"]
        ready = [j for j in jobs if j["status"] == "pending" and set(j["deps"]) <= done
                 and abs(j["z0"] - top) < 1e-6]
        open_active = [j for j in jobs if j["status"] == "active" and len(j["arms"]) < j["max_arms"]]
        station = me["default_station_deg"]
        make_mistake = not refuted and rng.random() < mistake_rate
        if me["role"] == "greedy" and open_active:
            j = open_active[0]
            p = {**base, "action": "join_job", "job_id": j["id"], "station_deg": station,
                 "surface_speed_mm_s": speed, "interweave": True,
                 "rationale": f"{j['id']} has a free slot; interweaving halves its time"}
            if make_mistake:
                p["interweave"] = False
            return p
        if ready:
            j = ready[0]
            p = {**base, "action": "start_job", "job_id": j["id"], "station_deg": station,
                 "surface_speed_mm_s": speed,
                 "rationale": f"{j['id']} is ready at the as-built top"}
            if make_mistake:
                p["surface_speed_mm_s"] = speed * 1.5
            return p
        return {**base, "action": "wait", "rationale": "nothing ready"}

    return policy
