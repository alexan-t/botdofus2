"""LOT 3B-6A : suggestions PA/PM pour la revue HUD assistée et gabarits locaux.

Une suggestion n'est jamais une vérité : elle préremplit la revue sur TRAIN/VALIDATION ; TEST (ou
split non déclaré) reste aveugle. Les gabarits de chiffres sont appris uniquement sur les vérités
humaines TRAIN (jamais VALIDATION, TEST ni RapidOCR seul), puis installés pour la Vision réelle.
Le seuil de sécurité RapidOCR (0,94) n'est pas modifié : une lecture sous ce seuil peut être
PROPOSÉE à l'humain, jamais acceptée par le lecteur.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import shutil

import numpy as np

from combatbot.corpus.repository import CorpusRepository

ASSISTED_SPLITS = ("train", "validation")


def frame_split(document: dict) -> str | None:
    value = (document.get("capture") or {}).get("entity_split_declared")
    return value if value in ("train", "validation", "test") else None


def hud_assistance_allowed(document: dict) -> bool:
    return frame_split(document) in ASSISTED_SPLITS


class HUDSuggestionProvider:
    """Lecteur spécialisé (gabarits runtime) + RapidOCR ; la valeur proposée est marquée non validée
    si le lecteur ne l'accepte pas lui-même."""

    def __init__(self, data_root: Path | None = None, *, rapidocr: bool = True) -> None:
        from combatbot.runtime import app_data_root
        self.data_root = data_root or app_data_root() / "data"
        self.rapidocr = rapidocr
        self.reload()

    @property
    def templates_directory(self) -> Path:
        return self.data_root / "hud_templates"

    def reload(self) -> None:
        from combatbot.vision.hud_reader import GlyphTemplateLibrary, HUDReader
        library = GlyphTemplateLibrary.load(self.templates_directory)
        reader = None
        if self.rapidocr:
            from combatbot.vision.combat_ocr import read_small_number
            reader = lambda image, minimum, maximum: read_small_number(image, minimum, maximum)
        # Intervalle nul : chaque crop de la revue est lu (pas de cadence temps réel ici).
        self.reader = HUDReader(library, rapidocr_reader=reader, rapidocr_interval=0.0)
        self.template_count = sum(len(values) for kind in ("AP", "MP") for values in library.digits(kind).values())

    def suggest(self, image: np.ndarray | None, kind: str) -> dict[str, object]:
        if image is None or not getattr(image, "size", 0):
            return {"value": None, "confidence": 0.0, "source": "ABSENT", "accepted": False}
        result = self.reader.read(image, kind.upper())
        raw = result.raw_candidates or {}
        if result.value is not None:
            return {"value": int(result.value), "confidence": round(float(result.confidence), 3),
                    "source": result.source.value, "accepted": True, "reason": result.reason.value}
        # Proposition non validée (le lecteur s'abstient) : gabarit incertain, sinon RapidOCR.
        proposal = raw.get("specialized_value")
        confidence = float(result.confidence or 0.0)
        source = "GLYPH_TEMPLATE_UNSURE"
        if proposal is None and raw.get("rapidocr_value") is not None:
            proposal, confidence, source = raw["rapidocr_value"], float(raw.get("rapidocr_confidence") or 0.0), "RAPIDOCR_UNSURE"
        return {"value": int(proposal) if proposal is not None else None, "confidence": round(confidence, 3),
                "source": source if proposal is not None else "UNKNOWN", "accepted": False,
                "reason": result.reason.value}


def build_local_templates(repository: CorpusRepository, data_root: Path) -> dict[str, object]:
    """Gabarits depuis les vérités HUMAINES du split TRAIN uniquement ; ancien jeu sauvegardé."""
    from combatbot.corpus.hud_dataset import build_templates, inventory
    samples = inventory(repository, require_human=True)
    train = [s for s in samples if s.split == "train" and s.truth is not None]
    library, issues = build_templates(repository, tuple(train))
    target = data_root / "hud_templates"
    backup = None
    if library.empty:
        return {"installed": False, "reason": "Aucune vérité PA/PM humaine TRAIN exploitable", "issues": issues,
                "train_samples": len(train)}
    if target.exists():
        backup = data_root / "backup" / f"hud-templates-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(target, backup)
        shutil.rmtree(target)
    library.save(target)
    counts = {kind: {str(digit): len(values) for digit, values in sorted(library.digits(kind).items())}
              for kind in ("AP", "MP")}
    provenance = {"schema_version": 1, "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                  "training_split": "train", "truth_source": "human_confirmed",
                  "train_samples": len(train),
                  "layouts": dict(Counter(s.layout_signature[:24] if s.layout_signature else "?" for s in train)),
                  "digits": counts, "issues": issues[:50], "backup": str(backup) if backup else None}
    (target / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"installed": True, **provenance}
