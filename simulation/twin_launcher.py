"""
Parent-side handle for the twin subprocess.

Creates the shared-memory block, launches ``simulation.twin_run`` as a separate
process (so PyBullet's GUI gets its own main thread), and exposes
:meth:`publish` to stream the latest :class:`StateSnapshot` to it. Designed to be
driven from the control panel by a small repeater thread.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import uuid
from multiprocessing import shared_memory
from typing import Optional

from simulation.twin_shm import SHM_NAME_DEFAULT, SIZE, write_snapshot
from src.system_state import StateSnapshot

logger = logging.getLogger(__name__)


class TwinLauncher:
    def __init__(self, name: Optional[str] = None, fps: float = 30.0,
                 arm_urdf: Optional[str] = None, drive_mode: str = "auto") -> None:
        # unique name avoids clashing with a stale block from a previous run
        self.name = name or f"{SHM_NAME_DEFAULT}_{uuid.uuid4().hex[:8]}"
        self.fps = fps
        self.arm_urdf = arm_urdf
        self.drive_mode = drive_mode
        self.shm: Optional[shared_memory.SharedMemory] = None
        self.proc: Optional[subprocess.Popen] = None
        self._seq = 0

    def start(self) -> None:
        if self.is_running():
            return
        self.shm = shared_memory.SharedMemory(name=self.name, create=True, size=SIZE)
        self.shm.buf[:SIZE] = b"\x00" * SIZE  # zero -> everything reads as None

        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cmd = [
            sys.executable, "-m", "simulation.twin_run",
            "--shm", self.name, "--fps", str(self.fps),
            "--drive-mode", self.drive_mode,
        ]
        if self.arm_urdf:
            cmd += ["--arm-urdf", self.arm_urdf]
        self.proc = subprocess.Popen(cmd, cwd=root)
        logger.info("Twin subprocess started (shm=%s, pid=%s)", self.name, self.proc.pid)

    def publish(self, snap: StateSnapshot) -> None:
        if self.shm is None:
            return
        self._seq += 1
        try:
            write_snapshot(self.shm.buf, snap, self._seq)
        except Exception:
            logger.debug("twin publish failed", exc_info=True)

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        try:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2.0)
                except Exception:
                    self.proc.kill()
        except Exception:
            logger.debug("twin stop failed", exc_info=True)
        finally:
            try:
                if self.shm is not None:
                    self.shm.close()
                    self.shm.unlink()
            except Exception:
                pass
            self.shm = None
            self.proc = None
