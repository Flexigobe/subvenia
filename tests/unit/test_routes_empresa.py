"""Tests for /api/empresa/search autocomplete endpoint."""

from fastapi.testclient import TestClient

from app.db.models import Empresa
from app.db.session import get_db
from app.main import app
from tests.conftest import TestSessionLocal


def _override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db
client = TestClient(app)


def _seed(
    db_session,
    slug: str,
    razon: str,
    provincia: str = "08",
    hoja: str | None = None,
    objeto_social: str | None = None,
    domicilio: str | None = None,
):
    """Insert an empresa; hoja_rm defaults to a unique value derived from slug."""
    db_session.add(Empresa(
        slug=slug,
        razon_social=razon,
        provincia=provincia,
        hoja_rm=hoja or f"H X {slug.replace(' ', '')}",
        objeto_social=objeto_social,
        domicilio=domicilio,
    ))


def test_empresa_search_empty_returns_empty(db_session):
    response = client.get("/api/empresa/search?q=")
    assert response.status_code == 200
    assert response.text == ""


def test_empresa_search_short_query_returns_empty(db_session):
    response = client.get("/api/empresa/search?q=a")  # 1 char
    assert response.status_code == 200
    assert response.text == ""


def test_empresa_search_returns_matches_by_slug_prefix(db_session):
    _seed(db_session, "flexigobe", "FLEXIGOBE SL")
    _seed(db_session, "flex sistemas", "FLEX SISTEMAS SA")
    _seed(db_session, "acme", "ACME SL")
    db_session.commit()

    response = client.get("/api/empresa/search?q=flex")
    assert response.status_code == 200
    assert "FLEXIGOBE SL" in response.text
    assert "FLEX SISTEMAS SA" in response.text
    assert "ACME SL" not in response.text


def test_empresa_search_normalizes_accents_and_suffix(db_session):
    _seed(db_session, "flexigobe", "FLEXIGOBE SL")
    db_session.commit()

    # Query with accent and suffix — slugify should normalize to "flexigobe"
    response = client.get("/api/empresa/search?q=Flexigob%C3%A8+SL")
    assert response.status_code == 200
    assert "FLEXIGOBE SL" in response.text


def test_empresa_search_limit_10_results(db_session):
    for i in range(15):
        _seed(db_session, f"acme{i:02d}", f"ACME{i:02d} SL", hoja=f"H X acme{i:02d}")
    db_session.commit()

    response = client.get("/api/empresa/search?q=acme")
    # Count li elements in the response
    count = response.text.count("<li>")
    assert count == 10


# ── Tests sesión 2026-05-18: enriquecimiento CNAE + dedup + Wikidata ──

def test_empresa_search_infers_cnae_from_objeto_social(db_session):
    """Cuando hay objeto_social, el endpoint devuelve data-cnae con el código inferido."""
    _seed(db_session, "flexibles y accesorios gobe sl", "FLEXIBLES Y ACCESORIOS GOBE SL",
          objeto_social="EL COMERCIO POR MAYOR Y POR MENOR DE PRODUCTOS DE FERRETERIA")
    db_session.commit()

    response = client.get("/api/empresa/search?q=flexibles")
    assert response.status_code == 200
    assert 'data-cnae="4674"' in response.text
    assert "ferreter" in response.text.lower()


def test_empresa_search_dedupes_picks_most_complete(db_session):
    """Cuando BORME tiene varias filas para la misma empresa (slug + razón + provincia
    iguales), nos quedamos con la más completa (la que tiene objeto_social, domicilio,
    etc.). El usuario nunca verá la versión "vacía" mezclada con la rica."""
    _seed(db_session, "flexibles y accesorios gobe sl", "FLEXIBLES Y ACCESORIOS GOBE SL",
          hoja="H1", objeto_social=None, domicilio=None)
    _seed(db_session, "flexibles y accesorios gobe sl", "FLEXIBLES Y ACCESORIOS GOBE SL",
          hoja="H2",
          objeto_social="COMERCIO POR MAYOR DE PRODUCTOS DE FERRETERIA",
          domicilio="CL ROGER DE LLURIA 54 (BARCELONA)")
    db_session.commit()

    response = client.get("/api/empresa/search?q=flexibles")
    assert response.text.count("<li>") == 1  # 1 sola opción tras dedup
    assert 'data-cnae="4674"' in response.text


def test_empresa_search_dedupes_keeps_different_provinces(db_session):
    """Empresas con MISMO nombre en provincias DIFERENTES son distintas: ambas aparecen."""
    _seed(db_session, "consultora xyz", "CONSULTORA XYZ SL", provincia="08", hoja="H1",
          objeto_social="CONSULTORIA INFORMATICA")
    _seed(db_session, "consultora xyz", "CONSULTORA XYZ SL", provincia="28", hoja="H2",
          objeto_social="ASESORIA FISCAL")
    db_session.commit()

    response = client.get("/api/empresa/search?q=consultora")
    assert response.text.count("<li>") == 2
    assert 'data-provincia="08"' in response.text
    assert 'data-provincia="28"' in response.text


def test_empresa_search_without_objeto_social_falls_back_to_razon_social(db_session):
    """Sin objeto_social, el inferer cae al razón social. Como "XYZ" no tiene
    palabras-clave reconocibles, devuelve CNAE vacío."""
    _seed(db_session, "xyz sl", "XYZ SL")
    db_session.commit()

    response = client.get("/api/empresa/search?q=xyz")
    assert response.status_code == 200
    assert 'data-cnae=""' in response.text


