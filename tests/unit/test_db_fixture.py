from app.db.models import Subvencion


def test_db_session_works(db_session):
    sub = Subvencion(
        source="bdns",
        external_id="TEST-001",
        titulo="Test subvención",
        ambito="estatal",
        cnae_elegible=[],
        finalidad=[],
    )
    db_session.add(sub)
    db_session.commit()
    assert sub.id is not None
