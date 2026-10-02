"""Parse user supplied word lists without consulting platform source data."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

MAX_FILE_BYTES = 1024 * 1024
MAX_ROWS = 10000
WORD_PATTERN = re.compile(r"^[A-Za-z]+(?:['-][A-Za-z]+)*$")


@dataclass(frozen=True)
class FileRow:
    line: int
    word: str
    meaning: str
    part_of_speech: str
    status: str
    reason: str = ""

    def as_dict(self) -> dict[str, str | int]:
        return vars(self).copy()


def parse_file(filename: str, content: bytes) -> list[FileRow]:
    extension = filename.rsplit(".", 1)[-1].lower()
    if extension not in ("txt", "csv"):
        raise ValueError("仅支持 TXT 或 CSV 文件")
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError("文件须为非空且不超过 1 MB")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("文件须使用 UTF-8 编码") from exc
    if "\x00" in text:
        raise ValueError("文件包含无效字符")

    records: list[tuple[int, str, str, str]] = []
    if extension == "txt":
        records = [(number, line.strip(), "", "") for number, line in enumerate(text.splitlines(), 1)
                   if line.strip()]
    else:
        try:
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            header = [cell.strip().casefold() for cell in next(reader)]
            aliases = {"word": ("word", "词", "单词"),
                       "meaning": ("meaning", "释义"),
                       "part_of_speech": ("part_of_speech", "词性", "pos")}
            columns = {key: next((header.index(alias) for alias in options if alias in header), None)
                       for key, options in aliases.items()}
            if columns["word"] is None or len(header) != len(set(header)):
                raise ValueError("CSV 必须含唯一的 word（或 单词）表头")
            for line in reader:
                if not any(cell.strip() for cell in line):
                    continue
                def cell(key: str, line: list[str] = line) -> str:
                    index = columns[key]
                    return line[index].strip() if index is not None and index < len(line) else ""
                records.append((reader.line_num, cell("word"), cell("meaning"), cell("part_of_speech")))
        except (csv.Error, StopIteration) as exc:
            raise ValueError("CSV 格式错误或缺少表头") from exc
    if len(records) > MAX_ROWS:
        raise ValueError("最多支持 10000 行")
    seen: set[str] = set()
    result: list[FileRow] = []
    for line, word, meaning, pos in records:
        reason = ""
        status = "valid"
        if not word or len(word) > 160 or not WORD_PATTERN.fullmatch(word) or len(meaning) > 2000 or len(pos) > 80:
            status, reason = "error", "单词无效或字段过长"
        elif word.casefold() in seen:
            status, reason = "duplicate", "文件内重复"
        else:
            seen.add(word.casefold())
        result.append(FileRow(line, word, meaning, pos, status, reason))
    return result
