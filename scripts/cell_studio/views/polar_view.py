"""Polar toolpath preview and turntable simulator for the cylinder job.

Same maths as the Tk canvas in gleadell_panel.py (update_preview, _sim_step),
drawn with QPainter. The preview redraws itself whenever a parameter changes.
Click the plot to add a node, click near an existing node to remove it.
"""

import math

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QPainterPath, QFont, QBrush
from PySide6.QtWidgets import QWidget

from .. import theme


class PolarView(QWidget):
    sim_state = Signal(bool)          # True while the simulation runs

    FRAME_MS = 20

    def __init__(self, ctl):
        super().__init__()
        self.c = ctl
        self.setMinimumSize(360, 320)
        self.setCursor(Qt.CrossCursor)
        self.sim_running = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._sim_step)
        watch = list(ctl.param_vars.values()) + [
            ctl.pattern_enabled, ctl.pattern_waveform, ctl.pattern_amplitude,
            ctl.pattern_wave_count, ctl.pattern_phase_offset, ctl.pattern_arm_left,
            ctl.pattern_arm_right, ctl.nodes_changed]
        for v in watch:
            v.changed.connect(self._param_changed)

    # ------------------------------------------------------------ helpers
    def _p(self):
        c = self.c
        g = lambda v, d=0.0: (lambda x: x if isinstance(x, (int, float)) else d)(v.get())
        return dict(
            radius=g(c.param_vars['radius']), rl=g(c.param_vars['radial_offset_left']),
            rr=g(c.param_vars['radial_offset_right']), amp=g(c.pattern_amplitude),
            wc=g(c.pattern_wave_count), ph=math.radians(g(c.pattern_phase_offset)),
            wf=c.pattern_waveform.get(), on=bool(c.pattern_enabled.get()),
            revs=g(c.param_vars['total_revs']), speed=g(c.turntable_speed_var),
            arm_l=bool(c.pattern_arm_left.get()), arm_r=bool(c.pattern_arm_right.get()))

    def _param_changed(self, *_):
        if not self.sim_running:
            self.trail_l, self.trail_r = [], []   # editing returns to the node editor
            self.update()

    def _bg(self, p):
        p.fillRect(self.rect(), QColor("#0b0f14"))

    # ------------------------------------------------------------ interaction
    def mousePressEvent(self, e):
        if self.sim_running or e.button() != Qt.LeftButton:
            return
        w, h = self.width(), self.height()
        dx = e.position().x() - w / 2
        dy = e.position().y() - h / 2
        if math.hypot(dx, dy) < 20:
            return
        theta = math.atan2(-dy, dx)
        if theta < 0:
            theta += 2 * math.pi
        self.c.toggle_node(theta)

    # ------------------------------------------------------------ simulation
    def toggle_simulation(self):
        if self.sim_running:
            self.stop_simulation()
        else:
            self.start_simulation()

    def start_simulation(self):
        p = self._p()
        if p['revs'] <= 0:
            return False
        self.sim_phi = 0.0
        self.sim_total = p['revs'] * 2 * math.pi
        self.trail_l, self.trail_r = [], []
        self.sim_running = True
        self.setCursor(Qt.ArrowCursor)
        self._timer.start(self.FRAME_MS)
        self.sim_state.emit(True)
        return True

    def stop_simulation(self):
        if self.sim_running:
            self.sim_running = False
            self._timer.stop()
            self.setCursor(Qt.CrossCursor)
            self.sim_state.emit(False)
            self.update()

    def _sim_step(self):
        p = self._p()
        speed = p['speed'] if p['speed'] > 0 else 0.6   # keep the preview turning even at 0
        dphi = speed * self.FRAME_MS / 1000.0
        self.sim_phi += dphi
        cap = int((2 * math.pi / dphi) * 1.05) + 2 if dphi > 0 else 4000
        for world, off, arm_on, trail in ((math.pi, p['rl'], p['arm_l'], self.trail_l),
                                          (0.0, p['rr'], p['arm_r'], self.trail_r)):
            pa = world - self.sim_phi
            wv = self._wave(pa, p) if arm_on else 0.0
            trail.append((pa, p['radius'] + off + wv))
            if len(trail) > cap:
                del trail[0:len(trail) - cap]
        if self.sim_phi >= self.sim_total:
            self._timer.stop()
            self.sim_running = False
            self.sim_state.emit(False)
            self._last_sim_done = True
        self.update()

    def _wave(self, pa, p):
        if not p['on']:
            return 0.0
        return self.c.wave_value(pa * p['wc'] + p['ph'], p['amp'], p['wf'])

    # ------------------------------------------------------------ painting
    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self._bg(painter)
        if self.sim_running or getattr(self, "trail_l", None):
            self._paint_sim(painter)
        else:
            self._paint_preview(painter)
        painter.end()

    def _legend(self, pnt, extra=""):
        pnt.setFont(QFont(pnt.font().family(), 9))
        y = 18
        for col, txt in ((theme.LEFT, "Left arm"), (theme.RIGHT, "Right arm")):
            pnt.setPen(Qt.NoPen)
            pnt.setBrush(QColor(col))
            pnt.drawEllipse(QPointF(18, y), 4, 4)
            pnt.setPen(QColor(theme.MUTED))
            pnt.drawText(QPointF(28, y + 4), txt)
            y += 18
        if extra:
            pnt.setPen(QColor(theme.MUTED))
            pnt.drawText(QPointF(14, self.height() - 12), extra)

    def _paint_preview(self, pnt):
        p = self._p()
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        gmax = min(w, h) / 2 - 40
        radius = p['radius']
        scale = gmax / radius if radius > 0 else 1.0

        grid = QPen(QColor("#1f2731"), 1)
        pnt.setPen(grid)
        pnt.setBrush(Qt.NoBrush)
        for i in range(1, 5):
            r = gmax / 4 * i
            pnt.drawEllipse(QPointF(cx, cy), r, r)
        for i in range(12):
            a = i * math.pi / 6
            pnt.drawLine(QPointF(cx, cy), QPointF(cx + gmax * math.cos(a), cy - gmax * math.sin(a)))
        # angle labels every 90 degrees
        pnt.setPen(QColor(theme.FAINT))
        pnt.setFont(QFont(pnt.font().family(), 8))
        for deg in (0, 90, 180, 270):
            a = math.radians(deg)
            pnt.drawText(QPointF(cx + (gmax + 14) * math.cos(a) - 8, cy - (gmax + 14) * math.sin(a) + 4), f"{deg}°")

        nodes = sorted(self.c.polar_nodes)
        if not nodes:
            self._legend(pnt, "No nodes: click the plot to add some")
            return

        def pts(off):
            out = []
            for th in nodes:
                pat = self.c.wave_value(th * p['wc'] + p['ph'], p['amp'], p['wf']) if p['on'] else 0.0
                r = (radius + off + pat) * scale
                out.append(QPointF(cx + r * math.cos(th), cy - r * math.sin(th)))
            return out

        for off, col in ((p['rr'], theme.RIGHT), (p['rl'], theme.LEFT)):
            ps = pts(off)
            if len(ps) >= 2:
                path = QPainterPath(ps[0])
                for q in ps[1:]:
                    path.lineTo(q)
                pen = QPen(QColor(col), 2)
                if col == theme.LEFT:
                    pen.setStyle(Qt.DashLine)   # stays visible when both paths coincide
                pnt.setPen(pen)
                pnt.setBrush(Qt.NoBrush)
                pnt.drawPath(path)
            pnt.setPen(QPen(QColor("#0b0f14"), 1))
            pnt.setBrush(QColor(col))
            for q in ps:
                pnt.drawEllipse(q, 3.2, 3.2)

        pnt.setPen(QColor(theme.MUTED))
        pnt.setFont(QFont(pnt.font().family(), 10))
        pnt.drawText(QRectF(cx - 80, cy - 12, 160, 24), Qt.AlignCenter, f"{len(nodes)} nodes")
        self._legend(pnt, "Click to add a node · click a node to remove it")

    def _paint_sim(self, pnt):
        p = self._p()
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        gmax = min(w, h) / 2 - 30
        denom = p['radius'] + abs(p['amp']) + 8 if (p['radius'] + abs(p['amp'])) > 0 else 1.0
        scale = gmax / denom
        phi = self.sim_phi
        edge = p['radius'] * scale + max(8.0, abs(p['amp']) * scale + 6.0)

        pnt.setPen(QPen(QColor("#2a333e"), 1))
        pnt.setBrush(QColor("#121820"))
        pnt.drawEllipse(QPointF(cx, cy), edge, edge)
        pnt.setPen(QPen(QColor("#1f2731"), 1))
        for k in range(8):   # spokes rotate with the plate
            a = phi + k * math.pi / 4
            pnt.drawLine(QPointF(cx, cy), QPointF(cx + edge * math.cos(a), cy - edge * math.sin(a)))
        pnt.setPen(Qt.NoPen)
        pnt.setBrush(QColor("#3a4452"))
        pnt.drawEllipse(QPointF(cx, cy), 5, 5)

        for trail, col in ((self.trail_l, theme.LEFT), (self.trail_r, theme.RIGHT)):
            if len(trail) >= 2:
                path = QPainterPath()
                for i, (pa, r_mm) in enumerate(trail):
                    wa = pa + phi
                    q = QPointF(cx + r_mm * scale * math.cos(wa), cy - r_mm * scale * math.sin(wa))
                    path.moveTo(q) if i == 0 else path.lineTo(q)
                pnt.setPen(QPen(QColor(col), 2))
                pnt.setBrush(Qt.NoBrush)
                pnt.drawPath(path)

        for world, off, arm_on, col in ((math.pi, p['rl'], p['arm_l'], theme.LEFT),
                                        (0.0, p['rr'], p['arm_r'], theme.RIGHT)):
            pa = world - phi
            wv = self._wave(pa, p) if arm_on else 0.0
            r_mm = p['radius'] + off + wv
            q = QPointF(cx + r_mm * scale * math.cos(world), cy - r_mm * scale * math.sin(world))
            pnt.setPen(QPen(QColor("white"), 2))
            pnt.setBrush(QColor(col))
            pnt.drawEllipse(q, 9, 9)
            pnt.setPen(Qt.NoPen)
            pnt.setBrush(QColor("white"))
            pnt.drawEllipse(q, 3, 3)

        rev = phi / (2 * math.pi)
        pnt.setPen(QColor(theme.TEXT))
        pnt.setFont(QFont(pnt.font().family(), 11))
        pnt.drawText(QRectF(cx - 90, cy - 14, 180, 28), Qt.AlignCenter,
                     f"{rev:0.1f} / {p['revs']:g} rev")
        self._legend(pnt, "Simulating" if self.sim_running else "Simulation finished · press Clear to return to the node editor")

    def clear_sim(self):
        self.stop_simulation()
        self.trail_l, self.trail_r = [], []
        self.update()
