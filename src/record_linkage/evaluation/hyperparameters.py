"""
evaluation.hyperparameters — record_linkage_pipeline

Componentes:
    - class HyperparameterOptimizer  (origen: notebook celda [134])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import time
from typing import Any

import optuna
import pandas as pd

from ..utils.logger import CustomLogger
from ._flags import OPTUNA_AVAILABLE
from .quality import QualityEvaluator


class HyperparameterOptimizer:
    """
    Optimizador de hiperparámetros usando Optuna.
    Encuentra la configuración óptima para maximizar calidad manteniendo eficiencia.
    """

    def __init__(self, pipeline, ground_truth_evaluator: QualityEvaluator | None = None):
        self.pipeline = pipeline
        self.evaluator = ground_truth_evaluator
        self.logger = CustomLogger("HyperparameterOptimizer")
        self.best_params = None
        self.optimization_history = []

    def optimize(
        self,
        train_data: pd.DataFrame,
        n_trials: int = 50,
        optimization_target: str = "f2_score",
        time_budget_minutes: float | None = None,
    ) -> dict[str, Any]:
        """
        Ejecutar optimización de hiperparámetros.

        Args:
            train_data: Datos de entrenamiento (subset)
            n_trials: Número de pruebas
            optimization_target: Métrica a optimizar ('f1_score', 'f2_score', 'recall', etc.)
            time_budget_minutes: Presupuesto de tiempo máximo

        Returns:
            Mejores parámetros encontrados
        """
        if not OPTUNA_AVAILABLE:
            raise ImportError("Optuna no está instalado")

        if not self.evaluator:
            raise ValueError("Se requiere un evaluador con ground truth")

        self.logger.info(
            f"Iniciando optimización: {n_trials} trials, objetivo: {optimization_target}"
        )

        # Crear función objetivo
        def objective(trial):
            # Sugerir parámetros
            params = self._suggest_parameters(trial)

            # Ejecutar pipeline con estos parámetros
            try:
                # Actualizar configuración del pipeline
                self._update_pipeline_config(params)

                # Ejecutar en datos de entrenamiento.
                # (v2.0.1) pipeline.run() retorna PipelineResult con carga lazy;
                # `df_linked` se materializa al accederlo y se libera tras el trial.
                start_time = time.time()
                result = self.pipeline.run(
                    sources={"train": train_data},
                    show_progress=False,  # Silencioso para optimización
                )
                execution_time = time.time() - start_time

                # Evaluar contra ground truth
                df_linked = result["df_linked"]
                if df_linked is None:
                    raise RuntimeError(
                        "El pipeline no produjo df_linked; revisar checkpoints de la fase 3."
                    )
                evaluation = self.evaluator.evaluate(df_linked, detailed=False)

                # Obtener métrica objetivo
                score = evaluation[optimization_target]

                # Penalizar si es muy lento (opcional)
                if execution_time > 300:  # 5 minutos
                    penalty = (execution_time - 300) / 1000
                    score = score * (1 - penalty)

                # Guardar en historial
                self.optimization_history.append(
                    {
                        "trial": trial.number,
                        "params": params,
                        "score": score,
                        "execution_time": execution_time,
                        "metrics": evaluation,
                    }
                )

                return score

            except Exception as e:
                self.logger.error(f"Error en trial {trial.number}: {e!s}")
                return 0.0  # Penalización máxima

        # Crear estudio
        study = optuna.create_study(
            direction="maximize", study_name=f"record_linkage_{optimization_target}"
        )

        # Configurar tiempo límite si se especifica
        timeout = time_budget_minutes * 60 if time_budget_minutes else None

        # Ejecutar optimización
        study.optimize(objective, n_trials=n_trials, timeout=timeout, show_progress_bar=True)

        # Guardar mejores parámetros
        self.best_params = study.best_params

        # Generar reporte
        report = self._generate_optimization_report(study)

        self.logger.info(
            f"Optimización completada. Mejor {optimization_target}: {study.best_value:.4f}"
        )

        return report

    def _suggest_parameters(self, trial) -> dict[str, Any]:
        """Sugerir parámetros para un trial."""
        params = {
            # Parámetros LSH
            "lsh_threshold": trial.suggest_float("lsh_threshold", 0.60, 0.85, step=0.05),
            "lsh_permutations": trial.suggest_int("lsh_permutations", 64, 128, step=32),
            # Parámetros de scoring
            "score_threshold": trial.suggest_float("score_threshold", 0.65, 0.85, step=0.05),
            "min_name_similarity": trial.suggest_float(
                "min_name_similarity", 0.50, 0.75, step=0.05
            ),
            "max_nit_distance": trial.suggest_int("max_nit_distance", 1, 4),
            # Preprocesamiento
            "remove_top_words": trial.suggest_int("remove_top_words", 5, 30, step=5),
            # Pesos
            "weight_name": trial.suggest_float("weight_name", 0.5, 0.8, step=0.1),
            "weight_nit": trial.suggest_float("weight_nit", 0.1, 0.4, step=0.1),
        }

        # Calcular peso fonético para que sumen 1
        params["weight_phonetic"] = 1 - params["weight_name"] - params["weight_nit"]

        # Ajustar si es negativo
        if params["weight_phonetic"] < 0:
            params["weight_phonetic"] = 0.05
            params["weight_name"] = 0.70
            params["weight_nit"] = 0.25

        return params

    def _update_pipeline_config(self, params: dict[str, Any]):
        """Actualizar configuración del pipeline con nuevos parámetros."""
        # Actualizar perfil
        profile = self.pipeline.config.get("profiles", {}).get(
            self.pipeline.config.get("profile", "standard"), {}
        )

        # Actualizar parámetros individuales
        profile.update(
            {
                "lsh_threshold": params["lsh_threshold"],
                "lsh_permutations": params["lsh_permutations"],
                "score_threshold": params["score_threshold"],
                "min_name_similarity": params["min_name_similarity"],
                "max_nit_distance": params["max_nit_distance"],
                "remove_top_words": params["remove_top_words"],
                "weights": {
                    "name": params["weight_name"],
                    "nit": params["weight_nit"],
                    "phonetic": params["weight_phonetic"],
                },
            }
        )

    def _generate_optimization_report(self, study) -> dict[str, Any]:
        """Generar reporte de optimización."""
        # Convertir historia a DataFrame
        history_df = pd.DataFrame(self.optimization_history)

        # Mejores trials
        best_trials = study.get_trials(deepcopy=False, states=[optuna.trial.TrialState.COMPLETE])
        best_trials = sorted(best_trials, key=lambda t: t.value, reverse=True)[:5]

        report = {
            "best_params": self.best_params,
            "best_score": study.best_value,
            "best_trial_number": study.best_trial.number,
            "total_trials": len(study.trials),
            "optimization_history": history_df,
            "top_5_trials": [
                {"trial": t.number, "score": t.value, "params": t.params} for t in best_trials
            ],
            "parameter_importance": self._calculate_parameter_importance(study),
            "convergence_plot": self._create_convergence_plot(history_df),
        }

        return report

    def _calculate_parameter_importance(self, study) -> pd.DataFrame:
        """Calcular importancia de cada parámetro."""
        if not OPTUNA_AVAILABLE:
            return pd.DataFrame()

        try:
            # Usar el analizador de importancia de Optuna
            importance = optuna.importance.get_param_importances(study)

            importance_df = pd.DataFrame(
                [{"parameter": param, "importance": imp} for param, imp in importance.items()]
            )

            importance_df = importance_df.sort_values("importance", ascending=False)

            return importance_df

        except Exception as e:
            self.logger.warning(f"No se pudo calcular importancia: {e!s}")
            return pd.DataFrame()

    def _create_convergence_plot(self, history_df: pd.DataFrame):
        """Crear gráfico de convergencia."""
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 6))

        # Score por trial
        ax.plot(history_df["trial"], history_df["score"], "b-", alpha=0.5, label="Score por trial")

        # Mejor score acumulado
        best_scores = history_df["score"].cummax()
        ax.plot(history_df["trial"], best_scores, "r-", linewidth=2, label="Mejor score")

        ax.set_xlabel("Trial")
        ax.set_ylabel("Score")
        ax.set_title("Convergencia de la Optimización")
        ax.legend()
        ax.grid(True, alpha=0.3)

        return fig

    def apply_best_params(self):
        """Aplicar los mejores parámetros encontrados al pipeline."""
        if not self.best_params:
            raise ValueError("No hay parámetros optimizados disponibles")

        self._update_pipeline_config(self.best_params)
        self.logger.info("Mejores parámetros aplicados al pipeline")
