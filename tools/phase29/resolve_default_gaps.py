"""Resolve only the 336 missing words from immutable Wiktionary response snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/phase29"))
from zhwiktionary_clean_measure import EN_TITLES, HEAD, norm_title, strip_markup

HAN = re.compile(r"[\u3400-\u9fff]")
NUMBERED = re.compile(r"^:\s*\d+[.、]\s*(.+)$")
TRANSLATION = re.compile(r"\|t=([^}|]+)")
SOURCE = "zhwiktionary"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract(text: str) -> list[dict]:
    """Read explicit Chinese definition lines in the exact page's English section."""
    if "CC-CEDICT" in text:
        return []
    found = []
    in_english = False
    subsection = ""
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        heading = HEAD.match(line)
        if heading:
            level = len(heading.group(1))
            title = norm_title(heading.group(2))
            if level == 2:
                in_english = title in EN_TITLES
                subsection = ""
            elif in_english:
                subsection = title
            continue
        if not in_english or not line:
            continue
        method = ""
        value = ""
        if line.startswith("#") and not line.startswith(("#:", "#*", "#;", "##")):
            if "{{standard spelling of|en|" in line:
                match = TRANSLATION.search(line)
                if match:
                    value, method = match.group(1).strip(), "exact_page_template_translation"
            if not value:
                value, method = strip_markup(line), "numbered_definition"
        elif line.startswith(":") and not line.startswith("::"):
            match = NUMBERED.match(line)
            if match:
                value, method = strip_markup(match.group(1)), "colon_numbered_definition"
        elif subsection in ("", "發音", "发音", "Pronunciation"):
            if (not line.startswith(("*", "[", "{", "|", "<", ";"))
                    and "{{" not in line and "[[" not in line and "http" not in line
                    and "=" not in line and not re.search(r"[。！？!?]$", line)
                    and len(line) <= 300):
                value, method = line, "bare_english_section_line"
        if not value or not HAN.search(value) or len(value) > 2000:
            continue
        if value.startswith(("音頻", "音频", "發音", "发音", "國際音標", "国际音标", "韻腳", "韵脚")):
            continue
        if any(item["meaning"] == value for item in found):
            continue
        found.append({"meaning": value, "wikitext_line": number,
                      "raw_line": raw, "raw_line_sha256": sha(raw.encode("utf-8")),
                      "parse_method": method})
    return found


def load_pages(snapshot: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    manifest = json.loads((snapshot / "snapshot-manifest.json").read_text("utf-8"))
    pinned = {}
    by_title = {}
    for item in manifest["responses"]:
        path = snapshot / item["file"]
        raw = path.read_bytes()
        if sha(raw) != item["sha256"]:
            raise ValueError(f"snapshot response drift: {path}")
        data = json.loads(raw)
        for page in data.get("query", {}).get("pages", []):
            for revision in page.get("revisions", []):
                fact = {"title": page["title"], "oldid": revision["revid"],
                        "timestamp": revision.get("timestamp"), "text": revision["slots"]["main"]["content"],
                        "snapshot_file": item["file"], "snapshot_sha256": item["sha256"]}
                if item["file"].startswith("pinned-"):
                    pinned[str(revision["revid"])] = fact
                else:
                    by_title[page["title"].casefold()] = fact
    return pinned, by_title


def run(snapshot: Path, output: Path) -> dict:
    package = ROOT / "test-artifacts/default-lexicon-candidate"
    cache_path = ROOT / "test-artifacts/phase29-provenance-evidence/kaoyan-vocab-research/reports/zhwiktionary-full-coverage.json"
    cache = json.loads(cache_path.read_text("utf-8"))
    manifest = json.loads((snapshot / "snapshot-manifest.json").read_text("utf-8"))
    if sha((package / "candidate-provenance.csv").read_bytes()) != manifest["candidate_provenance_sha256"]:
        raise ValueError("base candidate changed")
    if sha(cache_path.read_bytes()) != manifest["old_cache_sha256"]:
        raise ValueError("old cache changed")
    pinned, by_title = load_pages(snapshot)
    current_path = snapshot / "seemingly-current.json"
    current_bytes = current_path.read_bytes()
    current_page = json.loads(current_bytes)["query"]["pages"][0]
    if current_page["title"].casefold() != "seemingly":
        raise ValueError("seemingly current exact title mismatch")
    current_revision = current_page["revisions"][0]
    current_check = {"file": current_path.name, "sha256": sha(current_bytes),
                     "oldid": current_revision["revid"],
                     "cc_cedict_marker": "CC-CEDICT" in current_revision["slots"]["main"]["content"]}
    results = []
    counts = Counter()
    for word in manifest["missing_words"]:
        item = cache.get(word.casefold(), {})
        oldid = item.get("oldid")
        page = pinned.get(str(oldid)) if oldid else by_title.get(word.casefold())
        if page and page["title"].casefold() != word.casefold():
            raise ValueError(f"word/title mismatch: {word} != {page['title']}")
        if page and oldid and page["oldid"] != oldid:
            raise ValueError(f"revision mismatch: {word}")
        if not page:
            reason = "exact_title_missing_at_source" if not oldid else "pinned_revision_missing"
            result = {"word": word, "status": "missing", "reason": reason}
        elif "CC-CEDICT" in page["text"]:
            result = {"word": word, "status": "missing", "reason": "excluded_page_cc_cedict",
                      "oldid": page["oldid"], "snapshot_file": page["snapshot_file"]}
        else:
            definitions = extract(page["text"])
            meaning = "；".join(dict.fromkeys(row["meaning"] for row in definitions))
            if not meaning or len(meaning) > 2000:
                reason = "no_usable_chinese_in_exact_english_section" if not meaning else "meaning_exceeds_import_limit"
                result = {"word": word, "status": "missing", "reason": reason,
                          "oldid": page["oldid"], "snapshot_file": page["snapshot_file"]}
            else:
                result = {"word": word, "status": "adopted",
                          "basis": "parser_repair" if oldid else "network_new",
                          "meaning": meaning, "source": SOURCE, "source_title": page["title"],
                          "oldid": page["oldid"], "revision_timestamp": page["timestamp"],
                          "revision_url": f"https://zh.wiktionary.org/w/index.php?title={word}&oldid={page['oldid']}",
                          "snapshot_file": page["snapshot_file"], "snapshot_sha256": page["snapshot_sha256"],
                          "lines": definitions}
        counts[result.get("basis", "missing")] += 1
        results.append(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"snapshot_manifest_sha256": sha((snapshot / "snapshot-manifest.json").read_bytes()),
                                  "base_provenance_sha256": manifest["candidate_provenance_sha256"],
                                  "seemingly_current_check": current_check,
                                  "counts": dict(counts), "results": results}, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return dict(counts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.snapshot, args.output), ensure_ascii=False))
