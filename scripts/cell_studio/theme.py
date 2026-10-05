"""Colours and the application style sheet (dark, spacious, high contrast)."""

BG = "#0e1116"
SURFACE = "#161b22"
RAISED = "#1d232c"
INPUT = "#0f141a"
BORDER = "#29313c"
BORDER_STRONG = "#3a4452"
TEXT = "#e6edf3"
MUTED = "#8b96a5"
FAINT = "#5b6573"

ACCENT = "#2dd4bf"        # teal: primary actions, focus
ACCENT_DIM = "#134e4a"
LEFT = "#5b9dff"          # left arm colour everywhere
RIGHT = "#ff8a4c"         # right arm colour everywhere
OK = "#3fb950"
WARN = "#d29922"
DANGER = "#f85149"
DANGER_DARK = "#b62324"

KIND_COLOR = {            # slicer path kinds, tuned for a dark canvas
    "WALL_OUTER": "#5b9dff",
    "WALL_INNER": "#8fd3ff",
    "SKIN": "#3fb950",
    "INFILL": "#ff8a4c",
}

FONT_UI = '"Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif'
FONT_MONO = '"Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono", monospace'

import os as _os
_ASSETS = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "assets").replace("\\", "/")

QSS = f"""
* {{ font-family: {FONT_UI}; font-size: 13px; color: {TEXT}; }}
QMainWindow, QWidget#Root {{ background: {BG}; }}
QWidget {{ background: transparent; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; padding: 6px; }}

/* ---------- header ---------- */
QFrame#Header {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QLabel#AppTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#AppSub {{ color: {MUTED}; font-size: 12px; }}
QPushButton#EStop {{
    background: {DANGER_DARK}; border: 2px solid {DANGER}; border-radius: 10px;
    color: white; font-size: 15px; font-weight: 800; letter-spacing: 1px; padding: 10px 22px;
}}
QPushButton#EStop:hover {{ background: {DANGER}; }}
QPushButton#EStop:pressed {{ background: #8e1519; }}
QToolButton#MenuBtn {{
    background: {RAISED}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 14px;
}}
QToolButton#MenuBtn:hover {{ border-color: {BORDER_STRONG}; }}
QToolButton#MenuBtn::menu-indicator {{ image: none; width: 0; }}

/* ---------- nav rail ---------- */
QFrame#Nav {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QPushButton#NavBtn {{
    background: transparent; border: none; border-radius: 8px; text-align: left;
    padding: 11px 14px; color: {MUTED}; font-size: 14px;
}}
QPushButton#NavBtn:hover {{ background: {RAISED}; color: {TEXT}; }}
QPushButton#NavBtn:checked {{ background: {ACCENT_DIM}; color: {ACCENT}; font-weight: 600; }}

/* ---------- side live panel ---------- */
QFrame#LivePanel {{ background: {SURFACE}; border-left: 1px solid {BORDER}; }}

/* ---------- cards ---------- */
QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#CardTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#CardHint {{ color: {MUTED}; font-size: 12px; }}
QLabel#SectionLabel {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#FieldLabel {{ color: #c3ccd6; }}
QFrame#Tile {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#TileLabel {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QLabel#TileValue {{ font-family: {FONT_MONO}; font-size: 15px; }}
QLabel#TileBig {{ font-family: {FONT_MONO}; font-size: 22px; font-weight: 600; }}
QFrame#Divider {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}

/* ---------- collapsible section ---------- */
QToolButton#SectionHead {{
    background: transparent; border: none; text-align: left; padding: 10px 4px;
    font-size: 13px; font-weight: 600; color: {TEXT};
}}
QToolButton#SectionHead:hover {{ color: {ACCENT}; }}

/* ---------- buttons ---------- */
QPushButton {{
    background: {RAISED}; border: 1px solid {BORDER_STRONG}; border-radius: 8px;
    padding: 8px 14px; color: {TEXT};
}}
QPushButton:hover {{ border-color: {FAINT}; background: #232a34; }}
QPushButton:pressed {{ background: #151a20; }}
QPushButton:disabled {{ color: {FAINT}; border-color: {BORDER}; background: {SURFACE}; }}
QPushButton[kind="primary"] {{
    background: {ACCENT}; border: 1px solid {ACCENT}; color: #04201c; font-weight: 700; padding: 11px 16px;
}}
QPushButton[kind="primary"]:hover {{ background: #5eead4; }}
QPushButton[kind="primary"]:disabled {{ background: {ACCENT_DIM}; border-color: {ACCENT_DIM}; color: #4b7a74; }}
QPushButton[kind="danger"] {{ border-color: {DANGER}; color: {DANGER}; font-weight: 600; }}
QPushButton[kind="danger"]:hover {{ background: #2a1215; }}
QPushButton[kind="ghost"] {{ background: transparent; border-color: {BORDER}; color: {MUTED}; }}
QPushButton[kind="ghost"]:hover {{ color: {TEXT}; }}
QPushButton[kind="nudge"] {{
    padding: 3px 0px; min-width: 33px; border-radius: 6px; font-family: {FONT_MONO}; font-size: 12px;
    color: {MUTED}; background: {INPUT}; border-color: {BORDER};
}}
QPushButton[kind="nudge"]:hover {{ color: {TEXT}; border-color: {BORDER_STRONG}; }}
QPushButton[kind="jog"] {{ font-family: {FONT_MONO}; font-size: 14px; min-width: 54px; min-height: 34px; padding: 4px; }}
QPushButton[kind="seg"] {{ border-radius: 0px; padding: 6px 14px; background: {INPUT}; border-color: {BORDER}; color: {MUTED}; }}
QPushButton[kind="seg"]:checked {{ background: {ACCENT_DIM}; color: {ACCENT}; border-color: {ACCENT}; }}
QPushButton[pos="first"] {{ border-top-left-radius: 7px; border-bottom-left-radius: 7px; }}
QPushButton[pos="last"] {{ border-top-right-radius: 7px; border-bottom-right-radius: 7px; }}

/* ---------- inputs ---------- */
QDoubleSpinBox, QSpinBox, QLineEdit, QComboBox {{
    background: {INPUT}; border: 1px solid {BORDER}; border-radius: 7px; padding: 6px 8px;
    font-family: {FONT_MONO}; selection-background-color: {ACCENT_DIM};
}}
QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QDoubleSpinBox:disabled, QSpinBox:disabled, QLineEdit:disabled {{ color: {FAINT}; }}
QLineEdit[readOnly="true"] {{ color: {ACCENT}; }}
QDoubleSpinBox::up-button, QSpinBox::up-button, QDoubleSpinBox::down-button, QSpinBox::down-button {{
    width: 18px; border: none; border-left: 1px solid {BORDER}; background: {RAISED}; subcontrol-origin: border; }}
QDoubleSpinBox::up-button, QSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 6px; }}
QDoubleSpinBox::down-button, QSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 6px; }}
QDoubleSpinBox::up-button:hover, QSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover, QSpinBox::down-button:hover {{ background: {BORDER_STRONG}; }}
QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {{ image: url({_ASSETS}/arrow_up.svg); width: 10px; height: 6px; }}
QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {{ image: url({_ASSETS}/arrow_down.svg); width: 10px; height: 6px; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{ background: {RAISED}; border: 1px solid {BORDER_STRONG}; selection-background-color: {ACCENT_DIM}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER_STRONG}; background: {INPUT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QSlider::groove:horizontal {{ height: 6px; background: {INPUT}; border: 1px solid {BORDER}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 18px; height: 18px; margin: -7px 0; border-radius: 9px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_STRONG}; border-radius: 4px; min-width: 30px; }}

QPlainTextEdit {{ background: {INPUT}; border: 1px solid {BORDER}; border-radius: 8px; font-family: {FONT_MONO}; font-size: 12px; padding: 6px; }}
QSplitter::handle {{ background: {BG}; }}
QMenu {{ background: {RAISED}; border: 1px solid {BORDER_STRONG}; padding: 6px; }}
QMenu::item {{ padding: 8px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_DIM}; color: {ACCENT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QDialog, QMessageBox {{ background: {SURFACE}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 16px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}
"""
