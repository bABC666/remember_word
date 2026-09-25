"""Run the read-only public-lexicon CLI against the rehearsal package.

No database is opened: every command here is registered as ``file_only_preview`` in
``backend/app/cli.py``, which skips ``verify_schema_revision`` and settings entirely.
The production database is only fingerprinted, never read by the CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir",
                        default=r"C:\Temp\dsh-bSBXrH\kaoyan-vocab-research\rehearsal")
    parser.add_argument("--repo", default=r"D:\背单词web")
    return parser.parse_args()


ARGS = parse_args()
PKG = Path(ARGS.package_dir)
SRC = PKG / "sources"
OUT = PKG / "out"
NOTES = PKG / "notes"
REPO = Path(ARGS.repo)
PY = REPO / "backend" / ".venv" / "Scripts" / "python.exe"
DB = REPO / "data" / "vocab.db"
TARGET = "kaoyan-2027-public-rehearsal"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    return {
        "path": str(path), "exists": True, "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns, "sha256": sha256(path),
    }


def run(name: str, arguments: list[str], stdout_file: Path | None) -> dict[str, object]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    completed = subprocess.run(
        [str(PY), "-m", "app.cli", *arguments],
        cwd=str(REPO / "backend"), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    record = {
        "name": name, "argv": ["python", "-m", "app.cli", *arguments],
        "exit_code": completed.returncode,
        "stdout": completed.stdout, "stderr": completed.stderr,
    }
    if stdout_file is not None:
        stdout_file.write_text(completed.stdout, encoding="utf-8")
        record["stdout_file"] = stdout_file.name
        record["stdout_bytes"] = stdout_file.stat().st_size
        record["stdout_head"] = completed.stdout[:400]
    return record


OUT.mkdir(parents=True, exist_ok=True)
# The plan writer refuses to overwrite evidence, so each rehearsal run starts clean.
for stale in OUT.iterdir():
    if stale.is_file():
        stale.unlink()
before = {
    "db": fingerprint(DB),
    "data_dir": {
        path.name: fingerprint(path).get("sha256")
        for path in sorted((REPO / "data").glob("*")) if path.is_file()
    },
}
git_before = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                            capture_output=True, text=True, encoding="utf-8",
                            check=False).stdout

records = [
    run("preview-many R1",
        ["public-lexicon", "preview-many", "manifest-r1.json", "--source-root", str(SRC)],
        OUT / "preview-r1.json"),
    run("plan R1 (empty decisions)",
        ["public-lexicon", "plan", "manifest-r1.json", "--source-root", str(SRC),
         "--decisions", "decisions-r1-empty.json",
         "--plan", str(OUT / "plan-r1-empty.json"), "--target-lexicon", TARGET], None),
    run("plan R1 (--require-ready)",
        ["public-lexicon", "plan", "manifest-r1.json", "--source-root", str(SRC),
         "--decisions", "decisions-r1-empty.json",
         "--plan", str(OUT / "plan-r1-empty-requireready.json"),
         "--target-lexicon", TARGET, "--require-ready"], None),
    run("plan R2 (NETEM gloss mapped as meaning)",
        ["public-lexicon", "plan", "manifest-r2.json", "--source-root", str(SRC),
         "--decisions", "decisions-r1-empty.json",
         "--plan", str(OUT / "plan-r2-gloss.json"), "--target-lexicon", TARGET], None),
    run("plan demo20 (machine-drafted decisions)",
        ["public-lexicon", "plan", "manifest-demo20.json", "--source-root", str(SRC),
         "--decisions", "decisions-demo20.json",
         "--plan", str(OUT / "plan-demo20.json"), "--target-lexicon", TARGET,
         "--require-ready"], None),
    run("plan demo20 (repeat: idempotency)",
        ["public-lexicon", "plan", "manifest-demo20.json", "--source-root", str(SRC),
         "--decisions", "decisions-demo20.json",
         "--plan", str(OUT / "plan-demo20-repeat.json"), "--target-lexicon", TARGET,
         "--require-ready"], None),
    run("plan demo20 (refuse overwrite of an existing --plan path)",
        ["public-lexicon", "plan", "manifest-demo20.json", "--source-root", str(SRC),
         "--decisions", "decisions-demo20.json",
         "--plan", str(OUT / "plan-demo20.json"), "--target-lexicon", TARGET], None),
    run("plan R1 (manifest missing under the wrong --source-root)",
        ["public-lexicon", "plan", "manifest-r1.json", "--source-root", str(OUT),
         "--decisions", "decisions-r1-empty.json",
         "--plan", str(OUT / "plan-outside-root.json"), "--target-lexicon", TARGET], None),
    run("plan R1 (manifest path leaves --source-root)",
        ["public-lexicon", "plan", "../sources/manifest-r1.json", "--source-root", str(OUT),
         "--decisions", "decisions-r1-empty.json",
         "--plan", str(OUT / "plan-escape-manifest.json"), "--target-lexicon", TARGET], None),
    run("plan (source file leaves --source-root via manifest)",
        ["public-lexicon", "plan", "manifest-escape.json", "--source-root", str(SRC),
         "--decisions", "decisions-r1-empty.json",
         "--plan", str(OUT / "plan-escape-source.json"), "--target-lexicon", TARGET], None),
    run("preview-many (source file leaves --source-root via manifest)",
        ["public-lexicon", "preview-many", "manifest-escape.json", "--source-root", str(SRC)],
        None),
    run("plan R1 (exam_corpus role rejected)",
        ["public-lexicon", "plan", "manifest-r3-exam-corpus.json", "--source-root", str(SRC),
         "--decisions", "decisions-r1-empty.json",
         "--plan", str(OUT / "plan-r3-exam-corpus.json"), "--target-lexicon", TARGET], None),
]

after = {
    "db": fingerprint(DB),
    "data_dir": {
        path.name: fingerprint(path).get("sha256")
        for path in sorted((REPO / "data").glob("*")) if path.is_file()
    },
}
git_after = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                           capture_output=True, text=True, encoding="utf-8",
                           check=False).stdout

artifacts = {}
for path in sorted(OUT.iterdir()):
    if path.is_file():
        artifacts[path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}

report = {
    "python": str(PY),
    "cwd": str(REPO / "backend"),
    "target_lexicon": TARGET,
    "console_encoding": locale.getpreferredencoding(False),
    "before": before, "after": after,
    "db_unchanged": before["db"] == after["db"],
    "data_dir_unchanged": before["data_dir"] == after["data_dir"],
    "git_before": git_before, "git_after": git_after,
    "git_unchanged": git_before == git_after,
    "commands": records,
    "artifacts": artifacts,
}
(NOTES / "04-cli-run-log.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

lines = ["# 只读预演运行记录（CLI 输出与退出码）", ""]
lines.append(f"- 解释器：`{PY}`；工作目录：`{REPO / 'backend'}`")
lines.append("- 全部命令都是 `file_only_preview`：不打开应用数据库、不校验 schema、不写任何业务行。")
lines.append(f"- 生产库 `data/vocab.db` 前后一致：**{report['db_unchanged']}**"
             f"（{before['db'].get('sha256')}）")
lines.append(f"- `data/` 目录逐文件 SHA-256 前后一致：**{report['data_dir_unchanged']}**")
lines.append(f"- 主工作区 `git status --porcelain` 前后一致：**{report['git_unchanged']}**"
             f"（{git_before.strip() or '空'}）")
lines.append("")
for record in records:
    lines.append(f"## {record['name']}")
    lines.append("")
    lines.append("```")
    lines.append("python -m app.cli " + " ".join(record["argv"][3:]))
    lines.append(f"exit code: {record['exit_code']}")
    lines.append("```")
    lines.append("")
    if record.get("stdout_file"):
        lines.append(f"- stdout 写入 `out/{record['stdout_file']}`（{record['stdout_bytes']} 字节）")
        lines.append("")
        lines.append("```text")
        lines.append(record["stdout_head"].strip())
        lines.append("```")
    else:
        lines.append("```text")
        lines.append((record["stdout"] + record["stderr"]).strip())
        lines.append("```")
    lines.append("")
lines.append("## out/ 产物指纹")
lines.append("")
lines.append("| 文件 | 字节 | SHA-256 |")
lines.append("|---|---|---|")
for name, info in artifacts.items():
    lines.append(f"| `{name}` | {info['bytes']} | `{info['sha256']}` |")
(NOTES / "04-cli-run-log.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

print(json.dumps({
    "exit_codes": {r["name"]: r["exit_code"] for r in records},
    "db_unchanged": report["db_unchanged"],
    "data_dir_unchanged": report["data_dir_unchanged"],
    "git_unchanged": report["git_unchanged"],
}, ensure_ascii=False, indent=2))
sys.exit(0)
