"""Inventaire et banc du corpus PA/PM, fondés uniquement sur les annotations humaines."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from statistics import mean
import time
from typing import Any, Literal

import cv2
import numpy as np

from combatbot.corpus.repository import CorpusRepository
from combatbot.vision.hud_reader import (
    GlyphTemplateLibrary, HUDReader, distinguish_one_seven, segment_glyphs,
)


HUDSplit = Literal["train", "validation", "test"]


@dataclass(frozen=True)
class HUDSample:
    observation_id: str
    session_id: str
    map_id: int | None
    kind: Literal["AP", "MP"]
    truth: int | None
    crop_path: str
    client_size: tuple[int, int] | None
    layout_signature: str | None
    group_id: str
    crop_quality: str | None
    split: HUDSplit
    truth_source: str | None = None
    split_registered: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id, "session_id": self.session_id,
            "map_id": self.map_id, "type": self.kind, "truth": self.truth,
            "crop_path": self.crop_path,
            "client_size": list(self.client_size) if self.client_size else None,
            "layout_signature": self.layout_signature, "split": self.split.upper(),
            "group_id": self.group_id, "crop_quality": self.crop_quality,
            "truth_source": self.truth_source, "split_registered": self.split_registered,
        }


SPLIT_REGISTRY = "hud_split_registry.json"
_SPLIT_ORDER = {"train": 2, "validation": 1, "test": 0}


def _session_split(session_id: str) -> HUDSplit:
    bucket = int(hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "train" if bucket < 70 else ("validation" if bucket < 85 else "test")


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _explicit_usages(entries, group_by_observation: dict[str, str]) -> dict[str, HUDSplit]:
    explicit_by_group: dict[str, set[str]] = defaultdict(set)
    for entry in entries:
        if entry.usage in ("train", "validation", "test"):
            explicit_by_group[group_by_observation[entry.observation_id]].add(entry.usage)
    result: dict[str, HUDSplit] = {}
    for group_id, usages in explicit_by_group.items():
        if len(usages) > 1:
            raise ValueError(f"Fuite temporelle : le groupe {group_id} traverse plusieurs splits")
        result[group_id] = next(iter(usages))  # type: ignore[assignment]
    return result


def _assign_splits(group_digits: dict[str, set[str]],
                   fixed: dict[str, HUDSplit] | None = None) -> dict[str, HUDSplit]:
    """Répartit des groupes indépendants en 70/15/15, sans jamais déplacer un groupe fixé.

    Les groupes sont classés par hash. Avant le remplissage, les chiffres rares sont
    stratifiés : un chiffre observé doit exister en TRAIN (templates) ; dès deux groupes
    indépendants, aussi en TEST ; dès trois groupes, aussi en VALIDATION. Les vérités ne servent qu'à cette
    répartition, jamais à régler un seuil.
    """
    fixed = dict(fixed or {})
    group_ids = sorted(set(group_digits) | set(fixed), key=_hash_key)
    count = len(group_ids)
    if count < 3:
        targets = {"train": max(1, count - 1), "validation": 0, "test": 1 if count == 2 else 0}
    else:
        validation = max(1, round(count * 0.15))
        test = max(1, round(count * 0.15))
        targets = {"train": max(1, count - validation - test), "validation": validation, "test": test}
    result: dict[str, HUDSplit] = dict(fixed)
    current = Counter(result.values())
    holders: dict[str, list[str]] = defaultdict(list)
    for group_id in group_ids:
        for digit in group_digits.get(group_id, ()):
            holders[digit].append(group_id)
    for digit in sorted(holders, key=lambda value: (len(holders[value]), value)):
        groups = holders[digit]
        # Un chiffre vu dans un seul groupe va d'abord en TRAIN : il ne peut pas être à la fois
        # appris et testé ; on l'apprend, il sera testé quand un groupe indépendant arrivera.
        wanted = [split for split, minimum in (("train", 1), ("test", 2), ("validation", 3))
                  if len(groups) >= minimum]
        for split in wanted:
            if any(result.get(group_id) == split for group_id in groups):
                continue
            candidate = next((group_id for group_id in groups if group_id not in result), None)
            if candidate is None:
                break
            result[candidate] = split  # type: ignore[assignment]
            current[split] += 1
    for group_id in group_ids:
        if group_id in result:
            continue
        deficits = {name: targets[name] - current[name] for name in ("train", "validation", "test")}
        split = max(deficits, key=lambda name: (deficits[name], _SPLIT_ORDER[name]))
        result[group_id] = split  # type: ignore[assignment]
        current[split] += 1
    return result


def _group_splits(entries, group_by_observation: dict[str, str]) -> dict[str, HUDSplit]:
    """Compatibilité 3B-4R : répartition sans vérité, usages explicites prioritaires."""
    fixed = _explicit_usages(entries, group_by_observation)
    return _assign_splits({group: set() for group in group_by_observation.values()}, fixed)


def load_split_registry(repository: CorpusRepository) -> dict[str, Any]:
    path = repository.manifests / SPLIT_REGISTRY
    if not path.is_file():
        return {"schema_version": 1, "frozen_test": [], "groups": {}}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if int(raw.get("schema_version", 0)) != 1:
        raise ValueError("Version de registre de split HUD inconnue")
    return {"schema_version": 1, "frozen_test": list(raw.get("frozen_test", ())),
            "groups": dict(raw.get("groups", {})), **{key: value for key, value in raw.items()
                                                       if key not in ("schema_version", "frozen_test", "groups")}}


COLLECTION_CONTINUITY_SECONDS = 90.0


def _collection_timelines(repository: CorpusRepository, entries) -> list[list]:
    """Captures de la collecte HUD, triées par heure réelle, coupées après 90 s sans capture.

    Rouvrir la fenêtre de collecte crée une nouvelle session : la continuité temporelle doit
    donc être suivie entre sessions, pas seulement à l'intérieur d'une session.
    """
    stamped = []
    for entry in entries:
        if "hud-collection" not in entry.tags:
            continue
        try:
            created = datetime.fromisoformat(str(repository.read_observation(entry).get("created_at")))
        except (OSError, ValueError, TypeError):
            continue
        stamped.append((created, entry))
    stamped.sort(key=lambda item: (item[0], item[1].observation_id))
    timelines: list[list] = []
    previous = None
    for created, entry in stamped:
        if previous is None or (created - previous).total_seconds() > COLLECTION_CONTINUITY_SECONDS:
            timelines.append([])
        timelines[-1].append(entry)
        previous = created
    return timelines


def _merge_continuous_counters(entries, annotations, group_by_observation: dict[str, str],
                               timelines: list[list] | None = None) -> dict[str, str]:
    """Fusionne les groupes consécutifs qui partagent un compteur inchangé.

    Deux captures voisines où le PM reste à 3 montrent le même compteur immobile, même
    si le PA a changé : ce ne sont pas deux preuves indépendantes du PM, et elles ne
    doivent pas se retrouver l'une en TRAIN, l'autre en TEST. Les voisins sont pris dans
    l'ordre des frames de chaque session, puis dans les ``timelines`` chronologiques.
    """
    parent = {group: group for group in group_by_observation.values()}

    def find(group: str) -> str:
        while parent[group] != group:
            parent[group] = parent[parent[group]]
            group = parent[group]
        return group

    by_session: dict[str, list] = defaultdict(list)
    for entry in entries:
        by_session[entry.session_id].append(entry)
    sequences = [sorted(session_entries, key=lambda item: (item.frame_index, item.observation_id))
                 for session_entries in by_session.values()] + list(timelines or ())
    for ordered in sequences:
        for previous, current in zip(ordered, ordered[1:]):
            before, after = annotations[previous.observation_id], annotations[current.observation_id]
            if before is None or after is None:
                continue
            shared = any(first is not None and first == second for first, second in (
                (before.ap_truth, after.ap_truth), (before.mp_truth, after.mp_truth)))
            if shared:
                first, second = (find(group_by_observation[previous.observation_id]),
                                 find(group_by_observation[current.observation_id]))
                if first != second:
                    parent[max(first, second)] = min(first, second)
    members: dict[str, set[str]] = defaultdict(set)
    for group in parent:
        members[find(group)].add(group)
    names = {root: "+".join(sorted(groups)) for root, groups in members.items()}
    return {observation: names[find(group)] for observation, group in group_by_observation.items()}


def _confirmed_truths(annotation, require_human: bool) -> tuple[int | None, int | None]:
    if annotation is None or (require_human and not annotation.human_confirmed):
        return None, None
    return annotation.ap_truth, annotation.mp_truth


def _groups(repository: CorpusRepository, entries, annotations) -> dict[str, str]:
    def base_group(entry) -> str:
        annotation = annotations[entry.observation_id]
        if annotation is not None and annotation.hud_burst_id:
            return annotation.hud_burst_id
        # Une capture de collecte est horodatée : elle part seule, puis la continuité
        # (compteur inchangé entre captures voisines) la rattache à ses voisines.
        # Sans horodatage fiable (sessions anciennes), la session entière reste un groupe.
        return entry.observation_id if "hud-collection" in entry.tags else entry.session_id

    group_by_observation = {entry.observation_id: base_group(entry) for entry in entries}
    # Les valeurs inscrites, même non vérifiées, servent seulement à regrouper : c'est prudent.
    return _merge_continuous_counters(entries, annotations, group_by_observation,
                                      _collection_timelines(repository, entries))


def _group_digits(entries, annotations, group_by_observation, require_human: bool) -> dict[str, set[str]]:
    """Chiffres par groupe, qualifiés par compteur (``AP:2``, ``MP:2``).

    Les templates sont appris séparément pour PA et PM : un 2 PM en TRAIN n'apprend pas le 2 PA.
    La stratification doit donc garantir chaque chiffre de chaque compteur en TRAIN.
    """
    digits: dict[str, set[str]] = defaultdict(set)
    for entry in entries:
        truths = _confirmed_truths(annotations[entry.observation_id], require_human)
        for field, truth in zip(("AP", "MP"), truths):
            if truth is not None:
                digits[group_by_observation[entry.observation_id]].update(f"{field}:{digit}" for digit in str(truth))
    return digits


def ensure_split_registry(repository: CorpusRepository) -> dict[str, Any]:
    """Crée le registre au premier usage en gelant les groupes TEST du dernier manifeste HUD.

    Appelé avant toute réécriture de ``hud_manifest.json`` : le TEST 3B-4R ne peut pas être perdu.
    """
    path = repository.manifests / SPLIT_REGISTRY
    if path.is_file():
        return load_split_registry(repository)
    frozen: set[str] = set()
    previous_manifest = repository.manifests / "hud_manifest.json"
    if previous_manifest.is_file():
        raw = json.loads(previous_manifest.read_text(encoding="utf-8"))
        frozen = {str(sample["group_id"]) for sample in raw.get("samples", ())
                  if str(sample.get("split", "")).lower() == "test" and sample.get("group_id")}
    registry = {"schema_version": 1, "frozen_test": sorted(frozen), "groups": {},
                "seeded_from": "hud_manifest.json" if frozen else None,
                "seeded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    repository.manifests.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return registry


def _members(group_id: str) -> set[str]:
    return set(group_id.split("+"))


def _project_registry(registry: dict[str, Any], known: set[str]) -> tuple[dict[str, HUDSplit], set[str], list[str]]:
    """Projette le registre sur les groupes actuels par leurs membres.

    Une correction humaine peut fusionner deux groupes (ex. PM 6 corrigé en 1 relie deux
    captures voisines) et changer leur identifiant. TEST gelé l'emporte : un groupe qui
    contient un seul membre gelé reste en TEST. Sinon, un groupe garde le split commun à
    tous ses membres enregistrés ; en cas de désaccord, il redevient provisoire.
    """
    frozen_ids = set(registry["frozen_test"]) | {group for group, split in registry["groups"].items()
                                                 if split == "test"}
    frozen_members = set().union(*(_members(group) for group in frozen_ids)) if frozen_ids else set()
    member_split = {member: split for group, split in registry["groups"].items() for member in _members(group)}
    projected: dict[str, HUDSplit] = {}
    frozen_now: set[str] = set()
    for group in known:
        members = _members(group)
        if members & frozen_members:
            projected[group] = "test"
            frozen_now.add(group)
            continue
        splits = {member_split[member] for member in members if member in member_split}
        if len(splits) == 1 and members <= set(member_split):
            projected[group] = splits.pop()  # type: ignore[assignment]
    present = set().union(*(_members(group) for group in known)) if known else set()
    missing = sorted(frozen_members - present)
    return projected, frozen_now, missing


def build_split_registry(repository: CorpusRepository, *, require_human: bool = True) -> dict[str, Any]:
    """Reconstruit explicitement le split : TEST gelé conservé, le reste stratifié.

    Au premier appel, les groupes TEST du dernier ``hud_manifest.json`` (3B-4R) sont gelés.
    """
    entries = repository.list_entries()
    annotations = {entry.observation_id: repository.read_annotation(entry) for entry in entries}
    group_by_observation = _groups(repository, entries, annotations)
    registry = ensure_split_registry(repository)
    known = set(group_by_observation.values())
    # Un groupe placé une fois en TEST y reste, même fusionné : TEST n'est jamais recyclé.
    _projected, frozen_now, missing_frozen = _project_registry(registry, known)
    frozen = set(registry["frozen_test"]) | frozen_now
    fixed = _explicit_usages(entries, group_by_observation)
    for group_id in frozen_now:
        if fixed.get(group_id, "test") != "test":
            raise ValueError(f"Le groupe TEST gelé {group_id} porte un usage explicite contraire")
        fixed[group_id] = "test"
    digits = _group_digits(entries, annotations, group_by_observation, require_human)
    labelled = set(digits)
    assignment = _assign_splits({group: digits[group] for group in labelled},
                                {group: split for group, split in fixed.items() if group in labelled})
    unlabelled = known - labelled
    if unlabelled:
        assignment.update(_assign_splits({group: set() for group in unlabelled},
                                         {group: split for group, split in fixed.items()
                                          if group in unlabelled}))
    result = {
        "schema_version": 1,
        "policy": ("groupes temporels ; TEST gelé ; chiffres rares stratifiés TRAIN puis TEST puis "
                   "VALIDATION ; reste hash 70/15/15"),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "frozen_test": sorted(frozen),
        "missing_frozen_test": missing_frozen,
        "groups": dict(sorted(assignment.items())),
    }
    repository.manifests.mkdir(parents=True, exist_ok=True)
    (repository.manifests / SPLIT_REGISTRY).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def inventory(repository: CorpusRepository, *, require_human: bool = True) -> tuple[HUDSample, ...]:
    """Crops PA/PM avec vérité humaine confirmée seulement (``require_human``).

    Le split vient du registre explicite. Un groupe absent du registre reçoit une affectation
    provisoire (``split_registered=False``) sans jamais déplacer un groupe enregistré ou gelé.
    """
    samples: list[HUDSample] = []
    entries = repository.list_entries()
    annotations = {entry.observation_id: repository.read_annotation(entry) for entry in entries}
    group_by_observation = _groups(repository, entries, annotations)
    registry = load_split_registry(repository)
    fixed = _explicit_usages(entries, group_by_observation)
    known = set(group_by_observation.values())
    projected, _frozen_now, _missing = _project_registry(registry, known)
    for group_id, split in projected.items():
        if fixed.get(group_id, split) != split:
            raise ValueError(f"Fuite temporelle : le groupe {group_id} contredit le registre")
        fixed[group_id] = split
    registered = set(fixed)
    digits = _group_digits(entries, annotations, group_by_observation, require_human)
    labelled_groups = set(digits)
    split_by_group: dict[str, HUDSplit] = {}
    for selected_groups in (labelled_groups, known - labelled_groups):
        if not selected_groups:
            continue
        split_by_group.update(_assign_splits(
            {group: digits.get(group, set()) for group in selected_groups},
            {group: split for group, split in fixed.items() if group in selected_groups}))
    for entry in entries:
        try:
            document = repository.read_observation(entry)
            annotation = annotations[entry.observation_id]
        except (OSError, ValueError, KeyError):
            continue
        capture = document.get("capture", {})
        capture = capture if isinstance(capture, dict) else {}
        calibration = capture.get("calibration", {})
        calibration = calibration if isinstance(calibration, dict) else {}
        size = capture.get("client_size")
        client_size = ((int(size[0]), int(size[1]))
                       if isinstance(size, (list, tuple)) and len(size) == 2 else None)
        group_id = group_by_observation[entry.observation_id]
        split = split_by_group[group_id]
        ap_truth, mp_truth = _confirmed_truths(annotation, require_human)
        source = (annotation.truth_source or "unverified_import") if annotation else None
        for kind, key, truth, quality in (
            ("AP", "ap_crop", ap_truth, annotation.ap_crop_quality if annotation else None),
            ("MP", "mp_crop", mp_truth, annotation.mp_crop_quality if annotation else None),
        ):
            path = entry.paths.get(key)
            if not path:
                continue
            samples.append(HUDSample(
                entry.observation_id, entry.session_id,
                int(capture["map_id_declared"]) if capture.get("map_id_declared") is not None else None,
                kind, truth, path, client_size,
                str(calibration["layout_signature"]) if calibration.get("layout_signature") else None,
                group_id, quality,
                split, source, group_id in registered,
            ))
    return tuple(samples)


def write_manifest(repository: CorpusRepository, samples: tuple[HUDSample, ...]) -> Path:
    repository.manifests.mkdir(parents=True, exist_ok=True)
    path = repository.manifests / "hud_manifest.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "split_policy": ("temporal groups (burst + unchanged consecutive counter); registry "
                         f"{SPLIT_REGISTRY} and frozen TEST win; rare digits stratified; hash 70/15/15"),
        "samples": [sample.to_dict() for sample in samples],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_templates(repository: CorpusRepository, samples: tuple[HUDSample, ...]) -> tuple[GlyphTemplateLibrary, list[str]]:
    library, issues = GlyphTemplateLibrary(), []
    seen: set[tuple[str, int, bytes]] = set()
    for sample in samples:
        if sample.split != "train" or sample.truth is None:
            continue
        image = cv2.imread(str(repository.resolve(sample.crop_path)), cv2.IMREAD_COLOR)
        if image is None:
            issues.append(f"{sample.observation_id}/{sample.kind}: crop illisible")
            continue
        glyphs = segment_glyphs(image)
        digits = [int(value) for value in str(sample.truth)]
        if len(glyphs) != len(digits):
            issues.append(
                f"{sample.observation_id}/{sample.kind}: {len(glyphs)} glyphe(s), vérité {sample.truth}"
            )
            continue
        for digit, glyph in zip(digits, glyphs):
            signature = (sample.kind, digit, hashlib.sha256(glyph.image.tobytes()).digest())
            if signature in seen:
                continue
            seen.add(signature)
            library.add(sample.kind, digit, glyph.image,
                        source=f"{sample.observation_id}/{sample.kind}")
    return library, issues


def _classification_metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    accepted = [row for row in rows if row["predicted"] is not None]
    correct = sum(row["truth"] == row["predicted"] for row in rows)
    accepted_correct = sum(row["truth"] == row["predicted"] for row in accepted)
    confusion: dict[str, dict[str, int]] = defaultdict(dict)
    digit_confusion: dict[str, dict[str, int]] = {str(digit): {} for digit in range(10)}
    digit_pairs: list[tuple[str, str]] = []
    for row in rows:
        truth, predicted = str(row["truth"]), row["predicted"]
        label = "UNKNOWN" if predicted is None else str(predicted)
        confusion[truth][label] = confusion[truth].get(label, 0) + 1
        if predicted is not None and len(truth) == len(str(predicted)):
            pairs = list(zip(truth, str(predicted)))
            digit_pairs.extend(pairs)
        else:
            # Un nombre inconnu (ou de longueur différente) ne prouve aucun chiffre.
            pairs = [(digit, "UNKNOWN" if predicted is None else "LENGTH_MISMATCH") for digit in truth]
        for digit, predicted_digit in pairs:
            cell = digit_confusion[digit]
            cell[predicted_digit] = cell.get(predicted_digit, 0) + 1

    def precision_recall(digit: str) -> dict[str, float | None]:
        tp = sum(t == digit and p == digit for t, p in digit_pairs)
        fp = sum(t != digit and p == digit for t, p in digit_pairs)
        fn = sum(t == digit and p != digit for t, p in digit_pairs)
        return {
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
        }

    return {
        "examples": len(rows), "accuracy": correct / len(rows) if rows else None,
        "accepted_accuracy": accepted_correct / len(accepted) if accepted else None,
        "coverage": len(accepted) / len(rows) if rows else None,
        "unknown": len(rows) - len(accepted),
        "unknown_rate": (len(rows) - len(accepted)) / len(rows) if rows else None,
        "confusion_matrix": dict(confusion),
        "digit_confusion": {digit: cells for digit, cells in digit_confusion.items() if cells},
        "errors_1_to_7": sum(t == "1" and p == "7" for t, p in digit_pairs),
        "errors_7_to_1": sum(t == "7" and p == "1" for t, p in digit_pairs),
        "one_to_unknown": digit_confusion["1"].get("UNKNOWN", 0),
        "seven_to_unknown": digit_confusion["7"].get("UNKNOWN", 0),
        "digit_1": precision_recall("1"), "digit_7": precision_recall("7"),
        "margins": [row["margin"] for row in rows],
    }


def _mode_metrics(rows_by_split: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    rows = [row for split_rows in rows_by_split.values() for row in split_rows]
    return {
        "splits": {name: _classification_metrics(split_rows)
                   for name, split_rows in rows_by_split.items()},
        "global": _classification_metrics(rows),
        "by_kind": {
            kind: _classification_metrics([row for row in rows if row["kind"] == kind])
            for kind in ("AP", "MP")
        },
    }


def one_seven_verdict(labelled: tuple[HUDSample, ...] | list[HUDSample],
                      test_metrics: dict[str, object],
                      test_rows: list[dict[str, object]] | None = None) -> dict[str, object]:
    """Critère 3B-4R2 §16 : groupes indépendants de 1 et de 7, template 7 issu de TRAIN,
    1 et 7 en TEST, aucune confusion 1 ↔ 7 en TEST.

    Un UNKNOWN causé par un autre chiffre (ex. le « 2 » de « 12 ») ne déclasse pas 1/7 ;
    une vraie ambiguïté ``AMBIGUOUS_1_7`` en TEST donne IMPROVED. La couverture est rapportée.
    """
    groups: dict[str, dict[str, set[str]]] = {
        digit: defaultdict(set) for digit in ("1", "7")
    }
    for sample in labelled:
        for digit in ("1", "7"):
            if digit in str(sample.truth):
                groups[digit][sample.split].add(sample.group_id)
    counts = {digit: {split: len(groups[digit].get(split, ())) for split in ("train", "validation", "test")}
              for digit in ("1", "7")}
    missing = []
    for digit in ("1", "7"):
        total = len(set().union(*groups[digit].values())) if groups[digit] else 0
        if total < 2:
            missing.append(f"moins de deux groupes indépendants contenant {digit}")
        if not counts[digit]["train"]:
            missing.append(f"aucun {digit} en TRAIN pour construire un template")
        if not counts[digit]["test"]:
            missing.append(f"aucun {digit} en TEST")
    digit_confusion = test_metrics.get("digit_confusion", {})
    assert isinstance(digit_confusion, dict)
    confusions = int(test_metrics.get("errors_1_to_7", 0)) + int(test_metrics.get("errors_7_to_1", 0))
    unknown = int(test_metrics.get("one_to_unknown", 0)) + int(test_metrics.get("seven_to_unknown", 0))
    ambiguous = sum(row.get("reason") == "AMBIGUOUS_1_7" for row in (test_rows or ()))
    if missing or confusions:
        verdict = "NOT VALIDATED"
    else:
        verdict = "IMPROVED" if ambiguous else "VALIDATED"

    def coverage(digit: str) -> float | None:
        cells = digit_confusion.get(digit, {})
        total = sum(cells.values())
        return cells.get(digit, 0) / total if total else None

    return {"verdict": verdict, "independent_groups": counts, "missing": missing,
            "test_confusions_1_7": confusions, "test_unknown_1_7": unknown,
            "test_ambiguous_1_7": ambiguous,
            "test_coverage_1": coverage("1"), "test_coverage_7": coverage("7"),
            "test_digit_1": digit_confusion.get("1", {}), "test_digit_7": digit_confusion.get("7", {})}


def split_distribution(samples: tuple[HUDSample, ...] | list[HUDSample]) -> dict[str, Any]:
    """Crops et groupes indépendants par split, compteur, valeur et chiffre (vérités humaines)."""

    def table(items) -> dict[str, Any]:
        values: dict[str, dict[str, set]] = defaultdict(lambda: {"crops": set(), "groups": set()})
        digits: dict[str, dict[str, set]] = defaultdict(lambda: {"crops": set(), "groups": set()})
        for sample in items:
            key = (sample.observation_id, sample.kind)
            values[str(sample.truth)]["crops"].add(key)
            values[str(sample.truth)]["groups"].add(sample.group_id)
            for digit in set(str(sample.truth)):
                digits[digit]["crops"].add(key)
                digits[digit]["groups"].add(sample.group_id)

        def count(mapping) -> dict[str, dict[str, int]]:
            return {name: {"crops": len(cell["crops"]), "groups": len(cell["groups"])}
                    for name, cell in sorted(mapping.items(), key=lambda item: int(item[0]))}

        return {"crops": len(items), "groups": len({sample.group_id for sample in items}),
                "values": count(values), "digits": count(digits)}

    labelled = [sample for sample in samples if sample.truth is not None]
    result: dict[str, Any] = {}
    for split in ("train", "validation", "test"):
        in_split = [sample for sample in labelled if sample.split == split]
        result[split] = {kind: table([sample for sample in in_split if sample.kind == kind])
                         for kind in ("AP", "MP")}
        result[split]["groups"] = len({sample.group_id for sample in in_split})
    everything = table(labelled)
    result["global"] = {"crops": everything["crops"], "groups": everything["groups"],
                        "digits": {str(digit): everything["digits"].get(str(digit), {"crops": 0, "groups": 0})
                                   for digit in range(10)}}
    return result


def distribution_text(samples) -> str:
    """Distribution affichée avant tout benchmark : crops / groupes indépendants."""
    table = split_distribution(samples)
    lines = ["Distribution des vérités humaines (crops/groupes)"]
    for split in ("train", "validation", "test"):
        lines.append(f"  {split.upper()} — {table[split]['groups']} groupe(s)")
        for kind in ("AP", "MP"):
            cell = table[split][kind]
            values = ", ".join(f"{value}:{count['crops']}/{count['groups']}"
                               for value, count in cell["values"].items()) or "—"
            digits = ", ".join(f"{digit}:{count['crops']}/{count['groups']}"
                               for digit, count in cell["digits"].items()) or "—"
            lines.append(f"    {kind} valeurs [{values}]")
            lines.append(f"    {kind} chiffres [{digits}]")
    global_digits = table["global"]["digits"]
    lines.append("  GLOBAL chiffres " + " ".join(
        f"{digit}:{global_digits[digit]['crops']}/{global_digits[digit]['groups']}" for digit in map(str, range(10))))
    return "\n".join(lines)


def digit_status(labelled, test_metrics: dict[str, object]) -> dict[str, str]:
    """NOT OBSERVED / OBSERVED_INSUFFICIENT / VALIDATED (TRAIN + TEST, TEST sans confusion)."""
    confusion = test_metrics.get("digit_confusion", {})
    assert isinstance(confusion, dict)
    status: dict[str, str] = {}
    for digit in map(str, range(10)):
        splits = {sample.split for sample in labelled if digit in str(sample.truth)}
        cells = confusion.get(digit, {})
        wrong = sum(count for label, count in cells.items() if label not in (digit, "UNKNOWN"))
        if not splits:
            status[digit] = "NOT OBSERVED"
        elif {"train", "test"} <= splits and cells.get(digit, 0) and not wrong:
            status[digit] = "VALIDATED"
        else:
            status[digit] = "OBSERVED_INSUFFICIENT"
    return status


def complete_numbers(labelled) -> dict[str, dict[str, int]]:
    """Nombres complets surveillés : PA=1 seul n'est pas le chiffre 1 de « 15 »."""
    wanted = {"AP=1": ("AP", 1), "AP=7": ("AP", 7), "MP=1": ("MP", 1), "MP=7": ("MP", 7)}
    result = {}
    for name, (kind, value) in wanted.items():
        items = [sample for sample in labelled if sample.kind == kind and sample.truth == value]
        result[name] = {"crops": len(items), "groups": len({sample.group_id for sample in items})}
    return result


