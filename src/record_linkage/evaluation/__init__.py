"""record_linkage.evaluation — Módulos de evaluación contra ground truth.

Componentes principales:
    - evaluar_pares: cálculo de Precision/Recall/F1 a nivel de pares.
    - PairwiseMetrics: dataclass con los resultados.
    - GroundTruthEvaluator: grupos predichos contra una verdad con nulos
      (ÚNICA implementación en evaluador_verdad.py; separado en F2.8).
    - GroundTruthGenerator / ConfigGroundTruth: muestra estratificada de pares
      para revisión humana (ground_truth.py; restaurado en F2.14 sin
      optimization/).
    - OrchestratorOptimizer (v3.2.6; ampliado en F2.14 y F2.15): el ÚNICO
      optimizador Optuna, sobre Orchestrator.run(): default_search_space /
      espacio_de_busqueda, objetivos f1·f2·precision·recall·macro_f1,
      validacion_cruzada, ajustes_perfil para linkage(), verdad por registro
      o por pares etiquetados, evaluar_correlativa, guardar_estudio.
    - evaluar_pares_etiquetados / MetricasParesEtiquetados: la métrica sobre
      los pares que una persona etiquetó (pairwise.py; F2.15).
    - graficar_convergencia / graficar_importancia: figuras matplotlib de una
      optimización (graficas_optimizacion.py).
    - HyperparameterOptimizer: Optuna sobre linkage_pipeline.run() (legacy).
"""

from __future__ import annotations

from ._flags import OPTUNA_AVAILABLE
from .evaluador_verdad import GroundTruthEvaluator
from .graficas_optimizacion import graficar_convergencia, graficar_importancia
from .ground_truth import ConfigGroundTruth, GroundTruthGenerator
from .pairwise import (
    MetricasParesEtiquetados,
    PairwiseMetrics,
    evaluar_pares,
    evaluar_pares_etiquetados,
)

__all__ = [
    "OPTUNA_AVAILABLE",
    "ConfigGroundTruth",
    "GroundTruthEvaluator",
    "GroundTruthGenerator",
    "MetricasParesEtiquetados",
    "PairwiseMetrics",
    "evaluar_pares",
    "evaluar_pares_etiquetados",
    "graficar_convergencia",
    "graficar_importancia",
]

# Imports opt-in (requieren optuna)
if OPTUNA_AVAILABLE:
    from .hyperparameters import HyperparameterOptimizer
    from .orchestrator_hyperparameters import (
        OBJETIVOS_VALIDOS,
        OrchestratorOptimizer,
        default_search_space,
        espacio_de_busqueda,
    )

    __all__ += [
        "OBJETIVOS_VALIDOS",
        "HyperparameterOptimizer",
        "OrchestratorOptimizer",
        "default_search_space",
        "espacio_de_busqueda",
    ]
