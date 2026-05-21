"""Convert a sequence of timed waypoints to a jerk-limited trajectory.

Production should use the on-board motion planner (xArm controller does
trapezoidal blending automatically for ``set_position`` calls). This
file exists so we can pre-validate that the splitter's commanded
feedrates are achievable; if a segment requires acceleration above
``cartesian_accel_max_mm_s2`` we either slow it down or reject it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..coordination.scheduler import TimedWaypoint


@dataclass
class TrajectoryDiagnostics:
    max_speed_mm_s: float
    max_accel_mm_s2: float
    feasible: bool
    reason: str = "ok"


def diagnose(
    waypoints: Sequence[TimedWaypoint],
    speed_max_mm_s: float,
    accel_max_mm_s2: float,
) -> TrajectoryDiagnostics:
    if len(waypoints) < 2:
        return TrajectoryDiagnostics(0.0, 0.0, True)
    speeds = [wp.feed_mm_s for wp in waypoints]
    accels: list[float] = []
    for a, b in zip(waypoints[:-1], waypoints[1:]):
        dt = max(b.t_s - a.t_s, 1e-6)
        accels.append(abs(b.feed_mm_s - a.feed_mm_s) / dt)
    s_max = max(speeds)
    a_max = max(accels) if accels else 0.0
    feasible = s_max <= speed_max_mm_s and a_max <= accel_max_mm_s2
    return TrajectoryDiagnostics(
        s_max,
        a_max,
        feasible,
        "ok"
        if feasible
        else f"speed {s_max:.0f}>{speed_max_mm_s} or accel {a_max:.0f}>{accel_max_mm_s2}",
    )
