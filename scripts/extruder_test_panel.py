"""Extruder on/off test panel for "Klipper Configurations/non_extruder_control_test.cfg".

A small stand-alone window, separate from cell studio, for bench testing the
two independent extruders through the EXTRUDE_* macros:

    python3 scripts/extruder_test_panel.py                 # Moonraker from config/settings.yaml
    python3 scripts/extruder_test_panel.py --host 10.0.0.5 # override the Pi address
    python3 scripts/extruder_test_panel.py --sim           # no hardware, simulated Klipper

Per tool: heat, ON/OFF at a set rate, live flow scale, fixed-length push.
ALL OFF (button, Space or Esc) jumps the command queue. The watchdog
keepalive can be switched off to prove the firmware stops a tool by itself.
ON and push are blocked below the minimum extrusion temperature, because
manual_stepper does not enforce Klipper's cold-extrusion check.
"""
from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

from PySide6.QtCore import QObject, Qt, QTimer, Signal                       # noqa: E402
from PySide6.QtGui import QFont, QKeySequence, QShortcut                      # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QDoubleSpinBox, QFrame,  # noqa: E402
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QPlainTextEdit, QPushButton, QSlider,
                               QVBoxLayout, QWidget)

from extruder_binary import ExtruderBinary                                    # noqa: E402

TOOLS = [(0, "Right (T0)", "right_hotend"), (1, "Left (T1)", "left_hotend")]
NEEDED = ["heater_generic right_hotend", "heater_generic left_hotend",
          "manual_stepper right", "manual_stepper left", "gcode_macro _EXT"]


# ----------------------------------------------------------------------
# Klipper access: real (Moonraker HTTP) or simulated
class MoonrakerLink:
    def __init__(self, host: str, port: int):
        import requests
        from extruder_controller_manual import ManualExtruderController
        self.ext = ManualExtruderController(host, port)
        self.base = f"http://{host}:{port}"
        self.http = requests.Session()

    def send_gcode(self, script, timeout=5):
        return self.ext.send_gcode(script, timeout=timeout)

    def set_temperature(self, tool, temp, wait=False):
        self.ext.set_temperature(tool, temp, wait=wait)

    def objects(self) -> list[str]:
        r = self.http.get(f"{self.base}/printer/objects/list", timeout=3)
        r.raise_for_status()
        return r.json()["result"]["objects"]

    def poll(self) -> dict:
        q = "&".join(o.replace(" ", "%20") for o in
                     ["webhooks", "heater_generic right_hotend", "heater_generic left_hotend", "gcode_macro _EXT"])
        r = self.http.get(f"{self.base}/printer/objects/query?{q}", timeout=3)
        r.raise_for_status()
        return r.json()["result"]["status"]


class SimLink:
    """Simulated Klipper: heaters drift to target, macros update _EXT state."""

    def __init__(self):
        self.temp, self.target = [22.0, 22.0], [0.0, 0.0]
        self.on, self.rate, self.scale = [0, 0], [0.0, 0.0], [100.0, 100.0]
        self.deadline, self.lock, self.last = [0.0, 0.0], threading.Lock(), time.monotonic()

    def _step(self):
        now = time.monotonic()
        dt, self.last = now - self.last, now
        for t in (0, 1):
            self.temp[t] += (max(self.target[t], 22.0) - self.temp[t]) * min(1.0, dt * 0.6)
            if self.on[t] and now > self.deadline[t]:
                self.on[t] = 0

    def send_gcode(self, script, timeout=5):
        time.sleep(0.03)
        with self.lock:
            p = dict(x.split("=", 1) for x in script.split()[1:] if "=" in x)
            t = int(p.get("T", -1))
            cmd = script.split()[0]
            if cmd == "EXTRUDE_ON":
                self.on[t], self.rate[t] = 1, float(p["RATE"])
                self.deadline[t] = time.monotonic() + float(p.get("TIMEOUT", 5))
            elif cmd == "EXTRUDE_OFF":
                for k in ((0, 1) if t < 0 else (t,)):
                    self.on[k] = 0
            elif cmd == "EXTRUDE_SCALE":
                self.scale[t] = float(p["PERCENT"])
            elif cmd == "EXTRUDE_TEMP":
                self.target[t] = float(p["TARGET"])
        return {"result": "ok"}

    def set_temperature(self, tool, temp, wait=False):
        self.send_gcode(f"EXTRUDE_TEMP T={tool} TARGET={temp}")

    def objects(self):
        return list(NEEDED) + ["webhooks"]

    def poll(self):
        with self.lock:
            self._step()
            return {"webhooks": {"state": "ready", "state_message": "simulated"},
                    "heater_generic right_hotend": {"temperature": self.temp[0], "target": self.target[0]},
                    "heater_generic left_hotend": {"temperature": self.temp[1], "target": self.target[1]},
                    "gcode_macro _EXT": {"on": list(self.on), "rate": list(self.rate), "scale": list(self.scale)}}


