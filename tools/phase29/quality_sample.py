#!/usr/bin/env python3
"""Phase 2.9 · 有界质量核验（第 1 步）：分层抽样 + 指纹索引。

* 抽样：按 NETEM `词频` 分三层（高 ≥1000 / 中 100–999 / 低 <100），每层 20 词，
  用固定种子随机抽取 —— 同 seed 必得同一集合，可复现。
* 数据：NETEM（词表/词频/释义）、zh.wiktionary（按全量覆盖缓存里的 **oldid** 取回那一版，
  用与 `zhwiktionary_clean_measure.py` 相同的清洗规则）、WikDict en-zh（本地包）。
* 输出：
  - `quality-sample-index.tsv`：词 / 词频 / 分层 / zh.wiktionary oldid / wikitext SHA-256 / 两源义项条数（**小，可入库**）
  - `quality-sample-dump.json`：逐词的两源释义全文（**只落隔离目录，不入库**）

用法：
    python quality_sample.py --words <netem_full_list.json> --coverage <zhwiktionary-full-coverage.json> \
        --wikdict <wikdict-en-zh.zip> --out-index index.tsv --out-dump dump.json [--per-stratum 20] [--seed 20260924]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zhwiktionary_clean_measure import clean  # 同一套清洗规则

API = "https://zh.wiktionary.org/w/api.php"
UA = {"User-Agent": "shici-phase29-research/1.0 (local, non-commercial)"}


def norm(x: str) -> str:
    return "".join(c for c in (x or "") if c.isalnum()).lower()


def api(params: dict, attempts: int = 4) -> dict:
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
    ap.add_argument("--words", required=True)
    ap.add_argument("--coverage", required=True, help="zhwiktionary-full-coverage.json（提供 oldid）")
    ap.add_argument("--wikdict", required=True, help="wikdict-en-zh.zip 路径")
    ap.add_argument("--out-index", default="quality-sample-index.tsv")
    ap.add_argument("--out-dump", default="quality-sample-dump.json")
    ap.add_argument("--per-stratum", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260924)
    ap.add_argument("--sleep", type=float, default=3.0)
    args = ap.parse_args()

    with open(args.words, encoding="utf-8") as source:
        rows = next(v for v in json.load(source).values() if isinstance(v, list))
    by_norm = {norm(r["单词"]): r for r in rows}
    with open(args.coverage, encoding="utf-8") as source:
        cov = json.load(source)

    def band(f: int) -> str:
        return "high" if f >= 1000 else ("mid" if f >= 100 else "low")

    strata: dict[str, list[str]] = {"high": [], "mid": [], "low": []}
    for k, r in by_norm.items():
        strata[band(int(r["词频"]))].append(k)
    rng = random.Random(args.seed)
    sample: list[str] = []
    for name in ("high", "mid", "low"):
        pool = sorted(strata[name])
        sample += rng.sample(pool, min(args.per_stratum, len(pool)))

    # ---- zh.wiktionary：按 oldid 取回抽样词的那一版
    oldids = {k: (cov.get(k) or {}).get("oldid") for k in sample}
    want = {str(v): k for k, v in oldids.items() if v}
    texts: dict[str, str] = {}
    ids = list(want)
    for i in range(0, len(ids), 50):
        d = api({"action": "query", "format": "json", "prop": "revisions",
                 "rvprop": "ids|content", "rvslots": "main", "revids": "|".join(ids[i:i + 50])})
        for p in d.get("query", {}).get("pages", {}).values():
            rev = (p.get("revisions") or [None])[0]
            if rev:
                texts[str(rev["revid"])] = rev["slots"]["main"]["*"]
        time.sleep(args.sleep)

    # ---- WikDict（本地包）
    wik: dict[str, list[str]] = {}
    with zipfile.ZipFile(args.wikdict) as z:
        idx = z.read("wikdict-en-zh/stardict.idx")
        dic = z.read("wikdict-en-zh/stardict.dict")
        i = 0
        while i < len(idx):
            j = idx.find(b"\0", i)
            if j < 0:
                break
            w = idx[i:j].decode("utf-8", "replace")
            off = int.from_bytes(idx[j + 1:j + 5], "big")
            size = int.from_bytes(idx[j + 5:j + 9], "big")
            k = norm(w)
            if k in set(sample) and k not in wik:
                body = dic[off:off + size].decode("utf-8", "replace")
                zh = [x for x in re.findall(r"<div>([^<>]{1,40})</div>", body) if re.search(r"[\u4e00-\u9fff]", x)]
                wik[k] = zh[:10]
            i = j + 9

    index = ["word\tfreq\tband\tzw_oldid\tzw_wikitext_sha256\tzw_defs\tzw_ipa\twikdict_defs\tnetem_gloss"]
    dump = []
    for k in sample:
        r = by_norm[k]
        oldid = oldids.get(k)
        text = texts.get(str(oldid), "")
        defs, _why = clean(text) if text else ([], "no_revision")
        ipa = ""
        m = re.search(r"\{\{IPA\|en\|([^}|]+)", text)
        if m:
            ipa = m.group(1).strip().strip("/")
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "-"
        wdefs = wik.get(k, [])
        index.append("\t".join([r["单词"], str(r["词频"]), band(int(r["词频"])), str(oldid or "-"),
                                sha, str(len(defs)), ipa or "-", str(len(wdefs)), r.get("释义", "")]))
        dump.append({"word": r["单词"], "norm": k, "freq": int(r["词频"]), "band": band(int(r["词频"])),
                     "netem_gloss": r.get("释义", ""), "zw_oldid": oldid, "zw_sha256": sha,
                     "zw_defs": defs, "zw_ipa": ipa, "wikdict_defs": wdefs})

    with open(args.out_index, "w", encoding="utf-8") as output:
        output.write("\n".join(index) + "\n")
    with open(args.out_dump, "w", encoding="utf-8") as output:
        json.dump(dump, output, ensure_ascii=False, indent=2)
    zwn = sum(1 for d in dump if d["zw_defs"])
    wkn = sum(1 for d in dump if d["wikdict_defs"])
    print(f"sample={len(dump)} (high/mid/low={sum(1 for d in dump if d['band']=='high')}/"
          f"{sum(1 for d in dump if d['band']=='mid')}/{sum(1 for d in dump if d['band']=='low')}); "
          f"zh.wiktionary 有释义 {zwn}; WikDict 有释义 {wkn}; 组合原始非空 {sum(1 for d in dump if d['zw_defs'] or d['wikdict_defs'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
