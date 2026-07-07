"""
Tests for the generated primitive arm URDF (no PyBullet required).

    python test\\test_twin_assets.py
"""

import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from simulation.primitive_arm import EE_LINK_NAME, generate_urdf  # noqa: E402


class TestPrimitiveUrdf(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "arm.urdf")
        generate_urdf(self.path)

    def test_file_written(self):
        self.assertTrue(os.path.exists(self.path))

    def test_valid_xml(self):
        tree = ET.parse(self.path)  # raises on malformed XML
        self.assertEqual(tree.getroot().tag, "robot")

    def test_six_revolute_joints(self):
        root = ET.parse(self.path).getroot()
        revolute = [j for j in root.findall("joint") if j.get("type") == "revolute"]
        self.assertEqual(len(revolute), 6)

    def test_has_tool_tip_link(self):
        root = ET.parse(self.path).getroot()
        names = {lnk.get("name") for lnk in root.findall("link")}
        self.assertIn(EE_LINK_NAME, names)

    def test_joint_chain_is_connected(self):
        # every joint's parent must be a defined link
        root = ET.parse(self.path).getroot()
        links = {lnk.get("name") for lnk in root.findall("link")}
        for j in root.findall("joint"):
            parent = j.find("parent").get("link")
            child = j.find("child").get("link")
            self.assertIn(parent, links)
            self.assertIn(child, links)


if __name__ == "__main__":
    unittest.main(verbosity=2)
