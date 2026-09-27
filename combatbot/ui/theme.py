"""Palette sombre de l'interface historique, alignée sur les tokens DofBot2 (vert #8fd14f).

Elle habille les outils avancés (calibration, projection, recette, revue HUD, annotations) et
les boîtes de dialogue, pour qu'ils restent cohérents avec la nouvelle interface."""

STYLE = """
QWidget { background: #0c100d; color: #e6ede4; font-family: 'Manrope', 'Segoe UI'; font-size: 13px; }
QLabel { background: transparent; }
QMainWindow, QStackedWidget, QDialog { background: #0c100d; }
QFrame#sidebar { background: #0a0d0b; border-right: 1px solid rgba(255,255,255,13); }
QFrame#card { background: #121813; border: 1px solid rgba(255,255,255,15); border-radius: 16px; }
QLabel#title { font-size: 24px; font-weight: 800; color: #e6ede4; }
QLabel#subtitle { color: #8e9c8f; }
QLabel#metric { font-family: 'JetBrains Mono'; font-size: 24px; font-weight: 600; color: #b6e68a; }
QPushButton { background: transparent; border: 1px solid rgba(255,255,255,31); border-radius: 16px;
    padding: 7px 14px; font-weight: 700; color: #e6ede4; }
QPushButton:hover { border-color: #8fd14f; }
QPushButton:checked { background: rgba(143,209,79,41); border-color: rgba(143,209,79,140); color: #b6e68a; }
QPushButton:disabled { color: #5d6a5f; border-color: rgba(255,255,255,13); }
QPushButton#primary { background: #8fd14f; border: none; color: #0b1408; font-weight: 800; }
QPushButton#primary:hover { background: #a3dc6a; }
QPushButton#primary:disabled { background: #1b231e; color: #5d6a5f; }
QPushButton#danger { background: transparent; border-color: rgba(224,103,126,140); color: #f0a3b1; }
QPushButton#danger:hover { border-color: #e0677e; }
QPushButton#nav { background: transparent; border: none; border-radius: 12px; text-align: left; padding: 10px 14px;
    color: #8e9c8f; }
QPushButton#nav:hover { color: #e6ede4; }
QPushButton#nav:checked { background: rgba(143,209,79,41); color: #b6e68a; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QListWidget, QTableWidget, QTextEdit, QPlainTextEdit {
    background: #121813; border: 1px solid rgba(255,255,255,20); border-radius: 10px; padding: 5px 8px;
    color: #e6ede4; selection-background-color: rgba(143,209,79,90); selection-color: #e6ede4;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTextEdit:focus { border-color: #8fd14f; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #161e17; border: 1px solid rgba(255,255,255,20);
    selection-background-color: rgba(143,209,79,60); }
QListWidget::item, QTableWidget::item { padding: 4px; }
QListWidget::item:selected, QTableWidget::item:selected { background: rgba(143,209,79,46); color: #e6ede4; }
QHeaderView::section { background: #1b231e; color: #8e9c8f; border: none; padding: 6px; font-weight: 700; }
QTableCornerButton::section { background: #1b231e; border: none; }
QTabWidget::pane { border: 1px solid rgba(255,255,255,15); border-radius: 12px; top: -1px; }
QTabBar::tab { background: transparent; color: #8e9c8f; padding: 8px 14px; border: none; font-weight: 700; }
QTabBar::tab:selected { color: #b6e68a; border-bottom: 2px solid #8fd14f; }
QGroupBox { border: 1px solid rgba(255,255,255,15); border-radius: 12px; margin-top: 14px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #8e9c8f; font-weight: 700; }
QCheckBox, QRadioButton { spacing: 8px; background: transparent; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; border: 1.5px solid #3a453c;
    background: #0c100d; }
QCheckBox::indicator { border-radius: 5px; }
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked { background: #8fd14f; border-color: #8fd14f; }
QCheckBox:disabled, QRadioButton:disabled { color: #5d6a5f; }
QProgressBar { background: #1b231e; border: none; border-radius: 3px; height: 6px; text-align: center; color: transparent; }
QProgressBar::chunk { background: #8fd14f; border-radius: 3px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #1b231e; border-radius: 3px; min-height: 30px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #1b231e; border-radius: 3px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QToolTip { background: #161e17; color: #e6ede4; border: 1px solid rgba(255,255,255,20); padding: 6px 8px; }
QMessageBox, QInputDialog, QFileDialog { background: #121813; }
QMenu { background: #161e17; border: 1px solid rgba(255,255,255,20); padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 6px; }
QMenu::item:selected { background: rgba(143,209,79,46); }
QSlider::groove:horizontal { height: 4px; background: #1b231e; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px; background: #8fd14f; }
"""
