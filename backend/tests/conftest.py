import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session


@pytest.fixture(scope="session", autouse=True)
def isolated_app_data(tmp_path_factory: pytest.TempPathFactory):
    previous = os.environ.get("VOCAB_DATA_DIR")
    os.environ["VOCAB_DATA_DIR"] = str(tmp_path_factory.mktemp("app-data"))
    yield
    if previous is None:
        os.environ.pop("VOCAB_DATA_DIR", None)
    else:
        os.environ["VOCAB_DATA_DIR"] = previous


@pytest.fixture(scope="session", autouse=True)
def app_schema(isolated_app_data):
    """Create the application schema inside the isolated data directory.

    The application no longer creates tables at startup (Alembic owns the
    schema), so tests that exercise the real ``app.db`` engine must build it
    explicitly. Pure service tests use the per-test ``session`` fixture instead.
    """
    import app.models  # noqa: F401
    from app.db import Base, engine

    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def session(tmp_path: Path) -> Session:
    import app.models  # noqa: F401
    from app.db import Base, make_engine

    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value
