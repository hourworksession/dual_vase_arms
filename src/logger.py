import csv
import time
from datetime import datetime

class DataLogger:
    """Logs time, commanded arm positions, extrusion amounts, and turntable angle."""
    def __init__(self, filepath: str = "dual_print_log.csv"):
        self.filepath = filepath
        self.file = open(filepath, 'w', newline='')
        self.writer = csv.writer(self.file)
        self.writer.writerow(["timestamp", "arm_left_x", "arm_left_y", "arm_left_z",
                              "arm_right_x", "arm_right_y", "arm_right_z",
                              "extrude_left", "extrude_right", "turntable_angle"])

    def log(self, arm_left_pose, arm_right_pose, extrude_l, extrude_r, turntable_angle):
        self.writer.writerow([time.time(),
                              *arm_left_pose[0:3] if arm_left_pose else (-1, -1, -1),
                              *arm_right_pose[0:3] if arm_right_pose else (-1, -1, -1),
                              extrude_l, extrude_r,
                              turntable_angle])

    def close(self):
        self.file.close()