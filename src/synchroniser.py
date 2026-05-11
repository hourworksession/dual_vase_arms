import time
import logging
from typing import List
from tqdm import tqdm
from .arm_controller import ArmController
from .extruder_controller import ExtruderController
from .turntable_controller import TurntableController
from .command_parser import Segment
from .logger import DataLogger

logger = logging.getLogger(__name__)

class PrintSynchroniser:
    def __init__(self,
                 arm_left: ArmController,
                 arm_right: ArmController,
                 extruder: ExtruderController,
                 turntable: TurntableController,
                 default_speed: float = 50,
                 default_extrusion_feedrate: float = 5.0,
                 logger_instance: DataLogger = None):
        """
        Args:
            arm_left: Left arm controller.
            arm_right: Right arm controller.
            extruder: Extruder controller (Klipper/Moonraker).
            turntable: Turntable controller (serial).
            default_speed: Default arm speed (mm/s) if segment doesn't specify.
            default_extrusion_feedrate: Default filament feedrate (mm/s).
            logger_instance: Optional DataLogger. If None, a default one is created.
        """
        self.left = arm_left
        self.right = arm_right
        self.extruder = extruder
        self.turntable = turntable
        self.default_speed = default_speed
        self.default_extrusion_feedrate = default_extrusion_feedrate
        self.data_logger = logger_instance or DataLogger()

    def execute_segment(self, seg: Segment, seg_idx: int = 0):
        """Run one segment: arms, turntable, and extrusion in sync, then log it."""
        # 1. Turntable move (sequential for safety)
        if seg.has_turntable_move:
            if seg.turntable_abs_angle is not None:
                self.turntable.rotate_absolute(seg.turntable_abs_angle,
                                               seg.turntable_speed or 20,
                                               wait=False)
            else:
                self.turntable.rotate_relative(seg.turntable_rel_angle,
                                               seg.turntable_speed or 20,
                                               wait=False)
            self.turntable.wait_ok()

        # 2. Start arm motions (non-blocking)
        if seg.left_pose is not None:
            s = seg.left_speed or self.default_speed
            self.left.move_to(**seg.left_pose, speed=s, wait=False)

        if seg.right_pose is not None:
            s = seg.right_speed or self.default_speed
            self.right.move_to(**seg.right_pose, speed=s, wait=False)

        # 3. Extrude while arms are moving
        if seg.left_extrude > 0:
            fr = seg.extrusion_feedrate or self.default_extrusion_feedrate
            self.extruder.extrude(tool=0, length_mm=seg.left_extrude,
                                  feedrate_mm_s=fr)
        if seg.right_extrude > 0:
            fr = seg.extrusion_feedrate or self.default_extrusion_feedrate
            self.extruder.extrude(tool=1, length_mm=seg.right_extrude,
                                  feedrate_mm_s=fr)

        # 4. Wait for both arms to finish moving
        while self.left.arm.get_state()[1] == 1:      # 1 = moving
            time.sleep(0.05)
        while self.right.arm.get_state()[1] == 1:
            time.sleep(0.05)

        # 5. Log the completed segment with full command data
        self.data_logger.log(
            segment_idx=seg_idx,
            left_pose=seg.left_pose,
            right_pose=seg.right_pose,
            left_extrude=seg.left_extrude,
            right_extrude=seg.right_extrude,
            extrusion_feedrate=seg.extrusion_feedrate or self.default_extrusion_feedrate,
            turntable_abs_angle=seg.turntable_abs_angle,
            turntable_rel_angle=seg.turntable_rel_angle,
            turntable_speed=seg.turntable_speed,
            status="OK"
        )
        logger.debug(f"Segment {seg_idx} complete")

    def execute_sequence(self, segments: List[Segment]):
        """Run a full sequence of coordinated segments with a progress bar."""
        for i, seg in enumerate(tqdm(segments, desc="Printing", unit="seg")):
            logger.info(f"Executing segment {i+1}/{len(segments)}")
            self.execute_segment(seg, seg_idx=i+1)
        logger.info("Sequence finished")