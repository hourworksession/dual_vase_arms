#!/usr/bin/env python3
"""Cell Studio: the rebuilt control panel for the dual arm cell (Qt).

    pip install PySide6 pyyaml
    python scripts/gleadell_panel_qt.py

The old Tk panel (gleadell_panel.py) is untouched and still works.
"""
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                       # slicer.py, planner.py
sys.path.insert(0, os.path.dirname(HERE))      # src/, config_loader.py

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

from cell_studio import theme
from cell_studio.main_window import MainWindow

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Cell Studio")
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
