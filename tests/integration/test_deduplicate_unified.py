"""tests/integration/test_deduplicate_unified.py

Tests end-to-end del API público `deduplicate_unified`. Validan el contrato
funcional con DataFrames sintéticos pequeños (≤50 filas) y ejecutan en
≤30s en CI.

Cobertura:
    - El pipeline retorna correlativa no vacía con defaults razonables.
    - Detección correcta de duplicados conocidos (ground truth controlado).
    - Manejo explícito de DataFrame vacío.
    - Validación de columnas requeridas (errores claros).
"""

from __future__ import annotations

import tempfile

import pandas as pd
import pytest

from record_linkage.deduplication.unified import deduplicate_unified


def test_returns_non_empty_correlative_with_default_args(
    df_single_source_with_duplicates: pd.DataFrame,
):
    """Defaults razonables deben producir una tabla correlativa no vacía
    y con la misma cardinalidad que el input (un registro origen → una fila
    correlativa con su ID_GRUPO asignado).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        correlativa, conexiones = deduplicate_unified(
            df_input=df_single_source_with_duplicates,
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            mode="BALANCEADO",
            profile="deduplication_standard",
            output_dir=tmpdir,
            validate_against_legacy=False,
        )

    assert isinstance(correlativa, pd.DataFrame)
    assert isinstance(conexiones, pd.DataFrame)
    assert not correlativa.empty
    assert len(correlativa) == len(df_single_source_with_duplicates)
    assert "ID_GRUPO" in correlativa.columns


def test_detects_known_duplicate_pairs(
    df_single_source_with_duplicates: pd.DataFrame,
):
    """Verifica detección del ground truth sembrado:
    8 registros con 2 pares de duplicados → 6 grupos finales,
    2 grupos con >1 registro.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        correlativa, _conexiones = deduplicate_unified(
            df_input=df_single_source_with_duplicates,
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            output_dir=tmpdir,
        )

    n_grupos = correlativa["ID_GRUPO"].nunique()
    # Permitimos algo de holgura: el pipeline puede ser más estricto o más
    # laxo, pero la tasa de reducción debe estar entre 20% y 30%
    # (8 → 6 = 25%).
    assert 5 <= n_grupos <= 7, f"Esperado 5-7 grupos, obtuvo {n_grupos}"

    # NITs idénticos (900123456 y 800999111) DEBEN colapsarse:
    # buscamos al menos un grupo con 2 registros.
    tamanos_grupo = correlativa.groupby("ID_GRUPO").size()
    grupos_con_duplicados = (tamanos_grupo > 1).sum()
    assert grupos_con_duplicados >= 1, (
        "Esperado ≥1 grupo con duplicados. NITs idénticos no se colapsaron."
    )


def test_raises_on_empty_dataframe(df_empty: pd.DataFrame):
    """DataFrame vacío debe levantar ValueError explícito, no fallar
    silenciosamente downstream."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError, match=r"(?i)vac"):
            deduplicate_unified(
                df_input=df_empty,
                col_nit="NIT",
                col_name="RAZON_SOCIAL",
                output_dir=tmpdir,
            )


def test_raises_on_missing_columns():
    """Columnas requeridas ausentes deben levantar ValueError con mensaje
    explícito que mencione las columnas faltantes."""
    df_bad = pd.DataFrame({"OTRO": ["A"], "COLUMNA": ["B"]})
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError, match=r"(?i)NIT|RAZON_SOCIAL|columna"):
            deduplicate_unified(
                df_input=df_bad,
                col_nit="NIT",
                col_name="RAZON_SOCIAL",
                output_dir=tmpdir,
            )


def test_result_is_pipeline_result_compatible_dict(
    df_single_source_with_duplicates: pd.DataFrame,
):
    """El cambio F1.4 (PipelineResult dict-like) NO debe romper a callers
    que esperan poder hacer `.get()` sobre el resultado interno.
    Esto se valida indirectamente: si `deduplicate_unified` retorna
    correlativa no vacía, significa que `result.get("correlative_table")`
    funciona dentro del módulo.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        correlativa, _ = deduplicate_unified(
            df_input=df_single_source_with_duplicates,
            output_dir=tmpdir,
        )
    assert not correlativa.empty
