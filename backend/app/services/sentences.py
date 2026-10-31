"""Stable sentence identities shared by contextual lookup and aligned translation."""
from __future__ import annotations

import re

_BOUNDARY = re.compile(r'''[.!?]+["”’')\]]*(?=\s|$)|\n\s*\n''')
_ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "vs.", "e.g.", "i.e."}


def article_sentences(content: str) -> list[dict]:
    result = []
    start = 0
    paragraph = 0
    for boundary in _BOUNDARY.finditer(content):
        is_paragraph = boundary.group().startswith("\n")
        prefix = content[start:boundary.end()].strip()
        token = prefix.split()[-1].casefold() if prefix else ""
        if not is_paragraph and (token in _ABBREVIATIONS or re.fullmatch(r"(?:[a-z]\.)+", token)):
            continue
        if (not is_paragraph and boundary.group()[-1:] in ('"', '”', '’')
                and re.match(r"\s+(?:he|she|they|we|you|it)\b", content[boundary.end():])):
            continue
        text = content[start:boundary.start() if is_paragraph else boundary.end()].strip()
        if text:
            result.append({"index": len(result), "text": text, "paragraph": paragraph})
        start = boundary.end()
        if is_paragraph:
            paragraph += 1
    if content[start:].strip():
        result.append({"index": len(result), "text": content[start:].strip(), "paragraph": paragraph})
    return result


def aligned_translations(article) -> list[str]:
    raw = article.translation_ai_raw_json or {}
    sources = [item["text"] for item in article_sentences(article.content)]
    values = raw.get("sentences", [])
    if (raw.get("sentence_sources") == sources and len(values) == len(sources)
            and all(isinstance(value, str) and value.strip() for value in values)):
        return values
    return []
