from sqlalchemy.orm import Session


def test_word_survives_new_database_session(tmp_path) -> None:
    from app.db import Base, make_engine
    from app.models import Word

    engine = make_engine(f"sqlite:///{tmp_path / 'persist.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as first:
        first.add(Word(word="retain", source_meanings=["保留"], source_raw="retain v. 保留"))
        first.commit()
    with Session(engine) as second:
        assert second.query(Word).one().source_raw == "retain v. 保留"