def test_empresa_search_infers_cnae_from_razon_social_alone(db_session):
    """Si BORME no tiene objeto_social pero la razón social contiene la actividad
    (ej. "TECH CONSULTING MADRID SL"), el inferer la usa como fallback."""
    _seed(db_session, "tech consulting madrid sl", "TECH CONSULTING MADRID SL")
    db_session.commit()

    response = client.get("/api/empresa/search?q=tech+consulting")
    assert response.status_code == 200
    # "consulting" debería matchear CNAE 7022 (consultoría empresarial) o 6202 (IT consulting)
    assert 'data-cnae="6202"' in response.text or 'data-cnae="7022"' in response.text


def test_empresa_search_accepts_razon_social_param(db_session):
    """El form HTMX envía el parámetro como `razon_social=` (nombre del input), no `q=`.
    El endpoint debe aceptar ambos."""
    _seed(db_session, "flexigobe", "FLEXIGOBE SL")
    db_session.commit()

    response = client.get("/api/empresa/search?razon_social=flex")
    assert response.status_code == 200
    assert "FLEXIGOBE SL" in response.text


def test_empresa_search_mixes_wikidata_and_borme(db_session):
    """Las empresas con hoja_rm 'WD:Qxxx' (de Wikidata) y las normales BORME conviven
    en la misma tabla y el autocomplete las devuelve sin distinguir, después de dedup."""
    # Wikidata: BBVA SA (no existe en BORME)
    _seed(db_session, "banco bilbao vizcaya argentaria",
          "BANCO BILBAO VIZCAYA ARGENTARIA",
          provincia="48", hoja="WD:Q806198",
          objeto_social="servicios financieros")
    # BORME: una filial
    _seed(db_session, "bbva broker correduria de seguros",
          "BBVA BROKER CORREDURIA DE SEGUROS Y REASEGUROS SA",
          provincia="28", hoja="H1",
          objeto_social="correduria de seguros")
    db_session.commit()

    # Búsqueda 'banco bilbao' encuentra BBVA matriz (Wikidata)
    r = client.get("/api/empresa/search?q=banco+bilbao")
    assert r.status_code == 200
    assert "BANCO BILBAO VIZCAYA ARGENTARIA" in r.text
    # Búsqueda 'bbva' encuentra la filial BORME
    r = client.get("/api/empresa/search?q=bbva")
    assert "BBVA BROKER" in r.text


def test_empresa_search_tokenized_fallback_finds_words_in_any_order(db_session):
    """Cuando el prefix match falla, el fallback tokenizado encuentra empresas con
    todas las palabras en cualquier orden."""
    _seed(db_session, "telefonica innovacion digital sl",
          "TELEFONICA INNOVACION DIGITAL SL",
          objeto_social="desarrollo de aplicaciones")
    db_session.commit()

    # Buscar con palabras en otro orden ("digital telefonica") no matchea por prefijo
    # pero sí por tokens
    r = client.get("/api/empresa/search?q=digital+telefonica")
    assert r.status_code == 200
    assert "TELEFONICA INNOVACION DIGITAL SL" in r.text


def test_empresa_search_multiple_sectors_all_get_correct_cnae(db_session):
    """Test exhaustivo: 10 empresas de sectores distintos, todas deben recibir CNAE."""
    cases = [
        ("software lab sl",        "SOFTWARE LAB SL",      "DESARROLLO DE SOFTWARE Y APLICACIONES",          "6201"),
        ("restaurante el bueno",   "RESTAURANTE EL BUENO", "RESTAURANTE Y SERVICIO DE COMIDAS",              "5610"),
        ("transportes lopez sl",   "TRANSPORTES LOPEZ SL", "TRANSPORTE DE MERCANCIAS POR CARRETERA",          "4941"),
        ("inmuebles madrid sl",    "INMUEBLES MADRID SL",  "ALQUILER DE INMUEBLES Y PROMOCION INMOBILIARIA", "4110"),
        ("asesoria fiscal sl",     "ASESORIA FISCAL SL",   "ASESORIA FISCAL Y CONTABILIDAD",                 "6920"),
        ("ferretera barcelona sl", "FERRETERA BCN SL",     "COMERCIO AL POR MAYOR DE FERRETERIA",            "4674"),
        ("hotel central sl",       "HOTEL CENTRAL SL",     "EXPLOTACION DE HOTEL Y ALOJAMIENTO",             "5510"),
        ("clinica dental sl",      "CLINICA DENTAL SL",    "ACTIVIDADES ODONTOLOGICAS",                      "8623"),
        ("publicitaria sl",        "PUBLICITARIA SL",      "PUBLICIDAD Y MARKETING DIGITAL",                 "7311"),
        ("ingenieria abc sl",      "INGENIERIA ABC SL",    "INGENIERIA Y CONSULTORIA TECNICA",               "7112"),
    ]
    for slug, razon, objeto, _ in cases:
        _seed(db_session, slug, razon, objeto_social=objeto)
    db_session.commit()

    for slug, razon, objeto, expected_cnae in cases:
        first_word = razon.split()[0].lower()
        response = client.get(f"/api/empresa/search?q={first_word}")
        assert response.status_code == 200, f"Failed for {razon}"
        assert razon in response.text, f"{razon} not in response"
        assert f'data-cnae="{expected_cnae}"' in response.text, (
            f"Expected CNAE {expected_cnae} for {razon} (objeto={objeto!r})"
        )
