"""Lancer l'application Windows de combat simulé."""

from __future__ import annotations

import sys
import logging

from combatbot.runtime import configure_logging, install_exception_hooks, log_directory

try:
    configure_logging()
    from PySide6.QtWidgets import QApplication, QMessageBox
    from combatbot.storage import Storage
    from combatbot.ui.dofbot2 import theme as dofbot2_theme
    from combatbot.ui.dofbot2.shell import SCREENS, DofBot2Window
    from combatbot.ui.theme import STYLE
    from combatbot.vision.window import enable_dpi_awareness
except Exception as bootstrap_error:
    logging.exception("Échec avant l'initialisation de l'interface")
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f"PythonBot ne peut pas démarrer : {bootstrap_error}\n\nConsultez :\n{log_directory() / 'pythonbot.log'}",
            "Erreur PythonBot",
            0x10,
        )
    raise SystemExit(1) from bootstrap_error


def _start_screen(argv: list[str]) -> str:
    """``--screen=connect`` (ou profile, create…) ouvre directement un écran, utile pour le design."""
    for argument in argv:
        if argument.startswith("--screen="):
            name = argument.split("=", 1)[1]
            if name in SCREENS:
                return name
    return "splash"


def main() -> int:
    app = QApplication(sys.argv)
    install_exception_hooks(lambda message: QMessageBox.critical(None, "Erreur DofBot2", message))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)  # dialogues sans parent de l'interface historique
    dofbot2_theme.load_fonts()
    smoke_test = "--package-smoke-test" in sys.argv
    try:
        enable_dpi_awareness()
        storage = Storage()
        if smoke_test:  # le test d'empaquetage vérifie l'interface historique complète
            from combatbot.ui.main_window import MainWindow
            window = MainWindow(storage)
        else:
            window = DofBot2Window(storage, _start_screen(sys.argv))
    except Exception as exc:
        logging.exception("Échec pendant l'initialisation")
        QMessageBox.critical(
            None, "DofBot2",
            f"Initialisation impossible : {exc}\n\nConsultez le journal :\n{log_directory() / 'pythonbot.log'}",
        )
        return 1
    if smoke_test:
        from combatbot.packaging_smoke import run_packaging_smoke
        return run_packaging_smoke(app, storage, window)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
