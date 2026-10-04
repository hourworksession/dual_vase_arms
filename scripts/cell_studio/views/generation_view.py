"""Generation tab: watch how the toolpaths are made, and compare planar with spiral.

Spiral mode  : the section's bead rings at the current height (outer and inner), the
               rays from the axis, and the two helices growing as the slider advances.
Planar mode  : one layer, its paths appearing in print order as the slider advances.
Delta mode   : both path sets for a band of height overlaid, with the coverage numbers.
"""

import math

import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPainterPath, QPolygonF
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSlider, QPlainTextEdit

from .. import theme
from ..widgets import label, hrow, Segmented
from ..state import StrVar


class GenerationView(QWidget):
    def __init__(self):
        super().__init__()
        self.spiral = None          # SliceResult from the spiral fit
        self.planar = None          # SliceResult from the planar slicer
        self.delta = None           # dict from compare()
        self.mode = StrVar("spiral")
        self.canvas = _Canvas(self)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(8)
        v.addWidget(self.canvas, 1)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.valueChanged.connect(lambda _: self.canvas.update())
        self.lbl = label("", "Muted")
        v.addWidget(hrow(Segmented(self.mode, [("spiral", "Spiral"), ("planar", "Planar"), ("delta", "Delta")]),
                         self.slider, self.lbl, spacing=10))
        self.mode.changed.connect(lambda _: self.canvas.update())
        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setMaximumHeight(96)
        self.info.setStyleSheet(f"font-family:{theme.FONT_MONO}; font-size:11px;")
        v.addWidget(self.info)

    def set_results(self, spiral=None, planar=None, delta=None):
        if spiral is not None:
            self.spiral = spiral
        if planar is not None:
            self.planar = planar
        if delta is not None:
            self.delta = delta
            self.info.setPlainText(delta.get("text", ""))
        self.canvas.update()

    def frac(self):
        return self.slider.value() / 1000.0


def _bounds(res):
    b = res.bounds
    return float(b[0][0]), float(b[0][1]), float(b[1][0]), float(b[1][1]), float(b[0][2]), float(b[1][2])


