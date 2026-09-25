"""Phase 2.9 pilot: build a per-word adjudication sheet for 100-300 kaoyan words.

Continues directly from the 300-word read-only rehearsal
(`docs/V1.2-PHASE2.9-REAL-DATA-REHEARSAL-SLICE-RECORD.md`). The pilot's job is not to
import anything: it produces

  * `pilot-sheet.tsv`  -- one row per word: the *verbatim* original value of every
    candidate source, that source's version fingerprint and licence evidence, the
    mechanical pre-screen flags, and **empty human-decision columns**;
  * `pilot-sheet.md`   -- the same sheet in a form a person can fill in by hand;
  * `pilot-stats.json` -- problem statistics over the pilot set;
  * `refetch-trace.tsv` (with `--refetch`) -- re-fetch of the *pinned* zh.wiktionary
    revisions for the words our cleaner produced nothing for, so "no definition" can
    be told apart from "our cleaner missed it".

Nothing here writes to a database, and NETEM's rights-unclear `释义` column is never
read: `rehearsal/sources/netem-words.csv` has no such column at all.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

ZH_ID = "zhwiktionary-pinned-oldid"
WIK_ID = "wikdict-en-zh-2026-06-23"
NETEM_ID = "netem-5530-vocab-2026-09-24"
PILOT_SEED = 20260924
CONFLICT_SAMPLE = 20

#: Traditional-character probe set reused verbatim from the research scripts that produced
#: the report's "19% 含繁体字形" figure (reports/v7_rubric.py, reports/v5_rubric.py).
#: It is a bounded probe set, so a hit means "worth a human look", not "is traditional".
TRAD_PROBE = set("問題精緻妥協網頁兒童學習國語詞彙釋義關閉發佈發生兒子快樂無聲發現種類應該經歷")

LICENSE_ZH = "CC BY-SA 4.0 + GFDL（zh.wiktionary 项目页「Wiktionary:版权信息」原文；逐词 oldid 固定）"
LICENSE_WIK = "CC BY-SA 4.0（包内 stardict.ifo description 原文，date=2026-06-23）"
LICENSE_NETEM = "CC BY-NC-SA 4.0（仓库 README/LICENSE；仅词表与顺序，不含释义列）"
FREQ_LABEL = "来源自述的混合考试语料计数（四六级+考研+专四专八约 200 套、词形还原、未区分英一/英二）"

VERDICT_VOCAB = (
    "采用-zh", "采用-wikdict", "采用-多源", "修订", "弃用该词", "待补来源",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir",
                        default=r"C:\Temp\dsh-bSBXrH\kaoyan-vocab-research\rehearsal")
    parser.add_argument("--pilot-dir", default=None,
                        help="默认 <package-dir>/pilot")
    parser.add_argument("--repo", default=None,
                        help="工作区（只读：导入已入库的清洗规则做复算）；默认取本脚本所在的仓库")
    parser.add_argument("--refetch", action="store_true",
                        help="按 oldid 重取 zh.wiktionary 修订并复算（需要网络）")
    parser.add_argument("--from-dump", action="store_true",
                        help="不联网：用 pilot/zh-pinned-wikitext.json 里已保存的修订复算")
    parser.add_argument("--sleep", type=float, default=4.0)
    return parser.parse_args()


ARGS = parse_args()
PKG = Path(ARGS.package_dir)
PILOT = Path(ARGS.pilot_dir) if ARGS.pilot_dir else PKG / "pilot"
# <repo>/tools/phase29/rehearsal/pilot_select.py -> parents[3] is the checkout root
REPO = Path(ARGS.repo) if ARGS.repo else Path(__file__).resolve().parents[3]
OUT = PKG / "out"
NOTES = PKG / "notes"
SRC = PKG / "sources"

plan = json.loads((OUT / "plan-r1-empty.json").read_text(encoding="utf-8"))
fingerprints = json.loads((NOTES / "03-fingerprints.json").read_text(encoding="utf-8"))

def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


zh_csv = read_csv_rows(SRC / "zhwiktionary.csv")
wik_csv = read_csv_rows(SRC / "wikdict.csv")
netem_csv = read_csv_rows(SRC / "netem-words.csv")
zh_sha = file_sha256(SRC / "zhwiktionary.csv")
wik_sha = file_sha256(SRC / "wikdict.csv")
netem_sha = file_sha256(SRC / "netem-words.csv")

pins: dict[str, dict[str, str]] = {}
with (NOTES / "per-word-pins.tsv").open(encoding="utf-8", newline="") as handle:
    for row in csv.DictReader(handle, delimiter="\t"):
        pins[row["key_b"]] = row

# CSV line number -> row, so a recorded locator resolves to the exact source bytes.
zh_by_line = {index + 2: row for index, row in enumerate(zh_csv)}
wik_by_line = {index + 2: row for index, row in enumerate(wik_csv)}
netem_by_line = {index + 2: row for index, row in enumerate(netem_csv)}


def evidence(entry: dict, source_id: str, field: str) -> tuple[int, str] | None:
    for item in entry["evidence"][field]:
        if item["source_id"] == source_id and item["raw_value"].strip():
            return item["line"], item["raw_value"]
    return None


def senses(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[；;]", value) if part.strip()]


def band_of(freq: int) -> str:
    return ("high(>=1000)" if freq >= 1000
            else ("mid(100-999)" if freq >= 100 else "low(<100)"))


def relation(left: str, right: str) -> str:
    a, b = set(senses(left)), set(senses(right))
    if not a or not b:
        return "无法比较"
    if a == b:
        return "两侧义项集相同"
    if a < b:
        return "左 ⊂ 右（真子集）"
    if b < a:
        return "右 ⊂ 左（真子集）"
    if a & b:
        return "部分重叠"
    return "字符串义项集无重叠"


# --------------------------------------------------------------------------- selection
entries = {entry["normalized_word"]: entry for entry in plan["entries"]}
ready = [entry for entry in plan["entries"] if entry["status"] == "ready"]
blocked = [entry for entry in plan["entries"] if entry["status"] == "blocked"]


def supplied(entry: dict) -> set[str]:
    return {item["source_id"] for item in entry["default_evidence"]["meaning"]}


group_a = [e for e in ready if supplied(e) == {WIK_ID}]
group_b = [e for e in ready if supplied(e) == {ZH_ID}]
group_c = [e for e in ready if supplied(e) == {ZH_ID, WIK_ID}]
conflicts = [e for e in blocked if "undecided_conflict:meaning" in e["block_reasons"]]
missing = [e for e in blocked if "missing_required_field:meaning" in e["block_reasons"]]
group_d = sorted(
    random.Random(PILOT_SEED).sample(sorted(conflicts, key=lambda e: e["normalized_word"]),
                                     CONFLICT_SAMPLE),
    key=lambda e: e["sequence"],
)
group_e = sorted(missing, key=lambda e: e["sequence"])

groups = [
    ("A", "ready·仅 WikDict 单来源（全量纳入）", group_a),
    ("B", "ready·仅 zh.wiktionary 单来源（全量纳入）", group_b),
    ("C", "ready·两源一致（全量纳入）", group_c),
    ("D", f"blocked·释义冲突（{CONFLICT_SAMPLE}/156 确定性抽样）", group_d),
    ("E", "blocked·完全无释义（全量纳入）", group_e),
]

# ------------------------------------------------------------------- per-row assembly
rows: list[dict[str, object]] = []
for tag, label, members in groups:
    for entry in members:
        word = entry["normalized_word"]
        pin = pins[word]
        zh_hit = evidence(entry, ZH_ID, "meaning")
        wik_hit = evidence(entry, WIK_ID, "meaning")
        zh_line, zh_value = zh_hit if zh_hit else (None, "")
        wik_line, wik_value = wik_hit if wik_hit else (None, "")
        # provenance: the value in the sheet must be the byte-identical cell of the
        # fingerprinted source file, not a copy from the plan.
        zh_csv_value = (zh_by_line.get(zh_line, {}) or {}).get("zh_meaning", "")
        wik_csv_value = (wik_by_line.get(wik_line, {}) or {}).get("wikdict_meaning", "")
        wik_headword = (wik_by_line.get(wik_line, {}) or {}).get("wikdict_headword", "")

        flags: list[str] = []
        if zh_value and wik_value:
            flags.append(f"两侧关系：{relation(wik_value, zh_value)}")
        if tag == "A":
            flags.append("无第二来源可交叉校验")
        if tag in ("A", "B"):
            flags.append("单一来源")
        if tag == "C":
            flags.append("两源取值一致（已互证）")
        if tag == "E":
            flags.append("两个许可明确来源都没有释义")
        if pin["zh_exists"] == "yes" and not zh_value:
            flags.append("zh 页面存在但清洗后无中文释义（可能是清洗漏抽，需复核原 wikitext）")
        if wik_headword and wik_headword != str(pin["word"]):
            flags.append(f"WikDict 命中词头与词条拼写不同：{wik_headword}")
        if "|" in wik_headword:
            flags.append("WikDict 同键合并了多个同形词条（词头以 | 分隔）")
        if wik_value and len(senses(wik_value)) == 1:
            flags.append("WikDict 只给 1 个义项")
        if any(ch in TRAD_PROBE for ch in zh_value + wik_value):
            flags.append("粗测集命中繁体字形（需人工确认是否统一为简体并注明已修改）")
        if re.search(r"[〈（(][^）)〉]{1,8}[）)〉]", zh_value + wik_value):
            flags.append("含括号/域标签，需人工确认保留或删除")

        if tag == "D":
            suggestion = "不预选：机器不在两个来源之间取舍，必须人工裁定"
            suggested_source = ""
            suggested_value = ""
        elif tag == "E":
            suggestion = "无可用释义：补授权来源、购买许可，或记录弃用"
            suggested_source = ""
            suggested_value = ""
        elif tag == "C":
            suggestion = "建议多选（计划会同时引用双方证据），仍需人工确认义项是否覆盖最常见义"
            suggested_source = f"{ZH_ID} + {WIK_ID}"
            suggested_value = zh_value or wik_value
        elif tag == "B":
            suggestion = "建议采用 zh.wiktionary 原值（逐字）；如有繁体/标签问题按修订流程处理"
            suggested_source = ZH_ID
            suggested_value = zh_value
        else:
            suggestion = "建议采用 WikDict 原值，但必须人工确认义项是否覆盖最常见义"
            suggested_source = WIK_ID
            suggested_value = wik_value

        rows.append({
            "pilot_id": "",
            "group": tag,
            "group_label": label,
            "normalized_word": word,
            "netem_surface": pin["word"],
            "key_a_rule": pin["key_a"],
            "netem_rank": pin["netem_rank"],
            "freq_claimed_mixed_exam_corpus": pin["freq_claimed_mixed_exam_corpus"],
            "freq_label": FREQ_LABEL,
            "band": band_of(int(pin["freq_claimed_mixed_exam_corpus"])),
            "plan_status": entry["status"],
            "plan_block_reasons": "|".join(entry["block_reasons"]),
            "zh_license": LICENSE_ZH,
            "zh_version": f"oldid={pin['zh_oldid'] or '—'}；页面存在={pin['zh_exists']}",
            "zh_source_file": "sources/zhwiktionary.csv",
            "zh_source_line": zh_line or "",
            "zh_meaning_original": zh_csv_value,
            "zh_defs_n": pin["zh_defs_n"],
            "zh_ipa": pin["zh_ipa"],
            "wikdict_license": LICENSE_WIK,
            "wikdict_version": "wikdict-en-zh.zip sha256=62d6d4a8ccf28c28bbe4ec82ac65fa67fd52c0f34d71294ab1d9d95fa3233830；"
                               "stardict.ifo date=2026-06-23, wordcount=27250",
            "wikdict_source_file": "sources/wikdict.csv",
            "wikdict_source_line": wik_line or "",
            "wikdict_meaning_original": wik_csv_value,
            "wikdict_headword": wik_headword,
            "wikdict_entry_index": (wik_by_line.get(wik_line, {}) or {}).get("wikdict_entry_index", ""),
            "netem_list_license": LICENSE_NETEM,
            "netem_list_version": "netem_full_list.json sha256=6d71a301321056291902bc4804e223c6926dca0d629a45076a6ca5adab185f62",
            "netem_source_file": "sources/netem-words.csv",
            "netem_source_line": "",
            "provenance_ok": str(bool(
                (not zh_value or zh_csv_value == zh_value)
                and (not wik_value or wik_csv_value == wik_value))),
            "prescreen_flags": " ｜ ".join(flags) or "无机械标记",
            "machine_suggestion": suggestion,
            "machine_suggested_source": suggested_source,
            "machine_suggested_value": suggested_value,
            "human_verdict": "",
            "human_adopted_value": "",
            "human_modified": "",
            "human_reason": "",
            "human_open_question": "",
            "human_reviewer": "",
            "human_date": "",
        })

# the primary row's own line is the plan's membership locator
for row in rows:
    entry = entries[str(row["normalized_word"])]
    primary_line = int(entry["primary"]["line"])
    row["netem_source_line"] = primary_line
    primary_row = netem_by_line.get(primary_line, {})
    row["provenance_ok"] = str(
        row["provenance_ok"] == "True"
        and primary_row.get("word", "") == str(row["netem_surface"])
    )

for index, row in enumerate(rows, start=1):
    row["pilot_id"] = f"P{index:03d}"

group_ids = {
    tag: {row["pilot_id"] for row in rows if row["group"] == tag}
    for tag, _label, _members in groups
}

# ------------------------------------------------------------------- refetch (optional)
refetch_rows: list[dict[str, object]] = []
if ARGS.refetch or ARGS.from_dump:
    sys.path.insert(0, str(REPO / "tools" / "phase29"))
    from zhwiktionary_clean_measure import (
        EN_TITLES,
        HEAD,
        NOISE_RE,
        api,
        clean,
        norm_title,
        split_sections,
        strip_markup,
    )

    targets = [row for row in rows if not row["zh_meaning_original"]]
    dump_path = PILOT / "zh-pinned-wikitext.json"
    if ARGS.from_dump:
        texts = json.loads(dump_path.read_text(encoding="utf-8"))
    else:
        ids: dict[str, list[dict[str, object]]] = {}
        for row in targets:
            oldid = str(row["zh_version"]).split("oldid=")[1].split("；")[0]
            if oldid and oldid != "—":
                ids.setdefault(oldid, []).append(row)
        texts = {}
        ordered = sorted(ids)
        for start in range(0, len(ordered), 20):
            batch = ordered[start:start + 20]
            payload = api({"action": "query", "format": "json", "prop": "revisions",
                           "rvprop": "ids|content|timestamp", "rvslots": "main",
                           "revids": "|".join(batch)})
            for page in payload.get("query", {}).get("pages", {}).values():
                revision = (page.get("revisions") or [None])[0]
                if revision:
                    texts[str(revision["revid"])] = revision["slots"]["main"]["*"]
            print(f"fetched {min(start + 20, len(ordered))}/{len(ordered)} revisions",
                  flush=True)
            time.sleep(ARGS.sleep)
        # Keep the pinned revisions locally so later steps do not need the network again.
        dump_path.write_text(
            json.dumps({oldid: text for oldid, text in sorted(texts.items())},
                       ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def recovery_probe(text: str) -> dict[str, object]:
        """What could be extracted with *widened* rules that today's cleaner drops.

        Blind spots of the current rule, all visible in the pilot data:
          * definitions written as **plain lines** (no leading ``#``/``*``) — the current
            rule only reads list lines;
          * definitions in the **page lead**, before the first heading — ``split_sections``
            drops everything above the first heading (e.g. ``who``: ``===代詞=== 谁``);
          * definitions under a **non-English heading** (``==中文==``) — the current rule
            only reads ``==英語/英语/英文/English==`` and its sub-sections;
          * definitions wrapped in ``-{...}-`` conversion blocks — dropped wholesale;
          * definitions expressed only as a template (``{{alternative spelling of|...}}``).

        Every candidate is still source text and would have to be reviewed by a human.
        """
        sections = split_sections(text)
        en_index = next(
            (index for index, (level, title, _body) in enumerate(sections)
             if level == 2 and norm_title(title) in EN_TITLES),
            None,
        )

        def in_english_section(index: int) -> bool:
            """The English L2 section *and* the level>2 sections that follow it."""
            if en_index is None or index < en_index:
                return False
            if index == en_index:
                return True
            if sections[index][0] <= 2:
                return False
            return all(level > 2 for level, _title, _body in sections[en_index + 1:index + 1])

        lead: list[str] = []
        plain: list[str] = []
        elsewhere: list[str] = []
        other_titles: list[str] = []
        conversion: list[str] = []

        def keep(candidate: str) -> bool:
            return bool(candidate) and bool(re.search(r"[\u4e00-\u9fff]", candidate)) and not (
                NOISE_RE.search(candidate)
                and len(re.sub(r"[^\u4e00-\u9fff]", "", candidate)) <= 4
            )

        # Category/interwiki/file links survive markup stripping but are not glosses.
        NON_GLOSS = re.compile(
            r"^\[\[(?:Category|category|分類|分类|File|Image|w|Wikipedia|wikipedia|Wiktionary)"
        )
        # A bare part-of-speech label is a heading artifact, not a definition.
        POS_ONLY = re.compile(
            r"^[（(【\[]?(?:名詞|名词|動詞|动词|形容詞|形容词|副詞|副词|代詞|代词|介詞|介词|"
            r"連詞|连词|數詞|数词|冠詞|冠词|助動詞|助动词|感嘆詞|感叹词|縮寫|缩写|"
            r"前綴|前缀|後綴|后缀|詞組|词组|短語|短语)[）)】\]]?[；;、,，。]?$"
        )

        def harvest(raw: str, bucket: list[str]) -> None:
            if NON_GLOSS.match(raw):
                return
            if "-{" in raw or "}-" in raw:
                cleaned = strip_markup(re.sub(r"-\{|\}-", " ", raw))
                if keep(cleaned) and not POS_ONLY.match(cleaned):
                    conversion.append(cleaned)
                return
            cleaned = strip_markup(raw)
            if keep(cleaned) and not POS_ONLY.match(cleaned):
                bucket.append(cleaned)

        # page lead: everything above the first heading, which split_sections discards
        for line in text.splitlines():
            if HEAD.match(line.strip()):
                break
            harvest(line.strip(), lead)

        for index, (level, title, body) in enumerate(sections):
            is_en = in_english_section(index)
            for line in body.splitlines():
                raw = line.strip()
                if not raw:
                    continue
                if is_en:
                    harvest(raw, plain if raw[0] not in "#*" else [])
                elif raw[0] in "#*":
                    before = len(elsewhere)
                    harvest(raw, elsewhere)
                    if len(elsewhere) > before:
                        other_titles.append(str(title))

        lead_candidates = list(dict.fromkeys(lead))
        candidates = list(dict.fromkeys([*lead_candidates, *plain, *elsewhere, *conversion]))
        domain_tag = re.compile(r"\[(?:S:|正:|简:|繁:)?[^\]]{1,12}\]|［[^］]{1,12}］")
        untagged = [item for item in candidates if not domain_tag.search(item)]
        return {
            "probe_lead_defs": len(lead_candidates),
            "probe_plain_line_defs": len(dict.fromkeys(plain)),
            "probe_other_section_defs": len(dict.fromkeys(elsewhere)),
            "probe_conversion_block_defs": len(dict.fromkeys(conversion)),
            "probe_candidates_n": len(candidates),
            "probe_untagged_candidates_n": len(untagged),
            "probe_first_candidate": candidates[0][:60] if candidates else "",
            "probe_first_untagged": untagged[0][:60] if untagged else "",
            "probe_other_section_titles": "|".join(dict.fromkeys(other_titles))[:120],
            "page_has_cc_cedict": "yes" if "CC-CEDICT" in text else "no",
            "page_has_translation_blocks": "yes" if "翻譯-頂" in text else "no",
            "page_has_region_variant_marker": (
                "yes" if ("正體" in text or "繁體" in text) else "no"),
        }

    for row in targets:
        oldid = str(row["zh_version"]).split("oldid=")[1].split("；")[0]
        text = texts.get(oldid, "")
        defs, why = clean(text) if text else ([], "no_revision")
        redirect = re.match(r"^\s*#\s*(?:REDIRECT|重定向)\s*:?\s*\[\[([^\]|]+)",
                            text or "", re.IGNORECASE)
        en_heading = re.search(r"^(==+)\s*(\[\[)?(英語|英语|英文|English)", text or "",
                               re.MULTILINE)
        han_anywhere = bool(re.search(r"[\u4e00-\u9fff]", text or ""))
        hash_han = 0
        for line in (text or "").splitlines():
            stripped = line.strip()
            if (stripped.startswith("#")
                    and not stripped.startswith(("#*", "#:", "#;", "##"))
                    and re.search(r"[\u4e00-\u9fff]", stripped)):
                hash_han += 1
        probe = recovery_probe(text)
        if redirect:
            diag = "redirect"
        elif not han_anywhere:
            diag = "no_han_anywhere"
        elif int(probe["probe_candidates_n"]) > 0:
            diag = "recovered_by_widened_rule"
        else:
            diag = "han_present_but_not_extractable"
        refetch_rows.append({
            "pilot_id": row["pilot_id"],
            "word": row["normalized_word"],
            "oldid": oldid,
            "wikitext_bytes": len(text.encode("utf-8")),
            "wikitext_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "",
            "regap_defs_n": len(defs),
            "regap_result": "recovered" if defs else why,
            "is_redirect": "yes" if redirect else "no",
            "redirect_target": redirect.group(1).strip() if redirect else "",
            "english_heading_present": "yes" if en_heading else "no",
            "han_anywhere": "yes" if han_anywhere else "no",
            "hash_lines_with_han_anywhere": hash_han,
            "diag": diag,
            "head_160": " ".join((text or "")[:160].split()),
            "regap_joined": "；".join(defs),
            **probe,
        })

# --------------------------------------------------------------------------- outputs
PILOT.mkdir(parents=True, exist_ok=True)

if refetch_rows:
    with (PILOT / "refetch-trace.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(refetch_rows[0]), delimiter="\t",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(refetch_rows)
    by_id = {str(item["pilot_id"]): item for item in refetch_rows}
    for row in rows:
        item = by_id.get(str(row["pilot_id"]))
        if item:
            row["zh_regap_result"] = item["regap_result"]
            row["zh_regap_defs_n"] = item["regap_defs_n"]
            row["zh_regap_sha256"] = item["wikitext_sha256"]
            row["zh_regap_joined"] = item["regap_joined"]
            row["zh_regap_diag"] = item["diag"]
            row["zh_widened_rule_candidates"] = item["probe_candidates_n"]
            row["zh_widened_rule_untagged"] = item["probe_untagged_candidates_n"]
            row["zh_widened_rule_first"] = item["probe_first_candidate"]
            row["zh_widened_rule_first_untagged"] = item["probe_first_untagged"]
            if int(item["probe_candidates_n"]) > 0:
                detail = (f"zh.wiktionary 漏抽候选 {item['probe_candidates_n']} 条"
                          f"（其中无域/地区标签 {item['probe_untagged_candidates_n']} 条）"
                          f"，例：{item['probe_first_candidate']}")
                row["prescreen_flags"] = f"{row['prescreen_flags']} ｜ {detail}"
                if row["group"] in ("A", "E"):
                    row["machine_suggestion"] = (
                        f"{row['machine_suggestion']}；**另有 zh.wiktionary 漏抽候选**，"
                        "应先人工判断这些候选是否覆盖最常见义，再决定采用哪个来源"
                    )
            elif item["diag"] == "no_han_anywhere":
                row["prescreen_flags"] = (
                    f"{row['prescreen_flags']} ｜ 该修订页面上**完全没有汉字文本**"
                    "（确认来源缺口，不是清洗漏抽）"
                )
            elif item["diag"] == "han_present_but_not_extractable":
                row["prescreen_flags"] = (
                    f"{row['prescreen_flags']} ｜ 页面有汉字但扩大规则仍取不到候选"
                    "（需人工看原 wikitext 判断结构）"
                )

sheet_path = PILOT / "pilot-sheet.tsv"
with sheet_path.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t",
                            lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)

markdown = [
    "# 考研词试点逐词裁定清单（100–300 词）",
    "",
    "本清单是**裁定记录表**，不是导入结果。每个词一行：候选来源的**原值（逐字）**、来源版本指纹、",
    "许可证据、机器预筛标记、机器建议，以及**留空的人工决定栏**（`human_*`）。",
    "",
    "裁定词表（`human_verdict` 只能填以下之一）：" + "、".join(f"`{v}`" for v in VERDICT_VOCAB),
    "",
    "- `采用-zh` / `采用-wikdict` / `采用-多源`：采用该来源**原值逐字不改**；多源表示同时引用多个来源的证据。",
    "- `修订`：人工改写文本 → 必须同时填 `human_modified=是`，因为 CC BY-SA 义务要求**注明是否修改**。",
    "- `弃用该词`：本次试点不收录（计划里对应 `exclude_word`，需写明理由）。",
    "- `待补来源`：等授权/购买后再定。",
    "",
    f"- 词频列 `freq_claimed_mixed_exam_corpus` 的口径：**{FREQ_LABEL}**，",
    "  不得称为「考研真题词频」。",
    "- NETEM 的 `释义` 列权属未明，**本清单不含该列**，也不得把它作为默认选择。",
    "",
]
for tag, label, members in groups:
    markdown.append(f"## {tag}·{label}（{len(members)} 词）")
    markdown.append("")
    markdown.append("| id | 词 | 词频* | zh.wiktionary（oldid / 原值） | WikDict（词头 / 原值） | 机器预筛 | 机器建议 | 人工决定 |")
    markdown.append("|---|---|---|---|---|---|---|---|")
    for row in rows:
        if row["group"] != tag:
            continue
        markdown.append(
            f"| {row['pilot_id']} | `{row['normalized_word']}` | "
            f"{row['freq_claimed_mixed_exam_corpus']} | "
            f"{(str(row['zh_version']).replace('oldid=', '') or '—')}"
            f"{'：' + str(row['zh_meaning_original']) if row['zh_meaning_original'] else '：—'} | "
            f"{row['wikdict_headword'] or '—'}"
            f"{'：' + str(row['wikdict_meaning_original']) if row['wikdict_meaning_original'] else '：—'} | "
            f"{row['prescreen_flags']} | {row['machine_suggestion']} | （待填） |")
    markdown.append("")
markdown.append("\\* 词频为来源自述的混合考试语料计数，不是考研真题词频。")
(PILOT / "pilot-sheet.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")

# ------------------------------------------------------------------------- statistics
def flag_counter(predicate) -> int:
    return sum(1 for row in rows if predicate(row))


regap_recovered = flag_counter(lambda r: r.get("zh_regap_result") == "recovered")
stats = {
    "pilot_words": len(rows),
    "selection": {
        "rule": f"从 300 词预演切片（seed {PILOT_SEED}）里按分组取词："
                "A/B/C/E 全量纳入，D 用 random.Random(seed) 从 156 条冲突里抽 20 条",
        "groups": {tag: len(members) for tag, _label, members in groups},
        "conflict_pool": len(conflicts),
        "missing_pool": len(missing),
    },
    "source_row_integrity": {
        "rows": len(rows),
        "provenance_ok": flag_counter(lambda r: r["provenance_ok"] == "True"),
        "zhwiktionary_csv_sha256": zh_sha,
        "wikdict_csv_sha256": wik_sha,
        "netem_csv_sha256": netem_sha,
    },
    "problem_flags": {
        "no_second_source": flag_counter(lambda r: r["group"] in ("A", "B")),
        "wikdict_only": flag_counter(lambda r: r["group"] == "A"),
        "wikdict_single_gloss": flag_counter(lambda r: "WikDict 只给 1 个义项" in str(r["prescreen_flags"])),
        "wikdict_cross_lexeme_headword": flag_counter(
            lambda r: "WikDict 命中词头与词条拼写不同" in str(r["prescreen_flags"])),
        "wikdict_multi_entry_merge": flag_counter(
            lambda r: "同键合并了多个同形词条" in str(r["prescreen_flags"])),
        "zh_page_exists_but_no_defs": flag_counter(
            lambda r: "zh 页面存在但清洗后无中文释义" in str(r["prescreen_flags"])),
        "zh_regap_recovered": regap_recovered,
        "zh_widened_rule_recovery": {
            "words_probed": len(refetch_rows),
            "words_with_candidates": sum(
                1 for item in refetch_rows if int(item["probe_candidates_n"]) > 0),
            "words_with_untagged_candidates": sum(
                1 for item in refetch_rows if int(item["probe_untagged_candidates_n"]) > 0),
            "words_only_domain_tagged": sum(
                1 for item in refetch_rows
                if int(item["probe_candidates_n"]) > 0
                and int(item["probe_untagged_candidates_n"]) == 0),
            "plain_line_defs": sum(1 for item in refetch_rows
                                   if int(item["probe_plain_line_defs"]) > 0),
            "lead_defs": sum(1 for item in refetch_rows
                             if int(item["probe_lead_defs"]) > 0),
            "other_section_defs": sum(1 for item in refetch_rows
                                      if int(item["probe_other_section_defs"]) > 0),
            "conversion_block_defs": sum(1 for item in refetch_rows
                                         if int(item["probe_conversion_block_defs"]) > 0),
            "no_han_anywhere": sum(1 for item in refetch_rows
                                   if item["diag"] == "no_han_anywhere"),
            "han_present_but_not_extractable": sum(
                1 for item in refetch_rows
                if item["diag"] == "han_present_but_not_extractable"),
            "pages_with_cc_cedict_template": sum(
                1 for item in refetch_rows if item["page_has_cc_cedict"] == "yes"),
            "pages_with_translation_blocks": sum(
                1 for item in refetch_rows if item["page_has_translation_blocks"] == "yes"),
            "pages_with_region_variant_marker": sum(
                1 for item in refetch_rows
                if item["page_has_region_variant_marker"] == "yes"),
            "by_group": {
                tag: {
                    "probed": sum(1 for item in refetch_rows
                                  if item["pilot_id"] in group_ids[tag]),
                    "with_candidates": sum(
                        1 for item in refetch_rows
                        if item["pilot_id"] in group_ids[tag]
                        and int(item["probe_candidates_n"]) > 0),
                    "with_untagged_candidates": sum(
                        1 for item in refetch_rows
                        if item["pilot_id"] in group_ids[tag]
                        and int(item["probe_untagged_candidates_n"]) > 0),
                }
                for tag, _label, _members in groups
            },
            "examples": [
                {"word": item["word"], "oldid": item["oldid"],
                 "candidates": item["probe_candidates_n"],
                 "untagged": item["probe_untagged_candidates_n"],
                 "first": item["probe_first_candidate"],
                 "first_untagged": item["probe_first_untagged"]}
                for item in refetch_rows if int(item["probe_candidates_n"]) > 0
            ][:12],
        },
        "traditional_probe_hit": flag_counter(
            lambda r: "粗测集命中繁体字形" in str(r["prescreen_flags"])),
        "tag_or_bracket": flag_counter(
            lambda r: "含括号/域标签" in str(r["prescreen_flags"])),
        "relation_counts": dict(Counter(
            str(row["prescreen_flags"]).split(" ｜ ")[0]
            for row in rows if str(row["prescreen_flags"]).startswith("两侧关系")
        )),
    },
    "human_workload": {
        "rows_to_fill": len(rows),
        "verdict_vocabulary": list(VERDICT_VOCAB),
        "columns_left_blank": [key for key in rows[0] if key.startswith("human_")],
        "filled_rows": 0,
        "note": "本文件交付时人工栏位全部为空：机器只做机械核对与风险标注，"
                "是否采用释义、是否修订由人工逐词决定。",
    },
    "pilot_words_by_group": {
        tag: [row["normalized_word"] for row in rows if row["group"] == tag]
        for tag, _label, _members in groups
    },
    "boundaries": {
        "netem_gloss_column_present_in_package": any(
            "netem_gloss" in column for column in next(iter(netem_csv))),
        "frequency_label": FREQ_LABEL,
        "imports_confirmed": 0,
        "production_database_touched": False,
    },
}
(PILOT / "pilot-stats.json").write_text(
    json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

print(json.dumps(stats, ensure_ascii=False, indent=2))
