"""Hemera direct-drive extruder control.

Each Hemera is driven by a small board (likely Duet 3 Mini 5+) that the
xArm cannot natively command. We expose simple ``set_temp`` /
``set_flow`` calls; the actual transport (USB/Ethernet/G-code stream)
plugs in here.

# STUB.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class HemeraDriver:
    serial_port: str
    live: bool = False

    def set_temp(self, temp_c: float) -> None:
        log.info("[mock] %s temp=%.1f°C", self.serial_port, temp_c)

    def set_flow(self, mm_per_s: float) -> None:
        log.info("[mock] %s flow=%.3f mm/s", self.serial_port, mm_per_s)

    def retract(self, mm: float) -> None:
        log.info("[mock] %s retract %.2f mm", self.serial_port, mm)
