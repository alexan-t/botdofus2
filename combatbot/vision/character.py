"""Lecture prudente des compteurs et de l'identité visibles."""

from __future__ import annotations

import re

from combatbot.vision.models import Calibration, CapturedFrame, RecognizedProfile
from combatbot.vision.tooltip import read_visible_text


def recognize_profile(frame: CapturedFrame, calibration: Calibration) -> RecognizedProfile:
    texts: dict[str, str] = {}
    confidences: list[float] = []
    for zone in ("hp", "ap", "mp", "identity"):
        if zone not in calibration.zones:
            continue
        text, confidence = read_visible_text(calibration.crop(frame, zone))
        texts[zone] = text
        if text:
            confidences.append(confidence)
    hp_match = re.search(r"\b(\d{1,6})\s*/\s*(\d{1,6})\b", texts.get("hp", ""))
    ap_match = re.search(r"\b(\d{1,2})\b", texts.get("ap", ""))
    mp_match = re.search(r"\b(\d{1,2})\b", texts.get("mp", ""))
    identity = texts.get("identity", "")
    name_match = re.search(r"(?:nom|personnage)\s*[:=]\s*([^\n]+)", identity, re.I)
    class_match = re.search(r"classe\s*[:=]\s*([^\n]+)", identity, re.I)
    name = name_match.group(1).strip() if name_match else None
    character_class = class_match.group(1).strip() if class_match else None
    fields = {
        "name": name,
        "character_class": character_class,
        "hp_current": int(hp_match.group(1)) if hp_match else None,
        "hp_max": int(hp_match.group(2)) if hp_match else None,
        "ap": int(ap_match.group(1)) if ap_match else None,
        "mp": int(mp_match.group(1)) if mp_match else None,
    }
    return RecognizedProfile(
        **fields,
        confidence=sum(confidences) / len(confidences) if confidences else 0.0,
        raw_text="\n".join(f"[{zone}] {text}" for zone, text in texts.items()),
        uncertain_fields=tuple(key for key, value in fields.items() if value is None),
    )
