"""
pipeline.linkage_pipeline — record_linkage_pipeline

Componentes:
    - class RecordLinkagePipeline  (origen: notebook celda [142])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.

DEPRECADO (F2.9): ``RecordLinkagePipeline`` avisa con ``DeprecationWarning``
al construirse. El ``Orchestrator`` ya no lo necesita (sus componentes de
preparación salen de ``pipeline.componentes``), y los scripts y pruebas que
lo ejecutaban directamente pasan por ``deduplication.deduplicate_unified``
con ``AjustesDeduplicacion``. Se retira en F5, cuando ``dedupe()`` deje de
usarlo como motor; hasta entonces ``deduplicate_unified`` lo construye con
``_uso_interno=True`` para no avisar a quien no puede hacer nada al respecto.
"""

from __future__ import annotations

import gc
import os
import time
import warnings
from pathlib import Path
from typing import Any

import pandas as pd

from ..engine.linkage import RecordLinkageEngine
from ..engine.lsh.disk_based import DiskBasedLSHEngine
from ..engine.similarity import BasicSimilarityCalculator, SimilarityCalculator
from ..exporters.smart import SmartExporter
from ..golden.containment import consolidate_groups_by_nit_balanced
from ..golden.generator import GoldenRecordGeneratorV7
from ..processing.validator import DataValidator
from ..reporting.reports import ReportGenerator
from ..utils.logger import CustomLogger
from ..utils.memory import MemoryManager
from ..utils.output import safe_print as print
from ..utils.performance import track_performance

# v0.7.2 (Sprint 0.8.2, Tarea 2.4): imports de dashboard/suite/visualizer
# se hacen LAZY dentro de _generate_reports/run para evitar cargar
# matplotlib/seaborn (~500 MB) cuando el pipeline corre con skip_reporting=True
# o cuando alguien solo necesita importar las clases del pipeline.
# La disponibilidad se chequea ahora con _class_exists() en lugar de globals().
from ._internal import DEFAULT_CONFIG, PROFILES, _class_exists as _class_is_importable
from .componentes import fabricar_componentes
from .errores import ErrorPipeline


