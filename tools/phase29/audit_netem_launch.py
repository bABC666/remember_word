"""Read-only, deterministic launch sample. Writes only a new audit directory.

Run with backend/.venv/Scripts/python.exe -X utf8 tools/phase29/audit_netem_launch.py.
Network retrieval is explicit via --fetch; cached responses are never overwritten.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "test-artifacts/netem-launch-decision-20261005"
BASE = ROOT / "test-artifacts/phase29-provenance-evidence/kaoyan-vocab-research"
SEED = "8a3df2035088b8ad2ec8fa2ad4838b55a919b598:launch-v1"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def write(name, value):
    (OUT / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(fetch=False):
    OUT.mkdir(parents=True, exist_ok=True)
    p = ROOT / "test-artifacts/default-lexicon-candidate-v2/candidate-provenance.csv"
    assert sha(p.read_bytes()) == "fb9b943c36abce1e6441e41f2b3485e1f0fbdd017af47713134c9b1fdc37a9e9"
    rows = list(csv.DictReader(p.open(encoding="utf-8", newline="")))
    gap_raw = (ROOT / "test-artifacts/default-lexicon-gap-20261004/gap-results.json").read_bytes()
    assert sha(gap_raw) == "d62dcccd07bff015241a7d28bae1bfd4a6d2e89a0dd48b1288a6962c7f434532"
    gap = json.loads(gap_raw)
    new = {r["word"].casefold() for r in gap["results"] if r["status"] == "adopted"}
    strata = {
        "wikdict": [r for r in rows if r["meaning_source"] == "wikdict"],
        "zh_original": [
            r
            for r in rows
            if r["meaning_source"] == "zhwiktionary" and r["word"].casefold() not in new
        ],
        "zh_restored": [r for r in rows if r["word"].casefold() in new],
    }
    selected = {}

    def add(r, tag):
        selected.setdefault(r["word"], dict(r, strata=[]))["strata"].append(tag)

    for name, pool in strata.items():
        for r in sorted(
            pool,
            key=lambda r: (sha((SEED + "\0" + r["word"].casefold()).encode()), int(r["sequence"])),
        )[:16]:
            add(r, name + ":hash16")
        for r in sorted(pool, key=lambda r: (-len(r["meaning"]), int(r["sequence"])))[:4]:
            add(r, name + ":long4")
    for r in rows:
        if " " in r["word"]:
            add(r, "multiword:all")
        if r["word"] in ["X-ray", "well-known"]:
            add(r, "new_page:all")
    selected = sorted(selected.values(), key=lambda r: int(r["sequence"]))
    write(
        "sample.json",
        {"seed": SEED, "pool_sizes": {k: len(v) for k, v in strata.items()}, "rows": selected},
    )
    preserved = [
        ROOT / "docs" / n
        for n in [
            "V1.2-NETEM-DEFAULT-LEXICON-LAUNCH-CANDIDATE-2026-10-03.md",
            "V1.2-PHASE2.9-POS-MEANING-CONTRACT-DESIGN.md",
            "V1.2-PHASE2.9-ZHWIKTIONARY-LICENSE-EVIDENCE-CHECK.md",
        ]
    ]
    if not (OUT / "preserved-before.json").exists():
        write(
            "preserved-before.json",
            {str(f.relative_to(ROOT)): sha(f.read_bytes()) for f in preserved},
        )
    sys.path.insert(0, str(ROOT / "tools/phase29"))
    from resolve_default_gaps import load_pages

    pinned, titles = load_pages(ROOT / "test-artifacts/default-lexicon-gap-20261004")
    pages = {str(v["oldid"]): v for v in [*pinned.values(), *titles.values()]}
    need = [
        r
        for r in selected
        if r["meaning_source"] == "zhwiktionary"
        and r["source_locator"].split("#")[0][6:] not in pages
    ]
    if fetch and need and not (OUT / "sample-revisions.json").exists():
        url = "https://zh.wiktionary.org/w/api.php?" + urllib.parse.urlencode(
            {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "prop": "revisions",
                "rvprop": "ids|timestamp|content",
                "rvslots": "main",
                "revids": "|".join(r["source_locator"].split("#")[0][6:] for r in need),
            }
        )
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "NETEM-launch-review/1.0"}),
            timeout=45,
        ) as response:
            raw = response.read()
        (OUT / "sample-revisions.json").write_bytes(raw)
        write("sample-fetch.json", {"url": url, "sha256": sha(raw)})
    if (OUT / "sample-revisions.json").exists():
        raw = (OUT / "sample-revisions.json").read_bytes()
        assert sha(raw) == json.loads((OUT / "sample-fetch.json").read_text("utf-8"))["sha256"]
        d = json.loads(raw)
        for page in d.get("query", {}).get("pages", []):
            for rev in page.get("revisions", []):
                pages[str(rev["revid"])] = {
                    "title": page["title"],
                    "oldid": rev["revid"],
                    "timestamp": rev["timestamp"],
                    "text": rev["slots"]["main"]["content"],
                }
    archive = BASE / "raw/wikdict-en-zh.zip"
    assert (
        sha(archive.read_bytes())
        == "62d6d4a8ccf28c28bbe4ec82ac65fa67fd52c0f34d71294ab1d9d95fa3233830"
    )
    with zipfile.ZipFile(archive) as z:
        idx = z.read("wikdict-en-zh/stardict.idx")
        dictionary = z.read("wikdict-en-zh/stardict.dict")
        (OUT / "stardict.ifo").write_bytes(z.read("wikdict-en-zh/stardict.ifo"))
    entries = {}
    cursor = 0
    n = 0
    while cursor < len(idx):
        end = idx.index(b"\0", cursor)
        offset = int.from_bytes(idx[end + 1 : end + 5], "big")
        size = int.from_bytes(idx[end + 5 : end + 9], "big")
        entries[str(n)] = {
            "headword": idx[cursor:end].decode(),
            "offset": offset,
            "body": dictionary[offset : offset + size].decode(),
        }
        cursor = end + 9
        n += 1
    review = []
    for r in selected:
        detail = json.loads(r["source_detail"] or "{}")
        if r["meaning_source"] == "wikdict":
            evidence = [entries[i] for i in detail["entry_index_zero_based"].split("|")]
            assert "|".join(str(e["offset"]) for e in evidence) == detail["dict_offset"]
            assert all(e["headword"].casefold() == r["word"].casefold() for e in evidence)
        elif r["meaning_source"] == "zhwiktionary":
            evidence = pages[r["source_locator"].split("#")[0][6:]]
            assert evidence["title"].casefold() == r["word"].casefold()
        else:
            evidence = {
                "reason": next(
                    x["reason"]
                    for x in gap["results"]
                    if x["word"].casefold() == r["word"].casefold()
                )
            }
        review.append(dict(r, evidence=evidence))
    write("review-evidence.json", review)
    definite = {
        "set": "固定译文把 prearranged 译作“但是”、tennis 相关义译作“袖”等",
        "complex": "数学义译为“綜合大樓”，词性与译文明显不合",
        "bit": "钥匙部件译作“胡子”",
        "honor": "respect 相关义译作“你好”",
        "inhabit": "be present 相关义译作“办”",
        "pitch": "树脂译作“魚”，若干其他义也显著不合",
        "progressive": "原文含“渐进酡”错字",
    }
    boundary = {"prefer", "sick", "gadget", "naive"}
    examine = {
        "build": "编纂对应的是 software compilation，需确认领域和义项",
        "collect": "“累”缺少上下文，需核对原始译文",
        "locate": "“位于指出”疑为旧来源粘连",
        "outward": "“外服的”疑为上游错字",
        "operation": "原文多个英语义项只收“手术”，存在覆盖范围问题",
    }
    ledger = []
    for r in review:
        word = r["word"]
        status = (
            "需剔除样本译文"
            if word in definite
            else "边界修复"
            if word in boundary
            else "待语义裁定"
            if word in examine
            else "抽样未见明显问题"
        )
        note = definite.get(word) or (
            "非释义小节混入，见 repair-results.json" if word in boundary else examine.get(word, "")
        )
        ledger.append(
            {
                "sequence": r["sequence"],
                "word": word,
                "strata": "|".join(r["strata"]),
                "source": r["meaning_source"],
                "locator": r["source_locator"],
                "headword": "精确/大小写折叠匹配"
                if r["meaning_source"] != "missing"
                else "固定缺页",
                "language": "英语小节"
                if r["meaning_source"] == "zhwiktionary"
                else "英中 StarDict 条目"
                if r["meaning_source"] == "wikdict"
                else "无页面",
                "status": status,
                "note": note,
            }
        )
    with (OUT / "sample-review.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, ledger[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(ledger)
    chunks = []
    for r in review:
        chunks.append(
            "## "
            + r["word"]
            + " | "
            + ",".join(r["strata"])
            + "\n"
            + r["meaning"]
            + "\n"
            + r["source_locator"]
            + "\n"
            + json.dumps(r["evidence"], ensure_ascii=False, indent=2)
        )
    (OUT / "review-evidence.txt").write_text("\n\n".join(chunks), encoding="utf-8")
    print(
        json.dumps(
            {
                "sample_size": len(selected),
                "pools": {k: len(v) for k, v in strata.items()},
                "missing_snapshots": sum(
                    isinstance(r["evidence"], dict) and r["evidence"].get("missing_snapshot", False)
                    for r in review
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true")
    main(parser.parse_args().fetch)