class _Canvas(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.o = owner
        self.setMinimumSize(360, 300)

    def paintEvent(self, _):
        o = self.o
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0b0f14"))
        w, h = self.width(), self.height()
        mode = o.mode.get()
        res = o.spiral if mode != "planar" else o.planar
        if res is None:
            p.setPen(QColor(theme.FAINT))
            p.setFont(QFont(p.font().family(), 11))
            p.drawText(self.rect().adjusted(20, 20, -20, -20), Qt.AlignCenter | Qt.TextWordWrap,
                       "Press  Compare planar vs spiral  to generate both and watch them here.")
            p.end()
            return
        x0, y0, x1, y1, z0, z1 = _bounds(res)
        span = max(x1 - x0, y1 - y0, 1.0) * 1.15
        scale = (min(w, h) - 60) / span
        ox, oy = w / 2 - (x0 + x1) / 2 * scale, h / 2 + 10 + (y0 + y1) / 2 * scale
        X = lambda x: ox + x * scale
        Y = lambda y: oy - y * scale
        f = o.frac()
        if mode == "spiral":
            self._spiral(p, res, f, X, Y, scale)
        elif mode == "planar":
            self._planar(p, res, f, X, Y)
        else:
            self._delta(p, f, X, Y)
        p.end()

    # ---------------------------------------------------------------- spiral
    def _spiral(self, p, res, f, X, Y, scale):
        tr = getattr(res, "spiral_trace", None) or {}
        rings = tr.get("rings", [])
        # the helices per arm, as separate runs (never joined across paths)
        runs = {0: [], 1: []}
        for L in res.layers:
            for pth in L.paths:
                a = getattr(pth, "arm", None)
                if a is None or len(pth.points[0]) < 6 or pth.kind == "TRAVEL":
                    continue
                runs[a].append(pth.points)
        helix = [(a, q) for a in runs for pts in runs[a] for q in pts]
        zs = [q[2] for _, q in helix] or [0.0]
        zlo, zhi = min(zs), max(zs)
        zc = zlo + (zhi - zlo) * f
        # rings at this height
        ring = min(rings, key=lambda r: abs(r[0] - zc)) if rings else None
        if ring is not None:
            for coords, col in zip(ring[1], ("#34516b", "#4b3b63")):
                if not coords:
                    continue
                if coords and isinstance(coords[0][0], (tuple, list)):
                    parts = coords                        # several rings
                else:
                    parts = [coords]
                p.setPen(QPen(QColor(col), 1.2, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                for part in parts:
                    p.drawPolygon(QPolygonF([QPointF(X(x), Y(y)) for x, y in part]))
        # helices so far, per arm
        lh = tr.get("lh", 0.6)
        for arm, col in ((0, theme.RIGHT), (1, theme.LEFT)):
            last = None
            for pts_all in runs[arm]:
                pts = [q for q in pts_all if q[2] <= zc]
                if len(pts) < 2:
                    continue
                for lo, hi, alpha, width in ((None, zc - 3 * lh, 60, 1.0), (zc - 3 * lh, None, 255, 2.0)):
                    seg = [q for q in pts if (lo is None or q[2] >= lo) and (hi is None or q[2] < hi)]
                    if len(seg) < 2:
                        continue
                    c = QColor(col)
                    c.setAlpha(alpha)
                    p.setPen(QPen(c, width))
                    p.setBrush(Qt.NoBrush)
                    path = QPainterPath(QPointF(X(seg[0][0]), Y(seg[0][1])))
                    for q in seg[1:]:
                        path.lineTo(X(q[0]), Y(q[1]))
                    p.drawPath(path)
                if last is None or pts[-1][2] >= last[2]:
                    last = pts[-1]
            if last is None:
                continue
            # the ray from the axis to the current bead point
            p.setPen(QPen(QColor(col), 1, Qt.DotLine))
            p.drawLine(QPointF(X(0), Y(0)), QPointF(X(last[0]), Y(last[1])))
            p.setBrush(QColor(col))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(X(last[0]), Y(last[1])), 5, 5)
            p.setPen(QColor(col))
            p.setFont(QFont(p.font().family(), 9))
            p.drawText(QPointF(X(last[0]) + 8, Y(last[1]) + (18 if arm else -6)),
                       f"{'right' if arm == 0 else 'left'}  r {math.hypot(last[0], last[1]):.1f}  z {last[2]:.1f}  lean {last[5]:.0f}°")
        p.setPen(QColor(theme.TEXT))
        p.setFont(QFont(p.font().family(), 11, QFont.DemiBold))
        p.drawText(QPointF(16, 26), f"Spiral fit  ·  z = {zc:.1f} mm  ·  {(zc - zlo) / max(tr.get('lh', 0.6), 1e-6):.0f} turns")
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont(p.font().family(), 9))
        p.drawText(QPointF(16, 44), "dashed: bead rings of the section at this height (outer, inner) · dotted: ray from the axis")
        self.o.lbl.setText(f"z {zc:.1f} mm")

    # ---------------------------------------------------------------- planar
    def _planar(self, p, res, f, X, Y):
        n = len(res.layers)
        i = int(min(n - 1, f * n))
        L = res.layers[i]
        k = int(round(((f * n) - i) * len(L.paths)))
        for j, pth in enumerate(L.paths[:max(1, k)]):
            col = QColor(theme.KIND_COLOR.get(pth.kind, "#888888"))
            if j < k - 1:
                col.setAlpha(140)
            pts = pth.points + ([pth.points[0]] if (pth.closed and len(pth.points) > 1) else [])
            if len(pts) < 2:
                continue
            path = QPainterPath(QPointF(X(pts[0][0]), Y(pts[0][1])))
            for q in pts[1:]:
                path.lineTo(X(q[0]), Y(q[1]))
            p.setPen(QPen(col, 2.0 if j == k - 1 else 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.setPen(QColor(theme.TEXT))
        p.setFont(QFont(p.font().family(), 11, QFont.DemiBold))
        p.drawText(QPointF(16, 26), f"Planar  ·  layer {i + 1}/{n}  z = {L.z:.2f} mm  ·  path {min(k, len(L.paths))}/{len(L.paths)}")
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont(p.font().family(), 9))
        p.drawText(QPointF(16, 44), "paths appear in print order: outer wall, inner wall, skin, infill")
        self.o.lbl.setText(f"layer {i + 1}")

    # ---------------------------------------------------------------- delta
    def _delta(self, p, f, X, Y):
        d = self.o.delta
        if not d:
            p.setPen(QColor(theme.FAINT))
            p.drawText(self.rect(), Qt.AlignCenter, "No comparison yet.")
            return
        zl, zh = d["zmin"], d["zmax"]
        band = 2.0
        zc = zl + (zh - zl) * f
        S = d["spiral_pts"]; P = d["planar_pts"]
        S = S[(S[:, 2] >= zc - band) & (S[:, 2] <= zc + band)]
        P = P[(P[:, 2] >= zc - band) & (P[:, 2] <= zc + band)]
        # where only one of them puts material (plan view, within one line width)
        try:
            from scipy.spatial import cKDTree
            lw = d.get("lw", 1.1)
            p_only = P[cKDTree(S[:, :2]).query(P[:, :2])[0] > lw] if len(S) and len(P) else P
            s_only = S[cKDTree(P[:, :2]).query(S[:, :2])[0] > lw] if len(S) and len(P) else S
        except Exception:
            p_only, s_only = P, S
        for pts, col, width in ((S, "#3a4654", 1.0), (P, "#3a4654", 1.0),
                                (p_only, "#9aa4b0", 2.2), (s_only, theme.RIGHT, 2.2)):
            p.setPen(QPen(QColor(col), width))
            for q in pts:
                p.drawPoint(QPointF(X(q[0]), Y(q[1])))
        p.setPen(QColor(theme.TEXT))
        p.setFont(QFont(p.font().family(), 11, QFont.DemiBold))
        p.drawText(QPointF(16, 26), f"Delta  ·  z = {zc:.1f} ± {band:g} mm")
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont(p.font().family(), 9))
        p.drawText(QPointF(16, 44), f"dark: both lay material here · grey: planar only ({len(p_only)} pts) · "
                                    f"orange: spiral only ({len(s_only)} pts)")
        self.o.lbl.setText(f"z {zc:.1f} mm")
