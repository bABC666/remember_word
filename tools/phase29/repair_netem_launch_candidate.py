"""Apply only section-boundary deletions to adopted definitions in an isolated copy.

Does not add senses from beyond the historical cache limit, change headwords,
perform semantic correction, or touch the frozen v2 package / any database.
"""

import csv
import hashlib
import json
from pathlib import Path

from resolve_default_gaps import extract, load_pages
from zhwiktionary_clean_measure import clean

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "test-artifacts/netem-launch-decision-20261005"
BASE = ROOT / "test-artifacts/phase29-provenance-evidence/kaoyan-vocab-research"


def main():
    candidate = ROOT / "test-artifacts/default-lexicon-candidate-v2/candidate-provenance.csv"
    assert (
        hashlib.sha256(candidate.read_bytes()).hexdigest()
        == "fb9b943c36abce1e6441e41f2b3485e1f0fbdd017af47713134c9b1fdc37a9e9"
    )
    rows = list(csv.DictReader(candidate.open(encoding="utf-8")))
    cache_raw = (BASE / "reports/zhwiktionary-full-coverage.json").read_bytes()
    assert (
        hashlib.sha256(cache_raw).hexdigest()
        == "50c7cda85bdaa731afb0d25caca39c23a8248497c332a8a10014ce3b32978ff6"
    )
    cache = json.loads(cache_raw)
    pages = {}
    manifest = json.loads((OUT / "upstream/fetch-manifest.json").read_text("utf-8"))
    for file in sorted((OUT / "upstream").glob("original-*.json")):
        assert hashlib.sha256(file.read_bytes()).hexdigest() == manifest[file.name]["sha256"]
        for page in json.loads(file.read_bytes())["query"]["pages"]:
            for revision in page.get("revisions", []):
                pages[str(revision["revid"])] = (
                    page["title"],
                    revision["slots"]["main"]["content"],
                )
    pinned, titles = load_pages(ROOT / "test-artifacts/default-lexicon-gap-20261004")
    gaps = {str(r["oldid"]): r for r in [*pinned.values(), *titles.values()]}
    changes = []
    issues = []
    capped = []
    for row in rows:
        if row["meaning_source"] != "zhwiktionary":
            continue
        oldid = row["source_locator"].split("#")[0][6:]
        if "#line:" in row["source_locator"]:
            fact = gaps[oldid]
            assert fact["title"].casefold() == row["word"].casefold()
            parsed = extract(fact["text"])
            assert "；".join(x["meaning"] for x in parsed) == row["meaning"], row["word"]
            assert [str(x["wikitext_line"]) for x in parsed] == row["source_locator"].split(
                "#line:"
            )[1].split("|")
            continue
        title, text = pages[oldid]
        assert title.casefold() == row["word"].casefold(), row["word"]
        item = cache[row["word"].casefold()]
        if item.get("n", 0) > len(item["defs"]):
            capped.append(
                {
                    "word": row["word"],
                    "cached": len(item["defs"]),
                    "original_count_including_noise": item["n"],
                }
            )
        if "CC-CEDICT" in text:
            issues.append({"word": row["word"], "issue": "CC-CEDICT"})
        allowed = set(clean(text)[0])
        detail = json.loads(row["source_detail"])
        indices = detail["cache_definition_indices"]
        removed = [
            {"index": i, "text": item["defs"][i]}
            for i in indices
            if item["defs"][i].strip() not in allowed
        ]
        kept = [i for i in indices if item["defs"][i].strip() in allowed]
        if removed:
            before = row["meaning"]
            row["meaning"] = "；".join(dict.fromkeys(item["defs"][i].strip() for i in kept))
            detail["cache_definition_indices"] = kept
            row["source_detail"] = json.dumps(detail, ensure_ascii=False, sort_keys=True)
            if not row["meaning"]:
                row.update(meaning_source="missing", source_locator="", source_detail="")
            changes.append(
                {
                    "word": row["word"],
                    "oldid": oldid,
                    "before": before,
                    "after": row["meaning"],
                    "removed": removed,
                    "kept_cache_indices": kept,
                }
            )
    target = OUT / "repaired-candidate-provenance.csv"
    with target.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, rows[0].keys(), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    result = {
        "changes": changes,
        "changed_words": len(changes),
        "removed_segments": sum(len(c["removed"]) for c in changes),
        "empty_after": sum(not c["after"] for c in changes),
        "extra_license_markers": issues,
        "historical_cache_caps": capped,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    original = list(csv.DictReader(candidate.open(encoding="utf-8")))
    assert [(r["word"], r["sequence"], r["netem_rank"]) for r in rows] == [
        (r["word"], r["sequence"], r["netem_rank"]) for r in original
    ]
    assert [r for r in rows if r["meaning_source"] == "missing"] == [
        r for r in original if r["meaning_source"] == "missing"
    ]
    assert sum(r["meaning_source"] == "missing" for r in rows) == 59
    from build_default_public_package import run

    package = OUT / "repaired-public-package"
    run(ROOT / "test-artifacts/default-lexicon-candidate-v2", package, "2026-10-05T00:00:00Z")
    source_path = package / "zhwiktionary.csv"
    source_rows = list(csv.DictReader(source_path.open(encoding="utf-8")))
    by_word = {r["word"]: r for r in rows}
    for source in source_rows:
        repaired = by_word[source["word"]]
        source["meaning"] = repaired["meaning"]
        detail = json.loads(repaired["source_detail"])
        if "cache_definition_indices" in detail:
            source["source_position"] = "cache:def:" + "|".join(
                map(str, detail["cache_definition_indices"])
            )
    with source_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, source_rows[0].keys(), lineterminator="\n")
        w.writeheader()
        w.writerows(source_rows)
    manifest_path = package / "manifest.json"
    source_manifest = json.loads(manifest_path.read_text("utf-8"))
    prov = source_manifest["sources"][2]["provenance"]
    prov["version"] += "; section-boundary-repair-20261005"
    prov["storage_locator"] += "; isolated repaired candidate sha256:" + result["sha256"]
    manifest_path.write_text(
        json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "repair-results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
