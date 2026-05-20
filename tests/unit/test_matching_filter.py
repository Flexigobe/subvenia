from datetime import date, timedelta

import pytest

from app.db.models import Subvencion


@pytest.fixture
def perfil_pyme_digital():
    from app.matching.filter import EmpresaProfile

    return EmpresaProfile(
        cnae="6201",
        tamano="pequena",
        provincia="08",  # Barcelona → CCAA Cataluña
        finalidad=["digitalizacion"],
    )


def _make_subvencion(**kwargs):
    raw_id = kwargs.pop("external_id", "001")
    defaults = dict(
        source="bdns",
        external_id=f"TEST-{raw_id}",
        titulo="Test",
        ambito="estatal",
        cnae_elegible=[],
        finalidad=[],
        estado="abierta",
        fecha_fin=date.today() + timedelta(days=60),
        beneficiarios={"tamanos": ["micro", "pequena", "mediana", "grande"]},
    )
    defaults.update(kwargs)
    return Subvencion(**defaults)


def test_filter_excludes_expired_fecha_fin(db_session, perfil_pyme_digital):
    """El flag BDNS `estado` no es fiable (muchos records marcados 'cerrada' siguen
    aceptando solicitudes), así que el filtro se basa en `fecha_fin` como fuente de
    verdad. Un record con fecha_fin pasada se excluye independientemente del estado."""
    from app.matching.filter import find_candidates

    db_session.add(_make_subvencion(
        external_id="A",
        estado="abierta",
        fecha_fin=date.today() - timedelta(days=1),
        finalidad=["digitalizacion"],
        cnae_elegible=["6201"],
    ))
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)
    assert len(results) == 0


def test_filter_includes_cerrada_flag_if_fecha_fin_future(db_session, perfil_pyme_digital):
    """BDNS marca muchos records como 'cerrada' aunque sigan abiertos; el filtro debe
    confiar en fecha_fin y mostrarlos cuando aún hay plazo."""
    from app.matching.filter import find_candidates

    db_session.add(_make_subvencion(
        external_id="A",
        estado="cerrada",  # BDNS dice cerrada
        fecha_fin=date.today() + timedelta(days=30),  # ...pero hay plazo abierto
        finalidad=["digitalizacion"],
        cnae_elegible=["6201"],
    ))
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)
    assert len(results) == 1


def test_filter_excludes_cnae_no_compatible(db_session, perfil_pyme_digital):
    from app.matching.filter import find_candidates

    db_session.add(_make_subvencion(external_id="A", cnae_elegible=["1010"], finalidad=["digitalizacion"]))
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)
    assert len(results) == 0


def test_filter_includes_when_cnae_elegible_empty(db_session, perfil_pyme_digital):
    from app.matching.filter import find_candidates

    db_session.add(_make_subvencion(external_id="A", cnae_elegible=[], finalidad=["digitalizacion"]))
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)
    assert len(results) == 1


def test_filter_excludes_when_finalidad_no_solapa(db_session, perfil_pyme_digital):
    from app.matching.filter import find_candidates

    db_session.add(_make_subvencion(external_id="A", cnae_elegible=["6201"], finalidad=["contratacion"]))
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)
    assert len(results) == 0


def test_filter_ranks_by_match_quality(db_session, perfil_pyme_digital):
    from app.matching.filter import find_candidates

    # alta_relevancia: cnae exacto + finalidad exacta + cerca de cierre
    alta = _make_subvencion(
        external_id="ALTA",
        titulo="Match perfecto",
        cnae_elegible=["6201"],
        finalidad=["digitalizacion"],
        fecha_fin=date.today() + timedelta(days=10),
    )
    # media: cnae genérico (vacío) + finalidad exacta + lejos
    media = _make_subvencion(
        external_id="MEDIA",
        titulo="Match medio",
        cnae_elegible=[],
        finalidad=["digitalizacion"],
        fecha_fin=date.today() + timedelta(days=120),
    )
    db_session.add_all([alta, media])
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)

    assert len(results) == 2
    assert results[0].subvencion.external_id == "TEST-ALTA"
    assert results[0].score > results[1].score


def test_filter_respects_limit(db_session, perfil_pyme_digital):
    from app.matching.filter import find_candidates

    for i in range(35):
        db_session.add(
            _make_subvencion(
                external_id=f"S{i:03d}",
                cnae_elegible=[],
                finalidad=["digitalizacion"],
            )
        )
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)
    assert len(results) == 30


def test_filter_includes_subvencion_with_empty_finalidad_too(db_session, perfil_pyme_digital):
    """Records sin finalidad clasificada (BDNS detail con descripcionFinalidad vacía o
    sin keyword conocida) deben aparecer en resultados pero con menor score que los que
    sí matchean finalidad. Esto evita que toda la web quede a 0 resultados cuando el
    enrichment no clasifica todos los records."""
    from app.matching.filter import find_candidates

    matches = _make_subvencion(
        external_id="MATCHES", cnae_elegible=["6201"], finalidad=["digitalizacion"]
    )
    generic = _make_subvencion(
        external_id="GENERIC", cnae_elegible=["6201"], finalidad=[]
    )
    db_session.add_all([matches, generic])
    db_session.commit()

    results = find_candidates(db_session, perfil_pyme_digital, limit=30)
    ids = [c.subvencion.external_id for c in results]

    # Both should appear
    assert "TEST-MATCHES" in ids
    assert "TEST-GENERIC" in ids
    # The one that matches finalidad must come FIRST
    assert ids.index("TEST-MATCHES") < ids.index("TEST-GENERIC")
