"""Streamed extrusion for the dual arm cell.

Replaces the single long ``G1 X.. E..`` with a stream of short segments sent
from the control loop, so flow can change every tick and pause actually stops
the filament.

Tool 0 is the Klipper E axis, tool 1 is the X axis (second extruder), exactly
as ``ExtruderController`` already wires them.

Usage in the print loop::

    stream = ExtruderStream(extruder, lead_s=0.5)
    stream.start()
    while running:
        stream.tick(e0_mm_s, e1_mm_s)   # call every loop pass, any rate
        ...
    stream.stop()

    on pause:   stream.pause()         # drains the queue, then retracts
    on resume:  stream.resume()        # un-retracts, refills the lead
"""
import logging
import time
import threading

import requests

logger = logging.getLogger(__name__)


class ExtruderStream:
    def __init__(self, extruder, lead_s: float = 0.5, min_seg_s: float = 0.08,
                 retract_mm: float = 1.0, retract_mm_s: float = 40.0):
        self.ext = extruder
        self.lead_s = lead_s            # how far ahead of real time the queue runs
        self.min_seg_s = min_seg_s      # smallest segment we bother sending
        self.retract_mm = retract_mm
        self.retract_mm_s = retract_mm_s
        self._lock = threading.Lock()
        self._t0 = None                 # wall clock at start()
        self._sent_s = 0.0              # cumulative seconds of motion sent
        self._sent_e = [0.0, 0.0]       # cumulative mm sent per tool
        self.running = False
        self.paused = False

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self.ext.set_relative_extrusion()
        self.ext.send_gcode("G91")
        self._t0 = time.monotonic()
        self._sent_s = 0.0
        self._sent_e = [0.0, 0.0]
        self.running = True
        self.paused = False
        logger.info("ExtruderStream started")

    def stop(self):
        """Stop at the next segment boundary and retract."""
        with self._lock:
            self.running = False
        self._drain()
        self._retract()
        self.ext.send_gcode("G90")
        logger.info("ExtruderStream stopped: sent %.1f mm / %.1f mm", *self._sent_e)

    def pause(self):
        with self._lock:
            self.paused = True
        self._drain()
        self._retract()
        logger.info("ExtruderStream paused")

    def resume(self):
        self._unretract()
        with self._lock:
            # re-anchor time so the lead refills from now
            self._t0 = time.monotonic() - self._sent_s
            self.paused = False
        logger.info("ExtruderStream resumed")

    # -- per-tick call -----------------------------------------------------
    def tick(self, e0_mm_s: float, e1_mm_s: float = 0.0):
        """Send enough motion to keep the queue ``lead_s`` ahead of real time.

        Call this every pass of the control loop with the flow rates wanted
        from now on. Rates may change every call.
        """
        with self._lock:
            if not self.running or self.paused:
                return
            now = time.monotonic() - self._t0
            seg_s = (now + self.lead_s) - self._sent_s
            if seg_s < self.min_seg_s:
                return
            e0 = e0_mm_s * seg_s
            e1 = e1_mm_s * seg_s
            if e0 == 0.0 and e1 == 0.0:
                self._sent_s += seg_s          # idle time still counts
                return
            # combined feed so each axis finishes the segment in seg_s
            dist = (e0 * e0 + e1 * e1) ** 0.5
            feed_mm_min = (dist / seg_s) * 60.0
            script = f"G1 X{e1:.4f} E{e0:.4f} F{feed_mm_min:.1f}"
            self._send_async(script)
            self._sent_s += seg_s
            self._sent_e[0] += e0
            self._sent_e[1] += e1

    # -- helpers -----------------------------------------------------------
    def _send_async(self, script: str):
        try:
            self.ext.send_gcode(script, async_mode=True, timeout=2)
        except requests.exceptions.ReadTimeout:
            pass

    def _drain(self):
        """Wait for Klipper to finish what is already queued (at most lead_s)."""
        try:
            self.ext.send_gcode("M400", timeout=max(2.0, self.lead_s * 4))
        except requests.exceptions.ReadTimeout:
            logger.warning("M400 timed out while draining")

    def _retract(self):
        f = self.retract_mm_s * 60.0
        self.ext.send_gcode(f"G1 X-{self.retract_mm:.3f} E-{self.retract_mm:.3f} F{f:.1f}")

    def _unretract(self):
        f = self.retract_mm_s * 60.0
        self.ext.send_gcode(f"G1 X{self.retract_mm:.3f} E{self.retract_mm:.3f} F{f:.1f}")

    # -- diagnostics -------------------------------------------------------
    def status(self) -> dict:
        now = (time.monotonic() - self._t0) if self._t0 else 0.0
        return {"running": self.running, "paused": self.paused,
                "sent_e0_mm": round(self._sent_e[0], 2),
                "sent_e1_mm": round(self._sent_e[1], 2),
                "lead_s": round(self._sent_s - now, 3)}
