# dual_vase_arms

Control software for the dual-arm robotic AM cell: two UFactory xArm 850 arms with E3D
Hemera extruders (Revo HF 1.0 mm), an Aerotech ADRS turntable (Automation1), and Klipper
driving the extruders. Part of the PhD on LLM-driven, agentic toolpath generation.

Working branch: `lab-merged`. Run everything from the repo root.

## Printing tests: `cylinder_test.py`

The current way to print on the cell: prime, opening circle, spiral, closing circle, on one
arm or both.

    python3 cylinder_test.py T=2 R=60 VB=0.55 FLOW_K=1.3 REVS=40 MODE=layered --dry-run

- `T=0` right, `T=1` left, `T=2` both. `MODE=layered`, `side` or `merge` for two arms.
- `E=` (filament mm/s, bed follows) or `VB=` (bed rad/s, extruders follow).
- Throttle tests: `E_MIN`, `E_MAX`, `WAVE_T` or `WAVES_PER_REV`, `SHAPE=sine|square|triangle`.
- Per-arm calibration lives in `DEFAULTS` at the top (Z0, centre, left radius trim `DR`).
- Every run logs to `runs/cylinder_test_<time>.csv` (commanded vs reported arm position,
  flow, hot-end temperature). The full list of settings is at the top of the file.

Always `--dry-run` first. `--sim` runs against simulated hardware.

## Calibration and extruders

| File | What it does |
| --- | --- |
| `cali_jog.py` | Jog each arm to the turntable centre (X/Y/Z); prints CX, CY, Z0 |
| `cylinder_test.py --find-z0` | Paper-drag touch-off for an arm's zero height |
| `extrude.py` | Terminal extruder control: heat, purge, on/off, flow (`python3 -i extrude.py`) |
| `scripts/extruder_test_panel.py` | Small Qt panel for the same |
| `Klipper Configurations/` | The Pi's Klipper configs; `extrude_control.cfg` runs both extruders as G-code axes A/B (see its README) |

## Other parts

| Folder | What it is |
| --- | --- |
| `scripts/cell_studio/` | The Qt control panel (`python3 scripts/gleadell_panel_qt.py`); base for the coming panel refresh |
| `scripts/` (`slicer.py`, `planner.py`, `features.py`, `rules.py`, ...) | Slicer, motion planner and print rules used by Cell Studio |
| `generators/` | FullControl generator scripts Cell Studio can run |
| `scripts/cellcam/` | Camera Pi stream server with an alignment crosshair (see its README) |
| `src/` | Arm, turntable and extruder controllers; `src/rehearsal/` is the LLM test bed (`docs/rehearsal_testbed.md`) |
| `experiments/voron0/` | FullControl experiments printed on a Voron 0.2 first (e.g. `wall_imprint_v1.py`) |
| `cad/` | Part geometry (hopper) |
| `config/` | Cell settings, calibration, print rules, rehearsal configs |
| `simulation/assets/` | Arm and tool meshes used by Cell Studio's 3D view |
| `tests/` | `python3 -m pytest tests` |

Older code (the Tk panels, the `dual_arm_printer` package, the PyBullet twin, early
one-off scripts) was removed on 3 Nov 2026; it is all still in the git history
(`git log --diff-filter=D --name-only` lists it, `git checkout <commit>^ -- <path>` brings a file back).

## Setup

    pip install -r requirements.txt      # pip install --break-system-packages on some Linux systems

The rehearsal test bed needs an OpenRouter key in `.env` (never commit it).
