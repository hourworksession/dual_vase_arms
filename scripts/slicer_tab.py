#!/usr/bin/env python3
"""
"Print a model" tab for the Gleadall multi-cell panel.

Clean, slicer-style workflow: Import -> Slice -> Print, with the dense settings
tucked behind dialogs (Print settings, Machine + motion, Calibration). Imports
a model, slices it (walls / bottom / infill / top), previews any layer, plans
coordinated single-arm + turntable motion, and streams it to the machine.

Motion notes
------------
* Extrusion is CONTINUOUS PER PATH: one extruder.extrude(0, total_e, feed,
  wait=False) per wall loop / infill line (relative-mode async G1 E).
* Arm moves use a BLEND RADIUS via arm.arm.set_position(..., radius=..) so short
  segments don't stop at every point (smooth motion).
* 'Log Motion (dry run)' walks the whole program WITHOUT hardware and writes
  every point to motion_debug.csv plus dt statistics.

Heavy deps (trimesh/shapely via slicer.py, planner.py) are imported lazily.
"""

import os
import csv as csvmod
import math
import time
import threading
import logging
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

logger = logging.getLogger("slicer_tab")

_KIND_COLOR = {
    "WALL_OUTER": "#1f6feb",
    "WALL_INNER": "#3fb0ff",
    "SKIN":       "#2ea043",
    "INFILL":     "#e3853b",
}


