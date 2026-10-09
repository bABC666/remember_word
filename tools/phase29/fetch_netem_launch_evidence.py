"""Fetch fixed original-cache revisions and primary licence evidence, never DBs."""

import csv
import hashlib
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "test-artifacts/netem-launch-decision-20261005/upstream"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    candidate = ROOT / "test-artifacts/default-lexicon-candidate-v2/candidate-provenance.csv"
    assert (
        hashlib.sha256(candidate.read_bytes()).hexdigest()
        == "fb9b943c36abce1e6441e41f2b3485e1f0fbdd017af47713134c9b1fdc37a9e9"
    )
    rows = list(csv.DictReader(candidate.open(encoding="utf-8")))
    ids = [
        r["source_locator"][6:]
        for r in rows
        if r["meaning_source"] == "zhwiktionary" and "#line:" not in r["source_locator"]
    ]
    urls = {}
    for i in range(0, len(ids), 50):
        urls[f"original-{i // 50:02}.json"] = (
            "https://zh.wiktionary.org/w/api.php?"
            + urllib.parse.urlencode(
                {
                    "action": "query",
                    "format": "json",
                    "formatversion": 2,
                    "prop": "revisions",
                    "rvprop": "ids|timestamp|content",
                    "rvslots": "main",
                    "revids": "|".join(ids[i : i + 50]),
                }
            )
        )
    commit = "70dc6b68c855f21e666a7a291ff8ead5ca1f7b44"
    urls.update(
        {
            "netem-readme.md": f"https://raw.githubusercontent.com/exam-data/NETEMVocabulary/{commit}/README.md",
            "netem-license.txt": f"https://raw.githubusercontent.com/exam-data/NETEMVocabulary/{commit}/LICENSE",
            "netem-contributors.json": "https://api.github.com/repos/exam-data/NETEMVocabulary/contributors",
            "wikdict-about.html": "https://www.wikdict.com/page/about",
            "dbnary-about.html": "https://kaiko.getalp.org/about-dbnary/",
            "zh-copyright.json": "https://zh.wiktionary.org/w/api.php?action=query&format=json&formatversion=2&prop=revisions&rvprop=ids%7Ctimestamp%7Ccontent&rvslots=main&revids=8368252",
        }
    )
    manifest_path = OUT / "fetch-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {}
    for name, url in urls.items():
        path = OUT / name
        if path.exists():
            assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest[name]["sha256"]
            continue
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "NETEM-launch-review/1.0"})
                with urllib.request.urlopen(req, timeout=40) as r:
                    raw = r.read()
                    status = r.status
                path.write_bytes(raw)
                manifest[name] = {
                    "url": url,
                    "status": status,
                    "retrieved_utc": datetime.now(UTC).isoformat(),
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
                break
            except (OSError, URLError) as exc:
                if attempt == 2:
                    manifest[name] = {"url": url, "error": str(exc)}
                else:
                    time.sleep(2)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(name, manifest[name].get("status", manifest[name].get("error")), flush=True)


if __name__ == "__main__":
    main()
