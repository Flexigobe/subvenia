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
