"""Resolve the reviewed frozen 300 words against local preserved source bytes.

This module does no networking and never opens the application database. Its output is
an audit record, not a confirmation. The separate isolated runner consumes it.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path

EVIDENCE = Path("test-artifacts/phase29-provenance-evidence/kaoyan-vocab-research/rehearsal")
DECISIONS = Path("tools/phase29/frozen300-decisions.tsv")


def find_english_candidate(candidates: list[dict], needle: str) -> dict | None:
    """Use only an English line; a matching French gloss is never a fallback."""
    return next((candidate for candidate in candidates
                 if candidate.get("language") == "en"
                 and (needle in candidate.get("text", "")
                      or needle in candidate.get("raw_line", ""))), None)


def _csv(path: Path, *, delimiter: str = ",") -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source, delimiter=delimiter))


def resolve(root: Path) -> list[dict]:
    root = root.resolve()
    evidence = root / EVIDENCE
    frozen = _csv(evidence / "notes/05-worklist.tsv", delimiter="\t")
    selected = _csv(root / DECISIONS, delimiter="|")
    pages = json.loads((evidence / "pilot/v4-extraction-300.json").read_text(encoding="utf-8"))["pages"]
    wikitext = json.loads((evidence / "pilot/zh-pinned-wikitext-300.json").read_text(encoding="utf-8"))
    wikdict = {}
    for line, row in enumerate(_csv(evidence / "sources/wikdict.csv"), 2):
        wikdict.setdefault(row["word"].casefold(), (line, row))
    choices: dict[str, list[dict]] = defaultdict(list)
    for choice in selected:
        choices[choice["word"]].append(choice)
    frozen_words = [item["normalized_word"] for item in frozen]
    if len(frozen_words) != 300 or len(set(frozen_words)) != 300:
        raise ValueError("the frozen worklist is not 300 unique words")
    if set(choices) != set(frozen_words):
        raise ValueError(f"decision coverage differs: missing={set(frozen_words)-set(choices)}, "
                         f"extra={set(choices)-set(frozen_words)}")

    records = []
    for item in frozen:
        word, oldid = item["normalized_word"], item["zh_oldid"]
        page = pages[oldid]
        lines = wikitext[oldid].split("\n")
        groups: dict[str, dict] = {}
        used_positions = set()
        for choice in choices[word]:
            pos, text, basis, needle = (choice[key].strip() for key in
                                        ("pos", "text", "basis", "needle"))
            if not pos or not text or basis not in ("Z", "W", "AI"):
                raise ValueError(f"malformed decision for {word}: {choice}")
            group = groups.setdefault(pos, {"pos_key": pos, "pos_order": len(groups) + 1,
                                            "meanings": []})
            if len(group["meanings"]) == 3:
                raise ValueError(f"too many senses in {word}/{pos}")
            sense = {"text": text, "basis": basis, "reason": choice["reason"].strip(),
                     "decision": "propose", "source_locator": "", "raw_line": "",
                     "language": "en", "oldid": oldid, "pos_key": pos,
                     "display_order": len(group["meanings"]) + 1}
            if basis == "AI":
                sense.update(decision="pending", language="", reason=sense["reason"])
            elif basis == "Z":
                candidate = find_english_candidate(page["candidates"], needle)
                if candidate is None:
                    sense.update(decision="pending", reason=sense["reason"] + "；英语原文针未命中")
                else:
                    number = candidate["line_no"]
                    if lines[number - 1] != candidate["raw_line"]:
                        raise ValueError(f"extraction/raw mismatch {word} {oldid}:{number}")
                    sense.update(source_locator=f"zhwiktionary:{oldid}:{number}",
                                 raw_line=candidate["raw_line"], source_text=candidate["text"],
                                 language=candidate["language"], section=candidate["section"],
                                 line_number=number)
                    used_positions.add(("Z", number))
            else:
                located = wikdict.get(word)
                if located is None or needle not in located[1]["wikdict_meaning"]:
                    sense.update(decision="pending", reason=sense["reason"] + "；W原文针未命中")
                else:
                    number, row = located
                    sense.update(source_locator=f"wikdict:{number}",
                                 raw_line=row["wikdict_meaning"],
                                 source_text=row["wikdict_meaning"], line_number=number)
                    used_positions.add(("W", number))
            group["meanings"].append(sense)
        omitted = []
        for candidate in page["candidates"]:
            if candidate["language"] != "en" or ("Z", candidate["line_no"]) in used_positions:
                continue
            omitted.append({"locator": f"zhwiktionary:{oldid}:{candidate['line_no']}",
                            "raw_line": candidate["raw_line"],
                            "reason": "未采用：近义重复、次要领域义或词性与原文不足以支持本轮展示"})
        for rejected in page.get("rejected", []):
            if rejected.get("rejected_reason") == "cross_language":
                omitted.append({"locator": f"zhwiktionary:{oldid}:{rejected['line_no']}",
                                "raw_line": rejected["raw_line"],
                                "reason": "跨语言拒绝：该行不属于英语小节"})
        wrow = wikdict.get(word)
        if wrow and ("W", wrow[0]) not in used_positions:
            omitted.append({"locator": f"wikdict:{wrow[0]}",
                            "raw_line": wrow[1]["wikdict_meaning"],
                            "reason": "未采用：与所选义重复、混杂或词性证据不足"})
        records.append({"word": word, "oldid": oldid, "groups": list(groups.values()),
                        "omitted": omitted, "frozen_rank": item["netem_rank"]})
    return records


def key_evidence(root: Path) -> dict:
    """Inspect the already preserved WikDict package; never fetch a new page."""
    evidence = root.resolve() / EVIDENCE
    archive_path = evidence.parent / "raw/wikdict-en-zh.zip"
    with zipfile.ZipFile(archive_path) as archive:
        index = archive.read("wikdict-en-zh/stardict.idx")
        body = archive.read("wikdict-en-zh/stardict.dict")
    entries = []
    cursor = number = 0
    while cursor < len(index):
        end = index.find(b"\0", cursor)
        if end < 0:
            raise ValueError("invalid preserved Stardict index")
        headword = index[cursor:end].decode("utf-8")
        offset = int.from_bytes(index[end + 1:end + 5], "big")
        size = int.from_bytes(index[end + 5:end + 9], "big")
        number += 1
        if headword.casefold() == "key":
            raw = body[offset:offset + size].decode("utf-8")
            glosses = re.findall(r"<div>([^<]*[\u4e00-\u9fff][^<]*)</div>", raw)
            entries.append({"entry_number": number, "index_offset": cursor,
                            "dict_byte_offset": offset, "dict_byte_length": size,
                            "pos_html": re.findall(r'class="grammar"[^>]*>([^<]+)', raw),
                            "chinese_glosses": glosses, "raw_sha256":
                            hashlib.sha256(raw.encode("utf-8")).hexdigest()})
        cursor = end + 9
    coverage = json.loads((evidence.parent / "reports/zhwiktionary-full-coverage.json")
                          .read_text(encoding="utf-8"))["key"]
    noun_support = [item for item in entries
                    if "noun" in item["pos_html"] and "钥匙" in item["chinese_glosses"]]
    return {
        "word": "key", "inside_frozen_300": False,
        "wikdict_zip_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "zhwiktionary_coverage": coverage,
        "senses": {
            "钥匙": {"supported": bool(noun_support), "stardict_entries": noun_support,
                     "reason": "保全的 WikDict 名词条原文含简体「钥匙」"},
            "关键": {"supported": False, "stardict_entries": [],
                     "reason": "保全的 WikDict key 条无「关键」汉语释义；zh.wiktionary 缓存 defs 为空且未保全该 oldid 的逐行原文"},
        },
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as target:
        json.dump(resolve(args.root), target, ensure_ascii=False, indent=2)
        target.write("\n")
