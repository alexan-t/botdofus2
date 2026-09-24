"""Fusionne un corpus PythonBot source dans un corpus cible, sans rien écraser.

Usage (simulation par défaut) :
    python scripts/merge_corpus.py --source data/corpus --target "%LOCALAPPDATA%/PythonBot/data/corpus"
    python scripts/merge_corpus.py ... --apply

Règles :
- aucune observation, frame ou dossier de session existant dans la cible n'est modifié ;
- une collision d'identifiant, de frame ou de dossier arrête tout avant écriture ;
- les annotations sont copiées telles quelles : aucune vérité n'est créée ni promue
  (une annotation sans provenance reste « unverified_import ») ;
- le registre de split HUD de la source (TEST gelé) est repris si la cible n'en a pas ;
- sauvegarde du dossier manifests cible, copie vérifiée par SHA-256, écriture atomique.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from combatbot.corpus.models import CorpusManifest  # noqa: E402

REGISTRY = "hud_split_registry.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def pythonbot_running() -> bool:
    if os.name != "nt":
        return False
    output = subprocess.run(["tasklist", "/FI", "IMAGENAME eq PythonBot.exe"],
                            capture_output=True, text=True, check=False).stdout
    return "PythonBot.exe" in output


def load(path: Path) -> CorpusManifest:
    return CorpusManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))


def plan(source: Path, target: Path) -> dict:
    source_manifest = load(source / "manifests" / "corpus_manifest.json")
    target_path = target / "manifests" / "corpus_manifest.json"
    target_manifest = load(target_path) if target_path.is_file() else CorpusManifest()
    target_ids = {entry.observation_id for entry in target_manifest.entries}
    target_frames = {(entry.session_id, entry.frame_index) for entry in target_manifest.entries}
    incoming = [entry for entry in source_manifest.entries if entry.observation_id not in target_ids]
    already = [entry.observation_id for entry in source_manifest.entries if entry.observation_id in target_ids]
    problems = []
    for entry in incoming:
        if (entry.session_id, entry.frame_index) in target_frames:
            problems.append(f"frame déjà présente : {entry.session_id}/{entry.frame_index}")
        for relative in entry.paths.values():
            if not (source / relative).is_file():
                problems.append(f"fichier source absent : {relative}")
            if (target / relative).exists():
                problems.append(f"fichier cible existant : {relative}")
    for observation_id in already:
        problems.append(f"observation déjà présente dans la cible (non recopiée) : {observation_id}")
    sessions = sorted({entry.session_id for entry in incoming})
    folders = sorted({str(Path(entry.paths["observation"]).parent) for entry in incoming})
    files = [path for folder in folders for path in (source / folder).rglob("*") if path.is_file()]
    return {"source_entries": len(source_manifest.entries), "target_entries": len(target_manifest.entries),
            "incoming": incoming, "sessions": sessions, "folders": folders, "files": files,
            "problems": problems, "target_manifest": target_manifest,
            "copy_registry": (source / "manifests" / REGISTRY).is_file()
            and not (target / "manifests" / REGISTRY).exists()}


def apply(source: Path, target: Path, result: dict) -> None:
    manifests = target / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    backup = target / f"manifests.backup-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copytree(manifests, backup)
    print(f"Sauvegarde : {backup}")
    for folder in result["folders"]:
        destination = target / folder
        if destination.exists():
            raise SystemExit(f"Arrêt : {destination} existe déjà")
        shutil.copytree(source / folder, destination)
    for path in result["files"]:
        copy = target / path.relative_to(source)
        if sha256(path) != sha256(copy):
            raise SystemExit(f"Arrêt : copie corrompue {copy}")
    # Relecture juste avant l'écriture : toute modification de la cible entre-temps est conservée.
    current_path = manifests / "corpus_manifest.json"
    current = load(current_path) if current_path.is_file() else CorpusManifest()
    merged = CorpusManifest(current.entries + tuple(result["incoming"]))
    merged.validate()
    temporary = current_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(merged.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, current_path)
    if result["copy_registry"]:
        shutil.copy2(source / "manifests" / REGISTRY, manifests / REGISTRY)
        print(f"Registre de split copié (TEST gelé conservé) : {manifests / REGISTRY}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Écrit réellement (sinon simulation)")
    args = parser.parse_args(argv)
    source, target = args.source.expanduser().resolve(), Path(os.path.expandvars(str(args.target))).resolve()
    if source == target:
        raise SystemExit("Source et cible identiques")
    result = plan(source, target)
    size = sum(path.stat().st_size for path in result["files"]) / 1e6
    print(f"Source : {source} ({result['source_entries']} observations)")
    print(f"Cible  : {target} ({result['target_entries']} observations)")
    print(f"À ajouter : {len(result['incoming'])} observations, {len(result['files'])} fichiers, {size:.1f} Mo")
    print(f"Sessions : {', '.join(result['sessions']) or '—'}")
    print(f"Registre de split à reprendre : {result['copy_registry']}")
    for problem in result["problems"]:
        print(f"PROBLÈME : {problem}")
    if result["problems"]:
        return 2
    if not args.apply:
        print("Simulation seulement : relancez avec --apply.")
        return 0
    if pythonbot_running():
        print("PythonBot.exe est ouvert : fermez-le avant --apply (risque de perdre une confirmation).")
        return 3
    apply(source, target, result)
    final = load(target / "manifests" / "corpus_manifest.json")
    print(f"Fusion terminée : {len(final.entries)} observations dans la cible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
