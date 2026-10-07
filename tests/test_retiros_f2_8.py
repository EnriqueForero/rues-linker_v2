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

F2.14 (2026-10-07, decisión de Enrique: «no porque no se use seguido se debe
eliminar»): se MANTIENEN los dos primeros retiros y el de ``optimization/``
(su reemplazo declarado desde v3.2.7 es ``evaluation.OrchestratorOptimizer``,
que ahora cubre también la validación cruzada y el espacio por parámetro).
``GroundTruthGenerator`` VUELVE a ``evaluation/ground_truth.py`` sin depender
de ``optimization/``; ``GroundTruthEvaluator`` sigue teniendo UNA sola
implementación (``evaluador_verdad.py``) y ``ground_truth.py`` la re-exporta.

Esta prueba fija lo que queda: los módulos retirados no existen, sus símbolos
no se usan en el código vivo (src/, scripts/, tests/; docstrings y comentarios
pueden nombrarlos para dejar constancia), lo conservado sigue importable y hay
UN solo optimizador y UN solo evaluador en src (AST).
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
    # ``GroundTruthGenerator`` no está: F2.14 lo restauró (ver docstring).
    # ``cross_validate`` no está: es el nombre de sklearn.model_selection y un
    # uso legítimo futuro haría fallar la prueba sin que nada retirado volviera.
    # Su retiro lo fija ``test_evaluador_separado_sigue_disponible`` (hasattr).
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
    # La validación cruzada vive en el optimizador (F2.14), no en el evaluador.
    assert not hasattr(GroundTruthEvaluator, "cross_validate")


def test_generador_restaurado_sin_optimization() -> None:
    """F2.14: el generador vuelve y re-exporta el ÚNICO evaluador."""
    from record_linkage.evaluation import (
        GroundTruthEvaluator,
        GroundTruthGenerator,
        ground_truth,
    )

    assert ground_truth.GroundTruthGenerator is GroundTruthGenerator
    assert ground_truth.GroundTruthEvaluator is GroundTruthEvaluator
    assert "optimization" not in ground_truth.__file__.replace("\\", "/").split("/")


def _clases_definidas(ruta: Path) -> list[str]:
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    return [n.name for n in ast.walk(arbol) if isinstance(n, ast.ClassDef)]


def test_un_solo_optimizador_y_un_solo_evaluador_en_src() -> None:
    """Regla 3 (una regla una vez): ``OptimizationEngine`` no vuelve y
    ``GroundTruthEvaluator`` se define exactamente una vez en ``src/``."""
    definiciones: dict[str, list[str]] = {}
    for ruta in sorted((RAIZ / "src").rglob("*.py")):
        for clase in _clases_definidas(ruta):
            definiciones.setdefault(clase, []).append(ruta.relative_to(RAIZ).as_posix())
    assert "OptimizationEngine" not in definiciones, definiciones.get("OptimizationEngine")
    assert definiciones.get("GroundTruthEvaluator") == [
        "src/record_linkage/evaluation/evaluador_verdad.py"
    ]
    assert definiciones.get("GroundTruthGenerator") == [
        "src/record_linkage/evaluation/ground_truth.py"
    ]
    assert definiciones.get("OrchestratorOptimizer") == [
        "src/record_linkage/evaluation/orchestrator_hyperparameters.py"
    ]


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


@pytest.fixture(scope="module")
def identificadores_vivos() -> dict[str, set[str]]:
    """``{ruta relativa: identificadores}`` de todo el código vivo, parseado UNA vez.

    Parsear los ~400 archivos con ``ast`` por cada símbolo costaba ≈1,4 s por
    caso (≈13 s el archivo); el dict se calcula una vez por módulo y cada caso
    parametrizado solo lo consulta.
    """
    return {p.relative_to(RAIZ).as_posix(): _identificadores(p) for p in _archivos_vivos()}


@pytest.mark.parametrize("simbolo", SIMBOLOS_RETIRADOS)
def test_simbolo_retirado_no_se_usa_en_codigo_vivo(
    simbolo: str, identificadores_vivos: dict[str, set[str]]
) -> None:
    con_uso = [ruta for ruta, ids in identificadores_vivos.items() if simbolo in ids]
    assert not con_uso, f"{simbolo} sigue en uso en: {con_uso}"
