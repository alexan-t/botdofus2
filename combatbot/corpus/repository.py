"""Stockage fichier du corpus, import de debug et promotion volontaire en fixture."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any
from uuid import uuid4

from combatbot.corpus.models import Annotation, CorpusEntry, CorpusManifest, SCHEMA_VERSION
from combatbot.runtime import PROJECT_ROOT, app_data_root


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"Fichier absent : {path}") from None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON illisible : {path.name} ({exc})") from exc


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _grid_snapshot(prediction: dict[str, Any]) -> dict[str, object]:
    grid = prediction.get("grid") if isinstance(prediction.get("grid"), dict) else {}
    cells = grid.get("cells") if isinstance(grid, dict) else []
    return {
        "cell_count": len(cells) if isinstance(cells, list) else 0,
        "confidence": grid.get("confidence") if isinstance(grid, dict) else None,
        "grid_source": grid.get("grid_source", "VISION_DETECTED") if isinstance(grid, dict) else None,
        "map_id_declared": grid.get("map_id_declared") if isinstance(grid, dict) else None,
        "grid_profile_version": grid.get("grid_profile_version") if isinstance(grid, dict) else None,
        "projection_confidence": grid.get("projection_confidence") if isinstance(grid, dict) else None,
        "cells": cells if isinstance(cells, list) else [],
        "player_cell": prediction.get("player_cell"),
        "player_confidence": prediction.get("player_confidence"),
        "enemies": prediction.get("enemies", []),
    }


class CorpusRepository:
    """Dépôt local explicite ; aucune observation n'est importée automatiquement."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or (app_data_root() / "data" / "corpus")).resolve()
        self.sessions = self.root / "sessions"
        self.annotations = self.root / "annotations"
        self.manifests = self.root / "manifests"
        self.manifest_path = self.manifests / "corpus_manifest.json"

    def ensure_layout(self) -> None:
        for path in (self.sessions, self.annotations, self.manifests):
            path.mkdir(parents=True, exist_ok=True)
        if not self.manifest_path.exists():
            _write_json_atomic(self.manifest_path, CorpusManifest().to_dict())

    def load_manifest(self) -> CorpusManifest:
        self.ensure_layout()
        return CorpusManifest.from_dict(_read_json(self.manifest_path))

    def save_manifest(self, manifest: CorpusManifest) -> None:
        manifest.validate()
        _write_json_atomic(self.manifest_path, manifest.to_dict())

    def list_entries(self) -> tuple[CorpusEntry, ...]:
        return tuple(sorted(self.load_manifest().entries, key=lambda item: (item.session_id, item.frame_index)))

    def get_entry(self, observation_id: str) -> CorpusEntry:
        entry = next((item for item in self.load_manifest().entries if item.observation_id == observation_id), None)
        if entry is None:
            raise ValueError(f"Observation inconnue : {observation_id}")
        return entry

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise ValueError("Le chemin sort du corpus") from None
        return candidate

    def read_observation(self, entry: CorpusEntry | str) -> dict[str, Any]:
        item = self.get_entry(entry) if isinstance(entry, str) else entry
        raw = _read_json(self.resolve(item.paths["observation"]))
        if not isinstance(raw, dict):
            raise ValueError("Observation JSON invalide")
        return raw

    def read_annotation(self, entry: CorpusEntry | str) -> Annotation | None:
        item = self.get_entry(entry) if isinstance(entry, str) else entry
        relative = item.paths.get("annotation")
        if not relative:
            return None
        path = self.resolve(relative)
        if not path.exists():
            if item.annotation_available:
                raise ValueError(f"Annotation déclarée mais absente : {path}")
            return None
        return Annotation.from_dict(_read_json(path))

    def validate_files(self, entry: CorpusEntry) -> tuple[str, ...]:
        issues: list[str] = []
        for required in ("frame", "overlay", "observation"):
            relative = entry.paths.get(required)
            if not relative:
                issues.append(f"chemin {required} absent")
            elif not self.resolve(relative).is_file():
                issues.append(f"fichier {required} absent")
        if entry.annotation_available and not entry.paths.get("annotation"):
            issues.append("annotation déclarée sans chemin")
        elif entry.annotation_available and not self.resolve(entry.paths["annotation"]).is_file():
            issues.append("fichier annotation absent")
        return tuple(issues)

    @staticmethod
    def _find_debug_files(source: Path, payload: dict[str, Any]) -> dict[str, Path]:
        if source.is_dir():
            json_path = source / "observation.json"
            if not json_path.exists():
                candidates = sorted(source.glob("*-observation.json"))
                if len(candidates) != 1:
                    raise ValueError("Sélectionnez un dossier contenant une seule observation JSON")
                json_path = candidates[0]
        else:
            json_path = source
        if not json_path.is_file():
            raise ValueError("Observation JSON de debug introuvable")
        base = json_path.parent
        files: dict[str, Path] = {"observation": json_path}
        declared = payload.get("files") if isinstance(payload.get("files"), dict) else {}
        aliases = {
            "frame": ("frame.png", "original.png"),
            "overlay": ("overlay.png", "annotated.png"),
            "ap": ("hud/ap_original.png", "ap_original.png"),
            "mp": ("hud/mp_original.png", "mp_original.png"),
        }
        prefix = json_path.name.removesuffix("-observation.json")
        legacy = {
            "frame": base / f"{prefix}-original.png",
            "overlay": base / f"{prefix}-annotated.png",
            "ap": base / f"{prefix}-ap_original.png",
            "mp": base / f"{prefix}-mp_original.png",
        }
        for key, names in aliases.items():
            candidates: list[Path] = []
            declared_value = declared.get(key) if isinstance(declared, dict) else None
            if isinstance(declared_value, str):
                candidates.append(base / declared_value)
            candidates.extend(base / name for name in names)
            candidates.append(legacy[key])
            found = next((item for item in candidates if item.is_file()), None)
            if found is not None:
                files[key] = found
        if "frame" not in files or "overlay" not in files:
            raise ValueError("Le debug doit contenir la capture originale et l'overlay")
        return files

    def import_debug(self, source: Path, *, session_id: str | None = None,
                     frame_index: int | None = None, usage: str = "diagnostic",
                     tags: tuple[str, ...] = ()) -> CorpusEntry:
        """Copie un debug sans jamais le modifier ou le supprimer."""
        source = source.resolve()
        json_source = source / "observation.json" if source.is_dir() else source
        if source.is_dir() and not json_source.exists():
            matches = sorted(source.glob("*-observation.json"))
            if len(matches) == 1:
                json_source = matches[0]
        raw = _read_json(json_source)
        if not isinstance(raw, dict):
            raise ValueError("Observation de debug invalide")
        files = self._find_debug_files(source, raw)
        manifest = self.load_manifest()
        existing_ids = {item.observation_id for item in manifest.entries}
        requested_id = str(raw.get("observation_id", "")).strip()
        observation_id = requested_id if requested_id and requested_id not in existing_ids else f"obs_{uuid4().hex[:16]}"
        raw_session = raw.get("session_id")
        saved_session = raw_session.strip() if isinstance(raw_session, str) else ""
        chosen_session = session_id or saved_session or f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if frame_index is None:
            raw_index = raw.get("frame_index")
            if isinstance(raw_index, int) and not isinstance(raw_index, bool) and raw_index >= 0:
                frame_index = raw_index
            else:
                used = [item.frame_index for item in manifest.entries if item.session_id == chosen_session]
                frame_index = max(used, default=-1) + 1
        if any(item.session_id == chosen_session and item.frame_index == frame_index for item in manifest.entries):
            raise ValueError(f"La frame {chosen_session}/{frame_index} existe déjà")

        destination = self.sessions / chosen_session / observation_id
        if destination.exists():
            raise ValueError(f"Le dossier cible existe déjà : {destination}")
        destination.mkdir(parents=True)
        try:
            shutil.copy2(files["frame"], destination / "frame.png")
            shutil.copy2(files["overlay"], destination / "overlay.png")
            hud_paths: dict[str, str] = {}
            if "ap" in files or "mp" in files:
                hud = destination / "hud"
                hud.mkdir()
                for key, filename in (("ap", "ap_original.png"), ("mp", "mp_original.png")):
                    if key in files:
                        shutil.copy2(files[key], hud / filename)
                        hud_paths[key] = f"hud/{filename}"

            prediction = raw.get("prediction") if isinstance(raw.get("prediction"), dict) else raw
            document: dict[str, object] = {
                "schema_version": SCHEMA_VERSION,
                "observation_id": observation_id,
                "session_id": chosen_session,
                "frame_index": frame_index,
                "created_at": raw.get("created_at") or datetime.now(timezone.utc).isoformat(),
                "capture": raw.get("capture") if isinstance(raw.get("capture"), dict) else {},
                "prediction": prediction,
                "grid_snapshot": raw.get("grid_snapshot")
                if isinstance(raw.get("grid_snapshot"), dict) else _grid_snapshot(prediction),
                "files": {"frame": "frame.png", "overlay": "overlay.png", **hud_paths},
            }
            _write_json_atomic(destination / "observation.json", document)
            relative_base = destination.relative_to(self.root).as_posix()
            paths = {
                "frame": f"{relative_base}/frame.png",
                "overlay": f"{relative_base}/overlay.png",
                "observation": f"{relative_base}/observation.json",
            }
            if "ap" in hud_paths:
                paths["ap_crop"] = f"{relative_base}/{hud_paths['ap']}"
            if "mp" in hud_paths:
                paths["mp_crop"] = f"{relative_base}/{hud_paths['mp']}"
            entry = CorpusEntry(observation_id, chosen_session, frame_index, paths,
                                tags=tags, usage=usage)  # type: ignore[arg-type]
            self.save_manifest(CorpusManifest(manifest.entries + (entry,)))
            return entry
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    def import_packet(self, packet, *, tags: tuple[str, ...] = ("entity-sequence",)) -> CorpusEntry:
        """Enregistre une observation de « Vision réelle » dans le corpus (séquence d'entités).

        Réutilise le format debug (frame, overlay, crops HUD, grille projetée) puis l'import
        explicite ; le dossier temporaire est supprimé. Aucune vérité n'est créée.
        """
        import tempfile
        from combatbot.vision.combat_observer import save_debug_observation

        self.ensure_layout()
        temporary = Path(tempfile.mkdtemp(prefix="packet-", dir=self.root))
        try:
            json_path = save_debug_observation(packet, temporary)
            return self.import_debug(json_path, tags=tags)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def save_annotation(self, annotation: Annotation) -> CorpusEntry:
        annotation.validate()
        manifest = self.load_manifest()
        entry = next((item for item in manifest.entries if item.observation_id == annotation.observation_id), None)
        if entry is None:
            raise ValueError("L'observation annotée n'existe pas dans le manifeste")
        previous = self.read_annotation(entry)
        if previous is not None and (previous.enemy_cells_truth != annotation.enemy_cells_truth
                                     or previous.enemy_occluded_tracks != annotation.enemy_occluded_tracks):
            self.confirm_tracking_sequence(self.tracking_sequence_id(entry), confirmed=False)
            annotation = replace(annotation, tracking_identity_confirmed=False, tracking_identity_source=None,
                                 tracking_confirmed_at=None, tracking_sequence_id=None)
        observation_path = self.resolve(entry.paths["observation"])
        annotation_path = observation_path.parent / "annotation.json"
        _write_json_atomic(annotation_path, annotation.to_dict())
        relative = annotation_path.relative_to(self.root).as_posix()
        errors = set(entry.known_errors) - {"1_vs_7"}
        if annotation.digit_issue:
            errors.add(annotation.digit_issue)
        entity = annotation.entities_confirmed
        updated = replace(entry, paths={**entry.paths, "annotation": relative}, annotation_available=True,
                          ap_truth=annotation.ap_truth, mp_truth=annotation.mp_truth,
                          known_errors=tuple(sorted(errors)),
                          player_cell_id_truth=annotation.player_cell_id_truth if entity else None,
                          enemy_cells_truth=tuple(int(item["cell_id"]) for item in annotation.enemy_cells_truth)
                          if entity else (),
                          enemy_track_truth={str(item["track_id"]): int(item["cell_id"])
                                             for item in annotation.enemy_cells_truth if item.get("track_id")}
                          if entity else None,
                          entity_annotation_source=annotation.entity_annotation_source if entity else None)
        entries = tuple(updated if item.observation_id == updated.observation_id else item for item in manifest.entries)
        self.save_manifest(CorpusManifest(entries))
        return updated

    def tracking_sequence_id(self, entry: CorpusEntry) -> str:
        document = self.read_observation(entry)
        return f"{entry.session_id}|map{(document.get('grid_snapshot') or {}).get('map_id_declared')}"

    def tracking_sequence_entries(self, sequence_id: str) -> tuple[CorpusEntry, ...]:
        session = sequence_id.split("|map", 1)[0]
        return tuple(e for e in self.list_entries() if e.session_id == session
                     and self.tracking_sequence_id(e) == sequence_id)

    def tracking_sequence_confirmed(self, sequence_id: str) -> bool:
        annotations = [self.read_annotation(e) for e in self.tracking_sequence_entries(sequence_id)]
        return bool(annotations) and all(a and a.entities_confirmed and a.tracking_identity_confirmed
            and a.tracking_identity_source == "human_ui_review" and a.tracking_confirmed_at
            and a.tracking_sequence_id == sequence_id for a in annotations)

    def confirm_tracking_sequence(self, sequence_id: str, *, confirmed: bool) -> int:
        """Revue humaine explicite de toute la séquence. Positions/HUD/manifeste intacts.

        Les écritures de chaque JSON sont atomiques ; retour intégral aux octets précédents en
        cas d'échec. Une annotation d'identité modifiée révoque la confirmation de toute la séquence.
        """
        entries = self.tracking_sequence_entries(sequence_id)
        if not entries:
            raise ValueError("Séquence absente")
        changes = []
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        for entry in entries:
            previous = self.read_annotation(entry)
            if not previous or not previous.entities_confirmed:
                if confirmed:
                    raise ValueError("Toutes les frames de la séquence doivent être annotées avant confirmation")
                continue
            updated = replace(previous, tracking_identity_confirmed=confirmed,
                              tracking_identity_source="human_ui_review" if confirmed else None,
                              tracking_confirmed_at=stamp if confirmed else None,
                              tracking_sequence_id=sequence_id if confirmed else None)
            if updated != previous:
                path = self.resolve(entry.paths["annotation"])
                changes.append((path, path.read_bytes(), updated.to_dict()))
        written = []
        try:
            for path, previous_bytes, value in changes:
                written.append((path, previous_bytes))
                _write_json_atomic(path, value)
        except BaseException:
            for path, previous_bytes in reversed(written):
                temporary = path.with_suffix(".rollback")
                temporary.write_bytes(previous_bytes)
                os.replace(temporary, path)
            raise
        return len(changes)

    def confirm_hud_truth(self, observation_id: str, *, ap: int | None, mp: int | None,
                          ap_unreadable: bool = False, mp_unreadable: bool = False,
                          ap_crop_quality: str | None = None, mp_crop_quality: str | None = None,
                          confirmed_by: str = "user", confirmed_at: str | None = None) -> Annotation:
        """Enregistre une décision humaine PA/PM traçable.

        L'ancienne vérité active reste dans ``truth_history`` ; le split n'est jamais recalculé ici.
        """
        entry = self.get_entry(observation_id)
        previous = self.read_annotation(entry) or Annotation(observation_id)
        stamp = confirmed_at or datetime.now().astimezone().isoformat(timespec="seconds")
        values = {"ap": None if ap_unreadable else ap, "mp": None if mp_unreadable else mp}
        unreadable = {"ap": ap_unreadable, "mp": mp_unreadable}
        before = {"ap": previous.ap_truth, "mp": previous.mp_truth}
        review: dict[str, str] = {}
        for kind in ("ap", "mp"):
            if unreadable[kind]:
                review[kind] = "unreadable"
            elif values[kind] is None:
                raise ValueError(f"{kind.upper()} : saisissez une valeur ou marquez le crop illisible")
            elif before[kind] is None:
                review[kind] = "entered"
            else:
                review[kind] = "confirmed" if before[kind] == values[kind] else "corrected"
        history = previous.truth_history
        if before != values or previous.truth_source != "human_confirmed":
            history = history + ({
                "ap_truth": before["ap"], "mp_truth": before["mp"],
                "truth_source": previous.truth_source or "unverified_import",
                "confirmed_at": previous.confirmed_at, "replaced_at": stamp,
                "comments": previous.comments,
            },)
        annotation = replace(
            previous, ap_truth=values["ap"], mp_truth=values["mp"],
            ap_crop_quality=ap_crop_quality if ap_crop_quality is not None else previous.ap_crop_quality,
            mp_crop_quality=mp_crop_quality if mp_crop_quality is not None else previous.mp_crop_quality,
            truth_source="human_confirmed", confirmed_at=stamp, confirmed_by=confirmed_by,
            session_id=entry.session_id, hud_review=review, truth_history=history,
        )
        self.save_annotation(annotation)
        return annotation

    def confirm_entities(self, observation_id: str, *, player_cell_id: int | None, player_visible: bool,
                         enemies: list[tuple[int, str | None]], occluded_tracks: list[str] = (),
                         empty_cells: list[int] = (), frame_phase: str | None = None,
                         occlusion: bool | None = None, tactical_mode: bool | None = None,
                         confirmed_at: str | None = None, tracking_identity_source: str | None = None,
                         sampled_cells: list[tuple[int, str]] | None = None,
                         sampled_cells_version: str | None = None, annotation_mode: str | None = None,
                         suggestion_snapshot: dict | None = None, suggestion_review: dict | None = None,
                         confirmed_by: str = "user") -> Annotation:
        """Enregistre une vérité entités humaine ; les champs HUD et grille restent intacts.

        ``sampled_cells`` (LOT 3B-5D) : décisions EMPTY/OCCUPIED/UNKNOWN sur l'échantillon
        déterministe ; ``None`` conserve l'échantillon déjà enregistré.
        """
        entry = self.get_entry(observation_id)
        previous = self.read_annotation(entry) or Annotation(observation_id)
        stamp = confirmed_at or datetime.now().astimezone().isoformat(timespec="seconds")
        annotation = replace(
            previous, entity_annotation_source="human_confirmed", entity_confirmed_at=stamp,
            player_cell_id_truth=player_cell_id if player_visible else None,
            player_visibility="VISIBLE" if player_visible else "NOT_VISIBLE",
            enemy_cells_truth=tuple({"cell_id": int(cell), "track_id": track or None} for cell, track in enemies),
            enemy_occluded_tracks=tuple(occluded_tracks), empty_confirmed_cells=tuple(int(c) for c in empty_cells),
            frame_phase=frame_phase, occlusion=occlusion, tactical_mode=tactical_mode,
            tracking_identity_confirmed=bool(tracking_identity_source) or previous.tracking_identity_confirmed,
            tracking_identity_source=tracking_identity_source or previous.tracking_identity_source,
            session_id=previous.session_id or entry.session_id,
            annotation_mode=annotation_mode, suggestion_snapshot=suggestion_snapshot,
            suggestion_review=suggestion_review, entity_confirmed_by=confirmed_by,
        )
        if sampled_cells is not None:
            annotation = replace(
                annotation, sampled_cells_truth=tuple({"cell_id": int(cell), "label": label}
                                                      for cell, label in sampled_cells),
                sampled_cells_version=sampled_cells_version if sampled_cells else None)
        self.save_annotation(annotation)
        return annotation

    def hud_review_summary(self) -> dict[str, object]:
        """Compte les décisions humaines PA/PM ; tout le reste est « non traité »."""
        counts = {kind: {"confirmed": 0, "corrected": 0, "entered": 0, "unreadable": 0, "untreated": 0}
                  for kind in ("ap", "mp")}
        observations = {"human_confirmed": 0, "untreated": 0}
        for entry in self.list_entries():
            if not ({"ap_crop", "mp_crop"} & set(entry.paths)):
                continue
            annotation = self.read_annotation(entry)
            confirmed = annotation is not None and annotation.human_confirmed
            observations["human_confirmed" if confirmed else "untreated"] += 1
            for kind in ("ap", "mp"):
                if f"{kind}_crop" not in entry.paths:
                    continue
                decision = (annotation.hud_review or {}).get(kind) if confirmed and annotation else None
                counts[kind][decision or "untreated"] += 1
        return {"observations": observations, "counters": counts}

    def promote_fixture(self, observation_id: str, stable_name: str,
                        destination_root: Path | None = None) -> Path:
        """Copie explicite d'une observation annotée vers les fixtures de non-régression."""
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", stable_name):
            raise ValueError("Utilisez un nom stable en minuscules, chiffres, _ ou -")
        entry = self.get_entry(observation_id)
        annotation = self.read_annotation(entry)
        if annotation is None:
            raise ValueError("Une observation doit être annotée avant sa promotion")
        source = self.resolve(entry.paths["observation"]).parent
        root = (destination_root or (PROJECT_ROOT / "tests" / "fixtures" / "combat_real")).resolve()
        destination = root / stable_name
        if destination.exists():
            raise ValueError(f"La fixture existe déjà : {stable_name}")
        root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        fixture_manifest = {
            "schema_version": SCHEMA_VERSION,
            "source_observation_id": observation_id,
            "usage": "test",
            "promoted_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_atomic(destination / "fixture.json", fixture_manifest)
        return destination
