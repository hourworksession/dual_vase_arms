"""
Live 3D digital twin (PyBullet), driven by a snapshot provider.

The viewer maps each arm's pose into the world frame via :mod:`src.calibration`
and updates the scene every frame:

  * each arm linkage follows its calibrated TCP by inverse kinematics;
  * a white marker is pinned to the *exact* calibrated TCP (the authoritative
    "where the nozzle is" indicator, independent of IK quality);
  * the disc rotates to the mapped turntable angle.

Threading note
--------------
PyBullet's GUI cannot run on a background thread (and the control panel's Tk
main loop already owns the main thread). So the viewer is designed to be run via
:meth:`run_blocking` on the MAIN thread of whatever process hosts it -- in
practice a dedicated subprocess launched by :mod:`simulation.twin_launcher`. The
viewer pulls state through a ``snapshot_provider`` callable, so it doesn't care
whether that state comes from an in-process ``SystemState`` or shared memory.

When the real xArm 850 URDF + meshes are available, pass ``arm_urdf=<path>`` and
``drive_mode="joints"`` to play back true servo angles for an exact match.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Callable, List, Optional

from src.calibration import Calibration
from src.system_state import StateSnapshot, SystemState

logger = logging.getLogger(__name__)

_MM_TO_M = 0.001

SnapshotProvider = Callable[[], Optional[StateSnapshot]]


class TwinViewer:
    def __init__(
        self,
        snapshot_provider: SnapshotProvider,
        calibration: Optional[Calibration] = None,
        *,
        arm_urdf: Optional[str] = None,
        drive_mode: str = "auto",   # "auto": joints if present else IK; "ik": force IK
        mirror_arms: bool = False,  # demo aid: drive right with left's IK solution
        fps: float = 30.0,
        gui: bool = True,
    ) -> None:
        self.provider = snapshot_provider
        self.cal = calibration or Calibration.load()
        self.arm_urdf = arm_urdf
        self.drive_mode = drive_mode
        self.mirror_arms = mirror_arms
        self.period = 1.0 / fps if fps > 0 else 0.0
        self.gui = gui

        self._stop = threading.Event()

        # populated in _setup
        self._arm_ids = {}
        self._movable = {}
        self._ee_index = {}
        self._tcp_marker = {}
        self._ik = {}            # arm -> dict(lower, upper, ranges, rest, damping)
        self._disc_id = None
        self._disc_mark_id = None

    @classmethod
    def from_state(cls, state: SystemState, *args, **kwargs) -> "TwinViewer":
        return cls(state.snapshot, *args, **kwargs)

    def stop(self) -> None:
        self._stop.set()

    # -- main-thread run loop -------------------------------------------

    def run_blocking(self) -> None:
        """Open the window and render until it is closed or :meth:`stop` is
        called. MUST be called on the host process's main thread."""
        try:
            import pybullet as p
            import pybullet_data
        except Exception:
            logger.exception("PyBullet not available; install with: pip install pybullet")
            return

        try:
            self._setup(p, pybullet_data)
            while not self._stop.is_set():
                start = time.monotonic()
                if not p.isConnected():
                    break
                try:
                    self._update_frame(p)
                    p.stepSimulation()
                except Exception:
                    if not p.isConnected():
                        break  # window closed mid-frame
                    logger.exception("twin frame update failed")
                elapsed = time.monotonic() - start
                self._stop.wait(max(0.0, self.period - elapsed))
        finally:
            try:
                if p.isConnected():
                    p.disconnect()
            except Exception:
                pass

    # -- scene construction ---------------------------------------------

    def _setup(self, p, pybullet_data) -> None:
        p.connect(p.GUI if self.gui else p.DIRECT)
        p.setAdditionalSearchPath(pybullet_data.getDataPath())
        p.setGravity(0, 0, 0)              # kinematic viewer, no dynamics
        p.loadURDF("plane.urdf")
        if self.gui:
            p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
            p.resetDebugVisualizerCamera(
                cameraDistance=1.8, cameraYaw=45, cameraPitch=-30,
                cameraTargetPosition=[0, 0, 0.1],
            )

        thick = self.cal.disc_surface_thickness_mm * _MM_TO_M
        radius = self.cal.disc_radius_mm * _MM_TO_M
        disc_vis = p.createVisualShape(p.GEOM_CYLINDER, radius=radius, length=thick,
                                       rgbaColor=[0.85, 0.85, 0.9, 0.6])
        disc_col = p.createCollisionShape(p.GEOM_CYLINDER, radius=radius, height=thick)
        self._disc_id = p.createMultiBody(
            baseMass=0, baseCollisionShapeIndex=disc_col,
            baseVisualShapeIndex=disc_vis, basePosition=[0, 0, -thick / 2.0],
        )
        mark_vis = p.createVisualShape(p.GEOM_BOX,
                                       halfExtents=[0.02, 0.02, thick / 2 + 0.002],
                                       rgbaColor=[0.9, 0.2, 0.2, 1])
        self._disc_mark_id = p.createMultiBody(
            baseMass=0, baseVisualShapeIndex=mark_vis,
            basePosition=[radius * 0.8, 0, 0],
        )

        self._draw_safe_zones(p, radius)

        for arm in self.cal.arms:
            if self.arm_urdf:
                urdf = self.arm_urdf
            else:
                from simulation.primitive_arm import urdf_for_arm
                urdf, _ = urdf_for_arm(arm)
            bx, by, bz = self.cal.base_world_xyz(arm)
            base_pos = [bx * _MM_TO_M, by * _MM_TO_M, bz * _MM_TO_M]
            yaw = math.radians(self.cal.arms[arm].base_yaw_deg)
            base_orn = p.getQuaternionFromEuler([0, 0, yaw])
            body = p.loadURDF(urdf, basePosition=base_pos,
                              baseOrientation=base_orn, useFixedBase=True)
            self._arm_ids[arm] = body
            self._movable[arm] = [
                j for j in range(p.getNumJoints(body))
                if p.getJointInfo(body, j)[2] == p.JOINT_REVOLUTE
            ]
            self._ee_index[arm] = self._find_ee_index(p, body)
            self._ik[arm] = self._build_ik_config(p, body, self._movable[arm])
            # seat the arm at its rest pose so frame 1 starts from a sane posture
            for idx, q in zip(self._movable[arm], self._ik[arm]["rest"]):
                p.resetJointState(body, idx, q)

            tcp_vis = p.createVisualShape(p.GEOM_SPHERE, radius=0.015,
                                          rgbaColor=[1, 1, 1, 1])
            self._tcp_marker[arm] = p.createMultiBody(
                baseMass=0, baseVisualShapeIndex=tcp_vis, basePosition=base_pos,
            )

    def _draw_safe_zones(self, p, radius) -> None:
        """Draw the two world-fixed safe-zone halves as translucent slabs just
        above the disc surface (right = orange, left = blue), split by a thin
        white divider. The disc rotates beneath them; the zones stay fixed
        because they are defined relative to the arms.

        Uses solid createMultiBody geometry only -- the addUserDebug* API is
        avoided because it can destabilise the GUI server on some drivers.
        """
        sz = getattr(self.cal, "safe_zones", None)
        if sz is None:
            return
        try:
            from simulation.zone_mesh import half_disc_obj
            z = 0.0016
            for arm in ("right", "left"):
                a0, a1 = sz.angular_bounds(arm)
                obj = half_disc_obj(f"zone_{arm}", radius * 0.99, a0, a1)
                rgba = list(sz.color(arm)) + [0.40]
                vis = p.createVisualShape(p.GEOM_MESH, fileName=obj,
                                          meshScale=[1, 1, 1], rgbaColor=rgba)
                p.createMultiBody(baseMass=0, baseVisualShapeIndex=vis,
                                  basePosition=[0, 0, z])
            # thin white divider line along the split
            d = math.radians(sz.divider_deg())
            dv = p.createVisualShape(p.GEOM_BOX, halfExtents=[radius, 0.004, 0.0012],
                                     rgbaColor=[1, 1, 1, 0.9])
            p.createMultiBody(baseMass=0, baseVisualShapeIndex=dv,
                              basePosition=[0, 0, z + 0.001],
                              baseOrientation=p.getQuaternionFromEuler([0, 0, d]))
        except Exception:
            logger.exception("could not draw safe zones (continuing without them)")

    @staticmethod
    def _find_ee_index(p, body) -> int:
        last = p.getNumJoints(body) - 1
        for j in range(p.getNumJoints(body)):
            if p.getJointInfo(body, j)[12].decode() == "tool_tip":
                return j
        return last

    @staticmethod
    def _build_ik_config(p, body, movable):
        """Read joint limits and assemble null-space IK parameters so the arm
        holds a steady, natural posture instead of flailing each frame."""
        try:
            from simulation.primitive_arm import REST_POSE
            rest = list(REST_POSE)
        except Exception:
            rest = [0.0] * len(movable)
        lower, upper = [], []
        for j in movable:
            info = p.getJointInfo(body, j)
            lo, hi = info[8], info[9]
            if lo >= hi:          # unlimited -> clamp for IK sanity
                lo, hi = -math.pi, math.pi
            lower.append(lo)
            upper.append(hi)
        if len(rest) != len(movable):
            rest = [0.0] * len(movable)
        return {
            "lower": lower,
            "upper": upper,
            "ranges": [hi - lo for lo, hi in zip(lower, upper)],
            "rest": rest,
            "damping": [0.08] * len(movable),
        }

    # -- per-frame update ------------------------------------------------

    def _update_frame(self, p) -> None:
        snap = self.provider()
        if snap is None:
            return

        if snap.turntable_deg is not None:
            world_deg = self.cal.turntable_world_deg(snap.turntable_deg)
            orn = p.getQuaternionFromEuler([0, 0, math.radians(world_deg)])
            thick = self.cal.disc_surface_thickness_mm * _MM_TO_M
            p.resetBasePositionAndOrientation(self._disc_id, [0, 0, -thick / 2.0], orn)
            r = self.cal.disc_radius_mm * _MM_TO_M * 0.8
            mark_pos = [r * math.cos(math.radians(world_deg)),
                        r * math.sin(math.radians(world_deg)), 0]
            p.resetBasePositionAndOrientation(self._disc_mark_id, mark_pos, orn)

        poses = {"left": snap.left_pose, "right": snap.right_pose}
        joints = {"left": snap.left_joints, "right": snap.right_joints}
        order = [a for a in ("left", "right") if a in self._arm_ids]
        left_sol = None
        for arm in order:
            pose = poses.get(arm)
            jt = joints.get(arm)
            if pose is None and jt is None:
                continue
            target = target_orn = None
            if pose is not None:
                world = self.cal.arm_to_world(arm, pose)
                wx, wy, wz, roll, pitch, wyaw = world
                target = [wx * _MM_TO_M, wy * _MM_TO_M, wz * _MM_TO_M]
                target_orn = p.getQuaternionFromEuler(
                    [math.radians(roll), math.radians(pitch), math.radians(wyaw)]
                )

            if jt is not None and self.drive_mode != "ik":
                # exact configuration from real servo angles (or a home pose)
                self._set_joints(p, arm, jt)
            elif self.mirror_arms and arm == "right" and left_sol is not None:
                # demo: drive the right arm with the left's solution so the two
                # identical arms move as exact mirror images
                self._apply_solution(p, "right", left_sol)
            elif target is not None:
                # offline / commanded-only: infer a steady posture by IK
                sol = self._ik_to(p, arm, target, target_orn)
                if arm == "left":
                    left_sol = sol

            # pin the nozzle marker to the actual model tip (forward kinematics)
            self._set_marker_fk(p, arm)

    def _set_marker_fk(self, p, arm) -> None:
        """Place the nozzle marker on the actual model tip via forward kinematics."""
        try:
            ls = p.getLinkState(self._arm_ids[arm], self._ee_index[arm])
            p.resetBasePositionAndOrientation(self._tcp_marker[arm], ls[4], ls[5])
        except Exception:
            pass

    def _set_joints(self, p, arm, joint_deg: List[float]) -> None:
        body = self._arm_ids[arm]
        for idx, q in zip(self._movable[arm], joint_deg):
            p.resetJointState(body, idx, math.radians(q))

    def _apply_solution(self, p, arm, sol) -> None:
        """Apply an IK solution (radians, in movable-joint order) to an arm."""
        body = self._arm_ids[arm]
        for idx, q in zip(self._movable[arm], sol):
            p.resetJointState(body, idx, q)

    def _ik_to(self, p, arm, target, target_orn):
        """Solve IK and apply it; return the solution (radians) or None."""
        body = self._arm_ids[arm]
        ee = self._ee_index[arm]
        cfg = self._ik[arm]
        try:
            sol = p.calculateInverseKinematics(
                body, ee, target, target_orn,
                lowerLimits=cfg["lower"], upperLimits=cfg["upper"],
                jointRanges=cfg["ranges"], restPoses=cfg["rest"],
                jointDamping=cfg["damping"],
                maxNumIterations=120, residualThreshold=1e-4,
            )
        except Exception:
            try:
                sol = p.calculateInverseKinematics(body, ee, target)
            except Exception:
                return None
        for idx, q in zip(self._movable[arm], sol):
            p.resetJointState(body, idx, q)
        return sol
