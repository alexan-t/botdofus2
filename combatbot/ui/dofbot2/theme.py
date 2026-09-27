"""Tokens visuels DofBot2 (handoff design_handoff_dofbot2_ui) et feuille QSS associée."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase


FONTS_DIR = Path(__file__).resolve().parents[3] / "assets" / "fonts"
UI_FONT = "Manrope"
MONO_FONT = "JetBrains Mono"

# Fonds
PAGE = "#070908"
WINDOW = "#0c100d"
TITLE_BAR = "#0a0d0b"
SURFACE = "#121813"
SURFACE_HOVER = "#151d16"
SURFACE_2 = "#1b231e"
TOAST = "#161e17"
SWITCH_OFF = "#2a332c"
PLACEHOLDER = "#111712"

# Vert
GREEN = "#8fd14f"
GREEN_HOVER = "#a3dc6a"
GREEN_LIGHT = "#b6e68a"

# Texte
TEXT = "#e6ede4"
TEXT_2 = "#8e9c8f"
TEXT_3 = "#6f7f71"
TEXT_MUTED = "#5d6a5f"
ON_GREEN = "#0b1408"

ALERT = "#e5a13a"
CLOSE_HOVER = "#c42b1c"

AVATAR_COLORS = ("#8fd14f", "#5cc4d6", "#e0a257", "#b27ae0", "#e0677e", "#d9cf5a", "#4fd1a0", "#8a97ff")
CLASSES = ("Cra", "Iop", "Eniripsa", "Sram", "Enutrof", "Féca", "Sacrieur", "Osamodas", "Xélor",
           "Pandawa", "Ecaflip", "Roublard")


def green(alpha: float) -> QColor:
    color = QColor(GREEN)
    color.setAlphaF(alpha)
    return color


def white(alpha: float) -> QColor:
    return QColor(255, 255, 255, round(alpha * 255))


def load_fonts() -> bool:
    """Enregistre Manrope et JetBrains Mono (SIL OFL) ; Qt retombe sur la police système sinon."""
    loaded = False
    for path in sorted(FONTS_DIR.glob("*.ttf")):
        loaded = QFontDatabase.addApplicationFont(str(path)) >= 0 or loaded
    return loaded


def font(size: int, weight: int = 400, mono: bool = False, spacing: float = 0.0) -> QFont:
    """Police en pixels ; ``spacing`` est un tracking en em (ex. -0.03 pour les grands titres)."""
    result = QFont(MONO_FONT if mono else UI_FONT)
    result.setPixelSize(size)
    result.setWeight(QFont.Weight(weight))
    if spacing:
        result.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing * size)
    result.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return result


# Les règles génériques ne s'appliquent qu'aux écrans DofBot2 : l'interface historique intégrée
# porte sa propre feuille, plus proche dans la cascade Qt, et garde donc son apparence.
STYLE = f"""
QWidget {{ background: transparent; color: {TEXT}; font-family: '{UI_FONT}'; font-size: 13px; }}
QStackedWidget#d2Screens, QWidget#d2Screen {{ background: transparent; }}
QLabel {{ background: transparent; }}

QWidget#d2TitleBar {{ background: {TITLE_BAR}; border-bottom: 1px solid rgba(255,255,255,13);
    border-top-left-radius: 14px; border-top-right-radius: 14px; }}
QWidget#d2TitleBar[maximized="true"] {{ border-radius: 0; }}
QLabel#d2TitleName {{ font-size: 12px; font-weight: 700; letter-spacing: 0.24px; }}
QLabel#d2Crumb {{ font-size: 12px; color: {TEXT_MUTED}; }}

QLabel#d2H1 {{ font-size: 34px; font-weight: 800; letter-spacing: -1px; }}
QLabel#d2H2 {{ font-size: 30px; font-weight: 800; letter-spacing: -0.9px; }}
QLabel#d2Lead {{ font-size: 15px; color: {TEXT_2}; }}
QLabel#d2Hint {{ font-size: 12px; color: {TEXT_2}; }}
QLabel#d2HintTitle {{ font-size: 12px; font-weight: 700; color: {GREEN}; }}
QLabel#d2FieldLabel {{ font-size: 13px; font-weight: 700; color: {TEXT_2}; }}
QLabel#d2Mono {{ font-family: '{MONO_FONT}'; font-size: 12px; font-weight: 500; color: {TEXT_MUTED}; }}
QLabel#d2Caps {{ font-family: '{MONO_FONT}'; font-size: 11px; font-weight: 600; color: {TEXT_MUTED};
    letter-spacing: 0.9px; }}
