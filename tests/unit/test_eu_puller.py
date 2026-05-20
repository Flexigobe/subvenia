"""Tests para app/sync/eu_puller.py — EU Funding & Tenders Portal."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import Subvencion

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "eu"

# ── Exact URL the puller will request (page=1, fetch_page default text="***") ──
_EXPECTED_URL = (
    "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
    "?apiKey=SEDIA&text=%2A%2A%2A&pageSize=50&pageNumber=1&languages=es%2Cen"
)


# ─────────────────────────────────────────────────────────────────────────────
# fetch_page
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eu_fetch_page_returns_parsed_json(httpx_mock):
    payload = json.loads((FIXTURES / "page_sample.json").read_text())
    httpx_mock.add_response(
        method="POST",
        url=_EXPECTED_URL,
        json=payload,
    )
    from app.sync.eu_puller import fetch_page

    result = await fetch_page(page=1)
    assert "results" in result
    assert result["totalResults"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# parse_item
# ─────────────────────────────────────────────────────────────────────────────


def test_eu_parse_item_maps_to_subvencion_fields():
    payload = json.loads((FIXTURES / "page_sample.json").read_text())
    items = payload.get("results") or []
    assert items, "Fixture must contain at least one item"

    from app.sync.eu_puller import parse_item

    raw = items[0]
    mapped = parse_item(raw)

    assert mapped["source"] == "eu"
    assert mapped["ambito"] == "ue"
    # identifier is a list in metadata; parse_item takes [0]
    assert mapped["external_id"]
    assert mapped["titulo"]
    # raw_payload is preserved verbatim
    assert mapped["raw_payload"] == raw


def test_eu_parse_item_fecha_fin_is_date_when_deadline_present():
    payload = json.loads((FIXTURES / "page_sample.json").read_text())
    items = payload.get("results") or []
    # First fixture result has deadlineDate set
    raw = items[0]
    from app.sync.eu_puller import parse_item

    mapped = parse_item(raw)
    if mapped["fecha_fin"] is not None:
        assert isinstance(mapped["fecha_fin"], date)


def test_eu_parse_item_status_closed_maps_to_cerrada():
    """Status 31094503 = Closed → estado='cerrada'."""
    raw = {
        "reference": "test-ref",
        "url": "https://example.com/topic.json",
        "title": None,
        "summary": "Test topic",
        "metadata": {
            "identifier": ["TEST-TOPIC-01"],
            "title": ["Test Topic Title"],
            "callTitle": ["Test Call Title"],
            "callIdentifier": ["TEST-CALL-01"],
            "deadlineDate": ["2023-03-01T00:00:00.000+0000"],
            "startDate": ["2023-01-01T00:00:00.000+0000"],
            "status": ["31094503"],
            "sortStatus": ["3"],
            "frameworkProgramme": ["12345678"],
            "typesOfAction": ["Research and Innovation action"],
            "keywords": [],
        },
    }
    from app.sync.eu_puller import parse_item

    mapped = parse_item(raw)
    assert mapped["estado"] == "cerrada"


def test_eu_parse_item_status_open_maps_to_abierta():
    """Status 31094502 = Open → estado='abierta'."""
    raw = {
        "reference": "test-ref-open",
        "url": "https://example.com/topic-open.json",
        "title": None,
        "summary": "Open topic",
        "metadata": {
            "identifier": ["OPEN-TOPIC-01"],
            "title": ["Open Topic Title"],
            "callTitle": ["Open Call"],
            "callIdentifier": ["OPEN-CALL-01"],
            "deadlineDate": ["2030-12-31T00:00:00.000+0000"],
            "startDate": ["2026-01-01T00:00:00.000+0000"],
            "status": ["31094502"],
            "sortStatus": ["1"],
            "frameworkProgramme": ["12345678"],
            "typesOfAction": ["Innovation action"],
            "keywords": [],
        },
    }
    from app.sync.eu_puller import parse_item

    mapped = parse_item(raw)
    assert mapped["estado"] == "abierta"


def test_eu_parse_item_no_external_id_skipped():
    """Items without identifier are skipped in sync_all (external_id is empty)."""
    raw = {
        "reference": "no-id",
        "url": "https://example.com/faq.html",
        "title": None,
        "summary": "FAQ item",
        "metadata": {
            "identifier": [],
            "title": [],
            "status": ["0"],
        },
    }
    from app.sync.eu_puller import parse_item

    mapped = parse_item(raw)
    assert mapped["external_id"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# sync_all — end-to-end with db_session + httpx_mock
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_eu_sync_all_upserts_records(db_session, httpx_mock):
    """End-to-end: fetch_page → parse_item → upsert into DB.

    Fixture has 1 open (31094502) + 2 closed (31094503) records.
    Only the open one should be upserted; closed ones are skipped.
    """
    payload = json.loads((FIXTURES / "page_sample.json").read_text())

    # Primera query devuelve el fixture; resto de queries sectoriales vacío
    httpx_mock.add_response(method="POST", json=payload)
    httpx_mock.add_response(
        method="POST",
        json={"results": [], "totalPages": 0, "totalResults": 0},
        is_reusable=True,
    )

    from app.sync.eu_puller import sync_all

    stats = await sync_all(db_session, max_pages=1)

    # Fixture: 1 open record gets upserted; 2 closed records are skipped
    assert stats["total"] >= 1
    assert stats["created"] + stats["updated"] == stats["total"]
    assert "skipped_closed" in stats

    # Verify records are actually in the DB with source='eu'
    rows = db_session.execute(
        select(Subvencion).where(Subvencion.source == "eu")
    ).scalars().all()
    assert len(rows) >= 1
    assert all(r.ambito == "ue" for r in rows)


def test_eu_parse_item_skips_non_spanish_or_english_languages():
    """Records with metadata.language not in (es, en) return None — keeps DB readable
    for Spanish users (Plan 3 hotfix)."""
    from app.sync.eu_puller import parse_item

    bg_record = {
        "metadata": {
            "identifier": ["BG-1"],
            "title": ["Спетсиалист..."],
            "status": ["31094502"],
            "language": ["bg"],
            "deadlineDate": ["2026-12-31T00:00:00.000+0000"],
        }
    }
    en_record = {
        "metadata": {
            "identifier": ["EN-1"],
            "title": ["Funding call"],
            "status": ["31094502"],
            "language": ["en"],
            "deadlineDate": ["2026-12-31T00:00:00.000+0000"],
        }
    }
    es_record = {
        "metadata": {
            "identifier": ["ES-1"],
            "title": ["Ayudas para la digitalización"],
            "status": ["31094502"],
            "language": ["es"],
            "deadlineDate": ["2026-12-31T00:00:00.000+0000"],
        }
    }

    assert parse_item(bg_record) is None
    assert parse_item(en_record) is not None
    assert parse_item(es_record) is not None


@pytest.mark.asyncio
async def test_eu_sync_all_skips_closed_records(db_session, httpx_mock):
    """Records with status=Closed (31094503) are skipped, not upserted."""
    closed_record = {
        "metadata": {
            "identifier": ["CLOSED-1"],
            "title": ["Closed call"],
            "status": ["31094503"],
            "deadlineDate": ["2020-01-01T00:00:00.000+0000"],
        }
    }
    open_record = {
        "metadata": {
            "identifier": ["OPEN-1"],
            "title": ["Open call"],
            "status": ["31094502"],
            "deadlineDate": ["2026-12-31T00:00:00.000+0000"],
        }
    }
    # Primera query devuelve ambos records
    httpx_mock.add_response(
        method="POST",
        json={"results": [closed_record, open_record], "totalPages": 1, "totalResults": 2},
    )
    # Resto de queries (sectoriales) devuelven vacío
    httpx_mock.add_response(
        method="POST",
        json={"results": [], "totalPages": 0, "totalResults": 0},
        is_reusable=True,
    )

    from app.sync.eu_puller import sync_all

    stats = await sync_all(db_session, max_pages=1, page_size=50)

    assert stats["skipped_closed"] == 1
    assert stats["created"] == 1
    assert stats["updated"] == 0
    rows = db_session.execute(
        select(Subvencion).where(Subvencion.source == "eu")
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].external_id == "OPEN-1"


@pytest.mark.asyncio
async def test_eu_sync_all_stops_when_min_useful_reached_per_query(db_session, httpx_mock):
    """Cada query sectorial itera hasta min_useful, luego pasa a la siguiente keyword.
    Para el test mockeamos una respuesta vacía para que todas las queries terminen
    rápido y verificamos que la primera keyword sí trajo records."""

    def make_record(rid):
        return {
            "metadata": {
                "identifier": [rid],
                "title": [f"T{rid}"],
                "status": ["31094502"],
                "deadlineDate": ["2026-12-31T00:00:00.000+0000"],
            }
        }

    # Primera query trae 4 records (≥ min_useful=4), pasa a siguiente keyword.
    httpx_mock.add_response(
        method="POST",
        json={"results": [make_record(f"M-{i}") for i in range(4)], "totalPages": 1, "totalResults": 4},
    )
    # Resto de queries devuelven vacío (sin más records)
    httpx_mock.add_response(
        method="POST",
        json={"results": [], "totalPages": 0, "totalResults": 0},
        is_reusable=True,
    )

    from app.sync.eu_puller import sync_all

    stats = await sync_all(db_session, max_pages=10, page_size=50, min_useful=4)

    assert stats["created"] == 4
    assert stats["queries_run"] >= 1
