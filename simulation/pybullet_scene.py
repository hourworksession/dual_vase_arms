import pybullet as p
import pybullet_data
import time
import os
import numpy as np

class PyBulletScene:
    def __init__(self, urdf_path="xarm8.urdf"):
        p.connect(p.GUI)
        p.setAdditionalSearchPath(pybullet_data.getDataPath())
        p.setGravity(0, 0, -9.81)
        p.loadURDF("plane.urdf")

        # Make sure the mesh directory is in the search path
        mesh_dir = os.path.join(os.path.dirname(__file__), "..", "meshes", "xarm8", "visual")
        p.setAdditionalSearchPath(mesh_dir)

        # Load two xArm8 robots – fixed base, placed realistically
        self.left_arm = p.loadURDF(urdf_path,
                                   basePosition=[0.3, -0.2, 0.0],
                                   baseOrientation=p.getQuaternionFromEuler([0, 0, 0]),
                                   useFixedBase=True,
                                   globalScaling=1.0)   # URDF is in metres
        self.right_arm = p.loadURDF(urdf_path,
                                    basePosition=[0.3, 0.7, 0.0],
                                    baseOrientation=p.getQuaternionFromEuler([0, 0, 0]),
                                    useFixedBase=True)

        # Turntable between them
        self.turntable = p.createMultiBody(
            baseMass=0,
            baseCollisionShapeIndex=p.createCollisionShape(p.GEOM_CYLINDER, radius=0.25, height=0.03),
            baseVisualShapeIndex=p.createVisualShape(p.GEOM_CYLINDER, radius=0.25, length=0.03, rgbaColor=[0.7,0.7,0.7,1]),
            basePosition=[0.6, 0.25, 0.015]
        )

        # Home positions
        self.set_joint_angles(self.left_arm, [0]*6)
        self.set_joint_angles(self.right_arm, [0]*6)
        self.set_turntable_angle(0)

        p.setRealTimeSimulation(1)

    def set_joint_angles(self, arm_id, q):
        num_joints = p.getNumJoints(arm_id)
        for i, angle in enumerate(q[:num_joints]):
            p.resetJointState(arm_id, i, angle)
        p.stepSimulation()

    def set_turntable_angle(self, angle_rad):
        p.resetBasePositionAndOrientation(
            self.turntable,
            [0.6, 0.25, 0.015],
            p.getQuaternionFromEuler([0, 0, angle_rad])
        )
        p.stepSimulation()

    def close(self):
        p.disconnect()