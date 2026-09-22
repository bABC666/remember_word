"""Rebuild the incident evidence archive and write its manifest.

The corrupted database must never be overwritten, so its hash is captured
before anything else touches the archive. A hash that changes between two runs
means something is writing to a artefact it should not.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path("_INCIDENT-20260922")
CORRUPTED_SOURCE = Path("data/backups/pre-p1.2-2026-09-22-131040-vocab.db")
RECOVERY_SNAPSHOT = Path("data/backups/RECOVERY-20260922-131057")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def describe(path: Path, *, label: str) -> dict[str, object]:
    info: dict[str, object] = {"label": label, "path": str(path), "exists": path.exists()}
    if not path.exists():
        return info
    info["bytes"] = path.stat().st_size
    info["sha256"] = sha256(path)
    if path.suffix in {".db", ".sqlite"} or path.name.endswith(".db"):
        try:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            info["tables"] = [
                row[0]
                for row in connection.execute(
                    "select name from sqlite_master where type='table' order by name"
                )
            ]
            info["page_count"] = connection.execute("pragma page_count").fetchone()[0]
            info["freelist_count"] = connection.execute("pragma freelist_count").fetchone()[0]
            info["integrity_check"] = connection.execute("pragma integrity_check").fetchone()[0]
            connection.close()
        except sqlite3.Error as error:
            info["sqlite_error"] = str(error)
    return info


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    corrupted_dir = EVIDENCE / "corrupted-live-db"
    corrupted_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir = EVIDENCE / "recovery-snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # The damaged database is preserved under an explicit name.
    corrupted_target = corrupted_dir / "vocab-CORRUPTED-20260922.db"
    if not corrupted_target.exists() and CORRUPTED_SOURCE.exists():
        shutil.copy2(CORRUPTED_SOURCE, corrupted_target)

    if RECOVERY_SNAPSHOT.exists():
        for item in RECOVERY_SNAPSHOT.iterdir():
            if item.is_file():
                shutil.copy2(item, snapshot_dir / item.name)

    # The pre-migration copy that turned out to be damaged (not a safe backup).
    pre_migration = EVIDENCE / "NOT-A-SAFE-BACKUP-pre-p1.2-2026-09-22-131040-vocab.db"
    if not pre_migration.exists() and CORRUPTED_SOURCE.exists():
        shutil.copy2(CORRUPTED_SOURCE, pre_migration)

    artefacts = [
        describe(corrupted_target, label="corrupted live database (preserved)"),
        describe(
            snapshot_dir / "vocab.db",
            label="post-damage snapshot taken before any recovery attempt",
        ),
        describe(pre_migration, label="mis-labelled pre-migration copy (already damaged)"),
        describe(
            Path("data/backups/2026-09-22-vocab.db"),
            label="08:58 automatic backup used as the recovery source",
        ),
        describe(
            Path("data/backups/2026-09-21-vocab.db"),
            label="09-21 automatic backup (older, near-empty)",
        ),
        describe(
            Path("data/vocab.db"),
            label="live database after restore (V1.1, verified)",
        ),
    ]

    manifest = {
        "incident": "V1.1 production database lost all business tables during the Phase 1 test session",
        "date": "2026-09-22",
        "detected_local_time": "13:09",
        "root_cause": (
            "A test fixture executed Base.metadata.drop_all(engine) against the "
            "application's module-level engine, which had resolved to the real "
            "data/vocab.db instead of a temporary test database. The fixture "
            "trusted VOCAB_DATA_DIR to redirect the engine rather than verifying "
            "the engine's actual binding."
        ),
        "evidence_of_damage": {
            "remaining_tables": ["alembic_version"],
            "page_count": 115,
            "freelist_count": 112,
            "note": "112 of 115 pages were on the freelist: the signature of dropping every table",
        },
        "recovery_attempts": [
            "WAL replay across 1004 frames / 205 commit points: no frame contained the pre-damage schema",
            "b-tree leaf-page carving over the whole file: recovered only 5 app_setting rows",
            "the pre-migration copy and the 13:10 snapshot were both already damaged",
        ],
        "physics_of_loss": (
            "SQLite in WAL mode defaults to secure_delete, so freed pages were "
            "zeroed when the tables were dropped. The data is physically gone."
        ),
        "irrecoverable": {
            "review_event": 10,
            "article_word_lookup": 5,
            "article_translation_characters": 1089,
            "history_event": 6,
            "app_setting": ["onboarding_seen"],
            "word_state_progress": "9 words reverted from familiar/learning to new",
        },
        "recovered_from": "data/backups/2026-09-22-vocab.db (08:58 automatic backup)",
        "artefacts": artefacts,
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    output = EVIDENCE / "INCIDENT-MANIFEST.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    for item in artefacts:
        print("==", item["label"])
        for key, value in item.items():
            if key in {"label", "path"}:
                continue
            print("   %-16s %s" % (key, value))
    print()
    print("manifest:", output)


if __name__ == "__main__":
    main()
