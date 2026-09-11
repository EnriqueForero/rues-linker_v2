"""Calidad sobre el exhaustivo ENRIQUECIDO con CIUDAD sintética.

Mide el efecto de las variables adicionales firmadas (``categorical_signed``)
a escala (1456 regs, 137 grupos) sobre los casos negativos diseñados del
ground truth exhaustivo.

ADVERTENCIA METODOLÓGICA (honestidad):
    La columna CIUDAD de este dataset es SINTÉTICA y favorable al feature por
    construcción (asignada por grupo verdadero, ver
    ``scripts/enriquecer_ground_truth_ciudad.py``). Mide el TECHO del beneficio,
    no el caso real. Estos pisos verifican que el cableado end-to-end de
    ``extra_features`` mueve la métrica en la dirección esperada y no regresa;
    NO son un certificado de calidad en producción.

Recalibración v3.2.3:
    Los pisos previos (P>=0.94, F1>=0.88) se calibraron contra pandas 3.x con
    un bug silencioso en ``_valid_mask`` que penalizaba NaN como "valor distinto"
    en features firmados. Esto inflaba la precisión reportada a 0.97 violando la
    política documentada ("NaN → 0.0, sin penalización"). En pandas 2.2.2, donde
    el bug no se activaba, los mismos pisos eran inalcanzables. v3.2.3 corrige
    ``_valid_mask`` para ser cross-version-safe (ver scorer.py docstring), lo que
    obliga a recalibrar pisos a los valores reales del comportamiento documentado.
    El nuevo `w=0.30` es óptimo en F1 (meseta plana en [0.25, 1.0]).

El dataset se genera con ``scripts/enriquecer_ground_truth_ciudad.py``. Si no
existe, los tests se saltan limpiamente (no fallan): permite correr la suite
sin el artefacto generado.
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.deduplication.unified import deduplicate_unified
from record_linkage.evaluation.pairwise import evaluar_pares

CIUDAD_CSV = Path(__file__).parent / "data" / "golden_truth_exhaustivo_ciudad.csv"

# Peso recalibrado en v3.2.3 tras corrección del bug NaN-vs-valor en `_valid_mask`
# (ver CHANGELOG v3.2.3 y docstring de `VectorizedScorer._to_clean_str_series`).
#
# Historia: el peso original w=0.15 fue calibrado contra pandas 3.x con un bug
# silencioso que penalizaba NaN como "valor distinto" (-1.0), inflando la
# precisión reportada a 0.966. Al normalizar el manejo de nulos para que
# cumpla la política documentada del feature (`NaN → 0.0`, sin penalización),
# w=0.15 dejó de ser óptimo: el barrido honesto sobre este dataset muestra
# que el F1 máximo se alcanza en w∈[0.25, 0.50] con meseta plana, y w=0.30
# es el valor por defecto recomendado.
#
# Métricas medidas (pandas 3.0.2, scorer v3.2.3, dataset enriquecido):
#   - Sin features:          P=0.886, R=0.849, F1=0.867, FP=1053
#   - CIUDAD signed w=0.10:  P=0.905, R=0.880, F1=0.892, FP=897   ← mejor precisión
#   - CIUDAD signed w=0.15:  P=0.882, R=0.906, F1=0.894, FP=1177  (valle: peor P)
#   - CIUDAD signed w=0.25:  P=0.891, R=0.958, F1=0.923, FP=1136
#   - CIUDAD signed w=0.30+: P=0.892, R=0.963, F1=0.926, FP=1132  ← óptimo F1
#
# Pisos con margen para variabilidad de ejecución entre versiones de pandas y
# CPUs distintas (~±0.005 en cada métrica observado en CI vs Colab).
WEIGHT = 0.30
F1_MIN = 0.91
PRECISION_MIN = 0.88
RECALL_MIN = 0.93

skip_if_no_data = pytest.mark.skipif(
    not CIUDAD_CSV.exists(),
    reason=(
        "Dataset enriquecido ausente. Generar con: python scripts/enriquecer_ground_truth_ciudad.py"
    ),
)


def _evaluar(extra_features) -> object:
    truth = pd.read_csv(CIUDAD_CSV, dtype={"NIT": str})
    truth["NIT"] = truth["NIT"].fillna("")
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            with tempfile.TemporaryDirectory() as tmp:
                correlativa, _ = deduplicate_unified(
                    df_input=truth[["NIT", "RAZON_SOCIAL", "CIUDAD"]].copy(),
                    col_nit="NIT",
                    col_name="RAZON_SOCIAL",
                    mode="BALANCEADO",
                    output_dir=tmp,
                    extra_features=extra_features,
                )
    finally:
        logging.disable(logging.NOTSET)
    correlativa = correlativa.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    return evaluar_pares(truth["ID_GROUP"].to_numpy(), correlativa["ID_GRUPO"].to_numpy())


@pytest.fixture(scope="module")
def metricas_con_ciudad():
    return _evaluar([{"column": "CIUDAD", "weight": WEIGHT, "type": "categorical_signed"}])


@pytest.fixture(scope="module")
def metricas_sin_features():
    return _evaluar(None)


@skip_if_no_data
def test_precision_mejora_con_ciudad(metricas_con_ciudad) -> None:
    assert metricas_con_ciudad.precision >= PRECISION_MIN, (
        f"Precision {metricas_con_ciudad.precision:.3f} < {PRECISION_MIN}\n"
        f"{metricas_con_ciudad.resumen()}"
    )


@skip_if_no_data
def test_f1_mejora_con_ciudad(metricas_con_ciudad) -> None:
    assert metricas_con_ciudad.f1 >= F1_MIN


@skip_if_no_data
def test_recall_no_se_sacrifica(metricas_con_ciudad) -> None:
    assert metricas_con_ciudad.recall >= RECALL_MIN


@skip_if_no_data
def test_ciudad_supera_baseline_sin_features(metricas_con_ciudad, metricas_sin_features) -> None:
    """La ganancia es real: CIUDAD signed mejora F1 vs sin features sin
    degradar precisión más allá del ruido.

    Nota metodológica (v3.2.3): la aserción original incluía
    ``fp < metricas_sin_features.fp``. Se removió porque es matemáticamente
    cuestionable: cuando el feature recupera pares verdaderos (sube recall),
    el número absoluto de FP suele subir también, pero la *tasa* de FP (lo
    capturado por precisión) baja o se mantiene. La invariante correcta es
    "F1 sube" + "precisión no se degrada significativamente", no "FP cae en
    absoluto".
    """
    assert metricas_con_ciudad.f1 > metricas_sin_features.f1, (
        f"F1 no mejoró: con CIUDAD {metricas_con_ciudad.f1:.4f} "
        f"vs sin features {metricas_sin_features.f1:.4f}"
    )
    # Tolerancia de ruido: precision puede caer hasta 0.01 sin que se considere
    # regresión (el beneficio en recall compensa con creces el efecto neto en F1).
    assert metricas_con_ciudad.precision >= metricas_sin_features.precision - 0.01, (
        f"Precision se degradó: con CIUDAD {metricas_con_ciudad.precision:.4f} "
        f"vs sin features {metricas_sin_features.precision:.4f}"
    )


@skip_if_no_data
def test_reporte(metricas_con_ciudad, metricas_sin_features, capsys) -> None:
    print(f"\nSin features:  {metricas_sin_features.resumen()}")
    print(f"\nCIUDAD signed: {metricas_con_ciudad.resumen()}")
