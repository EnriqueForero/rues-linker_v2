"""Test de calidad de linkage contra ground truth (Validation Level 3).

NUEVO en v2.2.0. Este es el test que faltaba en todo el paquete: hasta v2.1.0
los 84 tests verificaban que el código CORRE (smoke) y que las vectorizaciones
son equivalentes entre sí, pero NINGUNO medía si el sistema AGRUPA BIEN.

Usa un golden set de 269 registros empresariales colombianos reales con su
etiqueta de grupo verdadera (``ID_GROUP``), embebido en ``tests/data/``. Los
casos incluyen retos genuinos: typos severos, NITs con dígito de verificación
errado, ruido aduanero, sufijos societarios contradictorios y filiales.

La métrica es pairwise precision/recall/F1, el estándar de record linkage.

Los umbrales de ``assert`` se fijan como REGRESIÓN: son ligeramente inferiores
al rendimiento medido en v2.2.0 (F1≈0.65, recall≈0.53, precision≈0.84) para
que el test falle si una futura modificación degrada la calidad, sin ser tan
frágil que rompa por ruido de redondeo.

IMPORTANTE: F1≈0.65 NO es un resultado "bueno" en términos absolutos — el
sistema aún fragmenta ~47% de los pares verdaderos. Este test no certifica
calidad de producción; certifica que la calidad no RETROCEDE y da una cifra
objetiva sobre la cual mejorar. Ver MIGRATION_LOG §11 para el plan de recall.
"""

from __future__ import annotations

import logging
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.deduplication.unified import deduplicate_unified
from record_linkage.evaluation.pairwise import evaluar_pares

GOLDEN_CSV = Path(__file__).parent / "data" / "golden_truth.csv"

# Umbrales de regresión (piso de calidad). Medidos v0.10.0 sobre el golden
# reconstruido (270 regs, 36 grupos): F1=0.991, recall=0.992, precision=0.989.
# Se deja ~0.05 de margen hacia abajo.
F1_MIN = 0.94
RECALL_MIN = 0.94
PRECISION_MIN = 0.94


@pytest.fixture(scope="module")
def golden() -> pd.DataFrame:
    """Carga el golden set embebido. NIT como string (preserva ceros/formato)."""
    df = pd.read_csv(GOLDEN_CSV, dtype={"NIT": str})
    df["NIT"] = df["NIT"].fillna("")
    return df


@pytest.fixture(scope="module")
def metricas(golden: pd.DataFrame):
    """Corre el pipeline real sobre el golden set y devuelve PairwiseMetrics.

    Silencia el stdout/stderr verboso del pipeline para no contaminar el
    reporte de pytest. El cálculo es determinista (random_state fijo en el
    pipeline), por eso scope='module': se corre una sola vez.
    """
    df_in = golden[["NIT", "RAZON_SOCIAL"]].copy()
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                correlativa, _ = deduplicate_unified(
                    df_input=df_in,
                    col_nit="NIT",
                    col_name="RAZON_SOCIAL",
                    mode="BALANCEADO",
                    output_dir=tmp,
                )
    finally:
        logging.disable(logging.NOTSET)

    correlativa = correlativa.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    assert len(correlativa) == len(golden), (
        f"El pipeline devolvió {len(correlativa)} filas pero la entrada "
        f"tenía {len(golden)}. Se perdieron/duplicaron registros."
    )
    return evaluar_pares(
        golden["ID_GROUP"].to_numpy(),
        correlativa["ID_GRUPO"].to_numpy(),
    )


def test_pipeline_preserva_todos_los_registros(golden, metricas) -> None:
    """El pipeline no debe perder ni duplicar registros (contractual).

    Se compara contra el número real de filas del golden set (no un literal
    fijo), de modo que el test siga siendo válido si el dataset se regenera.
    """
    assert metricas.n_records == len(golden)


def test_recall_no_retrocede(metricas) -> None:
    """Recall mínimo: el sistema debe detectar al menos el 45% de los pares.

    Recall es la métrica frágil de este sistema (fragmenta entidades). Este
    piso evita que una futura 'optimización' lo empeore en silencio.
    """
    assert metricas.recall >= RECALL_MIN, (
        f"Recall {metricas.recall:.3f} < {RECALL_MIN}. El sistema está "
        f"fragmentando más entidades que en v2.2.0. {metricas.fn} pares "
        f"verdaderos quedaron sin detectar."
    )


def test_precision_no_retrocede(metricas) -> None:
    """Precision mínima: de lo que une, al menos el 78% debe estar bien unido."""
    assert metricas.precision >= PRECISION_MIN, (
        f"Precision {metricas.precision:.3f} < {PRECISION_MIN}. El sistema "
        f"está sobre-fusionando: {metricas.fp} pares unidos no debían unirse."
    )


def test_f1_no_retrocede(metricas) -> None:
    """F1 mínimo de regresión. Imprime el reporte completo si falla."""
    assert metricas.f1 >= F1_MIN, (
        f"F1 {metricas.f1:.3f} < {F1_MIN} — REGRESIÓN de calidad.\n" + metricas.resumen()
    )


def test_reporte_visible(metricas, capsys) -> None:
    """No es un assert de calidad: imprime el reporte para trazabilidad.

    Correr con ``pytest -s`` para ver F1/precision/recall actuales.
    """
    print("\n" + metricas.resumen())
