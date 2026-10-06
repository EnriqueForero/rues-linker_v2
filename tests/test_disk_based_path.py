"""Test de regresión v2.10.0 — Path disk_based del pipeline.

Verifica que el pipeline funcione end-to-end con engine_type=disk_based,
que es la ruta CRÍTICA para producción con >1M registros en Colab.

Bug encontrado en v2.10.0 (heredado de v2.7.0/v2.8.0):
    `DiskBasedLSHEngine.find_candidates()` no aceptaba el kwarg
    `trusted_unique_sources` que `RecordLinkageEngine.link()` le pasaba
    incondicionalmente. Esto rompía con TypeError cualquier corrida con
    `linkage_engine_class="disk_based"`.

Fix v2.10.0 (MIGRATION_LOG §21): aceptar el kwarg en DiskBasedLSHEngine
(ignorándolo con warning si no está vacío) y en TrustedSourceLSHEngine
(uniéndolo al set del __init__).

Este test corre el flujo completo con disk_based forzado y verifica que:
    1. No levanta excepción.
    2. Produce una correlativa con todos los registros.
    3. Da F1 razonable sobre el dataset robusto (no peor que F1=0.85).
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.deduplication.unified import (
    AjustesDeduplicacion,
    deduplicate_unified,
)
from record_linkage.evaluation.pairwise import evaluar_pares

DATASET = Path(__file__).parent / "data" / "golden_truth_sintetico_robusto.csv"


@pytest.fixture(scope="module")
def truth() -> pd.DataFrame:
    if not DATASET.exists():
        pytest.skip(f"Dataset no disponible: {DATASET}")
    df = pd.read_csv(DATASET, dtype={"NIT": str})
    df["NIT"] = df["NIT"].fillna("")
    return df


def _correr_con_motor(truth: pd.DataFrame, engine: str) -> pd.DataFrame:
    """F2.9: el motor se fuerza con ``AjustesDeduplicacion`` en lugar de
    construir ``RecordLinkagePipeline`` a mano (misma partición, medido)."""
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
            with tempfile.TemporaryDirectory() as tmp:
                corr, _ = deduplicate_unified(
                    truth[["NIT", "RAZON_SOCIAL"]].copy(),
                    "NIT",
                    "RAZON_SOCIAL",
                    "BALANCEADO",
                    output_dir=tmp,
                    ajustes=AjustesDeduplicacion(motor=engine),
                )
    finally:
        logging.disable(logging.NOTSET)
    return corr.sort_values("ORIGINAL_INDEX").reset_index(drop=True)


def test_disk_based_no_levanta_excepcion(truth) -> None:
    """v2.10.0 bug-fix: trusted_unique_sources kwarg debe ser aceptado."""
    corr = _correr_con_motor(truth, "disk_based")
    assert len(corr) == len(truth), f"Correlativa debe tener {len(truth)} filas, tiene {len(corr)}"
    assert "ID_GRUPO" in corr.columns


def test_disk_based_produce_calidad_razonable(truth) -> None:
    """El motor disk_based debe dar F1 >= 0.85 sobre el dataset robusto."""
    corr = _correr_con_motor(truth, "disk_based")
    m = evaluar_pares(truth["ID_GROUP"].to_numpy(), corr["ID_GRUPO"].to_numpy())
    # Piso conservador: ambas rutas (default/disk_based) deben superar 0.85.
    assert m.f1 >= 0.85, (
        f"F1={m.f1:.3f} con disk_based es demasiado bajo (P={m.precision:.3f}, R={m.recall:.3f})"
    )


def test_disk_based_da_pred_para_todos_los_registros(truth) -> None:
    """Ningún registro queda sin grupo asignado en el path disk_based."""
    corr = _correr_con_motor(truth, "disk_based")
    assert corr["ID_GRUPO"].notna().all(), (
        f"Hay {corr['ID_GRUPO'].isna().sum()} registros sin grupo asignado"
    )


def test_disk_based_y_default_dan_resultados_similares(truth) -> None:
    """No deben divergir más de 0.05 F1 entre motores (regresión sanity check)."""
    corr_default = _correr_con_motor(truth, "default")
    corr_disk = _correr_con_motor(truth, "disk_based")
    m_default = evaluar_pares(truth["ID_GROUP"].to_numpy(), corr_default["ID_GRUPO"].to_numpy())
    m_disk = evaluar_pares(truth["ID_GROUP"].to_numpy(), corr_disk["ID_GRUPO"].to_numpy())
    delta = abs(m_default.f1 - m_disk.f1)
    assert delta <= 0.05, (
        f"Divergencia entre motores demasiado grande: "
        f"default F1={m_default.f1:.3f}, disk_based F1={m_disk.f1:.3f}, "
        f"|Δ|={delta:.3f}"
    )
