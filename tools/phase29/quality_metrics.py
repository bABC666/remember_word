#!/usr/bin/env python3
"""Phase 2.9 · 有界质量核验（第 2 步）：把人工核对结论汇总成比率与样例。

输入：
  quality-sample-dump.json      —— 由 quality_sample.py 产出（逐词两源释义全文，隔离目录）
  quality-verdicts.json         —— 逐词逐源的人工核对判定（class + reason，可逐条复核）
输出：
  人工核对比率表（markdown，可直接粘进报告）+ 结构化 JSON

分层口径（与报告 §8.4 一致）：
  A 直接可用 = 至少一个来源 OK
  B 轻修订后可用 = 至少一个来源 EDIT 且没有 OK
  C 不可用 = 所有来源都属 MISSING/WRONG/NONE
  A+B 即"人工核对后可用"；并与"原始非空释义覆盖"并列给出。
"""
from __future__ import annotations

import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(ROOT, "reports")
dump = json.load(open(os.path.join(REP, "quality-sample-dump.json"), encoding="utf-8"))
verd = json.load(open(os.path.join(REP, "quality-verdicts.json"), encoding="utf-8"))["verdicts"]
V = {v["word"]: v for v in verd}

CLASSES = ("OK", "EDIT", "MISSING", "WRONG", "NONE")
out = ["# Phase 2.9 有界质量核验：原始非空覆盖 vs 人工核对后可用（n=60）", ""]

n = len(dump)
raw_nonempty = [d for d in dump if d["zw_defs"] or d["wikdict_defs"]]
tier = {"A": [], "B": [], "C": []}
for d in dump:
    v = V.get(d["word"], {})
    zw, wd = v.get("zw"), v.get("wd")
    if "OK" in (zw, wd):
        tier["A"].append(d["word"])
    elif "EDIT" in (zw, wd):
        tier["B"].append(d["word"])
    else:
        tier["C"].append(d["word"])

out.append("## 1. 两个口径并列")
out.append(f"- **原始非空释义覆盖（本抽样）**：{len(raw_nonempty)}/{n} = {len(raw_nonempty)/n:.1%}"
           "（只表示取到了非空释义字段，不含正确性判断）")
out.append(f"- **人工核对后可用（A+B，本抽样）**：{(len(tier['A'])+len(tier['B']))}/{n} = "
           f"{(len(tier['A'])+len(tier['B']))/n:.1%}")
out.append(f"  - A 直接可用（至少一源 OK）：**{len(tier['A'])}/{n} = {len(tier['A'])/n:.1%}**")
out.append(f"  - B 轻修订后可用（至少一源 EDIT 且无 OK）：**{len(tier['B'])}/{n} = {len(tier['B'])/n:.1%}**")
out.append(f"  - C 不可用（全部源 MISSING/WRONG/NONE）：**{len(tier['C'])}/{n} = {len(tier['C'])/n:.1%}**")
out.append(f"  - ⚠ 因此本抽样里 **{(len(tier['B'])+len(tier['C']))/n:.1%} 的词至少需要一次人工修订**"
           "（B 类轻修订 + C 类不可用）")

out.append("\n## 2. 逐源判定分布")
for src, key in (("zh.wiktionary", "zw"), ("WikDict en-zh", "wd")):
    cnt = {c: sum(1 for d in dump if V.get(d["word"], {}).get(key) == c) for c in CLASSES}
    out.append(f"- **{src}**：" + "、".join(f"{c} {cnt[c]}（{cnt[c]/n:.0%}）" for c in CLASSES))
out.append("- 说明：EDIT 多数是繁体/重复/标签混入等**机械可修**问题；WRONG/MISSING 是**必须人工裁定**的问题。")

