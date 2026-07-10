#!/usr/bin/env python3
"""
Reorganised UI layout for the Gleadall multi-cell control panel.

Kept separate from control_panel.py so it can be built and syntax-checked on its
own. `build_gui(app)` constructs the whole window on the existing ControlPanel
instance (`app`): a persistent top bar (status + confirm-first Emergency stop)
and a Notebook with three tabs -

  * Machine        - connect, home, prepare, jog / offsets / extruder dialogs,
                     live status.
  * Print a model  - the SlicerTab (slicer_tab.py).
  * Cylinder       - the parametric cylinder print + turntable simulator, with
                     cylinder parameters and wave pattern behind dialogs.

Dense settings live in Toplevel dialogs. All widgets bind to variables and
callbacks that already exist on `app`, so no print/motion logic is duplicated.
Widgets other methods rely on (canvas, sim_btn, pause_btn, lbl_* status labels)
are assigned back onto `app`.
"""

import tkinter as tk
from tkinter import ttk, messagebox


# ---------------------------------------------------------------------------
def build_gui(app):
    root = app.root
    style = ttk.Style()
    style.configure("Red.TButton", foreground="red", font=("Arial", 10, "bold"))
    style.configure("Primary.TButton", font=("Arial", 11, "bold"))

    # dialog opener used by the Print tab too
    app.open_connections_dialog = lambda: connections_dialog(app)

    # ---- persistent top bar ----
    top = ttk.Frame(root)
    top.pack(fill=tk.X, side=tk.TOP, padx=8, pady=(6, 2))
    ttk.Label(top, text="Multi-cell print control", font=("Arial", 13, "bold")).pack(side=tk.LEFT)
    ttk.Label(top, textvariable=app.conn_status_var, foreground="#555").pack(side=tk.LEFT, padx=14)
    ttk.Button(top, text="  EMERGENCY STOP  ", style="Red.TButton",
               command=lambda: emergency_stop_confirm(app)).pack(side=tk.RIGHT)
    ttk.Separator(root, orient="horizontal").pack(fill=tk.X)

    nb = ttk.Notebook(root)
    nb.pack(fill=tk.BOTH, expand=True)
    app.notebook = nb

    machine = ttk.Frame(nb); nb.add(machine, text="  Machine  ")
    _machine_tab(app, machine)

    printtab = ttk.Frame(nb); nb.add(printtab, text="  Print a model  ")
    try:
        from slicer_tab import SlicerTab
        app.slicer_tab = SlicerTab(printtab, app)
    except Exception as e:
        app.slicer_tab = None
        ttk.Label(printtab, padding=20, justify="left",
                  text=("Slicer tab could not load:\n" + str(e) + "\n\n"
                        "Ensure slicer_tab.py, slicer.py and planner.py sit next to "
                        "control_panel.py and that trimesh/shapely/numpy/scipy/networkx "
                        "are installed.")).pack(anchor="w")

    cyl = ttk.Frame(nb); nb.add(cyl, text="  Cylinder  ")
    _cylinder_tab(app, cyl)


