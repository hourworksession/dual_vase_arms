"""Reusable widgets bound to ``Var`` objects."""

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QDoubleSpinBox, QSpinBox, QCheckBox, QComboBox, QToolButton, QWidget,
    QSizePolicy, QButtonGroup, QLineEdit, QScrollArea,
)

from . import theme


# ---------------------------------------------------------------- helpers
def button(text, kind=None, slot=None, tip=None, min_w=None):
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if slot:
        b.clicked.connect(lambda _=False: slot())
    if tip:
        b.setToolTip(tip)
    if min_w:
        b.setMinimumWidth(min_w)
    b.setCursor(Qt.PointingHandCursor)
    b.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
    b.setMinimumWidth(max(min_w or 0, b.fontMetrics().horizontalAdvance(text) + 40))
    return b


def label(text, obj=None, wrap=False):
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    return lb


def divider():
    d = QFrame()
    d.setObjectName("Divider")
    return d


def hrow(*widgets, spacing=8, stretch_last=False):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            lay.addStretch(1)
        else:
            lay.addWidget(x)
    if stretch_last:
        lay.addStretch(1)
    return w


def scroll(inner):
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sa.setWidget(inner)
    return sa


# ---------------------------------------------------------------- card
class Card(QFrame):
    def __init__(self, title=None, hint=None, parent=None, spacing=12):
        super().__init__(parent)
        self.setObjectName("Card")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(18, 16, 18, 18)
        self.v.setSpacing(spacing)
        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            self.title_lbl = label(title, "CardTitle")
            head.addWidget(self.title_lbl)
            head.addStretch(1)
            self.head_right = head
            self.v.addLayout(head)
            if hint:
                self.v.addWidget(label(hint, "CardHint", wrap=True))

    def add(self, w, stretch=0):
        self.v.addWidget(w, stretch)
        return w

    def add_layout(self, lay):
        self.v.addLayout(lay)
        return lay

    def add_header_widget(self, w):
        self.head_right.addWidget(w)


# ---------------------------------------------------------------- number field
class _NoWheelMixin:
    """Ignore the mouse wheel unless focused, so scrolling a page never nudges a live value."""

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class DSpin(_NoWheelMixin, QDoubleSpinBox):
    pass


class ISpin(_NoWheelMixin, QSpinBox):
    pass


class NoWheelCombo(_NoWheelMixin, QComboBox):
    pass


class NumberField(QWidget):
    """Spin box bound to a Var, optional unit suffix and nudge chips.

    Values commit on Enter / focus out (keyboard tracking off), so a half typed
    number never reaches a print that reads the value live.
    """

    def __init__(self, var, unit="", decimals=2, step=0.1, nudges=(),
                 minimum=-1e6, maximum=1e6, width=120, integer=False, clamp=None):
        super().__init__()
        self.var = var
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        if integer:
            sp = ISpin()
            sp.setRange(int(max(minimum, -2**31 + 1)), int(min(maximum, 2**31 - 1)))
            sp.setSingleStep(int(step) or 1)
        else:
            sp = DSpin()
            sp.setDecimals(decimals)
            sp.setRange(minimum, maximum)
            sp.setSingleStep(step)
        sp.setKeyboardTracking(False)
        sp.setFocusPolicy(Qt.StrongFocus)
        sp.setAlignment(Qt.AlignRight)
        sp.setButtonSymbols(QDoubleSpinBox.NoButtons if nudges else QDoubleSpinBox.UpDownArrows)
        if unit:
            sp.setSuffix(f"  {unit}")
        sp.setFixedWidth(width)
        sp.setValue(var.get())
        sp.valueChanged.connect(var.set)
        var.changed.connect(self._from_var)
        self.spin = sp
        lay.addWidget(sp)
        self._clamp = clamp
        for d in nudges:
            txt = (f"+{d:g}" if d > 0 else f"−{abs(d):g}")
            b = QPushButton(txt)
            b.setProperty("kind", "nudge")
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, dv=d: self.nudge(dv))
            lay.addWidget(b)
        lay.addStretch(1)

    def nudge(self, d):
        try:
            nv = round(float(self.var.get()) + d, 4)
        except (TypeError, ValueError):
            nv = 0.0
        if self._clamp:
            nv = max(self._clamp[0], min(self._clamp[1], nv))
        self.var.set(nv)

    def _from_var(self, v):
        if self.spin.value() != v:
            self.spin.blockSignals(True)
            self.spin.setValue(v)
            self.spin.blockSignals(False)


class ReadoutField(QLineEdit):
    """Read only mono field showing a Var."""

    def __init__(self, var, fmt="{:.1f}", unit="", width=120):
        super().__init__()
        self.setReadOnly(True)
        self.setFixedWidth(width)
        self.setAlignment(Qt.AlignRight)
        self._fmt, self._unit = fmt, unit
        var.changed.connect(self._show)
        self._show(var.get())

    def _show(self, v):
        try:
            self.setText(self._fmt.format(v) + (f"  {self._unit}" if self._unit else ""))
        except Exception:
            self.setText(str(v))


