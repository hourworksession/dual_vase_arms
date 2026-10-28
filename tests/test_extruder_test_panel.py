"""Headless check of scripts/extruder_test_panel.py against its simulated Klipper."""
import os
import sys
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from PySide6.QtWidgets import QApplication  # noqa: E402

import extruder_test_panel as etp  # noqa: E402


def pump(app, secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def test_panel_on_off_interlock_and_watchdog():
    app = QApplication.instance() or QApplication([])
    link = etp.SimLink()
    win = etp.Panel(link, "sim")
    right = win.cards[0]
    pump(app, 0.8)
    right.turn_on()                                   # cold: refused
    pump(app, 0.3)
    assert link.on[0] == 0 and "refused" in win.logbox.toPlainText()
    link.temp = [230.0, 230.0]
    link.target = [225.0, 225.0]
    pump(app, 0.8)                                    # poll sees it hot
    right.turn_on()
    pump(app, 0.5)
    assert link.on[0] == 1
    win.all_off()
    pump(app, 0.5)
    assert link.on == [0, 0]
    win.keepalive.setChecked(False)                   # watchdog: stops by itself after 5 s
    right.turn_on()
    pump(app, 0.5)
    assert link.on[0] == 1
    pump(app, 5.5)
    assert link.on[0] == 0
    win.keepalive.setChecked(True)                    # keepalive: still on after 7 s
    right.turn_on()
    pump(app, 7.0)
    assert link.on[0] == 1
    win.close()
