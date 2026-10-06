"""
reporting.strategies — record_linkage_pipeline

Componentes:
    - class Phase  (origen: notebook celda [192])
    - class ReportingContext  (origen: notebook celda [192])
    - class PhaseResult  (origen: notebook celda [192])
    - class ReportingStrategy  (origen: notebook celda [192])
    - class BaseReportingStrategy  (origen: notebook celda [192])
    - class ExportTask  (origen: notebook celda [192])
    - class DataExportStrategy  (origen: notebook celda [192])
    - class ExcelReportsStrategy  (origen: notebook celda [192])
    - class VisualizationsStrategy  (origen: notebook celda [192])
    - class DashboardStrategy  (origen: notebook celda [192])
    - class EnhancedInsightsStrategy  (origen: notebook celda [192])
    - class ConfigAuditStrategy  (origen: notebook celda [192])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import contextlib
import gc
import gzip
import json
import logging
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import pandas as pd
import psutil
import pyarrow.parquet as pq

from ..exporters._spreadsheet import prepare_spreadsheet_data
from ..pipeline._internal import _class_exists
from ..pipeline.errores import EstrategiaFallo
from ._flags import PYARROW_AVAILABLE
from .contrato_l6 import ArtefactoOmitido, es_estrategia_obligatoria
from .reports import ReportGenerator

# v0.7.2 (Sprint 0.8.2, Tarea 2.4): los imports de ExecutiveDashboard,
# EnhancedReportingSuite y DataVisualizer se hacen LAZY dentro de cada
# strategy._execute_impl (NO en top-level). Estos módulos arrastran
# matplotlib/seaborn (~500 MB en RAM), y antes se cargaban siempre que
# alguien importara `Orchestrator` — incluso con skip_reporting=True.
# La validación de disponibilidad sigue ocurriendo en `is_available()`
# vía `_class_exists()`, que ya hace lazy import internamente.


class Phase(Enum):
    """
    Fases del pipeline de Record Linkage.

    El orden de ejecución es secuencial: L1 → L2 → L3 → L4 → L5 → L6
    Cada fase depende de los resultados de la anterior.
    """

    L1_PREP = "L1_prep"
    L2_LSH_CANDIDATES = "L2_lsh_candidates"
    L3_SCORING = "L3_scoring"
    L4_CLUSTERING = "L4_clustering"
    L5_GOLDEN = "L5_golden"
    L6_REPORTING = "L6_reporting"


@dataclass(frozen=True)
class ReportingContext:
    """
    DTO inmutable para transferir datos a las estrategias de reporte.

    Frozen=True garantiza inmutabilidad, evitando efectos secundarios
    accidentales durante la generación de reportes.

    Attributes:
        golden_df: DataFrame con los Golden Records consolidados
        correlative_df: DataFrame con la tabla correlativa completa
        config: Configuración del pipeline
        output_dir: Directorio donde se guardarán los reportes
        metrics: Métricas de ejecución calculadas
        start_time: Timestamp de inicio del proceso
        phase_times: Diccionario con duración de cada fase en segundos
    """

    golden_df: pd.DataFrame
    correlative_df: pd.DataFrame
    config: dict[str, Any]
    output_dir: Path
    metrics: dict[str, Any]
    start_time: float
    phase_times: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        """Validación en construcción - falla rápido si hay datos inválidos."""
        if self.golden_df is None:
            raise ValueError("golden_df no puede ser None")
        if self.correlative_df is None:
            raise ValueError("correlative_df no puede ser None")


@dataclass
class PhaseResult:
    """
    Resultado de una fase con metadatos asociados.

    Attributes:
        data: Datos producidos por la fase (DataFrame, Path, dict, etc.)
        files: Lista de archivos generados que deben persistirse
        duration: Tiempo de ejecución en segundos
        metrics: Métricas específicas de la fase
    """

    data: Any
    files: list[Path]
    duration: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ReportingStrategy(Protocol):
    """
    Protocolo para estrategias de generación de reportes.

    Permite extensibilidad sin modificar el orquestador (Open/Closed Principle).
    Cualquier clase que implemente 'name' y 'execute' es compatible.
    """

    name: str

    def execute(self, ctx: ReportingContext, logger: logging.Logger) -> list[Path]:
        """
        Ejecuta la estrategia y retorna lista de archivos generados.

        Args:
            ctx: Contexto con datos necesarios para generar reportes
            logger: Logger para reportar progreso y errores

        Returns:
            Lista de Paths a los archivos generados
        """
        ...


class BaseReportingStrategy(ABC):
    """
    Clase base abstracta para estrategias de reporte.

    Implementa el patrón Template Method para:
    - Verificación de dependencias antes de ejecutar
    - Logging estructurado y consistente
    - Fallos tipados: una excepción dentro de ``_execute_impl`` se relanza
      como ``EstrategiaFallo`` (F1.4). Hasta entonces se convertía en ``[]``
      y una corrida sin entregables pasaba por buena.

    Las subclases solo deben implementar:
    - name: Nombre descriptivo
    - required_class: Clase del notebook que necesita
    - _execute_impl: Lógica específica de generación

    Un artefacto OPCIONAL que no se pudo escribir se registra con
    ``self.omitir(artefacto, motivo)``; el orquestador lo lleva al manifiesto.
    ``omitidos`` existe desde la construcción (una subclase con ``__init__``
    propio llama a ``super().__init__()``) y se vacía al empezar cada
    ``execute``.
    """

    omitidos: list[ArtefactoOmitido]

    def __init__(self) -> None:
        self.omitidos = []

    @property
    def obligatoria(self) -> bool:
        """``True`` si la estrategia produce algún artefacto obligatorio del
        contrato de L6 (``reporting.contrato_l6``). Si falla, la corrida falla.
        Las subclases heredan el contrato de su base declarada."""
        return es_estrategia_obligatoria(self)

    def omitir(self, artefacto: str, motivo: str) -> None:
        """Deja constancia de un artefacto opcional que NO se escribió."""
        self.omitidos.append(ArtefactoOmitido(artefacto, type(self).__name__, motivo))

    @property
    @abstractmethod
    def name(self) -> str:
        """Nombre descriptivo de la estrategia para logging."""
        pass

    @property
    @abstractmethod
    def required_class(self) -> str:
        """
        Nombre de la clase requerida del notebook.
        Usar "None" si la estrategia no tiene dependencias externas.
        """
        pass

    @abstractmethod
    def _execute_impl(self, ctx: ReportingContext, logger: logging.Logger) -> list[Path]:
        """
        Implementación específica de la estrategia.

        Esta función se ejecuta solo si is_available() retorna True.
        Debe manejar sus propias excepciones internas si es necesario.
        """
        pass

    def is_available(self) -> bool:
        """
        Verifica si la clase requerida está disponible en el entorno.

        Returns:
            True si no hay dependencias o si la dependencia existe
        """
        if self.required_class == "None":
            return True
        return _class_exists(self.required_class)

    def execute(self, ctx: ReportingContext, logger: logging.Logger) -> list[Path]:
        """
        Template Method: ejecuta con validación y fallo tipado.

        Flujo:
        1. Verificar disponibilidad de dependencias
        2. Ejecutar implementación específica
        3. Reportar resultado, o relanzar ``EstrategiaFallo``

        Returns:
            Lista de archivos generados (vacía si la dependencia no está:
            esa omisión queda en ``omitidos`` con su motivo).

        Raises:
            EstrategiaFallo: si ``_execute_impl`` lanza. La causa queda
                encadenada en ``__cause__``. Quien llama decide si la
                corrida falla (estrategia obligatoria) o se registra la
                omisión (opcional). Nunca se devuelve ``[]`` por un error.
        """
        self.omitidos = []

        # Verificar dependencias
        if not self.is_available():
            motivo = f"clase '{self.required_class}' no disponible en el entorno"
            logger.warning(f"   ⚠️ {self.name}: {motivo}, omitiendo")
            self.omitir(self.name, motivo)
            return []

        try:
            files = self._execute_impl(ctx, logger)
        except Exception as e:
            logger.error(f"   ❌ {self.name} falló: {type(e).__name__}: {e}")
            logger.debug("   Traceback:", exc_info=True)
            raise EstrategiaFallo(self.name, e) from e

        if files:
            logger.info(f"   ✅ {self.name}: {len(files)} archivo(s) generado(s)")
        else:
            logger.debug(f"   ℹ️ {self.name}: Sin archivos generados")
        for omitido in self.omitidos:
            logger.warning(f"   ⚠️ {self.name}: omitido {omitido.artefacto} ({omitido.motivo})")
        return files


@dataclass
class ExportTask:
    """Tarea de exportación con metadatos."""

    name: str
    df_source: pd.DataFrame
    checkpoint_patterns: list[str]
    checkpoint_dir: Path | None

    def find_checkpoint(self) -> Path | None:
        """Busca archivo checkpoint que coincida con los patrones."""
        if not self.checkpoint_dir or not self.checkpoint_dir.exists():
            return None
        for pattern in self.checkpoint_patterns:
            path = self.checkpoint_dir / pattern
            if path.exists():
                return path
        return None


class DataExportStrategy(BaseReportingStrategy):
    """
    Estrategia de exportación con arquitectura Disk-First.

    PRINCIPIOS:
    - Disk-First: Prioriza lectura desde checkpoints en disco
    - Streaming: Usa iteradores para memoria constante O(1)
    - Fail-Safe: Múltiples niveles de fallback
    """

    STREAMING_BATCH_SIZE: int = 50_000
    EXCEL_ROW_LIMIT: int = 100_000
    MEMORY_WARNING_PERCENT: float = 80.0

    @property
    def name(self) -> str:
        return "Exportación de Datos (Disk-First)"

    @property
    def required_class(self) -> str:
        return "None"

    def _execute_impl(self, ctx: ReportingContext, logger: logging.Logger) -> list[Path]:
        """Implementación principal de exportación."""
        generated_files: list[Path] = []

        mem_percent = psutil.virtual_memory().percent
        logger.info(f"   💾 Memoria al inicio: {mem_percent:.1f}%")

        export_config = ctx.config.get("export_settings", {})
        excel_limit = min(
            export_config.get("excel_max_rows", self.EXCEL_ROW_LIMIT), self.EXCEL_ROW_LIMIT
        )

        checkpoint_dir = (
            self._find_checkpoint_dir(ctx.output_dir)
            if ctx.config.get("reporting_use_checkpoints", True)
            else None
        )

        export_tasks = [
            ExportTask(
                name="golden_records",
                df_source=ctx.golden_df,
                checkpoint_patterns=["golden.parquet", "golden_records.parquet"],
                checkpoint_dir=checkpoint_dir,
            ),
            ExportTask(
                name="tabla_correlativa",
                df_source=ctx.correlative_df,
                checkpoint_patterns=["correlative.parquet", "correlativa.parquet"],
                checkpoint_dir=checkpoint_dir,
            ),
        ]

        # F1.4: una tarea que falla NO se registra como advertencia y se
        # sigue: parquet y csv.gz son obligatorios y la excepción sube como
        # EstrategiaFallo → ArtefactoObligatorioError. Solo el Excel (opcional)
        # se omite con constancia.
        for task in export_tasks:
            files = self._process_export_task(task, ctx.output_dir, excel_limit, logger)
            generated_files.extend(files)
            gc.collect()

        return generated_files

    def _process_export_task(
        self, task: ExportTask, output_dir: Path, excel_limit: int, logger: logging.Logger
    ) -> list[Path]:
        """Procesa una tarea de exportación."""
        checkpoint_path = task.find_checkpoint()

        if checkpoint_path and PYARROW_AVAILABLE:
            logger.info(f"   📂 {task.name}: Streaming desde disco")
            return self._export_from_disk_streaming(
                checkpoint_path, task.name, output_dir, excel_limit, logger
            )
        else:
            logger.info(f"   🧠 {task.name}: Desde memoria ({len(task.df_source):,} filas)")
            return self._export_from_memory(
                task.df_source, task.name, output_dir, excel_limit, logger
            )

    def _export_from_disk_streaming(
        self,
        source_path: Path,
        base_name: str,
        output_dir: Path,
        excel_limit: int,
        logger: logging.Logger,
    ) -> list[Path]:
        """Exporta usando streaming REAL desde disco."""
        files: list[Path] = []

        parquet_file = pq.ParquetFile(source_path)
        total_rows = parquet_file.metadata.num_rows
        logger.info(f"      📊 Total: {total_rows:,} filas")

        # 1. CSV.gz (Streaming)
        csv_path = output_dir / f"{base_name}.csv.gz"
        logger.info("      💾 Streaming a CSV.gz...")

        with gzip.open(csv_path, "wt", encoding="utf-8", newline="") as f_out:
            first_batch = True
            for batch in parquet_file.iter_batches(batch_size=self.STREAMING_BATCH_SIZE):
                df_chunk = batch.to_pandas()
                prepare_spreadsheet_data(df_chunk).to_csv(f_out, index=False, header=first_batch)
                first_batch = False
                del df_chunk

        logger.info(f"      ✅ {csv_path.name} ({csv_path.stat().st_size / (1024**2):.1f} MB)")
        files.append(csv_path)

        # 2. Parquet (Copia directa)
        parquet_dest = output_dir / f"{base_name}.parquet"
        if source_path != parquet_dest:
            shutil.copy2(source_path, parquet_dest)
            logger.info(f"      ✅ {parquet_dest.name} (copia)")
            files.append(parquet_dest)

        # 3. Excel (opcional: si falla se omite con constancia, no se traga)
        if total_rows <= excel_limit:
            xlsx_path = output_dir / f"{base_name}.xlsx"
            try:
                df_excel = pd.read_parquet(source_path)
                prepare_spreadsheet_data(df_excel).to_excel(
                    xlsx_path, index=False, engine="openpyxl"
                )
                del df_excel
                gc.collect()
                logger.info(f"      ✅ {xlsx_path.name}")
                files.append(xlsx_path)
            except Exception as e:
                self._omitir_excel(xlsx_path, e, logger)
        else:
            xlsx_path = output_dir / f"{base_name}_MUESTRA_{excel_limit // 1000}k.xlsx"
            try:
                df_sample = next(parquet_file.iter_batches(batch_size=excel_limit)).to_pandas()
                prepare_spreadsheet_data(df_sample).to_excel(
                    xlsx_path, index=False, engine="openpyxl"
                )
                del df_sample
                gc.collect()
                logger.info(f"      ✅ {xlsx_path.name} (muestra)")
                files.append(xlsx_path)
            except Exception as e:
                self._omitir_excel(xlsx_path, e, logger)

        return files

    def _omitir_excel(self, xlsx_path: Path, causa: Exception, logger: logging.Logger) -> None:
        """Un Excel que falla no se escribe a medias ni se olvida: se borra el
        archivo parcial y la omisión queda registrada con su motivo."""
        with contextlib.suppress(OSError):
            xlsx_path.unlink(missing_ok=True)
        motivo = f"{type(causa).__name__}: {causa}"
        logger.warning(f"      ⚠️ {xlsx_path.name} omitido: {motivo}")
        self.omitir(xlsx_path.name, motivo)

    def _export_from_memory(
        self,
        df: pd.DataFrame,
        base_name: str,
        output_dir: Path,
        excel_limit: int,
        logger: logging.Logger,
    ) -> list[Path]:
        """Exporta desde DataFrame en memoria.

        Parquet y CSV.gz son obligatorios: si fallan, la excepción sube (hasta
        F1.4 se convertía en una advertencia y la corrida seguía sin
        entregable). El Excel es opcional y se omite con constancia.
        """
        files: list[Path] = []
        n_rows = len(df)

        # 1. Parquet (obligatorio)
        parquet_path = output_dir / f"{base_name}.parquet"
        df_clean = self._prepare_for_parquet(df)
        df_clean.to_parquet(parquet_path, index=False, engine="pyarrow", compression="snappy")
        del df_clean
        gc.collect()
        logger.info(f"      ✅ {parquet_path.name}")
        files.append(parquet_path)

        # 2. CSV.gz (obligatorio)
        csv_path = output_dir / f"{base_name}.csv.gz"
        prepare_spreadsheet_data(df).to_csv(csv_path, index=False, compression="gzip")
        logger.info(f"      ✅ {csv_path.name}")
        files.append(csv_path)

        # 3. Excel (opcional; solo si es seguro para la RAM)
        mem_percent = psutil.virtual_memory().percent
        if n_rows <= excel_limit and mem_percent < 85:
            xlsx_path = output_dir / f"{base_name}.xlsx"
            try:
                prepare_spreadsheet_data(df).to_excel(xlsx_path, index=False, engine="openpyxl")
                gc.collect()
                logger.info(f"      ✅ {xlsx_path.name}")
                files.append(xlsx_path)
            except Exception as e:
                self._omitir_excel(xlsx_path, e, logger)
        elif n_rows > excel_limit:
            xlsx_path = output_dir / f"{base_name}_MUESTRA_{excel_limit // 1000}k.xlsx"
            try:
                prepare_spreadsheet_data(df.head(excel_limit)).to_excel(
                    xlsx_path, index=False, engine="openpyxl"
                )
                gc.collect()
                logger.info(f"      ✅ {xlsx_path.name} (muestra)")
                files.append(xlsx_path)
            except Exception as e:
                self._omitir_excel(xlsx_path, e, logger)
        else:
            self.omitir(
                f"{base_name}.xlsx",
                f"RAM al {mem_percent:.1f} %: se evita el Excel completo para proteger la corrida",
            )

        return files

    def _find_checkpoint_dir(self, output_dir: Path) -> Path | None:
        """Busca directorio de checkpoints L5."""
        for path in [
            output_dir.parent / "L5_golden",
            output_dir.parent / "checkpoints" / "L5_golden",
        ]:
            if path.exists():
                return path
        return None

    def _prepare_for_parquet(self, df: pd.DataFrame) -> pd.DataFrame:
        """Prepara DataFrame para Parquet."""
        cat_cols = df.select_dtypes(include=["category"]).columns.tolist()
        string_cols = [
            "NIT",
            "NIT_FINAL",
            "NIT_OK",
            "NIT_BASE",
            "RAZON_SOCIAL",
            "RAZON_SOCIAL_FINAL",
            "NOMBRE_LIMPIO",
            "SRC",
            "PHONETIC_KEY1",
        ]
        cols_to_clean = [c for c in string_cols if c in df.columns]

        if not cat_cols and not cols_to_clean:
            return df

        df_clean = df.copy()
        for col in cat_cols:
            df_clean[col] = df_clean[col].astype("object")
        for col in cols_to_clean:
            df_clean[col] = df_clean[col].fillna("").astype(str).replace("nan", "")
        return df_clean


class ExcelReportsStrategy(BaseReportingStrategy):
    """Genera reportes Excel detallados usando ReportGenerator."""

    @property
    def name(self) -> str:
        return "Reportes Excel Detallados"

    @property
    def required_class(self) -> str:
        return "ReportGenerator"

    def _execute_impl(self, ctx: ReportingContext, logger: logging.Logger) -> list[Path]:
        generator = ReportGenerator(
            correlative_data=ctx.correlative_df,
            golden_records_data=ctx.golden_df,
            metrics=ctx.metrics,
            config=ctx.config,
        )

        reports = generator.generate_all_reports()
        # Los reportes que el generador no pudo producir ya vienen con motivo.
        for archivo, motivo in generator.omitidos:
            self.omitir(archivo, motivo)
        generated = []

        for report_name, df_report in reports.items():
            path = ctx.output_dir / f"reporte_{report_name}.xlsx"
            try:
                prepare_spreadsheet_data(df_report).to_excel(path, index=False, engine="openpyxl")
            except Exception as e:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
                self.omitir(path.name, f"escritura Excel falló: {type(e).__name__}: {e}")
                continue
            generated.append(path)
            logger.debug(f"      • reporte_{report_name}.xlsx ({len(df_report):,} filas)")

        return generated


class VisualizationsStrategy(BaseReportingStrategy):
    """Genera visualizaciones PNG usando DataVisualizer."""

    @property
    def name(self) -> str:
        return "Visualizaciones PNG"

    @property
    def required_class(self) -> str:
        return "DataVisualizer"

    def _execute_impl(self, ctx: ReportingContext, logger: logging.Logger) -> list[Path]:
        # v0.7.2 (Tarea 2.4): lazy import — matplotlib/seaborn solo se cargan
        # cuando se va a generar visualizaciones realmente.
        from .visualizer import DataVisualizer

        # Enriquecer métricas con información de tiempo
        metrics_enriched = ctx.metrics.copy()
        metrics_enriched["phase_times"] = ctx.phase_times
        metrics_enriched["execution_time"] = time.time() - ctx.start_time if ctx.start_time else 0

        visualizer = DataVisualizer(
            correlative_data=ctx.correlative_df,
            golden_records_data=ctx.golden_df,
            metrics=metrics_enriched,
            pipeline_start_time=ctx.start_time,
            config=ctx.config,
        )

        # Crear subdirectorio para visualizaciones
        viz_dir = ctx.output_dir / "visualizaciones"
        viz_dir.mkdir(exist_ok=True)

        files_map = visualizer.save_all_visualizations(str(viz_dir))
        self.omitidos.extend(
            ArtefactoOmitido(f"visualizaciones/{nombre}", type(self).__name__, motivo)
            for nombre, motivo in visualizer.omitidos
        )

        return [Path(p) for p in files_map.values() if Path(p).exists()]


class DashboardStrategy(BaseReportingStrategy):
    """Genera dashboard ejecutivo usando ExecutiveDashboard."""

    @property
    def name(self) -> str:
        return "Dashboard Ejecutivo"

    @property
    def required_class(self) -> str:
        return "ExecutiveDashboard"

    def _execute_impl(self, ctx: ReportingContext, logger: logging.Logger) -> list[Path]:
        # v0.7.2 (Tarea 2.4): lazy import.
        from .dashboard import ExecutiveDashboard

        # Enriquecer métricas
        metrics_enriched = ctx.metrics.copy()
        metrics_enriched["phase_times"] = ctx.phase_times
        metrics_enriched["execution_time"] = time.time() - ctx.start_time if ctx.start_time else 0

        # Agregar tiempos individuales de cada fase
        for phase_name, duration in ctx.phase_times.items():
            metrics_enriched[f"{phase_name}_time"] = duration

        dashboard = ExecutiveDashboard(
            correlative_data=ctx.correlative_df,
            golden_records_data=ctx.golden_df,
            metrics=metrics_enriched,
            pipeline_start_time=ctx.start_time,
            config=ctx.config,
        )

        # Dashboard principal
        path = ctx.output_dir / "dashboard_ejecutivo.png"
        dashboard.generate_dashboard(str(path))
        generated = [path]

        # Dashboard mejorado si el método existe
        if hasattr(dashboard, "generate_enhanced_dashboard"):
            enhanced_path = ctx.output_dir / "dashboard_ejecutivo_mejorado.png"
            try:
                dashboard.generate_enhanced_dashboard(str(enhanced_path))
                generated.append(enhanced_path)
            except Exception as e:
                self.omitir(enhanced_path.name, f"{type(e).__name__}: {e}")

        return generated


class EnhancedInsightsStrategy(BaseReportingStrategy):
    """Genera reportes avanzados con insights usando EnhancedReportingSuite."""

    @property
    def name(self) -> str:
        return "Insights Avanzados"

    @property
    def required_class(self) -> str:
        return "EnhancedReportingSuite"

    def _execute_impl(self, ctx: ReportingContext, logger: logging.Logger) -> list[Path]:
        # v0.7.2 (Tarea 2.4): lazy import.
        from .suite import EnhancedReportingSuite

        metrics_enriched = ctx.metrics.copy()
        metrics_enriched["phase_times"] = ctx.phase_times
        metrics_enriched["execution_time"] = time.time() - ctx.start_time if ctx.start_time else 0

        suite = EnhancedReportingSuite(
            correlative_data=ctx.correlative_df,
            golden_records_data=ctx.golden_df,
            metrics=metrics_enriched,
            config=ctx.config,
            pipeline_start_time=ctx.start_time,
        )

        files_map = suite.generate_all_enhanced_reports(str(ctx.output_dir))
        self.omitidos.extend(
            ArtefactoOmitido(nombre, type(self).__name__, motivo)
            for nombre, motivo in suite.omitidos
        )

        return [Path(p) for p in files_map.values() if Path(p).exists()]


class ConfigAuditStrategy(BaseReportingStrategy):
    """
    Exporta configuración y métricas para auditoría y reproducibilidad.

    Genera:
    - Archivo JSON con configuración completa
    - Archivo TXT legible con resumen
    """

    @property
    def name(self) -> str:
        return "Auditoría de Configuración"

    @property
    def required_class(self) -> str:
        return "None"  # Siempre disponible

    def _execute_impl(self, ctx: ReportingContext, logger: logging.Logger) -> list[Path]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        profile_name = ctx.config.get("profile", "Unknown")
        profile_params = ctx.config.get("profiles", {}).get(profile_name, {})

        # Construir estructura de auditoría
        audit_data = {
            "meta": {
                "timestamp": datetime.now().isoformat(),
                "orchestrator_version": "8.5",
                "profile": profile_name,
            },
            "metrics": ctx.metrics,
            "parameters": {
                "lsh": {
                    k: profile_params.get(k)
                    for k in ["lsh_permutations", "lsh_threshold", "lsh_ngram", "cross_source_only"]
                },
                "scoring": {
                    k: profile_params.get(k)
                    for k in ["score_threshold", "min_name_similarity", "max_nit_distance"]
                },
                "weights": profile_params.get("weights", {}),
                "source_priority": profile_params.get("source_quality_weights", {}),
            },
            "phase_times": ctx.phase_times,
            "config_completa": ctx.config,
        }

        generated = []

        # JSON (para procesamiento programático)
        json_path = ctx.output_dir / f"config_auditoria_{timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(audit_data, f, indent=2, default=str, ensure_ascii=False)
        generated.append(json_path)

        # TXT (para lectura humana): OPCIONAL en el contrato de L6. Si falla
        # (un perfil con pesos no numéricos, p. ej.) no tumba la corrida: el
        # JSON obligatorio ya está, el TXT a medias se borra y la omisión queda
        # con su motivo. La misma regla que el Excel en DataExportStrategy.
        txt_path = ctx.output_dir / f"config_auditoria_{timestamp}.txt"
        try:
            self._write_txt_audit(
                txt_path, audit_data, profile_params, ctx.metrics, ctx.phase_times
            )
            generated.append(txt_path)
        except Exception as e:
            with contextlib.suppress(OSError):
                txt_path.unlink(missing_ok=True)
            motivo = f"{type(e).__name__}: {e}"
            logger.warning(f"      ⚠️ {txt_path.name} omitido: {motivo}")
            self.omitir(txt_path.name, motivo)

        return generated

    def _write_txt_audit(
        self, path: Path, audit: dict, params: dict, metrics: dict, phase_times: dict
    ):
        """Escribe archivo de auditoría en formato legible para humanos."""
        with open(path, "w", encoding="utf-8") as f:
            # Header
            f.write("=" * 70 + "\n")
            f.write("📋 AUDITORÍA DE CONFIGURACIÓN - ORCHESTRATOR v8.5\n")
            f.write(f"   Timestamp: {audit['meta']['timestamp']}\n")
            f.write(f"   Profile: {audit['meta']['profile']}\n")
            f.write("=" * 70 + "\n\n")

            # Tiempos por fase
            f.write("⏱️ TIEMPOS POR FASE\n")
            f.write("-" * 70 + "\n")
            total_time = 0
            for phase, duration in phase_times.items():
                mins = duration / 60
                total_time += duration
                f.write(f"  {phase:<25}: {mins:>8.1f} min\n")
            f.write("-" * 70 + "\n")
            f.write(f"  {'TOTAL':<25}: {total_time / 60:>8.1f} min ({total_time / 3600:.2f} h)\n\n")

            # Métricas de ejecución
            f.write("🎯 MÉTRICAS DE EJECUCIÓN\n")
            f.write("-" * 70 + "\n")
            for k, v in metrics.items():
                if k != "phase_times" and k != "records_per_source":
                    if isinstance(v, float):
                        f.write(f"  {k:<30}: {v:>12.4f}\n")
                    elif isinstance(v, int):
                        f.write(f"  {k:<30}: {v:>12,}\n")
                    elif isinstance(v, dict):
                        f.write(f"  {k:<30}: {len(v)} items\n")
                    else:
                        f.write(f"  {k:<30}: {v}\n")
            f.write("\n")

            # Parámetros LSH
            f.write("📊 PARÁMETROS LSH\n")
            f.write("-" * 70 + "\n")
            for k, v in audit["parameters"]["lsh"].items():
                f.write(f"  {k:<30}: {v}\n")
            f.write("\n")

            # Parámetros Scoring
            f.write("🎯 PARÁMETROS SCORING\n")
            f.write("-" * 70 + "\n")
            for k, v in audit["parameters"]["scoring"].items():
                f.write(f"  {k:<30}: {v}\n")
            f.write("\n")

            # Pesos de matching
            f.write("⚖️ PESOS DE MATCHING\n")
            f.write("-" * 70 + "\n")
            for k, v in audit["parameters"]["weights"].items():
                f.write(f"  {k:<30}: {v}\n")
            f.write("\n")

            # Prioridad de fuentes
            f.write("🏆 PRIORIDAD DE FUENTES\n")
            f.write("-" * 70 + "\n")
            source_priority = audit["parameters"]["source_priority"]
            for src, weight in sorted(source_priority.items(), key=lambda x: -x[1]):
                f.write(f"  {src:<30}: {weight:.2f}\n")

            # Footer
            f.write("\n" + "=" * 70 + "\n")
            f.write("FIN DEL ARCHIVO DE AUDITORÍA\n")
            f.write("=" * 70 + "\n")
