"""Reconstruct G-code into a structured ``TaskGraph`` of toolpath segments.

The TaskGraph is the canonical intermediate representation between slicing
and splitting. It carries enough information that any of the splitters
(dual-spiral, Reeb, SafeZone) can operate on a single, well-typed object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from .gcode_parser import GcodeProgram, GMove


@dataclass(slots=True)
class Segment:
    """One straight extrusion or travel move."""
    start_xyz: tuple[float, float, float]
    end_xyz: tuple[float, float, float]
    extrude_mm: float           # delta-E
    feed_mm_min: float          # commanded feedrate
    is_travel: bool
    layer_index: int            # 0-based
    seq: int                    # ordinal within the program

    @property
    def length_mm(self) -> float:
        dx = self.end_xyz[0] - self.start_xyz[0]
        dy = self.end_xyz[1] - self.start_xyz[1]
        dz = self.end_xyz[2] - self.start_xyz[2]
        return math.sqrt(dx * dx + dy * dy + dz * dz)


@dataclass(slots=True)
class TaskGraph:
    """All segments in execution order, partitioned by layer."""
    segments: list[Segment] = field(default_factory=list)
    layers: list[list[int]] = field(default_factory=list)   # segment indices per layer

    def as_array(self) -> np.ndarray:
        """Return midpoints (N, 3) for fast geometric ops."""
        if not self.segments:
            return np.zeros((0, 3))
        return np.array(
            [
                [
                    0.5 * (s.start_xyz[0] + s.end_xyz[0]),
                    0.5 * (s.start_xyz[1] + s.end_xyz[1]),
                    0.5 * (s.start_xyz[2] + s.end_xyz[2]),
                ]
                for s in self.segments
            ]
        )


def reconstruct(prog: GcodeProgram) -> TaskGraph:
    """Convert the flat move list into per-layer segments."""
    graph = TaskGraph()
    if not prog.moves:
        return graph

    prev: GMove | None = None
    layer_index = -1
    last_z: float | None = None
    current_layer: list[int] = []

    last_e: float | None = None
    for mv in prog.moves:
        if mv.x is None or mv.y is None or mv.z is None:
            prev = mv
            if mv.e is not None:
                last_e = mv.e
            continue

        # New layer detected when Z rises by more than 1 µm.
        if last_z is None or mv.z > last_z + 1e-3:
            layer_index += 1
            last_z = mv.z
            if current_layer:
                graph.layers.append(current_layer)
            current_layer = []

        if prev is not None and prev.x is not None and prev.y is not None and prev.z is not None:
            de = 0.0
            if mv.e is not None and last_e is not None:
                de = mv.e - last_e
            seg = Segment(
                start_xyz=(prev.x, prev.y, prev.z),
                end_xyz=(mv.x, mv.y, mv.z),
                extrude_mm=max(de, 0.0),
                feed_mm_min=mv.f or 0.0,
                is_travel=mv.is_travel or de <= 1e-9,
                layer_index=layer_index,
                seq=len(graph.segments),
            )
            graph.segments.append(seg)
            current_layer.append(seg.seq)

        prev = mv
        if mv.e is not None:
            last_e = mv.e

    if current_layer:
        graph.layers.append(current_layer)
    return graph
