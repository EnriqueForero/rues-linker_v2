"""Arreglos declarados de F3.1b en la comparación de nombres (``comparar``)."""

from __future__ import annotations

import random
import time

import pytest
from rapidfuzz.distance import Levenshtein

from record_linkage.flujo.vinculacion.comparar import (
    alinear,
    es_abreviatura,
    es_subsecuencia,
    misma_palabra,
)


def _referencia(a, b, genericos, subsecuencia, i=0, j=0, abrevio=False, distintiva=False):
    """El ``_cubre`` recursivo del motor original (sin memoizar), como referencia de equivalencia."""
    if i == len(a):
        return abrevio and distintiva
    if j >= len(b):
        return False
    t = a[i]
    if b[j].startswith(t) and _referencia(
        a, b, genericos, subsecuencia, i + 1, j + 1,
        abrevio or len(t) < len(b[j]),
        distintiva or (len(t) < len(b[j]) and b[j] not in genericos),
    ):  # fmt: skip
        return True
    if (
        subsecuencia
        and es_subsecuencia(t, b[j])
        and _referencia(
            a, b, genericos, subsecuencia, i + 1, j + 1, True, distintiva or b[j] not in genericos
        )
    ):
        return True
    n = len(t)
    if (
        2 <= n <= len(b) - j
        and all(b[j + k][0] == t[k] for k in range(n))
        and _referencia(
            a, b, genericos, subsecuencia, i + 1, j + n, True,
            distintiva or any(x not in genericos for x in b[j : j + n]),
        )
    ):  # fmt: skip
        return True
    return i > 0 and _referencia(a, b, genericos, subsecuencia, i, j + 1, abrevio, distintiva)


def _referencia_es_abreviatura(a, b, genericos, subsecuencia=False):
    if not a or not b or a[0][0] != b[0][0]:
        return False
    return (len(a) >= 2 or len(a[0]) >= 3) and _referencia(a, b, genericos, subsecuencia)


_PALABRAS = [
    "K",
    "KR",
    "KRO",
    "KRONOS",
    "E",
    "EMP",
    "EMPRESARIAL",
    "V",
    "VAL",
    "VALDORIA",
    "ALIM",
    "IND",
    "SA",
]
_GENERICOS = frozenset({"EMPRESARIAL", "ALIM", "IND", "EMP"})


def test_b9_el_resultado_es_el_mismo_que_el_de_la_busqueda_recursiva_original():
    """Memoizar/iterar no cambia ninguna respuesta: 4000 pares al azar con y sin subsecuencia."""
    azar = random.Random(20261012)
    for _ in range(4000):
        a = [azar.choice(_PALABRAS) for _ in range(azar.randint(1, 4))]
        b = [azar.choice(_PALABRAS) for _ in range(azar.randint(1, 7))]
        for subsecuencia in (False, True):
            assert es_abreviatura(a, b, _GENERICOS, subsecuencia) == _referencia_es_abreviatura(
                a, b, _GENERICOS, subsecuencia
            ), (a, b, subsecuencia)


def test_b9_palabras_repetidas_ya_no_disparan_un_costo_exponencial():
    # Antes: 12 palabras contra 24 repetidas (todas genéricas: nunca hay una distintiva y se exploran todos los
    # caminos) tardaban ~4 s; el costo crecía ~16 veces por cada dos palabras más.
    t0 = time.perf_counter()
    assert es_abreviatura(["A"] * 12, ["AA"] * 24, frozenset({"AA"})) is False
    assert time.perf_counter() - t0 < 0.5


@pytest.mark.parametrize("n", [1500, 5000])
def test_d1_nombres_de_miles_de_palabras_no_revientan_la_pila(n):
    # Antes: RecursionError con ≥ ~990 palabras (la búsqueda de la sigla recorría ``b`` de una en una).
    b = ["ABC"] * n
    assert es_abreviatura(["AB", "ZZ"], b, frozenset()) is False
    assert es_abreviatura(["AB"] * 3, b, frozenset()) is True


# ── B3 · la pérdida por truncamiento se mide por posición, no por la primera aparición de la palabra ──────
def test_b3_con_palabras_repetidas_lo_que_viene_despues_del_corte_no_es_diferencia():
    # Antes: alinear(['SAN','PEDRO'], ['SAN','PEDRO','SAN'], 1, True) daba sobra_b = ['SAN']: ``index`` tomaba la
    # primera aparición de 'SAN' (posición 0, antes del corte) y no la que de verdad viene después de PEDRO.
    sobra_a, sobra_b, pares = alinear(["SAN", "PEDRO"], ["SAN", "PEDRO", "SAN"], 1, True)
    assert (sobra_a, sobra_b) == ([], [])
    assert pares == [("SAN", "SAN", "exacta"), ("PEDRO", "PEDRO", "exacta")]


def test_b3_el_simetrico_con_el_nombre_truncado_del_lado_de_b():
    sobra_a, sobra_b, _ = alinear(["SAN", "PEDRO", "SAN"], ["SAN", "PEDRO"], 1, False, True)
    assert (sobra_a, sobra_b) == ([], [])


