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
        """
        Phased dual-arm print:
          Phase 1 (layers 1-3): RIGHT extruder. Layer 1 = circle, layers 2-3 = spiral.
          Phase 2: RIGHT retracts to a holding position.
          Phase 3 (layers 4-6): LEFT extruder prints 3 layers on the SAME toolpath, stacked on top.
          Phase 4 (layer 7+): halfway through layer 7's bed rotation, RIGHT rejoins and both
                              arms co-print the SAME toolpath (commanded to same point, one layer
                              up in Z; physically 180 deg apart on the circle) for the remainder.
        """
        try:
            # ---------- Turntable centers (as seen by each arm) ----------
            TT_CX_LEFT  = 575.1
            TT_CY_LEFT  = -11.1
            TT_CX_RIGHT = 575.6
            TT_CY_RIGHT = 3.5
    
            # ---------- Path geometry ----------
            RADIUS          = 120.0
            LEFT_R_OFFSET   = 5.0
            RIGHT_R_OFFSET  = 8.5
            START_ANGLE_DEG = 135.0
    
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
            Z_RISE_PER_REV  = 0.4          # mm per revolution (== layer height)
            TURNTABLE_DEG_S = 15
            PRE_EXTRUDE_TIME = 1.5
    
            # ---------- Phase definitions ----------
            RIGHT_PHASE1_REVS = 3          # layers 1-3 (right): circle then spiral
            LEFT_REVS         = 3          # layers 4-6 (left)
            TOTAL_REVS        = 110        # total layers across the whole print
            REJOIN_LAYER      = 7          # 1-indexed layer where right rejoins
            REJOIN_AT_FRAC    = 0.5        # halfway through that layer's rotation
    
            # ---------- Holding position for the idle right arm ----------
            HOLD_X = 450.0
            HOLD_Y = 150.0
            HOLD_Z = 220.0
    
            # ---------- Derived ----------
            eff_radius_left  = RADIUS + LEFT_R_OFFSET
            eff_radius_right = RADIUS + RIGHT_R_OFFSET
            start_rad        = math.radians(START_ANGLE_DEG)
            sec_per_rev      = 360.0 / TURNTABLE_DEG_S
            update_interval  = 0.5
    
            # Layer index (0-based) at which the right arm rejoins, in revolutions
            right_rejoin_rev = (REJOIN_LAYER - 1) + REJOIN_AT_FRAC   # e.g. 6.5
    
            def circle_point(angle_rad, radius, cx, cy):
                return (cx + radius * math.cos(angle_rad),
                        cy + radius * math.sin(angle_rad))
    
            # The shared toolpath point at a given total revolution count.
            # Layer 1 (rev < 1) is a flat circle (no Z rise); spiral begins at layer 2.
            def path_z(base_z, rev):
                if rev <= 1.0:
                    return base_z                      # circle: constant Z for first layer
                return base_z + (rev - 1.0) * Z_RISE_PER_REV
    
            # =========================================================
            # PHASE 1 — RIGHT extruder: layers 1-3 (circle + spiral)
            # =========================================================
            phase1_rot_deg = RIGHT_PHASE1_REVS * 360.0
            phase1_time    = phase1_rot_deg / TURNTABLE_DEG_S
    
            right_len = SPEED_RIGHT * phase1_time
            self.extruder.extrude_sync(0.0, SPEED_LEFT,
                                       right_len, SPEED_RIGHT,
                                       wait=False)
            time.sleep(PRE_EXTRUDE_TIME)
    
            rx, ry = circle_point(start_rad, eff_radius_right, TT_CX_RIGHT, TT_CY_RIGHT)
            self.right.arm.set_position(rx, ry, Z_RIGHT_START,
                                        ROLL, PITCH, RIGHT_YAW, speed=100, wait=True)
    
            self.turntable.rotate_absolute(phase1_rot_deg, TURNTABLE_DEG_S, wait=False)
    
            steps = max(1, int(phase1_time / update_interval))
            t0 = time.time()
            for _ in range(steps):
                frac = min(1.0, (time.time() - t0) / phase1_time)
                rev  = frac * RIGHT_PHASE1_REVS
                rz   = path_z(Z_RIGHT_START, rev)
                self.right.arm.set_position(rx, ry, rz,
                                            ROLL, PITCH, RIGHT_YAW, speed=10, wait=True)
                time.sleep(update_interval)
    
            while self.turntable.is_moving():
                time.sleep(0.1)
            self.turntable.wait_ok()
    
            # =========================================================
            # PHASE 2 — RIGHT retracts to holding position
            # =========================================================
            self.extruder.stop_right() if hasattr(self.extruder, "stop_right") else None
            self.right.arm.set_position(HOLD_X, HOLD_Y, HOLD_Z,
                                        ROLL, PITCH, RIGHT_YAW, speed=100, wait=True)
    
            # =========================================================
            # PHASE 3 — LEFT extruder: layers 4-6, SAME toolpath, stacked on top
            # =========================================================
            # Left continues where the right left off in Z (3 layers already laid down).
            z_left_phase3_base = Z_LEFT_START + RIGHT_PHASE1_REVS * Z_RISE_PER_REV
            phase3_rot_deg = LEFT_REVS * 360.0
            phase3_time    = phase3_rot_deg / TURNTABLE_DEG_S
    
            left_len = SPEED_LEFT * phase3_time
            self.extruder.extrude_sync(left_len, SPEED_LEFT,
                                       0.0, SPEED_RIGHT,
                                       wait=False)
            time.sleep(PRE_EXTRUDE_TIME)
    
            lx, ly = circle_point(start_rad, eff_radius_left, TT_CX_LEFT, TT_CY_LEFT)
            self.left.arm.set_position(lx, ly, z_left_phase3_base,
                                       ROLL, PITCH, LEFT_YAW, speed=100, wait=True)
    
            # Reset turntable origin so phase-3 rotation starts clean
            self.turntable.rotate_absolute(phase1_rot_deg + phase3_rot_deg,
                                            TURNTABLE_DEG_S, wait=False)
    
            steps = max(1, int(phase3_time / update_interval))
            t0 = time.time()
            for _ in range(steps):
                frac = min(1.0, (time.time() - t0) / phase3_time)
                rev  = RIGHT_PHASE1_REVS + frac * LEFT_REVS    # absolute layer count
                lz   = path_z(Z_LEFT_START, rev)
                self.left.arm.set_position(lx, ly, lz,
                                           ROLL, PITCH, LEFT_YAW, speed=10, wait=False)
                time.sleep(update_interval)
    
            while self.turntable.is_moving():
                time.sleep(0.1)
            self.turntable.wait_ok()
    
            # =========================================================
            # PHASE 4 — Layers 7..TOTAL_REVS: co-printing
            #   Both arms commanded to the SAME point, one layer up in Z.
            #   (Physically they sit 180 deg apart on the circle.)
            #   RIGHT rejoins halfway through layer 7's rotation.
            # =========================================================
            revs_done    = RIGHT_PHASE1_REVS + LEFT_REVS        # = 6
            phase4_revs  = TOTAL_REVS - revs_done               # remaining layers
            phase4_deg   = phase4_revs * 360.0
            phase4_time  = phase4_deg / TURNTABLE_DEG_S
    
            # Left extrudes for the whole phase; right extrudes only after it rejoins.
            left_len  = SPEED_LEFT  * phase4_time
            right_len = SPEED_RIGHT * (phase4_time * (1 - (REJOIN_AT_FRAC / phase4_revs)))
            self.extruder.extrude_sync(left_len, SPEED_LEFT,
                                       right_len, SPEED_RIGHT,
                                       wait=False)
    
            # Left re-anchors on the shared path at the start of layer 7.
            lz_start = path_z(Z_LEFT_START, revs_done)
            lx, ly = circle_point(start_rad, eff_radius_left, TT_CX_LEFT, TT_CY_LEFT)
            self.left.arm.set_position(lx, ly, lz_start,
                                       ROLL, PITCH, LEFT_YAW, speed=100, wait=True)
    
            self.turntable.rotate_absolute(phase1_rot_deg + phase3_rot_deg + phase4_deg,
                                            TURNTABLE_DEG_S, wait=False)
    
            steps = max(1, int(phase4_time / update_interval))
            t0 = time.time()
            right_active = False
            right_extrude_started = False
    
            for _ in range(steps):
                frac = min(1.0, (time.time() - t0) / phase4_time)
                abs_rev = revs_done + frac * phase4_revs        # absolute layer count
    
                # --- Left arm follows shared toolpath ---
                lz = path_z(Z_LEFT_START, abs_rev)
                self.left.arm.set_position(lx, ly, lz,
                                           ROLL, PITCH, LEFT_YAW, speed=10, wait=False)
    
                # --- Right arm rejoins at layer 7 + halfway ---
                if abs_rev >= right_rejoin_rev:
                    # Commanded to the SAME point as left, one layer (Z_RISE) up.
                    # Physically the arm geometry places it 180 deg opposite on the circle.
                    rx, ry = circle_point(start_rad, eff_radius_right, TT_CX_RIGHT, TT_CY_RIGHT)
                    rz = path_z(Z_RIGHT_START, abs_rev) + Z_RISE_PER_REV
    
                    if not right_active:
                        # Rapid move into position the first time, then settle.
                        self.right.arm.set_position(rx, ry, rz,
                                                    ROLL, PITCH, RIGHT_YAW, speed=100, wait=True)
                        right_active = True
                    else:
                        self.right.arm.set_position(rx, ry, rz,
                                                    ROLL, PITCH, RIGHT_YAW, speed=10, wait=True)
                else:
                    # Right not yet rejoined; let the left move settle.
                    time.sleep(0)
    
                time.sleep(update_interval)
    
            while self.turntable.is_moving():
                time.sleep(0.1)
            self.turntable.wait_ok()
    
            print("Phased dual-arm spiral printed successfully.")
    
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