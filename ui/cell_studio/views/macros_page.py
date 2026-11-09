"""Macros page: merge calibration, dual-arm vase, George's slicer.

Left: camera settings and the three macros. Right: what the camera sees (with
the detected beads drawn on), the spiral preview, and a log.
"""

import json
import os
import sys
import tempfile
import threading
import time

import numpy as np
from PySide6.QtCore import Qt, QProcess, QTimer, QRectF, QPointF
from PySide6.QtGui import QImage, QPainter, QPen, QColor, QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QPlainTextEdit, QFileDialog,
                               QMessageBox, QTabWidget, QGridLayout, QSizePolicy)

from .. import theme, macros, vision, gen_library
from ..state import DoubleVar, IntVar, BoolVar, StrVar
from ..widgets import (Card, NumberField, Check, Combo, Segmented, Section, FormGrid, button, label, hrow,
                       scroll, divider)
from .toolpath_view import ToolpathView

RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gen_runner.py")
DUAL_GEN = os.path.join(gen_library.LIBRARY_ROOT, "macros", "dual_vase_interweave.py")
STANDIN_SLICER = os.path.join(gen_library.LIBRARY_ROOT, "examples", "contour_slicer_example.py")


class CameraView(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 200)
        self.img = None
        self.det = None
        self.message = "No image yet. Press Live view, or run the calibration."

    def show_frame(self, frame, det=None):
        a = np.ascontiguousarray(np.asarray(frame, dtype=np.uint8))
        if a.ndim == 3:
            a = np.ascontiguousarray(a[..., :3].mean(axis=2).astype(np.uint8))
        h, w = a.shape
        self._buf = a
        self.img = QImage(self._buf.data, w, h, w, QImage.Format_Grayscale8)
        self.det = det
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0b0f14"))
        if self.img is None:
            p.setPen(QColor(theme.FAINT))
            p.drawText(self.rect().adjusted(16, 16, -16, -16), Qt.AlignCenter | Qt.TextWordWrap, self.message)
            return
        iw, ih = self.img.width(), self.img.height()
        sc = min(self.width() / iw, self.height() / ih)
        ox, oy = (self.width() - iw * sc) / 2, (self.height() - ih * sc) / 2
        p.drawImage(QRectF(ox, oy, iw * sc, ih * sc), self.img)
        d = self.det
        if d is not None:
            p.setFont(QFont(p.font().family(), 9))
            if getattr(d, "ok", False):
                for i, b in enumerate(d.beads):
                    col = QColor(theme.ACCENT if i == 0 else theme.RIGHT)
                    p.setPen(QPen(col, 2))
                    x, y = ox + b[0] * sc, oy + b[1] * sc
                    p.drawLine(QPointF(x, y - 14), QPointF(x, y + 40 * sc))
                    p.drawText(QPointF(x + 4, y - 4), f"bead {i + 1}")
                txt = "1 bead (merged)" if len(d.beads) == 1 else "2 beads"
            else:
                txt = d.message
            p.setPen(QColor("white"))
            p.fillRect(QRectF(8, 8, min(self.width() - 16, 420), 22), QColor(0, 0, 0, 150))
            p.drawText(QRectF(14, 8, self.width() - 28, 22), Qt.AlignVCenter, txt)


