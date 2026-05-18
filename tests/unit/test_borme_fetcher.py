"""Tests for BORME XML sumario fetcher + PDF downloader."""

from datetime import date

import pytest

# Use a recent monday-friday date — the test mocks HTTP, so the date doesn't need to be real
TEST_DATE = date(2025, 5, 16)
SUMARIO_URL = "https://www.boe.es/datosabiertos/api/borme/sumario/20250516"


@pytest.mark.asyncio
async def test_fetch_sumario_returns_section_a_items_only(httpx_mock):
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <status><code>200</code></status>
  <data><sumario>
    <metadatos><fecha_publicacion>20250516</fecha_publicacion></metadatos>
    <diario numero="91">
      <seccion codigo="A">
        <item>
          <identificador>BORME-A-2025-91-03</identificador>
          <titulo>ALICANTE/ALACANT</titulo>
          <url_pdf>https://www.boe.es/borme/dias/2025/05/16/pdfs/BORME-A-2025-91-03.pdf</url_pdf>
        </item>
        <item>
          <identificador>BORME-A-2025-91-08</identificador>
          <titulo>BARCELONA</titulo>
          <url_pdf>https://www.boe.es/borme/dias/2025/05/16/pdfs/BORME-A-2025-91-08.pdf</url_pdf>
        </item>
        <item>
          <identificador>BORME-A-2025-91-99</identificador>
          <titulo>ÍNDICE ALFABÉTICO DE SOCIEDADES</titulo>
          <url_pdf>https://...index.pdf</url_pdf>
        </item>
      </seccion>
      <seccion codigo="B">
        <item><identificador>BORME-B-2025-91-99</identificador><titulo>OTHER</titulo><url_pdf>...</url_pdf></item>
      </seccion>
    </diario>
  </sumario></data>
</response>"""
    httpx_mock.add_response(url=SUMARIO_URL, text=xml, headers={"Content-Type": "application/xml"})
    from app.sync.borme_fetcher import fetch_sumario

    items = await fetch_sumario(TEST_DATE)
    # 2 items: ALICANTE + BARCELONA. Index (-99) is skipped. Section B skipped.
    assert len(items) == 2
    assert {it["identificador"] for it in items} == {"BORME-A-2025-91-03", "BORME-A-2025-91-08"}
    by_id = {it["identificador"]: it for it in items}
    assert by_id["BORME-A-2025-91-03"]["provincia"] == "03"
    assert by_id["BORME-A-2025-91-08"]["provincia"] == "08"


@pytest.mark.asyncio
async def test_fetch_sumario_returns_empty_on_404(httpx_mock):
    httpx_mock.add_response(url=SUMARIO_URL, status_code=404)
    from app.sync.borme_fetcher import fetch_sumario

    items = await fetch_sumario(TEST_DATE)
    assert items == []


@pytest.mark.asyncio
async def test_fetch_sumario_returns_empty_on_non_xml_response(httpx_mock):
    httpx_mock.add_response(
        url=SUMARIO_URL,
        text="<html>maintenance</html>",
        headers={"Content-Type": "text/html"},
    )
    from app.sync.borme_fetcher import fetch_sumario

    items = await fetch_sumario(TEST_DATE)
    assert items == []


@pytest.mark.asyncio
async def test_fetch_sumario_returns_empty_on_malformed_xml(httpx_mock):
    httpx_mock.add_response(
        url=SUMARIO_URL,
        text="not xml at all <<<>>>",
        headers={"Content-Type": "application/xml"},
    )
    from app.sync.borme_fetcher import fetch_sumario

    items = await fetch_sumario(TEST_DATE)
    assert items == []


@pytest.mark.asyncio
async def test_fetch_sumario_handles_unmapped_province(httpx_mock):
    """Items with an unknown province name appear with provincia=None (not filtered out)."""
    xml = """<?xml version="1.0"?>
<response><data><sumario><diario><seccion codigo="A">
<item>
  <identificador>BORME-A-2025-91-99X</identificador>
  <titulo>PROVINCIA INVENTADA QUE NO EXISTE</titulo>
  <url_pdf>https://example.com/x.pdf</url_pdf>
</item>
</seccion></diario></sumario></data></response>"""
    httpx_mock.add_response(url=SUMARIO_URL, text=xml, headers={"Content-Type": "application/xml"})
    from app.sync.borme_fetcher import fetch_sumario

    items = await fetch_sumario(TEST_DATE)
    assert len(items) == 1
    assert items[0]["provincia"] is None


@pytest.mark.asyncio
async def test_fetch_pdf_returns_bytes(httpx_mock):
    fake_pdf = b"%PDF-1.4 fake content"
    httpx_mock.add_response(url="https://example.com/borme.pdf", content=fake_pdf)
    from app.sync.borme_fetcher import fetch_pdf

    data = await fetch_pdf("https://example.com/borme.pdf")
    assert data == fake_pdf


@pytest.mark.asyncio
async def test_fetch_pdf_returns_none_on_404(httpx_mock):
    httpx_mock.add_response(url="https://example.com/missing.pdf", status_code=404)
    from app.sync.borme_fetcher import fetch_pdf

    assert await fetch_pdf("https://example.com/missing.pdf") is None


def test_provincia_to_ine_maps_known_variants():
    from app.sync.borme_fetcher import _provincia_to_ine

    assert _provincia_to_ine("ALICANTE/ALACANT") == "03"
    assert _provincia_to_ine("Barcelona") == "08"  # case insensitive
    assert _provincia_to_ine("A CORUÑA") == "15"
    assert _provincia_to_ine("LA CORUÑA") == "15"
    assert _provincia_to_ine("CASTELLÓN/CASTELLÓ") == "12"
    assert _provincia_to_ine("BIZKAIA") == "48"
    assert _provincia_to_ine("VIZCAYA") == "48"
    assert _provincia_to_ine("FANTASIA") is None
