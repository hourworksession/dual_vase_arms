"""Generators page: toolpaths from code (FullControl, colleagues' scripts, G-code).

Library on the left (generators/ plus any folders you add), the selected
generator's parameters as a form, then Generate → preview → Plan → Dry run /
Print. Planning and streaming are shared with Model print (ModelPrintPage), so a
generated toolpath goes through the same planner, checks and streamer as a
sliced model.
"""

import json
import os
import sys
import tempfile
import threading

from PySide6.QtCore import Qt, QProcess, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QSlider, QPlainTextEdit,
                               QFileDialog, QMessageBox, QTreeWidget, QTreeWidgetItem, QLineEdit,
                               QGridLayout, QSizePolicy, QHeaderView)

from .. import theme, gen_library
from .. import toolpath as tpmod
from ..state import DoubleVar, IntVar, BoolVar, StrVar
from ..widgets import (Card, NumberField, Check, Combo, Section, FormGrid, button, label, hrow, scroll, divider)
from .model_print import ModelPrintPage
from .toolpath_view import ToolpathView

RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gen_runner.py")


def _decimals(v):
    s = repr(float(v))
    return max(1, min(4, len(s.split(".")[1]) if "." in s else 1))


class GeneratorsPage(ModelPrintPage):
    def __init__(self, ctl, win):
        self.meta = None
        self.tp = None
        self.tp_errors = []
        self.proc = None
        self._values = {}          # path -> {param: value}, kept while the panel is open
        self._param_vars = {}
        self._gen_id = 0
        self._planned_id = None
        self.planning = False
        self.v_centre = BoolVar(True)
        self.v_timeout = DoubleVar(60.0)
        self.v_gen_status = StrVar("")
        super().__init__(ctl, win)
        self.need_program_msg = "Generate a toolpath first (pick a generator, press Generate)."
        self.redo_verb = "Generate"
        self.v_status.set("Pick a generator from the library.")
        for v in (self.v_centre, self.v_part_off_x, self.v_part_off_y):
            v.changed.connect(lambda *_: self._placement_changed())
        self.reload()

    # ================================================================ layout
    def _build(self):
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        split.addWidget(self._left())
        split.addWidget(self._right())
        split.setStretchFactor(1, 1)
        split.setSizes([400, 900])
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 14, 12, 14)
        v.addWidget(split)

    def _left(self):
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(4, 0, 12, 12)
        v.setSpacing(14)

        lib = Card("Library", "Scripts in the generators folder and any folders you add. "
                              "Listing them never runs them.")
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Search by name, author or folder")
        self.filter.textChanged.connect(self._apply_filter)
        lib.add(self.filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(170)
        self.tree.setStyleSheet(f"QTreeWidget {{ background:{theme.INPUT}; border:1px solid {theme.BORDER}; "
                                f"border-radius:8px; padding:4px; }} QTreeWidget::item {{ padding:4px; }} "
                                f"QTreeWidget::item:selected {{ background:{theme.ACCENT_DIM}; color:{theme.ACCENT}; }}")
        self.tree.currentItemChanged.connect(lambda cur, _: self._select(cur))
        lib.add(self.tree)
        g = QGridLayout()
        g.setSpacing(8)
        for i, (txt, fn, tip) in enumerate((
                ("Open file…", self.open_file, "Run a generator or G-code file from anywhere"),
                ("Add folder…", self.add_folder, "Add a folder (e.g. George's repo) to the library"),
                ("Reload", self.reload, "Re-read the library after editing scripts"),
                ("Show folder", self.show_folder, "Open the generators folder"))):
            b = button(txt, "ghost", fn, tip=tip)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            g.addWidget(b, i // 2, i % 2)
        lib.add_layout(g)
        v.addWidget(lib)

        self.gen_card = Card("No generator selected")
        self.desc = label("", "CardHint", wrap=True)
        self.gen_card.add(self.desc)
        self.where = label("", "Muted", wrap=True)
        self.where.setStyleSheet(f"font-family:{theme.FONT_MONO}; font-size:11px;")
        self.where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.gen_card.add(self.where)
        self.form_host = QWidget()
        self.form_lay = QVBoxLayout(self.form_host)
        self.form_lay.setContentsMargins(0, 4, 0, 0)
        self.gen_card.add(self.form_host)
        self.gen_btn = button("Generate", "primary", self.generate)
        self.gen_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cancel_btn = button("Cancel", "ghost", self.cancel)
        self.cancel_btn.setEnabled(False)
        self.reset_btn = button("Defaults", "ghost", self._reset_params, tip="Reset the parameters")
        self.gen_card.add(hrow(self.gen_btn, self.cancel_btn, self.reset_btn))
        st = label("", "CardHint", wrap=True)
        self.v_gen_status.changed.connect(st.setText)
        self.gen_card.add(st)
        v.addWidget(self.gen_card)

        box = QWidget()
        bv = QVBoxLayout(box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(2)
        pl = Section("Placement")
        pl.full(Check("Centre the part on the turntable axis", self.v_centre))
        pl.row("Part offset X", NumberField(self.v_part_off_x, "mm", 1, 1))
        pl.row("Part offset Y", NumberField(self.v_part_off_y, "mm", 1, 1))
        bv.addWidget(pl); bv.addWidget(divider())
        de = Section("Defaults", expanded=False)
        de.full(label("Used when the code does not say: line width and layer height for plain points, "
                      "layer height to turn G-code E values into widths.", "CardHint", wrap=True))
        de.row("Line width", NumberField(self.v_line_width, "mm", 2, 0.05, minimum=0.05))
        de.row("Layer height", NumberField(self.v_layer_height, "mm", 2, 0.05, minimum=0.01))
        de.row("Flow", NumberField(self.v_flow, "%", 0, 5, minimum=0))
        de.row("First layer flow", NumberField(self.v_first_layer_flow, "%", 0, 5, minimum=0))
        de.row("Time limit", NumberField(self.v_timeout, "s", 0, 10, minimum=5, maximum=3600),
               "Stop a generator that runs longer than this")
        bv.addWidget(de); bv.addWidget(divider())
        bv.addWidget(self._machine_section()); bv.addWidget(divider())
        bv.addWidget(self._calibration_section()); bv.addWidget(divider())
        bv.addWidget(Check("Write debug log while printing", self.v_debug))
        v.addWidget(box)
        v.addStretch(1)
        sa = scroll(inner)
        sa.setMinimumWidth(340)
        return sa

    def _right(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 0, 0, 0)
        v.setSpacing(0)
        vs = QSplitter(Qt.Vertical)
        vs.setHandleWidth(12)

        prev = Card("Preview")
        self.view = ToolpathView()
        prev.add(self.view, 1)
        self.order = QSlider(Qt.Horizontal)
        self.order.setRange(0, 1000)
        self.order.setValue(1000)
        self.order.valueChanged.connect(self._order_changed)
        trav = Check("Travels", BoolVar(True))
        trav.toggled.connect(lambda on: (setattr(self.view, "show_travel", on), self.view.update()))
        self.order_lbl = label("All", "Muted")
        self.order_lbl.setMinimumWidth(44)
        prev.add(hrow(label("Print order", "Muted"), self.order, self.order_lbl, trav, spacing=10))
        self.stats_lbl = label("", "CardHint", wrap=True)
        prev.add(self.stats_lbl)
        vs.addWidget(prev)

        run = Card("Run")
        g = QGridLayout()
        g.setSpacing(8)
        for c in range(4):
            g.setColumnStretch(c, 1)
        self.plan_btn = button("Plan", None, self.do_plan, tip="Run the motion planner (also done after Generate)")
        self.dry_btn = button("Dry run", "ghost", self.start_dry_run,
                              tip="Walk the plan without hardware and write motion_debug.csv")
        self.print_btn = button("▶  Print", "primary", self.start_print)
        self.stop_btn = button("Stop", "danger", self.stop_print)
        for i, b in enumerate((self.plan_btn, self.dry_btn, self.print_btn, self.stop_btn)):
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            g.addWidget(b, 0, i)
        run.add_layout(g)
        st = label(self.v_status.get(), "CardHint", wrap=True)
        self.v_status.changed.connect(st.setText)
        run.add(st)
        self.stats_text = QPlainTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        run.add(self.stats_text, 1)
        vs.addWidget(run)
        vs.setSizes([620, 300])
        v.addWidget(vs)
        self._update_buttons()
        return w

    # ================================================================ library
    def reload(self):
        keep = self.meta["path"] if self.meta else None
        self.tree.clear()
        self._items = []
        select = None
        for root, metas in gen_library.scan():
            top_name = "Library" if root == gen_library.LIBRARY_ROOT else os.path.basename(root.rstrip(os.sep))
            top = QTreeWidgetItem([top_name])
            top.setFlags(top.flags() & ~Qt.ItemIsSelectable)
            self.tree.addTopLevelItem(top)
            groups = {}
            for m in metas:
                parent = top
                if m["folder"]:
                    if m["folder"] not in groups:
                        gi = QTreeWidgetItem([m["folder"]])
                        gi.setFlags(gi.flags() & ~Qt.ItemIsSelectable)
                        top.addChild(gi)
                        groups[m["folder"]] = gi
                    parent = groups[m["folder"]]
                it = QTreeWidgetItem([m["name"] + ("   ⚠" if m["error"] else "")])
                it.setData(0, Qt.UserRole, m)
                it.setToolTip(0, m["error"] or m["description"] or m["path"])
                parent.addChild(it)
                self._items.append(it)
                if m["path"] == keep:
                    select = it
            top.setExpanded(True)
            for gi in groups.values():
                gi.setExpanded(True)
        if not self._items:
            self.tree.addTopLevelItem(QTreeWidgetItem(["(no generators found)"]))
        if select is not None:
            self.tree.setCurrentItem(select)
        self._apply_filter(self.filter.text())

    def _apply_filter(self, text):
        t = text.lower().strip()
        for it in self._items:
            m = it.data(0, Qt.UserRole)
            hay = " ".join((m["name"], m["author"], m["folder"], m["description"])).lower()
            it.setHidden(bool(t) and t not in hay)

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open generator", gen_library.LIBRARY_ROOT,
                                              "Generators (*.py *.gcode *.gco *.nc);;All files (*)")
        if path:
            self.load_meta(gen_library.read_meta(path))

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Add a folder of generators")
        if path:
            gen_library.add_folder(path)
            self.reload()

    def show_folder(self):
        os.makedirs(gen_library.LIBRARY_ROOT, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(gen_library.LIBRARY_ROOT))

    def _select(self, item):
        if item is None:
            return
        m = item.data(0, Qt.UserRole)
        if m:
            self.load_meta(m)

    def load_meta(self, m):
        if self.proc is not None:
            return
        self._store_values()
        self.meta = m
        self.gen_card.title_lbl.setText(m["name"])
        who = f"by {m['author']} · " if m["author"] else ""
        style = {"generate": "generate()", "script": "FullControl script", "build_steps": "build_steps()",
                 "gcode": "G-code"}.get(m["style"], "?")
        self.desc.setText(m["error"] or m["description"] or "No description.")
        self.desc.setStyleSheet(f"color:{theme.WARN};" if m["error"] else "")
        self.where.setText(f"{who}{style}\n{m['path']}")
        self._build_form(m)
        self.v_model.set(m["name"])
        self.v_gen_status.set("")
        self._update_buttons()

    def _build_form(self, m):
        while self.form_lay.count():
            w = self.form_lay.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)        # gone now, not at the next event-loop turn
                w.deleteLater()
        self._param_vars = {}
        if not m["params"]:
            if m["style"] in ("generate", "script", "build_steps"):
                self.form_lay.addWidget(label("No parameters.", "Muted"))
            return
        saved = self._values.get(m["path"], {})
        host = QWidget()
        fg = FormGrid()
        host.setLayout(fg)
        fg.setContentsMargins(0, 0, 0, 0)
        for p in m["params"]:
            val = saved.get(p["name"], p["default"])
            k = p["kind"]
            lab = p["label"]
            if k == "bool":
                var = BoolVar(bool(val))
                wdg = Check("", var)
            elif k == "choice":
                var = StrVar(str(val))
                var._choices = {str(c): c for c in p["choices"]}
                wdg = Combo(var, [str(c) for c in p["choices"]], width=150)
            elif k == "str":
                var = StrVar(str(val))
                wdg = QLineEdit(str(val))
                wdg.textChanged.connect(var.set)
            elif k == "int":
                var = IntVar(int(val))
                wdg = NumberField(var, p["unit"], 0, p["step"] or 1, integer=True,
                                  minimum=p["min"] if p["min"] is not None else -10 ** 6,
                                  maximum=p["max"] if p["max"] is not None else 10 ** 6, width=130)
            else:
                var = DoubleVar(float(val))
                wdg = NumberField(var, p["unit"], _decimals(p["default"]), p["step"] or 0.1,
                                  minimum=p["min"] if p["min"] is not None else -1e6,
                                  maximum=p["max"] if p["max"] is not None else 1e6, width=130)
            self._param_vars[p["name"]] = (var, p)
            fg.row(lab, wdg, p["help"] or None)
        self.form_lay.addWidget(host)

    def _store_values(self):
        if self.meta and self._param_vars:
            self._values[self.meta["path"]] = {n: v.get() for n, (v, _) in self._param_vars.items()}

    def _reset_params(self):
        if not self.meta:
            return
        for n, (v, p) in self._param_vars.items():
            v.set(p["default"] if p["kind"] != "choice" else str(p["default"]))

    def _param_values(self):
        out = {}
        for n, (v, p) in self._param_vars.items():
            val = v.get()
            if p["kind"] == "choice":
                val = getattr(v, "_choices", {}).get(val, val)
            out[n] = val
        return out

    # ================================================================ generate
    def generate(self):
        m = self.meta
        if m is None:
            QMessageBox.information(self, "Generate", "Pick a generator from the library first.")
            return
        if m["error"]:
            QMessageBox.warning(self, "Generate", m["error"])
            return
        if self.proc is not None or self.printing:
            return
        self._store_values()
        vals = self._param_values()
        job = {"defaults": {"line_width": self.v_line_width.get(), "layer_height": self.v_layer_height.get(),
                            "filament_diameter": self.v_filament.get()}}
        if m["style"] == "script":
            job["constants"] = vals
        else:
            job["params"] = vals
        tmp = tempfile.mkdtemp(prefix="cellgen_")
        self._job = os.path.join(tmp, "job.json")
        self._out = os.path.join(tmp, "result.json")
        with open(self._job, "w") as f:
            json.dump(job, f)
        self._gen_id += 1
        self.program = None
        self._gen_snapshot = (m["path"], json.dumps(vals, sort_keys=True, default=str))
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(os.path.dirname(os.path.abspath(m["path"])))
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._proc_error)
        self._timed_out = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)
        self._timer.start(int(self.v_timeout.get() * 1000))
        self.v_gen_status.set("Running…")
        self.v_status.set("Generating…")
        self._update_buttons()
        self.proc.start(sys.executable, [RUNNER, m["path"], self._job, self._out])
        self.proc.closeWriteChannel()        # input() gets EOF instead of hanging

    def _timeout(self):
        if self.proc is not None:
            self._timed_out = True
            self.proc.kill()

    def cancel(self):
        if self.proc is not None:
            self._cancelled = True
            self.proc.kill()

    def _proc_error(self, err):
        if err == QProcess.FailedToStart and self.proc is not None:
            self.v_gen_status.set("Could not start Python for the generator.")
            self.proc = None
            self._update_buttons()

    def _finished(self, code, status):
        self._timer.stop()
        raw = bytes(self.proc.readAll()).decode(errors="replace") if self.proc else ""
        self.proc = None
        cancelled = getattr(self, "_cancelled", False)
        self._cancelled = False
        result = None
        try:
            with open(self._out) as f:
                result = json.load(f)
        except Exception:
            pass
        if self._timed_out or cancelled or result is None:
            if self._timed_out:
                msg = (f"Stopped after {self.v_timeout.get():.0f} s (time limit in Defaults). The generator may "
                       "be stuck in a loop, or need a longer limit.")
            elif cancelled:
                msg = "Cancelled."
            else:
                msg = f"The generator process ended without a result (exit code {code})."
            self._fail(msg, raw)
            return
        if not result.get("ok"):
            self._fail(result.get("error", "Unknown error"), result.get("output", "") +
                       ("\n" + result["traceback"] if result.get("traceback") else ""))
            return
        self.tp = {k: result[k] for k in ("paths", "source", "notes")}
        out = result.get("output", "").strip()
        self._log(f"Generated {self.meta['name']} ({self.tp['source']})" +
                  (f"\n\nOutput from the script:\n{out}" if out else ""))
        for n in self.tp.get("notes", []):
            self._log("Note: " + n, append=True)
        self._check_and_show(plan=True)

    def _fail(self, msg, detail=""):
        self.tp = None
        self.program = None
        self.view.set_message("Generation failed. See the report below.")
        self.v_gen_status.set(msg.splitlines()[0][:160])
        self.v_status.set("No toolpath.")
        self._log("Generation failed:\n" + msg + ("\n\n" + detail.strip() if detail.strip() else ""))
        self.stats_lbl.setText("")
        self._update_buttons()

    # ================================================================ checks, preview, plan
    def _shift(self):
        return tpmod.placement_shift(self.tp, self.v_centre.get()) if self.tp else (0.0, 0.0)

    def _offset(self):
        return (self.v_part_off_x.get(), self.v_part_off_y.get())

    def _placement_changed(self):
        if self.tp is not None and self.proc is None:
            self._check_and_show(plan=False)

    def _check_and_show(self, plan):
        errors, warnings, st = tpmod.validate(self.tp, self._shift(), self._offset(), self.v_filament.get(),
                                              first_layer_h=self.v_layer_height.get())
        self.tp_errors = errors
        self.tp_stats = st
        self.view.set_toolpath(self.tp, self._shift(), self._offset())
        if "size" in st:
            sx, sy, sz = st["size"]
            self.stats_lbl.setText(
                f"{st['paths']} path{'s' if st['paths'] != 1 else ''} · {st['points']:,} points · {sx:.1f} × {sy:.1f} × {sz:.1f} mm · "
                f"reaches {st['rmax']:.0f} mm from the axis · {st['length_m']:.1f} m of line · "
                f"≈ {st['filament_m']:.2f} m filament ({st['grams']:.0f} g PLA) · "
                f"width {st['width'][0]:.2f}–{st['width'][1]:.2f} mm")
        if warnings:
            self._log("Check:\n• " + "\n• ".join(warnings), append=True)
        if errors:
            self._log("Cannot plan:\n• " + "\n• ".join(errors), append=True)
            self.v_gen_status.set(errors[0])
            self.v_status.set("Fix the problems in the report before planning.")
            self.program = None
            self._update_buttons()
            return
        self.v_gen_status.set("Toolpath ready." + (f"  {len(warnings)} warning(s) in the report." if warnings else ""))
        self._update_buttons()
        if plan:
            self.do_plan()
        else:
            self.program = None
            self.v_status.set("Placement changed: press Plan.")
            self._update_buttons()

    def do_plan(self):
        if self.tp is None or self.tp_errors or self.planning:
            return
        try:
            from planner import PlannerConfig  # noqa: F401  (fail early with a clear message)
        except Exception as e:
            QMessageBox.critical(self, "Planner", f"Planner unavailable:\n{e}")
            return
        cfg = self._planner_config()
        zmin = self.tp_stats.get("zmin", 0.0)
        h0 = self.tp_stats.get("height", (cfg.layer_height,))[0]
        cfg.first_layer_z = zmin + 0.5 * h0
        slc = tpmod.to_slice_result(self.tp, self._shift())
        self.planning = True
        self.program = None
        self.v_status.set("Planning…")
        self._update_buttons()
        gen_id = self._gen_id

        def work():
            from planner import plan, analyze, dt_stats, extrusion_runs
            try:
                prog = plan(slc, cfg)
                st = analyze(prog)
                dts = dt_stats(prog)
                runs = len(extrusion_runs(prog))
                self.app.ui.post(lambda: self._planned(gen_id, prog, cfg, st, dts, runs, None))
            except Exception as e:
                self.app.ui.post(lambda e=e: self._planned(gen_id, None, cfg, None, None, 0, e))
        threading.Thread(target=work, daemon=True).start()

    def _planned(self, gen_id, prog, cfg, st, dts, runs, err):
        self.planning = False
        if err is not None:
            self.v_status.set("Planning failed.")
            self._log(f"Planning failed: {type(err).__name__}: {err}", append=True)
            self._update_buttons()
            return
        if gen_id != self._gen_id:
            self._update_buttons()
            return
        self.program = prog
        mode = "polar (turntable coordinated)" if cfg.use_turntable else "cartesian (fixed plate)"
        lines = [f"Motion plan: {mode}, {cfg.num_arms} arm(s)",
                 f"  points: {len(prog.steps)}   est. time: {st.total_time / 60:.1f} min",
                 f"  extrusion runs: {runs}",
                 f"  arm avg {st.arm_avg_speed[0]:.1f} / peak {st.arm_peak_speed[0]:.1f} mm/s"]
        if cfg.use_turntable:
            lines.append(f"  turntable: {st.tt_travel / (2 * 3.141592653589793):.1f} turns, {st.tt_reversals} reversals")
        lines.append(f"  dt(ms): min {dts['dt_ms_min']} / avg {dts['dt_ms_avg']} / max {dts['dt_ms_max']}")
        self._log("\n".join(lines), append=True)
        self._planned_settings = self._settings_snapshot()
        self.v_status.set(f"Planned: {len(prog.steps):,} points, about {st.total_time / 60:.1f} min. "
                          "Dry run or Print.")
        self._update_buttons()

    # ================================================================ shared-with-model-print hooks
    def redo(self):
        self.generate()

    def _settings_snapshot(self):
        snap = super()._settings_snapshot()
        snap.pop("v_timeout", None)
        snap.pop("v_gen_status", None)
        snap["v_generator_parameters"] = json.dumps(self._param_values(), sort_keys=True, default=str)
        snap["v_generator_file"] = self.meta["path"] if self.meta else None
        return snap

    def _launch(self, dry):
        super()._launch(dry)
        self._update_buttons()

    def _run(self, dry=False):
        try:
            super()._run(dry)
        finally:
            self.app.ui.post(self._update_buttons)

    def _order_changed(self, v):
        self.view.progress = v / 1000
        self.order_lbl.setText("All" if v == 1000 else f"{v / 10:.0f} %")
        self.view.update()

    def _update_buttons(self):
        if not hasattr(self, "print_btn"):
            return
        busy = self.proc is not None
        self.gen_btn.setEnabled(not busy and not self.printing and self.meta is not None)
        self.cancel_btn.setEnabled(busy)
        self.plan_btn.setEnabled(not busy and not self.planning and self.tp is not None and not self.tp_errors
                                 and not self.printing)
        ready = self.program is not None and not busy and not self.planning
        self.dry_btn.setEnabled(ready and not self.printing)
        self.print_btn.setEnabled(ready and not self.printing)
        self.stop_btn.setEnabled(self.printing)
