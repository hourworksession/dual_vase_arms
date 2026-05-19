import requests
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ExtruderController:
    """Controls extruders via Klipper/Moonraker HTTP API."""

    def __init__(self, host: str, port: int = 7125) -> None:
        self.base_url = f"http://{host}:{port}"
        self.session = requests.Session()
        self.relative_mode = False

    # ------------------------------------------------------------------
    # Low‑level G‑code sending
    # ------------------------------------------------------------------

    def send_gcode(self, script: str, timeout: int = 300, async_mode: bool = False) -> dict:
        """Send raw G‑code to Moonraker.

        Args:
            script: G‑code lines to execute.
            timeout: HTTP timeout in seconds.
            async_mode: If True, Moonraker returns immediately and the
                        command runs in the background.
        """
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
        """Set target temperature. tool=0 → extruder, tool=1 → heater_bed."""
        if tool == 0:
            if wait:
                self.send_gcode(f"M109 S{temp} T0", timeout=120)
            else:
                self.send_gcode(f"M104 S{temp} T0")
        elif tool == 1:
            self.send_gcode(f"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET={temp}")
            if wait:
                self._wait_for_heater_bed(temp)
        else:
            raise ValueError(f"Invalid tool index: {tool}")

    def heat_and_wait(self, tool: int, temp: float, timeout: int = 120) -> None:
        """Send non‑blocking heat command, then poll until target reached."""
        if tool == 0:
            self.send_gcode(f"M104 S{temp} T0")
            logger.info(f"Heating T0 to {temp}°C, waiting...")
            self._wait_for_extruder(temp, timeout)
        elif tool == 1:
            self.send_gcode(f"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET={temp}")
            logger.info(f"Heating heater_bed (second nozzle) to {temp}°C, waiting...")
            self._wait_for_heater_bed(temp, timeout)
        else:
            raise ValueError(f"Invalid tool index: {tool}")

    def _wait_for_extruder(self, target, timeout=120):
        start = time.time()
        while True:
            current = self.get_temperature(0)
            if current >= target:
                break
            if time.time() - start > timeout:
                raise TimeoutError(f"T0 did not reach {target}°C within {timeout}s")
            time.sleep(2)
        logger.info(f"T0 at {target}°C")

    def _wait_for_heater_bed(self, target, timeout=120):
        start = time.time()
        while True:
            current = self.get_temperature(1)
            if current >= target:
                break
            if time.time() - start > timeout:
                raise TimeoutError(f"heater_bed did not reach {target}°C within {timeout}s")
            time.sleep(2)
        logger.info(f"heater_bed at {target}°C")

    def get_temperature(self, tool: int) -> float:
        """Return current temperature. tool=0 → extruder, tool=1 → heater_bed."""
        try:
            status = self.get_printer_status()
            if tool == 0:
                return status.get('extruder', {}).get('temperature', 0.0)
            elif tool == 1:
                return status.get('heater_bed', {}).get('temperature', 0.0)
            else:
                return 0.0
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
            wait: If False, the command is sent but the HTTP response is
                  not waited for (the move runs in the background).
        """
        self.set_relative_extrusion()
        feedrate_mm_min = feedrate_mm_s * 60.0
        if tool == 0:
            script = f"G1 E{length_mm:.3f} F{feedrate_mm_min:.1f}"
        elif tool == 1:
            script = f"G1 X{length_mm:.3f} F{feedrate_mm_min:.1f}"
        else:
            raise ValueError(f"Invalid tool index: {tool}")

        if wait:
            self.send_gcode(script)
        else:
            # Fire‑and‑forget: send with a very short timeout, ignore any timeout
            try:
                self.send_gcode(script, async_mode=True, timeout=10)
            except requests.exceptions.ReadTimeout:
                # The command was accepted and is now running in Klipper
                pass

    # ------------------------------------------------------------------
    # Synchronous dual extrusion (single G1 command)
    # ------------------------------------------------------------------

    def extrude_sync(self, length_t0: float, speed_t0: float,
                     length_t1: float, speed_t1: float,
                     wait: bool = True) -> None:
        """Extrude both tools simultaneously with one G1 command.

        Args:
            length_t0: Filament length for tool 0 (E axis) in mm.
            speed_t0:  Desired linear speed for tool 0 (mm/s).
            length_t1: Filament length for tool 1 (X axis) in mm.
            speed_t1:  Desired linear speed for tool 1 (mm/s).
            wait: If False, the command is sent in the background.

        Both moves start and end together.
        """
        if speed_t0 <= 0 or speed_t1 <= 0:
            raise ValueError("Speeds must be positive.")

        # Time each axis would take at its requested speed
        time_t0 = abs(length_t0) / speed_t0
        time_t1 = abs(length_t1) / speed_t1

        # Use the longer time so both finish together
        duration = max(time_t0, time_t1)

        # Combined path length
        L = (length_t0**2 + length_t1**2) ** 0.5
        # Feedrate in mm/min to cover L in 'duration' seconds
        F = (L / duration) * 60.0

        self.set_relative_extrusion()
        script = f"G91\nG1 F{F:.1f} X{length_t1:.3f} E{length_t0:.3f} \nG90"

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
            "extruder": "",
            "heater_bed": "",
            "toolhead": "",
            "print_stats": ""
        })
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", {}).get("status", {})

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def disable_all_heaters(self) -> None:
        self.send_gcode("M104 S0 T0")
        self.send_gcode("SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=0")
        logger.info("All heaters disabled")