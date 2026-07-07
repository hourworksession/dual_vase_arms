"""
Unit tests for the shared state model and frame calibration.

Runnable without hardware:

    python -m unittest test.test_calibration        # from project root
    # or
    python -m pytest test/test_calibration.py
"""

import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.calibration import ArmCalibration, Calibration, _rot2d  # noqa: E402
from src.system_state import StateSnapshot, StateSource, SystemState  # noqa: E402

TOL = 6  # decimal places


class TestRotation(unittest.TestCase):
    def test_identity(self):
        self.assertAlmostEqual(_rot2d(3, 4, 0)[0], 3, TOL)
        self.assertAlmostEqual(_rot2d(3, 4, 0)[1], 4, TOL)

    def test_180(self):
        x, y = _rot2d(3, 4, 180)
        self.assertAlmostEqual(x, -3, TOL)
        self.assertAlmostEqual(y, -4, TOL)

    def test_90(self):
        x, y = _rot2d(1, 0, 90)
        self.assertAlmostEqual(x, 0, TOL)
        self.assertAlmostEqual(y, 1, TOL)


class TestCalibrationYaml(unittest.TestCase):
    """The shipped calibration.yaml must parse and load."""

    def setUp(self):
        self.cal = Calibration.load()

    def test_loads_both_arms(self):
        self.assertIn("left", self.cal.arms)
        self.assertIn("right", self.cal.arms)

    def test_disc_geometry(self):
        self.assertAlmostEqual(self.cal.disc_radius_mm, 300.0, TOL)


class TestArmToWorld(unittest.TestCase):
    """Use the measured anchors that the control panel currently hard-codes."""

    def setUp(self):
        self.left = ArmCalibration(
            base_yaw_deg=0.0,
            turntable_centre_in_base_mm=(575.1, -11.1),
            disc_surface_z_in_base_mm=151.8,
        )
        self.right = ArmCalibration(
            base_yaw_deg=180.0,
            turntable_centre_in_base_mm=(575.6, 3.5),
            disc_surface_z_in_base_mm=152.0,
        )

    def test_disc_centre_maps_to_world_origin(self):
        # A nozzle at the measured turntable centre must land at world (0,0,0).
        for arm, c, z in (
            (self.left, (575.1, -11.1), 151.8),
            (self.right, (575.6, 3.5), 152.0),
        ):
            wx, wy, wz, *_ = arm.arm_to_world((c[0], c[1], z, 180, 45, 0))
            self.assertAlmostEqual(wx, 0.0, TOL)
            self.assertAlmostEqual(wy, 0.0, TOL)
            self.assertAlmostEqual(wz, 0.0, TOL)

    def test_base_world_positions(self):
        lx, ly, lz = self.left.base_world_xyz()
        self.assertAlmostEqual(lx, -575.1, TOL)
        self.assertAlmostEqual(ly, 11.1, TOL)
        self.assertAlmostEqual(lz, -151.8, TOL)

        rx, ry, rz = self.right.base_world_xyz()
        self.assertAlmostEqual(rx, 575.6, TOL)
        self.assertAlmostEqual(ry, 3.5, TOL)
        self.assertAlmostEqual(rz, -152.0, TOL)

    def test_yaw_offset_applied(self):
        # Right arm base is rotated 180 deg: a commanded yaw of 20 -> 200 world.
        *_, wyaw = self.right.arm_to_world((575.6, 3.5, 152.0, 180, 45, 20))
        self.assertAlmostEqual(wyaw, 200.0, TOL)

    def test_round_trip_world_to_arm(self):
        poses = [
            (575.1, -11.1, 151.8, 180, 45, 0),
            (400.0, 200.0, 155.0, 180, 45, 20),
            (612.3, -40.0, 220.5, 175, 50, -33),
        ]
        for arm in (self.left, self.right):
            for p in poses:
                w = arm.arm_to_world(p)
                back = arm.world_to_arm(w)
                for a, b in zip(p, back):
                    self.assertAlmostEqual(a, b, TOL)


class TestCrossCheck(unittest.TestCase):
    def setUp(self):
        self.cal = Calibration.load()

    def test_shared_point_consistency(self):
        # Pick any world point; express it in each arm's frame via the inverse
        # map; the forward map of both must agree -> cross_check ~ 0.
        world_pt = (60.0, -25.0, 10.0, 180, 45, 0)
        left_cmd = self.cal.world_to_arm("left", world_pt)
        right_cmd = self.cal.world_to_arm("right", world_pt)
        self.assertAlmostEqual(self.cal.cross_check(left_cmd, right_cmd), 0.0, TOL)


class TestTurntableMapping(unittest.TestCase):
    def test_direction_and_offset(self):
        cal = Calibration.from_dict(
            {
                "arms": {
                    "left": {
                        "base_yaw_deg": 0.0,
                        "turntable_centre_in_base_mm": [575.1, -11.1],
                        "disc_surface_z_in_base_mm": 151.8,
                    }
                },
                "turntable": {"home_offset_deg": 10.0, "direction": -1},
            }
        )
        self.assertAlmostEqual(cal.turntable_world_deg(90.0), 10.0 - 90.0, TOL)


class TestSystemState(unittest.TestCase):
    def test_update_and_snapshot(self):
        s = SystemState()
        self.assertEqual(s.snapshot().source, StateSource.NONE)

        s.update_left((400, 200, 155, 180, 45, 20), StateSource.COMMANDED)
        snap = s.snapshot()
        self.assertEqual(snap.left_pose, (400, 200, 155, 180, 45, 20))
        self.assertEqual(snap.source, StateSource.COMMANDED)
        self.assertIsNotNone(snap.left_ts)
        self.assertIsNone(snap.right_ts)

    def test_temps_merge(self):
        s = SystemState()
        s.update_temps({"tool0": 200.0})
        s.update_temps({"tool1": 205.0})
        temps = s.snapshot().temps_c
        self.assertEqual(temps, {"tool0": 200.0, "tool1": 205.0})

    def test_snapshot_is_immutable(self):
        s = SystemState()
        s.update_left((1, 2, 3, 4, 5, 6), StateSource.LIVE)
        snap = s.snapshot()
        with self.assertRaises(Exception):
            snap.left_pose = None  # frozen dataclass

    def test_age(self):
        s = SystemState()
        s.update_turntable(45.0, StateSource.LIVE, ts=100.0)
        snap = s.snapshot()
        self.assertAlmostEqual(snap.age("turntable", now=100.5), 0.5, TOL)
        self.assertIsNone(snap.age("left", now=100.5))

    def test_thread_safety_smoke(self):
        s = SystemState()
        errors = []

        def worker(tag):
            try:
                for i in range(2000):
                    s.update_left((i, i, i, 0, 0, 0), StateSource.LIVE)
                    s.update_turntable(i % 360, StateSource.LIVE)
                    _ = s.snapshot().left_pose
            except Exception as e:  # pragma: no cover
                errors.append((tag, e))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        snap = s.snapshot()
        self.assertIsInstance(snap, StateSnapshot)
        self.assertEqual(len(snap.left_pose), 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
