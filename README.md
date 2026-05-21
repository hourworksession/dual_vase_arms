# dual_arm_printer

Slice → split → simulate → drive: a Python control stack for cooperative 3D printing
on an Aerotech ADRS rotary stage (300 mm radius acrylic disc) with two UFactory xArm 850
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
