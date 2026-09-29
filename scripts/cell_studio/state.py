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
        return self._kind(v)          # raises for "abc" into a number

    def get(self):
        return self._v

    def set(self, v):
        try:
            v = self._coerce(v)
        except (TypeError, ValueError):
            import logging
            logging.getLogger("cell_studio").warning("Ignored invalid value %r", v)
            return
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

    # ---- notices (safe from any thread) ----
    # Non-modal on purpose: a pop-up must never stop someone reaching the E-stop.
    def _notice(self, icon, title, msg):
        def show():
            box = QMessageBox(icon, title, str(msg), QMessageBox.Ok, self.parent_widget)
            box.setModal(False)
            box.setWindowModality(Qt.NonModal)
            box.setAttribute(Qt.WA_DeleteOnClose)
            box.show()
        self.post(show)

    def info(self, title, msg):
        self._notice(QMessageBox.Information, title, msg)

    def warn(self, title, msg):
        self._notice(QMessageBox.Warning, title, msg)

    def error(self, title, msg):
        self._notice(QMessageBox.Critical, title, msg)

    def choose(self, title, msg, buttons):
        """GUI thread. buttons: [(label, "accept"|"destructive"|"reject")]. Returns the label or None."""
        roles = {"accept": QMessageBox.AcceptRole, "destructive": QMessageBox.DestructiveRole,
                 "reject": QMessageBox.RejectRole}
        box = QMessageBox(QMessageBox.Question, title, str(msg), QMessageBox.NoButton, self.parent_widget)
        made = {}
        for label, role in buttons:
            made[label] = box.addButton(label, roles[role])
        box.setDefaultButton(made[buttons[0][0]])
        box.exec()
        clicked = box.clickedButton()
        for label, b in made.items():
            if b is clicked:
                return label
        return None

    def confirm(self, title, msg):
        """GUI thread only. Returns True on Yes."""
        r = QMessageBox.question(self.parent_widget, title, str(msg),
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return r == QMessageBox.Yes