# ---------------------------------------------------------------------------
def _machine_tab(app, parent):
    wrap = ttk.Frame(parent); wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    leftc = ttk.Frame(wrap, width=270); leftc.pack(side=tk.LEFT, fill=tk.Y)

    conn = ttk.LabelFrame(leftc, text="Connections", padding=8); conn.pack(fill=tk.X)
    for txt, var in [("Left arm", app.conn_left), ("Right arm", app.conn_right),
                     ("Turntable", app.conn_turntable), ("Extruder", app.conn_extruder)]:
        ttk.Checkbutton(conn, text=txt, variable=var).pack(anchor="w")
    brow = ttk.Frame(conn); brow.pack(fill=tk.X, pady=(4, 0))
    ttk.Button(brow, text="Connect", command=app.connect_hw).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 3))
    ttk.Button(brow, text="Disconnect", command=app.disconnect_hw).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(3, 0))

    act = ttk.LabelFrame(leftc, text="Actions", padding=8); act.pack(fill=tk.X, pady=(8, 0))
    ttk.Button(act, text="Home", command=app.home_all).pack(fill=tk.X, pady=2)
    ttk.Button(act, text="Prepare to print", command=app.prepare_to_print).pack(fill=tk.X, pady=2)
    ttk.Button(act, text="Jog...", command=lambda: jog_dialog(app)).pack(fill=tk.X, pady=2)
    ttk.Button(act, text="Live offsets...", command=lambda: offsets_dialog(app)).pack(fill=tk.X, pady=2)
    ttk.Button(act, text="Extruder / prime...", command=lambda: extruder_dialog(app)).pack(fill=tk.X, pady=2)

    rightc = ttk.Frame(wrap); rightc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))
    stat = ttk.LabelFrame(rightc, text="Live status", padding=10); stat.pack(fill=tk.X)
    app.lbl_left_pos = ttk.Label(stat, text="Left arm: --"); app.lbl_left_pos.pack(anchor="w")
    app.lbl_right_pos = ttk.Label(stat, text="Right arm: --"); app.lbl_right_pos.pack(anchor="w")
    app.lbl_turntable = ttk.Label(stat, text="Turntable: --"); app.lbl_turntable.pack(anchor="w")
    app.lbl_t0 = ttk.Label(stat, text="T0: --"); app.lbl_t0.pack(anchor="w")
    app.lbl_t1 = ttk.Label(stat, text="T1 (X axis): --"); app.lbl_t1.pack(anchor="w")

    job = ttk.LabelFrame(rightc, text="Current job", padding=10); job.pack(fill=tk.X, pady=(8, 0))
    ttk.Label(job, text="Elapsed:").grid(row=0, column=0, sticky="w")
    ttk.Label(job, textvariable=app.elapsed_time_var).grid(row=0, column=1, sticky="w", padx=8)
    ttk.Label(job, text="Remaining:").grid(row=1, column=0, sticky="w")
    ttk.Label(job, textvariable=app.remaining_time_var).grid(row=1, column=1, sticky="w", padx=8)


# ---------------------------------------------------------------------------
def _cylinder_tab(app, parent):
    wrap = ttk.Frame(parent); wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    leftc = ttk.Frame(wrap, width=260); leftc.pack(side=tk.LEFT, fill=tk.Y)

    act = ttk.LabelFrame(leftc, text="Actions", padding=8); act.pack(fill=tk.X)
    ttk.Button(act, text="▶  Start cylinder", command=app.start_cylinder,
               style="Primary.TButton").pack(fill=tk.X, pady=(0, 4))
    brow = ttk.Frame(act); brow.pack(fill=tk.X)
    app.pause_btn = ttk.Button(brow, text="Pause", command=app.toggle_pause)
    app.pause_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 3))
    ttk.Button(brow, text="Stop", command=app.stop_print).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(3, 0))
    ttk.Button(act, text="Extruders off", command=app.extruders_off).pack(fill=tk.X, pady=(4, 0))

    setg = ttk.LabelFrame(leftc, text="Settings", padding=8); setg.pack(fill=tk.X, pady=(8, 0))
    ttk.Button(setg, text="Cylinder parameters...", command=lambda: cylinder_params_dialog(app)).pack(fill=tk.X, pady=2)
    ttk.Button(setg, text="Wave pattern...", command=lambda: pattern_dialog(app)).pack(fill=tk.X, pady=2)
    ttk.Button(setg, text="Extruder / prime...", command=lambda: extruder_dialog(app)).pack(fill=tk.X, pady=2)

    tts = ttk.LabelFrame(leftc, text="Live turntable speed (rad/s)", padding=8); tts.pack(fill=tk.X, pady=(8, 0))
    app.tt_speed_slider = tk.Scale(tts, variable=app.turntable_speed_var, from_=0.0,
                                   to=getattr(app, "tt_speed_max", 2.0), resolution=0.005,
                                   orient=tk.HORIZONTAL)
    app.tt_speed_slider.pack(fill=tk.X)
    ttk.Label(tts, text="(safe to drag during a print)", foreground="#888").pack(anchor="w")

    rightc = ttk.Frame(wrap); rightc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))
    prev = ttk.LabelFrame(rightc, text="Turntable simulator / toolpath preview", padding=4)
    prev.pack(fill=tk.BOTH, expand=True)
    app.canvas = tk.Canvas(prev, bg="white", height=340)
    app.canvas.pack(fill=tk.BOTH, expand=True)
    app.canvas.bind("<Button-1>", app.on_canvas_click)
    prow = ttk.Frame(prev); prow.pack(fill=tk.X, pady=(5, 0))
    app.sim_btn = ttk.Button(prow, text="▶ Simulate", command=app.toggle_simulation)
    app.sim_btn.pack(side=tk.LEFT, padx=2)
    ttk.Button(prow, text="Update preview", command=app.update_preview).pack(side=tk.LEFT, padx=2)
    ttk.Button(prow, text="Reset nodes", command=app.reset_nodes).pack(side=tk.LEFT, padx=2)
    ttk.Button(prow, text="Save config", command=app.save_config).pack(side=tk.LEFT, padx=2)
    ttk.Button(prow, text="Load config", command=app.load_config).pack(side=tk.LEFT, padx=2)


