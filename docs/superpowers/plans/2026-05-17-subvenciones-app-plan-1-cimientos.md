# Plan 1 — Cimientos y búsqueda core (subvenciones-app)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dejar la app funcional de punta a punta con búsqueda contra BDNS y ranking determinista, sin LLM ni enrichment ni alertas. Al terminar este plan, un usuario puede levantar la app en local, meter NIF + datos manualmente, ver subvenciones rankeadas y abrir el detalle.

**Architecture:** FastAPI server-rendered con Jinja2 + HTMX + Tailwind CDN. Postgres como base. APScheduler in-process descarga la BDNS una vez al día. Matching = filtro SQL + pre-rank determinista basado en match de CNAE, finalidad y proximidad a fecha de cierre.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Postgres 15, Jinja2, HTMX, Tailwind (CDN), APScheduler, httpx, pytest, ruff.

**Pre-requisitos antes de empezar:**
- Python 3.12 instalado.
- Postgres 15+ disponible localmente (Docker o instalación nativa). Si no tienes Docker, instalar `brew install postgresql@15 && brew services start postgresql@15`.
- No se necesita ninguna API key externa para este Plan 1. La BDNS es pública sin auth.

---

## Estructura de ficheros a crear en este Plan

```
subvenciones-app/
├── pyproject.toml                          # Task 1
├── .env.example                            # Task 3
├── app/
│   ├── __init__.py                         # Task 1
│   ├── main.py                             # Task 4
│   ├── config.py                           # Task 2
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py                      # Task 5
│   │   ├── base.py                         # Task 6
│   │   └── models.py                       # Task 6
│   ├── lib/
│   │   ├── __init__.py
│   │   ├── nif_validator.py                # Task 9
│   │   └── cnae_catalog.py                 # Task 10
│   ├── sync/
│   │   ├── __init__.py
│   │   ├── bdns_puller.py                  # Tasks 11-13
│   │   └── runner.py                       # Task 14
│   ├── matching/
│   │   ├── __init__.py
│   │   ├── filter.py                       # Task 15
│   │   └── service.py                      # Task 16
│   └── web/
│       ├── __init__.py
│       ├── routes_search.py                # Tasks 18-20
│       └── templates/
│           ├── base.html                   # Task 17
│           ├── home.html                   # Task 18
│           ├── results.html                # Task 19
│           └── subsidy_detail.html         # Task 20
├── data/
│   └── cnae_2009.json                      # Task 10
├── migrations/                             # Task 7 (Alembic init)
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial.py
├── alembic.ini                             # Task 7
└── tests/
    ├── __init__.py
    ├── conftest.py                         # Task 8
    ├── unit/
    │   ├── test_nif_validator.py           # Task 9
    │   ├── test_cnae_catalog.py            # Task 10
    │   ├── test_bdns_puller.py             # Tasks 11-13
    │   └── test_matching_filter.py         # Task 15
    ├── integration/
    │   └── test_search_flow.py             # Task 21
    └── fixtures/
        ├── bdns/
        │   └── page_sample.json            # Task 11
        └── cnae_sample.json
```

---

## Task 1: Bootstrap del proyecto + pyproject.toml

**Files:**
- Create: `pyproject.toml`
- Create: `app/__init__.py` (vacío)

- [ ] **Step 1: Crear `pyproject.toml`**

```toml
[project]
name = "subvenciones-app"
version = "0.1.0"
description = "Buscador de subvenciones para empresas españolas"
requires-python = ">=3.12"
dependencies = [
    "fastapi[standard]>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlalchemy>=2.0.36",
    "alembic>=1.13.3",
    "psycopg[binary]>=3.2.3",
    "pydantic>=2.9.2",
    "pydantic-settings>=2.6.0",
    "jinja2>=3.1.4",
    "httpx>=0.27.2",
    "apscheduler>=3.10.4",
    "python-multipart>=0.0.12",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.3",
    "pytest-asyncio>=0.24.0",
    "pytest-httpx>=0.32.0",
    "ruff>=0.7.0",
    "httpx>=0.27.2",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-v --tb=short"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP"]
```

- [ ] **Step 2: Crear `app/__init__.py` vacío**

```bash
mkdir -p app && touch app/__init__.py
```

- [ ] **Step 3: Crear venv e instalar dependencias**

```bash
cd /Users/victorgomez/Desktop/subvenciones-app
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Expected: instalación termina sin errores. Verificar con `python -c "import fastapi; print(fastapi.__version__)"`.

- [ ] **Step 4: Añadir `.venv/` y `*.egg-info/` al `.gitignore`** (ya existe el fichero, solo confirmar)

Verificar con: `cat .gitignore | grep -E "venv|egg-info"`. Si falta `*.egg-info/` añadirlo.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml app/__init__.py .gitignore
git -c commit.gpgsign=false commit -m "chore: bootstrap python project with FastAPI deps"
```

---

## Task 2: Settings con Pydantic

**Files:**
- Create: `app/config.py`

- [ ] **Step 1: Crear `app/config.py`**

```python
"""Settings centralizados leídos del entorno."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+psycopg://subvenciones:subvenciones@localhost:5432/subvenciones"

    # App
    base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    # BDNS
    bdns_base_url: str = "https://www.infosubvenciones.es/bdnstrans/api"
    bdns_page_size: int = 100
    bdns_sync_hour: int = 3  # 03:00
    bdns_sync_minute: int = 0

    # Matching
    matching_candidate_limit: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 2: Verificar que importa sin error**

```bash
python -c "from app.config import get_settings; print(get_settings().database_url)"
```

Expected: imprime la URL por defecto.

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git -c commit.gpgsign=false commit -m "feat(config): add pydantic Settings module"
```

---

## Task 3: Plantilla `.env.example`

**Files:**
- Create: `.env.example`

- [ ] **Step 1: Crear `.env.example`**

```env
# Database (local dev)
DATABASE_URL=postgresql+psycopg://subvenciones:subvenciones@localhost:5432/subvenciones

# App
BASE_URL=http://localhost:8000
LOG_LEVEL=INFO

# BDNS
BDNS_BASE_URL=https://www.infosubvenciones.es/bdnstrans/api
BDNS_PAGE_SIZE=100
BDNS_SYNC_HOUR=3
BDNS_SYNC_MINUTE=0

# Matching
MATCHING_CANDIDATE_LIMIT=30
```

- [ ] **Step 2: Crear `.env` local copiando del ejemplo (NO commitear)**

```bash
cp .env.example .env
```

Verificar que `.env` está en `.gitignore` (debería estar ya).

- [ ] **Step 3: Commit**

```bash
git add .env.example
git -c commit.gpgsign=false commit -m "chore: add .env.example template"
```

---

## Task 4: FastAPI app skeleton + /healthz

**Files:**
- Create: `app/main.py`
- Create: `tests/__init__.py`, `tests/unit/__init__.py`
- Create: `tests/unit/test_main.py`

- [ ] **Step 1: Escribir test que falla**

Crear `tests/__init__.py` y `tests/unit/__init__.py` vacíos. Luego:

```python
# tests/unit/test_main.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz_returns_200():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Ejecutar test para confirmar que falla**

```bash
pytest tests/unit/test_main.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 3: Implementar `app/main.py` mínimo**

```python
"""FastAPI entrypoint."""

from fastapi import FastAPI

app = FastAPI(title="Buscador de subvenciones")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 4: Ejecutar test, verificar PASS**

```bash
pytest tests/unit/test_main.py -v
```

Expected: PASS.

- [ ] **Step 5: Verificar que el servidor arranca a mano**

```bash
uvicorn app.main:app --reload --port 8000
```

En otra terminal: `curl http://localhost:8000/healthz` → debe devolver `{"status":"ok"}`. Parar el server con Ctrl-C.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/__init__.py tests/unit/__init__.py tests/unit/test_main.py
git -c commit.gpgsign=false commit -m "feat(web): add FastAPI app skeleton with /healthz"
```

---

## Task 5: DB session (SQLAlchemy)

**Files:**
- Create: `app/db/__init__.py` (vacío)
- Create: `app/db/session.py`

- [ ] **Step 1: Crear `app/db/__init__.py` vacío**

```bash
mkdir -p app/db && touch app/db/__init__.py
```

- [ ] **Step 2: Implementar `app/db/session.py`**

```python
"""SQLAlchemy engine y session factory."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a DB session and closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 3: Verificar import**

```bash
python -c "from app.db.session import engine, SessionLocal, get_db; print('ok')"
```

Expected: imprime "ok".

- [ ] **Step 4: Commit**

```bash
git add app/db/__init__.py app/db/session.py
git -c commit.gpgsign=false commit -m "feat(db): add SQLAlchemy engine and session factory"
```

---

## Task 6: Modelos SQLAlchemy de Plan 1

Solo creamos las tablas necesarias para Plan 1: `subvencion`, `search`, `search_result`. Las tablas de alerts las añadirá el Plan 3.

**Files:**
- Create: `app/db/base.py`
- Create: `app/db/models.py`

- [ ] **Step 1: Crear `app/db/base.py`**

```python
"""Base declarativa para SQLAlchemy."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

- [ ] **Step 2: Crear `app/db/models.py` con las 3 tablas**

```python
"""Modelos ORM para Plan 1: subvencion, search, search_result."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Subvencion(Base):
    __tablename__ = "subvencion"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(Enum("bdns", "eu", name="source_enum"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    organismo: Mapped[str | None] = mapped_column(Text)
    ambito: Mapped[str] = mapped_column(
        Enum("estatal", "autonomico", "local", "ue", name="ambito_enum"), nullable=False
    )
    ccaa: Mapped[str | None] = mapped_column(String(64))
    fecha_inicio: Mapped[date | None] = mapped_column(Date)
    fecha_fin: Mapped[date | None] = mapped_column(Date)
    importe_total: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    importe_max_beneficiario: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    porcentaje: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    beneficiarios: Mapped[dict | None] = mapped_column(JSONB)
    cnae_elegible: Mapped[list[str]] = mapped_column(ARRAY(String(8)), default=list, nullable=False)
    finalidad: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    enlace_oficial: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    estado: Mapped[str] = mapped_column(
        Enum("abierta", "cerrada", "proximamente", name="estado_enum"),
        nullable=False,
        default="abierta",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_subvencion_source_extid"),)


class Search(Base):
    __tablename__ = "search"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nif: Mapped[str] = mapped_column(String(16), nullable=False)
    razon_social: Mapped[str | None] = mapped_column(Text)
    cnae: Mapped[str] = mapped_column(String(8), nullable=False)
    tamano: Mapped[str] = mapped_column(
        Enum("micro", "pequena", "mediana", "grande", name="tamano_enum"), nullable=False
    )
    provincia: Mapped[str] = mapped_column(String(2), nullable=False)  # código INE 2 dígitos
    finalidad: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    results: Mapped[list[SearchResult]] = relationship(back_populates="search", cascade="all, delete-orphan")


class SearchResult(Base):
    __tablename__ = "search_result"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    search_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search.id", ondelete="CASCADE"), nullable=False
    )
    subvencion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subvencion.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    razon: Mapped[str | None] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    search: Mapped[Search] = relationship(back_populates="results")
    subvencion: Mapped[Subvencion] = relationship()
```

