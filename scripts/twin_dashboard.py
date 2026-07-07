#!/usr/bin/env python3
"""
Dual-arm printer DASHBOARD: the 3D twin nested inside the control panel.

A single Tk window with the headless PyBullet twin painted into a canvas on the
right, and control_panel_old-style controls on the left. An Offline/Online
toggle switches between:

  * Offline (simulation): controls drive the shared state and you watch the twin
    move -- no hardware required.
  * Online (connected): a setup step connects the arms / turntable / extruder,
    a poller streams live feedback into the twin, and controls also command the
    real hardware.

Requires: pybullet, numpy, pillow.
    pip install pybullet numpy pillow
    python scripts\\twin_dashboard.py
"""

import math
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tkinter as tk
from tkinter import messagebox, ttk

from src.calibration import Calibration
from src.system_state import StateSource, SystemState

try:
    from PIL import Image, ImageTk
    _PIL_OK = True
except Exception:
    _PIL_OK = False


class Dashboard:
    RENDER_MS = 66          # ~15 fps
    STATUS_MS = 500

    def __init__(self, root):
        self.root = root
        root.title("Dual-Arm Printer Dashboard")

        self.state = SystemState()
        self.cal = Calibration.load()
        self.online = False
        self.left = self.right = self.turntable = self.extruder = None
        self.poller = None
        self.cfg = None
        self.twin = None
        self._photo = None
        self._tt_cmd = 0.0
        self._sim_thread = None
        self._sim_stop = None
        self._demo_phase = ""

        self._build_ui()
        self._init_twin()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._schedule_render()
        self._schedule_status()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        main = ttk.Frame(self.root, padding=6)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="y", padx=(0, 8))
        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True)

        # --- mode ---
        mode = ttk.LabelFrame(left, text="Mode", padding=6)
        mode.pack(fill="x", pady=3)
        self.mode_var = tk.StringVar(value="offline")
        ttk.Radiobutton(mode, text="Offline (sim)", value="offline",
                        variable=self.mode_var, command=self._toggle_mode).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(mode, text="Online (connected)", value="online",
                        variable=self.mode_var, command=self._toggle_mode).grid(row=0, column=1, sticky="w")
        self.lbl_mode = ttk.Label(mode, text="● simulation", foreground="#1a7f37")
        self.lbl_mode.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # --- status ---
        st = ttk.LabelFrame(left, text="Live Status", padding=6)
        st.pack(fill="x", pady=3)
        self.lbl_left = ttk.Label(st, text="Left:  --")
        self.lbl_left.pack(anchor="w")
        self.lbl_right = ttk.Label(st, text="Right: --")
        self.lbl_right.pack(anchor="w")
        self.lbl_tt = ttk.Label(st, text="Turntable: --°")
        self.lbl_tt.pack(anchor="w")
        self.lbl_temps = ttk.Label(st, text="T0/T1: --")
        self.lbl_temps.pack(anchor="w")
        self.lbl_zone = ttk.Label(st, text="Zones: --")
        self.lbl_zone.pack(anchor="w")
        self.lbl_demo = ttk.Label(st, text="", foreground="#1a5fb4")
        self.lbl_demo.pack(anchor="w")

        # --- manual control ---
        mc = ttk.LabelFrame(left, text="Manual Coordinate Control", padding=6)
        mc.pack(fill="x", pady=3)
        self.arm_var = tk.StringVar(value="left")
        ttk.Radiobutton(mc, text="Left", value="left", variable=self.arm_var).grid(row=0, column=0)
        ttk.Radiobutton(mc, text="Right", value="right", variable=self.arm_var).grid(row=0, column=1)
        self.coords = {}
        labels = [("X", 0), ("Y", 0), ("Z", 160), ("Roll", 180), ("Pitch", 45), ("Yaw", 0)]
        for i, (name, default) in enumerate(labels):
            ttk.Label(mc, text=name).grid(row=1 + i // 2, column=(i % 2) * 2, sticky="e", padx=2)
            v = tk.DoubleVar(value=float(default))
            self.coords[name] = v
            ttk.Entry(mc, textvariable=v, width=8).grid(row=1 + i // 2, column=(i % 2) * 2 + 1, padx=2, pady=1)
        ttk.Button(mc, text="Send", command=self.send_move).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        # quick jog
        jg = ttk.Frame(mc)
        jg.grid(row=6, column=0, columnspan=4, pady=4)
        for ax in ("X", "Y", "Z"):
            ttk.Label(jg, text=ax).pack(side="left")
            ttk.Button(jg, text="–", width=2, command=lambda a=ax: self.jog(a, -5)).pack(side="left")
            ttk.Button(jg, text="+", width=2, command=lambda a=ax: self.jog(a, 5)).pack(side="left", padx=(0, 6))

        # --- turntable + actions ---
        ax = ttk.LabelFrame(left, text="Turntable / Actions", padding=6)
        ax.pack(fill="x", pady=3)
        ttk.Button(ax, text="TT +90°", command=lambda: self.jog_turntable(90)).grid(row=0, column=0, padx=2, pady=2)
        ttk.Button(ax, text="TT −90°", command=lambda: self.jog_turntable(-90)).grid(row=0, column=1, padx=2, pady=2)
        ttk.Button(ax, text="Demo circle", command=self.start_demo).grid(row=1, column=0, padx=2, pady=2)
        ttk.Button(ax, text="Stop demo", command=self.stop_demo).grid(row=1, column=1, padx=2, pady=2)
        ttk.Button(ax, text="Home", command=self.home_all).grid(row=2, column=0, padx=2, pady=2)
        ttk.Button(ax, text="EMERGENCY STOP", command=self.emergency_stop,
                   style="Red.TButton").grid(row=2, column=1, padx=2, pady=2)

        # --- extrude ---
        ex = ttk.LabelFrame(left, text="Synchronous Extrude (online)", padding=6)
        ex.pack(fill="x", pady=3)
        ttk.Label(ex, text="mm").grid(row=0, column=0)
        self.ext_len = tk.DoubleVar(value=5.0)
        ttk.Entry(ex, textvariable=self.ext_len, width=6).grid(row=0, column=1)
        ttk.Label(ex, text="mm/s").grid(row=0, column=2)
        self.ext_rate = tk.DoubleVar(value=5.0)
        ttk.Entry(ex, textvariable=self.ext_rate, width=6).grid(row=0, column=3)
        ttk.Button(ex, text="Extrude Both", command=self.extrude_both).grid(row=0, column=4, padx=4)

        style = ttk.Style()
        style.configure("Red.TButton", foreground="red", font=("Arial", 10, "bold"))

        # --- 3D view ---
        view = ttk.LabelFrame(right, text="3D Twin", padding=4)
        view.pack(fill="both", expand=True)
        self.canvas = tk.Label(view, background="#202028", cursor="fleur")
        self.canvas.pack(fill="both", expand=True)
        # live orbit: click-drag to rotate, wheel to zoom
        self._drag = None
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        self.canvas.bind("<MouseWheel>", self._on_wheel)          # Windows / macOS
        self.canvas.bind("<Button-4>", lambda e: self._wheel(1))   # Linux up
        self.canvas.bind("<Button-5>", lambda e: self._wheel(-1))  # Linux down
        cam = ttk.Frame(right)
        cam.pack(fill="x")
        ttk.Button(cam, text="◀", width=3, command=lambda: self.twin and self.twin.orbit(d_yaw=-10)).pack(side="left")
        ttk.Button(cam, text="▶", width=3, command=lambda: self.twin and self.twin.orbit(d_yaw=10)).pack(side="left")
        ttk.Button(cam, text="▲", width=3, command=lambda: self.twin and self.twin.orbit(d_pitch=-8)).pack(side="left")
        ttk.Button(cam, text="▼", width=3, command=lambda: self.twin and self.twin.orbit(d_pitch=8)).pack(side="left")
        ttk.Button(cam, text="＋", width=3, command=lambda: self.twin and self.twin.zoom(0.85)).pack(side="left", padx=(8, 0))
        ttk.Button(cam, text="－", width=3, command=lambda: self.twin and self.twin.zoom(1.18)).pack(side="left")

    # --------------------------------------------------------------- twin
    def _init_twin(self):
        if not _PIL_OK:
            self.canvas.config(text="Pillow not installed.\n\npip install pillow",
                               foreground="white", font=("Arial", 12))
            return
        try:
            from simulation.embedded_twin import EmbeddedTwin
            self.twin = EmbeddedTwin.from_state(self.state, self.cal)
        except Exception as e:
            self.canvas.config(text=f"3D twin unavailable:\n{e}",
                               foreground="white", font=("Arial", 11))
            self.twin = None

    def _on_drag_start(self, event):
        self._drag = (event.x, event.y)

    def _on_drag(self, event):
        if self.twin is None or self._drag is None:
            return
        dx = event.x - self._drag[0]
        dy = event.y - self._drag[1]
        self._drag = (event.x, event.y)
        self.twin.orbit(d_yaw=-dx * 0.4, d_pitch=dy * 0.4)

    def _on_wheel(self, event):
        self._wheel(1 if event.delta > 0 else -1)

    def _wheel(self, direction):
        if self.twin is not None:
            self.twin.zoom(0.88 if direction > 0 else 1.14)

    def _schedule_render(self):
        if self.twin is not None and _PIL_OK:
            w = max(320, self.canvas.winfo_width())
            h = max(280, self.canvas.winfo_height())
            try:
                rgb = self.twin.update_and_render(min(w, 720), min(h, 600))
                img = Image.fromarray(rgb)
                self._photo = ImageTk.PhotoImage(img)
                self.canvas.config(image=self._photo)
            except Exception:
                pass
        self.root.after(self.RENDER_MS, self._schedule_render)

    # ------------------------------------------------------------- control
    def _read_pose(self):
        c = self.coords
        return (c["X"].get(), c["Y"].get(), c["Z"].get(),
                c["Roll"].get(), c["Pitch"].get(), c["Yaw"].get())

    def _push(self, arm, pose):
        if arm == "left":
            self.state.update_left(tuple(pose), StateSource.COMMANDED)
        else:
            self.state.update_right(tuple(pose), StateSource.COMMANDED)
        # offline: a pose command should drive IK, not a stale home joint set
        if not self.online:
            self.state.clear_joints(arm)

    def send_move(self):
        arm = self.arm_var.get()
        pose = self._read_pose()
        self._push(arm, pose)
        if self.online:
            ctrl = self.left if arm == "left" else self.right
            if ctrl:
                try:
                    ctrl.arm.set_position(*pose, speed=50, wait=False)
                except Exception as e:
                    messagebox.showerror("Move failed", str(e))

    def jog(self, axis, delta):
        self.coords[axis].set(self.coords[axis].get() + delta)
        self.send_move()

    def jog_turntable(self, angle):
        self._tt_cmd += angle
        self.state.update_turntable(self._tt_cmd, StateSource.COMMANDED)
        if self.online and self.turntable:
            try:
                self.turntable.rotate_relative(angle, 30, wait=False)
            except Exception as e:
                messagebox.showerror("Turntable failed", str(e))

    # ----- demo (sim) -----
    def start_demo(self):
        if self._sim_thread and self._sim_thread.is_alive():
            return
        self._sim_stop = threading.Event()
        self._sim_thread = threading.Thread(target=self._demo_loop, daemon=True)
        self._sim_thread.start()

    def stop_demo(self):
        if self._sim_stop:
            self._sim_stop.set()
        self._demo_phase = ""

    # ----- world-pose helpers for smooth, phased motion -----
    def _push_world(self, arm, wp):
        """Command an arm to a WORLD pose (height z is mm above the disc)."""
        self._push(arm, tuple(self.cal.world_to_arm(arm, wp)))

    def _snap_world(self, posed):
        for arm, wp in posed.items():
            self._push_world(arm, wp)

    def _sleep(self, secs):
        end = time.time() + secs
        while time.time() < end:
            if self._sim_stop.is_set():
                return True
            time.sleep(0.03)
        return False

    def _smooth_world(self, a_poses, b_poses, duration):
        """Ease both arms from a_poses to b_poses (world frame) over duration."""
        t0 = time.time()
        while True:
            if self._sim_stop.is_set():
                return True
            t = time.time() - t0
            if t >= duration:
                break
            f = t / duration
            f = f * f * (3 - 2 * f)            # smoothstep easing
            for arm in a_poses:
                a, b = a_poses[arm], b_poses[arm]
                wp = tuple(a[i] + (b[i] - a[i]) * f for i in range(6))
                self._push_world(arm, wp)
            time.sleep(1 / 60)
        self._snap_world(b_poses)
        return False

    def _demo_loop(self):
        """Full sequence: home -> prepare -> move to print -> spin turntable,
        with eased transitions between phases (the disc spinning under the
        stationary nozzle is what traces the printed circle)."""
        sz = self.cal.safe_zones
        R = 150.0
        arms = ("left", "right")

        def wp(arm, r, z, pitch):
            a = math.radians(sz.center_deg(arm))
            return (r * math.cos(a), r * math.sin(a), z, 180.0, pitch, 0.0)

        home = {a: wp(a, 70.0, 250.0, 0.0) for a in arms}     # tucked up, nozzle straight
        prepare = {a: wp(a, R, 70.0, 45.0) for a in arms}      # poised above the spot, 45°
        printpos = {a: wp(a, R, 2.0, 45.0) for a in arms}      # down on the disc, 45°

        self._tt_cmd = 0.0
        self.state.update_turntable(0.0, StateSource.COMMANDED)

        self._demo_phase = "1/4  Homing…"
        self._snap_world(home)
        if self._sleep(1.3):
            return
        self._demo_phase = "2/4  Preparing to print…"
        if self._smooth_world(home, prepare, 2.6):
            return
        if self._sleep(0.5):
            return
        self._demo_phase = "3/4  Moving to print position…"
        if self._smooth_world(prepare, printpos, 2.0):
            return
        self._demo_phase = "4/4  Printing (turntable rotating)…"
        t0 = time.time()
        while not self._sim_stop.is_set():
            for arm in arms:
                self._push_world(arm, printpos[arm])
            self._tt_cmd = ((time.time() - t0) * 20.0) % 360.0
            self.state.update_turntable(self._tt_cmd, StateSource.COMMANDED)
            time.sleep(1 / 60)
        self._demo_phase = ""

    def extrude_both(self):
        if not (self.online and self.extruder):
            messagebox.showinfo("Extrude", "Extrusion runs in Online mode with hardware connected.")
            return
        length = self.ext_len.get()
        feed = self.ext_rate.get() * 60
        try:
            self.extruder.send_gcode(f"G91\nG1 E{length:.3f} X{length:.3f} F{feed:.1f}\nG90")
        except Exception as e:
            messagebox.showerror("Extrude failed", str(e))

    # home joint configuration (deg), xArm joint order
    HOME_JOINTS_DEG = [0.0, -45.0, -45.0, 0.0, 90.0, 0.0]

    def home_all(self):
        # works offline (poses the twin) and online (also commands the arms)
        self.stop_demo()
        self.state.update_left(None, StateSource.COMMANDED, joints=self.HOME_JOINTS_DEG)
        self.state.update_right(None, StateSource.COMMANDED, joints=self.HOME_JOINTS_DEG)
        self._tt_cmd = 0.0
        self.state.update_turntable(0.0, StateSource.COMMANDED)
        if self.online and self.left and self.right:
            try:
                self.left.home(wait=False)
                self.right.home(wait=True)
            except Exception as e:
                messagebox.showerror("Home failed", str(e))

    def emergency_stop(self):
        try:
            if self.left:
                self.left.emergency_stop()
            if self.right:
                self.right.emergency_stop()
            if self.extruder:
                self.extruder.disable_all_heaters()
        except Exception:
            pass
        self.stop_demo()
        messagebox.showwarning("E-STOP", "Emergency stop sent.")

    # ----- mode toggle / setup -----
    def _toggle_mode(self):
        want_online = self.mode_var.get() == "online"
        if want_online and not self.online:
            if not messagebox.askokcancel(
                "Go Online — setup",
                "This will connect to the real hardware:\n"
                "  • left + right xArm 850\n  • Aerotech turntable\n  • Hemera extruder\n\n"
                "Arms may move when commanded. Continue?",
            ):
                self.mode_var.set("offline")
                return
            threading.Thread(target=self._connect_sequence, daemon=True).start()
        elif not want_online and self.online:
            self._go_offline()

    def _connect_sequence(self):
        try:
            from config_loader import load_config
            from src.arm_controller import ArmController
            from src.turntable_controller import TurntableController
            from src.extruder_controller import ExtruderController
            from simulation.state_provider import poller_from_controllers

            self.cfg = load_config()
            self.left = ArmController(self.cfg["arms"]["left"]["ip"], "left")
            self.right = ArmController(self.cfg["arms"]["right"]["ip"], "right")
            self.turntable = TurntableController(host=self.cfg["turntable"]["controller_ip"],
                                                 axis=self.cfg["turntable"]["axis"])
            self.extruder = ExtruderController(self.cfg["moonraker"]["host"],
                                               self.cfg["moonraker"]["port"])
            self.left.connect()
            self.right.connect()
            self.turntable.connect()
            self.poller = poller_from_controllers(
                self.state, is_live=lambda: self.online,
                left=self.left, right=self.right,
                turntable=self.turntable, extruder=self.extruder, hz=15.0,
            )
            self.online = True
            self.poller.start()
            self.root.after(0, lambda: self.lbl_mode.config(text="● connected — LIVE", foreground="#b3261e"))
        except Exception as e:
            self.online = False
            self.root.after(0, lambda e=e: (
                self.mode_var.set("offline"),
                messagebox.showerror("Connection failed", str(e)),
                self.lbl_mode.config(text="● simulation", foreground="#1a7f37"),
            ))

    def _go_offline(self):
        self.online = False
        try:
            if self.poller:
                self.poller.stop(join=False)
            for c in (self.left, self.right, self.turntable):
                if c:
                    try:
                        c.disconnect()
                    except Exception:
                        pass
        finally:
            self.poller = None
            self.left = self.right = self.turntable = self.extruder = None
            self.lbl_mode.config(text="● simulation", foreground="#1a7f37")

    # ----- status -----
    def _schedule_status(self):
        snap = self.state.snapshot()
        sz = self.cal.safe_zones

        def fmt(p):
            return "--" if p is None else f"x={p[0]:.0f} y={p[1]:.0f} z={p[2]:.0f}"
        self.lbl_left.config(text=f"Left:  {fmt(snap.left_pose)}")
        self.lbl_right.config(text=f"Right: {fmt(snap.right_pose)}")
        self.lbl_tt.config(text=f"Turntable: {snap.turntable_deg:.0f}°" if snap.turntable_deg is not None else "Turntable: --°")
        t0 = snap.temps_c.get("tool0")
        t1 = snap.temps_c.get("bed")
        self.lbl_temps.config(text=f"T0: {t0:.0f}°C  Bed: {t1:.0f}°C" if t0 is not None else "T0/Bed: --")
        zones = []
        for arm, pose in (("left", snap.left_pose), ("right", snap.right_pose)):
            if pose is not None:
                wx, wy, *_ = self.cal.arm_to_world(arm, pose)
                zones.append(f"{arm[0].upper()}→{sz.zone_of(wx, wy)}")
        self.lbl_zone.config(text="Zones: " + (", ".join(zones) if zones else "--"))
        self.lbl_demo.config(text=self._demo_phase)
        self.root.after(self.STATUS_MS, self._schedule_status)

    def _on_close(self):
        self.stop_demo()
        self._go_offline()
        if self.twin:
            self.twin.disconnect()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    Dashboard(root)
    root.mainloop()