# ---------------------------------------------------------------------------
# Dialog helpers
# ---------------------------------------------------------------------------
def _dialog(app, title):
    win = tk.Toplevel(app.root)
    win.title(title)
    win.resizable(False, False)
    try:
        win.transient(app.root)
    except Exception:
        pass
    f = ttk.Frame(win, padding=12)
    f.pack(fill=tk.BOTH, expand=True)
    return win, f


def _erow(parent, r, label, var, col=0, width=10):
    ttk.Label(parent, text=label).grid(row=r, column=col * 2, sticky="w", padx=4, pady=2)
    ttk.Entry(parent, textvariable=var, width=width).grid(row=r, column=col * 2 + 1, padx=4, pady=2)


def emergency_stop_confirm(app):
    if messagebox.askyesno("Emergency stop",
                           "Stop all motion and disable heaters now?"):
        app.emergency_stop()


def connections_dialog(app):
    win, f = _dialog(app, "Connections")
    for i, (txt, var) in enumerate([("Left arm", app.conn_left), ("Right arm", app.conn_right),
                                    ("Turntable", app.conn_turntable), ("Extruder", app.conn_extruder)]):
        ttk.Checkbutton(f, text=txt, variable=var).grid(row=i, column=0, sticky="w", padx=4, pady=1)
    ttk.Label(f, textvariable=app.conn_status_var, foreground="#555").grid(row=4, column=0, sticky="w", padx=4, pady=(6, 0))
    brow = ttk.Frame(f); brow.grid(row=5, column=0, sticky="w", pady=(8, 0))
    ttk.Button(brow, text="Connect", command=app.connect_hw).pack(side=tk.LEFT, padx=2)
    ttk.Button(brow, text="Disconnect", command=app.disconnect_hw).pack(side=tk.LEFT, padx=2)
    ttk.Button(brow, text="Close", command=win.destroy).pack(side=tk.LEFT, padx=2)


