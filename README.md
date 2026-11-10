# dual_vase_arms

Control software for the dual-arm robotic AM cell: two UFactory xArm 850 arms with E3D
Hemera extruders (Revo HF 1.0 mm), an Aerotech ADRS turntable (Automation1), and Klipper
driving the extruders. Part of the PhD on LLM-driven, agentic toolpath generation.

Working branch: `lab-merged`. Run everything from the repo root.

## Layout

    src/cell/            the package everything imports (`import cell`)
      hardware/          arm.py, turntable.py, extruder.py (Klipper macros), extruder_binary.py, extruder_legacy.py
      config.py          load_config() for config/settings.yaml
      runs/              the printing tests: cylinder.py, square_indexing.py, square_polar.py
      program.py         speed-and-time programs: tracks of (speed, seconds) per device, a Player and a simulator
      shapes.py          outlines compiled into programs (cylinder, rounded square, rounded polygon) and manual actions
      ironing.py         Nino's ironing zones and motifs (line, zigzag, spiral) compiled into a bed + arm program
    src/rehearsal/       the LLM rehearsal test bed (docs/rehearsal_testbed.md)
    notebooks/           the same processes laid out part by part: 00 cell control, 01 rounded square, 02 column and ironing
    ui/web/              the browser panel: python3 ui/web/server.py --sim  then open http://localhost:8000
    ui/cell_studio/      the Qt studio (simulators, 3D view, generators);  ui/panels/  gleadell_panel_qt.py, extruder_test_panel.py
    tools/               extrude.py (terminal extruder control), cali_jog.py (centre and Z0 jog)
    scripts/             toolpath libraries used by Cell Studio: slicer, planner, rules, features, spiralfit;  cellcam/
    tests/               geometry/ (pure maths), rehearsal/, ui/ (Qt, needs PySide6);  python3 -m pytest tests
    config/ generators/ cad/ experiments/ simulation/ docs/ Klipper Configurations/   as before

`cylinder_test.py`, `square_test.py` and `square_polar_test.py` at the root are one-line shims onto
`src/cell/runs/`, so the usual commands still work. `pip install -e .` once and `import cell` works
from anywhere (the notebooks and shims add `src/` to the path themselves, so it is optional).

## Printing tests (`src/cell/runs/`)

Cylinder: prime, opening circle, spiral, closing circle, one arm or both.

    python3 cylinder_test.py T=2 R=60 VB=0.55 FLOW_K=1.3 REVS=40 MODE=layered --dry-run

- `T=0` right, `T=1` left, `T=2` both. `MODE=layered`, `side` or `merge` for two arms.
- `E=` (filament mm/s, bed follows) or `VB=` (bed rad/s, extruders follow).
- Throttle tests: `E_MIN`, `E_MAX`, `WAVE_T` or `WAVES_PER_REV`, `SHAPE=sine|square|triangle`.
- Per-arm calibration lives in `DEFAULTS` at the top of `cylinder.py` (Z0, centre, left radius trim `DR`).

Rounded square, bed as master (nothing stops; the arm only moves radially, the bed speed follows
the outline, the extruder follows the measured bed speed):

    python3 square_polar_test.py T=0 SIDE=80 RF=10 L_H=0.6 FLOW_K=1.3 V=8 BED_ACC=500 LOOPS=10 --dry-run

Rounded square by indexing (straights with the bed still, fillets by 90 deg bed moves), kept for comparison:

    python3 square_test.py T=0 SIDE=80 RF=10 L_H=0.6 FLOW_K=1.3 V_LINE=8 VB=0.5 LOOPS=10 --dry-run

Every run logs to `runs/<test>_<time>.csv`. The full list of settings is at the top of each file.
Always `--dry-run` first. `--sim` runs against Cell Studio's simulators; `--cold` moves without plastic.

## Calibration and extruders

| File | What it does |
| --- | --- |
| `tools/cali_jog.py` | Jog each arm to the turntable centre (X/Y/Z); prints CX, CY, Z0 |
| `cylinder_test.py --find-z0` | Paper-drag touch-off for an arm's zero height |
| `tools/extrude.py` | Terminal extruder control: heat, purge, on/off, flow (`python3 -i tools/extrude.py`) |
| `ui/panels/extruder_test_panel.py` | Small Qt panel for the same |
| `notebooks/00_cell_control.ipynb` | The same from a notebook: jog, bed, heat, purge, STOP |
| `Klipper Configurations/` | The Pi's Klipper configs; `extrude_control.cfg` runs both extruders as G-code axes A/B (see its README) |

Older code (the Tk panels, the `dual_arm_printer` package, the PyBullet twin, early
one-off scripts) was removed on 3 Nov 2026; it is all still in the git history
(`git log --diff-filter=D --name-only` lists it, `git checkout <commit>^ -- <path>` brings a file back).

## Setup

    pip install -r requirements.txt      # pip install --break-system-packages on some Linux systems

The rehearsal test bed needs an OpenRouter key in `.env` (never commit it).