def test_b3_una_palabra_repetida_antes_del_corte_sigue_siendo_diferencia():
    # El segundo PEDRO de b sobra ANTES de la palabra cortada (SANT = prefijo de SANTOS): es una diferencia real.
    sobra_a, sobra_b, pares = alinear(["PEDRO", "SANT"], ["PEDRO", "PEDRO", "SANTOS"], 1, True)
    assert (sobra_a, sobra_b) == ([], ["PEDRO"])
    assert pares[-1] == ("SANT", "SANTOS", "truncada")


def _alinear_original(ta, tb, max_ed, trunc_a=False, trunc_b=False):
    """El ``alinear`` del motor original (por valor), como referencia cuando no hay palabras repetidas."""
    libres = list(tb)
    sobra_a, pares = [], []
    for k, t in enumerate(ta):
        hit, tipo = (t, "exacta") if t in libres else (None, "")
        if hit is None and len(t) >= 4 and max_ed > 0:
            cand = sorted(
                (Levenshtein.distance(t, u), u) for u in libres if misma_palabra(t, u, max_ed)
            )
            if cand:
                hit, tipo = cand[0][1], "errata"
        if hit is None and trunc_a and k == len(ta) - 1 and len(t) >= 2:
            cand_u = [u for u in libres if u.startswith(t) and u != t]
            if cand_u:
                hit, tipo = cand_u[0], "truncada"
        if hit is None and trunc_b and len(t) >= 3:
            cand_u = [u for u in libres if t.startswith(u) and u == tb[-1] and len(u) >= 2]
            if cand_u:
                hit, tipo = cand_u[0], "truncada"
        if hit is None:
            sobra_a.append(t)
        else:
            libres.remove(hit)
            pares.append((t, hit, tipo))
    sobra_b = libres
    if trunc_a and sobra_b and ta and pares and pares[-1][0] == ta[-1]:
        corte = tb.index(pares[-1][1])
        sobra_b = [u for u in sobra_b if tb.index(u) < corte]
    if trunc_b and sobra_a and tb and pares:
        ult_b = [p for p in pares if p[1] == tb[-1]]
        if ult_b:
            corte = ta.index(ult_b[0][0])
            sobra_a = [u for u in sobra_a if ta.index(u) < corte]
    return sobra_a, sobra_b, pares


_VOCABULARIO = [
    "KRONOS",
    "KRONOZ",
    "EMPRESARIAL",
    "EMPRESARIA",
    "VALDORIA",
    "VALDORIO",
    "SAN",
    "SAN",
    "ALIM",
    "ALIMENTOS",
]


def test_b3_sin_palabras_repetidas_el_resultado_es_el_del_original():
    """Con palabras todas distintas, medir por posición y por valor es lo mismo: 3000 pares al azar, 4 combinaciones."""
    azar = random.Random(7)
    vocabulario = sorted(set(_VOCABULARIO))
    for _ in range(3000):
        ta = azar.sample(vocabulario, azar.randint(1, 4))
        tb = azar.sample(vocabulario, azar.randint(1, 5))
        for trunc_a, trunc_b in ((False, False), (True, False), (False, True), (True, True)):
            esperado = _alinear_original(ta, tb, 1, trunc_a, trunc_b)
            assert alinear(ta, tb, 1, trunc_a, trunc_b) == esperado, (ta, tb, trunc_a, trunc_b)


# ── B8 · una sustitución no se cuela en una palabra corta por llevar además una inserción ──────────────────
@pytest.mark.parametrize(
    ("t", "u", "max_ed", "esperado"),
    [
        ("MIRVANA", "MIRVAXAS", 2, False),  # sustitución + inserción: el original decía True
        ("TORBAL", "TORGAL", 2, False),  # sustitución sola en una palabra corta
        ("VELSAER", "VELSAR", 1, True),  # una supresión
        ("TORDEN", "TORDENSS", 2, True),  # dos inserciones
        ("ABCDEFGH", "ABCDEFGX", 1, True),  # sustitución en palabra de ≥ 8 letras
        ("ABCDEFGH", "ABCDEFXX", 2, True),
    ],
)
def test_b8_misma_palabra_solo_acepta_inserciones_en_palabras_cortas(t, u, max_ed, esperado):
    assert misma_palabra(t, u, max_ed) is esperado


def test_b8_con_la_errata_por_defecto_de_una_letra_nada_cambia():
    """Con max_ed=1 la regla nueva y la del original coinciden: 4000 pares de palabras al azar."""
    azar = random.Random(11)
    for _ in range(4000):
        t = "".join(azar.choice("ABCDE") for _ in range(azar.randint(3, 10)))
        u = "".join(azar.choice("ABCDE") for _ in range(azar.randint(3, 10)))
        original = t == u or (
            min(len(t), len(u)) >= 4
            and Levenshtein.distance(t, u) <= 1
            and (len(t) != len(u) or min(len(t), len(u)) >= 8)
        )
        assert misma_palabra(t, u, 1) is original, (t, u)
