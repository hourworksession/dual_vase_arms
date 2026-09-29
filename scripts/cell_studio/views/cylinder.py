"""Cylinder page: parameters on the left, preview and run controls on the right."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QFileDialog, QMessageBox,
                               QFrame, QSplitter)

from .. import theme
from ..controller import PARAM_META
from ..widgets import (Card, NumberField, Check, Combo, Section, ReadoutField,
                       button, label, hrow, scroll, divider)
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
        split.setSizes([470, 900])
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 18)
        v.addWidget(split)

    # ------------------------------------------------------------------
    def _pfield(self, sec, key, nudges=(), width=140):
        lab, unit, dec, step, _s, tip = PARAM_META[key]
        sec.row(lab, NumberField(self.c.param_vars[key], unit, dec, step, nudges=nudges, width=width), tip)

    def _settings(self):
        c = self.c
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(4, 0, 12, 12)
        v.setSpacing(2)
        title = label("Cylinder parameters", "CardTitle")
        v.addWidget(title)
        v.addWidget(label("Radial offsets, centre Z, turntable speed, base arm speed and the wave pattern "
                          "are read live, so they can be adjusted while the cylinder prints.", "CardHint", wrap=True))
        v.addSpacing(6)

        geo = Section("Geometry")
        for k in ('radius', 'z_start', 'pitch', 'total_revs', 'line_width'):
            self._pfield(geo, k)
        v.addWidget(geo); v.addWidget(divider())

        ext = Section("Extrusion")
        for k in ('filament_diameter', 'feed_rate_left', 'feed_rate_right',
                  'extrusion_factor_left', 'extrusion_factor_right'):
            self._pfield(ext, k)
        v.addWidget(ext); v.addWidget(divider())

        pl = Section("Placement")
        self._pfield(pl, 'start_angle_deg')
        self._pfield(pl, 'angular_offset_deg')
        self._pfield(pl, 'radial_offset_left', nudges=(-1, -0.1, 0.1, 1), width=110)
        self._pfield(pl, 'radial_offset_right', nudges=(-1, -0.1, 0.1, 1), width=110)
        v.addWidget(pl); v.addWidget(divider())

        mo = Section("Motion")
        mo.row("Base arm speed", NumberField(c.base_arm_speed_var, "mm/s", 1, 1.0,
                                             nudges=(-10, -1, 1, 10), width=100))
        mo.row("Turntable speed", NumberField(c.turntable_speed_var, "rad/s", 3, 0.01,
                                              nudges=(-0.1, -0.01, 0.01, 0.1), width=100,
                                              clamp=(0.0, c.tt_speed_max)),
               "Live: the print follows this value. Also on the slider in the Live panel.")
        mo.row("From surface speed", hrow(NumberField(c.calc_mms_var, "mm/s", 1, 1.0, width=100),
                                          button("→ rad/s", "ghost", c.calc_rads_from_mms,
                                                 tip="Sets turntable speed = surface speed / radius"),
                                          None))
        v.addWidget(mo); v.addWidget(divider())

        wv = Section("Wave pattern")
        wv.full(Check("Enable wrapped wave", c.pattern_enabled))
        wv.row("Waveform", Combo(c.pattern_waveform, ['sine', 'triangle', 'square'], width=130))
        wv.row("Amplitude", NumberField(c.pattern_amplitude, "mm", 2, 0.1, nudges=(-1, -0.1, 0.1, 1), width=100))
        wv.row("Wave count", NumberField(c.pattern_wave_count, "/rev", 2, 0.1, nudges=(-1, -0.1, 0.1, 1), width=100))
        wv.row("Phase offset", NumberField(c.pattern_phase_offset, "°", 1, 1.0, nudges=(-45, -5, 5, 45), width=100))
        wv.row("Apply to", hrow(Check("Left", c.pattern_arm_left), Check("Right", c.pattern_arm_right), None))
        v.addWidget(wv); v.addWidget(divider())

        cen = Section("Turntable centre (calibration)", expanded=False)
        for k in ('tt_cx_left', 'tt_cy_left', 'tt_cz_left', 'tt_cx_right', 'tt_cy_right', 'tt_cz_right'):
            self._pfield(cen, k)
        v.addWidget(cen)
        v.addStretch(1)

        sa = scroll(inner)
        sa.setMinimumWidth(470)
        return sa

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
        self.start_btn = button("▶  Start cylinder", "primary", c.start_cylinder, min_w=190,
                                tip="Needs both arms, the turntable and the extruder connected")
        self.pause_btn = button("Pause", None, c.toggle_pause, min_w=110)
        self.stop_btn = button("Stop", "danger", c.stop_print, min_w=90)
        run.add(hrow(self.start_btn, self.pause_btn, self.stop_btn, None))
        run.add(hrow(label("Filament  L", "Muted"), ReadoutField(c.calc_left_len, "{:.1f}", "mm", width=110),
                     label("R", "Muted"), ReadoutField(c.calc_right_len, "{:.1f}", "mm", width=110),
                     button("Recalculate", "ghost", c.calculate_extrusion_lengths), None))
        run.add(hrow(None, button("Extruders off", "ghost", c.extruders_off)))
        v.addWidget(run)

        c.job_state.changed.connect(self._state)
        self._state(c.job_state.get())
        return w

    def _state(self, s):
        running = s in ("running", "paused")
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
