"""Toolpath tab: the raw planned motion, a band of layers at a time.

What the arms will actually do, straight from the motion program (not the slice):
every extrusion move of every arm in the plate frame (what ends up on the part) or the
world frame (what the arms trace while the disc turns), travels dashed, older layers
dimmer than the newest, with the print order of the newest layer numbered so the
wall → circles → straight-lines order can be checked.
"""

import math

import numpy as np
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSlider, QCheckBox

from .. import theme
from ..widgets import label, hrow, ISpin


class PlanView(QWidget):
    def __init__(self):
        super().__init__()
        self.prog = None
        self.canvas = _Canvas(self)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(8)
        v.addWidget(self.canvas, 1)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.valueChanged.connect(lambda _: self.canvas.update())
        self.span = ISpin()
        self.span.setRange(1, 50)
        self.span.setValue(3)
        self.span.setToolTip("How many layers to show, ending at the slider's layer")
        self.span.valueChanged.connect(lambda _: self.canvas.update())
        self.world = QCheckBox("world frame")
        self.world.setToolTip("Off: the plate frame (what lands on the part).\nOn: what the nozzles trace while the disc turns.")
        self.world.toggled.connect(lambda _: self.canvas.update())
        self.travels = QCheckBox("travels")
        self.travels.setChecked(True)
        self.travels.toggled.connect(lambda _: self.canvas.update())
        self.lbl = label("", "Muted")
        row = hrow(label("to layer"), self.slider, label("show"), self.span, label("layers"),
                   self.world, self.travels, self.lbl, spacing=10)
        row.layout().setStretch(1, 1)                     # the slider takes the spare width
        self.slider.setMinimumWidth(160)
        v.addWidget(row)

    def set_program(self, prog):
        self.prog = prog
        self.data = None
        if prog is None or not prog.steps:
            self.slider.setRange(0, 0)
            self.canvas.update()
            return
        from planner import from_arm_frame
        cfg, steps = prog.config, prog.steps
        cx, cy = cfg.center[0], cfg.center[1]
        arms = max(len(s.arms) for s in steps)
        phi = np.radians([s.tt_angle_deg for s in steps])
        layer = np.array([s.layer for s in steps])
        d = {"arms": arms, "layer": layer, "px": [], "py": [], "wx": [], "wy": [], "z": [], "ext": [], "ok": []}
        for a in range(arms):
            ats = [s.arms[a] if a < len(s.arms) else None for s in steps]
            pts = [from_arm_frame(cfg, a, (at.x, at.y, at.z)) if at else (np.nan, np.nan, np.nan) for at in ats]
            wx = np.array([p[0] - cx for p in pts])
            wy = np.array([p[1] - cy for p in pts])
            c, s_ = np.cos(-phi), np.sin(-phi)
            d["px"].append(wx * c - wy * s_)
            d["py"].append(wx * s_ + wy * c)
            d["wx"].append(wx)
            d["wy"].append(wy)
            d["z"].append(np.array([p[2] - cfg.z_base for p in pts]))
            d["ext"].append(np.array([bool(at and at.extrude) for at in ats]))
            d["ok"].append(np.array([at is not None for at in ats]))
        # band the steps by height, not by the slicer's layer index: a spiral's helix is one
        # path over many layers, so "layer" here means the z band of one layer height
        lh = float(cfg.layer_height or 0.6)
        zs = np.full(len(steps), np.nan)
        for a in range(arms):
            za = d["z"][a].copy()
            za[~d["ext"][a]] = np.nan
            zs = np.where(np.isnan(zs), za, zs)
        band = np.floor(zs / lh + 1e-6)
        band = np.where(np.isnan(band), -1, band).astype(int)
        # travels between extrusions take the band of the extrusion that follows
        nxt = -1
        for i in range(len(band) - 1, -1, -1):
            if band[i] >= 0:
                nxt = band[i]
            else:
                band[i] = nxt
        d["layer"] = band
        rr = [np.hypot(d["px"][a], d["py"][a])[d["ext"][a]] for a in range(arms)]
        r = max((float(np.nanmax(v)) for v in rr if v.size), default=20.0)
        d["r"] = float(max(r, 20.0))             # the part's radius: moves outside it are parking
        layers = sorted(set(int(v) for v in d["layer"] if v >= 0))
        d["layers"] = layers
        self.data = d
        self.slider.setRange(0, max(0, len(layers) - 1))
        self.slider.setValue(min(len(layers) - 1, 2))
        self.canvas.update()


