#!/usr/bin/env python3
"""
Slicer / 3MF tab for the Gleadall multi-cell panel.

A self-contained Tkinter frame that:
  * imports a .3mf (or other trimesh-loadable) model,
  * slices it with the built-in slicer (walls / bottom / infill / top),
  * previews any layer,
  * plans coordinated arm + turntable motion (1 or 2 arms, turntable on/off),
  * and (when hardware is connected) streams that motion to the machines.

Heavy dependencies (trimesh / shapely / numpy via slicer.py, and planner.py)
are imported lazily inside the action handlers, so simply having this tab in the
panel never breaks the Live Control tab even if those packages aren't installed.
"""

import os
import math
import threading
import logging
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

logger = logging.getLogger("slicer_tab")

# preview colours per path kind
_KIND_COLOR = {
    "WALL_OUTER": "#1f6feb",   # blue
    "WALL_INNER": "#3fb0ff",   # light blue
    "SKIN":       "#2ea043",   # green
    "INFILL":     "#e3853b",   # orange
}


class SlicerTab:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app                     # the ControlPanel instance (controllers, params)
        self.slice_result = None
        self.program = None
        self.model_path = None
        self.printing = False
        self.stop_requested = False

        # ---- slice settings ----
        self.v_layer_height = tk.DoubleVar(value=0.2)
        self.v_line_width = tk.DoubleVar(value=0.4)
        self.v_wall_count = tk.IntVar(value=2)
        self.v_infill_density = tk.DoubleVar(value=20.0)      # percent
        self.v_infill_pattern = tk.StringVar(value="grid")
        self.v_top_layers = tk.IntVar(value=3)
        self.v_bottom_layers = tk.IntVar(value=3)

        # ---- machine / motion config ----
        self.v_num_arms = tk.IntVar(value=1)
        self.v_use_turntable = tk.BooleanVar(value=True)
        self.v_print_speed = tk.DoubleVar(value=20.0)
        self.v_travel_speed = tk.DoubleVar(value=80.0)
        self.v_max_arm_speed = tk.DoubleVar(value=100.0)
        self.v_max_tt_speed = tk.DoubleVar(value=1.5)         # rad/s
        self.v_part_off_x = tk.DoubleVar(value=0.0)
        self.v_part_off_y = tk.DoubleVar(value=0.0)
        self.v_az_left = tk.DoubleVar(value=135.0)            # deg
        self.v_az_right = tk.DoubleVar(value=130.0)           # deg
        self.v_filament = tk.DoubleVar(value=1.75)

        # calibration (prefilled from the main panel where possible)
        self.v_center_x = tk.DoubleVar(value=self._app_param('tt_cx_right', 570.0))
        self.v_center_y = tk.DoubleVar(value=self._app_param('tt_cy_right', 0.0))
        self.v_center_z = tk.DoubleVar(value=self._app_param('tt_cz_right', 151.0))
        self.v_z_base = tk.DoubleVar(value=self._app_param('z_start', 151.8))

        # preview / status
        self.v_layer_index = tk.IntVar(value=0)
        self.v_status = tk.StringVar(value="No model loaded.")
        self.stats_text = None

        self._build()

    # ------------------------------------------------------------------
    def _app_param(self, name, default):
        try:
            return float(self.app.param_vars[name].get())
        except Exception:
            return default

    # ------------------------------------------------------------------
    def _build(self):
        root = ttk.Frame(self.parent)
        root.pack(fill=tk.BOTH, expand=True)

        # ============ LEFT: controls ============
        left = ttk.Frame(root, width=430)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)

        # Connections: the SAME variables as the Live Control tab, so toggling
        # here updates there and vice-versa (one shared set of connections).
        if hasattr(self.app, "conn_left"):
            conn = ttk.LabelFrame(left, text="Connections (shared with Live Control)", padding=6)
            conn.pack(fill=tk.X, pady=(0, 6))
            crow = ttk.Frame(conn); crow.pack(fill=tk.X)
            ttk.Checkbutton(crow, text="Left arm", variable=self.app.conn_left).pack(side=tk.LEFT, padx=2)
            ttk.Checkbutton(crow, text="Right arm", variable=self.app.conn_right).pack(side=tk.LEFT, padx=2)
            ttk.Checkbutton(crow, text="Turntable", variable=self.app.conn_turntable).pack(side=tk.LEFT, padx=2)
            ttk.Checkbutton(crow, text="Extruder", variable=self.app.conn_extruder).pack(side=tk.LEFT, padx=2)
            brow = ttk.Frame(conn); brow.pack(fill=tk.X, pady=(3, 0))
            ttk.Button(brow, text="Connect Selected", command=self.app.connect_hw).pack(side=tk.LEFT, padx=2)
            ttk.Button(brow, text="Disconnect", command=self.app.disconnect_hw).pack(side=tk.LEFT, padx=2)
            ttk.Label(conn, textvariable=self.app.conn_status_var).pack(anchor="w", pady=(3, 0))

        # Import
        imp = ttk.LabelFrame(left, text="Model", padding=6)
        imp.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(imp, text="Import 3MF / STL...", command=self.import_model).pack(side=tk.LEFT)
        ttk.Label(imp, textvariable=self.v_status, wraplength=250).pack(side=tk.LEFT, padx=8)

        # Slice settings
        ss = ttk.LabelFrame(left, text="Slice Settings", padding=6)
        ss.pack(fill=tk.X, pady=(0, 6))
        self._row(ss, 0, "Layer height (mm)", self.v_layer_height)
        self._row(ss, 1, "Line width (mm)", self.v_line_width)
        self._row(ss, 2, "Wall/perimeter count", self.v_wall_count)
        self._row(ss, 3, "Infill density (%)", self.v_infill_density)
        ttk.Label(ss, text="Infill pattern").grid(row=4, column=0, sticky="w", padx=2, pady=1)
        ttk.Combobox(ss, textvariable=self.v_infill_pattern, values=["grid", "lines"],
                     width=8, state="readonly").grid(row=4, column=1, sticky="w")
        self._row(ss, 5, "Bottom layers", self.v_bottom_layers)
        self._row(ss, 6, "Top layers", self.v_top_layers)

        # Machine config
        mc = ttk.LabelFrame(left, text="Machine / Motion", padding=6)
        mc.pack(fill=tk.X, pady=(0, 6))
        arm_row = ttk.Frame(mc); arm_row.grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(arm_row, text="Arms:").pack(side=tk.LEFT)
        ttk.Radiobutton(arm_row, text="1", variable=self.v_num_arms, value=1).pack(side=tk.LEFT)
        ttk.Radiobutton(arm_row, text="2", variable=self.v_num_arms, value=2).pack(side=tk.LEFT)
        ttk.Checkbutton(mc, text="Use turntable (off = Cartesian plate)",
                        variable=self.v_use_turntable).grid(row=1, column=0, columnspan=2, sticky="w")
        self._row(mc, 2, "Print speed (mm/s)", self.v_print_speed)
        self._row(mc, 3, "Max arm speed (mm/s)", self.v_max_arm_speed)
        self._row(mc, 4, "Max turntable speed (rad/s)", self.v_max_tt_speed)
        self._row(mc, 5, "Part offset X on plate (mm)", self.v_part_off_x)
        self._row(mc, 6, "Part offset Y on plate (mm)", self.v_part_off_y)
        self._row(mc, 7, "Arm 1 azimuth (deg)", self.v_az_left)
        self._row(mc, 8, "Arm 2 azimuth (deg)", self.v_az_right)

        # Calibration
        cal = ttk.LabelFrame(left, text="Turntable Axis (arm frame)", padding=6)
        cal.pack(fill=tk.X, pady=(0, 6))
        self._row(cal, 0, "Center X", self.v_center_x)
        self._row(cal, 1, "Center Y", self.v_center_y)
        self._row(cal, 2, "Center Z", self.v_center_z)
        self._row(cal, 3, "Z base (model z=0 world)", self.v_z_base)

        # Actions
        act = ttk.Frame(left); act.pack(fill=tk.X, pady=(2, 0))
        ttk.Button(act, text="Slice + Preview", command=self.do_slice).pack(side=tk.LEFT, padx=2)
        ttk.Button(act, text="Plan Motion", command=self.do_plan).pack(side=tk.LEFT, padx=2)
        ttk.Button(act, text="Print", command=self.start_print,
                   style="Red.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(act, text="Stop", command=self.stop_print).pack(side=tk.LEFT, padx=2)

        # ============ RIGHT: preview + stats ============
        right = ttk.Frame(root)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)

        prev = ttk.LabelFrame(right, text="Layer Preview", padding=4)
        prev.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(prev, bg="white", height=360)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        scrub = ttk.Frame(prev); scrub.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(scrub, text="Layer:").pack(side=tk.LEFT)
        self.layer_slider = tk.Scale(scrub, variable=self.v_layer_index, from_=0, to=0,
                                     orient=tk.HORIZONTAL, command=lambda e: self.draw_layer())
        self.layer_slider.pack(side=tk.LEFT, fill=tk.X, expand=True)

        legend = ttk.Frame(prev); legend.pack(fill=tk.X)
        for kind, col in _KIND_COLOR.items():
            tag = ttk.Label(legend, text="  " + kind.replace("_", " ").title(), foreground=col)
            tag.pack(side=tk.LEFT, padx=4)

        stats = ttk.LabelFrame(right, text="Slice / Plan Report", padding=4)
        stats.pack(fill=tk.X, pady=(6, 0))
        self.stats_text = tk.Text(stats, height=9, wrap="word")
        self.stats_text.pack(fill=tk.X)
        self.stats_text.configure(state="disabled")

    def _row(self, parent, r, label, var):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=2, pady=1)
        ttk.Entry(parent, textvariable=var, width=10).grid(row=r, column=1, padx=2, pady=1)

    def _log(self, text, append=False):
        self.stats_text.configure(state="normal")
        if not append:
            self.stats_text.delete("1.0", tk.END)
        self.stats_text.insert(tk.END, text + "\n")
        self.stats_text.configure(state="disabled")
        self.stats_text.see(tk.END)

    # ------------------------------------------------------------------
    def _settings(self):
        from slicer import SliceSettings
        return SliceSettings(
            layer_height=float(self.v_layer_height.get()),
            line_width=float(self.v_line_width.get()),
            wall_count=int(self.v_wall_count.get()),
            infill_density=max(0.0, min(1.0, float(self.v_infill_density.get()) / 100.0)),
            infill_pattern=self.v_infill_pattern.get(),
            top_layers=int(self.v_top_layers.get()),
            bottom_layers=int(self.v_bottom_layers.get()),
        )

    def _planner_config(self):
        from planner import PlannerConfig
        az = (math.radians(float(self.v_az_left.get())),
              math.radians(float(self.v_az_right.get())))
        return PlannerConfig(
            num_arms=int(self.v_num_arms.get()),
            use_turntable=bool(self.v_use_turntable.get()),
            center=(float(self.v_center_x.get()), float(self.v_center_y.get()),
                    float(self.v_center_z.get())),
            z_base=float(self.v_z_base.get()),
            part_offset=(float(self.v_part_off_x.get()), float(self.v_part_off_y.get())),
            arm_azimuths=az,
            max_arm_speed=float(self.v_max_arm_speed.get()),
            max_tt_speed=float(self.v_max_tt_speed.get()),
            print_speed=float(self.v_print_speed.get()),
            travel_speed=float(self.v_travel_speed.get()),
            line_width=float(self.v_line_width.get()),
            layer_height=float(self.v_layer_height.get()),
            filament_diameter=float(self.v_filament.get()),
        )

    # ------------------------------------------------------------------
    def import_model(self):
        path = filedialog.askopenfilename(
            filetypes=[("3D models", "*.3mf *.stl *.obj *.ply"), ("All files", "*.*")])
        if not path:
            return
        self.model_path = path
        self.slice_result = None
        self.program = None
        self.v_status.set(f"Loaded: {os.path.basename(path)}")
        self._log(f"Imported {os.path.basename(path)}\nClick 'Slice + Preview' to slice.")

    def do_slice(self):
        if not self.model_path:
            messagebox.showwarning("Slicer", "Import a model first.")
            return
        try:
            from slicer import slice_model
        except Exception as e:
            messagebox.showerror("Slicer", f"Slicing packages unavailable:\n{e}\n\n"
                                 "Install with: pip install trimesh shapely numpy scipy networkx")
            return
        try:
            self.slice_result = slice_model(self.model_path, self._settings())
        except Exception as e:
            messagebox.showerror("Slice failed", str(e))
            return
        n = len(self.slice_result.layers)
        self.layer_slider.config(to=max(0, n - 1))
        self.v_layer_index.set(n // 2)
        self._log("Sliced: " + self.slice_result.summary())
        self.draw_layer()

    def do_plan(self):
        if not self.slice_result:
            messagebox.showwarning("Planner", "Slice a model first.")
            return
        try:
            from planner import plan, analyze, PlannerConfig
        except Exception as e:
            messagebox.showerror("Planner", f"Planner unavailable:\n{e}")
            return
        cfg = self._planner_config()
        self.program = plan(self.slice_result, cfg)
        st = analyze(self.program)
        mode = "POLAR (turntable coordinated)" if cfg.use_turntable else "CARTESIAN (fixed plate)"
        lines = [f"Motion plan: {mode}",
                 f"  steps: {len(self.program.steps)}   est. time: {st.total_time/60:.1f} min",
                 f"  arms: {cfg.num_arms}"]
        for i in range(cfg.num_arms):
            lines.append(f"  arm {i+1}: avg {st.arm_avg_speed[i]:.1f} mm/s, "
                         f"peak {st.arm_peak_speed[i]:.1f} mm/s, "
                         f"travel {st.arm_path_len[i]/1000:.2f} m")
        if cfg.use_turntable:
            lines.append(f"  turntable: peak {st.tt_peak_speed:.2f} rad/s, "
                         f"{st.tt_travel/(2*math.pi):.1f} turns, "
                         f"{st.tt_reversals} reversals")
            # quick comparison against Cartesian to show the benefit
            cfg2 = self._planner_config(); cfg2.use_turntable = False
            st2 = analyze(plan(self.slice_result, cfg2))
            if st2.arm_avg_speed[0] > 0:
                lines.append(f"  (vs Cartesian: arm travel {st2.arm_path_len[0]/1000:.2f} m -> "
                             f"{st.arm_path_len[0]/1000:.2f} m)")
        self._log("\n".join(lines))

    # ------------------------------------------------------------------
    def draw_layer(self):
        if not self.slice_result:
            return
        idx = int(self.v_layer_index.get())
        if idx < 0 or idx >= len(self.slice_result.layers):
            return
        layer = self.slice_result.layers[idx]
        c = self.canvas
        c.delete("all")
        w = c.winfo_width() or 500
        h = c.winfo_height() or 360
        b = self.slice_result.bounds
        minx, miny = float(b[0][0]), float(b[0][1])
        maxx, maxy = float(b[1][0]), float(b[1][1])
        span = max(maxx - minx, maxy - miny, 1.0)
        scale = (min(w, h) - 40) / span
        ox = w / 2 - (minx + maxx) / 2 * scale
        oy = h / 2 + (miny + maxy) / 2 * scale

        def tx(x, y):
            return (ox + x * scale, oy - y * scale)

        for p in layer.paths:
            col = _KIND_COLOR.get(p.kind, "#888888")
            pts = p.points + ([p.points[0]] if (p.closed and len(p.points) > 1) else [])
            flat = []
            for (x, y) in pts:
                px, py = tx(x, y)
                flat.extend([px, py])
            if len(flat) >= 4:
                c.create_line(*flat, fill=col, width=1)
        c.create_text(w / 2, 14,
                      text=f"Layer {idx+1}/{len(self.slice_result.layers)}  "
                           f"z={layer.z:.2f}mm  {'SOLID' if layer.solid else 'sparse'}",
                      fill="black", font=("Arial", 10, "bold"))

    # ------------------------------------------------------------------
    def start_print(self):
        if self.printing:
            return
        if not self.program:
            messagebox.showwarning("Print", "Plan the motion first.")
            return
        if not getattr(self.app, "hw_connected", False):
            messagebox.showwarning("Print", "Hardware not connected (Live Control > Connect Selected).")
            return
        if not messagebox.askyesno("Confirm print",
                                   "Stream the planned motion to the machines now?"):
            return
        self.printing = True
        self.stop_requested = False
        threading.Thread(target=self._print_thread, daemon=True).start()

    def stop_print(self):
        self.stop_requested = True

    def _connected_arms(self):
        """[(arm, tool), ...] for arms actually connected in the panel.

        The right arm carries the extruder (tool 0); the left arm (if present)
        is tool 1. Only connected arms are returned, so a single-arm print drives
        the connected arm rather than assuming the left one.
        """
        arms = []
        if getattr(self.app, "right", None) is not None:
            arms.append((self.app.right, 0))
        if getattr(self.app, "left", None) is not None:
            arms.append((self.app.left, 1))
        return arms

    def _print_thread(self):
        import time
        cfg = self.program.config
        app = self.app
        try:
            # heat + prime is assumed done via Live Control > Prepare to Print.
            active_arms = self._connected_arms()
            if len(active_arms) < cfg.num_arms:
                raise RuntimeError(
                    f"Plan uses {cfg.num_arms} arm(s) but only {len(active_arms)} "
                    "connected. Tick the arms you want in Live Control > Connections.")

            for si, step in enumerate(self.program.steps):
                if self.stop_requested:
                    break
                dt = max(step.dt, 1e-3)

                # turntable: velocity to reach the absolute target within dt
                if cfg.use_turntable and app.turntable is not None:
                    cur = app.turntable.get_angle()
                    err = ((step.tt_angle_deg - cur + 180.0) % 360.0) - 180.0
                    vel = err / dt
                    vmax = math.degrees(cfg.max_tt_speed)
                    vel = max(-vmax, min(vmax, vel))
                    app.turntable.rotate_velocity(vel)

                # arms + extrusion (only drive connected arms)
                for i, at in enumerate(step.arms):
                    if at is None or i >= len(active_arms):
                        continue
                    arm, tool = active_arms[i]
                    arm.move_to(at.x, at.y, at.z, roll=at.roll, pitch=at.pitch, yaw=at.yaw,
                                speed=cfg.max_arm_speed, wait=False)
                    if at.extrude and at.e > 0 and app.extruder is not None:
                        feed = max(0.5, at.e / dt)
                        app.extruder.extrude(tool, at.e, feed, wait=False)

                time.sleep(dt)
                if si % 25 == 0:
                    frac = (si + 1) / len(self.program.steps)
                    app.root.after(0, lambda f=frac: self.v_status.set(f"Printing... {f*100:.0f}%"))

            if app.turntable is not None:
                app.turntable.stop_rotation()
            if app.extruder is not None:
                try:
                    app.extruder.send_gcode("M18 E")
                except Exception:
                    pass
            app.root.after(0, lambda: self.v_status.set(
                "Print stopped." if self.stop_requested else "Print complete."))
        except Exception as e:
            if app.turntable is not None:
                try:
                    app.turntable.stop_rotation()
                except Exception:
                    pass
            app.root.after(0, lambda e=e: messagebox.showerror("Print error", str(e)))
        finally:
            self.printing = False
