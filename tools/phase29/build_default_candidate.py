"""Build a reproducible, offline NETEM default lexicon candidate.

Only the two named NETEM fields are read.  Glosses stay source text, never
confirmed concise meanings.  Output is review material, not a public import.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "test-artifacts/phase29-provenance-evidence/kaoyan-vocab-research"
NETEM = EVIDENCE / "raw/netem_full_list.json"
WIKDICT = EVIDENCE / "raw/wikdict-en-zh.zip"
ZH = EVIDENCE / "reports/zhwiktionary-full-coverage.json"
EXPECTED = {
    NETEM: "6d71a301321056291902bc4804e223c6926dca0d629a45076a6ca5adab185f62",
    WIKDICT: "62d6d4a8ccf28c28bbe4ec82ac65fa67fd52c0f34d71294ab1d9d95fa3233830",
    ZH: "50c7cda85bdaa731afb0d25caca39c23a8248497c332a8a10014ce3b32978ff6",
}
HAN = re.compile(r"[\u3400-\u9fff]")
CEDICT_OLDIDS = {6667176, 6668835, 6669049, 6669117, 6670534,
                 6670631, 8442262, 8446690, 8456548, 8459237}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run(output: Path, gap_results: Path | None = None) -> dict:
    for path, expected in EXPECTED.items():
        if sha(path) != expected:
            raise ValueError(f"source fingerprint changed: {path}")
    sys.path.insert(0, str(ROOT / "backend"))
    helper_path = ROOT / "tools/phase29/rehearsal/50_full_lexicon_rehearsal.py"
    spec = importlib.util.spec_from_file_location("phase29_full_helper", helper_path)
    assert spec and spec.loader
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    netem = json.loads(NETEM.read_text("utf-8"))
    original = next(iter(netem.values()))
    membership, duplicates = helper.membership_rows(original)
    wik_rows, _ = helper.wikdict_backfill_rows(WIKDICT, membership)
    wik = {row["word"].strip().casefold(): row for row in wik_rows}
    zh = json.loads(ZH.read_text("utf-8"))
    supplements: dict[str, dict] = {}
    if gap_results is not None:
        gap = json.loads(gap_results.read_text("utf-8"))
        base_path = ROOT / "test-artifacts/default-lexicon-candidate/candidate-provenance.csv"
        if sha(base_path) != gap["base_provenance_sha256"]:
            raise ValueError("gap results are not based on the frozen candidate")
        supplements = {item["word"].strip().casefold(): item for item in gap["results"]
                       if item["status"] == "adopted"}
        if len(supplements) != sum(item["status"] == "adopted" for item in gap["results"]):
            raise ValueError("duplicate gap supplement")
    provenance = []
    import_rows = []
    counts = {"wikdict": 0, "zhwiktionary": 0, "missing": 0}
    for sequence, member in enumerate(membership, 1):
        word = member["word"].strip()
        key = word.casefold()
        source = wik.get(key)
        if source:
            meaning = source["wikdict_meaning"]
            origin = "wikdict"
            locator = "stardict.idx#" + source["wikdict_entry_index"]
            source_detail = json.dumps({
                "headword": source["wikdict_headword"],
                "entry_index_zero_based": source["wikdict_entry_index"],
                "dict_offset": source["wikdict_entry_offset"],
                "match_rule": source["match_rule"],
            }, ensure_ascii=False, sort_keys=True)
        else:
            item = zh.get(key, {})
            definitions = item.get("defs", []) if item.get("oldid") not in CEDICT_OLDIDS else []
            usable = list(dict.fromkeys(value.strip() for value in definitions
                                        if isinstance(value, str) and HAN.search(value) and value.strip()))
            meaning = "；".join(usable)
            if len(meaning) > 2000:
                # Preserve the whole source in provenance; the existing upload
                # parser cannot admit a longer meaning field.
                meaning = ""
            origin = "zhwiktionary" if meaning else "missing"
            locator = f"oldid:{item['oldid']}" if origin == "zhwiktionary" else ""
            source_detail = json.dumps({"oldid": item.get("oldid"),
                                        "cache_definition_indices": [i for i, value in enumerate(definitions)
                                                                     if isinstance(value, str) and HAN.search(value)]},
                                       ensure_ascii=False, sort_keys=True) if origin == "zhwiktionary" else ""
            supplement = supplements.get(key)
            if origin == "missing" and supplement:
                meaning = supplement["meaning"]
                origin = "zhwiktionary"
                locator = f"oldid:{supplement['oldid']}#line:" + "|".join(
                    str(line["wikitext_line"]) for line in supplement["lines"])
                source_detail = json.dumps({
                    "basis": supplement["basis"], "revision_timestamp": supplement["revision_timestamp"],
                    "revision_url": supplement["revision_url"],
                    "snapshot_file": supplement["snapshot_file"],
                    "snapshot_sha256": supplement["snapshot_sha256"],
                    "line_sha256": [line["raw_line_sha256"] for line in supplement["lines"]],
                    "parse_method": [line["parse_method"] for line in supplement["lines"]],
                }, ensure_ascii=False, sort_keys=True)
        counts[origin] += 1
        import_rows.append({"word": word, "meaning": meaning})
        provenance.append({"sequence": sequence, "netem_rank": member["netem_rank"],
                           "word": word, "meaning": meaning, "meaning_source": origin,
                           "source_locator": locator, "source_detail": source_detail})
    output.mkdir(parents=True, exist_ok=True)
    rows_csv(output / "candidate-provenance.csv", provenance,
             ["sequence", "netem_rank", "word", "meaning", "meaning_source", "source_locator", "source_detail"])
    # Parser limit is 1 MiB. Split by encoded bytes, retaining source order.
    parts = []
    chunk = []
    for row in import_rows:
        trial = io.StringIO(newline="")
        writer = csv.DictWriter(trial, ["word", "meaning"], lineterminator="\n")
        writer.writeheader()
        writer.writerows([*chunk, row])
        if len(trial.getvalue().encode("utf-8")) > 900_000 and chunk:
            parts.append(chunk)
            chunk = []
        chunk.append(row)
    if chunk:
        parts.append(chunk)
    part_manifest = []
    for number, chunk in enumerate(parts, 1):
        path = output / f"import-{number:02}.csv"
        rows_csv(path, chunk, ["word", "meaning"])
        part_manifest.append({"file": path.name, "rows": len(chunk), "sha256": sha(path)})
    manifest = {"status": "review_candidate_only", "netem_commit": "70dc6b68c855f21e666a7a291ff8ead5ca1f7b44",
                "wikdict_version": "wikdict-en-zh-2026-06-23", "source_sha256": {str(p.relative_to(ROOT)): h for p, h in EXPECTED.items()},
                "normalization": "strip_casefold_v1", "raw_rows": len(original), "unique_words": len(membership),
                "duplicates": duplicates, "counts": counts, "import_parts": part_manifest,
                "provenance_sha256": sha(output / "candidate-provenance.csv"),
                "notes": "WikDict exact headword first; zh cache oldid only on whole-word miss; no English definitions or confirmed concise meanings."}
    if gap_results is not None:
        manifest["gap_results_sha256"] = sha(gap_results)
        manifest["gap_basis_counts"] = gap["counts"]
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "test-artifacts/default-lexicon-candidate")
    parser.add_argument("--gap-results", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.gap_results), ensure_ascii=False, indent=2))
