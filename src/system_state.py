"""
Shared, thread-safe state model for the dual-arm + turntable cell.

This is the single source of truth that the control panel, the hardware
feedback poller, and the 3D twin all read from / write to.  It deliberately
holds the state in the SAME coordinate convention the control panel already
uses:

  * Arm poses are 6-DOF (x, y, z, roll, pitch, yaw) in **each arm's own base
    frame**, millimetres and degrees -- exactly what ``ArmController.get_pose``
    returns and what ``set_position`` consumes.
  * The turntable angle is in **degrees**, matching ``TurntableController``.

Mapping those arm-base poses into a common world frame for visualisation is the
job of :mod:`src.calibration`; this module stays frame-agnostic so nothing here
depends on calibration being correct.

Concurrency
-----------
A single ``threading.RLock`` guards every field.  Producers (the hardware
poller, or the panel pushing commanded targets) call the ``update_*`` methods;
consumers (the twin viewer, status labels) call :meth:`snapshot`, which returns
an immutable copy that can be read without holding the lock.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, Optional, Tuple

# A 6-DOF pose: (x, y, z, roll, pitch, yaw) in mm / degrees, arm base frame.
Pose6 = Tuple[float, float, float, float, float, float]

# A joint vector: 6 servo angles in degrees (base -> wrist).
Joints6 = Tuple[float, float, float, float, float, float]


class StateSource(str, Enum):
    """Where the current state value came from."""

    NONE = "none"            # never populated
    LIVE = "live"            # measured from hardware encoders
    COMMANDED = "commanded"  # last target the panel issued (no feedback)


@dataclass(frozen=True)
class StateSnapshot:
    """Immutable point-in-time copy of the whole cell state.

    All timestamps are ``time.monotonic()`` seconds at the moment the
    corresponding field was last written (``None`` if never written).
    """

    left_pose: Optional[Pose6] = None
    right_pose: Optional[Pose6] = None
    left_joints: Optional[Joints6] = None
    right_joints: Optional[Joints6] = None
    turntable_deg: Optional[float] = None
    temps_c: Dict[str, float] = field(default_factory=dict)

    source: StateSource = StateSource.NONE
    left_ts: Optional[float] = None
    right_ts: Optional[float] = None
    turntable_ts: Optional[float] = None
    temps_ts: Optional[float] = None

    def age(self, channel: str, now: Optional[float] = None) -> Optional[float]:
        """Seconds since ``channel`` ('left'|'right'|'turntable'|'temps') was
        last updated, or ``None`` if it never was."""
        ts = {
            "left": self.left_ts,
            "right": self.right_ts,
            "turntable": self.turntable_ts,
            "temps": self.temps_ts,
        }[channel]
        if ts is None:
            return None
        return (now if now is not None else time.monotonic()) - ts


class SystemState:
    """Thread-safe holder for the live cell state.

    Example
    -------
    >>> state = SystemState()
    >>> state.update_left((400, 200, 155, 180, 45, 20), StateSource.COMMANDED)
    >>> snap = state.snapshot()
    >>> snap.left_pose
    (400, 200, 155, 180, 45, 20)
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snap = StateSnapshot()

    # -- writers ---------------------------------------------------------

    def update_left(self, pose: Optional[Pose6], source: StateSource,
                    joints: Optional[Joints6] = None,
                    ts: Optional[float] = None) -> None:
        changes = dict(source=source,
                       left_ts=ts if ts is not None else time.monotonic())
        if pose is not None:
            changes["left_pose"] = tuple(pose)
        if joints is not None:
            changes["left_joints"] = tuple(joints)
        self._update(**changes)

    def update_right(self, pose: Optional[Pose6], source: StateSource,
                     joints: Optional[Joints6] = None,
                     ts: Optional[float] = None) -> None:
        changes = dict(source=source,
                       right_ts=ts if ts is not None else time.monotonic())
        if pose is not None:
            changes["right_pose"] = tuple(pose)
        if joints is not None:
            changes["right_joints"] = tuple(joints)
        self._update(**changes)

    def update_turntable(self, angle_deg: float, source: StateSource,
                         ts: Optional[float] = None) -> None:
        self._update(turntable_deg=float(angle_deg), source=source,
                     turntable_ts=ts if ts is not None else time.monotonic())

    def update_temps(self, temps_c: Dict[str, float],
                     ts: Optional[float] = None) -> None:
        # Temps are not tied to a motion source; merge into existing dict.
        with self._lock:
            merged = dict(self._snap.temps_c)
            merged.update({k: float(v) for k, v in temps_c.items()})
            self._snap = replace(
                self._snap,
                temps_c=merged,
                temps_ts=ts if ts is not None else time.monotonic(),
            )

    def clear_joints(self, arm: str) -> None:
        """Drop the stored joint vector for an arm so pose-driven IK takes over."""
        with self._lock:
            if arm == "left":
                self._snap = replace(self._snap, left_joints=None)
            else:
                self._snap = replace(self._snap, right_joints=None)

    def set_source(self, source: StateSource) -> None:
        """Override the active source label (e.g. when toggling live/offline)."""
        self._update(source=source)

    # -- reader ----------------------------------------------------------

    def snapshot(self) -> StateSnapshot:
        """Return an immutable copy of the current state.

        Safe to read field-by-field without further locking because
        :class:`StateSnapshot` is frozen and never mutated in place.
        """
        with self._lock:
            return self._snap

    # -- internal --------------------------------------------------------

    def _update(self, **changes) -> None:
        with self._lock:
            self._snap = replace(self._snap, **changes)
