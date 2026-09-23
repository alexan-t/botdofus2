"""Corpus réel, annotations humaines et mesures de baseline du LOT 3B-0."""

from combatbot.corpus.models import (
    Annotation, CorpusEntry, CorpusManifest, PixelAnnotation,
)
from combatbot.corpus.repository import CorpusRepository

__all__ = [
    "Annotation", "CorpusEntry", "CorpusManifest", "PixelAnnotation", "CorpusRepository",
]