# ----------------------------------------------------------------------
class Worker(QObject):
    """Runs Klipper commands off the UI thread, one at a time, in order."""
    log = Signal(str)
    polled = Signal(dict)

    def __init__(self, link):
        super().__init__()
        self.link = link
        self.eb = ExtruderBinary(link, timeout_s=5.0)
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, label, fn):
        self.q.put((label, fn))

    def _call(self, label, fn):
        t0 = time.monotonic()
        try:
            fn()
            self.log.emit(f"{label}  ok  {1000 * (time.monotonic() - t0):.0f} ms")
        except Exception as e:  # report and carry on; the panel must stay usable
            self.log.emit(f"{label}  FAILED: {str(e)[:200]}")

    def _run(self):
        while True:
            label, fn = self.q.get()
            self._call(label, fn)

    def all_off_now(self):
        """Skip the queue: drop pending commands and stop both tools immediately."""
        dropped = 0
        while True:
            try:
                self.q.get_nowait()
                dropped += 1
            except queue.Empty:
                break
        if dropped:
            self.log.emit(f"ALL OFF: dropped {dropped} queued command(s)")
        threading.Thread(target=self._call, args=("ALL OFF  EXTRUDE_OFF", lambda: self.eb.off()),
                         daemon=True).start()

    def poll_async(self):
        def go():
            try:
                self.polled.emit(self.link.poll())
            except Exception as e:
                self.polled.emit({"error": str(e)[:200]})
        threading.Thread(target=go, daemon=True).start()


