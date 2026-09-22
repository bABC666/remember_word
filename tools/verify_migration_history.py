"""Verify that the recorded migration history still matches the repository.

An applied migration is history: the bytes that ran against a real database must
stay recoverable, and if a revision file is edited after it has been applied, that
edit has to be *recorded* rather than discovered later by whoever notices the diff.

``docs/migration-history-manifest.json`` is that record. This tool computes every
fact it can from the repository and refuses to pass if anything drifts:

* every revision file on disk must match the recorded content hash and git blob;
* the set of revision files must equal the recorded set (a new migration has to be
  added to the manifest);
* the version recorded as applied to the live database must still exist in the git
  object store, with a matching content hash -- that is what makes the applied
  version recoverable byte for byte;
* ``changed_since_audit_commit`` / ``changed_after_application`` and
  ``change_commits`` must agree with what git says;
* a revision that changed after it was applied must carry a written explanation of
  its schema and data-semantics impact.

Annotations (``applied_to_production``, ``applied_at_commit``, ``schema_impact``,
``data_semantics_impact``, ``notes``) come from the forensic review and are
preserved by ``--write``; everything else is recomputed.

Read-only with respect to databases: this never opens one.

Usage::

    python tools/verify_migration_history.py            # verify
    python tools/verify_migration_history.py --write    # refresh the recorded facts
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = PROJECT_ROOT / "docs" / "migration-history-manifest.json"
VERSIONS = PROJECT_ROOT / "backend" / "alembic" / "versions"

#: The commit that was checked out when the unauthorized ``alembic upgrade head``
#: ran against the live database (2026-09-22 15:04:30 +0800). It is the reference
#: point for "what the applied bytes were": see the forensic report in docs/.
AUDIT_COMMIT = "6ad8fce58041b03977deb2a98ae668cc22eef35a"

#: Fields this tool computes; every other field in an entry is an annotation.
COMPUTED_KEYS = (
    "path",
    "sha256",
    "blob",
    "blob_at_audit_commit",
    "sha256_at_audit_commit",
    "changed_since_audit_commit",
    "as_applied",
    "change_commits",
)


def normalised(path: Path) -> bytes:
    """The file content as git stores it, so the hash does not depend on CRLF."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def git(*arguments: str) -> bytes:
    """Run git and return raw bytes.

    Never through a shell pipe: PowerShell re-encodes text between commands, which
    silently changes any hash computed from the result.
    """
    result = subprocess.run(
        ["git", *arguments], cwd=str(PROJECT_ROOT), capture_output=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(arguments)} failed: {result.stderr.decode('utf-8', 'replace')}"
        )
    return result.stdout


