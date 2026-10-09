"""Fetch actual zh.wiktionary revisions for NETEM candidate gaps into a new snapshot.

Never overwrites an existing response. Run only for the candidate's missing words.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "test-artifacts/default-lexicon-candidate"
CACHE = ROOT / "test-artifacts/phase29-provenance-evidence/kaoyan-vocab-research/reports/zhwiktionary-full-coverage.json"
API = "https://zh.wiktionary.org/w/api.php"
UA = "ShiciDefaultLexiconGapAudit/1.0 (read-only source research)"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request(params: dict[str, str], target: Path) -> dict:
    if target.exists():
        raise FileExistsError(target)
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                body = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "")
            break
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 4:
                raise
            time.sleep(10 * (attempt + 1))
    parsed = json.loads(body)
    target.write_bytes(body)
    return {"file": target.name, "url": url, "http_status": status,
            "content_type": content_type, "sha256": digest(body),
            "bytes": len(body), "fetched_at_utc": datetime.now(UTC).isoformat(),
            "response": parsed}


def run(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    with (PACKAGE / "candidate-provenance.csv").open(encoding="utf-8", newline="") as handle:
        missing = [row["word"] for row in csv.DictReader(handle) if row["meaning_source"] == "missing"]
    cache = json.loads(CACHE.read_text("utf-8"))
    oldids = sorted({str(cache[word.casefold()]["oldid"]) for word in missing
                     if cache.get(word.casefold(), {}).get("oldid")})
    no_oldid = [word for word in missing if not cache.get(word.casefold(), {}).get("oldid")]
    manifest = {"candidate_provenance_sha256": digest((PACKAGE / "candidate-provenance.csv").read_bytes()),
                "old_cache_sha256": digest(CACHE.read_bytes()), "missing_words": missing,
                "pinned_oldids": oldids, "unresolved_titles": no_oldid, "responses": []}
    # MediaWiki limits revids/titles per request. Store the original JSON bytes,
    # not a reconstructed extract, so title, redirect and revision can be audited.
    for number, start in enumerate(range(0, len(oldids), 40), 1):
        item = request({"action": "query", "format": "json", "formatversion": "2",
                        "prop": "revisions", "rvprop": "ids|timestamp|content", "rvslots": "main",
                        "revids": "|".join(oldids[start:start + 40])},
                       output / f"pinned-{number:02}.json")
        item.pop("response")
        manifest["responses"].append(item)
        time.sleep(2)
    if no_oldid:
        item = request({"action": "query", "format": "json", "formatversion": "2",
                        "redirects": "1", "prop": "revisions", "rvprop": "ids|timestamp|content",
                        "rvslots": "main", "titles": "|".join(no_oldid)}, output / "unresolved-titles.json")
        item.pop("response")
        manifest["responses"].append(item)
    # The archived seemingly revision is excluded because its page bears a
    # CC-CEDICT marker. Check the current exact title independently as well.
    item = request({"action": "query", "format": "json", "formatversion": "2",
                    "redirects": "1", "prop": "revisions", "rvprop": "ids|timestamp|content",
                    "rvslots": "main", "titles": "seemingly"}, output / "seemingly-current.json")
    item.pop("response")
    manifest["responses"].append(item)
    (output / "snapshot-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"oldids": len(oldids), "unresolved_titles": len(no_oldid), "responses": len(manifest["responses"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False))
