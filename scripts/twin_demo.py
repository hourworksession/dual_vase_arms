#!/usr/bin/env python3
"""
Stand-alone twin demo -- watch the digital twin move with NO hardware connected.

A background thread fills the shared SystemState with synthetic *commanded*
poses (both nozzles tracing a circle on the disc while the turntable spins); the
PyBullet viewer renders them on the MAIN thread (where its GUI must live). This
validates the whole sim pipeline (state -> calibration -> viewer).

    python scripts\\twin_demo.py

Requires PyBullet:  pip install pybullet
"""

import math
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.calibration import Calibration
from src.system_state import StateSource, SystemState
from simulation.twin_viewer import TwinViewer


def feeder(state: SystemState, cal: Calibration, stop: threading.Event):
    """Real demo behaviour: each arm moves to ONE fixed point over the disc,
    then the turntable rotates -- the spinning disc under the stationary nozzle
    is what traces the printed circle."""
    radius = 150.0          # mm from disc centre
    sz = cal.safe_zones
    fixed = {}
    for arm in ("left", "right"):
        wa = math.radians(sz.center_deg(arm))
        wp = (radius * math.cos(wa), radius * math.sin(wa), 1.0, 180, 45, 0)
        cmd = list(cal.world_to_arm(arm, wp))
        cmd[2] = cal.arms[arm].disc_surface_z_in_base_mm + 1.0
        fixed[arm] = tuple(cmd)
    t0 = time.time()
    while not stop.is_set():
        t = time.time() - t0
        state.update_left(fixed["left"], StateSource.COMMANDED)
        state.update_right(fixed["right"], StateSource.COMMANDED)
        state.update_turntable((t * 20.0) % 360.0, StateSource.COMMANDED)
        time.sleep(1 / 60)


def main():
    cal = Calibration.load()
    state = SystemState()
    stop = threading.Event()
    threading.Thread(target=feeder, args=(state, cal, stop), daemon=True).start()

    print("Twin demo running. Close the PyBullet window to stop.")
    viewer = TwinViewer.from_state(state, cal, gui=True, fps=30, mirror_arms=True)
    try:
        viewer.run_blocking()          # blocks on the main thread until window closes
    finally:
        stop.set()
        print("Twin demo stopped.")


if __name__ == "__main__":
    main()
