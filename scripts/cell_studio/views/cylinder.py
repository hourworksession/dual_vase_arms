"""Cylinder page: parameters on the left, preview and run controls on the right."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QFileDialog, QMessageBox,
                               QFrame, QSplitter, QSizePolicy)

from .. import theme
from ..controller import PARAM_META
from ..widgets import (Card, NumberField, Check, Combo, Section, ReadoutField,
                       button, label, hrow, scroll, divider, Segmented)
from .polar_view import PolarView


class CylinderPage(QWidget):
    def __init__(self, ctl, win):
        super().__init__()
        self.c, self.win = ctl, win
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        split.addWidget(self._settings())
        split.addWidget(self._right())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([450, 900])
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 14, 12, 14)
        v.addWidget(split)

    # ------------------------------------------------------------------
    def _pfield(self, sec, key, nudges=(), width=130):
        lab, unit, dec, step, _s, tip, live, minimum = PARAM_META[key]
        f = NumberField(self.c.param_vars[key], unit, dec, step, nudges=nudges, width=width,
                        minimum=-1e6 if minimum is None else minimum)
        if live:
            lab += "  ⟳"
        else:
            self.locked.append(f)          # read once at start: locked while a job runs
        sec.row(lab, f, tip)
        return f

    def _settings(self):
        c = self.c
        self.locked = []
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(4, 0, 12, 12)
        v.setSpacing(2)
        title = label("Cylinder parameters", "CardTitle")
        v.addWidget(title)
        v.addWidget(label("⟳ marks values read live, which you can adjust while the cylinder prints. "
                          "The rest are read at start and locked during a job.", "CardHint", wrap=True))
        v.addSpacing(6)

        geo = Section("Geometry")
        arms = Segmented(c.cyl_arms, [("both", "Both"), ("left", "Left only"), ("right", "Right only")])
        geo.row("Arms", arms, "Which arm(s) print this cylinder; the other stays where it is")
        self.locked.append(arms)
        for k in ('radius', 'z_start', 'pitch', 'total_revs', 'flat_revs', 'line_width'):
            self._pfield(geo, k)
        v.addWidget(geo); v.addWidget(divider())

        ext = Section("Extrusion")
        for k in ('filament_diameter', 'feed_rate_left', 'feed_rate_right',
                  'extrusion_factor_left', 'extrusion_factor_right'):
            self._pfield(ext, k)

        mode = Segmented(c.extrusion_mode, [("single", "One move"), ("streamed", "Follow turntable")])
        ext.row("Extrusion", mode,
                "One move (original): the whole wall's filament is sent as one G1 at start, at the feed "
                "rates above. Pause, Stop and speed changes cannot reach it.\n\n"
                "Follow turntable: filament is sent 10° at a time at the rate the turntable is actually "
                "turning, so Pause, Stop and speed changes stop or follow the flow. Not yet proven on the cell.")
        self.locked.append(mode)
        ext.row("Match to turntable", button("Set feed rates", "ghost", self._match,
                                             tip="Feed rates so the extrusion lasts exactly as long as the rotation"))
        v.addWidget(ext); v.addWidget(divider())

        pl = Section("Placement")
        self._pfield(pl, 'start_angle_deg')
        self._pfield(pl, 'angular_offset_deg')
        self._pfield(pl, 'radial_offset_left', nudges=(-1, -0.1, 0.1, 1), width=92)
        self._pfield(pl, 'radial_offset_right', nudges=(-1, -0.1, 0.1, 1), width=92)
        v.addWidget(pl); v.addWidget(divider())

        mo = Section("Motion")
        mo.row("Base arm speed  ⟳", NumberField(c.base_arm_speed_var, "mm/s", 1, 1.0, minimum=1,
                                                nudges=(-10, -1, 1, 10), width=92))
        mo.row("Turntable speed  ⟳", NumberField(c.turntable_speed_var, "rad/s", 3, 0.01,
                                                 nudges=(-0.1, -0.01, 0.01, 0.1), width=96,
                                                 minimum=0.0, maximum=c.tt_speed_max,
                                                 clamp=(0.0, c.tt_speed_max)),
               "Live: the turntable follows this value (also the slider in the Live panel). "
               "In One move extrusion mode the flow does not.")
        mo.row("From surface speed", hrow(NumberField(c.calc_mms_var, "mm/s", 1, 1.0, width=100),
                                          button("→ rad/s", "ghost", c.calc_rads_from_mms,
                                                 tip="Sets turntable speed = surface speed / radius"),
                                          None))
        v.addWidget(mo); v.addWidget(divider())

        wv = Section("Wave pattern")
        wv.full(Check("Enable wrapped wave", c.pattern_enabled))
        wv.row("Waveform", Combo(c.pattern_waveform, ['sine', 'triangle', 'square'], width=130))
        wv.row("Amplitude  ⟳", NumberField(c.pattern_amplitude, "mm", 2, 0.1, nudges=(-1, -0.1, 0.1, 1), width=92))
        wv.row("Wave count  ⟳", NumberField(c.pattern_wave_count, "/rev", 2, 0.1, nudges=(-1, -0.1, 0.1, 1), width=92))
        wv.row("Phase offset  ⟳", NumberField(c.pattern_phase_offset, "°", 1, 1.0, nudges=(-45, -5, 5, 45), width=92))
        wv.row("Apply to", hrow(Check("Left", c.pattern_arm_left), Check("Right", c.pattern_arm_right), None))
        v.addWidget(wv); v.addWidget(divider())

        cen = Section("Turntable centre (calibration)", expanded=False)
        for k in ('tt_cx_left', 'tt_cy_left', 'tt_cz_left', 'tt_cx_right', 'tt_cy_right', 'tt_cz_right'):
            self._pfield(cen, k)
        v.addWidget(cen)
        v.addStretch(1)

        sa = scroll(inner)
        sa.setMinimumWidth(360)
        return sa

    def _match(self):
        self.c.calculate_extrusion_lengths()
        self.c.match_feed_rates()

    # ------------------------------------------------------------------
    def _right(self):
        c = self.c
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 0, 0, 0)
        v.setSpacing(16)

        prev = Card("Toolpath preview")
        self.view = PolarView(c)
        self.sim_btn = button("▶  Simulate", None, self._toggle_sim)
        self.view.sim_state.connect(lambda on: self.sim_btn.setText("■  Stop" if on else "▶  Simulate"))
        prev.add(self.view, 1)
        prev.add(hrow(self.sim_btn,
                      button("Clear", "ghost", self.view.clear_sim, tip="Return to the node editor"),
                      button("Reset nodes", "ghost", c.reset_nodes), None))
        prev.add(hrow(label("Presets", "Muted"), button("Save config…", "ghost", self._save),
                      button("Load config…", "ghost", self._load), None))
        v.addWidget(prev, 1)

        run = Card("Run")
        # A grid whose cells stretch, so nothing overlaps however narrow the window gets
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(10)
        for col in range(4):
            g.setColumnStretch(col, 1)
        self.start_btn = button("▶  Start cylinder", "primary", c.start_cylinder,
                                tip="Needs both arms, the turntable and the extruder connected")
        self.pause_btn = button("Pause", None, c.toggle_pause)
        self.stop_btn = button("Stop", "danger", c.stop_print)
        for b in (self.start_btn, self.pause_btn, self.stop_btn):
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        g.addWidget(self.start_btn, 0, 0, 1, 2)
        g.addWidget(self.pause_btn, 0, 2)
        g.addWidget(self.stop_btn, 0, 3)
        fl = ReadoutField(c.calc_left_len, "{:.1f}", "mm")
        fr = ReadoutField(c.calc_right_len, "{:.1f}", "mm")
        for f in (fl, fr):
            f.setMinimumWidth(80)
            f.setMaximumWidth(16777215)
            f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        g.addWidget(hrow(label("Filament L", "Muted"), fl, spacing=8), 1, 0, 1, 2)
        g.addWidget(hrow(label("R", "Muted"), fr, spacing=8), 1, 2, 1, 2)
        recalc = button("Recalculate", "ghost", c.calculate_extrusion_lengths)
        off = button("Extruders off", "ghost", c.extruders_off, tip="Sends CANCEL_PRINT to Klipper")
        for b in (recalc, off):
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        g.addWidget(recalc, 2, 0, 1, 2)
        g.addWidget(off, 2, 2, 1, 2)
        run.add_layout(g)
        v.addWidget(run)

        c.job_state.changed.connect(self._state)
        self._state(c.job_state.get())
        return w

    def _state(self, s):
        running = s in ("running", "paused")
        for w in self.locked:
            w.setEnabled(not running)
        self.start_btn.setEnabled(s == "idle")
        self.pause_btn.setEnabled(running)
        self.stop_btn.setEnabled(running)
        self.pause_btn.setText("Resume" if s == "paused" else "Pause")

    def _toggle_sim(self):
        if self.view.sim_running:
            self.view.stop_simulation()
        elif not self.view.start_simulation():
            QMessageBox.warning(self, "Simulation", "Total revolutions must be greater than 0 to simulate.")

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save cylinder config", "", "JSON files (*.json)")
        if path:
            if not path.lower().endswith(".json"):
                path += ".json"
            self.c.save_config_to(path)

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load cylinder config", "", "JSON files (*.json);;All files (*)")
        if path:
            try:
                self.c.load_config_from(path)
            except Exception as e:
                QMessageBox.critical(self, "Load error", str(e))
