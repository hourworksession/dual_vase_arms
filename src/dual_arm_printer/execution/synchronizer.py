"""Run-loop coordinating both arms, the disc, and both extruders.

Reads a saved ExecutionPlan and dispatches commands to the drivers in
time order. The loop is deliberately single-threaded: each tick it
computes the *next* command per resource that should fire by ``t = now``,
sends it, then sleeps to the next earliest event. This is enough for
the rates the xArm controllers care about (they queue points internally).
"""
from __future__ import annotations

import heapq
import time
from dataclasses import dataclass
from typing import Callable

from ..coordination.scheduler import ExecutionPlan, TimedWaypoint
from ..control.safety import SafetyPolicy
from ..control.turntable_driver import AdrsDriver
from ..control.xarm_driver import XArmDriver
from ..utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class Synchronizer:
    left: XArmDriver
    right: XArmDriver
    turntable: AdrsDriver
    safety: SafetyPolicy
    on_step: Callable[[float], None] | None = None

    def run(self, plan: ExecutionPlan, dry_run: bool = True) -> None:
        # Priority queue: (time, kind, payload).
        events: list[tuple[float, str, object]] = []
        for wp in plan.left_waypoints:
            heapq.heappush(events, (wp.t_s, "left", wp))
        for wp in plan.right_waypoints:
            heapq.heappush(events, (wp.t_s, "right", wp))
        for t, a in plan.turntable_schedule:
            heapq.heappush(events, (t, "disc", a))

        last_left_xyz = (0.0, 0.0, 50.0)
        last_right_xyz = (0.0, 0.0, 50.0)
        t_start = time.monotonic()
        while events:
            t_event, kind, payload = heapq.heappop(events)
            if not dry_run:
                # Real time pacing.
                while time.monotonic() - t_start < t_event:
                    time.sleep(0.001)

            if kind == "left":
                wp: TimedWaypoint = payload  # type: ignore[assignment]
                self.left.move_linear_world(wp.xyz_mm, speed_mm_s=wp.feed_mm_s)
                last_left_xyz = wp.xyz_mm
            elif kind == "right":
                wp = payload  # type: ignore[assignment]
                self.right.move_linear_world(wp.xyz_mm, speed_mm_s=wp.feed_mm_s)
                last_right_xyz = wp.xyz_mm
            elif kind == "disc":
                self.turntable.move_to(float(payload) * 57.2957795, speed_deg_s=180.0)

            ok, why = self.safety.check_step(last_left_xyz, last_right_xyz)
            if not ok:
                log.error("Safety violation: %s — stopping", why)
                self.left.emergency_stop()
                self.right.emergency_stop()
                return

            if self.on_step:
                self.on_step(t_event)
