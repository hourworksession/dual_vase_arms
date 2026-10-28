"""Deterministic referee: the only judge of whether a proposed move is allowed.

An agent's proposal is assumed good until one of these rules refutes it
(the chess-engine idea). Each rule has an id so the refutation that goes back
to the agent, and into the log, is specific.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .world import World

ACTIONS = ("start_job", "join_job", "wait", "park")


@dataclass
class Verdict:
    ok: bool
    refutations: list[tuple[str, str]] = field(default_factory=list)  # (rule id, reason)

    def text(self) -> str:
        return "; ".join(f"[{r}] {m}" for r, m in self.refutations) or "accepted"


def check(world: World, arm: str, p: dict) -> Verdict:
    bad: list[tuple[str, str]] = []
    act = p.get("action")
    if act not in ACTIONS:
        return Verdict(False, [("R0", f"unknown action {act!r}; use one of {ACTIONS}")])
    st = world.state[arm]
    if act in ("wait", "park"):
        if act == "park" and st.status == "working":
            bad.append(("R1", "cannot park while working a job; finish or wait"))
        return Verdict(not bad, bad)

    if st.status == "working":
        return Verdict(False, [("R1", f"{arm} is already working job {st.job}")])

    jid = p.get("job_id")
    if jid not in world.jobs:
        return Verdict(False, [("R2", f"job {jid!r} does not exist")])
    j = world.jobs[jid]

    # Job state and dependencies (start interface must exist)
    if act == "start_job":
        if j.status != "pending":
            bad.append(("R3", f"job {jid} is {j.status}; start_job needs a pending job"))
        missing = [d for d in j.deps if world.jobs[d].status != "done"]
        if missing:
            bad.append(("R4", f"dependencies not done: {missing}; no surface to build on yet"))
        if abs(j.z0 - world.as_built_top()) > 1e-6:
            bad.append(("R5", f"job starts at z={j.z0} but as-built top is {world.as_built_top():.2f}"))
    else:  # join_job
        if j.status != "active":
            bad.append(("R3", f"job {jid} is {j.status}; join_job needs an active job"))
        if len(j.arms) >= j.max_arms:
            bad.append(("R6", f"job {jid} already has {len(j.arms)}/{j.max_arms} arms"))
        if not p.get("interweave"):
            bad.append(("R7", "joining a wall without interweave would lay a bead on the partner's track"))
        partners = [world.state[a].station_deg for a in j.arms if world.state[a].station_deg is not None]
        st_new = p.get("station_deg")
        if partners and st_new is not None:
            off = abs((st_new - partners[0] + 180) % 360 - 180)
            if abs(off - 180) > 30:
                bad.append(("R13", f"interweave needs the joiner about 180 deg from its partner (offset is {off:.0f} deg), or the beads stack unevenly"))

    # Parameters
    station = p.get("station_deg")
    speed = p.get("surface_speed_mm_s")
    if station is None:
        bad.append(("R8", "station_deg is required"))
    if speed is None or speed <= 0:
        bad.append(("R9", "surface_speed_mm_s must be > 0"))
    elif speed > world.max_surface_speed + 1e-9:
        bad.append(("R9", f"surface speed {speed} exceeds extruder limit {world.max_surface_speed} mm/s"))
    if station is None:
        return Verdict(False, bad)

    # Geometry: reach for the whole height of the job, floor, workspace
    spec = world.arms[arm]
    for z in (max(j.z0, world.z_floor), j.z1):
        x, y, zz = world.nozzle_xyz(station, z)
        bx, by, bz = spec.base_xyz
        d = math.dist((x, y, zz), (bx, by, bz))
        if d > spec.reach_mm:
            bad.append(("R10", f"nozzle at station {station} deg, z={z} is {d:.0f} mm from {arm} base; reach {spec.reach_mm:.0f}"))
            break
    if world.radius > world.workspace_r:
        bad.append(("R11", f"wall radius {world.radius} outside workspace {world.workspace_r}"))

    # Clearance to the other arms' nozzles
    for other, ost in world.state.items():
        if other == arm or ost.station_deg is None:
            continue
        a = world.nozzle_xyz(station, 0)
        b = world.nozzle_xyz(ost.station_deg, 0)
        gap = math.dist(a[:2], b[:2])
        if gap < world.arm_min_gap:
            bad.append(("R12", f"{gap:.0f} mm from {other}'s nozzle; minimum {world.arm_min_gap:.0f} mm"))
    return Verdict(not bad, bad)
