"""Machine logic for the cell, independent of any widgets.

Ported from scripts/gleadell_panel.py. In the default "one move" extrusion mode
the cylinder print sends the same moves, speeds, poses and extrusion in the same
order as the Tk panel. The plumbing changed:

* tk variables  -> ``Var`` (same get/set API)
* root.after(0) -> ``ui.post``
* messagebox    -> ``ui.info/warn/error`` (non-blocking, so the E-stop stays reachable)

Safety and usability changes, all found by tests/test_cell_studio_scenarios.py:
* Start checks the job first (revolutions, radius, speeds...) and shows a
  pre-flight summary, warning when the extrusion time no longer matches the
  rotation time or live offsets are non-zero. Filament is always recalculated.
* Stop is honoured during the approach, before any extrusion is queued.
* E-stop: every device is stopped even if one call fails, nothing is commanded
  afterwards, and the panel marks everything disconnected until you reconnect.
* Connect tries every ticked device, reports each failure, and closes old
  sessions first. Disconnect is refused while a job runs.
* Jog is refused while homing or preparing; Prepare says when nothing was heated.
* The telemetry shows "no response" for a device that stops answering.
* Optional "follow turntable" extrusion streams filament in small chunks tied
  to the measured turntable angle, so Pause, Stop, speed changes and errors stop
  or follow the flow. Off by default until it has been tried on the cell.
"""

import json
import logging
import math
import os
import sys
import threading
import time

from .state import DoubleVar, BoolVar, StrVar, Var, UiBridge
from . import home_config

logger = logging.getLogger("control_panel")

REPO_ROOT = home_config.REPO_ROOT
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# key: (label, unit, decimals, step, section, tooltip, live, minimum)
PARAM_META = {
    'radius':                 ("Radius", "mm", 2, 0.5, "geometry", None, False, 0.0),
    'z_start':                ("Z start", "mm", 2, 0.1, "geometry", "Height of the first layer above the turntable centre Z", False, None),
    'pitch':                  ("Pitch", "mm", 3, 0.05, "geometry", "Z rise per turntable revolution (layer height in vase mode)", False, 0.0),
    'total_revs':             ("Total revolutions", "rev", 1, 1.0, "geometry", None, False, 0.0),
    'line_width':             ("Line width", "mm", 2, 0.05, "geometry", None, False, 0.0),
    'filament_diameter':      ("Filament diameter", "mm", 2, 0.05, "extrusion", None, False, 0.0),
    'feed_rate_left':         ("Feed rate · left", "mm/s", 2, 0.1, "extrusion", "Filament speed. Set at start in one move mode", False, 0.0),
    'feed_rate_right':        ("Feed rate · right", "mm/s", 2, 0.1, "extrusion", "Filament speed. Set at start in one move mode", False, 0.0),
    'extrusion_factor_left':  ("Extrusion factor · left", "×", 3, 0.01, "extrusion", None, False, 0.0),
    'extrusion_factor_right': ("Extrusion factor · right", "×", 3, 0.01, "extrusion", None, False, 0.0),
    'start_angle_deg':        ("Start angle", "°", 1, 1.0, "placement", "Angle on the disc where the right nozzle starts", False, None),
    'angular_offset_deg':     ("Angular offset · left", "°", 1, 1.0, "placement", "Left nozzle start angle relative to the right nozzle", False, None),
    'radial_offset_left':     ("Radial offset · left", "mm", 2, 0.1, "placement", "Live: read during a print", True, None),
    'radial_offset_right':    ("Radial offset · right", "mm", 2, 0.1, "placement", "Live: read during a print", True, None),
    'tt_cx_left':             ("Centre X · left", "mm", 1, 0.1, "centre", None, False, None),
    'tt_cy_left':             ("Centre Y · left", "mm", 1, 0.1, "centre", None, False, None),
    'tt_cz_left':             ("Centre Z · left", "mm", 1, 0.1, "centre", "Live: read during a print", True, None),
    'tt_cx_right':            ("Centre X · right", "mm", 1, 0.1, "centre", None, False, None),
    'tt_cy_right':            ("Centre Y · right", "mm", 1, 0.1, "centre", None, False, None),
    'tt_cz_right':            ("Centre Z · right", "mm", 1, 0.1, "centre", "Live: read during a print", True, None),
}

STREAM_CHUNK_REV = 1.0 / 36.0     # follow-turntable mode queues at most 10 degrees of filament
FLOW_TOLERANCE = 0.05             # warn when extrusion and rotation time differ by more than 5 %


def _mmss(t):
    t = max(0, int(t))
    return f"{t // 60:02d}:{t % 60:02d}"