- [ ] **Step 3: Verificar que el módulo importa**

```bash
python -c "from app.db.models import Subvencion, Search, SearchResult; print('ok')"
```

Expected: imprime "ok".

- [ ] **Step 4: Commit**

```bash
git add app/db/base.py app/db/models.py
git -c commit.gpgsign=false commit -m "feat(db): add ORM models for subvencion, search, search_result"
```

---

## Task 7: Alembic init + migración inicial

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_initial.py`

- [ ] **Step 1: Inicializar Alembic**

```bash
cd /Users/victorgomez/Desktop/subvenciones-app
source .venv/bin/activate
alembic init migrations
```

Esto crea `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/`.

- [ ] **Step 2: Configurar `alembic.ini`**

Editar `alembic.ini` y cambiar la línea `sqlalchemy.url = ...` por:

```ini
sqlalchemy.url = postgresql+psycopg://subvenciones:subvenciones@localhost:5432/subvenciones
```

(Se sobreescribe en runtime con `app/config.py`, esto es solo el default offline.)

- [ ] **Step 3: Modificar `migrations/env.py` para leer la URL de `app/config.py` y registrar metadata**

Reemplazar el contenido de `migrations/env.py` por:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401  importa para registrar metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Crear la DB local de desarrollo**

Con Postgres corriendo:

```bash
psql postgres -c "CREATE USER subvenciones WITH PASSWORD 'subvenciones';"
psql postgres -c "CREATE DATABASE subvenciones OWNER subvenciones;"
```

Verificar conexión:
```bash
psql postgresql://subvenciones:subvenciones@localhost:5432/subvenciones -c "SELECT version();"
```

- [ ] **Step 5: Autogenerar la primera migración**

```bash
alembic revision --autogenerate -m "initial schema: subvencion, search, search_result"
```

Esto crea un fichero en `migrations/versions/<hash>_initial_schema_subvencion_search_search_result.py`. Renómbralo a `0001_initial.py` para que el orden sea claro:

```bash
mv migrations/versions/*_initial_schema*.py migrations/versions/0001_initial.py
```

Abre el fichero y verifica que contiene `op.create_table('subvencion', ...)`, `op.create_table('search', ...)`, `op.create_table('search_result', ...)` con sus tipos. Si Alembic omite algún campo (ej. ARRAY), añádelo a mano.

- [ ] **Step 6: Aplicar la migración**

```bash
alembic upgrade head
```

Expected: termina sin error. Verificar con `psql`:

```bash
psql postgresql://subvenciones:subvenciones@localhost:5432/subvenciones -c "\dt"
```

Expected: tablas `subvencion`, `search`, `search_result`, `alembic_version`.

- [ ] **Step 7: Commit**

```bash
git add alembic.ini migrations/
git -c commit.gpgsign=false commit -m "feat(db): alembic init + first migration (3 core tables)"
```

---

## Task 8: Test fixtures + conftest

**Files:**
- Create: `tests/conftest.py`

Configuramos pytest para usar una DB de test separada y resetearla entre tests.

- [ ] **Step 1: Crear DB de test**

```bash
psql postgres -c "CREATE DATABASE subvenciones_test OWNER subvenciones;"
```

- [ ] **Step 2: Crear `tests/conftest.py`**

```python
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
```

- [ ] **Step 3: Test de humo de la fixture**

Crear `tests/unit/test_db_fixture.py`:

```python
from app.db.models import Subvencion


def test_db_session_works(db_session):
    sub = Subvencion(
        source="bdns",
        external_id="TEST-001",
        titulo="Test subvención",
        ambito="estatal",
        cnae_elegible=[],
        finalidad=[],
    )
    db_session.add(sub)
    db_session.commit()
    assert sub.id is not None
```

Ejecutar:
```bash
pytest tests/unit/test_db_fixture.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/unit/test_db_fixture.py
git -c commit.gpgsign=false commit -m "test: pytest fixtures with isolated test DB"
```

---

## Task 9: NIF validator (TDD)

Validamos NIF persona física (DNI), CIF (empresa) y NIE (extranjeros) con sus respectivos checksums oficiales AEAT.

**Files:**
- Create: `app/lib/__init__.py` (vacío)
- Create: `app/lib/nif_validator.py`
- Create: `tests/unit/test_nif_validator.py`

- [ ] **Step 1: Crear `app/lib/__init__.py` vacío**

```bash
mkdir -p app/lib && touch app/lib/__init__.py
```

- [ ] **Step 2: Escribir tests que fallan**

```python
# tests/unit/test_nif_validator.py
import pytest

from app.lib.nif_validator import NifKind, validate_nif


class TestDNI:
    @pytest.mark.parametrize("nif", ["12345678Z", "00000000T", "99999999R"])
    def test_valid_dni(self, nif):
        result = validate_nif(nif)
        assert result.valid is True
        assert result.kind == NifKind.DNI
        assert result.normalized == nif

    @pytest.mark.parametrize("nif", ["12345678A", "00000000A", "99999999A"])
    def test_invalid_dni_checksum(self, nif):
        result = validate_nif(nif)
        assert result.valid is False


class TestNIE:
    @pytest.mark.parametrize("nif", ["X1234567L", "Y0000000Z", "Z9999999R"])
    def test_valid_nie(self, nif):
        result = validate_nif(nif)
        assert result.valid is True
        assert result.kind == NifKind.NIE

    def test_invalid_nie_checksum(self):
        result = validate_nif("X1234567A")
        assert result.valid is False


class TestCIF:
    @pytest.mark.parametrize("nif", ["A58818501", "B12345674", "P1234567D"])
    def test_valid_cif(self, nif):
        result = validate_nif(nif)
        assert result.valid is True
        assert result.kind == NifKind.CIF

    @pytest.mark.parametrize("nif", ["A58818500", "B12345670", "P1234567A"])
    def test_invalid_cif_checksum(self, nif):
        result = validate_nif(nif)
        assert result.valid is False


class TestEdgeCases:
    def test_lowercase_normalized_to_upper(self):
        result = validate_nif("12345678z")
        assert result.valid is True
        assert result.normalized == "12345678Z"

    def test_spaces_and_dashes_stripped(self):
        result = validate_nif(" 12345678-Z ")
        assert result.valid is True
        assert result.normalized == "12345678Z"

    def test_empty_string(self):
        result = validate_nif("")
        assert result.valid is False

    def test_wrong_format(self):
        result = validate_nif("ABCDEFGH")
        assert result.valid is False

    def test_too_short(self):
        result = validate_nif("1234567Z")
        assert result.valid is False
```

- [ ] **Step 3: Ejecutar tests para confirmar fallo**

```bash
pytest tests/unit/test_nif_validator.py -v
```

Expected: ImportError porque `app.lib.nif_validator` no existe.

- [ ] **Step 4: Implementar `app/lib/nif_validator.py`**

```python
"""Validador de NIF, CIF y NIE según especificación oficial AEAT."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_CIF_LETTERS = "JABCDEFGHI"  # letra de control para CIFs que la usan
_CIF_FIRST_LETTER_DIGIT_CHECK = set("KPQRSNW")  # CIFs con letra de control obligatoria
_CIF_FIRST_LETTER_NUMBER_CHECK = set("ABCDEFGHJUV")  # CIFs con dígito de control
_NIE_PREFIX_MAP = {"X": "0", "Y": "1", "Z": "2"}

_DNI_RE = re.compile(r"^\d{8}[A-Z]$")
_NIE_RE = re.compile(r"^[XYZ]\d{7}[A-Z]$")
_CIF_RE = re.compile(r"^[A-HJNPQRSUVW]\d{7}[0-9A-J]$")


class NifKind(str, Enum):
    DNI = "dni"
    NIE = "nie"
    CIF = "cif"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    kind: NifKind
    normalized: str


def _normalize(value: str) -> str:
    return re.sub(r"[\s\-]", "", value.upper())


def _validate_dni(value: str) -> bool:
    number = int(value[:8])
    expected_letter = _DNI_LETTERS[number % 23]
    return value[8] == expected_letter


def _validate_nie(value: str) -> bool:
    prefix = value[0]
    converted = _NIE_PREFIX_MAP[prefix] + value[1:8]
    number = int(converted)
    expected_letter = _DNI_LETTERS[number % 23]
    return value[8] == expected_letter


def _validate_cif(value: str) -> bool:
    first_letter = value[0]
    digits = value[1:8]
    check_char = value[8]

    even_sum = sum(int(d) for d in digits[1::2])
    odd_sum = 0
    for d in digits[::2]:
        n = int(d) * 2
        odd_sum += (n // 10) + (n % 10)
    total = even_sum + odd_sum
    control_digit = (10 - (total % 10)) % 10

    if first_letter in _CIF_FIRST_LETTER_DIGIT_CHECK:
        # Letra obligatoria
        return check_char == _CIF_LETTERS[control_digit]
    if first_letter in _CIF_FIRST_LETTER_NUMBER_CHECK:
        # Dígito o letra equivalente
        if check_char.isdigit():
            return int(check_char) == control_digit
        return check_char == _CIF_LETTERS[control_digit]
    # Letras N, W, etc se aceptan con letra
    return check_char == _CIF_LETTERS[control_digit]


def validate_nif(raw: str) -> ValidationResult:
    if not raw:
        return ValidationResult(False, NifKind.UNKNOWN, "")

    normalized = _normalize(raw)

    if _DNI_RE.match(normalized):
        valid = _validate_dni(normalized)
        return ValidationResult(valid, NifKind.DNI, normalized)

    if _NIE_RE.match(normalized):
        valid = _validate_nie(normalized)
        return ValidationResult(valid, NifKind.NIE, normalized)

    if _CIF_RE.match(normalized):
        valid = _validate_cif(normalized)
        return ValidationResult(valid, NifKind.CIF, normalized)

    return ValidationResult(False, NifKind.UNKNOWN, normalized)
```

- [ ] **Step 5: Ejecutar tests, verificar PASS**

```bash
pytest tests/unit/test_nif_validator.py -v
```

Expected: todos los tests PASS. Si algún caso del CIF falla, revisar la lógica de control_digit con la spec oficial AEAT (https://sede.agenciatributaria.gob.es/static_files/Sede/Procedimiento_ayuda/G314.pdf).

- [ ] **Step 6: Commit**

```bash
git add app/lib/__init__.py app/lib/nif_validator.py tests/unit/test_nif_validator.py
git -c commit.gpgsign=false commit -m "feat(lib): NIF/CIF/NIE validator with AEAT checksums"
```

---

## Task 10: CNAE catalog + autocomplete

CNAE-2009 tiene ~800 códigos. Descargamos el catálogo oficial una sola vez como JSON estático y exponemos búsqueda fuzzy por código o descripción.

**Files:**
- Create: `data/cnae_2009.json`
- Create: `app/lib/cnae_catalog.py`
- Create: `tests/unit/test_cnae_catalog.py`

- [ ] **Step 1: Descargar y normalizar el catálogo CNAE-2009**

El INE publica el listado oficial. Para no depender de scraping en runtime, lo bajamos una vez y lo guardamos en `data/cnae_2009.json` con esta estructura:

```json
[
  {"code": "0111", "description": "Cultivo de cereales (excepto arroz), leguminosas y semillas oleaginosas"},
  {"code": "0112", "description": "Cultivo de arroz"},
  ...
]
```

Fuente: https://www.ine.es/dyngs/INEbase/es/operacion.htm?c=Estadistica_C&cid=1254736177032&menu=ultiDatos&idp=1254735976614 → descargar Excel oficial CNAE-2009.

Comando para generarlo (script one-off, no se commitea):

```python
# scripts/build_cnae_json.py  (NO se commitea, solo se ejecuta una vez)
import json

import openpyxl  # pip install openpyxl

wb = openpyxl.load_workbook("cnae2009_oficial.xlsx")
sheet = wb.active
records = []
for row in sheet.iter_rows(min_row=2, values_only=True):
    code, description = row[0], row[1]
    if code and description:
        records.append({"code": str(code).strip(), "description": str(description).strip()})

with open("data/cnae_2009.json", "w", encoding="utf-8") as f:
    json.dump(records, f, ensure_ascii=False, indent=2)
```

Alternativa rápida si no quieres descargar el Excel ahora: usa el dataset libre y semi-actualizado en https://datos.gob.es/es/catalogo/ea0010587-cnae-2009 (mismos códigos oficiales en CSV).

Guardar el JSON final en `data/cnae_2009.json`. Debe contener entre 800 y 1.000 entradas.

- [ ] **Step 2: Escribir tests que fallan**

```python
# tests/unit/test_cnae_catalog.py
import pytest

from app.lib.cnae_catalog import get_by_code, search


def test_get_by_code_returns_description():
    result = get_by_code("6201")
    assert result is not None
    assert "ordenador" in result.description.lower() or "programación" in result.description.lower()


def test_get_by_code_unknown_returns_none():
    assert get_by_code("9999") is None


def test_search_by_partial_description_returns_matches():
    results = search("agricultura", limit=5)
    assert len(results) >= 1
    assert any("agricultura" in r.description.lower() or r.code.startswith("01") for r in results)


def test_search_by_code_prefix():
    results = search("62", limit=10)
    assert len(results) >= 1
    assert all(r.code.startswith("62") for r in results[:3])


def test_search_empty_returns_empty():
    assert search("", limit=10) == []


def test_search_respects_limit():
    results = search("ind", limit=3)
    assert len(results) <= 3
```

- [ ] **Step 3: Ejecutar para confirmar fallo**

```bash
pytest tests/unit/test_cnae_catalog.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implementar `app/lib/cnae_catalog.py`**

```python
"""Catálogo CNAE-2009 cargado desde JSON estático con búsqueda fuzzy ligera."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "cnae_2009.json"


@dataclass(frozen=True)
class CnaeEntry:
    code: str
    description: str


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _normalize(s: str) -> str:
    return _strip_accents(s).lower().strip()


@lru_cache(maxsize=1)
def _load_catalog() -> list[CnaeEntry]:
    with open(DATA_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return [CnaeEntry(code=r["code"], description=r["description"]) for r in raw]


@lru_cache(maxsize=1)
def _index_by_code() -> dict[str, CnaeEntry]:
    return {entry.code: entry for entry in _load_catalog()}


def get_by_code(code: str) -> CnaeEntry | None:
    return _index_by_code().get(code.strip())


def search(query: str, limit: int = 10) -> list[CnaeEntry]:
    q = _normalize(query)
    if not q:
        return []
    catalog = _load_catalog()

    # Match: prefijo de código > coincidencia en descripción
    prefix_matches = [e for e in catalog if e.code.startswith(q)]
    desc_matches = [e for e in catalog if q in _normalize(e.description) and not e.code.startswith(q)]
    return (prefix_matches + desc_matches)[:limit]
```

- [ ] **Step 5: Ejecutar tests, verificar PASS**

```bash
pytest tests/unit/test_cnae_catalog.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add data/cnae_2009.json app/lib/cnae_catalog.py tests/unit/test_cnae_catalog.py
git -c commit.gpgsign=false commit -m "feat(lib): CNAE-2009 catalog with fuzzy search"
```

---

## Task 11: BDNS puller — fetch HTTP

**Files:**
- Create: `app/sync/__init__.py` (vacío)
- Create: `app/sync/bdns_puller.py` (solo función `fetch_page` esta tarea)
- Create: `tests/fixtures/bdns/page_sample.json`
- Create: `tests/unit/test_bdns_puller.py`

**Nota previa sobre la API BDNS:** la API real está en https://www.infosubvenciones.es/bdnstrans/api. El endpoint principal de listado de convocatorias es `GET /convocatorias/busqueda` (puede variar). Antes de implementar la llamada real, abre el navegador en https://www.infosubvenciones.es/bdnstrans y desde DevTools → Network observa qué requests dispara una búsqueda. Anota el endpoint, los query params (`page`, `pageSize`, `fechaDesde`...) y el formato de respuesta. Si difiere de la asunción de abajo, ajusta los nombres antes de implementar.

- [ ] **Step 1: Crear módulo + fixture**

```bash
mkdir -p app/sync && touch app/sync/__init__.py
mkdir -p tests/fixtures/bdns
```

Crear `tests/fixtures/bdns/page_sample.json` con un payload sintético de ejemplo. **Tendrás que reemplazarlo con un payload real de la BDNS** la primera vez que la llames. De momento ponemos uno mínimo:

```json
{
  "page": 1,
  "totalPages": 2,
  "items": [
    {
      "id": "BDNS-001",
      "titulo": "Ayudas para digitalización de PYMEs",
      "organismo": "Ministerio de Industria",
      "ambito": "estatal",
      "ccaa": null,
      "fechaInicio": "2026-01-15",
      "fechaFin": "2026-12-31",
      "importeTotal": 1000000.00,
      "importeMaxBeneficiario": 12000.00,
      "porcentaje": null,
      "beneficiarios": {"tamanos": ["micro", "pequena", "mediana"]},
      "cnaeElegible": ["6201", "6202"],
      "finalidad": ["digitalizacion"],
      "descripcion": "Ayudas Kit Digital para PYMEs.",
      "enlaceOficial": "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-1"
    }
  ]
}
```

- [ ] **Step 2: Escribir test que falla**

```python
# tests/unit/test_bdns_puller.py
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bdns"


@pytest.mark.asyncio
async def test_fetch_page_returns_parsed_json(httpx_mock):
    payload = json.loads((FIXTURES / "page_sample.json").read_text())
    httpx_mock.add_response(
        url="https://www.infosubvenciones.es/bdnstrans/api/convocatorias/busqueda?page=1&pageSize=100&fechaDesde=2026-01-01",
        json=payload,
    )
    from app.sync.bdns_puller import fetch_page

    result = await fetch_page(page=1, since=date(2026, 1, 1))

    assert result["page"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == "BDNS-001"
```

- [ ] **Step 3: Ejecutar para confirmar fallo**

```bash
pytest tests/unit/test_bdns_puller.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implementar `fetch_page` en `app/sync/bdns_puller.py`**

```python
"""Cliente HTTP para la BDNS (Base de Datos Nacional de Subvenciones)."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from app.config import get_settings

settings = get_settings()


async def fetch_page(page: int, since: date, page_size: int | None = None) -> dict[str, Any]:
    """Descarga una página del listado de convocatorias BDNS.

    Args:
        page: número de página (1-indexed).
        since: fecha desde la que filtrar convocatorias modificadas.
        page_size: tamaño de página. Si None, usa el de config.

    Returns:
        Dict con claves `page`, `totalPages`, `items` (lista de convocatorias en bruto).

    Raises:
        httpx.HTTPStatusError: si el servidor responde con >= 400.
    """
    size = page_size or settings.bdns_page_size
    url = f"{settings.bdns_base_url}/convocatorias/busqueda"
    params = {
        "page": page,
        "pageSize": size,
        "fechaDesde": since.isoformat(),
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()
```

- [ ] **Step 5: Ejecutar test, verificar PASS**

```bash
pytest tests/unit/test_bdns_puller.py::test_fetch_page_returns_parsed_json -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/sync/__init__.py app/sync/bdns_puller.py tests/fixtures/bdns/page_sample.json tests/unit/test_bdns_puller.py
git -c commit.gpgsign=false commit -m "feat(sync): BDNS HTTP fetch_page with pagination"
```

---

## Task 12: BDNS puller — parse a modelo

**Files:**
- Modify: `app/sync/bdns_puller.py` (añadir `parse_item`)
- Modify: `tests/unit/test_bdns_puller.py` (añadir tests)

- [ ] **Step 1: Escribir test que falla**

Añadir al final de `tests/unit/test_bdns_puller.py`:

```python
from datetime import date as date_t


def test_parse_item_maps_all_fields():
    from app.sync.bdns_puller import parse_item

    raw = {
        "id": "BDNS-001",
        "titulo": "Ayudas digitalización",
        "organismo": "Ministerio",
        "ambito": "estatal",
        "ccaa": None,
        "fechaInicio": "2026-01-15",
        "fechaFin": "2026-12-31",
        "importeTotal": 1000000.00,
        "importeMaxBeneficiario": 12000.00,
        "porcentaje": None,
        "beneficiarios": {"tamanos": ["micro", "pequena"]},
        "cnaeElegible": ["6201"],
        "finalidad": ["digitalizacion"],
        "descripcion": "desc",
        "enlaceOficial": "https://boe.es/...",
    }

    parsed = parse_item(raw)

    assert parsed["source"] == "bdns"
    assert parsed["external_id"] == "BDNS-001"
    assert parsed["titulo"] == "Ayudas digitalización"
    assert parsed["ambito"] == "estatal"
    assert parsed["fecha_inicio"] == date_t(2026, 1, 15)
    assert parsed["fecha_fin"] == date_t(2026, 12, 31)
    assert parsed["importe_max_beneficiario"] == 12000.00
    assert parsed["cnae_elegible"] == ["6201"]
    assert parsed["finalidad"] == ["digitalizacion"]
    assert parsed["raw_payload"] == raw


def test_parse_item_handles_missing_optional_fields():
    from app.sync.bdns_puller import parse_item

    raw = {
        "id": "BDNS-002",
        "titulo": "Test",
        "ambito": "autonomico",
    }

    parsed = parse_item(raw)

    assert parsed["fecha_inicio"] is None
    assert parsed["importe_total"] is None
    assert parsed["cnae_elegible"] == []
    assert parsed["finalidad"] == []
```

- [ ] **Step 2: Ejecutar para confirmar fallo**

```bash
pytest tests/unit/test_bdns_puller.py -v
```

Expected: 2 tests fallan por ImportError en `parse_item`.

- [ ] **Step 3: Implementar `parse_item` en `app/sync/bdns_puller.py`**

Añadir al final del fichero:

```python
from datetime import date as date_t


def _parse_date(value: str | None) -> date_t | None:
    if not value:
        return None
    return date_t.fromisoformat(value)


def parse_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Mapea un item bruto de BDNS al formato de nuestro modelo Subvencion."""
    return {
        "source": "bdns",
        "external_id": str(raw["id"]),
        "titulo": raw.get("titulo", ""),
        "organismo": raw.get("organismo"),
        "ambito": raw.get("ambito", "estatal"),
        "ccaa": raw.get("ccaa"),
        "fecha_inicio": _parse_date(raw.get("fechaInicio")),
        "fecha_fin": _parse_date(raw.get("fechaFin")),
        "importe_total": raw.get("importeTotal"),
        "importe_max_beneficiario": raw.get("importeMaxBeneficiario"),
        "porcentaje": raw.get("porcentaje"),
        "beneficiarios": raw.get("beneficiarios"),
        "cnae_elegible": raw.get("cnaeElegible") or [],
        "finalidad": raw.get("finalidad") or [],
        "descripcion": raw.get("descripcion"),
        "enlace_oficial": raw.get("enlaceOficial"),
        "raw_payload": raw,
    }
```

- [ ] **Step 4: Ejecutar tests, verificar PASS**

```bash
pytest tests/unit/test_bdns_puller.py -v
```

Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sync/bdns_puller.py tests/unit/test_bdns_puller.py
git -c commit.gpgsign=false commit -m "feat(sync): BDNS parse_item maps raw payload to model fields"
```

---

## Task 13: BDNS puller — upsert en DB + función `sync_all`

**Files:**
- Modify: `app/sync/bdns_puller.py` (añadir `upsert_subvencion` y `sync_all`)
- Modify: `tests/unit/test_bdns_puller.py`

- [ ] **Step 1: Escribir test que falla**

Añadir al final de `tests/unit/test_bdns_puller.py`:

```python
from sqlalchemy import select

from app.db.models import Subvencion


def test_upsert_inserts_new_subvencion(db_session):
    from app.sync.bdns_puller import upsert_subvencion

    parsed = {
        "source": "bdns",
        "external_id": "BDNS-NEW",
        "titulo": "Nueva ayuda",
        "ambito": "estatal",
        "ccaa": None,
        "fecha_inicio": None,
        "fecha_fin": None,
        "importe_total": None,
        "importe_max_beneficiario": None,
        "porcentaje": None,
        "beneficiarios": None,
        "cnae_elegible": ["6201"],
        "finalidad": ["digitalizacion"],
        "descripcion": None,
        "enlace_oficial": None,
        "raw_payload": {"id": "BDNS-NEW"},
        "organismo": None,
    }

    created = upsert_subvencion(db_session, parsed)
    db_session.commit()

    rows = db_session.execute(select(Subvencion).where(Subvencion.external_id == "BDNS-NEW")).all()
    assert len(rows) == 1
    assert created is True


def test_upsert_updates_existing(db_session):
    from app.sync.bdns_puller import upsert_subvencion

    parsed = {
        "source": "bdns",
        "external_id": "BDNS-DUPE",
        "titulo": "Original",
        "ambito": "estatal",
        "ccaa": None,
        "fecha_inicio": None,
        "fecha_fin": None,
        "importe_total": None,
        "importe_max_beneficiario": None,
        "porcentaje": None,
        "beneficiarios": None,
        "cnae_elegible": [],
        "finalidad": [],
        "descripcion": None,
        "enlace_oficial": None,
        "raw_payload": {},
        "organismo": None,
    }

    upsert_subvencion(db_session, parsed)
    db_session.commit()

    parsed["titulo"] = "Modificado"
    created = upsert_subvencion(db_session, parsed)
    db_session.commit()

    assert created is False
    row = db_session.execute(select(Subvencion).where(Subvencion.external_id == "BDNS-DUPE")).scalar_one()
    assert row.titulo == "Modificado"
```

- [ ] **Step 2: Ejecutar tests, confirmar fallo**

```bash
pytest tests/unit/test_bdns_puller.py -v
```

Expected: 2 nuevos tests fallan.

- [ ] **Step 3: Implementar `upsert_subvencion` y `sync_all`**

Añadir al final de `app/sync/bdns_puller.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Subvencion


def upsert_subvencion(session: Session, parsed: dict[str, Any]) -> bool:
    """Inserta o actualiza una subvención por (source, external_id).

    Returns:
        True si se creó nueva, False si se actualizó existente.
    """
    existing = session.execute(
        select(Subvencion).where(
            Subvencion.source == parsed["source"],
            Subvencion.external_id == parsed["external_id"],
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(Subvencion(**parsed))
        return True

    for key, value in parsed.items():
        setattr(existing, key, value)
    return False


async def sync_all(session: Session, since: date) -> dict[str, int]:
    """Descarga todas las páginas BDNS desde `since` y hace upsert.

    Returns:
        {"created": N, "updated": M, "total": N+M}
    """
    created = 0
    updated = 0
    page = 1
    while True:
        payload = await fetch_page(page=page, since=since)
        items = payload.get("items", [])
        if not items:
            break
        for raw in items:
            parsed = parse_item(raw)
            if upsert_subvencion(session, parsed):
                created += 1
            else:
                updated += 1
        session.commit()
        total_pages = payload.get("totalPages", 1)
        if page >= total_pages:
            break
        page += 1
    return {"created": created, "updated": updated, "total": created + updated}
```

- [ ] **Step 4: Ejecutar tests, verificar PASS**

```bash
pytest tests/unit/test_bdns_puller.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/sync/bdns_puller.py tests/unit/test_bdns_puller.py
git -c commit.gpgsign=false commit -m "feat(sync): BDNS upsert and sync_all paginated"
```

---

## Task 14: APScheduler runner con cron diario

**Files:**
- Create: `app/sync/runner.py`
- Modify: `app/main.py` (lifespan que arranca/para el scheduler)

- [ ] **Step 1: Crear `app/sync/runner.py`**

```python
"""Cron in-process con APScheduler."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db.session import SessionLocal
from app.sync.bdns_puller import sync_all

logger = logging.getLogger(__name__)


async def run_bdns_sync() -> None:
    """Tarea: descarga últimos 14 días de BDNS y aplica."""
    settings = get_settings()
    since = date.today() - timedelta(days=14)
    logger.info("Starting BDNS sync since %s", since)
    with SessionLocal() as session:
        stats = await sync_all(session, since=since)
    logger.info(
        "BDNS sync done: created=%d updated=%d total=%d",
        stats["created"],
        stats["updated"],
        stats["total"],
    )


def build_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone="Europe/Madrid")
    scheduler.add_job(
        run_bdns_sync,
        CronTrigger(hour=settings.bdns_sync_hour, minute=settings.bdns_sync_minute),
        id="bdns_sync",
        replace_existing=True,
    )
    return scheduler
```

- [ ] **Step 2: Conectar el scheduler en `app/main.py`**

Reemplazar `app/main.py` por:

```python
"""FastAPI entrypoint con scheduler in-process."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.sync.runner import build_scheduler

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Scheduler started")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


app = FastAPI(title="Buscador de subvenciones", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 3: Verificar que la app arranca con el scheduler**

```bash
uvicorn app.main:app --port 8000
```

Expected: en logs `Scheduler started` y luego `Application startup complete.` Parar con Ctrl-C; debe loguear `Scheduler stopped`.

- [ ] **Step 4: Ejecutar tests existentes para verificar que el scheduler no rompe el TestClient**

```bash
pytest tests/unit/test_main.py -v
```

Expected: PASS (TestClient gestiona el lifespan automáticamente).

- [ ] **Step 5: Commit**

```bash
git add app/sync/runner.py app/main.py
git -c commit.gpgsign=false commit -m "feat(sync): APScheduler with daily BDNS cron at 03:00"
```

---

## Task 15: Matching SQL filter + pre-rank determinista (TDD)

Filtro determinista que reduce las 80k subvenciones de la BDNS a un top 30 ranqueado por relevancia.

**Files:**
- Create: `app/matching/__init__.py` (vacío)
- Create: `app/matching/filter.py`
- Create: `tests/unit/test_matching_filter.py`

- [ ] **Step 1: Crear `app/matching/__init__.py` vacío**

```bash
mkdir -p app/matching && touch app/matching/__init__.py
```

- [ ] **Step 2: Definir el dataclass de perfil de empresa y escribir tests**

```python
# tests/unit/test_matching_filter.py
from datetime import date, timedelta

import pytest

from app.db.models import Subvencion


@pytest.fixture
def perfil_pyme_digital():
    from app.matching.filter import EmpresaProfile

    return EmpresaProfile(
        cnae="6201",
        tamano="pequena",
        provincia="08",  # Barcelona → CCAA Cataluña
        finalidad=["digitalizacion"],
    )


def _make_subvencion(**kwargs):
    defaults = dict(
        source="bdns",
        external_id=f"TEST-{kwargs.get('external_id', '001')}",
        titulo="Test",
        ambito="estatal",
        cnae_elegible=[],
        finalidad=[],
        estado="abierta",
        fecha_fin=date.today() + timedelta(days=60),
        beneficiarios={"tamanos": ["micro", "pequena", "mediana", "grande"]},
    )
    defaults.update(kwargs)
    return Subvencion(**defaults)


def test_filter_excludes_cerradas(db_session, perfil_pyme_digital):
    from app.matching.filter import find_candidates

    db_session.add(_make_subvencion(external_id="A", estado="cerrada", finalidad=["digitalizacion"], cnae_elegible=["6201"]))
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)
    assert len(results) == 0


def test_filter_excludes_cnae_no_compatible(db_session, perfil_pyme_digital):
    from app.matching.filter import find_candidates

    db_session.add(_make_subvencion(external_id="A", cnae_elegible=["1010"], finalidad=["digitalizacion"]))
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)
    assert len(results) == 0


def test_filter_includes_when_cnae_elegible_empty(db_session, perfil_pyme_digital):
    from app.matching.filter import find_candidates

    db_session.add(_make_subvencion(external_id="A", cnae_elegible=[], finalidad=["digitalizacion"]))
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)
    assert len(results) == 1


def test_filter_excludes_when_finalidad_no_solapa(db_session, perfil_pyme_digital):
    from app.matching.filter import find_candidates

    db_session.add(_make_subvencion(external_id="A", cnae_elegible=["6201"], finalidad=["contratacion"]))
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)
    assert len(results) == 0


def test_filter_ranks_by_match_quality(db_session, perfil_pyme_digital):
    from app.matching.filter import find_candidates

    # alta_relevancia: cnae exacto + finalidad exacta + cerca de cierre
    alta = _make_subvencion(
        external_id="ALTA",
        titulo="Match perfecto",
        cnae_elegible=["6201"],
        finalidad=["digitalizacion"],
        fecha_fin=date.today() + timedelta(days=10),
    )
    # media: cnae genérico (vacío) + finalidad exacta + lejos
    media = _make_subvencion(
        external_id="MEDIA",
        titulo="Match medio",
        cnae_elegible=[],
        finalidad=["digitalizacion"],
        fecha_fin=date.today() + timedelta(days=120),
    )
    db_session.add_all([alta, media])
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)

    assert len(results) == 2
    assert results[0].subvencion.external_id == "TEST-ALTA"
    assert results[0].score > results[1].score


def test_filter_respects_limit(db_session, perfil_pyme_digital):
    from app.matching.filter import find_candidates

    for i in range(35):
        db_session.add(
            _make_subvencion(
                external_id=f"S{i:03d}",
                cnae_elegible=[],
                finalidad=["digitalizacion"],
            )
        )
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)
    assert len(results) == 30
```

- [ ] **Step 3: Confirmar fallo**

```bash
pytest tests/unit/test_matching_filter.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implementar `app/matching/filter.py`**

```python
"""Filtro SQL + pre-ranking determinista para candidatos de subvención."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Subvencion

# Mapeo simplificado provincia INE → CCAA
_PROVINCIA_TO_CCAA: dict[str, str] = {
    "01": "PV", "02": "CM", "03": "VC", "04": "AN", "05": "CL", "06": "EX",
    "07": "IB", "08": "CT", "09": "CL", "10": "EX", "11": "AN", "12": "VC",
    "13": "CM", "14": "AN", "15": "GA", "16": "CM", "17": "CT", "18": "AN",
    "19": "CM", "20": "PV", "21": "AN", "22": "AR", "23": "AN", "24": "CL",
    "25": "CT", "26": "RI", "27": "GA", "28": "MD", "29": "AN", "30": "MC",
    "31": "NC", "32": "GA", "33": "AS", "34": "CL", "35": "CN", "36": "GA",
    "37": "CL", "38": "CN", "39": "CB", "40": "CL", "41": "AN", "42": "CL",
    "43": "CT", "44": "AR", "45": "CM", "46": "VC", "47": "CL", "48": "PV",
    "49": "CL", "50": "AR", "51": "CE", "52": "ML",
}


@dataclass(frozen=True)
class EmpresaProfile:
    cnae: str
    tamano: str  # micro|pequena|mediana|grande
    provincia: str  # código INE 2 dígitos
    finalidad: list[str] = field(default_factory=list)

    @property
    def ccaa(self) -> str | None:
        return _PROVINCIA_TO_CCAA.get(self.provincia)


@dataclass(frozen=True)
class Candidate:
    subvencion: Subvencion
    score: int  # 0-100


def _compute_score(sub: Subvencion, perfil: EmpresaProfile) -> int:
    """Score determinista 0-100 basado en:
    - CNAE exacto: +40 ; CNAE genérico (lista vacía): +20
    - Finalidad solapada (cualquiera): +30
    - Cercanía a fecha_fin: hasta +20 (más cerca = más score)
    - Tamaño elegible: +10
    """
    score = 0

    if perfil.cnae in (sub.cnae_elegible or []):
        score += 40
    elif not sub.cnae_elegible:
        score += 20

    if set(perfil.finalidad) & set(sub.finalidad or []):
        score += 30

    if sub.fecha_fin:
        days_to_end = (sub.fecha_fin - date.today()).days
        if days_to_end >= 0:
            # Más cerca = más urgente y normalmente mejor priorizarlo
            urgency = max(0, 20 - (days_to_end // 7))  # 20 si <1 semana, baja con el tiempo
            score += min(20, urgency)

    benef = sub.beneficiarios or {}
    if perfil.tamano in benef.get("tamanos", []):
        score += 10

    return min(100, max(0, score))


def find_candidates(session: Session, perfil: EmpresaProfile, limit: int = 30) -> list[Candidate]:
    """Filtra y pre-rankea las subvenciones más relevantes para `perfil`.

    Filtros SQL aplicados:
    - estado = 'abierta'
    - fecha_fin >= hoy (o NULL)
    - cnae_elegible contiene el CNAE del perfil O está vacío
    - finalidad solapa con la del perfil
    - ámbito 'estatal' o 'ue' o (ámbito autonómico y CCAA coincide)
    """
    stmt = select(Subvencion).where(Subvencion.estado == "abierta")

    today = date.today()
    stmt = stmt.where((Subvencion.fecha_fin.is_(None)) | (Subvencion.fecha_fin >= today))

    # CNAE
    stmt = stmt.where(
        (Subvencion.cnae_elegible.contains([perfil.cnae])) | (Subvencion.cnae_elegible == [])
    )

    # Finalidad (al menos una en común)
    if perfil.finalidad:
        stmt = stmt.where(Subvencion.finalidad.overlap(perfil.finalidad))

    # Ámbito
    ccaa = perfil.ccaa
    if ccaa:
        stmt = stmt.where(
            (Subvencion.ambito.in_(["estatal", "ue"])) | (Subvencion.ccaa == ccaa)
        )
    else:
        stmt = stmt.where(Subvencion.ambito.in_(["estatal", "ue"]))

    rows = session.execute(stmt.limit(500)).scalars().all()

    candidates = [Candidate(subvencion=sub, score=_compute_score(sub, perfil)) for sub in rows]
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]
```

- [ ] **Step 5: Ejecutar tests, verificar PASS**

```bash
pytest tests/unit/test_matching_filter.py -v
```

Expected: todos los tests PASS. Si alguno falla por la query SQL (operadores `contains`/`overlap` en SQLAlchemy ARRAY), revisa la documentación de SQLAlchemy 2.x para postgres ARRAY operators.

- [ ] **Step 6: Commit**

```bash
git add app/matching/__init__.py app/matching/filter.py tests/unit/test_matching_filter.py
git -c commit.gpgsign=false commit -m "feat(matching): SQL filter + deterministic pre-rank"
```

---

## Task 16: Matching service orchestrator

Interfaz fina sobre `filter.find_candidates` que envuelve los resultados con campo `razon` (vacío en Plan 1; será relleno por el LLM en Plan 2).

**Files:**
- Create: `app/matching/service.py`

- [ ] **Step 1: Crear `app/matching/service.py`**

```python
"""Servicio de matching: orquesta filter + (en futuro) LLM scorer."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Subvencion
from app.matching.filter import Candidate, EmpresaProfile, find_candidates


@dataclass(frozen=True)
class RankedResult:
    subvencion: Subvencion
    score: int
    razon: str | None  # Plan 2 lo llenará con el LLM
    rank: int


def rank_for(session: Session, perfil: EmpresaProfile, limit: int = 30) -> list[RankedResult]:
    candidates: list[Candidate] = find_candidates(session, perfil, limit=limit)
    return [
        RankedResult(subvencion=c.subvencion, score=c.score, razon=None, rank=i + 1)
        for i, c in enumerate(candidates)
    ]
```

- [ ] **Step 2: Test ligero**

Crear `tests/unit/test_matching_service.py`:

```python
from datetime import date, timedelta

from app.db.models import Subvencion
from app.matching.filter import EmpresaProfile
from app.matching.service import rank_for


def test_rank_for_returns_ranked_results(db_session):
    db_session.add(
        Subvencion(
            source="bdns",
            external_id="X1",
            titulo="Match",
            ambito="estatal",
            cnae_elegible=["6201"],
            finalidad=["digitalizacion"],
            estado="abierta",
            fecha_fin=date.today() + timedelta(days=30),
            beneficiarios={"tamanos": ["pequena"]},
        )
    )
    db_session.commit()

    perfil = EmpresaProfile(cnae="6201", tamano="pequena", provincia="08", finalidad=["digitalizacion"])
    results = rank_for(db_session, perfil, limit=10)

    assert len(results) == 1
    assert results[0].rank == 1
    assert results[0].score > 0
    assert results[0].razon is None
```

```bash
pytest tests/unit/test_matching_service.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add app/matching/service.py tests/unit/test_matching_service.py
git -c commit.gpgsign=false commit -m "feat(matching): rank_for service wrapping filter"
```

---

## Task 17: Templates base con Tailwind (CDN)

Para MVP usamos Tailwind via CDN: cero build step. En Plan 4 se puede mover a Tailwind compilado si interesa.

**Files:**
- Create: `app/web/__init__.py` (vacío)
- Create: `app/web/templates/base.html`

- [ ] **Step 1: Crear `app/web/__init__.py` vacío**

```bash
mkdir -p app/web/templates && touch app/web/__init__.py
```

- [ ] **Step 2: Crear `app/web/templates/base.html`**

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}Buscador de subvenciones{% endblock %}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/htmx.org@2.0.3"></script>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  </style>
</head>
<body class="bg-gray-50 text-gray-900 min-h-screen">
  <header class="bg-white border-b border-gray-200">
    <div class="max-w-5xl mx-auto px-6 py-4 flex items-center justify-between">
      <a href="/" class="font-bold text-lg">Buscador de subvenciones</a>
      <span class="text-xs text-gray-500">España · BDNS oficial</span>
    </div>
  </header>
  <main class="max-w-5xl mx-auto px-6 py-8">
    {% block content %}{% endblock %}
  </main>
  <footer class="max-w-5xl mx-auto px-6 py-8 text-center text-xs text-gray-500">
    Información orientativa. Consulta siempre la convocatoria oficial.
  </footer>
</body>
</html>
```

- [ ] **Step 3: No hay test de plantilla (se prueba en Tasks 18-20). Commit ya**

```bash
git add app/web/__init__.py app/web/templates/base.html
git -c commit.gpgsign=false commit -m "feat(web): Jinja2 base template with Tailwind + HTMX via CDN"
```

---

## Task 18: Home route (GET /) + formulario

**Files:**
- Create: `app/web/routes_search.py`
- Create: `app/web/templates/home.html`
- Modify: `app/main.py` (montar router + Jinja2)
- Create: `tests/unit/test_routes_search.py`

- [ ] **Step 1: Escribir test que falla**

```python
# tests/unit/test_routes_search.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_home_returns_form():
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "NIF" in html or "nif" in html
    assert "tamaño" in html.lower() or "tamano" in html.lower()
    assert 'name="finalidad"' in html or "finalidad" in html.lower()
```

- [ ] **Step 2: Confirmar fallo**

```bash
pytest tests/unit/test_routes_search.py::test_home_returns_form -v
```

Expected: 404 (la ruta no existe).

- [ ] **Step 3: Crear `app/web/templates/home.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="text-center mb-8">
  <h1 class="text-3xl font-bold mb-2">¿Qué subvenciones puede pedir tu empresa?</h1>
  <p class="text-gray-600">Buscamos en la BDNS (Base de Datos Nacional de Subvenciones) las convocatorias abiertas que encajan con tu perfil.</p>
</div>

<form method="post" action="/search" class="bg-white rounded-2xl border border-gray-200 p-6 space-y-6">
  <section>
    <h2 class="text-xs font-semibold text-gray-500 uppercase mb-3">① Tu empresa</h2>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div>
        <label class="block text-sm mb-1">NIF / CIF</label>
        <input type="text" name="nif" required pattern=".{8,10}" placeholder="B12345674"
               class="w-full border border-gray-300 rounded px-3 py-2">
      </div>
      <div>
        <label class="block text-sm mb-1">Razón social <span class="text-gray-400 text-xs">(opcional)</span></label>
        <input type="text" name="razon_social" class="w-full border border-gray-300 rounded px-3 py-2">
      </div>
      <div>
        <label class="block text-sm mb-1">CNAE (código de actividad)</label>
        <input type="text" name="cnae" required pattern="\d{3,4}" placeholder="6201"
               class="w-full border border-gray-300 rounded px-3 py-2">
        <p class="text-xs text-gray-500 mt-1">Catálogo CNAE-2009. Busca en <a class="underline" target="_blank" href="https://www.ine.es/dyngs/INEbase/es/operacion.htm?c=Estadistica_C&cid=1254736177032">ine.es</a> si no lo sabes.</p>
      </div>
      <div>
        <label class="block text-sm mb-1">Tamaño de empresa</label>
        <select name="tamano" required class="w-full border border-gray-300 rounded px-3 py-2">
          <option value="micro">Microempresa (&lt;10 empleados)</option>
          <option value="pequena">Pequeña (10-49)</option>
          <option value="mediana">Mediana (50-249)</option>
          <option value="grande">Grande (250+)</option>
        </select>
      </div>
      <div class="md:col-span-2">
        <label class="block text-sm mb-1">Provincia</label>
        <select name="provincia" required class="w-full border border-gray-300 rounded px-3 py-2">
          {% for code, name in provincias %}
            <option value="{{ code }}">{{ code }} — {{ name }}</option>
          {% endfor %}
        </select>
      </div>
    </div>
  </section>

  <section>
    <h2 class="text-xs font-semibold text-gray-500 uppercase mb-3">② ¿Para qué necesitas financiación?</h2>
    <div class="flex flex-wrap gap-2">
      {% for f in finalidades %}
        <label class="cursor-pointer">
          <input type="checkbox" name="finalidad" value="{{ f.value }}" class="peer hidden">
          <span class="inline-block px-3 py-1.5 rounded-full border border-gray-300 text-sm peer-checked:bg-green-700 peer-checked:text-white peer-checked:border-green-700">{{ f.label }}</span>
        </label>
      {% endfor %}
    </div>
    <p class="text-xs text-gray-500 mt-2">Marca al menos una.</p>
  </section>

  <button type="submit" class="w-full bg-green-700 hover:bg-green-800 text-white font-semibold py-3 rounded-lg">
    Buscar subvenciones →
  </button>
</form>
{% endblock %}
```

- [ ] **Step 4: Crear `app/web/routes_search.py` con GET /**

```python
"""Rutas web de búsqueda."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


# Datos estáticos para el formulario
_PROVINCIAS: list[tuple[str, str]] = [
    ("01", "Álava"), ("02", "Albacete"), ("03", "Alicante"), ("04", "Almería"),
    ("05", "Ávila"), ("06", "Badajoz"), ("07", "Baleares"), ("08", "Barcelona"),
    ("09", "Burgos"), ("10", "Cáceres"), ("11", "Cádiz"), ("12", "Castellón"),
    ("13", "Ciudad Real"), ("14", "Córdoba"), ("15", "A Coruña"), ("16", "Cuenca"),
    ("17", "Girona"), ("18", "Granada"), ("19", "Guadalajara"), ("20", "Guipúzcoa"),
    ("21", "Huelva"), ("22", "Huesca"), ("23", "Jaén"), ("24", "León"),
    ("25", "Lleida"), ("26", "La Rioja"), ("27", "Lugo"), ("28", "Madrid"),
    ("29", "Málaga"), ("30", "Murcia"), ("31", "Navarra"), ("32", "Ourense"),
    ("33", "Asturias"), ("34", "Palencia"), ("35", "Las Palmas"), ("36", "Pontevedra"),
    ("37", "Salamanca"), ("38", "S/C Tenerife"), ("39", "Cantabria"), ("40", "Segovia"),
    ("41", "Sevilla"), ("42", "Soria"), ("43", "Tarragona"), ("44", "Teruel"),
    ("45", "Toledo"), ("46", "Valencia"), ("47", "Valladolid"), ("48", "Vizcaya"),
    ("49", "Zamora"), ("50", "Zaragoza"), ("51", "Ceuta"), ("52", "Melilla"),
]

_FINALIDADES: list[dict[str, str]] = [
    {"value": "digitalizacion", "label": "Digitalización"},
    {"value": "i+d", "label": "I+D"},
    {"value": "contratacion", "label": "Contratación"},
    {"value": "eficiencia_energetica", "label": "Eficiencia energética"},
    {"value": "internacionalizacion", "label": "Internacionalización"},
    {"value": "formacion", "label": "Formación"},
    {"value": "innovacion", "label": "Innovación"},
    {"value": "otros", "label": "Otros"},
]


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "home.html",
        {"provincias": _PROVINCIAS, "finalidades": _FINALIDADES},
    )
```

- [ ] **Step 5: Montar el router en `app/main.py`**

Editar `app/main.py` para incluir:

```python
# Tras crear `app = FastAPI(...)`:
from app.web.routes_search import router as search_router  # noqa: E402

app.include_router(search_router)
```

Posición: justo después de la línea `app = FastAPI(...)`.

- [ ] **Step 6: Ejecutar test, verificar PASS**

```bash
pytest tests/unit/test_routes_search.py -v
```

Expected: PASS.

- [ ] **Step 7: Verificación visual**

```bash
uvicorn app.main:app --reload --port 8000
```

Abrir http://localhost:8000 en navegador. Debe verse el formulario con campos y chips de finalidades. Parar con Ctrl-C.

- [ ] **Step 8: Commit**

```bash
git add app/web/routes_search.py app/web/templates/home.html app/main.py tests/unit/test_routes_search.py
git -c commit.gpgsign=false commit -m "feat(web): home form GET / with provincias + finalidades"
```

---

## Task 19: POST /search + página de resultados

**Files:**
- Modify: `app/web/routes_search.py` (añadir POST /search)
- Create: `app/web/templates/results.html`
- Modify: `tests/unit/test_routes_search.py`

- [ ] **Step 1: Escribir test que falla**

Añadir al final de `tests/unit/test_routes_search.py`:

```python
from datetime import date, timedelta

from app.db.models import Subvencion


def test_search_returns_results_html(db_session):
    # Sembrar una subvención que matchea
    db_session.add(
        Subvencion(
            source="bdns",
            external_id="SEED-001",
            titulo="Kit Digital test",
            organismo="Red.es",
            ambito="estatal",
            cnae_elegible=["6201"],
            finalidad=["digitalizacion"],
            estado="abierta",
            fecha_fin=date.today() + timedelta(days=30),
            beneficiarios={"tamanos": ["micro", "pequena"]},
            importe_max_beneficiario=12000,
            enlace_oficial="https://boe.es/test",
        )
    )
    db_session.commit()

    response = client.post(
        "/search",
        data={
            "nif": "B12345674",
            "razon_social": "Flexigobe SL",
            "cnae": "6201",
            "tamano": "pequena",
            "provincia": "08",
            "finalidad": ["digitalizacion"],
        },
    )

    assert response.status_code == 200
    html = response.text
    assert "Kit Digital test" in html
    assert "Top 3 recomendadas" in html or "recomendadas" in html.lower()


def test_search_invalid_nif_returns_error():
    response = client.post(
        "/search",
        data={
            "nif": "INVALIDO",
            "cnae": "6201",
            "tamano": "pequena",
            "provincia": "08",
            "finalidad": ["digitalizacion"],
        },
    )
    assert response.status_code == 400
    assert "NIF" in response.text or "no válido" in response.text.lower()


def test_search_requires_at_least_one_finalidad():
    response = client.post(
        "/search",
        data={
            "nif": "B12345674",
            "cnae": "6201",
            "tamano": "pequena",
            "provincia": "08",
        },
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Confirmar fallo**

```bash
pytest tests/unit/test_routes_search.py -v
```

Expected: 3 tests nuevos fallan.

- [ ] **Step 3: Crear `app/web/templates/results.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="mb-6">
  <a href="/" class="text-sm text-gray-500 hover:underline">← Nueva búsqueda</a>
  <h1 class="text-2xl font-bold mt-2">Resultados para {{ razon_social or nif }}</h1>
  <p class="text-gray-600 text-sm">{{ total }} subvenciones afines en BDNS</p>
</div>

{% if top3 %}
<section class="mb-8">
  <h2 class="text-xs font-semibold text-green-800 uppercase mb-3">★ Top 3 recomendadas</h2>
  <div class="space-y-3">
    {% for r in top3 %}
    <a href="/subsidy/{{ r.subvencion.id }}" class="block bg-gradient-to-r from-green-50 to-white border border-green-200 rounded-2xl p-5 hover:shadow-md transition">
      <div class="flex items-baseline justify-between mb-1">
        <span class="text-xs font-bold bg-green-700 text-white px-2 py-0.5 rounded">{{ r.score }}% match</span>
        <span class="text-xs text-gray-500">
          {% if r.subvencion.fecha_fin %}Cierra: {{ r.subvencion.fecha_fin.strftime("%d/%m/%Y") }}{% else %}Sin fecha fin{% endif %}
        </span>
      </div>
      <h3 class="font-bold text-lg">{{ r.subvencion.titulo }}</h3>
      <p class="text-sm text-gray-600 mt-1">
        {{ r.subvencion.organismo or "Organismo no especificado" }}
        {% if r.subvencion.importe_max_beneficiario %} · Hasta {{ "{:,.0f}".format(r.subvencion.importe_max_beneficiario) }}€{% endif %}
      </p>
    </a>
    {% endfor %}
  </div>
</section>
{% endif %}

{% if rest %}
<section>
  <h2 class="text-xs font-semibold text-gray-500 uppercase mb-3">Otras elegibles</h2>
  <div class="bg-white border border-gray-200 rounded-2xl overflow-hidden">
    <table class="w-full text-sm">
      <thead class="bg-gray-50 text-xs text-gray-500 uppercase">
        <tr>
          <th class="px-4 py-2 text-left">Score</th>
          <th class="px-4 py-2 text-left">Convocatoria</th>
          <th class="px-4 py-2 text-left">Organismo</th>
          <th class="px-4 py-2 text-left">Importe</th>
          <th class="px-4 py-2 text-left">Cierra</th>
        </tr>
      </thead>
      <tbody>
        {% for r in rest %}
        <tr class="border-t border-gray-100 hover:bg-gray-50">
          <td class="px-4 py-2 font-semibold {% if r.score >= 60 %}text-green-700{% elif r.score >= 40 %}text-yellow-700{% else %}text-gray-500{% endif %}">{{ r.score }}</td>
          <td class="px-4 py-2"><a class="hover:underline" href="/subsidy/{{ r.subvencion.id }}">{{ r.subvencion.titulo }}</a></td>
          <td class="px-4 py-2 text-gray-600">{{ r.subvencion.organismo or "—" }}</td>
          <td class="px-4 py-2 text-gray-600">{% if r.subvencion.importe_max_beneficiario %}{{ "{:,.0f}".format(r.subvencion.importe_max_beneficiario) }}€{% else %}—{% endif %}</td>
          <td class="px-4 py-2 text-gray-600">{% if r.subvencion.fecha_fin %}{{ r.subvencion.fecha_fin.strftime("%d/%m/%Y") }}{% else %}—{% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>
{% endif %}

{% if total == 0 %}
<div class="bg-yellow-50 border border-yellow-200 rounded-xl p-6 text-center">
  <p class="font-semibold mb-1">No hemos encontrado subvenciones abiertas para tu perfil.</p>
  <p class="text-sm text-gray-700">Prueba con otra finalidad o vuelve a buscar más adelante — la BDNS se actualiza a diario.</p>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Modificar `app/web/routes_search.py` para añadir POST /search**

Añadir imports al inicio:

```python
import hashlib
import uuid
from typing import Annotated

from fastapi import Depends, Form, HTTPException
from sqlalchemy.orm import Session

from app.db.models import Search, SearchResult
from app.db.session import get_db
from app.lib.nif_validator import validate_nif
from app.matching.filter import EmpresaProfile
from app.matching.service import rank_for
```

Añadir al final del fichero:

```python
@router.post("/search", response_class=HTMLResponse)
def search(
    request: Request,
    nif: Annotated[str, Form()],
    cnae: Annotated[str, Form()],
    tamano: Annotated[str, Form()],
    provincia: Annotated[str, Form()],
    finalidad: Annotated[list[str], Form()],
    razon_social: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    # Validar NIF
    nif_result = validate_nif(nif)
    if not nif_result.valid:
        raise HTTPException(status_code=400, detail=f"El NIF {nif} no es válido")

    # Validar al menos una finalidad
    if not finalidad:
        raise HTTPException(status_code=422, detail="Selecciona al menos una finalidad")

    # Persistir la búsqueda como lead
    ip = request.client.host if request.client else ""
    ip_hash = hashlib.sha256(ip.encode()).hexdigest() if ip else None

    search_row = Search(
        id=uuid.uuid4(),
        nif=nif_result.normalized,
        razon_social=razon_social,
        cnae=cnae,
        tamano=tamano,
        provincia=provincia,
        finalidad=finalidad,
        ip_hash=ip_hash,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(search_row)
    db.flush()

    # Matching
    perfil = EmpresaProfile(cnae=cnae, tamano=tamano, provincia=provincia, finalidad=finalidad)
    ranked = rank_for(db, perfil, limit=30)

    # Persistir search_results
    for r in ranked:
        db.add(SearchResult(
            search_id=search_row.id,
            subvencion_id=r.subvencion.id,
            score=r.score,
            razon=r.razon,
            rank=r.rank,
        ))
    db.commit()

    top3 = ranked[:3]
    rest = ranked[3:]

    return templates.TemplateResponse(
        request,
        "results.html",
        {
            "nif": nif_result.normalized,
            "razon_social": razon_social,
            "top3": top3,
            "rest": rest,
            "total": len(ranked),
        },
    )
```

- [ ] **Step 5: Hacer que el TestClient use la DB de test**

Editar `tests/unit/test_routes_search.py` para sobrescribir la dependencia `get_db`. Añadir tras los imports existentes:

```python
from app.db.session import get_db
from tests.conftest import TestSessionLocal


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
```

- [ ] **Step 6: Ejecutar tests, verificar PASS**

```bash
pytest tests/unit/test_routes_search.py -v
```

Expected: todos PASS.

- [ ] **Step 7: Commit**

```bash
git add app/web/routes_search.py app/web/templates/results.html tests/unit/test_routes_search.py
git -c commit.gpgsign=false commit -m "feat(web): POST /search with deterministic matching + results page"
```

---

## Task 20: GET /subsidy/{id} — página de detalle

**Files:**
- Modify: `app/web/routes_search.py`
- Create: `app/web/templates/subsidy_detail.html`
- Modify: `tests/unit/test_routes_search.py`

- [ ] **Step 1: Test que falla**

Añadir a `tests/unit/test_routes_search.py`:

```python
def test_subsidy_detail_renders(db_session):
    sub = Subvencion(
        source="bdns",
        external_id="DETAIL-1",
        titulo="Detalle ayuda",
        organismo="Ministerio X",
        ambito="estatal",
        cnae_elegible=["6201"],
        finalidad=["digitalizacion"],
        estado="abierta",
        fecha_inicio=date.today(),
        fecha_fin=date.today() + timedelta(days=60),
        importe_max_beneficiario=15000,
        descripcion="Descripción completa.",
        enlace_oficial="https://boe.es/detalle",
    )
    db_session.add(sub)
    db_session.commit()

    response = client.get(f"/subsidy/{sub.id}")
    assert response.status_code == 200
    assert "Detalle ayuda" in response.text
    assert "Descripción completa." in response.text
    assert "https://boe.es/detalle" in response.text


def test_subsidy_detail_404_when_not_found():
    response = client.get("/subsidy/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
```

- [ ] **Step 2: Confirmar fallo**

```bash
pytest tests/unit/test_routes_search.py::test_subsidy_detail_renders -v
```

Expected: 404 (la ruta no existe).

- [ ] **Step 3: Crear `app/web/templates/subsidy_detail.html`**

```html
{% extends "base.html" %}
{% block content %}
<a href="javascript:history.back()" class="text-sm text-gray-500 hover:underline">← Volver</a>

<div class="bg-white rounded-2xl border border-gray-200 p-8 mt-4">
  <div class="text-xs uppercase text-gray-500 mb-2">
    {{ sub.organismo or "Organismo no especificado" }}
    · {{ sub.ambito|capitalize }}{% if sub.ccaa %} · {{ sub.ccaa }}{% endif %}
  </div>
  <h1 class="text-2xl font-bold mb-3">{{ sub.titulo }}</h1>

  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
    <div>
      <div class="text-xs text-gray-500 uppercase">Importe máx.</div>
      <div class="font-semibold">{% if sub.importe_max_beneficiario %}{{ "{:,.0f}".format(sub.importe_max_beneficiario) }}€{% else %}—{% endif %}</div>
    </div>
    <div>
      <div class="text-xs text-gray-500 uppercase">% Subvención</div>
      <div class="font-semibold">{% if sub.porcentaje %}{{ sub.porcentaje }}%{% else %}—{% endif %}</div>
    </div>
    <div>
      <div class="text-xs text-gray-500 uppercase">Apertura</div>
      <div class="font-semibold">{% if sub.fecha_inicio %}{{ sub.fecha_inicio.strftime("%d/%m/%Y") }}{% else %}—{% endif %}</div>
    </div>
    <div>
      <div class="text-xs text-gray-500 uppercase">Cierre</div>
      <div class="font-semibold">{% if sub.fecha_fin %}{{ sub.fecha_fin.strftime("%d/%m/%Y") }}{% else %}—{% endif %}</div>
    </div>
  </div>

  {% if sub.descripcion %}
  <section class="mb-6">
    <h2 class="text-xs font-semibold text-gray-500 uppercase mb-2">Descripción</h2>
    <p class="whitespace-pre-line text-gray-800">{{ sub.descripcion }}</p>
  </section>
  {% endif %}

  {% if sub.cnae_elegible %}
  <section class="mb-6">
    <h2 class="text-xs font-semibold text-gray-500 uppercase mb-2">CNAE elegibles</h2>
    <div class="flex flex-wrap gap-1">
      {% for c in sub.cnae_elegible %}<span class="text-xs bg-gray-100 px-2 py-0.5 rounded">{{ c }}</span>{% endfor %}
    </div>
  </section>
  {% endif %}

  {% if sub.finalidad %}
  <section class="mb-6">
    <h2 class="text-xs font-semibold text-gray-500 uppercase mb-2">Finalidad</h2>
    <div class="flex flex-wrap gap-1">
      {% for f in sub.finalidad %}<span class="text-xs bg-blue-50 text-blue-800 px-2 py-0.5 rounded">{{ f }}</span>{% endfor %}
    </div>
  </section>
  {% endif %}

  {% if sub.enlace_oficial %}
  <a href="{{ sub.enlace_oficial }}" target="_blank" rel="noopener" class="inline-block bg-green-700 hover:bg-green-800 text-white font-semibold px-6 py-3 rounded-lg">
    Ver convocatoria oficial →
  </a>
  {% endif %}

  <p class="text-xs text-gray-500 mt-6">Información orientativa generada automáticamente. Consulta siempre la convocatoria oficial.</p>
</div>
{% endblock %}
```

- [ ] **Step 4: Añadir ruta `GET /subsidy/{id}` en `app/web/routes_search.py`**

Añadir imports si faltan: `from uuid import UUID`.

```python
@router.get("/subsidy/{subsidy_id}", response_class=HTMLResponse)
def subsidy_detail(
    request: Request,
    subsidy_id: UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    from app.db.models import Subvencion

    sub = db.get(Subvencion, subsidy_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subvención no encontrada")
    return templates.TemplateResponse(request, "subsidy_detail.html", {"sub": sub})
```

- [ ] **Step 5: Ejecutar tests, verificar PASS**

```bash
pytest tests/unit/test_routes_search.py -v
```

Expected: todos PASS.

- [ ] **Step 6: Commit**

```bash
git add app/web/routes_search.py app/web/templates/subsidy_detail.html tests/unit/test_routes_search.py
git -c commit.gpgsign=false commit -m "feat(web): GET /subsidy/{id} detail page"
```

---

## Task 21: Test de integración end-to-end

Un test que ejerce el flujo completo: siembra DB → POST /search → comprueba que `Search` y `SearchResult` se guardaron → GET de un subsidy de los resultados.

**Files:**
- Create: `tests/integration/__init__.py` (vacío)
- Create: `tests/integration/test_search_flow.py`

- [ ] **Step 1: Crear directorio integration**

```bash
mkdir -p tests/integration && touch tests/integration/__init__.py
```

- [ ] **Step 2: Crear `tests/integration/test_search_flow.py`**

```python
"""Test end-to-end del flujo de búsqueda completo."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Search, SearchResult, Subvencion
from app.db.session import get_db
from app.main import app
from tests.conftest import TestSessionLocal


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
client = TestClient(app)


def test_full_search_flow(db_session):
    # Sembrar varias subvenciones
    db_session.add_all([
        Subvencion(
            source="bdns", external_id=f"FLOW-{i}",
            titulo=f"Ayuda {i}",
            ambito="estatal",
            cnae_elegible=["6201"] if i % 2 == 0 else [],
            finalidad=["digitalizacion"],
            estado="abierta",
            fecha_fin=date.today() + timedelta(days=30 + i),
            beneficiarios={"tamanos": ["pequena"]},
            organismo="Ministerio test",
            importe_max_beneficiario=12000,
        )
        for i in range(5)
    ])
    db_session.commit()

    # POST /search
    response = client.post("/search", data={
        "nif": "B12345674",
        "razon_social": "Empresa Test SL",
        "cnae": "6201",
        "tamano": "pequena",
        "provincia": "08",
        "finalidad": ["digitalizacion"],
    })
    assert response.status_code == 200
    assert "Ayuda" in response.text

    # Verificar que se guardó la búsqueda
    searches = db_session.execute(select(Search)).scalars().all()
    assert len(searches) == 1
    s = searches[0]
    assert s.nif == "B12345674"
    assert s.razon_social == "Empresa Test SL"

    # Verificar que se guardaron los resultados
    results = db_session.execute(select(SearchResult).where(SearchResult.search_id == s.id)).scalars().all()
    assert len(results) == 5
    # Todos los rangs entre 1 y 5
    ranks = sorted(r.rank for r in results)
    assert ranks == [1, 2, 3, 4, 5]

    # Coger un subvencion_id y abrir su detalle
    first_subv_id = results[0].subvencion_id
    detail = client.get(f"/subsidy/{first_subv_id}")
    assert detail.status_code == 200
    assert "Ayuda" in detail.text
```

- [ ] **Step 3: Ejecutar test**

```bash
pytest tests/integration/test_search_flow.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/__init__.py tests/integration/test_search_flow.py
git -c commit.gpgsign=false commit -m "test(integration): end-to-end search flow"
```

---

## Task 22: Smoke manual local + README rápido

Cierre del Plan 1: probar la app a mano y dejar instrucciones para futuros desarrolladores (o para Victor si vuelve después de un tiempo).

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Lanzar la app local con scheduler**

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Logs esperados: `Scheduler started`, `Application startup complete.`

- [ ] **Step 2: Cargar datos reales de BDNS** (manual una vez para probar)

En otra terminal:

```bash
source .venv/bin/activate
python -c "
import asyncio
from datetime import date, timedelta
from app.db.session import SessionLocal
from app.sync.bdns_puller import sync_all

with SessionLocal() as session:
    stats = asyncio.run(sync_all(session, since=date.today() - timedelta(days=30)))
    print(stats)
"
```

Expected: imprime algo como `{'created': N, 'updated': M, 'total': N+M}` con N > 0. Si la API de BDNS responde con error, mirar el log y ajustar `bdns_puller.py:fetch_page` con el endpoint/query params reales (ver nota en Task 11).

- [ ] **Step 3: Probar el flujo en navegador**

Abrir http://localhost:8000:
1. Rellenar NIF válido (ej. `B12345674`), CNAE `6201`, tamaño `pequena`, Barcelona, marcar `Digitalización` y `I+D`.
2. Pulsar "Buscar subvenciones".
3. Comprobar que aparece la página de resultados con cards y/o tabla.
4. Clic en una subvención → ver detalle.

- [ ] **Step 4: Actualizar `README.md` con instrucciones de arranque**

Reemplazar contenido por:

```markdown
# subvenciones-app

Buscador de subvenciones públicas para empresas españolas (Plan 1: BDNS + matching determinista).

## Pre-requisitos

- Python 3.12
- Postgres 15+

## Setup local

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Postgres local
createuser subvenciones --pwprompt   # password: subvenciones
createdb subvenciones --owner subvenciones
createdb subvenciones_test --owner subvenciones

# Variables
cp .env.example .env

# Migraciones
alembic upgrade head

# Arrancar
uvicorn app.main:app --reload --port 8000
```

Abrir http://localhost:8000.

## Sincronizar BDNS manualmente

```bash
python -c "
import asyncio
from datetime import date, timedelta
from app.db.session import SessionLocal
from app.sync.bdns_puller import sync_all
with SessionLocal() as s:
    print(asyncio.run(sync_all(s, since=date.today() - timedelta(days=30))))
"
```

El scheduler in-process lo ejecuta automáticamente cada día a las 03:00 (Europe/Madrid) mientras la app está corriendo.

## Tests

```bash
pytest -v
```

## Documentación

- Spec: [docs/superpowers/specs/2026-05-17-subvenciones-app-design.md](docs/superpowers/specs/2026-05-17-subvenciones-app-design.md)
- Plan 1: [docs/superpowers/plans/2026-05-17-subvenciones-app-plan-1-cimientos.md](docs/superpowers/plans/2026-05-17-subvenciones-app-plan-1-cimientos.md)
```

- [ ] **Step 5: Pasada final de tests**

```bash
pytest -v
```

Expected: todos los tests PASS.

- [ ] **Step 6: Commit + tag**

```bash
git add README.md
git -c commit.gpgsign=false commit -m "docs: README with local setup instructions"
git tag -a v0.1.0-plan1 -m "Plan 1 complete: core search with BDNS + deterministic matching"
```

---

## Cierre del Plan 1

Al terminar todas las tareas anteriores tendrás:

- App FastAPI funcional en local.
- DB Postgres con esquema completo de las 3 tablas core.
- Sync de BDNS automático diario + ejecutable a mano.
- Validador NIF/CIF/NIE oficial.
- Catálogo CNAE-2009 cargado y buscable.
- Matching determinista que devuelve top 30 ranked.
- Formulario web responsivo, página de resultados híbrida (top 3 + tabla) y página de detalle.
- ~60-80 tests unitarios e integración.

**Lo que NO tiene todavía** (lo trae el Plan 2):
- LLM scoring con Gemini (las razones en lenguaje natural).
- Auto-enrichment del NIF (libreborme + OpenCorporates).
- Cobertura de UE Funding & Tenders Portal.

**Lo que NO tiene todavía** (Plan 3):
- Captura de email + envío de PDF.
- Alertas por email de nuevas convocatorias.

**Lo que NO tiene todavía** (Plan 4):
- Panel admin.
- Rate limiting.
- Despliegue a Railway con dominio.
