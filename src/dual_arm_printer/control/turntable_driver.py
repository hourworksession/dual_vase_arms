"""Driver for the Aerotech ADRS rotary stage.

Aerotech systems are controlled by A3200/Soloist. The simplest path is
to send AeroBasic command strings over Ethernet to the controller's
ASCII socket interface; a more robust path is the Automation1 Python API.

# STUB: only the ASCII path is sketched here.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass

from ..utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class AdrsDriver:
    host: str
    port: int = 5002
    live: bool = False
    _sock: socket.socket | None = None

    # ---- lifecycle --------------------------------------------------------

    def connect(self) -> None:
        if not self.live:
            log.info("[mock] connect ADRS at %s:%d", self.host, self.port)
            return
        self._sock = socket.create_connection((self.host, self.port), timeout=5.0)

    def home(self) -> None:
        self._send("HOME")

    def move_to(self, angle_deg: float, speed_deg_s: float) -> None:
        self._send(f"MOVEABS A {angle_deg:.6f} F {speed_deg_s:.3f}")

    def stream_schedule(self, schedule_seconds_deg: list[tuple[float, float]]) -> None:
        """Send a time-angle profile as a PVT (position-velocity-time) sequence."""
        # AeroBasic PVT syntax differs by controller; this is illustrative.
        for t_s, angle_deg in schedule_seconds_deg:
            self._send(f"PVT A {angle_deg:.6f} 0.0 {t_s:.4f}")
        self._send("PVT EXEC")

    # ---- internals --------------------------------------------------------

    def _send(self, cmd: str) -> None:
        if not self.live:
            log.info("[mock] ADRS << %s", cmd)
            return
        assert self._sock is not None
        self._sock.sendall((cmd + "\n").encode())
