import serial
import time
import logging

logger = logging.getLogger(__name__)

class TurntableController:
    """Serial connection to turntable microcontroller.
    Expected protocol:
      - ROT:<angle>,<speed>\\n -> absolute move, reply OK\\n when done
      - ROT:REL:<angle>,<speed>\\n -> relative move
      - STOP\\n
    """
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 30):
        self.ser = serial.Serial(port, baudrate, timeout=timeout)
        time.sleep(2)  # wait for microcontroller reset
        self._flush()

    def _flush(self):
        self.ser.reset_input_buffer()

    def _send_command(self, cmd: str):
        self.ser.write(f"{cmd}\n".encode())
        logger.debug(f"Turntable TX: {cmd}")

    def _wait_ok(self, timeout: float = 30):
        """Wait for 'OK' response."""
        self.ser.timeout = timeout
        response = self.ser.readline().decode().strip()
        if response == "OK":
            logger.info("Turntable motion completed")
        else:
            logger.warning(f"Unexpected turntable response: {response}")

    def rotate_absolute(self, angle_deg: float, speed_dps: float, wait: bool = True):
        self._send_command(f"ROT:{angle_deg:.2f},{speed_dps:.2f}")
        if wait:
            self._wait_ok()

    def rotate_relative(self, angle_deg: float, speed_dps: float, wait: bool = True):
        self._send_command(f"ROT:REL:{angle_deg:.2f},{speed_dps:.2f}")
        if wait:
            self._wait_ok()

    def stop(self):
        self._send_command("STOP")

    def close(self):
        self.ser.close()