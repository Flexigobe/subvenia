"""Fixtures globales para pytest."""

import os

os.environ["DATABASE_URL"] = "postgresql+psycopg://subvenciones:subvenciones@localhost:5432/subvenciones_test"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db import models  # noqa: F401

TEST_DB_URL = os.environ["DATABASE_URL"]
test_engine = create_engine(TEST_DB_URL, future=True)
TestSessionLocal = sessionmaker(bind=test_engine, autocommit=False, autoflush=False, future=True)


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """Crea todo el schema una vez al inicio."""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session() -> Session:
    """Sesión limpia para cada test: truncate al inicio."""
    session = TestSessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    try:
        yield session
    finally:
        session.close()
