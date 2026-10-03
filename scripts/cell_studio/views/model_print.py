"""Model print page (the slicer workflow), ported from slicer_tab.py.

Import -> Slice + preview -> Print / Dry run. The slicing, planning and
streaming code is unchanged; settings that lived in pop up dialogs are now
collapsible sections beside the preview.
"""

import csv as csvmod
import logging
import math
import numpy as np
import os
import threading
import time

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath
from PySide6.QtWidgets import (QTabWidget, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QSlider,
                               QPlainTextEdit, QFileDialog, QMessageBox)

from .. import theme
from ..state import DoubleVar, IntVar, BoolVar, StrVar
from ..widgets import (Card, NumberField, Check, Combo, Section, Segmented,
                       button, label, hrow, scroll, divider, Dot)

logger = logging.getLogger("slicer_tab")
SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class LayerView(QWidget):
    def __init__(self):
        super().__init__()
        self.result = None
        self.idx = 0
        self.show_neighbours = True              # previous (dark) and next (faint) layers too
        self.side = False                        # side view: radius from the axis vs height
        self.setMinimumSize(360, 300)

    def show_layer(self, result, idx):
        self.result, self.idx = result, idx
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0b0f14"))
        w, h = self.width(), self.height()
        if not self.result or not (0 <= self.idx < len(self.result.layers)):
            p.setPen(QColor(theme.FAINT))
            p.setFont(QFont(p.font().family(), 12))
            p.drawText(self.rect(), Qt.AlignCenter, "Import a model and slice it to see layers here")
            return
        layer = self.result.layers[self.idx]
        b = self.result.bounds
        if self.side:
            rmax = float(np.hypot(b[:, 0], b[:, 1]).max()) if hasattr(b, "shape") else 100.0
            minx, maxx = -rmax, rmax
            miny, maxy = float(b[0][2]), float(b[1][2])
        else:
            minx, miny = float(b[0][0]), float(b[0][1])
            maxx, maxy = float(b[1][0]), float(b[1][1])
        span = max(maxx - minx, maxy - miny, 1.0)
        top = 62                                  # room for the layer caption
        scale = (min(w, h - top) - 40) / span
        ox = w / 2 - (minx + maxx) / 2 * scale
        oy = top + (h - top) / 2 + (miny + maxy) / 2 * scale
        # bounding box
        p.setPen(QPen(QColor("#1f2731"), 1, Qt.DashLine))
        p.drawRect(QRectF(ox + minx * scale, oy - maxy * scale, (maxx - minx) * scale, (maxy - miny) * scale))
        side = self.side

        def xy(pt, lay):
            if not side:
                return pt[0], pt[1]
            r = math.hypot(pt[0], pt[1])
            r = r if pt[1] >= 0 else -r              # near half to the right, far half to the left
            return r, (pt[2] if len(pt) >= 3 else lay.z)

        def draw_layer(lay, colour_of, width):
            for path in lay.paths:
                pts = path.points + ([path.points[0]] if (path.closed and len(path.points) > 1) else [])
                if len(pts) < 2:
                    continue
                x0, y0 = xy(pts[0], lay)
                qp = QPainterPath(QPointF(ox + x0 * scale, oy - y0 * scale))
                for pt in pts[1:]:                       # (x, y) or (x, y, z, width, height)
                    x1, y1 = xy(pt, lay)
                    qp.lineTo(ox + x1 * scale, oy - y1 * scale)
                p.setPen(QPen(colour_of(path.kind), width))
                p.drawPath(qp)

        layers = self.result.layers
        # previous layer: darker, under everything
        if self.show_neighbours and self.idx > 0:
            draw_layer(layers[self.idx - 1], lambda k: QColor(theme.KIND_COLOR.get(k, "#888888")).darker(260), 2.2)
        # current layer: bright
        draw_layer(layer, lambda k: QColor(theme.KIND_COLOR.get(k, "#888888")).lighter(125), 1.6)
        # next layer: faint on top, so where the three cross you still see the other two
        if self.show_neighbours and self.idx + 1 < len(layers):
            def faint(k):
                c = QColor("#ffffff")
                c.setAlpha(70)
                return c
            draw_layer(layers[self.idx + 1], faint, 1.2)
        if self.show_neighbours:
            p.setFont(QFont(p.font().family(), 9))
            y0 = h - 16
            for text, col in (("previous", QColor(theme.KIND_COLOR.get("WALL_OUTER")).darker(260)),
                              ("this layer", QColor(theme.KIND_COLOR.get("WALL_OUTER")).lighter(125)),
                              ("next", QColor(255, 255, 255, 110))):
                p.setPen(QPen(col, 3))
                p.drawLine(QPointF(16, y0), QPointF(34, y0))
                p.setPen(QColor(theme.MUTED))
                p.drawText(QPointF(40, y0 + 4), text)
                p.translate(110, 0)
            p.resetTransform()
        p.setPen(QColor(theme.TEXT))
        p.setFont(QFont(p.font().family(), 11, QFont.DemiBold))
        p.drawText(QPointF(16, 26), f"Layer {self.idx + 1} / {len(self.result.layers)}")
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont(p.font().family(), 10))
        zs = [pt[2] for path in layer.paths for pt in path.points if len(pt) >= 3]
        ztxt = f"z = {min(zs):.1f}–{max(zs):.1f} mm" if zs and max(zs) - min(zs) > 0.05 else f"z = {layer.z:.2f} mm"
        p.drawText(QPointF(16, 46), f"{ztxt}   ·   {'solid' if layer.solid else 'sparse'}"
                   + ("   ·   side view (radius vs height)" if side else ""))


