"""Wrap FullControl GCode Designer (Gleadall, 2021) for our pipeline.

FullControl is chosen as the slicer for this cell because the cooperative
dual-arm + rotary workflow cannot be expressed in conventional slicer GUIs.
FullControl exposes geometry as a Python list of ``Point`` / ``Extruder`` /
``Printer`` objects, which is exactly the right representation for us to
post-process into two cooperative arm plans.

Each example under ``examples/`` is a Python module that defines a
``build_steps()`` function returning the FullControl step list and a
printer config. This runner imports the module, runs it, and writes the
resulting G-code to disk so the next pipeline stage can parse it.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from ..utils.logging import get_logger

log = get_logger(__name__)


def _load_module(script_path: Path):
    spec = importlib.util.spec_from_file_location(script_path.stem, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def slice_with_fullcontrol(script_path: str | Path, out_gcode: str | Path) -> Path:
    """Execute a FullControl design script and write its G-code to disk.

    The script must define ``build_steps() -> tuple[list, dict]`` where the
    second element is a printer-settings dict accepted by
    ``fullcontrol.transform``.
    """
    script_path = Path(script_path)
    out_gcode = Path(out_gcode)
    out_gcode.parent.mkdir(parents=True, exist_ok=True)

    module = _load_module(script_path)
    if not hasattr(module, "build_steps"):
        raise AttributeError(
            f"{script_path} must define build_steps() returning (steps, settings)"
        )

    steps, settings = module.build_steps()
    log.info("Running FullControl with %d steps", len(steps))

    try:
        import fullcontrol as fc  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "fullcontrol not installed. `pip install fullcontrol`."
        ) from e

    gcode = fc.transform(steps, "gcode", settings)
    out_gcode.write_text(gcode if isinstance(gcode, str) else str(gcode))
    log.info("Wrote G-code to %s", out_gcode)
    return out_gcode


def default_settings() -> dict[str, Any]:
    """Hemera-friendly defaults; override per-script."""
    return {
        "extrusion_width": 0.45,
        "extrusion_height": 0.2,
        "nozzle_temp": 215,
        "bed_temp": 0,
        "print_speed": 1500,        # mm/min
        "travel_speed": 6000,       # mm/min
        "dia_feed": 1.75,
        "primer": "no_primer",
        "printer_name": "generic",
    }
