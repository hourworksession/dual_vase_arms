"""Calibration jog: move one arm's nozzle by hand in X, Y and Z to find its turntable
centre and zero height.

    python3 cali_jog.py T=0          # right arm
    python3 cali_jog.py T=1          # left arm
    python3 cali_jog.py T=1 --sim    # practise without hardware

Starts 5 mm above where the arm thinks the turntable centre is (its CX, CY, Z0).
Type moves like  x+1  y-0.5  z-0.1  (or just -0.1 for Z), up to 5 mm a step, at most
30 mm from the start and 25 mm below Z0.
  r  = lift 2 mm, turn the bed 180 deg, come back down
  ok = record this point (bring Z to paper-drag first to record the zero height too)
  q  = lift, park, and print CX, CY, Z0 to use

Finding the axis: put a pen dot on the disc under the nozzle and press r. If the dot is
still under the nozzle you are on the axis; if not, jog HALF way towards where it went
and repeat. Start values can be overridden: CX= CY= Z0=.
The same jog is  python3 cylinder_test.py T=1 --find-centre.
"""
import sys

import cylinder_test

if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if not any(a.upper().startswith("T=") for a in args):
        raise SystemExit(__doc__)
    sys.exit(cylinder_test.main(args + ["--find-centre"]))
