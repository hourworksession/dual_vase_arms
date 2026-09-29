"""Settings ▸ Home positions dialog."""

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget,
                               QWidget, QStackedWidget, QMessageBox, QDialogButtonBox)

from .. import theme, home_config
from ..state import DoubleVar, BoolVar, StrVar
from ..widgets import (NumberField, Check, Segmented, Combo, FormGrid, button, label, hrow, divider, Dot)


class _ArmEditor(QWidget):
    def __init__(self, ctl, side, cfg):
        super().__init__()
        self.c, self.side = ctl, side
        self.enabled = BoolVar(cfg.get("enabled", True))
        self.mode = StrVar(cfg.get("mode", "joint"))
        self.joints = [DoubleVar(v) for v in cfg["joints"]]
        self.pose = [DoubleVar(v) for v in cfg["pose"]]
        self.joint_speed = DoubleVar(cfg.get("joint_speed", 20.0))
        self.pose_speed = DoubleVar(cfg.get("pose_speed", 50.0))
        self.lift = BoolVar(cfg.get("lift_first", True))
        self.safe_z = DoubleVar(cfg.get("safe_z", 250.0))

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 16, 6, 6)
        v.setSpacing(12)
        v.addWidget(Check("Use a custom home for this arm", self.enabled))
        v.addWidget(label("When off, Home calls the xArm's built in factory home (move_gohome).",
                          "CardHint", wrap=True))
        self.body = QWidget()
        bv = QVBoxLayout(self.body)
        bv.setContentsMargins(0, 4, 0, 0)
        bv.setSpacing(12)
        bv.addWidget(hrow(label("Defined by", "FieldLabel"),
                          Segmented(self.mode, [("joint", "Joint angles"), ("pose", "Cartesian pose")])))

        self.stack = QStackedWidget()
        # joints
        jw = QWidget()
        jg = QGridLayout(jw)
        jg.setContentsMargins(0, 0, 0, 0)
        jg.setHorizontalSpacing(14)
        jg.setVerticalSpacing(8)
        for i, var in enumerate(self.joints):
            jg.addWidget(label(f"J{i + 1}", "FieldLabel"), i % 3, (i // 3) * 2)
            jg.addWidget(NumberField(var, "°", 2, 1.0, minimum=-360, maximum=360, width=130), i % 3, (i // 3) * 2 + 1)
        jg.addWidget(label("Speed", "FieldLabel"), 3, 0)
        jg.addWidget(NumberField(self.joint_speed, "°/s", 1, 1.0, minimum=1, maximum=180, width=130), 3, 1)
        jg.addWidget(label("Moves every joint straight to these angles. Best for the new mounts: "
                           "the arm always ends in the same configuration.", "CardHint", wrap=True), 4, 0, 1, 4)
        self.stack.addWidget(jw)
        # pose
        pw = QWidget()
        pg = QGridLayout(pw)
        pg.setContentsMargins(0, 0, 0, 0)
        pg.setHorizontalSpacing(14)
        pg.setVerticalSpacing(8)
        names = [("X", "mm"), ("Y", "mm"), ("Z", "mm"), ("Roll", "°"), ("Pitch", "°"), ("Yaw", "°")]
        for i, ((n, u), var) in enumerate(zip(names, self.pose)):
            pg.addWidget(label(n, "FieldLabel"), i % 3, (i // 3) * 2)
            pg.addWidget(NumberField(var, u, 2, 1.0, width=130), i % 3, (i // 3) * 2 + 1)
        pg.addWidget(label("Speed", "FieldLabel"), 3, 0)
        pg.addWidget(NumberField(self.pose_speed, "mm/s", 1, 5.0, minimum=1, maximum=500, width=130), 3, 1)
        pg.addWidget(Check("Rise to safe Z first", self.lift), 4, 0, 1, 2)
        pg.addWidget(NumberField(self.safe_z, "mm", 1, 5.0, width=130), 4, 3)
        pg.addWidget(label("Safe Z", "FieldLabel"), 4, 2)
        self.stack.addWidget(pw)
        bv.addWidget(self.stack)
        bv.addWidget(divider())
        bv.addWidget(hrow(button("Use current position", None, self.capture,
                                 tip="Reads the connected arm's joints and pose into these fields"),
                          button("Move there now", None, self.test,
                                 tip="Test move this arm to the values shown (not yet saved)"),
                          None))
        v.addWidget(self.body)
        v.addStretch(1)

        self.mode.changed.connect(lambda m: self.stack.setCurrentIndex(0 if m == "joint" else 1))
        self.stack.setCurrentIndex(0 if self.mode.get() == "joint" else 1)
        self.enabled.changed.connect(self.body.setEnabled)
        self.body.setEnabled(self.enabled.get())

    def data(self):
        return {"enabled": self.enabled.get(), "mode": self.mode.get(),
                "joints": [v.get() for v in self.joints], "pose": [v.get() for v in self.pose],
                "joint_speed": self.joint_speed.get(), "pose_speed": self.pose_speed.get(),
                "lift_first": self.lift.get(), "safe_z": self.safe_z.get()}

    def capture(self):
        try:
            joints, pose = self.c.capture_home(self.side)
        except Exception as e:
            QMessageBox.critical(self, "Capture", str(e))
            return
        if joints is None and pose is None:
            QMessageBox.warning(self, "Capture", f"Connect the {self.side} arm first.")
            return
        if joints:
            for var, val in zip(self.joints, joints):
                var.set(round(float(val), 2))
        if pose:
            for var, val in zip(self.pose, pose):
                var.set(round(float(val), 2))
        QMessageBox.information(self, "Capture",
                                f"Copied the {self.side} arm's current position. Press Save to keep it.")

    def test(self):
        d = self.data()
        if not self.c.ui.confirm("Move arm", f"Move the {self.side} arm to this home now?\n"
                                             "Check the path is clear."):
            return
        self.c.home_one(self.side, d)


class HomeDialog(QDialog):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.c = ctl
        self.setWindowTitle("Home positions")
        self.setMinimumWidth(640)
        data = copy.deepcopy(ctl.home)

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(12)
        v.addWidget(label("Home positions", "CardTitle"))
        v.addWidget(label("Where the arms go when you press Home. Saved to config/home_positions.json.",
                          "CardHint", wrap=True))
        tabs = QTabWidget()
        self.eds = {}
        for side, col in (("left", theme.LEFT), ("right", theme.RIGHT)):
            ed = _ArmEditor(ctl, side, data[side])
            self.eds[side] = ed
            tabs.addTab(ed, f"{side.title()} arm")
        v.addWidget(tabs)
        v.addWidget(divider())
        self.seq = StrVar(data.get("sequence", "simultaneous"))
        labels = dict(home_config.SEQUENCES)
        seq_combo = Combo(StrVar(labels[self.seq.get()]), [t for _, t in home_config.SEQUENCES], width=200)
        rev = {t: k for k, t in home_config.SEQUENCES}
        seq_combo.currentTextChanged.connect(lambda t: self.seq.set(rev[t]))
        v.addWidget(hrow(label("When homing both arms", "FieldLabel"), seq_combo, None))

        save = button("Save", "primary", self.accept, min_w=110)
        cancel = button("Cancel", "ghost", self.reject, min_w=90)
        v.addWidget(hrow(None, cancel, save))

    def result_data(self):
        out = {side: ed.data() for side, ed in self.eds.items()}
        out["sequence"] = self.seq.get()
        return out
