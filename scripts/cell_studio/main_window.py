"""Main window: header with E-stop, navigation, pages, live panel and log console."""

import logging

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction, QKeySequence, QFont
from PySide6.QtWidgets import (QLabel, QMainWindow, QWidget, QFrame, QHBoxLayout, QVBoxLayout, QStackedWidget,
                               QPushButton, QToolButton, QMenu, QMessageBox, QSlider, QPlainTextEdit,
                               QButtonGroup, QSizePolicy)

from . import theme
from .controller import CellController
from .state import UiBridge
from .widgets import Chip, Tile, button, label, hrow, divider, NumberField
from .views.machine import MachinePage
from .views.cylinder import CylinderPage
from .views.model_print import ModelPrintPage
from .views.generators import GeneratorsPage
from .views.macros_page import MacrosPage
from .views.tool_dialog import ToolDialog
from .views.home_dialog import HomeDialog

JOB_COLOURS = {"idle": theme.FAINT, "running": theme.OK, "paused": theme.WARN,
               "homing": theme.ACCENT, "preparing": theme.ACCENT}


class _QtLogHandler(logging.Handler):
    def __init__(self, ui, sink):
        super().__init__()
        self.ui, self.sink = ui, sink
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            msg = self.format(record)
            self.ui.post(lambda m=msg: self.sink.appendPlainText(m))
        except Exception:
            pass