def jog_dialog(app):
    win, f = _dialog(app, "Manual jog")
    sel = ttk.Frame(f); sel.grid(row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(sel, text="Arm:").pack(side=tk.LEFT)
    for txt, val in [("Left", "left"), ("Right", "right"), ("Both", "both")]:
        ttk.Radiobutton(sel, text=txt, variable=app.jog_arm, value=val).pack(side=tk.LEFT)
    step = ttk.Frame(f); step.grid(row=1, column=0, columnspan=3, sticky="w", pady=4)
    ttk.Label(step, text="Step:").pack(side=tk.LEFT)
    for val, label in [(0.1, "0.1"), (1.0, "1"), (10.0, "10")]:
        ttk.Radiobutton(step, text=label, variable=app.jog_step, value=val).pack(side=tk.LEFT)
    ttk.Label(step, text="Custom:").pack(side=tk.LEFT, padx=(8, 0))
    ttk.Entry(step, textvariable=app.jog_custom, width=5).pack(side=tk.LEFT)
    ttk.Button(step, text="Use", command=lambda: app.jog_step.set(app.jog_custom.get())).pack(side=tk.LEFT)
    axes = [("X", 0, 0), ("Y", 0, 1), ("Z", 0, 2), ("Roll", 1, 0), ("Pitch", 1, 1), ("Yaw", 1, 2)]
    for axis, r, c in axes:
        fr = ttk.Frame(f); fr.grid(row=2 + r, column=c, padx=3, pady=3)
        ttk.Button(fr, text=f"{axis}+", width=5, command=lambda ax=axis: app.jog(ax, +1)).pack(side=tk.LEFT)
        ttk.Button(fr, text=f"{axis}-", width=5, command=lambda ax=axis: app.jog(ax, -1)).pack(side=tk.LEFT)
    ttk.Button(f, text="Close", command=win.destroy).grid(row=4, column=0, columnspan=3, pady=(10, 0))


def offsets_dialog(app):
    win, f = _dialog(app, "Live offsets (applied continuously)")
    ttk.Label(f, text="Axis").grid(row=0, column=0, padx=4)
    ttk.Label(f, text="Left").grid(row=0, column=1, padx=4)
    ttk.Label(f, text="Right").grid(row=0, column=2, padx=4)
    for i, ax in enumerate(app.axes):
        ttk.Label(f, text=ax).grid(row=i + 1, column=0, sticky="w", padx=4, pady=1)
        ttk.Entry(f, textvariable=app.offset_vars[f"left_{ax}"], width=8).grid(row=i + 1, column=1, padx=4, pady=1)
        ttk.Entry(f, textvariable=app.offset_vars[f"right_{ax}"], width=8).grid(row=i + 1, column=2, padx=4, pady=1)
    ttk.Button(f, text="Close", command=win.destroy).grid(row=len(app.axes) + 1, column=0, columnspan=3, pady=(10, 0))


def cylinder_params_dialog(app):
    win, f = _dialog(app, "Cylinder parameters")
    items = list(app.param_vars.items())
    for i, (name, var) in enumerate(items):
        label = name.replace("_", " ").title()
        _erow(f, i // 2, label, var, col=i % 2)
    base = (len(items) + 1) // 2
    _erow(f, base, "Base Arm Speed (mm/s)", app.base_arm_speed_var, col=0)
    ttk.Button(f, text="Close", command=win.destroy).grid(row=base + 1, column=0, columnspan=4, pady=(10, 0))


def pattern_dialog(app):
    win, f = _dialog(app, "Wave pattern (cylindrical wrapping)")
    ttk.Checkbutton(f, text="Enable wrapped wave", variable=app.pattern_enabled).grid(
        row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(f, text="Waveform").grid(row=1, column=0, sticky="w", padx=4, pady=2)
    ttk.Combobox(f, textvariable=app.pattern_waveform, values=["sine", "triangle", "square"],
                 width=8, state="readonly").grid(row=1, column=1, sticky="w", padx=4)
    _erow(f, 2, "Amplitude (mm)", app.pattern_amplitude)
    _erow(f, 3, "Wave count (/rev)", app.pattern_wave_count)
    _erow(f, 4, "Phase offset (deg)", app.pattern_phase_offset)
    arms = ttk.Frame(f); arms.grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))
    ttk.Label(arms, text="Apply to:").pack(side=tk.LEFT)
    ttk.Checkbutton(arms, text="Left", variable=app.pattern_arm_left).pack(side=tk.LEFT)
    ttk.Checkbutton(arms, text="Right", variable=app.pattern_arm_right).pack(side=tk.LEFT)
    ttk.Button(f, text="Close", command=win.destroy).grid(row=6, column=0, columnspan=2, pady=(10, 0))


def extruder_dialog(app):
    win, f = _dialog(app, "Extruder / prime")
    ttk.Button(f, text="Calculate path lengths",
               command=app.calculate_extrusion_lengths).grid(row=0, column=0, columnspan=2, pady=(0, 6))
    ttk.Label(f, text="Left filament (mm)").grid(row=1, column=0, sticky="w", padx=4)
    ttk.Entry(f, textvariable=app.calc_left_len, width=10, state="readonly").grid(row=1, column=1, padx=4)
    ttk.Label(f, text="Right filament (mm)").grid(row=2, column=0, sticky="w", padx=4)
    ttk.Entry(f, textvariable=app.calc_right_len, width=10, state="readonly").grid(row=2, column=1, padx=4)
    ttk.Separator(f, orient="horizontal").grid(row=3, column=0, columnspan=2, sticky="ew", pady=6)
    _erow(f, 4, "Prime length (mm)", app.prime_len)
    prow = ttk.Frame(f); prow.grid(row=5, column=0, columnspan=2, pady=(4, 0))
    ttk.Button(prow, text="Prime left", command=lambda: app.prime_extruder("left")).pack(side=tk.LEFT, padx=2)
    ttk.Button(prow, text="Prime right", command=lambda: app.prime_extruder("right")).pack(side=tk.LEFT, padx=2)
    ttk.Button(f, text="Close", command=win.destroy).grid(row=6, column=0, columnspan=2, pady=(10, 0))
