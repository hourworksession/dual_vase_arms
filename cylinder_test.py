"""Shim: the test now lives in src/cell/runs/cylinder.py. Same command line as before."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from cell.runs.cylinder import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
