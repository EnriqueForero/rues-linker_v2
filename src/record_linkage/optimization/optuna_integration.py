"""
optimization.optuna_integration — record_linkage_pipeline (DEPRECATED v3.2.7)

⚠️  DEPRECATED desde v3.2.7. Esta clase es código heredado del notebook
fuente. Para optimización Optuna sobre el flujo de producción real
(`Orchestrator.run()`), usar `record_linkage.evaluation.OrchestratorOptimizer`
(disponible desde v3.2.6).

Por ahora se mantiene importable para retrocompatibilidad, pero emitirá
DeprecationWarning al usarse. Será removido en v3.3.0.

Componentes:
    - class OptunaIntegration  (origen: notebook celda [167])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import time
import warnings
from typing import Any

import numpy as np
import optuna
import pandas as pd

from ..utils.output import safe_print as print
from .engine import OptimizationEngine

# v3.2.7 (FASE 4): emitir DeprecationWarning al importar este módulo.
warnings.warn(
    "record_linkage.optimization.optuna_integration es DEPRECATED desde v3.2.7. "
    "Usar record_linkage.evaluation.OrchestratorOptimizer (v3.2.6+) para "
    "optimización sobre Orchestrator.run(). Será removido en v3.3.0.",
    DeprecationWarning,
    stacklevel=2,
)


class OptunaIntegration:
    """
    Integra el motor de optimización con Optuna para búsqueda inteligente.
    """

    def __init__(
        self,
        optimization_engine: OptimizationEngine,
        metric: str = "f1_score",
        direction: str = "maximize",
    ):
        """
        Inicializa la integración con Optuna.

        Args:
            optimization_engine: Motor de optimización configurado
            metric: Métrica a optimizar ('f1_score', 'recall', 'precision')
            direction: Dirección de optimización ('maximize' o 'minimize')
        """
        self.engine = optimization_engine
        self.metric = metric
        self.direction = direction
        self.study = None
        self.trial_count = 0

    def objective(self, trial: optuna.Trial) -> float:
        """
        Función objetivo para Optuna.

        Args:
            trial: Trial de Optuna

        Returns:
            Valor de la métrica a optimizar
        """
        self.trial_count += 1

        # Obtener parámetros sugeridos
        params = self.engine.parameter_space.get_optuna_params(trial)

        # Añadir número de trial para tracking
        params["trial_number"] = self.trial_count

        print(f"\n🔄 Trial {self.trial_count}:")
        print("   Evaluando configuración...")

        # Evaluar configuración con timeout
        with self.engine._trial_context(self.trial_count):
            # Timeout handling
            start_time = time.time()

            # Evaluar
            metrics = self.engine.evaluate_single_config(params)

            # Verificar timeout
            if time.time() - start_time > self.engine.max_time_per_trial:
                raise optuna.TrialPruned()

            # Guardar resultados
            trial_result = {"trial_number": self.trial_count, **params, **metrics}
            self.engine.trial_results.append(trial_result)

            # Actualizar mejor resultado
            score = metrics.get(self.metric, 0.0)
            if score > self.engine.best_score:
                self.engine.best_score = score
                self.engine.best_config = params
                print(f"   🎯 ¡Nuevo mejor score! {self.metric}={score:.4f}")
            else:
                print(f"   📊 Score: {self.metric}={score:.4f}")

            # Guardar checkpoint cada 10 trials
            if self.trial_count % 10 == 0:
                self.engine.save_checkpoint(self.study, self.trial_count)

            # Reportar métricas adicionales a Optuna
            for key, value in metrics.items():
                if isinstance(value, int | float):
                    trial.set_user_attr(key, value)

            return score

    def create_study(self, study_name: str | None = None, load_if_exists: bool = True):
        """
        Crea o carga un estudio de Optuna.
        """
        storage = None
        if study_name:
            # Usar SQLite para persistencia
            db_path = self.engine.output_dir / f"{study_name}.db"
            storage = f"sqlite:///{db_path}"

        try:
            if load_if_exists and study_name:
                self.study = optuna.load_study(study_name=study_name, storage=storage)
                self.trial_count = len(self.study.trials)
                print(f"✅ Estudio '{study_name}' cargado con {self.trial_count} trials previos")
            else:
                raise ValueError("Crear nuevo estudio")
        except:
            self.study = optuna.create_study(
                study_name=study_name,
                storage=storage,
                direction=self.direction,
                sampler=optuna.samplers.TPESampler(seed=42),
                pruner=optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=10),
            )
            print(f"✅ Nuevo estudio creado: '{study_name}'")

        return self.study

    def optimize(
        self,
        n_trials: int = 100,
        timeout: int | None = None,
        n_jobs: int = 1,
        show_progress_bar: bool = True,
    ):
        """
        Ejecuta la optimización.

        Args:
            n_trials: Número de trials a ejecutar
            timeout: Tiempo máximo total en segundos
            n_jobs: Número de trabajos paralelos
            show_progress_bar: Mostrar barra de progreso
        """
        if self.study is None:
            self.create_study()

        print(f"\n🚀 Iniciando optimización con {n_trials} trials...")
        print(f"   Métrica objetivo: {self.metric} ({self.direction})")

        try:
            self.study.optimize(
                self.objective,
                n_trials=n_trials,
                timeout=timeout,
                n_jobs=n_jobs,
                show_progress_bar=show_progress_bar,
            )
        except KeyboardInterrupt:
            print("\n⚠️ Optimización interrumpida por el usuario")

        print(f"\n✅ Optimización completada: {len(self.study.trials)} trials ejecutados")

        # Guardar checkpoint final
        self.engine.save_checkpoint(self.study, len(self.study.trials))

        return self.study

    def get_best_params(self) -> dict[str, Any]:
        """Obtiene los mejores parámetros encontrados."""
        if self.study is None or len(self.study.trials) == 0:
            return {}

        best_trial = self.study.best_trial
        best_params = best_trial.params.copy()

        # DEBUGGING TEMPORAL
        print(f"DEBUG - best_trial.value: {best_trial.value}")
        print(f"DEBUG - best_trial.user_attrs: {best_trial.user_attrs}")

        # Añadir métricas del mejor trial
        best_params["best_score"] = best_trial.value
        for key, value in best_trial.user_attrs.items():
            best_params[f"metric_{key}"] = value

        return best_params

    def get_param_importance(self) -> pd.DataFrame:
        """
        Calcula la importancia de cada parámetro.
        """
        if self.study is None or len(self.study.trials) < 10:
            return pd.DataFrame()

        importances = optuna.importance.get_param_importances(self.study)

        df = pd.DataFrame([{"parameter": k, "importance": v} for k, v in importances.items()])

        return df.sort_values("importance", ascending=False)

    def analyze_convergence(self) -> dict[str, Any]:
        """
        Analiza la convergencia de la optimización.
        """
        if not self.study:
            return {}

        trials_df = self.study.trials_dataframe()

        # Calcular métricas de convergencia
        values = trials_df["value"].values
        best_so_far = np.maximum.accumulate(values)

        # Detectar punto de convergencia (donde mejora < 1%)
        improvement_threshold = 0.01
        converged_at = len(values)

        for i in range(10, len(values)):
            recent_improvement = (best_so_far[i] - best_so_far[i - 10]) / best_so_far[i - 10]
            if recent_improvement < improvement_threshold:
                converged_at = i
                break

        return {
            "total_trials": len(values),
            "converged_at_trial": converged_at,
            "final_best_score": best_so_far[-1],
            "improvement_last_10": (best_so_far[-1] - best_so_far[-11]) / best_so_far[-11]
            if len(values) > 10
            else 0,
        }
