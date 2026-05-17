"""Tests para app/sync/eu_puller.py — EU Funding & Tenders Portal."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import Subvencion

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "eu"

# ── Exact URL the puller will request (page=1) ─────────────────────────────
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
    """End-to-end: fetch_page → parse_item → upsert into DB."""
    payload = json.loads((FIXTURES / "page_sample.json").read_text())

    # First page returns the fixture (has totalResults = large number but we cap at max_pages=1)
    httpx_mock.add_response(
        method="POST",
        url=_EXPECTED_URL,
        json=payload,
    )

    from app.sync.eu_puller import sync_all

    stats = await sync_all(db_session, max_pages=1)

    assert stats["total"] >= 1
    assert stats["created"] + stats["updated"] == stats["total"]

    # Verify records are actually in the DB with source='eu'
    rows = db_session.execute(
        select(Subvencion).where(Subvencion.source == "eu")
    ).scalars().all()
    assert len(rows) >= 1
    assert all(r.ambito == "ue" for r in rows)
