import json
from datetime import date
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "bdns" / "detail_sample.json"


def test_map_detail_returns_subvencion_fields():
    from app.sync.bdns_mappers import map_detail

    detail = json.loads(FIXTURE.read_text())
    mapped = map_detail(detail)

    assert mapped["source"] == "bdns"
    assert mapped["external_id"] == str(detail["codigoBDNS"])
    assert mapped["raw_payload"] == detail
    # ambito viene de nivel1 normalizado
    nivel1 = (detail.get("organo") or {}).get("nivel1", "").upper()
    if nivel1 == "LOCAL":
        assert mapped["ambito"] == "local"
    elif nivel1 == "ESTATAL":
        assert mapped["ambito"] == "estatal"
    # estado depende de abierto
    assert mapped["estado"] in ("abierta", "cerrada")
    # finalidad es lista (puede estar vacía)
    assert isinstance(mapped["finalidad"], list)
    # cnae_elegible es lista (puede estar vacía si no hay sectores)
    assert isinstance(mapped["cnae_elegible"], list)


def test_map_detail_with_full_data():
    from app.sync.bdns_mappers import map_detail

    detail = {
        "codigoBDNS": "TEST-001",
        "descripcion": "Ayuda digital",
        "organo": {"nivel1": "ESTATAL", "nivel2": "MINISTERIO", "nivel3": "DG"},
        "presupuestoTotal": 1000000.0,
        "fechaInicioSolicitud": "2026-06-01",
        "fechaFinSolicitud": "2026-12-31",
        "tiposBeneficiarios": [{"descripcion": "PYMES"}],
        "sectores": [{"codigo": "62", "descripcion": "Programación"}],
        "descripcionFinalidad": "Promover la digitalización empresarial",
        "descripcionBasesReguladoras": "Bases reguladoras completas...",
        "urlBasesReguladoras": "https://www.boe.es/x",
        "anuncios": [{"url": "https://www.boe.es/anuncio/y", "desDiarioOficial": "BOE", "datPublicacion": "2026-06-01"}],
        "abierto": True,
    }

    mapped = map_detail(detail)

    assert mapped["external_id"] == "TEST-001"
    assert mapped["importe_total"] == 1000000.0
    assert mapped["fecha_inicio"] == date(2026, 6, 1)
    assert mapped["fecha_fin"] == date(2026, 12, 31)
    assert mapped["beneficiarios"] == {"tipos": ["PYMES"]}
    assert mapped["cnae_elegible"] == ["62"]
    assert "digitalizacion" in mapped["finalidad"]
    assert mapped["enlace_oficial"] == "https://www.boe.es/anuncio/y"
    assert mapped["estado"] == "abierta"
    assert mapped["ambito"] == "estatal"
    assert mapped["organismo"] == "DG"  # nivel3 preferred
    # descripcion bases reguladoras gana sobre descripcion corta
    assert mapped["descripcion"] == "Bases reguladoras completas..."


def test_map_detail_handles_missing_optional_fields():
    from app.sync.bdns_mappers import map_detail

    detail = {"codigoBDNS": "MIN-001", "descripcion": "x"}
    mapped = map_detail(detail)

    assert mapped["fecha_inicio"] is None
    assert mapped["fecha_fin"] is None
    assert mapped["importe_total"] is None
    assert mapped["cnae_elegible"] == []
    assert mapped["finalidad"] == []
    assert mapped["enlace_oficial"] is None
    assert mapped["beneficiarios"] is None
    assert mapped["estado"] == "cerrada"  # abierto missing → default to cerrada
    assert mapped["ambito"] == "estatal"  # default


def test_infer_finalidad_keywords():
    from app.sync.bdns_mappers import infer_finalidad

    assert "digitalizacion" in infer_finalidad("Programa de transformación digital")
    assert "i+d" in infer_finalidad("Apoyo a I+D+i empresarial")
    assert "contratacion" in infer_finalidad("Bonificaciones a la contratación")
    assert "eficiencia_energetica" in infer_finalidad("Eficiencia energética y renovables")
    assert "internacionalizacion" in infer_finalidad("Apoyo a la internacionalización y exportación")
    assert "formacion" in infer_finalidad("Programa de formación profesional")
    assert "innovacion" in infer_finalidad("Innovación en procesos productivos")
    # Sin keyword → otros si hay texto, vacío si no
    assert infer_finalidad("") == []
    assert infer_finalidad(None) == []
    assert "otros" in infer_finalidad("Algo no clasificado específicamente")


@pytest.mark.asyncio
async def test_fetch_detail_returns_parsed_json(httpx_mock):
    payload = {"codigoBDNS": "X1", "descripcion": "Test"}
    httpx_mock.add_response(
        url="https://www.infosubvenciones.es/bdnstrans/api/convocatorias?numConv=X1",
        json=payload,
    )
    from app.sync.bdns_enricher import fetch_detail

    result = await fetch_detail("X1")
    assert result == payload


@pytest.mark.asyncio
async def test_fetch_detail_returns_none_on_204(httpx_mock):
    httpx_mock.add_response(
        url="https://www.infosubvenciones.es/bdnstrans/api/convocatorias?numConv=NOEXISTE",
        status_code=204,
    )
    from app.sync.bdns_enricher import fetch_detail

    result = await fetch_detail("NOEXISTE")
    assert result is None


@pytest.mark.asyncio
async def test_enrich_existing_only_processes_empty_records(db_session, httpx_mock):
    from datetime import date
    from app.db.models import Subvencion
    from app.sync.bdns_enricher import enrich_existing

    sub_empty = Subvencion(
        source="bdns", external_id="EMPTY-1", titulo="t", ambito="estatal",
        cnae_elegible=[], finalidad=[], estado="abierta",
    )
    sub_filled = Subvencion(
        source="bdns", external_id="FILLED-1", titulo="t", ambito="estatal",
        cnae_elegible=[], finalidad=[], estado="abierta",
        importe_total=1000, fecha_fin=date(2026, 12, 31),
    )
    db_session.add_all([sub_empty, sub_filled])
    db_session.commit()

    httpx_mock.add_response(
        url="https://www.infosubvenciones.es/bdnstrans/api/convocatorias?numConv=EMPTY-1",
        json={
            "codigoBDNS": "EMPTY-1",
            "descripcion": "Enriched title",
            "presupuestoTotal": 50000,
            "fechaFinSolicitud": "2026-09-30",
            "descripcionFinalidad": "Digitalización",
            "sectores": [{"codigo": "62", "descripcion": "x"}],
            "abierto": True,
        },
    )

    stats = await enrich_existing(db_session)
    assert stats["enriched"] == 1
    assert stats["total"] == 1
    db_session.refresh(sub_empty)
    assert sub_empty.importe_total == 50000
    assert "digitalizacion" in sub_empty.finalidad
