import csv
import time
import os
from datetime import datetime
from typing import Optional

class DataLogger:
    """Logs one row per segment with full commanded state.
    
    Automatically creates a ``logs/`` folder and writes a timestamped CSV file.
    """

    COLUMNS = [
        "segment",
        "timestamp_iso",
        "timestamp_unix",
        # left arm
        "left_x", "left_y", "left_z", "left_roll", "left_pitch", "left_yaw", "left_speed",
        # right arm
        "right_x", "right_y", "right_z", "right_roll", "right_pitch", "right_yaw", "right_speed",
        # extrusion
        "left_extrude_mm", "left_extrude_feedrate",
        "right_extrude_mm", "right_extrude_feedrate",
        # turntable
        "turntable_angle", "turntable_rel_angle", "turntable_speed",
        "status"
    ]

    def __init__(self, folder: str = "logs", filename_prefix: str = "print") -> None:
        """
        Args:
            folder: Directory where log files will be stored. Will be created if needed.
            filename_prefix: Prefix for the CSV file. Date/time will be appended.
        """
        self.folder = folder
        os.makedirs(self.folder, exist_ok=True)

        # Generate filename: print_2026-05-10_14-28-00.csv
        now_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.filepath = os.path.join(self.folder, f"{filename_prefix}_{now_str}.csv")

        self.file = open(self.filepath, 'w', newline='')
        self.writer = csv.writer(self.file)
        self.writer.writerow(self.COLUMNS)

    def log(self,
            segment_idx: int,
            left_pose: Optional[dict] = None,
            right_pose: Optional[dict] = None,
            left_extrude: float = 0.0,
            right_extrude: float = 0.0,
            extrusion_feedrate: Optional[float] = None,
            turntable_abs_angle: Optional[float] = None,
            turntable_rel_angle: Optional[float] = None,
            turntable_speed: Optional[float] = None,
            status: str = "OK") -> None:
        """
        Append one row to the log.

        Args:
            segment_idx: 1‑based segment number.
            left_pose: Dict with keys ``x,y,z,roll,pitch,yaw,speed`` (if left arm moved).
            right_pose: Same for right arm.
            left_extrude: mm of filament on T0.
            right_extrude: mm of filament on T1.
            extrusion_feedrate: Feedrate used for extrusion (mm/s).
            turntable_abs_angle: Absolute angle command, if any.
            turntable_rel_angle: Relative angle command, if any.
            turntable_speed: Turntable speed in °/s.
            status: 'OK' or an error description.
        """
        now = time.time()
        iso = datetime.utcfromtimestamp(now).isoformat(timespec='microseconds')

        def pose_defaults(pose):
            if pose:
                return [
                    pose.get('x', 0), pose.get('y', 0), pose.get('z', 0),
                    pose.get('roll', 180), pose.get('pitch', 0), pose.get('yaw', 0)
                ]
            return [0]*6

        left_vals = pose_defaults(left_pose)
        right_vals = pose_defaults(right_pose)

        left_fr = extrusion_feedrate if left_extrude > 0 else 0.0
        right_fr = extrusion_feedrate if right_extrude > 0 else 0.0

        row = [
            segment_idx,
            iso,
            now,
            *left_vals,
            left_pose.get('speed', 0) if left_pose else 0,
            *right_vals,
            right_pose.get('speed', 0) if right_pose else 0,
            left_extrude, left_fr,
            right_extrude, right_fr,
            turntable_abs_angle if turntable_abs_angle is not None else "",
            turntable_rel_angle if turntable_rel_angle is not None else "",
            turntable_speed if turntable_speed is not None else "",
            status
        ]
        self.writer.writerow(row)

    def close(self) -> None:
        self.file.close()