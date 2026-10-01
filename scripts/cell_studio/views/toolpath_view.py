"""3D preview of a generator toolpath: drag to orbit, wheel to zoom, double-click to reset.

Drawn with QPainter (no OpenGL needed on the cell PC). Colour runs from blue at
the bottom to orange at the top; travels are thin dashed lines. The turntable
disc (r = 150 mm) is drawn at z = 0 so you can see how the part sits on it.
`progress` (0..1) shows the toolpath only up to that point in print order.
"""

import math

import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath
from PySide6.QtWidgets import QWidget

from .. import theme
from ..toolpath import DISC_RADIUS

MAX_DRAW_POINTS = 80_000


def _mix(c0, c1, t):
    return QColor(int(c0[0] + (c1[0] - c0[0]) * t), int(c0[1] + (c1[1] - c0[1]) * t),
                  int(c0[2] + (c1[2] - c0[2]) * t))


LOW, HIGH = (91, 157, 255), (255, 138, 76)


class ToolpathView(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(300, 240)
        self.setMouseTracking(False)
        self.paths = []           # [(np.array Nx3, extrude)]
        self.total = 0
        self.progress = 1.0
        self.show_travel = True
        self.show_disc = True
        self.message = "Pick a generator and press Generate"
        self.reset_view()

    def reset_view(self):
        self.yaw = math.radians(-35)
        self.pitch = math.radians(28)
        self.zoom = 1.0
        self.update()

    def set_toolpath(self, tp, shift=(0.0, 0.0), offset=(0.0, 0.0)):
        self.paths = []
        dx, dy = shift[0] + offset[0], shift[1] + offset[1]
        if tp:
            for p in tp["paths"]:
                a = np.asarray(p["p"], dtype=float)[:, :3]
                a[:, 0] += dx
                a[:, 1] += dy
                self.paths.append((a, p["e"]))
        self.total = sum(len(a) for a, _ in self.paths)
        self.message = "" if self.paths else self.message
        self.update()

    def set_message(self, text):
        self.message = text
        self.paths = []
        self.update()

    # ---------------------------------------------------------------- interaction
    def mousePressEvent(self, e):
        self._drag = e.position()

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag", None) is None:
            return
        d = e.position() - self._drag
        self._drag = e.position()
        self.yaw += d.x() * 0.01
        self.pitch = max(-1.4, min(1.45, self.pitch + d.y() * 0.01))
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.reset_view()

    def wheelEvent(self, e):
        self.zoom = max(0.2, min(20.0, self.zoom * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15)))
        self.update()

    # ---------------------------------------------------------------- projection
    def _project(self, a):
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        x = a[:, 0] * cy - a[:, 1] * sy
        y = a[:, 0] * sy + a[:, 1] * cy
        sx = x
        sz = a[:, 2] * cp + y * sp            # screen up
        return sx, sz

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0b0f14"))
        w, h = self.width(), self.height()
        if not self.paths:
            p.setPen(QColor(theme.FAINT))
            p.setFont(QFont(p.font().family(), 11))
            p.drawText(self.rect().adjusted(20, 20, -20, -20), Qt.AlignCenter | Qt.TextWordWrap, self.message)
            return
        allpts = np.concatenate([a for a, _ in self.paths])
        zmin, zmax = float(allpts[:, 2].min()), float(allpts[:, 2].max())
        # frame the part (the disc is drawn for scale and may run off the edges)
        t = np.linspace(0, 2 * math.pi, 145)
        disc = np.stack([DISC_RADIUS * np.cos(t), DISC_RADIUS * np.sin(t), np.zeros_like(t)], axis=1)
        sx, sz = self._project(allpts)
        cxm, czm = (sx.max() + sx.min()) / 2, (sz.max() + sz.min()) / 2
        span = max(sx.max() - sx.min(), sz.max() - sz.min(), 10.0)
        scale = min(w, h) * 0.78 / span * self.zoom
        ox, oy = w / 2 - cxm * scale, h / 2 + czm * scale

        def to_screen(a):
            X, Z = self._project(a)
            return ox + X * scale, oy - Z * scale

        if self.show_disc:
            X, Y = to_screen(disc)
            path = QPainterPath(QPointF(X[0], Y[0]))
            for i in range(1, len(X)):
                path.lineTo(X[i], Y[i])
            p.setPen(QPen(QColor("#2a333e"), 1.2))
            p.setBrush(QColor(18, 24, 32, 160))
            p.drawPath(path)
            ax = np.array([[0, 0, 0], [0, 0, max(zmax, 10)]])
            X, Y = to_screen(ax)
            p.setPen(QPen(QColor("#2a333e"), 1, Qt.DashLine))
            p.drawLine(QPointF(X[0], Y[0]), QPointF(X[1], Y[1]))

        limit = int(self.total * self.progress)
        stride = max(1, self.total // MAX_DRAW_POINTS)
        shown = 0
        zr = max(zmax - zmin, 1e-6)
        last_pt = None
        for a, ext in self.paths:
            if shown >= limit:
                break
            n = min(len(a), limit - shown)
            seg = a[:n]
            shown += len(a)
            if n < 2:
                continue
            if stride > 1 and n > 2 * stride:
                idx = np.r_[np.arange(0, n - 1, stride), n - 1]
                seg = seg[idx]
            X, Y = to_screen(seg)
            if not ext:
                if not self.show_travel:
                    continue
                pen = QPen(QColor("#5b6573"), 0.8, Qt.DashLine)
                p.setPen(pen)
                path = QPainterPath(QPointF(X[0], Y[0]))
                for i in range(1, len(X)):
                    path.lineTo(X[i], Y[i])
                p.drawPath(path)
                continue
            # colour by height, in chunks so long spirals still shade bottom to top
            chunk = 64
            for s in range(0, len(X) - 1, chunk):
                e = min(len(X), s + chunk + 1)
                zt = (float(seg[s:e, 2].mean()) - zmin) / zr
                p.setPen(QPen(_mix(LOW, HIGH, zt), 1.4))
                path = QPainterPath(QPointF(X[s], Y[s]))
                for i in range(s + 1, e):
                    path.lineTo(X[i], Y[i])
                p.drawPath(path)
            last_pt = (X[-1], Y[-1])
        if last_pt is not None and self.progress < 1.0:
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(QColor(theme.ACCENT))
            p.drawEllipse(QPointF(*last_pt), 5, 5)
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont(p.font().family(), 9))
        p.drawText(QRectF(12, h - 26, w - 24, 20), Qt.AlignLeft,
                   "Drag to orbit · scroll to zoom · double-click to reset")
        p.drawText(QRectF(12, 8, w - 24, 20), Qt.AlignRight,
                   f"z {zmin:.1f} to {zmax:.1f} mm")
