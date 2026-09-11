"""Tests v0.21.0 — El contrato de salida de la correlativa.

Qué se protege
--------------
1. Las cuatro columnas finales SIEMPRE salen: es el entregable del cruce.
2. Si la identidad no llega, se reconstruye desde el golden y **se avisa**.
3. Si la reconstrucción es imposible, se falla — no se entrega a medias.
4. La operación es idempotente y no toca el DataFrame cuando ya está completo.
5. El flujo verifica el contrato en sus DOS caminos, memoria y disco.
6. `identidad_adoptada` responde lo mismo en los dos modos de resultado (LSP).

Contexto del defecto que corrigen
---------------------------------
Hasta 0.20.0 las columnas se producían pero no estaban garantizadas: dos
bloques ``try/except`` las perdían escribiendo solo una advertencia, y
``flujo/cruce.py`` no mencionaba ``NIT_FINAL`` ni una vez, así que ninguna
invariante las exigía.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-30  ·  Version: 0.21.0
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from record_linkage.golden.columnas_finales import (
    COLUMNAS_DIAGNOSTICO,
    COLUMNAS_FINALES,
    COLUMNAS_IDENTIDAD,
    ReporteColumnasFinales,
    faltantes,
    garantizar_columnas_finales,
)


@pytest.fixture
def correlativa() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ID_GRUPO": [0, 0, 1, 1],
            "SRC": ["A", "B", "A", "B"],
            "NIT": ["8909034362", "890903436", "9001112223", ""],
            "RAZON_SOCIAL": ["ACME SAS", "ACME S.A.S.", "BETA LTDA", "BETA LIMITADA"],
            "NOMBRE_LIMPIO": ["ACME SAS", "ACME SAS", "BETA LTDA", "BETA LTDA"],
        }
    )


@pytest.fixture
def golden() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ID_GRUPO": [0, 1],
            "NIT_FINAL": ["8909034362", "9001112223"],
            "RAZON_SOCIAL_FINAL": ["ACME SAS", "BETA LTDA"],
        }
    )


# ── El contrato ───────────────────────────────────────────────────────────


def test_el_contrato_son_cuatro_columnas() -> None:
    assert COLUMNAS_FINALES == COLUMNAS_IDENTIDAD + COLUMNAS_DIAGNOSTICO
    assert set(COLUMNAS_IDENTIDAD) == {"NIT_FINAL", "RAZON_SOCIAL_FINAL"}
    assert set(COLUMNAS_DIAGNOSTICO) == {"NAME_SIMILARITY_SCORE", "NIT_DISTANCE"}


def test_faltantes_nombra_exactamente_lo_que_falta(correlativa: pd.DataFrame) -> None:
    assert faltantes(correlativa) == COLUMNAS_FINALES
    completo = correlativa.assign(**dict.fromkeys(COLUMNAS_FINALES, 0))
    assert faltantes(completo) == ()


# ── Curso normal: calcular el diagnóstico no es una anomalía ──────────────


def test_calcular_el_diagnostico_no_es_anomalia(
    correlativa: pd.DataFrame, golden: pd.DataFrame
) -> None:
    con_identidad = correlativa.merge(golden, on="ID_GRUPO")
    salida, reporte = garantizar_columnas_finales(con_identidad, golden)
    assert faltantes(salida) == ()
    assert not reporte.hubo_anomalia, "calcular el diagnóstico es trabajo esperado"
    assert set(reporte.calculadas) == set(COLUMNAS_DIAGNOSTICO)
    assert reporte.completo


def test_no_toca_el_marco_si_ya_esta_completo(
    correlativa: pd.DataFrame, golden: pd.DataFrame
) -> None:
    completo = correlativa.assign(**dict.fromkeys(COLUMNAS_FINALES, 0))
    salida, reporte = garantizar_columnas_finales(completo, golden)
    assert salida is completo, "sin nada que hacer, no se copia el DataFrame"
    assert reporte.ya_estaban == COLUMNAS_FINALES
    assert not reporte.hubo_anomalia


def test_es_idempotente(correlativa: pd.DataFrame, golden: pd.DataFrame) -> None:
    una, _ = garantizar_columnas_finales(correlativa, golden)
    dos, reporte = garantizar_columnas_finales(una, golden)
    assert dos is una
    assert reporte.ya_estaban == COLUMNAS_FINALES


# ── Anomalía: la identidad no llegó ───────────────────────────────────────


def test_reconstruye_la_identidad_desde_el_golden(
    correlativa: pd.DataFrame, golden: pd.DataFrame
) -> None:
    salida, reporte = garantizar_columnas_finales(correlativa, golden)
    assert faltantes(salida) == ()
    assert reporte.hubo_anomalia
    assert set(reporte.reparadas) == set(COLUMNAS_IDENTIDAD)
    assert salida.loc[salida["ID_GRUPO"] == 0, "NIT_FINAL"].unique().tolist() == ["8909034362"]
    assert salida.loc[salida["ID_GRUPO"] == 1, "RAZON_SOCIAL_FINAL"].unique().tolist() == [
        "BETA LTDA"
    ]


def test_la_anomalia_queda_en_el_registro(
    correlativa: pd.DataFrame, golden: pd.DataFrame, caplog
) -> None:
    """Reparar en silencio es lo que produjo el problema. Debe avisar."""
    with caplog.at_level(logging.WARNING):
        garantizar_columnas_finales(correlativa, golden)
    assert any("NIT_FINAL" in r.message or "NIT_FINAL" in r.getMessage() for r in caplog.records)


def test_sin_golden_utilizable_falla_en_vez_de_entregar_a_medias(
    correlativa: pd.DataFrame,
) -> None:
    with pytest.raises(RuntimeError, match="entregable del cruce"):
        garantizar_columnas_finales(correlativa, None)
    inservible = pd.DataFrame({"ID_GRUPO": [0, 1]})
    with pytest.raises(RuntimeError, match="entregable del cruce"):
        garantizar_columnas_finales(correlativa, inservible)


def test_sin_id_grupo_no_hay_nada_que_adjudicar(correlativa: pd.DataFrame) -> None:
    with pytest.raises(KeyError, match="ID_GRUPO"):
        garantizar_columnas_finales(correlativa.drop(columns=["ID_GRUPO"]), None)


# ── Semántica de las métricas de diagnóstico ──────────────────────────────


def test_la_similitud_es_uno_cuando_el_nombre_coincide(
    correlativa: pd.DataFrame, golden: pd.DataFrame
) -> None:
    salida, _ = garantizar_columnas_finales(correlativa, golden)
    # NOMBRE_LIMPIO == RAZON_SOCIAL_FINAL en las cuatro filas de este caso.
    assert salida["NAME_SIMILARITY_SCORE"].tolist() == pytest.approx([1.0, 1.0, 1.0, 1.0])


def test_la_distancia_cuenta_el_digito_de_verificacion(
    correlativa: pd.DataFrame, golden: pd.DataFrame
) -> None:
    salida, _ = garantizar_columnas_finales(correlativa, golden)
    distancias = salida["NIT_DISTANCE"].tolist()
    assert distancias[0] == 0, "el NIT de la fila es el adoptado"
    assert distancias[1] == 1, "difiere en el dígito de verificación"
    assert distancias[3] == 0, "sin identificador, la ausencia no es distancia"


def test_sin_columna_de_nombre_la_similitud_sale_en_cero(golden: pd.DataFrame) -> None:
    """Un consumidor que espera cuatro columnas no debe recibir tres."""
    minima = pd.DataFrame({"ID_GRUPO": [0, 1], "NIT": ["8909034362", "9001112223"]})
    salida, reporte = garantizar_columnas_finales(minima, golden)
    assert faltantes(salida) == ()
    assert salida["NAME_SIMILARITY_SCORE"].tolist() == [0.0, 0.0]
    assert "cero" in reporte.origen["NAME_SIMILARITY_SCORE"]


def test_la_similitud_esta_acotada(golden: pd.DataFrame) -> None:
    rng = np.random.default_rng(3)
    marco = pd.DataFrame(
        {
            "ID_GRUPO": rng.integers(0, 2, 200),
            "NIT": [str(x) for x in rng.integers(10**8, 10**9, 200)],
            "NOMBRE_LIMPIO": [f"EMPRESA {x}" for x in rng.integers(0, 50, 200)],
        }
    )
    salida, _ = garantizar_columnas_finales(marco, golden)
    assert salida["NAME_SIMILARITY_SCORE"].between(0.0, 1.0).all()
    assert (salida["NIT_DISTANCE"] >= 0).all()


# ── El reporte ────────────────────────────────────────────────────────────


def test_el_reporte_se_serializa() -> None:
    reporte = ReporteColumnasFinales(
        ya_estaban=("NIT_FINAL",),
        calculadas=("NIT_DISTANCE",),
        reparadas=("RAZON_SOCIAL_FINAL",),
        origen={"RAZON_SOCIAL_FINAL": "golden por ID_GRUPO"},
    )
    datos = reporte.a_dict()
    assert datos["hubo_anomalia"] is True
    assert datos["completo"] is False
    assert "RECONSTRUIDAS" in reporte.resumen()


def test_el_reporte_vacio_no_declara_anomalia() -> None:
    assert not ReporteColumnasFinales().hubo_anomalia


# ── La invariante del flujo ───────────────────────────────────────────────


def test_el_flujo_rechaza_una_correlativa_sin_el_contrato(
    correlativa: pd.DataFrame, golden: pd.DataFrame
) -> None:
    from record_linkage.flujo.cruce import _verificar_invariantes

    completa, _ = garantizar_columnas_finales(correlativa, golden)
    golden_lleno = golden.assign(NAME_SIMILARITY_SCORE=1.0, NIT_DISTANCE=0)
    _verificar_invariantes(completa, golden_lleno, len(completa))

    incompleta = completa.drop(columns=["NIT_FINAL"])
    with pytest.raises(RuntimeError, match="NIT_FINAL"):
        _verificar_invariantes(incompleta, golden_lleno, len(incompleta))


def test_el_protocolo_de_calidad_exige_identidad_adoptada() -> None:
    """Los dos modos de resultado deben ser sustituibles (LSP)."""
    from record_linkage.flujo.cruce import (
        ControlCalidad,
        ResultadoCruce,
        ResultadoCruceDisco,
    )

    assert hasattr(ControlCalidad, "identidad_adoptada")
    for clase in (ResultadoCruce, ResultadoCruceDisco):
        assert callable(getattr(clase, "identidad_adoptada", None)), clase.__name__
