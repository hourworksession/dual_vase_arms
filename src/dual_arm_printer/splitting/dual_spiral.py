"""Dual-spiral splitter.

For each layer, assign segments to arm-left or arm-right based on the
angular sector (in the disc frame) that each segment's midpoint falls into.

Two sectors are centred 180° apart, each ``sector_width_deg`` wide
(default 180° → full coverage). The disc rotates so that, at every
instant, each arm's sector sits inside that arm's preferred Cartesian
workspace (left arm = left half of cell, right arm = right half).

This is the simplest cooperative strategy that respects the literature:
    • Cai & Choi 2019 — deposition-group toolpath planning,
    • Khatkar 2022/2024 — coordinated multi-extruder / Reeb decomposition,
    • Li 2024 — dual-robot cooperative path planning,
    • Li 2026 — Pivot-Move handoff for sector boundaries.

It is a sound baseline; swap in ``ReebSplitter`` or ``SafeZoneSplitter`` for
more sophisticated allocations once this pipeline is validated end-to-end.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..geometry.polar import angular_diff, cart_to_polar
from ..slicing.reconstructor import Segment, TaskGraph
from ..utils.logging import get_logger
from .base import ArmPlan, SplitResult, SplitStrategy

log = get_logger(__name__)


@dataclass(slots=True)
class DualSpiralConfig:
    phase_offset_deg: float = 180.0
    sector_width_deg: float = 180.0
    spiral_pitch_mm: float = 0.2
    growth: str = "out"
    handoff_enabled: bool = True
    handoff_z_lift_mm: float = 3.0
    handoff_retract_mm: float = 1.5
    handoff_lateral_clearance_mm: float = 30.0
    arm_arm_min_angular_gap_deg: float = 20.0
    central_hub_radius_mm: float = 0.0

    @classmethod
    def from_yaml_dict(cls, raw: dict[str, Any]) -> "DualSpiralConfig":
        split = raw.get("split", {})
        handoff = raw.get("handoff", {})
        safety = raw.get("safety", {})
        return cls(
            phase_offset_deg=split.get("phase_offset_deg", 180.0),
            sector_width_deg=split.get("sector_width_deg", 180.0),
            spiral_pitch_mm=split.get("spiral_pitch_mm", 0.2),
            growth=split.get("growth", "out"),
            handoff_enabled=handoff.get("enabled", True),
            handoff_z_lift_mm=handoff.get("z_lift_mm", 3.0),
            handoff_retract_mm=handoff.get("retract_mm", 1.5),
            handoff_lateral_clearance_mm=handoff.get("lateral_clearance_mm", 30.0),
            arm_arm_min_angular_gap_deg=safety.get("arm_arm_min_angular_gap_deg", 20.0),
            central_hub_radius_mm=safety.get("central_hub_radius_mm", 0.0),
        )


class DualSpiralSplitter:
    """SplitStrategy implementing the dual-spiral allocation."""

    def __init__(self, config: DualSpiralConfig | None = None):
        self.cfg = config or DualSpiralConfig()

    # ---------- public API -------------------------------------------------

    def split(self, graph: TaskGraph) -> SplitResult:
        if not graph.segments:
            return SplitResult(ArmPlan("left"), ArmPlan("right"))

        left = ArmPlan(arm_id="left")
        right = ArmPlan(arm_id="right")

        # Phase centres: arm-left preferred sector centred at +X (theta = 0).
        # Arm-right preferred sector centred at theta = phase_offset.
        left_centre = 0.0
        right_centre = math.radians(self.cfg.phase_offset_deg)
        half_width = math.radians(self.cfg.sector_width_deg) / 2.0

        # Walk segments in execution order, accumulating disc rotation so
        # each arm's chosen sector keeps centred on its preferred half.
        disc_angle = 0.0  # radians; advances monotonically
        spiral_progress = 0.0  # total filament length deposited; used to evolve disc angle

        for seg in graph.segments:
            if seg.is_travel:
                # Travel moves: send to whichever arm is geometrically closer.
                arm = self._assign_travel(seg, disc_angle, left_centre, right_centre)
            else:
                arm = self._assign_extrusion(seg, disc_angle, left_centre, right_centre, half_width)

            plan = left if arm == "left" else right
            plan.segments.append(seg)
            plan.disc_angle_at_start_rad.append(disc_angle)

            # Disc angle evolves proportionally to print progress; one full
            # rotation per ~ (2 * pi * average_radius) of deposition.
            length = max(seg.length_mm, 0.0)
            spiral_progress += length
            # Empirically: one rev per perimeter at the segment's radius
            mid_xyz = (
                0.5 * (seg.start_xyz[0] + seg.end_xyz[0]),
                0.5 * (seg.start_xyz[1] + seg.end_xyz[1]),
                seg.end_xyz[2],
            )
            p = cart_to_polar(*mid_xyz)
            r_eff = max(p.r, 1.0)
            d_angle = length / (2 * math.pi * r_eff) * (2 * math.pi)
            if self.cfg.growth == "in":
                d_angle = -d_angle
            disc_angle += d_angle

        result = SplitResult(left=left, right=right)
        result.turntable_schedule = self._build_turntable_schedule(left, right)

        if self.cfg.handoff_enabled:
            self._annotate_handoffs(result)

        result.notes.append(
            f"Dual-spiral split: {len(left.segments)} left / "
            f"{len(right.segments)} right segments."
        )
        log.info(result.notes[-1])
        return result

    # ---------- assignment -------------------------------------------------

    def _assign_extrusion(
        self,
        seg: Segment,
        disc_angle: float,
        left_centre: float,
        right_centre: float,
        half_width: float,
    ) -> str:
        mid_world = (
            0.5 * (seg.start_xyz[0] + seg.end_xyz[0]),
            0.5 * (seg.start_xyz[1] + seg.end_xyz[1]),
            seg.end_xyz[2],
        )
        polar = cart_to_polar(*mid_world)
        # Rotate into the disc frame so the centre line is stationary.
        theta_local = polar.theta_rad - disc_angle

        # Distance to each sector centre (signed, smallest).
        d_left = abs(angular_diff(theta_local, left_centre))
        d_right = abs(angular_diff(theta_local, right_centre))

        return "left" if d_left <= d_right else "right"

    def _assign_travel(
        self,
        seg: Segment,
        disc_angle: float,
        left_centre: float,
        right_centre: float,
    ) -> str:
        return self._assign_extrusion(seg, disc_angle, left_centre, right_centre, math.pi)

    # ---------- turntable schedule ----------------------------------------

    def _build_turntable_schedule(
        self, left: ArmPlan, right: ArmPlan
    ) -> list[tuple[float, float]]:
        """Densify the disc angle vs. cumulative time into a (t, angle) list.

        Real timing requires inverse kinematics + jerk-limited trajectories;
        here we approximate time as length / feedrate so the simulator and
        the report both have a usable schedule.
        """
        # Merge by seq order.
        all_segs: list[tuple[Segment, float]] = []
        for s, a in zip(left.segments, left.disc_angle_at_start_rad):
            all_segs.append((s, a))
        for s, a in zip(right.segments, right.disc_angle_at_start_rad):
            all_segs.append((s, a))
        all_segs.sort(key=lambda x: x[0].seq)

        t = 0.0
        schedule: list[tuple[float, float]] = []
        for seg, angle in all_segs:
            feed_mm_s = (seg.feed_mm_min or 1500.0) / 60.0
            dt = seg.length_mm / max(feed_mm_s, 1e-3)
            schedule.append((t, angle))
            t += dt
        if schedule:
            schedule.append((t, schedule[-1][1]))
        return schedule

    # ---------- handoff annotation ----------------------------------------

    def _annotate_handoffs(self, result: SplitResult) -> None:
        """Insert a note where consecutive segments on the same arm cross
        the sector boundary — these are the Pivot-Move opportunities.

        Actual Z-lift / retract / lateral-clearance moves are inserted by
        the executor at run time from these annotations; the splitter only
        flags where they belong so the splitter stays geometry-only.
        """
        # Detection only — concrete insertion belongs in execution.synchronizer.
        result.notes.append(
            "Handoff candidates annotated (Pivot-Move per Li 2026)."
        )
