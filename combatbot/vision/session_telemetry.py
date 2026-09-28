"""LOT 3B-7 : journal d'une session d'observation réelle (lecture seule).

Une ligne JSON par frame analysée (latence par étape, états produits) et un résumé chiffré à
l'arrêt : cadence effective, latence (moyenne, médiane, p95, max) par étape, taux d'inconnus et
configuration (taille client, DPI, layout, exécutable ou sources). Aucune image n'est écrite.
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from combatbot import __version__
from combatbot.runtime import is_frozen

STAGES = ("capture", "map", "grid", "entities", "hud", "combat_state", "overlay", "total")
# Non détectés par la vision à ce jour : documentés comme tels, jamais devinés.
UNDETECTED_CONTEXT = {"tactical_mode": "non détecté", "dofus_theme": "non détecté"}


def _stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "p95": None, "max": None}
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, max(0, round(0.95 * len(ordered)) - 1))]
    return {"n": len(values), "mean": round(statistics.fmean(values), 2),
            "median": round(statistics.median(values), 2), "p95": round(p95, 2), "max": round(ordered[-1], 2)}


def frame_record(metadata: dict, observation, *, wall_time: float) -> dict[str, object]:
    """Ligne compacte d'une frame : latence et états, sans pixels ni données personnelles."""
    semantic = metadata.get("semantic_combat_state") or {}
    resolution = metadata.get("map_resolution") or {}
    return {
        "t": round(wall_time, 3),
        "frame": metadata.get("frame_index"),
        "stage_ms": metadata.get("stage_ms") or {"total": metadata.get("analysis_ms")},
        "capture_ms": metadata.get("capture_ms") or None,
        "capture_source": metadata.get("capture_source"),
        "grid_ms": metadata.get("grid_ms") or None,
        "entities_ms": {key: value for key, value in (metadata.get("entities") or {}).items()
                        if isinstance(value, (int, float))} or None,
        "client_size": metadata.get("client_size"),
        "map_status": resolution.get("status"),
        "map_id": resolution.get("map_id"),
        "grid_source": metadata.get("grid_source"),
        "grid_visibility": metadata.get("grid_visibility_state"),
        "alignment": metadata.get("alignment_status"),
        "phase": semantic.get("phase") if semantic else metadata.get("phase"),
        "turn_owner": semantic.get("turn_owner") if semantic else None,
        "ap": getattr(observation, "ap", None),
        "mp": getattr(observation, "mp", None),
        "player_cell_id": getattr(observation, "player_cell_id", None),
        "enemies": len(getattr(observation, "enemies", ()) or ()),
    }


class SessionTelemetry:
    """Écrit ``<dossier>/<session>/frames.jsonl`` pendant la session et ``summary.json/.md`` à la fin."""

    def __init__(self, directory: Path, session_id: str, context: dict[str, object]) -> None:
        self.directory = Path(directory) / session_id
        self.session_id = session_id
        self.context = {
            "session_id": session_id,
            "pythonbot_version": __version__,
            "runtime": "exécutable" if is_frozen() else "sources",
            "python": sys.version.split()[0],
            "os": platform.platform(),
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **UNDETECTED_CONTEXT,
            **context,
        }
        self._started = time.monotonic()
        self._records: list[dict] = []
        self.skipped_busy = 0
        self.errors: list[str] = []
        self._stream = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._stream = (self.directory / "frames.jsonl").open("a", encoding="utf-8")
        except OSError:
            self._stream = None          # journal indisponible : l'observation continue

    def record(self, metadata: dict, observation) -> None:
        item = frame_record(metadata, observation, wall_time=time.monotonic() - self._started)
        self._records.append(item)
        if self._stream is not None:
            try:
                self._stream.write(json.dumps(item, ensure_ascii=False) + "\n")
                self._stream.flush()
            except OSError:
                pass

    def skip(self) -> None:
        """Tick du minuteur ignoré car l'analyse précédente n'était pas finie."""
        self.skipped_busy += 1

    def error(self, message: str) -> None:
        self.errors.append(message)

    def summary(self) -> dict[str, object]:
        return summarize(self._records, context={**self.context,
                                                 "duration_s": round(time.monotonic() - self._started, 1)},
                         skipped_busy=self.skipped_busy, errors=self.errors)

    def close(self) -> dict[str, object] | None:
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError:
                pass
            self._stream = None
        if not self._records:
            return None
        report = self.summary()
        try:
            (self.directory / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                         encoding="utf-8")
            (self.directory / "summary.md").write_text(markdown_summary(report), encoding="utf-8")
        except OSError:
            pass
        return report


