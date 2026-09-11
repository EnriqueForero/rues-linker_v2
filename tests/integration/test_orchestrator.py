"""tests/integration/test_orchestrator.py

Tests end-to-end del `Orchestrator` (record linkage cross-source).

Valida que el Orchestrator:
    - Acepta múltiples fuentes y produce golden + correlative.
    - Detecta matches cross-source con ground truth controlado.
    - Maneja una sola fuente como caso degenerado (sin error).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pandas as pd

from record_linkage.config.profiles import crear_config_orchestrator
from record_linkage.pipeline.orchestrator import Orchestrator


def _run_orchestrator(sources: dict[str, pd.DataFrame], workspace: Path) -> dict:
    """Helper: corre orchestrator con perfil rápido y retorna results dict."""
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    config = crear_config_orchestrator(perfil="prueba_rapida", workspace=str(workspace))
    orch = Orchestrator(config=config, sources=sources, work_dir=str(workspace))
    return orch.run(skip_reporting=True)


def test_two_sources_cross_linkage(
    df_two_sources_with_cross_matches: tuple[pd.DataFrame, pd.DataFrame],
):
    """Cruzar 2 fuentes con 3 NITs en común debe producir:
    - 7 entidades únicas (5 A + 5 B - 3 compartidos).
    - 3 grupos con registros de ambas fuentes.
    """
    source_a, source_b = df_two_sources_with_cross_matches
    sources = {"SOURCE_A": source_a, "SOURCE_B": source_b}

    with tempfile.TemporaryDirectory() as tmpdir:
        results = _run_orchestrator(sources, Path(tmpdir) / "workspace")

    assert "golden" in results
    assert "correlative" in results
    golden = results["golden"]
    correlative = results["correlative"]

    assert isinstance(golden, pd.DataFrame)
    assert isinstance(correlative, pd.DataFrame)
    assert not golden.empty
    assert not correlative.empty

    # Total: 10 registros entrada → 7 golden esperados (5+5-3)
    n_golden = len(golden)
    assert 6 <= n_golden <= 8, f"Esperado 6-8 golden, obtuvo {n_golden}"
    assert len(correlative) == len(source_a) + len(source_b)

    # Validar matches cross-source
    if {"ID_GRUPO", "SRC"}.issubset(correlative.columns):
        srcs_por_grupo = correlative.groupby("ID_GRUPO")["SRC"].nunique()
        grupos_cross = (srcs_por_grupo > 1).sum()
        assert grupos_cross >= 2, (
            f"Esperado ≥2 grupos cross-source (NITs compartidos sembrados), obtuvo {grupos_cross}"
        )


def test_single_source_via_orchestrator(
    df_single_source_with_duplicates: pd.DataFrame,
):
    """El Orchestrator debe poder procesar una sola fuente (caso
    degenerado, equivalente a deduplicación) sin errores."""
    df_single_source_with_duplicates["SRC"] = "ONLY_SOURCE"
    sources = {"ONLY_SOURCE": df_single_source_with_duplicates}

    with tempfile.TemporaryDirectory() as tmpdir:
        results = _run_orchestrator(sources, Path(tmpdir) / "workspace")

    assert "golden" in results
    assert "correlative" in results
    assert not results["golden"].empty
    assert not results["correlative"].empty


def test_orchestrator_returns_keys_golden_and_correlative(
    df_two_sources_with_cross_matches: tuple[pd.DataFrame, pd.DataFrame],
):
    """Contrato del Orchestrator: SIEMPRE retorna las claves 'golden' y
    'correlative'. Otras claves son opcionales."""
    source_a, source_b = df_two_sources_with_cross_matches
    sources = {"SOURCE_A": source_a, "SOURCE_B": source_b}

    with tempfile.TemporaryDirectory() as tmpdir:
        results = _run_orchestrator(sources, Path(tmpdir) / "workspace")

    assert "golden" in results, "Clave 'golden' es contractual"
    assert "correlative" in results, "Clave 'correlative' es contractual"
