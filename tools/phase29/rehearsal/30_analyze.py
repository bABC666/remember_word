"""Turn the rehearsal plan artifacts into a human-adjudication worklist and a summary.

Reads only files under <PKG>; writes notes/05-worklist.tsv, notes/06-analysis.md and
notes/06-analysis.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir",
                        default=r"C:\Temp\dsh-bSBXrH\kaoyan-vocab-research\rehearsal")
    return parser.parse_args()


PKG = Path(parse_args().package_dir)
NOTES = PKG / "notes"
OUT = PKG / "out"

plan_r1 = json.loads((OUT / "plan-r1-empty.json").read_text(encoding="utf-8"))
plan_r2 = json.loads((OUT / "plan-r2-gloss.json").read_text(encoding="utf-8"))
plan_demo = json.loads((OUT / "plan-demo20.json").read_text(encoding="utf-8"))
preview_r1 = json.loads((OUT / "preview-r1.json").read_text(encoding="utf-8"))
fingerprints = json.loads((NOTES / "03-fingerprints.json").read_text(encoding="utf-8"))

pins = {}
with (NOTES / "per-word-pins.tsv").open(encoding="utf-8", newline="") as handle:
    for row in csv.DictReader(handle, delimiter="\t"):
        pins[row["key_b"]] = row

ZH_ID = "zhwiktionary-pinned-oldid"
WIK_ID = "wikdict-en-zh-2026-06-23"
NETEM_ID = "netem-5530-vocab-2026-09-24"

CONFLICT_LIMIT = 6  # characters kept per candidate value in the TSV


def wilson(successes: int, total: int, z: float = 1.959963985) -> tuple[float, float]:
    """95% Wilson interval: honest about what n=300 can and cannot support."""
    if total == 0:
        return (0.0, 0.0)
    phat = successes / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    spread = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denominator
    return (centre - spread, centre + spread)


def sources_of(entry: dict, field: str) -> set[str]:
    return {
        item["source_id"] for item in entry["default_evidence"].get(field) or []
    }


def truncate(text: str) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= CONFLICT_LIMIT else flat[:CONFLICT_LIMIT] + "…"


entries = plan_r1["entries"]
blocked = [e for e in entries if e["status"] == "blocked"]
ready = [e for e in entries if e["status"] == "ready"]

reason_counts = Counter(
    reason for entry in blocked for reason in entry["block_reasons"]
)
missing_meaning = [
    e for e in blocked if "missing_required_field:meaning" in e["block_reasons"]
]
conflict_meaning = [
    e for e in blocked if "undecided_conflict:meaning" in e["block_reasons"]
]
both = [e for e in blocked if e in missing_meaning and e in conflict_meaning]

ready_by_sources = Counter()
for entry in ready:
    kinds = sources_of(entry, "meaning")
    if kinds == {ZH_ID}:
        ready_by_sources["仅 zh.wiktionary"] += 1
    elif kinds == {WIK_ID}:
        ready_by_sources["仅 WikDict"] += 1
    elif kinds == {ZH_ID, WIK_ID}:
        ready_by_sources["zh.wiktionary 与 WikDict 取值一致（双方证据都被引用）"] += 1
    elif not kinds:
        ready_by_sources["无释义证据（不可能：required field 会阻断）"] += 1
    else:
        ready_by_sources["/".join(sorted(kinds))] += 1

# ---- cross-checks against the importer's own rules -------------------------------------
primary_preview = next(
    item for item in preview_r1["sources"] if item["source_id"] == NETEM_ID
)["preview"]
primary_keys = [row["normalized_word"] for row in primary_preview["rows"]]
plan_keys = [entry["normalized_word"] for entry in entries]
checks = {
    "preview_candidate_words": preview_r1["summary"]["candidate_words"],
    "plan_entries": len(entries),
    "plan_keys_equal_primary_rule_b_keys": plan_keys == primary_keys,
    "primary_rows": primary_preview["summary"]["total_rows"],
    "primary_duplicate_rows": primary_preview["summary"]["duplicate_rows"],
    "primary_error_rows": primary_preview["summary"]["error_rows"],
    "unmatched_supplement_words": len(plan_r1["unmatched_supplement_words"]),
    "unmatched_is_the_ten_extras": plan_r1["unmatched_supplement_words"]
    == sorted(fingerprints["summary"]["extras_outside_slice"]),
    "normalization": plan_r1["rule_versions"]["normalization"],
    "case_fold_collisions_in_slice": [
        key for key in ("may", "march") if key in plan_keys
    ],
    "demo20_ready_entries": plan_demo["summary"]["ready_entries"],
    "demo20_blockers": plan_demo["confirmation_blockers"],
    "r2_blocked_entries": plan_r2["summary"]["blocked_entries"],
    "r1_blocked_entries": plan_r1["summary"]["blocked_entries"],
}
checks["r2_minus_r1_blocked"] = (
    checks["r2_blocked_entries"] - checks["r1_blocked_entries"]
)
checks["r1_plan_sha256"] = plan_r1["plan_sha256"]
checks["r1_joint_report_sha256"] = plan_r1["joint_report_sha256"]
checks["demo20_plan_sha256"] = plan_demo["plan_sha256"]

# ---- source_raw evidence: what actually travels into the entry ------------------------
sample_ready = next(e for e in ready if e["default_snapshot"]["source_meanings"])
raw_columns_r1 = sample_ready["default_snapshot"]["source_raw"].split(",")
r1_has_gloss = any("netem_gloss" in column for column in raw_columns_r1)
r2_entry = next(
    e for e in plan_r2["entries"] if e["normalized_word"] == sample_ready["normalized_word"]
)
raw_columns_r2 = r2_entry["default_snapshot"]["source_raw"].split(",")
checks["r1_source_raw_example"] = {
    "word": sample_ready["normalized_word"],
    "source_raw": sample_ready["default_snapshot"]["source_raw"],
    "columns": raw_columns_r1,
    "contains_netem_gloss_column": r1_has_gloss,
}
checks["r2_source_raw_example"] = {
    "word": r2_entry["normalized_word"],
    "columns": raw_columns_r2,
    "contains_netem_gloss_column": any("netem_gloss" in c for c in raw_columns_r2),
    "default_source_meanings": r2_entry["default_snapshot"]["source_meanings"],
}

# R2 danger count: entries the plan would call ready whose only meaning is NETEM's
# rights-unclear column -- auto-defaulted, not adjudicated.
r2_netem_only = [
    e for e in plan_r2["entries"]
    if e["status"] == "ready"
    and {item["source_id"] for item in e["default_evidence"]["meaning"]} == {NETEM_ID}
]
r2_including_netem = [
    e for e in plan_r2["entries"]
    if e["status"] == "ready"
    and NETEM_ID in {item["source_id"] for item in e["default_evidence"]["meaning"]}
]
checks["r2_ready_meaning_from_netem_only"] = len(r2_netem_only)
checks["r2_ready_meaning_including_netem"] = len(r2_including_netem)
checks["r2_netem_only_examples"] = [e["normalized_word"] for e in r2_netem_only[:12]]

# Named examples the earlier quality pass flagged, so the rehearsal and the report line up.
probe_words = ["capacity", "denounce", "vocal", "information", "even", "job", "study",
               "capital", "delicate", "management"]
named = []
for word in probe_words:
    entry = next((e for e in entries if e["normalized_word"] == word), None)
    if entry is None:
        named.append({"word": word, "in_slice": False})
        continue
    meaning = [item for item in entry["evidence"]["meaning"]]
    named.append({
        "word": word,
        "in_slice": True,
        "status": entry["status"],
        "block_reasons": entry["block_reasons"],
        "evidence": [
            {"source_id": item["source_id"], "line": item["line"],
             "raw_value": item["raw_value"][:80]}
            for item in meaning
        ],
        "default_source_meanings": entry["default_snapshot"]["source_meanings"]
        if entry["default_snapshot"] else None,
    })
checks["named_examples"] = named

missing_words = [
    {
        "word": e["normalized_word"],
        "netem_rank": pins[e["normalized_word"]]["netem_rank"],
        "freq_claimed": pins[e["normalized_word"]]["freq_claimed_mixed_exam_corpus"],
        "zh_exists": pins[e["normalized_word"]]["zh_exists"],
        "wikdict_headword": pins[e["normalized_word"]]["wikdict_headword"],
    }
    for e in missing_meaning
]
checks["missing_meaning_words"] = sorted(
    missing_words, key=lambda item: int(item["freq_claimed"]), reverse=True)

# ---- frequency band breakdown (the slice is a plain random sample) --------------------
def band(freq: int) -> str:
    return "high(>=1000)" if freq >= 1000 else ("mid(100-999)" if freq >= 100 else "low(<100)")


band_totals: Counter[str] = Counter()
band_ready: Counter[str] = Counter()
band_conflict: Counter[str] = Counter()
band_missing: Counter[str] = Counter()
for entry in entries:
    row = pins[entry["normalized_word"]]
    key = band(int(row["freq_claimed_mixed_exam_corpus"]))
    band_totals[key] += 1
    if entry["status"] == "ready":
        band_ready[key] += 1
    if "undecided_conflict:meaning" in entry["block_reasons"]:
        band_conflict[key] += 1
    if "missing_required_field:meaning" in entry["block_reasons"]:
        band_missing[key] += 1

population = fingerprints["summary"]["slice"]["population_bands"]
population_total = sum(population.values())
band_rows = []
for key in ("high(>=1000)", "mid(100-999)", "low(<100)"):
    total = band_totals[key]
    pop_share = population.get(key, 0) / population_total
    band_rows.append({
        "band": key,
        "slice_words": total,
        "population_words": population.get(key, 0),
        "population_share": round(pop_share, 4),
        "ready": band_ready[key],
        "ready_rate": round(band_ready[key] / total, 4) if total else None,
        "conflict": band_conflict[key],
        "missing_meaning": band_missing[key],
    })
weighted_ready = sum(
    row["population_share"] * (row["ready_rate"] or 0.0) for row in band_rows
)

# ---- size extrapolation: how close is a full-library plan to the hard caps? ----------
def compact_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


plan_bytes = compact_size(plan_r1)
report_bytes = compact_size(preview_r1)
per_entry = plan_bytes / len(entries)
library = fingerprints["summary"]["slice"]["rule_b_unique"]
size_projection = {
    "plan_bytes_slice": plan_bytes,
    "plan_bytes_per_entry": round(per_entry, 1),
    "plan_bytes_full_library_estimate": int(per_entry * library),
    "plan_cap_bytes": 32 * 1024 * 1024,
    "joint_report_bytes_slice": report_bytes,
    "joint_report_bytes_full_library_estimate": int(report_bytes / len(entries) * library),
    "joint_report_cap_bytes": 32 * 1024 * 1024,
    "file_names": [block["file"]["name"] for block in plan_r1["sources"]],
    "file_byte_sizes": [block["file"]["byte_size"] for block in plan_r1["sources"]],
    "file_row_counts": [
        block["summary"]["total_rows"] for block in plan_r1["sources"]
    ],
    "file_cap_bytes": 8 * 1024 * 1024,
    "row_cap": 20_000,
}

# ---- conflict shape: how much of the "conflict" is granularity, not disagreement ------
def sense_set(value: str) -> set[str]:
    return {part.strip() for part in re.split(r"[；;]", value) if part.strip()}


conflict_shapes: Counter[str] = Counter()
conflict_examples: dict[str, list[str]] = defaultdict(list)
for entry in conflict_meaning:
    meaning = next(c for c in entry["conflicts"] if c["field"] == "meaning")
    values = meaning["raw_values"]
    if len(values) != 2:
        conflict_shapes[f"取值数={len(values)}"] += 1
        continue
    first, second = sense_set(values[0]), sense_set(values[1])
    if first == second:
        shape = "相同（不应出现）"
    elif first < second:
        shape = "WikDict 义项是 zh 义项的真子集（粒度差异）"
    elif second < first:
        shape = "zh 义项是 WikDict 义项的真子集（粒度差异）"
    elif first & second:
        shape = "部分重叠（既有相同义项也各有独有义项）"
    else:
        shape = "完全无重叠（真正不同的释义）"
    conflict_shapes[shape] += 1
    if len(conflict_examples[shape]) < 5:
        conflict_examples[shape].append(
            f"{entry['normalized_word']}: " + " || ".join(truncate(v) for v in values)
        )

# ---- worklist ------------------------------------------------------------------------
worklist_rows = []
for entry in entries:
    row = pins[entry["normalized_word"]]
    conflicts = {conflict["field"]: conflict for conflict in entry["conflicts"]}
    meaning = conflicts.get("meaning")
    zh_evidence = [
        item for item in entry["evidence"]["meaning"]
        if item["source_id"] == ZH_ID and item["raw_value"].strip()
    ]
    wik_evidence = [
        item for item in entry["evidence"]["meaning"]
        if item["source_id"] == WIK_ID and item["raw_value"].strip()
    ]
    candidates = [
        f"{item['source_id']}#{item['line']}={truncate(item['raw_value'])}"
        for item in entry["evidence"]["meaning"] if item["raw_value"].strip()
    ]
    if entry["status"] == "ready":
        supplied = sources_of(entry, "meaning")
        if supplied == {WIK_ID}:
            action = "自动默认（仅 WikDict，无第二来源交叉）→ 建议人工抽检"
        elif supplied == {ZH_ID, WIK_ID}:
            action = "自动默认（两源一致，已引用双方证据）→ 建议抽检"
        elif supplied == {ZH_ID}:
            action = "自动默认（仅 zh.wiktionary）→ 建议抽检"
        else:
            action = "自动默认"
    elif meaning:
        action = "人工 select（在下列候选值中逐词裁定）"
    elif "missing_required_field:meaning" in entry["block_reasons"]:
        action = "无任何许可明确来源的释义：补来源、购买授权或 exclude_word（no_default 会继续阻断）"
    else:
        action = "人工裁定"
    worklist_rows.append({
        "normalized_word": entry["normalized_word"],
        "netem_surface": row["word"],
        "netem_rank": row["netem_rank"],
        "freq_claimed": row["freq_claimed_mixed_exam_corpus"],
        "band": band(int(row["freq_claimed_mixed_exam_corpus"])),
        "plan_status": entry["status"],
        "block_reasons": "|".join(entry["block_reasons"]),
        "zh_oldid": row["zh_oldid"],
        "zh_defs_n": row["zh_defs_n"],
        "zh_meaning_evidence_n": len(zh_evidence),
        "zh_value": truncate(zh_evidence[0]["raw_value"]) if zh_evidence else "",
        "zh_ipa": row["zh_ipa"],
        "wikdict_headword": row["wikdict_headword"],
        "wikdict_defs_n": row["wikdict_defs_n"],
        "wik_meaning_evidence_n": len(wik_evidence),
        "wik_value": truncate(wik_evidence[0]["raw_value"]) if wik_evidence else "",
        "conflict_candidates": " || ".join(candidates),
        "human_action": action,
    })

with (NOTES / "05-worklist.tsv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(worklist_rows[0]), delimiter="\t",
                            lineterminator="\n")
    writer.writeheader()
    writer.writerows(worklist_rows)

# ---- report --------------------------------------------------------------------------
blocked_rate = len(blocked) / len(entries)
conflict_rate = len(conflict_meaning) / len(entries)
missing_rate = len(missing_meaning) / len(entries)
blocked_ci = wilson(len(blocked), len(entries))
conflict_ci = wilson(len(conflict_meaning), len(entries))
missing_ci = wilson(len(missing_meaning), len(entries))

summary = {
    "slice": fingerprints["summary"]["slice"],
    "plan_r1": {
        "summary": plan_r1["summary"],
        "confirmation_ready": plan_r1["confirmation_ready"],
        "confirmation_blockers": plan_r1["confirmation_blockers"],
        "block_reason_counts": dict(reason_counts),
        "blocked_rate": round(blocked_rate, 4),
        "blocked_rate_ci95": [round(value, 4) for value in blocked_ci],
        "conflict_rate": round(conflict_rate, 4),
        "conflict_rate_ci95": [round(value, 4) for value in conflict_ci],
        "missing_meaning_rate": round(missing_rate, 4),
        "missing_meaning_rate_ci95": [round(value, 4) for value in missing_ci],
        "ready_by_sources": dict(ready_by_sources),
        "conflict_shapes": dict(conflict_shapes),
        "conflict_examples": dict(conflict_examples),
    },
    "bands": band_rows,
    "weighted_ready_rate_estimate": round(weighted_ready, 4),
    "extrapolation": {
        "library_words": library,
        "blocked_estimate": round(blocked_rate * library),
        "blocked_estimate_ci95": [round(value * library) for value in blocked_ci],
        "conflict_estimate": round(conflict_rate * library),
        "missing_meaning_estimate": round(missing_rate * library),
        "note": "按比例外推，未按词频分档加权；只用于说明工作量量级。",
    },
    "size_projection": size_projection,
    "checks": checks,
}
(NOTES / "06-analysis.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

lines = ["# 预演分析（真实数据小规模切片）", ""]
lines.append("## 计划结论")
lines.append("")
lines.append(f"- 候选词条 **{len(entries)}**；`ready` **{len(ready)}**；`blocked` **{len(blocked)}**；"
             f"`excluded` 0；`confirmation_ready={plan_r1['confirmation_ready']}`")
lines.append(f"- 阻断原因计数：{json.dumps(dict(reason_counts), ensure_ascii=False)}")
lines.append(f"- 释义冲突（`undecided_conflict:meaning`）**{len(conflict_meaning)}**"
             f"（{conflict_rate:.1%}，95% Wilson 区间 {conflict_ci[0]:.1%}–{conflict_ci[1]:.1%}）")
lines.append(f"- 无任何来源释义（`missing_required_field:meaning`）**{len(missing_meaning)}**"
             f"（{missing_rate:.1%}，区间 {missing_ci[0]:.1%}–{missing_ci[1]:.1%}）")
lines.append(f"- 待人工裁定合计 **{len(blocked)}**（{blocked_rate:.1%}，区间 "
             f"{blocked_ci[0]:.1%}–{blocked_ci[1]:.1%}）")
lines.append("")
lines.append("`ready` 的释义证据来源分布（**未经人工核对，只说明没有冲突**）：")
lines.append("")
for key, value in ready_by_sources.most_common():
    lines.append(f"- {key}: {value}")
lines.append("")
lines.append("## 释义冲突的形态（按 `；` 拆义项后比较）")
lines.append("")
lines.append("| 形态 | 条数 | 例 |")
lines.append("|---|---|---|")
for shape, count in conflict_shapes.most_common():
    examples = "；".join(conflict_examples.get(shape, [])[:2])
    lines.append(f"| {shape} | {count} | {examples} |")
lines.append("")
lines.append("> 说明：这一列只用于**描述人工工作量的性质**；无论哪种形态，计划都**不会**自动取舍，"
             "必须由人工 `select`。若要减少粒度差异造成的裁定量，只能改**转换规则**（同词义项的合并且规则）"
             "或在设计文档里显式声明一条可复核的取舍规则，不能在导入时静默处理。")
lines.append("")
lines.append("## 按词频档（本切片是纯随机抽样，档位分布贴近总体）")
lines.append("")
lines.append("| 档位 | 总体占比 | 切片词数 | ready | ready 率 | 释义冲突 | 缺释义 |")
lines.append("|---|---|---|---|---|---|---|")
for row in band_rows:
    lines.append(
        f"| {row['band']} | {row['population_share']:.1%}（{row['population_words']}） | "
        f"{row['slice_words']} | {row['ready']} | "
        f"{(row['ready_rate'] or 0):.1%} | {row['conflict']} | {row['missing_meaning']} |")
lines.append("")
lines.append(f"- 按总体占比加权后的 ready 粗估 ≈ **{weighted_ready:.1%}**（`[推断·粗估]`）")
lines.append(f"- 按比例外推到全库 {library} 词：待裁定 ≈ **{summary['extrapolation']['blocked_estimate']}**"
             f"（区间 {summary['extrapolation']['blocked_estimate_ci95'][0]}–"
             f"{summary['extrapolation']['blocked_estimate_ci95'][1]}）；"
             f"其中释义冲突 ≈ {summary['extrapolation']['conflict_estimate']}、"
             f"完全缺释义 ≈ {summary['extrapolation']['missing_meaning_estimate']}（`[推断]`）")
lines.append("")
lines.append("## 契约自查")
lines.append("")
lines.append("```json")
lines.append(json.dumps(checks, ensure_ascii=False, indent=2))
lines.append("```")
lines.append("")
lines.append("## 规模与硬上限")
lines.append("")
lines.append("```json")
lines.append(json.dumps(size_projection, ensure_ascii=False, indent=2))
lines.append("```")
lines.append("")
lines.append("## R2 对照（把 NETEM 权属未明的释义映射为 `meaning` 候选）")
lines.append("")
lines.append(f"- R1 `blocked` {checks['r1_blocked_entries']} → R2 `blocked` "
             f"{checks['r2_blocked_entries']}（**+{checks['r2_minus_r1_blocked']}**）")
lines.append(f"- R1 `ready` {plan_r1['summary']['ready_entries']} → R2 "
             f"{plan_r2['summary']['ready_entries']}")
lines.append(f"- R2 里**只靠 NETEM 释义就被自动默认**的词条："
             f"**{checks['r2_ready_meaning_from_netem_only']}** 条"
             f"（例：{'、'.join(checks['r2_netem_only_examples'])}）")
lines.append("")
lines.append("## 缺释义词条（14 条，切片内）")
lines.append("")
lines.append("| 词 | NETEM 排名 | 来源自述词频 | zh 页面存在 | WikDict 词头 |")
lines.append("|---|---|---|---|---|")
for item in checks["missing_meaning_words"]:
    lines.append(f"| `{item['word']}` | {item['netem_rank']} | {item['freq_claimed']} | "
                 f"{item['zh_exists']} | {item['wikdict_headword'] or '—'} |")
lines.append("")
lines.append("## 与既有质量核查对齐的具名词条")
lines.append("")
lines.append("```json")
lines.append(json.dumps(checks["named_examples"], ensure_ascii=False, indent=2))
lines.append("```")
(NOTES / "06-analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

print(json.dumps({
    "entries": len(entries), "ready": len(ready), "blocked": len(blocked),
    "conflict": len(conflict_meaning), "missing": len(missing_meaning),
    "reason_counts": dict(reason_counts),
    "ready_by_sources": dict(ready_by_sources),
    "checks": checks,
}, ensure_ascii=False, indent=2))
