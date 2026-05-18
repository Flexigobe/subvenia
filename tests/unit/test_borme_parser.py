"""Tests for BORME PDF parser. Focuses on the text-parsing path; pypdf extract
is tested separately via small synthetic PDF bytes if needed."""

from datetime import date
from decimal import Decimal

# ─── slugify ─────────────────────────────────────────────────────────────────


def test_slugify_strips_sl_suffix():
    from app.sync.borme_parser import slugify
    assert slugify("FLEXIGOBE SL") == "flexigobe"


def test_slugify_strips_accents_and_lowercases():
    from app.sync.borme_parser import slugify
    assert slugify("ALEMAÑA S.A.U.") == "alemana"


def test_slugify_strips_multiple_suffix_variants():
    from app.sync.borme_parser import slugify
    for raw, expected in [
        ("ACME SL", "acme"),
        ("ACME S.L.", "acme"),
        ("ACME S.L.U.", "acme"),
        ("ACME SA", "acme"),
        ("ACME S.A.U.", "acme"),
        ("ACME SLNE", "acme"),
        ("COCINAS Y BAÑOS DEL NORTE, S.L.", "cocinas y banos del norte"),
        ("COOPERATIVA DE TRABAJO COOP", "cooperativa de trabajo"),
    ]:
        assert slugify(raw) == expected, f"failed: {raw} → got {slugify(raw)!r}"


def test_slugify_empty_returns_empty():
    from app.sync.borme_parser import slugify
    assert slugify("") == ""
    assert slugify("   ") == ""


# ─── parse_pdf_text ──────────────────────────────────────────────────────────


CONSTITUCION_ENTRY = """
218391 - NOPESABOX VILLENA SL.
Constitución. Comienzo de operaciones: 11.04.25. Objeto social: La explotación
de negocios dedicados a la actividad de gimnasios. Domicilio: C/ SANTO CRISTO 5 1º 1
(BANYERES DE MARIOLA). Capital: 3.000,00 Euros. Nombramientos. Adm. Solid.:
MAESTRE SANTIAGO ERNESTO. Datos registrales. S 8, H A 197635, I/A 1 (8.05.25).
"""


def test_parse_constitucion_full():
    from app.sync.borme_parser import parse_pdf_text

    entries = parse_pdf_text(CONSTITUCION_ENTRY, provincia_code="03")
    assert len(entries) == 1
    e = entries[0]
    assert e["razon_social"] == "NOPESABOX VILLENA SL"
    assert e["slug"] == "nopesabox villena"
    assert e["provincia"] == "03"
    assert e["domicilio"] is not None and "SANTO CRISTO" in e["domicilio"]
    assert e["objeto_social"] is not None and "gimnasios" in e["objeto_social"].lower()
    assert e["capital_social"] == Decimal("3000.00")
    assert e["fecha_constitucion"] == date(2025, 4, 11)
    assert e["hoja_rm"] is not None and "H A 197635" in e["hoja_rm"]
    assert e["fecha_ultima_act"] == date(2025, 5, 8)
    assert e["estado"] == "activa"
    tipos = {a["tipo"] for a in e["actos"]}
    assert "Constitución" in tipos
    assert "Nombramientos" in tipos


NOMBRAMIENTOS_ONLY = """
218392 - ACME SL.
Nombramientos. Adm. Único: PEREZ GARCIA JUAN. Datos registrales. S 8, H A 197636, I/A 1 (8.05.25).
"""


def test_parse_nombramientos_only_no_capital_or_objeto():
    from app.sync.borme_parser import parse_pdf_text

    entries = parse_pdf_text(NOMBRAMIENTOS_ONLY, provincia_code="08")
    assert len(entries) == 1
    e = entries[0]
    assert e["razon_social"] == "ACME SL"
    assert e["capital_social"] is None
    assert e["objeto_social"] is None
    assert e["fecha_constitucion"] is None
    assert e["fecha_ultima_act"] == date(2025, 5, 8)
    assert e["estado"] == "activa"


DISOLUCION_ENTRY = """
218393 - VIEJA EMPRESA SL.
Disolución. Datos registrales. S 8, H A 197637, I/A 1 (8.05.25).
"""


def test_parse_disolucion_marks_disuelta():
    from app.sync.borme_parser import parse_pdf_text

    entries = parse_pdf_text(DISOLUCION_ENTRY, provincia_code="08")
    assert len(entries) == 1
    assert entries[0]["estado"] == "disuelta"


CONCURSO_ENTRY = """
218394 - PROBLEMAS SL.
Declaración de concurso. Datos registrales. S 8, H A 197638, I/A 1 (8.05.25).
"""


def test_parse_concurso_marks_concursal():
    from app.sync.borme_parser import parse_pdf_text

    entries = parse_pdf_text(CONCURSO_ENTRY, provincia_code="08")
    assert len(entries) == 1
    assert entries[0]["estado"] == "concursal"


MULTIPLE_ENTRIES = """
\n218391 - PRIMERA SL.
Constitución. Comienzo de operaciones: 11.04.25. Capital: 3.000,00 Euros.
Datos registrales. S 8, H A 100001, I/A 1 (8.05.25).

218392 - SEGUNDA SL.
Constitución. Comienzo de operaciones: 12.04.25. Capital: 4.000,00 Euros.
Datos registrales. S 8, H A 100002, I/A 1 (9.05.25).

218393 - TERCERA SL.
Modificación. Datos registrales. S 8, H A 100003, I/A 2 (10.05.25).
"""


def test_parse_multiple_entries_in_one_text():
    from app.sync.borme_parser import parse_pdf_text

    entries = parse_pdf_text(MULTIPLE_ENTRIES, provincia_code="08")
    assert len(entries) == 3
    slugs = [e["slug"] for e in entries]
    assert slugs == ["primera", "segunda", "tercera"]
    # The third entry has only Modificación, no Constitución
    assert entries[2]["capital_social"] is None
    assert entries[2]["fecha_constitucion"] is None


def test_parse_returns_empty_for_empty_text():
    from app.sync.borme_parser import parse_pdf_text

    assert parse_pdf_text("", provincia_code="08") == []
    assert parse_pdf_text("\n\n\n", provincia_code="08") == []


# ─── extract_pdf_text (sanity) ───────────────────────────────────────────────


def test_extract_pdf_text_returns_empty_for_invalid_bytes():
    """pypdf returns empty/error for non-PDF bytes — wrapper should swallow and return ''."""
    from app.sync.borme_parser import extract_pdf_text

    assert extract_pdf_text(b"not a pdf") == ""
    assert extract_pdf_text(b"") == ""
