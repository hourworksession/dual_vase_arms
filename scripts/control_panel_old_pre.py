#!/usr/bin/env python3
import sys, os, time, math, threading, logging
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
import tkinter as tk
from tkinter import ttk, messagebox
from src.arm_controller import ArmController
from src.turntable_controller import TurntableController
from src.extruder_controller import ExtruderController
from config_loader import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("control_panel")

class ControlPanel:
    def __init__(self, root):
        self.root = root
        self.root.title("Dual Arm Printer Control")
        self.cfg = load_config()
        self.hw_connected = False
        self.left = self.right = self.turntable = self.extruder = None

        # Virtual base offsets
        self.left_base  = [0.0]*6
        self.right_base = [0.0]*6

        # ---------- Status frame ----------
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

        # ---------- Control buttons ----------
        btn_frame = ttk.Frame(root)
        btn_frame.pack(padx=5, pady=5)

        ttk.Button(btn_frame, text="Connect All", command=self.connect_hw).grid(row=0, column=0, padx=2, pady=2)
        ttk.Button(btn_frame, text="Home All", command=self.home_all).grid(row=0, column=1, padx=2, pady=2)
        ttk.Button(btn_frame, text="Extruders off", command=self.extruders_off).grid(row=0, column=2, padx=2, pady=2)
        ttk.Button(btn_frame, text="Prepare to Print", command=self.prepare_to_print).grid(row=0, column=3, padx=2, pady=2)
        ttk.Button(btn_frame, text="Start Demo Circle", command=self.start_print).grid(row=0, column=4, padx=2, pady=2)
        ttk.Button(btn_frame, text="Start Spiral", command=lambda: threading.Thread(target=self._run_spiral, daemon=True).start()).grid(row=0, column=5, padx=2, pady=2)
        ttk.Button(btn_frame, text="EMERGENCY STOP", command=self.emergency_stop, style="Red.TButton").grid(row=1, column=0, padx=2, pady=2)
        ttk.Button(btn_frame, text="Turntable +90°", command=lambda: self.jog_turntable(90)).grid(row=1, column=1, padx=2, pady=2)
        ttk.Button(btn_frame, text="Turntable -90°", command=lambda: self.jog_turntable(-90)).grid(row=1, column=2, padx=2, pady=2)

        # ---------- Synchronous extrusion test ----------
        ext_frame = ttk.LabelFrame(root, text="Synchronous Extrude Test", padding=5)
        ext_frame.pack(fill="x", padx=5, pady=5)
        ttk.Label(ext_frame, text="Length (mm):").grid(row=0, column=0)
        self.ext_len = tk.DoubleVar(value=5.0)
        ttk.Entry(ext_frame, textvariable=self.ext_len, width=8).grid(row=0, column=1)
        ttk.Label(ext_frame, text="Rate (mm/s):").grid(row=0, column=2)
        self.ext_rate = tk.DoubleVar(value=5.0)
        ttk.Entry(ext_frame, textvariable=self.ext_rate, width=8).grid(row=0, column=3)
        ttk.Button(ext_frame, text="Extrude Both", command=self.extrude_both).grid(row=0, column=4, padx=5)

        # ---------- Manual coordinate control (with auto‑fill) ----------
        coord_frame = ttk.LabelFrame(root, text="Manual Coordinate Control", padding=5)
        coord_frame.pack(fill="x", padx=5, pady=5)

        self.coord_x = tk.DoubleVar(value=0.0)
        self.coord_y = tk.DoubleVar(value=0.0)
        self.coord_z = tk.DoubleVar(value=0.0)
        self.coord_roll  = tk.DoubleVar(value=0.0)
        self.coord_pitch = tk.DoubleVar(value=0.0)
        self.coord_yaw   = tk.DoubleVar(value=0.0)

        row0 = 0
        ttk.Label(coord_frame, text="X").grid(row=row0, column=0, padx=2)
        ttk.Entry(coord_frame, textvariable=self.coord_x, width=8).grid(row=row0, column=1, padx=2)
        ttk.Label(coord_frame, text="Y").grid(row=row0, column=2, padx=2)
        ttk.Entry(coord_frame, textvariable=self.coord_y, width=8).grid(row=row0, column=3, padx=2)
        ttk.Label(coord_frame, text="Z").grid(row=row0, column=4, padx=2)
        ttk.Entry(coord_frame, textvariable=self.coord_z, width=8).grid(row=row0, column=5, padx=2)
        ttk.Label(coord_frame, text="Roll").grid(row=row0, column=6, padx=2)
        ttk.Entry(coord_frame, textvariable=self.coord_roll, width=8).grid(row=row0, column=7, padx=2)
        ttk.Label(coord_frame, text="Pitch").grid(row=row0, column=8, padx=2)
        ttk.Entry(coord_frame, textvariable=self.coord_pitch, width=8).grid(row=row0, column=9, padx=2)
        ttk.Label(coord_frame, text="Yaw").grid(row=row0, column=10, padx=2)
        ttk.Entry(coord_frame, textvariable=self.coord_yaw, width=8).grid(row=row0, column=11, padx=2)

        row1 = 1
        ttk.Button(coord_frame, text="Get Left Pos", command=self.fill_left_pos).grid(row=row1, column=0, columnspan=4, pady=2, sticky="ew")
        ttk.Button(coord_frame, text="Get Right Pos", command=self.fill_right_pos).grid(row=row1, column=4, columnspan=4, pady=2, sticky="ew")
        ttk.Button(coord_frame, text="Set Left Base", command=self.set_left_base).grid(row=row1, column=8, columnspan=2, pady=2, sticky="ew")
        ttk.Button(coord_frame, text="Clear Left Base", command=self.clear_left_base).grid(row=row1, column=10, columnspan=2, pady=2, sticky="ew")

        row2 = 2
        ttk.Button(coord_frame, text="Send to Left", command=self.send_left_move).grid(row=row2, column=0, columnspan=4, pady=2, sticky="ew")
        ttk.Button(coord_frame, text="Send to Right", command=self.send_right_move).grid(row=row2, column=4, columnspan=4, pady=2, sticky="ew")
        ttk.Button(coord_frame, text="Set Right Base", command=self.set_right_base).grid(row=row2, column=8, columnspan=2, pady=2, sticky="ew")
        ttk.Button(coord_frame, text="Clear Right Base", command=self.clear_right_base).grid(row=row2, column=10, columnspan=2, pady=2, sticky="ew")

        row3 = 3
        self.lbl_left_offset = ttk.Label(coord_frame, text="Left offset: (0,0,0,0,0,0)")
        self.lbl_left_offset.grid(row=row3, column=0, columnspan=6, sticky="w", padx=2)
        self.lbl_right_offset = ttk.Label(coord_frame, text="Right offset: (0,0,0,0,0,0)")
        self.lbl_right_offset.grid(row=row3, column=6, columnspan=6, sticky="w", padx=2)

        style = ttk.Style()
        style.configure("Red.TButton", foreground="red", font=('Arial', 10, 'bold'))

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.update_status()

    # ======================================================================
    #  Hardware connection & homing
    # ======================================================================
    def connect_hw(self):
        try:
            self.left  = ArmController(self.cfg['arms']['left']['ip'], "left")
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
        print("All axes homed")

    # ======================================================================
    #  Prepare & Demo Circle
    # ======================================================================
    def prepare_to_print(self):
        if not self.hw_connected:
            messagebox.showwarning("Warning", "Hardware not connected.")
            return
        threading.Thread(target=self._prepare_thread, daemon=True).start()

    def _prepare_thread(self):
        try:
            temp = self.cfg['defaults']['temperature']['tool0']
            self.extruder.set_temperature(0, temp, wait=False)
            self.extruder.set_temperature(1, temp, wait=False)

            self.left.arm.set_position(442.5, 225, 160, 180, 45, 0, speed=100, wait=False)
            self.right.arm.set_position(442.5, 230, 172, 180, 45, 0, speed=100, wait=False)

            self.extruder.heat_and_wait(0, temp)
            self.extruder.heat_and_wait(1, temp)

            self.left.arm.set_position(400, 174.4, 155, 180, 45, 20, speed=100, wait=False)
            self.right.arm.set_position(400, 174.4, 155, 180, 45, 20, speed=100, wait=False)
            time.sleep(5)
            self.root.after(0, lambda: messagebox.showinfo("Info", "Ready to print."))
        except Exception as e:
            self.root.after(0, lambda e=e: messagebox.showerror("Prepare Error", str(e)))

    def start_print(self):
        threading.Thread(target=self._run_demo, daemon=True).start()

    def _run_demo(self):
        """Demo circle: arms stay at fixed XY, turntable rotates 360°."""
        try:
            TT_CX_LEFT  = 575.3
            TT_CY_LEFT  = -10.6
            TT_CX_RIGHT = 573.1
            TT_CY_RIGHT = 3.0
            RADIUS = 155
            LEFT_R_OFFSET  = 3.0
            RIGHT_R_OFFSET = 5.2
            GLOBAL_ANGLE = 135.0
            LEFT_YAW  = 20.0
            RIGHT_YAW = 20.0
            SPEED_LEFT  = 3.0
            SPEED_RIGHT = 3.0
            Z_LEFT  = 151.8
            Z_RIGHT = 152.0
            TOTAL_TIME = 36   # 10°/s → 360° in 36 s

            def nozzle_pos(angle_deg, rad, off, cx, cy):
                r = rad + off
                a = math.radians(angle_deg)
                return cx + r * math.cos(a), cy + r * math.sin(a)

            left_x, left_y = nozzle_pos(GLOBAL_ANGLE, RADIUS, LEFT_R_OFFSET, TT_CX_LEFT, TT_CY_LEFT)
            right_x, right_y = nozzle_pos(GLOBAL_ANGLE, RADIUS, RIGHT_R_OFFSET, TT_CX_RIGHT, TT_CY_RIGHT)

            self.extruder.extrude_sync(SPEED_LEFT*TOTAL_TIME, SPEED_LEFT,
                                       SPEED_RIGHT*TOTAL_TIME, SPEED_RIGHT,
                                       wait=False)

            self.left.arm.set_position(left_x, left_y, Z_LEFT, 180, 45, LEFT_YAW, speed=100, wait=False)
            self.right.arm.set_position(right_x, right_y, Z_RIGHT, 180, 45, RIGHT_YAW, speed=100, wait=True)

            self.turntable.rotate_absolute(360, 20, wait=True)

            print("Demo circle printed.")
        except Exception as e:
            print(f"Demo print error: {e}")


    def _run_spiral(self):
        try:
            # ---------------------- Spiral parameters -----------------------
            TT_CX_RIGHT = 567.7
            TT_CY_RIGHT = 6.7
            TT_CX_LEFT  = 574.1
            TT_CY_LEFT  = -5.4

            RADIUS          = 100.0
            LEFT_R_OFFSET   = 0.1
            RIGHT_R_OFFSET  = 0
            START_ANGLE_DEG = 135.0

            Z_LEFT          = 151.8
            Z_RIGHT         = 151.8
            Z_STEP_PER_REV  = 0.4
            LAYER_HEIGHT    = Z_STEP_PER_REV

            LINE_WIDTH      = 0.4
            FILAMENT_DIAMETER = 1.75
            FEED_RATE       = 4.0

            RIGHT_REVS      = 5
            LEFT_REVS       = 5
            COMBINED_REVS   = 10

            TURNTABLE_SPEED_FIXED = 30

            # ---------- Tunable factors (separate for each section) ----------
            EXTRUSION_FACTOR_RIGHT_SOLO = 1.0   # Phase 1 – already good
            EXTRUSION_FACTOR_LEFT_SOLO  = 1.004   # Phase 3 – increase until left doesn't run dry
            EXTRUSION_FACTOR_LEFT_COMB  = 1.06    # Phase 4 left – start high, reduce if blob
            EXTRUSION_FACTOR_RIGHT_COMB = 1.06    # Phase 4 right – tune independently
            LOOP_SAFETY = 1.01                    # keeps arms in place after motion stops

            PARK_X = 400.0
            PARK_Y = 174.0
            PARK_Z = 180.0
            SAFE_Z = 250.0

            temp = self.cfg['defaults']['temperature']['tool0']
            # -----------------------------------------------------------------

            self.extruder.set_temperature(0, temp, wait=False)
            self.extruder.set_temperature(1, temp, wait=False)
            logger.info("Both nozzles at %d°C and held", temp)

            FILAMENT_AREA = math.pi * (FILAMENT_DIAMETER/2)**2

            def filament_per_rev(eff_radius):
                circ = 2 * math.pi * eff_radius
                vol = circ * LINE_WIDTH * LAYER_HEIGHT
                return vol / FILAMENT_AREA

            def path_z(base_z, rev):
                if rev <= 1.0:
                    return base_z
                return base_z + (rev - 1.0) * Z_STEP_PER_REV

            def nozzle_pos(angle_deg, rad, off, cx, cy):
                r = rad + off
                a = math.radians(angle_deg)
                return cx + r * math.cos(a), cy + r * math.sin(a)

            eff_radius_left  = RADIUS + LEFT_R_OFFSET
            eff_radius_right = RADIUS + RIGHT_R_OFFSET

            left_xy  = nozzle_pos(START_ANGLE_DEG, RADIUS, LEFT_R_OFFSET,  TT_CX_LEFT,  TT_CY_LEFT)
            right_xy = nozzle_pos(START_ANGLE_DEG, RADIUS, RIGHT_R_OFFSET, TT_CX_RIGHT, TT_CY_RIGHT)

            TOOL_RIGHT = 0
            TOOL_LEFT  = 1

            def print_phase(arm, tool, base_z, start_rev, end_rev, fixed_xy, extrusion_factor):
                revs = end_rev - start_rev
                if revs <= 0:
                    return

                eff_radius = eff_radius_left if tool == TOOL_LEFT else eff_radius_right
                len_per_rev = filament_per_rev(eff_radius)
                theoretical_len = len_per_rev * revs
                rev_time = len_per_rev / FEED_RATE
                turntable_speed = 360.0 / rev_time

                # Padded loop duration
                loop_time = rev_time * revs * LOOP_SAFETY
                # Actual extrusion length (tunable)
                extrude_len = theoretical_len * extrusion_factor

                logger.info("Phase %.0f–%.0f revs, filament %.0f mm (×%.2f = %.0f mm), loop %.1f s",
                            start_rev, end_rev, theoretical_len, extrusion_factor,
                            extrude_len, loop_time)

                # Move to start Z
                start_z = path_z(base_z, start_rev)
                arm.move_to(*fixed_xy, start_z, roll=180, pitch=45, yaw=20, speed=80, wait=True)

                # Start the turntable (non‑blocking)
                target_angle = end_rev * 360.0
                self.turntable.rotate_absolute(target_angle, turntable_speed, wait=False)

                # Short delay to let the motion start (otherwise fraction jumps instantly)
                time.sleep(0.3)

                # Start extrusion (non‑blocking)
                self.extruder.extrude(tool, extrude_len, FEED_RATE, wait=False)

                # Timed Z‑update loop – runs for the padded loop_time
                t_start = time.time()
                while time.time() - t_start < loop_time:
                    elapsed = time.time() - t_start
                    # fraction of the *theoretical* move (clamped to 1.0 after it finishes)
                    frac = elapsed / (rev_time * revs)
                    if frac > 1.0:
                        frac = 1.0
                    cur_rev = start_rev + frac * revs
                    cur_z = path_z(base_z, cur_rev)
                    arm.move_to(*fixed_xy, cur_z, roll=180, pitch=45, yaw=20, speed=100, wait=False)
                    time.sleep(0.1)

                # Wait until the turntable has *actually* stopped before continuing
                logger.info("Phase complete.")

            # ---------- Start spiral ----------
            logger.info("Parking arms on opposite sides...")
            self.right.arm.set_position(PARK_X, PARK_Y, PARK_Z, 180, 45, 20, speed=100, wait=True)
            self.left.arm.set_position(PARK_X, PARK_Y, PARK_Z, 180, 45, 20, speed=100, wait=True)

            # Phase 1: right arm base
            logger.info("=== PHASE 1: Right arm ===")
            print_phase(self.right, TOOL_RIGHT, Z_RIGHT, 0.0, float(RIGHT_REVS), right_xy, EXTRUSION_FACTOR_RIGHT_SOLO)

            # Phase 2: park right
            logger.info("=== PHASE 2: Park right ===")
            self.right.arm.set_position(PARK_X, PARK_Y, PARK_Z, 180, 45, 20, speed=100, wait=True)

            # Phase 3: left arm continues
            logger.info("=== PHASE 3: Left arm ===")
            start_z = path_z(Z_LEFT, RIGHT_REVS)
            self.left.move_to(PARK_X, PARK_Y, SAFE_Z, 180, 45, 20, speed=100, wait=True)
            self.left.move_to(*left_xy, start_z, 180, 45, 20, speed=100, wait=True)
            print_phase(self.left, TOOL_LEFT, Z_LEFT, float(RIGHT_REVS),
                        float(RIGHT_REVS + LEFT_REVS), left_xy, EXTRUSION_FACTOR_LEFT_SOLO)

            # Phase 4: both extruders together – constant 0.4 mm Z offset, smooth ramps
            total_revs = RIGHT_REVS + LEFT_REVS + COMBINED_REVS
            logger.info("=== PHASE 4: Both arms – synchronous extrusion ===")
            start_rev_co = float(RIGHT_REVS + LEFT_REVS)   # = 10

            layer_z = path_z(Z_LEFT, start_rev_co)         # 155.4 mm

            # --- Constant vertical offset between the two spirals ---
            LAYER_OFFSET = 0.6               # left always this much higher than right

            # --- Right bonding ramp: starts 0.1 mm lower, returns to 0 over 1 rev ---
            RIGHT_BOND_LOW = -0.1
            BOND_START_FRAC = 0.0
            BOND_END_FRAC   = 0.10           # 10% of COMBINED_REVS = 1 rev

            # --- Global lift (both nozzles together) ---
            GLOBAL_LIFT_AMOUNT = 0.2
            LIFT_START_FRAC = 0.05            # 5%
            LIFT_END_FRAC   = 0.15            # 15%  (1 rev later)

            # Move right arm into position (left stays where it is)
            self.right.move_to(PARK_X, PARK_Y, SAFE_Z, 180, 45, 0, speed=100, wait=True)
            self.right.move_to(*right_xy, SAFE_Z, 180, 45, 0, speed=100, wait=True)
            # Right starts at its lowest bonding offset
            self.right.move_to(*right_xy,
                               layer_z + RIGHT_BOND_LOW,  # base + bond offset
                               180, 45, 20, speed=50, wait=True)

            # --- Extrusion parameters (normal layer height) ---
            len_per_rev_left  = filament_per_rev(eff_radius_left)
            len_per_rev_right = filament_per_rev(eff_radius_right)

            rev_time = max(len_per_rev_left, len_per_rev_right) / FEED_RATE
            turntable_speed = 360.0 / rev_time

            # Very generous – loop runs twice theoretical time, covers deceleration
            LOOP_SAFETY = 2.0
            loop_time = rev_time * COMBINED_REVS * LOOP_SAFETY

            extrude_len_left  = len_per_rev_left  * COMBINED_REVS * EXTRUSION_FACTOR_LEFT_COMB
            extrude_len_right = len_per_rev_right * COMBINED_REVS * EXTRUSION_FACTOR_RIGHT_COMB

            logger.info("Left: %.0f mm, Right: %.0f mm, turntable %.1f°/s, loop %.1f s",
                        extrude_len_left, extrude_len_right, turntable_speed, loop_time)

            # Start turntable (full speed)
            target_angle = total_revs * 360.0
            self.turntable.rotate_absolute(target_angle, turntable_speed, wait=False)
            time.sleep(0.3)

            self.extruder.extrude_sync(extrude_len_left, FEED_RATE,
                                       extrude_len_right, FEED_RATE, wait=False)

            # --- Continuous Z‑update loop with smooth ramps ---
            t_start = time.time()
            while time.time() - t_start < loop_time:
                elapsed = time.time() - t_start
                frac = elapsed / (rev_time * COMBINED_REVS)   # can go beyond 1.0
                frac_clamped = min(frac, 1.0)

                cur_rev = start_rev_co + frac_clamped * COMBINED_REVS
                # Base Z from the continuous spiral (0.4 mm/rev)
                base_z = layer_z + (cur_rev - start_rev_co) * Z_STEP_PER_REV

                # --- Global lift ramp (0 → GLOBAL_LIFT_AMOUNT) ---
                lift_frac = (frac_clamped - LIFT_START_FRAC) / (LIFT_END_FRAC - LIFT_START_FRAC)
                lift_frac = max(0.0, min(1.0, lift_frac))
                global_lift = lift_frac * GLOBAL_LIFT_AMOUNT

                # --- Right bonding ramp (RIGHT_BOND_LOW → 0) ---
                bond_frac = (frac_clamped - BOND_START_FRAC) / (BOND_END_FRAC - BOND_START_FRAC)
                bond_frac = max(0.0, min(1.0, bond_frac))
                right_extra = RIGHT_BOND_LOW * (1.0 - bond_frac)   # decays to 0

                # Apply offsets – note the global lift is added to BOTH arms
                cur_z_left  = base_z + LAYER_OFFSET + global_lift
                cur_z_right = base_z + right_extra + global_lift

                self.left.move_to(*left_xy,  cur_z_left,  180, 45, 20, speed=100, wait=False)
                self.right.move_to(*right_xy, cur_z_right, 180, 45, 20, speed=100, wait=False)
                time.sleep(0.1)

            # Loop finished – turntable definitely stopped
            # No extra sleep, no wait_ok()

            # Park
            self.right.arm.set_position(PARK_X, PARK_Y, PARK_Z, 180, 45, 0, speed=100, wait=True)
            self.left.arm.set_position(PARK_X, PARK_Y, PARK_Z, 180, 45, 0, speed=100, wait=True)

            logger.info("✅ Spiral complete.")

        except Exception as e:
            print(f"Spiral error: {e}")



    # ======================================================================
    #  Extrusion & emergency
    # ======================================================================
    def extruders_off(self):
        if not self.hw_connected: return
        self.extruder.send_gcode("CANCEL_PRINT")

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
        self.extruder.extrude_sync(length, rate_mm_s, length, rate_mm_s, wait=False)

    # ======================================================================
    #  Manual coordinate control (with auto‑fill)
    # ======================================================================
    def _fill_pose(self, pose):
        if pose:
            self.coord_x.set(pose[0])
            self.coord_y.set(pose[1])
            self.coord_z.set(pose[2])
            self.coord_roll.set(pose[3])
            self.coord_pitch.set(pose[4])
            self.coord_yaw.set(pose[5])

    def fill_left_pos(self):
        if self.left:
            pose = self.left.get_pose()
            self._fill_pose(pose)

    def fill_right_pos(self):
        if self.right:
            pose = self.right.get_pose()
            self._fill_pose(pose)

    def send_left_move(self):
        if not self.left: return
        target = self._get_adjusted_coords(self.left_base)
        self.left.arm.set_position(*target, speed=50, wait=False)

    def send_right_move(self):
        if not self.right: return
        target = self._get_adjusted_coords(self.right_base)
        self.right.arm.set_position(*target, speed=50, wait=False)

    def _get_adjusted_coords(self, base_offset):
        return [self.coord_x.get() - base_offset[0],
                self.coord_y.get() - base_offset[1],
                self.coord_z.get() - base_offset[2],
                self.coord_roll.get()  - base_offset[3],
                self.coord_pitch.get() - base_offset[4],
                self.coord_yaw.get()   - base_offset[5]]

    def set_left_base(self):
        if self.left:
            pose = self.left.get_pose()
            if pose:
                self.left_base = list(pose[:6])
                self._update_offset_label()

    def clear_left_base(self):
        self.left_base = [0.0]*6
        self._update_offset_label()

    def set_right_base(self):
        if self.right:
            pose = self.right.get_pose()
            if pose:
                self.right_base = list(pose[:6])
                self._update_offset_label()

    def clear_right_base(self):
        self.right_base = [0.0]*6
        self._update_offset_label()

    def _update_offset_label(self):
        self.lbl_left_offset.config(
            text=f"Left offset: ({self.left_base[0]:.1f},{self.left_base[1]:.1f},"
                 f"{self.left_base[2]:.1f},{self.left_base[3]:.1f},"
                 f"{self.left_base[4]:.1f},{self.left_base[5]:.1f})")
        self.lbl_right_offset.config(
            text=f"Right offset: ({self.right_base[0]:.1f},{self.right_base[1]:.1f},"
                 f"{self.right_base[2]:.1f},{self.right_base[3]:.1f},"
                 f"{self.right_base[4]:.1f},{self.right_base[5]:.1f})")

    # ======================================================================
    #  Status polling & shutdown
    # ======================================================================
    def update_status(self):
        if self.hw_connected:
            try:
                left_pose = self.left.get_pose()
                right_pose = self.right.get_pose()
                if left_pose:
                    self.lbl_left_pos.config(
                        text=f"Left arm: x={left_pose[0]:.1f} y={left_pose[1]:.1f} z={left_pose[2]:.1f}")
                if right_pose:
                    self.lbl_right_pos.config(
                        text=f"Right arm: x={right_pose[0]:.1f} y={right_pose[1]:.1f} z={right_pose[2]:.1f}")

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

if __name__ == "__main__":
    root = tk.Tk()
    app = ControlPanel(root)
    root.mainloop()