"""Common types for any toolpath splitter.

A splitter takes a single ``TaskGraph`` and returns two ``ArmPlan`` objects
plus a turntable angular schedule. Future splitters (Reeb, SafeZone)
implement the same ``SplitStrategy`` protocol so the rest of the pipeline
(simulator, executor) is strategy-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..slicing.reconstructor import Segment, TaskGraph


@dataclass(slots=True)
class ArmPlan:
    """Ordered segments for one arm, plus per-segment disc angle.

    ``disc_angle_at_start_rad[i]`` is the disc orientation at the moment the
    arm starts segment ``i``. The arm's world-frame endpoints for that
    segment are computed by rotating the (disc-local) segment endpoints by
    that angle.
    """
    arm_id: str
    segments: list[Segment] = field(default_factory=list)
    disc_angle_at_start_rad: list[float] = field(default_factory=list)


@dataclass(slots=True)
class SplitResult:
    left: ArmPlan
    right: ArmPlan
    # Optional dense turntable schedule (time, angle) for the controller.
    turntable_schedule: list[tuple[float, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class SplitStrategy(Protocol):
    """Any class implementing this protocol can be plugged into the pipeline."""

    def split(self, graph: TaskGraph) -> SplitResult: ...