def summarize(records: list[dict], *, context: dict, skipped_busy: int = 0,
              errors: list[str] | None = None) -> dict[str, object]:
    frames = len(records)
    times = [float(item["t"]) for item in records if item.get("t") is not None]
    intervals = [b - a for a, b in zip(times, times[1:]) if b >= a]

    def rate(key: str, unknown: tuple = (None,)) -> float | None:
        return round(sum(1 for item in records if item.get(key) in unknown) / frames, 3) if frames else None

    latency = {stage: _stats([float(item["stage_ms"][stage]) for item in records
                              if isinstance(item.get("stage_ms"), dict) and item["stage_ms"].get(stage) is not None])
               for stage in STAGES}
    sizes = Counter(tuple(item["client_size"]) for item in records if item.get("client_size"))
    return {
        "context": context,
        "frames": frames,
        "skipped_busy_ticks": skipped_busy,
        "errors": list(errors or []),
        "cadence": {"interval_s": _stats(intervals),
                    "fps": round(1 / statistics.median(intervals), 2) if intervals and statistics.median(intervals) > 0
                    else None},
        "latency_ms": latency,
        "client_sizes": {f"{w}x{h}": n for (w, h), n in sizes.items()},
        "states": {
            "map_status": dict(Counter(str(item.get("map_status")) for item in records)),
            "grid_source": dict(Counter(str(item.get("grid_source")) for item in records)),
            "grid_visibility": dict(Counter(str(item.get("grid_visibility")) for item in records)),
            "alignment": dict(Counter(str(item.get("alignment")) for item in records)),
            "phase": dict(Counter(str(item.get("phase")) for item in records)),
            "turn_owner": dict(Counter(str(item.get("turn_owner")) for item in records)),
        },
        "unknown_rates": {
            "ap": rate("ap"), "mp": rate("mp"),
            "map": round(sum(1 for item in records if item.get("map_status") != "RESOLVED") / frames, 3)
            if frames else None,
            "player_cell": rate("player_cell_id"),
        },
        "distinct_maps": sorted({int(item["map_id"]) for item in records
                                 if item.get("map_status") == "RESOLVED" and item.get("map_id") is not None}),
    }


def markdown_summary(report: dict) -> str:
    context = report["context"]
    lines = [f"# Session d'observation {context.get('session_id')}", "",
             f"- DofBot2 {context.get('pythonbot_version')} ({context.get('runtime')}), Python {context.get('python')}",
             f"- Système : {context.get('os')}",
             f"- Client : {', '.join(report['client_sizes']) or 'inconnu'} · DPI {context.get('dpi')} · "
             f"layout {context.get('layout_digest')}",
             f"- Mode tactique : {context.get('tactical_mode')} · thème DOFUS : {context.get('dofus_theme')}",
             f"- Durée {context.get('duration_s')} s · {report['frames']} frames analysées · "
             f"cadence {report['cadence']['fps']} img/s · ticks ignorés (analyse en cours) "
             f"{report['skipped_busy_ticks']}", "",
             "| Étape | n | moyenne | médiane | p95 | max |", "|---|---|---|---|---|---|"]
    for stage, stats in report["latency_ms"].items():
        if stats["n"]:
            lines.append(f"| {stage} | {stats['n']} | {stats['mean']} | {stats['median']} | {stats['p95']} | "
                         f"{stats['max']} |")
    unknown = report["unknown_rates"]
    lines += ["", f"Inconnus : PA {unknown['ap']} · PM {unknown['mp']} · map non résolue {unknown['map']} · "
              f"case joueur {unknown['player_cell']}", "",
              f"États de map : {report['states']['map_status']}",
              f"Alignement : {report['states']['alignment']}",
              f"Phase : {report['states']['phase']}",
              f"Maps résolues distinctes : {len(report['distinct_maps'])}"]
    if report["errors"]:
        lines += ["", "Erreurs : " + " | ".join(report["errors"])]
    return "\n".join(lines) + "\n"
