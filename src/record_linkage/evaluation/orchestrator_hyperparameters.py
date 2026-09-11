"""evaluation.orchestrator_hyperparameters — Optuna sobre Orchestrator (v3.2.6).

A diferencia de `HyperparameterOptimizer` (que trabaja con `linkage_pipeline.run()`),
esta clase optimiza hiperparámetros sobre el flujo de producción real
(`Orchestrator.run()`), evaluando contra un ground truth a nivel de pares.

Diseño:
    - Recibe un `base_config` dict (el que pasarías al Orchestrator).
    - Recibe `sources` (dict de DataFrames) y `truth` (DF con ID_REGISTRO + ID_GROUP).
    - En cada trial:
        1. Copia profunda del config.
        2. Aplica params sugeridos sobre profiles[active].
        3. Corre Orchestrator en un work_dir temporal.
        4. Alinea correlativa con truth → calcula F1 vía evaluar_pares().
        5. Retorna el score (F1 por defecto).
    - Devuelve un `best_config` listo para producción + historial completo.

Uso típico:
    from record_linkage.evaluation.orchestrator_hyperparameters import (
        OrchestratorOptimizer
    )

    base_cfg = crear_config_orchestrator(perfil="produccion_calibrada",
                                          validate=False)
    optimizer = OrchestratorOptimizer(
        base_config=base_cfg,
        sources=mis_fuentes,
        truth=mi_ground_truth,
    )
    result = optimizer.optimize(n_trials=20, optimization_target="f1")
    best_cfg = result["best_config"]  # listo para producción
"""

from __future__ import annotations

import contextlib
import copy
import io
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from ..utils.logger import CustomLogger
from ._flags import OPTUNA_AVAILABLE
from .pairwise import PairwiseMetrics, evaluar_pares


# ─────────────────────────────────────────────────────────────────────────
#  Estrategia de búsqueda — define el espacio Optuna
# ─────────────────────────────────────────────────────────────────────────
def default_search_space(trial) -> dict[str, Any]:
    """Espacio de búsqueda por defecto para el Orchestrator.

    Cubre los parámetros que el código REALMENTE LEE (verificados en
    auditoría Fase 1). Cualquiera puede pasar su propia función al
    constructor de OrchestratorOptimizer si necesita otro espacio.

    Returns:
        Dict con valores sugeridos para cada parámetro. Estructura plana
        (se inyecta directo en profiles[active] por OrchestratorOptimizer).
    """
    params = {
        # LSH (filtro previo — más permisivo aquí, más estricto en scoring)
        "lsh_threshold": trial.suggest_float("lsh_threshold", 0.50, 0.75, step=0.05),
        # Filtros previos al scoring (los más decisivos para precision)
        "score_threshold": trial.suggest_float("score_threshold", 0.45, 0.85, step=0.05),
        "min_name_similarity": trial.suggest_float("min_name_similarity", 0.40, 0.85, step=0.05),
        "max_nit_distance": trial.suggest_int("max_nit_distance", 0, 3),
        "nit_empty_passes_filter": trial.suggest_categorical(
            "nit_empty_passes_filter", [True, False]
        ),
        # Pesos (con phonetic=0 por evidencia, name/nit a calibrar)
        "weight_name": trial.suggest_float("weight_name", 0.30, 0.70, step=0.05),
    }
    # Asegurar suma = 1.0; phonetic = 0 (recomendación heredada IT-7)
    params["weight_nit"] = 1.0 - params["weight_name"]
    return params