class ModelPrintPage(QWidget):
    @property
    def program(self):
        return getattr(self, "_program", None)

    @program.setter
    def program(self, prog):
        """Setting the plan also loads it into the Simulation tab (or clears it)."""
        self._program = prog
        sim = getattr(self, "sim", None)
        if sim is not None:
            sim.set_program(prog)

    def __init__(self, ctl, win):
        super().__init__()
        self.app = ctl
        self.win = win
        self.slice_result = None
        self.program = None
        self.model_path = None
        self.printing = False
        self.dry = False
        self.stop_requested = False
        self.need_program_msg = "Slice a model first (steps 1 and 2)."
        self.redo_verb = "Slice"
        self._planned_settings = None

        # slice settings
        self.v_layer_height = DoubleVar(ctl.tool["layer_height"])     # Settings ▸ Tool and nozzle
        self.v_line_width = DoubleVar(ctl.tool["line_width"])
        self.v_wall_count = IntVar(2)
        self.v_infill_density = DoubleVar(20.0)
        self.v_infill_pattern = StrVar("grid")
        self.v_strategy = StrVar("auto")
        self.v_cone_angle = DoubleVar(15.0)
        self.v_strategy_used = StrVar("")
        self.v_top_layers = IntVar(3)
        self.v_bottom_layers = IntVar(3)
        # machine / motion
        self.v_num_arms = IntVar(1)
        self.v_use_turntable = BoolVar(True)
        self.v_use_rules = BoolVar(True)
        self.v_tt_for_infill = BoolVar(False)
        self.v_print_speed = DoubleVar(30.0)
        self.v_travel_speed = DoubleVar(150.0)
        self.v_max_arm_speed = DoubleVar(100.0)
        self.v_max_tt_speed = DoubleVar(1.5)
        self.v_part_off_x = DoubleVar(0.0)
        self.v_part_off_y = DoubleVar(0.0)
        self.v_az_left = DoubleVar(-45.0)
        self.v_az_right = DoubleVar(135.0)
        self.v_filament = DoubleVar(1.75)
        self.v_min_seg = DoubleVar(0.0)
        self.v_max_seg = DoubleVar(1.0)
        self.v_blend_radius = DoubleVar(1.0)
        # flow
        self.v_flow = DoubleVar(100.0)
        self.v_first_layer_flow = DoubleVar(120.0)
        # calibration (right arm defaults)
        self.v_center_x = DoubleVar(self._app_param('tt_cx_right', 575.6))
        self.v_center_y = DoubleVar(self._app_param('tt_cy_right', 3.5))
        self.v_center_z = DoubleVar(self._app_param('tt_cz_right', 152.0))
        self.v_z_base = DoubleVar(self._app_param('z_start', 152.0))
        # debug / preview
        self.v_debug = BoolVar(False)
        self.v_status = StrVar("No model loaded.")
        self.v_model = StrVar("")

        self._build()

    def _app_param(self, name, default):
        try:
            return float(self.app.param_vars[name].get())
        except Exception:
            return default

    def _debug_csv_path(self):
        return os.path.join(SCRIPTS_DIR, "motion_debug.csv")

    # ------------------------------------------------------------------ layout
    def _build(self):
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        split.addWidget(self._left())
        split.addWidget(self._right())
        split.setStretchFactor(1, 1)
        split.setSizes([420, 900])
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 18)
        v.addWidget(split)

    def _step(self, n, text, btn):
        num = label(str(n))
        num.setAlignment(Qt.AlignCenter)
        num.setFixedSize(26, 26)
        num.setStyleSheet(f"background:{theme.RAISED}; border:1px solid {theme.BORDER_STRONG};"
                          f"border-radius:13px; color:{theme.MUTED}; font-weight:600;")
        col = QWidget()
        cv = QVBoxLayout(col)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(6)
        cv.addWidget(label(text, "FieldLabel"))
        cv.addWidget(btn)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        h.addWidget(num, 0, Qt.AlignTop)
        h.addWidget(col, 1)
        return row

    def _left(self):
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(4, 0, 12, 12)
        v.setSpacing(14)

        wf = Card("Workflow")
        wf.add(self._step(1, "Import a model (3MF, STL, OBJ, PLY)",
                          button("Import model…", None, self.import_model)))
        model = label("", "Muted")
        model.setStyleSheet(f"font-family:{theme.FONT_MONO}; font-size:12px; color:{theme.ACCENT};")
        self.v_model.changed.connect(model.setText)
        wf.add(model)
        wf.add(self._step(2, "Slice and plan the motion", button("Slice + preview", None, self.do_slice)))
        self.print_btn = button("▶  Print", "primary", self.start_print)
        wf.add(self._step(3, "Stream to the machine", self.print_btn))
        wf.add(hrow(button("Dry run", "ghost", self.start_dry_run,
                           tip="Walks the whole program without hardware and writes motion_debug.csv"),
                    button("Stop", "danger", self.stop_print), None))
        st = label(self.v_status.get(), "CardHint", wrap=True)
        self.v_status.changed.connect(st.setText)
        wf.add(st)
        v.addWidget(wf)

        box = QWidget()
        bv = QVBoxLayout(box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(2)

        ps = Section("Print settings")
        ps.row("Layer height", NumberField(self.v_layer_height, "mm", 2, 0.05, minimum=0.01))
        ps.row("Line width", NumberField(self.v_line_width, "mm", 2, 0.05, minimum=0.05))
        ps.row("Wall count", NumberField(self.v_wall_count, "", 0, 1, integer=True, minimum=0, maximum=20))
        ps.row("Layer strategy", Combo(self.v_strategy, ["auto", "planar", "spiral", "cone_out", "cone_in"], width=130),
               "auto picks from the model: cones for overhangs (cone_out grows outward like a tree), "
               "spiral for one continuous loop, else planar")
        ps.row("Cone angle", NumberField(self.v_cone_angle, "°", 0, 5, minimum=5, maximum=40))
        ps.row("Infill density", NumberField(self.v_infill_density, "%", 0, 5, minimum=0, maximum=100))
        ps.row("Infill pattern", Combo(self.v_infill_pattern, ["grid", "lines"], width=130))
        ps.row("Bottom layers", NumberField(self.v_bottom_layers, "", 0, 1, integer=True, minimum=0, maximum=50))
        ps.row("Top layers", NumberField(self.v_top_layers, "", 0, 1, integer=True, minimum=0, maximum=50))
        ps.row("Flow", NumberField(self.v_flow, "%", 0, 5, minimum=0))
        ps.row("First layer flow", NumberField(self.v_first_layer_flow, "%", 0, 5, minimum=0))
        bv.addWidget(ps); bv.addWidget(divider())

        bv.addWidget(self._machine_section()); bv.addWidget(divider())
        bv.addWidget(self._calibration_section()); bv.addWidget(divider())
        bv.addWidget(Check("Write debug log while printing", self.v_debug))
        v.addWidget(box)
        v.addStretch(1)
        sa = scroll(inner)
        sa.setMinimumWidth(340)
        return sa

    def _machine_section(self):
        mm = Section("Machine + motion", expanded=False)
        mm.row("Arms", Segmented(self.v_num_arms, [(1, "1"), (2, "2 (plan only)")]),
               "The planner can split a model across two arms, but printing drives one arm so far. "
               "Use 2 for planning and dry runs.")
        mm.full(Check("Use turntable (off = Cartesian plate)", self.v_use_turntable))
        mm.full(Check("Follow the print rules (Settings ▸ Print rules)", self.v_use_rules))
        infill_chk = Check("Turntable coordinates infill too (rules off only)", self.v_tt_for_infill)
        infill_chk.setEnabled(not self.v_use_rules.get())
        self.v_use_rules.changed.connect(lambda on: infill_chk.setEnabled(not on))
        mm.full(infill_chk)
        mm.row("Print speed", NumberField(self.v_print_speed, "mm/s", 1, 1))
        mm.row("Travel speed", NumberField(self.v_travel_speed, "mm/s", 1, 5))
        mm.row("Max arm speed", NumberField(self.v_max_arm_speed, "mm/s", 1, 5))
        mm.row("Max turntable speed", NumberField(self.v_max_tt_speed, "rad/s", 2, 0.1))
        mm.row("Corner blend radius", NumberField(self.v_blend_radius, "mm", 2, 0.1, minimum=0))
        mm.row("Wall arc resolution", NumberField(self.v_max_seg, "mm", 2, 0.1, minimum=0))
        mm.row("Min segment length", NumberField(self.v_min_seg, "mm", 2, 0.1, minimum=0))
        mm.row("Part offset X", NumberField(self.v_part_off_x, "mm", 1, 1))
        mm.row("Part offset Y", NumberField(self.v_part_off_y, "mm", 1, 1))
        mm.row("Arm 1 azimuth", NumberField(self.v_az_left, "°", 1, 5))
        mm.row("Arm 2 azimuth", NumberField(self.v_az_right, "°", 1, 5))

        return mm

    def _calibration_section(self):
        cal = Section("Calibration (turntable axis, arm frame)", expanded=False)
        cal.row("Centre X", NumberField(self.v_center_x, "mm", 1, 0.1))
        cal.row("Centre Y", NumberField(self.v_center_y, "mm", 1, 0.1))
        cal.row("Centre Z", NumberField(self.v_center_z, "mm", 1, 0.1))
        cal.row("Z base", NumberField(self.v_z_base, "mm", 1, 0.1), "World Z of model z = 0")
        cal.row("Filament diameter", NumberField(self.v_filament, "mm", 2, 0.05))
        return cal

    def _right(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 0, 0, 0)
        v.setSpacing(0)
        vs = QSplitter(Qt.Vertical)
        vs.setHandleWidth(12)

        prev = Card("Preview")
        for kind, col in theme.KIND_COLOR.items():
            prev.add_header_widget(Dot(col, 8))
            lb = label(kind.replace("_", " ").title(), "Muted")
            lb.setStyleSheet("font-size:12px; margin-right:8px;")
            prev.add_header_widget(lb)
        self.canvas = LayerView()
        layers_tab = QWidget()
        lt = QVBoxLayout(layers_tab)
        lt.setContentsMargins(0, 6, 0, 0)
        lt.addWidget(self.canvas, 1)
        self.layer_slider = QSlider(Qt.Horizontal)
        self.layer_slider.setRange(0, 0)
        self.layer_slider.valueChanged.connect(lambda i: self.canvas.show_layer(self.slice_result, i))
        self.layer_lbl = label("Layer", "Muted")
        self.layer_slider.valueChanged.connect(lambda i: self.layer_lbl.setText(f"Layer {i + 1}"))
        side_chk = Check("Side view", BoolVar(False))
        side_chk.toggled.connect(lambda on: (setattr(self.canvas, "side", on), self.canvas.update()))
        nb_chk = Check("Prev / next", BoolVar(True))
        nb_chk.toggled.connect(lambda on: (setattr(self.canvas, "show_neighbours", on), self.canvas.update()))
        lt.addWidget(hrow(self.layer_lbl, self.layer_slider, nb_chk, side_chk, spacing=10))
        from .sim_view import SimulationView
        self.sim = SimulationView()
        self.preview_tabs = QTabWidget()
        self.preview_tabs.addTab(layers_tab, "Layers")
        self.preview_tabs.addTab(self.sim, "Simulation")
        self.preview_tabs.currentChanged.connect(lambda i: i == 0 and self.sim.stop())
        prev.add(self.preview_tabs, 1)
        vs.addWidget(prev)

        rep = Card("Report and motion debug")
        self.stats_text = QPlainTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        rep.add(self.stats_text, 1)
        vs.addWidget(rep)
        vs.setSizes([600, 240])
        v.addWidget(vs)
        return w

    # ------------------------------------------------------------------ logic
    def _log(self, text, append=False):
        if not append:
            self.stats_text.clear()
        self.stats_text.appendPlainText(text)

    def _settings(self):
        from slicer import SliceSettings
        nozzle = float(self.app.tool.get("nozzle_diameter", 1.2))
        rs = self._rules() if self.v_use_rules.get() else None
        thin = dict(thin_mode="skip")
        if rs is not None:
            thin = dict(thin_mode=str(rs.get("lines.thin_features", "centreline")),
                        thin_min_width=float(rs.get("lines.min_width", 0.8)) * nozzle,
                        thin_skip_below=float(rs.get("lines.thin_skip_below", 0.4)) * nozzle)
        return SliceSettings(
            **thin,
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
        az = (math.radians(float(self.v_az_left.get())), math.radians(float(self.v_az_right.get())))
        return PlannerConfig(
            num_arms=int(self.v_num_arms.get()),
            use_turntable=bool(self.v_use_turntable.get()),
            turntable_for_infill=bool(self.v_tt_for_infill.get()),
            center=(float(self.v_center_x.get()), float(self.v_center_y.get()), float(self.v_center_z.get())),
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
            rules=self._rules() if self.v_use_rules.get() else None,
            max_segment_length=float(self.v_max_seg.get()),
            extruder_tool=0,
            orientation=self.app.orientation("right"),   # arm 0 = right (primary)
        )

    def redo(self):
        self.do_slice()

    def _settings_snapshot(self):
        return {k: v.get() for k, v in vars(self).items() if k.startswith("v_") and
                k not in ("v_status", "v_model", "v_debug", "v_strategy_used")}

    def _changed_since_plan(self):
        if self._planned_settings is None:
            return []
        now = self._settings_snapshot()
        return [k[2:].replace("_", " ") for k in now if now[k] != self._planned_settings.get(k)]

    def import_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import model", "",
                                              "3D models (*.3mf *.stl *.obj *.ply);;All files (*)")
        if not path:
            return
        self.load_model(path)

    def load_model(self, path):
        self.model_path = path
        self.slice_result = None
        self.program = None
        self.v_model.set(os.path.basename(path))
        self.v_status.set(f"Loaded {os.path.basename(path)}. Next: Slice + preview.")
        self._log(f"Imported {os.path.basename(path)}")
        self.canvas.show_layer(None, 0)

    def do_slice(self):
        """Slice and plan on a worker thread: big models take tens of seconds and must not freeze the panel."""
        if not self.model_path:
            QMessageBox.warning(self, "Slicer", "Import a model first.")
            return
        if getattr(self, "_slicing", False):
            return
        try:
            from slicer import slice_model
            from planner import plan, analyze, dt_stats, extrusion_runs  # noqa: F401
        except Exception as e:
            QMessageBox.critical(self, "Slicer", f"Slicing packages unavailable:\n{e}\n\n"
                                 "pip install trimesh shapely numpy scipy networkx")
            return
        settings, cfg, path = self._settings(), self._planner_config(), self.model_path
        rs = cfg.rules
        self._slicing = True
        self.program = None
        self.v_status.set("Slicing…")
        snapshot = self._settings_snapshot()
        post = self.app.ui.post
        want = self.v_strategy.get()
        settings.cone_angle_deg = float(self.v_cone_angle.get())
        if rs is not None and rs.get("nonplanar.enabled", False):
            from rules import classify_layers
            settings.layer_style = lambda stats: [st for _, st, _ in classify_layers(stats, rs)[0]]

        def progress(done, total):
            post(lambda d=done, t=total: self.v_status.set(f"Slicing… layer {d} of {t}"))

        def work():
            try:
                if want == "auto":
                    from dataclasses import replace
                    from rules import recommend_strategy, RuleSet
                    post(lambda: self.v_status.set("Looking at the model to choose a layer strategy…"))
                    coarse = slice_model(path, replace(settings, layer_height=max(2.0, settings.layer_height),
                                                       wall_count=1, infill_density=0.0, top_layers=0,
                                                       bottom_layers=0, thin_mode="skip", strategy="planar",
                                                       layer_style=None))
                    strat, why = recommend_strategy(coarse.region_features, rs or RuleSet.load())
                    settings.strategy = strat
                    post(lambda: self._log(f"Layer strategy: {strat} ({why}) [rule strategy.default=auto]", append=True))
                else:
                    settings.strategy = want
                post(lambda: self.v_strategy_used.set(settings.strategy))
                res = slice_model(path, settings, progress)
                post(lambda: self.v_status.set(f"Planning the motion for {len(res.layers)} layers…"))
                out = self._plan_compute(res, cfg)
                post(lambda: self._sliced(res, out, snapshot, None))
            except Exception as e:
                post(lambda e=e: self._sliced(None, None, snapshot, e))
        threading.Thread(target=work, daemon=True).start()

    def _sliced(self, res, out, snapshot, err):
        self._slicing = False
        if err is not None:
            self.v_status.set("Slicing failed.")
            self._log(f"Slicing failed: {type(err).__name__}: {err}")
            QMessageBox.critical(self, "Slice failed", str(err))
            return
        self.slice_result = res
        n = len(res.layers)
        self.layer_slider.setRange(0, max(0, n - 1))
        self.layer_slider.setValue(n // 2)
        self.canvas.show_layer(res, n // 2)
        self._log("Sliced: " + res.summary() + f" | strategy {res.settings.strategy}"
                  + (f", {res.spiral_layers} spiral layers" if getattr(res, "spiral_layers", 0) else ""))
        self._show_plan(*out)
        self._planned_settings = snapshot
        self._check_narrow(res)

    def _check_narrow(self, res):
        """Parts of the model narrower than one line are not printed. Say so, and offer the
        widest line width that keeps them (not below 80 % of the nozzle)."""
        narrow = getattr(res, "narrow", None)
        if not narrow:
            return
        if res.settings.thin_mode == "centreline":
            filled = getattr(res, "thin_filled", [])
            if filled:
                self._log(f"Thin features: in {len(filled)} layers, parts narrower than the "
                          f"{res.settings.line_width:g} mm line are printed as single lines down their middle, "
                          f"as wide as the feature (min {res.settings.thin_min_width or 0.75 * res.settings.line_width:.2f} mm) "
                          "with less plastic [rule lines.thin_features].", append=True)
            return
        from slicer import suggest_line_width
        lw = res.settings.line_width
        nozzle = float(self.app.tool.get("nozzle_diameter", lw))
        worst = max(narrow, key=lambda n: n[3])
        zmin = res.bounds[0][2]
        text = (f"In {len(narrow)} of {len(res.layers)} layers, part of the model is narrower than the "
                f"{lw:g} mm line and will NOT be printed (worst: layer {worst[0] + 1}, "
                f"{worst[1] - zmin:.1f} mm up, {worst[3]:.0f} % of that layer).")
        w = suggest_line_width(res.narrow_worst_polys or [], lw, round(0.8 * nozzle, 2))
        self._log("Warning: " + text, append=True)
        if w is None:
            self.app.ui.warn("Thin features", text + f"\n\nThese features are too thin for the {nozzle:g} mm "
                                                     "nozzle even at its narrowest line. Thicken them in CAD.")
            return
        if self.app.ui.confirm("Thin features", text + f"\n\nA {w:g} mm line keeps them. "
                                                       f"Use {w:g} mm and slice again?"):
            self.v_line_width.set(w)
            self.do_slice()

    @staticmethod
    def _plan_compute(res, cfg):
        from planner import plan, analyze, dt_stats, extrusion_runs
        prog = plan(res, cfg)
        return prog, cfg, analyze(prog), dt_stats(prog), len(extrusion_runs(prog))

    def _save_cells(self, cells, rs):
        """Cells, their measurements, the chosen tactics and the rules in force, as JSON:
        the record an AI can learn from (add the print outcome to it afterwards)."""
        import json
        name = os.path.splitext(os.path.basename(self.model_path or "model"))[0]
        path = os.path.join(SCRIPTS_DIR, f"cells_{name}.json")
        try:
            with open(path, "w") as f:
                json.dump({"model": self.model_path, "rules": {k: r.value for k, r in rs.rules.items()},
                           "cells": cells, "outcome": None}, f, indent=1, default=str)
            return path
        except OSError as e:
            self._log(f"Could not write {path}: {e}", append=True)
            return None

    def _rules(self):
        from rules import RuleSet
        rs = RuleSet.load()
        if rs.load_error:
            self._log(f"Print rules not loaded ({rs.load_error}); using the defaults.", append=True)
        return rs

    def _show_plan(self, prog, cfg, st, dts, runs):
        self.program = prog
        mode = "polar (turntable coordinated)" if cfg.use_turntable else "cartesian (fixed plate)"
        lines = [f"Motion plan: {mode}",
                 f"  points: {len(prog.steps)}   est. time: {st.total_time / 60:.1f} min",
                 f"  extrusion paths: {runs}",
                 f"  arm avg {st.arm_avg_speed[0]:.1f} / peak {st.arm_peak_speed[0]:.1f} mm/s"]
        if cfg.use_turntable:
            lines.append(f"  turntable: {st.tt_travel / (2 * math.pi):.1f} turns, {st.tt_reversals} reversals")
        lines.append(f"  dt(ms): min {dts['dt_ms_min']} / avg {dts['dt_ms_avg']} / max {dts['dt_ms_max']}")
        if getattr(prog, "decisions", None) is not None:
            lines.append("Turntable decisions (print rules):")
            lines.append(prog.decisions.summary())
        stats = getattr(self.slice_result, "section_stats", None)
        if cfg.rules is not None and stats:
            from rules import classify_layers
            on = cfg.rules.get("nonplanar.enabled", False)
            lines.append("Non-planar bands (preview only, " + ("rules on" if on else "rules off: all layers flat")
                         + "; not yet generated):")
            lines.append(classify_layers(stats, cfg.rules)[1])
        feats = getattr(self.slice_result, "region_features", None)
        if cfg.rules is not None and feats:
            from rules import build_cells, cells_summary
            cells = build_cells(feats, cfg.rules)
            lines.append(f"Cells and tactics ({len(cells)} cells; in_use = done by the planner, "
                         "preview = what it would use):")
            lines.append(cells_summary(cells))
            path = self._save_cells(cells, cfg.rules)
            if path:
                lines.append(f"  cells written to {path} (for the AI layer)")
        self._log("\n".join(lines), append=True)
        self.v_status.set(f"Planned: {len(prog.steps):,} points, about {st.total_time / 60:.1f} min. Ready to print.")

    def do_plan(self):
        """Re-plan the current slice (synchronous; used after settings change and by tests)."""
        if not self.slice_result:
            return
        self._show_plan(*self._plan_compute(self.slice_result, self._planner_config()))
        self._planned_settings = self._settings_snapshot()

    def start_print(self):
        app = self.app
        if self.printing or app.printing or app.busy or app.model_job_active():
            what = ("This print" if self.printing else "The cylinder" if app.printing
                    else "Another print" if app.model_job_active() else app.job_state.get().title())
            QMessageBox.warning(self, "Print", f"{what} is still running. Wait for it to finish or press Stop.")
            return
        if not self.program:
            QMessageBox.warning(self, "Print", self.need_program_msg)
            return
        if not getattr(app, "hw_connected", False):
            QMessageBox.warning(self, "Print", "Hardware not connected (Machine ▸ Connections).")
            return
        cfg = self.program.config
        missing = []
        if app.extruder is None:
            missing.append("the extruder (it would print air)")
        if cfg.use_turntable and app.turntable is None:
            missing.append("the turntable (this plan rotates the part)")
        if missing:
            QMessageBox.warning(self, "Print", "Connect " + " and ".join(missing) + " first.")
            return
        if cfg.num_arms > 1:
            QMessageBox.warning(self, "Print",
                                "This plan is for 2 arms, but the print streamer only drives one arm so far. "
                                f"Set Arms to 1 in Machine + motion and {self.redo_verb.lower()} again.")
            return
        changed = self._changed_since_plan()
        if changed:
            if QMessageBox.question(self, "Settings changed",
                                    f"These settings changed since the last {self.redo_verb.lower()}: " +
                                    ", ".join(changed) + f".\n\n{self.redo_verb} again now? (No cancels the print.)"
                                    ) == QMessageBox.Yes:
                self.redo()
            return
        if not app.ui.confirm("Confirm print", "Stream the planned motion to the machines now?"):
            return
        self._launch(dry=False)

    def start_dry_run(self):
        if self.printing:
            return
        if not self.program:
            QMessageBox.warning(self, "Dry run", self.need_program_msg)
            return
        self._launch(dry=True)

    def _launch(self, dry):
        self.dry = dry
        self.printing = True
        self.stop_requested = False
        if not dry:
            self.app.job_name.set(self.v_model.get() or "Model")
            self.app.job_state.set("running")
        threading.Thread(target=self._run, kwargs=dict(dry=dry), daemon=True).start()

    def stop_print(self):
        self.stop_requested = True

    def _connected_arms(self):
        arms = []
        if getattr(self.app, "right", None) is not None:
            arms.append((self.app.right, 0))
        if getattr(self.app, "left", None) is not None:
            arms.append((self.app.left, 1))
        return arms

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
                raise RuntimeError(f"Plan uses {cfg.num_arms} arm(s) but only {len(active)} connected. "
                                   "Switch the arms on in Connections.")
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
            last_follow = 0.0
            if not dry:
                app.ui.post(lambda: self.preview_tabs.setCurrentWidget(self.sim))
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
                    now = time.monotonic()
                    if now - last_follow > 0.1:          # Simulation tab follows the print live
                        last_follow = now
                        app.ui.post(lambda k=si: self.sim.follow(k))
                        if step.layer >= 0 and hasattr(self, "layer_slider"):
                            app.ui.post(lambda L=step.layer: self.layer_slider.setValue(L))
                t += dt
                if si % 50 == 0:
                    frac = (si + 1) / max(1, len(prog.steps))
                    app.ui.post(lambda fr=frac, d=dry:
                                self.v_status.set(("Dry run " if d else "Printing ") + f"{fr * 100:.0f}%"))
                    if not dry:
                        app.ui.post(lambda tt=t: app.elapsed_time_var.set(f"{int(tt) // 60:02d}:{int(tt) % 60:02d}"))
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
            app.ui.post(lambda s=summary, r=list(panel_rows): self._show_debug(s, r))
            app.ui.post(lambda d=dry: self.v_status.set(
                "Dry run complete." if d else ("Stopped." if self.stop_requested else "Print complete.")))
        except Exception as e:
            if not dry and app.turntable is not None:
                try:
                    app.turntable.stop_rotation()
                except Exception:
                    pass
            app.ui.error("Motion error", str(e))
        finally:
            self.printing = False
            if not dry:
                app.ui.post(lambda: app.job_state.set("idle"))
                app.ui.post(self.sim.end_follow)

    def _move_arm(self, arm, at, speed, blend):
        # Each arm has its own mount orientation; use the one for the arm actually moving
        # (the plan carries the right arm's, but the left may be the one streaming).
        side = "left" if arm is getattr(self.app, "left", None) else "right"
        try:
            r, p, y = self.app.orientation(side)
            at = type(at)(**{**vars(at), "roll": r, "pitch": p, "yaw": y})
        except Exception:
            pass
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
                f"{stats['total_time_s'] / 60:.1f} min\n"
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