QLabel#d2Empty {{ font-size: 13px; color: {TEXT_2}; background: {SURFACE}; border: 1px dashed rgba(255,255,255,31);
    border-radius: 14px; padding: 18px 16px; }}

QPushButton#d2Primary {{ background: {GREEN}; color: {ON_GREEN}; border: none; border-radius: 23px;
    min-height: 46px; max-height: 46px; padding: 0 26px; font-size: 14px; font-weight: 800; }}
QPushButton#d2Primary:hover {{ background: {GREEN_HOVER}; }}
QPushButton#d2Primary:disabled {{ background: {SURFACE_2}; color: {TEXT_MUTED}; }}
QPushButton#d2Link {{ background: transparent; border: none; color: {TEXT_2}; font-size: 13px;
    font-weight: 600; padding: 4px 2px; }}
QPushButton#d2Link:hover {{ color: {TEXT}; }}
QPushButton#d2Secondary {{ background: transparent; border: 1px solid rgba(255,255,255,31); border-radius: 19px;
    min-height: 38px; max-height: 38px; padding: 0 18px; font-size: 13px; font-weight: 700; color: {TEXT}; }}
QPushButton#d2Secondary:hover {{ border-color: {GREEN}; }}

QPushButton#d2Chip {{ background: transparent; border: 1px solid rgba(255,255,255,23); border-radius: 16px;
    min-height: 32px; max-height: 32px; padding: 0 14px; font-size: 13px; font-weight: 600; color: {TEXT_2}; }}
QPushButton#d2Chip:hover {{ color: {TEXT}; }}
QPushButton#d2Chip:checked {{ background: rgba(143,209,79,41); border-color: rgba(143,209,79,140); color: {GREEN_LIGHT}; }}

QLineEdit#d2Input {{ background: {SURFACE}; border: 1px solid rgba(255,255,255,20); border-radius: 12px;
    min-height: 46px; max-height: 46px; padding: 0 16px; font-size: 15px; font-weight: 600; color: {TEXT};
    selection-background-color: rgba(143,209,79,90); }}
QLineEdit#d2Input:focus {{ border-color: {GREEN}; }}

QWidget#d2Preview {{ background: {SURFACE}; border: 1px solid rgba(255,255,255,15); border-radius: 24px; }}
QLabel#d2PreviewName {{ font-size: 22px; font-weight: 800; letter-spacing: -0.44px; }}
QLabel#d2PreviewClass {{ font-size: 13px; color: {TEXT_2}; }}

QWidget#d2AppBar {{ border-bottom: 1px solid rgba(255,255,255,13); }}

QWidget#d2Card {{ background: {SURFACE}; border: 1px solid rgba(255,255,255,15); border-radius: 18px; }}
QWidget#d2Acc {{ background: {SURFACE}; border: 1px solid rgba(255,255,255,15); border-radius: 16px; }}
QWidget#d2Acc[open="true"] {{ border-color: rgba(143,209,79,56); }}
QWidget#d2Row {{ border-top: 1px solid rgba(255,255,255,13); }}
QLabel#d2RowTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#d2RowDesc {{ font-size: 12px; color: {TEXT_2}; }}
QLabel#d2RowError {{ font-size: 12px; color: {ALERT}; }}
QWidget#d2Stepper {{ background: {WINDOW}; border: 1px solid rgba(255,255,255,20); border-radius: 17px; }}
QPushButton#d2StepButton {{ background: transparent; border: none; color: {TEXT_2}; font-size: 16px;
    min-width: 34px; max-width: 34px; min-height: 32px; max-height: 32px; padding: 0; }}
QPushButton#d2StepButton:hover {{ color: {GREEN}; }}
QPushButton#d2StepButton:disabled {{ color: #3a453c; }}
QLabel#d2StepValue {{ font-family: '{MONO_FONT}'; font-size: 13px; font-weight: 600; }}
QPushButton#d2Choice {{ background: transparent; border: 1px solid rgba(255,255,255,23); border-radius: 15px;
    min-height: 28px; max-height: 28px; padding: 0 12px; font-size: 12px; font-weight: 700; color: {TEXT_2}; }}
QPushButton#d2Choice:hover {{ color: {TEXT}; }}
QPushButton#d2Choice:checked {{ background: rgba(143,209,79,41); border-color: rgba(143,209,79,140); color: {GREEN_LIGHT}; }}
QPushButton#d2Choice:disabled {{ color: #3a453c; border-color: rgba(255,255,255,13); }}
QLineEdit#d2Field {{ background: {WINDOW}; border: 1px solid rgba(255,255,255,20); border-radius: 10px;
    padding: 0 12px; font-family: '{MONO_FONT}'; font-size: 12px; font-weight: 500; color: {TEXT};
    selection-background-color: rgba(143,209,79,90); }}
