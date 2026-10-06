"""F2.8 — retiros con evidencia.

Lo medido el 6 de octubre de 2026 (``git grep`` sobre src/, tests/, scripts/,
notebooks/ y docs/) antes de retirar:

* ``deduplicate_dataframe`` (y con ella ``analyze_duplicates`` y
  ``deduplicate_file``, del mismo módulo): 0 usos fuera de su módulo.
* ``deduplicate_large_dataset_colab``: nadie la importaba desde src/, scripts/
  ni notebooks/; una sola prueba la ejercitaba. Deduplicaba por chunks sin
  enlazar entidades entre chunks.
* ``OptimizationEngine``, ``optuna_integration``, ``visualizer`` y
  ``ParameterSpace`` (paquete ``optimization/``): deprecados desde v3.2.7; sus
  únicos consumidores eran ``GroundTruthEvaluator.cross_validate`` y 2 pruebas.
* ``GroundTruthGenerator``: 2 pruebas humo. ``GroundTruthEvaluator`` se separó
  antes en ``evaluation/evaluador_verdad.py`` (sin ``cross_validate``, que
  dependía del motor retirado).

Esta prueba fija el retiro: los módulos no existen, los símbolos no se
usan en el código vivo (src/, scripts/, tests/; docstrings y comentarios pueden
nombrarlos para dejar constancia) y lo que se conservó
sigue importable desde su sitio nuevo.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
ESTE_ARCHIVO = Path(__file__).resolve()

MODULOS_RETIRADOS = [
    "record_linkage.deduplication.dataframe",
    "record_linkage.evaluation.ground_truth",
    "record_linkage.optimization",
    "record_linkage.optimization.engine",
    "record_linkage.optimization.optuna_integration",
    "record_linkage.optimization.parameters",
    "record_linkage.optimization.visualizer",
]

SIMBOLOS_RETIRADOS = [
    "deduplicate_dataframe",
    "deduplicate_large_dataset_colab",
    "CrossChunkDeduplicationWarning",
    "OptimizationEngine",
    "OptunaIntegration",
    "OptimizationVisualizerLite",
    "ParameterSpace",
    "GroundTruthGenerator",
    "cross_validate",
]


def _existe_modulo(nombre: str) -> bool:
    """``find_spec`` importa el paquete padre: si ese ya no existe, lanza."""
    try:
        return importlib.util.find_spec(nombre) is not None
    except ModuleNotFoundError:
        return False


@pytest.mark.parametrize("modulo", MODULOS_RETIRADOS)
def test_modulo_retirado_no_existe(modulo: str) -> None:
    assert not _existe_modulo(modulo), f"{modulo} debía estar retirado"


def test_colab_ya_no_expone_deduplicacion_por_chunks() -> None:
    colab = importlib.import_module("record_linkage.deduplication.colab")
    assert not hasattr(colab, "deduplicate_large_dataset_colab")
    assert not hasattr(colab, "CrossChunkDeduplicationWarning")
    # Lo que sigue vivo (lo ejercitan test_colab_safe_cache y
    # test_safe_cleanup_paths_v013) no se toca.
    assert hasattr(colab, "ColabOptimizedManager")
    assert hasattr(colab, "SQLiteJSONCache")


def test_evaluador_separado_sigue_disponible() -> None:
    from record_linkage.evaluation import GroundTruthEvaluator
    from record_linkage.evaluation.evaluador_verdad import (
        GroundTruthEvaluator as DesdeModulo,
    )

    assert GroundTruthEvaluator is DesdeModulo
    assert not hasattr(GroundTruthEvaluator, "cross_validate")


def _archivos_vivos() -> list[Path]:
    """Código vivo: src/, scripts/ y tests/, sin esta prueba (regla 7 del preámbulo)."""
    archivos: list[Path] = []
    for carpeta in ("src", "scripts", "tests"):
        archivos.extend(p for p in (RAIZ / carpeta).rglob("*.py") if p.resolve() != ESTE_ARCHIVO)
    return sorted(archivos)


def _identificadores(ruta: Path) -> set[str]:
    """Nombres, atributos, imports y definiciones del módulo (AST): ni docstrings
    ni comentarios, que sí pueden nombrar lo retirado para dejar constancia."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Name):
            ids.add(nodo.id)
        elif isinstance(nodo, ast.Attribute):
            ids.add(nodo.attr)
        elif isinstance(nodo, ast.alias):
            ids.update(nodo.name.split("."))
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            ids.update(nodo.module.split("."))
        elif isinstance(nodo, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            ids.add(nodo.name)
    return ids


@pytest.mark.parametrize("simbolo", SIMBOLOS_RETIRADOS)
def test_simbolo_retirado_no_se_usa_en_codigo_vivo(simbolo: str) -> None:
    con_uso = [
        p.relative_to(RAIZ).as_posix() for p in _archivos_vivos() if simbolo in _identificadores(p)
    ]
    assert not con_uso, f"{simbolo} sigue en uso en: {con_uso}"
