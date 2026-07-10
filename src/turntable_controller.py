"""
Turntable controller for Aerotech iXC4 via the automation1 library.

The rotary axis is named ``"U"`` and is controlled by the iXC4 at the
given IP address. All moves use absolute positioning in degrees.
"""

import time
import logging
from typing import Optional

#import automation1 as a1
import pyautomation as a1

logger = logging.getLogger(__name__)


class TurntableController:
    """Controls a single rotary axis on an Aerotech iXC4 controller.

    Args:
        host: IP address of the iXC4 controller.
        axis: Name of the rotary axis (default ``"U"``).
    """

    def __init__(self, host: str, axis: str = "U") -> None:
        self.host = host
        self.axis = axis
        self.controller: Optional[a1.Controller] = None
        self._connected = False

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def moving_q(self, axis_status) -> bool:  
            axis_status = int(axis_status)
            #Defining Bit Masks From AeroTech API
            jogging = 1 << 8
    
            #Need To Add Leading Zeros To Pack Enum To 26 Bits
            binary = bin(axis_status)[2:] #Get Raw Binary Number 
            pad_bin = int(binary.zfill(26)) #Pad To Make 26 Bits
    
            value = pad_bin & jogging != 0
    
            return value
    
    def wait_motion_start(self, timeout=5.0):
        """Wait until the rotary axis task is Running (motion has actually started)."""
        if self.controller is None:
            return
        start = time.time()
        while True:
            state = self.controller.runtime.tasks[1].status.task_state
            if state == "Running":
                break
            if time.time() - start > timeout:
                logger.warning("Turntable motion did not start within %.1f s", timeout)
                break
            time.sleep(0.02)    


    def get_angle(self) -> float:
        """Return current absolute angle of the rotary axis in degrees."""
        config = a1.StatusItemConfiguration()
        config.axis.add(a1.AxisStatusItem.PositionFeedback, self.axis)
        status = self.controller.runtime.status.get_status_items(config)
        return status.axis.get(a1.AxisStatusItem.PositionFeedback, self.axis).value

    def is_moving(self) -> bool:
        """Return True if the rotary axis is currently executing a motion."""
        if self.controller is None or not self._connected:
            return False
    
        try:
            # Request the AxisStatus item for our axis
            config = a1.StatusItemConfiguration()
            config.axis.add(a1.AxisStatusItem.AxisStatus, self.axis)
            status_item = self.controller.runtime.status.get_status_items(config)
            axis_status = status_item.axis.get(a1.AxisStatusItem.AxisStatus, self.axis).value
    
            # Bit 8 = jogging / moving
            return (axis_status >> 8) & 1 == 1
        except Exception:
            # Fallback: use task state
            task_status = self.controller.runtime.tasks[1].status
            return task_status.task_state == "Running"

    def connect(self) -> None:
        """Establish connection to the iXC4 and enable the rotary axis."""
        if self._connected:
            logger.warning("Turntable already connected.")
            return

        logger.info(f"Connecting to iXC4 at {self.host}...")
        self.controller = a1.Controller.connect(host=self.host)
        logger.info("Connected to iXC4.")
        
        # Start the controller runtime if not already running
        self.controller.start()
        logger.info("Controller started.")

        config_temp = a1.StatusItemConfiguration()
        config_temp.axis.add(a1.AxisStatusItem.AxisStatus, self.axis)
        axis_status = 0
        print(f"Axis Status:",{axis_status})
        axis_status = self.controller.runtime.status.get_status_items(config_temp).axis.get(a1.AxisStatusItem.AxisStatus, self.axis).value
        print(f"Axis Status:",{axis_status})
        testing_value = self.moving_q(axis_status)
        if testing_value == True:
            self.controller.runtime.commands.motion.movefreerunstop(self.axis)
        # Enable the axis
        self.controller.runtime.commands.motion.enable([self.axis])
        self._wait_for_task("Enabling axis")
        # Home the axis
        self.controller.runtime.commands.motion.home([self.axis])
        self._wait_for_task("Homing axis")
        self._connected = True
        logger.info(f"Axis '{self.axis}' enabled and homed.")

    

    def disconnect(self) -> None:
        """Disable the axis and disconnect from the controller."""
        if not self._connected or self.controller is None:
            return

        logger.info(f"Disabling axis '{self.axis}'...")
        self.controller.runtime.commands.motion.disable([self.axis])
        self._wait_for_task("Disabling axis")

        self.controller.disconnect()
        self._connected = False
        logger.info("Turntable disconnected.")

    # ------------------------------------------------------------------
    # Motion commands
    # ------------------------------------------------------------------

    def rotate_absolute(self, angle_deg: float, speed_dps: float,
                        wait: bool = True) -> None:
        """Rotate the turntable to an absolute angle.

        Args:
            angle_deg: Target angle in degrees.
            speed_dps: Rotation speed in degrees per second.
            wait: If ``True``, block until the motion is complete.
        """
        if not self._connected:
            raise RuntimeError("Turntable not connected. Call connect() first.")

        logger.info(f"Moving axis '{self.axis}' to {angle_deg:.2f}° "
                     f"at {speed_dps:.1f}°/s")
        self._last_target_angle = angle_deg
        self.controller.runtime.commands.motion.moveabsolute(
            [self.axis],
            [angle_deg],
            [speed_dps]
        )
        if wait:
            self._wait_for_task("Rotate absolute")
    
    def rotate_linear(self, angle_deg, speed_dps):
        self.controller.runtime.commands.motion.movelinear(self.axis, [angle_deg], speed_dps)

    def rotate_relative(self, angle_deg: float, speed_dps: float,
                        wait: bool = True) -> None:
        """Rotate the turntable by a relative angle.

        Args:
            angle_deg: Relative angle in degrees.
            speed_dps: Rotation speed in degrees per second.
            wait: If ``True``, block until the motion is complete.
        """
        if not self._connected:
            raise RuntimeError("Turntable not connected. Call connect() first.")

        logger.info(f"Incrementing axis '{self.axis}' by {angle_deg:.2f}° "
                     f"at {speed_dps:.1f}°/s")
        self.controller.runtime.commands.motion.moveincremental(
            [self.axis],
            [angle_deg],
            [speed_dps]
        )
        if wait:
            self._wait_for_task("Rotate relative")


    def rotate_velocity(self, speed_dps: float) -> None:
        """Start rotating at a constant velocity (asynchronous, free‑run).
        Use speed_dps = 0 to stop the motion.
        """
        if not self._connected:
            raise RuntimeError("Turntable not connected.")
        logger.info(f"Setting axis '{self.axis}' free‑run velocity to {speed_dps:.1f}°/s")
        self.controller.runtime.commands.motion.movefreerun(
            [self.axis],     # list of axes
            [speed_dps]      # list of corresponding velocities
        )

    def stop_rotation(self) -> None:
        """Convenience method to stop the free‑run motion."""
        self.rotate_velocity(0.0)


    def wait_ok(self, timeout: Optional[float] = None) -> None:
        """Wait until the turntable motion is complete.

        Args:
            timeout: Maximum time to wait in seconds. ``None`` means no limit.
        """
        self._wait_for_task("Motion", timeout=timeout)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _wait_for_task(self, action_name: str,
                       timeout: Optional[float] = None) -> None:
        """Poll the controller task status until the current task finishes.

        Args:
            action_name: Human-readable label for log messages.
            timeout: Maximum seconds to wait (``None`` = no limit).
        """
        if self.controller is None:
            raise RuntimeError("Controller is None; cannot wait for task.")

        task_status = self.controller.runtime.tasks[1].status
        start = time.time()

        while task_status.task_state == "Running":
            if timeout and (time.time() - start) > timeout:
                raise TimeoutError(f"Timeout waiting for '{action_name}'.")
            time.sleep(0.1)

        if task_status.error:
            raise RuntimeError(
                f"Error during '{action_name}': {task_status.error_message}"
            )
        logger.info(f"'{action_name}' completed successfully.")