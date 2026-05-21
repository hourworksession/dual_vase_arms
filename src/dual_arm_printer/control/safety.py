"""Cell-level safety policies enforced before each motion is sent.

This module is the *last* check before commands hit hardware. Splitter
and simulator do their own checks, but those are advisory; the safety
policy here is authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..coordination.collision import nozzle_above_disc, tcp_tcp_distance


@dataclass
class SafetyPolicy:
    arm_arm_min_distance_mm: float = 80.0
    nozzle_to_disc_z_min_mm: float = 0.2
    disc_top_z_mm: float = 0.0

    def check_step(
        self,
        left_tcp: tuple[float, float, float],
        right_tcp: tuple[float, float, float],
    ) -> tuple[bool, str]:
        d = tcp_tcp_distance(left_tcp, right_tcp)
        if d < self.arm_arm_min_distance_mm:
            return False, f"TCPs too close: {d:.1f} mm"
        for name, tcp in (("left", left_tcp), ("right", right_tcp)):
            if not nozzle_above_disc(tcp[2], self.disc_top_z_mm, self.nozzle_to_disc_z_min_mm):
                return False, f"{name} nozzle below disc clearance"
        return True, "ok"
