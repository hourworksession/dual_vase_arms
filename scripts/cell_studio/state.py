"""Observable variables and a bridge for touching the UI from worker threads.

``Var`` mirrors the tkinter variable API (``get`` / ``set``) so the ported
machine logic reads exactly as before. Reading from a worker thread is safe.
Setting should happen on the GUI thread: worker threads use ``ui.post(fn)``.
"""

import threading

from PySide6.QtCore import QObject, Signal, Slot, Qt
from PySide6.QtWidgets import QMessageBox, QApplication


class Var(QObject):
    changed = Signal(object)

    def __init__(self, value=None, kind=None):
        super().__init__()
        self._kind = kind or (type(value) if value is not None else None)
        self._v = self._coerce(value)

    def _coerce(self, v):
        if self._kind is None or v is None:
            return v
        if self._kind is bool:
            return bool(v)
        try:
            return self._kind(v)
        except (TypeError, ValueError):
            return v

    def get(self):
        return self._v

    def set(self, v):
        v = self._coerce(v)
        if v != self._v:
            self._v = v
            self.changed.emit(v)


def DoubleVar(value=0.0):
    return Var(float(value), float)


def IntVar(value=0):
    return Var(int(value), int)


def BoolVar(value=False):
    return Var(bool(value), bool)


def StrVar(value=""):
    return Var(str(value), str)


class UiBridge(QObject):
    """Runs callables on the GUI thread and shows dialogs from any thread."""

    _call = Signal(object)

    def __init__(self):
        super().__init__()
        self._call.connect(self._run, Qt.QueuedConnection)
        self._gui_thread = threading.current_thread()
        self.parent_widget = None

    @Slot(object)
    def _run(self, fn):
        try:
            fn()
        except Exception:  # never let a UI callback kill the event loop
            import logging
            logging.getLogger("cell_studio").exception("UI callback failed")

    def post(self, fn):
        """Equivalent of tkinter's root.after(0, fn)."""
        if threading.current_thread() is self._gui_thread:
            fn()
        else:
            self._call.emit(fn)

    # ---- dialogs (safe from any thread) ----
    def info(self, title, msg):
        self.post(lambda: QMessageBox.information(self.parent_widget, title, str(msg)))

    def warn(self, title, msg):
        self.post(lambda: QMessageBox.warning(self.parent_widget, title, str(msg)))

    def error(self, title, msg):
        self.post(lambda: QMessageBox.critical(self.parent_widget, title, str(msg)))

    def confirm(self, title, msg):
        """GUI thread only. Returns True on Yes."""
        r = QMessageBox.question(self.parent_widget, title, str(msg),
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return r == QMessageBox.Yes
