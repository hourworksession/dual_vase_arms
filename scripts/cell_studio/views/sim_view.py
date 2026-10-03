"""Plays back a planned print: the disc turns, the nozzle moves, the part builds up.

Top-down view from above the disc. The part is drawn in the disc's own frame, so it
turns with the disc. The nozzle is drawn where the arm actually puts it (world frame).
Everything comes from the MotionProgram the planner made, so what you watch is what
would be streamed to the cell; no hardware is needed.

Drag the time slider to scrub; the speed buttons replay faster than real time.
Earlier layers fade so the current one stands out.
"""

import math

import numpy as np
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPixmap, QPainterPath
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSlider

from .. import theme
from ..widgets import button, label, Segmented, hrow
from ..state import DoubleVar

DISC_R = 150.0
CHUNK = 1500                     # steps per live-drawn chunk
KEEP = 40                        # finished chunks kept visible (older ones have faded out)
PIX = 1200                       # size of the off-screen image the part is drawn into


def _mmss(t):
    t = max(0, int(t))
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"


class _Canvas(QWidget):
    def __init__(self, owner):
        super().__init__()
        self.o = owner
        self.setMinimumSize(300, 240)
        self.zoom = 1.0

    def wheelEvent(self, e):
        self.zoom = max(0.3, min(12.0, self.zoom * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15)))
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.zoom = 1.0
        self.update()

    def paintEvent(self, _):
        o = self.o
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#0b0f14"))
        w, h = self.width(), self.height()
        if o.n == 0:
            p.setPen(QColor(theme.FAINT))
            p.setFont(QFont(p.font().family(), 11))
            p.drawText(self.rect().adjusted(20, 20, -20, -20), Qt.AlignCenter | Qt.TextWordWrap,
                       "Slice (or generate) and plan a part, then press Play to watch the print.")
            p.end()
            return
        k = o.k
        cx, cy = w / 2, h / 2
        sppm = min(w, h) / 2 * 0.9 / o.view_r * self.zoom          # screen px per mm
        phi = float(o.phi[k]) if o.use_tt else 0.0
        # disc (turns with the plate)
        p.save()
        p.translate(cx, cy)
        p.rotate(-math.degrees(phi))
        R = DISC_R * sppm
        p.setPen(QPen(QColor("#2a333e"), 1.2))
        p.setBrush(QColor(18, 24, 32))
        p.drawEllipse(QPointF(0, 0), R, R)
        p.setPen(QPen(QColor("#1f2731"), 1))
        for i in range(12):
            a = i * math.pi / 6
            p.drawLine(QPointF(0, 0), QPointF(R * math.cos(a), -R * math.sin(a)))
        p.setPen(QPen(QColor(theme.ACCENT), 3))
        p.drawLine(QPointF(R * 0.92, 0), QPointF(R, 0))                 # 0° mark, to see it turn
        # deposited part (drawn into a plate-frame image, then turned with the disc)
        s = sppm / o.ppm
        p.scale(s, s)
        p.drawPixmap(QPointF(-PIX / 2, -PIX / 2), o.pix)
        p.translate(-PIX / 2, -PIX / 2)
        for pen, path in o.current_paths():
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.restore()
        # nozzle(s), in the fixed world frame
        for a in range(o.arms):
            if not o.valid[a][k]:
                continue
            wx, wy = o.wx[a][k], o.wy[a][k]
            X, Y = cx + wx * sppm, cy - wy * sppm
            col = QColor(theme.RIGHT if a == 0 else theme.LEFT)
            if o.ext[a][k]:
                glow = QColor(col)
                glow.setAlpha(70)
                p.setPen(Qt.NoPen)
                p.setBrush(glow)
                p.drawEllipse(QPointF(X, Y), 13, 13)
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(col)
            p.drawEllipse(QPointF(X, Y), 6, 6)
        p.setPen(QColor(theme.MUTED))
        p.setFont(QFont(p.font().family(), 9))
        p.drawText(QRectF(12, h - 24, w - 24, 18), Qt.AlignLeft,
                   "View from above · scroll to zoom · double-click to reset")
        p.end()


