"""Freeze a separate final package from pinned repair evidence; never overwrite it.

Download candidates contain separate per-source CSVs and matching statements.
The combined provenance ledger stays internal and is not in the download archive.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from pathlib import Path

from build_default_public_package import FIELDS, _source_position
from build_netem_exclusion_scenario import EXCLUDE
from resolve_default_gaps import load_pages

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "test-artifacts/netem-launch-decision-20261005"
DEFAULT = ROOT / "test-artifacts/netem-final-20261005/frozen"
REVIEW = {
    "build": "编纂的固定上游义项为软件编译；整条混合释义不能确认，留空。",
    "collect": "累的固定上游段没有义项说明；整条混合释义不能确认，留空。",
    "locate": "旧修订原文含位于指出，并非解析粘连；不补译，整条留空。",
    "outward": "旧修订原文含外服的，并非解析错误；不猜测纠字，整条留空。",
    "operation": "固定条目手术与军事义混列，手術组混列操作与手术；不能确认整条采用文本，留空。",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run(target: Path) -> dict:
    target = target.resolve()
    if not target.is_relative_to(ROOT / "test-artifacts") or target.exists():
        raise ValueError("freeze requires a new directory inside test-artifacts")
    index = json.loads((ROOT / "docs/NETEM-V2-LAUNCH-EVIDENCE-2026-10-05.json").read_text("utf-8"))
    # Lock every input used here against the previous committed evidence index.
    expected = index["files"]
    def pinned(path: Path) -> Path:
        key = path.relative_to(ROOT).as_posix()
        if sha(path) != expected[key]:
            raise ValueError(f"pinned input changed: {key}")
        return path

    candidate = pinned(AUDIT / "repaired-candidate-provenance.csv")
    repair = json.loads(pinned(AUDIT / "repair-results.json").read_text("utf-8"))
    assert (repair["changed_words"], repair["removed_segments"]) == (17, 29)
    rows = list(csv.DictReader(candidate.open(encoding="utf-8", newline="")))
    base_manifest = json.loads(pinned(AUDIT / "repaired-public-package/manifest.json").read_text("utf-8"))
    sample = json.loads(pinned(AUDIT / "review-evidence.json").read_text("utf-8"))
    sampled = {r["word"]: r for r in sample}
    assert len(EXCLUDE) == 13 and set(REVIEW) <= sampled.keys()
    excluded = EXCLUDE | REVIEW.keys()
    decisions = []
    for row in rows:
        if row["word"] in excluded:
            decisions.append({"word": row["word"], "source": row["meaning_source"],
                              "locator": row["source_locator"],
                              "reason": REVIEW.get(row["word"], "上一决策确定的 13 条问题释义"),
                              "action": "empty_whole_adopted_meaning"})
            row.update(meaning="", meaning_source="missing", source_locator="", source_detail="")
    assert len(decisions) == 18 and len(rows) == 5528
    counts = {name: sum(r["meaning_source"] == name for r in rows)
              for name in ("wikdict", "zhwiktionary", "missing")}
    assert counts == {"wikdict": 4814, "zhwiktionary": 637, "missing": 77}

    pages = {}
    for path in sorted((AUDIT / "upstream").glob("original-*.json")):
        for page in json.loads(pinned(path).read_bytes())["query"]["pages"]:
            for revision in page["revisions"]:
                pages[str(revision["revid"])] = revision["timestamp"]
    gap = ROOT / "test-artifacts/default-lexicon-gap-20261004"
    for path in [*sorted(gap.glob("pinned-*.json")), gap / "unresolved-titles.json", gap / "snapshot-manifest.json"]:
        pinned(path)
    frozen, titles = load_pages(gap)
    pages.update({str(p["oldid"]): p["timestamp"] for p in [*frozen.values(), *titles.values()]})
    statements = json.loads((ROOT / "docs/NETEM-FINAL-SOURCE-STATEMENTS-2026-10-05.json").read_text("utf-8"))
    target.mkdir(parents=True)
    package = target / "public-package"
    package.mkdir()
    values = {name: [] for name in statements}
    attribution_rows = []
    for row in rows:
        values["netem"].append({"word": row["word"], "meaning": "",
                                "source_position": "netem:rank:" + row["netem_rank"],
                                "source_revision": base_manifest["sources"][0]["revision"]["value"]})
        source = row["meaning_source"]
        if source == "missing":
            continue
        revision = row["source_locator"].split("#")[0].removeprefix("oldid:") if source == "zhwiktionary" else ""
        values[source].append({"word": row["word"], "meaning": row["meaning"],
                               "source_position": _source_position(row, json.loads(row["source_detail"])),
                               "source_revision": revision})
        if revision:
            attribution_rows.append({"word": row["word"], "oldid": revision,
                "revision_timestamp": pages[revision],
                "source_url": "https://zh.wiktionary.org/w/index.php?oldid=" + revision,
                "history_url": "https://zh.wiktionary.org/w/index.php?oldid=" + revision + "&action=history",
                "original_license": "CC-BY-SA-3.0" if pages[revision] < "2023-06-07" else "CC-BY-SA-4.0",
                "adapter_license": "CC-BY-SA-4.0"})
    for item in base_manifest["sources"]:
        key = item["id"]
        write_csv(package / item["file"], values[key], FIELDS)
        item["attribution"] = statements[key]
        prov = item["provenance"]
        prov["publisher"] = statements[key]["creators"]
        prov["license_id"] = "CC-BY-NC-SA-4.0" if key == "netem" else "CC-BY-SA-4.0"
        prov["use_scope"] = "负责人确认：个人与朋友学习，不超过 10 人，不商用；用户数据不对外共享。"
        prov["display_scope"] = "网页学习、词条及来源说明；允许词库下载与导出，随文件保留来源声明和各自许可。人数为本服务首发范围，不是对接收者许可权利的额外限制。"
        # A statement accompanies each CSV, even when separated from the archive.
        write_json(package / (Path(item["file"]).stem + ".SOURCE.json"), statements[key])
        body = "\n\n".join(["# " + item["file"] + " 来源声明", statements[key]["creators"],
            "许可：" + prov["license_id"] + " " + statements[key]["license_url"],
            "版本：" + statements[key]["snapshot_label"], statements[key]["modifications"],
            statements[key]["disclaimer"], *[f"[{x['label']}]({x['url']})" for x in statements[key]["links"]]])
        if key == "zhwiktionary":
            body += "\n\n逐词固定页面、历史与原许可路径见 zhwiktionary-attribution.csv；该表必须随 zhwiktionary.csv 一起提供。"
        elif key == "wikdict":
            body += "\n\n固定包 SHA-256：" + statements[key]["snapshot_sha256"]
        (package / (Path(item["file"]).stem + ".SOURCE.md")).write_text(body + "\n", encoding="utf-8")
    write_csv(package / "zhwiktionary-attribution.csv", attribution_rows, list(attribution_rows[0]))
    (package / "WIKDICT-ORIGINAL-NOTICE.ifo").write_bytes(pinned(AUDIT / "stardict.ifo").read_bytes())
    (package / "NETEM-LICENSE.txt").write_bytes(pinned(AUDIT / "upstream/netem-license.txt").read_bytes())
    write_json(package / "manifest.json", base_manifest)
    write_json(package / "decisions.json", {"format_version": 1, "decisions": []})
    (package / "SOURCES.md").write_text(
        "# NETEM 首发候选的来源与再分发\n\n"
        "netem-words.csv 的词头与排列：CC BY-NC-SA 4.0；wikdict.csv、zhwiktionary.csv 的抽取改编：CC BY-SA 4.0。"
        "三份文件是独立来源材料的集合，不能把 BY-SA 释义改称 NC，也不能把 NETEM 称为 BY-SA。"
        "提供或导出任一文件必须携带对应 SOURCE.md／SOURCE.json；维基文件另携逐词归属表；"
        "保留署名、原许可／免责和修改说明，改编依各自同要素 SA，不附加限制接收者许可权利的条款或技术措施。"
        "本项目不主张该集合的额外数据库专有权。内容条件已按项目定稿决策落实；本包仍为候选，未作生产发布。\n",
        encoding="utf-8")
    write_csv(target / "candidate-provenance.csv", rows, list(rows[0]))
    write_json(target / "five-word-review.json", {"reviewed_words": REVIEW, "excluded": decisions,
               "evidence_sha256": sha(AUDIT / "review-evidence.json"), "no_new_meanings": True})
    download = target / "download-candidate.zip"
    with zipfile.ZipFile(download, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package.iterdir()):
            info = zipfile.ZipInfo(path.name, date_time=(2026, 10, 5, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    hashes = {p.relative_to(target).as_posix(): sha(p) for p in sorted(target.rglob("*")) if p.is_file()}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result = {"status": "final_candidate_conditions_implemented", "production_published": False,
              "counts": counts, "words": 5528, "definitions": 5451, "empty": 77,
              "structure_repair": {"words": 17, "segments": 29}, "excluded_meanings": 18,
              "package_sha256": digest, "files": hashes}
    write_json(target / "fingerprints.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))
