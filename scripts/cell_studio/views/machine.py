"""Machine page: connections, actions, jog, live offsets, extruder."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QGridLayout, QVBoxLayout, QHBoxLayout, QLabel

from .. import theme
from ..widgets import (Card, NumberField, Check, Segmented, FormGrid, ReadoutField,
                       button, label, hrow, scroll, divider, Dot)


class MachinePage(QWidget):
    def __init__(self, ctl, win):
        super().__init__()
        self.c, self.win = ctl, win
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setContentsMargins(24, 22, 24, 24)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(18)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        grid.addWidget(self._connections(), 0, 0)
        grid.addWidget(self._actions(), 0, 1)
        grid.addWidget(self._jog(), 1, 0)
        grid.addWidget(self._offsets(), 1, 1)
        grid.addWidget(self._extruder(), 2, 0, 1, 2)
        grid.setRowStretch(3, 1)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(scroll(inner))

    # ------------------------------------------------------------------
    def _connections(self):
        c = self.c
        card = Card("Connections", "Choose which devices to connect. The left arm has no extruder "
                                   "at present, so it can stay off.")
        cfg = c.cfg
        rows = [("Left arm", c.conn_left, theme.LEFT, cfg['arms']['left']['ip']),
                ("Right arm", c.conn_right, theme.RIGHT, cfg['arms']['right']['ip']),
                ("Turntable", c.conn_turntable, theme.ACCENT, cfg['turntable']['controller_ip']),
                ("Extruder (Moonraker)", c.conn_extruder, theme.WARN,
                 f"{cfg['moonraker']['host']}:{cfg['moonraker']['port']}")]
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(10)
        for i, (name, var, col, addr) in enumerate(rows):
            g.addWidget(Dot(col, 9), i, 0)
            g.addWidget(Check(name, var), i, 1)
            ip = label(addr, "Muted")
            ip.setStyleSheet(f"font-family: {theme.FONT_MONO}; font-size: 12px;")
            g.addWidget(ip, i, 2, Qt.AlignRight)
        g.setColumnStretch(1, 1)
        card.add_layout(g)
        card.add(divider())
        sim = Check("Simulated hardware (no motion, for rehearsal)", c.conn_simulated)
        card.add(sim)
        card.add(hrow(button("Connect", "primary", c.connect_hw, min_w=140),
                      button("Disconnect", None, c.disconnect_hw), None))
        st = label("", "Muted", wrap=True)
        c.conn_status_var.changed.connect(st.setText)
        st.setText(c.conn_status_var.get())
        card.add(st)
        return card

    def _actions(self):
        c = self.c
        card = Card("Actions")
        home = button("⌂  Home arms", "primary", c.home_all,
                      tip="Moves the arms to the home positions set in Settings ▸ Home positions")
        card.add(home)
        self.home_hint = label("", "CardHint", wrap=True)
        card.add(self.home_hint)
        self.refresh_home_hint()
        card.add(button("Edit home positions…", "ghost", self.win.open_home_dialog))
        card.add(divider())
        card.add(button("Prepare to print", None, c.prepare_to_print,
                        tip="Heats both tools and moves the arms to the pre print pose"))
        card.add(button("Extruders off", "danger", c.extruders_off, tip="Sends CANCEL_PRINT to Klipper"))
        card.v.addStretch(1)
        return card

    def refresh_home_hint(self):
        h = self.c.home
        parts = []
        for side in ("left", "right"):
            a = h[side]
            if not a.get("enabled", True):
                parts.append(f"{side}: xArm factory home")
            elif a.get("mode") == "joint":
                parts.append(f"{side}: joints [" + ", ".join(f"{v:g}" for v in a['joints']) + "]")
            else:
                parts.append(f"{side}: pose [" + ", ".join(f"{v:g}" for v in a['pose']) + "]")
        self.home_hint.setText("  ·  ".join(parts))

    def _jog(self):
        c = self.c
        card = Card("Manual jog", "Relative moves from the current pose at 50 mm/s. Disabled during a print.")
        fg = FormGrid()
        fg.row("Arm", Segmented(c.jog_arm, [("left", "Left"), ("right", "Right"), ("both", "Both")]))
        fg.row("Step", Segmented(c.jog_step, [(0.1, "0.1"), (1.0, "1"), (10.0, "10")]))
        fg.row("Custom step", NumberField(c.jog_step, "mm / °", 2, 0.1, minimum=0.01, maximum=100, width=130))
        card.add_layout(fg)
        card.add(divider())
        pad = QGridLayout()
        pad.setHorizontalSpacing(8)
        pad.setVerticalSpacing(8)
        lin = [("X", "mm"), ("Y", "mm"), ("Z", "mm")]
        rot = [("Roll", "°"), ("Pitch", "°"), ("Yaw", "°")]
        for col, group in ((0, lin), (4, rot)):
            for r, (ax, unit) in enumerate(group):
                name = label(ax, "FieldLabel")
                name.setMinimumWidth(44)
                pad.addWidget(name, r, col)
                pad.addWidget(button("−", "jog", lambda a=ax: c.jog(a, -1)), r, col + 1)
                pad.addWidget(button("+", "jog", lambda a=ax: c.jog(a, +1)), r, col + 2)
        pad.setColumnMinimumWidth(3, 24)
        pad.setColumnStretch(7, 1)
        card.add_layout(pad)
        return card

    def _offsets(self):
        c = self.c
        card = Card("Live offsets", "Added to every move of the cylinder print while it runs.")
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(8)
        for col, (txt, colr) in enumerate((("Left", theme.LEFT), ("Right", theme.RIGHT)), start=1):
            h = QHBoxLayout()
            h.addWidget(Dot(colr, 8))
            h.addWidget(label(txt.upper(), "SectionLabel"))
            h.addStretch(1)
            g.addLayout(h, 0, col)
        for i, ax in enumerate(c.axes, start=1):
            unit = "mm" if ax in ("X", "Y", "Z") else "°"
            g.addWidget(label(ax, "FieldLabel"), i, 0)
            g.addWidget(NumberField(c.offset_vars[f'left_{ax}'], unit, 2, 0.1, width=120), i, 1)
            g.addWidget(NumberField(c.offset_vars[f'right_{ax}'], unit, 2, 0.1, width=120), i, 2)
        g.setColumnStretch(3, 1)
        card.add_layout(g)
        card.add(button("Zero all offsets", "ghost",
                        lambda: [v.set(0.0) for v in c.offset_vars.values()]))
        return card

    def _extruder(self):
        c = self.c
        card = Card("Extruder")
        row = QHBoxLayout()
        row.setSpacing(28)

        prime = FormGrid()
        prime.row("Prime length", NumberField(c.prime_len, "mm", 1, 1.0, minimum=0, width=120))
        prime.addWidget(hrow(button("Prime left", None, lambda: c.prime_extruder('left')),
                             button("Prime right", None, lambda: c.prime_extruder('right')), None),
                        prime._row, 1)
        row.addLayout(prime, 1)

        calc = FormGrid()
        calc.row("Left filament", ReadoutField(c.calc_left_len, "{:.1f}", "mm"))
        calc.row("Right filament", ReadoutField(c.calc_right_len, "{:.1f}", "mm"))
        calc.addWidget(button("Calculate", None, c.calculate_extrusion_lengths,
                              tip="Filament needed for the cylinder as currently set"),
                       calc._row, 1)
        row.addLayout(calc, 1)
        card.add_layout(row)
        return card
