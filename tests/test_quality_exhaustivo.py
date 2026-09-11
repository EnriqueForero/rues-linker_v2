"""Calidad de linkage sobre el ground truth EXHAUSTIVO.

Complementa test_quality_golden.py con un dataset más exigente. El dataset
`tests/data/golden_truth_exhaustivo.csv` fue reconstruido (v0.10.0) de forma
determinista desde `ground_truth_grande.csv` con
`scripts/reconstruir_golden_sets.py`: 1460 registros, 173 grupos, grupos con
múltiples variantes, NITs con errores y CASOS NEGATIVOS (empresas de nombre
similar pero distintas, para medir falsos positivos).

Pisos de regresión (medidos sobre el dataset reconstruido, motor v0.10.0):
    - Medido: F1=0.948, precision=0.943, recall=0.952.
    - Los pisos se fijan ~0.03 por debajo de lo medido, para tolerar
      variabilidad de ejecución sin dejar que la calidad retroceda.

Estos números NO son un certificado de producción, son un piso que impide que
la calidad RETROCEDA.
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

GOLDEN = Path(__file__).parent / "data" / "golden_truth_exhaustivo.csv"

# Pisos de regresión v0.10.0 (medidos: F1=0.948, P=0.943, R=0.952).
# Se dejan ~0.03 de margen para variabilidad en ejecución.
F1_MIN = 0.91
PRECISION_MIN = 0.91
RECALL_MIN = 0.92


@pytest.fixture(scope="module")
def metricas():
    """Corre el pipeline completo sobre el ground truth exhaustivo."""
    truth = pd.read_csv(GOLDEN, dtype={"NIT": str})
    truth["NIT"] = truth["NIT"].fillna("")
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            with tempfile.TemporaryDirectory() as tmp:
                correlativa, _ = deduplicate_unified(
                    df_input=truth[["NIT", "RAZON_SOCIAL"]].copy(),
                    col_nit="NIT",
                    col_name="RAZON_SOCIAL",
                    mode="BALANCEADO",
                    output_dir=tmp,
                )
    finally:
        logging.disable(logging.NOTSET)
    correlativa = correlativa.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    assert len(correlativa) == len(truth)
    return evaluar_pares(truth["ID_GROUP"].to_numpy(), correlativa["ID_GRUPO"].to_numpy())


def test_f1_no_retrocede(metricas) -> None:
    assert metricas.f1 >= F1_MIN, f"F1 {metricas.f1:.3f} < {F1_MIN}\n{metricas.resumen()}"


def test_precision_alta(metricas) -> None:
    """Con casos negativos en el dataset, la precision es la métrica clave."""
    assert metricas.precision >= PRECISION_MIN, (
        f"Precision {metricas.precision:.3f} < {PRECISION_MIN}: sobre-fusión "
        f"({metricas.fp} pares unidos indebidamente)."
    )


def test_recall_no_retrocede(metricas) -> None:
    assert metricas.recall >= RECALL_MIN, f"Recall {metricas.recall:.3f} < {RECALL_MIN}"


def test_reporte(metricas, capsys) -> None:
    print("\n" + metricas.resumen())
