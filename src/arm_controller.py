import time
import logging
from xarm.wrapper import XArmAPI

logger = logging.getLogger(__name__)

class ArmController:
    """Controls a single xArm 8 via the xArm Python SDK."""
    def __init__(self, ip: str, name: str = "arm"):
        self.ip = ip
        self.name = name
        self.arm = None

    def connect(self):
        """Connect to the xArm and enable motion."""
        try:
            self.arm = XArmAPI(self.ip)
            self.arm.motion_enable(enable=True)
            self.arm.set_mode(0)        # position mode
            self.arm.set_state(0)       # sport state
            logger.info("%s connected at %s", self.name, self.ip)
        except Exception as e:
            logger.error("Failed to connect %s: %s", self.name, e)
            raise

    def home(self, wait=True):
        """Homing with default sequence; ensure no obstacles."""
        try:
            self.arm.move_gohome(wait=wait)
        except Exception as e:
            logger.error("Failed to home %s: %s", self.name, e)
            raise

    def move_to(self, x, y, z, roll=180, pitch=0, yaw=0,
                speed=None, wait=False):
        """Move tool-centre-point to Cartesian pose (mm, degrees)."""
        try:
            if speed is None:
                speed = self.arm.get_default_move_speed() or 100
            self.arm.set_position(x=x, y=y, z=z,
                              roll=roll, pitch=pitch, yaw=yaw,
                              speed=speed, wait=False)
        except Exception as e:
            logger.error("Failed to move %s to position: %s", self.name, e)
            raise

    def get_pose(self):
        """Return current pose (x,y,z,roll,pitch,yaw)."""
        try:
            code, pose = self.arm.get_position()
            if code == 0:
                return pose[1:]  # ignore error code
            else:
                logger.error("Failed to get pose: %s", code)
                return None
        except Exception as e:
            logger.error("Error retrieving pose for %s: %s", self.name, e)
            raise

    def get_joints(self):
        """Return current joint angles (servo angles) in degrees, or None.

        Used by the digital twin to mirror the arm's exact configuration
        instead of inferring it from the TCP pose. Wraps the xArm SDK's
        ``get_servo_angle`` (is_radian=False -> degrees).
        """
        try:
            code, angles = self.arm.get_servo_angle(is_radian=False)
            if code == 0:
                return angles[:6]
            logger.error("Failed to get joints for %s: %s", self.name, code)
            return None
        except Exception as e:
            logger.error("Error retrieving joints for %s: %s", self.name, e)
            return None

    def emergency_stop(self):
        """Trigger emergency stop."""
        try:
            self.arm.emergency_stop()
        except Exception as e:
            logger.error("Failed to emergency stop %s: %s", self.name, e)
            raise

    def disconnect(self):
        """Disconnect from the xArm."""
        try:
            if self.arm:
                self.arm.disconnect()
                logger.info("%s disconnected", self.name)
        except Exception as e:
            logger.error("Failed to disconnect %s: %s", self.name, e)
            raise
