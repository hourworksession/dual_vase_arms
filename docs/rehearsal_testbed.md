# Rehearsal test bed

Two extruder agents (one per arm) propose jobs for a part; a deterministic referee
accepts or refutes each proposal with a rule id; a simple world model simulates the
build. Every call is logged so runs can be compared across models.

## Setup (laptop)

1. `pip install -e .` (or run with `PYTHONPATH=src`).
2. Create `.env` in the repo root (already gitignored):
   `OPENROUTER_API_KEY=sk-or-...`
3. `python -m rehearsal doctor` checks the key and credit, confirms the models in
   `config/rehearsal.yaml` support structured outputs, and lists free ones.

## Runs

| Command | What it does |
| --- | --- |
| `python -m rehearsal run --mock --report` | Offline baseline, no key |
| `python -m rehearsal run --mock --mistakes 0.4 --report` | Offline, agents make errors the referee must catch |
| `python -m rehearsal run --report` | Live, models from the config |
| `python -m rehearsal run --models openai/gpt-4o-mini --report` | Live, one model |
| `python -m rehearsal report runs/<file>.jsonl` | Table and timeline PNG for any run |

A local model works the same way: set `base_url` to `http://localhost:11434/v1`
(Ollama) or `http://localhost:1234/v1` (LM Studio) and `models` to the local name.

## What to compare between models

- `completed`, `makespan_s` and `speedup` against the single-arm baseline: did the
  greedy arm find the interweave?
- `first_try_accept_rate` and `rule_hits`: how often the model breaks which rule.
- `calls`, `tokens`, `cost_usd`, latency in the log: what a decision costs.

## Referee rules

R0 action · R1 arm busy · R2 job exists · R3 job state · R4 dependencies · R5 as-built
height · R6 arm slots · R7 interweave flag · R8 station given · R9 surface speed limit ·
R10 reach · R11 workspace · R12 nozzle gap · R13 interweave offset.

## Simplifications in this first version

Nozzles sit at fixed world stations while the disc turns; wall rise is pitch x arms
per revolution; the slowest arm sets the disc speed. No orientation, collar tilt,
collision meshes or Klipper/xArm verification yet: those are the next referee layers
(`set_only_check_type` for the arms, Klipper batch mode for the extruders).
