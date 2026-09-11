"""Tests v0.19.0 — Identificadores en forma canónica y comparadores multicriterio.

Qué se protege aquí
-------------------
1. El módulo 11 de la DIAN calcula el dígito que dice calcular.
2. Reducir a la base SOLO quita dígitos que validan: nunca adivina.
3. La reducción es idempotente y estable.
4. El comparador de conjuntos usa solapamiento, no Jaccard —la diferencia
   entre encontrar el 99 % de los pares y encontrar el 20 %—.
5. El comparador de categorías tolera que un topónimo elabore al otro.
6. El banco reporta calidad por estrato y macro-F1.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.19.0
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.matching.comparadores_extra import obtener, tipos_disponibles
from record_linkage.matching.identificadores import (
    LONGITUD_MINIMA_BASE,
    PASOS_MAXIMOS_DV,
    base_canonica,
    bases_canonicas,
    digito_verificacion_dian,
    es_extension_por_digito_verificacion,
    formas_canonicas,
)

# ── Dígito de verificación ────────────────────────────────────────────────

#: Casos reales tomados del cruce CRM con RUES. El DV es el que publica el RUES.
CASOS_DV = [
    ("890903436", "2"),
    ("860020309", "6"),
    ("10282948", "2"),
    ("900493570", "6"),
]


@pytest.mark.parametrize(("base", "esperado"), CASOS_DV)
def test_dv_reproduce_el_publicado(base: str, esperado: str) -> None:
    assert digito_verificacion_dian(base) == esperado


@pytest.mark.parametrize("entrada", ["", "abc", "12a45", "1" * 20])
def test_dv_rechaza_lo_que_no_es_base(entrada: str) -> None:
    assert digito_verificacion_dian(entrada) == ""


def test_dv_siempre_es_un_digito() -> None:
    for n in range(10_000_000, 10_000_200):
        d = digito_verificacion_dian(str(n))
        assert d in set("0123456789"), (n, d)


# ── Base canónica ─────────────────────────────────────────────────────────


def test_base_canonica_quita_el_dv_valido() -> None:
    assert base_canonica("8909034362") == "890903436"


def test_base_canonica_encadena_hasta_el_tope() -> None:
    """`NIT+DV` al que el preprocesador le añade otro DV: dos capas."""
    doble = "10282948" + digito_verificacion_dian("10282948")
    triple = doble + digito_verificacion_dian(doble)
    assert base_canonica(triple) == "10282948"
    assert PASOS_MAXIMOS_DV == 2


def test_base_canonica_no_quita_un_digito_que_no_valida() -> None:
    base = "890903436"
    malo = base + ("0" if digito_verificacion_dian(base) != "0" else "1")
    assert base_canonica(malo) == malo


def test_base_canonica_respeta_la_longitud_minima() -> None:
    corto = "1234567"
    assert len(corto) == LONGITUD_MINIMA_BASE
    assert base_canonica(corto) == corto


def test_base_canonica_es_idempotente() -> None:
    for valor in ("8909034362", "890903436", "10282948", "", "NA"):
        una = base_canonica(valor)
        assert base_canonica(una) == una


def test_base_canonica_ignora_separadores_y_ceros() -> None:
    assert base_canonica("0890.903.436-2") == "890903436"


def test_bases_canonicas_coincide_con_la_escalar() -> None:
    valores = np.array(["8909034362", "890903436", None, "", "10282948"], dtype=object)
    esperado = [base_canonica(v) for v in valores]
    assert list(bases_canonicas(valores)) == esperado


# ── Extensión por DV ──────────────────────────────────────────────────────


def test_extension_detecta_el_par_con_y_sin_dv() -> None:
    izq = np.array(["8909034362", "890903436", "12345678", ""])
    der = np.array(["890903436", "8909034362", "123456789", "890903436"])
    assert list(es_extension_por_digito_verificacion(izq, der)) == [True, True, False, False]


def test_extension_es_simetrica() -> None:
    a = np.array(["8909034362", "890903436"])
    b = np.array(["890903436", "8909034362"])
    assert list(es_extension_por_digito_verificacion(a, b)) == list(
        es_extension_por_digito_verificacion(b, a)
    )


def test_extension_exige_longitudes_iguales() -> None:
    with pytest.raises(ValueError, match="longitudes distintas"):
        es_extension_por_digito_verificacion(np.array(["1"]), np.array(["1", "2"]))


def test_formas_canonicas_devuelve_las_dos_lecturas() -> None:
    nueve = "10282948" + digito_verificacion_dian("10282948")
    assert formas_canonicas(nueve) == frozenset({nueve, "10282948"})
    assert formas_canonicas("890903436") == frozenset({"890903436"})
    assert formas_canonicas("") == frozenset()


# ── Comparador de conjuntos ───────────────────────────────────────────────


def test_conjunto_esta_registrado() -> None:
    assert "conjunto_signed" in tipos_disponibles()
    assert obtener("conjunto_signed").firmado is True


def test_conjunto_usa_solapamiento_no_jaccard() -> None:
    """El caso que motivó el comparador: RUES publica varias actividades y la
    Superintendencia solo la principal. Jaccard daría 1/3; solapamiento da 1."""
    f = obtener("conjunto_signed").funcion
    a = np.array(["ALOJAMIENTO|ADMINISTRACION|COMERCIO"])
    b = np.array(["ALOJAMIENTO"])
    assert f(a, b)[0] == pytest.approx(1.0)


def test_conjunto_penaliza_cuando_no_hay_nada_en_comun() -> None:
    f = obtener("conjunto_signed").funcion
    assert f(np.array(["A|B"]), np.array(["C"]))[0] == pytest.approx(-1.0)


def test_conjunto_es_neutral_si_falta_un_lado() -> None:
    f = obtener("conjunto_signed").funcion
    assert f(np.array(["A|B", ""]), np.array(["", "C"]))[0] == 0.0
    assert f(np.array(["A|B", ""]), np.array(["", "C"]))[1] == 0.0


def test_conjunto_es_simetrico() -> None:
    f = obtener("conjunto_signed").funcion
    a, b = np.array(["A|B|C"]), np.array(["B|D"])
    assert f(a, b)[0] == pytest.approx(f(b, a)[0])


def test_conjunto_acepta_los_dos_separadores() -> None:
    """Punto y coma y barra parten igual; con B en común de dos, sale 0."""
    f = obtener("conjunto_signed").funcion
    assert f(np.array(["A;B"]), np.array(["B|C"]))[0] == pytest.approx(0.0)
    assert f(np.array(["A;B"]), np.array(["B"]))[0] == pytest.approx(1.0)


def test_conjunto_gradua_el_solapamiento_parcial() -> None:
    """Con dos de cuatro en común el valor cae entre el acuerdo y el desacuerdo."""
    f = obtener("conjunto_signed").funcion
    assert f(np.array(["A|B|C|D"]), np.array(["C|D|E|F"]))[0] == pytest.approx(0.0)
    assert f(np.array(["A|B|C|D"]), np.array(["B|C|D"]))[0] == pytest.approx(1.0)


# ── Comparador de categorías tolerante ────────────────────────────────────


@pytest.mark.parametrize(
    ("izq", "der"),
    [
        ("Bogotá", "BOGOTA D.C."),
        ("Cali", "Santiago de Cali"),
        ("Cartagena", "Cartagena de Indias"),
        ("San Andrés y Providencia", "san andres providencia"),
    ],
)
def test_categoria_tolerante_acepta_la_elaboracion(izq: str, der: str) -> None:
    f = obtener("categoria_tolerante_signed").funcion
    assert f(np.array([izq]), np.array([der]))[0] == pytest.approx(1.0)


@pytest.mark.parametrize(("izq", "der"), [("Cali", "Medellín"), ("Bogotá", "Cundinamarca")])
def test_categoria_tolerante_sigue_separando_lugares_distintos(izq: str, der: str) -> None:
    f = obtener("categoria_tolerante_signed").funcion
    assert f(np.array([izq]), np.array([der]))[0] == pytest.approx(-1.0)


def test_categoria_tolerante_es_neutral_ante_el_vacio() -> None:
    f = obtener("categoria_tolerante_signed").funcion
    assert f(np.array(["", "Cali"]), np.array(["Cali", "N/A"]))[0] == 0.0


def test_categoria_tolerante_es_simetrica() -> None:
    f = obtener("categoria_tolerante_signed").funcion
    a, b = np.array(["Cali"]), np.array(["Santiago de Cali"])
    assert f(a, b)[0] == pytest.approx(f(b, a)[0])


# ── Métricas por estrato ──────────────────────────────────────────────────


def test_calidad_por_estrato_separa_lo_que_el_global_esconde() -> None:
    from record_linkage.evaluation.banco import evaluar_calidad

    referencia = pd.DataFrame(
        {
            "ID_GROUP": ["A", "A", "B", "B"],
            "ESTRATO": ["facil", "facil", "dificil", "dificil"],
            "REGIMEN": ["CON_ID"] * 4,
            "CASO": ["positivo"] * 4,
        }
    )
    prediccion = np.array(["a", "a", "b1", "b2"])  # acierta 'facil', falla 'dificil'
    m = evaluar_calidad(referencia, prediccion)
    assert m.calidad_por_estrato["facil"]["recall"] == pytest.approx(1.0)
    assert m.calidad_por_estrato["dificil"]["recall"] == pytest.approx(0.0)
    assert m.macro_f1 == pytest.approx(0.5)


def test_calidad_por_estrato_es_opcional() -> None:
    from record_linkage.evaluation.banco import evaluar_calidad

    referencia = pd.DataFrame(
        {"ID_GROUP": ["A", "A"], "REGIMEN": ["CON_ID"] * 2, "CASO": ["positivo"] * 2}
    )
    m = evaluar_calidad(referencia, np.array(["a", "a"]))
    assert m.calidad_por_estrato == {}
    assert m.macro_f1 != m.macro_f1  # nan
