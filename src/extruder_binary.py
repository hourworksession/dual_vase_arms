"""Binary extruder control: on at a fixed rate, or off.

Thin wrapper over the EXTRUDE_ON / EXTRUDE_OFF macros in
config/klipper/extrude_binary.cfg. All state lives in Klipper; this class
only sends commands and re-sends the keepalive. Use this as the failsafe
mode when the streamed controller is not trusted.

    ext = ExtruderController(host)
    eb = ExtruderBinary(ext, timeout_s=5.0)
    eb.on(0, 3.0)        # tool 0 at 3 mm/s
    eb.on(1, 2.5)
    ...
    eb.keepalive()       # call at least every timeout_s/2 while printing
    ...
    eb.off(0)
    eb.off()             # both
"""
import logging
import time

logger = logging.getLogger(__name__)


class ExtruderBinary:
    def __init__(self, extruder, timeout_s: float = 5.0):
        self.ext = extruder
        self.timeout_s = timeout_s
        self.rate = [0.0, 0.0]
        self.on_flags = [False, False]
        self._last_keepalive = 0.0

    def on(self, tool: int, rate_mm_s: float):
        if tool not in (0, 1):
            raise ValueError("tool must be 0 or 1")
        if rate_mm_s <= 0:
            raise ValueError("rate must be > 0")
        self.rate[tool] = rate_mm_s
        self.on_flags[tool] = True
        self.ext.send_gcode(f"EXTRUDE_ON T={tool} RATE={rate_mm_s:.3f} TIMEOUT={self.timeout_s:.1f}", timeout=5)
        self._last_keepalive = time.monotonic()
        logger.info("extruder %d ON at %.2f mm/s", tool, rate_mm_s)

    def off(self, tool=None):
        if tool is None:
            self.on_flags = [False, False]
            self.ext.send_gcode("EXTRUDE_OFF", timeout=5)
            logger.info("extruders OFF")
        else:
            self.on_flags[tool] = False
            self.ext.send_gcode(f"EXTRUDE_OFF T={tool}", timeout=5)
            logger.info("extruder %d OFF", tool)

    def keepalive(self):
        """Re-arm the firmware watchdog. Safe to call every loop pass; only
        sends when half the timeout has elapsed and a tool is on."""
        if not any(self.on_flags):
            return
        if time.monotonic() - self._last_keepalive < self.timeout_s / 2:
            return
        for t in (0, 1):
            if self.on_flags[t]:
                self.ext.send_gcode(f"EXTRUDE_ON T={t} RATE={self.rate[t]:.3f} TIMEOUT={self.timeout_s:.1f}", timeout=5)
        self._last_keepalive = time.monotonic()

    def status(self) -> dict:
        return {"on": list(self.on_flags), "rate_mm_s": list(self.rate),
                "keepalive_age_s": round(time.monotonic() - self._last_keepalive, 2)}
