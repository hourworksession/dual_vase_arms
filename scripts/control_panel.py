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
        Phased dual-arm print, frame-stepped turntable control.
    
          Phase 1 (layers 1-3): LEFT arm.  Layer 1 = circle, layers 2-3 = spiral.
          Phase 2: LEFT arm retracts to holding position.
          Phase 3 (layers 4-6): RIGHT arm prints 3 layers on the SAME toolpath, stacked on top.
          Phase 4 (layer 7+): halfway through layer 7's rotation the LEFT arm rejoins; both
                              arms co-print the SAME toolpath to max layer height.
    
        Turntable is driven in fixed angular steps per frame (10 deg/s implied by
        STEP_DEG / FRAME_DT). Each frame: step the bed, then command the arm moves,
        then repeat until the target revolution count is reached.
    
        NOTE on wiring: commanding the RIGHT arm drives the LEFT extruder feed and
        vice-versa, so the extrude_sync(left_len, right_len) slots are crossed
        relative to the arm that is physically moving.
        """
        try:
            # ---------- Turntable centers (as seen by each arm) ----------
            TT_CX_LEFT  = 575.1
            TT_CY_LEFT  = -11.1
            TT_CX_RIGHT = 575.6
            TT_CY_RIGHT = 3.5
    
            # ---------- Path geometry ----------
            RADIUS          = 120.0
            LEFT_R_OFFSET   = 3.0
            RIGHT_R_OFFSET  = 5.2
            START_ANGLE_DEG = 135.0
    
            # ---------- Yaw ----------
            LEFT_YAW  = 20.0
            RIGHT_YAW = 20.0
    
            # ---------- Motion & layer ----------
            Z_LEFT_START    = 151.7
            Z_RIGHT_START   = 151.9
            ROLL            = 180.0
            PITCH           = 45.0
            Z_RISE_PER_REV  = 0.4          # mm per revolution (== layer height)
            PRE_EXTRUDE_TIME = 1.5
    
            # ---------- Frame-stepped turntable control ----------
            TURNTABLE_DEG_S = 10.0         # target bed speed
            FRAME_DT        = 0.5          # seconds per frame
            STEP_DEG        = TURNTABLE_DEG_S * FRAME_DT   # bed advance per frame (5 deg)
    
            # ---------- Phase definitions ----------
            LEFT_PHASE1_REVS = 3           # layers 1-3 (left): circle then spiral
            RIGHT_REVS       = 3           # layers 4-6 (right)
            TOTAL_REVS       = 110         # total layers to max height
            REJOIN_LAYER     = 7           # 1-indexed layer where left rejoins
            REJOIN_AT_FRAC   = 0.5         # halfway through that layer's rotation
    
            # ---------- Holding position for the idle left arm ----------
            HOLD_X = 450.0
            HOLD_Y = 150.0
            HOLD_Z = 220.0
    
            # ---------- Derived ----------
            eff_radius_left  = RADIUS + LEFT_R_OFFSET
            eff_radius_right = RADIUS + RIGHT_R_OFFSET
            start_rad        = math.radians(START_ANGLE_DEG)
            left_rejoin_rev  = (REJOIN_LAYER - 1) + REJOIN_AT_FRAC    # e.g. 6.5
    
            def circle_point(angle_rad, radius, cx, cy):
                return (cx + radius * math.cos(angle_rad),
                        cy + radius * math.sin(angle_rad))
    
            # Layer 1 (rev <= 1) is a flat circle (no Z rise); spiral begins at layer 2.
            def path_z(base_z, rev):
                if rev <= 1.0:
                    return base_z
                return base_z + (rev - 1.0) * Z_RISE_PER_REV
    
            # --- Geometry-based extrusion length ---
            # Filament/path length = circumference of the printed circle * number of revs.
            # (Previously this was speed*time, which under-fed; arc length is the right basis.)
            def extrude_len(radius, revs):
                return 2.0 * math.pi * radius * revs
    
            # Steps required to cover a given number of revolutions at STEP_DEG per frame.
            def steps_for(revs):
                return max(1, int(math.ceil(revs * 360.0 / STEP_DEG)))
    
            # =========================================================
            # PHASE 1 — LEFT arm: layers 1-3 (circle + spiral)
            # =========================================================
            lx, ly = circle_point(start_rad, eff_radius_left, TT_CX_LEFT, TT_CY_LEFT)
    
            # LEFT arm moves -> RIGHT extruder slot feeds it.
            len_p1 = extrude_len(eff_radius_left, LEFT_PHASE1_REVS)
            self.extruder.extrude_sync(0.0, 1.0, len_p1, 1.0, wait=False)
            time.sleep(PRE_EXTRUDE_TIME)
    
            self.left.arm.set_position(lx, ly, Z_LEFT_START,
                                       ROLL, PITCH, LEFT_YAW, speed=100, wait=True)
    
            bed_deg = 0.0
            n = steps_for(LEFT_PHASE1_REVS)
            for i in range(n):
                bed_deg = min(LEFT_PHASE1_REVS * 360.0, bed_deg + STEP_DEG)
                self.turntable.rotate_absolute(bed_deg, TURNTABLE_DEG_S, wait=True)
    
                rev = bed_deg / 360.0
                lz  = path_z(Z_LEFT_START, rev)
                self.left.arm.set_position(lx, ly, lz,
                                           ROLL, PITCH, LEFT_YAW, speed=10, wait=True)
                time.sleep(FRAME_DT)
    
            self.turntable.wait_ok()
    
            # =========================================================
            # PHASE 2 — LEFT arm retracts to holding position
            # =========================================================
            # LEFT arm idle -> stop the RIGHT extruder slot (which fed it).
            self.extruder.stop_right() if hasattr(self.extruder, "stop_right") else None
            self.left.arm.set_position(HOLD_X, HOLD_Y, HOLD_Z,
                                       ROLL, PITCH, LEFT_YAW, speed=100, wait=True)
    
            # =========================================================
            # PHASE 3 — RIGHT arm: layers 4-6, SAME toolpath, stacked on top
            # =========================================================
            rx, ry = circle_point(start_rad, eff_radius_right, TT_CX_RIGHT, TT_CY_RIGHT)
    
            # RIGHT arm moves -> LEFT extruder slot feeds it.
            len_p3 = extrude_len(eff_radius_right, RIGHT_REVS)
            self.extruder.extrude_sync(len_p3, 1.0, 0.0, 1.0, wait=False)
            time.sleep(PRE_EXTRUDE_TIME)
    
            self.right.arm.set_position(rx, ry, path_z(Z_RIGHT_START, LEFT_PHASE1_REVS),
                                        ROLL, PITCH, RIGHT_YAW, speed=100, wait=True)
    
            bed_target = (LEFT_PHASE1_REVS + RIGHT_REVS) * 360.0
            n = steps_for(RIGHT_REVS)
            for i in range(n):
                bed_deg = min(bed_target, bed_deg + STEP_DEG)
                self.turntable.rotate_absolute(bed_deg, TURNTABLE_DEG_S, wait=True)
    
                rev = bed_deg / 360.0
                rz  = path_z(Z_RIGHT_START, rev)
                self.right.arm.set_position(rx, ry, rz,
                                            ROLL, PITCH, RIGHT_YAW, speed=10, wait=True)
                time.sleep(FRAME_DT)
    
            self.turntable.wait_ok()
    
            # Z height the RIGHT arm finished at — the LEFT arm stacks on top of THIS.
            right_stop_rev = LEFT_PHASE1_REVS + RIGHT_REVS      # = 6
    
            # =========================================================
            # PHASE 4 — Layers 7..TOTAL_REVS: co-printing to max height
            #   RIGHT arm prints the whole phase.
            #   LEFT arm rejoins halfway through layer 7, printing ON TOP of where
            #   the right arm stopped: it uses the RIGHT arm's path radius (not its
            #   own edge radius) so it stacks rather than spiralling in.
            # =========================================================
            revs_done   = right_stop_rev                        # = 6
            phase4_revs = TOTAL_REVS - revs_done
    
            # LEFT arm rejoins on the RIGHT arm's path radius (stack on top, same XY path).
            lx_join, ly_join = circle_point(start_rad, eff_radius_right,
                                            TT_CX_LEFT, TT_CY_LEFT)
    
            # Geometry-based lengths.
            #   RIGHT arm runs the full phase on radius eff_radius_right.
            #   LEFT arm runs only from the rejoin point to the end, also on eff_radius_right.
            right_phase4_len = extrude_len(eff_radius_right, phase4_revs)
            left_run_revs    = TOTAL_REVS - left_rejoin_rev
            left_phase4_len  = extrude_len(eff_radius_right, left_run_revs)
    
            # RIGHT arm moving -> LEFT extruder slot. LEFT arm moving -> RIGHT extruder slot.
            self.extruder.extrude_sync(right_phase4_len, 1.0,
                                       left_phase4_len, 1.0, wait=False)
    
            # Re-anchor the RIGHT arm at the start of layer 7.
            rx, ry = circle_point(start_rad, eff_radius_right, TT_CX_RIGHT, TT_CY_RIGHT)
            self.right.arm.set_position(rx, ry, path_z(Z_RIGHT_START, revs_done),
                                        ROLL, PITCH, RIGHT_YAW, speed=100, wait=True)
    
            bed_target = TOTAL_REVS * 360.0
            n = steps_for(phase4_revs)
            left_active = False
            for i in range(n):
                bed_deg = min(bed_target, bed_deg + STEP_DEG)
                self.turntable.rotate_absolute(bed_deg, TURNTABLE_DEG_S, wait=True)
    
                abs_rev = bed_deg / 360.0
    
                # RIGHT arm follows shared toolpath.
                rz = path_z(Z_RIGHT_START, abs_rev)
                self.right.arm.set_position(rx, ry, rz,
                                            ROLL, PITCH, RIGHT_YAW, speed=10, wait=False)
    
                # LEFT arm rejoins at layer 7 + halfway, ON TOP of the right arm's layer.
                if abs_rev >= left_rejoin_rev:
                    lz = path_z(Z_LEFT_START, abs_rev) + Z_RISE_PER_REV   # one layer up
                    if not left_active:
                        self.left.arm.set_position(lx_join, ly_join, lz,
                                                   ROLL, PITCH, LEFT_YAW, speed=100, wait=True)
                        left_active = True
                    else:
                        self.left.arm.set_position(lx_join, ly_join, lz,
                                                   ROLL, PITCH, LEFT_YAW, speed=10, wait=True)
    
                time.sleep(FRAME_DT)
    
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