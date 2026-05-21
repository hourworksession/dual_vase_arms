"""Thin wrapper around UFactory's xArm-Python-SDK (``xarm.wrapper.XArmAPI``).

The wrapper exists so the rest of the project depends only on a small
interface and can be mocked in unit tests. Networking, error reporting,
and emergency-stop handling all live here.

# STUB sections: the actual send-to-arm calls are gated by ``self.live``.
When ``live=False`` (the default in simulation), the driver no-ops and
just records the commands for inspection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ..utils.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class XArmCommandLog:
    commands: list[tuple[str, tuple]] = field(default_factory=list)


class XArmDriver:
    """Single xArm 850 connection."""

    def __init__(self, ip: str, live: bool = False):
        self.ip = ip
        self.live = live
        self.log = XArmCommandLog()
        self._api = None

    # ---- lifecycle --------------------------------------------------------

    def connect(self) -> None:
        if not self.live:
            log.info("[mock] connect %s", self.ip)
            return
        from xarm.wrapper import XArmAPI  # type: ignore

        self._api = XArmAPI(self.ip)
        self._api.motion_enable(enable=True)
        self._api.set_mode(0)
        self._api.set_state(0)

    def home(self) -> None:
        if not self.live:
            self.log.commands.append(("home", ()))
            return
        assert self._api is not None
        self._api.move_gohome(wait=True)

    # ---- motion -----------------------------------------------------------

    def move_linear_world(
        self,
        xyz_mm: Sequence[float],
        orientation_rpy_deg: Sequence[float] | None = None,
        speed_mm_s: float = 100.0,
        wait: bool = False,
    ) -> None:
        if not self.live:
            self.log.commands.append(("move_linear_world", (tuple(xyz_mm), speed_mm_s)))
            return
        assert self._api is not None
        pose = list(xyz_mm) + list(orientation_rpy_deg or [180.0, 0.0, 0.0])
        self._api.set_position(*pose, speed=speed_mm_s, wait=wait)

    # ---- extruder I/O via Hemera board (e.g. Duet over Ethernet/USB) -----

    def set_extrude_rate(self, mm_per_s: float) -> None:
        """STUB: route to the Hemera control board.

        On the bench, we plan to use a small Duet running a custom firmware
        flavour, with the xArm's controller box providing only motion. This
        method should be wired to whatever serial protocol the Duet expects.
        """
        self.log.commands.append(("set_extrude_rate", (mm_per_s,)))

    # ---- safety -----------------------------------------------------------

    def emergency_stop(self) -> None:
        if not self.live:
            self.log.commands.append(("emergency_stop", ()))
            return
        assert self._api is not None
        self._api.emergency_stop()