class RecordLinkagePipeline:
    """
    Pipeline principal que orquesta todo el proceso de record linkage.

    Versión 2.0 - Características:
    - Sistema de checkpointing para reanudar procesos interrumpidos
    - Gestión eficiente de memoria con liberación opcional de DataFrames intermedios
    - Integración completa con todos los componentes del sistema
    - Manejo robusto de errores y recursos
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        profile: str = "standard",
        *,
        _uso_interno: bool = False,
    ):
        """
        Constructor que inicializa el pipeline con configuración y perfil.

        DEPRECADO (F2.9): avisa con ``DeprecationWarning``. Use
        ``deduplication.deduplicate_unified`` (con ``AjustesDeduplicacion`` si
        necesita forzar el motor o una perilla del perfil) o ``api.linkage``.

        Args:
            config: Diccionario de configuración personalizada
            profile: Perfil de configuración ('standard', 'high_precision', 'high_recall', etc.)
            _uso_interno: solo para ``deduplicate_unified``, que lo usa como
                motor hasta F5: con ``True`` no se emite el aviso.
        """
        if not _uso_interno:
            warnings.warn(
                "RecordLinkagePipeline está deprecado (F2.9) y se retira en F5: use "
                "record_linkage.deduplication.deduplicate_unified(..., ajustes=AjustesDeduplicacion(...)) "
                "o record_linkage.linkage().",
                DeprecationWarning,
                stacklevel=2,
            )
        self.config = config or DEFAULT_CONFIG.copy()
        self.config["profile"] = profile
        self.profile_name = profile
        self.logger = CustomLogger("RecordLinkagePipeline")

        # Inicialización directa de componentes (sin lazy loading para depuración).
        # F2.9: la regla del cleaning_mode (perfil > nivel superior > BALANCEADO)
        # vive en pipeline.componentes, compartida con el Orchestrator.
        _componentes = fabricar_componentes(self.config, profile)
        self._data_handler = _componentes.data_handler
        self._text_processor = _componentes.text_processor
        self._nit_processor = _componentes.nit_processor
        self._validator = DataValidator(self.config)

        # El motor sí mantiene lazy loading porque su implementación puede cambiar
        self._engine = None

        # Contenedores de resultados y estado
        self.results = {}
        self.is_fitted = False
        self.performance_metrics = {
            "start_time": None,
            "end_time": None,
            "phase_times": {},
            "memory_usage": {},
        }

        self.logger.info(
            f"Pipeline inicializado con perfil '{profile}' (modo producción con checkpointing)"
        )

    def _validate_configuration(self):
        """Validar que la configuración sea coherente."""
        # Verificar configuración del motor
        if self.config.get("linkage_engine_class") == "disk_based":
            # Asegurar que el directorio de almacenamiento existe
            storage_dir = self.config.get("lsh_storage_dir", "lsh_temp")
            os.makedirs(storage_dir, exist_ok=True)

            # Verificar que hay suficiente espacio en disco
            import shutil

            stat = shutil.disk_usage(storage_dir)
            free_gb = stat.free / (1024**3)

            if free_gb < 5:
                self.logger.warning(
                    f"Poco espacio en disco: {free_gb:.1f}GB libres. "
                    "El procesamiento en disco podría fallar."
                )

        # Validar perfil
        profile_name = self.config.get("profile")
        if profile_name and profile_name not in self.config.get("profiles", {}):
            raise ValueError(f"Perfil '{profile_name}' no encontrado en la configuración")

    # Propiedades de los componentes (F2.9: los crea la fábrica en __init__;
    # el «lazy loading» que había aquí nunca se ejercía y aplicaba una regla
    # de cleaning_mode distinta a la del constructor).
    @property
    def data_handler(self):
        return self._data_handler

    @property
    def text_processor(self):
        return self._text_processor

    @property
    def nit_processor(self):
        return self._nit_processor

    @property
    def validator(self):
        if self._validator is None:
            self._validator = DataValidator(self.config)
        return self._validator

    @property
    def engine(self):
        """
        Motor inteligente que selecciona la implementación de LSH correcta (en memoria o disco)
        basándose en la configuración del pipeline.
        """
        if self._engine is None:
            engine_type = self.config.get("linkage_engine_class", "default")
            profile_name = self.config.get("profile", "standard")

            # Obtener la configuración del perfil específico
            profile_config = self.config.get("profiles", {}).get(
                profile_name, PROFILES.get(profile_name, {})
            )

            self.logger.info(f"Seleccionando motor de linkage basado en config: '{engine_type}'")
            self._engine = RecordLinkageEngine(profile_name, self.config)

            # Sobreescribir el buscador de candidatos si es necesario
            if engine_type == "disk_based":
                self.logger.info(
                    "🔄 Usando motor LSH EFICIENTE con almacenamiento en disco (DiskBasedLSHEngine)."
                )
                self._engine._candidate_finder = DiskBasedLSHEngine(profile_config, self.config)
            else:
                self.logger.info("💾 Usando motor LSH estándar en memoria (OptimizedLSHEngine).")

        return self._engine

    @track_performance("Pipeline completo")
    def run(
        self,
        sources: dict[str, str | pd.DataFrame],
        output_dir: str | None = None,
        source_priority: list[str] | None = None,
        column_mapping: dict[str, dict[str, str]] | None = None,
        reports: list[str] | None = None,
        show_progress: bool = True,
        validate_data: bool = True,
        generate_visualizations: bool = True,
        keep_intermediate_results: bool = False,
        force_rerun: bool = True,
        _internal_optimization_run: bool = False,
    ) -> dict[str, Any]:
        """
        Ejecutar el pipeline completo de record linkage con sistema de checkpointing.

        Args:
            sources: Diccionario {nombre_fuente: ruta_archivo o DataFrame}
            output_dir: Directorio para guardar resultados
            source_priority: Lista ordenada de prioridad de fuentes
            column_mapping: Mapeo de columnas por fuente
            reports: Lista de reportes a generar
            show_progress: Mostrar barra de progreso
            validate_data: Ejecutar validación de calidad
            generate_visualizations: Generar gráficos
            keep_intermediate_results: Si True, mantiene DataFrames intermedios en memoria
            force_rerun: Si True (default seguro), ignora checkpoints y re-ejecuta
                todo el pipeline. Esta implementación heredada no guarda huellas
                verificables; pase False únicamente si confía explícitamente en que
                los checkpoints pertenecen a los mismos datos/configuración/código.
            _internal_optimization_run: Flag interno para optimización

        Returns:
            Diccionario con todos los resultados del proceso
        """
        # Forzar recreación del motor en cada ejecución
        self._engine = None

        self.performance_metrics["start_time"] = time.time()
        self.keep_intermediate_results = keep_intermediate_results

        if force_rerun:
            self.logger.warning(
                "MODO FORZADO: Se ignorarán todos los checkpoints y se re-ejecutará el pipeline completo."
            )
        else:
            self.logger.warning(
                "REUTILIZACIÓN LEGACY SIN HUELLA: force_rerun=False solo es segura "
                "si los checkpoints fueron creados con exactamente los mismos "
                "datos, configuración y código. Para reanudación verificada use Orchestrator."
            )

        self.logger.info(
            f"Modo de conservación de resultados intermedios: {'ACTIVADO' if self.keep_intermediate_results else 'DESACTIVADO'}"
        )

        # Configurar directorio de salida
        if output_dir:
            self.config["output_directory"] = output_dir
        output_dir = self.config.get("output_directory", "resultados_linkage")
        os.makedirs(output_dir, exist_ok=True)

        # Configurar directorio de checkpoints
        intermediate_dir = os.path.join(output_dir, "intermediate_checkpoints")
        os.makedirs(intermediate_dir, exist_ok=True)

        # Definir rutas de checkpoints.
        # Convención: checkpoint_NN_NOMBRE.parquet, donde NN es el orden de la fase.
        # Los checkpoints "_final" son los outputs CANÓNICOS post-consolidación
        # (fase 4.5) y deben ser la fuente de verdad para callers externos.
        checkpoints = {
            "consolidated": os.path.join(intermediate_dir, "checkpoint_01_consolidated.parquet"),
            "preprocessed": os.path.join(intermediate_dir, "checkpoint_02_preprocessed.parquet"),
            "linked": os.path.join(intermediate_dir, "checkpoint_03_linked.parquet"),
            "golden": os.path.join(intermediate_dir, "checkpoint_04_golden.parquet"),
            "correlative": os.path.join(intermediate_dir, "checkpoint_04_correlative.parquet"),
            "consolidated_final": os.path.join(
                intermediate_dir, "checkpoint_05_correlative_final.parquet"
            ),
            # (v2.0.1) Checkpoint del golden post-consolidación, escrito por fase 4.5.
            # PipelineResult lo expone como `golden_records` para garantizar que
            # los callers reciban el resultado final, no el pre-consolidación.
            "golden_final": os.path.join(intermediate_dir, "checkpoint_05_golden_final.parquet"),
        }

        # Configurar prioridad de fuentes
        if source_priority is None:
            source_priority = list(sources.keys())

        # Configurar reportes por defecto
        if reports is None:
            reports = ["summary", "quality", "coverage"]

        try:
            # FASE 1: Carga y Validación
            self._phase1_load_and_validate(
                sources, column_mapping, validate_data, checkpoints["consolidated"], force_rerun
            )

            # FASE 2: Preprocesamiento
            self._phase2_preprocessing(checkpoints["preprocessed"], force_rerun)

            # FASE 3: Record Linkage
            self._phase3_linkage(checkpoints["linked"], force_rerun)

            # FASE 4: Golden Records
            self._phase4_golden_records(
                source_priority, checkpoints["golden"], checkpoints["correlative"], force_rerun
            )

            # FASE 4.5: Consolidación de Grupos por NIT Idéntico
            self._phase4_5_consolidation(
                source_priority,
                checkpoints["consolidated_final"],
                checkpoints["golden_final"],
                force_rerun,
            )

            # FASE 5: Reportes y Exportación
            self._phase5_reports_and_export(reports, generate_visualizations)

            # Finalizar
            self._finalize_pipeline()

            # Mostrar resumen si está habilitado
            if show_progress:
                self._print_summary()

            self.is_fitted = True

            # ──────────────────────────────────────────────────────────
            # F1.4 (v2.0.1): Construir PipelineResult con carga lazy.
            #
            # Las rutas apuntan a los checkpoints parquet ya escritos por
            # las fases 3 y 4. El PipelineResult los lee bajo demanda,
            # eliminando la dependencia de `keep_intermediate_results` que
            # producía el bug #4 (RuntimeError "El pipeline no generó la
            # tabla correlativa").
            #
            # Retornar la propia instancia (PipelineResult) en vez del dict
            # plano `self.results`. PipelineResult implementa __getitem__,
            # get(), __contains__, keys(), por lo que mantiene compat con
            # callers que accedían como dict.
            # ──────────────────────────────────────────────────────────
            from .result import PipelineResult

            # Cualquier entrada residual del dict que no esté mapeada
            # como propiedad (load_report, validation_reports, etc.)
            # se preserva en `extra` para compat hacia atrás.
            _RESERVED_KEYS = {
                "golden_records",
                "correlative_table",
                "df_linked",
                "df_consolidated",
                "df_preprocessed",
                "metrics",
            }
            extra = {k: v for k, v in self.results.items() if k not in _RESERVED_KEYS}
            # Si tras la limpieza de memoria los DataFrames siguen en
            # self.results (caso keep_intermediate_results=True), también
            # se exponen via PipelineResult — pero los checkpoints parquet
            # son la fuente canónica.
            if "metrics" not in self.results:
                self.results["metrics"] = {}

            return PipelineResult(
                work_dir=Path(self.config.get("output_directory", ".")),
                metrics=self.results.get("metrics", {}),
                extra=extra,
                # Apuntar a los outputs POST-consolidación (fase 4.5), que son
                # los canónicos para callers externos. Los checkpoints
                # pre-consolidación (checkpoint_04_*) son intermedios.
                golden_path=Path(checkpoints["golden_final"]),
                correlative_path=Path(checkpoints["consolidated_final"]),
                linked_path=Path(checkpoints["linked"]),
            )

        except Exception as e:
            self.logger.error(f"Error en pipeline: {e!s}", exc_info=True)

            # Guardar resultados parciales si es posible
            self._save_partial_results()

            raise

        finally:
            # Limpiar recursos
            self._cleanup_resources()

    @track_performance("Fase 1: Carga y Validación")
    def _phase1_load_and_validate(
        self,
        sources: dict[str, str | pd.DataFrame],
        column_mapping: dict[str, dict[str, str]] | None,
        validate_data: bool,
        checkpoint_path: str,
        force_rerun: bool,
    ):
        """Fase 1: Cargar fuentes y validar calidad con checkpointing."""
        self.logger.info("=" * 60)
        self.logger.info("FASE 1: CARGA Y VALIDACIÓN DE DATOS")
        self.logger.info("=" * 60)

        # Verificar checkpoint
        if not force_rerun and os.path.exists(checkpoint_path):
            self.logger.info(f"✅ Cargando datos consolidados desde checkpoint: {checkpoint_path}")
            self.results["df_consolidated"] = pd.read_parquet(checkpoint_path)
            self.logger.info(
                f"Registros cargados desde checkpoint: {len(self.results['df_consolidated']):,}"
            )
            return

        # Cargar fuentes
        loaded_sources, load_report = self.data_handler.load_sources(sources, column_mapping)
        self.results["load_report"] = load_report
        self.results["loaded_sources"] = loaded_sources

        # Validar cada fuente si está habilitado
        if validate_data:
            validation_reports = {}
            for source_name, df in loaded_sources.items():
                validation_report = self.validator.validate_data_quality(df, source_name)
                validation_reports[source_name] = validation_report

                # Log warnings si hay problemas
                if validation_report["quality_score"] < 0.5:
                    self.logger.warning(
                        f"{source_name}: Calidad baja ({validation_report['quality_score']:.2%})"
                    )

            self.results["validation_reports"] = validation_reports

        # Consolidar fuentes
        df_consolidated = self.data_handler.consolidate_sources(loaded_sources)
        self.results["df_consolidated"] = df_consolidated

        # Guardar checkpoint
        df_consolidated.to_parquet(checkpoint_path)
        self.logger.info(f"💾 Checkpoint de la Fase 1 guardado en: {checkpoint_path}")
        self.logger.info(f"Total registros consolidados: {len(df_consolidated):,}")

        # Guardar métricas
        self.performance_metrics["phase_times"]["load_validate"] = (
            time.time() - self.performance_metrics["start_time"]
        )
        self.performance_metrics["memory_usage"]["after_load"] = MemoryManager.get_memory_status()

    @track_performance("Fase 2: Preprocesamiento")
    def _phase2_preprocessing(self, checkpoint_path: str, force_rerun: bool):
        """Fase 2: Preprocesamiento de datos con checkpointing."""
        self.logger.info("=" * 60)
        self.logger.info("FASE 2: PREPROCESAMIENTO DE DATOS")
        self.logger.info("=" * 60)

        # Verificar checkpoint
        if not force_rerun and os.path.exists(checkpoint_path):
            self.logger.info(f"✅ Cargando datos preprocesados desde checkpoint: {checkpoint_path}")
            self.results["df_preprocessed"] = pd.read_parquet(checkpoint_path)

            # Liberar memoria si no se requieren resultados intermedios
            if not self.keep_intermediate_results and "df_consolidated" in self.results:
                del self.results["df_consolidated"]
                gc.collect()
                self.logger.debug("Liberada memoria de df_consolidated")

            return

        phase_start = time.time()
        df = self.results["df_consolidated"]

        # Procesar nombres
        self.logger.info("Limpiando nombres de empresas...")
        df["NOMBRE_LIMPIO"] = self.text_processor.process_series(
            df["RAZON_SOCIAL"], column_name="RAZON_SOCIAL"
        )

        # Procesar NITs
        self.logger.info("Procesando NITs...")
        nit_results = self.nit_processor.process_series(df["NIT"])
        df["NIT_BASE"] = nit_results["NIT_BASE"]
        df["NIT_OK"] = nit_results["NIT_OK"]
        df["NIT_VALID"] = nit_results["IS_VALID"]

        # Generar claves fonéticas
        if "SimilarityCalculator" in globals():
            sim_calc = SimilarityCalculator()
        elif "BasicSimilarityCalculator" in globals():
            sim_calc = BasicSimilarityCalculator()
            self.logger.warning("Usando SimilarityCalculator básico")
        else:
            self.logger.warning("SimilarityCalculator no disponible, omitiendo claves fonéticas")
            df["PHONETIC_KEY1"] = ""
            df["PHONETIC_KEY2"] = ""
            self.results["df_preprocessed"] = df
            return

        phonetic_keys = df["NOMBRE_LIMPIO"].apply(sim_calc.phonetic_keys)
        df["PHONETIC_KEY1"] = phonetic_keys.str[0]
        df["PHONETIC_KEY2"] = phonetic_keys.str[1]

        # ✅ FIX BUG 2 (Fase 3): Leer remove_top_words del config del pipeline
        #    (que contiene los parámetros de Optuna), no del PROFILES global
        _prof_words = self.config.get("profiles", {}).get(self.profile_name, {})
        if not _prof_words:
            _prof_words = PROFILES.get(self.profile_name, PROFILES.get("standard", {}))
        remove_top_words = _prof_words.get("remove_top_words", 0)

        if remove_top_words > 0:
            self.logger.info(f"Removiendo top {remove_top_words} palabras comunes...")
            df["NOMBRE_LIMPIO"] = self.text_processor.remove_common_words(
                df["NOMBRE_LIMPIO"], threshold=remove_top_words
            )

        # Guardar resultado
        self.results["df_preprocessed"] = df

        # Guardar checkpoint
        df.to_parquet(checkpoint_path)
        self.logger.info(f"💾 Checkpoint de la Fase 2 guardado en: {checkpoint_path}")

        # Liberar memoria si no se requieren resultados intermedios
        if not self.keep_intermediate_results and "df_consolidated" in self.results:
            del self.results["df_consolidated"]
            gc.collect()
            self.logger.debug("Liberada memoria de df_consolidated")

        # Métricas
        self.performance_metrics["phase_times"]["preprocessing"] = time.time() - phase_start
        self.performance_metrics["memory_usage"]["after_preprocessing"] = (
            MemoryManager.get_memory_status()
        )

        self.logger.info(f"Preprocesamiento completado: {len(df):,} registros")

    @track_performance("Fase 3: Record Linkage")
    def _phase3_linkage(self, checkpoint_path: str, force_rerun: bool):
        """Fase 3: Ejecutar motor de linkage con checkpointing."""
        self.logger.info("=" * 60)
        self.logger.info("FASE 3: RECORD LINKAGE")
        self.logger.info("=" * 60)

        # Verificar checkpoint
        if not force_rerun and os.path.exists(checkpoint_path):
            self.logger.info(
                f"✅ Cargando resultados de linkage desde checkpoint: {checkpoint_path}"
            )
            self.results["df_linked"] = pd.read_parquet(checkpoint_path)

            # Recalcular métricas desde el checkpoint
            df_linked = self.results["df_linked"]
            unique_groups_count = df_linked["ID_GRUPO"].nunique()
            total_records_count = len(df_linked)

            self.results["metrics"] = {
                "total_records": total_records_count,
                "unique_groups": unique_groups_count,
                "linkage_rate": (
                    (total_records_count - unique_groups_count) / total_records_count
                    if total_records_count > 0
                    else 0
                ),
                "reduction_rate": (
                    1 - (unique_groups_count / total_records_count)
                    if total_records_count > 0
                    else 0
                ),
                "multi_source_groups": self._count_multi_source_groups(df_linked),
            }

            # Liberar memoria si no se requieren resultados intermedios
            if not self.keep_intermediate_results and "df_preprocessed" in self.results:
                del self.results["df_preprocessed"]
                gc.collect()
                self.logger.debug("Liberada memoria de df_preprocessed")

            return

        phase_start = time.time()
        df = self.results["df_preprocessed"]

        # Determinar directorio de salida
        output_dir_for_run = self.config.get("output_directory")
        if not output_dir_for_run:
            self.logger.error(
                "⚠️  No se ha definido 'output_directory' en la configuración. "
                "Se utilizará 'resultados_linkage_default'."
            )
            output_dir_for_run = "resultados_linkage_default"
        os.makedirs(output_dir_for_run, exist_ok=True)

        # Obtener configuración del perfil activo
        profile_config = self.config.get("profiles", {}).get(self.profile_name, {})

        # Detectar modo (deduplicación o linkage) y determinar cross_source_flag
        is_deduplication_mode = df["SRC"].nunique() == 1
        cross_source_flag = profile_config.get("cross_source_only", not is_deduplication_mode)
        trusted_sources = profile_config.get("trusted_unique_sources", [])

        if is_deduplication_mode:
            self.logger.info("MODO DETECTADO: Deduplicación. `cross_source_only` forzado a False.")
            cross_source_flag = False
        elif trusted_sources:
            self.logger.info(
                f"MODO DETECTADO: Linkage ({df['SRC'].nunique()} fuentes). "
                f"Trusted Sources={trusted_sources} — dedup interna bloqueada para estas fuentes."
            )
        else:
            self.logger.info(f"MODO DETECTADO: Linkage ({df['SRC'].nunique()} fuentes).")

        self.logger.info(
            f"Ejecutando motor de linkage con cross_source_only={cross_source_flag}, trusted_sources={trusted_sources}"
        )

        # Ejecutar motor de linkage
        df_linked, diagnostics = self.engine.link(
            df,
            output_dir=output_dir_for_run,
            cross_source_only=cross_source_flag,
            return_diagnostics=True,
        )

        # --- CÓDIGO DE DEPURACIÓN (se puede comentar en producción) ---
        scored_pairs = diagnostics.get("scored_pairs", pd.DataFrame())
        if not scored_pairs.empty:
            IDS_A_OBSERVAR = {1130623599, 1130623590, 1130623596, 1130623595}
            df_preprocessed = self.results["df_preprocessed"]
            pares_de_interes = scored_pairs[
                (df_preprocessed.iloc[scored_pairs["idx_0"]].index.isin(IDS_A_OBSERVAR))
                | (df_preprocessed.iloc[scored_pairs["idx_1"]].index.isin(IDS_A_OBSERVAR))
            ]

            if not pares_de_interes.empty:
                print("\n" + "=" * 80)
                print("🕵️  DEBUGGING FASE 3: PARES ENVIADOS AL CLUSTERIZADOR")
                print("=" * 80)
                print(pares_de_interes.to_string())
                print("=" * 80 + "\n")
        # --- FIN DEL CÓDIGO DE DEPURACIÓN ---

        # Guardar resultados y métricas
        self.results["df_linked"] = df_linked
        self.results["linkage_diagnostics"] = diagnostics

        # Guardar checkpoint
        df_linked.to_parquet(checkpoint_path)
        self.logger.info(f"💾 Checkpoint de la Fase 3 guardado en: {checkpoint_path}")

        # Liberar memoria si no se requieren resultados intermedios
        if not self.keep_intermediate_results and "df_preprocessed" in self.results:
            del self.results["df_preprocessed"]
            gc.collect()
            self.logger.debug("Liberada memoria de df_preprocessed")

        # Métricas del motor
        engine_metrics = self.engine.get_metrics()
        unique_groups_count = df_linked["ID_GRUPO"].nunique()
        total_records_count = len(df)

        self.results["metrics"] = {
            "total_records": total_records_count,
            "unique_groups": unique_groups_count,
            "linkage_rate": (
                (total_records_count - unique_groups_count) / total_records_count
                if total_records_count > 0
                else 0
            ),
            "reduction_rate": (
                1 - (unique_groups_count / total_records_count) if total_records_count > 0 else 0
            ),
            "multi_source_groups": self._count_multi_source_groups(df_linked),
            **engine_metrics,
        }

        # Métricas de performance
        self.performance_metrics["phase_times"]["linkage"] = time.time() - phase_start
        self.performance_metrics["memory_usage"]["after_linkage"] = (
            MemoryManager.get_memory_status()
        )

        self.logger.info(
            f"Linkage completado: {unique_groups_count:,} grupos únicos "
            f"(reducción: {self.results['metrics']['reduction_rate']:.1%})"
        )

    @track_performance("Fase 4: Golden Records")
    def _phase4_golden_records(
        self, source_priority: list[str], golden_path: str, correlative_path: str, force_rerun: bool
    ):
        """Fase 4: Generación de Golden Records con checkpointing."""
        self.logger.info("=" * 60)
        self.logger.info("FASE 4: GENERACIÓN DE GOLDEN RECORDS (V7.0 - Producción)")
        self.logger.info("=" * 60)

        # Verificar checkpoints
        if not force_rerun and os.path.exists(golden_path) and os.path.exists(correlative_path):
            self.logger.info("✅ Cargando Golden Records y tabla correlativa desde checkpoints")
            self.results["golden_records"] = pd.read_parquet(golden_path)
            self.results["correlative_table"] = pd.read_parquet(correlative_path)

            # Liberar memoria si no se requieren resultados intermedios
            if not self.keep_intermediate_results and "df_linked" in self.results:
                del self.results["df_linked"]
                gc.collect()
                self.logger.debug("Liberada memoria de df_linked")

            return

        phase_start = time.time()

        # Instanciar y ejecutar el generador V7
        generator = GoldenRecordGeneratorV7(source_priority, self.config)
        golden_records, correlative_table = generator.generate(self.results["df_linked"])

        # Guardar resultados y métricas
        self.results["golden_records"] = golden_records
        self.results["correlative_table"] = correlative_table
        self.results.setdefault("metrics", {})["golden_records_time"] = time.time() - phase_start

        # Guardar métricas críticas antes de liberar memoria
        if "CONFIDENCE_SCORE" in golden_records.columns:
            self.results["metrics"]["avg_confidence"] = golden_records["CONFIDENCE_SCORE"].mean()
            self.results["metrics"]["review_cases"] = (
                golden_records["CONFIDENCE_SCORE"] < 0.75
            ).sum()

        # Guardar checkpoints
        golden_records.to_parquet(golden_path)
        correlative_table.to_parquet(correlative_path)
        self.logger.info("💾 Checkpoints de la Fase 4 guardados")

        self.logger.info(
            f"✅ Golden records generados en {time.time() - phase_start:.1f}s | "
            f"Velocidad: {len(golden_records) / (time.time() - phase_start + 1e-6):.0f} grupos/s"
        )

        # Liberar memoria si no se requieren resultados intermedios
        if not self.keep_intermediate_results and "df_linked" in self.results:
            self.logger.info("Liberando 'df_linked' de la memoria...")
            del self.results["df_linked"]
            gc.collect()
        else:
            self.logger.warning("Se mantiene 'df_linked' en memoria para optimización.")

    def _phase4_5_consolidation(
        self,
        source_priority: list[str],
        checkpoint_path: str,
        golden_checkpoint_path: str,
        force_rerun: bool,
    ):
        """Fase 4.5: Consolidación de grupos por NIT idéntico con checkpointing.

        Args:
            source_priority: Orden de prioridad de fuentes para resolver conflictos.
            checkpoint_path: Ruta del parquet de la tabla correlativa consolidada
                (output canónico para callers externos).
            golden_checkpoint_path: Ruta del parquet del golden record consolidado.
                Escrito explícitamente para garantizar que `PipelineResult.golden_records`
                lea el golden POST-consolidación, no el pre-consolidación de la fase 4.
            force_rerun: Si True, ignora cualquier checkpoint existente.
        """
        self.logger.info("=" * 60)
        self.logger.info("FASE 4.5: CONSOLIDACIÓN DE GRUPOS POR NIT IDÉNTICO")
        self.logger.info("=" * 60)

        # Verificar checkpoint (ambos archivos deben existir para reutilizar)
        if (
            not force_rerun
            and os.path.exists(checkpoint_path)
            and os.path.exists(golden_checkpoint_path)
        ):
            self.logger.info("✅ Cargando consolidación final desde checkpoints")
            final_correlative = pd.read_parquet(checkpoint_path)
            final_golden = pd.read_parquet(golden_checkpoint_path)
            self.results["correlative_table"] = final_correlative
            self.results["golden_records"] = final_golden
            self.results.setdefault("metrics", {})["unique_groups"] = len(final_golden)
            return

        golden_initial = self.results["golden_records"]
        correlative_initial = self.results["correlative_table"]

        # Llamar a la función de consolidación
        final_golden, final_correlative = consolidate_groups_by_nit_balanced(
            golden_initial,
            correlative_initial,
            strict_mode=False,
            verbose=True,
            prioridad_fuentes=source_priority,
        )

        # Actualizar resultados en memoria
        self.results["golden_records"] = final_golden
        self.results["correlative_table"] = final_correlative
        self.results.setdefault("metrics", {})["unique_groups"] = len(final_golden)

        # Persistir AMBOS checkpoints (correlativo y golden post-consolidación).
        # Esto garantiza que PipelineResult pueda leer la versión canónica
        # incluso si el DataFrame en memoria fue liberado por gc.
        final_correlative.to_parquet(checkpoint_path)
        final_golden.to_parquet(golden_checkpoint_path)
        self.logger.info(
            f"💾 Checkpoints Fase 4.5 guardados: "
            f"{os.path.basename(checkpoint_path)}, "
            f"{os.path.basename(golden_checkpoint_path)}"
        )

    @track_performance("Fase 5: Reportes y Exportación")
    def _phase5_reports_and_export(
        self,
        reports: list[str],
        generate_visualizations: bool,
    ) -> None:
        """
        Fase 5 V7.0 - Arquitectura completamente robusta y desacoplada.
        Esta fase siempre se ejecuta (no tiene checkpoint) ya que es principalmente I/O.
        """
        # Header del log
        self.logger.info("=" * 60)
        self.logger.info("FASE 5: REPORTES Y EXPORTACIÓN V7.0")
        self.logger.info("=" * 60)
        phase_start = time.time()

        # Configuración inicial
        output_dir = self.config.get("output_directory", "resultados_linkage")
        os.makedirs(output_dir, exist_ok=True)

        # Validar disponibilidad de componentes
        # v0.7.2 (Tarea 2.4): para los componentes con dependencias pesadas
        # (matplotlib/seaborn), usamos `_class_is_importable` que hace lazy
        # import sin cargar matplotlib hasta el primer uso real.
        components_available = {
            "SmartExporter": "SmartExporter" in globals(),
            "ReportGenerator": "ReportGenerator" in globals(),
            "DataVisualizer": _class_is_importable("DataVisualizer"),
            "ExecutiveDashboard": _class_is_importable("ExecutiveDashboard"),
            "EnhancedReportingSuite": _class_is_importable("EnhancedReportingSuite"),
        }

        self.logger.info("Componentes disponibles:")
        for comp, available in components_available.items():
            self.logger.info(f"  - {comp}: {'✓' if available else '✗'}")

        if not components_available["SmartExporter"]:
            self.logger.error("SmartExporter no disponible - abortando fase 5")
            return

        # Preparar datos y exportador
        try:
            exporter = SmartExporter(self.config)

            # Obtener referencias a los datos (pueden ser DataFrames o rutas)
            correlative_data = self.results.get("correlative_table_db") or self.results.get(
                "correlative_table"
            )
            golden_data = self.results.get("golden_records_db") or self.results.get(
                "golden_records"
            )
            metrics_data = self.results.get("metrics", {})

            # Manejo de datos en disco si es necesario
            if self.config.get("force_disk_results", False):
                if isinstance(correlative_data, str) and correlative_data.endswith(".db"):
                    self.logger.info(
                        "Datos en disco detectados, el sistema los manejará apropiadamente"
                    )

            # Debug logging
            self.logger.info("Referencias de datos:")
            self.logger.info(
                f"  - Correlative: {type(correlative_data)} {'(existe)' if correlative_data is not None else '(nulo)'}"
            )
            self.logger.info(
                f"  - Golden Records: {type(golden_data)} {'(existe)' if golden_data is not None else '(nulo)'}"
            )
            self.logger.info(f"  - Métricas: {len(metrics_data)} claves")

        except Exception as e:
            self.logger.error(f"Error preparando datos: {e!s}", exc_info=True)
            return

        # Exportar archivos base (CRÍTICO)
        try:
            self.logger.info("Exportando archivos base...")

            # Exportar tabla correlativa
            if correlative_data is not None:
                self._export_data_safely(exporter, correlative_data, "tabla_correlativa")
            else:
                self.logger.warning("No hay datos de tabla correlativa para exportar")

            # Exportar golden records
            if golden_data is not None:
                self._export_data_safely(exporter, golden_data, "golden_records")
            else:
                self.logger.warning("No hay golden records para exportar")

        except Exception as e:
            self.logger.error(f"Error crítico exportando archivos base: {e!s}", exc_info=True)

        # Liberar memoria (respetando keep_intermediate_results)
        if not self.keep_intermediate_results:
            if isinstance(correlative_data, pd.DataFrame) and "correlative_table" in self.results:
                self.logger.info("Liberando tabla correlativa de memoria...")
                del self.results["correlative_table"]

            if isinstance(golden_data, pd.DataFrame) and "golden_records" in self.results:
                self.logger.info("Liberando golden records de memoria...")
                del self.results["golden_records"]

            gc.collect()
        else:
            self.logger.warning(
                "Se mantienen 'correlative_table' y 'golden_records' en memoria para optimización."
            )

        # Generar reportes en Excel
        if reports and components_available["ReportGenerator"]:
            try:
                self.logger.info("-" * 40)
                self.logger.info("Generando reportes en Excel...")

                report_gen = ReportGenerator(
                    correlative_data=correlative_data,
                    golden_records_data=golden_data,
                    metrics=metrics_data,
                    config=self.config,
                )

                all_reports = report_gen.generate_all_reports()

                if all_reports:
                    self.logger.info(f"Exportando {len(all_reports)} reportes...")
                    for report_name, report_df in all_reports.items():
                        try:
                            filename = f"reporte_{report_name}"
                            exporter.export(report_df, filename, format="xlsx")
                            self.logger.info(f"  ✓ Exportado: {filename}.xlsx")
                        except Exception as e:
                            self.logger.error(f"  ✗ Error exportando {filename}: {e!s}")

            except ErrorPipeline:
                # Un error específico del pipeline (muestreo, tiempos por fase)
                # no se convierte en una línea de log: sube al llamador.
                raise
            except Exception as e:
                self.logger.error(f"Error en generación de reportes: {e!s}", exc_info=True)

        # Generar visualizaciones
        if generate_visualizations and components_available["DataVisualizer"]:
            try:
                self.logger.info("-" * 40)
                self.logger.info("Generando visualizaciones...")
                # v0.7.2 (Tarea 2.4): lazy import — matplotlib se carga aquí
                # solo si efectivamente vamos a generar visualizaciones.
                from ..reporting.visualizer import DataVisualizer

                visualizer = DataVisualizer(
                    correlative_data=correlative_data,
                    golden_records_data=golden_data,
                    metrics=metrics_data,
                    pipeline_start_time=self.performance_metrics.get("start_time"),
                )

                viz_dir = os.path.join(output_dir, "visualizaciones_detalladas")
                visualizer.save_all_visualizations(viz_dir)

            except ErrorPipeline:
                raise
            except Exception as e:
                self.logger.error(f"Error en visualizaciones: {e!s}", exc_info=True)

        # Generar reportes mejorados si está disponible
        if generate_visualizations and components_available["EnhancedReportingSuite"]:
            try:
                self.logger.info("-" * 40)
                self.logger.info("Generando reportes mejorados - EnhancedReportingSuite...")
                # v0.7.2 (Tarea 2.4): lazy import.
                from ..reporting.suite import EnhancedReportingSuite

                enhanced_suite = EnhancedReportingSuite(
                    correlative_data=correlative_data,
                    golden_records_data=golden_data,
                    metrics=metrics_data,
                    config=self.config,
                    max_memory_mb=500,
                    pipeline_start_time=self.performance_metrics.get("start_time"),
                )

                enhanced_files = enhanced_suite.generate_all_enhanced_reports(output_dir)

                if enhanced_files:
                    self.logger.info(
                        f"✓ Reportes mejorados generados: {len(enhanced_files)} archivos"
                    )
                    for report_type, filepath in enhanced_files.items():
                        self.logger.info(f"  - {report_type}: {os.path.basename(filepath)}")

            except ErrorPipeline:
                raise
            except Exception as e:
                self.logger.error(f"Error en reportes mejorados: {e!s}", exc_info=True)

        # Generar dashboard ejecutivo
        if generate_visualizations and components_available["ExecutiveDashboard"]:
            try:
                self.logger.info("-" * 40)
                self.logger.info("Generando dashboard ejecutivo...")
                # v0.7.2 (Tarea 2.4): lazy import.
                from ..reporting.dashboard import ExecutiveDashboard

                dashboard = ExecutiveDashboard(
                    correlative_data=correlative_data,
                    golden_records_data=golden_data,
                    metrics=metrics_data,
                    pipeline_start_time=self.performance_metrics.get("start_time"),
                )

                dashboard_path = os.path.join(output_dir, "dashboard_ejecutivo.png")
                dashboard.generate_dashboard(dashboard_path)
                self.logger.info("  ✓ Dashboard guardado: dashboard_ejecutivo.png")

            except ErrorPipeline:
                raise
            except Exception as e:
                self.logger.error(f"Error en dashboard ejecutivo: {e!s}", exc_info=True)

        # Finalizar y resumir
        elapsed = time.time() - phase_start
        self.results.setdefault("metrics", {})["export_time"] = elapsed
        self.performance_metrics.setdefault("phase_times", {})["reports_export"] = elapsed

        self.logger.info("-" * 40)
        self.logger.info(f"✅ Fase 5 completada en {elapsed:.1f}s")
        self.logger.info(f"📁 Resultados en: {output_dir}")

        # Listar archivos generados
        try:
            files = os.listdir(output_dir)
            if files:
                self.logger.info("Archivos generados:")
                for f in sorted(files):
                    if os.path.isfile(os.path.join(output_dir, f)):
                        size_kb = os.path.getsize(os.path.join(output_dir, f)) / 1024
                        self.logger.info(f"  - {f} ({size_kb:.1f} KB)")
        except:
            pass

    def _export_data_safely(self, exporter, data, filename: str):
        """
        Exporta datos de forma segura manejando diferentes tipos de entrada.
        """
        if data is None:
            self.logger.warning(f"No hay datos para exportar en '{filename}'")
            return

        try:
            # Si es un DataFrame vacío, advertir pero no fallar
            if isinstance(data, pd.DataFrame) and data.empty:
                self.logger.warning(f"DataFrame '{filename}' está vacío")
                return

            # Si es una ruta, verificar que existe
            if isinstance(data, str) and not os.path.exists(data):
                self.logger.error(f"Archivo no encontrado para '{filename}': {data}")
                return

            # Exportar
            exporter.export_with_auto_detection(data=data, filename=filename, format="auto")
            self.logger.info(f"✓ Exportado: {filename}")

        except Exception as e:
            self.logger.error(f"✗ Error exportando '{filename}': {e!s}")

    def _count_multi_source_groups(self, df_linked: pd.DataFrame) -> int:
        """Contar grupos con registros de múltiples fuentes."""
        if "SRC" not in df_linked.columns:
            return 0

        multi_source = df_linked.groupby("ID_GRUPO")["SRC"].nunique()
        return (multi_source > 1).sum()

    def _print_summary(self):
        """Imprimir resumen del proceso."""
        print("\n" + "=" * 60)
        print("✅ PROCESO COMPLETADO EXITOSAMENTE")
        print("=" * 60)

        metrics = self.results.get("metrics", {})

        print("\n📊 RESULTADOS:")
        print(f"   • Registros procesados: {metrics.get('total_records', 0):,}")
        print(f"   • Entidades únicas: {metrics.get('unique_groups', 0):,}")
        print(f"   • Entidades multi-fuente: {metrics.get('multi_source_groups', 0):,}")
        print(f"   • Tasa de linkage: {metrics.get('linkage_rate', 0):.2%}")

        # Manejar golden_records que podría no estar disponible
        if "golden_records" in self.results:
            golden_df = self.results["golden_records"]
            if "CONFIDENCE_SCORE" in golden_df.columns:
                print(f"   • Confidence promedio: {golden_df['CONFIDENCE_SCORE'].mean():.3f}")
                review_count = (golden_df["CONFIDENCE_SCORE"] < 0.75).sum()
                if review_count > 0:
                    print(f"\n📝 {review_count:,} grupos requieren revisión manual")
        else:
            # Si golden_records no está en memoria, usar métricas guardadas
            if "avg_confidence" in metrics:
                print(f"   • Confidence promedio: {metrics.get('avg_confidence', 0):.3f}")
            if "review_cases" in metrics:
                print(f"\n📝 {metrics.get('review_cases', 0):,} grupos requieren revisión manual")

        print(f"   • Tiempo total: {metrics.get('execution_time', 0) / 60:.1f} minutos")
        print(f"   • Memoria máxima: {metrics.get('max_memory_gb', 0):.1f} GB")

        print(f"\n📁 Resultados guardados en: {self.config.get('output_directory', 'N/A')}")
        print("   • tabla_correlativa.[xlsx/csv.zip]")
        print("   • golden_records.[xlsx/csv.zip]")
        print("   • reportes_calidad.xlsx")
        print("   • visualizaciones/")
        print("   • dashboard_ejecutivo.png")

        # Warnings basados en métricas guardadas
        if metrics.get("avg_confidence", 1.0) < 0.75:
            print("\n⚠️ ATENCIÓN: Confidence promedio bajo. Revisar calidad de datos.")

    def _save_partial_results(self):
        """Guardar resultados parciales en caso de error."""
        try:
            partial_dir = os.path.join(
                self.config.get("output_directory", "resultados_linkage"), "resultados_parciales"
            )
            os.makedirs(partial_dir, exist_ok=True)

            # Guardar lo que se pueda
            exporter = SmartExporter(self.config)

            for key, value in self.results.items():
                if isinstance(value, pd.DataFrame) and not value.empty:
                    exporter.export(value, f"partial_{key}", format="csv")

            self.logger.info(f"Resultados parciales guardados en: {partial_dir}")

        except Exception as e:
            self.logger.error(f"No se pudieron guardar resultados parciales: {e}")

    def _cleanup_resources(self):
        """Limpiar recursos y liberar memoria."""
        # Limpiar componentes
        if self._text_processor:
            self._text_processor.clear_cache()

        if self._nit_processor:
            self._nit_processor.clear_cache()

        if self._engine:
            # Usar método seguro que existe
            if hasattr(self._engine, "cleanup"):
                self._engine.cleanup()
            elif hasattr(self._engine, "_cleanup_resources"):
                self._engine._cleanup_resources()

        # Garbage collection
        gc.collect()
        self.logger.debug("Recursos liberados")

    def _finalize_pipeline(self):
        """Finalizar pipeline y calcular métricas totales."""
        self.performance_metrics["end_time"] = time.time()

        # Tiempo total
        total_time = self.performance_metrics["end_time"] - self.performance_metrics["start_time"]
        self.results["metrics"]["execution_time"] = total_time

        # Memoria máxima usada
        max_memory_gb = 0
        if self.performance_metrics.get("memory_usage"):
            memory_values = [
                status.get("used_gb", 0)
                for status in self.performance_metrics["memory_usage"].values()
                if isinstance(status, dict) and "used_gb" in status
            ]
            if memory_values:
                max_memory_gb = max(memory_values)

        self.results["metrics"]["max_memory_gb"] = max_memory_gb

        # Agregar métricas de performance a resultados
        self.results["performance_metrics"] = self.performance_metrics

        # Log final
        self.logger.debug(f"Pipeline finalizado en {total_time:.1f}s")

        # Limpiar archivos temporales de manera segura
        if hasattr(self, "_engine") and self._engine:
            if hasattr(self._engine, "cleanup_files_manually"):
                self._engine.cleanup_files_manually()

    # Métodos públicos adicionales

    def get_results(self) -> dict[str, Any]:
        """Obtener todos los resultados del pipeline."""
        if not self.is_fitted:
            raise ValueError("Pipeline no ha sido ejecutado. Llame a run() primero.")
        return self.results

    def get_golden_records(self) -> pd.DataFrame:
        """Obtener golden records generados."""
        if "golden_records" not in self.results:
            raise ValueError("No hay golden records disponibles")
        return self.results["golden_records"]

    def get_correlative_table(self) -> pd.DataFrame:
        """Obtener tabla correlativa."""
        if "correlative_table" not in self.results:
            raise ValueError("No hay tabla correlativa disponible")
        return self.results["correlative_table"]

    def get_metrics(self) -> dict[str, Any]:
        """Obtener métricas del proceso."""
        if "metrics" not in self.results:
            raise ValueError("No hay métricas disponibles")
        return self.results["metrics"]

    def update_config(self, **kwargs):
        """Actualizar configuración del pipeline."""
        self.config.update(kwargs)
        self.logger.info(f"Configuración actualizada: {list(kwargs.keys())}")

    def cleanup_temp_files(self, force: bool = False):
        """
        Limpiar archivos temporales generados durante el procesamiento.

        Args:
            force: Si True, elimina sin preguntar
        """
        temp_files = []

        # Recopilar archivos temporales de diferentes fuentes
        if hasattr(self, "_temp_files"):
            temp_files.extend(self._temp_files)

        # Verificar archivos del motor LSH
        if hasattr(self, "_engine") and self._engine:
            if hasattr(self._engine, "_candidate_finder"):
                candidate_finder = self._engine._candidate_finder
                if hasattr(candidate_finder, "_temp_files"):
                    temp_files.extend(candidate_finder._temp_files)

                # Archivos específicos del DiskBasedLSHEngine
                if hasattr(candidate_finder, "storage_dir"):
                    storage_dir = candidate_finder.storage_dir
                    # Buscar archivos .db y .h5 en el directorio
                    if os.path.exists(storage_dir):
                        for file in os.listdir(storage_dir):
                            if file.endswith((".db", ".h5")):
                                temp_files.append(os.path.join(storage_dir, file))

        # Eliminar duplicados
        temp_files = list(set(temp_files))

        if not temp_files:
            self.logger.info("No hay archivos temporales para limpiar")
            return

        self.logger.info(f"Archivos temporales a limpiar: {len(temp_files)}")

        if not force:
            # Listar archivos
            total_size_mb = 0
            for filepath in temp_files:
                if os.path.exists(filepath):
                    size_mb = os.path.getsize(filepath) / (1024**2)
                    total_size_mb += size_mb
                    self.logger.info(f"  - {os.path.basename(filepath)}: {size_mb:.1f} MB")

            self.logger.info(f"Total: {total_size_mb:.1f} MB")

            # Preguntar confirmación
            response = input("\n¿Desea eliminar estos archivos temporales? (s/n): ")
            if response.lower() != "s":
                self.logger.info("Limpieza cancelada")
                return

        # Eliminar archivos
        eliminated = 0
        for filepath in temp_files:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    eliminated += 1
                except Exception as e:
                    self.logger.warning(f"No se pudo eliminar {filepath}: {e}")

        self.logger.info(f"✅ Eliminados {eliminated} archivos temporales")
        temp_files.clear()

    def cleanup_checkpoints(self, output_dir: str | None = None):
        """
        Limpiar archivos de checkpoint generados durante el procesamiento.

        Args:
            output_dir: Directorio donde buscar checkpoints. Si None, usa el configurado.
        """
        if output_dir is None:
            output_dir = self.config.get("output_directory", "resultados_linkage")

        checkpoint_dir = os.path.join(output_dir, "intermediate_checkpoints")

        if not os.path.exists(checkpoint_dir):
            self.logger.info("No hay directorio de checkpoints para limpiar")
            return

        # Buscar archivos de checkpoint
        checkpoint_files = []
        for file in os.listdir(checkpoint_dir):
            if file.startswith("checkpoint_") and file.endswith(".parquet"):
                checkpoint_files.append(os.path.join(checkpoint_dir, file))

        if not checkpoint_files:
            self.logger.info("No hay archivos de checkpoint para limpiar")
            return

        # Calcular tamaño total
        total_size_mb = sum(os.path.getsize(f) / (1024**2) for f in checkpoint_files)

        self.logger.info(f"Archivos de checkpoint encontrados: {len(checkpoint_files)}")
        self.logger.info(f"Tamaño total: {total_size_mb:.1f} MB")

        # Preguntar confirmación
        response = input("\n¿Desea eliminar estos checkpoints? (s/n): ")
        if response.lower() != "s":
            self.logger.info("Limpieza de checkpoints cancelada")
            return

        # Eliminar archivos
        eliminated = 0
        for filepath in checkpoint_files:
            try:
                os.remove(filepath)
                eliminated += 1
            except Exception as e:
                self.logger.warning(f"No se pudo eliminar {filepath}: {e}")

        # Intentar eliminar el directorio si está vacío
        try:
            os.rmdir(checkpoint_dir)
            self.logger.info("Directorio de checkpoints eliminado")
        except:
            pass

        self.logger.info(f"✅ Eliminados {eliminated} archivos de checkpoint")
