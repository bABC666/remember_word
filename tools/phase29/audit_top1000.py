"""Read a completed isolated top-1000 run through the real grouped word API."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import make_engine
from app.models import LexiconEntry, User
from app.services.userdata import get_or_create_word_state

EXPECTED = {
    "state": [("noun", ["状态", "国家"]), ("verb", ["声称"])],
    "light": [("noun", ["光"]), ("adj", ["轻"])],
    "charge": [("noun", ["电荷", "价格", "指控"])],
    "key": [("noun", ["钥匙"])],
    "performance": [("noun", ["表演", "执行", "性能"])],
    "play": [("verb", ["玩", "演奏", "播放"]), ("noun", ["剧"])],
    "device": [],
    "could": [],
}


def audit(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    if (not output.is_relative_to((root / "test-artifacts").resolve())
            or not output.name.startswith("phase29-top1000-")
            or not (output / "report.json").is_file()):
        raise ValueError("only a completed isolated top-1000 output may be audited")
    db = output / "vocab.db"
    engine = make_engine(f"sqlite:///{db.as_posix()}")
    with Session(engine) as session:
        actor = session.scalar(select(User).where(User.username == "phase29-ai-top1000"))
        entries = {word: session.scalar(select(LexiconEntry).where(
            LexiconEntry.normalized_word == word)) for word in EXPECTED}
        states = {word: get_or_create_word_state(session, actor, entry).id
                  for word, entry in entries.items()}
        actor_id = actor.id
        session.commit()
    from app.api.deps import get_current_user
    from app.db import get_session
    from app.main import app

    def isolated_session():
        with Session(engine) as current:
            yield current

    app.dependency_overrides[get_session] = isolated_session
    app.dependency_overrides[get_current_user] = lambda: User(
        id=actor_id, username="phase29-ai-top1000", role="admin", is_active=True)
    try:
        client = TestClient(app)
        groups = {}
        for word, state_id in states.items():
            response = client.get(f"/api/words/state/{state_id}")
            if response.status_code != 200:
                raise RuntimeError(f"API read failed for {word}: {response.status_code}")
            groups[word] = response.json()["concise_meanings"]
    finally:
        app.dependency_overrides.clear()
    compact = {word: [(group["pos_key"], [sense["text"] for sense in group["meanings"]])
                      for group in value] for word, value in groups.items()}
    if compact != EXPECTED:
        raise RuntimeError(f"grouped API drift: {compact}")
    for word in ("state", "light", "charge", "key"):
        if any(group["pos_source"] != "reviewer" or
               group["pos_source_label"] != "裁定者试判" for group in groups[word]):
            raise RuntimeError(f"WikDict POS was mislabelled as source heading: {word}")
    if "关键" in json.dumps(groups["key"], ensure_ascii=False):
        raise RuntimeError("unsupported key/crucial sense reached the API")
    result = {"api_groups": compact, "wikdict_pos_source": "reviewer",
              "unsupported_key_crucial_hidden": True,
              "candidate_and_no_source_hidden": ["device", "could"]}
    (output / "api-audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(ROOT, args.output), ensure_ascii=False, indent=2))
