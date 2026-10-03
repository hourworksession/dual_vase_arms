"""Live 3D view of the cell: both UFACTORY 850s, the disc, and the part being printed.

The arms are the real 850 shapes (low-poly) on UFACTORY's joint chain, placed
where the calibration puts them. They follow the machine as it moves:
  * real arms report their joint angles, which are drawn exactly;
  * simulated arms report only their pose, so the joints are solved (IK).
The disc turns with the turntable angle. While a print is running in Model print
or Generators, the plan printed so far is drawn on the disc.

Drawn with QPainter (no OpenGL needed): drag to orbit, right-drag or shift-drag to
pan, scroll to zoom, double-click to reset.
"""

import math
import threading

import numpy as np
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPolygonF, QBrush
from PySide6.QtWidgets import QWidget, QVBoxLayout

from .. import theme, arm_model as am
from ..geometry import CellGeometry
from ..widgets import label, hrow, Check
from ..state import BoolVar

DISC_R = 150.0
SIDE_TINT = {"left": QColor(theme.LEFT), "right": QColor(theme.RIGHT)}


class _ArmState:
    def __init__(self):
        self.q = np.radians(am.HOME_JOINTS_DEG)
        self.pose = None
        self.ok = False


class Cell3DView(QWidget):
    def __init__(self, ctl, part_source=None, title=True):
        super().__init__()
        self.c = ctl
        self.geom = CellGeometry(ctl)
        self.part_source = part_source          # callable -> SimulationView.part_so_far() result, or None
        self.arms = {"left": _ArmState(), "right": _ArmState()}
        self.tt_deg = 0.0
        self.yaw, self.elev, self.dist = math.radians(-60), math.radians(28), 1900.0
        self.target = np.array([0.0, 0.0, 120.0])
        self._drag = None
        self.setMinimumSize(320, 260)
        self.setMouseTracking(False)
        self.show_part = BoolVar(True)
        self.info = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addStretch(1)
        if title:
            self.hint = label("", "Muted")
            v.addWidget(hrow(self.hint, None, Check("Part so far", self.show_part), spacing=10))
        else:
            self.hint = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(66)                    # ~15 frames a second while visible

    # ------------------------------------------------------------ state
    def _tick(self):
        if not self.isVisible():
            return
        live = getattr(self.c, "live", None) or {}
        sim = bool(self.c.conn_simulated.get())
        for side, st in self.arms.items():
            pose = live.get(f"{side}_pose")
            joints = live.get(f"{side}_joints")
            if joints is not None and not sim:
                st.q = np.radians(joints[:6])
                st.ok = True
            elif pose is not None and (st.pose is None or any(abs(a - b) > 1e-3 for a, b in zip(pose, st.pose))):
                q, _, ok = am.ik(pose, st.q)
                if not ok:                         # try again from the home posture
                    q2, _, ok2 = am.ik(pose, np.radians(am.HOME_JOINTS_DEG))
                    if ok2:
                        q, ok = q2, ok2
                st.q, st.ok = q, ok
            st.pose = pose
        if live.get("tt_deg") is not None:
            self.tt_deg = float(live["tt_deg"])
        self.update()

    # ------------------------------------------------------------ camera
    def _camera(self, w, h):
        ce, se = math.cos(self.elev), math.sin(self.elev)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        eye = self.target + self.dist * np.array([ce * cy, ce * sy, se])
        f = self.target - eye
        f /= np.linalg.norm(f)
        r = np.cross(f, [0, 0, 1.0])
        r /= np.linalg.norm(r)
        u = np.cross(r, f)
        focal = 0.5 * min(w, h) / math.tan(math.radians(22))
        return eye, r, u, f, focal

    def _project(self, P, cam, w, h):
        """World points (..., 3) -> screen x, y and depth."""
        eye, r, u, f, focal = cam
        d = P - eye
        x, y, z = d @ r, d @ u, d @ f
        z = np.maximum(z, 1.0)
        return w / 2 + focal * x / z, h / 2 - focal * y / z, z

    # ------------------------------------------------------------ input
    def mousePressEvent(self, e):
        self._drag = (e.position(), e.button(), e.modifiers())

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        p0, btn, mods = self._drag
        d = e.position() - p0
        self._drag = (e.position(), btn, mods)
        if btn == Qt.RightButton or mods & Qt.ShiftModifier:
            _, r, u, _, focal = self._camera(self.width(), self.height())
            k = self.dist / focal
            self.target = self.target - r * d.x() * k + u * d.y() * k
        else:
            self.yaw -= d.x() * 0.008
            self.elev = max(math.radians(-10), min(math.radians(89), self.elev + d.y() * 0.008))
        self.update()

    def wheelEvent(self, e):
        self.dist = max(300.0, min(6000.0, self.dist * (0.88 if e.angleDelta().y() > 0 else 1 / 0.88)))
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.yaw, self.elev, self.dist = math.radians(-60), math.radians(28), 1900.0
        self.target = np.array([0.0, 0.0, 120.0])
        self.update()

    # ------------------------------------------------------------ drawing
    def _arm_world(self, side):
        """4x4: arm base frame -> world (mm), from the calibration (centre, base yaw)."""
        cx, cy, cz = self.geom.centre(side)
        a = math.radians(self.geom.yaw[side])
        c, s = math.cos(a), math.sin(a)
        T = np.eye(4)
        T[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
        T[:3, 3] = [-(c * cx - s * cy), -(s * cx + c * cy), -cz]
        return T

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#0b0f14"))
        cam = self._camera(w, h)
        self._draw_floor(p, cam, w, h)
        self._draw_disc(p, cam, w, h)
        if self.show_part.get() and self.part_source is not None:
            self._draw_part(p, cam, w, h)
        self._draw_arms(p, cam, w, h)
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont(p.font().family(), 9))
        msg = "Drag to orbit · right-drag to pan · scroll to zoom · double-click to reset"
        if not self.c.hw_connected:
            msg = "Not connected: arms shown at home. Connect (or tick Simulated hardware) to see them move.  ·  " + msg
        p.drawText(QRectF(12, 8, w - 24, 18), Qt.AlignLeft, msg)
        if self.hint is not None:
            parts = []
            for side in ("right", "left"):
                st = self.arms[side]
                if st.pose is not None:
                    parts.append(f"{side}: " + ("ok" if st.ok else "pose out of reach?"))
            self.hint.setText(f"Disc {self.tt_deg % 360:.1f}°   " + "   ".join(parts))
        p.end()

    def _draw_floor(self, p, cam, w, h):
        pen = QPen(QColor("#151b23"), 1)
        p.setPen(pen)
        ext, step = 900, 100
        zf = -float(self.geom.centre("right")[2])          # table level ≈ arm base level
        for k in range(-ext, ext + 1, step):
            for a, b in (((k, -ext), (k, ext)), ((-ext, k), (ext, k))):
                P = np.array([[a[0], a[1], zf], [b[0], b[1], zf]], float)
                x, y, _ = self._project(P, cam, w, h)
                p.drawLine(QPointF(x[0], y[0]), QPointF(x[1], y[1]))

    def _draw_disc(self, p, cam, w, h):
        n = 72
        a = np.linspace(0, 2 * math.pi, n, endpoint=False)
        P = np.stack([DISC_R * np.cos(a), DISC_R * np.sin(a), np.zeros(n)], 1)
        x, y, _ = self._project(P, cam, w, h)
        poly = QPolygonF([QPointF(float(i), float(j)) for i, j in zip(x, y)])
        p.setPen(QPen(QColor("#3a4654"), 1.2))
        p.setBrush(QColor(40, 52, 66, 200))
        p.drawPolygon(poly)
        phi = math.radians(self.tt_deg)
        m = np.array([[0.8 * DISC_R * math.cos(phi), 0.8 * DISC_R * math.sin(phi), 0.5],
                      [DISC_R * math.cos(phi), DISC_R * math.sin(phi), 0.5]])
        x, y, _ = self._project(m, cam, w, h)
        p.setPen(QPen(QColor(theme.ACCENT), 3))
        p.drawLine(QPointF(x[0], y[0]), QPointF(x[1], y[1]))

    def _draw_part(self, p, cam, w, h):
        src = self.part_source()
        if src is None:
            return
        xy, z, ext, idx = src
        if len(xy) < 2:
            return
        phi = math.radians(self.tt_deg)          # the part sits on the disc and turns with it
        c, s = math.cos(phi), math.sin(phi)
        P = np.stack([xy[:, 0] * c - xy[:, 1] * s, xy[:, 0] * s + xy[:, 1] * c, z], 1)
        x, y, _ = self._project(P, cam, w, h)
        draw = ext[1:] & (np.diff(idx) <= 12)     # extruding, and not across a skipped stretch
        zmax = float(z.max()) if len(z) else 1.0
        p.setBrush(Qt.NoBrush)
        # older (lower) parts darker so the shape reads in depth
        for band, alpha in ((z[1:] < zmax - 3.0, 110), (z[1:] >= zmax - 3.0, 255)):
            sel = np.flatnonzero(draw & band)
            if not len(sel):
                continue
            col = QColor("#7fd4ff")
            col.setAlpha(alpha)
            p.setPen(QPen(col, 1.6))
            from PySide6.QtCore import QLineF
            p.drawLines([QLineF(x[i], y[i], x[i + 1], y[i + 1]) for i in sel])

    def _draw_arms(self, p, cam, w, h):
        meshes = am.meshes()
        if not meshes:
            return
        tris, cols = [], []
        light = np.array([0.35, -0.45, 0.82])
        light /= np.linalg.norm(light)
        eye = cam[0]
        for side, st in self.arms.items():
            if not self._arm_present(side):
                continue
            W = self._arm_world(side)
            for T, tri in zip(am.fk(st.q), meshes):
                M = W @ T
                V = tri @ M[:3, :3].T + M[:3, 3]
                n = np.cross(V[:, 1] - V[:, 0], V[:, 2] - V[:, 0])
                nl = np.linalg.norm(n, axis=1)
                ok = nl > 1e-9
                V, n = V[ok], n[ok] / nl[ok, None]
                facing = np.einsum("ij,ij->i", n, eye - V[:, 0]) > 0      # back faces are hidden
                V, n = V[facing], n[facing]
                shade = 0.32 + 0.68 * np.clip(n @ light, 0, 1)
                tris.append(V)
                cols.append(np.stack([shade, np.full(len(shade), 0 if side == "left" else 1)], 1))
        if not tris:
            return
        V = np.concatenate(tris)
        C = np.concatenate(cols)
        x, y, z = self._project(V.reshape(-1, 3), cam, w, h)
        x, y, z = x.reshape(-1, 3), y.reshape(-1, 3), z.reshape(-1, 3).mean(1)
        order = np.argsort(-z)
        p.setPen(Qt.NoPen)
        base = np.array([232, 234, 238], float)
        tint = {0: np.array([91, 157, 255], float), 1: np.array([255, 138, 76], float)}
        for i in order:
            sh, sd = C[i]
            col = (0.88 * base + 0.12 * tint[int(sd)]) * sh
            q = QColor(int(col[0]), int(col[1]), int(col[2]))
            p.setBrush(q)
            p.setPen(QPen(q, 0.6))              # hides hairline gaps between triangles
            p.drawPolygon(QPolygonF([QPointF(x[i, 0], y[i, 0]), QPointF(x[i, 1], y[i, 1]),
                                     QPointF(x[i, 2], y[i, 2])]))

    def _arm_present(self, side):
        return True
