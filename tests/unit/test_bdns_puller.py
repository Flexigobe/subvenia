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
    httpx_mock.add_response(
        url="https://www.infosubvenciones.es/bdnstrans/api/convocatorias/busqueda?page=1&pageSize=100&fechaDesde=2026-01-01",
        json=payload,
    )
    from app.sync.bdns_puller import fetch_page

    result = await fetch_page(page=1, since=date(2026, 1, 1))

    assert result["page"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == "BDNS-001"


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
