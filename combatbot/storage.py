"""Persistance SQLite des réglages, sorts, combats et journaux structurés."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from combatbot.models import Spell, Strategy, StrategyMode, TargetPriority, CombatEvent
from combatbot.vision.models import Calibration, IconCandidate, Profile, RecognizedText, RelativeRect, ZoneEvidence
from combatbot.vision.icons import KnownIcon
from combatbot.runtime import PROJECT_ROOT, database_path, migrate_legacy_database


DEFAULT_DATABASE = PROJECT_ROOT / "data" / "pythonbot.sqlite3"  # Compatibilité des imports existants.


class Storage:
    def __init__(self, path: Path | None = None) -> None:
        path = path or database_path()
        path = path.resolve()
        if path == database_path().resolve():
            migrate_legacy_database(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        existed = path.exists()
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.backup_path: Path | None = None
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version < 4 and existed:
            self.backup_path = path.with_name(f"{path.stem}.pre-lot2-{uuid4().hex[:8]}.bak")
            with sqlite3.connect(self.backup_path) as backup:
                self.connection.backup(backup)
        self._initialize()
        if version < 2:
            self._migrate_v2()
        if version < 3:
            self._migrate_v3()
            self.connection.execute("PRAGMA user_version = 3")
            self.connection.commit()
        if version < 4:
            self._migrate_v4()
            self.connection.execute("PRAGMA user_version = 4")
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def _initialize(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS spells (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                ap_cost INTEGER NOT NULL,
                min_range INTEGER NOT NULL,
                max_range INTEGER NOT NULL,
                modifiable_range INTEGER NOT NULL,
                line_cast INTEGER NOT NULL,
                line_of_sight INTEGER NOT NULL,
                per_turn INTEGER NOT NULL,
                per_target INTEGER NOT NULL,
                priority INTEGER NOT NULL,
                damage INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS combats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ended_at TEXT NOT NULL,
                outcome TEXT NOT NULL,
                turns INTEGER NOT NULL,
                xp INTEGER NOT NULL,
                kamas INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                level TEXT NOT NULL,
                event TEXT NOT NULL,
                message TEXT NOT NULL,
                context_json TEXT NOT NULL
            );
        """)
        if self.get_setting("player_name") is None:
            self.set_setting("player_name", "Personnage test")
        if self.get_setting("tick_ms") is None:
            self.set_setting("tick_ms", 350)
        if self.get_setting("strategy") is None:
            self.save_strategy(Strategy())
        if self.get_setting("demo_seeded") is None:
            if self.connection.execute("SELECT COUNT(*) FROM spells").fetchone()[0] == 0:
                self.save_spell(Spell(
                    name="Sort simulé A", ap_cost=3, min_range=1, max_range=4,
                    modifiable_range=False, line_cast=False, line_of_sight=True,
                    per_turn=2, per_target=2, priority=10, damage=6,
                ))
            self.set_setting("demo_seeded", True)

    def _migrate_v2(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL UNIQUE,
                window_hwnd INTEGER,
                name TEXT,
                character_class TEXT,
                hp_current INTEGER,
                hp_max INTEGER,
                ap INTEGER,
                mp INTEGER,
                notes TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS calibrations (
                profile_id INTEGER PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
                client_width INTEGER NOT NULL,
                client_height INTEGER NOT NULL,
                zones_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profile_settings (
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                PRIMARY KEY(profile_id, key)
            );
            CREATE TABLE IF NOT EXISTS profile_spells (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                page INTEGER NOT NULL,
                slot INTEGER NOT NULL,
                icon_png BLOB NOT NULL,
                visual_hash TEXT NOT NULL,
                presence_confidence REAL NOT NULL,
                recognition_confidence REAL NOT NULL,
                status TEXT NOT NULL,
                name TEXT,
                ap_cost INTEGER,
                min_range INTEGER,
                max_range INTEGER,
                modifiable_range INTEGER,
                line_cast INTEGER,
                line_of_sight INTEGER,
                per_turn INTEGER,
                per_target INTEGER,
                effects TEXT,
                damage INTEGER,
                raw_ocr TEXT,
                UNIQUE(profile_id, page, slot)
            );
            CREATE INDEX IF NOT EXISTS idx_profile_spells_hash ON profile_spells(profile_id, visual_hash);
        """)
        if self.connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0:
            self.connection.execute("INSERT INTO profiles (label) VALUES ('Profil 1')")
            self.connection.commit()

    def _migrate_v3(self) -> None:
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(profile_spells)")}
        if "ocr_confidence" not in columns:
            self.connection.execute("ALTER TABLE profile_spells ADD COLUMN ocr_confidence REAL")
        self.connection.commit()

    def _migrate_v4(self) -> None:
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(calibrations)")}
        if "metadata_json" not in columns:
            self.connection.execute("ALTER TABLE calibrations ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
        if "layout_signature" not in columns:
            self.connection.execute("ALTER TABLE calibrations ADD COLUMN layout_signature TEXT NOT NULL DEFAULT ''")
        spell_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(profile_spells)")}
        if "decision_ready" not in spell_columns:
            self.connection.execute("ALTER TABLE profile_spells ADD COLUMN decision_ready INTEGER NOT NULL DEFAULT 0")
            self.connection.execute("""
                UPDATE profile_spells SET decision_ready=1
                WHERE status='Confirmé' AND name IS NOT NULL AND ap_cost IS NOT NULL
                  AND min_range IS NOT NULL AND max_range IS NOT NULL
                  AND modifiable_range IS NOT NULL AND line_cast IS NOT NULL
                  AND line_of_sight IS NOT NULL AND per_turn IS NOT NULL AND per_target IS NOT NULL
            """)
        self.connection.commit()

    def get_setting(self, key: str) -> object | None:
        row = self.connection.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_setting(self, key: str, value: object) -> None:
        self.connection.execute(
            "INSERT INTO settings (key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self.connection.commit()

    def load_strategy(self) -> Strategy:
        raw = self.get_setting("strategy")
        if not isinstance(raw, dict):
            return Strategy()
        return Strategy(
            mode=StrategyMode(raw.get("mode", StrategyMode.DISTANCE.value)),
            hp_threshold=int(raw.get("hp_threshold", 35)),
            target_priority=TargetPriority(raw.get("target_priority", TargetPriority.NEAREST.value)),
            reserve_ap=int(raw.get("reserve_ap", 0)),
        )

    def save_strategy(self, strategy: Strategy) -> None:
        strategy.validate()
        self.set_setting("strategy", {
            "mode": strategy.mode.value,
            "hp_threshold": strategy.hp_threshold,
            "target_priority": strategy.target_priority.value,
            "reserve_ap": strategy.reserve_ap,
        })

    def list_spells(self) -> list[Spell]:
        rows = self.connection.execute("SELECT * FROM spells ORDER BY priority DESC, name").fetchall()
        return [Spell(
            id=row["id"], name=row["name"], ap_cost=row["ap_cost"],
            min_range=row["min_range"], max_range=row["max_range"],
            modifiable_range=bool(row["modifiable_range"]), line_cast=bool(row["line_cast"]),
            line_of_sight=bool(row["line_of_sight"]), per_turn=row["per_turn"],
            per_target=row["per_target"], priority=row["priority"], damage=row["damage"],
        ) for row in rows]

    def save_spell(self, spell: Spell) -> int:
        spell.validate()
        values = (
            spell.name.strip(), spell.ap_cost, spell.min_range, spell.max_range,
            int(spell.modifiable_range), int(spell.line_cast), int(spell.line_of_sight),
            spell.per_turn, spell.per_target, spell.priority, spell.damage,
        )
        if spell.id is None:
            cursor = self.connection.execute("""
                INSERT INTO spells (name, ap_cost, min_range, max_range, modifiable_range,
                    line_cast, line_of_sight, per_turn, per_target, priority, damage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, values)
            spell_id = int(cursor.lastrowid)
        else:
            cursor = self.connection.execute("""
                UPDATE spells SET name=?, ap_cost=?, min_range=?, max_range=?, modifiable_range=?,
                    line_cast=?, line_of_sight=?, per_turn=?, per_target=?, priority=?, damage=?
                WHERE id=?
            """, values + (spell.id,))
            if cursor.rowcount == 0:
                raise ValueError("Sort introuvable")
            spell_id = spell.id
        self.connection.commit()
        return spell_id

    def delete_spell(self, spell_id: int) -> None:
        self.connection.execute("DELETE FROM spells WHERE id=?", (spell_id,))
        self.connection.commit()

    def record_event(self, event: CombatEvent) -> None:
        self.connection.execute(
            "INSERT INTO events (created_at, level, event, message, context_json) VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), event.level, event.event,
             event.message, json.dumps(event.context, ensure_ascii=False)),
        )
        self.connection.commit()

    def recent_events(self, limit: int = 200) -> list[sqlite3.Row]:
        rows = self.connection.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return list(reversed(rows))

    def record_combat(self, outcome: str, turns: int, xp: int, kamas: int) -> None:
        self.connection.execute(
            "INSERT INTO combats (ended_at, outcome, turns, xp, kamas) VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), outcome, turns, xp, kamas),
        )
        self.connection.commit()

    def statistics(self) -> dict[str, int]:
        row = self.connection.execute("""
            SELECT COUNT(*) AS combats,
                   COALESCE(SUM(CASE WHEN outcome='victoire' THEN 1 ELSE 0 END), 0) AS victories,
                   COALESCE(SUM(CASE WHEN outcome='défaite' THEN 1 ELSE 0 END), 0) AS defeats,
                   COALESCE(SUM(xp), 0) AS xp,
                   COALESCE(SUM(kamas), 0) AS kamas
            FROM combats
        """).fetchone()
        return {key: int(row[key]) for key in ("combats", "victories", "defeats", "xp", "kamas")}

    def recent_combats(self, limit: int = 30) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM combats ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    # Les données du client réel restent dans des tables distinctes des sorts simulés.
    def list_profiles(self) -> list[Profile]:
        return [self._profile_from_row(row) for row in self.connection.execute("SELECT * FROM profiles ORDER BY id")]

    def get_profile(self, profile_id: int) -> Profile:
        row = self.connection.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            raise ValueError("Profil introuvable")
        return self._profile_from_row(row)

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> Profile:
        return Profile(
            id=row["id"], label=row["label"], window_hwnd=row["window_hwnd"],
            name=row["name"], character_class=row["character_class"],
            hp_current=row["hp_current"], hp_max=row["hp_max"], ap=row["ap"], mp=row["mp"],
            notes=row["notes"],
        )

    def save_profile(self, profile: Profile) -> int:
        if not profile.label.strip():
            raise ValueError("Le libellé du profil est requis")
        values = (
            profile.label.strip(), profile.window_hwnd, profile.name or None,
            profile.character_class or None, profile.hp_current, profile.hp_max,
            profile.ap, profile.mp, profile.notes,
        )
        if profile.id is None:
            cursor = self.connection.execute("""
                INSERT INTO profiles (label, window_hwnd, name, character_class,
                    hp_current, hp_max, ap, mp, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, values)
            profile_id = int(cursor.lastrowid)
        else:
            cursor = self.connection.execute("""
                UPDATE profiles SET label=?, window_hwnd=?, name=?, character_class=?,
                    hp_current=?, hp_max=?, ap=?, mp=?, notes=? WHERE id=?
            """, values + (profile.id,))
            if cursor.rowcount == 0:
                raise ValueError("Profil introuvable")
            profile_id = profile.id
        self.connection.commit()
        return profile_id

    def save_calibration(self, calibration: Calibration) -> None:
        calibration.validate()
        zones_json = json.dumps({key: rect.to_dict() for key, rect in calibration.zones.items()})
        self.connection.execute("""
            INSERT INTO calibrations (profile_id, client_width, client_height, zones_json, metadata_json,
                                      layout_signature, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                client_width=excluded.client_width, client_height=excluded.client_height,
                zones_json=excluded.zones_json, metadata_json=excluded.metadata_json,
                layout_signature=excluded.layout_signature, updated_at=excluded.updated_at
        """, (calibration.profile_id, calibration.client_width, calibration.client_height,
              zones_json, json.dumps({key: value.to_dict() for key, value in calibration.zone_meta.items()}),
              calibration.layout_signature, datetime.now(timezone.utc).isoformat()))
        self.connection.commit()

    def load_calibration(self, profile_id: int) -> Calibration | None:
        row = self.connection.execute("SELECT * FROM calibrations WHERE profile_id=?", (profile_id,)).fetchone()
        if row is None:
            return None
        zones = {key: RelativeRect.from_dict(value) for key, value in json.loads(row["zones_json"]).items()}
        metadata = json.loads(row["metadata_json"]) if "metadata_json" in row.keys() else {}
        evidence = {key: ZoneEvidence.from_dict(value) for key, value in metadata.items()}
        result = Calibration(profile_id, row["client_width"], row["client_height"], zones,
                             evidence, row["layout_signature"] if "layout_signature" in row.keys() else "")
        result.validate()
        return result

    def get_profile_setting(self, profile_id: int, key: str, default: object = None) -> object:
        row = self.connection.execute(
            "SELECT value_json FROM profile_settings WHERE profile_id=? AND key=?", (profile_id, key)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def set_profile_setting(self, profile_id: int, key: str, value: object) -> None:
        self.connection.execute("""
            INSERT INTO profile_settings (profile_id, key, value_json) VALUES (?, ?, ?)
            ON CONFLICT(profile_id, key) DO UPDATE SET value_json=excluded.value_json
        """, (profile_id, key, json.dumps(value, ensure_ascii=False)))
        self.connection.commit()

    def list_profile_spells(self, profile_id: int, page: int | None = None) -> list[sqlite3.Row]:
        if page is None:
            return self.connection.execute(
                "SELECT * FROM profile_spells WHERE profile_id=? ORDER BY page, slot", (profile_id,)
            ).fetchall()
        return self.connection.execute(
            "SELECT * FROM profile_spells WHERE profile_id=? AND page=? ORDER BY slot", (profile_id, page)
        ).fetchall()

    def get_profile_spell(self, spell_id: int) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM profile_spells WHERE id=?", (spell_id,)).fetchone()
        if row is None:
            raise ValueError("Sort scanné introuvable")
        return row

    def known_icons(self, profile_id: int) -> list[KnownIcon]:
        rows = self.connection.execute("""
            SELECT id, name, icon_png, visual_hash FROM profile_spells
            WHERE profile_id=? AND status='Confirmé' AND name IS NOT NULL
        """, (profile_id,)).fetchall()
        return [KnownIcon(row["id"], row["name"], row["icon_png"], row["visual_hash"]) for row in rows]

    def save_scan_candidate(self, profile_id: int, candidate: IconCandidate) -> int:
        row = self.connection.execute(
            "SELECT id, visual_hash, status FROM profile_spells WHERE profile_id=? AND page=? AND slot=?",
            (profile_id, candidate.page, candidate.slot),
        ).fetchone()
        if row is not None and row["visual_hash"] == candidate.visual_hash:
            status = row["status"] if row["status"] in ("Confirmé", "À vérifier") else candidate.status
            self.connection.execute("""
                UPDATE profile_spells SET icon_png=?, presence_confidence=?, recognition_confidence=?, status=?
                WHERE id=?
            """, (candidate.icon_png, candidate.presence_confidence,
                  candidate.recognition_confidence, status, row["id"]))
            spell_id = int(row["id"])
        else:
            if row is not None and row["status"] == "Confirmé":
                raise ValueError(
                    f"L'icône de la page {candidate.page}, case {candidate.slot} a changé ; "
                    "vérifiez le sort confirmé avant de remplacer cet emplacement"
                )
            if row is not None:
                self.connection.execute("DELETE FROM profile_spells WHERE id=?", (row["id"],))
            cursor = self.connection.execute("""
                INSERT INTO profile_spells (profile_id, page, slot, icon_png, visual_hash,
                    presence_confidence, recognition_confidence, status, name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (profile_id, candidate.page, candidate.slot, candidate.icon_png,
                  candidate.visual_hash, candidate.presence_confidence,
                  candidate.recognition_confidence, candidate.status, candidate.known_name))
            spell_id = int(cursor.lastrowid)
            if candidate.status == "Reconnu" and candidate.known_spell_id is not None:
                source = self.get_profile_spell(candidate.known_spell_id)
                if source["profile_id"] == profile_id and source["status"] == "Confirmé":
                    columns = (
                        "name", "ap_cost", "min_range", "max_range", "modifiable_range",
                        "line_cast", "line_of_sight", "per_turn", "per_target", "effects", "damage",
                    )
                    self.connection.execute("""
                        UPDATE profile_spells SET name=?, ap_cost=?, min_range=?, max_range=?,
                            modifiable_range=?, line_cast=?, line_of_sight=?, per_turn=?, per_target=?,
                            effects=?, damage=? WHERE id=?
                    """, tuple(source[column] for column in columns) + (spell_id,))
        self.connection.commit()
        return spell_id

    def save_recognized_characteristics(self, spell_id: int, recognized: RecognizedText) -> None:
        row = self.get_profile_spell(spell_id)
        if row["status"] == "Confirmé":
            raise ValueError("Un sort confirmé doit être modifié manuellement avant une nouvelle validation")
        fields = recognized.fields
        names = (
            "name", "ap_cost", "min_range", "max_range", "modifiable_range", "line_cast",
            "line_of_sight", "per_turn", "per_target", "effects", "damage",
        )
        values = [fields.get(name) if fields.get(name) is not None else row[name] for name in names]
        self.connection.execute("""
            UPDATE profile_spells SET name=?, ap_cost=?, min_range=?, max_range=?,
                modifiable_range=?, line_cast=?, line_of_sight=?, per_turn=?, per_target=?,
                effects=?, damage=?, raw_ocr=?, ocr_confidence=?, status='À vérifier'
            WHERE id=?
        """, (*values, recognized.raw_text, recognized.confidence, spell_id))
        self.connection.commit()

    def save_profile_spell_fields(self, spell_id: int, fields: dict[str, object], confirm: bool = False) -> None:
        row = self.get_profile_spell(spell_id)
        names = (
            "name", "ap_cost", "min_range", "max_range", "modifiable_range", "line_cast",
            "line_of_sight", "per_turn", "per_target", "effects", "damage",
        )
        values = {name: fields.get(name, row[name]) for name in names}
        if values["ap_cost"] is not None and int(values["ap_cost"]) < 1:
            raise ValueError("Coût en PA invalide")
        if values["min_range"] is not None and values["max_range"] is not None and int(values["min_range"]) > int(values["max_range"]):
            raise ValueError("Portée invalide")
        for key in ("per_turn", "per_target"):
            if values[key] is not None and int(values[key]) < 1:
                raise ValueError("Limites de lancer invalides")
        ready = int(confirm and all(values[key] is not None and values[key] != "" for key in names[:9]))
        status = "Confirmé" if confirm else "À vérifier"
        self.connection.execute("""
            UPDATE profile_spells SET name=?, ap_cost=?, min_range=?, max_range=?,
                modifiable_range=?, line_cast=?, line_of_sight=?, per_turn=?, per_target=?,
                effects=?, damage=?, status=?, decision_ready=? WHERE id=?
        """, tuple(values[name] for name in names) + (status, ready, spell_id))
        self.connection.commit()

    def ignore_profile_spell(self, spell_id: int) -> None:
        self.connection.execute("DELETE FROM profile_spells WHERE id=?", (spell_id,))
        self.connection.commit()

    def clear_unconfirmed_slot(self, profile_id: int, page: int, slot: int) -> bool:
        """Remove stale automatic reads, preserving confirmed human data."""
        row = self.connection.execute(
            "SELECT id, status FROM profile_spells WHERE profile_id=? AND page=? AND slot=?",
            (profile_id, page, slot),
        ).fetchone()
        if row is None:
            return True
        if row["status"] == "Confirmé":
            return False
        self.ignore_profile_spell(row["id"])
        return True
