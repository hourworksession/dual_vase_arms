import requests
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ExtruderController:
    """Controls extruders via Klipper/Moonraker HTTP API.

    Supports T0 and T1 with full preparation routines (heat, load, purge, macros).
    """

    def __init__(self, host: str, port: int = 7125) -> None:
        self.base_url = f"http://{host}:{port}"
        self.session = requests.Session()
        self.relative_mode = False

    # ------------------------------------------------------------------
    # Low‑level G‑code sending
    # ------------------------------------------------------------------

    def send_gcode(self, script: str, timeout: int = 30) -> dict:
        """Send raw G‑code script and return the JSON response."""
        url = f"{self.base_url}/printer/gcode/script"
        payload = {"script": script}
        resp = self.session.post(url, data=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Temperature management
    # ------------------------------------------------------------------

    def set_temperature(self, tool: int, temp: float, wait: bool = False) -> None:
        """Set target temperature for a specific extruder.

        Args:
            tool: Extruder index (0 or 1).
            temp: Target temperature in Celsius.
            wait: If True, block until temperature is reached (via M109).
                  Note: For long heating times, use ``heat_and_wait``
                  instead, which polls safely.
        """
        if wait:
            # M109 can cause HTTP timeouts if heating takes too long.
            # Use a generous timeout.
            self.send_gcode(f"M109 S{temp} T{tool}", timeout=120)
        else:
            self.send_gcode(f"M104 S{temp} T{tool}")

    def heat_and_wait(self, tool: int, temp: float, timeout: int = 120) -> None:
        """Send non‑blocking M104, then poll until temperature is reached.

        Args:
            tool: Extruder index.
            temp: Target temperature in °C.
            timeout: Maximum seconds to wait (default 120).
        """
        # Send non‑blocking heat command
        self.send_gcode(f"M104 S{temp} T{tool}")
        logger.info(f"Heating T{tool} to {temp}°C, waiting...")

        start = time.time()
        while True:
            current = self.get_temperature(tool)
            logger.debug(f"T{tool}: {current:.1f}°C")
            if current >= temp:
                break
            if time.time() - start > timeout:
                raise TimeoutError(
                    f"T{tool} did not reach {temp}°C within {timeout}s"
                )
            time.sleep(2)

        logger.info(f"T{tool} at {temp}°C")

    def get_temperature(self, tool: int) -> float:
        """Return current temperature of a tool, or 0.0 if unknown."""
        try:
            status = self.get_printer_status()
            extruder_key = "extruder" if tool == 0 else "extruder1"
            temp = status.get(extruder_key, {}).get("temperature")
            if temp is None:
                # fallback names
                alt = "extruder0" if tool == 0 else "extruder"
                temp = status.get(alt, {}).get("temperature", 0.0)
            return temp if temp is not None else 0.0
        except Exception:
            logger.warning("Could not read temperature for tool %d", tool)
            return 0.0

    # ------------------------------------------------------------------
    # Basic extrusion moves
    # ------------------------------------------------------------------

    def set_relative_extrusion(self) -> None:
        """Switch to relative extrusion mode (M83)."""
        if not self.relative_mode:
            self.send_gcode("M83")
            self.relative_mode = True

    def extrude(self, tool: int, length_mm: float, feedrate_mm_s: float) -> None:
        """Extrude or retract a length of filament.

        Args:
            tool: Extruder index (0 or 1).
            length_mm: Positive = forward, negative = retract.
            feedrate_mm_s: Extrusion speed in mm/s.
        """
        self.set_relative_extrusion()
        feedrate_mm_min = feedrate_mm_s * 60.0
        script = f"T{tool}\nG1 E{length_mm:.3f} F{feedrate_mm_min:.1f}"
        self.send_gcode(script)

    # ------------------------------------------------------------------
    # Preparation helpers
    # ------------------------------------------------------------------

    def load_filament(self, tool: int, length_mm: float = 50.0,
                      feedrate_mm_s: float = 5.0) -> None:
        """Extrude a priming length (useful after inserting new filament)."""
        logger.info("Loading filament on T%d (%d mm)", tool, length_mm)
        self.extrude(tool, length_mm, feedrate_mm_s)

    def purge(self, tool: int, amount: float = 10.0,
              feedrate_mm_s: float = 5.0) -> None:
        """Quick purge to clean the nozzle."""
        logger.info("Purging T%d (%.1f mm)", tool, amount)
        self.extrude(tool, amount, feedrate_mm_s)

    # ------------------------------------------------------------------
    # Custom macros (from printer.cfg)
    # ------------------------------------------------------------------

    def run_macro(self, macro_name: str, **params) -> None:
        """Execute a G‑code macro defined in your Klipper config.

        Example:
            run_macro("EXTRUDE_E0", E=10)
            run_macro("DUAL_STREAM", E=100, F=1000, N=10)
        """
        param_str = " ".join(f"{k.upper()}={v}" for k, v in params.items())
        script = f"{macro_name} {param_str}"
        logger.info("Running macro: %s", script)
        self.send_gcode(script)

    # ------------------------------------------------------------------
    # Status retrieval (for monitoring/GUI)
    # ------------------------------------------------------------------

    def get_printer_status(self) -> Dict[str, Any]:
        """Query the full printer object from Moonraker.

        Returns a dict with keys like ``extruder``, ``extruder1``,
        ``heater_bed``, ``print_stats``, etc.
        """
        url = f"{self.base_url}/printer/objects/query"
        resp = self.session.get(url, params={
            "extruder": "",
            "extruder1": "",
            "heater_bed": "",
            "toolhead": "",
            "print_stats": ""
        }, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", {}).get("status", {})

    # ------------------------------------------------------------------
    # Convenience shutdown
    # ------------------------------------------------------------------

    def disable_all_heaters(self) -> None:
        """Turn off all heaters."""
        self.send_gcode("M104 S0 T0")
        self.send_gcode("M104 S0 T1")
        self.send_gcode("M140 S0")
        logger.info("All heaters disabled")