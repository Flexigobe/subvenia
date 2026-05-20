from datetime import date, timedelta

import pytest

from app.db.models import Subvencion
from app.matching.filter import EmpresaProfile
from app.matching.service import rank_for


@pytest.mark.asyncio
async def test_rank_for_returns_ranked_results(db_session, monkeypatch):
    # Disable LLM by clearing API key — uses deterministic score
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "gemini_api_key", "")

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

    perfil = EmpresaProfile(
        cnae="6201", tamano="pequena", provincia="08", finalidad=["digitalizacion"]
    )
    results = await rank_for(db_session, perfil, limit=10)

    assert len(results) == 1
    assert results[0].rank == 1
    assert results[0].score > 0
    # Ahora razón es generada por el analyzer determinista
    assert results[0].razon is not None
    assert "CNAE" in results[0].razon or "tamaño" in results[0].razon or "Cierra" in results[0].razon
    assert results[0].applicable is True
