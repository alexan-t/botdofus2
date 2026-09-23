"""Inventaire borné au dossier explicitement choisi, sans suivre les liens."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET

from .models import InventoryFile, ScanReport

INTERESTING = {".d2o", ".d2p", ".dlm"}
MAX_FILES = 100_000


def is_link(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def within_client(root: Path, relative: str) -> Path:
    candidate = root / relative
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("Chemin hors du dossier client")
    current = candidate
    while current != root:
        if is_link(current):
            raise ValueError("Lien/jonction ignoré dans le dossier client")
        current = current.parent
    return candidate


def discover_client(folder: str | Path | None, *, hashes: bool = False) -> ScanReport:
    report = ScanReport()
    if folder is None or not str(folder).strip():
        return report
    try:
        root = Path(folder).expanduser().resolve()
        report.client_path = str(root)
        if not root.is_dir() or root == Path(root.anchor):
            report.status = "INVALID_DIRECTORY"
            report.message = "Dossier du client DOFUS non configuré"
            report.errors.append("Choisissez un dossier client existant, pas la racine d'un disque.")
            return report
        versions: set[str] = set()
        count = 0

        def error(exc):
            report.errors.append(f"Lecture impossible : {exc}")

        for directory, directories, files in os.walk(root, followlinks=False, onerror=error):
            kept = []
            for name in sorted(directories):
                try:
                    child = Path(directory) / name
                    if is_link(child):
                        report.errors.append(f"Lien/jonction ignoré : {child.relative_to(root)}")
                    else:
                        kept.append(name)
                except OSError as exc:
                    error(exc)
            directories[:] = kept
            for name in sorted(files):
                count += 1
                if count > MAX_FILES:
                    report.errors.append("Limite de 100000 fichiers atteinte ; inventaire incomplet.")
                    report.status = "LIMIT"
                    report.message = "Inventaire interrompu : dossier trop volumineux"
                    return report
                path = Path(directory) / name
                relative = path.relative_to(root).as_posix()
                try:
                    if is_link(path) or not path.is_file():
                        report.errors.append(f"Fichier spécial/lien ignoré : {relative}")
                        continue
                    info = path.stat()
                    extension = path.suffix.lower()
                    with path.open("rb") as stream:
                        header = stream.read(32)
                        digest = None
                        if hashes and extension in INTERESTING:
                            stream.seek(0)
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    classification = (
                        "archive D2P 2.1 probable" if header.startswith(b"\x02\x01") else
                        "objets D2O probables" if header.startswith(b"D2O") else
                        "map DLM brute probable" if extension == ".dlm" and header.startswith(b"M") else
                        "map DLM compressée à vérifier" if extension == ".dlm" else "inconnu"
                    )
                    report.files.append(InventoryFile(
                        relative, extension, info.st_size, info.st_mtime_ns,
                        datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                        header.hex(), classification, digest,
                    ))
                    if info.st_size <= 65536 and name.lower() in ("version.txt", "application.xml"):
                        text = path.read_text(encoding="utf-8-sig")
                        version = None
                        if name.lower() == "version.txt":
                            if re.fullmatch(r"\s*(?:DOFUS\s+)?2\.\d+\.\d+(?:\.\d+)?\s*", text, re.I):
                                version = text.strip().removeprefix("DOFUS ").strip()
                        else:
                            document = ET.fromstring(text)
                            nodes = {node.tag.rsplit("}", 1)[-1]: node.text or "" for node in document.iter()}
                            if "dofus" in nodes.get("id", "").lower():
                                version = nodes.get("versionNumber") or nodes.get("version")
                        if version and re.fullmatch(r"2\.\d+\.\d+(?:\.\d+)?", version):
                            versions.add(version)
                            report.version_evidence.append(f"{relative}: {version} (version déclarée)")
                except (OSError, UnicodeError, ET.ParseError) as exc:
                    report.errors.append(f"{relative}: {exc}")
        report.detected_version = next(iter(versions)) if len(versions) == 1 else None
        if len(versions) > 1:
            report.errors.append("Versions déclarées contradictoires ; version non déterminée.")
        report.status = "SCANNED" if report.files else "EMPTY"
        report.message = "Inventaire terminé" if report.files else "Dossier vide ou illisible"
    except (OSError, ValueError) as exc:
        report.status = "INVALID_DIRECTORY"
        report.message = "Dossier du client DOFUS non configuré"
        report.errors.append(str(exc))
    return report
