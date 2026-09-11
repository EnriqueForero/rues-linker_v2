"""Comparadores por tipo (F2.2): unit + property-based (hypothesis).

Propiedades universales que TODO comparador debe cumplir:
    - Rango: firmado → [-1, 1]; no firmado → [0, 1].
    - Identidad: comparar un valor no-faltante consigo mismo da el máximo.
    - Simetría: compare(a, b) == compare(b, a).
    - Faltante neutro (salvaguarda F2.4): faltante en cualquier lado → 0.0.
    - Longitud: salida de la misma longitud que la entrada.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import assume, given, settings, strategies as st

from record_linkage.matching.comparators import (
    _INVALID_VALUES,
    ExactWithDV,
    FechaDelta,
    GeoHaversine,
    JaroWinklerSigned,
    NumericoRelativo,
    PhoneLastDigits,
    TokenSetSigned,
)
from record_linkage.matching.normalizadores import PLACEHOLDERS

#: Un comparador trata como faltante su PROPIO conjunto de centinelas, que
#: no coincide con el de los normalizadores: `_INVALID_VALUES` incluye
#: "INVALID" y "NAT", que PLACEHOLDERS no tiene. Excluir solo uno de los
#: dos dejaba una propiedad falsa que Hypothesis encontraba de vez en
#: cuando (con a='INVALID'): una prueba intermitente, que es peor que una
#: que falla siempre.
_CENTINELAS = PLACEHOLDERS | _INVALID_VALUES

# Comparadores 1-D (arrays de strings/valores por par).
_COMPARADORES_1D = [
    JaroWinklerSigned(),
    TokenSetSigned(),
    ExactWithDV(),
    PhoneLastDigits(n=7),
    FechaDelta(30),
    NumericoRelativo(0.1),
]


@pytest.mark.parametrize("cmp", _COMPARADORES_1D, ids=lambda c: c.name)
def test_rango_segun_signed(cmp) -> None:
    izq = np.array(["ACME SAS", "900123456", "2026-01-01", "100.0", "6017502020"])
    der = np.array(["ACME S.A.S.", "900123457", "2026-02-01", "110.0", "6017502021"])
    out = cmp.compare(izq, der)
    lo = -1.0 if cmp.signed else 0.0
    assert out.shape == izq.shape
    assert np.all(out >= lo - 1e-9) and np.all(out <= 1.0 + 1e-9)


@pytest.mark.parametrize("cmp", _COMPARADORES_1D, ids=lambda c: c.name)
def test_faltante_es_neutro(cmp) -> None:
    """Faltante en cualquier lado → 0.0 (salvaguarda F2.4)."""
    izq = np.array(["ACME SAS", "", "900123456"])
    der = np.array(["", "GLOBEX", ""])
    out = cmp.compare(izq, der)
    assert out[0] == 0.0  # faltante a la derecha
    assert out[1] == 0.0  # faltante a la izquierda
    assert out[2] == 0.0  # faltante a la derecha


@pytest.mark.parametrize("cmp", _COMPARADORES_1D, ids=lambda c: c.name)
def test_longitud_vacia(cmp) -> None:
    out = cmp.compare(np.array([], dtype=object), np.array([], dtype=object))
    assert len(out) == 0


def test_identidad_nombre() -> None:
    v = np.array(["COMERCIALIZADORA ANDINA SAS"])
    assert JaroWinklerSigned().compare(v, v)[0] == pytest.approx(1.0)
    assert TokenSetSigned().compare(v, v)[0] == pytest.approx(1.0)


def test_nit_discrepancia_veta() -> None:
    """NITs con base distinta → −1.0 (habilita el veto)."""
    out = ExactWithDV().compare(np.array(["900123456"]), np.array(["800555111"]))
    assert out[0] == -1.0


def test_nit_dv_distinto_es_match() -> None:
    """Mismo NIT base, DV distinto → +1.0 (tolerancia al dígito de verificación)."""
    out = ExactWithDV().compare(np.array(["900123456-1"]), np.array(["900123456-7"]))
    assert out[0] == 1.0


def test_geo_rango_y_faltante() -> None:
    cmp = GeoHaversine(radio_km=1.0)
    izq = np.array([[4.65, -74.05], [4.65, -74.05], [np.nan, -74.0]])
    der = np.array([[4.65, -74.05], [5.65, -74.05], [4.65, -74.05]])
    out = cmp.compare(izq, der)
    assert out[0] == pytest.approx(1.0)  # mismo punto
    assert out[1] == 0.0  # >1 km
    assert out[2] == 0.0  # coordenada faltante
    assert np.all(out >= 0.0) and np.all(out <= 1.0)


def test_numerico_ceros_y_tolerancia() -> None:
    cmp = NumericoRelativo(0.1)
    out = cmp.compare(np.array(["0.0", "100.0"]), np.array(["0.0", "105.0"]))
    assert out[0] == pytest.approx(1.0)  # ambos cero
    assert 0.0 < out[1] < 1.0  # dentro de tolerancia


# ─── Property-based (hypothesis) ───────────────────────────────────────────

_texto = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=90),  # A-Z: siempre contenido real
    min_size=2,
    max_size=20,
)


@settings(max_examples=150, deadline=None)
@given(a=_texto, b=_texto)
def test_prop_simetria_jaro(a: str, b: str) -> None:
    c = JaroWinklerSigned()
    ab = c.compare(np.array([a]), np.array([b]))[0]
    ba = c.compare(np.array([b]), np.array([a]))[0]
    assert ab == pytest.approx(ba, abs=1e-9)


@settings(max_examples=150, deadline=None)
@given(a=_texto)
def test_prop_identidad_jaro(a: str) -> None:
    """Un texto A-Z no-placeholder consigo mismo → +1.0 (identidad).

    Se excluyen los placeholders (NULL, NA, …): por diseño se normalizan a
    faltante y dan 0.0, que es el comportamiento correcto, no un fallo.
    """
    assume(a.upper() not in _CENTINELAS)
    out = JaroWinklerSigned().compare(np.array([a]), np.array([a]))[0]
    assert out == pytest.approx(1.0, abs=1e-9)


@settings(max_examples=100, deadline=None)
@given(
    lat=st.floats(-89, 89, allow_nan=False),
    lon=st.floats(-179, 179, allow_nan=False),
)
def test_prop_geo_identidad_y_simetria(lat: float, lon: float) -> None:
    c = GeoHaversine(radio_km=5.0)
    p = np.array([[lat, lon]])
    assert c.compare(p, p)[0] == pytest.approx(1.0, abs=1e-6)
    q = np.array([[lat + 0.01, lon + 0.01]])
    assert c.compare(p, q)[0] == pytest.approx(c.compare(q, p)[0], abs=1e-9)


@settings(max_examples=150, deadline=None)
@given(
    x=st.floats(-1e6, 1e6, allow_nan=False),
    y=st.floats(-1e6, 1e6, allow_nan=False),
)
def test_prop_numerico_rango_y_simetria(x: float, y: float) -> None:
    c = NumericoRelativo(0.2)
    xy = c.compare(np.array([repr(x)]), np.array([repr(y)]))[0]
    yx = c.compare(np.array([repr(y)]), np.array([repr(x)]))[0]
    assert 0.0 <= xy <= 1.0
    assert xy == pytest.approx(yx, abs=1e-9)
