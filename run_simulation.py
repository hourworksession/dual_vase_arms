#!/usr/bin/env python3
"""Launch the dual arm + turntable simulation using PyBullet."""
import time
import numpy as np
from simulation.pybullet_scene import PyBulletScene

scene = PyBulletScene()

print("Simulation running. Close the PyBullet window to exit.")
time.sleep(1)

# Move left arm to a ready pose (approximate joint angles)
scene.set_joint_angles(scene.left_arm, [0, -0.5, 0, -1.5, 0, 1.0])
time.sleep(1)

# Rotate turntable 45 degrees
scene.set_turntable_angle(np.deg2rad(45))
time.sleep(1)

# Move right arm
scene.set_joint_angles(scene.right_arm, [0, -0.5, 0, -1.5, 0, 1.0])
time.sleep(2)

print("Demo complete. You can now control the arms via Python.")
input("Press Enter to exit...")
scene.close()