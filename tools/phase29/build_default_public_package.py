"""Turn the pinned v2 candidate into a local public-import source package.

The package is for isolated engineering acceptance. Source declarations record
licence evidence; they do not approve distribution or production publication.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "test-artifacts/default-lexicon-candidate-v2"
EXPECTED_PROVENANCE_SHA = "fb9b943c36abce1e6441e41f2b3485e1f0fbdd017af47713134c9b1fdc37a9e9"
COUNTS = {"wikdict": 4823, "zhwiktionary": 646, "missing": 59}
FIELDS = ["word", "meaning", "source_position", "source_revision"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _source_position(row: dict[str, str], detail: dict) -> str:
    origin = row["meaning_source"]
    if origin == "wikdict":
        index = detail["entry_index_zero_based"]
        offset = detail["dict_offset"]
        assert row["source_locator"] == f"stardict.idx#{index}"
        assert row["word"].casefold() in {
            headword.casefold() for headword in detail["headword"].split("|")
        }
        return f"stardict.idx#{index}:offset:{offset}"
    if origin == "zhwiktionary":
        oldid = detail.get("oldid")
        if oldid is None:
            # The pinned gap result stores its oldid in source_locator instead.
            oldid = row["source_locator"].split("#", 1)[0].removeprefix("oldid:")
        assert row["source_locator"].startswith(f"oldid:{oldid}")
        if "#line:" in row["source_locator"]:
            lines = row["source_locator"].split("#line:", 1)[1]
            assert len(detail["line_sha256"]) == len(lines.split("|"))
            return f"wikitext:line:{lines}"
        indices = detail["cache_definition_indices"]
        assert indices and all(isinstance(value, int) and value >= 0 for value in indices)
        return "cache:def:" + "|".join(map(str, indices))
    raise ValueError(f"unsupported meaning source: {origin}")


def run(candidate: Path, output: Path, obtained_at_utc: str) -> dict:
    stamp = datetime.fromisoformat(obtained_at_utc)
    if stamp.tzinfo is None:
        raise ValueError("obtained_at_utc must include a timezone")
    manifest = json.loads((candidate / "manifest.json").read_text("utf-8"))
    provenance = candidate / "candidate-provenance.csv"
    if manifest["provenance_sha256"] != EXPECTED_PROVENANCE_SHA or sha256(provenance) != EXPECTED_PROVENANCE_SHA:
        raise ValueError("not the pinned v2 candidate")
    if manifest["unique_words"] != 5528 or manifest["counts"] != COUNTS:
        raise ValueError("v2 candidate counts changed")
    with provenance.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 5528:
        raise ValueError("v2 candidate row count changed")
    seen: set[str] = set()
    primary: list[dict[str, str]] = []
    wikdict: list[dict[str, str]] = []
    zhwiktionary: list[dict[str, str]] = []
    observed = dict.fromkeys(COUNTS, 0)
    for sequence, row in enumerate(rows, 1):
        word = row["word"].strip()
        key = word.casefold()
        if not word or key in seen or int(row["sequence"]) != sequence:
            raise ValueError(f"candidate membership/order changed at {sequence}")
        seen.add(key)
        rank = int(row["netem_rank"])
        primary.append({"word": word, "meaning": "", "source_position": f"netem:rank:{rank}",
                        "source_revision": manifest["netem_commit"]})
        origin = row["meaning_source"]
        if origin not in observed:
            raise ValueError(f"unexpected source: {origin}")
        observed[origin] += 1
        if origin == "missing":
            if row["meaning"] or row["source_locator"] or row["source_detail"]:
                raise ValueError(f"missing meaning is not empty: {word}")
            continue
        if not row["meaning"] or not row["source_detail"]:
            raise ValueError(f"missing adopted provenance: {word}")
        detail = json.loads(row["source_detail"])
        source_position = _source_position(row, detail)
        oldid = row["source_locator"].split("#", 1)[0].removeprefix("oldid:")
        target = wikdict if origin == "wikdict" else zhwiktionary
        target.append({"word": word, "meaning": row["meaning"],
                       "source_position": source_position,
                       "source_revision": oldid if origin == "zhwiktionary" else ""})
    if observed != COUNTS:
        raise ValueError(f"source counts changed: {observed}")
    output.mkdir(parents=True, exist_ok=True)
    for name, values in (("netem-words", primary), ("wikdict", wikdict), ("zhwiktionary", zhwiktionary)):
        _write_csv(output / f"{name}.csv", values)

    def provenance_block(publisher: str, version: str, license_id: str, storage: str) -> dict[str, str]:
        return {"publisher": publisher, "version": version, "obtained_at_utc": obtained_at_utc,
                "license_id": license_id, "use_scope": "local-isolated-evaluation-only",
                "display_scope": "local-isolated-evaluation-only; publication-pending-approval",
                "storage_locator": storage}

    source_manifest = {"required_fields": [], "sources": [
        {"id": "netem", "role": "primary", "file": "netem-words.csv",
         "columns": {"word": "word"}, "sense_key_column": "source_position",
         "revision": {"value": manifest["netem_commit"],
                      "url_template": "https://github.com/exam-data/NETEMVocabulary/commit/{revision}"},
         "provenance": provenance_block("exam-data/NETEMVocabulary", manifest["netem_commit"],
                                        "CC-BY-NC-SA-4.0", "pinned NETEM JSON sha256:" + manifest["source_sha256"][next(k for k in manifest["source_sha256"] if k.endswith("netem_full_list.json"))])},
        {"id": "wikdict", "role": "meaning", "file": "wikdict.csv",
         "columns": {"word": "word", "meaning": "meaning"}, "sense_key_column": "source_position",
         "provenance": provenance_block("WikDict / Wiktionary via DBnary", manifest["wikdict_version"],
                                        "CC-BY-SA-4.0-declared-in-ifo", "pinned WikDict ZIP sha256:" + manifest["source_sha256"][next(k for k in manifest["source_sha256"] if k.endswith("wikdict-en-zh.zip"))])},
        {"id": "zhwiktionary", "role": "meaning", "file": "zhwiktionary.csv",
         "columns": {"word": "word", "meaning": "meaning"},
         "sense_key_column": "source_position",
         "revision": {"column": "source_revision",
                      "url_template": "https://zh.wiktionary.org/w/index.php?oldid={revision}"},
         "provenance": provenance_block("中文维基词典 contributors", "pinned oldid per row / v2 gap snapshot",
                                        "per-revision-license-review-pending", "v2 candidate provenance sha256:" + EXPECTED_PROVENANCE_SHA)},
    ]}
    (output / "manifest.json").write_text(json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "decisions.json").write_text(json.dumps({"format_version": 1, "decisions": []}) + "\n", encoding="utf-8")
    return {"rows": len(primary), "counts": observed,
            "files": {path.name: sha256(path) for path in sorted(output.iterdir()) if path.is_file()},
            "candidate_sha256": EXPECTED_PROVENANCE_SHA}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, default=CANDIDATE)
    parser.add_argument("--output", type=Path, default=ROOT / "test-artifacts/default-lexicon-public-v2")
    parser.add_argument("--obtained-at-utc", required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.candidate, args.output, args.obtained_at_utc), ensure_ascii=False, indent=2))