QLineEdit#d2Field:focus {{ border-color: {GREEN}; }}
QLabel#d2InfoValue {{ font-family: '{MONO_FONT}'; font-size: 12px; font-weight: 600; color: {GREEN_LIGHT};
    background: {WINDOW}; border-radius: 8px; padding: 4px 10px; }}
QPushButton#d2InfoAction {{ background: transparent; border: none; color: {TEXT_2}; font-size: 12px;
    font-weight: 700; padding: 2px; }}
QPushButton#d2InfoAction:hover {{ color: {GREEN}; }}

QLabel#d2PageTitle {{ font-size: 28px; font-weight: 800; letter-spacing: -0.84px; }}
QLabel#d2PageText {{ font-size: 14px; color: {TEXT_2}; }}
QLabel#d2PlanName {{ font-size: 20px; font-weight: 800; }}
QLabel#d2PlanSub {{ font-size: 13px; color: {TEXT_2}; }}
QLabel#d2LogTime {{ font-family: '{MONO_FONT}'; font-size: 12px; font-weight: 500; color: {TEXT_MUTED}; }}
QLabel#d2LogText {{ font-size: 13px; }}
QWidget#d2LogRow {{ border-top: 1px solid rgba(255,255,255,10); }}
QLabel#d2Note {{ font-size: 12px; color: {TEXT_MUTED}; }}
QLabel#d2Banner {{ font-size: 13px; color: {TEXT_2}; }}
QLabel#d2SpellName {{ font-size: 18px; font-weight: 800; }}
QLabel#d2UseLabel {{ font-size: 13px; font-weight: 600; color: {TEXT_2}; }}

QWidget#d2StatusPill {{ background: {SURFACE}; border-radius: 16px; }}
QLabel#d2StatusText {{ font-size: 12px; font-weight: 600; }}
QPushButton#d2Run {{ background: {GREEN}; color: {ON_GREEN}; border: none; border-radius: 19px;
    min-height: 38px; max-height: 38px; padding: 0 20px; font-size: 13px; font-weight: 800; }}
QPushButton#d2Run:hover {{ background: {GREEN_HOVER}; }}
QPushButton#d2Run[running="true"] {{ background: transparent; color: {TEXT}; border: 1px solid rgba(255,255,255,46); }}
QPushButton#d2Run[running="true"]:hover {{ border-color: {TEXT_2}; }}
QPushButton#d2Schedule {{ background: {GREEN}; color: {ON_GREEN}; border: none; border-radius: 22px;
    min-height: 44px; max-height: 44px; padding: 0 24px; font-size: 14px; font-weight: 800; }}
QPushButton#d2Schedule:hover {{ background: {GREEN_HOVER}; }}
QPushButton#d2SmallOutline {{ background: transparent; border: 1px solid rgba(255,255,255,31); border-radius: 16px;
    min-height: 32px; max-height: 32px; padding: 0 14px; font-size: 12px; font-weight: 700; color: {TEXT}; }}
QPushButton#d2SmallOutline:hover {{ border-color: {GREEN}; }}
QPushButton#d2GreenOutline {{ background: transparent; border: 1px solid rgba(143,209,79,102); border-radius: 18px;
    min-height: 36px; max-height: 36px; padding: 0 16px; font-size: 12px; font-weight: 700; color: {GREEN_LIGHT}; }}
QPushButton#d2GreenOutline:hover {{ background: rgba(143,209,79,20); }}
QPushButton#d2Back {{ background: transparent; border: 1px solid rgba(255,255,255,31); border-radius: 16px;
    min-height: 32px; max-height: 32px; padding: 0 14px; font-size: 12px; font-weight: 700; color: {TEXT}; }}
QPushButton#d2Back:hover {{ border-color: {GREEN}; }}

QWidget#d2Toast {{ background: {TOAST}; border: 1px solid rgba(255,255,255,20); border-radius: 14px; }}
QLabel#d2ToastTitle {{ font-size: 13px; font-weight: 700; }}
QLabel#d2ToastBody {{ font-size: 12px; color: {TEXT_2}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_2}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #2a352d; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QToolTip {{ background: {TOAST}; color: {TEXT}; border: 1px solid rgba(255,255,255,20); padding: 6px 8px; }}
"""
