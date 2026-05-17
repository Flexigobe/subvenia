# tests/unit/test_bdns_puller.py
import json
from datetime import date, date as date_t
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bdns"


@pytest.mark.asyncio
async def test_fetch_page_returns_parsed_json(httpx_mock):
    payload = json.loads((FIXTURES / "page_sample.json").read_text())
    # fetch_page(page=1) converts to page=0 (0-indexed) for the BDNS API.
    # Date format is DD/MM/YYYY as required by the BDNS API.
    httpx_mock.add_response(
        url="https://www.infosubvenciones.es/bdnstrans/api/convocatorias/busqueda?page=0&pageSize=100&fechaDesde=01%2F01%2F2026",
        json=payload,
    )
    from app.sync.bdns_puller import fetch_page

    result = await fetch_page(page=1, since=date(2026, 1, 1))

    assert len(result["content"]) == 1
    assert result["content"][0]["numeroConvocatoria"] == "906115"
    assert result["last"] is False


def test_parse_item_maps_all_fields():
    from app.sync.bdns_puller import parse_item

    # Reflects the real BDNS listing-endpoint field names.
    raw = {
        "id": 1107676,
        "mrr": False,
        "numeroConvocatoria": "906115",
        "descripcion": "AYUDAS PARA DIGITALIZACIÓN DE PYMES 2026",
        "descripcionLeng": None,
        "fechaRecepcion": "2026-01-15",
        "nivel1": "ESTATAL",
        "nivel2": "MINISTERIO DE INDUSTRIA",
        "nivel3": "SECRETARÍA DE ESTADO DE DIGITALIZACIÓN",
        "codigoInvente": None,
    }

    parsed = parse_item(raw)

    assert parsed["source"] == "bdns"
    assert parsed["external_id"] == "906115"
    assert parsed["titulo"] == "AYUDAS PARA DIGITALIZACIÓN DE PYMES 2026"
    assert parsed["ambito"] == "estatal"
    assert parsed["organismo"] == "SECRETARÍA DE ESTADO DE DIGITALIZACIÓN"
    assert parsed["fecha_inicio"] == date_t(2026, 1, 15)
    # Fields not in the listing endpoint are None / [].
    assert parsed["fecha_fin"] is None
    assert parsed["importe_total"] is None
    assert parsed["importe_max_beneficiario"] is None
    assert parsed["cnae_elegible"] == []
    assert parsed["finalidad"] == []
    assert parsed["enlace_oficial"] is None
    assert parsed["raw_payload"] == raw


def test_parse_item_handles_missing_optional_fields():
    from app.sync.bdns_puller import parse_item

    # Minimal item: only the required key is numeroConvocatoria.
    raw = {
        "numeroConvocatoria": "999999",
    }

    parsed = parse_item(raw)

    assert parsed["external_id"] == "999999"
    assert parsed["titulo"] == ""
    assert parsed["ambito"] == "estatal"  # default when nivel1 is absent
    assert parsed["fecha_inicio"] is None
    assert parsed["importe_total"] is None
    assert parsed["cnae_elegible"] == []
    assert parsed["finalidad"] == []


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
