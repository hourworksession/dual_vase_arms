# dual_arm_printer

Slice → split → simulate → drive: a Python control stack for cooperative 3D printing
on an Aerotech ADRS rotary stage (300 mm diameter acrylic disc) with two UFactory xArm 850
manipulators carrying Hemera direct-drive extruders.

## What it does

1. **Slice.** Generate G-code for the desired part using FullControl GCode Designer
   (Gleadall, 2021). FullControl is chosen over STL slicers because it natively expresses
   toolpaths as Python objects, which is essential for a non-planar / rotary / dual-arm cell.
2. **Reconstruct.** Parse the G-code back into a structured toolpath
   (`TaskGraph` of segments, with extrusion and motion metadata).
3. **Split.** Decompose the toolpath into two arm-specific subplans using a configurable
   strategy (default: 180°-offset dual spiral in the disc's polar frame). Other strategies
   in the package: Reeb decomposition (Khatkar 2024), SafeZone graph scheduling (Stone 2025),
   and Pivot-Move handoffs (Li 2026).
4. **Coordinate.** Generate a synchronized motion plan: per-arm trajectories in the
   *world* frame, plus a turntable angular profile. Each arm's path is recomputed for the
   instantaneous disc orientation so that the deposition lands at the right `(r, θ, z)` in
   the disc-local frame.
5. **Simulate.** Replay the plan in PyBullet using the xArm 850 URDFs, the ADRS turntable,
   and the Hemera end-effector mesh. Collision-check between arms, between arms and disc,
   and between arms and deposited material.
6. **Execute.** Stream the trajectories to the two arms via `xArm-Python-SDK` and the
   turntable via its serial/Ethernet interface.

## Repository layout

See `docs/architecture.md` for the full file tree and per-file purpose. The
recommendation report (`docs/RECOMMENDATIONS.md`) explains the literature each
component is grounded in.

## Quick start

```bash
pip install -e .

# 1. Generate a part with FullControl
python -m dual_arm_printer slice examples/01_cylinder.py -o build/cylinder.gcode

# 2. Reconstruct and split into two arm plans
python -m dual_arm_printer split build/cylinder.gcode \
    --strategy dual_spiral --config config/splitter/dual_spiral.yaml \
    -o build/cylinder.plan.json

# 3. Visualize in PyBullet
python -m dual_arm_printer simulate build/cylinder.plan.json

# 4. (Optional) drive real hardware
python -m dual_arm_printer run build/cylinder.plan.json --confirm
```

## Status

This is an MVP scaffold. Modules marked `# STUB` need hardware-side validation.
The slicing → splitting → simulation pipeline is runnable end-to-end on the included
cylinder example.

## Control panel (Cell Studio)

The day to day control panel is a Qt app:

```bash
pip install PySide6 pyyaml          # plus trimesh shapely networkx scipy for Model print
python scripts/gleadell_panel_qt.py
```

* **Machine**: connections (with a *Simulated hardware* option for rehearsal), Home,
  Prepare to print, jog, live offsets, extruder priming.
* **Cylinder**: the dual arm cylinder print, polar preview / turntable simulator, presets.
A start-up window opens at once and shows each library and page as it loads; missing optional
parts (slicer libraries, FullControl, camera, xArm SDK, turntable driver) are listed there and in
the log console, and an error that stops the panel opening stays on that window.

* **Model print**: import → slice → print / dry run. The preview has a **Simulation** tab that
  replays the plan from above: the disc turns, the nozzle moves and the part builds up layer by
  layer (play / pause, scrub, 1× to 200×). Generators has the same tab. No hardware needed.
* **Generators**: toolpaths from code. FullControl designs (generate() functions or
  ordinary scripts that call `fc.transform`), colleagues' Python, or G-code files. Set
  parameters, preview in 3D, then plan / dry run / print through the same planner as
  Model print. See `generators/README.md`. FullControl itself:
  `pip install git+https://github.com/FullControlXYZ/fullcontrol`
* **Macros**:
  1. *Merge calibration*: the right arm prints a reference ring and the left arm prints the same layer
     while sweeping across it. A side camera scans the beads and works out where the left bead lands on
     the right's (radial offset and height), and how many pixels per mm the camera sees. Then print
     confirmation spirals.
  2. *Dual-arm vase*: two FullControl spirals (`generators/macros/dual_vase_interweave.py`), one per arm,
     sharing one wall (interwoven when the waves are in antiphase). Uses the calibration.
  3. *George's code as the slicer*: any script with `slice(model_path, ...)`, or a `MODEL_PATH = "…stl"`
     constant, slices a model in Generators; the right arm prints it.
  Camera: `pip install opencv-python` (detection itself needs only numpy/scipy).
* **Settings ▸ Tool and nozzle**: nozzle size, default line width / layer height, and the
  orientation sent to EACH arm. New mounts (Oct 2026, Revo High Flow 1.2 mm, flange horizontal,
  nozzle straight down): right roll 90 / pitch -90 / yaw 90, left roll 0 / pitch 90 / yaw 0.
  Pitch ±90 is the xArm's roll/yaw gimbal lock, so the arm may report a different but equivalent
  roll/yaw; the panel always commands these. Turntable centre defaults (Cylinder ▸ centre) are the
  new-mount values: right 508.18, -22.88, Z 107.4; left 512.1, -8.4, Z 110.2.
* **Live panel** (always visible): arm poses, turntable angle, temperatures, job timer,
  and the live turntable speed slider.
* **Settings ▸ Home positions**: a custom home per arm (joint angles or Cartesian pose,
  with "Use current position"). Home goes there instead of the xArm factory home.
  Stored in `config/home_positions.json`.

3D digital twin (PyBullet, `simulation/`): the arms are drawn with UFACTORY's real 850
geometry (`simulation/assets/uf850/`, converted from the official STEP by
`python -m simulation.step_to_link_meshes <file.STEP>`) on the official joint chain.

User scenario tests (simulated hardware, no cell needed):
`python tests/test_cell_studio_scenarios.py` (add `--mode streamed` for follow-turntable extrusion).

The machine logic is in `scripts/cell_studio/controller.py`, ported unchanged from the
Tk panel. The old `scripts/gleadell_panel.py` still works.
