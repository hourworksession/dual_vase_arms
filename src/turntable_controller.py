"""
Turntable controller for Aerotech iXC4 via the automation1 library.

The rotary axis is named ``"U"`` and is controlled by the iXC4 at the
given IP address. All moves use absolute positioning in degrees.
"""

import time
import logging
from typing import Optional

import automation1 as a1

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
        self.controller.runtime.commands.motion.moveabsolute(
            [self.axis],
            [angle_deg],
            [speed_dps]
        )
        if wait:
            self._wait_for_task("Rotate absolute")

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