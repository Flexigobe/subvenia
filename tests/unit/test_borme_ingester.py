"""Tests for BORME ingester `sync_day`."""

from datetime import date
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.db.models import Empresa


@pytest.mark.asyncio
async def test_sync_day_returns_zeros_on_weekend(monkeypatch, db_session):
    """When fetch_sumario returns [] (weekend/holiday), sync_day returns zero stats."""
    import app.sync.borme_ingester as ingester_mod

    async def _empty(target, client=None):
        return []
    monkeypatch.setattr(ingester_mod, "fetch_sumario", _empty)

    stats = await ingester_mod.sync_day(db_session, date(2025, 5, 17))  # saturday
    assert stats["created"] == 0
    assert stats["updated"] == 0
    assert stats["total_pdfs"] == 0
    assert stats["total_entries"] == 0


@pytest.mark.asyncio
async def test_sync_day_inserts_new_empresa(monkeypatch, db_session):
    """Happy path: 1 sumario item, 1 PDF, 1 entry → empresa inserted."""
    import app.sync.borme_ingester as ingester_mod

    async def _sumario(target, client=None):
        return [{
            "identificador": "BORME-A-2025-91-03",
            "titulo": "ALICANTE",
            "url_pdf": "https://example.com/foo.pdf",
            "provincia": "03",
        }]

    async def _pdf(url, client=None):
        return b"fake pdf bytes"

    def _extract(pdf_bytes):
        return (
            "218391 - NUEVA EMPRESA SL.\n"
            "Constitución. Comienzo de operaciones: 11.04.25. Capital: 3.000,00 Euros. "
            "Domicilio: C/ TEST 1. Datos registrales. S 8, H A 999001, I/A 1 (8.05.25).\n"
        )

    monkeypatch.setattr(ingester_mod, "fetch_sumario", _sumario)
    monkeypatch.setattr(ingester_mod, "fetch_pdf", _pdf)
    monkeypatch.setattr(ingester_mod, "extract_pdf_text", _extract)

    stats = await ingester_mod.sync_day(db_session, date(2025, 5, 16))
    assert stats["created"] == 1
    assert stats["updated"] == 0
    assert stats["total_pdfs"] == 1
    assert stats["total_entries"] == 1

    rows = db_session.execute(select(Empresa).where(Empresa.hoja_rm.like("H A 999001%"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].razon_social == "NUEVA EMPRESA SL"
    assert rows[0].provincia == "03"


@pytest.mark.asyncio
async def test_sync_day_upserts_existing_by_hoja_rm(monkeypatch, db_session):
    """Re-ingesting the same hoja_rm (same inscription number) on a second day → updated,
    not duplicated. We use the same I/A number in both entries so the upsert key matches."""
    import app.sync.borme_ingester as ingester_mod

    # Day 1: Constitución
    async def _sumario_d1(target, client=None):
        return [{
            "identificador": "BORME-A-2025-91-03",
            "titulo": "ALICANTE",
            "url_pdf": "https://example.com/d1.pdf",
            "provincia": "03",
        }]

    async def _pdf_any(url, client=None):
        return b"fake"

    def _extract_d1(pdf_bytes):
        return (
            "200001 - REPETIDA SL.\n"
            "Constitución. Capital: 3.000,00 Euros. "
            "Datos registrales. S 8, H A 999002, I/A 1 (1.05.25).\n"
        )

    monkeypatch.setattr(ingester_mod, "fetch_sumario", _sumario_d1)
    monkeypatch.setattr(ingester_mod, "fetch_pdf", _pdf_any)
    monkeypatch.setattr(ingester_mod, "extract_pdf_text", _extract_d1)
    stats_d1 = await ingester_mod.sync_day(db_session, date(2025, 5, 1))
    assert stats_d1["created"] == 1

    # Day 2: same empresa re-appears with same hoja_rm (I/A 1 stays the same — only
    # the fecha_registral changes). This is the real upsert scenario.
    def _extract_d2(pdf_bytes):
        return (
            "200001 - REPETIDA SL.\n"
            "Nombramientos. Adm. Único: SOMEONE NEW. "
            "Datos registrales. S 8, H A 999002, I/A 1 (15.05.25).\n"
        )
    monkeypatch.setattr(ingester_mod, "extract_pdf_text", _extract_d2)
    stats_d2 = await ingester_mod.sync_day(db_session, date(2025, 5, 15))
    assert stats_d2["created"] == 0
    assert stats_d2["updated"] == 1

    # Exactly one empresa row exists (no duplicate inserted)
    all_rows = db_session.execute(select(Empresa).where(Empresa.razon_social == "REPETIDA SL")).scalars().all()
    assert len(all_rows) == 1
    # Merged actos should contain both Constitución and Nombramientos
    actos_tipos = {a["tipo"] for a in (all_rows[0].actos or [])}
    assert "Constitución" in actos_tipos
    assert "Nombramientos" in actos_tipos


@pytest.mark.asyncio
async def test_sync_day_skips_entry_without_hoja_rm(monkeypatch, db_session):
    """Entries without hoja_rm in the PDF (rare) are skipped, counted as skipped_no_hoja."""
    import app.sync.borme_ingester as ingester_mod

    async def _sumario(target, client=None):
        return [{
            "identificador": "BORME-A-2025-91-03",
            "titulo": "ALICANTE",
            "url_pdf": "https://example.com/x.pdf",
            "provincia": "03",
        }]
    async def _pdf(url, client=None):
        return b"x"
    def _extract(pdf_bytes):
        # No "Datos registrales" line → no hoja_rm
        return "300001 - SIN HOJA SL.\nConstitución. Capital: 3.000,00 Euros.\n"

    monkeypatch.setattr(ingester_mod, "fetch_sumario", _sumario)
    monkeypatch.setattr(ingester_mod, "fetch_pdf", _pdf)
    monkeypatch.setattr(ingester_mod, "extract_pdf_text", _extract)

    stats = await ingester_mod.sync_day(db_session, date(2025, 5, 16))
    assert stats["created"] == 0
    assert stats["skipped_no_hoja"] == 1


@pytest.mark.asyncio
async def test_sync_day_tolerates_pdf_download_failure(monkeypatch, db_session):
    """A failed PDF download increments `errors` but doesn't crash the day."""
    import app.sync.borme_ingester as ingester_mod

    async def _sumario(target, client=None):
        return [
            {"identificador": "ok-1", "titulo": "X", "url_pdf": "https://example.com/ok.pdf", "provincia": "03"},
            {"identificador": "bad-1", "titulo": "Y", "url_pdf": "https://example.com/bad.pdf", "provincia": "08"},
        ]

    async def _pdf(url, client=None):
        if "bad" in url:
            return None  # failure
        return b"ok"

    def _extract(pdf_bytes):
        return "400001 - OK SL.\nConstitución. Capital: 1,00 Euros. Datos registrales. S 8, H A 888001, I/A 1 (1.05.25).\n"

    monkeypatch.setattr(ingester_mod, "fetch_sumario", _sumario)
    monkeypatch.setattr(ingester_mod, "fetch_pdf", _pdf)
    monkeypatch.setattr(ingester_mod, "extract_pdf_text", _extract)

    stats = await ingester_mod.sync_day(db_session, date(2025, 5, 16))
    assert stats["created"] == 1
    assert stats["errors"] == 1
    assert stats["total_pdfs"] == 2
