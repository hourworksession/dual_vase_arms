"""SafeZone time-optimal cooperative scheduler (Stone et al., 2025).

Stub. Builds a graph where nodes are toolpath patches and edges are
spatial-temporal incompatibilities; finds the assignment minimising
makespan subject to no-collision constraints.

Use when the part is large enough that wall-clock time matters more than
geometric symmetry.
"""
from __future__ import annotations

from ..slicing.reconstructor import TaskGraph
from .base import SplitResult


class SafeZoneSplitter:
    def split(self, graph: TaskGraph) -> SplitResult:  # pragma: no cover
        raise NotImplementedError(
            "SafeZone splitter is a planned strategy; see Stone et al. 2025."
        )
