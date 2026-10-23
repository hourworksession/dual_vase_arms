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

## Duct-mismatch case study (intake test and route comparison)

The scenario: a hopper outlet is drawn correctly for one duct standard while the
site uses the other. Metric spiral duct (EN 1506) is 250 mm; US spiral duct is
10 in. (254 mm). The outlet slides inside the duct, so the spigot is 248.5 mm for
a 250 mm duct and 9.94 in. (252.5 mm) for a 10 in. duct. Parts are made exactly
to the drawing and pass QA, because QA checks against the drawing: the error only
shows at installation (verification passes, validation fails).

### Intake test (`config/intake/duct_cases.yaml`)

| Case | Documents | Right answer |
| --- | --- | --- |
| C1 | metric drawing + order naming a 10 in. duct | conflict, loose, recommend about 252.5 mm |
| C2 | metric drawing + order with no interface | unknown, ask |
| C3 | metric drawing + order naming a 250 mm duct | consistent (control) |
| C4 | metric drawing + plain order + site survey showing a 10" duct | conflict, loose |
| C5 | imperial drawing + European order for a 250 mm duct | conflict, will not fit, recommend about 248.5 mm |

    python -m rehearsal intake --baseline technician ideal     # offline
    python -m rehearsal intake --models openai/gpt-4o-mini google/gemini-2.5-flash

The summary's `p_catch` (conflicts caught out of three) feeds the route comparison.

### Route comparison (`config/routes/duct_case.yaml`)

Traditional: extrude on the die, cut, QA, ship; if the mismatch is missed, return,
then expand on a mandrel in a second cell (loose) or remake on the right die
(will not fit). Cell: one job order; if missed, the same order gains a job per
part: add outer beads round the spigot (loose) or cut the spigot off and reprint
it (will not fit; additive cannot remove material).

    python -m rehearsal routes --p-catch 0.8
    python -m rehearsal routes --direction will_not_fit --on-site

Every figure in the YAML is a placeholder assumption to be replaced.

### Repeated runs into Excel

    pip install openpyxl
    python -m rehearsal batch --models openai/gpt-4o-mini google/gemini-2.5-flash --repeats 30

Runs every case `--repeats` times per model in both modes (`--modes direct extract`),
four calls at a time (`--workers`), and writes `runs/intake_batch_<time>.xlsx`:
Summary (p_catch, p_fix with a 95% Wilson interval, false-alarm rate, cost, grade),
By case, Direct vs extract, Answers (one row per call; the other sheets are formulas
over it), Settings (thresholds and run settings) and Prompts and cases. Ctrl+C stops
and still saves what finished.