# ----------------------------------------------------------------------
class ToolCard(QGroupBox):
    def __init__(self, tool: int, title: str, panel: "Panel"):
        super().__init__(title)
        self.tool, self.panel = tool, panel
        g = QGridLayout(self)
        self.temp = QLabel("-- / -- °C")
        self.temp.setFont(QFont("", 20, QFont.Bold))
        g.addWidget(self.temp, 0, 0, 1, 4)

        self.target = self._spin(0, 250, 225, 1, " °C")
        heat, cool = QPushButton("Heat"), QPushButton("Heater off")
        heat.clicked.connect(lambda: panel.cmd(f"T{tool} heat {self.target.value():.0f} C",
                                               lambda: panel.w.eb.temp(tool, self.target.value())))
        cool.clicked.connect(lambda: panel.cmd(f"T{tool} heater off", lambda: panel.w.eb.temp(tool, 0)))
        g.addWidget(QLabel("Target"), 1, 0)
        g.addWidget(self.target, 1, 1)
        g.addWidget(heat, 1, 2)
        g.addWidget(cool, 1, 3)

        self.rate = self._spin(0.1, 10, 2.0, 0.1, " mm/s")
        g.addWidget(QLabel("Rate"), 2, 0)
        g.addWidget(self.rate, 2, 1)
        self.on_btn, self.off_btn = QPushButton("ON"), QPushButton("OFF")
        for b, colour in ((self.on_btn, "#2e7d32"), (self.off_btn, "#555")):
            b.setMinimumHeight(48)
            b.setStyleSheet(f"QPushButton{{background:{colour};color:white;font-size:18px;font-weight:bold}}"
                            "QPushButton:disabled{background:#999}")
        self.on_btn.clicked.connect(self.turn_on)
        self.off_btn.clicked.connect(lambda: panel.cmd(f"T{tool} OFF", lambda: panel.w.eb.off(tool)))
        g.addWidget(self.on_btn, 3, 0, 1, 2)
        g.addWidget(self.off_btn, 3, 2, 1, 2)

        self.scale = QSlider(Qt.Horizontal)
        self.scale.setRange(0, 300)
        self.scale.setValue(100)
        self.scale_lbl = QLabel("Flow 100 %")
        self.scale.valueChanged.connect(lambda v: self.scale_lbl.setText(f"Flow {v} %"))
        self.scale.sliderReleased.connect(
            lambda: panel.cmd(f"T{tool} flow {self.scale.value()} %",
                              lambda v=self.scale.value(): panel.w.eb.scale(tool, v)))
        g.addWidget(self.scale_lbl, 4, 0)
        g.addWidget(self.scale, 4, 1, 1, 3)

        self.dose = self._spin(-10, 200, 20, 1, " mm")
        push = QPushButton("Push length")
        push.clicked.connect(self.push)
        g.addWidget(QLabel("Push"), 5, 0)
        g.addWidget(self.dose, 5, 1)
        g.addWidget(push, 5, 2, 1, 2)
        self.push_btn = push

        self.state = QLabel("state: unknown")
        g.addWidget(self.state, 6, 0, 1, 4)
        self.hot = False

    @staticmethod
    def _spin(lo, hi, val, step, suffix):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        s.setSingleStep(step)
        s.setSuffix(suffix)
        return s

    def turn_on(self):
        if not self.hot:
            self.panel.log("T%d ON refused: below minimum extrusion temperature" % self.tool)
            return
        r = self.rate.value()
        self.panel.cmd(f"T{self.tool} ON {r:.1f} mm/s", lambda: self.panel.w.eb.on(self.tool, r))

    def push(self):
        if not self.hot:
            self.panel.log("T%d push refused: below minimum extrusion temperature" % self.tool)
            return
        mm, r = self.dose.value(), self.rate.value()
        self.panel.cmd(f"T{self.tool} push {mm:.0f} mm at {r:.1f} mm/s", lambda: self.panel.w.eb.dose(self.tool, mm, r))

    def refresh(self, heater: dict, ext: dict, min_temp: float):
        t, tgt = heater.get("temperature", 0.0), heater.get("target", 0.0)
        self.temp.setText(f"{t:.1f} / {tgt:.0f} °C")
        self.hot = t >= min_temp
        self.on_btn.setEnabled(self.hot)
        self.push_btn.setEnabled(self.hot)
        if ext:
            on = ext.get("on", [0, 0])[self.tool]
            self.state.setText(f"state: {'EXTRUDING' if on else 'off'}   rate {ext.get('rate', [0, 0])[self.tool]:.2f} mm/s"
                               f"   flow {ext.get('scale', [100, 100])[self.tool]:.0f} %")
            self.state.setStyleSheet("color:#2e7d32;font-weight:bold" if on else "")


