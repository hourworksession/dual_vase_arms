"""
Safe printing zones: which half of the disc each arm is allowed to work in.

The two arms face each other across the turntable. To avoid collisions the disc
is split into two halves by a line through the centre, rotated 45 deg clockwise
from the vertical (left/right) split. The RIGHT arm owns the right / lower-right
half; the LEFT arm owns the opposite half.

Geometry (world frame, disc-centre origin, top-down X-right / Y-up):
  * Each zone is a half-plane through the origin, described by the direction its
    interior points toward -- its "centre" angle.
  * Default: right zone centre = -45 deg (lower-right), left zone centre = +135
    deg (upper-left); the dividing line therefore runs at 45 deg (y = x).
  * A world point (x, y) is in a zone if it lies on that zone's side of the
    line, i.e. its dot product with the zone-centre direction is positive.

All angles in degrees. Flip ``right_zone_center_deg`` (or edit calibration.yaml)
if the assignment is mirrored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class SafeZones:
    right_zone_center_deg: float = -45.0
    left_zone_center_deg: float = 135.0
    right_color: Tuple[float, float, float] = (0.95, 0.55, 0.15)   # orange
    left_color: Tuple[float, float, float] = (0.20, 0.50, 0.95)    # blue

    @classmethod
    def from_dict(cls, d: dict) -> "SafeZones":
        d = d or {}
        right = float(d.get("right_zone_center_deg", -45.0))
        left = float(d.get("left_zone_center_deg", right + 180.0))
        return cls(right_zone_center_deg=right, left_zone_center_deg=left)

    # -- queries ---------------------------------------------------------

    def center_deg(self, arm: str) -> float:
        return self.right_zone_center_deg if arm == "right" else self.left_zone_center_deg

    def color(self, arm: str) -> Tuple[float, float, float]:
        return self.right_color if arm == "right" else self.left_color

    def _side_value(self, arm: str, x: float, y: float) -> float:
        """Signed distance metric: > 0 means (x, y) is inside ``arm``'s zone."""
        c = math.radians(self.center_deg(arm))
        return x * math.cos(c) + y * math.sin(c)

    def zone_of(self, x: float, y: float, tol: float = 1e-9) -> str:
        """Return 'right', 'left', or 'boundary' for a world point."""
        rv = self._side_value("right", x, y)
        if rv > tol:
            return "right"
        if rv < -tol:
            return "left"
        return "boundary"

    def contains(self, arm: str, x: float, y: float, tol: float = 1e-9) -> bool:
        return self._side_value(arm, x, y) > -tol

    def angular_bounds(self, arm: str) -> Tuple[float, float]:
        """[start, end] degrees of the half-disc sector for drawing."""
        c = self.center_deg(arm)
        return (c - 90.0, c + 90.0)

    def divider_deg(self) -> float:
        """Angle of the dividing line (through the centre)."""
        return self.right_zone_center_deg + 90.0