class CellController:
    def __init__(self, ui: UiBridge):
        self.ui = ui
        from config_loader import load_config
        self.cfg = load_config()
        self.hw_connected = False
        self.left = self.right = self.turntable = self.extruder = None
        self.printing = False
        self.stop_requested = False
        self.paused = False
        self.busy = False       # homing / preparing
        self.estopped = False
        self.model_job_active = lambda: False   # set by the Model print page

        # Which devices to connect / drive. Both arms carry extruders on the
        # current machine, so both connect by default.
        self.conn_left = BoolVar(True)
        self.conn_right = BoolVar(True)
        self.conn_turntable = BoolVar(True)
        self.conn_extruder = BoolVar(True)
        self.conn_simulated = BoolVar(False)
        self.conn_status_var = StrVar("Not connected")
        self.connected_var = Var({}, dict)   # {"left": "real"/"sim", ...}

        self.axes = ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw']

        # ---------- Cylinder parameters ----------
        self.param_vars = {
            'radius':             DoubleVar(100.0),
            'z_start':            DoubleVar(151.8),
            'pitch':              DoubleVar(self._tool_default('layer_height', 0.4)),
            'total_revs':         DoubleVar(20.0),
            'start_angle_deg':    DoubleVar(135.0),
            'line_width':         DoubleVar(self._tool_default('line_width', 0.4)),
            'filament_diameter':  DoubleVar(1.75),
            'feed_rate_left':     DoubleVar(4.0),
            'feed_rate_right':    DoubleVar(4.0),
            'extrusion_factor_left':  DoubleVar(1.0),
            'extrusion_factor_right': DoubleVar(1.0),
            # New mounts (Oct 2026): XC/YC_{RIGHT,LEFT} and Z_{R,L}_OFFSET from the cell config.
            # (Old mounts: left 574.1, -5.4, 153; right 567.7, 6.7, 151.1.)
            'tt_cx_left':         DoubleVar(512.1),
            'tt_cy_left':         DoubleVar(-8.4),
            'tt_cz_left':         DoubleVar(110.2),
            'tt_cx_right':        DoubleVar(508.18),
            'tt_cy_right':        DoubleVar(-22.88),
            'tt_cz_right':        DoubleVar(107.4),
            'angular_offset_deg': DoubleVar(5.0),
            'radial_offset_left':  DoubleVar(0.0),
            'radial_offset_right': DoubleVar(0.0),
        }

        # Pattern modulation
        self.pattern_enabled = BoolVar(False)
        self.pattern_waveform = StrVar('sine')
        self.pattern_amplitude = DoubleVar(2.0)
        self.pattern_wave_count = DoubleVar(5.0)
        self.pattern_phase_offset = DoubleVar(0.0)
        self.pattern_arm_left = BoolVar(True)
        self.pattern_arm_right = BoolVar(True)

        # Live offsets per arm
        self.offset_vars = {}
        for ax in self.axes:
            self.offset_vars[f'left_{ax}'] = DoubleVar(0.0)
            self.offset_vars[f'right_{ax}'] = DoubleVar(0.0)

        self.turntable_speed_var = DoubleVar(0.6)   # rad/s
        self.tt_speed_max = 2.0
        self.base_arm_speed_var = DoubleVar(100.0)  # mm/s
        self.extrusion_mode = StrVar("single")      # "single" (original) | "streamed"

        # Jog controls
        self.jog_arm = StrVar('left')
        self.jog_step = DoubleVar(1.0)

        # Extruder panel
        self.calc_left_len = DoubleVar(0.0)
        self.calc_right_len = DoubleVar(0.0)
        self.prime_len = DoubleVar(20.0)
        _tdef = self.cfg.get('defaults', {}).get('temperature', {})
        self.temp_left = DoubleVar(float(_tdef.get('tool1', 235)))
        self.temp_right = DoubleVar(float(_tdef.get('tool0', 235)))

        # Job status
        self.elapsed_time_var = StrVar("00:00")
        self.remaining_time_var = StrVar("00:00")
        self.job_state = StrVar("idle")      # idle | running | paused | homing | preparing
        self.job_name = StrVar("")

        self.calc_mms_var = DoubleVar(10.0)

        # Polar preview nodes (preview only: the print follows radius + wave pattern)
        self.polar_nodes = [i * (2 * math.pi / 36) for i in range(36)]
        self.nodes_changed = Var(0, int)

        # Telemetry (filled by the poller)
        self.tel = {k: StrVar("—") for k in
                    ("left_pos", "left_rot", "right_pos", "right_rot", "turntable", "t0", "t1")}

        from . import tool as toolmod
        self.tool = toolmod.load()
        self.home = home_config.load()
        self.home_load_error = home_config.LOAD_ERROR

        self._speed_notice_given = False
        self.turntable_speed_var.changed.connect(self._speed_changed_live)

        self.live = {}
        self._poll_stop = threading.Event()
        threading.Thread(target=self._poll_loop, daemon=True, name="telemetry").start()

    def _tool_default(self, key, fallback):
        try:
            from . import tool as toolmod
            return float(toolmod.load()[key])
        except Exception:
            return fallback

    def orientation(self, side="right"):
        """(roll, pitch, yaw) that arm's nozzle is commanded at: Settings ▸ Tool and nozzle.
        Each arm has its own (the new mounts face each other: right 90/-90/90, left 0/90/0)."""
        from . import tool as toolmod
        return toolmod.orientation(self.tool, side)

    # ==================================================================
    def safe_get(self, var, name):
        if not hasattr(self, '_safe_cache'):
            self._safe_cache = {}
        try:
            float_val = float(var.get())
            self._safe_cache[name] = float_val
            return float_val
        except (ValueError, TypeError):
            return self._safe_cache.get(name, 0.0)

    def nudge_turntable_speed(self, delta):
        try:
            new_val = self.turntable_speed_var.get() + delta
        except (ValueError, TypeError):
            new_val = 0.0
        self.turntable_speed_var.set(max(0.0, min(self.tt_speed_max, round(new_val, 3))))

    def calc_rads_from_mms(self):
        mms = self.calc_mms_var.get()
        radius = self.param_vars['radius'].get()
        rads = mms / radius if radius > 0 else 0.0
        self.turntable_speed_var.set(round(min(rads, self.tt_speed_max), 4))

    def _speed_changed_live(self, v):
        if self.printing and self.extrusion_mode.get() == "single" and not self._speed_notice_given:
            self._speed_notice_given = True
            self.ui.warn("Turntable speed changed",
                         "The turntable follows the new speed, but in one move extrusion mode the "
                         "extruder keeps the rate set at start, so the wall gets thinner or thicker. "
                         "Speed 0 stops the turntable but not the extruder.\n\n"
                         "Switch Extrusion to 'Follow turntable' if you need to change speed mid-print.")

    # ---------------- preview nodes ----------------
    def reset_nodes(self):
        self.polar_nodes = [i * (2 * math.pi / 36) for i in range(36)]
        self.nodes_changed.set(self.nodes_changed.get() + 1)

    def toggle_node(self, theta):
        for i, node_theta in enumerate(self.polar_nodes):
            diff = abs(node_theta - theta)
            if diff > math.pi:
                diff = 2 * math.pi - diff
            if diff < 0.05:
                self.polar_nodes.pop(i)
                self.nodes_changed.set(self.nodes_changed.get() + 1)
                return
        self.polar_nodes.append(theta)
        self.polar_nodes.sort()
        self.nodes_changed.set(self.nodes_changed.get() + 1)

    @staticmethod
    def wave_value(phase, amp, waveform):
        if waveform == 'sine':
            return amp * math.sin(phase)
        elif waveform == 'triangle':
            return amp * (2 / math.pi * math.asin(math.sin(phase)))
        elif waveform == 'square':
            return amp * (1 if math.sin(phase) >= 0 else -1)
        return 0.0

    # ---------------- config files ----------------
    def _simple_vars(self):
        return {'pattern_enabled': self.pattern_enabled, 'pattern_waveform': self.pattern_waveform,
                'pattern_amplitude': self.pattern_amplitude, 'pattern_wave_count': self.pattern_wave_count,
                'pattern_phase_offset': self.pattern_phase_offset, 'pattern_arm_left': self.pattern_arm_left,
                'pattern_arm_right': self.pattern_arm_right, 'turntable_speed': self.turntable_speed_var,
                'base_arm_speed': self.base_arm_speed_var}

    def config_dict(self):
        data = {name: var.get() for name, var in self.param_vars.items()}
        for k, v in self._simple_vars().items():
            data[k] = v.get()
        data['polar_nodes'] = self.polar_nodes
        for ax in self.axes:
            data[f'left_{ax}'] = self.offset_vars[f'left_{ax}'].get()
            data[f'right_{ax}'] = self.offset_vars[f'right_{ax}'].get()
        return data

    def save_config_to(self, path):
        with open(path, 'w') as f:
            json.dump(self.config_dict(), f, indent=2)
        logger.info(f"Configuration saved to {path}")

    def load_config_from(self, path):
        """Load a cylinder preset. Raises ValueError (nothing changed) if the file is wrong."""
        with open(path, 'r') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("This file is not a cylinder preset.")
        targets = dict(self.param_vars)
        targets.update(self._simple_vars())
        for ax in self.axes:
            for side in ('left', 'right'):
                targets[f'{side}_{ax}'] = self.offset_vars[f'{side}_{ax}']
        known = [k for k in data if k in targets or k == 'polar_nodes']
        if not known:
            raise ValueError("No cylinder settings found in this file. Is it a cylinder preset "
                             "(saved with Save config)?")
        bad = []
        for key in known:
            if key == 'polar_nodes':
                continue
            var, val = targets[key], data[key]
            kind = var._kind
            if kind in (float, int):
                if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
                    bad.append(f"{key} = {val!r}")
            elif kind is bool and not isinstance(val, bool):
                bad.append(f"{key} = {val!r}")
        if bad:
            raise ValueError("Nothing was loaded. These values are not valid numbers:\n  " + "\n  ".join(bad))
        for key in known:
            if key == 'polar_nodes':
                self.polar_nodes = [float(t) for t in data['polar_nodes']]
            else:
                targets[key].set(data[key])
        self.nodes_changed.set(self.nodes_changed.get() + 1)
        logger.info(f"Configuration loaded from {path}")

    # ==================================================================
    # Connections
    # ==================================================================
    def _device_class(self, which):
        if self.conn_simulated.get():
            from .sim_hardware import SimArm, SimTurntable, SimExtruder
            return {'arm': SimArm, 'turntable': SimTurntable, 'extruder': SimExtruder}[which]
        try:
            if which == 'arm':
                from src.arm_controller import ArmController as cls
            elif which == 'turntable':
                from src.turntable_controller import TurntableController as cls
            else:
                from src.extruder_controller import ExtruderController as cls
            return cls
        except ImportError as e:
            pkg = {'arm': "xarm-python-sdk", 'turntable': "Aerotech Automation1 (pyautomation)",
                   'extruder': "requests"}[which]
            raise RuntimeError(f"the {pkg} package is not installed on this computer ({e}). "
                               f"Install it, or tick Simulated hardware to rehearse without the cell.")

    def connect_hw(self):
        """Connect the ticked devices. Each is tried independently; failures are listed."""
        if self.printing or self.busy:
            self.ui.warn("Connect", "A job is running. Wait for it to finish before reconnecting.")
            return
        self._close_devices()               # never leave a second session open
        self.estopped = False
        kind = "sim" if self.conn_simulated.get() else "real"
        cfg = self.cfg
        plan = [('left', "left arm", self.conn_left,
                 lambda: self._device_class('arm')(cfg['arms']['left']['ip'], "left")),
                ('right', "right arm", self.conn_right,
                 lambda: self._device_class('arm')(cfg['arms']['right']['ip'], "right")),
                ('turntable', "turntable", self.conn_turntable,
                 lambda: self._device_class('turntable')(host=cfg['turntable']['controller_ip'],
                                                         axis=cfg['turntable']['axis'])),
                ('extruder', "extruder", self.conn_extruder,
                 lambda: self._device_class('extruder')(cfg['moonraker']['host'], cfg['moonraker']['port']))]
        connected, failed, state = [], [], {}
        for attr, name, ticked, make in plan:
            setattr(self, attr, None)
            if not ticked.get():
                continue
            try:
                dev = make()
                if attr != 'extruder':
                    dev.connect()
                setattr(self, attr, dev)
                connected.append(name)
                state[attr] = kind
            except Exception as e:
                failed.append(f"{name}: {e}")
                logger.error("Could not connect %s: %s", name, e)
        if kind == "sim":
            # the simulated arms' true frames: the calibration they were connected with, plus any test error
            from .sim_hardware import FAULTS
            for side in ("left", "right"):
                dev = getattr(self, side)
                if dev is not None:
                    c = [self.param_vars[f"tt_c{a}_{side}"].get() for a in "xyz"]
                    err = FAULTS.get("frame_error", {}).get(side, (0.0, 0.0, 0.0))
                    dev.true_centre = tuple(c[i] + err[i] for i in range(3))
        self.hw_connected = bool(connected)
        self.connected_var.set(state)
        prefix = "Simulated: " if kind == "sim" else "Connected: "
        status = (prefix + ", ".join(connected)) if connected else "Not connected"
        if failed:
            status += "   ·   failed: " + ", ".join(f.split(":")[0] for f in failed)
        if not connected and not failed:
            status = "Nothing selected to connect"
        self.conn_status_var.set(status)
        logger.info(status)
        if failed:
            self.ui.error("Connection problem",
                          "Could not connect:\n\n" + "\n".join(failed) +
                          ("\n\nConnected: " + ", ".join(connected) if connected else ""))

    def _close_devices(self):
        for dev in (self.left, self.right, self.turntable):
            try:
                if dev is not None:
                    dev.disconnect()
            except Exception:
                pass
        self.left = self.right = self.turntable = self.extruder = None
        self.hw_connected = False

    def disconnect_hw(self):
        if self.printing or self.busy:
            self.ui.warn("Disconnect", "A job is running. Stop it first, or use the EMERGENCY STOP.")
            return
        self._close_devices()
        self.conn_status_var.set("Not connected")
        self.connected_var.set({})
        for v in self.tel.values():
            v.set("—")
        logger.info("Disconnected all hardware")

    def _require(self, *devices):
        names = {'left': self.left, 'right': self.right,
                 'turntable': self.turntable, 'extruder': self.extruder}
        missing = [d for d in devices if names.get(d) is None]
        if missing:
            nice = {'left': "left arm", 'right': "right arm"}
            self.ui.warn("Not connected",
                         "This action needs: " + ", ".join(nice.get(m, m) for m in missing) +
                         ".\nTick them in Connections and press Connect.")
            return False
        return True

    def _job_running_message(self, what):
        if self.printing or self.busy or self.model_job_active():
            job = "A model print" if self.model_job_active() else (
                "The cylinder" if self.printing else self.job_state.get().title())
            self.ui.warn(what, f"{job} is still running. Wait for it to finish or press Stop.")
            return True
        return False

    def _require_connected(self, what):
        if not self.hw_connected:
            msg = "Hardware not connected. Tick the devices in Connections and press Connect."
            if self.estopped:
                msg = ("The EMERGENCY STOP disabled the cell. Check it is safe, then press Connect "
                       "to re-enable the arms and turntable.")
            self.ui.warn(what, msg)
            return False
        return True

    # ==================================================================
    # Home (configurable, see home_config.py)
    # ==================================================================
    def home_all(self):
        if not self._require_connected("Home") or self._job_running_message("Home"):
            return
        self._run_busy("homing", self._home_thread)

    def _home_thread(self):
        if self.extruder is not None:
            self.extruder.send_gcode("SET_KINEMATIC_POSITION X=0 Y=0 Z=0")
        n = home_config.home_arms({'left': self.left, 'right': self.right}, self.home)
        logger.info(f"Homed {n} arm(s)")

    def home_one(self, side, cfg):
        arm = self.left if side == 'left' else self.right
        if arm is None:
            self.ui.warn("Home", f"The {side} arm is not connected.")
            return
        if self._job_running_message("Home"):
            return
        problems = home_config.validate({side: cfg}, sides=(side,))
        if problems:
            self.ui.warn("Home", "\n".join(problems))
            return
        self._run_busy("homing", lambda: home_config.go_home(arm, cfg, wait=True))

    def capture_home(self, side):
        arm = self.left if side == 'left' else self.right
        if arm is None:
            return None, None
        joints = arm.get_joints() if hasattr(arm, 'get_joints') else None
        pose = arm.get_pose()
        return (list(joints) if joints else None), (list(pose[:6]) if pose else None)

    def save_home(self, data):
        problems = home_config.validate(data)
        if problems:
            raise ValueError("\n".join(problems))
        self.home = data
        home_config.save(data)
        self.home_load_error = None

    def _run_busy(self, state, fn):
        self.busy = True                      # before the thread starts: no window for a jog to slip in
        self.job_state.set(state)

        def worker():
            try:
                fn()
            except Exception as e:
                if not self.estopped:
                    self.ui.error(state.title() + " error", str(e))
            finally:
                self.busy = False
                self.ui.post(lambda: self.job_state.set("idle"))
        threading.Thread(target=worker, daemon=True).start()

    # ==================================================================
    def prepare_to_print(self):
        if not self._require_connected("Prepare to print") or self._job_running_message("Prepare to print"):
            return
        self._run_busy("preparing", self._prepare_thread)

    def _prepare_thread(self):
        temp = self.cfg['defaults']['temperature']['tool0']
        left, right, ext = self.left, self.right, self.extruder
        RL, PL, YL = self.orientation("left")
        RR, PR, YR = self.orientation("right")
        if ext is not None:
            ext.set_temperature(0, temp, wait=False)
            ext.set_temperature(1, temp, wait=False)
        if left is not None:
            left.arm.set_position(442.5, 225, 160, RL, PL, YL, speed=100, wait=False)
        if right is not None:
            right.arm.set_position(442.5, 230, 172, RR, PR, YR, speed=100, wait=False)
        if ext is not None:
            ext.heat_and_wait(0, temp)
            ext.heat_and_wait(1, temp)
        if left is not None:
            left.arm.set_position(400, 174.4, 155, RL, PL, YL, speed=100, wait=False)
        if right is not None:
            right.arm.set_position(400, 174.4, 155, RR, PR, YR, speed=100, wait=False)
        time.sleep(5)
        if self.estopped:
            return
        if ext is None:
            self.ui.warn("Prepare to print", "Arms are at the pre-print pose, but the extruder is not "
                                             "connected so nothing was heated.")
        else:
            self.ui.info("Prepare to print", f"Ready to print. Both tools at {temp} °C.")

    def calculate_extrusion_lengths(self):
        try:
            radius = self.param_vars['radius'].get()
            pitch = self.param_vars['pitch'].get()
            total_revs = self.param_vars['total_revs'].get()
            line_w = self.param_vars['line_width'].get()
            fil_diam = self.param_vars['filament_diameter'].get()
            left_factor = self.param_vars['extrusion_factor_left'].get()
            right_factor = self.param_vars['extrusion_factor_right'].get()
            left_len = total_revs * math.sqrt((2 * math.pi * radius) ** 2 + pitch ** 2)
            right_len = left_len
            fil_area = math.pi * (fil_diam / 2) ** 2
            fil_left = (left_len * line_w * pitch) / fil_area * left_factor
            fil_right = (right_len * line_w * pitch) / fil_area * right_factor
            self.calc_left_len.set(round(fil_left, 1))
            self.calc_right_len.set(round(fil_right, 1))
            logger.info(f"Calculated filament: Left={fil_left:.1f} mm, Right={fil_right:.1f} mm")
        except Exception as e:
            self.ui.error("Calculation Error", str(e))

    def prime_extruder(self, side):
        if not self._require('extruder'):
            return
        length = self.prime_len.get()
        if side == 'left':
            speed = self.param_vars['feed_rate_left'].get()
            tool = 1
        else:
            speed = self.param_vars['feed_rate_right'].get()
            tool = 0
        self.extruder.extrude(tool, length, speed, wait=False)
        logger.info(f"Primed {side} extruder: {length} mm at {speed} mm/s")

    def set_tool_temperature(self, side):
        """Heat one tool to its panel target (left = tool 1, right = tool 0)."""
        if not self._require('extruder'):
            return
        tool = 1 if side == 'left' else 0
        temp = (self.temp_left if side == 'left' else self.temp_right).get()
        self.extruder.set_temperature(tool, temp, wait=False)
        logger.info(f"{side} extruder (tool {tool}) target set to {temp:.0f} C")

    def heaters_off(self):
        if not self._require('extruder'):
            return
        self.extruder.disable_all_heaters()
        logger.info("All heaters off")

    # ==================================================================
    # Cylinder job
    # ==================================================================
    def cylinder_problems(self):
        """Values that would make the job unsafe or meaningless."""
        p = {k: v.get() for k, v in self.param_vars.items()}
        out = []
        for key in ('total_revs', 'radius', 'pitch', 'line_width', 'filament_diameter',
                    'feed_rate_left', 'feed_rate_right', 'extrusion_factor_left', 'extrusion_factor_right'):
            if not p[key] > 0:
                out.append(f"{PARAM_META[key][0]} must be greater than 0 (it is {p[key]:g}).")
        speed = self.turntable_speed_var.get()
        if not 0 < speed <= self.tt_speed_max:
            out.append(f"Turntable speed must be between 0 and {self.tt_speed_max:g} rad/s (it is {speed:g}).")
        return out

    def flow_timing(self):
        """(extrusion seconds, rotation seconds) for the one move extrusion."""
        speed = self.turntable_speed_var.get()
        rot = self.param_vars['total_revs'].get() * 2 * math.pi / speed if speed > 0 else float('inf')
        fl, fr = self.param_vars['feed_rate_left'].get(), self.param_vars['feed_rate_right'].get()
        ext = max(self.calc_left_len.get() / fl if fl > 0 else float('inf'),
                  self.calc_right_len.get() / fr if fr > 0 else float('inf'))
        return ext, rot

    def match_feed_rates(self):
        _, rot = self.flow_timing()
        if rot > 0 and math.isfinite(rot):
            self.param_vars['feed_rate_left'].set(round(self.calc_left_len.get() / rot, 3))
            self.param_vars['feed_rate_right'].set(round(self.calc_right_len.get() / rot, 3))

    def preflight(self):
        """Summary text and warnings shown before a cylinder starts."""
        self.calculate_extrusion_lengths()
        p = {k: v.get() for k, v in self.param_vars.items()}
        ext_t, rot_t = self.flow_timing()
        lines = [f"{p['total_revs']:g} rev at radius {p['radius']:g} mm, pitch {p['pitch']:g} mm "
                 f"→ wall {p['total_revs'] * p['pitch']:.1f} mm tall",
                 f"Turntable {self.turntable_speed_var.get():g} rad/s → about {_mmss(rot_t)} (mm:ss)",
                 f"Filament L {self.calc_left_len.get():.1f} mm · R {self.calc_right_len.get():.1f} mm",
                 "Wave pattern " + ("on" if self.pattern_enabled.get() else "off"),
                 "Extrusion: " + ("follows the turntable" if self.extrusion_mode.get() == "streamed"
                                  else "one move, fixed rate")]
        warnings = []
        flow_off = False
        if self.extrusion_mode.get() == "single" and rot_t > 0 and math.isfinite(ext_t):
            diff = (ext_t - rot_t) / rot_t
            if abs(diff) > FLOW_TOLERANCE:
                flow_off = True
                warnings.append(f"Extrusion lasts {_mmss(ext_t)} but the turntable needs {_mmss(rot_t)} "
                                f"({diff * 100:+.0f} %). The wall will be "
                                f"{'over' if diff < 0 else 'under'}-extruded at the start and "
                                f"{'run dry' if diff < 0 else 'blob'} at the end. "
                                "Match the feed rates to the turntable?")
        offs = [f"{k.replace('_', ' ')} {v.get():+g}" for k, v in self.offset_vars.items() if v.get() != 0]
        if offs:
            warnings.append("Live offsets are not zero: " + ", ".join(offs) + ".")
        for side in ('left', 'right'):
            if p[f'radial_offset_{side}'] != 0:
                warnings.append(f"Radial offset {side} is {p[f'radial_offset_{side}']:+g} mm.")
        return lines, warnings, flow_off

    def start_cylinder(self, confirmed=False):
        """GUI thread. Checks, shows the pre-flight summary, then starts the job."""
        if self._job_running_message("Start cylinder"):
            return
        if not self._require('left', 'right', 'turntable', 'extruder'):
            return
        problems = self.cylinder_problems()
        if problems:
            self.ui.warn("Cannot start cylinder", "\n".join(problems))
            return
        lines, warnings, flow_off = self.preflight()
        if not confirmed:
            text = "\n".join(lines)
            if warnings:
                text += "\n\nCheck:\n• " + "\n• ".join(warnings)
            buttons = [("Start", "accept")]
            if flow_off:
                buttons = [("Match feed rates and start", "accept"), ("Start as set", "destructive")]
            choice = self.ui.choose("Start cylinder", text, buttons + [("Cancel", "reject")])
            if choice is None or choice == "Cancel":
                return
            if choice == "Match feed rates and start":
                self.match_feed_rates()
        self.printing = True
        self.stop_requested = False
        self.paused = False
        self._speed_notice_given = False
        self.job_name.set("Cylinder")
        self.elapsed_time_var.set("00:00")
        self.job_state.set("running")
        threading.Thread(target=self._cylinder_thread, daemon=True).start()

    def stop_print(self):
        if self.printing:
            self.stop_requested = True

    def toggle_pause(self):
        if not self.printing:
            return
        if not self.paused and self.extrusion_mode.get() == "single":
            if not self.ui.confirm("Pause", "In one move extrusion mode the extruder cannot pause: "
                                            "it keeps pushing filament while the turntable waits, "
                                            "leaving a blob.\n\nPause the turntable anyway?"):
                return
        self.paused = not self.paused
        self.job_state.set("paused" if self.paused else "running")

    def _job_finished(self):
        self.printing = False
        self.paused = False
        self.ui.post(lambda: self.job_state.set("idle"))

    def _cylinder_thread(self):
        # Local handles: an E-stop or disconnect clears self.* but must not crash this thread
        left, right, tt, ext = self.left, self.right, self.turntable, self.extruder
        streamed = self.extrusion_mode.get() == "streamed"
        try:
            def safe(name, var):
                return self.safe_get(var, name)

            total_revs = safe('total_revs', self.param_vars['total_revs'])
            radius = safe('radius', self.param_vars['radius'])
            pitch = safe('pitch', self.param_vars['pitch'])
            z_start = safe('z_start', self.param_vars['z_start'])
            start_angle_deg = safe('start_angle_deg', self.param_vars['start_angle_deg'])
            angular_off_deg = safe('angular_offset_deg', self.param_vars['angular_offset_deg'])
            tt_cx_l = safe('tt_cx_left', self.param_vars['tt_cx_left'])
            tt_cy_l = safe('tt_cy_left', self.param_vars['tt_cy_left'])
            tt_cz_l = safe('tt_cz_left', self.param_vars['tt_cz_left'])
            tt_cx_r = safe('tt_cx_right', self.param_vars['tt_cx_right'])
            tt_cy_r = safe('tt_cy_right', self.param_vars['tt_cy_right'])
            tt_cz_r = safe('tt_cz_right', self.param_vars['tt_cz_right'])
            feed_l = safe('feed_rate_left', self.param_vars['feed_rate_left'])
            feed_r = safe('feed_rate_right', self.param_vars['feed_rate_right'])

            start_angle_rad = math.radians(start_angle_deg)
            angular_off_rad = math.radians(angular_off_deg)

            fil_left = self.calc_left_len.get()
            fil_right = self.calc_right_len.get()

            def nozzle_pos(cx, cy, r, angle_rad, radial_off):
                eff_r = r + radial_off
                x = cx + eff_r * math.cos(angle_rad)
                y = cy + eff_r * math.sin(angle_rad)
                return round(x, 1), round(y, 1)

            def pattern_offset(rev, base_x, base_y, tt_cx, tt_cy):
                if not self.pattern_enabled.get():
                    return 0.0, 0.0
                amp = safe('amp', self.pattern_amplitude)
                wave_count = safe('wave_count', self.pattern_wave_count)
                phase_offset = safe('pattern_phase_offset', self.pattern_phase_offset)
                waveform = self.pattern_waveform.get()
                phase = ((rev * wave_count) % 1.0) * 2 * math.pi + math.radians(phase_offset)
                val = self.wave_value(phase, amp, waveform)
                dx = base_x - tt_cx
                dy = base_y - tt_cy
                length = math.hypot(dx, dy)
                if length < 0.001:
                    return 0.0, 0.0
                ux, uy = dx / length, dy / length
                return ux * val, uy * val

            rad_off_l = safe('radial_offset_left', self.param_vars['radial_offset_left'])
            rad_off_r = safe('radial_offset_right', self.param_vars['radial_offset_right'])
            right_base_x, right_base_y = nozzle_pos(tt_cx_r, tt_cy_r, radius, start_angle_rad, rad_off_r)
            left_base_x, left_base_y = nozzle_pos(tt_cx_l, tt_cy_l, radius, start_angle_rad + angular_off_rad, rad_off_l)

            park_x, park_y, park_z = 400.0, 174.0, 180.0
            safe_z = 250.0
            RR, PR, YR = self.orientation("right")     # per arm: the new mounts face each other
            RL, PL, YL = self.orientation("left")
            left_yaw_off = safe('left_yaw', self.offset_vars['left_Yaw'])

            approach = [
                lambda: right.arm.set_position(park_x, park_y, park_z, RR, PR, YR, speed=100, wait=True),
                lambda: left.arm.set_position(park_x, park_y, park_z, RL, PL, YL + left_yaw_off, speed=100, wait=True),
                lambda: right.move_to(park_x, park_y, safe_z, RR, PR, YR, speed=100, wait=True),
                lambda: left.move_to(park_x, park_y, safe_z, RL, PL, YL + left_yaw_off, speed=100, wait=True),
                lambda: right.move_to(right_base_x, right_base_y, z_start + tt_cz_r, RR, PR, YR, speed=50, wait=True),
                lambda: left.move_to(left_base_x, left_base_y, z_start + tt_cz_l, RL, PL, YL + left_yaw_off, speed=50, wait=True),
            ]
            for step in approach:
                if self.stop_requested:
                    break
                step()

            aborted_early = self.stop_requested
            if not aborted_early and not streamed:
                ext.extrude_sync(fil_left, feed_l, fil_right, feed_r, wait=False)
                logger.info("Extrusion started")

            last_speed_rad = safe('turntable_speed', self.turntable_speed_var)
            start_angle_deg_tt = tt.get_angle()
            target_angle_deg_tt = start_angle_deg_tt + total_revs * 360.0
            start_angle_rad_tt = math.radians(start_angle_deg_tt)
            if not aborted_early:
                tt.rotate_velocity(math.degrees(last_speed_rad))
                total_time_sec = total_revs * 2 * math.pi / last_speed_rad if last_speed_rad > 0 else 0
                self.ui.post(lambda t=total_time_sec: self.remaining_time_var.set(_mmss(t)))

            CMD_INTERVAL = 0.1
            last_cmd_time = time.time()
            start_wall_time = time.time()
            rev_extruded = 0.0

            waveform_speed_factor = {'sine': 0.8, 'triangle': 1.0, 'square': 2.0}

            while not self.stop_requested:
                while self.paused and not self.stop_requested:
                    tt.stop_rotation()
                    time.sleep(0.1)
                if self.stop_requested:
                    break
                if self.paused == False:
                    tt.rotate_velocity(math.degrees(last_speed_rad))

                cur_speed_rad = safe('turntable_speed', self.turntable_speed_var)
                if cur_speed_rad != last_speed_rad:
                    tt.rotate_velocity(math.degrees(cur_speed_rad))
                    last_speed_rad = cur_speed_rad
                    total_time_sec = total_revs * 2 * math.pi / cur_speed_rad if cur_speed_rad > 0 else 0
                    self.ui.post(lambda t=total_time_sec: self.remaining_time_var.set(_mmss(t)))

                now = time.time()
                if now - last_cmd_time < CMD_INTERVAL:
                    time.sleep(0.01)
                    continue
                last_cmd_time = now

                act_deg = tt.get_angle()
                if act_deg >= target_angle_deg_tt:
                    break
                act_rad = math.radians(act_deg)
                rev = (act_rad - start_angle_rad_tt) / (2 * math.pi)
                z_now = z_start + rev * pitch

                if streamed and last_speed_rad > 0:
                    # queue filament for the next few degrees only, at the rate the turntable is turning
                    target_rev = min(total_revs, max(rev, 0.0) + STREAM_CHUNK_REV)
                    if target_rev - rev_extruded > STREAM_CHUNK_REV * 0.5:
                        d = target_rev - rev_extruded
                        secs = d * 2 * math.pi / last_speed_rad
                        l_amt, r_amt = fil_left * d / total_revs, fil_right * d / total_revs
                        ext.extrude_sync(l_amt, max(l_amt / secs, 1e-3), r_amt, max(r_amt / secs, 1e-3), wait=False)
                        rev_extruded = target_rev

                rad_off_l = safe('radial_offset_left', self.param_vars['radial_offset_left'])
                rad_off_r = safe('radial_offset_right', self.param_vars['radial_offset_right'])
                tt_cz_l = safe('tt_cz_left', self.param_vars['tt_cz_left'])
                tt_cz_r = safe('tt_cz_right', self.param_vars['tt_cz_right'])

                right_base_x, right_base_y = nozzle_pos(tt_cx_r, tt_cy_r, radius, start_angle_rad, rad_off_r)
                left_base_x, left_base_y = nozzle_pos(tt_cx_l, tt_cy_l, radius, start_angle_rad + angular_off_rad, rad_off_l)

                if self.pattern_arm_left.get():
                    pat_dx_l, pat_dy_l = pattern_offset(rev, left_base_x, left_base_y, tt_cx_l, tt_cy_l)
                else:
                    pat_dx_l, pat_dy_l = 0, 0
                if self.pattern_arm_right.get():
                    pat_dx_r, pat_dy_r = pattern_offset(rev, right_base_x, right_base_y, tt_cx_r, tt_cy_r)
                else:
                    pat_dx_r, pat_dy_r = 0, 0

                lo = [safe(f'left_{ax}', self.offset_vars[f'left_{ax}']) for ax in self.axes]
                ro = [safe(f'right_{ax}', self.offset_vars[f'right_{ax}']) for ax in self.axes]

                left_x = left_base_x + pat_dx_l + lo[0]
                left_y = left_base_y + pat_dy_l + lo[1]
                left_z = z_now + tt_cz_l + lo[2]
                right_x = right_base_x + pat_dx_r + ro[0]
                right_y = right_base_y + pat_dy_r + ro[1]
                right_z = z_now + tt_cz_r + ro[2]

                wave_count = safe('wave_count', self.pattern_wave_count)
                amp = safe('amp', self.pattern_amplitude)
                revs_per_sec = last_speed_rad / (2 * math.pi)
                max_radial_speed = amp * 2 * math.pi * wave_count * revs_per_sec
                tangential_speed = 2 * math.pi * radius * revs_per_sec
                z_speed = pitch * revs_per_sec
                required_speed = math.sqrt(max_radial_speed ** 2 + tangential_speed ** 2 + z_speed ** 2)

                base_arm_speed = safe('base_arm_speed', self.base_arm_speed_var)
                waveform = self.pattern_waveform.get()
                factor = waveform_speed_factor.get(waveform, 1.0)
                arm_speed = max(base_arm_speed * factor, required_speed * factor)

                left.move_to(left_x, left_y, left_z,
                             roll=RL + lo[3], pitch=PL + lo[4], yaw=YL + lo[5],
                             speed=arm_speed, wait=False)
                right.move_to(right_x, right_y, right_z,
                              roll=RR + ro[3], pitch=PR + ro[4], yaw=YR + ro[5],
                              speed=arm_speed, wait=False)

                elapsed = time.time() - start_wall_time
                self.ui.post(lambda e=elapsed: self.elapsed_time_var.set(_mmss(e)))
                done_fraction = (act_deg - start_angle_deg_tt) / (target_angle_deg_tt - start_angle_deg_tt)
                remaining = (elapsed / done_fraction) - elapsed if done_fraction > 0 else 0
                self.ui.post(lambda r=remaining: self.remaining_time_var.set(_mmss(r)))

            if self.estopped:
                self._job_finished()          # E-stop: command nothing more
                logger.info("Cylinder aborted by EMERGENCY STOP.")
                return

            tt.stop_rotation()
            time.sleep(0.5)
            ext.send_gcode("M18 E X")
            lo = [safe(f'left_{ax}', self.offset_vars[f'left_{ax}']) for ax in self.axes]
            right.arm.set_position(park_x, park_y, park_z, RR, PR, YR, speed=100, wait=True)
            left.arm.set_position(park_x, park_y, park_z, RL, PL, YL + lo[5], speed=100, wait=True)
            stopped = self.stop_requested
            self._job_finished()
            logger.info("Cylinder stopped." if stopped else "Cylinder finished.")
            if stopped and not aborted_early and not streamed and ext is not None:
                self.ui.warn("Cylinder stopped",
                             "The arms are parked, but the extruder may still be running the rest of the "
                             "one move extrusion. Press Extruders off (sends CANCEL_PRINT) to stop it.")

        except Exception as e:
            self._job_finished()
            if self.estopped:
                return
            try:
                if tt is not None:
                    tt.stop_rotation()
            except Exception:
                pass
            extra = ""
            if ext is not None and not streamed:
                extra = ("\n\nThe extruder may still be running the queued extrusion. "
                         "Press Extruders off to stop it.")
            self.ui.error("Print error", f"{e}{extra}")

    # ==================================================================
    def jog(self, axis, direction):
        if self.printing:
            self.ui.warn("Jog disabled", "Cannot jog during a print.")
            return
        if self.busy:
            self.ui.warn("Jog disabled", f"Cannot jog while {self.job_state.get()}.")
            return
        if not self.hw_connected:
            return
        arm_sel = self.jog_arm.get()
        delta = direction * self.jog_step.get()
        left_pose = self.left.get_pose() if self.left else None
        right_pose = self.right.get_pose() if self.right else None
        idx = self.axes.index(axis)
        if arm_sel in ('left', 'both') and left_pose:
            cmd = list(left_pose[:6])
            cmd[idx] = left_pose[idx] + delta
            self.left.arm.set_position(*cmd, speed=50, wait=False)
        if arm_sel in ('right', 'both') and right_pose:
            cmd = list(right_pose[:6])
            cmd[idx] = right_pose[idx] + delta
            self.right.arm.set_position(*cmd, speed=50, wait=False)

    def extruders_off(self):
        if self.extruder is None:
            return
        self.extruder.send_gcode("CANCEL_PRINT")

    def emergency_stop(self):
        """Stop everything. Each step runs even if an earlier one fails."""
        self.estopped = True
        self.stop_requested = True
        left, right, tt, ext = self.left, self.right, self.turntable, self.extruder
        steps = [("turntable stop", lambda: tt and tt.stop_rotation()),
                 ("left arm stop", lambda: left and left.emergency_stop()),
                 ("right arm stop", lambda: right and right.emergency_stop()),
                 ("turntable disable", lambda: tt and tt.disconnect()),
                 ("heaters off", lambda: ext and ext.disable_all_heaters())]
        failed = []
        for name, fn in steps:
            try:
                fn()
            except Exception as e:
                failed.append(f"{name}: {e}")
        for arm in (left, right):
            try:
                if arm is not None:
                    arm.disconnect()
            except Exception:
                pass
        self.left = self.right = self.turntable = self.extruder = None
        self.hw_connected = False
        self.connected_var.set({})
        self.conn_status_var.set("EMERGENCY STOP. Check the cell, then press Connect to carry on.")
        for v in self.tel.values():
            v.set("—")
        logger.info("EMERGENCY STOP ACTIVATED")
        if failed:
            self.ui.error("Emergency stop", "Some devices did not confirm the stop. Use the hardware "
                                            "E-stop buttons:\n\n" + "\n".join(failed))

    # ==================================================================
    # Telemetry poller (background thread, posts text to the UI)
    # ==================================================================
    def _poll_loop(self):
        """Every 0.1 s: arm poses / joints and the turntable angle into self.live (numbers, for
        the 3D view). Every 1 s: the text telemetry and temperatures for the live panel."""
        self.live = {}
        tick = 0
        while not self._poll_stop.wait(0.1):
            if not self.hw_connected:
                self.live = {}
                continue
            live = {}
            for side, arm in (('left', self.left), ('right', self.right)):
                if arm is None:
                    continue
                try:
                    p = arm.get_pose()
                    if p:
                        live[f'{side}_pose'] = [float(v) for v in p[:6]]
                    if not self.conn_simulated.get() and hasattr(arm, "get_joints"):
                        j = arm.get_joints()
                        if j:
                            live[f'{side}_joints'] = [float(v) for v in j[:6]]
                except Exception:
                    pass
            if self.turntable is not None:
                try:
                    live['tt_deg'] = float(self.turntable.get_angle())
                except Exception:
                    pass
            self.live = live
            tick += 1
            if tick % 10:
                continue
            out = {}
            for side, arm in (('left', self.left), ('right', self.right)):
                if arm is None:
                    out[f'{side}_pos'], out[f'{side}_rot'] = "not connected", ""
                    continue
                try:
                    p = arm.get_pose()
                    if p:
                        out[f'{side}_pos'] = f"X {p[0]:7.1f}  Y {p[1]:7.1f}  Z {p[2]:7.1f}"
                        out[f'{side}_rot'] = f"R {p[3]:7.1f}  P {p[4]:7.1f}  Y {p[5]:7.1f}" if len(p) >= 6 else ""
                    else:
                        out[f'{side}_pos'], out[f'{side}_rot'] = "no response", ""
                except Exception:
                    out[f'{side}_pos'], out[f'{side}_rot'] = "no response", ""
            ext = self.extruder
            if ext is not None:
                try:
                    status = ext.get_printer_status()
                    out['t0'] = f"{status.get('extruder', {}).get('temperature', 0.0):.1f} °C"
                    out['t1'] = f"{status.get('heater_bed', {}).get('temperature', 0.0):.1f} °C"
                except Exception:
                    out['t0'] = out['t1'] = "no response"
            tt = self.turntable
            if tt is not None:
                try:
                    out['turntable'] = f"{tt.get_angle():.1f}°"
                except Exception:
                    out['turntable'] = "no response"
            else:
                out['turntable'] = "not connected"
            if self.hw_connected:
                self.ui.post(lambda o=out: [self.tel[k].set(v) for k, v in o.items()])

    def shutdown(self):
        """Window closing: stop any job, stop the turntable, release devices."""
        self._poll_stop.set()
        if self.printing or self.busy:
            self.stop_requested = True
            try:
                if self.turntable is not None:
                    self.turntable.stop_rotation()
            except Exception:
                pass
            t = time.time()
            while self.printing and time.time() - t < 3:
                time.sleep(0.05)
        for dev in (self.left, self.right, self.turntable):
            try:
                if dev is not None:
                    dev.disconnect()
            except Exception:
                pass
