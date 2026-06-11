"""
Embeddable 3D twin: PyBullet in headless DIRECT mode, rendered to an RGB image.

The normal PyBullet GUI opens its own OS window and can't live inside a Tk panel.
Instead we run PyBullet in DIRECT mode (no window, no debug-API server -- which
also sidesteps the GUI crashes seen on some drivers) and grab frames with
``getCameraImage`` (CPU TinyRenderer). The dashboard paints those frames into a
Tk canvas, so the 3D view is nested inside the control panel.

Scene construction and per-frame state mapping are reused from
:class:`~simulation.twin_viewer.TwinViewer` -- we drive its ``_setup`` and
``_update_frame`` directly instead of its blocking GUI loop.
"""

from __future__ import annotations

import math
from typing import Optional

from src.calibration import Calibration
from src.system_state import StateSnapshot, SystemState
from simulation.twin_viewer import TwinViewer


class EmbeddedTwin:
    def __init__(
        self,
        provider,
        calibration: Optional[Calibration] = None,
        *,
        mirror_arms: bool = False,
        drive_mode: str = "auto",
    ) -> None:
        import pybullet as p
        import pybullet_data

        self.p = p
        self._pd = pybullet_data
        self.tv = TwinViewer(provider, calibration, gui=False,
                             mirror_arms=mirror_arms, drive_mode=drive_mode)
        self.tv._setup(p, pybullet_data)

        # camera state (orbit around the disc centre)
        self.cam_target = [0.0, 0.0, 0.05]
        self.cam_distance = 1.9
        self.cam_yaw = 50.0
        self.cam_pitch = -32.0
        self._np = None
        try:
            import numpy as np
            self._np = np
        except Exception:
            self._np = None

    @classmethod
    def from_state(cls, state: SystemState, calibration=None, **kw) -> "EmbeddedTwin":
        return cls(state.snapshot, calibration, **kw)

    # -- camera -----------------------------------------------------------

    def orbit(self, d_yaw: float = 0.0, d_pitch: float = 0.0) -> None:
        self.cam_yaw += d_yaw
        self.cam_pitch = max(-89.0, min(-2.0, self.cam_pitch + d_pitch))

    def zoom(self, factor: float) -> None:
        self.cam_distance = max(0.4, min(5.0, self.cam_distance * factor))

    # -- render -----------------------------------------------------------

    def update_and_render(self, width: int, height: int):
        """Pull the latest state, update the scene, and return an (H, W, 3)
        uint8 RGB array (numpy) or a flat bytes fallback."""
        p = self.p
        try:
            self.tv._update_frame(p)
        except Exception:
            pass

        view = p.computeViewMatrixFromYawPitchRoll(
            cameraTargetPosition=self.cam_target,
            distance=self.cam_distance,
            yaw=self.cam_yaw, pitch=self.cam_pitch, roll=0, upAxisIndex=2,
        )
        proj = p.computeProjectionMatrixFOV(
            fov=55.0, aspect=float(width) / float(height), nearVal=0.02, farVal=12.0,
        )
        w, h, rgba, _, _ = p.getCameraImage(
            width, height, viewMatrix=view, projectionMatrix=proj,
            renderer=p.ER_TINY_RENDERER,
        )
        if self._np is not None:
            arr = self._np.reshape(self._np.array(rgba, dtype=self._np.uint8), (h, w, 4))
            return arr[:, :, :3]
        return (w, h, bytes(bytearray(rgba)))

    def disconnect(self) -> None:
        try:
            if self.p.isConnected():
                self.p.disconnect()
        except Exception:
            pass