def cross_split_identical_crops(repository: CorpusRepository, samples) -> dict[str, object]:
    """Crops pixel-identiques présents dans plusieurs splits (diagnostic).

    Le HUD est un rendu déterministe : deux combats indépendants au même compteur donnent
    souvent les mêmes pixels. L'indépendance est donc temporelle ; ce compte l'expose.
    """
    splits_by_hash: dict[tuple[str, bytes], set[str]] = defaultdict(set)
    values: dict[tuple[str, bytes], set[str]] = defaultdict(set)
    for sample in samples:
        if sample.truth is None:
            continue
        image = cv2.imread(str(repository.resolve(sample.crop_path)), cv2.IMREAD_COLOR)
        if image is None:
            continue
        key = (sample.kind, hashlib.sha256(image.tobytes()).digest())
        splits_by_hash[key].add(sample.split)
        values[key].add(str(sample.truth))
    shared = [key for key, splits in splits_by_hash.items() if len(splits) > 1]
    return {"identical_crop_images": len(splits_by_hash), "shared_between_splits": len(shared),
            "shared_values": sorted({f"{key[0]}={value}" for key in shared for value in values[key]})}


def truth_conflicts(repository: CorpusRepository, samples, *, max_difference: float = 0.5) -> list[dict[str, object]]:
    """Crops identiques ou quasi identiques annotés avec des valeurs différentes.

    Le HUD est un rendu déterministe : mêmes pixels = même valeur affichée. Un tel conflit
    révèle une erreur de saisie humaine (cas réel 3B-4R2 : un PM « 1 » saisi 6), qui ferait
    entrer dans TRAIN deux templates identiques pour deux chiffres différents.
    """
    from combatbot.corpus.hud_collection import crop_difference

    labelled = [sample for sample in samples if sample.truth is not None]
    images = {(sample.observation_id, sample.kind): cv2.imread(str(repository.resolve(sample.crop_path)),
                                                               cv2.IMREAD_COLOR)
              for sample in labelled}
    conflicts = []
    for index, first in enumerate(labelled):
        for second in labelled[index + 1:]:
            if first.kind != second.kind or first.truth == second.truth:
                continue
            difference = crop_difference(images[(first.observation_id, first.kind)],
                                         images[(second.observation_id, second.kind)])
            if difference <= max_difference:
                conflicts.append({"kind": first.kind, "difference": round(difference, 3),
                                  "a": {"observation_id": first.observation_id, "session_id": first.session_id,
                                        "truth": first.truth},
                                  "b": {"observation_id": second.observation_id, "session_id": second.session_id,
                                        "truth": second.truth}})
    return conflicts