# ─────────────────────────────────────────────────────────────────────────
#  Optimizador
# ─────────────────────────────────────────────────────────────────────────
class OrchestratorOptimizer:
    """Optimizador Optuna que trabaja con `Orchestrator.run()` real.

    Args:
        base_config: dict de configuración completo (con 'profile' y 'profiles').
            Sirve de plantilla; los params del trial se aplican encima.
        sources: dict {nombre_fuente: DataFrame}.
        truth: DataFrame con columnas 'ID_REGISTRO' y 'ID_GROUP' alineadas
            con las fuentes (cada ID_REGISTRO de las sources debe existir
            en truth).
        search_space: función `(trial) -> dict[str, Any]` que define el
            espacio de búsqueda. Default: `default_search_space`.
        silent: si True (default), silencia logs del Orchestrator durante
            los trials para no inundar la consola.
        keep_workdir: si True, conserva los work_dirs de cada trial (útil
            para debug). Default False (se borran al terminar).

    Raises:
        ImportError: si Optuna no está instalado.
        ValueError: si truth no tiene las columnas requeridas.
    """

    def __init__(
        self,
        base_config: dict[str, Any],
        sources: dict[str, pd.DataFrame],
        truth: pd.DataFrame,
        search_space: Callable | None = None,
        silent: bool = True,
        keep_workdir: bool = False,
    ):
        if not OPTUNA_AVAILABLE:
            raise ImportError(
                "Optuna no está instalado. Instalar con: pip install 'rues-linker[optimization]'"
            )

        # Validar truth
        required_cols = {"ID_REGISTRO", "ID_GROUP"}
        missing = required_cols - set(truth.columns)
        if missing:
            raise ValueError(f"`truth` debe tener columnas {required_cols}. Falta(n): {missing}")
        # Validar base_config
        if "profile" not in base_config or "profiles" not in base_config:
            raise ValueError("base_config debe tener claves 'profile' y 'profiles'.")
        active = base_config["profile"]
        if active not in base_config["profiles"]:
            raise ValueError(
                f"base_config['profile']='{active}' no está en base_config['profiles']."
            )

        self.base_config = base_config
        self.sources = sources
        self.truth = truth[["ID_REGISTRO", "ID_GROUP"]].copy()
        self.search_space = search_space or default_search_space
        self.silent = silent
        self.keep_workdir = keep_workdir

        self.logger = CustomLogger("OrchestratorOptimizer")
        self.best_params: dict[str, Any] | None = None
        self.best_config: dict[str, Any] | None = None
        self.optimization_history: list[dict[str, Any]] = []
        self._tmp_workdirs: list[Path] = []  # para cleanup

    # ─────────────────────────────────────────────────────────────────────
    #  Helpers internos
    # ─────────────────────────────────────────────────────────────────────
    def _build_config_for_trial(self, params: dict[str, Any]) -> dict[str, Any]:
        """Inyecta los params del trial en una copia del base_config."""
        cfg = copy.deepcopy(self.base_config)
        active = cfg["profile"]
        prof = cfg["profiles"][active]

        # Mapear params planos → estructura del config
        weights = None
        for key, value in params.items():
            if key == "weight_name":
                weights = weights or {}
                weights["name"] = value
            elif key == "weight_nit":
                weights = weights or {}
                weights["nit"] = value
            elif key == "weight_phonetic":
                weights = weights or {}
                weights["phonetic"] = value
            else:
                prof[key] = value

        if weights is not None:
            # Asegurar 3 claves; default phonetic=0
            prof["weights"] = {
                "name": weights.get("name", 0.5),
                "nit": weights.get("nit", 0.5),
                "phonetic": weights.get("phonetic", 0.0),
            }
        return cfg

    def _run_orchestrator(self, cfg: dict[str, Any]) -> tuple[pd.DataFrame | None, float]:
        """Corre Orchestrator en un work_dir temporal y devuelve correlativa + tiempo.

        Args:
            cfg: configuración inyectada del trial.

        Returns:
            (correlativa_df, tiempo_segundos). Si el run falla, correlativa=None.
        """
        from ..pipeline.orchestrator import Orchestrator

        tmp_dir = Path(tempfile.mkdtemp(prefix="optuna_trial_"))
        if not self.keep_workdir:
            self._tmp_workdirs.append(tmp_dir)
        cfg["output_directory"] = str(tmp_dir)

        t0 = time.perf_counter()
        try:
            if self.silent:
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    orch = Orchestrator(
                        config=cfg,
                        sources=copy.deepcopy(self.sources),
                        work_dir=str(tmp_dir),
                    )
                    result = orch.run(skip_reporting=True)
            else:
                orch = Orchestrator(
                    config=cfg,
                    sources=copy.deepcopy(self.sources),
                    work_dir=str(tmp_dir),
                )
                result = orch.run(skip_reporting=True)
        except Exception as exc:
            self.logger.warning(f"Trial falló durante Orchestrator.run(): {exc}")
            return None, time.perf_counter() - t0

        return result.get("correlative"), time.perf_counter() - t0

    def _evaluate(
        self, correlative: pd.DataFrame, optimization_target: str
    ) -> tuple[float, dict[str, Any]]:
        """Alinea correlativa con truth y calcula métricas.

        Returns:
            (score_target, metrics_dict)
        """
        if "ID_GRUPO" not in correlative.columns:
            return 0.0, {"error": "correlativa sin ID_GRUPO"}

        # Detectar columna de ID en correlative
        col_id = None
        for cand in ("ID_REGISTRO", "ID", "id_registro"):
            if cand in correlative.columns:
                col_id = cand
                break
        if col_id is None:
            return 0.0, {"error": "correlativa sin columna de ID conocida"}

        # Merge — usar inner join + sin validate estricto (algunos pipelines
        # pueden duplicar registros y queremos seguir evaluando)
        df = self.truth.merge(
            correlative[[col_id, "ID_GRUPO"]].rename(columns={col_id: "ID_REGISTRO"}),
            on="ID_REGISTRO",
            how="inner",
        )
        if len(df) == 0:
            return 0.0, {"error": "no hay solapamiento entre truth y correlativa"}

        try:
            m: PairwiseMetrics = evaluar_pares(
                df["ID_GROUP"].astype(str).values,
                df["ID_GRUPO"].astype(str).values,
            )
        except Exception as exc:
            return 0.0, {"error": f"evaluar_pares falló: {exc}"}

        metrics = {
            "precision": m.precision,
            "recall": m.recall,
            "f1": m.f1,
            "tp": m.tp,
            "fp": m.fp,
            "fn": m.fn,
            "n_records": m.n_records,
            "n_pred_groups": m.n_pred_groups,
        }
        # F2 score (favorece recall) — útil cuando los duplicados perdidos cuestan más
        beta2 = 4.0
        if m.precision + m.recall > 0:
            metrics["f2"] = (1 + beta2) * m.precision * m.recall / (beta2 * m.precision + m.recall)
        else:
            metrics["f2"] = 0.0

        score = metrics.get(optimization_target, m.f1)
        return float(score), metrics

    def _cleanup_workdirs(self) -> None:
        """Borra workdirs temporales acumulados."""
        import shutil

        for d in self._tmp_workdirs:
            try:
                if d.exists():
                    shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
        self._tmp_workdirs.clear()

    # ─────────────────────────────────────────────────────────────────────
    #  API pública
    # ─────────────────────────────────────────────────────────────────────
    def optimize(
        self,
        n_trials: int = 20,
        optimization_target: str = "f1",
        time_budget_minutes: float | None = None,
        time_penalty_seconds: float = 600.0,
        show_progress_bar: bool = True,
        sampler=None,
        pruner=None,
        seed: int | None = 42,
    ) -> dict[str, Any]:
        """Ejecuta el bucle de optimización.

        Args:
            n_trials: número máximo de trials.
            optimization_target: métrica a maximizar. Opciones: 'f1' (default),
                'f2' (favorece recall), 'precision', 'recall'.
            time_budget_minutes: presupuesto máximo en minutos (None = sin tope).
            time_penalty_seconds: si un trial tarda más, penaliza el score
                proporcionalmente. Default 600s (10 min). Set 0 para desactivar.
            show_progress_bar: barra de progreso Optuna.
            sampler: sampler de Optuna opcional (ej. TPESampler). Si se
                proporciona, tiene prioridad sobre ``seed``.
            pruner: pruner de Optuna opcional.
            seed: semilla del TPESampler creado por defecto. Default 42 para
                que el ejemplo documentado sea reproducible. Use None para el
                comportamiento aleatorio de Optuna.

        Returns:
            Dict con:
              - 'best_params': mejores hiperparámetros encontrados.
              - 'best_score': mejor score (de optimization_target).
              - 'best_config': config completo listo para Orchestrator.
              - 'best_metrics': métricas completas del mejor trial.
              - 'optimization_history': lista de dicts por trial.
              - 'study': objeto Optuna study (acceso completo).
              - 'total_trials_completed': cantidad de trials no-fallidos.
        """
        import optuna

        valid_targets = {"f1", "f2", "precision", "recall"}
        if optimization_target not in valid_targets:
            raise ValueError(
                f"optimization_target debe ser uno de {valid_targets}, "
                f"recibido: '{optimization_target}'"
            )

        self.logger.info(
            f"🎯 Iniciando optimización sobre Orchestrator: "
            f"n_trials={n_trials}, target={optimization_target}"
        )

        best_metrics_holder: dict[str, Any] = {}

        def objective(trial):
            params = self.search_space(trial)
            cfg = self._build_config_for_trial(params)

            correlative, elapsed = self._run_orchestrator(cfg)
            if correlative is None:
                # Run falló
                self.optimization_history.append(
                    {
                        "trial": trial.number,
                        "params": params,
                        "score": 0.0,
                        "elapsed_s": elapsed,
                        "status": "failed",
                        "metrics": {},
                    }
                )
                return 0.0

            score, metrics = self._evaluate(correlative, optimization_target)

            # Penalización por tiempo (opcional)
            if time_penalty_seconds > 0 and elapsed > time_penalty_seconds:
                penalty = min((elapsed - time_penalty_seconds) / 1000.0, 0.5)
                score = score * (1 - penalty)

            self.optimization_history.append(
                {
                    "trial": trial.number,
                    "params": params,
                    "score": float(score),
                    "elapsed_s": elapsed,
                    "status": "completed",
                    "metrics": metrics,
                }
            )

            # Guardar métricas completas del mejor trial visto hasta ahora
            if not best_metrics_holder or score > best_metrics_holder.get("score", -1):
                best_metrics_holder["score"] = score
                best_metrics_holder["metrics"] = metrics
                best_metrics_holder["params"] = params

            return float(score)

        # Crear estudio
        study_kwargs = {"direction": "maximize"}
        if sampler is None and seed is not None:
            sampler = optuna.samplers.TPESampler(seed=seed)
        if sampler is not None:
            study_kwargs["sampler"] = sampler
        if pruner is not None:
            study_kwargs["pruner"] = pruner
        study = optuna.create_study(**study_kwargs)

        timeout_s = time_budget_minutes * 60 if time_budget_minutes else None
        try:
            study.optimize(
                objective,
                n_trials=n_trials,
                timeout=timeout_s,
                show_progress_bar=show_progress_bar,
            )
        finally:
            self._cleanup_workdirs()

        # Construir best_config
        completed_trials = [h for h in self.optimization_history if h["status"] == "completed"]
        if not completed_trials:
            self.logger.warning("Ningún trial completó exitosamente.")
            return {
                "best_params": None,
                "best_score": 0.0,
                "best_config": None,
                "best_metrics": {},
                "optimization_history": self.optimization_history,
                "study": study,
                "total_trials_completed": 0,
            }

        self.best_params = best_metrics_holder.get("params") or study.best_params
        self.best_config = self._build_config_for_trial(self.best_params)
        best_metrics = best_metrics_holder.get("metrics", {})

        self.logger.info(
            f"✅ Optimización completa. Mejor {optimization_target}: "
            f"{study.best_value:.4f} (trial #{study.best_trial.number}). "
            f"Trials completados: {len(completed_trials)}/{n_trials}"
        )

        return {
            "best_params": self.best_params,
            "best_score": study.best_value,
            "best_config": self.best_config,
            "best_metrics": best_metrics,
            "optimization_history": self.optimization_history,
            "study": study,
            "total_trials_completed": len(completed_trials),
        }

    def history_df(self) -> pd.DataFrame:
        """Devuelve el historial de optimización como DataFrame plano."""
        if not self.optimization_history:
            return pd.DataFrame()
        rows = []
        for h in self.optimization_history:
            row = {
                "trial": h["trial"],
                "score": h["score"],
                "elapsed_s": h["elapsed_s"],
                "status": h["status"],
            }
            row.update({f"param_{k}": v for k, v in h["params"].items()})
            row.update({f"metric_{k}": v for k, v in h["metrics"].items()})
            rows.append(row)
        return pd.DataFrame(rows)
