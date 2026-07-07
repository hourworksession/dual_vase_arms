"""Calibrate the two arm bases relative to the disc centre.

Procedure (with the arms in free-drive mode):
  1. Touch off the nozzle tip at three points on the disc rim.
  2. Fit a circle to those points → disc centre and radius in arm frame.
  3. Solve for the arm-base pose that puts the disc centre at world origin.
  4. Write the result back into ``config/system.yaml``.

# STUB — implement once arms are on the bench.
"""
from __future__ import annotations


def main() -> None:
    raise NotImplementedError("Calibration runs on hardware; this is a stub.")


if __name__ == "__main__":  # pragma: no cover
    main()
