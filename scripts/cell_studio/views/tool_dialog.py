"""Settings ▸ Tool and nozzle."""

from PySide6.QtWidgets import QDialog, QVBoxLayout, QMessageBox

from .. import tool as toolmod
from ..state import DoubleVar, StrVar
from ..widgets import NumberField, FormGrid, button, label, hrow, divider, Segmented

_APPROACHES = [("disc", "Facing the disc"), ("y_plus", "Sideways +90°"), ("y_minus", "Sideways −90°")]
_APPROACH_DEG = {"disc": 0.0, "y_plus": 90.0, "y_minus": -90.0}


def _approach_name(deg):
    deg = float(deg or 0.0)
    return "y_plus" if deg > 45 else "y_minus" if deg < -45 else "disc"


class ToolDialog(QDialog):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.c = ctl
        self.setWindowTitle("Tool and nozzle")
        self.setMinimumWidth(460)
        t = dict(ctl.tool)
        self.v = {k: DoubleVar(t[k]) for k in ("nozzle_diameter", "line_width", "layer_height",
                                             "roll_right", "pitch_right", "yaw_right",
                                             "roll_left", "pitch_left", "yaw_left")}
        self.v["approach_right"] = StrVar(_approach_name(t.get("approach_right", 0.0)))
        self.v["approach_left"] = StrVar(_approach_name(t.get("approach_left", 0.0)))
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
        v.addWidget(label("Orientation commanded to each arm (degrees). Current mounts: right roll 90, "
                          "pitch -90, yaw 90; left roll 0, pitch 90, yaw 0. At pitch ±90 the arm may report a "
                          "different but equivalent roll/yaw; that is normal.", "CardHint", wrap=True))
        fg2 = FormGrid()
        for side in ("right", "left"):
            S = side.capitalize()
            fg2.row(f"{S} roll", NumberField(self.v[f"roll_{side}"], "°", 1, 1, minimum=-360, maximum=360))
            fg2.row(f"{S} pitch", NumberField(self.v[f"pitch_{side}"], "°", 1, 1, minimum=-90, maximum=90))
            fg2.row(f"{S} yaw", NumberField(self.v[f"yaw_{side}"], "°", 1, 1, minimum=-360, maximum=360))
        v.addLayout(fg2)
        v.addWidget(divider())
        v.addWidget(label("Approach: how the whole tool is turned about the vertical. Facing the disc = the "
                          "flange points at the turntable. Sideways = the flange points along the arm's Y "
                          "(mount edge-on to the other arm, extruder able to get in beside tall features). "
                          "Added to the yaw above; the collision outline turns with it.", "CardHint", wrap=True))
        fg3 = FormGrid()
        for side in ("right", "left"):
            fg3.row(f"{side.capitalize()} approach", Segmented(self.v[f"approach_{side}"], _APPROACHES))
        v.addLayout(fg3)
        v.addWidget(hrow(None, button("Cancel", "ghost", self.reject), button("Save", "primary", self.accept)))

    def data(self):
        d = dict(self.c.tool)
        d.update({k: var.get() for k, var in self.v.items() if not k.startswith("approach_")})
        for side in ("right", "left"):
            d[f"approach_{side}"] = _APPROACH_DEG[self.v[f"approach_{side}"].get()]
        return d

    def accept(self):
        probs = toolmod.problems(self.data())
        if probs and QMessageBox.question(self, "Tool and nozzle", "\n".join(probs) + "\n\nSave anyway?") != QMessageBox.Yes:
            return
        super().accept()