class MacrosPage(QWidget):
    def __init__(self, ctl, win):
        super().__init__()
        self.c, self.win = ctl, win
        t = ctl.tool
        # camera
        self.v_source = StrVar("Simulated")
        self.v_method = StrVar("silhouette")
        self.v_invert = BoolVar(False)
        self.v_outward_right = BoolVar(True)
        self.v_cam_angle = DoubleVar(45.0)
        # calibration
        self.v_radius = DoubleVar(40.0)
        self.v_sweep = DoubleVar(2.5)
        self.v_z = DoubleVar(t["layer_height"])
        self.v_width = DoubleVar(t["line_width"])
        self.v_print_speed = DoubleVar(0.25)
        self.v_right_angle = DoubleVar(-45.0)
        self.v_left_angle = DoubleVar(135.0)
        self.v_right_tool = IntVar(0)
        # dual vase
        self.v_dv = {"radius": DoubleVar(30.0), "height": DoubleVar(20.0), "layer_height": DoubleVar(t["layer_height"]),
                     "line_width": DoubleVar(t["line_width"]), "waves": DoubleVar(6.0), "amplitude": DoubleVar(1.0),
                     "antiphase": BoolVar(True)}
        # George's slicer
        self.v_slicer = StrVar(gen_library._settings().get("slicer_script", STANDIN_SLICER))
        self.v_slice_model = StrVar("")

        self.camera = None
        self.result = None
        self.proc = None
        self.dual = None
        self._live = QTimer(self)
        self._live.timeout.connect(self._live_tick)
        self._last_frame_post = 0.0

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        split.addWidget(self._left())
        split.addWidget(self._right())
        split.setStretchFactor(1, 1)
        split.setSizes([420, 880])
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 14, 12, 14)
        v.addWidget(split)
        ctl.job_state.changed.connect(lambda *_: self._update_buttons())
        self._show_last_alignment()
        self._update_buttons()

    # ================================================================ layout
    def _left(self):
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(4, 0, 12, 12)
        v.setSpacing(14)

        cam = Card("Camera", "Side camera looking along the wall at the disc edge, at the same height as the "
                             "first layer, with a plain backdrop behind.")
        fg = FormGrid()
        fg.row("Source", Combo(self.v_source, ["Simulated", "Camera 0", "Camera 1", "Camera 2"], width=150))
        fg.row("Detection", Segmented(self.v_method, [("silhouette", "Silhouette"), ("edges", "Edges")]))
        fg.row("Camera angle", NumberField(self.v_cam_angle, "°", 1, 5),
               "Angle on the disc (world frame) where the camera looks along the wall")
        cam.add_layout(fg)
        cam.add(Check("Beads lighter than the backdrop", self.v_invert))
        cam.add(Check("Larger radius appears to the right", self.v_outward_right))
        self.live_btn = button("Live view", "ghost", self.toggle_live)
        cam.add(self.live_btn)
        v.addWidget(cam)

        m1 = Card("1 · Merge calibration",
                  "The right arm prints a reference ring. The left arm prints the same layer while sweeping "
                  "across it, and the camera finds where its bead lands on the right's. Clear the disc first.")
        sec = Section("Settings", expanded=False)
        sec.row("Ring radius", NumberField(self.v_radius, "mm", 1, 1, minimum=10, maximum=140))
        sec.row("Sweep ±", NumberField(self.v_sweep, "mm", 2, 0.25, minimum=0.5, maximum=8))
        sec.row("Layer height", NumberField(self.v_z, "mm", 2, 0.05, minimum=0.1, maximum=1.0))
        sec.row("Line width", NumberField(self.v_width, "mm", 2, 0.05, minimum=0.3, maximum=3.0))
        sec.row("Disc speed", NumberField(self.v_print_speed, "rad/s", 2, 0.05, minimum=0.05, maximum=1.5))
        sec.row("Right arm at", NumberField(self.v_right_angle, "°", 1, 5))
        sec.row("Left arm at", NumberField(self.v_left_angle, "°", 1, 5))
        sec.row("Right arm tool", Segmented(self.v_right_tool, [(0, "T0 (E)"), (1, "T1 (X)")]),
                "Which extruder motor feeds the RIGHT arm; the other feeds the left")
        m1.add(sec)
        self.cal_btn = button("Run calibration", "primary", self.run_calibration)
        self.cal_stop = button("Stop", "danger", self.c.stop_print)
        self.cal_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        m1.add(hrow(self.cal_btn, self.cal_stop))
        self.res_lbl = label("", "CardHint", wrap=True)
        m1.add(self.res_lbl)
        self.apply_btn = button("Apply to left arm", None, self.apply_result)
        self.confirm_btn = button("Print confirmation spirals", "ghost", self.confirm_spirals,
                                  tip="Both arms print a few interleaved turns with the correction applied")
        for b in (self.apply_btn, self.confirm_btn):
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        m1.add(hrow(self.apply_btn, self.confirm_btn))
        self.last_lbl = label("", "Muted", wrap=True)
        m1.add(self.last_lbl)
        v.addWidget(m1)

        m2 = Card("2 · Dual-arm vase (FullControl)",
                  "Two FullControl spirals, one per arm, each laying every other layer of the same wall. "
                  "Waves in antiphase interweave; without waves both share one profile.")
        fg2 = FormGrid()
        d = self.v_dv
        fg2.row("Radius", NumberField(d["radius"], "mm", 1, 1, minimum=5, maximum=140))
        fg2.row("Height", NumberField(d["height"], "mm", 1, 1, minimum=0.5, maximum=200))
        fg2.row("Layer height", NumberField(d["layer_height"], "mm", 2, 0.05, minimum=0.05, maximum=1.0))
        fg2.row("Line width", NumberField(d["line_width"], "mm", 2, 0.05, minimum=0.2, maximum=3.0))
        fg2.row("Waves", NumberField(d["waves"], "/rev", 0, 1, minimum=0, maximum=60))
        fg2.row("Amplitude", NumberField(d["amplitude"], "mm", 2, 0.1, minimum=0, maximum=10))
        m2.add_layout(fg2)
        m2.add(Check("Antiphase (interweave)", d["antiphase"]))
        self.dv_gen_btn = button("Generate and preview", None, self.generate_dual)
        self.dv_start_btn = button("▶  Start dual vase", "primary", self.start_dual)
        for b in (self.dv_gen_btn, self.dv_start_btn):
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        m2.add(hrow(self.dv_gen_btn, self.dv_start_btn))
        m2.add(hrow(button("Pause", "ghost", self.c.toggle_pause), button("Stop", "danger", self.c.stop_print), None))
        v.addWidget(m2)

        m3 = Card("3 · George's code as the slicer",
                  "Slices a model with a slicer script (slice(model_path, …), or a script with a MODEL_PATH "
                  "constant) and opens it in Generators to preview, plan and print with the right arm.")
        self.slicer_lbl = label("", "Muted", wrap=True)
        self.slicer_lbl.setStyleSheet(f"font-family:{theme.FONT_MONO}; font-size:11px;")
        self.model_lbl = label("No model chosen", "Muted", wrap=True)
        self.model_lbl.setStyleSheet(f"font-family:{theme.FONT_MONO}; font-size:11px;")
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.addWidget(label("Slicer", "FieldLabel"), 0, 0)
        g.addWidget(self.slicer_lbl, 0, 1)
        g.addWidget(button("Choose…", "ghost", self.choose_slicer), 0, 2)
        g.addWidget(label("Model", "FieldLabel"), 1, 0)
        g.addWidget(self.model_lbl, 1, 1)
        g.addWidget(button("Choose…", "ghost", self.choose_model), 1, 2)
        g.setColumnStretch(1, 1)
        m3.add_layout(g)
        go = button("Slice in Generators  →", "primary", self.slice_in_generators)
        go.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        m3.add(go)
        self._show_slicer()
        v.addWidget(m3)
        v.addStretch(1)
        sa = scroll(inner)
        sa.setMinimumWidth(340)
        return sa

    def _right(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 0, 0, 0)
        vs = QSplitter(Qt.Vertical)
        vs.setHandleWidth(12)
        card = Card("View")
        self.tabs = QTabWidget()
        self.cam_view = CameraView()
        self.cam_view.setMinimumSize(280, 180)
        self.preview = ToolpathView()
        self.preview.set_message("Generate the dual vase or run the confirmation spirals to see them here.")
        self.tabs.addTab(self.cam_view, "Camera")
        self.tabs.addTab(self.preview, "Spirals")
        card.add(self.tabs, 1)
        vs.addWidget(card)
        logc = Card("Log")
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        logc.add(self.log_text, 1)
        vs.addWidget(logc)
        vs.setSizes([600, 260])
        v.addWidget(vs)
        return w

    # ================================================================ helpers
    def log(self, text):
        self.c.ui.post(lambda: self.log_text.appendPlainText(text))

    def _settings(self):
        return macros.MergeSettings(radius=self.v_radius.get(), z=self.v_z.get(), line_width=self.v_width.get(),
                                    sweep=self.v_sweep.get(), print_speed=self.v_print_speed.get(),
                                    camera_angle=self.v_cam_angle.get(), outward_right=self.v_outward_right.get(),
                                    right_angle=self.v_right_angle.get(), left_angle=self.v_left_angle.get(),
                                    right_tool=self.v_right_tool.get(),
                                    scan_speed=max(0.4, self.v_print_speed.get()))

    def _vision(self):
        return vision.VisionSettings(method=self.v_method.get(), invert=self.v_invert.get(), min_area=60)

    def _camera(self, settings=None):
        src = self.v_source.get()
        if src == "Simulated":
            if not self.c.connected_var.get() or any(v != "sim" for v in self.c.connected_var.get().values()):
                raise RuntimeError("The simulated camera needs the simulated hardware: tick Simulated hardware "
                                   "in Connections and press Connect.")
            return macros.sim_bead_camera_for(self.c, settings or self._settings())
        idx = int(src.split()[-1])
        if not isinstance(self.camera, vision.CvCamera) or self.camera.index != idx:
            if self.camera is not None:
                self.camera.close()
            self.camera = vision.CvCamera(idx)
        return self.camera

    def toggle_live(self):
        if self._live.isActive():
            self._live.stop()
            self.live_btn.setText("Live view")
            return
        try:
            self._live_cam = self._camera()
            self._live_cam.open()
        except Exception as e:
            QMessageBox.warning(self, "Camera", str(e))
            return
        self.tabs.setCurrentIndex(0)
        self._live.start(250)
        self.live_btn.setText("Stop live view")

    def _live_tick(self):
        if self.c.printing:
            return
        try:
            img = self._live_cam.read()
        except Exception as e:
            self._live.stop()
            self.live_btn.setText("Live view")
            self.cam_view.message = str(e)
            self.cam_view.img = None
            self.cam_view.update()
            return
        self.cam_view.show_frame(img, vision.detect_beads(img, self._vision()))

    def _frame_from_worker(self, img, det):
        now = time.time()
        if now - self._last_frame_post > 0.1:
            self._last_frame_post = now
            self.c.ui.post(lambda: self.cam_view.show_frame(img, det))

    def _busy(self, what):
        c = self.c
        if c.printing or c.busy or c.model_job_active() or self.proc is not None:
            QMessageBox.warning(self, what, "A job is running. Wait for it to finish or press Stop.")
            return True
        return False

    # ================================================================ 1 merge calibration
    def run_calibration(self):
        c = self.c
        if self._busy("Calibration"):
            return
        if not c._require("left", "right", "turntable", "extruder"):
            return
        s = self._settings()
        try:
            cam = self._camera(s)
            cam.open()
        except Exception as e:
            QMessageBox.warning(self, "Camera", str(e))
            return
        if not c.ui.confirm("Merge calibration",
                            f"Both arms will print a ring at radius {s.radius:g} mm on the disc "
                            f"(right at {s.right_angle:g}°, left at {s.left_angle:g}°), then the disc turns "
                            "once for the camera.\n\nIs the disc clear and are both tools at temperature?"):
            return
        if self._live.isActive():
            self.toggle_live()
        self.tabs.setCurrentIndex(0)
        c.printing = True
        c.stop_requested = False
        c.paused = False
        c.job_name.set("Merge calibration")
        c.job_state.set("running")
        self.result = None
        self.log_text.clear()

        def work():
            try:
                res = macros.MergeCalibration(c, cam, s, self._vision(), log=self.log,
                                              on_frame=self._frame_from_worker).run()
                c.ui.post(lambda: self._calibrated(res, None))
            except Exception as e:
                c.ui.post(lambda e=e: self._calibrated(None, e))
            finally:
                c.printing = False
                c.ui.post(lambda: c.job_state.set("idle"))
        threading.Thread(target=work, daemon=True).start()

    def _calibrated(self, res, err):
        if err is not None:
            self.res_lbl.setText(f"Calibration stopped: {err}")
            self.log(f"Stopped: {err}")
            self._update_buttons()
            return
        self.result = res
        side = "outwards" if res.merge_offset_mm > 0 else "inwards"
        self.res_lbl.setText(
            f"Left bead lands {abs(res.merge_offset_mm):.3f} mm {'inside' if res.merge_offset_mm > 0 else 'outside'} "
            f"the right's: move the left arm {abs(res.merge_offset_mm):.3f} mm {side}. "
            f"Left nozzle {abs(res.height_diff_mm):.3f} mm {'higher' if res.height_diff_mm > 0 else 'lower'}. "
            f"Fit {res.fit_rms_mm:.3f} mm RMS over {res.points} frames, {res.scale_px_per_mm:.1f} px/mm.")
        self.log("Result: " + json.dumps({k: v for k, v in res.__dict__.items() if k != "notes"}))
        for n in res.notes:
            self.log("Note: " + n)
        self._update_buttons()

    def apply_result(self):
        if not self.result:
            return
        r = self.result
        if not self.c.ui.confirm("Apply", f"Radial offset · left: {r.radial_offset_left_before:+.3f} → "
                                          f"{r.radial_offset_left_after:+.3f} mm\nCentre Z · left: "
                                          f"{r.centre_z_left_before:.3f} → {r.centre_z_left_after:.3f} mm\n\nApply?"):
            return
        macros.apply_merge(self.c, r)
        self.log("Applied to the left arm and saved to config/alignment.json.")
        self._show_last_alignment()
        self._update_buttons()

    def _show_last_alignment(self):
        hist = [h for h in macros.load_alignments() if h.get("applied")]
        if hist:
            h = hist[-1]
            self.last_lbl.setText(f"Last applied {h['time']}: left radial {h['radial_offset_left_after']:+.3f} mm, "
                                  f"centre Z {h['centre_z_left_after']:.3f} mm (arms at {h['right_angle']:g}° / "
                                  f"{h['left_angle']:g}°).")
        else:
            self.last_lbl.setText("No calibration applied yet.")

    def confirm_spirals(self):
        s = self._settings()
        params = {"radius": s.radius, "height": 4 * s.z, "layer_height": s.z, "line_width": s.line_width,
                  "waves": 0, "amplitude": 0.0, "antiphase": False,
                  "right_start": s.right_angle, "left_start": s.left_angle}
        self._generate(params, start_after=True, name="Confirmation spirals")

    # ================================================================ 2 dual vase
    def _dual_params(self):
        p = {k: v.get() for k, v in self.v_dv.items()}
        p.update(right_start=self.v_right_angle.get(), left_start=self.v_left_angle.get())
        return p

    def generate_dual(self):
        self._generate(self._dual_params(), start_after=False, name="Dual vase")

    def start_dual(self):
        if self.dual is None:
            self._generate(self._dual_params(), start_after=True, name="Dual vase")
        else:
            self._start(self.dual, "Dual vase")

    def _generate(self, params, start_after, name):
        if self._busy(name):
            return
        tmp = tempfile.mkdtemp(prefix="cellmacro_")
        job, out = os.path.join(tmp, "job.json"), os.path.join(tmp, "result.json")
        with open(job, "w") as f:
            json.dump({"params": params, "defaults": {}}, f)
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.finished.connect(lambda *_: self._generated(out, start_after, name))
        self.log(f"{name}: generating the spirals with FullControl…")
        self._update_buttons()
        self.proc.start(sys.executable, [RUNNER, DUAL_GEN, job, out])
        self.proc.closeWriteChannel()
        QTimer.singleShot(60000, lambda p=self.proc: p.kill() if p is self.proc and p is not None else None)

    def _generated(self, out, start_after, name):
        self.proc = None
        try:
            with open(out) as f:
                r = json.load(f)
        except Exception:
            r = {"ok": False, "error": "The generator did not finish."}
        if not r.get("ok"):
            self.log(f"{name}: {r.get('error')}\n{r.get('traceback', '')}")
            QMessageBox.warning(self, name, r.get("error", "Generation failed"))
            self._update_buttons()
            return
        tp = {"paths": r["paths"]}
        arms, d, problems, warnings = macros.prepare_spirals(
            tp, self.c.param_vars["filament_diameter"].get(), 1.0)
        self.preview.set_toolpath(tp)
        self.tabs.setCurrentIndex(1)
        for w in warnings:
            self.log("Check: " + w)
        if problems:
            self.log("Cannot print:\n• " + "\n• ".join(problems))
            QMessageBox.warning(self, name, "\n".join(problems))
            self._update_buttons()
            return
        total = max(a.end for a in arms.values())
        sp = self.c.turntable_speed_var.get()
        self.log(f"{name}: two spirals, {total / 6.283:.1f} turns, about {total / max(sp, 1e-6) / 60:.1f} min "
                 f"at {sp:g} rad/s.")
        if name == "Dual vase":
            self.dual = (arms, d)
        if start_after:
            self._start((arms, d), name)
        self._update_buttons()

    def _start(self, prepared, name):
        if self._busy(name):
            return
        arms, d = prepared
        c = self.c
        if not c._require("left", "right", "turntable", "extruder"):
            return
        hist = [h for h in macros.load_alignments() if h.get("applied")]
        warn = []
        if not hist:
            warn.append("No merge calibration has been applied: the arms may not land on the same profile.")
        else:
            h = hist[-1]
            for side in ("right", "left"):
                off = abs((arms[side].start_deg - h[f"{side}_angle"] + 180) % 360 - 180)
                if off > 10:
                    warn.append(f"The {side} arm starts {off:.0f}° from where it was calibrated "
                                f"({h[f'{side}_angle']:g}°); the radial correction may not hold there.")
        ro, lo = c.orientation("right"), c.orientation("left")
        text = (f"{name}: right arm at {arms['right'].start_deg:.0f}°, left at {arms['left'].start_deg:.0f}°.\n"
                f"Right arm tool T{self.v_right_tool.get()}, left T{1 - self.v_right_tool.get()}.\n"
                f"Orientation (roll/pitch/yaw): right {ro[0]:g}/{ro[1]:g}/{ro[2]:g}, left {lo[0]:g}/{lo[1]:g}/{lo[2]:g} "
                f"(Settings ▸ Tool and nozzle).\n"
                f"Disc speed {c.turntable_speed_var.get():g} rad/s (live slider).")
        if warn:
            text += "\n\nCheck:\n• " + "\n• ".join(warn)
        if not c.ui.confirm(name, text + "\n\nStart?"):
            return
        job = macros.DualSpiralJob(c, arms, d, right_tool=self.v_right_tool.get(), name=name, log=self.log)
        job.start()
        self._update_buttons()

    # ================================================================ 3 George's slicer
    def _show_slicer(self):
        p = self.v_slicer.get()
        self.slicer_lbl.setText(os.path.basename(p) if p else "none")
        self.slicer_lbl.setToolTip(p)

    def choose_slicer(self):
        path, _ = QFileDialog.getOpenFileName(self, "Slicer script", gen_library.LIBRARY_ROOT, "Python (*.py)")
        if not path:
            return
        m = gen_library.read_meta(path)
        if not m.get("needs_model"):
            QMessageBox.warning(self, "Slicer", "That script does not take a model: it needs a slice(model_path, …) "
                                                "function or a MODEL_PATH = \"….stl\" constant.")
            return
        self.v_slicer.set(path)
        s = gen_library._settings()
        s["slicer_script"] = path
        try:
            os.makedirs(os.path.dirname(gen_library.SETTINGS_FILE), exist_ok=True)
            with open(gen_library.SETTINGS_FILE, "w") as f:
                json.dump(s, f, indent=2)
        except Exception:
            pass
        self._show_slicer()

    def choose_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "Model to slice", "",
                                              "3D models (*.stl *.3mf *.obj *.ply *.step *.stp);;All files (*)")
        if path:
            self.v_slice_model.set(path)
            self.model_lbl.setText(os.path.basename(path))
            self.model_lbl.setToolTip(path)

    def slice_in_generators(self):
        path = self.v_slicer.get()
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Slicer", "Choose the slicer script first.")
            return
        if not os.path.isfile(self.v_slice_model.get()):
            QMessageBox.warning(self, "Slicer", "Choose the model to slice first.")
            return
        g = self.win.generators
        g.v_num_arms.set(1)                      # right arm prints (primary)
        g.load_meta(gen_library.read_meta(path))
        g.set_model(self.v_slice_model.get())
        self.win.go(3)
        g.generate()

    # ================================================================ state
    def _update_buttons(self):
        busy = self.c.printing or self.c.busy or self.proc is not None
        self.cal_btn.setEnabled(not busy)
        self.cal_stop.setEnabled(self.c.printing)
        self.apply_btn.setEnabled(self.result is not None and not busy and not self.result.applied)
        self.confirm_btn.setEnabled(not busy)
        self.dv_gen_btn.setEnabled(not busy)
        self.dv_start_btn.setEnabled(not busy)
