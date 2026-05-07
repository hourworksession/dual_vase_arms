import requests
import logging
import json

logger = logging.getLogger(__name__)

class ExtruderController:
    """Talks to Klipper/Moonraker over HTTP. Supports T0 and T1."""
    def __init__(self, host: str, port: int = 7125):
        self.base_url = f"http://{host}:{port}"
        self.session = requests.Session()
        self.relative_mode = False  # track to avoid redundant G91/G90

    def send_gcode(self, script: str, timeout=10):
        """Send raw G-code script and optionally wait for OK."""
        url = f"{self.base_url}/printer/gcode/script"
        payload = {"script": script}
        resp = self.session.post(url, data=payload, timeout=timeout)
        resp.raise_for_status()
        # Moonraker returns {"result": "ok"}
        return resp.json()

    def set_temperature(self, tool: int, temp: float, wait: bool = False):
        """Set extruder temperature, optionally wait."""
        self.send_gcode(f"T{tool}")
        self.send_gcode(f"M109 S{temp}") if wait else self.send_gcode(f"M104 S{temp}")

    def set_relative_extrusion(self):
        """Put extruder into relative mode (M83)."""
        if not self.relative_mode:
            self.send_gcode("M83")
            self.relative_mode = True

    def extrude(self, tool: int, length_mm: float, feedrate_mm_s: float):
        """Extrude `length_mm` mm of filament on tool at given feedrate."""
        self.set_relative_extrusion()
        # Extruder feedrate in mm/min for G-code
        feedrate_mm_min = feedrate_mm_s * 60.0
        script = f"T{tool}\nG1 E{length_mm:.3f} F{feedrate_mm_min:.1f}"
        self.send_gcode(script)

    def retract(self, tool: int, length_mm: float, feedrate_mm_s: float = 20):
        """Retract filament."""
        self.extrude(tool, -length_mm, feedrate_mm_s)

    def disable_all_heaters(self):
        self.send_gcode("M104 S0")
        self.send_gcode("M140 S0")  # bed if exists