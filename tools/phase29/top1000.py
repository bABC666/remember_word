"""Audit NETEM's first 1000 distinct words against local, pinned evidence only."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frozen300 import resolve as frozen_resolve

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "test-artifacts/phase29-provenance-evidence/kaoyan-vocab-research"
DECISIONS = ROOT / "tools/phase29/top1000-decisions.tsv"
FULL_SCRIPT = ROOT / "tools/phase29/rehearsal/50_full_lexicon_rehearsal.py"
HAN = re.compile(r"[\u3400-\u9fff]")
POS = {"noun", "verb", "adj", "adv", "pron", "det", "prep", "conj", "num", "interj"}


def _full_module():
    spec = importlib.util.spec_from_file_location("phase29_full", FULL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def source_rows(root: Path) -> tuple[list[dict], dict[str, tuple[int, dict]], dict[int, dict]]:
    """Recreate full WikDict CSV row numbers from the pinned package and NETEM order."""
    full = _full_module()
    raw = full._pinned_json(root / EVIDENCE.relative_to(ROOT) / "raw/netem_full_list.json",
                            full.RAW_SHA256)
    members, _ = full.membership_rows(raw["5530考研词汇词频排序表"])
    rows, _ = full.wikdict_backfill_rows(
        root / EVIDENCE.relative_to(ROOT) / "raw/wikdict-en-zh.zip", members)
    first = members[:1000]
    wanted_words = {row["word"].strip().casefold() for row in first}
    selected = {row["word"].strip().casefold(): (number, row)
                for number, row in enumerate(rows, 2)
                if row["word"].strip().casefold() in wanted_words}
    wanted_indices = {int(index) for _, row in selected.values()
                      for index in row["wikdict_entry_index"].split("|")}
    archive = root / EVIDENCE.relative_to(ROOT) / "raw/wikdict-en-zh.zip"
    with zipfile.ZipFile(archive) as package:
        index_bytes = package.read("wikdict-en-zh/stardict.idx")
        body_bytes = package.read("wikdict-en-zh/stardict.dict")
    originals = {}
    cursor = number = 0
    while cursor < len(index_bytes):
        end = index_bytes.index(b"\0", cursor)
        offset = int.from_bytes(index_bytes[end + 1:end + 5], "big")
        size = int.from_bytes(index_bytes[end + 5:end + 9], "big")
        if number in wanted_indices:
            raw = body_bytes[offset:offset + size]
            originals[number] = {
                "index": number, "index_byte_offset": cursor,
                "dict_byte_offset": offset, "dict_byte_length": size,
                "headword": index_bytes[cursor:end].decode("utf-8"),
                "body_sha256": hashlib.sha256(raw).hexdigest(),
                "grammar": re.findall(r'class="grammar"[^>]*>([^<]+)',
                                      raw.decode("utf-8")),
                "body": raw.decode("utf-8"),
            }
        cursor = end + 9
        number += 1
    if set(originals) != wanted_indices:
        raise ValueError("missing original WikDict Stardict positions")
    for word, (_, row) in selected.items():
        for number_text, offset_text in zip(row["wikdict_entry_index"].split("|"),
                                            row["wikdict_entry_offset"].split("|"), strict=True):
            original = originals[int(number_text)]
            if (original["dict_byte_offset"] != int(offset_text)
                    or original["headword"].strip().casefold() != word):
                raise ValueError(f"WikDict CSV/original position mismatch: {word}")
    return first, selected, originals


def resolve(root: Path) -> list[dict]:
    root = root.resolve()
    members, wikdict, originals = source_rows(root)
    frozen = {record["word"]: record for record in frozen_resolve(root)}
    with (root / DECISIONS.relative_to(ROOT)).open(encoding="utf-8-sig", newline="") as stream:
        choices = list(csv.DictReader(stream, delimiter="|"))
    by_word: dict[str, list[dict]] = defaultdict(list)
    for choice in choices:
        by_word[choice["word"]].append(choice)
    member_words = [row["word"].strip().casefold() for row in members]
    if len(member_words) != 1000 or len(set(member_words)) != 1000:
        raise ValueError("NETEM first 1000 unique words drifted")
    fresh = set(member_words) - set(frozen)
    if set(by_word) != fresh:
        raise ValueError(f"decision coverage drift: missing={fresh-set(by_word)}, "
                         f"extra={set(by_word)-fresh}")
    result = []
    for rank, member in enumerate(members, 1):
        word = member_words[rank - 1]
        if word in frozen:
            record = json.loads(json.dumps(frozen[word], ensure_ascii=False))
            record["reused_frozen"] = True
            record["netem_unique_order"] = rank
            result.append(record)
            continue
        source = wikdict.get(word)
        groups: dict[str, dict] = {}
        omitted: list[dict] = []
        used = set()
        no_source = False
        for choice in by_word[word]:
            status, pos, text, needle = (choice[k].strip() for k in
                                         ("status", "pos", "text", "needle"))
            reason = choice["reason"].strip()
            if not reason or status not in {"confirm", "candidate", "no_source"}:
                raise ValueError(f"malformed decision: {word} {choice}")
            if status == "no_source":
                if source or len(by_word[word]) != 1 or pos or text or needle:
                    raise ValueError(f"invalid no-source decision: {word}")
                no_source = True
                continue
            if source is None:
                raise ValueError(f"source missing for decision: {word}")
            line, row = source
            raw = row["wikdict_meaning"]
            if (pos not in POS or not text or not HAN.search(text) or not needle
                    or needle not in raw or len(text) > 36 or "\n" in text):
                raise ValueError(f"invalid text, POS or original needle: {word} {choice}")
            positions = [originals[int(index)] for index in
                         row["wikdict_entry_index"].split("|")
                         if needle in originals[int(index)]["body"]]
            if not positions:
                raise ValueError(f"WikDict needle missing from original body: {word} {needle}")
            group = groups.setdefault(pos, {"pos_key": pos, "pos_order": len(groups) + 1,
                                            "meanings": []})
            if len(group["meanings"]) >= 3:
                raise ValueError(f"overfull POS group: {word}/{pos}")
            sense = {
                "text": text, "basis": "W", "reason": reason,
                "decision": "propose" if status == "confirm" else "pending",
                "source_locator": f"wikdict:{line}", "raw_line": raw,
                "source_text": raw, "language": "en", "pos_key": pos,
                "pos_source": "reviewer", "display_order": len(group["meanings"]) + 1,
                "line_number": line, "needle": needle,
                "original_positions": [{k: v for k, v in position.items() if k != "body"}
                                       for position in positions],
                "wikdict_package_sha256": row["wikdict_package_sha256"],
                "derivation_note": "" if text in raw else "由所引中文原文抽义或繁简转换；" + reason,
            }
            group["meanings"].append(sense)
            used.add(needle)
        if source:
            line, row = source
            for fragment in row["wikdict_meaning"].split("；"):
                if not fragment or any(fragment == needle or needle in fragment or
                                       fragment in needle for needle in used):
                    continue
                omitted.append({"locator": f"wikdict:{line}", "raw_line": fragment,
                                "reason": "未采用：本词决策记录未把该片段判为常见且词性明确的独立展示义；可能重复、低频或噪声"})
        result.append({"word": word, "netem_unique_order": rank,
                       "netem_rank": member["netem_rank"], "groups": list(groups.values()),
                       "omitted": omitted, "no_source": no_source,
                       "source_row": source[0] if source else None,
                       "source_raw": source[1]["wikdict_meaning"] if source else "",
                       "source_indices": source[1]["wikdict_entry_index"] if source else "",
                       "source_offsets": source[1]["wikdict_entry_offset"] if source else ""})
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(resolve(ROOT), stream, ensure_ascii=False, indent=2)
        stream.write("\n")
