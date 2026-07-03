"""Convert a list of Segment objects into robot animations."""
from src.command_parser import Segment
import numpy as np

def run_sequence(scene, segments):
    """
    For each segment, calculate a simple joint‑space trajectory and
    step the simulator.
    """
    for seg in segments:
        # Placeholder: convert Cartesian pose to joint angles
        # In practice you'd call scene.left_arm.ikine_LM(T) etc.
        if seg.left_pose:
            q_left = _cartesian_to_joint(seg.left_pose)   # your IK logic
            scene.move_to("left", q_left)
        if seg.right_pose:
            q_right = _cartesian_to_joint(seg.right_pose)
            scene.move_to("right", q_right)
        # Simulate extrusion: you could colour the end‑effector or draw a line

def _cartesian_to_joint(pose):
    # Use roboticstoolbox's inverse kinematics (IK)
    # For now, return zero angles
    return [0] * 6