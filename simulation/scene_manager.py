"""Swift 3D scene with two xArm8 robots and a turntable."""
import swift
import numpy as np
from roboticstoolbox import RevoluteDH, DHRobot
from spatialmath import SE3
from simulation.robot_models import XArm8

class Turntable(DHRobot):
    """A simple rotating table modelled as a single revolute joint."""
    def __init__(self, radius=0.2, height=0.05):
        # One revolute joint at origin, vertical axis (Z rotation)
        L = [RevoluteDH(d=0, a=0, alpha=0, qlim=[-np.pi, np.pi])]
        super().__init__(L, name="turntable")
        self.radius = radius
        self.height = height
        self.qz = [0]  # home angle

    def add_to_swift(self, env):
        """Custom visual: a cylinder attached to the turntable."""
        # Place a cylinder shape at the joint location
        # Swift doesn't automatically draw links for simple DH models,
        # so we add a primitive.
        import spatialgeometry as sg
        # Base of turntable (static)
        base = sg.Cylinder(radius=self.radius, length=self.height, pose=SE3.Trans(0,0,-self.height/2))
        env.add(base)
        # Top (moving part) – we'll update its pose when joint moves
        self.top = sg.Cylinder(radius=self.radius, length=0.02, color=[0.8,0.8,0.8],
                               pose=SE3.Trans(0,0,0))
        env.add(self.top)

    def update_pose(self, env):
        """Update the top cylinder's pose based on current joint angle."""
        # The turntable rotates around Z
        angle = self.q[0]
        Rz = np.array([[np.cos(angle), -np.sin(angle), 0],
                       [np.sin(angle),  np.cos(angle), 0],
                       [0, 0, 1]])
        self.top.T[:3,:3] = Rz
        self.top.T[:3, 3] = [0, 0, 0.01]  # slightly above base

class DualArmScene:
    def __init__(self):
        self.env = swift.Swift()
        self.env.launch(realtime=True)

        # Left arm at origin
        self.left_arm = XArm8("left")
        self.left_arm.q = self.left_arm.qz
        self.env.add(self.left_arm)

        # Right arm shifted 0.4 m in +Y direction
        self.right_arm = XArm8("right")
        self.right_arm.base = SE3.Trans(0, 0.4, 0)
        self.right_arm.q = self.right_arm.qz
        self.env.add(self.right_arm)

        # Turntable between them, at (0.2, 0.0, 0)
        self.turntable = Turntable(radius=0.15, height=0.02)
        self.turntable.base = SE3.Trans(0.2, 0.2, 0)  # center between arms
        self.turntable.q = self.turntable.qz
        self.turntable.add_to_swift(self.env)

    def set_left_arm_q(self, q):
        self.left_arm.q = q
        self.env.step()

    def set_right_arm_q(self, q):
        self.right_arm.q = q
        self.env.step()

    def set_turntable_angle(self, angle_rad):
        self.turntable.q = [angle_rad]
        self.turntable.update_pose(self.env)
        self.env.step()