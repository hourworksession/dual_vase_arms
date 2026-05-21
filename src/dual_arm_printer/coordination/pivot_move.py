"""Concrete realisation of the Pivot-Move handoff (Li et al., 2026).

Given a handoff annotation, generates the three Cartesian sub-moves that
clear an arm out of the way for the other arm to enter the shared
workspace: retract → lift → lateral.
"""
from __future__ import annotations

import math
from typing import Sequence

from ..splitting.handoff import HandoffMove


def realise(
    current_world_xyz: tuple[float, float, float],
    handoff: HandoffMove,
) -> Sequence[tuple[float, float, float]]:
    x, y, z = current_world_xyz
    lift = (x, y, z + handoff.z_lift_mm)
    angle = math.radians(handoff.direction_deg)
    lat = (
        x + handoff.lateral_clearance_mm * math.cos(angle),
        y + handoff.lateral_clearance_mm * math.sin(angle),
        z + handoff.z_lift_mm,
    )
    return [lift, lat]
