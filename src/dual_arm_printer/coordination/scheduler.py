"""Time-synchronised motion plan builder.

Takes a ``SplitResult`` (two ArmPlans + turntable schedule) and produces a
fully-timed ``ExecutionPlan`` consisting of:

  • a list of timed Cartesian waypoints per arm in the *world* frame,
  • a list of timed disc angle setpoints,
  • a list of extruder flow commands per arm.

The executor consumes the ExecutionPlan; the simulator replays it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..splitting.base import ArmPlan, SplitResult
from .turntable_sync import transform_segment


@dataclass(slots=True)
class TimedWaypoint:
    t_s: float
    xyz_mm: tuple[float, float, float]
    feed_mm_s: float
    extrude_mm: float
    is_travel: bool


@dataclass(slots=True)
class ExecutionPlan:
    left_waypoints: list[TimedWaypoint] = field(default_factory=list)
    right_waypoints: list[TimedWaypoint] = field(default_factory=list)
    turntable_schedule: list[tuple[float, float]] = field(default_factory=list)
    total_time_s: float = 0.0


def build_execution_plan(
    split: SplitResult,
    disc_centre_world: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> ExecutionPlan:
    plan = ExecutionPlan()
    plan.turntable_schedule = list(split.turntable_schedule)

    t_left = _arm_to_waypoints(split.left, disc_centre_world)
    t_right = _arm_to_waypoints(split.right, disc_centre_world)

    plan.left_waypoints = t_left
    plan.right_waypoints = t_right
    plan.total_time_s = max(
        t_left[-1].t_s if t_left else 0.0,
        t_right[-1].t_s if t_right else 0.0,
    )
    return plan


def _arm_to_waypoints(
    arm: ArmPlan, disc_centre_world: tuple[float, float, float]
) -> list[TimedWaypoint]:
    out: list[TimedWaypoint] = []
    t = 0.0
    for seg, angle in zip(arm.segments, arm.disc_angle_at_start_rad):
        start_world, end_world = transform_segment(
            seg.start_xyz, seg.end_xyz, angle, disc_centre_world
        )
        feed_mm_s = (seg.feed_mm_min or 1500.0) / 60.0
        dt = max(seg.length_mm, 1e-3) / max(feed_mm_s, 1e-3)
        out.append(
            TimedWaypoint(
                t_s=t,
                xyz_mm=end_world,
                feed_mm_s=feed_mm_s,
                extrude_mm=seg.extrude_mm,
                is_travel=seg.is_travel,
            )
        )
        t += dt
    return out
