#!/usr/bin/env python3
"""Phase 2.9 · 中文维基词典（zh.wiktionary）抽样与版本固定（oldid）。

用途：把"从主词表里抽了哪些词、当时那些页面是哪个版本"变成可复核的证据。
本脚本**只输出词头、pageid、oldid、时间戳、最后编辑者、wikitext 的 SHA-256 与字节数**，
不保存也不输出词条正文（正文属 CC BY-SA 内容，留在仓库外的隔离目录）。

用法：
    python zhwiktionary_pin_sample.py --words <词表 json 路径> --seed 20260924 --n 100 --out pinned.tsv

词表 json 需是 NETEMVocabulary 的 netem_full_list.json（顶层 dict，取值为数组，元素含 "单词"）。

规则（写死在代码里，便于复核）：
  * 抽样：sorted(去重词形) 后用 random.Random(seed).sample(words, n) —— 同 seed 同 n 必得同一集合
  * 取数：action=query&prop=revisions&rvprop=ids|timestamp|content|user（MediaWiki API）
  * 限速：每批（≤50 个标题）之间 sleep，避免触发 429
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
import urllib.parse
import urllib.request

API = "https://zh.wiktionary.org/w/api.php"
UA = {"User-Agent": "shici-phase29-research/1.0 (local, non-commercial)"}


def api(params: dict, attempts: int = 3) -> dict:
    url = f"{API}?{urllib.parse.urlencode(params)}"
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(8 * (i + 1))
    raise RuntimeError(f"give up: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", required=True, help="netem_full_list.json 路径")
    ap.add_argument("--seed", type=int, default=20260924)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--out", default="zhwiktionary-pinned.tsv")
    ap.add_argument("--sleep", type=float, default=5.0)
    args = ap.parse_args()

    with open(args.words, encoding="utf-8") as f:
        data = json.load(f)
    rows = next(v for v in data.values() if isinstance(v, list))
    words = sorted({str(r["单词"]).strip() for r in rows})
    sample = random.Random(args.seed).sample(words, args.n)

    out = ["word\tpageid\toldid\ttimestamp\tlast_editor\twikitext_sha256\twikitext_bytes"]
    for i in range(0, len(sample), 50):
        batch = sample[i:i + 50]
        d = api({"action": "query", "format": "json", "prop": "revisions",
                 "rvprop": "ids|timestamp|content|user", "rvslots": "main",
                 "titles": "|".join(batch), "redirects": "1"})
        by_title = {p.get("title", "").lower(): p for p in d.get("query", {}).get("pages", {}).values()}
        for w in batch:
            p = by_title.get(w.lower()) or by_title.get(w.capitalize().lower())
            rev = (p or {}).get("revisions", [None])[0] if p else None
            if not rev:
                out.append(f"{w}\t-\t-\t-\t-\t-\t-")
                continue
            text = rev["slots"]["main"]["*"]
            out.append("\t".join([
                w, str(p.get("pageid")), str(rev.get("revid")), str(rev.get("timestamp")),
                str(rev.get("user")), hashlib.sha256(text.encode("utf-8")).hexdigest(),
                str(len(text.encode("utf-8"))),
            ]))
        time.sleep(args.sleep)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    found = sum(1 for line in out[1:] if line.split("\t")[2] != "-")
    print(f"wrote {args.out}: {len(out) - 1} words, {found} pinned revisions "
          f"(seed={args.seed}, n={args.n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