def replay_transitions(repository: CorpusRepository, samples, library: GlyphTemplateLibrary) -> dict[str, Any]:
    """Rejoue le consensus temporel sur les vraies sessions, comparé à l'image annotée seulement."""
    from combatbot.vision.hud_reader import NumberTemporalTracker

    frames = {entry.observation_id: entry.frame_index for entry in repository.list_entries()}
    reader = HUDReader(library)
    sequences: dict[tuple[str, str], list[HUDSample]] = defaultdict(list)
    for sample in samples:
        if sample.truth is not None:
            sequences[(sample.session_id, sample.kind)].append(sample)
    summary: Counter[str] = Counter()
    rows = []
    for (session, kind), items in sorted(sequences.items()):
        if len(items) < 2:
            continue
        tracker = NumberTemporalTracker()
        previous_truth = None
        for sample in sorted(items, key=lambda item: frames.get(item.observation_id, 0)):
            image = cv2.imread(str(repository.resolve(sample.crop_path)), cv2.IMREAD_COLOR)
            if image is None:
                continue
            raw = reader.read(image, kind)
            output = tracker.update(raw)
            verdict = ("OK" if output.value == sample.truth
                       else "UNKNOWN" if output.value is None else "WRONG_OR_STALE")
            summary[verdict] += 1
            rows.append({"session_id": session, "kind": kind,
                         "frame_index": frames.get(sample.observation_id), "truth": sample.truth,
                         "transition": (f"{previous_truth}->{sample.truth}"
                                        if previous_truth is not None and previous_truth != sample.truth
                                        else None),
                         "raw": raw.value, "raw_reason": raw.reason.value,
                         "output": output.value, "output_reason": output.reason.value,
                         "verdict": verdict})
            previous_truth = sample.truth
    transitions = Counter(row["transition"] for row in rows if row["transition"])
    return {"summary": dict(summary), "transitions": dict(transitions), "rows": rows}


