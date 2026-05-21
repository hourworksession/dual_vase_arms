"""Reeb-decomposition splitter (Khatkar et al., 2024).

Stub. The Reeb graph of the part's height function decomposes the model
into topological "chunks" that can be 3D-printed in parallel by different
arms without violating layer-precedence constraints. This is the right
strategy for parts that are *not* radially symmetric.

Out of scope for the dual-spiral MVP but the file is reserved so the rest
of the pipeline can switch strategies via the config without code edits.
"""
from __future__ import annotations

from ..slicing.reconstructor import TaskGraph
from .base import ArmPlan, SplitResult


class ReebSplitter:
    def split(self, graph: TaskGraph) -> SplitResult:  # pragma: no cover
        raise NotImplementedError(
            "Reeb-decomposition splitter is a planned strategy; "
            "see Khatkar et al. 2024 for the algorithm."
        )