# ---------------------------------------------------------------- toggles / choices
class Check(QCheckBox):
    def __init__(self, text, var):
        super().__init__(text)
        self.setChecked(bool(var.get()))
        self.toggled.connect(var.set)
        var.changed.connect(lambda v: self.setChecked(bool(v)) if self.isChecked() != bool(v) else None)
        self.setCursor(Qt.PointingHandCursor)


class Combo(NoWheelCombo):
    def __init__(self, var, values, width=140):
        super().__init__()
        self.addItems([str(v) for v in values])
        self.setFixedWidth(width)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCurrentText(str(var.get()))
        self.currentTextChanged.connect(var.set)
        var.changed.connect(lambda v: self.setCurrentText(str(v)))


class Segmented(QWidget):
    """Row of exclusive buttons bound to a Var. options: [(value, label)]."""

    def __init__(self, var, options):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.var = var
        self.group = QButtonGroup(self)
        self.group.setExclusive(False)   # allows "none selected" for custom values
        self.buttons = {}
        for i, (val, text) in enumerate(options):
            b = QPushButton(text)
            b.setProperty("kind", "seg")
            if i == 0:
                b.setProperty("pos", "first")
            if i == len(options) - 1:
                b.setProperty("pos", "last")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, v=val: var.set(v))
            self.group.addButton(b)
            self.buttons[val] = b
            lay.addWidget(b)
        lay.addStretch(1)
        var.changed.connect(self._sync)
        self._sync(var.get())

    def _sync(self, v):
        for val, b in self.buttons.items():
            b.setChecked(val == v)


# ---------------------------------------------------------------- collapsible section
class Section(QWidget):
    def __init__(self, title, expanded=True, accent=None):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.head = QToolButton()
        self.head.setObjectName("SectionHead")
        self.head.setText(title)
        self.head.setCheckable(True)
        self.head.setChecked(expanded)
        self.head.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.head.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.head.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.head.setCursor(Qt.PointingHandCursor)
        self.head.toggled.connect(self._toggle)
        v.addWidget(self.head)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(6, 2, 2, 14)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnMinimumWidth(0, 150)
        v.addWidget(self.body)
        self.body.setVisible(expanded)
        self._row = 0

    def _toggle(self, on):
        self.head.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.body.setVisible(on)

    def row(self, text, widget, tip=None):
        lb = label(text, "FieldLabel")
        if tip:
            lb.setToolTip(tip)
            widget.setToolTip(tip)
        self.grid.addWidget(lb, self._row, 0, Qt.AlignVCenter)
        self.grid.addWidget(widget, self._row, 1)
        self._row += 1
        return widget

    def full(self, widget):
        self.grid.addWidget(widget, self._row, 0, 1, 2)
        self._row += 1
        return widget


class FormGrid(QGridLayout):
    """Label / field grid used inside cards."""

    def __init__(self):
        super().__init__()
        self.setHorizontalSpacing(14)
        self.setVerticalSpacing(10)
        self.setColumnStretch(1, 1)
        self._row = 0

    def row(self, text, widget, tip=None):
        lb = label(text, "FieldLabel")
        if tip:
            lb.setToolTip(tip)
        self.addWidget(lb, self._row, 0)
        self.addWidget(widget, self._row, 1)
        self._row += 1
        return widget


# ---------------------------------------------------------------- status bits
class Dot(QWidget):
    def __init__(self, color=theme.FAINT, size=10):
        super().__init__()
        self._c = QColor(color)
        self._s = size
        self.setFixedSize(size + 2, size + 2)

    def set_color(self, c):
        self._c = QColor(c)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self._c)
        p.drawEllipse(1, 1, self._s, self._s)


class Chip(QFrame):
    """Small pill: coloured dot + text. Used for device state in the header."""

    def __init__(self, text):
        super().__init__()
        self.setObjectName("Tile")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 12, 5)
        lay.setSpacing(7)
        self.dot = Dot()
        self.lbl = label(text)
        self.lbl.setStyleSheet("font-size: 12px;")
        lay.addWidget(self.dot)
        lay.addWidget(self.lbl)

    def set_state(self, color, text=None):
        self.dot.set_color(color)
        if text is not None:
            self.lbl.setText(text)


class Tile(QFrame):
    """Telemetry tile: caption and one or more mono value lines."""

    def __init__(self, caption, accent=None, big=False):
        super().__init__()
        self.setObjectName("Tile")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        if accent:
            top.addWidget(Dot(accent, 8))
        top.addWidget(label(caption.upper(), "TileLabel"))
        top.addStretch(1)
        v.addLayout(top)
        self.value = label("—", "TileBig" if big else "TileValue")
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.value)

    def set(self, text):
        self.value.setText(text)
