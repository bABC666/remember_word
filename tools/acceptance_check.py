"""Human-visible acceptance check of the restored V1.1 application.

Reads through the running HTTP API (what a person would actually see) and then
compares it against the database directly. Writes only a JSON report into
data/recovery/.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"


def get(path: str):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    report: dict[str, object] = {}
    lines: list[str] = []

    def say(text: str = "") -> None:
        lines.append(text)
        print(text)

    dashboard = get("/api/dashboard")
    say("【今日概览 /api/dashboard】")
    say(f"  今日新词        : {dashboard['today_new']}")
    say(f"  待复习          : {dashboard['due_reviews']}")
    say(f"  weak 词         : {dashboard['weak_words']}")
    say(f"  阅读状态        : {dashboard['reading_status']}")
    say(f"  连续学习天数    : {dashboard['streak_days']}")
    report["dashboard"] = dashboard

    words = get("/api/words")
    say()
    say(f"【我的词库 /api/words】共 {words['total']} 个词")
    statuses: dict[str, int] = {}
    problems: list[str] = []
    for item in words["words"]:
        statuses[item["status"]] = statuses.get(item["status"], 0) + 1
        if not item["source_meanings"]:
            problems.append(f"{item['word']}: source_meanings 为空")
        if not item["anchor"]:
            problems.append(f"{item['word']}: anchor 为空")
        if not item["source_raw"]:
            problems.append(f"{item['word']}: source_raw 为空")
    say(f"  status 分布     : {statuses}")
    say("  内容完整性      : " + ("全部词条的释义 / anchor / 原文均非空" if not problems else str(problems)))
    say()
    say("  逐词核对：")
    for item in words["words"]:
        say(
            "    %-14s %-10s anchor=%-2d 释义=%d 原文=%-3d 复习成功=%d 失败=%d"
            % (
                item["word"],
                item["status"],
                len(item["anchor"]),
                len(item["source_meanings"]),
                len(item["source_raw"]),
                item["recall_success"],
                item["recall_fail"],
            )
        )
    report["words_total"] = words["total"]
    report["status_distribution"] = statuses
    report["content_problems"] = problems

    say()
    say("【单词详情 /api/words/{id}（复习历史与文章暴露）】")
    detail_summary = []
    for item in words["words"]:
        detail = get(f"/api/words/{item['id']}")
        detail_summary.append(
            {
                "word": detail["word"],
                "review_history": len(detail["review_history"]),
                "article_exposures": len(detail["article_exposures"]),
            }
        )
    total_reviews = sum(entry["review_history"] for entry in detail_summary)
    total_exposures = sum(entry["article_exposures"] for entry in detail_summary)
    with_history = [e for e in detail_summary if e["review_history"] or e["article_exposures"]]
    for entry in with_history:
        say(
            "    %-14s 复习历史=%d 文章暴露=%d"
            % (entry["word"], entry["review_history"], entry["article_exposures"])
        )
    say(f"  复习历史合计    : {total_reviews}")
    say(f"  文章暴露合计    : {total_exposures}")
    report["review_history_total"] = total_reviews
    report["exposure_total"] = total_exposures

    articles = get("/api/articles")
    say()
    say(f"【阅读 /api/articles】{len(articles)} 篇")
    for article in articles:
        detail = get(f"/api/articles/{article['id']}")
        say(f"    标题          : {detail['title']}")
        say(f"    正文字数      : {len(detail['content'])}")
        say(f"    目标词        : {len(detail['target_words'])} 个")
        say(f"    完成状态      : {detail['completed']}")
        say(f"    译文          : {len(detail.get('translation') or '')} 字")
        say(f"    测试词        : {len(detail.get('quiz_words') or [])} 个")
        say(f"    查词记录      : {len(detail.get('lookup_history') or [])} 条")
        report["article"] = {
            "id": article["id"],
            "title": detail["title"],
            "content_length": len(detail["content"]),
            "target_words": len(detail["target_words"]),
            "completed": detail["completed"],
            "translation_length": len(detail.get("translation") or ""),
            "quiz_words": len(detail.get("quiz_words") or []),
            "lookups": len(detail.get("lookup_history") or []),
        }

    imports = get("/api/imports")
    say()
    say(f"【导入批次 /api/imports】{len(imports)} 个")
    for batch in imports:
        detail = get(f"/api/imports/{batch['id']}")
        say(f"    批次 {detail['id']}  状态={detail['status']}  阶段={detail['stage']}")
        say(f"      OCR 原文长度  : {len(detail['raw_ocr_text'])}")
        say(f"      图片          : {len(detail['images'])} 张")
        for image in detail["images"]:
            exists = Path(image["file_path"]).exists()
            say(
                "        %s  %dx%d  OCR=%d 字  文件存在=%s"
                % (
                    image["original_name"],
                    image["width"] or 0,
                    image["height"] or 0,
                    len(image["ocr_text"]),
                    exists,
                )
            )
        say(f"      候选词条      : {len(detail['candidates'])} 个（已确认 {sum(1 for c in detail['candidates'] if c['confirmed'])}）")
        report["import"] = {
            "batch_id": detail["id"],
            "status": detail["status"],
            "images": len(detail["images"]),
            "candidates": len(detail["candidates"]),
            "confirmed": sum(1 for c in detail["candidates"] if c["confirmed"]),
            "ocr_text_length": len(detail["raw_ocr_text"]),
        }

    settings = get("/api/settings")
    say()
    say("【设置 /api/settings】")
    say(f"    每日新词      : {settings['daily_new_words']}")
    say(f"    文章长度      : {settings['article_length']}")
    say(f"    OCR 语言      : {settings['ocr_language']}")
    say(f"    DeepSeek 已配置: {settings['deepseek_api_key_configured']}")
    say(f"    模型          : {settings['deepseek_model_display']}")
    say(f"    PaddleOCR     : {settings['paddleocr_available']}")
    onboarding = get("/api/settings/onboarding")
    say(f"    首次说明已看过: {onboarding['seen']}  <- 该项在事故中丢失，需重新看一次")
    report["settings"] = settings
    report["onboarding_seen"] = onboarding["seen"]

    # Cross-check against the database file itself.
    connection = sqlite3.connect("data/vocab.db")
    counts = {
        table: connection.execute(f"select count(*) from {table}").fetchone()[0]
        for table in (
            "word",
            "review_event",
            "article",
            "article_word_exposure",
            "article_word_lookup",
            "import_batch",
            "import_image",
            "import_candidate",
            "history_event",
            "app_setting",
        )
    }
    revision = connection.execute("select version_num from alembic_version").fetchone()[0]
    tables = [
        row[0]
        for row in connection.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
        )
    ]
    connection.close()

    say()
    say("【数据库直查（与应用对照）】")
    say(f"    alembic revision: {revision}")
    say(f"    表数量          : {len(tables)}")
    say(f"    含 V1.2 表      : {sorted(set(tables) & {'user', 'user_session', 'lexicon', 'lexicon_entry', 'user_lexicon', 'user_word_state', 'user_settings'})}")
    for table, value in counts.items():
        say(f"    {table:<24} {value}")
    report["db_counts"] = counts
    report["db_revision"] = revision
    report["db_table_count"] = len(tables)
    report["v1_2_tables_present"] = sorted(
        set(tables)
        & {
            "user",
            "user_session",
            "lexicon",
            "lexicon_entry",
            "user_lexicon",
            "user_word_state",
            "user_settings",
        }
    )
    report["api_matches_db"] = {
        "words": words["total"] == counts["word"],
        "review_history": total_reviews == counts["review_event"],
        "exposures": total_exposures == counts["article_word_exposure"],
    }

    say()
    say("【一致性结论】")
    for key, value in report["api_matches_db"].items():
        say(f"    API 与数据库一致（{key}）: {value}")

    Path("data/recovery/acceptance-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    Path("data/recovery/acceptance-report.txt").write_text("\n".join(lines), encoding="utf-8")
    print()
    print("report: data/recovery/acceptance-report.json")


if __name__ == "__main__":
    main()
