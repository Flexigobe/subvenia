"""Tests for BDNS catalog sync."""

import pytest
from sqlalchemy import select

from app.db.models import BdnsCatalog


@pytest.mark.asyncio
async def test_sync_catalogs_upserts_all_kinds(db_session, httpx_mock):
    finalidades_data = [{"id": 1, "descripcion": "I+D+i"}, {"id": 2, "descripcion": "Digitalización"}]
    beneficiarios_data = [{"id": 1, "descripcion": "PYMES"}]
    instrumentos_data = [{"id": 1, "descripcion": "Subvención"}]
    regiones_data = [{"id": "ES", "descripcion": "España", "hijos": []}]
    actividades_data = [{"id": "A", "descripcion": "Agricultura"}]

    httpx_mock.add_response(
        url="https://www.infosubvenciones.es/bdnstrans/api/finalidades?vpd=GE",
        json=finalidades_data,
    )
    httpx_mock.add_response(
        url="https://www.infosubvenciones.es/bdnstrans/api/beneficiarios?vpd=GE",
        json=beneficiarios_data,
    )
    httpx_mock.add_response(
        url="https://www.infosubvenciones.es/bdnstrans/api/instrumentos",
        json=instrumentos_data,
    )
    httpx_mock.add_response(
        url="https://www.infosubvenciones.es/bdnstrans/api/regiones",
        json=regiones_data,
    )
    httpx_mock.add_response(
        url="https://www.infosubvenciones.es/bdnstrans/api/actividades",
        json=actividades_data,
    )

    from app.sync.catalogs import sync_catalogs

    stats = await sync_catalogs(db_session)

    assert stats == {
        "finalidades": 2,
        "beneficiarios": 1,
        "instrumentos": 1,
        "regiones": 1,
        "actividades": 1,
    }

    # Verify each kind in DB
    rows = db_session.execute(select(BdnsCatalog)).scalars().all()
    by_kind = {r.kind: r.payload for r in rows}
    assert by_kind["finalidades"] == finalidades_data
    assert by_kind["beneficiarios"] == beneficiarios_data


@pytest.mark.asyncio
async def test_sync_catalogs_updates_existing(db_session, httpx_mock):
    # Pre-seed an old catalog
    db_session.add(BdnsCatalog(kind="finalidades", payload=[{"old": "data"}]))
    db_session.commit()

    # Mock fresh data for all 5 endpoints
    httpx_mock.add_response(
        url="https://www.infosubvenciones.es/bdnstrans/api/finalidades?vpd=GE",
        json=[{"id": 1, "descripcion": "Nueva"}],
    )
    for url in [
        "https://www.infosubvenciones.es/bdnstrans/api/beneficiarios?vpd=GE",
        "https://www.infosubvenciones.es/bdnstrans/api/instrumentos",
        "https://www.infosubvenciones.es/bdnstrans/api/regiones",
        "https://www.infosubvenciones.es/bdnstrans/api/actividades",
    ]:
        httpx_mock.add_response(url=url, json=[])

    from app.sync.catalogs import sync_catalogs

    await sync_catalogs(db_session)

    row = db_session.get(BdnsCatalog, "finalidades")
    assert row.payload == [{"id": 1, "descripcion": "Nueva"}]


def test_get_catalog_returns_payload(db_session):
    db_session.add(BdnsCatalog(kind="actividades", payload=[{"id": "A"}]))
    db_session.commit()

    from app.sync.catalogs import get_catalog

    assert get_catalog(db_session, "actividades") == [{"id": "A"}]
    assert get_catalog(db_session, "nonexistent") is None


@pytest.mark.asyncio
async def test_sync_catalogs_tolerates_endpoint_failure(db_session, httpx_mock):
    # One endpoint fails, others succeed; we should not crash
    httpx_mock.add_response(
        url="https://www.infosubvenciones.es/bdnstrans/api/finalidades?vpd=GE",
        status_code=500,
    )
    for url in [
        "https://www.infosubvenciones.es/bdnstrans/api/beneficiarios?vpd=GE",
        "https://www.infosubvenciones.es/bdnstrans/api/instrumentos",
        "https://www.infosubvenciones.es/bdnstrans/api/regiones",
        "https://www.infosubvenciones.es/bdnstrans/api/actividades",
    ]:
        httpx_mock.add_response(url=url, json=[{"x": 1}])

    from app.sync.catalogs import sync_catalogs

    stats = await sync_catalogs(db_session)
    assert stats["finalidades"] == 0  # failed
    assert stats["beneficiarios"] == 1  # succeeded