class Panel(QMainWindow):
    def __init__(self, link, address: str):
        super().__init__()
        self.setWindowTitle(f"Extruder test panel ({address})")
        self.link, self.w = link, Worker(link)
        self.w.log.connect(self.log)
        self.w.polled.connect(self.on_poll)
        root = QWidget()
        v = QVBoxLayout(root)

        top = QHBoxLayout()
        self.status = QLabel("Connecting...")
        self.status.setFont(QFont("", 11, QFont.Bold))
        top.addWidget(self.status, 1)
        top.addWidget(QLabel("Min extrusion temp"))
        self.min_temp = ToolCard._spin(0, 260, 190, 5, " °C")
        top.addWidget(self.min_temp)
        v.addLayout(top)

        cards = QHBoxLayout()
        self.cards = [ToolCard(t, name, self) for t, name, _ in TOOLS]
        for c in self.cards:
            cards.addWidget(c)
        v.addLayout(cards)

        row = QHBoxLayout()
        both = QPushButton("BOTH ON")
        both.clicked.connect(lambda: [c.turn_on() for c in self.cards])
        alloff = QPushButton("ALL OFF  (Space / Esc)")
        alloff.setMinimumHeight(56)
        alloff.setStyleSheet("QPushButton{background:#c62828;color:white;font-size:20px;font-weight:bold}")
        alloff.clicked.connect(self.all_off)
        self.keepalive = QCheckBox("Keepalive (untick to test the 5 s watchdog)")
        self.keepalive.setChecked(True)
        row.addWidget(both)
        row.addWidget(alloff, 2)
        row.addWidget(self.keepalive)
        v.addLayout(row)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        v.addWidget(line)
        g = QHBoxLayout()
        self.raw = QLineEdit()
        self.raw.setPlaceholderText("Any G-code, e.g. EXTRUDE_STATUS")
        send = QPushButton("Send")
        send.clicked.connect(self.send_raw)
        self.raw.returnPressed.connect(self.send_raw)
        g.addWidget(self.raw, 1)
        g.addWidget(send)
        v.addLayout(g)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(2000)
        v.addWidget(self.logbox, 1)
        self.setCentralWidget(root)
        self.resize(900, 640)

        for key in ("Space", "Esc"):
            QShortcut(QKeySequence(key), self, activated=self.all_off)

        self.poll_t = QTimer(self, interval=500, timeout=self.w.poll_async)
        self.ka_t = QTimer(self, interval=500, timeout=self.tick_keepalive)
        self.poll_t.start()
        self.ka_t.start()
        self.check_config()

    # --------------------------------------------------------------
    def log(self, text: str):
        self.logbox.appendPlainText(time.strftime("%H:%M:%S  ") + text)

    def cmd(self, label, fn):
        self.w.submit(label, fn)

    def all_off(self):
        self.w.all_off_now()

    def send_raw(self):
        line = self.raw.text().strip()
        if line:
            self.cmd(line, lambda: self.link.send_gcode(line, timeout=10))
            self.raw.clear()

    def tick_keepalive(self):
        eb = self.w.eb
        due = time.monotonic() - eb._last_keepalive >= eb.timeout_s / 2
        if self.keepalive.isChecked() and any(eb.on_flags) and due:
            self.cmd("keepalive", eb.keepalive)

    def check_config(self):
        try:
            objs = set(self.link.objects())
        except Exception as e:
            self.log(f"Cannot reach Moonraker: {str(e)[:150]}")
            return
        missing = [o for o in NEEDED if o not in objs]
        if missing:
            self.log("WRONG CONFIG LOADED: missing " + ", ".join(missing) +
                     ". Load Klipper Configurations/non_extruder_control_test.cfg as printer.cfg.")
        else:
            self.log("Test config detected: two manual_steppers, two heater_generics, EXTRUDE macros.")

    def on_poll(self, st: dict):
        if "error" in st:
            self.status.setText("Not connected: " + st["error"][:80])
            self.status.setStyleSheet("color:#c62828")
            return
        wh = st.get("webhooks", {})
        state = wh.get("state", "?")
        self.status.setText(f"Klipper: {state}  {wh.get('state_message', '')[:60]}")
        self.status.setStyleSheet("color:#2e7d32" if state == "ready" else "color:#c62828")
        ext = st.get("gcode_macro _EXT", {})
        settled = time.monotonic() - self.w.eb._last_keepalive > 1.5   # ignore polls racing a fresh ON
        if ext and settled:   # keep the helper's flags honest (the watchdog may have stopped a tool)
            for t in (0, 1):
                if not ext.get("on", [0, 0])[t]:
                    self.w.eb.on_flags[t] = False
        for (t, _, h), card in zip(TOOLS, self.cards):
            card.refresh(st.get(f"heater_generic {h}", {}), ext, self.min_temp.value())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--sim", action="store_true", help="no hardware: simulated Klipper")
    a = ap.parse_args(argv)
    app = QApplication(sys.argv[:1])
    if a.sim:
        link, addr = SimLink(), "simulated"
    else:
        from config_loader import load_config
        mr = (load_config() or {}).get("moonraker", {})
        host, port = a.host or mr.get("host", "127.0.0.1"), a.port or int(mr.get("port", 7125))
        link, addr = MoonrakerLink(host, port), f"{host}:{port}"
    win = Panel(link, addr)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
