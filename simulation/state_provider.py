"""
Hardware state poller: feed measured hardware state into the shared SystemState.

When the cell is connected, a background thread polls each arm's TCP pose and
joint angles plus the turntable angle (and optionally extruder temps) at a fixed
rate and writes them with ``StateSource.LIVE``.  When offline, the poller idles,
so whatever the control panel pushed as ``StateSource.COMMANDED`` stays visible
in the twin.  This is the "both, switchable" behaviour: live feedback drives the
twin while hardware is connected, commanded targets drive it while it isn't.

The poller takes plain callables, not controller objects, so it has no hard
dependency on the xArm / Aerotech / Moonraker SDKs and is trivially testable with
fakes.  Use :func:`poller_from_controllers` to wire it to the real controllers.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional

from src.system_state import Joints6, Pose6, StateSource, SystemState

logger = logging.getLogger(__name__)

# Getter type aliases: each returns the reading, or None if unavailable.
PoseGetter = Callable[[], Optional[Pose6]]
JointGetter = Callable[[], Optional[Joints6]]
AngleGetter = Callable[[], Optional[float]]
TempGetter = Callable[[], Optional[Dict[str, float]]]


class HardwarePoller:
    """Polls hardware getters on a background thread into a SystemState.

    Args:
        state: the shared :class:`SystemState` to write into.
        is_live: callable returning True when hardware is connected and should
            be polled. When it returns False the loop idles (commanded values
            are left untouched).
        left_pose/right_pose: TCP pose getters (arm base frame, mm/deg).
        left_joints/right_joints: joint-angle getters (deg). Optional.
        turntable: turntable angle getter (deg). Optional.
        temps: extruder/bed temperature getter -> {name: celsius}. Optional.
        hz: polling rate.
    """

    def __init__(
        self,
        state: SystemState,
        *,
        is_live: Callable[[], bool],
        left_pose: Optional[PoseGetter] = None,
        right_pose: Optional[PoseGetter] = None,
        left_joints: Optional[JointGetter] = None,
        right_joints: Optional[JointGetter] = None,
        turntable: Optional[AngleGetter] = None,
        temps: Optional[TempGetter] = None,
        hz: float = 15.0,
    ) -> None:
        self.state = state
        self.is_live = is_live
        self._get_left_pose = left_pose
        self._get_right_pose = right_pose
        self._get_left_joints = left_joints
        self._get_right_joints = right_joints
        self._get_turntable = turntable
        self._get_temps = temps
        self._period = 1.0 / hz if hz > 0 else 0.0

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="HardwarePoller", daemon=True
        )
        self._thread.start()

    def stop(self, join: bool = True, timeout: float = 2.0) -> None:
        self._stop.set()
        if join and self._thread:
            self._thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # -- one polling pass (also handy to call directly in tests) ---------

    def poll_once(self) -> bool:
        """Read every available getter once and update the state.

        Returns True if anything was read (i.e. hardware was live and at least
        one getter produced a value).
        """
        if not self._safe(self.is_live, default=False):
            return False

        wrote = False

        if self._get_left_pose is not None:
            pose = self._safe(self._get_left_pose)
            if pose is not None:
                joints = (
                    self._safe(self._get_left_joints)
                    if self._get_left_joints is not None
                    else None
                )
                self.state.update_left(pose, StateSource.LIVE, joints=joints)
                wrote = True

        if self._get_right_pose is not None:
            pose = self._safe(self._get_right_pose)
            if pose is not None:
                joints = (
                    self._safe(self._get_right_joints)
                    if self._get_right_joints is not None
                    else None
                )
                self.state.update_right(pose, StateSource.LIVE, joints=joints)
                wrote = True

        if self._get_turntable is not None:
            angle = self._safe(self._get_turntable)
            if angle is not None:
                self.state.update_turntable(angle, StateSource.LIVE)
                wrote = True

        if self._get_temps is not None:
            temps = self._safe(self._get_temps)
            if temps:
                self.state.update_temps(temps)

        return wrote

    # -- internals -------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            start = time.monotonic()
            try:
                self.poll_once()
            except Exception:  # pragma: no cover - defensive
                logger.exception("HardwarePoller pass failed")
            elapsed = time.monotonic() - start
            self._stop.wait(max(0.0, self._period - elapsed))

    @staticmethod
    def _safe(fn: Callable, default=None):
        """Call ``fn`` and swallow exceptions (a dropped frame must not kill
        the poller)."""
        try:
            return fn()
        except Exception:
            logger.debug("getter raised; skipping this frame", exc_info=True)
            return default


def poller_from_controllers(
    state: SystemState,
    *,
    is_live: Callable[[], bool],
    left=None,
    right=None,
    turntable=None,
    extruder=None,
    hz: float = 15.0,
) -> HardwarePoller:
    """Build a poller wired to the project's controller objects.

    Each controller is optional; missing ones simply aren't polled. The arm
    controllers are expected to expose ``get_pose()`` and ``get_joints()``; the
    turntable ``get_angle()``; the extruder ``get_printer_status()``.
    """

    def temps_getter():
        if extruder is None:
            return None
        status = extruder.get_printer_status()
        out = {}
        if "extruder" in status:
            out["tool0"] = status["extruder"].get("temperature", 0.0)
        if "heater_bed" in status:
            out["bed"] = status["heater_bed"].get("temperature", 0.0)
        return out

    return HardwarePoller(
        state,
        is_live=is_live,
        left_pose=(lambda: left.get_pose()) if left is not None else None,
        right_pose=(lambda: right.get_pose()) if right is not None else None,
        left_joints=(lambda: left.get_joints()) if left is not None else None,
        right_joints=(lambda: right.get_joints()) if right is not None else None,
        turntable=(lambda: turntable.get_angle()) if turntable is not None else None,
        temps=temps_getter if extruder is not None else None,
        hz=hz,
    )