def blob_at(commit: str, path: str) -> bytes | None:
    """The blob id recorded for ``path`` at ``commit``, or None if absent there."""
    result = subprocess.run(
        ["git", "rev-parse", f"{commit}:{path}"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode().strip().encode()


def revision_files() -> dict[str, Path]:
    """revision id -> file, from the ``revision = "..."`` each migration declares."""
    found: dict[str, Path] = {}
    for path in sorted(VERSIONS.glob("[0-9][0-9][0-9][0-9]_*.py")):
        match = re.search(
            r'^revision\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.MULTILINE
        )
        if match is None:
            raise RuntimeError(f"{path} does not declare a revision")
        found[match.group(1)] = path
    return found


def compute_facts(revision: str, path: Path, applied_at: str | None) -> dict[str, object]:
    relative = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
    data = normalised(path)
    blob = git_blob_id(data)

    audit_blob = blob_at(AUDIT_COMMIT, relative)
    audit_sha256 = None
    if audit_blob is not None:
        audit_sha256 = sha256_of(git("cat-file", "blob", audit_blob.decode()))

    facts: dict[str, object] = {
        "revision": revision,
        "path": relative,
        "sha256": sha256_of(data),
        "blob": blob,
        "blob_at_audit_commit": audit_blob.decode() if audit_blob else None,
        "sha256_at_audit_commit": audit_sha256,
        "changed_since_audit_commit": audit_blob is not None and audit_blob.decode() != blob,
    }

    if applied_at:
        applied_blob = blob_at(applied_at, relative)
        if applied_blob is None:
            raise RuntimeError(f"{revision} was not present at {applied_at}")
        applied_content = git("cat-file", "blob", applied_blob.decode())
        facts["as_applied"] = {
            "commit": applied_at,
            "blob": applied_blob.decode(),
            "sha256": sha256_of(applied_content),
        }
    else:
        facts["as_applied"] = None

    committed = [
        line.split()[0]
        for line in git("log", "--format=%H", f"{AUDIT_COMMIT}..HEAD", "--", relative)
        .decode()
        .split()
    ]
    facts["change_commits"] = committed
    return facts


def load_manifest() -> dict[str, object]:
    if not MANIFEST.exists():
        return {"manifest_version": 1, "audit_commit": AUDIT_COMMIT, "revisions": []}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def entries_by_revision(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    return {entry["revision"]: entry for entry in manifest["revisions"]}  # type: ignore[index]


def verify_entry(entry: dict[str, object], facts: dict[str, object], failures: list[str]) -> None:
    revision = entry["revision"]
    for key in COMPUTED_KEYS:
        if entry.get(key) != facts.get(key):
            failures.append(f"{revision}: recorded {key}={entry.get(key)!r} but it is {facts.get(key)!r}")

    applied = entry.get("as_applied")
    if isinstance(applied, dict):
        try:
            content = git("cat-file", "blob", str(applied["blob"]))
        except RuntimeError as error:
            failures.append(f"{revision}: applied blob is gone from the object store: {error}")
            return
        if sha256_of(content) != applied.get("sha256"):
            failures.append(f"{revision}: the applied blob does not match its recorded content hash")

    changed = bool(entry.get("changed_since_audit_commit"))
    if changed:
        for required in ("schema_impact", "data_semantics_impact", "notes"):
            if not entry.get(required):
                failures.append(f"{revision}: changed since the audit commit with no {required}")
        if not entry.get("change_commits"):
            failures.append(f"{revision}: changed since the audit commit with no change_commits")
        if not entry.get("applied_to_production"):
            failures.append(f"{revision}: changed since the audit commit but not marked as applied")
    elif entry.get("applied_to_production") and entry.get("as_applied") is None:
        # Applied before the audit commit and unchanged since: recorded as such.
        if not entry.get("notes"):
            failures.append(f"{revision}: applied to the live database with no note about when")


def verify() -> int:
    manifest = load_manifest()
    recorded = entries_by_revision(manifest)
    on_disk = revision_files()
    failures: list[str] = []

    if manifest.get("audit_commit") != AUDIT_COMMIT:
        failures.append(
            f"the manifest records audit_commit={manifest.get('audit_commit')}, "
            f"this tool was built for {AUDIT_COMMIT}"
        )

    for revision in sorted(set(on_disk) - set(recorded)):
        failures.append(f"{revision}: migration exists but is not in the manifest")
    for revision in sorted(set(recorded) - set(on_disk)):
        failures.append(f"{revision}: recorded in the manifest but not on disk")

    for revision in sorted(set(on_disk) & set(recorded)):
        entry = recorded[revision]
        facts = compute_facts(revision, on_disk[revision], entry.get("applied_at_commit"))  # type: ignore[arg-type]
        verify_entry(entry, facts, failures)

    print(f"manifest    : {MANIFEST.relative_to(PROJECT_ROOT)}")
    print(f"audit commit: {AUDIT_COMMIT[:12]} (HEAD when the live database was migrated)")
    print(f"revisions   : {len(on_disk)} on disk, {len(recorded)} recorded")
    for revision in sorted(recorded):
        entry = recorded[revision]
        if not entry.get("applied_to_production"):
            state = "not applied to the live database"
        elif entry.get("changed_since_audit_commit"):
            state = f"CHANGED after being applied ({', '.join(entry.get('change_commits') or [])})"
        elif entry.get("blob_at_audit_commit"):
            state = "applied, byte-identical to the applied version"
        else:
            state = "applied before the audit commit, unchanged since"
        print(f"  {revision:28} {state}")

    print()
    if failures:
        print("MIGRATION HISTORY DRIFTED:")
        for failure in failures:
            print("  -", failure)
        return 1
    print("MIGRATION HISTORY VERIFIED: every applied revision is recoverable byte for byte,")
    print("and every change made after application is recorded with its impact.")
    return 0


def write_manifest() -> int:
    """Recompute the facts for every revision, preserving the annotations."""
    manifest = load_manifest()
    existing = entries_by_revision(manifest)
    on_disk = revision_files()

    entries: list[dict[str, object]] = []
    for revision in sorted(on_disk):
        previous = existing.get(revision, {})
        entry: dict[str, object] = {
            key: value for key, value in previous.items() if key not in COMPUTED_KEYS
        }
        entry.update(
            compute_facts(
                revision,
                on_disk[revision],
                previous.get("applied_at_commit"),  # type: ignore[arg-type]
            )
        )
        entry.setdefault("applied_to_production", False)
        entry.setdefault("applied_at_commit", None)
        entries.append({key: entry[key] for key in sorted(entry)})

    manifest["manifest_version"] = 1
    manifest["audit_commit"] = AUDIT_COMMIT
    manifest["revisions"] = entries
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {MANIFEST.relative_to(PROJECT_ROOT)} ({len(entries)} revisions)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the recorded migration history")
    parser.add_argument("--write", action="store_true", help="refresh the recorded facts")
    args = parser.parse_args(argv)
    if args.write:
        return write_manifest()
    return verify()


if __name__ == "__main__":
    sys.exit(main())
