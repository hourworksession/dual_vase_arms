from dataclasses import dataclass, field
from typing import Optional, Dict, List

@dataclass
class Segment:
    """One coordinated motion step."""
    # Arm Cartesian targets (mm, degrees). 'left' or 'right' can be None if stationary.
    left_pose: Optional[Dict[str, float]] = None   # {x,y,z,roll,pitch,yaw}
    right_pose: Optional[Dict[str, float]] = None
    # Speed overrides (None = use default)
    left_speed: Optional[float] = None
    right_speed: Optional[float] = None
    # Extrusion per tool (mm of filament)
    left_extrude: float = 0.0
    right_extrude: float = 0.0
    extrusion_feedrate: Optional[float] = None   # mm/s
    # Turntable command: choose absolute OR relative
    turntable_abs_angle: Optional[float] = None
    turntable_rel_angle: Optional[float] = None
    turntable_speed: Optional[float] = None      # deg/s

    @property
    def has_arm_move(self):
        return self.left_pose is not None or self.right_pose is not None

    @property
    def has_turntable_move(self):
        return (self.turntable_abs_angle is not None or
                self.turntable_rel_angle is not None)