class SlicerTab:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.slice_result = None
        self.program = None
        self.model_path = None
        self.printing = False
        self.stop_requested = False

        # slice settings
        self.v_layer_height = tk.DoubleVar(value=0.2)
        self.v_line_width = tk.DoubleVar(value=0.4)
        self.v_wall_count = tk.IntVar(value=2)
        self.v_infill_density = tk.DoubleVar(value=20.0)
        self.v_infill_pattern = tk.StringVar(value="grid")
        self.v_top_layers = tk.IntVar(value=3)
        self.v_bottom_layers = tk.IntVar(value=3)

        # machine / motion
        self.v_num_arms = tk.IntVar(value=1)
        self.v_use_turntable = tk.BooleanVar(value=True)
        self.v_tt_for_infill = tk.BooleanVar(value=False)
        self.v_print_speed = tk.DoubleVar(value=30.0)
        self.v_travel_speed = tk.DoubleVar(value=150.0)
        self.v_max_arm_speed = tk.DoubleVar(value=100.0)
        self.v_max_tt_speed = tk.DoubleVar(value=1.5)
        self.v_part_off_x = tk.DoubleVar(value=0.0)
        self.v_part_off_y = tk.DoubleVar(value=0.0)
        self.v_az_left = tk.DoubleVar(value=-45.0)
        self.v_az_right = tk.DoubleVar(value=135.0)
        self.v_filament = tk.DoubleVar(value=1.75)
        self.v_min_seg = tk.DoubleVar(value=0.0)
        self.v_max_seg = tk.DoubleVar(value=1.0)
        self.v_blend_radius = tk.DoubleVar(value=1.0)

        # flow
        self.v_flow = tk.DoubleVar(value=100.0)
        self.v_first_layer_flow = tk.DoubleVar(value=120.0)

        # calibration (right arm defaults from calibration.yaml)
        self.v_center_x = tk.DoubleVar(value=self._app_param('tt_cx_right', 575.6))
        self.v_center_y = tk.DoubleVar(value=self._app_param('tt_cy_right', 3.5))
        self.v_center_z = tk.DoubleVar(value=self._app_param('tt_cz_right', 152.0))
        self.v_z_base = tk.DoubleVar(value=self._app_param('z_start', 152.0))

        # debug / preview
        self.v_debug = tk.BooleanVar(value=False)
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

    def _debug_csv_path(self):
        try:
            base = os.path.dirname(os.path.abspath(__file__))
        except Exception:
            base = os.getcwd()
        return os.path.join(base, "motion_debug.csv")

    # ------------------------------------------------------------------
    def _build(self):
        root = ttk.Frame(self.parent)
        root.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(root, width=250)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=8)

        # ---- Workflow ----
        wf = ttk.LabelFrame(left, text="Workflow", padding=8)
        wf.pack(fill=tk.X)
        ttk.Label(wf, text="Step 1", foreground="#888").pack(anchor="w")
        ttk.Button(wf, text="Import model...", command=self.import_model).pack(fill=tk.X, pady=(0, 6))
        ttk.Label(wf, text="Step 2", foreground="#888").pack(anchor="w")
        ttk.Button(wf, text="Slice + preview", command=self.do_slice).pack(fill=tk.X, pady=(0, 6))
        ttk.Label(wf, text="Step 3", foreground="#888").pack(anchor="w")
        ttk.Button(wf, text="▶  Print", command=self.start_print,
                   style="Primary.TButton").pack(fill=tk.X, pady=(0, 4))
        row = ttk.Frame(wf); row.pack(fill=tk.X)
        ttk.Button(row, text="Dry run", command=self.start_dry_run).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 3))
        ttk.Button(row, text="Stop", command=self.stop_print).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(3, 0))
        ttk.Label(wf, textvariable=self.v_status, wraplength=210,
                  foreground="#555").pack(anchor="w", pady=(8, 0))

        # ---- Settings (dialogs) ----
        st = ttk.LabelFrame(left, text="Settings", padding=8)
        st.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(st, text="Print settings...", command=self._dlg_print_settings).pack(fill=tk.X, pady=2)
        ttk.Button(st, text="Machine + motion...", command=self._dlg_machine_motion).pack(fill=tk.X, pady=2)
        ttk.Button(st, text="Calibration...", command=self._dlg_calibration).pack(fill=tk.X, pady=2)
        ttk.Button(st, text="Connections...", command=self._open_connections).pack(fill=tk.X, pady=2)
        ttk.Checkbutton(st, text="Debug log while printing", variable=self.v_debug).pack(anchor="w", pady=(6, 0))

        # ---- Right: preview + report ----
        right = ttk.Frame(root)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=8)

        prev = ttk.LabelFrame(right, text="Layer preview", padding=4)
        prev.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(prev, bg="white", height=320)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        scrub = ttk.Frame(prev); scrub.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(scrub, text="Layer:").pack(side=tk.LEFT)
        self.layer_slider = tk.Scale(scrub, variable=self.v_layer_index, from_=0, to=0,
                                     orient=tk.HORIZONTAL, command=lambda e: self.draw_layer())
        self.layer_slider.pack(side=tk.LEFT, fill=tk.X, expand=True)
        legend = ttk.Frame(prev); legend.pack(fill=tk.X)
        for kind, col in _KIND_COLOR.items():
            ttk.Label(legend, text="  " + kind.replace("_", " ").title(), foreground=col).pack(side=tk.LEFT, padx=4)

        stats = ttk.LabelFrame(right, text="Report / motion debug", padding=4)
        stats.pack(fill=tk.BOTH, pady=(6, 0))
        self.stats_text = tk.Text(stats, height=12, wrap="none")
        yscroll = ttk.Scrollbar(stats, orient="vertical", command=self.stats_text.yview)
        self.stats_text.configure(yscrollcommand=yscroll.set)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.stats_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.stats_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Settings dialogs
    # ------------------------------------------------------------------
    def _drow(self, parent, r, label, var, width=10):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(parent, textvariable=var, width=width).grid(row=r, column=1, padx=4, pady=2)

    def _dialog(self, title):
        win = tk.Toplevel(self.parent)
        win.title(title)
        win.resizable(False, False)
        try:
            win.transient(self.parent.winfo_toplevel())
        except Exception:
            pass
        f = ttk.Frame(win, padding=12)
        f.pack(fill=tk.BOTH, expand=True)
        return win, f

    def _dlg_print_settings(self):
        win, f = self._dialog("Print settings")
        ttk.Label(f, text="Layers", font=("Arial", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        self._drow(f, 1, "Layer height (mm)", self.v_layer_height)
        self._drow(f, 2, "Line width (mm)", self.v_line_width)
        ttk.Label(f, text="Walls + infill", font=("Arial", 10, "bold")).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._drow(f, 4, "Wall count", self.v_wall_count)
        self._drow(f, 5, "Infill density (%)", self.v_infill_density)
        ttk.Label(f, text="Infill pattern").grid(row=6, column=0, sticky="w", padx=4, pady=2)
        ttk.Combobox(f, textvariable=self.v_infill_pattern, values=["grid", "lines"],
                     width=8, state="readonly").grid(row=6, column=1, padx=4)
        self._drow(f, 7, "Bottom layers", self.v_bottom_layers)
        self._drow(f, 8, "Top layers", self.v_top_layers)
        ttk.Label(f, text="Flow", font=("Arial", 10, "bold")).grid(row=9, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._drow(f, 10, "Flow (%)", self.v_flow)
        self._drow(f, 11, "First-layer flow (%)", self.v_first_layer_flow)
        ttk.Button(f, text="Close", command=win.destroy).grid(row=12, column=0, columnspan=2, pady=(12, 0))

    def _dlg_machine_motion(self):
        win, f = self._dialog("Machine + motion")
        ttk.Label(f, text="Arms:").grid(row=0, column=0, sticky="w", padx=4)
        arow = ttk.Frame(f); arow.grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(arow, text="1", variable=self.v_num_arms, value=1).pack(side=tk.LEFT)
        ttk.Radiobutton(arow, text="2", variable=self.v_num_arms, value=2).pack(side=tk.LEFT)
        ttk.Checkbutton(f, text="Use turntable (off = Cartesian plate)",
                        variable=self.v_use_turntable).grid(row=1, column=0, columnspan=2, sticky="w", padx=4)
        ttk.Checkbutton(f, text="Turntable coordinates infill too",
                        variable=self.v_tt_for_infill).grid(row=2, column=0, columnspan=2, sticky="w", padx=4)
        self._drow(f, 3, "Print speed (mm/s)", self.v_print_speed)
        self._drow(f, 4, "Travel speed (mm/s)", self.v_travel_speed)
        self._drow(f, 5, "Max arm speed (mm/s)", self.v_max_arm_speed)
        self._drow(f, 6, "Max turntable speed (rad/s)", self.v_max_tt_speed)
        self._drow(f, 7, "Corner blend radius (mm)", self.v_blend_radius)
        self._drow(f, 8, "Wall arc resolution (mm)", self.v_max_seg)
        self._drow(f, 9, "Min segment length (mm)", self.v_min_seg)
        self._drow(f, 10, "Part offset X (mm)", self.v_part_off_x)
        self._drow(f, 11, "Part offset Y (mm)", self.v_part_off_y)
        self._drow(f, 12, "Arm 1 azimuth (deg)", self.v_az_left)
        self._drow(f, 13, "Arm 2 azimuth (deg)", self.v_az_right)
        ttk.Button(f, text="Close", command=win.destroy).grid(row=14, column=0, columnspan=2, pady=(12, 0))

    def _dlg_calibration(self):
        win, f = self._dialog("Calibration - turntable axis (arm frame)")
        self._drow(f, 0, "Center X", self.v_center_x)
        self._drow(f, 1, "Center Y", self.v_center_y)
        self._drow(f, 2, "Center Z", self.v_center_z)
        self._drow(f, 3, "Z base (model z=0 world)", self.v_z_base)
        self._drow(f, 4, "Filament diameter (mm)", self.v_filament)
        ttk.Button(f, text="Close", command=win.destroy).grid(row=5, column=0, columnspan=2, pady=(12, 0))

    def _open_connections(self):
        opener = getattr(self.app, "open_connections_dialog", None)
        if callable(opener):
            opener()
        else:
            messagebox.showinfo("Connections", "Use the Machine tab to connect hardware.")

    # ------------------------------------------------------------------
    def _log(self, text, append=False):
        self.stats_text.configure(state="normal")
        if not append:
            self.stats_text.delete("1.0", tk.END)
        self.stats_text.insert(tk.END, text + "\n")
        self.stats_text.configure(state="disabled")
        self.stats_text.see(tk.END)

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
            turntable_for_infill=bool(self.v_tt_for_infill.get()),
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
            flow_multiplier=float(self.v_flow.get()) / 100.0,
            first_layer_flow=float(self.v_first_layer_flow.get()) / 100.0,
            min_segment_length=float(self.v_min_seg.get()),
            max_segment_length=float(self.v_max_seg.get()),
            extruder_tool=0,
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
        self._log(f"Imported {os.path.basename(path)}\nClick 'Slice + preview'.")

    def do_slice(self):
        if not self.model_path:
            messagebox.showwarning("Slicer", "Import a model first.")
            return
        try:
            from slicer import slice_model
        except Exception as e:
            messagebox.showerror("Slicer", f"Slicing packages unavailable:\n{e}\n\n"
                                 "pip install trimesh shapely numpy scipy networkx")
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
        self.do_plan()

    def do_plan(self):
        if not self.slice_result:
            messagebox.showwarning("Planner", "Slice a model first.")
            return
        try:
            from planner import plan, analyze, dt_stats, extrusion_runs
        except Exception as e:
            messagebox.showerror("Planner", f"Planner unavailable:\n{e}")
            return
        cfg = self._planner_config()
        self.program = plan(self.slice_result, cfg)
        st = analyze(self.program)
        dts = dt_stats(self.program)
        mode = "polar (turntable coordinated)" if cfg.use_turntable else "cartesian (fixed plate)"
        lines = [f"Motion plan: {mode}",
                 f"  points: {len(self.program.steps)}   est. time: {st.total_time/60:.1f} min",
                 f"  extrusion paths: {len(extrusion_runs(self.program))}",
                 f"  arm avg {st.arm_avg_speed[0]:.1f} / peak {st.arm_peak_speed[0]:.1f} mm/s"]
        if cfg.use_turntable:
            lines.append(f"  turntable: {st.tt_travel/(2*math.pi):.1f} turns, {st.tt_reversals} reversals")
        lines.append(f"  dt(ms): min {dts['dt_ms_min']} / avg {dts['dt_ms_avg']} / max {dts['dt_ms_max']}")
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
        h = c.winfo_height() or 320
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
            messagebox.showwarning("Print", "Slice a model first (Step 1 + Step 2).")
            return
        if not getattr(self.app, "hw_connected", False):
            messagebox.showwarning("Print", "Hardware not connected (Machine tab > Connect).")
            return
        if not messagebox.askyesno("Confirm print", "Stream the planned motion to the machines now?"):
            return
        self.printing = True
        self.stop_requested = False
        threading.Thread(target=self._run, kwargs=dict(dry=False), daemon=True).start()

    def start_dry_run(self):
        if self.printing:
            return
        if not self.program:
            messagebox.showwarning("Dry run", "Slice a model first (Step 1 + Step 2).")
            return
        self.printing = True
        self.stop_requested = False
        threading.Thread(target=self._run, kwargs=dict(dry=True), daemon=True).start()

    def stop_print(self):
        self.stop_requested = True

    def _connected_arms(self):
        arms = []
        if getattr(self.app, "right", None) is not None:
            arms.append((self.app.right, 0))
        if getattr(self.app, "left", None) is not None:
            arms.append((self.app.left, 1))
        return arms

    # ------------------------------------------------------------------
    def _run(self, dry=False):
        from planner import extrusion_runs, dt_stats
        prog = self.program
        cfg = prog.config
        app = self.app
        pidx = 0
        runs = extrusion_runs(prog, pidx)
        debug = bool(self.v_debug.get()) or dry
        stats = dt_stats(prog, pidx)

        active = [] if dry else self._connected_arms()
        blend = float(self.v_blend_radius.get())
        tool = cfg.extruder_tool

        try:
            if not dry and len(active) < cfg.num_arms:
                raise RuntimeError(
                    f"Plan uses {cfg.num_arms} arm(s) but only {len(active)} connected. "
                    "Tick the arms in Connections.")

            f = writer = None
            if debug:
                try:
                    f = open(self._debug_csv_path(), "w", newline="")
                    writer = csvmod.writer(f)
                    writer.writerow(["i", "t_s", "dt_ms", "layer", "kind", "move",
                                     "x", "y", "z", "yaw", "tt_deg", "e_mm", "feed_mm_s"])
                except Exception as e:
                    logger.warning("Could not open debug CSV: %s", e)

            panel_rows = []
            sample = max(1, len(prog.steps) // 40)
            t = 0.0
            for si, step in enumerate(prog.steps):
                if self.stop_requested:
                    break
                dt = max(step.dt, 1e-3)
                at = step.arms[pidx] if pidx < len(step.arms) else None
                feed_dbg = ""

                run = runs.get(si)
                if run:
                    total_e, feed = run
                    feed_dbg = feed
                    if not dry and app.extruder is not None and total_e > 0 and feed > 0:
                        app.extruder.extrude(tool, total_e, feed, wait=False)

                if not dry and cfg.use_turntable and app.turntable is not None:
                    cur = app.turntable.get_angle()
                    err = ((step.tt_angle_deg - cur + 180.0) % 360.0) - 180.0
                    vmax = math.degrees(cfg.max_tt_speed)
                    vel = max(-vmax, min(vmax, err / dt))
                    app.turntable.rotate_velocity(vel)

                if at is not None and not dry and active:
                    arm, _tool = active[pidx]
                    self._move_arm(arm, at, cfg.max_arm_speed, blend)

                if debug:
                    row = [si, round(t, 4), round(dt * 1000, 2), step.layer, step.kind,
                           ("EXTRUDE" if (at and at.extrude) else "travel"),
                           (at.x if at else ""), (at.y if at else ""), (at.z if at else ""),
                           (at.yaw if at else ""), round(step.tt_angle_deg, 3),
                           (at.e if at else 0.0), feed_dbg]
                    if writer:
                        writer.writerow(row)
                    if si < 40 or si % sample == 0:
                        panel_rows.append(row)

                if not dry:
                    time.sleep(dt)
                t += dt
                if si % 50 == 0:
                    frac = (si + 1) / max(1, len(prog.steps))
                    app.root.after(0, lambda fr=frac, d=dry:
                                   self.v_status.set(("Dry run " if d else "Printing ") + f"{fr*100:.0f}%"))

            if not dry:
                if app.turntable is not None:
                    app.turntable.stop_rotation()
                if app.extruder is not None:
                    try:
                        app.extruder.send_gcode("M18 E")
                    except Exception:
                        pass
            if f:
                f.close()

            summary = self._summary(dry, stats, runs)
            app.root.after(0, lambda s=summary, r=list(panel_rows): self._show_debug(s, r))
            app.root.after(0, lambda d=dry: self.v_status.set(
                "Dry run complete." if d else ("Stopped." if self.stop_requested else "Print complete.")))
        except Exception as e:
            if not dry and app.turntable is not None:
                try:
                    app.turntable.stop_rotation()
                except Exception:
                    pass
            app.root.after(0, lambda e=e: messagebox.showerror("Motion error", str(e)))
        finally:
            self.printing = False

    def _move_arm(self, arm, at, speed, blend):
        try:
            if blend and blend > 0:
                arm.arm.set_position(x=at.x, y=at.y, z=at.z, roll=at.roll, pitch=at.pitch,
                                     yaw=at.yaw, speed=speed, radius=blend, wait=False)
            else:
                arm.arm.set_position(x=at.x, y=at.y, z=at.z, roll=at.roll, pitch=at.pitch,
                                     yaw=at.yaw, speed=speed, wait=False)
        except TypeError:
            arm.move_to(at.x, at.y, at.z, roll=at.roll, pitch=at.pitch, yaw=at.yaw,
                        speed=speed, wait=False)

    def _summary(self, dry, stats, runs):
        return (f"{'DRY RUN' if dry else 'PRINT'} - {stats['steps']} points, "
                f"{stats['total_time_s']/60:.1f} min\n"
                f"dt(ms): min {stats['dt_ms_min']} / avg {stats['dt_ms_avg']} / max {stats['dt_ms_max']}  "
                f"(steps <5ms: {stats['steps_under_5ms']})\n"
                f"segment(mm): avg {stats['seg_mm_avg']} / max {stats['seg_mm_max']}\n"
                f"extrusion paths: {len(runs)}\n"
                f"full per-point log: {self._debug_csv_path()}")

    def _show_debug(self, summary, rows):
        self._log(summary)
        if rows:
            hdr = f"{'#':>6} {'t(s)':>8} {'dt(ms)':>7} {'lyr':>4} {'kind':<10} {'move':<8} " \
                  f"{'x':>8} {'y':>8} {'z':>7} {'ttdeg':>8} {'e':>6}"
            self._log(hdr, append=True)
            for r in rows:
                self._log(f"{r[0]:>6} {r[1]:>8} {r[2]:>7} {r[3]:>4} {str(r[4]):<10} {str(r[5]):<8} "
                          f"{str(r[6]):>8} {str(r[7]):>8} {str(r[8]):>7} {str(r[10]):>8} {str(r[11]):>6}",
                          append=True)
