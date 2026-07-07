#!/usr/bin/env python3
"""
Twin subprocess entry point.

Runs the PyBullet viewer on THIS process's main thread (the only place its GUI
is happy), pulling state from a shared-memory block written by the control
panel. Launched by simulation.twin_launcher; not usually run by hand, but you
can:

    python -m simulation.twin_run --shm dualarm_twin --fps 30
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shm", default="dualarm_twin")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--arm-urdf", default=None)
    ap.add_argument("--drive-mode", default="auto")
    args = ap.parse_args()

    from multiprocessing import shared_memory

    from simulation.twin_shm import read_snapshot
    from simulation.twin_viewer import TwinViewer

    shm = shared_memory.SharedMemory(name=args.shm)
    try:
        viewer = TwinViewer(
            lambda: read_snapshot(shm.buf),
            arm_urdf=args.arm_urdf,
            drive_mode=args.drive_mode,
            fps=args.fps,
            gui=True,
        )
        viewer.run_blocking()
    finally:
        shm.close()


if __name__ == "__main__":
    main()
