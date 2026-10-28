"""Blackboard: the cell, the part, the job list and the simulated as-built state.

Simplifications for the first test bed (stated so results are read correctly):
- The disc turns continuously; each arm's nozzle sits at a fixed world angle
  (its "station") on the wall radius, so a wall job is a spiral laid while the
  disc turns under it.
- Bead height per revolution = ``pitch``. Two arms interweaving on one wall
  each lay one bead per revolution, offset by half a revolution, so the wall
  rises 2 x pitch per revolution.
- Time per revolution = wall circumference / surface speed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass
class ArmSpec:
    name: str
    base_xyz: tuple[float, float, float]
    reach_mm: float
    role: str                    # "primary" or "greedy"
    default_station_deg: float


@dataclass
class Job:
    id: str
    feature: str
    z0: float
    z1: float
    deps: list[str]
    max_arms: int = 1
    status: str = "pending"      # pending | active | done
    done_mm: float = 0.0         # height laid so far
    arms: list[str] = field(default_factory=list)
    started_s: float | None = None
    finished_s: float | None = None

    @property
    def height(self) -> float:
        return self.z1 - self.z0

    @property
    def remaining(self) -> float:
        return max(0.0, self.height - self.done_mm)


@dataclass
class ArmState:
    status: str = "idle"         # idle | working | parked
    job: str | None = None
    station_deg: float | None = None
    speed_mm_s: float = 0.0
    busy_s: float = 0.0


@dataclass
class World:
    name: str
    radius: float
    bead: float
    pitch: float
    max_surface_speed: float
    z_floor: float
    workspace_r: float
    arm_min_gap: float
    arms: dict[str, ArmSpec]
    jobs: dict[str, Job]
    t: float = 0.0
    state: dict[str, ArmState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for n in self.arms:
            self.state.setdefault(n, ArmState())

    # ------------------------------------------------------------------
    @classmethod
    def from_yaml(cls, scenario_path: str, system_path: str | None = None) -> "World":
        sc = yaml.safe_load(open(scenario_path, encoding="utf-8"))
        cell = {}
        if system_path:
            cell = yaml.safe_load(open(system_path, encoding="utf-8")).get("cell", {})
        arms_cfg = cell.get("arms", {})
        arms = {}
        for name, a in sc["arms"].items():
            base = arms_cfg.get(name, {})
            arms[name] = ArmSpec(
                name=name,
                base_xyz=tuple(a.get("base_xyz", base.get("base_world_xyz", [0, 0, 0]))),
                reach_mm=float(a.get("reach_mm", base.get("reach_mm", 850.0))),
                role=a["role"],
                default_station_deg=float(a["station_deg"]),
            )
        part = sc["part"]
        safety = cell.get("safety", {})
        jobs = {}
        for j in sc["jobs"]:
            jobs[j["id"]] = Job(id=j["id"], feature=j["feature"], z0=float(j["z0"]),
                                z1=float(j["z1"]), deps=list(j.get("deps", [])),
                                max_arms=int(j.get("max_arms", 1)))
        return cls(
            name=sc["name"],
            radius=float(part["radius"]),
            bead=float(part["bead"]),
            pitch=float(part["pitch"]),
            max_surface_speed=float(part["max_surface_speed_mm_s"]),
            z_floor=float(sc.get("z_floor_mm", safety.get("nozzle_to_disc_z_min_mm", 0.2))),
            workspace_r=float(cell.get("workspace", {}).get("outer_radius_mm", 295.0)),
            arm_min_gap=float(sc.get("arm_min_gap_mm", safety.get("arm_arm_min_distance_mm", 80.0))),
            arms=arms,
            jobs=jobs,
        )

    # ------------------------------------------------------------------
    def nozzle_xyz(self, station_deg: float, z: float) -> tuple[float, float, float]:
        a = math.radians(station_deg)
        return (self.radius * math.cos(a), self.radius * math.sin(a), z)

    def as_built_top(self) -> float:
        done = [j.z1 for j in self.jobs.values() if j.status == "done"]
        return max(done, default=0.0)

    def rev_time(self, speed: float) -> float:
        return 2 * math.pi * self.radius / speed

    def all_done(self) -> bool:
        return all(j.status == "done" for j in self.jobs.values())

    def snapshot(self) -> dict[str, Any]:
        """What an agent sees: compact, JSON-serialisable."""
        return {
            "t_s": round(self.t, 1),
            "part": {"wall_radius_mm": self.radius, "bead_mm": self.bead,
                     "pitch_mm_per_rev": self.pitch,
                     "max_surface_speed_mm_s": self.max_surface_speed},
            "as_built_top_mm": round(self.as_built_top(), 2),
            "jobs": [
                {"id": j.id, "feature": j.feature, "z0": j.z0, "z1": j.z1, "deps": j.deps,
                 "max_arms": j.max_arms, "status": j.status,
                 "remaining_mm": round(j.remaining, 2), "arms": list(j.arms)}
                for j in self.jobs.values()
            ],
            "arms": {
                n: {"role": self.arms[n].role, "status": s.status, "job": s.job,
                    "station_deg": s.station_deg,
                    "default_station_deg": self.arms[n].default_station_deg}
                for n, s in self.state.items()
            },
        }

    # ------------------------------------------------------------------
    # Simulated execution
    def apply(self, arm: str, action: dict) -> None:
        s = self.state[arm]
        act = action["action"]
        if act in ("start_job", "join_job"):
            j = self.jobs[action["job_id"]]
            if j.status == "pending":
                j.status, j.started_s = "active", self.t
            j.arms.append(arm)
            s.status, s.job = "working", j.id
            s.station_deg = float(action["station_deg"])
            s.speed_mm_s = float(action["surface_speed_mm_s"])
        elif act == "park":
            s.status, s.job, s.station_deg = "parked", None, None
        # "wait" changes nothing

    def _job_rate(self, j: Job) -> float:
        """mm of wall height per second for an active job (slowest arm sets the disc)."""
        speeds = [self.state[a].speed_mm_s for a in j.arms]
        if not speeds:
            return 0.0
        rev = self.rev_time(min(speeds))
        return self.pitch * len(j.arms) / rev

    def advance(self) -> list[str]:
        """Run to the next job completion. Returns ids of jobs that finished."""
        active = [j for j in self.jobs.values() if j.status == "active" and j.arms]
        if not active:
            return []
        dt = min(j.remaining / self._job_rate(j) for j in active)
        finished = []
        for j in active:
            j.done_mm += self._job_rate(j) * dt
            for a in j.arms:
                self.state[a].busy_s += dt
            if j.remaining < 1e-6:
                j.status, j.finished_s, j.done_mm = "done", self.t + dt, j.height
                for a in j.arms:
                    st = self.state[a]
                    st.status, st.job, st.station_deg, st.speed_mm_s = "idle", None, None, 0.0
                j.arms = []
                finished.append(j.id)
        self.t += dt
        return finished
