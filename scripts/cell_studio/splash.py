"""Start-up window: opens straight away and shows what is loading.

The heavy libraries (numpy, scipy, trimesh, shapely, FullControl) are imported
here, one at a time, so the user sees progress instead of a blank wait, and the
first Slice afterwards is quicker because they are already loaded.

Missing optional parts (slicer libraries, FullControl, camera, hardware drivers)
are listed as warnings; the panel still opens. A failure that stops the panel
from opening is shown on this window with the full error, instead of the
program silently vanishing.
"""

import importlib
import importlib.util
import time
import traceback

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel, QProgressBar, QPlainTextEdit,
                               QPushButton, QHBoxLayout)

from . import theme


def _imp(name):
    return lambda: importlib.import_module(name)


def _present(*names):
    """Check a module is installed without importing it (drivers can be slow to load)."""
    def f():
        for n in names:
            try:
                if importlib.util.find_spec(n) is None:
                    raise ImportError(f"{n} is not installed")
            except (ImportError, ValueError) as e:
                raise ImportError(str(e))
    return f


# (label, action, required, what is lost without it)
STEPS = [
    ("Numerical libraries (numpy)", _imp("numpy"), True, ""),
    ("Image processing (scipy)", _imp("scipy.ndimage"), True, ""),
    ("Configuration (yaml)", _imp("yaml"), True, ""),
    ("Mesh library (trimesh)", _imp("trimesh"), False, "Model print cannot slice STL/3MF/OBJ files."),
    ("Geometry library (shapely)", _imp("shapely"), False, "Model print cannot slice."),
    ("Slicer", _imp("slicer"), False, "Model print cannot slice."),
    ("Motion planner", _imp("planner"), False, "Model print and Generators cannot plan."),
    ("FullControl", _imp("fullcontrol"), False, "FullControl generators and the dual vase macro will not run."),
    ("Camera (OpenCV)", _present("cv2"), False, "Macros can only use the simulated camera."),
    ("xArm SDK", _present("xarm"), False, "The arms cannot be connected (simulated hardware still works)."),
    ("Turntable driver (pyautomation)", _present("pyautomation"), False,
     "The turntable cannot be connected (simulated hardware still works)."),
    ("Extruder link (requests)", _present("requests"), False, "The extruder cannot be connected."),
]


class Splash(QWidget):
    def __init__(self):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setObjectName("Splash")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(520)
        self.setStyleSheet(f"""
            #Splash {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 12px; }}
            QLabel#SplashTitle {{ font-size: 22px; font-weight: 700; color: {theme.TEXT}; }}
            QLabel#SplashSub {{ color: {theme.MUTED}; }}
            QLabel#SplashStep {{ color: {theme.TEXT}; }}
            QProgressBar {{ border: none; background: {theme.BORDER}; border-radius: 3px; height: 6px; }}
            QProgressBar::chunk {{ background: {theme.ACCENT}; border-radius: 3px; }}
            QPlainTextEdit {{ background: {theme.BG}; color: {theme.MUTED}; border: none;
                              font-family: {theme.FONT_MONO}; font-size: 11px; }}
        """)
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 24, 28, 22)
        v.setSpacing(10)
        t = QLabel("Cell Studio")
        t.setObjectName("SplashTitle")
        s = QLabel("Dual xArm 850 · turntable · print control")
        s.setObjectName("SplashSub")
        v.addWidget(t)
        v.addWidget(s)
        v.addSpacing(8)
        self.step = QLabel("Starting…")
        self.step.setObjectName("SplashStep")
        v.addWidget(self.step)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        v.addWidget(self.bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(150)
        v.addWidget(self.log)
        self.buttons = QWidget()
        bl = QHBoxLayout(self.buttons)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addStretch(1)
        self.copy_btn = QPushButton("Copy error")
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(QApplication.instance().quit)
        bl.addWidget(self.copy_btn)
        bl.addWidget(self.close_btn)
        self.buttons.hide()
        v.addWidget(self.buttons)
        self.warnings = []
        self._t0 = time.perf_counter()

    def note(self, text):
        self.log.appendPlainText(text)
        self._pump()

    def set_step(self, text, value=None, maximum=None):
        self.step.setText(text)
        if maximum is not None:
            self.bar.setMaximum(maximum)
        if value is not None:
            self.bar.setValue(value)
        self._pump()

    def _pump(self):
        QApplication.processEvents()

    def run_steps(self, extra_total):
        """Import everything in STEPS. Returns False if a required part failed."""
        total = len(STEPS) + extra_total
        for i, (name, action, required, lost) in enumerate(STEPS):
            self.set_step(f"Loading {name}…", i, total)
            t = time.perf_counter()
            try:
                action()
                self.note(f"✓ {name}  ({time.perf_counter() - t:.1f} s)")
            except Exception as e:
                msg = f"{type(e).__name__}: {e}".splitlines()[0]
                if required:
                    self.fail(f"{name} could not be loaded", traceback.format_exc())
                    return False
                self.warnings.append(f"{name}: {lost}  ({msg})")
                self.note(f"⚠ {name}: not available. {lost}")
        return True

    def fail(self, title, detail):
        self.step.setText(f"<span style='color:{theme.DANGER}; font-weight:600'>{title}</span>")
        self.note("\n" + detail.rstrip())
        self.log.setFixedHeight(260)
        self.copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(title + "\n\n" + detail))
        self.buttons.show()
        self.adjustSize()
        self._center()

    def finish_text(self):
        return f"Ready in {time.perf_counter() - self._t0:.1f} s"

    def _center(self):
        scr = QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)

    def showEvent(self, e):
        super().showEvent(e)
        self._center()
