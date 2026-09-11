"""tests/integration/test_pipeline_result.py

Tests unitarios del nuevo contrato `PipelineResult` (F1.4).

Cubre:
    - Carga lazy de DataFrames desde parquet.
    - Compatibilidad dict-like (`__getitem__`, `get`, `__contains__`).
    - Manejo de rutas None (golden_path = None → golden_records = None).
    - Caché funciona (segundo acceso no relee el archivo).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.pipeline.result import PipelineResult


@pytest.fixture
def workspace_with_parquets(tmp_path: Path) -> Path:
    """Crea un workspace temporal con 3 parquets mínimos simulando los
    checkpoints que escribe RecordLinkagePipeline."""
    golden = pd.DataFrame({"ID_GRUPO": [1, 2], "NIT_FINAL": ["900", "800"]})
    correlative = pd.DataFrame(
        {
            "ID_GRUPO": [1, 1, 2],
            "NIT": ["900", "900", "800"],
            "RAZON_SOCIAL": ["ACME", "ACME SAS", "BETA"],
        }
    )
    linked = pd.DataFrame({"ID_GRUPO": [1, 1, 2], "score": [0.9, 0.85, 1.0]})

    golden.to_parquet(tmp_path / "golden.parquet")
    correlative.to_parquet(tmp_path / "correlative.parquet")
    linked.to_parquet(tmp_path / "linked.parquet")

    return tmp_path


def test_lazy_load_golden_records(workspace_with_parquets: Path):
    """golden_records debe leer el parquet la primera vez que se accede."""
    result = PipelineResult(
        work_dir=workspace_with_parquets,
        golden_path=workspace_with_parquets / "golden.parquet",
        correlative_path=workspace_with_parquets / "correlative.parquet",
        linked_path=workspace_with_parquets / "linked.parquet",
    )
    df = result.golden_records
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert "ID_GRUPO" in df.columns


def test_dict_like_getitem(workspace_with_parquets: Path):
    """result['correlative_table'] debe funcionar (backward-compat)."""
    result = PipelineResult(
        work_dir=workspace_with_parquets,
        golden_path=workspace_with_parquets / "golden.parquet",
        correlative_path=workspace_with_parquets / "correlative.parquet",
        linked_path=workspace_with_parquets / "linked.parquet",
    )
    df = result["correlative_table"]
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3


def test_dict_like_get_with_default(workspace_with_parquets: Path):
    """result.get('key', default) debe devolver default si la clave no existe."""
    result = PipelineResult(
        work_dir=workspace_with_parquets,
        golden_path=workspace_with_parquets / "golden.parquet",
        correlative_path=workspace_with_parquets / "correlative.parquet",
    )
    assert result.get("inexistente", "fallback") == "fallback"
    assert result.get("golden_records") is not None


def test_returns_none_when_path_is_none():
    """Si una ruta no se provee, la propiedad correspondiente debe retornar None
    sin levantar excepción."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = PipelineResult(
            work_dir=Path(tmpdir),
            golden_path=None,
            correlative_path=None,
            linked_path=None,
        )
    assert result.golden_records is None
    assert result.correlative_table is None
    assert result.df_linked is None


def test_cached_property_does_not_reread(workspace_with_parquets: Path):
    """Acceder dos veces debe devolver el MISMO objeto en memoria (caché)."""
    result = PipelineResult(
        work_dir=workspace_with_parquets,
        correlative_path=workspace_with_parquets / "correlative.parquet",
    )
    df1 = result.correlative_table
    df2 = result.correlative_table
    assert df1 is df2  # mismo objeto, no relectura


def test_contains_operator(workspace_with_parquets: Path):
    """`"correlative_table" in result` debe ser True si el parquet existe."""
    result = PipelineResult(
        work_dir=workspace_with_parquets,
        correlative_path=workspace_with_parquets / "correlative.parquet",
        extra={"custom_metric": 0.95},
    )
    assert "correlative_table" in result
    assert "custom_metric" in result
    assert "no_existe" not in result


def test_extra_dict_accessible(workspace_with_parquets: Path):
    """Resultados adicionales en `extra` deben ser accesibles via __getitem__."""
    result = PipelineResult(
        work_dir=workspace_with_parquets,
        extra={"load_report": {"loaded_sources": 3}, "execution_seconds": 12.5},
    )
    assert result["load_report"]["loaded_sources"] == 3
    assert result["execution_seconds"] == 12.5


def test_to_dict_materialize_true(workspace_with_parquets: Path):
    """to_dict(materialize=True) debe cargar los DataFrames a memoria."""
    result = PipelineResult(
        work_dir=workspace_with_parquets,
        golden_path=workspace_with_parquets / "golden.parquet",
        correlative_path=workspace_with_parquets / "correlative.parquet",
    )
    d = result.to_dict(materialize=True)
    assert isinstance(d["golden_records"], pd.DataFrame)
    assert isinstance(d["correlative_table"], pd.DataFrame)
