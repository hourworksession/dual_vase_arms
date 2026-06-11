"""
Unit tests for the hardware state poller (no hardware / SDKs required).

    python test\\test_state_provider.py
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from simulation.state_provider import HardwarePoller, poller_from_controllers  # noqa: E402
from src.system_state import StateSource, SystemState  # noqa: E402


class FakeArm:
    def __init__(self, pose, joints):
        self._pose = pose
        self._joints = joints

    def get_pose(self):
        return self._pose

    def get_joints(self):
        return self._joints


class FakeTurntable:
    def __init__(self, angle):
        self._angle = angle

    def get_angle(self):
        return self._angle


class FakeExtruder:
    def get_printer_status(self):
        return {
            "extruder": {"temperature": 201.5},
            "heater_bed": {"temperature": 59.0},
        }


class TestPollerGating(unittest.TestCase):
    def test_offline_does_not_write(self):
        state = SystemState()
        poller = HardwarePoller(
            state,
            is_live=lambda: False,
            left_pose=lambda: (1, 2, 3, 4, 5, 6),
        )
        self.assertFalse(poller.poll_once())
        self.assertEqual(state.snapshot().source, StateSource.NONE)
        self.assertIsNone(state.snapshot().left_pose)

    def test_commanded_values_survive_offline(self):
        state = SystemState()
        state.update_left((10, 20, 30, 0, 0, 0), StateSource.COMMANDED)
        poller = HardwarePoller(
            state, is_live=lambda: False, left_pose=lambda: (1, 1, 1, 1, 1, 1)
        )
        poller.poll_once()
        snap = state.snapshot()
        self.assertEqual(snap.left_pose, (10, 20, 30, 0, 0, 0))
        self.assertEqual(snap.source, StateSource.COMMANDED)


class TestPollerLive(unittest.TestCase):
    def setUp(self):
        self.state = SystemState()
        self.left = FakeArm((400, 200, 155, 180, 45, 20), (1, 2, 3, 4, 5, 6))
        self.right = FakeArm((410, -200, 150, 180, 45, 200), (-1, -2, -3, -4, -5, -6))
        self.tt = FakeTurntable(33.0)
        self.ext = FakeExtruder()
        self.poller = poller_from_controllers(
            self.state,
            is_live=lambda: True,
            left=self.left,
            right=self.right,
            turntable=self.tt,
            extruder=self.ext,
        )

    def test_poll_once_populates_everything(self):
        self.assertTrue(self.poller.poll_once())
        snap = self.state.snapshot()
        self.assertEqual(snap.source, StateSource.LIVE)
        self.assertEqual(snap.left_pose, (400, 200, 155, 180, 45, 20))
        self.assertEqual(snap.left_joints, (1, 2, 3, 4, 5, 6))
        self.assertEqual(snap.right_joints, (-1, -2, -3, -4, -5, -6))
        self.assertAlmostEqual(snap.turntable_deg, 33.0)
        self.assertAlmostEqual(snap.temps_c["tool0"], 201.5)
        self.assertAlmostEqual(snap.temps_c["bed"], 59.0)

    def test_thread_runs_and_stops(self):
        self.poller.start()
        self.assertTrue(self.poller.is_running())
        time.sleep(0.1)
        self.poller.stop()
        self.assertFalse(self.poller.is_running())
        self.assertEqual(self.state.snapshot().source, StateSource.LIVE)


class TestPollerRobustness(unittest.TestCase):
    def test_getter_exception_is_swallowed(self):
        state = SystemState()

        def boom():
            raise RuntimeError("comms dropped")

        poller = HardwarePoller(
            state,
            is_live=lambda: True,
            left_pose=boom,
            right_pose=lambda: (1, 2, 3, 4, 5, 6),
        )
        # Must not raise; right arm still recorded despite left failing.
        poller.poll_once()
        snap = state.snapshot()
        self.assertIsNone(snap.left_pose)
        self.assertEqual(snap.right_pose, (1, 2, 3, 4, 5, 6))


if __name__ == "__main__":
    unittest.main(verbosity=2)