class SimulationView(QWidget):
    def __init__(self):
        super().__init__()
        self.n = 0
        self.k = 0
        self.t = 0.0
        self.playing = False
        self.speed = DoubleVar(10.0)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.canvas = _Canvas(self)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        v.addWidget(self.canvas, 1)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(self._scrub)
        self.play_btn = button("▶  Play", None, self.toggle)
        self.play_btn.setMinimumWidth(96)
        self.time_lbl = label("00:00 / 00:00", "Muted")
        self.time_lbl.setStyleSheet(f"font-family:{theme.FONT_MONO}; font-size:12px;")
        v.addWidget(hrow(self.play_btn, self.slider, self.time_lbl, spacing=10))
        v.addWidget(hrow(label("Speed", "Muted"),
                         Segmented(self.speed, [(1.0, "1×"), (10.0, "10×"), (50.0, "50×"), (200.0, "200×")]),
                         None, spacing=10))
        self.info = label("", "Muted")
        self.info.setStyleSheet(f"font-family:{theme.FONT_MONO}; font-size:12px;")
        self.info.setWordWrap(True)
        v.addWidget(self.info)
        self.pix = QPixmap(PIX, PIX)
        self.pix.fill(Qt.transparent)

    # ---------------------------------------------------------------- data
    def set_program(self, prog):
        self.stop()
        steps = prog.steps if prog is not None else []
        self.n = len(steps)
        if not self.n:
            self._update_info()
            self.canvas.update()
            return
        cfg = prog.config
        self.arms = max(len(s.arms) for s in steps)
        self.use_tt = bool(cfg.use_turntable)
        cx, cy = cfg.center[0], cfg.center[1]
        dt = np.array([s.dt for s in steps])
        self.t_end = np.cumsum(dt)
        self.total = float(self.t_end[-1])
        self.phi = np.radians([s.tt_angle_deg for s in steps])
        self.layer = np.array([s.layer for s in steps])
        self.lw = cfg.line_width
        self.wx, self.wy, self.z, self.px, self.py, self.ext, self.valid = [], [], [], [], [], [], []
        for a in range(self.arms):
            ats = [s.arms[a] if a < len(s.arms) else None for s in steps]
            ok = np.array([at is not None for at in ats])
            wx = np.array([at.x - cx if at else np.nan for at in ats])
            wy = np.array([at.y - cy if at else np.nan for at in ats])
            z = np.array([at.z - cfg.z_base if at else np.nan for at in ats])
            ex = np.array([bool(at and at.extrude) for at in ats])
            c, s_ = np.cos(-self.phi), np.sin(-self.phi)
            self.px.append(wx * c - wy * s_)                      # plate = Rz(−phi) · (world − centre)
            self.py.append(wx * s_ + wy * c)
            self.wx.append(wx)
            self.wy.append(wy)
            self.z.append(z)
            self.ext.append(ex)
            self.valid.append(ok)
        r = np.nanmax([np.nanmax(np.hypot(self.px[a], self.py[a])) for a in range(self.arms)])
        self.view_r = float(max(r * 1.25, 30.0))
        self.ppm = PIX / 2 / self.view_r                        # image px per mm
        self.zmax = float(np.nanmax([np.nanmax(self.z[a]) for a in range(self.arms)]))
        self.layers = int(self.layer.max()) + 1 if self.layer.max() >= 0 else 1
        # contiguous runs of the same layer number (travel steps carry layer -1 or repeat)
        # (long runs, e.g. a whole spiral vase, are cut into chunks so redraws stay quick)
        change = np.flatnonzero(np.diff(self.layer)) + 1
        bounds = [0] + change.tolist() + [self.n]
        cut = []
        for b0, b1 in zip(bounds[:-1], bounds[1:]):
            cut.extend(range(b0 + CHUNK, b1, CHUNK))
        change = np.array(sorted(change.tolist() + cut), dtype=int)
        starts = np.concatenate([[0], change])
        ends = np.concatenate([change - 1, [self.n - 1]])
        self.layer_span = list(zip(starts.tolist(), ends.tolist()))
        self.layer_ix = np.repeat(np.arange(len(starts)), ends - starts + 1)
        self._reset_pix()
        self.k = 0
        self.t = 0.0
        self.slider.setValue(0)
        self._update_info()
        self.canvas.update()

    def _reset_pix(self):
        self.pix.fill(Qt.transparent)
        self.pix_empty = True
        self.base_layer = 0            # layers below this one are already in self.pix
        self._paths = {}               # (arm, layer) -> QPainterPath of that layer's extrusion

    def _pen(self, a, alpha=255):
        col = QColor(theme.RIGHT if a == 0 else theme.LEFT) if self.arms > 1 else QColor("#7fd4ff")
        col.setAlpha(alpha)
        pen = QPen(col, max(1.0, self.lw * self.ppm))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        return pen

    def _path(self, a, i0, i1):
        """Extruded segments of arm a between steps i0..i1 (inclusive), in image pixels."""
        ex, vx = self.ext[a], self.valid[a]
        X = PIX / 2 + self.px[a] * self.ppm
        Y = PIX / 2 - self.py[a] * self.ppm
        path = QPainterPath()
        pen_down = False
        for i in range(max(i0, 1), i1 + 1):
            if ex[i] and vx[i] and vx[i - 1]:
                if not pen_down:
                    path.moveTo(X[i - 1], Y[i - 1])
                    pen_down = True
                path.lineTo(X[i], Y[i])
            else:
                pen_down = False
        return path

    def _layer_path(self, a, L):
        key = (a, L)
        if key not in self._paths:
            i0, i1 = self.layer_span[L]
            self._paths[key] = self._path(a, i0, i1)
        return self._paths[key]

    def _draw_until(self, k):
        """Bake every finished layer below step k's layer into the plate-frame image (dimmed).
        The layer being printed is drawn live in paintEvent."""
        L = int(self.layer_ix[k])
        if L == self.base_layer:
            return
        if L < self.base_layer or L - self.base_layer > KEEP:
            # Jumped: rebuild. Layers more than KEEP below have faded out, so skip them.
            self.pix.fill(Qt.transparent)
            self.pix_empty = True
            self.base_layer = max(0, L - KEEP)
        p = QPainter(self.pix)
        p.setRenderHint(QPainter.Antialiasing)
        for lay in range(self.base_layer, L):
            real = self.layer[self.layer_span[lay][0]]
            new_layer = lay > 0 and real >= 0 and real != self.layer[self.layer_span[lay - 1][0]]
            if new_layer and not self.pix_empty:     # older layers fade so the one being printed stands out
                p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
                p.fillRect(0, 0, PIX, PIX, QColor(0, 0, 0, 215))
                p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            for a in range(self.arms):
                p.setPen(self._pen(a, 140))          # finished layers dimmer than the live one
                p.drawPath(self._layer_path(a, lay))
        p.end()
        self.base_layer = L
        self.pix_empty = False

    def current_paths(self):
        """(pen, path) for the part of the current layer printed so far."""
        k = self.k
        i0 = self.layer_span[int(self.layer_ix[k])][0]
        return [(self._pen(a), self._path(a, i0, k)) for a in range(self.arms)]

    # ---------------------------------------------------------------- playback
    def toggle(self):
        if self.n == 0:
            return
        if self.playing:
            self.stop()
        else:
            if self.k >= self.n - 1:
                self.t = 0.0
                self.k = 0
            self.playing = True
            self.play_btn.setText("❚❚  Pause")
            self._timer.start(33)

    def stop(self):
        self.playing = False
        self._timer.stop()
        self.play_btn.setText("▶  Play")

    def _tick(self):
        self.t = min(self.total, self.t + 0.033 * self.speed.get())
        self._seek_time(self.t)
        if self.t >= self.total:
            self.stop()

    def _scrub(self, v):
        self._seek_time(self.total * v / 1000)

    def _seek_time(self, t):
        self.t = t
        self.k = int(min(self.n - 1, np.searchsorted(self.t_end, t)))
        self._draw_until(self.k)
        if not self.slider.isSliderDown():
            self.slider.setValue(int(1000 * t / self.total) if self.total else 0)
        self._update_info()
        self.canvas.update()

    def _update_info(self):
        if self.n == 0:
            self.time_lbl.setText("00:00 / 00:00")
            self.info.setText("")
            return
        k = self.k
        self.time_lbl.setText(f"{_mmss(self.t)} / {_mmss(self.total)}")
        parts = [f"layer {self.layer[k] + 1}/{self.layers}",
                 f"disc {math.degrees(self.phi[k]) % 360:6.1f}°"]
        for a in range(self.arms):
            if self.valid[a][k]:
                tag = "right" if a == 0 else "left"
                parts.append(f"{tag} arm  r {math.hypot(self.wx[a][k], self.wy[a][k]):.1f}  z {self.z[a][k]:.2f} mm  "
                             f"{'● extruding' if self.ext[a][k] else '○ travel'}")
        self.info.setText("   ·   ".join(parts[:2]) + "\n" + "\n".join(parts[2:]))