def run_hud_benchmark(repository: CorpusRepository, *, rapidocr: bool = False) -> dict[str, Any]:
    ensure_split_registry(repository)
    samples = inventory(repository)
    manifest_path = write_manifest(repository, samples)
    labelled = tuple(sample for sample in samples if sample.truth is not None)
    library, issues = build_templates(repository, labelled)
    if not library.empty:
        library.save(repository.root.parent / "hud_templates")
    rapid_reader = None
    if rapidocr:
        from combatbot.vision.combat_ocr import read_small_number
        rapid_reader = lambda image, minimum, maximum: read_small_number(image, minimum, maximum)
    specialized_reader = HUDReader(library)
    rapid_cache: dict[tuple[tuple[int, ...], bytes], tuple[int | None, float]] = {}
    combined_reader = HUDReader(
        library,
        rapidocr_reader=lambda image, minimum, maximum: rapid_cache[
            (tuple(image.shape), hashlib.sha256(image.tobytes()).digest())
        ],
        rapidocr_interval=0.0,
    ) if rapid_reader is not None else None
    mode_rows: dict[str, dict[str, list[dict[str, object]]]] = {
        mode: {name: [] for name in ("train", "validation", "test")}
        for mode in (("specialized", "rapidocr", "combined") if rapidocr else ("specialized",))
    }
    timings: dict[str, list[float]] = defaultdict(list)
    segmentation: dict[str, Counter[int]] = {"AP": Counter(), "MP": Counter()}
    for sample in samples:
        image = cv2.imread(str(repository.resolve(sample.crop_path)), cv2.IMREAD_COLOR)
        if image is None:
            issues.append(f"{sample.observation_id}/{sample.kind}: crop illisible")
            continue
        started = time.perf_counter()
        result = specialized_reader.read(image, sample.kind)
        segmentation[sample.kind][len(result.glyphs)] += 1
        timings[f"specialized_{sample.kind.lower()}"] += [result.timings_ms.get("specialized", 0.0)]
        if sample.truth is not None:
            common = {
                "observation_id": sample.observation_id, "kind": sample.kind,
                "truth": sample.truth,
            }
            mode_rows["specialized"][sample.split].append({**common,
                "predicted": result.value, "confidence": result.confidence,
                "margin": result.margin, "reason": result.reason.value,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
            })
        if rapid_reader is not None:
            signature = (tuple(image.shape), hashlib.sha256(image.tobytes()).digest())
            if signature not in rapid_cache:
                rapid_started = time.perf_counter()
                rapid_cache[signature] = rapid_reader(image, 0, 99)
                timings[f"rapidocr_{sample.kind.lower()}"] += [
                    (time.perf_counter() - rapid_started) * 1000
                ]
            rapid_value, rapid_confidence = rapid_cache[signature]
            combined_started = time.perf_counter()
            assert combined_reader is not None
            combined = combined_reader.read(image, sample.kind)
            timings[f"combined_{sample.kind.lower()}"] += [
                (time.perf_counter() - combined_started) * 1000
            ]
            if sample.truth is not None:
                common = {"observation_id": sample.observation_id,
                          "kind": sample.kind, "truth": sample.truth}
                mode_rows["rapidocr"][sample.split].append({**common,
                    "predicted": rapid_value, "confidence": rapid_confidence,
                    "margin": 0.0, "reason": "RAPIDOCR_RAW",
                })
                mode_rows["combined"][sample.split].append({**common,
                    "predicted": combined.value, "confidence": combined.confidence,
                    "margin": combined.margin, "reason": combined.reason.value,
                })
    truth_numbers = Counter(str(sample.truth) for sample in labelled)
    truth_digits = Counter(digit for sample in labelled for digit in str(sample.truth))
    modes = {name: _mode_metrics(rows) for name, rows in mode_rows.items()}
    one_seven = one_seven_verdict(labelled, modes["specialized"]["splits"]["test"],
                                  mode_rows["specialized"]["test"])
    missing_digits = [str(digit) for digit in range(10) if not truth_digits.get(str(digit))]
    # 4 et 8 absents ne sont pas un échec du lecteur (3B-4R2 §17) : seul 1/7 bloque.
    if not labelled or not mode_rows["specialized"]["test"]:
        status = "INSUFFICIENT"
    elif one_seven["verdict"] != "VALIDATED":
        status = "PARTIAL"
    else:
        status = "PASS"
    review = repository.hud_review_summary()
    untreated = sum(counts["untreated"] for counts in review["counters"].values())  # type: ignore[union-attr]
    conflicts = truth_conflicts(repository, samples)
    return {
        "schema_version": 1, "manifest": str(manifest_path),
        "inventory_examples": len(samples), "labelled_examples": len(labelled),
        "unlabelled_examples": len(samples) - len(labelled),
        "truth_policy": "human_confirmed uniquement ; unverified_import exclu",
        "review": review,
        "ground_truth_status": "PASS" if untreated == 0 and not conflicts else "PARTIAL",
        "truth_conflicts": conflicts,
        "unregistered_groups": sorted({sample.group_id for sample in samples if not sample.split_registered}),
        "split_distribution": split_distribution(samples),
        "distribution_text": distribution_text(samples),
        "digit_status": digit_status(labelled, modes["specialized"]["splits"]["test"]),
        "complete_numbers": complete_numbers(labelled),
        "cross_split_identical_crops": cross_split_identical_crops(repository, samples),
        "transitions": replay_transitions(repository, samples, library),
        "distribution": {
            "digits": {str(digit): truth_digits.get(str(digit), 0) for digit in range(10)},
            "numbers": dict(sorted(truth_numbers.items(), key=lambda item: int(item[0]))),
            "types": dict(Counter(sample.kind for sample in labelled)),
            "splits": dict(Counter(sample.split for sample in labelled)),
            "groups": len({sample.group_id for sample in labelled}),
            "crop_quality": dict(Counter(sample.crop_quality or "UNCLASSIFIED" for sample in samples)),
        },
        "splits": modes["specialized"]["splits"],
        "modes": modes,
        "one_seven": one_seven,
        "missing_digits": missing_digits,
        "performance_ms_mean": {name: mean(values) if values else None for name, values in timings.items()},
        "segmentation_glyph_counts": {
            kind: {str(count): occurrences for count, occurrences in sorted(values.items())}
            for kind, values in segmentation.items()
        },
        "template_digits": {
            kind: {str(digit): len(values) for digit, values in library.digits(kind).items()}
            for kind in ("AP", "MP")
        },
        "issues": issues,
        "status": status,
        "note": "Seules les annotations humaines PA/PM sont utilisées.",
    }


