"""Slice an example part, split it, and render a PNG preview.

This is the smallest possible smoke test of the full pipeline.

    python scripts/preview_split.py examples/01_cylinder.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from dual_arm_printer.coordination.scheduler import build_execution_plan
from dual_arm_printer.execution.output_writer import write_plan
from dual_arm_printer.simulation.visualizer import plot_split
from dual_arm_printer.slicing.fullcontrol_runner import slice_with_fullcontrol
from dual_arm_printer.slicing.gcode_parser import parse_gcode
from dual_arm_printer.slicing.reconstructor import reconstruct
from dual_arm_printer.splitting.dual_spiral import DualSpiralConfig, DualSpiralSplitter


def main(script: str) -> None:
    script_path = Path(script)
    gcode = Path("build") / (script_path.stem + ".gcode")
    slice_with_fullcontrol(script_path, gcode)

    graph = reconstruct(parse_gcode(gcode))
    split = DualSpiralSplitter(DualSpiralConfig()).split(graph)
    plan = build_execution_plan(split)
    paths = write_plan(plan, Path("build") / script_path.stem)
    for k, v in paths.items():
        print(f"{k}: {v}")
    plot_split(split, save_to=str(Path("build") / (script_path.stem + ".png")))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "examples/01_cylinder.py")
