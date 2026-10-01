"""Settings ▸ Tool and nozzle."""

from PySide6.QtWidgets import QDialog, QVBoxLayout, QMessageBox

from .. import tool as toolmod
from ..state import DoubleVar, StrVar
from ..widgets import NumberField, FormGrid, button, label, hrow, divider


class ToolDialog(QDialog):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.c = ctl
        self.setWindowTitle("Tool and nozzle")
        self.setMinimumWidth(460)
        t = dict(ctl.tool)
        self.v = {k: DoubleVar(t[k]) for k in ("nozzle_diameter", "line_width", "layer_height", "roll", "pitch", "yaw")}
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(12)
        v.addWidget(label("Tool and nozzle", "CardTitle"))
        v.addWidget(label("Used by the cylinder print, Prepare to print, Model print, Generators and the macros. "
                          "The xArm's own TCP offset must also match the mount.", "CardHint", wrap=True))
        fg = FormGrid()
        fg.row("Nozzle diameter", NumberField(self.v["nozzle_diameter"], "mm", 2, 0.1, minimum=0.1, maximum=3))
        fg.row("Default line width", NumberField(self.v["line_width"], "mm", 2, 0.05, minimum=0.1, maximum=5))
        fg.row("Default layer height", NumberField(self.v["layer_height"], "mm", 2, 0.05, minimum=0.05, maximum=2))
        v.addLayout(fg)
        v.addWidget(divider())
        v.addWidget(label("Nozzle orientation commanded to both arms (degrees). Straight down: roll 180, pitch 0. "
                          "The old tilted mount used pitch 45.", "CardHint", wrap=True))
        fg2 = FormGrid()
        fg2.row("Roll", NumberField(self.v["roll"], "°", 1, 1, minimum=-360, maximum=360))
        fg2.row("Pitch", NumberField(self.v["pitch"], "°", 1, 1, minimum=-90, maximum=90))
        fg2.row("Yaw", NumberField(self.v["yaw"], "°", 1, 1, minimum=-360, maximum=360))
        v.addLayout(fg2)
        v.addWidget(hrow(None, button("Cancel", "ghost", self.reject), button("Save", "primary", self.accept)))

    def data(self):
        d = dict(self.c.tool)
        d.update({k: var.get() for k, var in self.v.items()})
        return d

    def accept(self):
        probs = toolmod.problems(self.data())
        if probs and QMessageBox.question(self, "Tool and nozzle", "\n".join(probs) + "\n\nSave anyway?") != QMessageBox.Yes:
            return
        super().accept()
