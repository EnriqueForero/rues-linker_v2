"""Tests de los tipos y comparadores nuevos de v0.13.0 (N2).

Cubre: LevenshteinSigned, FoneticoEspanolSigned (+clave_fonetica_es),
JerarquicoPrefijo, ConjuntoJaccard, NumericoAbsoluto, TipoCampo
JERARQUICO/CONJUNTO/BOOLEANO, y propiedades universales (simetría, rango,
consistencia de valid_mask) con hypothesis.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from hypothesis import example, given, settings, strategies as st

from record_linkage import CampoSpec, EsquemaCampos, TipoCampo
from record_linkage.matching.comparators import (
    ConjuntoJaccard,
    FoneticoEspanolSigned,
    JerarquicoPrefijo,
    LevenshteinSigned,
    NumericoAbsoluto,
    NumericoRelativo,
    clave_fonetica_es,
)
from record_linkage.matching.normalizadores import (
    normalizar_booleano,
    normalizar_conjunto,
    normalizar_jerarquico,
)

# ─────────────────────────────────────────────────────────────────────
# Fonética española
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("VASQUEZ", "BASQUES"),  # B/V + Z/S
        ("LLUVIA", "YUVIA"),  # yeísmo
        ("CIGARRA", "SIGARA"),  # C(e,i)/S + RR/R
        ("HELADO", "ELADO"),  # H muda
        ("QUESO", "KESO"),  # QU/K
        ("GIMENEZ", "JIMENES"),  # G(e,i)/J + Z/S
    ],
)
def test_fonetica_es_equivalencias(a, b):
    claves = clave_fonetica_es(pd.Series([a, b]))
    assert claves.iloc[0] == claves.iloc[1], f"{a} vs {b} → {claves.tolist()}"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("GUERRA", "GENTE"),  # G dura vs suave NO se confunden
        ("CASA", "GASA"),
        ("PERRO", "PELO"),
    ],
)
def test_fonetica_es_distingue(a, b):
    claves = clave_fonetica_es(pd.Series([a, b]))
    assert claves.iloc[0] != claves.iloc[1]


def test_fonetico_comparador_orden_de_tokens():
    comp = FoneticoEspanolSigned()
    out = comp.compare(np.array(["HIJOS DE VASQUEZ"]), np.array(["BASQUES HIJOS DE"]))
    assert out[0] == 1.0  # token-set fonético: orden irrelevante


# ─────────────────────────────────────────────────────────────────────
# Jerárquico (CIIU/HS)
# ─────────────────────────────────────────────────────────────────────


def test_jerarquico_niveles_parciales():
    comp = JerarquicoPrefijo(niveles=(2, 4, 6))
    left = np.array(["620201", "620100", "999999", "62", ""])
    right = np.array(["620202", "620201", "620201", "620201", "620201"])
    out = comp.compare(left, right)
    assert out[0] == pytest.approx(2 / 3)  # comparte 2 y 4, no 6
    assert out[1] == pytest.approx(1 / 3)  # comparte solo capítulo
    assert out[2] == -1.0  # ramas distintas: evidencia en contra
    assert out[3] == pytest.approx(1 / 3)  # código corto: solo nivel 2 comparable
    assert out[4] == 0.0  # faltante → neutro
    assert comp.valid_mask(left, right).tolist() == [True, True, True, True, False]


def test_jerarquico_normaliza_separadores():
    comp = JerarquicoPrefijo()
    assert comp.compare(np.array(["6202-01"]), np.array(["620201"]))[0] == 1.0


def test_jerarquico_niveles_invalidos():
    with pytest.raises(ValueError):
        JerarquicoPrefijo(niveles=(4, 2))


# ─────────────────────────────────────────────────────────────────────
# Conjunto (Jaccard)
# ─────────────────────────────────────────────────────────────────────


def test_conjunto_jaccard_valores():
    comp = ConjuntoJaccard()
    left = np.array(["A;B;C", "A;B", "A", ""])
    right = np.array(["B;C;D", "X", "A", "A"])
    out = comp.compare(left, right)
    assert out[0] == pytest.approx(2 / 4)
    assert out[1] == 0.0
    assert out[2] == 1.0
    assert out[3] == 0.0
    assert comp.valid_mask(left, right).tolist() == [True, True, True, False]


def test_conjunto_canonico_ordena():
    canon = normalizar_conjunto(pd.Series(["B; A", "A;B", "  "]))
    assert canon.iloc[0] == canon.iloc[1] == "A;B"
    assert canon.iloc[2] == ""


# ─────────────────────────────────────────────────────────────────────
# Numérico absoluto y booleano
# ─────────────────────────────────────────────────────────────────────


def test_numerico_absoluto_vs_relativo():
    comp = NumericoAbsoluto(tolerancia=5.0)
    out = comp.compare(np.array(["2020", "2020", "10"]), np.array(["2022", "2030", "1000"]))
    assert out[0] == pytest.approx(0.6)  # |Δ|=2, tol=5 → 1-0.4
    assert out[1] == 0.0  # fuera de tolerancia
    assert out[2] == 0.0
    assert comp.valid_mask(np.array(["1", None]), np.array(["2", "3"])).tolist() == [True, False]


def test_booleano_canonico_y_faltantes():
    s = normalizar_booleano(pd.Series(["SI", "1", "NO", "FALSE", "tal vez", None, "0"]))
    assert s.tolist() == ["V", "V", "F", "F", "", "", "F"]


def test_booleano_falso_concuerda_con_falso():
    """Regresión del bug atrapado en el smoke de v0.13.0: FALSO no es faltante."""
    df = pd.DataFrame(
        {
            "TAX_ID": ["900333444", "900333444"],
            "COMPANY": ["BETA LTDA", "BETA LIMITADA"],
            "EXPORTA": ["NO", "FALSE"],
        }
    )
    import record_linkage as rl

    esq = EsquemaCampos(
        campos=[
            CampoSpec("TAX_ID", TipoCampo.IDENTIFICADOR, peso=3.0),
            CampoSpec("COMPANY", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
            CampoSpec("EXPORTA", TipoCampo.BOOLEANO, peso=0.5),
        ],
        umbral_score=0.6,
        min_concordancias=1,
    )
    res = rl.dedupe_esquema(df, esq, incluir_desglose=True)
    assert res.metricas["desglose"]["sim_EXPORTA"].iloc[0] == 1.0


# ─────────────────────────────────────────────────────────────────────
# Integración: esquema con los tres tipos nuevos
# ─────────────────────────────────────────────────────────────────────


def test_esquema_con_tipos_nuevos_end_to_end():
    import record_linkage as rl

    df = pd.DataFrame(
        {
            "TAX_ID": ["900111222", "900111222", "900333444", "900555666"],
            "COMPANY": ["ACME SAS", "ACME S.A.S.", "BETA LTDA", "GAMA SA"],
            "CIIU": ["6202-01", "620201", "1101", "2202"],
            "CANALES": ["WEB;TIENDA", "TIENDA; WEB", "WEB", "TV"],
            "EXPORTA": ["SI", "1", "NO", "SI"],
        }
    )
    esq = EsquemaCampos(
        campos=[
            CampoSpec("TAX_ID", TipoCampo.IDENTIFICADOR, peso=3.0),
            CampoSpec("COMPANY", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
            CampoSpec("CIIU", TipoCampo.JERARQUICO, peso=1.0),
            CampoSpec("CANALES", TipoCampo.CONJUNTO, peso=0.5),
            CampoSpec("EXPORTA", TipoCampo.BOOLEANO, peso=0.5),
        ],
        umbral_score=0.6,
        min_concordancias=1,
    )
    res = rl.dedupe_esquema(df, esq)
    et = res.correlativa["ID_GRUPO"]
    assert et[0] == et[1] and len(set(et)) == 3
    # El manifiesto serializa los tipos nuevos.
    tipos = {c["tipo"] for c in res.manifiesto["parametros"]["esquema"]["campos"]}
    assert {"jerarquico", "conjunto", "booleano"}.issubset(tipos)


def test_numerico_tolerancia_absoluta_via_esquema():
    campo = CampoSpec("ANIO", TipoCampo.NUMERICO, params={"tolerancia_absoluta": 2})
    assert campo.comparador.name.startswith("numerico_abs")
    campo_rel = CampoSpec("VALOR", TipoCampo.NUMERICO)
    assert campo_rel.comparador.name.startswith("numerico_rel")


# ─────────────────────────────────────────────────────────────────────
# Propiedades universales (hypothesis)
# ─────────────────────────────────────────────────────────────────────

_texto = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÑ 0123456789;", min_size=0, max_size=24)


@settings(max_examples=60, deadline=None)
@given(a=_texto, b=_texto)
@example(a="INF", b="INF")  # contraejemplo real: producía NaN (v0.14.0)
@example(a="1e400", b="1e400")
def test_propiedades_simetria_y_rango(a, b):
    """Todo comparador nuevo: simétrico, dentro de rango, valid consistente."""
    la, lb = np.array([a], dtype=object), np.array([b], dtype=object)
    for comp, lo in (
        (LevenshteinSigned(), -1.0),
        (FoneticoEspanolSigned(), -1.0),
        (JerarquicoPrefijo(), -1.0),
        (ConjuntoJaccard(), 0.0),
        (NumericoAbsoluto(tolerancia=3.0), 0.0),
    ):
        ab = comp.compare(la, lb)[0]
        ba = comp.compare(lb, la)[0]
        assert ab == pytest.approx(ba, abs=1e-12), f"{comp.name} no es simétrico"
        assert lo - 1e-12 <= ab <= 1.0 + 1e-12, f"{comp.name} fuera de rango: {ab}"
        va = comp.valid_mask(la, lb)[0]
        if not va:
            assert ab == 0.0, f"{comp.name}: inválido debe ser neutro"


@settings(max_examples=60, deadline=None)
@given(a=_texto)
def test_propiedades_identidad(a):
    """x comparado consigo mismo: máximo del rango cuando es válido."""
    la = np.array([a], dtype=object)
    for comp in (
        LevenshteinSigned(),
        FoneticoEspanolSigned(),
        JerarquicoPrefijo(),
        ConjuntoJaccard(),
    ):
        if comp.valid_mask(la, la)[0]:
            assert comp.compare(la, la)[0] == pytest.approx(1.0), comp.name


# ─────────────────────────────────────────────────────────────────────
# Regresión v0.14.0: valores no finitos (hallazgo de property-based)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("izq", "der"),
    [("INF", "INF"), ("INF", "5"), ("-INF", "INF"), ("inf", "inf"), ("1e400", "1e400")],
)
@pytest.mark.parametrize("comp", [NumericoAbsoluto(tolerancia=3.0), NumericoRelativo()])
def test_no_finitos_son_faltantes_y_nunca_producen_nan(comp, izq, der):
    """`INF`/`NaN`/desborde no son magnitudes: se tratan como faltante.

    Antes de v0.14.0 devolvían NaN, que se propagaba en silencio al score
    combinado. Lo encontró hypothesis con el ejemplo mínimo "INF" vs "INF".
    """
    li, ld = np.array([izq], dtype=object), np.array([der], dtype=object)
    score = comp.compare(li, ld)[0]
    assert np.isfinite(score), f"{comp.name} devolvió {score!r}"
    assert score == 0.0
    assert not comp.valid_mask(li, ld)[0]


@pytest.mark.parametrize("comp", [NumericoAbsoluto(tolerancia=3.0), NumericoRelativo()])
def test_los_numeros_normales_siguen_intactos(comp):
    izq = np.array(["2020", "0", "100"], dtype=object)
    der = np.array(["2020", "0", "100"], dtype=object)
    assert comp.compare(izq, der).tolist() == [1.0, 1.0, 1.0]
    assert comp.valid_mask(izq, der).tolist() == [True, True, True]
