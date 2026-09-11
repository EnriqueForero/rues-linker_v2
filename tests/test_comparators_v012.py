"""Tests de los arreglos v0.12.0 en comparadores y combinador (M5, M6, M8).

Cubren tres hallazgos de la auditoría 2026-08-26:
    - prefix_weight de JaroWinklerSigned era un parámetro MUERTO (se guardaba
      en __init__ y jamás llegaba a cpdist).
    - La validez de un par se infería de ``score != 0.0``: en comparadores no
      firmados un 0.0 significa "muy distinto", no "faltante", y el par se
      excluía de la masa efectiva inflando el score combinado (C5).
    - cpdist corría con workers=1 (un núcleo de los dos de Colab).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.matching.combiner import VariableMatcher
from record_linkage.matching.comparators import (
    ExactWithDV,
    FechaDelta,
    GeoHaversine,
    JaroWinklerSigned,
    NumericoRelativo,
    PhoneLastDigits,
)
from record_linkage.matching.spec import MatchingProfile, VariableSpec

# ─────────────────────────────────────────────────────────────────────
# M5 · prefix_weight vivo
# ─────────────────────────────────────────────────────────────────────


def test_prefix_weight_cambia_el_score():
    """Con prefijo común largo, subir prefix_weight debe SUBIR la similitud.

    En 0.11.x este test falla: ambos objetos devolvían exactamente lo mismo
    porque el parámetro nunca llegaba a cpdist.
    """
    left = np.array(["CONSTRUCTORA BOLIVAR SA"])
    right = np.array(["CONSTRUCTORA BOLIVAR LTDA"])
    s_estandar = JaroWinklerSigned(prefix_weight=0.1).compare(left, right)[0]
    s_prefijo = JaroWinklerSigned(prefix_weight=0.25).compare(left, right)[0]
    assert s_prefijo != s_estandar
    assert s_prefijo > s_estandar  # el prefijo común pesa más


def test_prefix_weight_default_reproduce_0_11():
    """Con el default 0.1 el valor es el estándar JW (paridad 0.11.x)."""
    left = np.array(["KANGNAM PRIMEINC"])
    right = np.array(["KANGNAM TEXTILE"])
    obtenido = JaroWinklerSigned().compare(left, right)[0]
    from rapidfuzz.distance import JaroWinkler

    esperado = 2.0 * JaroWinkler.normalized_similarity("KANGNAM PRIMEINC", "KANGNAM TEXTILE") - 1.0
    assert obtenido == pytest.approx(esperado, abs=1e-12)


# ─────────────────────────────────────────────────────────────────────
# M6 · valid_mask: faltante ≠ muy distinto
# ─────────────────────────────────────────────────────────────────────


def test_valid_mask_numerico_distingue_faltante_de_distinto():
    comp = NumericoRelativo(tolerancia=0.10)
    left = np.array(["100", "100", None], dtype=object)
    right = np.array(["1000", "101", "50"], dtype=object)
    scores = comp.compare(left, right)
    valid = comp.valid_mask(left, right)
    # Par 0: ambos presentes y MUY distintos → score 0.0 pero VÁLIDO.
    assert scores[0] == 0.0 and bool(valid[0]) is True
    # Par 2: faltante → inválido.
    assert bool(valid[2]) is False


def test_valid_mask_geo_y_fecha():
    geo = GeoHaversine(radio_km=1.0)
    lat_lon_a = np.array([[4.60, -74.08], [np.nan, -74.08]])
    lat_lon_b = np.array([[4.70, -74.05], [4.60, -74.08]])
    valid = geo.valid_mask(lat_lon_a, lat_lon_b)
    assert valid.tolist() == [True, False]
    # A ~11 km con radio 1 km la similitud es 0.0 pero el par ES válido.
    assert geo.compare(lat_lon_a, lat_lon_b)[0] == 0.0

    fecha = FechaDelta(dias_tolerancia=30)
    v = fecha.valid_mask(np.array(["2024-01-01", ""]), np.array(["2024-01-31", "2024-01-01"]))
    assert v.tolist() == [True, False]
    # delta == tolerancia → score exactamente 0.0, pero par válido.
    assert fecha.compare(np.array(["2024-01-01"]), np.array(["2024-01-31"]))[0] == 0.0


def test_todos_los_comparadores_exponen_valid_mask():
    from record_linkage.matching import comparators as c

    clases = [
        c.ExactWithDV,
        c.ExactSigned,
        c.CategoricalSigned,
        c.ExactOrZero,
        c.JaroWinklerSigned,
        c.TokenSetSigned,
        c.TokenSortSigned,
        c.PhoneLastDigits,
        c.EmailDomainLocal,
        c.CityNormalizedEqual,
        c.AddressTokenSet,
    ]
    for cls in clases:
        assert callable(getattr(cls(), "valid_mask", None)), cls.__name__
    assert callable(FechaDelta().valid_mask)
    assert callable(GeoHaversine().valid_mask)
    assert callable(NumericoRelativo().valid_mask)


def test_combinador_no_infla_score_con_unsigned_cero():
    """El caso C5: variable numérica presente y MUY distinta debe DILUIR el
    score, no desaparecer de la masa efectiva.

    Perfil: nombre (peso 1) + valor numérico (peso 1). Par con nombre idéntico
    y valor totalmente distinto:
        0.11.x → masa = solo nombre → score 1.0 (inflado).
        0.12.0 → masa = nombre + numérico → score 0.5.
    """
    df = pd.DataFrame(
        {
            "NOMBRE": ["ACME SAS", "ACME SAS"],
            "VALOR": ["100", "10000"],
            "NIT": ["", ""],
        }
    )
    profile = MatchingProfile(
        variables=[
            VariableSpec("NOMBRE", JaroWinklerSigned(), weight=1.0),
            VariableSpec("VALOR", NumericoRelativo(tolerancia=0.10), weight=1.0),
        ],
        min_concordances_with_nit=1,
        min_concordances_without_nit=1,
        score_threshold=0.4,
    )
    pairs = pd.DataFrame({"id_left": [0], "id_right": [1]})
    out = VariableMatcher(profile).score_pairs(pairs, df)
    assert out.loc[0, "score_NOMBRE"] == pytest.approx(1.0)
    assert out.loc[0, "score_VALOR"] == pytest.approx(0.0)
    # Masa efectiva = ambos pesos → score combinado 0.5, no 1.0.
    assert out.loc[0, "score"] == pytest.approx(0.5, abs=1e-9)


def test_combinador_faltante_sigue_fuera_de_la_masa():
    """Un valor REALMENTE faltante sí se excluye de la masa (renormaliza)."""
    df = pd.DataFrame(
        {
            "NOMBRE": ["ACME SAS", "ACME SAS"],
            "VALOR": [None, "10000"],
            "NIT": ["", ""],
        }
    )
    profile = MatchingProfile(
        variables=[
            VariableSpec("NOMBRE", JaroWinklerSigned(), weight=1.0),
            VariableSpec("VALOR", NumericoRelativo(tolerancia=0.10), weight=1.0),
        ],
        min_concordances_with_nit=1,
        min_concordances_without_nit=1,
        score_threshold=0.4,
    )
    pairs = pd.DataFrame({"id_left": [0], "id_right": [1]})
    out = VariableMatcher(profile).score_pairs(pairs, df)
    assert out.loc[0, "score"] == pytest.approx(1.0, abs=1e-9)  # solo nombre pesa


# ─────────────────────────────────────────────────────────────────────
# M8 · workers=-1 no cambia valores
# ─────────────────────────────────────────────────────────────────────


def test_workers_multihilo_paridad_de_valores():
    """cpdist con workers=-1 produce exactamente los mismos scores."""
    base = ["COMERCIALIZADORA ANDINA", "TEXTILES DEL PACIFICO", "CAFE EXPORT"]
    left = np.array([f"{base[i % 3]} {i}" for i in range(500)])
    right = np.array([f"{base[(i + 1) % 3]} {i}" for i in range(500)])
    got = JaroWinklerSigned().compare(left, right)
    from rapidfuzz.distance import JaroWinkler

    esperado = np.array(
        [
            2.0 * JaroWinkler.normalized_similarity(a, b) - 1.0
            for a, b in zip(left, right, strict=True)
        ]
    )
    np.testing.assert_allclose(got, esperado, atol=1e-12)
    # Determinismo entre corridas multihilo
    np.testing.assert_array_equal(got, JaroWinklerSigned().compare(left, right))


def test_valid_mask_identificador_y_telefono():
    dv = ExactWithDV()
    v = dv.valid_mask(np.array(["900123456-1", "123"]), np.array(["900123456", "900123456"]))
    assert v.tolist() == [True, False]
    tel = PhoneLastDigits(n=7)
    v2 = tel.valid_mask(np.array(["3012345678", "123"]), np.array(["3012345678", "3012345678"]))
    assert v2.tolist() == [True, False]
