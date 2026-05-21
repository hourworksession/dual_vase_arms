"""Pivot-Move handoff primitives (Li et al., 2026).

A "handoff" is the short sequence inserted whenever an arm needs to cede
its current workspace to the other arm: retract, lift Z, lateral move to
a safe staging pose, then re-engage on the other side. Kept here as a
data structure so the executor can render it consistently from any
splitter that flags handoff candidates.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class HandoffMove:
    z_lift_mm: float
    retract_mm: float
    lateral_clearance_mm: float
    direction_deg: float          # angle in disc frame to retreat toward

    def description(self) -> str:
        return (
            f"PivotMove: retract {self.retract_mm} mm, lift {self.z_lift_mm} mm, "
            f"move {self.lateral_clearance_mm} mm @ {self.direction_deg:.1f}°."
        )
