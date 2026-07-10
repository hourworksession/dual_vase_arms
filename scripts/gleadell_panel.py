#!/usr/bin/env python3
import sys, os, time, math, threading, logging, json
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from src.arm_controller import ArmController
from src.turntable_controller import TurntableController
from src.extruder_controller import ExtruderController
from config_loader import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("control_panel")

class ControlPanel:
    def __init__(self, root):
        self.root = root
        self.root.title("Live Cylinder Print Control")
        self.root.geometry("1200x850")
        self.cfg = load_config()
        self.hw_connected = False
        self.left = self.right = self.turntable = self.extruder = None
        self.printing = False
        self.stop_requested = False
        self.paused = False

        # Which devices to connect / drive. The left arm has no extruder on the
        # current machine, so it can be left disconnected entirely.
        self.conn_left = tk.BooleanVar(value=False)
        self.conn_right = tk.BooleanVar(value=True)
        self.conn_turntable = tk.BooleanVar(value=True)
        self.conn_extruder = tk.BooleanVar(value=True)
        self.conn_status_var = tk.StringVar(value="Not connected")

        self.axes = ['X', 'Y', 'Z', 'Roll', 'Pitch', 'Yaw']

        # ---------- Cylinder parameters ----------
        self.param_vars = {
            'radius':             tk.DoubleVar(value=100.0),
            'z_start':            tk.DoubleVar(value=151.8),
            'pitch':              tk.DoubleVar(value=0.4),
            'total_revs':         tk.DoubleVar(value=20.0),
            'start_angle_deg':    tk.DoubleVar(value=135.0),
            'line_width':         tk.DoubleVar(value=0.4),
            'filament_diameter':  tk.DoubleVar(value=1.75),
            'feed_rate_left':     tk.DoubleVar(value=4.0),
            'feed_rate_right':    tk.DoubleVar(value=4.0),
            'extrusion_factor_left':  tk.DoubleVar(value=1.0),
            'extrusion_factor_right': tk.DoubleVar(value=1.0),
            'tt_cx_left':         tk.DoubleVar(value=574.1),
            'tt_cy_left':         tk.DoubleVar(value=-5.4),
            'tt_cz_left':         tk.DoubleVar(value=153),
            'tt_cx_right':        tk.DoubleVar(value=567.7),
            'tt_cy_right':        tk.DoubleVar(value=6.7),
            'tt_cz_right':        tk.DoubleVar(value=151.1),
            'angular_offset_deg': tk.DoubleVar(value=5.0),
            'radial_offset_left':  tk.DoubleVar(value=0.0),
            'radial_offset_right': tk.DoubleVar(value=0.0),
        }

        # Pattern modulation
        self.pattern_enabled = tk.BooleanVar(value=False)
        self.pattern_waveform = tk.StringVar(value='sine')
        self.pattern_amplitude = tk.DoubleVar(value=2.0)
        self.pattern_wave_count = tk.DoubleVar(value=5.0)
        self.pattern_phase_offset = tk.DoubleVar(value=0.0) # Phase shift in degrees
        self.pattern_arm_left  = tk.BooleanVar(value=True)
        self.pattern_arm_right = tk.BooleanVar(value=True)

        # Live offsets per arm
        self.offset_vars = {}
        for ax in self.axes:
            self.offset_vars[f'left_{ax}']  = tk.DoubleVar(value=0.0)
            self.offset_vars[f'right_{ax}'] = tk.DoubleVar(value=0.0)

        # Turntable speed (rad/s)
        self.turntable_speed_var = tk.DoubleVar(value=0.6)
        self.tt_speed_max = 2.0  # upper bound for the live speed slider (rad/s)

        # Base arm speed (mm/s)
        self.base_arm_speed_var = tk.DoubleVar(value=100.0)

        # Jog controls
        self.jog_arm = tk.StringVar(value='left')
        self.jog_step = tk.DoubleVar(value=1.0)
        self.jog_custom = tk.DoubleVar(value=1.0)

        # Extruder panel variables
        self.calc_left_len = tk.DoubleVar(value=0.0)
        self.calc_right_len = tk.DoubleVar(value=0.0)
        self.prime_len = tk.DoubleVar(value=20.0)

        # Job status
        self.elapsed_time_var = tk.StringVar(value="00:00")
        self.remaining_time_var = tk.StringVar(value="00:00")

        # Calculator
        self.calc_mms_var = tk.DoubleVar(value=10.0)

        # --- NEW: Polar Graph Node Points ---
        # Initialize a default set of 36 nodes around the 360-degree circle
        self.polar_nodes = [i * (2 * math.pi / 36) for i in range(36)]

        # --- NEW: Simulation state (animated nozzle preview) ---
        self.sim_running = False
        self.sim_after_id = None
        self.sim_angle = 0.0          # accumulated angle in radians (0 -> total_revs*2pi)
        self.sim_total_angle = 0.0    # target accumulated angle
        self.sim_angle_step = 0.0     # radians advanced per frame
        self.sim_frame_ms = 20        # animation frame interval
        self.sim_geom = {}            # cached canvas geometry for the run
        self.sim_pts_left = []        # accumulated [x,y,...] canvas points
        self.sim_pts_right = []
        self.sim_seg_left = []        # list of canvas line-item ids (per height band)
        self.sim_seg_right = []
        self.sim_dot_left = None      # moving nozzle dot item ids
        self.sim_dot_right = None
        self.sim_band_rev = -1        # which colour band the current segment belongs to

        try:
            from panel_ui import build_gui
            build_gui(self)                       # reorganised 3-tab layout
        except Exception as e:
            logger.error("New UI failed to build (%s); using legacy layout", e)
            self._build_gui()                     # fallback: original single-page layout
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.update_status()

    # ----------------------------------------------------------------
    def _build_gui(self):
        # Tabbed layout: the original live-control UI plus a slicer/3MF tab.
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        live_tab = ttk.Frame(self.notebook)
        self.notebook.add(live_tab, text="Live Control")

        main_pw = tk.PanedWindow(live_tab, orient=tk.HORIZONTAL, sashrelief=tk.RAISED)
        main_pw.pack(fill=tk.BOTH, expand=True)

        # ----- Left panel -----
        left_frame = ttk.Frame(main_pw, width=500)
        main_pw.add(left_frame)

        # Status
        status_frame = ttk.LabelFrame(left_frame, text="Live Status", padding=5)
        status_frame.pack(fill=tk.X, padx=5, pady=5)
        self.lbl_left_pos = ttk.Label(status_frame, text="Left arm: --")
        self.lbl_left_pos.pack(anchor="w")
        self.lbl_right_pos = ttk.Label(status_frame, text="Right arm: --")
        self.lbl_right_pos.pack(anchor="w")
        self.lbl_turntable = ttk.Label(status_frame, text="Turntable: --°")
        self.lbl_turntable.pack(anchor="w")
        self.lbl_t0 = ttk.Label(status_frame, text="T0: --°C")
        self.lbl_t0.pack(anchor="w")
        self.lbl_t1 = ttk.Label(status_frame, text="T1 (X axis): --°C")
        self.lbl_t1.pack(anchor="w")

        # Job status
        job_frame = ttk.LabelFrame(left_frame, text="Current Job", padding=5)
        job_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(job_frame, text="Elapsed:").grid(row=0, column=0, sticky='w')
        ttk.Label(job_frame, textvariable=self.elapsed_time_var).grid(row=0, column=1, sticky='w')
        ttk.Label(job_frame, text="Remaining:").grid(row=1, column=0, sticky='w')
        ttk.Label(job_frame, textvariable=self.remaining_time_var).grid(row=1, column=1, sticky='w')

        # Connection selection: choose which devices to connect / drive.
        conn_frame = ttk.LabelFrame(left_frame, text="Connections", padding=5)
        conn_frame.pack(fill=tk.X, padx=5, pady=(5, 0))
        sel_row = ttk.Frame(conn_frame)
        sel_row.pack(fill=tk.X)
        ttk.Checkbutton(sel_row, text="Left arm", variable=self.conn_left).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(sel_row, text="Right arm", variable=self.conn_right).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(sel_row, text="Turntable", variable=self.conn_turntable).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(sel_row, text="Extruder", variable=self.conn_extruder).pack(side=tk.LEFT, padx=4)
        ttk.Button(sel_row, text="Connect Selected", command=self.connect_hw).pack(side=tk.LEFT, padx=8)
        ttk.Button(sel_row, text="Disconnect", command=self.disconnect_hw).pack(side=tk.LEFT, padx=2)
        ttk.Label(conn_frame, textvariable=self.conn_status_var).pack(anchor="w", pady=(3, 0))

        # Buttons
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(btn_frame, text="Home", command=self.home_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Prepare to Print", command=self.prepare_to_print).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Start Cylinder", command=self.start_cylinder).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="STOP", command=self.stop_print, style="Red.TButton").pack(side=tk.LEFT, padx=2)
        self.pause_btn = ttk.Button(btn_frame, text="Pause", command=self.toggle_pause)
        self.pause_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Extruders Off", command=self.extruders_off).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="EMERGENCY STOP", command=self.emergency_stop, style="Red.TButton").pack(side=tk.LEFT, padx=2)

        # ---- Parameters / Extruder / Pattern ----
        param_ext_frame = ttk.Frame(left_frame)
        param_ext_frame.pack(fill=tk.X, padx=5, pady=5)

        # ================= LEFT COLUMN =================
        param_frame = ttk.LabelFrame(param_ext_frame, text="Cylinder Parameters", padding=5)
        param_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0,5))

        row = 0
        def add_param_row(label, var):
            nonlocal row
            ttk.Label(param_frame, text=label).grid(row=row, column=0, sticky='w', padx=2, pady=1)
            ttk.Entry(param_frame, textvariable=var, width=10).grid(row=row, column=1, padx=2, pady=1)
            row += 1

        for name, var in self.param_vars.items():
            if name in ('radial_offset_left', 'radial_offset_right'):
                continue
            label = name.replace('_',' ').title()
            add_param_row(label, var)

        # Turntable speed with calculator
        ttk.Label(param_frame, text="Turntable Speed (rad/s)").grid(row=row, column=0, sticky='w', padx=2, pady=1)
        tt_spd_frame = ttk.Frame(param_frame)
        tt_spd_frame.grid(row=row, column=1, columnspan=2, sticky='w')
        ttk.Entry(tt_spd_frame, textvariable=self.turntable_speed_var, width=8).pack(side=tk.LEFT, padx=2)
        for val, txt in [(-1, '-1'), (-0.1, '-0.1'), (-0.01, '-0.01'), (0.01, '+0.01'), (0.1, '+0.1'), (1, '+1')]:
            ttk.Button(tt_spd_frame, text=txt, width=3,
                       command=lambda v=val: self.turntable_speed_var.set(round(self.turntable_speed_var.get() + v, 3))
                       ).pack(side=tk.LEFT, padx=1)
        ttk.Label(tt_spd_frame, text="  from mm/s:").pack(side=tk.LEFT, padx=(10,2))
        ttk.Entry(tt_spd_frame, textvariable=self.calc_mms_var, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Button(tt_spd_frame, text="→ rad/s", width=6,
                   command=self.calc_rads_from_mms).pack(side=tk.LEFT, padx=2)
        row += 1

        # Base arm speed with quick buttons
        ttk.Label(param_frame, text="Base Arm Speed (mm/s)").grid(row=row, column=0, sticky='w', padx=2, pady=1)
        arm_spd_frame = ttk.Frame(param_frame)
        arm_spd_frame.grid(row=row, column=1, columnspan=2, sticky='w')
        ttk.Entry(arm_spd_frame, textvariable=self.base_arm_speed_var, width=8).pack(side=tk.LEFT, padx=2)
        for val, txt in [(-10, '-10'), (-1, '-1'), (-0.1, '-0.1'), (0.1, '+0.1'), (1, '+1'), (10, '+10')]:
            ttk.Button(arm_spd_frame, text=txt, width=3,
                       command=lambda v=val: self.base_arm_speed_var.set(round(self.base_arm_speed_var.get() + v, 1))
                       ).pack(side=tk.LEFT, padx=1)
        row += 1

        # Radial offset rows
        def add_radial_row(label, var):
            nonlocal row
            ttk.Label(param_frame, text=label).grid(row=row, column=0, sticky='w', padx=2, pady=1)
            ttk.Entry(param_frame, textvariable=var, width=8).grid(row=row, column=1, padx=2, pady=1)
            btn_sub = ttk.Frame(param_frame)
            btn_sub.grid(row=row, column=2, padx=2)
            for val, txt in [(-1, '-1'), (-0.1, '-0.1'), (0.1, '+0.1'), (1, '+1')]:
                ttk.Button(btn_sub, text=txt, width=3,
                           command=lambda v=val, dv=var: dv.set(round(dv.get() + v, 3))
                           ).pack(side=tk.LEFT, padx=1)
            row += 1

        add_radial_row("Radial Offset L (mm)", self.param_vars['radial_offset_left'])
        add_radial_row("Radial Offset R (mm)", self.param_vars['radial_offset_right'])

        # ================= RIGHT COLUMN (pattern + extruder) =================
        right_col = ttk.Frame(param_ext_frame)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Pattern Modulation (Now labeled Cylindrical Pattern Wrapping)
        pattern_frame = ttk.LabelFrame(right_col, text="Cylindrical Pattern Wrapping", padding=3)
        pattern_frame.pack(fill=tk.X, pady=(0,3))

        ttk.Checkbutton(pattern_frame, text="Enable Wrapped Wave", variable=self.pattern_enabled).grid(row=0, column=0, columnspan=4, sticky='w')
        ttk.Label(pattern_frame, text="Waveform:").grid(row=1, column=0, sticky='w')
        ttk.Combobox(pattern_frame, textvariable=self.pattern_waveform, values=['sine','triangle','square'], width=8).grid(row=1, column=1, sticky='w')

        # Amplitude
        ttk.Label(pattern_frame, text="Amplitude (mm):").grid(row=2, column=0, sticky='w')
        ttk.Entry(pattern_frame, textvariable=self.pattern_amplitude, width=8).grid(row=2, column=1)
        amp_btn_frame = ttk.Frame(pattern_frame)
        amp_btn_frame.grid(row=2, column=2, columnspan=2, padx=2)
        for val, txt in [(-1, '-1'), (-0.1, '-0.1'), (0.1, '+0.1'), (1, '+1')]:
            ttk.Button(amp_btn_frame, text=txt, width=3,
                       command=lambda v=val: self.pattern_amplitude.set(round(self.pattern_amplitude.get() + v, 3))
                       ).pack(side=tk.LEFT, padx=1)

        # Wave count
        ttk.Label(pattern_frame, text="Wave Count (/rev):").grid(row=3, column=0, sticky='w')
        ttk.Entry(pattern_frame, textvariable=self.pattern_wave_count, width=8).grid(row=3, column=1)
        wc_btn_frame = ttk.Frame(pattern_frame)
        wc_btn_frame.grid(row=3, column=2, columnspan=2, padx=2)
        for val, txt in [(-1, '-1'), (-0.1, '-0.1'), (0.1, '+0.1'), (1, '+1')]:
            ttk.Button(wc_btn_frame, text=txt, width=3,
                       command=lambda v=val: self.pattern_wave_count.set(round(self.pattern_wave_count.get() + v, 3))
                       ).pack(side=tk.LEFT, padx=1)

        # NEW: Phase Offset
        ttk.Label(pattern_frame, text="Phase Offset (deg):").grid(row=4, column=0, sticky='w')
        ttk.Entry(pattern_frame, textvariable=self.pattern_phase_offset, width=8).grid(row=4, column=1)
        ph_btn_frame = ttk.Frame(pattern_frame)
        ph_btn_frame.grid(row=4, column=2, columnspan=2, padx=2)
        for val, txt in [(-45, '-45'), (-5, '-5'), (5, '+5'), (45, '+45')]:
            ttk.Button(ph_btn_frame, text=txt, width=3,
                       command=lambda v=val: self.pattern_phase_offset.set(round(self.pattern_phase_offset.get() + v, 3))
                       ).pack(side=tk.LEFT, padx=1)

        ttk.Label(pattern_frame, text="Apply to arms:").grid(row=5, column=0, sticky='w')
        ttk.Checkbutton(pattern_frame, text="Left", variable=self.pattern_arm_left).grid(row=5, column=1, sticky='w')
        ttk.Checkbutton(pattern_frame, text="Right", variable=self.pattern_arm_right).grid(row=5, column=2, sticky='w')

        # --- NEW: LIVE turntable speed control (adjustable during a print) ---
        ttk.Separator(pattern_frame, orient='horizontal').grid(row=6, column=0, columnspan=4, sticky='ew', pady=3)
        ttk.Label(pattern_frame, text="Live Turntable Speed (rad/s)",
                  font=('Arial', 9, 'bold')).grid(row=7, column=0, columnspan=2, sticky='w')
        # Numeric entry stays in sync with the slider and the copy in Cylinder Parameters.
        ttk.Entry(pattern_frame, textvariable=self.turntable_speed_var, width=8).grid(row=7, column=2)
        tt_wave_btn_frame = ttk.Frame(pattern_frame)
        tt_wave_btn_frame.grid(row=7, column=3, padx=2)
        for val, txt in [(-0.01, '-'), (0.01, '+')]:
            ttk.Button(tt_wave_btn_frame, text=txt, width=2,
                       command=lambda v=val: self._nudge_turntable_speed(v)
                       ).pack(side=tk.LEFT, padx=1)

        # Drag-able slider: the print thread reads this value live, so moving it
        # while a job is running changes the turntable speed on the fly.
        self.tt_speed_slider = tk.Scale(
            pattern_frame, variable=self.turntable_speed_var,
            from_=0.0, to=self.tt_speed_max, resolution=0.005,
            orient=tk.HORIZONTAL, length=240, showvalue=True)
        self.tt_speed_slider.grid(row=8, column=0, columnspan=4, sticky='ew', pady=(2, 0))
        ttk.Label(pattern_frame, text="(safe to drag during a print)",
                  font=('Arial', 8, 'italic')).grid(row=9, column=0, columnspan=4, sticky='w')

        # Extruder Control
        extr_frame = ttk.LabelFrame(right_col, text="Extruder Control", padding=5)
        extr_frame.pack(fill=tk.X)

        ttk.Button(extr_frame, text="Calculate Path Lengths", command=self.calculate_extrusion_lengths).pack(pady=2)
        ttk.Label(extr_frame, text="Left filament (mm):").pack(anchor='w')
        ttk.Entry(extr_frame, textvariable=self.calc_left_len, width=10, state='readonly').pack(pady=1)
        ttk.Label(extr_frame, text="Right filament (mm):").pack(anchor='w')
        ttk.Entry(extr_frame, textvariable=self.calc_right_len, width=10, state='readonly').pack(pady=1)
        ttk.Separator(extr_frame, orient='horizontal').pack(fill='x', pady=5)
        ttk.Label(extr_frame, text="Prime Length (mm):").pack(anchor='w')
        ttk.Entry(extr_frame, textvariable=self.prime_len, width=8).pack(pady=1)
        btn_prime_frame = ttk.Frame(extr_frame)
        btn_prime_frame.pack(pady=2)
        ttk.Button(btn_prime_frame, text="Prime Left", command=lambda: self.prime_extruder('left')).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_prime_frame, text="Prime Right", command=lambda: self.prime_extruder('right')).pack(side=tk.LEFT, padx=2)

        # ----- Right panel (jog & live offsets & preview) -----
        right_frame = ttk.Frame(main_pw, width=700)
        main_pw.add(right_frame)

        # Jog controls
        jog_frame = ttk.LabelFrame(right_frame, text="Manual Jog (not while printing)", padding=5)
        jog_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Radiobutton(jog_frame, text="Left", variable=self.jog_arm, value='left').grid(row=0, column=0)
        ttk.Radiobutton(jog_frame, text="Right", variable=self.jog_arm, value='right').grid(row=0, column=1)
        ttk.Radiobutton(jog_frame, text="Both", variable=self.jog_arm, value='both').grid(row=0, column=2)
        step_frame = ttk.Frame(jog_frame)
        step_frame.grid(row=1, column=0, columnspan=3, pady=5)
        ttk.Label(step_frame, text="Step:").pack(side=tk.LEFT)
        for val, label in [(0.1,'0.1'), (1.0,'1'), (10.0,'10')]:
            ttk.Radiobutton(step_frame, text=label, variable=self.jog_step, value=val).pack(side=tk.LEFT)
        ttk.Label(step_frame, text="Custom:").pack(side=tk.LEFT, padx=(10,0))
        ttk.Entry(step_frame, textvariable=self.jog_custom, width=5).pack(side=tk.LEFT)
        ttk.Button(step_frame, text="Use", command=lambda: self.jog_step.set(self.jog_custom.get())).pack(side=tk.LEFT)

        axes_buttons = [
            ('X', 0, 0), ('Y', 0, 1), ('Z', 0, 2),
            ('Roll', 1, 0), ('Pitch', 1, 1), ('Yaw', 1, 2)
        ]
        for axis, r, c in axes_buttons:
            frame = ttk.Frame(jog_frame)
            frame.grid(row=2+r, column=c, padx=2, pady=2)
            ttk.Button(frame, text=f"{axis}+", command=lambda ax=axis: self.jog(ax, +1)).pack(side=tk.LEFT)
            ttk.Button(frame, text=f"{axis}-", command=lambda ax=axis: self.jog(ax, -1)).pack(side=tk.LEFT)

        # Live offsets
        off_frame = ttk.LabelFrame(right_frame, text="Live Offsets (applied continuously)", padding=5)
        off_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(off_frame, text="Axis").grid(row=0, column=0)
        ttk.Label(off_frame, text="Left Offset").grid(row=0, column=1)
        ttk.Label(off_frame, text="Right Offset").grid(row=0, column=2)
        for i, ax in enumerate(self.axes):
            ttk.Label(off_frame, text=ax).grid(row=i+1, column=0, padx=2, pady=1)
            ttk.Entry(off_frame, textvariable=self.offset_vars[f'left_{ax}'], width=8).grid(row=i+1, column=1, padx=2, pady=1)
            ttk.Entry(off_frame, textvariable=self.offset_vars[f'right_{ax}'], width=8).grid(row=i+1, column=2, padx=2, pady=1)

        # ----- Preview canvas (Polar) -----
        preview_frame = ttk.LabelFrame(right_frame, text="Polar Toolpath Preview", padding=5)
        preview_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.canvas = tk.Canvas(preview_frame, bg='white', height=300)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        # Bind left mouse click for adding/removing nodes on the preview
        self.canvas.bind("<Button-1>", self.on_canvas_click)

        btn_preview_frame = ttk.Frame(preview_frame)
        btn_preview_frame.pack(fill=tk.X, pady=(5,0))
        ttk.Button(btn_preview_frame, text="Reset Nodes (Default)", command=self.reset_nodes).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_preview_frame, text="Update Preview", command=self.update_preview).pack(side=tk.LEFT, padx=2)
        # --- NEW: Simulate button ---
        self.sim_btn = ttk.Button(btn_preview_frame, text="▶ Simulate", command=self.toggle_simulation)
        self.sim_btn.pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_preview_frame, text="Save Config", command=self.save_config).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_preview_frame, text="Load Config", command=self.load_config).pack(side=tk.LEFT, padx=2)

        style = ttk.Style()
        style.configure("Red.TButton", foreground="red", font=('Arial', 10, 'bold'))

        # ----- Slicer / 3MF tab -----
        slicer_frame = ttk.Frame(self.notebook)
        self.notebook.add(slicer_frame, text="Slicer / 3MF")
        try:
            from slicer_tab import SlicerTab
            self.slicer_tab = SlicerTab(slicer_frame, self)
        except Exception as e:
            self.slicer_tab = None
            logger.warning(f"Slicer tab unavailable: {e}")
            ttk.Label(slicer_frame,
                      text=("Slicer tab could not load:\n"
                            f"{e}\n\n"
                            "Ensure slicer_tab.py, slicer.py and planner.py are alongside "
                            "control_panel.py, and that trimesh/shapely/numpy/scipy/networkx "
                            "are installed."),
                      justify="left", padding=20).pack(anchor="w")

    # ======================================================================
    def safe_get(self, var, name):
        if not hasattr(self, '_safe_cache'):
            self._safe_cache = {}
        try:
            val = var.get()
            float_val = float(val)
            self._safe_cache[name] = float_val
            return float_val
        except (ValueError, tk.TclError):
            return self._safe_cache.get(name, 0.0)

    # ======================================================================
    def _nudge_turntable_speed(self, delta):
        """Bump the live turntable speed by delta, clamped to [0, tt_speed_max]."""
        try:
            new_val = self.turntable_speed_var.get() + delta
        except (ValueError, tk.TclError):
            new_val = 0.0
        new_val = max(0.0, min(self.tt_speed_max, round(new_val, 3)))
        self.turntable_speed_var.set(new_val)

    # ======================================================================
    def calc_rads_from_mms(self):
        try:
            mms = self.calc_mms_var.get()
            radius = self.param_vars['radius'].get()
            rads = mms / radius if radius > 0 else 0.0
            self.turntable_speed_var.set(round(rads, 4))
        except (ValueError, tk.TclError):
            pass

    # ======================================================================
    def reset_nodes(self):
        """Reset the polar graph to 36 evenly spaced nodes."""
        self.polar_nodes = [i * (2 * math.pi / 36) for i in range(36)]
        self.update_preview()

    # ======================================================================
    def on_canvas_click(self, event):
        """Add or remove a polar node based on where the user clicks."""
        if self.sim_running:
            return  # ignore edits while a simulation is playing
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 10 or h < 10:
            return

        cx, cy = w / 2, h / 2
        dx = event.x - cx
        dy = event.y - cy

        # Only respond if click is within the graph area
        if math.hypot(dx, dy) < 20:
            return

        theta = math.atan2(-dy, dx)  # Canvas Y is inverted, so negative dy
        if theta < 0:
            theta += 2 * math.pi

        # Check if we clicked near an existing node (remove it)
        threshold = 12.0
        for i, node_theta in enumerate(self.polar_nodes):
            # Angular distance
            diff = abs(node_theta - theta)
            if diff > math.pi:
                diff = 2 * math.pi - diff
            if diff < 0.05: # Approximation close to the node angle
                removed = self.polar_nodes.pop(i)
                self.update_preview()
                return

        # Otherwise, add a new node at the clicked angle
        self.polar_nodes.append(theta)
        self.polar_nodes.sort()
        self.update_preview()

    # ======================================================================
    def _wave_value(self, phase, amp, waveform):
        """Return the radial pattern displacement for a given phase (radians)."""
        if waveform == 'sine':
            return amp * math.sin(phase)
        elif waveform == 'triangle':
            return amp * (2/math.pi * math.asin(math.sin(phase)))
        elif waveform == 'square':
            return amp * (1 if math.sin(phase) >= 0 else -1)
        return 0.0

    # ======================================================================
    def update_preview(self):
        """Redraw the top-down Polar view on the canvas with interactive nodes."""
        try:
            radius = self.param_vars['radius'].get()
            radial_left = self.param_vars['radial_offset_left'].get()
            radial_right = self.param_vars['radial_offset_right'].get()
            amp = self.pattern_amplitude.get()
            wave_count = self.pattern_wave_count.get()
            phase_offset = math.radians(self.pattern_phase_offset.get())
            waveform = self.pattern_waveform.get()
            pat_enabled = self.pattern_enabled.get()
        except (ValueError, tk.TclError):
            return

        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 10 or h < 10:
            w, h = 400, 300

        cx, cy = w / 2, h / 2
        # Scale the drawing to fit nicely (reserve 50px for margins and labels)
        graph_max_radius = min(w, h) / 2 - 40
        # Scale actual radius to canvas radius
        if radius > 0:
            scale_factor = graph_max_radius / radius
        else:
            scale_factor = 1.0

        # --- 1. Draw Polar Grid (Concentric circles & Spokes) ---
        grid_rings = 4
        for i in range(1, grid_rings + 1):
            r = (graph_max_radius / grid_rings) * i
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline='lightgray', width=1)
        for i in range(12):  # 12 spokes
            angle = i * (2 * math.pi / 12)
            x1 = cx + graph_max_radius * math.cos(angle)
            y1 = cy - graph_max_radius * math.sin(angle)
            self.canvas.create_line(cx, cy, x1, y1, fill='lightgray', width=1)

        # --- 2. Calculate Left and Right Paths from Nodes ---
        # Sort nodes for continuous drawing
        nodes = sorted(self.polar_nodes)
        if not nodes:
            return

        points_left = []
        points_right = []

        for theta in nodes:
            pat = 0.0
            if pat_enabled:
                phase = theta * wave_count + phase_offset
                pat = self._wave_value(phase, amp, waveform)

            r_left = radius + radial_left + pat
            r_right = radius + radial_right + pat

            # Convert to canvas pixels
            x_l = cx + r_left * scale_factor * math.cos(theta)
            y_l = cy - r_left * scale_factor * math.sin(theta)
            x_r = cx + r_right * scale_factor * math.cos(theta)
            y_r = cy - r_right * scale_factor * math.sin(theta)

            points_left.extend([x_l, y_l])
            points_right.extend([x_r, y_r])

        # --- 3. Draw the Lines ---
        if len(points_left) >= 4:
            self.canvas.create_line(points_left, fill='blue', width=2)
        if len(points_right) >= 4:
            self.canvas.create_line(points_right, fill='red', width=2)

        # --- 4. Draw the Nodes themselves (dots) ---
        for theta in nodes:
            # Draw left node
            if pat_enabled:
                phase = theta * wave_count + phase_offset
                pat = self._wave_value(phase, amp, waveform)
            else:
                pat = 0.0

            r_l_canvas = (radius + radial_left + pat) * scale_factor
            x_l = cx + r_l_canvas * math.cos(theta)
            y_l = cy - r_l_canvas * math.sin(theta)
            self.canvas.create_oval(x_l-3, y_l-3, x_l+3, y_l+3, fill='blue', outline='black')

            r_r_canvas = (radius + radial_right + pat) * scale_factor
            x_r = cx + r_r_canvas * math.cos(theta)
            y_r = cy - r_r_canvas * math.sin(theta)
            self.canvas.create_oval(x_r-3, y_r-3, x_r+3, y_r+3, fill='red', outline='black')

        # --- 5. Legend ---
        self.canvas.create_text(cx, cy, text=f"Left (Blue) / Right (Red)\nNodes: {len(nodes)}", fill='black', font=('Arial', 9))

    # ======================================================================
    # ============ NEW: Animated nozzle simulation =========================
    # ======================================================================
    def _lerp_color(self, c0, c1, t):
        """Linearly interpolate between two (r,g,b) tuples -> #rrggbb hex."""
        t = max(0.0, min(1.0, t))
        r = int(round(c0[0] + (c1[0] - c0[0]) * t))
        g = int(round(c0[1] + (c1[1] - c0[1]) * t))
        b = int(round(c0[2] + (c1[2] - c0[2]) * t))
        return f"#{r:02x}{g:02x}{b:02x}"

    def toggle_simulation(self):
        """Start or stop the animated nozzle preview."""
        if self.sim_running:
            self.stop_simulation()
        else:
            self.start_simulation()

    def start_simulation(self):
        """Animate the real machine: the plate (turntable) rotates while two
        nozzles fixed on either side deposit the wave pattern. The toolpath is
        drawn in the part frame, so it is carried around with the plate."""
        try:
            radius = self.param_vars['radius'].get()
            total_revs = self.param_vars['total_revs'].get()
            amp = self.pattern_amplitude.get()
        except (ValueError, tk.TclError):
            messagebox.showwarning("Simulation", "Check that the numeric parameters are valid.")
            return
        if total_revs <= 0:
            messagebox.showwarning("Simulation", "Total Revs must be greater than 0 to simulate.")
            return

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 10 or h < 10:
            w, h = 400, 300
        cx, cy = w / 2, h / 2
        graph_max_radius = min(w, h) / 2 - 30
        denom = radius + abs(amp) + 8 if (radius + abs(amp)) > 0 else 1.0
        scale = graph_max_radius / denom
        self.sim_geom = dict(cx=cx, cy=cy, scale=scale)

        self.sim_phi = 0.0                       # turntable angle (radians)
        self.sim_total_angle = total_revs * 2 * math.pi
        self.sim_trail_l = []                    # list of (part_angle, r_mm)
        self.sim_trail_r = []

        self.sim_running = True
        self.sim_btn.config(text="■ Stop")
        self._sim_step()

    def stop_simulation(self):
        """Stop the animation and leave the drawn path on screen."""
        self.sim_running = False
        if self.sim_after_id is not None:
            try:
                self.root.after_cancel(self.sim_after_id)
            except Exception:
                pass
            self.sim_after_id = None
        try:
            self.sim_btn.config(text="▶ Simulate")
        except Exception:
            pass

    def _sim_step(self):
        """One animation frame: rotate the plate, bob the two fixed nozzles on
        the wave, and trail the deposited toolpath (drawn in the rotating part
        frame). Called repeatedly via root.after."""
        if not self.sim_running:
            return
        try:
            radius = self.param_vars['radius'].get()
            radial_left = self.param_vars['radial_offset_left'].get()
            radial_right = self.param_vars['radial_offset_right'].get()
            amp = self.pattern_amplitude.get()
            wave_count = self.pattern_wave_count.get()
            phase_offset = math.radians(self.pattern_phase_offset.get())
            waveform = self.pattern_waveform.get()
            pat_enabled = self.pattern_enabled.get()
            total_revs = self.param_vars['total_revs'].get()
            speed = self.turntable_speed_var.get()
        except (ValueError, tk.TclError):
            self.sim_after_id = self.root.after(self.sim_frame_ms, self._sim_step)
            return

        cx = self.sim_geom['cx']; cy = self.sim_geom['cy']; scale = self.sim_geom['scale']
        dt = self.sim_frame_ms / 1000.0
        if speed <= 0:
            speed = 0.6                        # keep the preview turning even at 0
        dphi = speed * dt
        self.sim_phi += dphi
        phi = self.sim_phi

        def wave(pa):
            if not pat_enabled:
                return 0.0
            return self._wave_value(pa * wave_count + phase_offset, amp, waveform)

        # nozzles fixed on either side of the plate (left = west, right = east)
        noz = [(math.pi, radial_left, self.pattern_arm_left.get(), self.sim_trail_l),
               (0.0, radial_right, self.pattern_arm_right.get(), self.sim_trail_r)]

        # append the current contact point (in the PART frame) to each trail
        cap = int((2 * math.pi / dphi) * 1.05) + 2 if dphi > 0 else 4000
        for world, off, arm_on, trail in noz:
            pa = world - phi
            wv = wave(pa) if arm_on else 0.0
            trail.append((pa, radius + off + wv))
            if len(trail) > cap:
                del trail[0:len(trail) - cap]

        # ---- redraw the whole scene ----
        c = self.canvas
        c.delete("all")
        edge = radius * scale + max(8.0, abs(amp) * scale + 6.0)
        c.create_oval(cx - edge, cy - edge, cx + edge, cy + edge,
                      fill='#f2f2f2', outline='#b0b0b0', width=1)
        for k in range(8):                     # spokes rotate with the plate
            a = phi + k * math.pi / 4
            c.create_line(cx, cy, cx + edge * math.cos(a), cy - edge * math.sin(a),
                          fill='#dcdcdc', width=1)
        c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill='#9a9a9a', outline='')

        for trail, col in ((self.sim_trail_l, 'blue'), (self.sim_trail_r, 'red')):
            if len(trail) >= 2:
                pts = []
                for pa, r_mm in trail:
                    wa = pa + phi
                    pts.append(cx + r_mm * scale * math.cos(wa))
                    pts.append(cy - r_mm * scale * math.sin(wa))
                c.create_line(*pts, fill=col, width=2, smooth=True)

        for world, off, arm_on, trail in noz:
            pa = world - phi
            wv = wave(pa) if arm_on else 0.0
            r_mm = radius + off + wv
            x = cx + r_mm * scale * math.cos(world)
            y = cy - r_mm * scale * math.sin(world)
            col = 'blue' if world > math.pi / 2 else 'red'
            c.create_oval(x - 9, y - 9, x + 9, y + 9, fill=col, outline='white', width=2)
            c.create_oval(x - 3, y - 3, x + 3, y + 3, fill='white', outline='')

        rev = phi / (2 * math.pi)
        c.create_text(cx, cy - 2, text=f"{rev:0.1f} / {total_revs:0.0f} rev",
                      fill='#555555', font=('Arial', 9))

        if phi >= self.sim_total_angle:
            self.stop_simulation()
            return
        self.sim_after_id = self.root.after(self.sim_frame_ms, self._sim_step)

    # ======================================================================
    def save_config(self):
        """Save all current parameter values to a JSON file."""
        data = {}
        for name, var in self.param_vars.items():
            data[name] = var.get()
        data['pattern_enabled'] = self.pattern_enabled.get()
        data['pattern_waveform'] = self.pattern_waveform.get()
        data['pattern_amplitude'] = self.pattern_amplitude.get()
        data['pattern_wave_count'] = self.pattern_wave_count.get()
        data['pattern_phase_offset'] = self.pattern_phase_offset.get()
        data['pattern_arm_left'] = self.pattern_arm_left.get()
        data['pattern_arm_right'] = self.pattern_arm_right.get()
        data['turntable_speed'] = self.turntable_speed_var.get()
        data['base_arm_speed'] = self.base_arm_speed_var.get()
        data['polar_nodes'] = self.polar_nodes # Save the node list
        for ax in self.axes:
            data[f'left_{ax}'] = self.offset_vars[f'left_{ax}'].get()
            data[f'right_{ax}'] = self.offset_vars[f'right_{ax}'].get()

        file_path = filedialog.asksaveasfilename(defaultextension=".json",
                                                 filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if file_path:
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Configuration saved to {file_path}")

    def load_config(self):
        """Load parameters from a JSON file."""
        file_path = filedialog.askopenfilename(defaultextension=".json",
                                               filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not file_path:
            return
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("Load Error", str(e))
            return

        for name, var in self.param_vars.items():
            if name in data:
                var.set(data[name])
        if 'pattern_enabled' in data:
            self.pattern_enabled.set(data['pattern_enabled'])
        if 'pattern_waveform' in data:
            self.pattern_waveform.set(data['pattern_waveform'])
        if 'pattern_amplitude' in data:
            self.pattern_amplitude.set(data['pattern_amplitude'])
        if 'pattern_wave_count' in data:
            self.pattern_wave_count.set(data['pattern_wave_count'])
        if 'pattern_phase_offset' in data:
            self.pattern_phase_offset.set(data['pattern_phase_offset'])
        if 'pattern_arm_left' in data:
            self.pattern_arm_left.set(data['pattern_arm_left'])
        if 'pattern_arm_right' in data:
            self.pattern_arm_right.set(data['pattern_arm_right'])
        if 'turntable_speed' in data:
            self.turntable_speed_var.set(data['turntable_speed'])
        if 'base_arm_speed' in data:
            self.base_arm_speed_var.set(data['base_arm_speed'])
        if 'polar_nodes' in data:
            self.polar_nodes = data['polar_nodes'] # Restore nodes
        for ax in self.axes:
            key = f'left_{ax}'
            if key in data:
                self.offset_vars[key].set(data[key])
            key = f'right_{ax}'
            if key in data:
                self.offset_vars[key].set(data[key])
        logger.info(f"Configuration loaded from {file_path}")
        self.update_preview()

    # ======================================================================
    def connect_hw(self):
        """Connect only the devices ticked in the Connections panel."""
        connected = []
        try:
            # Fresh start: drop any existing handles for devices we won't use.
            if self.conn_left.get():
                self.left = ArmController(self.cfg['arms']['left']['ip'], "left")
                self.left.connect()
                connected.append("left arm")
            else:
                self.left = None

            if self.conn_right.get():
                self.right = ArmController(self.cfg['arms']['right']['ip'], "right")
                self.right.connect()
                connected.append("right arm")
            else:
                self.right = None

            if self.conn_turntable.get():
                self.turntable = TurntableController(host=self.cfg['turntable']['controller_ip'],
                                                     axis=self.cfg['turntable']['axis'])
                self.turntable.connect()
                connected.append("turntable")
            else:
                self.turntable = None

            if self.conn_extruder.get():
                self.extruder = ExtruderController(self.cfg['moonraker']['host'],
                                                   self.cfg['moonraker']['port'])
                connected.append("extruder")
            else:
                self.extruder = None

            self.hw_connected = bool(connected)
            status = "Connected: " + ", ".join(connected) if connected else "Nothing selected to connect"
            self.conn_status_var.set(status)
            logger.info(status)
        except Exception as e:
            self.conn_status_var.set(f"Connection failed: {e}")
            messagebox.showerror("Connection Failed", str(e))

    def disconnect_hw(self):
        """Disconnect all currently-connected devices."""
        for dev in (self.left, self.right, self.turntable):
            try:
                if dev is not None:
                    dev.disconnect()
            except Exception:
                pass
        self.left = self.right = self.turntable = self.extruder = None
        self.hw_connected = False
        self.conn_status_var.set("Not connected")
        logger.info("Disconnected all hardware")

    def _require(self, *devices):
        """Return True if all named devices are connected, else warn and False."""
        names = {'left': self.left, 'right': self.right,
                 'turntable': self.turntable, 'extruder': self.extruder}
        missing = [d for d in devices if names.get(d) is None]
        if missing:
            messagebox.showwarning("Not connected",
                                   "This action needs: " + ", ".join(missing) +
                                   ".\nTick them in Connections and press Connect Selected.")
            return False
        return True

    def home_all(self):
        if not self.hw_connected: return
        if self.extruder is not None:
            self.extruder.send_gcode("SET_KINEMATIC_POSITION X=0 Y=0 Z=0")
        arms = [a for a in (self.left, self.right) if a is not None]
        for i, arm in enumerate(arms):
            arm.home(wait=(i == len(arms) - 1))  # wait on the last one
        logger.info(f"Homed {len(arms)} arm(s)")

    def prepare_to_print(self):
        if not self.hw_connected:
            messagebox.showwarning("Warning", "Hardware not connected.")
            return
        threading.Thread(target=self._prepare_thread, daemon=True).start()

    def _prepare_thread(self):
        try:
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
            self.root.after(0, lambda: messagebox.showinfo("Info", "Ready to print."))
        except Exception as e:
            self.root.after(0, lambda e=e: messagebox.showerror("Prepare Error", str(e)))

    def calculate_extrusion_lengths(self):
        try:
            radius = self.param_vars['radius'].get()
            pitch = self.param_vars['pitch'].get()
            total_revs = self.param_vars['total_revs'].get()
            line_w = self.param_vars['line_width'].get()
            fil_diam = self.param_vars['filament_diameter'].get()
            left_factor = self.param_vars['extrusion_factor_left'].get()
            right_factor = self.param_vars['extrusion_factor_right'].get()
            left_len = total_revs * math.sqrt((2 * math.pi * radius)**2 + pitch**2)
            right_len = left_len
            fil_area = math.pi * (fil_diam/2)**2
            fil_left = (left_len * line_w * pitch) / fil_area * left_factor
            fil_right = (right_len * line_w * pitch) / fil_area * right_factor
            self.calc_left_len.set(round(fil_left, 1))
            self.calc_right_len.set(round(fil_right, 1))
            logger.info(f"Calculated filament: Left={fil_left:.1f} mm, Right={fil_right:.1f} mm")
        except Exception as e:
            messagebox.showerror("Calculation Error", str(e))

    def prime_extruder(self, side):
        if not self._require('extruder'): return
        length = self.prime_len.get()
        if side == 'left':
            speed = self.param_vars['feed_rate_left'].get()
            tool = 1
        else:
            speed = self.param_vars['feed_rate_right'].get()
            tool = 0
        self.extruder.extrude(tool, length, speed, wait=False)
        logger.info(f"Primed {side} extruder: {length} mm at {speed} mm/s")

    # ======================================================================
    def start_cylinder(self):
        # The cylinder routine is the original dual-arm coordinated print and
        # expects both arms, the turntable and the extruder.
        if not self._require('left', 'right', 'turntable', 'extruder'): return
        if self.printing: return
        if self.calc_left_len.get() == 0 or self.calc_right_len.get() == 0:
            self.calculate_extrusion_lengths()
        self.printing = True
        self.stop_requested = False
        self.paused = False
        self.pause_btn.config(text="Pause")
        threading.Thread(target=self._cylinder_thread, daemon=True).start()

    def stop_print(self):
        if self.printing:
            self.stop_requested = True

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self.pause_btn.config(text="Resume")
        else:
            self.pause_btn.config(text="Pause")

    # ======================================================================
    def _cylinder_thread(self):
        try:
            def safe(name, var):
                return self.safe_get(var, name)

            total_revs    = safe('total_revs', self.param_vars['total_revs'])
            radius        = safe('radius', self.param_vars['radius'])
            pitch         = safe('pitch', self.param_vars['pitch'])
            z_start       = safe('z_start', self.param_vars['z_start'])
            start_angle_deg = safe('start_angle_deg', self.param_vars['start_angle_deg'])
            angular_off_deg = safe('angular_offset_deg', self.param_vars['angular_offset_deg'])
            tt_cx_l = safe('tt_cx_left', self.param_vars['tt_cx_left'])
            tt_cy_l = safe('tt_cy_left', self.param_vars['tt_cy_left'])
            tt_cz_l = safe('tt_cz_left', self.param_vars['tt_cz_left'])
            tt_cx_r = safe('tt_cx_right', self.param_vars['tt_cx_right'])
            tt_cy_r = safe('tt_cy_right', self.param_vars['tt_cy_right'])
            tt_cz_r = safe('tt_cz_right', self.param_vars['tt_cz_right'])
            feed_l  = safe('feed_rate_left', self.param_vars['feed_rate_left'])
            feed_r  = safe('feed_rate_right', self.param_vars['feed_rate_right'])

            start_angle_rad = math.radians(start_angle_deg)
            angular_off_rad = math.radians(angular_off_deg)

            fil_left = self.calc_left_len.get()
            fil_right = self.calc_right_len.get()

            def nozzle_pos(cx, cy, r, angle_rad, radial_off):
                eff_r = r + radial_off
                x = cx + eff_r * math.cos(angle_rad)
                y = cy + eff_r * math.sin(angle_rad)
                return round(x,1), round(y,1)

            def pattern_offset(rev, base_x, base_y, tt_cx, tt_cy):
                if not self.pattern_enabled.get():
                    return 0.0, 0.0
                amp = safe('amp', self.pattern_amplitude)
                wave_count = safe('wave_count', self.pattern_wave_count)
                phase_offset = safe('pattern_phase_offset', self.pattern_phase_offset)
                waveform = self.pattern_waveform.get()

                phase = ((rev * wave_count) % 1.0) * 2 * math.pi + math.radians(phase_offset)

                if waveform == 'sine':
                    val = amp * math.sin(phase)
                elif waveform == 'triangle':
                    val = amp * (2/math.pi * math.asin(math.sin(phase)))
                elif waveform == 'square':
                    val = amp * (1 if math.sin(phase) >= 0 else -1)
                else:
                    val = 0.0

                dx = base_x - tt_cx
                dy = base_y - tt_cy
                length = math.hypot(dx, dy)
                if length < 0.001:
                    return 0.0, 0.0
                ux, uy = dx/length, dy/length
                return ux * val, uy * val

            rad_off_l = safe('radial_offset_left', self.param_vars['radial_offset_left'])
            rad_off_r = safe('radial_offset_right', self.param_vars['radial_offset_right'])
            right_base_x, right_base_y = nozzle_pos(tt_cx_r, tt_cy_r, radius, start_angle_rad, rad_off_r)
            left_base_x, left_base_y   = nozzle_pos(tt_cx_l, tt_cy_l, radius, start_angle_rad + angular_off_rad, rad_off_l)

            park_x, park_y, park_z = 400.0, 174.0, 180.0
            safe_z = 250.0
            base_yaw = 20.0
            left_yaw_off  = safe('left_yaw', self.offset_vars['left_Yaw'])
            right_yaw_off = safe('right_yaw', self.offset_vars['right_Yaw'])

            self.right.arm.set_position(park_x, park_y, park_z, 180,45, base_yaw, speed=100, wait=True)
            self.left.arm.set_position(park_x, park_y, park_z, 180,45, base_yaw+left_yaw_off, speed=100, wait=True)
            self.right.move_to(park_x, park_y, safe_z, 180,45, base_yaw, speed=100, wait=True)
            self.left.move_to(park_x, park_y, safe_z, 180,45, base_yaw+left_yaw_off, speed=100, wait=True)
            self.right.move_to(right_base_x, right_base_y, z_start+tt_cz_r, 180,45, base_yaw, speed=50, wait=True)
            self.left.move_to(left_base_x, left_base_y, z_start+tt_cz_l, 180,45, base_yaw+left_yaw_off, speed=50, wait=True)

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
            self.root.after(0, lambda t=total_time_sec: self.remaining_time_var.set(f"{int(t)//60:02d}:{int(t)%60:02d}"))

            CMD_INTERVAL = 0.1
            last_cmd_time = time.time()
            start_wall_time = time.time()

            waveform_speed_factor = {
                'sine': 0.8, 'triangle': 1.0, 'square': 2.0
            }

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
                    self.root.after(0, lambda t=total_time_sec: self.remaining_time_var.set(f"{int(t)//60:02d}:{int(t)%60:02d}"))

                now = time.time()
                if now - last_cmd_time < CMD_INTERVAL:
                    time.sleep(0.01)
                    continue
                last_cmd_time = now

                act_deg = self.turntable.get_angle()
                if act_deg >= target_angle_deg_tt:
                    break
                act_rad = math.radians(act_deg)
                rev = (act_rad - start_angle_rad_tt) / (2*math.pi)
                z_now = z_start + rev * pitch

                rad_off_l = safe('radial_offset_left', self.param_vars['radial_offset_left'])
                rad_off_r = safe('radial_offset_right', self.param_vars['radial_offset_right'])
                tt_cz_l = safe('tt_cz_left', self.param_vars['tt_cz_left'])
                tt_cz_r = safe('tt_cz_right', self.param_vars['tt_cz_right'])

                right_base_x, right_base_y = nozzle_pos(tt_cx_r, tt_cy_r, radius, start_angle_rad, rad_off_r)
                left_base_x, left_base_y   = nozzle_pos(tt_cx_l, tt_cy_l, radius, start_angle_rad + angular_off_rad, rad_off_l)

                if self.pattern_arm_left.get():
                    pat_dx_l, pat_dy_l = pattern_offset(rev, left_base_x, left_base_y, tt_cx_l, tt_cy_l)
                else:
                    pat_dx_l, pat_dy_l = 0,0
                if self.pattern_arm_right.get():
                    pat_dx_r, pat_dy_r = pattern_offset(rev, right_base_x, right_base_y, tt_cx_r, tt_cy_r)
                else:
                    pat_dx_r, pat_dy_r = 0,0

                lo = [safe(f'left_{ax}', self.offset_vars[f'left_{ax}']) for ax in self.axes]
                ro = [safe(f'right_{ax}', self.offset_vars[f'right_{ax}']) for ax in self.axes]

                left_x  = left_base_x  + pat_dx_l + lo[0]
                left_y  = left_base_y  + pat_dy_l + lo[1]
                left_z  = z_now + tt_cz_l + lo[2]
                right_x = right_base_x + pat_dx_r + ro[0]
                right_y = right_base_y + pat_dy_r + ro[1]
                right_z = z_now + tt_cz_r + ro[2]

                wave_count = safe('wave_count', self.pattern_wave_count)
                amp = safe('amp', self.pattern_amplitude)
                revs_per_sec = last_speed_rad / (2*math.pi)
                max_radial_speed = amp * 2 * math.pi * wave_count * revs_per_sec
                tangential_speed = 2 * math.pi * radius * revs_per_sec
                z_speed = pitch * revs_per_sec
                required_speed = math.sqrt(max_radial_speed**2 + tangential_speed**2 + z_speed**2)

                base_arm_speed = safe('base_arm_speed', self.base_arm_speed_var)
                waveform = self.pattern_waveform.get()
                factor = waveform_speed_factor.get(waveform, 1.0)
                arm_speed = max(base_arm_speed * factor, required_speed * factor)

                self.left.move_to(left_x, left_y, left_z,
                                  roll=180+lo[3], pitch=45+lo[4], yaw=base_yaw+lo[5],
                                  speed=arm_speed, wait=False)
                self.right.move_to(right_x, right_y, right_z,
                                   roll=180+ro[3], pitch=45+ro[4], yaw=base_yaw+ro[5],
                                   speed=arm_speed, wait=False)

                elapsed = time.time() - start_wall_time
                self.root.after(0, lambda e=elapsed: self.elapsed_time_var.set(f"{int(e)//60:02d}:{int(e)%60:02d}"))
                done_fraction = (act_deg - start_angle_deg_tt) / (target_angle_deg_tt - start_angle_deg_tt)
                if done_fraction > 0:
                    remaining = (elapsed / done_fraction) - elapsed
                else:
                    remaining = 0
                self.root.after(0, lambda r=remaining: self.remaining_time_var.set(f"{int(r)//60:02d}:{int(r)%60:02d}"))

            self.turntable.stop_rotation()
            time.sleep(0.5)
            self.extruder.send_gcode("M18 E X")
            lo = [safe(f'left_{ax}', self.offset_vars[f'left_{ax}']) for ax in self.axes]
            self.right.arm.set_position(park_x, park_y, park_z, 180,45, base_yaw, speed=100, wait=True)
            self.left.arm.set_position(park_x, park_y, park_z, 180,45, base_yaw+lo[5], speed=100, wait=True)
            self.printing = False
            self.paused = False
            self.root.after(0, lambda: self.pause_btn.config(text="Pause"))
            logger.info("Cylinder finished.")

        except Exception as e:
            self.printing = False
            self.paused = False
            self.root.after(0, lambda: self.pause_btn.config(text="Pause"))
            if self.turntable:
                self.turntable.stop_rotation()
            messagebox.showerror("Print Error", str(e))

    # ======================================================================
    def jog(self, axis, direction):
        if self.printing:
            messagebox.showwarning("Jog disabled", "Cannot jog during a print.")
            return
        if not self.hw_connected: return
        arm_sel = self.jog_arm.get()
        step = self.jog_step.get()
        delta = direction * step
        left_pose = self.left.get_pose() if self.left else None
        right_pose = self.right.get_pose() if self.right else None
        def apply(pose, ax):
            idx = self.axes.index(ax)
            return pose[idx] + delta
        if arm_sel in ('left', 'both') and left_pose:
            cmd = list(left_pose[:6])
            cmd[self.axes.index(axis)] = apply(left_pose, axis)
            self.left.arm.set_position(*cmd, speed=50, wait=False)
        if arm_sel in ('right', 'both') and right_pose:
            cmd = list(right_pose[:6])
            cmd[self.axes.index(axis)] = apply(right_pose, axis)
            self.right.arm.set_position(*cmd, speed=50, wait=False)

    def extruders_off(self):
        if self.extruder is None: return
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

    def update_status(self):
        if self.hw_connected:
            try:
                if self.left is not None:
                    lp = self.left.get_pose()
                    if lp:
                        self.lbl_left_pos.config(text=f"Left arm: x={lp[0]:.1f} y={lp[1]:.1f} z={lp[2]:.1f}")
                else:
                    self.lbl_left_pos.config(text="Left arm: (not connected)")
                if self.right is not None:
                    rp = self.right.get_pose()
                    if rp:
                        self.lbl_right_pos.config(text=f"Right arm: x={rp[0]:.1f} y={rp[1]:.1f} z={rp[2]:.1f}")
                else:
                    self.lbl_right_pos.config(text="Right arm: (not connected)")
                if self.extruder is not None:
                    status = self.extruder.get_printer_status()
                    t0 = status.get('extruder', {}).get('temperature', 0.0)
                    t1 = status.get('heater_bed', {}).get('temperature', 0.0)
                    self.lbl_t0.config(text=f"T0: {t0:.1f}°C")
                    self.lbl_t1.config(text=f"T1 (X axis): {t1:.1f}°C")
                if self.turntable is not None:
                    angle = self.turntable.get_angle()
                    self.lbl_turntable.config(text=f"Turntable: {angle:.1f}°")
                else:
                    self.lbl_turntable.config(text="Turntable: (not connected)")
            except Exception:
                pass
        self.root.after(1000, self.update_status)

    def on_closing(self):
        if self.printing:
            self.stop_requested = True
            time.sleep(0.5)
        self.stop_simulation()
        for dev in (self.left, self.right, self.turntable):
            try:
                if dev is not None:
                    dev.disconnect()
            except Exception:
                pass
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = ControlPanel(root)
    root.mainloop()
