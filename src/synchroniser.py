import time
import logging
from typing import List
from .arm_controller import ArmController
from .extruder_controller import ExtruderController
from .turntable_controller import TurntableController
from .command_parser import Segment

logger = logging.getLogger(__name__)

class PrintSynchroniser:
    def __init__(self,
                 arm_left: ArmController,
                 arm_right: ArmController,
                 extruder: ExtruderController,
                 turntable: TurntableController,
                 default_speed: float = 50,
                 default_extrusion_feedrate: float = 5.0):
        self.left = arm_left
        self.right = arm_right
        self.extruder = extruder
        self.turntable = turntable
        self.default_speed = default_speed
        self.default_extrusion_feedrate = default_extrusion_feedrate

    def execute_segment(self, seg: Segment):
        """Run one segment: arms, turntable, and extrusion in sync."""
        # 1. Start turntable move (if any). Sequential move before arms for simplicity.
        if seg.has_turntable_move:
            if seg.turntable_abs_angle is not None:
                self.turntable.rotate_absolute(seg.turntable_abs_angle,
                                               seg.turntable_speed or 20,
                                               wait=False)  # non-blocking for now? will block later.
            else:
                self.turntable.rotate_relative(seg.turntable_rel_angle,
                                               seg.turntable_speed or 20,
                                               wait=False)
            # For simplicity, we wait for turntable to finish before arm move (avoids collisions)
            self.turntable._wait_ok()

        # 2. Start arm motions (non-blocking)
        if seg.left_pose is not None:
            s = seg.left_speed or self.default_speed
            self.left.move_to(**seg.left_pose, speed=s, wait=False)

        if seg.right_pose is not None:
            s = seg.right_speed or self.default_speed
            self.right.move_to(**seg.right_pose, speed=s, wait=False)

        # 3. Extrude while arms are moving. We approximate by dividing extrusion
        #    into a few pulses (or one long command) – extruder will finish before arms.
        #    For better sync, we could time it, but a simple approach: extrude immediately.
        if seg.left_extrude > 0:
            fr = seg.extrusion_feedrate or self.default_extrusion_feedrate
            self.extruder.extrude(tool=0, length_mm=seg.left_extrude,
                                  feedrate_mm_s=fr)
        if seg.right_extrude > 0:
            fr = seg.extrusion_feedrate or self.default_extrusion_feedrate
            self.extruder.extrude(tool=1, length_mm=seg.right_extrude,
                                  feedrate_mm_s=fr)

        # 4. Wait for both arms to finish moving
        while self.left.arm.get_state()[1] == 1:  # 1 = moving
            time.sleep(0.05)
        while self.right.arm.get_state()[1] == 1:
            time.sleep(0.05)
        logger.debug("Segment complete")

    def execute_sequence(self, segments: List[Segment]):
        """Run a full sequence of coordinated segments."""
        for i, seg in enumerate(segments):
            logger.info(f"Executing segment {i+1}/{len(segments)}")
            self.execute_segment(seg)
        logger.info("Sequence finished")