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
        self.arm = XArmAPI(self.ip)
        self.arm.motion_enable(enable=True)
        self.arm.set_mode(0)        # position mode
        self.arm.set_state(0)       # sport state
        logger.info(f"{self.name} connected at {self.ip}")

    def home(self, wait=True):
        """Homing with default sequence; ensure no obstacles."""
        self.arm.move_gohome(wait=wait)
        # Optionally set a custom home position after:
        # self.arm.set_position(x=..., y=..., z=..., roll=..., pitch=..., yaw=..., wait=True)

    def move_to(self, x, y, z, roll=180, pitch=0, yaw=0,
                speed=None, wait=False):
        """Move tool-centre-point to Cartesian pose (mm, degrees)."""
        if speed is None:
            speed = self.arm.get_default_move_speed() or 100
        self.arm.set_position(x=x, y=y, z=z,
                              roll=roll, pitch=pitch, yaw=yaw,
                              speed=speed, wait=wait)

    def get_pose(self):
        """Return current pose (x,y,z,roll,pitch,yaw)."""
        code, pose = self.arm.get_position()
        if code == 0:
            return pose[1:]  # ignore error code
        else:
            logger.error(f"Failed to get pose: {code}")
            return None

    def emergency_stop(self):
        self.arm.emergency_stop()

    def disconnect(self):
        if self.arm:
            self.arm.disconnect()