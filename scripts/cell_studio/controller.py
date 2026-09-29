"""Machine logic for the cell, independent of any widgets.

Ported from scripts/gleadell_panel.py. The connect, prepare, cylinder print,
jog, prime and emergency stop routines keep the same moves, speeds, poses and
order as the Tk panel; only the plumbing changed:

* tk variables  -> ``Var`` (same get/set API)
* root.after(0) -> ``ui.post``
* messagebox    -> ``ui.info/warn/error`` (safe from worker threads)
* pause_btn.config(text=...) -> ``job_state`` Var the UI listens to

Deliberate changes (flagged so they are easy to review):
* Home uses the configurable home positions (home_config.py) and runs off the
  GUI thread instead of freezing the window.
* Live status polling runs on a background thread for the same reason.
* Hardware SDKs are imported only when you press Connect, so the panel opens
  on a laptop without xarm/pyautomation installed.
* A "Simulated hardware" connection option.
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

# (key, label, unit, decimals, step, section, tooltip)
PARAM_META = {
    'radius':                 ("Radius", "mm", 2, 0.5, "geometry", None),
    'z_start':                ("Z start", "mm", 2, 0.1, "geometry", "Height of the first layer above the turntable centre Z"),
    'pitch':                  ("Pitch", "mm", 3, 0.05, "geometry", "Z rise per turntable revolution (layer height in vase mode)"),
    'total_revs':             ("Total revolutions", "rev", 1, 1.0, "geometry", None),
    'line_width':             ("Line width", "mm", 2, 0.05, "geometry", None),
    'filament_diameter':      ("Filament diameter", "mm", 2, 0.05, "extrusion", None),
    'feed_rate_left':         ("Feed rate · left", "mm/s", 2, 0.1, "extrusion", None),
    'feed_rate_right':        ("Feed rate · right", "mm/s", 2, 0.1, "extrusion", None),
    'extrusion_factor_left':  ("Extrusion factor · left", "×", 3, 0.01, "extrusion", None),
    'extrusion_factor_right': ("Extrusion factor · right", "×", 3, 0.01, "extrusion", None),
    'start_angle_deg':        ("Start angle", "°", 1, 1.0, "placement", "Angle on the disc where the right nozzle starts"),
    'angular_offset_deg':     ("Angular offset · left", "°", 1, 1.0, "placement", "Left nozzle start angle relative to the right nozzle"),
    'radial_offset_left':     ("Radial offset · left", "mm", 2, 0.1, "placement", "Read live during a print"),
    'radial_offset_right':    ("Radial offset · right", "mm", 2, 0.1, "placement", "Read live during a print"),
    'tt_cx_left':             ("Centre X · left", "mm", 1, 0.1, "centre", None),
    'tt_cy_left':             ("Centre Y · left", "mm", 1, 0.1, "centre", None),
    'tt_cz_left':             ("Centre Z · left", "mm", 1, 0.1, "centre", "Read live during a print"),
    'tt_cx_right':            ("Centre X · right", "mm", 1, 0.1, "centre", None),
    'tt_cy_right':            ("Centre Y · right", "mm", 1, 0.1, "centre", None),
    'tt_cz_right':            ("Centre Z · right", "mm", 1, 0.1, "centre", "Read live during a print"),
}


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
        self.busy = False  # homing / preparing

        # Which devices to connect / drive. The left arm has no extruder on the
        # current machine, so it can be left disconnected entirely.
        self.conn_left = BoolVar(False)
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
            'pitch':              DoubleVar(0.4),
            'total_revs':         DoubleVar(20.0),
            'start_angle_deg':    DoubleVar(135.0),
            'line_width':         DoubleVar(0.4),
            'filament_diameter':  DoubleVar(1.75),
            'feed_rate_left':     DoubleVar(4.0),
            'feed_rate_right':    DoubleVar(4.0),
            'extrusion_factor_left':  DoubleVar(1.0),
            'extrusion_factor_right': DoubleVar(1.0),
            'tt_cx_left':         DoubleVar(574.1),
            'tt_cy_left':         DoubleVar(-5.4),
            'tt_cz_left':         DoubleVar(153),
            'tt_cx_right':        DoubleVar(567.7),
            'tt_cy_right':        DoubleVar(6.7),
            'tt_cz_right':        DoubleVar(151.1),
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

        # Jog controls
        self.jog_arm = StrVar('left')
        self.jog_step = DoubleVar(1.0)

        # Extruder panel
        self.calc_left_len = DoubleVar(0.0)
        self.calc_right_len = DoubleVar(0.0)
        self.prime_len = DoubleVar(20.0)

        # Job status
        self.elapsed_time_var = StrVar("00:00")
        self.remaining_time_var = StrVar("00:00")
        self.job_state = StrVar("idle")      # idle | running | paused | homing | preparing
        self.job_name = StrVar("")

        self.calc_mms_var = DoubleVar(10.0)

        # Polar preview nodes
        self.polar_nodes = [i * (2 * math.pi / 36) for i in range(36)]
        self.nodes_changed = Var(0, int)

        # Telemetry (filled by the poller)
        self.tel = {k: StrVar("—") for k in
                    ("left_pos", "left_rot", "right_pos", "right_rot", "turntable", "t0", "t1")}

        self.home = home_config.load()

        self._poll_stop = threading.Event()
        threading.Thread(target=self._poll_loop, daemon=True, name="telemetry").start()

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
        """Bump the live turntable speed by delta, clamped to [0, tt_speed_max]."""
        try:
            new_val = self.turntable_speed_var.get() + delta
        except (ValueError, TypeError):
            new_val = 0.0
        self.turntable_speed_var.set(max(0.0, min(self.tt_speed_max, round(new_val, 3))))

    def calc_rads_from_mms(self):
        mms = self.calc_mms_var.get()
        radius = self.param_vars['radius'].get()
        rads = mms / radius if radius > 0 else 0.0
        self.turntable_speed_var.set(round(rads, 4))

    # ---------------- preview nodes ----------------
    def reset_nodes(self):
        self.polar_nodes = [i * (2 * math.pi / 36) for i in range(36)]
        self.nodes_changed.set(self.nodes_changed.get() + 1)

    def toggle_node(self, theta):
        """Remove a node near theta, otherwise add one (same rule as the Tk canvas click)."""
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
    def config_dict(self):
        data = {name: var.get() for name, var in self.param_vars.items()}
        data['pattern_enabled'] = self.pattern_enabled.get()
        data['pattern_waveform'] = self.pattern_waveform.get()
        data['pattern_amplitude'] = self.pattern_amplitude.get()
        data['pattern_wave_count'] = self.pattern_wave_count.get()
        data['pattern_phase_offset'] = self.pattern_phase_offset.get()
        data['pattern_arm_left'] = self.pattern_arm_left.get()
        data['pattern_arm_right'] = self.pattern_arm_right.get()
        data['turntable_speed'] = self.turntable_speed_var.get()
        data['base_arm_speed'] = self.base_arm_speed_var.get()
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
        with open(path, 'r') as f:
            data = json.load(f)
        for name, var in self.param_vars.items():
            if name in data:
                var.set(data[name])
        simple = {'pattern_enabled': self.pattern_enabled, 'pattern_waveform': self.pattern_waveform,
                  'pattern_amplitude': self.pattern_amplitude, 'pattern_wave_count': self.pattern_wave_count,
                  'pattern_phase_offset': self.pattern_phase_offset, 'pattern_arm_left': self.pattern_arm_left,
                  'pattern_arm_right': self.pattern_arm_right, 'turntable_speed': self.turntable_speed_var,
                  'base_arm_speed': self.base_arm_speed_var}
        for key, var in simple.items():
            if key in data:
                var.set(data[key])
        if 'polar_nodes' in data:
            self.polar_nodes = data['polar_nodes']
        for ax in self.axes:
            for side in ('left', 'right'):
                key = f'{side}_{ax}'
                if key in data:
                    self.offset_vars[key].set(data[key])
        self.nodes_changed.set(self.nodes_changed.get() + 1)
        logger.info(f"Configuration loaded from {path}")

    # ==================================================================
    # Connections
    # ==================================================================
    def _classes(self):
        if self.conn_simulated.get():
            from .sim_hardware import SimArm, SimTurntable, SimExtruder
            return SimArm, SimTurntable, SimExtruder
        from src.arm_controller import ArmController
        from src.turntable_controller import TurntableController
        from src.extruder_controller import ExtruderController
        return ArmController, TurntableController, ExtruderController

    def connect_hw(self):
        """Connect only the devices ticked in the Connections panel."""
        connected = []
        kind = "sim" if self.conn_simulated.get() else "real"
        state = {}
        try:
            ArmController, TurntableController, ExtruderController = self._classes()
            if self.conn_left.get():
                self.left = ArmController(self.cfg['arms']['left']['ip'], "left")
                self.left.connect()
                connected.append("left arm"); state['left'] = kind
            else:
                self.left = None

            if self.conn_right.get():
                self.right = ArmController(self.cfg['arms']['right']['ip'], "right")
                self.right.connect()
                connected.append("right arm"); state['right'] = kind
            else:
                self.right = None

            if self.conn_turntable.get():
                self.turntable = TurntableController(host=self.cfg['turntable']['controller_ip'],
                                                     axis=self.cfg['turntable']['axis'])
                self.turntable.connect()
                connected.append("turntable"); state['turntable'] = kind
            else:
                self.turntable = None

            if self.conn_extruder.get():
                self.extruder = ExtruderController(self.cfg['moonraker']['host'],
                                                   self.cfg['moonraker']['port'])
                connected.append("extruder"); state['extruder'] = kind
            else:
                self.extruder = None

            self.hw_connected = bool(connected)
            prefix = "Simulated: " if kind == "sim" else "Connected: "
            status = prefix + ", ".join(connected) if connected else "Nothing selected to connect"
            self.conn_status_var.set(status)
            self.connected_var.set(state)
            logger.info(status)
        except Exception as e:
            self.connected_var.set(state)
            self.conn_status_var.set(f"Connection failed: {e}")
            self.ui.error("Connection failed", str(e))

    def disconnect_hw(self):
        for dev in (self.left, self.right, self.turntable):
            try:
                if dev is not None:
                    dev.disconnect()
            except Exception:
                pass
        self.left = self.right = self.turntable = self.extruder = None
        self.hw_connected = False
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
            self.ui.warn("Not connected",
                         "This action needs: " + ", ".join(missing) +
                         ".\nSwitch them on in Connections and press Connect.")
            return False
        return True

    # ==================================================================
    # Home (configurable, see home_config.py)
    # ==================================================================
    def home_all(self):
        if not self.hw_connected:
            self.ui.warn("Home", "Hardware not connected.")
            return
        if self.printing or self.busy:
            self.ui.warn("Home", "Wait for the current job to finish.")
            return
        self._run_busy("homing", self._home_thread)

    def _home_thread(self):
        if self.extruder is not None:
            self.extruder.send_gcode("SET_KINEMATIC_POSITION X=0 Y=0 Z=0")
        n = home_config.home_arms({'left': self.left, 'right': self.right}, self.home)
        logger.info(f"Homed {n} arm(s)")

    def home_one(self, side, cfg):
        """Test move for the Home positions dialog."""
        arm = self.left if side == 'left' else self.right
        if arm is None:
            self.ui.warn("Home", f"The {side} arm is not connected.")
            return
        if self.printing or self.busy:
            self.ui.warn("Home", "Wait for the current job to finish.")
            return
        self._run_busy("homing", lambda: home_config.go_home(arm, cfg, wait=True))

    def capture_home(self, side):
        """Return (joints, pose) of a connected arm, for 'Use current position'."""
        arm = self.left if side == 'left' else self.right
        if arm is None:
            return None, None
        joints = arm.get_joints() if hasattr(arm, 'get_joints') else None
        pose = arm.get_pose()
        return (list(joints) if joints else None), (list(pose[:6]) if pose else None)

    def save_home(self, data):
        self.home = data
        home_config.save(data)

    def _run_busy(self, state, fn):
        def worker():
            self.busy = True
            self.ui.post(lambda: self.job_state.set(state))
            try:
                fn()
            except Exception as e:
                self.ui.error(state.title() + " error", str(e))
            finally:
                self.busy = False
                self.ui.post(lambda: self.job_state.set("idle"))
        threading.Thread(target=worker, daemon=True).start()

    # ==================================================================
    def prepare_to_print(self):
        if not self.hw_connected:
            self.ui.warn("Warning", "Hardware not connected.")
            return
        if self.printing or self.busy:
            return
        self._run_busy("preparing", self._prepare_thread)

    def _prepare_thread(self):
        temp = self.cfg['defaults']['temperature']['tool0']
        if self.extruder is not None:
            self.extruder.set_temperature(0, temp, wait=False)
            self.extruder.set_temperature(1, temp, wait=False)
        if self.left is not None:
            self.left.arm.set_position(442.5, 225, 160, 180, 45, 0, speed=100, wait=False)
        if self.right is not None:
            self.right.arm.set_position(442.5, 230, 172, 180, 45, 0, speed=100, wait=False)
        if self.extruder is not None:
            self.extruder.heat_and_wait(0, temp)
            self.extruder.heat_and_wait(1, temp)
        if self.left is not None:
            self.left.arm.set_position(400, 174.4, 155, 180, 45, 20, speed=100, wait=False)
        if self.right is not None:
            self.right.arm.set_position(400, 174.4, 155, 180, 45, 20, speed=100, wait=False)
        time.sleep(5)
        self.ui.info("Info", "Ready to print.")

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

    # ==================================================================
    # Cylinder job
    # ==================================================================
    def start_cylinder(self):
        # The cylinder routine is the original dual-arm coordinated print and
        # expects both arms, the turntable and the extruder.
        if not self._require('left', 'right', 'turntable', 'extruder'):
            return
        if self.printing or self.busy or self.job_state.get() != "idle":
            return
        if self.calc_left_len.get() == 0 or self.calc_right_len.get() == 0:
            self.calculate_extrusion_lengths()
        self.printing = True
        self.stop_requested = False
        self.paused = False
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
        self.paused = not self.paused
        self.job_state.set("paused" if self.paused else "running")

    def _job_finished(self):
        self.printing = False
        self.paused = False
        self.ui.post(lambda: self.job_state.set("idle"))

    def _cylinder_thread(self):
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
            base_yaw = 20.0
            left_yaw_off = safe('left_yaw', self.offset_vars['left_Yaw'])

            self.right.arm.set_position(park_x, park_y, park_z, 180, 45, base_yaw, speed=100, wait=True)
            self.left.arm.set_position(park_x, park_y, park_z, 180, 45, base_yaw + left_yaw_off, speed=100, wait=True)
            self.right.move_to(park_x, park_y, safe_z, 180, 45, base_yaw, speed=100, wait=True)
            self.left.move_to(park_x, park_y, safe_z, 180, 45, base_yaw + left_yaw_off, speed=100, wait=True)
            self.right.move_to(right_base_x, right_base_y, z_start + tt_cz_r, 180, 45, base_yaw, speed=50, wait=True)
            self.left.move_to(left_base_x, left_base_y, z_start + tt_cz_l, 180, 45, base_yaw + left_yaw_off, speed=50, wait=True)

            self.extruder.extrude_sync(fil_left, feed_l, fil_right, feed_r, wait=False)
            logger.info("Extrusion started")

            turnt_speed_rad = safe('turntable_speed', self.turntable_speed_var)
            turnt_speed_deg = math.degrees(turnt_speed_rad)
            start_angle_deg_tt = self.turntable.get_angle()
            target_angle_deg_tt = start_angle_deg_tt + total_revs * 360.0
            self.turntable.rotate_velocity(turnt_speed_deg)
            last_speed_rad = turnt_speed_rad
            start_angle_rad_tt = math.radians(start_angle_deg_tt)

            total_time_sec = total_revs * 2 * math.pi / turnt_speed_rad if turnt_speed_rad > 0 else 0
            self.ui.post(lambda t=total_time_sec: self.remaining_time_var.set(_mmss(t)))

            CMD_INTERVAL = 0.1
            last_cmd_time = time.time()
            start_wall_time = time.time()

            waveform_speed_factor = {'sine': 0.8, 'triangle': 1.0, 'square': 2.0}

            while not self.stop_requested:
                while self.paused and not self.stop_requested:
                    self.turntable.stop_rotation()
                    time.sleep(0.1)
                if self.stop_requested:
                    break
                if self.paused == False:
                    self.turntable.rotate_velocity(math.degrees(last_speed_rad))

                cur_speed_rad = safe('turntable_speed', self.turntable_speed_var)
                if cur_speed_rad != last_speed_rad:
                    self.turntable.rotate_velocity(math.degrees(cur_speed_rad))
                    last_speed_rad = cur_speed_rad
                    total_time_sec = total_revs * 2 * math.pi / cur_speed_rad if cur_speed_rad > 0 else 0
                    self.ui.post(lambda t=total_time_sec: self.remaining_time_var.set(_mmss(t)))

                now = time.time()
                if now - last_cmd_time < CMD_INTERVAL:
                    time.sleep(0.01)
                    continue
                last_cmd_time = now

                act_deg = self.turntable.get_angle()
                if act_deg >= target_angle_deg_tt:
                    break
                act_rad = math.radians(act_deg)
                rev = (act_rad - start_angle_rad_tt) / (2 * math.pi)
                z_now = z_start + rev * pitch

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

                self.left.move_to(left_x, left_y, left_z,
                                  roll=180 + lo[3], pitch=45 + lo[4], yaw=base_yaw + lo[5],
                                  speed=arm_speed, wait=False)
                self.right.move_to(right_x, right_y, right_z,
                                   roll=180 + ro[3], pitch=45 + ro[4], yaw=base_yaw + ro[5],
                                   speed=arm_speed, wait=False)

                elapsed = time.time() - start_wall_time
                self.ui.post(lambda e=elapsed: self.elapsed_time_var.set(_mmss(e)))
                done_fraction = (act_deg - start_angle_deg_tt) / (target_angle_deg_tt - start_angle_deg_tt)
                remaining = (elapsed / done_fraction) - elapsed if done_fraction > 0 else 0
                self.ui.post(lambda r=remaining: self.remaining_time_var.set(_mmss(r)))

            self.turntable.stop_rotation()
            time.sleep(0.5)
            self.extruder.send_gcode("M18 E X")
            lo = [safe(f'left_{ax}', self.offset_vars[f'left_{ax}']) for ax in self.axes]
            self.right.arm.set_position(park_x, park_y, park_z, 180, 45, base_yaw, speed=100, wait=True)
            self.left.arm.set_position(park_x, park_y, park_z, 180, 45, base_yaw + lo[5], speed=100, wait=True)
            self._job_finished()
            logger.info("Cylinder finished.")

        except Exception as e:
            self._job_finished()
            if self.turntable:
                try:
                    self.turntable.stop_rotation()
                except Exception:
                    pass
            self.ui.error("Print Error", str(e))

    # ==================================================================
    def jog(self, axis, direction):
        if self.printing:
            self.ui.warn("Jog disabled", "Cannot jog during a print.")
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
        self.stop_requested = True
        if self.turntable:
            self.turntable.stop_rotation()
        if self.left: self.left.emergency_stop()
        if self.right: self.right.emergency_stop()
        if self.turntable: self.turntable.disconnect()
        if self.extruder: self.extruder.disable_all_heaters()
        logger.info("EMERGENCY STOP ACTIVATED")

    # ==================================================================
    # Telemetry poller (background thread, posts text to the UI)
    # ==================================================================
    def _poll_loop(self):
        while not self._poll_stop.wait(1.0):
            if not self.hw_connected:
                continue
            out = {}
            try:
                for side, arm in (('left', self.left), ('right', self.right)):
                    if arm is None:
                        out[f'{side}_pos'] = "not connected"
                        out[f'{side}_rot'] = ""
                        continue
                    p = arm.get_pose()
                    if p:
                        out[f'{side}_pos'] = f"X {p[0]:7.1f}  Y {p[1]:7.1f}  Z {p[2]:7.1f}"
                        if len(p) >= 6:
                            out[f'{side}_rot'] = f"R {p[3]:7.1f}  P {p[4]:7.1f}  Y {p[5]:7.1f}"
                if self.extruder is not None:
                    status = self.extruder.get_printer_status()
                    out['t0'] = f"{status.get('extruder', {}).get('temperature', 0.0):.1f} °C"
                    out['t1'] = f"{status.get('heater_bed', {}).get('temperature', 0.0):.1f} °C"
                if self.turntable is not None:
                    out['turntable'] = f"{self.turntable.get_angle():.1f}°"
                else:
                    out['turntable'] = "not connected"
            except Exception:
                pass
            if out:
                self.ui.post(lambda o=out: [self.tel[k].set(v) for k, v in o.items()])

    def shutdown(self):
        self._poll_stop.set()
        if self.printing:
            self.stop_requested = True
            time.sleep(0.5)
        for dev in (self.left, self.right, self.turntable):
            try:
                if dev is not None:
                    dev.disconnect()
            except Exception:
                pass
