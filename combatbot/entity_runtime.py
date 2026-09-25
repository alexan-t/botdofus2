"""Installation explicite des profils TRAIN. Une génération devient active par un seul replace.

Le corpus est lu uniquement. Le pointeur atomique sélectionne ensemble profils et registre.
Une sauvegarde complète permet aussi un retour volontaire à la génération précédente.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from uuid import uuid4


def runtime_data_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "PythonBot/data"


def hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Objet JSON attendu : {path}")
    return value


def active_profile_directory(data: Path) -> Path:
    base = data / "entity_profiles"
    pointer = base / "active.json"
    if not pointer.is_file():
        return base
    generation = read_json(pointer).get("generation")
    if not isinstance(generation, str) or not generation or Path(generation).name != generation:
        raise ValueError("Pointeur de profils invalide")
    directory = (base / "generations" / generation).resolve()
    directory.relative_to((base / "generations").resolve())
    if not directory.is_dir():
        raise ValueError("Génération de profils absente")
    return directory


def validate_payload(name: str, raw: dict) -> None:
    from combatbot.vision.entity_models import PlayerVisualProfileV2, TeamMarkerProfile
    from combatbot.corpus.entity_split import layout_digest
    if name == "entity_split_registry.v2.json":
        if raw.get("schema_version") != 2 or not isinstance(raw.get("groups"), dict):
            raise ValueError("Registre v2 invalide")
        if any(v not in ("train", "validation", "test") for v in raw["groups"].values()):
            raise ValueError("Split invalide")
        return
    if name == "installation.json":
        if raw.get("training_split") != "train":
            raise ValueError("Provenance TRAIN absente")
        return
    if name.startswith("player_train_"):
        profile = PlayerVisualProfileV2.from_dict(raw)
        if not profile.prototypes or profile.accepted < 1 or any(
                len(p) != 6 or not all(math.isfinite(v) for v in p) for p in profile.prototypes):
            raise ValueError("Prototypes joueur invalides")
        prefix = "player_train_"
    elif name.startswith("team_markers_"):
        profile = TeamMarkerProfile.from_dict(raw)
        if not profile.player_team or not profile.enemy_team or "TRAIN" not in (profile.source or ""):
            raise ValueError("Profil équipe TRAIN incomplet")
        prefix = "team_markers_"
    else:
        raise ValueError(f"Fichier non autorisé : {name}")
    if not profile.layout_signature or name != f"{prefix}{layout_digest(profile.layout_signature)}.json":
        raise ValueError("Layout du fichier incohérent")
    if raw.get("training_split") != "train" or not raw.get("training_observations"):
        raise ValueError("Traçabilité TRAIN absente")
    json.dumps(raw, allow_nan=False)


def prepare_installation(data: Path, seed_registry: dict) -> dict:
    """Calcule en mémoire, sans écrire ni lancer de détection sur TEST."""
    from collections import defaultdict
    from combatbot.corpus.repository import CorpusRepository
    from combatbot.corpus.entity_benchmark import entity_inventory, build_profiles
    from combatbot.corpus.entity_split import cover_layouts, layout_digest
    from combatbot.vision.entity_detector import CellEntityDetector
    repository = CorpusRepository(data / "corpus")
    if not repository.manifest_path.is_file():
        raise ValueError("Corpus runtime absent")
    before = hashes(repository.root)
    samples = entity_inventory(repository, freeze=False, registry_override=seed_registry)
    layouts = defaultdict(set)
    for sample in samples:
        layouts[layout_digest(sample.layout_signature)].add(sample.group_id)
    registry = cover_layouts(seed_registry, layouts)
    registry["groups"].update({s.group_id: s.split for s in samples})
    files = {"entity_split_registry.v2.json": registry}
    summary = []
    for signature in sorted({s.layout_signature for s in samples if s.layout_signature}):
        rows = [s for s in samples if s.layout_signature == signature]
        profiles, diagnostic = build_profiles(rows, CellEntityDetector())
        if profiles.player is None or profiles.teams is None:
            raise ValueError(f"Profils TRAIN incomplets : {layout_digest(signature)}")
        sources = [s.observation_id for s in rows if s.split == "train"]
        digest = layout_digest(signature)
        for name, profile in ((f"player_train_{digest}.json", profiles.player),
                              (f"team_markers_{digest}.json", profiles.teams)):
            files[name] = {**profile.to_dict(), "training_split": "train", "training_observations": sources}
        summary.append({"layout": digest, "player_version": profiles.player.schema_version,
                        "team_version": profiles.teams.schema_version, "sample_count": profiles.player.accepted,
                        "train_frames": len(sources), "diagnostics": diagnostic})
    if not summary:
        raise ValueError("Aucun profil TRAIN installable")
    files["installation.json"] = {"schema_version": 1, "training_split": "train", "layouts": summary,
                                  "corpus_sha256": before}
    for name, payload in files.items():
        validate_payload(name, payload)
    if hashes(repository.root) != before:
        raise RuntimeError("Le corpus a changé pendant la préparation : recommencer")
    return {"files": files, "summary": summary, "corpus_sha256": before}


def _write(path: Path, value: dict) -> None:
    with path.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        stream.flush()
        os.fsync(stream.fileno())


def install_prepared(data: Path, plan: dict) -> dict:
    """Sauvegarde, staging validé, bascule atomique, relecture ; rollback sur toute exception."""
    data = data.resolve()
    if hashes(data / "corpus") != plan["corpus_sha256"]:
        raise RuntimeError("Corpus modifié depuis le dry-run")
    base = data / "entity_profiles"
    base.mkdir(parents=True, exist_ok=True)
    lock = base / "install.lock"
    with lock.open("x"):
        pass
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    backup = data / "backup" / f"entity-runtime-{stamp}"
    staging = base / f".staging-{stamp}"
    destination = base / "generations" / stamp
    pointer = base / "active.json"
    old_pointer = None
    switched = False
    try:
        old_pointer = pointer.read_bytes() if pointer.exists() else None
        backup.mkdir(parents=True)
        shutil.copytree(base, backup / "entity_profiles", ignore=shutil.ignore_patterns("install.lock"))
        old_registry = active_profile_directory(data) / "entity_split_registry.v2.json"
        _write(backup / "entity_split_registry.v2.json",
               read_json(old_registry) if old_registry.exists() else {"schema_version": 2, "groups": {}})
        _write(backup / "backup.json", {"previous_pointer_present": old_pointer is not None,
                                      "files": hashes(backup / "entity_profiles")})
        staging.mkdir()
        for name, payload in plan["files"].items():
            if Path(name).name != name:
                raise ValueError("Nom de fichier invalide")
            _write(staging / name, payload)
            validate_payload(name, read_json(staging / name))
        if hashes(data / "corpus") != plan["corpus_sha256"]:
            raise RuntimeError("Corpus modifié pendant le staging")
        destination.parent.mkdir(exist_ok=True)
        os.replace(staging, destination)
        temporary = base / f".active-{stamp}.tmp"
        _write(temporary, {"schema_version": 1, "generation": stamp})
        os.replace(temporary, pointer)
        switched = True
        installed = active_profile_directory(data)
        for name, payload in plan["files"].items():
            loaded = read_json(installed / name)
            validate_payload(name, loaded)
            if loaded != payload:
                raise RuntimeError(f"Relecture différente : {name}")
        if hashes(data / "corpus") != plan["corpus_sha256"]:
            raise RuntimeError("Corpus modifié pendant l'installation")
        return {"installed": True, "backup": str(backup), "generation": str(installed),
                "layouts": [{k: v for k, v in s.items() if k != "diagnostics"} for s in plan["summary"]],
                "corpus_files_unchanged": len(plan["corpus_sha256"]), "files_sha256": hashes(installed)}
    except BaseException:
        if switched:
            if old_pointer is None:
                pointer.unlink(missing_ok=True)
            else:
                temporary = base / f".rollback-{stamp}.tmp"
                temporary.write_bytes(old_pointer)
                os.replace(temporary, pointer)
        # Répertoires créés par cette transaction seulement, bornés au stockage des profils.
        for path in (staging, destination):
            if path.exists():
                path.resolve().relative_to(base.resolve())
                shutil.rmtree(path)
        raise
    finally:
        lock.unlink(missing_ok=True)


def restore_backup(data: Path, backup: Path) -> dict:
    """Retour explicite au pointeur sauvegardé ; les générations précédentes sont conservées."""
    data, backup = data.resolve(), backup.resolve()
    backup.relative_to(data / "backup")
    metadata = read_json(backup / "backup.json")
    saved = backup / "entity_profiles"
    if hashes(saved) != metadata["files"]:
        raise ValueError("Sauvegarde modifiée : restauration refusée")
    base = data / "entity_profiles"
    pointer = base / "active.json"
    lock = base / "install.lock"
    with lock.open("x"):
        pass
    try:
        if metadata["previous_pointer_present"]:
            value = read_json(saved / "active.json")
            previous = value["generation"]
            if Path(previous).name != previous or not (base / "generations" / previous).is_dir():
                raise ValueError("Génération précédente absente")
            expected = hashes(saved / "generations" / previous)
            if hashes(base / "generations" / previous) != expected:
                raise ValueError("Génération précédente modifiée")
            temporary = base / f".restore-{uuid4().hex}.tmp"
            _write(temporary, value)
            os.replace(temporary, pointer)
        else:
            pointer.unlink(missing_ok=True)
        return {"restored": True, "backup": str(backup), "active_directory": str(active_profile_directory(data))}
    finally:
        lock.unlink(missing_ok=True)
