"""Minimal G-code parser tuned to FullControl's output.

We only need the subset of G-code that FullControl emits: linear moves
(G0/G1) with X/Y/Z/E/F, plus the occasional comment. This parser
intentionally does not implement arcs, modal groups, subprograms, etc.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass(slots=True)
class GMove:
    x: float | None = None
    y: float | None = None
    z: float | None = None
    e: float | None = None       # extruder cumulative position (mm of filament)
    f: float | None = None       # feedrate (mm/min)
    raw_index: int = 0           # line number in the source file
    is_travel: bool = False      # True if this move did not extrude


@dataclass(slots=True)
class GcodeProgram:
    moves: list[GMove] = field(default_factory=list)
    comments: list[tuple[int, str]] = field(default_factory=list)


def _parse_word(token: str) -> tuple[str, float] | None:
    if len(token) < 2:
        return None
    letter = token[0].upper()
    try:
        value = float(token[1:])
    except ValueError:
        return None
    return letter, value


def parse_gcode(path: str | Path) -> GcodeProgram:
    prog = GcodeProgram()
    last_e: float | None = None
    state = {"x": None, "y": None, "z": None, "f": None}

    for idx, raw in enumerate(Path(path).read_text().splitlines()):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(";"):
            prog.comments.append((idx, line[1:].strip()))
            continue
        # strip inline comments
        if ";" in line:
            line, comment = line.split(";", 1)
            prog.comments.append((idx, comment.strip()))
        tokens = line.split()
        if not tokens:
            continue
        head = tokens[0].upper()
        if head not in ("G0", "G00", "G1", "G01"):
            continue

        mv = GMove(raw_index=idx)
        for tok in tokens[1:]:
            parsed = _parse_word(tok)
            if not parsed:
                continue
            letter, value = parsed
            if letter == "X":
                mv.x = value
            elif letter == "Y":
                mv.y = value
            elif letter == "Z":
                mv.z = value
            elif letter == "E":
                mv.e = value
            elif letter == "F":
                mv.f = value

        # Inherit state for unset axes
        if mv.x is None:
            mv.x = state["x"]
        if mv.y is None:
            mv.y = state["y"]
        if mv.z is None:
            mv.z = state["z"]
        if mv.f is None:
            mv.f = state["f"]

        # Determine travel vs extrusion by delta-E
        if mv.e is None:
            mv.is_travel = True
        elif last_e is not None and mv.e - last_e <= 1e-9:
            mv.is_travel = True
        else:
            mv.is_travel = head in ("G0", "G00")

        state["x"], state["y"], state["z"], state["f"] = mv.x, mv.y, mv.z, mv.f
        if mv.e is not None:
            last_e = mv.e
        prog.moves.append(mv)

    return prog


def iter_segments(prog: GcodeProgram) -> Iterator[tuple[GMove, GMove]]:
    """Yield consecutive (prev, curr) move pairs that form a line segment."""
    prev = None
    for mv in prog.moves:
        if prev is not None and prev.x is not None and prev.y is not None:
            yield prev, mv
        prev = mv
