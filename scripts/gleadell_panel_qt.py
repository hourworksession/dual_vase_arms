#!/usr/bin/env python3
"""Cell Studio: the rebuilt control panel for the dual arm cell (Qt).

    pip install PySide6 pyyaml
    python scripts/gleadell_panel_qt.py

A start-up window opens straight away and shows each part as it loads; missing
optional parts are listed there, and an error that stops the panel opening is
shown there instead of the program just closing.

The old Tk panel (gleadell_panel.py) is untouched and still works.
"""
import logging
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                       # slicer.py, planner.py
sys.path.insert(0, os.path.dirname(HERE))      # src/, config_loader.py

from PySide6.QtWidgets import QApplication     # only Qt first, so the window can open at once

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Cell Studio")
    app.setStyle("Fusion")
    from cell_studio import theme
    app.setStyleSheet(theme.QSS)
    from cell_studio.splash import Splash
    splash = Splash()
    splash.show()
    app.processEvents()

    n_build = 7                                # panel modules + MainWindow's progress steps
    if not splash.run_steps(n_build):
        sys.exit(app.exec())                   # error is on the start-up window; Close quits
    from cell_studio.splash import STEPS
    done = len(STEPS)
    try:
        splash.set_step("Loading the panel modules…", done)
        from cell_studio.main_window import MainWindow
        counter = [done]

        def progress(text):
            counter[0] += 1
            splash.set_step(text + "…", counter[0])

        win = MainWindow(progress=progress)
    except Exception:
        splash.fail("The panel could not start", traceback.format_exc())
        sys.exit(app.exec())

    splash.set_step(splash.finish_text(), splash.bar.maximum())
    if splash.warnings:
        logging.getLogger("cell_studio").warning(
            "Started with missing parts:\n  " + "\n  ".join(splash.warnings))
    win.show()
    splash.close()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
