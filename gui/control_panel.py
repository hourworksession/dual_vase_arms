import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import logging

# Use the hardware factory to create controllers
from gui.hardware_factory import (
    create_arm_controller,
    create_turntable_controller,
    create_extruder_controller,
    can_use_real_hardware,
)

logger = logging.getLogger(__name__)


class ControlPanel:
    def __init__(self, root, config):
        self.root = root
        self.root.title("Dual Arm Printer Control")
        self.config = config
        self.hw_connected = False
        self.left = self.right = self.turntable = self.extruder = None
        self.use_real = can_use_real_hardware()

        # ---------- Status frame ----------
        status_frame = ttk.LabelFrame(root, text="Live Status", padding=5)
        status_frame.pack(fill="x", padx=5, pady=5)

        self.lbl_left = ttk.Label(status_frame, text="Left arm: --")
        self.lbl_left.pack(anchor="w")
        self.lbl_right = ttk.Label(status_frame, text="Right arm: --")
        self.lbl_right.pack(anchor="w")
        self.lbl_turntable = ttk.Label(status_frame, text="Turntable: --°")
        self.lbl_turntable.pack(anchor="w")
        self.lbl_t0 = ttk.Label(status_frame, text="T0: --°C")
        self.lbl_t0.pack(anchor="w")
        self.lbl_t1 = ttk.Label(status_frame, text="T1 (X axis): --°C")
        self.lbl_t1.pack(anchor="w")

        if not self.use_real:
            ttk.Label(status_frame, text="(MOCK MODE - no hardware connected)",
                      foreground="gray").pack(anchor="w")

        # ---------- Control buttons ----------
        btn_frame = ttk.Frame(root)
        btn_frame.pack(padx=5, pady=5)

        ttk.Button(btn_frame, text="Connect All", command=self.connect_hw)\
            .grid(row=0, column=0, padx=2, pady=2)
        ttk.Button(btn_frame, text="Home All", command=self.home_all)\
            .grid(row=0, column=1, padx=2, pady=2)
        ttk.Button(btn_frame, text="Prepare to Print", command=self.prepare_to_print)\
            .grid(row=0, column=2, padx=2, pady=2)
        ttk.Button(btn_frame, text="Start Demo Print", command=self.start_print)\
            .grid(row=0, column=3, padx=2, pady=2)
        ttk.Button(btn_frame, text="EMERGENCY STOP", command=self.emergency_stop,
                   style="Red.TButton")\
            .grid(row=1, column=0, padx=2, pady=2)
        ttk.Button(btn_frame, text="Turntable +90°",
                   command=lambda: self.jog_turntable(90))\
            .grid(row=1, column=1, padx=2, pady=2)
        ttk.Button(btn_frame, text="Turntable -90°",
                   command=lambda: self.jog_turntable(-90))\
            .grid(row=1, column=2, padx=2, pady=2)

        # ---------- Synchronous extrusion test ----------
        ext_frame = ttk.LabelFrame(root, text="Synchronous Extrude Test", padding=5)
        ext_frame.pack(fill="x", padx=5, pady=5)

        ttk.Label(ext_frame, text="Length (mm):").grid(row=0, column=0, padx=2)
        self.ext_len = tk.DoubleVar(value=20.0)
        ttk.Entry(ext_frame, textvariable=self.ext_len, width=8)\
            .grid(row=0, column=1, padx=2)

        ttk.Label(ext_frame, text="E speed (mm/s):").grid(row=0, column=2, padx=2)
        self.e_speed = tk.DoubleVar(value=10.0)
        ttk.Entry(ext_frame, textvariable=self.e_speed, width=8)\
            .grid(row=0, column=3, padx=2)

        ttk.Label(ext_frame, text="X speed (mm/s):").grid(row=0, column=4, padx=2)
        self.x_speed = tk.DoubleVar(value=10.0)
        ttk.Entry(ext_frame, textvariable=self.x_speed, width=8)\
            .grid(row=0, column=5, padx=2)

        ttk.Button(ext_frame, text="Extrude Both",
                   command=self.extrude_both)\
            .grid(row=0, column=6, padx=5)

        # ---------- Style for emergency button ----------
        style = ttk.Style()
        style.configure("Red.TButton", foreground="red", font=('Arial', 10, 'bold'))

        # Start periodic status updates
        self.update_status()

    
    def open_simulator(self):
        import threading, subprocess
        threading.Thread(target=lambda: subprocess.run(["python", "simulation/web_server.py"]), daemon=True).start()
        messagebox.showinfo("Simulator", "Simulator server started. Open http://localhost:5000")

    # ---------- Hardware connection ----------
    def connect_hw(self):
        if self.hw_connected:
            messagebox.showinfo("Info", "Hardware already connected.")
            return
        try:
            self.left = create_arm_controller(
                self.config['arms']['left']['ip'], "left", self.use_real)
            self.right = create_arm_controller(
                self.config['arms']['right']['ip'], "right", self.use_real)
            self.turntable = create_turntable_controller(
                host=self.config['turntable']['controller_ip'],
                axis=self.config['turntable']['axis'],
                use_real=self.use_real)
            self.extruder = create_extruder_controller(
                self.config['moonraker']['host'],
                self.config['moonraker']['port'],
                use_real=self.use_real)

            self.left.connect()
            self.right.connect()
            self.turntable.connect()
            # Extruder doesn't need an explicit connect (HTTP)
            self.hw_connected = True
            messagebox.showinfo("Success", "All hardware connected.")
        except Exception as e:
            messagebox.showerror("Connection Error", str(e))

    # ---------- Homing ----------
    def home_all(self):
        if not self.hw_connected:
            messagebox.showwarning("Warning", "Hardware not connected.")
            return
        threading.Thread(target=self._home_all_thread, daemon=True).start()

    def _home_all_thread(self):
        try:
            self.left.home(wait=True)
            self.right.home(wait=True)
            self.root.after(0, lambda: messagebox.showinfo("Info", "Homing complete."))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Homing Error", str(e)))

    # ---------- Prepare to print ----------
    def prepare_to_print(self):
        if not self.hw_connected:
            messagebox.showwarning("Warning", "Hardware not connected.")
            return
        threading.Thread(target=self._prepare_thread, daemon=True).start()

    def _prepare_thread(self):
        try:
            temp = self.config['defaults']['temperature']['tool0']
            # Heat both
            self.extruder.set_temperature(0, temp, wait=False)
            self.extruder.send_gcode(f"SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET={temp}")
            # Move to safe position
            self.left.move_to(572.5, 225, 153, roll=180, pitch=45, yaw=0, speed=100, wait=False)
            self.right.move_to(572.5, 230, 165, roll=180, pitch=45, yaw=0, speed=100, wait=False)
            # Wait for T0
            self.extruder.heat_and_wait(0, temp)
            # Wait for heater_bed (second nozzle)
            if self.use_real:
                while True:
                    status = self.extruder.get_printer_status()
                    t1 = status.get('heater_bed', {}).get('temperature', 0.0)
                    if t1 >= temp:
                        break
                    time.sleep(2)
            else:
                self.extruder._temps["heater_bed"] = temp  # mock
            # Move to print-ready positions
            self.left.move_to(572, 202.4, 152.2, roll=180, pitch=45, yaw=0, speed=100, wait=False)
            self.right.move_to(573, 217.5, 160.9, roll=180, pitch=45, yaw=0, speed=100, wait=False)
            time.sleep(5)
            self.root.after(0, lambda: messagebox.showinfo("Info", "Ready to print."))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Prepare Error", str(e)))

    # ---------- Demo print ----------
    def start_print(self):
        if not self.hw_connected:
            messagebox.showwarning("Warning", "Hardware not connected.")
            return
        threading.Thread(target=self._demo_print_thread, daemon=True).start()

    def _demo_print_thread(self):
        try:
            from src.command_parser import Segment
            from src.synchroniser import PrintSynchroniser

            segments = [
                Segment(left_pose={"x": 300, "y": -80, "z": 80},
                        right_pose={"x": 300, "y": 80, "z": 80}),
                Segment(left_pose={"x": 500, "y": -80, "z": 80},
                        right_pose={"x": 500, "y": 80, "z": 80},
                        left_extrude=20, right_extrude=20)
            ]
            sync = PrintSynchroniser(self.left, self.right, self.extruder, self.turntable)
            sync.execute_sequence(segments)
            self.root.after(0, lambda: messagebox.showinfo("Info", "Demo print finished."))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Print Error", str(e)))

    # ---------- Emergency stop ----------
    def emergency_stop(self):
        try:
            if self.left:
                self.left.emergency_stop()
            if self.right:
                self.right.emergency_stop()
            if self.turntable:
                self.turntable.disconnect()
            if self.extruder:
                self.extruder.disable_all_heaters()
            self.hw_connected = False
            messagebox.showwarning("EMERGENCY", "Emergency stop activated! Hardware disconnected.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ---------- Turntable jog ----------
    def jog_turntable(self, angle):
        if not self.hw_connected:
            messagebox.showwarning("Warning", "Hardware not connected.")
            return
        threading.Thread(target=lambda: self._jog_turntable_thread(angle), daemon=True).start()

    def _jog_turntable_thread(self, angle):
        try:
            self.turntable.rotate_relative(angle, 30, wait=True)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Turntable Error", str(e)))

    # ---------- Synchronous extrusion test ----------
    def extrude_both(self):
        if not self.hw_connected:
            messagebox.showwarning("Warning", "Hardware not connected.")
            return
        threading.Thread(target=self._extrude_both_thread, daemon=True).start()

    def _extrude_both_thread(self):
        try:
            d_e = self.ext_len.get()
            v_e = self.e_speed.get()
            v_x = self.x_speed.get()
            if v_e <= 0 or v_x <= 0:
                raise ValueError("Speeds must be > 0")
            time_s = d_e / v_e
            d_x = v_x * time_s
            L = (d_e**2 + d_x**2)**0.5
            F = (L / time_s) * 60
            self.extruder.send_gcode(f"G91\nG1 E{d_e:.3f} X{d_x:.3f} F{F:.1f}\nG90")
            self.root.after(0, lambda: messagebox.showinfo("Extrude", f"Extruded both: E {d_e:.1f}mm, X {d_x:.1f}mm"))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Extrude Error", str(e)))

    # ---------- Status poll ----------
    def update_status(self):
        if self.hw_connected:
            try:
                left_pose = self.left.get_pose()
                right_pose = self.right.get_pose()
                if left_pose:
                    self.lbl_left.config(
                        text=f"Left arm: x={left_pose[0]:.1f} y={left_pose[1]:.1f} z={left_pose[2]:.1f}")
                if right_pose:
                    self.lbl_right.config(
                        text=f"Right arm: x={right_pose[0]:.1f} y={right_pose[1]:.1f} z={right_pose[2]:.1f}")
                # Turntable angle (mock or real)
                if hasattr(self.turntable, 'get_angle'):
                    angle = self.turntable.get_angle()
                    self.lbl_turntable.config(text=f"Turntable: {angle:.1f}°")
                # Temperatures
                status = self.extruder.get_printer_status()
                t0 = status.get('extruder', {}).get('temperature', 0.0)
                t1 = status.get('heater_bed', {}).get('temperature', 0.0)
                self.lbl_t0.config(text=f"T0: {t0:.1f}°C")
                self.lbl_t1.config(text=f"T1 (X axis): {t1:.1f}°C")
            except Exception:
                pass
        self.root.after(1000, self.update_status)

    def on_closing(self):
        if self.hw_connected:
            try:
                self.left.disconnect()
                self.right.disconnect()
                self.turntable.disconnect()
            except:
                pass
        self.root.destroy()