"""
optimization.engine — record_linkage_pipeline

Componentes:
    - class OptimizationEngine  (origen: notebook celda [162])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import pickle
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..exporters._spreadsheet import prepare_spreadsheet_data
from ..utils.memory import get_process_rss_bytes

# Imports diferidos para evitar ciclo con evaluation.ground_truth.
# Las clases GroundTruthEvaluator y EntityMetricsEvaluator se importan dentro
# de los métodos que las usan. RecordLinkagePipeline igual.
from .parameters import ParameterSpace


# ────────────────────────────────────────────────────────────────────
# silent_run: stub local. La versión completa estaba en celda 159 del
# notebook fuente y no se migró por ser experimental. Si una función
# de optimización lo invoca, ejecuta la callable sin suprimir output.
# ────────────────────────────────────────────────────────────────────
def silent_run(func, *args, **kwargs):
    """Stub local: ejecuta func sin suprimir output (versión simplificada)."""
    return func(*args, **kwargs)


class OptimizationEngine:
    """
    Motor principal que gestiona la optimización de parámetros.

    Esta clase coordina la ejecución del pipeline con diferentes configuraciones,
    evalúa los resultados y gestiona el estado de la optimización.
    """

    def __init__(
        self,
        ground_truth_df: pd.DataFrame,
        parameter_space: ParameterSpace,
        output_dir: str = "optimization_results",
        max_time_per_trial: int = 600,  # 10 minutos por defecto
        enable_checkpoints: bool = True,
    ):
        """
        Inicializa el motor de optimización.

        Args:
            ground_truth_df: DataFrame con los datos de verdad absoluta
            parameter_space: Espacio de parámetros a optimizar
            output_dir: Directorio para guardar resultados
            max_time_per_trial: Tiempo máximo en segundos por trial
            enable_checkpoints: Si guardar checkpoints automáticamente
        """
        self.ground_truth_df = ground_truth_df
        self.parameter_space = parameter_space
        self.output_dir = Path(output_dir)
        # ⚠️ CAMBIO CRÍTICO: Agregar parents=True
        self.output_dir.mkdir(exist_ok=True, parents=True)
        self.max_time_per_trial = max_time_per_trial
        self.enable_checkpoints = enable_checkpoints

        # Estado interno
        self.trial_results = []
        self.best_config = None
        self.best_score = -float("inf")
        self.start_time = None

        # Configurar logging
        self.logger = logging.getLogger("OptimizationEngine")

    @contextmanager
    def _trial_context(self, trial_number: int):
        """Contexto para gestionar recursos durante un trial."""
        trial_start = time.time()
        initial_memory = get_process_rss_bytes() / 1024**3

        try:
            yield
        finally:
            # Limpiar recursos
            gc.collect()

            # Registrar métricas
            trial_time = time.time() - trial_start
            final_memory = get_process_rss_bytes() / 1024**3
            memory_used = final_memory - initial_memory

            self.logger.info(
                f"Trial {trial_number} completado en {trial_time:.1f}s, "
                f"memoria utilizada: {memory_used:.1f}GB"
            )

    def save_checkpoint(self, study, trial_number: int):
        """Guarda un checkpoint del estado actual."""
        if not self.enable_checkpoints:
            return

        checkpoint_path = self.output_dir / f"checkpoint_trial_{trial_number}.pkl"
        checkpoint_data = {
            "trial_number": trial_number,
            "best_config": self.best_config,
            "best_score": self.best_score,
            "trial_results": self.trial_results,
            "study": study,
            "timestamp": datetime.now(),
        }

        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=".checkpoint_", suffix=".tmp", dir=self.output_dir, delete=False
            ) as f:
                temp_path = f.name
                pickle.dump(checkpoint_data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, checkpoint_path)
        finally:
            if temp_path is not None:
                Path(temp_path).unlink(missing_ok=True)

        self.logger.info(f"Checkpoint guardado: {checkpoint_path}")

    def load_checkpoint(self, checkpoint_path: str, *, trusted: bool = False):
        """Carga un checkpoint pickle creado localmente y marcado como confiable.

        Pickle puede ejecutar código al deserializar. Por eso nunca se abre un
        checkpoint descargado o compartido sin una decisión explícita del
        caller. Los checkpoints propios se reanudan con ``trusted=True``.
        """
        if not trusted:
            raise ValueError(
                "Checkpoint no cargado: pickle requiere trusted=True. "
                "Use solo archivos creados localmente por esta misma ejecución."
            )
        with open(checkpoint_path, "rb") as f:
            # Unsafe format is reachable only through the explicit trust gate above.
            checkpoint_data = pickle.load(f)  # nosec B301

        if not isinstance(checkpoint_data, dict):
            raise ValueError("Checkpoint inválido: se esperaba un diccionario")
        required = {"trial_number", "best_config", "best_score", "trial_results"}
        missing = required.difference(checkpoint_data)
        if missing:
            raise ValueError(f"Checkpoint inválido; faltan claves: {sorted(missing)}")

        self.best_config = checkpoint_data["best_config"]
        self.best_score = checkpoint_data["best_score"]
        self.trial_results = checkpoint_data["trial_results"]

        self.logger.info(f"Checkpoint cargado: {checkpoint_data['trial_number']} trials")
        return checkpoint_data.get("study")

    def get_trial_summary(self) -> pd.DataFrame:
        """Genera un resumen de todos los trials ejecutados."""
        if not self.trial_results:
            return pd.DataFrame()

        df = pd.DataFrame(self.trial_results)
        df = df.sort_values("f1_score", ascending=False)

        # Añadir ranking
        df["rank"] = range(1, len(df) + 1)

        return df

    def export_results(self):
        """Exporta todos los resultados de la optimización."""
        # Crear directorio de exportación
        export_dir = self.output_dir / f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        export_dir.mkdir(exist_ok=True)

        # 1. Resumen de trials
        summary_df = self.get_trial_summary()
        prepare_spreadsheet_data(summary_df).to_csv(export_dir / "trials_summary.csv", index=False)

        # 2. Mejor configuración
        if self.best_config:
            with open(export_dir / "best_config.json", "w") as f:
                json.dump(self.best_config, f, indent=2)

        # 3. Espacio de parámetros usado
        self.parameter_space.save(str(export_dir / "parameter_space.json"))

        # 4. Reporte en Excel
        with pd.ExcelWriter(export_dir / "optimization_report.xlsx") as writer:
            # Hoja de resumen
            prepare_spreadsheet_data(summary_df).to_excel(
                writer, sheet_name="Trials Summary", index=False
            )

            # Hoja de mejor configuración
            if self.best_config:
                best_df = pd.DataFrame([self.best_config])
                prepare_spreadsheet_data(best_df).to_excel(
                    writer, sheet_name="Best Config", index=False
                )

            # Hoja de métricas
            if self.trial_results:
                metrics_df = pd.DataFrame(self.trial_results)
                prepare_spreadsheet_data(metrics_df).to_excel(
                    writer, sheet_name="All Metrics", index=False
                )

    def evaluate_single_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Versión CORREGIDA y LISTA PARA PRODUCCIÓN.
        - Mantiene las métricas de entidad y de pares del archivo Original.txt.
        - Añade liberación explícita de memoria, limpieza de archivos temporales y recolección de basura,
          según las recomendaciones de Sugerencias de mejora.txt.
        """
        import gc
        import shutil
        import time
        from pathlib import Path

        # ------------------------------------------------------------
        # 1. Preparar configuración del pipeline (sin cambios funcionales)
        # ------------------------------------------------------------
        pipeline_config = self.parameter_space.to_pipeline_config(params)
        config = {
            "profile": "optimization_profile",
            "profiles": {"optimization_profile": pipeline_config},
            "output_directory": str(self.output_dir / f"trial_{int(time.time() * 1000)}"),
            "cross_source_only": False,
            "trusted_unique_sources": [],  # ✅ Paso 1.6 (vacío para Optuna con fuente única)
            "generate_visualizations": False,
        }

        # [DIAG-1] Verificar config que entra al pipeline (Fase 3)
        if os.environ.get("OPTUNA_DIAGNOSTICS"):
            _dp = config["profiles"]["optimization_profile"]
            print(
                f"[DIAG-1] score_threshold={_dp.get('score_threshold')}, "
                f"lsh_threshold={_dp.get('lsh_threshold')}, "
                f"min_name_sim={_dp.get('min_name_similarity')}, "
                f"cleaning={_dp.get('cleaning_mode')}, "
                f"remove_top={_dp.get('remove_top_words')}"
            )

        # Asegurar que el ground truth tenga un índice único para el cruce
        gt_df_with_index = self.ground_truth_df.reset_index().rename(
            columns={"index": "ORIGINAL_INDEX"}
        )

        pipeline = None  # Para poder limpiarlo en finally
        result = None
        df_merged = None
        tabla_correlativa = None
        entity_metrics = None
        pair_metrics = None
        evaluator_entidad = None
        evaluator_pares = None
        final_metrics: dict[str, Any] = {}

        try:
            # ------------------------------------------------------------
            # 2. Ejecutar el pipeline en modo silencioso (nueva instancia SIEMPRE)
            # ------------------------------------------------------------
            # Imports diferidos para romper ciclo evaluation <-> optimization.
            from ..evaluation.ground_truth import GroundTruthEvaluator
            from ..evaluation.metrics import EntityMetricsEvaluator
            from ..pipeline.linkage_pipeline import RecordLinkagePipeline

            with silent_run():
                pipeline = RecordLinkagePipeline(config, profile="optimization_profile")
                # (v2.0.1) pipeline.run() retorna PipelineResult con carga lazy.
                # `force_rerun=True` se mantiene porque Optuna necesita
                # corridas independientes (cada trial cambia hiperparámetros);
                # los checkpoints de un trial no son válidos para el siguiente.
                result = pipeline.run(
                    sources={"GROUND_TRUTH": gt_df_with_index},
                    show_progress=False,
                    force_rerun=True,
                )

            # ------------------------------------------------------------
            # 3. CRUCE CRÍTICO: Unir resultados con la verdad
            # ------------------------------------------------------------
            tabla_correlativa = result.get("correlative_table")
            if tabla_correlativa is None:
                raise ValueError("El pipeline no retornó 'correlative_table'.")

            # NOTA: El ground truth usa ID_GRUPO_ESPERADO, no NIT_FINAL.
            #    NIT_FINAL es una columna creada por el pipeline, no existe en el input
            df_merged = pd.merge(
                tabla_correlativa,
                gt_df_with_index[["ORIGINAL_INDEX", "ID_GRUPO_ESPERADO"]].rename(
                    columns={"ID_GRUPO_ESPERADO": "NIT_FINAL_truth"}
                ),
                on="ORIGINAL_INDEX",
                how="left",
            )

            # ------------------------------------------------------------
            # 4. Evaluar métricas de ENTIDAD
            # ------------------------------------------------------------
            evaluator_entidad = EntityMetricsEvaluator()
            entity_metrics = evaluator_entidad.calculate_metrics(df_merged)

            # ------------------------------------------------------------
            # 5. Evaluar métricas de PARES
            # ------------------------------------------------------------
            evaluator_pares = GroundTruthEvaluator()
            pair_metrics = evaluator_pares.evaluate(
                predicted_df=tabla_correlativa,
                ground_truth_df=gt_df_with_index,
                group_col="ID_GRUPO",
                truth_col="ID_GRUPO_ESPERADO",  # ✅ FIX BUG 4 (Fase 3)
            )

            # ------------------------------------------------------------
            # 6. Combinar todas las métricas
            # ------------------------------------------------------------
            final_metrics = {
                # Métricas de entidad
                **entity_metrics["porcentajes"],
                **{f"count_{k}": v for k, v in entity_metrics["counts"].items()},
                # Métricas de pares
                "precision": pair_metrics.get("precision", 0.0),
                "recall": pair_metrics.get("recall", 0.0),
                "f1_score": pair_metrics.get("f1_score", 0.0),
                "true_positives": pair_metrics.get("true_positives", 0),
                "false_positives": pair_metrics.get("false_positives", 0),
                "false_negatives": pair_metrics.get("false_negatives", 0),
            }

        except Exception as e:
            # ------------------------------------------------------------
            # Manejo robusto de errores (como en el original)
            # ------------------------------------------------------------
            self.logger.error(f"Error evaluando configuración: {e}", exc_info=True)
            final_metrics = {
                "perfect_entities_pct": 0.0,
                "contaminated_entities_pct": 1.0,
                "fragmented_entities_pct": 1.0,
                "precision": 0.0,
                "recall": 0.0,
                "f1_score": 0.0,
                "error": str(e),
            }

        finally:
            # ------------------------------------------------------------
            # 7. LIMPIEZA DE MEMORIA Y RECURSOS (Fase 3: mejorada)
            # ------------------------------------------------------------
            out_dir = Path(config["output_directory"])

            # Eliminar objetos grandes de la memoria
            if result is not None:
                keys_to_del = [
                    "df_linked",
                    "correlative_table",
                    "golden_records",
                    "df_preprocessed",
                ]
                for key in keys_to_del:
                    try:
                        if key in result:
                            del result[key]
                    except (KeyError, TypeError, AttributeError):
                        pass

            # Soltar referencias locales explícitamente: es seguro, auditable
            # y no interpreta nombres de variables mediante exec().
            result = None
            df_merged = None
            tabla_correlativa = None
            entity_metrics = None
            pair_metrics = None
            evaluator_entidad = None
            evaluator_pares = None

            # Limpieza específica del pipeline (si existe el método)
            if pipeline and hasattr(pipeline, "_cleanup_resources"):
                try:
                    pipeline._cleanup_resources()
                except Exception:
                    # No bloquear la limpieza si ese método falla
                    pass

            pipeline = None

            # Forzar recolección de basura
            gc.collect()

            # Eliminar directorio temporal de salida
            if out_dir.exists():
                shutil.rmtree(out_dir, ignore_errors=True)

        return final_metrics
