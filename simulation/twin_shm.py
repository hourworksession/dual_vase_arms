"""
Shared-memory codec for streaming a StateSnapshot to the twin subprocess.

The control-panel process writes the latest snapshot into a small block of
shared memory; the twin subprocess reads it each frame. Layout is a flat array
of float64s:

    [ seq,
      left_valid,  lx, ly, lz, lr, lp, lyaw,
      right_valid, rx, ry, rz, rr, rp, ryaw,
      tt_valid,    tt_deg,
      lj_valid,    lj1..lj6,
      rj_valid,    rj1..rj6 ]

``seq`` is a monotonically increasing frame counter (handy for debugging /
detecting a stalled producer). No lock is used: a torn read at most shows one
stale field for a single frame, which is invisible at 30 fps. Validity flags
let unset channels read back as ``None``.
"""

from __future__ import annotations

import struct
from typing import Optional

from src.system_state import Pose6, StateSnapshot, StateSource

SHM_NAME_DEFAULT = "dualarm_twin"

# seq(1) + pose(7) + pose(7) + tt(2) + joints(7) + joints(7)
_N = 1 + 7 + 7 + 2 + 7 + 7
SIZE = _N * 8
_FMT = "<%dd" % _N


def _pose_slots(p: Optional[Pose6]):
    if p is None:
        return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    return [1.0, float(p[0]), float(p[1]), float(p[2]),
            float(p[3]), float(p[4]), float(p[5])]


def write_snapshot(buf, snap: StateSnapshot, seq: int) -> None:
    vals = [float(seq)]
    vals += _pose_slots(snap.left_pose)
    vals += _pose_slots(snap.right_pose)
    if snap.turntable_deg is not None:
        vals += [1.0, float(snap.turntable_deg)]
    else:
        vals += [0.0, 0.0]
    vals += _pose_slots(snap.left_joints)
    vals += _pose_slots(snap.right_joints)
    struct.pack_into(_FMT, buf, 0, *vals)


def read_snapshot(buf) -> StateSnapshot:
    vals = struct.unpack_from(_FMT, buf, 0)
    i = 1

    def take_pose():
        nonlocal i
        valid = vals[i]
        p = tuple(vals[i + 1:i + 7])
        i += 7
        return p if valid >= 0.5 else None

    left = take_pose()
    right = take_pose()
    tt_valid = vals[i]
    tt = vals[i + 1]
    i += 2
    lj = take_pose()
    rj = take_pose()

    return StateSnapshot(
        left_pose=left,
        right_pose=right,
        left_joints=lj,
        right_joints=rj,
        turntable_deg=(tt if tt_valid >= 0.5 else None),
        source=StateSource.LIVE,
    )