out.append("\n## 3. 必须人工裁定与需修订的清单")
out.append("\n### 3.1 错配（WRONG）—— 直接不能用，必须换来源或人工改写")
for d in dump:
    v = V.get(d["word"], {})
    for key, label in (("zw", "zh.wiktionary"), ("wd", "WikDict")):
        if v.get(key) == "WRONG":
            got = " ｜ ".join((d["zw_defs"] if key == "zw" else d["wikdict_defs"])[:5]) or "—"
            out.append(f"- `{d['word']}`（{label}）给出：{got}　→ {v.get(key + '_r', '')}")
out.append("\n### 3.2 缺主要义项（MISSING）—— 需补主义后再用")
for d in dump:
    v = V.get(d["word"], {})
    for key, label in (("zw", "zh.wiktionary"), ("wd", "WikDict")):
        if v.get(key) == "MISSING":
            got = " ｜ ".join((d["zw_defs"] if key == "zw" else d["wikdict_defs"])[:6]) or "—"
            out.append(f"- `{d['word']}`（{label}）给出：{got}　→ {v.get(key + '_r', '')}")
out.append("\n### 3.3 组合层面不可用（C 类，两源都不可用）")
for w in tier["C"]:
    d = next(x for x in dump if x["word"] == w)
    out.append(f"- `{w}`（词频 {d['freq']}）：zh.wiktionary {V[w].get('zw')}（{V[w].get('zw_r','')}）；"
               f"WikDict {V[w].get('wd')}（{V[w].get('wd_r','')}）")

out.append("\n## 4. 分层结果（高频词的质量是否更好）")
for band in ("high", "mid", "low"):
    sub = [d for d in dump if d["band"] == band]
    a = sum(1 for d in sub if "OK" in (V[d["word"]].get("zw"), V[d["word"]].get("wd")))
    b = sum(1 for d in sub if "OK" not in (V[d["word"]].get("zw"), V[d["word"]].get("wd"))
            and "EDIT" in (V[d["word"]].get("zw"), V[d["word"]].get("wd")))
    c = len(sub) - a - b
    raw = sum(1 for d in sub if d["zw_defs"] or d["wikdict_defs"])
    out.append(f"- **{band}**（{len(sub)} 词）：原始非空 {raw}、A {a}、B {b}、C {c}"
               f" → 人工核对后可用 {(a+b)/len(sub):.0%}")

out.append("\n## 5. 样例（各有代表性的判定原文）")
picks = ["information", "job", "capacity", "denounce", "vocal", "management", "even", "soldier", "suspect", "beauty"]
for w in picks:
    d = next((x for x in dump if x["word"] == w), None)
    if not d:
        continue
    v = V[w]
    out.append(f"\n**{w}**（词频 {d['freq']}，zh oldid {d['zw_oldid']}）")
    out.append(f"- NETEM 释义（权属未明，仅对照）：{d['netem_gloss']}")
    out.append(f"- zh.wiktionary：{' ｜ '.join(d['zw_defs'][:8]) or '—'}　→ **{v['zw']}**：{v['zw_r']}")
    out.append(f"- WikDict：{' ｜ '.join(d['wikdict_defs'][:6]) or '—'}　→ **{v['wd']}**：{v['wd_r']}")

md = "\n".join(out) + "\n"
open(os.path.join(REP, "quality-verification.md"), "w", encoding="utf-8").write(md)
summary = {
    "n": n,
    "raw_nonempty": len(raw_nonempty),
    "tier_A": len(tier["A"]), "tier_B": len(tier["B"]), "tier_C": len(tier["C"]),
    "usable": len(tier["A"]) + len(tier["B"]),
    "by_source": {k: {c: sum(1 for d in dump if V.get(d["word"], {}).get(k) == c) for c in CLASSES}
                  for k in ("zw", "wd")},
    "needs_human_revision": len(tier["B"]) + len(tier["C"]),
    "unusable_words": tier["C"],
}
json.dump(summary, open(os.path.join(REP, "quality-summary.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(json.dumps(summary, ensure_ascii=False))
