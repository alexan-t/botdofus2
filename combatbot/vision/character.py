"""Lecture prudente des compteurs et de l'identité visibles."""

from __future__ import annotations

import re

from combatbot.vision.models import Calibration, CapturedFrame, RecognizedProfile
from combatbot.vision.tooltip import read_visible_text


def parse_hp(text: str) -> tuple[int, int] | None:
    """PV actuels et maximum : « 3516/3585 » sur une ligne, ou empilés dans le cœur du HUD
    (actuels en haut, maximum en bas, séparés par un trait). Refus prudent si incohérent."""
    inline = re.search(r"\b(\d{1,6})\s*/\s*(\d{1,6})\b", text)
    if inline:
        values = int(inline.group(1)), int(inline.group(2))
    else:
        numbers = re.findall(r"\d{1,6}", text)
        if len(numbers) != 2:
            return None
        values = int(numbers[0]), int(numbers[1])   # lecture ordonnée de haut en bas
    current, maximum = values
    return values if 0 <= current <= maximum and maximum > 0 else None


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
    hp_values = parse_hp(texts.get("hp", ""))
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
        "hp_current": hp_values[0] if hp_values else None,
        "hp_max": hp_values[1] if hp_values else None,
        "ap": int(ap_match.group(1)) if ap_match else None,
        "mp": int(mp_match.group(1)) if mp_match else None,
    }
    return RecognizedProfile(
        **fields,
        confidence=sum(confidences) / len(confidences) if confidences else 0.0,
        raw_text="\n".join(f"[{zone}] {text}" for zone, text in texts.items()),
        uncertain_fields=tuple(key for key, value in fields.items() if value is None),
    )
