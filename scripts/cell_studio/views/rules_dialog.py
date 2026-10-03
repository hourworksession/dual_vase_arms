"""Settings ▸ Print rules: see what the rules say, and edit config/print_rules.yaml."""

from PySide6.QtWidgets import QDialog, QVBoxLayout, QTabWidget, QPlainTextEdit, QMessageBox

from .. import theme
from ..widgets import button, label, hrow


class RulesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        import rules as rulesmod
        self.rulesmod = rulesmod
        self.setWindowTitle("Print rules")
        self.resize(900, 680)
        self.path = rulesmod.RULES_FILE
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(label("Print rules", "CardTitle"))
        v.addWidget(label("How parts are printed: when the bed turns, line widths, thin features, the tactics the "
                          "cell can use per region, and non-planar preferences. The AI layer may only change values "
                          "inside each rule's range. Used by Model print when 'Follow the print rules' is ticked.",
                          "CardHint", wrap=True))
        self.tabs = QTabWidget()
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.editor = QPlainTextEdit()
        for w in (self.summary, self.editor):
            w.setStyleSheet(f"font-family:{theme.FONT_MONO}; font-size:12px;")
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.tabs.addTab(self.summary, "Summary")
        self.tabs.addTab(self.editor, f"Edit  ({self.path})")
        v.addWidget(self.tabs, 1)
        self.status = label("", "Muted", wrap=True)
        v.addWidget(self.status)
        v.addWidget(hrow(button("Reload", "ghost", self.reload), button("Check", None, self.check), None,
                         button("Close", "ghost", self.reject), button("Save", "primary", self.save)))
        self.reload()

    def reload(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            text = ""
        self.editor.setPlainText(text)
        rs = self.rulesmod.RuleSet.load(self.path)
        self.summary.setPlainText(rs.explain())
        self.status.setText(f"Could not read the file: {rs.load_error}" if rs.load_error else "Loaded.")

    def check(self):
        try:
            rs = self.rulesmod.RuleSet.from_text(self.editor.toPlainText(), self.path)
        except Exception as e:
            self.status.setText(f"<span style='color:{theme.DANGER}'>Not valid:</span> {e}")
            return None
        self.summary.setPlainText(rs.explain())
        self.status.setText(f"OK: {len(rs.rules)} rules, {len(rs.tactics)} tactics, {len(rs.bands)} bands.")
        return rs

    def save(self):
        if self.check() is None:
            QMessageBox.warning(self, "Print rules", "Fix the problem shown before saving.")
            return
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(self.editor.toPlainText())
        self.status.setText("Saved. Slice or plan again to use the new rules.")
