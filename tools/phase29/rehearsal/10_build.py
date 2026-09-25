"""Build the out-of-repository rehearsal package for the Phase 2.9 public-lexicon import.

Real data, no network, no repository write:
  * NETEM 5530-word table  -> primary source (membership + order only)
  * zh.wiktionary, pinned per-word oldid (local cleaned cache) -> meanings + IPA
  * WikDict en-zh stardict (CC BY-SA 4.0, in-package .ifo) -> supplementary meanings

Outputs land in <PKG>/sources (the --source-root the CLI will be pointed at),
<PKG>/notes (fingerprints, sidecars, source/licence table) and nothing else.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import shutil
import zipfile
from collections import Counter
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", default=r"C:\Temp\dsh-bSBXrH\kaoyan-vocab-research",
                        help="隔离的调研证据目录（内含 raw/ 与 reports/）")
    parser.add_argument("--package-dir", default=None,
                        help="预演包目录，默认 <evidence-dir>/rehearsal")
    parser.add_argument("--repo", default=r"D:\背单词web", help="仓库工作区（只读：读钉住清单）")
    return parser.parse_args()


ARGS = parse_args()
BASE = Path(ARGS.evidence_dir)
PKG = Path(ARGS.package_dir) if ARGS.package_dir else BASE / "rehearsal"
SRC = PKG / "sources"
NOTES = PKG / "notes"
REPO = Path(ARGS.repo)

SEED = 20260924
SLICE_SIZE = 300
EXTRA_SUPPLEMENT_ROWS = 10
DEMO_SIZE = 20

NETEM_ID = "netem-5530-vocab-2026-09-24"
ZH_ID = "zhwiktionary-pinned-oldid"
WIK_ID = "wikdict-en-zh-2026-06-23"

FREQ_DISCLAIMER = (
    "freq_claimed_mixed_exam_corpus 是 NETEM 作者自述的混合考试语料计数"
    "（四六级+考研+专四专八约 200 套试卷、词形还原、未区分英一/英二、未给年份范围），"
    "只能标为“来源自述的统计”，不得标成“考研真题词频”，也不得写进 frequency_source。"
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def rule_a(text: str) -> str:
    return "".join(char for char in (text or "") if char.isalnum()).lower()


def rule_b(text: str) -> str:
    """The implemented importer rule: backend/app/services/public_lexicon_preview.py:151."""
    return (text or "").strip().casefold()


def flat(text: str) -> tuple[str, bool]:
    """Cells must not contain physical newlines: the preview parses one line per row."""
    cleaned = re.sub(r"[\r\n\u2028\u2029]+", " ", text or "")
    return cleaned, cleaned != (text or "")


# --------------------------------------------------------------------------- inputs
netem_doc = json.loads((BASE / "raw" / "netem_full_list.json").read_text(encoding="utf-8"))
netem_rows = next(iter(netem_doc.values()))
coverage = json.loads(
    (BASE / "reports" / "zhwiktionary-full-coverage.json").read_text(encoding="utf-8")
)
quality_dump = json.loads(
    (BASE / "reports" / "quality-sample-dump.json").read_text(encoding="utf-8")
)
pinned_tsv = REPO / "tools" / "phase29" / "phase29-zhwiktionary-pinned-oldids.tsv"

# ----------------------------------------------------------------- WikDict (stardict)
wik_entries: list[tuple[str, int, str]] = []
with zipfile.ZipFile(BASE / "raw" / "wikdict-en-zh.zip") as archive:
    wik_idx = archive.read("wikdict-en-zh/stardict.idx")
    wik_dict = archive.read("wikdict-en-zh/stardict.dict")
    wik_ifo = archive.read("wikdict-en-zh/stardict.ifo")
    wik_syn = archive.read("wikdict-en-zh/stardict.syn")
cursor = 0
while cursor < len(wik_idx):
    end = wik_idx.find(b"\0", cursor)
    if end < 0:
        break
    headword = wik_idx[cursor:end].decode("utf-8", "replace")
    offset = int.from_bytes(wik_idx[end + 1:end + 5], "big")
    size = int.from_bytes(wik_idx[end + 5:end + 9], "big")
    wik_entries.append((headword, offset, wik_dict[offset:offset + size].decode("utf-8", "replace")))
    cursor = end + 9

HAN = re.compile(r"[\u4e00-\u9fff]")
GLOSS_DIV = re.compile(r"<div>([^<]*)</div>")


def wikdict_glosses(body: str) -> list[str]:
    """Chinese glosses: the <div>…</div> fragments that actually contain Han characters.

    Verified against reports/quality-sample-dump.json (see notes/03-fingerprints.md).
    """
    out: list[str] = []
    for raw in GLOSS_DIV.findall(body):
        text = raw.strip()
        if text and HAN.search(text) and text not in out:
            out.append(text)
    return out


def wikdict_flat(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


wik_by_a: dict[str, list[tuple[str, int, list[str]]]] = {}
for index, (headword, _offset, body) in enumerate(wik_entries):
    glosses = wikdict_glosses(body)
    if glosses:
        wik_by_a.setdefault(rule_a(headword), []).append((headword, index, glosses))

# extraction self-check against the earlier 60-word measurement
agree = 0
checked = 0
mismatch: list[str] = []
for item in quality_dump:
    key = rule_a(item["word"])
    recorded = [g for g in item.get("wikdict_defs") or [] if g]
    if not recorded:
        continue
    checked += 1
    mine: list[str] = []
    for _headword, _index, glosses in wik_by_a.get(key, []):
        for gloss in glosses:
            flat_gloss = wikdict_flat(gloss)
            if flat_gloss and flat_gloss not in mine:
                mine.append(flat_gloss)
    if mine == [wikdict_flat(g) for g in recorded]:
        agree += 1
    else:
        mismatch.append(f"{item['word']}: recorded={recorded} extracted={mine}")

# --------------------------------------------------------------------- NETEM universe
unique_rows: dict[str, dict[str, object]] = {}
duplicate_keys: dict[str, list[dict[str, object]]] = {}
for row in netem_rows:
    key = rule_b(str(row["单词"]))
    if key in unique_rows:
        duplicate_keys.setdefault(key, [unique_rows[key]]).append(row)
        continue
    unique_rows[key] = row
universe = sorted(unique_rows)
sample_keys = set(random.Random(SEED).sample(universe, SLICE_SIZE))
# First occurrence per key, primary order (== NETEM rank order) preserved; this is the
# same rule the locked plan applies (primary_source_first_occurrence_v1).
slice_keys: set[str] = set()
slice_rows: list[dict[str, object]] = []
for row in netem_rows:
    key = rule_b(str(row["单词"]))
    if key in sample_keys and key not in slice_keys:
        slice_keys.add(key)
        slice_rows.append(unique_rows[key])

# ------------------------------------------------------- per-word zh/WikDict resolution
raw_slice_rows = slice_rows
resolved: list[dict[str, object]] = []
for row in raw_slice_rows:
    surface = str(row["单词"])
    entry = coverage.get(rule_a(surface)) or {}
    resolved.append({
        "word": surface,
        "key_b": rule_b(surface),
        "key_a": rule_a(surface),
        "rank": int(row["序号"]),
        "freq": int(row["词频"]),
        "gloss": row["释义"],
        "zh_surface": entry.get("word", ""),
        "zh_exists": bool(entry.get("exists")),
        "zh_oldid": entry.get("oldid"),
        "zh_defs": [str(d) for d in entry.get("defs") or []],
        "zh_ipa": str(entry.get("ipa") or ""),
        "wik_entries": wik_by_a.get(rule_a(surface), []),
    })
slice_rows = resolved

# extras: supplement rows for words the primary source does not contain
zh_all_hits = sorted(key for key, value in coverage.items() if value.get("defs"))
extra_keys = [key for key in zh_all_hits if key not in sample_keys][:EXTRA_SUPPLEMENT_ROWS]

# ------------------------------------------------------------------------ CSV writing
MEMBERSHIP_HEADER = ["word", "netem_rank", "freq_claimed_mixed_exam_corpus"]
GLOSS_HEADER = [*MEMBERSHIP_HEADER, "netem_gloss_rights_unclear"]

if SRC.exists():
    shutil.rmtree(SRC)
SRC.mkdir(parents=True)
NOTES.mkdir(parents=True, exist_ok=True)

def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> dict[str, object]:
    flattened = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            cells = []
            for cell in row:
                text, changed = flat(str(cell))
                flattened += int(changed)
                cells.append(text)
            writer.writerow(cells)
    return {
        "file": path.name,
        "header": header,
        "data_rows": len(rows),
        "byte_size": path.stat().st_size,
        "sha256": sha256_file(path),
        "cells_with_newlines_flattened": flattened,
    }


def membership_rows(rows: list[dict[str, object]]) -> list[list[str]]:
    return [[r["word"], r["rank"], r["freq"]] for r in rows]


def gloss_rows(rows: list[dict[str, object]]) -> list[list[str]]:
    return [[r["word"], r["rank"], r["freq"], r["gloss"]] for r in rows]


def zh_included(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [r for r in rows if r["zh_defs"] or r["zh_ipa"]]


def wik_included(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [r for r in rows if r["wik_entries"]]


def zh_rows(rows: list[dict[str, object]], extra: list[str]) -> list[list[str]]:
    out: list[list[str]] = []
    for r in zh_included(rows):
        out.append([
            r["word"], "；".join(r["zh_defs"]), r["zh_ipa"],
            r["zh_oldid"] or "", "yes" if r["zh_exists"] else "no",
        ])
    for key in extra:
        entry = coverage[key]
        out.append([
            entry.get("word", key), "；".join(entry.get("defs") or []),
            entry.get("ipa") or "", entry.get("oldid") or "",
            "yes" if entry.get("exists") else "no",
        ])
    return out


def wik_rows(rows: list[dict[str, object]]) -> list[list[str]]:
    out: list[list[str]] = []
    for r in wik_included(rows):
        entries = r["wik_entries"]
        glosses: list[str] = []
        for _headword, _index, found in entries:
            for gloss in found:
                text = wikdict_flat(gloss)
                if text and text not in glosses:
                    glosses.append(text)
        out.append([
            r["word"], "；".join(glosses),
            "|".join(headword for headword, _i, _g in entries),
            "|".join(str(index) for _h, index, _g in entries),
        ])
    return out


ZH_HEADER = ["word", "zh_meaning", "zh_ipa", "zh_oldid", "zh_page_exists"]
WIK_HEADER = ["word", "wikdict_meaning", "wikdict_headword", "wikdict_entry_index"]

converted: list[dict[str, object]] = []
manifest_r1 = {
    "required_fields": ["meaning"],
    "sources": [
        {"id": NETEM_ID, "role": "primary", "file": "netem-words.csv",
         "columns": {"word": "word"}, "required_fields": ["word"],
         "encoding": "utf-8", "delimiter": ","},
        {"id": ZH_ID, "role": "meaning", "file": "zhwiktionary.csv",
         "columns": {"word": "word", "meaning": "zh_meaning", "phonetic": "zh_ipa"},
         "required_fields": ["word"], "encoding": "utf-8", "delimiter": ","},
        {"id": WIK_ID, "role": "meaning", "file": "wikdict.csv",
         "columns": {"word": "word", "meaning": "wikdict_meaning"},
         "required_fields": ["word"], "encoding": "utf-8", "delimiter": ","},
    ],
}
manifest_r2 = {
    "required_fields": ["meaning"],
    "sources": [
        {"id": NETEM_ID, "role": "primary", "file": "netem-words-with-gloss.csv",
         "columns": {"word": "word", "meaning": "netem_gloss_rights_unclear"},
         "required_fields": ["word"], "encoding": "utf-8", "delimiter": ","},
        *manifest_r1["sources"][1:],
    ],
}

converted.append(write_csv(SRC / "netem-words.csv", MEMBERSHIP_HEADER, membership_rows(slice_rows)))
converted.append(write_csv(
    SRC / "netem-words-with-gloss.csv", GLOSS_HEADER, gloss_rows(slice_rows)))
converted.append(write_csv(SRC / "zhwiktionary.csv", ZH_HEADER, zh_rows(slice_rows, extra_keys)))
converted.append(write_csv(SRC / "wikdict.csv", WIK_HEADER, wik_rows(slice_rows)))

# ---------------------------------------------------------------- demo subset (20 words)
demo_rows: list[dict[str, object]] = []
for r in slice_rows:
    if r["zh_defs"] or r["wik_entries"]:
        demo_rows.append(r)
    if len(demo_rows) == DEMO_SIZE:
        break
demo_dir = SRC / "demo20"
demo_dir.mkdir()
converted.append(write_csv(demo_dir / "netem-words.csv", MEMBERSHIP_HEADER, membership_rows(demo_rows)))
converted.append(write_csv(demo_dir / "zhwiktionary.csv", ZH_HEADER, zh_rows(demo_rows, [])))
converted.append(write_csv(demo_dir / "wikdict.csv", WIK_HEADER, wik_rows(demo_rows)))

manifest_demo = {
    "required_fields": ["meaning"],
    "sources": [
        {**manifest_r1["sources"][0], "file": "demo20/netem-words.csv"},
        {**manifest_r1["sources"][1], "file": "demo20/zhwiktionary.csv"},
        {**manifest_r1["sources"][2], "file": "demo20/wikdict.csv"},
    ],
}

# Contract probe: a manifest that declares the NETEM table as a frequency corpus.
# 2.9-B (考研真题词频) has no verified corpus or agreed statistics contract, so the
# locked plan must refuse the whole file rather than half-accept a frequency source.
manifest_r3 = {
    "required_fields": ["meaning"],
    "sources": [{**manifest_r1["sources"][0], "role": "exam_corpus"},
                manifest_r1["sources"][1]],
}

# Contract probe: a manifest whose source file climbs out of --source-root. The
# read-only tooling must refuse to read it, or --source-root would not be a boundary.
manifest_escape = {
    "required_fields": ["meaning"],
    "sources": [
        {**manifest_r1["sources"][0], "file": "../../raw/netem_full_list.json"},
        manifest_r1["sources"][2],
    ],
}

# CSV line numbers: header is line 1, so evidence line = index + 2.
demo_zh_line = {
    row["word"]: index + 2 for index, row in enumerate(zh_included(demo_rows))
}
demo_wik_line = {
    row["word"]: index + 2 for index, row in enumerate(wik_included(demo_rows))
}

demo_decisions: list[dict[str, object]] = []
demo_notes: list[dict[str, object]] = []
for r in demo_rows:
    zh_value = "；".join(r["zh_defs"]).strip()
    wik_value = "；".join(
        dict.fromkeys(wikdict_flat(g) for _h, _i, found in r["wik_entries"] for g in found)
    ).strip()
    values = [v for v in dict.fromkeys([zh_value, wik_value]) if v]
    needs = len(values) > 1
    demo_notes.append({
        "word": r["word"], "rank": r["rank"], "zh": zh_value, "wikdict": wik_value,
        "machine_drafted_select": needs,
    })
    if not needs:
        continue
    if zh_value:
        locator = {"source_id": ZH_ID, "line": demo_zh_line[r["word"]]}
    else:
        locator = {"source_id": WIK_ID, "line": demo_wik_line[r["word"]]}
    demo_decisions.append({
        "action": "select", "normalized_word": r["key_b"], "field": "meaning",
        "evidence": [locator],
        "note": "演练机器草案（非人工裁定）：在冲突值中选许可与质量记录都更清楚的一侧；"
                "真实导入必须由人工逐词重做。",
    })

# ------------------------------------------------------------------------ manifests
def write_json(path: Path, value: object) -> dict[str, object]:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "file": str(path.relative_to(SRC)).replace("\\", "/"),
        "data_rows": len(value.get("sources", [])) if isinstance(value, dict) else 0,
        "byte_size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


converted.append(write_json(SRC / "manifest-r1.json", manifest_r1))
converted.append(write_json(SRC / "manifest-r2.json", manifest_r2))
converted.append(write_json(SRC / "manifest-demo20.json", manifest_demo))
converted.append(write_json(SRC / "manifest-r3-exam-corpus.json", manifest_r3))
converted.append(write_json(SRC / "manifest-escape.json", manifest_escape))
converted.append(write_json(
    SRC / "decisions-r1-empty.json", {"format_version": 1, "decisions": []}))
converted.append(write_json(
    SRC / "decisions-demo20.json", {"format_version": 1, "decisions": demo_decisions}))

# ------------------------------------------------------------------------- sidecars
sidecar_header = ["word", "key_b", "key_a", "netem_rank", "freq_claimed_mixed_exam_corpus",
                  "zh_surface", "zh_exists", "zh_oldid", "zh_defs_n", "zh_ipa",
                  "wikdict_headword", "wikdict_entry_index", "wikdict_defs_n"]
with (NOTES / "per-word-pins.tsv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
    writer.writerow(sidecar_header)
    for r in slice_rows:
        writer.writerow([
            r["word"], r["key_b"], r["key_a"], r["rank"], r["freq"],
            r["zh_surface"], "yes" if r["zh_exists"] else "no", r["zh_oldid"] or "",
            len(r["zh_defs"]), r["zh_ipa"],
            "|".join(h for h, _i, _g in r["wik_entries"]),
            "|".join(str(i) for _h, i, _g in r["wik_entries"]),
            sum(len(g) for _h, _i, g in r["wik_entries"]),
        ])

# ------------------------------------------------- pinned-oldid cross-check (repo file)
pin_rows: dict[str, str] = {}
if pinned_tsv.is_file():
    with pinned_tsv.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for line in reader:
            word = line.get("word") or line.get("单词") or ""
            pin_rows[rule_a(word)] = str(line.get("oldid") or "")
cross_checked = cross_agree = 0
cross_mismatch: list[str] = []
for r in slice_rows:
    if not r["zh_oldid"]:
        continue
    pinned = pin_rows.get(r["key_a"])
    if not pinned:
        continue
    cross_checked += 1
    if pinned == str(r["zh_oldid"]):
        cross_agree += 1
    else:
        cross_mismatch.append(f"{r['word']}: pinned={pinned} coverage={r['zh_oldid']}")

# --------------------------------------------------------------------- fingerprints
originals = [
    {"label": "NETEM 5530 词表（原始 JSON，唯一真源）",
     "role": "词表/排序/词频", "path": str(BASE / "raw" / "netem_full_list.json")},
    {"label": "NETEM 5530 词表（作者附带的 SQL 导出）",
     "role": "同上（备用形态）", "path": str(BASE / "raw" / "netem_full_list.sql")},
    {"label": "WikDict en-zh stardict 包", "role": "补充释义",
     "path": str(BASE / "raw" / "wikdict-en-zh.zip")},
    {"label": "zh.wiktionary 全量清洗缓存（按 oldid 抓取）", "role": "释义/音标",
     "path": str(BASE / "reports" / "zhwiktionary-full-coverage.json")},
    {"label": "WikDict 60 词质量对照（抽取器回归基线）", "role": "校验用",
     "path": str(BASE / "reports" / "quality-sample-dump.json")},
    {"label": "zh.wiktionary 100 词 oldid 钉住清单（已入 Git 工具目录）", "role": "版本指纹",
     "path": str(pinned_tsv)},
]
fingerprint_rows = [
    {**item, "byte_size": Path(item["path"]).stat().st_size,
     "sha256": sha256_file(Path(item["path"]))}
    for item in originals
]
inner = [
    {"label": f"wikdict-en-zh/{name}", "role": "WikDict 包内文件",
     "byte_size": len(payload), "sha256": sha256_bytes(payload)}
    for name, payload in (("stardict.ifo", wik_ifo), ("stardict.idx", wik_idx),
                          ("stardict.dict", wik_dict), ("stardict.syn", wik_syn))
]

freq_values = [r["freq"] for r in slice_rows]
bands = Counter(
    "high(>=1000)" if r["freq"] >= 1000 else ("mid(100-999)" if r["freq"] >= 100 else "low(<100)")
    for r in slice_rows
)
population_bands = Counter(
    "high(>=1000)" if int(row["词频"]) >= 1000
    else ("mid(100-999)" if int(row["词频"]) >= 100 else "low(<100)")
    for row in netem_rows
)

summary = {
    "slice": {
        "rule": f"random.Random({SEED}).sample(sorted(rule_b_keys), {SLICE_SIZE})，"
                "按 NETEM 原始排序（词频降序）写回 primary 文件",
        "netem_rows": len(netem_rows),
        "rule_b_unique": len(universe),
        "slice_size": len(slice_rows),
        "bands": dict(bands),
        "population_bands": dict(population_bands),
        "freq_min": min(freq_values), "freq_max": max(freq_values),
        "freq_median": sorted(freq_values)[len(freq_values) // 2],
        "rule_a_differs": [r["word"] for r in slice_rows if r["key_a"] != r["key_b"]],
    },
    "zh": {
        "hit_defs": sum(1 for r in slice_rows if r["zh_defs"]),
        "hit_ipa": sum(1 for r in slice_rows if r["zh_ipa"]),
        "page_exists_no_defs": sum(
            1 for r in slice_rows if r["zh_exists"] and not r["zh_defs"]),
    },
    "wikdict": {
        "hit": sum(1 for r in slice_rows if r["wik_entries"]),
        "entries_total": len(wik_entries),
        "entries_with_han": sum(1 for _h, _o, body in wik_entries if HAN.search(body)),
    },
    "combination": {
        "either": sum(1 for r in slice_rows if r["zh_defs"] or r["wik_entries"]),
        "neither": sum(1 for r in slice_rows if not r["zh_defs"] and not r["wik_entries"]),
    },
    "extractor_check": {
        "words_checked": checked, "agree": agree, "mismatch": mismatch,
    },
    "pin_cross_check": {
        "tsv": str(pinned_tsv), "checked": cross_checked, "agree": cross_agree,
        "mismatch": cross_mismatch,
    },
    "duplicate_primary_keys": {
        key: [str(row["单词"]) + f"(#{row['序号']})" for row in group]
        for key, group in duplicate_keys.items()
    },
    "extras_outside_slice": {
        key: coverage[key].get("word") for key in extra_keys
    },
    "demo20": {
        "words": [r["word"] for r in demo_rows],
        "words_needing_select": sum(1 for n in demo_notes if n["machine_drafted_select"]),
        "detail": demo_notes,
    },
}

(NOTES / "03-fingerprints.json").write_text(
    json.dumps({
        "frequency_disclaimer": FREQ_DISCLAIMER,
        "originals": fingerprint_rows,
        "original_inner_files": inner,
        "converted": converted,
        "summary": summary,
    }, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print("package built")
