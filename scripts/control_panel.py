#!/usr/bin/env python3
import sys, os, time, threading, queue
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
import tkinter as tk
import math

from tkinter import ttk
from tkinter import messagebox
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

        # Virtual base offsets for manual coordinate entry
        self.left_base = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]   # x, y, z, roll, pitch, yaw
        self.right_base = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

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
        ttk.Button(btn_frame, text="Start Spiral", command=lambda: threading.Thread(target=self._run_spiral, daemon=True).start()).grid(row=0, column=4, padx=2, pady=2)
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

        # Manual coordinate control with virtual base reset 
        coord_frame = ttk.LabelFrame(root, text="Manual Coordinate Control", padding=5)
        coord_frame.pack(fill="x", padx=5, pady=5)

        # Coordinate input fields
        self.coord_x = tk.DoubleVar(value=0.0)
        self.coord_y = tk.DoubleVar(value=0.0)
        self.coord_z = tk.DoubleVar(value=0.0)
        self.coord_roll = tk.DoubleVar(value=0.0)
        self.coord_pitch = tk.DoubleVar(value=0.0)
        self.coord_yaw = tk.DoubleVar(value=0.0)

        ttk.Label(coord_frame, text="X").grid(row=0, column=0, padx=2)
        ttk.Entry(coord_frame, textvariable=self.coord_x, width=8).grid(row=0, column=1, padx=2)
        ttk.Label(coord_frame, text="Y").grid(row=0, column=2, padx=2)
        ttk.Entry(coord_frame, textvariable=self.coord_y, width=8).grid(row=0, column=3, padx=2)
        ttk.Label(coord_frame, text="Z").grid(row=0, column=4, padx=2)
        ttk.Entry(coord_frame, textvariable=self.coord_z, width=8).grid(row=0, column=5, padx=2)
        ttk.Label(coord_frame, text="Roll").grid(row=0, column=6, padx=2)
        ttk.Entry(coord_frame, textvariable=self.coord_roll, width=8).grid(row=0, column=7, padx=2)
        ttk.Label(coord_frame, text="Pitch").grid(row=0, column=8, padx=2)
        ttk.Entry(coord_frame, textvariable=self.coord_pitch, width=8).grid(row=0, column=9, padx=2)
        ttk.Label(coord_frame, text="Yaw").grid(row=0, column=10, padx=2)
        ttk.Entry(coord_frame, textvariable=self.coord_yaw, width=8).grid(row=0, column=11, padx=2)

        # Buttons row 1
        ttk.Button(coord_frame, text="Send to Left", command=self.send_left_move).grid(row=1, column=0, columnspan=4, pady=5, sticky="ew")
        ttk.Button(coord_frame, text="Send to Right", command=self.send_right_move).grid(row=1, column=4, columnspan=4, pady=5, sticky="ew")
        ttk.Button(coord_frame, text="Set Left Base", command=self.set_left_base).grid(row=1, column=8, columnspan=2, pady=5, sticky="ew")
        ttk.Button(coord_frame, text="Clear Left Base", command=self.clear_left_base).grid(row=1, column=10, columnspan=2, pady=5, sticky="ew")

        # Buttons row 2
        ttk.Button(coord_frame, text="Set Right Base", command=self.set_right_base).grid(row=2, column=0, columnspan=4, pady=5, sticky="ew")
        ttk.Button(coord_frame, text="Clear Right Base", command=self.clear_right_base).grid(row=2, column=4, columnspan=4, pady=5, sticky="ew")

        # Offset display labels
        self.lbl_left_offset = ttk.Label(coord_frame, text="Left offset: (0,0,0,0,0,0)")
        self.lbl_left_offset.grid(row=3, column=0, columnspan=6, sticky="w", padx=2)
        self.lbl_right_offset = ttk.Label(coord_frame, text="Right offset: (0,0,0,0,0,0)")
        self.lbl_right_offset.grid(row=3, column=6, columnspan=6, sticky="w", padx=2)

        # Style for emergency button
        style = ttk.Style()
        style.configure("Red.TButton", foreground="red", font=('Arial', 10, 'bold'))

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Start periodic status update
        self.update_status()

    def send_left_move(self):
        """Apply entered coordinates, adjusted by left virtual base offset."""
        if not self.hw_connected or not self.left:
            messagebox.showwarning("Warning", "Left arm not connected.")
            return
        target = self._get_adjusted_coords(self.left_base)
        self.left.arm.set_position(*target, speed=50, wait=False)

    def send_right_move(self):
        if not self.hw_connected or not self.right:
            messagebox.showwarning("Warning", "Right arm not connected.")
            return
        target = self._get_adjusted_coords(self.right_base)
        self.right.arm.set_position(*target, speed=50, wait=False)

    def _get_adjusted_coords(self, base_offset):
        """Return list of 6 coordinates: entered value minus base offset."""
        return [
            self.coord_x.get() - base_offset[0],
            self.coord_y.get() - base_offset[1],
            self.coord_z.get() - base_offset[2],
            self.coord_roll.get() - base_offset[3],
            self.coord_pitch.get() - base_offset[4],
            self.coord_yaw.get() - base_offset[5]
        ]

    def set_left_base(self):
        """Record left arm's current pose as virtual zero."""
        if not self.hw_connected or not self.left:
            return
        pose = self.left.get_pose()
        if pose:
            self.left_base = list(pose[:6])
            self._update_offset_label()

    def clear_left_base(self):
        self.left_base = [0.0]*6
        self._update_offset_label()

    def set_right_base(self):
        if not self.hw_connected or not self.right:
            return
        pose = self.right.get_pose()
        if pose:
            self.right_base = list(pose[:6])
            self._update_offset_label()

    def clear_right_base(self):
        self.right_base = [0.0]*6
        self._update_offset_label()

    def _update_offset_label(self):
        self.lbl_left_offset.config(text=f"Left offset: ({self.left_base[0]:.1f},{self.left_base[1]:.1f},{self.left_base[2]:.1f},{self.left_base[3]:.1f},{self.left_base[4]:.1f},{self.left_base[5]:.1f})")
        self.lbl_right_offset.config(text=f"Right offset: ({self.right_base[0]:.1f},{self.right_base[1]:.1f},{self.right_base[2]:.1f},{self.right_base[3]:.1f},{self.right_base[4]:.1f},{self.right_base[5]:.1f})")


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
        self.extruder.send_gcode("SET_KINEMATIC_POSITION X=0 Y=0 Z=0")
        self.left.home(wait=False)
        self.right.home(wait=True)
        # Turntable already homed in connect()
        print("All axes homed")

    def prepare_to_print(self):
        if not self.hw_connected:
            messagebox.showwarning("Warning", "Hardware not connected.")
            return
        threading.Thread(target=self._prepare_thread, daemon=True).start()

    def _prepare_thread(self):
        try:
            temp = self.cfg['defaults']['temperature']['tool0']   # <-- self.cfg, not self.config

            # Heat both nozzles (non‑blocking)
            self.extruder.set_temperature(0, temp, wait=False)
            self.extruder.set_temperature(1, temp, wait=False)

            # Move arms to safe position while heating
            self.left.arm.set_position(442.5, 225, 160, 180, 45, 0, speed=100, wait=False)
            self.right.arm.set_position(442.5, 230, 172, 180, 45, 0, speed=100, wait=False)

            # Wait for both to reach temperature (uses built‑in polling)
            self.extruder.heat_and_wait(0, temp)
            self.extruder.heat_and_wait(1, temp)

            # Move to print‑ready positions
            self.left.arm.set_position(400, 200-25.6, 155, 180, 45, 20, speed=100, wait=False)
            self.right.arm.set_position(400, 200-25.6, 155, 180, 45, 20, speed=100, wait=False)
            time.sleep(5)

            self.root.after(0, lambda: messagebox.showinfo("Info", "Ready to print."))
        except Exception as e:
            self.root.after(0, lambda e=e: messagebox.showerror("Prepare Error", str(e)))

    def start_print(self):
        # Run your dual_print_demo.py logic in a thread
        threading.Thread(target=self._run_demo, daemon=True).start()

 
    def _run_demo(self):
        """Demo: both arms print the SAME circle (stacked) using turntable rotation."""
        try:
            # ---------- Turntable centre (measure per arm) ----------
            TT_CX_LEFT  = 575.1
            TT_CY_LEFT  = -11.1
            TT_CX_RIGHT = 575.6
            TT_CY_RIGHT = 3.5

            # ---------- Circle parameters ----------
            RADIUS = 120.0                   # desired printed radius (mm)
            # Radial offsets – add to RADIUS for each arm (can be positive or negative)
            LEFT_R_OFFSET  = 5.0            # e.g. +4.0 to increase left circle radius
            RIGHT_R_OFFSET = 8.5            # e.g. -4.0 to decrease right circle radius

            # Start angle (same physical spot – you can keep as is)
            GLOBAL_ANGLE = 135.0
            LEFT_ANGLE  = GLOBAL_ANGLE
            RIGHT_ANGLE = GLOBAL_ANGLE

            # ---------- Yaw (tune these until nozzle is tangent) ----------
            LEFT_YAW  = 20.0
            RIGHT_YAW = 20.0

            # ---------- Other settings ----------
            SPEED_LEFT  = 3.0
            SPEED_RIGHT = 3.0
            Z_LEFT  = 151.8
            Z_RIGHT = 152.0
            ROLL    = 180.0
            PITCH   = 45.0
            TOTAL_TIME = 60.0       # turntable speed 10°/s → 360° in 36 s, adjust as needed

                    # ---------- Compute nozzle positions with radial offset ----------
            def nozzle_pos(angle_deg, radius, offset_r, cx, cy):
                effective_radius = radius + offset_r
                rad = math.radians(angle_deg)
                x = cx + effective_radius * math.cos(rad)
                y = cy + effective_radius * math.sin(rad)
                return x, y

            left_x, left_y = nozzle_pos(LEFT_ANGLE, RADIUS, LEFT_R_OFFSET,
                                        TT_CX_LEFT, TT_CY_LEFT)
            right_x, right_y = nozzle_pos(RIGHT_ANGLE, RADIUS, RIGHT_R_OFFSET,
                                          TT_CX_RIGHT, TT_CY_RIGHT)

            # Print effective radii for verification
            left_r = math.hypot(left_x - TT_CX_LEFT, left_y - TT_CY_LEFT)
            right_r = math.hypot(right_x - TT_CX_RIGHT, right_y - TT_CY_RIGHT)
            print(f"Left effective radius: {left_r:.1f} mm  |  Right effective radius: {right_r:.1f} mm")

            # Safety
            if left_x > 580 or left_y < -15:
                print(f"WARNING: Left out of bounds ({left_x:.1f}, {left_y:.1f})")
            if right_x > 580 or right_y < -15:
                print(f"WARNING: Right out of bounds ({right_x:.1f}, {right_y:.1f})")

            # ---------- Extrude & move ----------
            left_len  = SPEED_LEFT  * TOTAL_TIME
            right_len = SPEED_RIGHT * TOTAL_TIME

            self.extruder.extrude_sync(left_len, SPEED_LEFT,
                                       right_len, SPEED_RIGHT,
                                       wait=False)

            self.left.arm.set_position(left_x, left_y, Z_LEFT,
                                       ROLL, PITCH, LEFT_YAW,
                                       speed=100, wait=False)
            self.right.arm.set_position(right_x, right_y, Z_RIGHT,
                                        ROLL, PITCH, RIGHT_YAW,
                                        speed=100, wait=True)

            self.turntable.rotate_absolute(360, 10, wait=True)
            while self.turntable.is_moving():
                time.sleep(0.1)
            self.turntable.wait_ok()

            print("Demo circle printed successfully.")

        except Exception as e:
            print(f"Demo print error: {e}")

    def _run_spiral(self):
        """Print a merging spiral – left starts offset, then blends into right’s path."""
        try:
            TT_CX_LEFT  = 575.1
            TT_CY_LEFT  = -11.1
            TT_CX_RIGHT = 575.6
            TT_CY_RIGHT = 3.5

            # ---------- Final print geometry ----------
            RADIUS          = 120.0
            LEFT_R_OFFSET   = 5.0         # left arm’s final radius tuning
            RIGHT_R_OFFSET  = 8.5         # right arm’s final radius tuning
            START_ANGLE_DEG = 135.0        # Angle on a circle plotted on an XY plot starting with quadrant 1 Y = 0 as 0deg counterclockwise positive (135 in quadrant 2)

            # ---------- Yaw ----------
            LEFT_YAW  = 20.0
            RIGHT_YAW = 20.0

            # ---------- Motion & layer ----------
            SPEED_LEFT      = 3.0
            SPEED_RIGHT     = 3.0
            Z_LEFT_START    = 151.7
            Z_RIGHT_START   = 151.9
            ROLL            = 180.0
            PITCH           = 45.0
            TOTAL_REVS      = 50
            Z_RISE_PER_REV  = 0.4         # mm per revolution
            TURNTABLE_DEG_S = 15
            PRE_EXTRUDE_TIME = 1.5

            # ---------- MERGE parameters (anti‑collision) ----------
            MERGE_REVS          = 3        # revolutions over which merging happens
            LEFT_MERGE_EXTRA_R  = 1.2      # extra radius for left at start (mm)
            LEFT_MERGE_EXTRA_Z  = 0.4      # extra height for left at start (mm)

            # ---------- Derived values ----------
            effective_radius_left_final  = RADIUS + LEFT_R_OFFSET
            effective_radius_right       = RADIUS + RIGHT_R_OFFSET

            total_rotation_deg = TOTAL_REVS * 360.0
            total_time = total_rotation_deg / TURNTABLE_DEG_S
            total_z_rise = TOTAL_REVS * Z_RISE_PER_REV

            left_len  = SPEED_LEFT  * total_time
            right_len = SPEED_RIGHT * total_time

            # ---------- Helper ----------
            def circle_point(angle_rad, radius, cx, cy):
                return cx + radius * math.cos(angle_rad), cy + radius * math.sin(angle_rad)

            start_rad = math.radians(START_ANGLE_DEG)

            # ---------- Start extrusion ----------
            self.extruder.extrude_sync(left_len, SPEED_LEFT,
                                       right_len, SPEED_RIGHT,
                                       wait=False)
            time.sleep(PRE_EXTRUDE_TIME)

            # ---------- Move RIGHT arm to its FINAL path (stays there) ----------
            right_x, right_y = circle_point(start_rad, effective_radius_right,
                                            TT_CX_RIGHT, TT_CY_RIGHT)
            self.right.arm.set_position(right_x, right_y, Z_RIGHT_START,
                                        ROLL, PITCH, RIGHT_YAW, speed=100, wait=True)

            # ---------- Move LEFT arm to its OUTER, HIGHER start ----------
            left_initial_radius = effective_radius_left_final + LEFT_MERGE_EXTRA_R
            left_initial_z      = Z_LEFT_START + LEFT_MERGE_EXTRA_Z
            left_x_initial, left_y_initial = circle_point(start_rad, left_initial_radius,
                                                          TT_CX_LEFT, TT_CY_LEFT)
            self.left.arm.set_position(left_x_initial, left_y_initial, left_initial_z,
                                       ROLL, PITCH, LEFT_YAW, speed=100, wait=True)

            # ---------- Start turntable ----------
            self.turntable.rotate_absolute(total_rotation_deg, TURNTABLE_DEG_S, wait=False)

            # ---------- Synchronised Z‑rise with radius blending ----------
            update_interval = 0.5   # seconds between Z steps
            steps = max(1, int(total_time / update_interval))
            start_time = time.time()

            for i in range(steps):
                elapsed = time.time() - start_time
                frac = elapsed / total_time
                current_rev = frac * TOTAL_REVS

                # -- Left arm: merge logic --
                if current_rev < MERGE_REVS:
                    t = current_rev / MERGE_REVS          # 0 → 1
                    extra_r = LEFT_MERGE_EXTRA_R * (1 - t)
                    extra_z = LEFT_MERGE_EXTRA_Z * (1 - t)
                else:
                    extra_r = 0.0
                    extra_z = 0.0

                left_radius = effective_radius_left_final + extra_r
                left_z      = Z_LEFT_START + frac * total_z_rise + extra_z

                # Left XY moves radially along the fixed start angle
                left_x, left_y = circle_point(start_rad, left_radius,
                                              TT_CX_LEFT, TT_CY_LEFT)

                # -- Right arm: only Z changes (XY is constant) --
                right_z = Z_RIGHT_START + frac * total_z_rise

                # Move both arms (left XY + Z, right Z only)
                self.left.arm.set_position(left_x, left_y, left_z,
                                           ROLL, PITCH, LEFT_YAW, speed=10, wait=False)
                self.right.arm.set_position(right_x, right_y, right_z,
                                            ROLL, PITCH, RIGHT_YAW, speed=10, wait=True)

                time.sleep(update_interval)

            # ---------- Wait for turntable to finish ----------
            while self.turntable.is_moving():
                time.sleep(0.1)
            self.turntable.wait_ok()

            print("Merged spiral printed successfully.")

        except Exception as e:
            print(f"Spiral print error: {e}")

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
                # Temperatures – T1 is heater_bed
                status = self.extruder.get_printer_status()
                t0 = status.get('extruder', {}).get('temperature', 0.0)
                t1 = status.get('heater_bed', {}).get('temperature', 0.0)   # <-- corrected key
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