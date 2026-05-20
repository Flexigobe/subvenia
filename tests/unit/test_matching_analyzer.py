"""Tests for the determinista analyzer empresa-vs-subvención."""

from datetime import date, timedelta

import pytest

from app.db.models import Subvencion
from app.matching.analyzer import analyze
from app.matching.filter import EmpresaProfile


def _make_sub(**kwargs) -> Subvencion:
    defaults = dict(
        source="bdns",
        external_id="TEST-1",
        titulo="Test",
        ambito="estatal",
        cnae_elegible=[],
        finalidad=[],
        estado="abierta",
        fecha_fin=date.today() + timedelta(days=30),
        beneficiarios=None,
        raw_payload={},
    )
    defaults.update(kwargs)
    return Subvencion(**defaults)


def _perfil(**kwargs) -> EmpresaProfile:
    defaults = dict(cnae="6201", tamano="pequena", provincia="08", finalidad=[])
    defaults.update(kwargs)
    return EmpresaProfile(**defaults)


def test_analyze_applicable_when_pyme_and_region_match():
    sub = _make_sub(
        raw_payload={
            "tiposBeneficiarios": [{"descripcion": "PYME y personas físicas con actividad económica"}],
            "regiones": [{"descripcion": "ES511 - Barcelona"}],
        },
        cnae_elegible=["6201"],
        beneficiarios={"tamanos": ["micro", "pequena"]},
    )
    a = analyze(sub, _perfil())
    assert a.applicable is True
    assert any("actividad económica" in r.lower() or "elegibles" in r.lower() for r in a.match_reasons)
    assert any("tu provincia" in r.lower() for r in a.match_reasons)
    assert any("cnae" in r.lower() for r in a.match_reasons)
    assert any("tamaño" in r.lower() or "tamano" in r.lower() for r in a.match_reasons)


def test_analyze_excludes_if_no_profit_only():
    sub = _make_sub(
        raw_payload={
            "tiposBeneficiarios": [
                {"descripcion": "PERSONAS JURÍDICAS QUE NO DESARROLLAN ACTIVIDAD ECONÓMICA"}
            ],
        },
    )
    a = analyze(sub, _perfil())
    assert a.applicable is False
    assert any("sin ánimo" in r.lower() for r in a.exclusion_reasons)


def test_analyze_excludes_if_region_outside():
    sub = _make_sub(
        raw_payload={
            "regiones": [{"descripcion": "ES707 - La Palma"}],  # Provincia 38 Tenerife
        },
    )
    # Usuario Barcelona (08)
    a = analyze(sub, _perfil(provincia="08"))
    assert a.applicable is False
    assert any("ámbito" in r.lower() for r in a.exclusion_reasons)


def test_analyze_excludes_if_tamano_not_eligible():
    sub = _make_sub(beneficiarios={"tamanos": ["grande"]})
    a = analyze(sub, _perfil(tamano="micro"))
    assert a.applicable is False
    assert any("grande" in r.lower() for r in a.exclusion_reasons)


def test_analyze_urgency_signals():
    sub = _make_sub(fecha_fin=date.today() + timedelta(days=5))
    a = analyze(sub, _perfil())
    assert a.urgency_days == 5
    assert any("urgente" in r.lower() or "5 días" in r for r in a.match_reasons)


def test_analyze_nacional_region_matches():
    """Regiones con 'TODO EL MUNDO' o 'ESPAÑA' aplican a cualquier provincia."""
    sub = _make_sub(raw_payload={"regiones": [{"descripcion": "XXXX - TODO EL MUNDO"}]})
    a = analyze(sub, _perfil(provincia="08"))
    assert a.applicable is True


def test_analyze_excludes_natural_persons_only():
    """Becas para personas físicas individuales no aplican a empresas."""
    sub = _make_sub(raw_payload={
        "tiposBeneficiarios": [
            {"descripcion": "PERSONAS FÍSICAS QUE NO DESARROLLAN ACTIVIDAD ECONÓMICA"}
        ],
    })
    a = analyze(sub, _perfil())
    assert a.applicable is False
    assert any("personas físicas" in r.lower() for r in a.exclusion_reasons)


def test_analyze_only_self_employed_excludes_pequena_empresa():
    """Si solo acepta autónomos persona física, una pequeña empresa SL queda fuera."""
    sub = _make_sub(raw_payload={
        "tiposBeneficiarios": [
            {"descripcion": "TRABAJADORES AUTÓNOMOS"}
        ],
    })
    a = analyze(sub, _perfil(tamano="pequena"))
    assert a.applicable is False
    assert any("autónomos" in r.lower() for r in a.exclusion_reasons)


def test_analyze_only_self_employed_accepts_micro():
    """Si solo acepta autónomos, una microempresa (puede ser autónomo) sí aplica."""
    sub = _make_sub(raw_payload={
        "tiposBeneficiarios": [
            {"descripcion": "TRABAJADORES AUTÓNOMOS"}
        ],
    })
    a = analyze(sub, _perfil(tamano="micro"))
    assert a.applicable is True


def test_analyze_cooperativa_for_profit():
    """Las cooperativas son for-profit (no se excluyen)."""
    sub = _make_sub(raw_payload={
        "tiposBeneficiarios": [
            {"descripcion": "COOPERATIVAS Y PYMES"}
        ],
    })
    a = analyze(sub, _perfil())
    assert a.applicable is True


def test_analyze_confidence_grows_with_data():
    """Cuantos más campos rellenos, más confianza tiene el análisis."""
    sub_minimal = _make_sub()
    a_min = analyze(sub_minimal, _perfil())
    sub_rich = _make_sub(
        cnae_elegible=["6201"],
        beneficiarios={"tamanos": ["pequena"]},
        raw_payload={
            "tiposBeneficiarios": [{"descripcion": "PYME"}],
            "regiones": [{"descripcion": "ESPAÑA"}],
            "instrumentos": [{"descripcion": "SUBVENCIÓN"}],
        },
    )
    a_rich = analyze(sub_rich, _perfil())
    assert a_rich.confidence > a_min.confidence
