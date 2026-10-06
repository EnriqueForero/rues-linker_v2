"""record_linkage.evaluation — Módulos de evaluación contra ground truth.

Componentes principales:
    - evaluar_pares: cálculo de Precision/Recall/F1 a nivel de pares.
    - PairwiseMetrics: dataclass con los resultados.
    - GroundTruthEvaluator: grupos predichos contra una verdad con nulos
      (antes en evaluation/ground_truth.py; separado en F2.8).
    - OrchestratorOptimizer (v3.2.6): Optuna sobre Orchestrator.run().
    - HyperparameterOptimizer: Optuna sobre linkage_pipeline.run() (legacy).
"""

from __future__ import annotations

from ._flags import OPTUNA_AVAILABLE
from .evaluador_verdad import GroundTruthEvaluator
from .pairwise import PairwiseMetrics, evaluar_pares

__all__ = [
    "OPTUNA_AVAILABLE",
    "GroundTruthEvaluator",
    "PairwiseMetrics",
    "evaluar_pares",
]

# Imports opt-in (requieren optuna)
if OPTUNA_AVAILABLE:
    from .hyperparameters import HyperparameterOptimizer
    from .orchestrator_hyperparameters import (
        OrchestratorOptimizer,
        default_search_space,
    )

    __all__ += [
        "HyperparameterOptimizer",
        "OrchestratorOptimizer",
        "default_search_space",
    ]
