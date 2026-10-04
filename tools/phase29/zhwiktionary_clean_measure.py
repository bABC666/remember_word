#!/usr/bin/env python3
"""Phase 2.9 · zh.wiktionary 释义清洗与覆盖率度量（按固定 oldid）。

读取 `zhwiktionary_pin_sample.py` 产出的 TSV（word/pageid/oldid/...），
用 `revids=<oldid>` 精确取回**当时那个版本**的 wikitext，套用下方清洗规则，
输出**只含统计量的**报告（词头 + 条数 + 长度分布 + 样例词的 oldid），
不落盘词条正文。

清洗规则（v3b4，四条 bug 修复的历史见报告 §3.12）：
  1. 只取 level-2 且标题归一化后属于 {英語, 英语, 英文, English} 的小节
     （标题可能是 `==[[英语]]==` 这种带链接的形式，必须归一化）
  2. 该小节的**直接正文 + 其后所有 level>2 的子小节正文**（释义常在 ===名詞===/===動詞=== 里），
     遇到下一个 level<=2 小节即停止
  3. 逐行取 `#`/`*` 开头的释义行（排除 `#:` 例句、`#*`、`#;`、`##`、`*#`、`**:` 等）
  4. 含 `-{`/`}-` 的行整行丢弃（简繁/地区转换块，实测多为 `[自由軟件]` 一类领域标签）
  5. 去模板 `{{}}`、链接 `[[a|b]]→b`、HTML、粗斜体；丢弃不含汉字的行（发音/IPA 行由此消失）
  6. 行级噪声过滤（音頻/發音/韻腳/IPA/纯拉丁串），随后同串去重

用法：
    python zhwiktionary_clean_measure.py --pinned pinned.tsv --out metrics.md
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request

API = "https://zh.wiktionary.org/w/api.php"
UA = {"User-Agent": "shici-phase29-research/1.0 (local, non-commercial)"}
EN_TITLES = ("英語", "英语", "英文", "English")
HEAD = re.compile(r"^(={2,})([^=].*?)\1\s*$")
NOISE_RE = re.compile(
    r"^(?:音頻|音频|發音|发音|國際音標|国际音标|韻腳|韵脚|同音|Audio|Pronunciation|"
    r"enPR|IPA|Rhymes|Hyphenation|Homophones)\b"
    r"|音頻（|音频（"
    r"|^[（(【\[]?[A-Za-z\s,.\-/\[\]ˈˌːɹɾʔθðʃʒŋ]+[)）】\]]?$"
)


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


def norm_title(t: str) -> str:
    t = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", t)
    t = re.sub(r"\{\{[^{}]*\}\}", " ", t)
    t = re.sub(r"'''?", "", t)
    return t.strip()


NON_DEFINITION_SECTION = re.compile(
    r"近[義义]|同[義义]|反[義义]|[衍派]生|用法|使用[說说]明|[參参]考|[相有][關关]|"
    r"延伸|[異异]序|翻[譯译]|[詞词]源|其他[寫写形]|其他[詞词]形|替代|另[見见]|"
    r"synonyms|antonyms|derived|related|usage|references|further reading|"
    r"anagrams|translations|etymology|alternative|see also",
    re.IGNORECASE,
)


def definition_section_text(text: str) -> str:
    """Blank ancillary sections, preserving original line numbers and headings.

    A nested heading cannot reopen a blocked section; a sibling/ancestor can.
    Pronunciation is intentionally not blocked: legacy entries put bare
    definitions immediately after pronunciation without another heading.
    """
    blocked_level = None
    etymology_body = False
    lines = []
    for raw in text.splitlines():
        heading = HEAD.match(raw.strip())
        if heading:
            level = len(heading.group(1))
            etymology_body = bool(
                re.search(r"[詞词]源|etymology", norm_title(heading.group(2)), re.IGNORECASE)
            )
            if blocked_level is not None and level <= blocked_level:
                blocked_level = None
            if (
                blocked_level is None
                and level > 2
                and not etymology_body
                and NON_DEFINITION_SECTION.search(norm_title(heading.group(2)))
            ):
                blocked_level = level
            lines.append(raw)
        else:
            lines.append(raw if blocked_level is None and not etymology_body else "")
    return "\n".join(lines)


def split_sections(text: str) -> list[tuple[int, str, str]]:
    secs: list[tuple[int, str, str]] = []
    title, level, buf = None, 0, []
    for ln in text.splitlines():
        m = HEAD.match(ln.strip())
        if m:
            if title is not None:
                secs.append((level, title, "\n".join(buf)))
            level, title, buf = len(m.group(1)), m.group(2).strip(), []
        else:
            buf.append(ln)
    if title is not None:
        secs.append((level, title, "\n".join(buf)))
    return secs


def strip_markup(line: str) -> str:
    s, prev = line, None
    while prev != s:
        prev = s
        s = re.sub(r"\{\{[^{}]*\}\}", " ", s)
    s = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"'''''|'''|''", "", s)
    s = re.sub(r"^[#*:;]+\s*", "", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(wikitext: str) -> tuple[list[str], str]:
    secs = split_sections(definition_section_text(wikitext))
    idx = next((i for i, (lv, t, _) in enumerate(secs) if lv == 2 and norm_title(t) in EN_TITLES), None)
    if idx is None:
        return [], "no_en_section"
    bodies = [secs[idx][2]]
    for lv, _t, b in secs[idx + 1:]:
        if lv <= 2:
            break
        bodies.append(b)
    defs, seen = [], set()
    for b in bodies:
        for ln in b.splitlines():
            s = ln.strip()
            if not s or s[0] not in "#*" or s.startswith(("#*", "#:", "#;", "##", "*#", "*:", "**", "*;")):
                continue
            if "-{" in s or "}-" in s:
                continue
            t = strip_markup(s)
            if not re.search(r"[\u4e00-\u9fff]", t):
                continue
            if NOISE_RE.search(t) and len(re.sub(r"[^\u4e00-\u9fff]", "", t)) <= 4:
                continue
            if t in seen:
                continue
            seen.add(t)
            defs.append(t)
    return defs, "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pinned", required=True)
    ap.add_argument("--out", default="zhwiktionary-metrics.md")
    ap.add_argument("--sleep", type=float, default=4.0)
    args = ap.parse_args()

    rows = []
    with open(args.pinned, encoding="utf-8") as f:
        header = f.readline().rstrip("\n").split("\t")
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == len(header):
                rows.append(dict(zip(header, parts)))

    # fetch the pinned revisions by revid (deterministic)
    ids = [r["oldid"] for r in rows if r["oldid"] != "-"]
    texts: dict[str, str] = {}
    for i in range(0, len(ids), 50):
        d = api({"action": "query", "format": "json", "prop": "revisions",
                 "rvprop": "ids|content", "rvslots": "main",
                 "revids": "|".join(ids[i:i + 50])})
        for p in d.get("query", {}).get("pages", {}).values():
            rev = (p.get("revisions") or [None])[0]
            if rev:
                texts[str(rev["revid"])] = rev["slots"]["main"]["*"]
        time.sleep(args.sleep)

    results = []
    for r in rows:
        text = texts.get(r["oldid"], "")
        defs, why = clean(text) if text else ([], "no_revision")
        results.append({"word": r["word"], "oldid": r["oldid"], "n": len(defs),
                        "chars": sum(len(d) for d in defs),
                        "max_sense": max((len(d) for d in defs), default=0), "why": why})

    ok = [r for r in results if r["n"] > 0]
    chars = [r["chars"] for r in ok]
    mx = [r["max_sense"] for r in ok]
    out = ["# zh.wiktionary 清洗后覆盖率与释义质量（按固定 oldid 复测）", "",
           f"- 样本：{len(results)} 词；取回 revision：{len(texts)}；**清洗后得到 ≥1 条中文释义：{len(ok)}（{len(ok)/len(results):.0%}）**"]
    if chars:
        out.append(f"- 每词释义总长：中位 {statistics.median(chars):.0f} 字 / 均值 {statistics.mean(chars):.1f} / 最长 {max(chars)}")
        out.append(f"- 单条最长释义：中位 {statistics.median(mx):.0f} 字；≥10 字 {sum(1 for x in mx if x >= 10)} 词、"
                   f"≥20 字 {sum(1 for x in mx if x >= 20)} 词、≥30 字 {sum(1 for x in mx if x >= 30)} 词")
        out.append(f"- 释义条数：1 条 {sum(1 for r in ok if r['n'] == 1)}、2–3 条 {sum(1 for r in ok if 2 <= r['n'] <= 3)}、"
                   f"≥4 条 {sum(1 for r in ok if r['n'] >= 4)}")
    reasons = {w: sum(1 for r in results if r["why"] == w)
               for w in sorted({r["why"] for r in results})}
    out.append(f"- 未得到释义的原因：{reasons}")
    out.append("")
    out.append("## 样例词的 oldid（正文不落盘）")
    for r in ok[:15]:
        out.append(f"- {r['word']}：oldid {r['oldid']}，{r['n']} 条，共 {r['chars']} 字")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"wrote {args.out}: cleaned {len(ok)}/{len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