def export_one_seven(repository: CorpusRepository, directory: Path) -> tuple[Path, Path | None]:
    """Exporte les preuves de chaque glyphe annoté 1 ou 7 et une planche comparative.

    Les templates viennent seulement de TRAIN ; seuls les crops annotés sont lus.
    """
    samples = inventory(repository)
    labelled = tuple(sample for sample in samples if sample.truth is not None)
    library, _issues = build_templates(repository, labelled)
    reader = HUDReader(library)
    rows: list[dict[str, object]] = []
    tiles: list[np.ndarray] = []
    tile_digits: list[int] = []
    for sample in labelled:
        truth = str(sample.truth)
        if "1" not in truth and "7" not in truth:
            continue
        image = cv2.imread(str(repository.resolve(sample.crop_path)), cv2.IMREAD_COLOR)
        if image is None:
            continue
        result = reader.read(image, sample.kind)
        segments = segment_glyphs(image)
        if len(segments) != len(truth):
            rows.append({"observation_id": sample.observation_id, "kind": sample.kind,
                         "split": sample.split, "truth": sample.truth, "glyph_index": None,
                         "reason": result.reason.value, "segmented_glyphs": len(segments)})
            continue
        for index, (digit, segment) in enumerate(zip(truth, segments)):
            if digit not in ("1", "7"):
                continue
            glyph = next((item for item in result.glyphs if item.bbox == segment.bbox), None)
            mask = segment.image > 0
            preferred, features = distinguish_one_seven(segment.image)
            band = max(2, round(mask.shape[0] * 0.28))
            rows.append({
                "observation_id": sample.observation_id, "kind": sample.kind, "split": sample.split,
                "group_id": sample.group_id, "truth": sample.truth, "glyph_index": index,
                "truth_digit": int(digit),
                "horizontal_projection": [int(value) for value in mask.sum(axis=1)],
                "top_band_occupancy": float(mask[:band].mean()),
                "top_width": features.get("top_span"), "lower_center": features.get("lower_center"),
                "distinguish_one_seven": preferred,
                "best_digit": glyph.best_digit if glyph else None,
                "best_score": glyph.best_score if glyph else None,
                "second_digit": glyph.second_digit if glyph else None,
                "second_score": glyph.second_score if glyph else None,
                "margin": glyph.margin if glyph else None,
                "final_value": result.value, "reason": result.reason.value,
            })
            tile = cv2.cvtColor(cv2.resize(segment.image, None, fx=4, fy=4,
                                           interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
            label = np.full((34, tile.shape[1], 3), 40, np.uint8)
            predicted = "?" if glyph is None or glyph.best_digit is None else str(glyph.best_digit)
            color = (80, 220, 80) if predicted == digit else (60, 60, 230)
            cv2.putText(label, f"T{digit} P{predicted}", (3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1)
            cv2.putText(label, f"{sample.split[:3]} {result.value}", (3, 29),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (220, 220, 220), 1)
            tiles.append(np.vstack([label, tile]))
            tile_digits.append(int(digit))
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "one-seven.json"
    json_path.write_text(json.dumps({"schema_version": 1, "glyphs": rows}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    if not tiles:
        return json_path, None
    # Les 7 d'abord : ils sont rares et doivent rester visibles sur la planche.
    tiles = [tile for _digit, tile in sorted(zip(tile_digits, tiles), key=lambda item: -item[0])]
    per_row = 12
    blank = np.zeros_like(tiles[0])
    while len(tiles) % per_row:
        tiles.append(blank)
    plate = np.vstack([np.hstack(tiles[i:i + per_row]) for i in range(0, len(tiles), per_row)])
    plate_path = directory / "one-seven.png"
    cv2.imwrite(str(plate_path), plate)
    return json_path, plate_path


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Benchmark HUD PA/PM", "",
        f"- Statut : **{report['status']}**",
        f"- Crops inventoriés : **{report['inventory_examples']}**",
        f"- Vérités humaines : **{report['labelled_examples']}**",
        f"- Sans vérité : **{report['unlabelled_examples']}**",
        f"- Vérité terrain : **{report.get('ground_truth_status')}** — `{report.get('review')}`",
        f"- Conflits de vérité (crops identiques, valeurs différentes) : `{report.get('truth_conflicts')}`",
        f"- Groupes hors registre (affectation provisoire) : {len(report.get('unregistered_groups', ()))}", "",
        "## Distribution", "",
        "```", report.get("distribution_text", ""), "```", "",
        f"- Statut par chiffre : `{report.get('digit_status')}`",
        f"- Nombres complets : `{report.get('complete_numbers')}`",
        f"- Transitions réelles : `{report.get('transitions', {}).get('summary')}`",
        f"- Chiffres 0..9 : `{report['distribution']['digits']}`",
        f"- Nombres : `{report['distribution']['numbers']}`",
        f"- Splits : `{report['distribution']['splits']}`", "",
    ]
    for split in ("train", "validation", "test"):
        value = report["splits"][split]
        lines += [f"## {split.upper()}", "",
                  f"- Exemples : {value['examples']}", f"- Accuracy : {value['accuracy']}",
                  f"- Accepted accuracy : {value['accepted_accuracy']}",
                  f"- Coverage : {value['coverage']}", f"- UNKNOWN : {value['unknown']}",
                  f"- 1→7 / 7→1 : {value['errors_1_to_7']} / {value['errors_7_to_1']}", ""]
    one_seven = report.get("one_seven", {})
    lines += ["## 1 / 7", "",
              f"- Verdict : **{one_seven.get('verdict')}**",
              f"- Groupes indépendants : `{one_seven.get('independent_groups')}`",
              f"- Manques : {', '.join(one_seven.get('missing', ())) or 'aucun'}",
              f"- Chiffres jamais observés : {', '.join(report.get('missing_digits', ())) or 'aucun'}", ""]
    lines += ["## Limite", "", report["note"]]
    if report["status"] == "INSUFFICIENT":
        lines.append("Le corpus ne permet pas encore de fixer ou valider statistiquement les seuils.")
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / "hud-reader.json", directory / "hud-reader.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, md_path
