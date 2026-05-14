#!/usr/bin/env python3
import sys, os, time, threading, queue
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
import tkinter as tk
from tkinter import ttk
from src.arm_controller import ArmController
from src.turntable_controller import TurntableController
from src.extruder_controller import ExtruderController
from config_loader import load_config

class ControlPanel:
    def __init__(self, root):
        self.root = root
        self.root.title("Dual Arm Printer Control")
        self.cfg = load_config()
        self.hw_connected = False
        self.left = self.right = self.turntable = self.extruder = None
        self.running = True

        # --- Status section ---
        status_frame = ttk.LabelFrame(root, text="Live Status", padding=5)
        status_frame.pack(fill="x", padx=5, pady=5)

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

        # --- Control buttons ---
        btn_frame = ttk.Frame(root)
        btn_frame.pack(padx=5, pady=5)

        ttk.Button(btn_frame, text="Connect All", command=self.connect_hw).grid(row=0, column=0, padx=2, pady=2)
        ttk.Button(btn_frame, text="Home All", command=self.home_all).grid(row=0, column=1, padx=2, pady=2)
        ttk.Button(btn_frame, text="Prepare to Print", command=self.prepare_to_print).grid(row=0, column=2, padx=2, pady=2)
        ttk.Button(btn_frame, text="Start Demo Print", command=self.start_print).grid(row=0, column=3, padx=2, pady=2)
        ttk.Button(btn_frame, text="EMERGENCY STOP", command=self.emergency_stop, style="Red.TButton").grid(row=1, column=0, padx=2, pady=2)
        ttk.Button(btn_frame, text="Turntable +90°", command=lambda: self.jog_turntable(90)).grid(row=1, column=1, padx=2, pady=2)
        ttk.Button(btn_frame, text="Turntable -90°", command=lambda: self.jog_turntable(-90)).grid(row=1, column=2, padx=2, pady=2)

        # Extrusion test (synchronous)
        ext_frame = ttk.LabelFrame(root, text="Synchronous Extrude Test", padding=5)
        ext_frame.pack(fill="x", padx=5, pady=5)
        ttk.Label(ext_frame, text="Length (mm):").grid(row=0, column=0)
        self.ext_len = tk.DoubleVar(value=5.0)
        ttk.Entry(ext_frame, textvariable=self.ext_len, width=8).grid(row=0, column=1)
        ttk.Label(ext_frame, text="Rate (mm/s):").grid(row=0, column=2)
        self.ext_rate = tk.DoubleVar(value=5.0)
        ttk.Entry(ext_frame, textvariable=self.ext_rate, width=8).grid(row=0, column=3)
        ttk.Button(ext_frame, text="Extrude Both", command=self.extrude_both).grid(row=0, column=4, padx=5)

        # Style for emergency button
        style = ttk.Style()
        style.configure("Red.TButton", foreground="red", font=('Arial', 10, 'bold'))

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Start periodic status update
        self.update_status()

    def connect_hw(self):
        try:
            self.left = ArmController(self.cfg['arms']['left']['ip'], "left")
            self.right = ArmController(self.cfg['arms']['right']['ip'], "right")
            self.turntable = TurntableController(host=self.cfg['turntable']['controller_ip'],
                                                 axis=self.cfg['turntable']['axis'])
            self.extruder = ExtruderController(self.cfg['moonraker']['host'],
                                               self.cfg['moonraker']['port'])
            self.left.connect()
            self.right.connect()
            self.turntable.connect()
            self.hw_connected = True
            print("All hardware connected")
        except Exception as e:
            print(f"Connection failed: {e}")

    def home_all(self):
        if not self.hw_connected: return
        self.left.home(wait=True)
        self.right.home(wait=True)
        # Turntable already homed in connect()
        print("All axes homed")

    def prepare_to_print(self):
        if not self.hw_connected: return
        temp = self.cfg['defaults']['temperature']['tool0']
        self.extruder.set_temperature(0, temp, wait=False)
        # Set the generic heater temperature manually (needs a new method)
        self.extruder.send_gcode(f"SET_HEATER_TEMPERATURE HEATER=nozzle1_heater TARGET={temp}")
        # Move arms to safe position
        self.left.arm.set_position(572.5, 225, 153, 180, 45, 0, speed=100, wait=False)
        self.right.arm.set_position(572.5, 230, 165, 180, 45, 0, speed=100, wait=False)
        # Wait for T0 to heat
        self.extruder.heat_and_wait(0, temp)
        # Wait for the generic heater (polling)
        print("Waiting for nozzle1 heater...")
        while True:
            status = self.extruder.get_printer_status()
            t1 = status.get('heater_generic nozzle1_heater', {}).get('temperature', 0.0)
            if t1 >= temp:
                break
            time.sleep(2)
        # Move to print-ready positions
        self.left.arm.set_position(572, 202.4, 152.2, 180, 45, 0, speed=100, wait=False)
        self.right.arm.set_position(573, 217.5, 160.9, 180, 45, 0, speed=100, wait=False)
        time.sleep(5)
        print("Ready to print")

    def start_print(self):
        # Run your dual_print_demo.py logic in a thread
        threading.Thread(target=self._run_demo, daemon=True).start()

    def _run_demo(self):
        from src.command_parser import Segment
        from src.synchroniser import PrintSynchroniser
        segments = [
            Segment(left_pose={"x":300,"y":-80,"z":80}, right_pose={"x":300,"y":80,"z":80}),
            Segment(left_pose={"x":500,"y":-80,"z":80}, right_pose={"x":500,"y":80,"z":80},
                    left_extrude=20, right_extrude=20)
        ]
        sync = PrintSynchroniser(self.left, self.right, self.extruder, self.turntable)
        sync.execute_sequence(segments)
        print("Demo print finished")

    def emergency_stop(self):
        if self.left: self.left.emergency_stop()
        if self.right: self.right.emergency_stop()
        if self.turntable: self.turntable.disconnect()
        if self.extruder: self.extruder.disable_all_heaters()
        print("EMERGENCY STOP ACTIVATED")

    def jog_turntable(self, angle):
        if self.turntable:
            self.turntable.rotate_relative(angle, 30, wait=True)

    def extrude_both(self):
        if not self.extruder: return
        length = self.ext_len.get()
        rate_mm_s = self.ext_rate.get()
        feedrate = rate_mm_s * 60  # mm/min
        # Synchronous move: G1 E{len} X{len} F{feedrate}
        self.extruder.send_gcode(f"G91\nG1 E{length:.3f} X{length:.3f} F{feedrate:.1f}\nG90")

    def update_status(self):
        if self.hw_connected:
            try:
                # Arm positions
                left_pose = self.left.get_pose()
                right_pose = self.right.get_pose()
                if left_pose:
                    self.lbl_left_pos.config(text=f"Left arm: x={left_pose[0]:.1f} y={left_pose[1]:.1f} z={left_pose[2]:.1f}")
                if right_pose:
                    self.lbl_right_pos.config(text=f"Right arm: x={right_pose[0]:.1f} y={right_pose[1]:.1f} z={right_pose[2]:.1f}")
                # Temperatures
                status = self.extruder.get_printer_status()
                t0 = status.get('extruder', {}).get('temperature', 0.0)
                t1 = status.get('heater_generic nozzle1_heater', {}).get('temperature', 0.0)
                self.lbl_t0.config(text=f"T0: {t0:.1f}°C")
                self.lbl_t1.config(text=f"T1 (X axis): {t1:.1f}°C")
            except Exception as e:
                pass
        self.root.after(1000, self.update_status)

    def on_closing(self):
        self.running = False
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = ControlPanel(root)
    root.mainloop()