"""Offline tests for the rehearsal test bed (no network, no key)."""
import io
import json
import os
import sys
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rehearsal.agents import PROPOSAL_SCHEMA, Agent, mock_policy  # noqa: E402
from rehearsal.engine import Rehearsal  # noqa: E402
from rehearsal.llm import MockClient, OpenRouterClient, parse_json_object  # noqa: E402
from rehearsal.referee import check  # noqa: E402
from rehearsal.world import World  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCEN = os.path.join(ROOT, "config", "rehearsal", "hopper_P2_s0.5.yaml")
SYS = os.path.join(ROOT, "config", "system.yaml")


def world():
    return World.from_yaml(SCEN, SYS)


def prop(**kw):
    p = {"action": "start_job", "job_id": "J1", "station_deg": 0.0, "surface_speed_mm_s": 30.0,
         "interweave": False, "rationale": "", "confidence": 0.9}
    p.update(kw)
    return p


def test_valid_start_accepted():
    assert check(world(), "right", prop()).ok


@pytest.mark.parametrize("kw,rule", [
    (dict(job_id="J2"), "R4"),                       # dependency not done
    (dict(surface_speed_mm_s=45.0), "R9"),           # over extruder limit
    (dict(job_id="J9"), "R2"),                       # no such job
    (dict(action="fly"), "R0"),
])
def test_refutations(kw, rule):
    v = check(world(), "right", prop(**kw))
    assert not v.ok and rule in [r for r, _ in v.refutations]


def test_reach_refuted_for_far_side_of_large_part():
    w = world()
    w.radius = 290.0                                  # far side is 990 mm from the right base
    v = check(w, "right", prop(station_deg=180.0))
    assert "R10" in [r for r, _ in v.refutations]


def test_join_rules_gap_interweave_and_offset():
    w = world()
    w.apply("right", prop())                          # right working J1 at station 0
    w.jobs["J1"].max_arms = 2
    same = check(w, "left", prop(action="join_job", station_deg=0.0, interweave=True))
    assert "R12" in [r for r, _ in same.refutations]
    no_iw = check(w, "left", prop(action="join_job", station_deg=180.0, interweave=False))
    assert "R7" in [r for r, _ in no_iw.refutations]
    skew = check(w, "left", prop(action="join_job", station_deg=100.0, interweave=True))
    assert "R13" in [r for r, _ in skew.refutations]
    assert check(w, "left", prop(action="join_job", station_deg=180.0, interweave=True)).ok


def _run(mistakes, tmp_path, seed=1):
    w = world()
    agents = [Agent(n, s.role, s.default_station_deg,
                    MockClient(mock_policy(mistakes, seed=seed + i)))
              for i, (n, s) in enumerate(w.arms.items())]
    return Rehearsal(w, agents, str(tmp_path / "run.jsonl")).run()


def test_mock_run_completes_and_greedy_arm_speeds_up(tmp_path):
    s = _run(0.0, tmp_path)
    assert s["completed"] and s["refuted"] == 0
    assert 1.8 < s["speedup"] < 2.0                   # base ring is single-arm, rest interweaved


def test_mistakes_are_caught_and_recovered(tmp_path):
    s = _run(0.5, tmp_path, seed=3)
    assert s["completed"] and s["refuted"] > 0
    assert set(s["rule_hits"]) <= {"R7", "R9"}


def test_openrouter_payload_and_reply(monkeypatch):
    c = OpenRouterClient(models=["a/one", "b/two"], api_key="sk-test")
    payload = c.build_payload([{"role": "user", "content": "x"}], PROPOSAL_SCHEMA, "arm_proposal")
    assert payload["models"] == ["a/one", "b/two"]
    assert payload["provider"] == {"require_parameters": True}
    assert payload["response_format"]["json_schema"]["strict"] is True

    reply = {"model": "b/two", "choices": [{"message": {"content": json.dumps(prop())}}],
             "usage": {"prompt_tokens": 120, "completion_tokens": 40, "cost": 0.00002}}
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        seen["auth"] = req.headers.get("Authorization")
        seen["body"] = json.loads(req.data)
        return Resp(json.dumps(reply).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    r = c.complete_json([{"role": "user", "content": "x"}], PROPOSAL_SCHEMA, "arm_proposal")
    assert r.model == "b/two" and r.content["job_id"] == "J1" and r.cost_usd == 0.00002
    assert seen["auth"] == "Bearer sk-test" and seen["body"]["models"] == ["a/one", "b/two"]


def test_local_endpoint_uses_single_model_and_no_key():
    c = OpenRouterClient(models=["qwen-local"], base_url="http://localhost:11434/v1")
    p = c.build_payload([], PROPOSAL_SCHEMA, "x")
    assert p["model"] == "qwen-local" and "models" not in p and "provider" not in p


def test_parse_json_tolerates_fences():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
