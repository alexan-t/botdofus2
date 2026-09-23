"""Chemins d'exécution, migration locale et journalisation de l'application Windows."""

from __future__ import annotations

from datetime import datetime
from contextlib import closing
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sqlite3
import sys
import threading
import traceback
from typing import Callable

from combatbot import __version__


APP_NAME = "PythonBot"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_data_root() -> Path:
    override = os.environ.get("PYTHONBOT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if is_frozen():
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            local = str(Path.home() / "AppData" / "Local")
        return Path(local) / APP_NAME
    return PROJECT_ROOT


def database_path() -> Path:
    return app_data_root() / "data" / "pythonbot.sqlite3"


def log_directory() -> Path:
    return app_data_root() / "logs"


def executable_path() -> Path:
    return Path(sys.executable if is_frozen() else sys.argv[0]).resolve()


def _legacy_database_candidates() -> tuple[Path, ...]:
    if not is_frozen():
        return ()
    executable = executable_path()
    candidates = [
        executable.parent / "data" / "pythonbot.sqlite3",
        executable.parent.parent.parent / "data" / "pythonbot.sqlite3",
    ]
    return tuple(dict.fromkeys(candidate.resolve() for candidate in candidates))


def migrate_legacy_database(destination: Path | None = None) -> Path | None:
    """Copy a legacy project database once, atomically, before SQLite opens it."""
    target = (destination or database_path()).resolve()
    if target.exists():
        return None
    for source in _legacy_database_candidates():
        if not source.is_file() or source == target:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".migrating")
        try:
            with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as source_db:
                with closing(sqlite3.connect(temporary)) as target_db:
                    source_db.backup(target_db)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return source
    return None


def configure_logging() -> Path:
    directory = log_directory()
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "pythonbot.log"
    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(item, RotatingFileHandler) and Path(item.baseFilename) == log_path for item in root.handlers):
        root.addHandler(handler)
    logging.info("Démarrage PythonBot %s | Python %s | exécutable=%s | données=%s",
                 __version__, sys.version.replace("\n", " "), executable_path(), app_data_root())
    return log_path


def format_crash(stage: str, exception_type: type[BaseException], exception: BaseException,
                 tb) -> str:
    return (
        f"Date : {datetime.now().astimezone().isoformat()}\n"
        f"Version PythonBot : {__version__}\n"
        f"Étape : {stage}\n"
        f"Exécutable : {executable_path()}\n"
        f"Données : {app_data_root()}\n\n"
        + "".join(traceback.format_exception(exception_type, exception, tb))
    )


def install_exception_hooks(show_error: Callable[[str], None]) -> None:
    def handle(exception_type, exception, tb, stage: str = "interface principale") -> None:
        report = format_crash(stage, exception_type, exception, tb)
        logging.critical(report)
        show_error(
            "PythonBot a rencontré une erreur inattendue.\n\n"
            f"{exception_type.__name__} : {exception}\n\n"
            f"Le diagnostic a été enregistré dans :\n{log_directory() / 'pythonbot.log'}"
        )

    sys.excepthook = handle

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        handle(args.exc_type, args.exc_value, args.exc_traceback, f"thread {args.thread.name}")

    threading.excepthook = thread_hook
