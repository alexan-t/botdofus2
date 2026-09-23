"""Palette sombre de l'application."""

STYLE = """
QWidget { background: #111827; color: #e5e7eb; font-family: 'Segoe UI'; font-size: 13px; }
QLabel { background: transparent; }
QMainWindow, QStackedWidget { background: #111827; }
QFrame#sidebar { background: #0b1220; border-right: 1px solid #273449; }
QFrame#card { background: #1b2637; border: 1px solid #2e3d52; border-radius: 12px; }
QLabel#title { font-size: 23px; font-weight: 700; color: #f8fafc; }
QLabel#subtitle { color: #9ca3af; }
QLabel#metric { font-size: 26px; font-weight: 700; color: #65d6b5; }
QPushButton { background: #25364e; border: 1px solid #344a66; border-radius: 8px; padding: 8px 12px; }
QPushButton:hover { background: #304a6a; }
QPushButton:disabled { color: #778395; background: #1b2738; }
QPushButton#primary { background: #137f72; border-color: #159785; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #199885; }
QPushButton#danger { background: #873b45; border-color: #a84752; color: white; }
QPushButton#nav { background: transparent; border: none; text-align: left; padding: 11px 14px; }
QPushButton#nav:checked { background: #173b43; color: #65d6b5; border-left: 3px solid #40c8aa; }
QLineEdit, QSpinBox, QComboBox, QListWidget, QTableWidget, QTextEdit {
    background: #172234; border: 1px solid #34445b; border-radius: 7px; padding: 5px; color: #e5e7eb;
    selection-background-color: #137f72;
}
QHeaderView::section { background: #223248; color: #d9e4ef; border: none; padding: 6px; }
QScrollBar:vertical { background: #111827; width: 11px; }
QScrollBar::handle:vertical { background: #3d526d; border-radius: 5px; }
QCheckBox { spacing: 8px; }
"""
