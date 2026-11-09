"""Put the repo's import roots on sys.path once for every test: the repo root (config/),
src/ (the cell package), ui/ (cell_studio, panels) and scripts/ (slicer, planner, rules)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "ui"), os.path.join(ROOT, "scripts"), os.path.join(ROOT, "ui", "panels")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
