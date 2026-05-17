from datetime import date, timedelta

from app.db.models import Subvencion
from app.matching.filter import EmpresaProfile
from app.matching.service import rank_for


def test_rank_for_returns_ranked_results(db_session):
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

    perfil = EmpresaProfile(cnae="6201", tamano="pequena", provincia="08", finalidad=["digitalizacion"])
    results = rank_for(db_session, perfil, limit=10)

    assert len(results) == 1
    assert results[0].rank == 1
    assert results[0].score > 0
    assert results[0].razon is None
