"""``dual-arm-printer`` command line.

Subcommands:
    slice       run a FullControl design script → .gcode
    split       parse .gcode → ArmPlans → .plan.json
    simulate    replay a .plan.json in PyBullet
    run         send a .plan.json to real hardware
"""
from __future__ import annotations

from pathlib import Path

import click

from ..coordination.scheduler import build_execution_plan
from ..execution.output_writer import write_plan
from ..slicing.fullcontrol_runner import slice_with_fullcontrol
from ..slicing.gcode_parser import parse_gcode
from ..slicing.reconstructor import reconstruct
from ..splitting.dual_spiral import DualSpiralConfig, DualSpiralSplitter
from ..utils.config import SystemConfig, load_yaml
from ..utils.logging import get_logger

log = get_logger(__name__)


@click.group()
def cli() -> None:
    """Dual xArm 850 cooperative 3D printing pipeline."""


@cli.command()
@click.argument("script", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--out", default="build/part.gcode", show_default=True)
def slice(script: str, out: str) -> None:  # noqa: A001
    """Run a FullControl design script and emit G-code."""
    slice_with_fullcontrol(script, out)


@cli.command()
@click.argument("gcode", type=click.Path(exists=True, dir_okay=False))
@click.option("--strategy", default="dual_spiral", show_default=True)
@click.option("--config", "config_path", default="config/splitter/dual_spiral.yaml", show_default=True)
@click.option("--system", "system_path", default="config/system.yaml", show_default=True)
@click.option("-o", "--out", default="build/part", show_default=True)
def split(gcode: str, strategy: str, config_path: str, system_path: str, out: str) -> None:
    """Reconstruct a G-code file and split into two arm plans."""
    prog = parse_gcode(gcode)
    graph = reconstruct(prog)
    log.info("Reconstructed %d segments in %d layers", len(graph.segments), len(graph.layers))

    if strategy == "dual_spiral":
        cfg = DualSpiralConfig.from_yaml_dict(load_yaml(config_path))
        result = DualSpiralSplitter(cfg).split(graph)
    else:
        raise click.UsageError(f"Strategy '{strategy}' not implemented yet; see docs.")

    sys_cfg = SystemConfig.from_yaml(system_path)
    plan = build_execution_plan(result, disc_centre_world=tuple(sys_cfg.disc.centre_world_xyz))
    paths = write_plan(plan, out)
    for k, v in paths.items():
        click.echo(f"{k}: {v}")


@cli.command()
@click.argument("plan_json", type=click.Path(exists=True, dir_okay=False))
@click.option("--urdf-dir", default="src/dual_arm_printer/assets/urdf", show_default=True)
@click.option("--gui/--no-gui", default=True)
@click.option("--speed", default=1.0, show_default=True)
def simulate(plan_json: str, urdf_dir: str, gui: bool, speed: float) -> None:
    """Replay a saved plan in PyBullet."""
    from ..simulation.replay import replay_plan

    replay_plan(plan_json, urdf_dir, speed=speed, gui=gui)


@cli.command()
@click.argument("plan_json", type=click.Path(exists=True, dir_okay=False))
@click.option("--left-ip", default="192.168.1.241")
@click.option("--right-ip", default="192.168.1.242")
@click.option("--turntable-host", default="192.168.1.50")
@click.option("--confirm", is_flag=True, help="Required to actually move hardware.")
def run(plan_json: str, left_ip: str, right_ip: str, turntable_host: str, confirm: bool) -> None:
    """Stream a plan to the real arms + turntable."""
    import json

    from ..control.safety import SafetyPolicy
    from ..control.turntable_driver import AdrsDriver
    from ..control.xarm_driver import XArmDriver
    from ..coordination.scheduler import ExecutionPlan, TimedWaypoint
    from ..execution.synchronizer import Synchronizer

    data = json.loads(Path(plan_json).read_text())
    plan = ExecutionPlan(
        left_waypoints=[TimedWaypoint(**w) for w in data["left_waypoints"]],
        right_waypoints=[TimedWaypoint(**w) for w in data["right_waypoints"]],
        turntable_schedule=[(t, a) for t, a in data["turntable_schedule"]],
        total_time_s=data["total_time_s"],
    )

    sync = Synchronizer(
        left=XArmDriver(left_ip, live=confirm),
        right=XArmDriver(right_ip, live=confirm),
        turntable=AdrsDriver(turntable_host, live=confirm),
        safety=SafetyPolicy(),
    )
    sync.left.connect()
    sync.right.connect()
    sync.turntable.connect()
    sync.run(plan, dry_run=not confirm)


if __name__ == "__main__":  # pragma: no cover
    cli()