class MainWindow(QMainWindow):
    PAGES = ("Machine", "Cylinder", "Model print", "Generators", "Macros")

    def __init__(self, progress=None):
        """progress(text): optional callback, called before each slow part (start-up window)."""
        super().__init__()
        step = progress or (lambda text: None)
        self.setWindowTitle("Cell Studio · dual arm print control")
        self.resize(1480, 920)
        self.setMinimumSize(1180, 640)
        self.ui = UiBridge()
        self.ui.parent_widget = self
        step("Reading the cell configuration")
        self.c = CellController(self.ui)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.stack = QStackedWidget()
        step("Building the Machine page")
        self.machine = MachinePage(self.c, self)
        step("Building the Cylinder page")
        self.cylinder = CylinderPage(self.c, self)
        step("Building the Model print page")
        self.model = ModelPrintPage(self.c, self)
        step("Building the Generators page (scanning the generator library)")
        self.generators = GeneratorsPage(self.c, self)
        step("Building the Macros page")
        self.macros = MacrosPage(self.c, self)
        self.print_pages = (self.model, self.generators)
        for p in (self.machine, self.cylinder, self.model, self.generators, self.macros):
            self.stack.addWidget(p)

        centre = QVBoxLayout()
        centre.setContentsMargins(0, 0, 0, 0)
        centre.setSpacing(0)
        centre.addWidget(self.stack, 1)
        centre.addWidget(self._console())

        body.addWidget(self._nav())
        body.addLayout(centre, 1)
        body.addWidget(self._live())
        outer.addLayout(body, 1)

        self._log_handler = _QtLogHandler(self.ui, self.log_view)
        root_log = logging.getLogger()
        root_log.addHandler(self._log_handler)
        if root_log.level > logging.INFO or root_log.level == logging.NOTSET:
            root_log.setLevel(logging.INFO)
        logging.getLogger("cell_studio").info("Cell Studio started")
        self.act_console.toggled.connect(self.console_btn.setChecked)
        self.c.model_job_active = lambda: any(pg.printing and not pg.dry for pg in self.print_pages)
        self.go(0)
        if self.c.home_load_error:
            self.ui.warn("Home positions not loaded",
                         self.c.home_load_error + "\n\nCustom home is off for both arms (Home uses the xArm "
                         "factory home) until you set and save them again in Settings ▸ Home positions.")

    # ------------------------------------------------------------------ header
    def _header(self):
        c = self.c
        h = QFrame()
        h.setObjectName("Header")
        lay = QHBoxLayout(h)
        lay.setContentsMargins(18, 12, 18, 12)
        lay.setSpacing(10)
        tb = QVBoxLayout()
        tb.setSpacing(0)
        tb.addWidget(label("Cell Studio", "AppTitle"))
        tb.addWidget(label("Dual xArm 850 · ADRS turntable · Revo HF 1.2 mm", "AppSub"))
        lay.addLayout(tb)
        lay.addSpacing(18)

        self.chips = {}
        for key, text in (("left", "Left arm"), ("right", "Right arm"),
                          ("turntable", "Turntable"), ("extruder", "Extruder")):
            chip = Chip(text)
            self.chips[key] = chip
            lay.addWidget(chip)
        self.sim_badge = QLabel("SIMULATED HARDWARE")
        self.sim_badge.setStyleSheet(f"background:{theme.WARN}; color:#1b1300; font-weight:800; "
                                     "letter-spacing:1px; padding:6px 10px; border-radius:8px; font-size:12px;")
        self.sim_badge.setToolTip("Nothing on the cell moves. Untick Simulated hardware and reconnect to use the real cell.")
        self.sim_badge.setVisible(False)
        lay.addWidget(self.sim_badge)
        c.connected_var.changed.connect(self._update_chips)
        self._update_chips({})

        lay.addSpacing(10)
        self.job_chip = Chip("Idle")
        lay.addWidget(self.job_chip)
        c.job_state.changed.connect(self._update_job_chip)
        c.job_name.changed.connect(lambda _: self._update_job_chip(c.job_state.get()))
        self._update_job_chip("idle")
        lay.addStretch(1)

        menu_btn = QToolButton()
        menu_btn.setObjectName("MenuBtn")
        menu_btn.setText("⚙  Settings")
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        menu_btn.setCursor(Qt.PointingHandCursor)
        m = QMenu(menu_btn)
        a = m.addAction("Home positions…")
        a.triggered.connect(self.open_home_dialog)
        m.addAction("Tool and nozzle…").triggered.connect(self.open_tool_dialog)
        m.addAction("Print rules…").triggered.connect(self.open_rules_dialog)
        m.addSeparator()
        self.act_console = m.addAction("Show log console")
        self.act_console.setCheckable(True)
        self.act_console.toggled.connect(lambda on: self.console.setVisible(on))
        m.addSeparator()
        m.addAction("Configuration files…").triggered.connect(self._show_paths)
        menu_btn.setMenu(m)
        lay.addWidget(menu_btn)
        lay.addSpacing(8)

        estop = QPushButton("EMERGENCY STOP")
        estop.setObjectName("EStop")
        estop.setCursor(Qt.PointingHandCursor)
        estop.setToolTip("Stops all motion, disconnects the turntable and turns the heaters off")
        estop.clicked.connect(self._estop)
        lay.addWidget(estop)
        return h

    def _update_chips(self, state):
        state = state or {}
        self.sim_badge.setVisible(any(v == "sim" for v in state.values()))
        for key, chip in self.chips.items():
            kind = state.get(key)
            if kind == "real":
                chip.set_state(theme.OK)
                chip.setToolTip("Connected")
            elif kind == "sim":
                chip.set_state(theme.ACCENT)
                chip.setToolTip("Simulated")
            else:
                chip.set_state(theme.FAINT)
                chip.setToolTip("Not connected")

    def _update_job_chip(self, s):
        name = self.c.job_name.get()
        text = {"idle": "Idle", "running": f"Printing {name}".strip(), "paused": f"Paused {name}".strip(),
                "homing": "Homing", "preparing": "Preparing"}.get(s, s)
        self.job_chip.set_state(JOB_COLOURS.get(s, theme.FAINT), text)

    def _estop(self):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Emergency stop")
        box.setText("Stop all motion and turn the heaters off now?")
        yes = box.addButton("STOP", QMessageBox.DestructiveRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(yes)
        box.exec()
        if box.clickedButton() is yes:
            self.c.emergency_stop()

    # ------------------------------------------------------------------ nav
    def _nav(self):
        nav = QFrame()
        nav.setObjectName("Nav")
        nav.setFixedWidth(168)
        v = QVBoxLayout(nav)
        v.setContentsMargins(12, 16, 12, 16)
        v.setSpacing(4)
        v.addWidget(label("WORKSPACE", "SectionLabel"))
        v.addSpacing(4)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for i, text in enumerate(("◎   Machine", "◍   Cylinder", "▦   Model print", "⌬   Generators", "⟲   Macros")):
            b = QPushButton(text)
            b.setObjectName("NavBtn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=i: self.go(k))
            self.nav_group.addButton(b, i)
            v.addWidget(b)
        v.addStretch(1)
        self.console_btn = QPushButton("▤   Log console")
        self.console_btn.setObjectName("NavBtn")
        self.console_btn.setCheckable(True)
        self.console_btn.setCursor(Qt.PointingHandCursor)
        self.console_btn.toggled.connect(lambda on: self.act_console.setChecked(on))
        v.addWidget(self.console_btn)
        return nav

    def go(self, i):
        self.stack.setCurrentIndex(i)
        self.nav_group.button(i).setChecked(True)

    # ------------------------------------------------------------------ live panel
    def _live(self):
        c = self.c
        f = QFrame()
        f.setObjectName("LivePanel")
        f.setFixedWidth(268)
        v = QVBoxLayout(f)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        v.addWidget(label("LIVE", "SectionLabel"))

        job = Tile("Job")
        job.value.setObjectName("TileBig")
        c.elapsed_time_var.changed.connect(lambda _: self._job_tile(job))
        c.remaining_time_var.changed.connect(lambda _: self._job_tile(job))
        self._job_tile(job)
        v.addWidget(job)

        for side, col in (("left", theme.LEFT), ("right", theme.RIGHT)):
            t = Tile(f"{side} arm", col)
            t.value.setWordWrap(False)
            upd = (lambda tt=t, s=side: tt.set(
                c.tel[f'{s}_pos'].get() + ("\n" + c.tel[f'{s}_rot'].get() if c.tel[f'{s}_rot'].get() not in ("", "—") else "")))
            c.tel[f'{side}_pos'].changed.connect(lambda _, u=upd: u())
            c.tel[f'{side}_rot'].changed.connect(lambda _, u=upd: u())
            t.value.setStyleSheet("font-size: 12px;")
            upd()
            v.addWidget(t)

        tt = Tile("Turntable angle", theme.ACCENT)
        c.tel['turntable'].changed.connect(tt.set)
        tt.set(c.tel['turntable'].get())
        v.addWidget(tt)
        row = QHBoxLayout()
        row.setSpacing(10)
        for key, cap in (("t0", "Tool 0"), ("t1", "Tool 1 (X axis)")):
            t = Tile(cap)
            c.tel[key].changed.connect(t.set)
            t.set(c.tel[key].get())
            row.addWidget(t)
        v.addLayout(row)

        v.addSpacing(6)
        v.addWidget(divider())
        v.addSpacing(4)
        v.addWidget(label("TURNTABLE SPEED", "SectionLabel"))
        cap = label("", "CardHint", wrap=True)
        upd_cap = lambda m: cap.setText("Extrusion follows this speed." if m == "streamed" else
                                        "Changes rotation only: one move extrusion keeps its starting rate.")
        c.extrusion_mode.changed.connect(upd_cap)
        upd_cap(c.extrusion_mode.get())
        v.addWidget(cap)
        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, int(c.tt_speed_max * 1000))
        sl.setSingleStep(5)
        sl.setPageStep(50)
        sl.setValue(int(c.turntable_speed_var.get() * 1000))
        sl.valueChanged.connect(lambda i: c.turntable_speed_var.set(round(i / 1000.0, 3)))
        c.turntable_speed_var.changed.connect(
            lambda x: (sl.blockSignals(True), sl.setValue(int(round(x * 1000))), sl.blockSignals(False)))
        v.addWidget(sl)
        v.addWidget(NumberField(c.turntable_speed_var, "rad/s", 3, 0.01, nudges=(-0.01, 0.01), width=130,
                                clamp=(0.0, c.tt_speed_max)))
        v.addStretch(1)
        return f

    def _job_tile(self, tile):
        tile.set(f"{self.c.elapsed_time_var.get()}  ·  {self.c.remaining_time_var.get()}")
        tile.setToolTip("Elapsed · remaining")

    # ------------------------------------------------------------------ console
    def _console(self):
        self.console = QFrame()
        self.console.setObjectName("Card")
        self.console.setStyleSheet("QFrame#Card { border-radius: 0; border-left: none; border-right: none; border-bottom: none; }")
        v = QVBoxLayout(self.console)
        v.setContentsMargins(14, 8, 14, 10)
        v.setSpacing(6)
        v.addWidget(hrow(label("LOG", "SectionLabel"), None,
                         button("Clear", "ghost", lambda: self.log_view.clear())))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setFixedHeight(170)
        v.addWidget(self.log_view)
        self.console.setVisible(False)
        return self.console

    # ------------------------------------------------------------------ dialogs
    def open_home_dialog(self):
        """Non-modal, so the EMERGENCY STOP stays reachable while testing a home move."""
        if getattr(self, "_home_dlg", None) is not None and self._home_dlg.isVisible():
            self._home_dlg.raise_()
            return
        dlg = HomeDialog(self.c, self)
        dlg.setModal(False)
        dlg.setWindowModality(Qt.NonModal)
        dlg.setAttribute(Qt.WA_DeleteOnClose)

        def saved():
            try:
                self.c.save_home(dlg.result_data())
                self.machine.refresh_home_hint()
            except Exception as e:
                QMessageBox.critical(self, "Home positions", f"Could not save:\n{e}")
        dlg.accepted.connect(saved)
        self._home_dlg = dlg
        dlg.show()

    def open_tool_dialog(self):
        from . import tool as toolmod
        if self.c.printing or self.c.busy:
            QMessageBox.warning(self, "Tool and nozzle", "Wait for the current job to finish.")
            return
        dlg = ToolDialog(self.c, self)
        if dlg.exec():
            d = dlg.data()
            toolmod.save(d)
            self.c.tool = d
            logging.getLogger("cell_studio").info("Tool settings saved: %s", d)

    def open_rules_dialog(self):
        from .views.rules_dialog import RulesDialog
        self._rules_dlg = RulesDialog(self)
        self._rules_dlg.show()          # non-modal: the E-stop stays reachable

    def _show_paths(self):
        from . import home_config
        QMessageBox.information(self, "Configuration files",
                                "Hardware addresses:  config/settings.yaml\n"
                                f"Home positions:  {home_config.HOME_FILE}\n"
                                "Cylinder presets:  Cylinder ▸ Save config / Load config")

    def closeEvent(self, e):
        for pg in (self.generators, self.macros):
            if getattr(pg, "proc", None) is not None:
                pg.proc.kill()
        c = self.c
        if c.printing or c.busy or any(pg.printing for pg in self.print_pages):
            if not c.ui.confirm("Quit", "A job is running. Quit anyway?\n\n"
                                        "The job is stopped and the turntable halted before the panel closes."):
                e.ignore()
                return
            for pg in self.print_pages:
                pg.stop_requested = True
        self.cylinder.view.stop_simulation()
        logging.getLogger().removeHandler(self._log_handler)
        self.c.shutdown()
        super().closeEvent(e)
