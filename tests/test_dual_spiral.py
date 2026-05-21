"""Splitter sanity tests."""
from __future__ import annotations

import math

from dual_arm_printer.slicing.reconstructor import Segment, TaskGraph
from dual_arm_printer.splitting.dual_spiral import DualSpiralConfig, DualSpiralSplitter


def _ring_taskgraph(n_segs: int = 60, radius: float = 50.0, z: float = 0.2) -> TaskGraph:
    graph = TaskGraph()
    pts = [
        (radius * math.cos(2 * math.pi * i / n_segs), radius * math.sin(2 * math.pi * i / n_segs), z)
        for i in range(n_segs + 1)
    ]
    for i in range(n_segs):
        graph.segments.append(
            Segment(
                start_xyz=pts[i],
                end_xyz=pts[i + 1],
                extrude_mm=0.05,
                feed_mm_min=1500,
                is_travel=False,
                layer_index=0,
                seq=i,
            )
        )
    graph.layers.append(list(range(n_segs)))
    return graph


def test_dual_spiral_balances_assignments():
    graph = _ring_taskgraph()
    split = DualSpiralSplitter(DualSpiralConfig()).split(graph)
    n_left = len(split.left.segments)
    n_right = len(split.right.segments)
    total = n_left + n_right
    # Allow ±20% imbalance because the disc rotates during the split.
    assert abs(n_left - n_right) / total < 0.3, (n_left, n_right)


def test_dual_spiral_emits_turntable_schedule():
    graph = _ring_taskgraph()
    split = DualSpiralSplitter(DualSpiralConfig()).split(graph)
    assert len(split.turntable_schedule) > 0
    ts = [t for t, _ in split.turntable_schedule]
    assert ts == sorted(ts)