class _Canvas(QWidget):
    def __init__(self, view):
        super().__init__()
        self.view = view
        self.setMinimumSize(360, 300)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0b0f14"))
        v = self.view
        d = getattr(v, "data", None)
        if not d:
            p.setPen(QColor(theme.FAINT))
            p.setFont(QFont(p.font().family(), 12))
            p.drawText(self.rect(), Qt.AlignCenter, "Slice + preview to see the planned toolpath here")
            return
        w, h = self.width(), self.height()
        top = 62
        scale = (min(w, h - top) - 40) / (2 * d["r"] * 1.08)
        ox, oy = w / 2, top + (h - top) / 2

        def X(x):
            return ox + x * scale

        def Y(y):
            return oy - y * scale

        # the disc and axis
        p.setPen(QPen(QColor("#1f2731"), 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(ox, oy), d["r"] * scale, d["r"] * scale)   # the part's extent
        p.drawLine(QPointF(ox - 6, oy), QPointF(ox + 6, oy))
        p.drawLine(QPointF(ox, oy - 6), QPointF(ox, oy + 6))

        layers = d["layers"]
        k = v.slider.value()
        n = v.span.value()
        shown = layers[max(0, k - n + 1): k + 1]
        if not shown:
            return
        world = v.world.isChecked()
        xs, ys = (d["wx"], d["wy"]) if world else (d["px"], d["py"])
        lay = d["layer"]
        counts = {0: 0, 1: 0}
        zlo, zhi = math.inf, -math.inf
        for rank, L in enumerate(shown):
            age = (len(shown) - 1 - rank) / max(1, len(shown) - 1) if len(shown) > 1 else 0.0
            alpha = int(255 * (1.0 - 0.75 * age))
            idx = np.nonzero(lay == L)[0]
            if idx.size == 0:
                continue
            for a in range(d["arms"]):
                col = QColor(theme.RIGHT if a == 0 else theme.LEFT)
                col.setAlpha(alpha)
                tcol = QColor("#9aa4b1")
                tcol.setAlpha(max(40, alpha // 2))
                x, y, ext, ok, z = xs[a], ys[a], d["ext"][a], d["ok"][a], d["z"][a]
                wx, wy = d["wx"][a], d["wy"][a]
                path_e, path_t = QPainterPath(), QPainterPath()
                for i in idx:
                    i = int(i)
                    if i == 0 or not (ok[i] and ok[i - 1]):
                        continue
                    a0, a1 = QPointF(X(x[i - 1]), Y(y[i - 1])), QPointF(X(x[i]), Y(y[i]))
                    if ext[i]:
                        path_e.moveTo(a0)
                        path_e.lineTo(a1)
                        counts[a] = counts.get(a, 0) + 1
                        zlo, zhi = min(zlo, z[i]), max(zhi, z[i])
                    elif (v.travels.isChecked() and abs(z[i] - z[i - 1]) < 50
                          and math.hypot(wx[i] - wx[i - 1], wy[i] - wy[i - 1]) > 0.05      # the nozzle moved
                          and math.hypot(x[i], y[i]) <= d["r"] + 5 and math.hypot(x[i - 1], y[i - 1]) <= d["r"] + 5):
                        path_t.moveTo(a0)
                        path_t.lineTo(a1)
                if v.travels.isChecked():
                    p.setPen(QPen(tcol, 0.8, Qt.DashLine))
                    p.drawPath(path_t)
                p.setPen(QPen(col, 1.6 if rank == len(shown) - 1 else 1.2))
                p.drawPath(path_e)
        # print order of the newest layer: number each run of extrusion per arm
        L = shown[-1]
        idx = np.nonzero(lay == L)[0]
        p.setFont(QFont(p.font().family(), 8))
        placed = []
        for a in range(d["arms"]):
            x, y, ext, ok = xs[a], ys[a], d["ext"][a], d["ok"][a]
            num, last_end = 0, -10
            for i in idx:
                i = int(i)
                if ext[i] and ok[i] and (i - last_end > 1) and (not ext[i - 1] if i > 0 else True):
                    num += 1
                    px, py = X(x[i]) + 4, Y(y[i]) - 4
                    if num <= 60 and all(abs(px - qx) > 14 or abs(py - qy) > 12 for qx, qy in placed):
                        p.setPen(QColor(theme.RIGHT if a == 0 else theme.LEFT))
                        p.drawText(QPointF(px, py), str(num))
                        placed.append((px, py))
                if ext[i]:
                    last_end = i
            # nozzle now
            end = int(idx[-1]) if idx.size else None
            if end is not None and ok[end]:
                p.setBrush(QColor(theme.RIGHT if a == 0 else theme.LEFT))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(X(x[end]), Y(y[end])), 4, 4)
        p.setPen(QColor(theme.TEXT))
        p.setFont(QFont(p.font().family(), 11, QFont.DemiBold))
        first, last = shown[0] + 1, shown[-1] + 1
        p.drawText(QPointF(16, 26), f"Planned toolpath · layers {first}–{last} of {len(layers)}"
                   + (f" · z {zlo:.1f}–{zhi:.1f} mm" if zhi >= zlo else ""))
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont(p.font().family(), 10))
        p.drawText(QPointF(16, 46), ("world frame (what the nozzles trace while the disc turns)" if world
                                     else "plate frame (what lands on the part)")
                   + f" · right {counts.get(0, 0)} moves · left {counts.get(1, 0)} moves"
                   + " · numbers = print order on the newest layer · dashed = travel (disc turns and parking hidden)")
        v.lbl.setText(f"layer {last}")
