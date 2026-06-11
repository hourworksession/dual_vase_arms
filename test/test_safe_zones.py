"""
Unit tests for the 45-deg-split safe zones (no hardware / PyBullet).

    python test\\test_safe_zones.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.calibration import Calibration  # noqa: E402
from src.safe_zones import SafeZones  # noqa: E402


class TestSafeZones(unittest.TestCase):
    def setUp(self):
        self.sz = SafeZones()  # defaults: right -45, left 135

    def test_divider_is_45(self):
        self.assertAlmostEqual(self.sz.divider_deg(), 45.0, 6)

    def test_classification_along_axes(self):
        # boundary line is y = x. Right zone where x > y.
        self.assertEqual(self.sz.zone_of(100, 0), "right")   # +X -> right
        self.assertEqual(self.sz.zone_of(0, 100), "left")    # +Y -> left
        self.assertEqual(self.sz.zone_of(0, -100), "right")  # -Y -> right
        self.assertEqual(self.sz.zone_of(-100, 0), "left")   # -X -> left

    def test_boundary_point(self):
        self.assertEqual(self.sz.zone_of(50, 50), "boundary")  # on y = x

    def test_arm_bases_in_their_zones(self):
        # Real base positions: right ~ (+575, +3.5) world, left ~ (-575, +11)
        self.assertEqual(self.sz.zone_of(575.6, 3.5), "right")
        self.assertEqual(self.sz.zone_of(-575.1, 11.1), "left")

    def test_contains_matches_zone_of(self):
        for x, y in [(120, -30), (-40, 200), (300, 50), (-10, -400)]:
            z = self.sz.zone_of(x, y)
            if z != "boundary":
                self.assertTrue(self.sz.contains(z, x, y))
                other = "left" if z == "right" else "right"
                self.assertFalse(self.sz.contains(other, x, y))

    def test_angular_bounds_span_180(self):
        for arm in ("left", "right"):
            a0, a1 = self.sz.angular_bounds(arm)
            self.assertAlmostEqual(a1 - a0, 180.0, 6)

    def test_mirrored_assignment(self):
        flipped = SafeZones.from_dict({"right_zone_center_deg": 135.0})
        self.assertEqual(flipped.zone_of(100, 0), "left")    # +X now left

    def test_loaded_from_calibration_yaml(self):
        cal = Calibration.load()
        self.assertIsNotNone(cal.safe_zones)
        # shipped default: +X is the right arm's side
        self.assertEqual(cal.safe_zones.zone_of(200, 0), "right")


if __name__ == "__main__":
    unittest.main(verbosity=2)
