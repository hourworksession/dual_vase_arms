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

* **Model print**: import → slice → print / dry run, with **both arms** (Machine + motion ▸ Arms = 2,
  the default): walls around the axis are split in half, one half per arm, printed at the same time
  while the disc turns (halves alternate so the disc never swings back); features mirrored through
  the axis are printed together, one per arm; everything else by the right arm while the left
  waits. Right arm = tool 0, left = tool 1. Trug: 71 min with one arm, 47 with two.
  Two carpenters on a log: an arm with nothing to do backs off to its own side, outside and above
  the part; the planner never lets the nozzles come within 45 mm (the left arm waits if it would);
  default home puts each arm 250 mm back on its own side (the old joints put both nozzles over the
  disc centre). The tool bodies (Revo mount from `simulation/assets/tool/revo_mount_new.3mf` +
  extruder) are drawn on the flanges and their real outlines drive the clearance guard
  (`hardware.tool_margin`); the nozzle offsets (`tcp_right` / `tcp_left` in Settings ▸ Tool and
  nozzle, config/tool.json) are ESTIMATED from the mount model and must match each controller's
  TCP offset. The preview has a **Simulation** tab that
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
* **Settings ▸ Print rules** (`config/print_rules.yaml`, engine `scripts/rules.py`): the decisions
  about HOW a part is printed, in one file that is also the contract for the rule-based AI layer.
  Every rule has an id, a reason and an allowed range; `RuleSet.propose()` only accepts values in
  range, and every planner decision is logged with the rule that made it.
  - *Turntable*: the bed turns only for round paths around the axis. Squares, off-centre features
    and fill are drawn by the arm on a held bed (turned to face the arm first if far round).
    The trug: 3,712 turntable reversals → 552.
  - *Thin features*: parts narrower than one line print as one line down their middle, as wide as
    the feature, with less plastic (instead of being dropped).
  - *Hardware* limits and *tactics*: the manoeuvres the cell can use per region (polar wall, held
    Cartesian, thin centreline in use; dual-arm mirror, spiral vase, spiral brick with tilt, tilted
    overhang previewed; radial winding, conformal top planned). The slice is split into *cells*
    (a region carried up through layers); each cell's measurements and chosen tactic are written to
    `scripts/cells_<model>.json` for the AI to learn from.
  - *Non-planar bands*: which layers would be flat, spiral or spiral-brick (reported, not generated yet).
* **Spiral first** (`scripts/spiralfit.py`, Layer strategy *spiral*, the default of *auto* whenever
  the model has a wall round the turntable axis): the wall is not cut into layers. A ray from the
  axis at every degree and height finds the outside and inside surfaces; each bead follows its own
  surface as one continuous helix, stopping where the surface stops (slot, top) and restarting
  where it reappears. Two beads: the left arm runs the inner one ahead by half a layer plus
  lw·tan(cone angle), the right lays the outer on its shoulder with the nozzle leaning outward (the
  build grows from the axis outward); one bead: two interleaved helices at pitch 2·lh. Walls under
  ~35 mm radius cannot take two tools facing each other, so one arm does both beads, alternate laps.
  Where the wall is thinner than two full beads (a TinkerCAD 2.2 mm wall at 1.3 mm lines) the beads
  are squeezed narrower rather than dropped, and short dropouts are bridged, so a 0.1 mm thickness
  wobble does not break the helix. Posts and bores are local helices: one continuous helix over the
  run of layers where the feature is unchanged (see Features), a lap per layer elsewhere; only the
  bottom and top skins are flat. Points carry (x, y, z, width, height, tilt) and the planner turns
  the tilt into each arm's roll/pitch/yaw.
* **Features** (`scripts/features.py`, run after every slice, in the report): what in the model is a
  primitive the toolpath can be built from. Every solid piece and hole of every layer is matched to
  the layer below; a run of unchanged layers is a prism, classified as the FullControl primitive that
  draws it (circle → `helixZ`, rectangle → `rectangleXY`, regular polygon → `polygonXY`, else the
  vertex list) with its centre, size and z range. Then the mesh is cut by half-planes through the
  turntable axis every 0.6 mm of arc at the largest radius; angle ranges where the (r, z) profile
  is unchanged are surfaces of revolution (the spiral, both arms), the rest are local features with
  their angle and height range. `features.FeatureMap.fullcontrol()` prints the primitives as
  FullControl calls.
* **Print order** per layer (rule `print.order`): the wall round the axis first (one bed turn),
  then every circle (one smooth loop each), then straight lines, then skins and infill.
* **Toolpath** tab: the planned motion itself for a band of layers (slider + how many): plate frame
  (what lands on the part) or world frame (what the nozzles trace while the disc turns), travels
  dashed, the print order numbered on the newest layer.
* **Approach** (Settings ▸ Tool and nozzle, per arm): the tool facing the disc, or turned ±90° about
  the vertical so the flange points along the arm's Y and the mount lies edge-on to the other arm.
  Added to the commanded yaw; the tool outline the collision guard uses turns with it.
* **Other layer strategies**: *planar*; *cone_out* / *cone_in* (conical layers after Wüthrich et al.
  2021; the mesh is transformed so the cones are flat). *auto* falls back to these, chosen by the
  least material in the air (measured per layer against `lines.max_overhang_deg`).
* **Compare planar vs spiral** (Model print): slices both ways, plans both for two arms, and reports
  time, bead length, arm shares, disc reversals and the plan-view coverage delta per 5 mm band. The
  **Generation** tab replays how each is made: Spiral (bead rings, rays, the two helices growing),
  Planar (paths in print order), Delta (where only one of them lays material). *Use for Print*
  makes the plan shown the one Print streams; otherwise Print uses the Slice + preview result.
* **3D cell** page: both 850s (real CAD) on the official joint chain, the disc and the part so
  far, live. Real arms are drawn from their reported joint angles; simulated arms from their
  pose by IK. Drag to orbit, right-drag to pan, scroll to zoom.
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
