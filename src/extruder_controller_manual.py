"""Extruder controller for the manual_stepper Klipper config
("Klipper Configurations/printer.cfg": kinematics none, two heater_generic
hot ends, EXTRUDE_* macros).

NOT used by the cell studio panel yet: the panel's src/extruder_controller.py
targets the old config ([extruder] + [heater_bed], one G1 X E move for both).
Switch the panel over only after the new config passes its bench test.
"""
import requests
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ManualExtruderController:
    """Controls extruders via Klipper/Moonraker HTTP API."""

    def __init__(self, host: str, port: int = 7125) -> None:
        self.base_url = f"http://{host}:{port}"
        self.session = requests.Session()
        self.relative_mode = False

    # ------------------------------------------------------------------
    # Low‑level G‑code sending
    # ------------------------------------------------------------------

    def send_gcode(self, script: str, timeout: int = 300, async_mode: bool = False) -> dict:
        """Send raw G‑code to Moonraker."""
        url = f"{self.base_url}/printer/gcode/script"
        if async_mode:
            url += "?async=true"
        payload = {"script": script}
        resp = self.session.post(url, data=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Temperature management
    # ------------------------------------------------------------------

    def set_temperature(self, tool: int, temp: float, wait: bool = False) -> None:
        """Set target temperature. tool=0 → right_hotend, tool=1 → left_hotend
        (EXTRUDE_TEMP macro in config/klipper/extrude_control.cfg)."""
        if tool not in (0, 1):
            raise ValueError(f"Invalid tool index: {tool}")
        self.send_gcode(f"EXTRUDE_TEMP T={tool} TARGET={temp} WAIT={1 if wait else 0}",
                        timeout=300 if wait else 10)

    def heat_and_wait(self, tool: int, temp: float, timeout: int = 120,
                      tolerance: float = 5.0) -> None:
        """Set target temperature, then wait until the measured temperature
        is within `tolerance` degrees of the target (default ±5 °C).
        """
        self.set_temperature(tool, temp, wait=False)   # non‑blocking target
        if tool == 0:
            logger.info(f"Heating T0 to {temp}°C (tolerance ±{tolerance}°C)")
            self._wait_for_extruder(temp, timeout, tolerance)
        elif tool == 1:
            logger.info(f"Heating heater_bed to {temp}°C (tolerance ±{tolerance}°C)")
            self._wait_for_heater_bed(temp, timeout, tolerance)
        else:
            raise ValueError(f"Invalid tool index: {tool}")

    def _wait_for_extruder(self, target: float, timeout: float = 120,
                           tolerance: float = 5.0):
        start = time.time()
        while True:
            current = self.get_temperature(0)
            if abs(current - target) <= tolerance:
                break
            if time.time() - start > timeout:
                raise TimeoutError(
                    f"T0 did not reach {target}°C ±{tolerance}°C within {timeout}s")
            time.sleep(2)
        logger.info(f"T0 at {current:.1f}°C (target {target}°C)")

    def _wait_for_heater_bed(self, target: float, timeout: float = 120,
                             tolerance: float = 5.0):
        start = time.time()
        while True:
            current = self.get_temperature(1)
            if abs(current - target) <= tolerance:
                break
            if time.time() - start > timeout:
                raise TimeoutError(
                    f"heater_bed did not reach {target}°C ±{tolerance}°C within {timeout}s")
            time.sleep(2)
        logger.info(f"heater_bed at {current:.1f}°C (target {target}°C)")

    def get_temperature(self, tool: int) -> float:
        """Return current temperature. tool=0 → extruder, tool=1 → heater_bed."""
        try:
            status = self.get_printer_status()
            name = {0: 'heater_generic right_hotend', 1: 'heater_generic left_hotend'}.get(tool)
            return status.get(name, {}).get('temperature', 0.0) if name else 0.0
        except Exception:
            logger.warning(f"Could not read temperature for tool {tool}")
            return 0.0

    # ------------------------------------------------------------------
    # Extrusion – single tool
    # ------------------------------------------------------------------

    def set_relative_extrusion(self) -> None:
        """Switch to relative extrusion mode (M83)."""
        if not self.relative_mode:
            self.send_gcode("M83")
            self.relative_mode = True

    def extrude(self, tool: int, length_mm: float, feedrate_mm_s: float,
                wait: bool = True) -> None:
        """Extrude or retract a length of filament.

        Args:
            tool: 0 for E axis, 1 for X axis.
            length_mm: Positive = forward, negative = retract.
            feedrate_mm_s: Extrusion speed in mm/s.
            wait: If False, fire‑and‑forget (non‑blocking).
        """
        if tool not in (0, 1):
            raise ValueError(f"Invalid tool index: {tool}")
        script = f"EXTRUDE_MM T={tool} MM={length_mm:.3f} RATE={feedrate_mm_s:.3f}"
        self.send_gcode(script, timeout=10)      # queues a G1 on axis A/B: returns at once
        if wait:
            self.send_gcode("M400", timeout=max(10, abs(length_mm) / max(feedrate_mm_s, 0.1) + 5))

    # ------------------------------------------------------------------
    # Synchronous dual extrusion (single G1 command)
    # ------------------------------------------------------------------

    def extrude_sync(self, length_t0: float, speed_t0: float,
                     length_t1: float, speed_t1: float,
                     wait: bool = True) -> None:
        """Extrude both tools simultaneously with one G1 command.

        The steppers are G-code axes A (right) and B (left); X is a dummy axis
        (kinematics: none) that sets the move time: 100 mm of X per second.
        """
        if speed_t0 <= 0 or speed_t1 <= 0:
            raise ValueError("Speeds must be positive.")
        time_t0 = abs(length_t0) / speed_t0
        time_t1 = abs(length_t1) / speed_t1
        duration = max(time_t0, time_t1)
        script = f"G91\nG1 X{100.0 * duration:.4f} A{length_t0:.4f} B{length_t1:.4f} F6000\nG90"

        if wait:
            self.send_gcode(script)
        else:
            try:
                self.send_gcode(script, async_mode=True, timeout=10)
            except requests.exceptions.ReadTimeout:
                pass

    # ------------------------------------------------------------------
    # Preparation helpers
    # ------------------------------------------------------------------

    def load_filament(self, tool: int, length_mm: float = 50.0,
                      feedrate_mm_s: float = 5.0) -> None:
        logger.info("Loading filament on T%d (%d mm)", tool, length_mm)
        self.extrude(tool, length_mm, feedrate_mm_s)

    def purge(self, tool: int, amount: float = 10.0,
              feedrate_mm_s: float = 5.0) -> None:
        logger.info("Purging T%d (%.1f mm)", tool, amount)
        self.extrude(tool, amount, feedrate_mm_s)

    # ------------------------------------------------------------------
    # Custom macros
    # ------------------------------------------------------------------

    def run_macro(self, macro_name: str, **params) -> None:
        param_str = " ".join(f"{k.upper()}={v}" for k, v in params.items())
        script = f"{macro_name} {param_str}"
        logger.info("Running macro: %s", script)
        self.send_gcode(script)

    # ------------------------------------------------------------------
    # Status retrieval
    # ------------------------------------------------------------------

    def get_printer_status(self) -> Dict[str, Any]:
        url = f"{self.base_url}/printer/objects/query"
        resp = self.session.get(url, params={
            "heater_generic right_hotend": "",
            "heater_generic left_hotend": "",
            "manual_stepper right": "",
            "manual_stepper left": "",
            "toolhead": "",
            "gcode_macro _EXT": ""
        })
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", {}).get("status", {})

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def disable_all_heaters(self) -> None:
        self.send_gcode("EXTRUDE_OFF")
        self.send_gcode("EXTRUDE_TEMP T=0 TARGET=0")
        self.send_gcode("EXTRUDE_TEMP T=1 TARGET=0")
        logger.info("All heaters disabled")