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

F1.10: ``DataExportStrategy`` escribe ``tabla_correlativa.*`` y
``golden_records.*`` como ALIAS de v1 del estándar de salida (la carpeta que
deja ``linkage(carpeta_salida=...)`` vía ``exporters.escritor``). Avisa con
``DeprecationWarning`` una vez por proceso, el ``.xlsx`` lleva una primera
hoja ``LEEME`` que remite a ``excel/correlativa.xlsx`` / ``excel/golden.xlsx``
y todo pasa por las primitivas del escritor (ningún ``to_parquet``/
``to_excel``/``to_csv`` directo aquí). Los alias desaparecen en
``VERSION_RETIRO_ALIAS_V1``. El aviso sale también por ``logging`` (Python,
IPython y Colab silencian por defecto los ``DeprecationWarning`` que no nacen
en ``__main__``). ``ExcelReportsStrategy`` (``reporte_*.xlsx``) no es un
alias: pasa por ``escribir_xlsx`` pero conserva la hoja ``Sheet1`` de v1
hasta que F1.11 lo unifique en ``informe_cruce.xlsx``.

F1.12: ``ConfigAuditStrategy`` es un ALIAS de v1. Lo que era
``config_auditoria_<ts>.json/.txt`` (parámetros LSH, scoring, pesos,
prioridad de fuentes, tiempos por fase, métricas) vive en ``manifest.json`` de
la carpeta del estándar (``parametros`` / ``tiempos_por_fase`` / ``metricas``,
con ``version`` real e insumos con huella). La estrategia escribe
``config_auditoria.json`` (nombre estable, sin marca de tiempo) con
``vease: "manifest.json"`` y el mismo bloque de parámetros
(``config.auditoria.parametros_motor``: una regla, una vez), es OPCIONAL en
el contrato de L6 y avisa con ``DeprecationWarning``; el ``.txt`` ya no se
escribe.

hasta que se unifique en ``informe_cruce.xlsx``.

F1.11: el ``.xlsx`` de los alias se escribe COMPLETO hasta
``LIMITE_FILAS_EXCEL`` filas (en flujo, ``exporters.excel``) o, si no cabe,
``<alias>_LEEME.xlsx``; nunca más la muestra recortada de v1. La perilla
``export_settings.excel_max_rows`` (recortaba a N filas) ya no hace nada y se
avisa una vez por proceso si aparece en la configuración.
"""

from __future__ import annotations

import contextlib
import gc
import json
import logging
import shutil
import time
import warnings
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import pandas as pd
import psutil
import pyarrow.parquet as pq

from ..config.auditoria import parametros_motor
from ..exporters.escritor import (
    VERSION_RETIRO_ALIAS_V1,
    escribir_csv_gz,
    escribir_csv_gz_por_lotes,
    escribir_excel_o_leeme,
    escribir_parquet,
    escribir_xlsx,
    leeme_alias_v1,
)
from ..exporters.excel import LIMITE_FILAS_EXCEL, miles, motivo_no_cabe
from ..pipeline._internal import _class_exists
from ..pipeline.errores import EstrategiaFallo
from ._flags import PYARROW_AVAILABLE
from .contrato_l6 import ArtefactoOmitido, artefactos_de, es_estrategia_obligatoria
from .reports import ReportGenerator

# v0.7.2 (Sprint 0.8.2, Tarea 2.4): los imports de ExecutiveDashboard,
# EnhancedReportingSuite y DataVisualizer se hacen LAZY dentro de cada
# strategy._execute_impl (NO en top-level). Estos módulos arrastran
# matplotlib/seaborn (~500 MB en RAM), y antes se cargaban siempre que
# alguien importara `Orchestrator` — incluso con skip_reporting=True.
# La validación de disponibilidad sigue ocurriendo en `is_available()`
# vía `_class_exists()`, que ya hace lazy import internamente.


#: Alias de v1 → archivo del estándar al que remiten (F1.10).
ARCHIVO_NUEVO_DE_ALIAS: dict[str, str] = {
    "tabla_correlativa": "excel/correlativa.xlsx",
    "golden_records": "excel/golden.xlsx",
}

_ALIAS_V1_AVISADO = False
_PERILLA_EXCEL_AVISADA = False
_logger = logging.getLogger(__name__)

#: Perilla de v1 que recortaba el Excel a N filas. Retirada en F1.11: el Excel
#: va completo o se escribe el LEEME. Se avisa, no se ignora en silencio.
PERILLA_EXCEL_RETIRADA = "excel_max_rows"


def _avisar_alias_v1() -> None:
    """Avisa una sola vez por proceso que los alias de v1 se van.

    ``DeprecationWarning`` (lo que pide la especificación) y, con el mismo
    texto, ``logging.warning``: Python, IPython y Colab ignoran por defecto los
    ``DeprecationWarning`` que no se originan en ``__main__``, así que sin el
    log el usuario de v1 nunca lo vería.
    """
    global _ALIAS_V1_AVISADO
    if _ALIAS_V1_AVISADO:
        return
    _ALIAS_V1_AVISADO = True
    mensaje = (
        "L6_reporting/tabla_correlativa.* y golden_records.* son ALIAS de v1 desde 0.23.0: "
        "el entregable es la carpeta del estándar (linkage(carpeta_salida=...)), con "
        "excel/correlativa.xlsx y excel/golden.xlsx. Los alias desaparecen en rues-linker "
        f"{VERSION_RETIRO_ALIAS_V1}."
    )
    warnings.warn(mensaje, DeprecationWarning, stacklevel=2)
    _logger.warning(mensaje)


#: Nombre estable del alias de v1 de la auditoría de configuración (F1.12).
ALIAS_AUDITORIA = "config_auditoria.json"

_AUDITORIA_V1_AVISADA = False


def _avisar_auditoria_v1() -> None:
    """Avisa una sola vez por proceso que ``config_auditoria.json`` es un alias.

    Mismo patrón que ``_avisar_alias_v1``: ``DeprecationWarning`` y, con el
    mismo texto, ``logging.warning``.
    """
    global _AUDITORIA_V1_AVISADA
    if _AUDITORIA_V1_AVISADA:
        return
    _AUDITORIA_V1_AVISADA = True
    mensaje = (
        f"L6_reporting/{ALIAS_AUDITORIA} es un ALIAS de v1 desde 0.23.0: la configuración "
        "efectiva, los tiempos por fase y las métricas de la corrida viven en manifest.json "
        "de la carpeta del estándar (linkage(carpeta_salida=...)) bajo parametros / "
        "tiempos_por_fase / metricas; config_auditoria_<ts>.txt ya no se escribe. El alias "
        f"desaparece en rues-linker {VERSION_RETIRO_ALIAS_V1}."
    )
    warnings.warn(mensaje, DeprecationWarning, stacklevel=2)
    _logger.warning(mensaje)


def _avisar_perilla_excel(export_config: Mapping[str, Any], logger: logging.Logger) -> None:
    """``export_settings.excel_max_rows`` ya no recorta nada: se avisa una vez por proceso."""
    global _PERILLA_EXCEL_AVISADA
    if _PERILLA_EXCEL_AVISADA or PERILLA_EXCEL_RETIRADA not in export_config:
        return
    _PERILLA_EXCEL_AVISADA = True
    logger.warning(
        f"export_settings.{PERILLA_EXCEL_RETIRADA}={export_config[PERILLA_EXCEL_RETIRADA]!r} "
        "ya no aplica (F1.11): el Excel se escribe completo hasta "
        f"{miles(LIMITE_FILAS_EXCEL)} filas o se deja <alias>_LEEME.xlsx; nunca un recorte. "
        "Por qué importa: una muestra sin rótulo pasaba por la tabla completa. "
        "Qué hacer: retire la perilla de la configuración."
    )


def _leeme_de(base_name: str) -> pd.DataFrame:
    return leeme_alias_v1(ARCHIVO_NUEVO_DE_ALIAS.get(base_name, "la carpeta del estándar"))


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
        prioridad_fuentes: la prioridad REAL del golden (``Orchestrator.
            prioridad_fuentes``), para que el alias de auditoría (F1.12) diga
            lo mismo que el manifiesto; vacía si el llamador no la conoce.
    """

    golden_df: pd.DataFrame
    correlative_df: pd.DataFrame
    config: dict[str, Any]
    output_dir: Path
    metrics: dict[str, Any]
    start_time: float
    phase_times: dict[str, float] = field(default_factory=dict)
    prioridad_fuentes: tuple[str, ...] = ()

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

        # Verificar dependencias. La omisión se registra por cada artefacto
        # que la estrategia habría producido (la misma forma que usa el
        # orquestador cuando la salta por RAM crítica): quien lea el
        # manifiesto busca por nombre de archivo, no por nombre de estrategia.
        if not self.is_available():
            motivo = f"clase '{self.required_class}' no disponible en el entorno"
            for artefacto in artefactos_de(self) or (self.name,):
                self.omitir(artefacto, motivo)
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
        _avisar_alias_v1()

        mem_percent = psutil.virtual_memory().percent
        logger.info(f"   💾 Memoria al inicio: {mem_percent:.1f}%")

        _avisar_perilla_excel(ctx.config.get("export_settings", {}) or {}, logger)

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
            files = self._process_export_task(task, ctx.output_dir, logger)
            generated_files.extend(files)
            gc.collect()

        return generated_files

    def _process_export_task(
        self, task: ExportTask, output_dir: Path, logger: logging.Logger
    ) -> list[Path]:
        """Procesa una tarea de exportación."""
        checkpoint_path = task.find_checkpoint()

        if checkpoint_path and PYARROW_AVAILABLE:
            logger.info(f"   📂 {task.name}: Streaming desde disco")
            return self._export_from_disk_streaming(checkpoint_path, task.name, output_dir, logger)
        else:
            logger.info(f"   🧠 {task.name}: Desde memoria ({len(task.df_source):,} filas)")
            return self._export_from_memory(task.df_source, task.name, output_dir, logger)

    def _export_from_disk_streaming(
        self,
        source_path: Path,
        base_name: str,
        output_dir: Path,
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

        escribir_csv_gz_por_lotes(parquet_file, csv_path, filas_por_lote=self.STREAMING_BATCH_SIZE)

        logger.info(f"      ✅ {csv_path.name} ({csv_path.stat().st_size / (1024**2):.1f} MB)")
        files.append(csv_path)

        # 2. Parquet (Copia directa)
        parquet_dest = output_dir / f"{base_name}.parquet"
        if source_path != parquet_dest:
            shutil.copy2(source_path, parquet_dest)
            logger.info(f"      ✅ {parquet_dest.name} (copia)")
            files.append(parquet_dest)

        # 3. Excel (opcional: si falla se omite con constancia, no se traga).
        # F1.11: completo (por lotes desde el parquet) o <alias>_LEEME.xlsx.
        files.extend(self._excel_o_leeme(parquet_file, base_name, output_dir, total_rows, logger))

        return files

    def _excel_o_leeme(
        self,
        fuente: pd.DataFrame | pq.ParquetFile,
        base_name: str,
        output_dir: Path,
        n_rows: int,
        logger: logging.Logger,
    ) -> list[Path]:
        """Excel completo hasta ``LIMITE_FILAS_EXCEL`` o ``<alias>_LEEME.xlsx``, nunca recorte.

        Si la tabla no cabe, ``<alias>.xlsx`` queda en ``omitidos`` con el motivo
        y el LEEME (que remite al parquet del alias y al archivo nuevo) se
        devuelve como generado. Un fallo de escritura se omite con constancia.
        """
        xlsx_path = output_dir / f"{base_name}.xlsx"
        try:
            # hoja="datos": el nombre que F1.10 daba a los alias (escribir_xlsx); un
            # lector con pd.read_excel(sheet_name="datos") distingue mayúsculas.
            escrito = escribir_excel_o_leeme(
                fuente,
                xlsx_path,
                hoja="datos",
                leeme=_leeme_de(base_name),
                filas_por_lote=self.STREAMING_BATCH_SIZE,
            )
        except Exception as e:
            self._omitir_excel(xlsx_path, e, logger)
            return []
        gc.collect()
        if escrito == xlsx_path:
            logger.info(f"      ✅ {xlsx_path.name}")
            return [escrito]
        motivo = motivo_no_cabe(n_rows, escrito.name)  # el mismo texto que el estándar
        logger.warning(f"      ⚠️ {xlsx_path.name} omitido: {motivo}")
        self.omitir(xlsx_path.name, motivo)
        logger.info(f"      ✅ {escrito.name} (la tabla completa está en {base_name}.parquet)")
        return [escrito]

    def _omitir_excel(self, xlsx_path: Path, causa: Exception, logger: logging.Logger) -> None:
        """Un Excel que falla no se escribe a medias ni se olvida: se borra el
        archivo parcial y la omisión queda registrada con su motivo."""
        with contextlib.suppress(OSError):
            xlsx_path.unlink(missing_ok=True)
        # `execute` ya escribe un warning por cada omisión: aquí solo se registra.
        self.omitir(xlsx_path.name, f"{type(causa).__name__}: {causa}")

    def _export_from_memory(
        self,
        df: pd.DataFrame,
        base_name: str,
        output_dir: Path,
        logger: logging.Logger,
    ) -> list[Path]:
        """Exporta desde DataFrame en memoria.

        Parquet y CSV.gz son obligatorios: si fallan, la excepción sube (hasta
        F1.4 se convertía en una advertencia y la corrida seguía sin
        entregable). El Excel es opcional y se omite con constancia.
        """
        files: list[Path] = []
        n_rows = len(df)

        # 1. Parquet (obligatorio: un fallo tumba la corrida, F1.4)
        parquet_path = output_dir / f"{base_name}.parquet"
        df_clean = self._prepare_for_parquet(df)
        escribir_parquet(df_clean, parquet_path)
        del df_clean
        gc.collect()
        logger.info(f"      ✅ {parquet_path.name}")
        files.append(parquet_path)

        # 2. CSV.gz (obligatorio)
        csv_path = output_dir / f"{base_name}.csv.gz"
        escribir_csv_gz(df, csv_path)
        logger.info(f"      ✅ {csv_path.name}")
        files.append(csv_path)

        # 3. Excel (opcional; completo o LEEME, F1.11). Con la RAM al límite se
        # omite con constancia: la escritura va por lotes, pero cada lote copia.
        mem_percent = psutil.virtual_memory().percent
        if mem_percent < 85:
            files.extend(self._excel_o_leeme(df, base_name, output_dir, n_rows, logger))
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
                # hoja="Sheet1": la forma de v1 (to_excel sin sheet_name). No es un
                # alias, así que no lleva LEEME; F1.11 lo unifica en informe_cruce.xlsx.
                escribir_xlsx(df_report, path, hoja="Sheet1")
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
        # F1.6: el dashboard lee ``phase_times`` con reporting._fases; ya no se
        # duplican como ``<fase>_time`` (nadie los leía).

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
    """ALIAS de v1 (F1.12): escribe ``config_auditoria.json`` y remite al manifiesto.

    Hasta F1.12 escribía ``config_auditoria_<timestamp>.json`` (obligatorio) y
    ``.txt`` (opcional) con ``orchestrator_version: "8.5"`` fijo, sin huellas
    de insumos y con ``source_priority`` leído de ``source_quality_weights``
    sin la segunda mitad de la regla del golden (orden de las fuentes si el
    perfil no trae pesos): decía ``{}`` cuando el golden usaba otra cosa. Todo
    eso vive ahora en ``manifest.json`` de la carpeta del estándar, escrito
    por ``exporters.escritor`` con la versión real del paquete y las huellas.

    Lo que queda aquí es el alias: nombre ESTABLE (sin marca de tiempo),
    ``vease: "manifest.json"``, el mismo bloque ``parametros`` que el
    manifiesto (``config.auditoria.parametros_motor``), los tiempos de las
    fases cerradas (L1…L5) y las métricas de L6 (``Orchestrator._build_metrics``,
    cuyo bloque en español es ``pipeline.metricas.metricas_de_corrida``, el
    mismo que ``manifest.json → metricas``).
    Es OPCIONAL en el contrato de L6: si falla se omite con motivo. El ``.txt``
    desaparece. Avisa con ``DeprecationWarning`` una vez por proceso y se retira
    en ``VERSION_RETIRO_ALIAS_V1``.
    """

    @property
    def name(self) -> str:
        return "Auditoría de configuración (alias de v1)"

    @property
    def required_class(self) -> str:
        return "None"  # Siempre disponible

    def _execute_impl(self, ctx: ReportingContext, logger: logging.Logger) -> list[Path]:
        _avisar_auditoria_v1()
        ruta = ctx.output_dir / ALIAS_AUDITORIA
        ruta.write_text(
            json.dumps(contenido_alias_auditoria(ctx), indent=2, ensure_ascii=False, default=str)
            + "\n",
            encoding="utf-8",
        )
        return [ruta]


def contenido_alias_auditoria(ctx: ReportingContext) -> dict[str, Any]:
    """El JSON del alias: ``vease``, el aviso y lo que el manifiesto también trae.

    ``prioridad_fuentes`` es la real si el contexto la trae; si no, la del
    perfil (``parametros_motor``), que es lo único que L6 puede saber.
    """
    from .. import __version__

    prioridad = list(ctx.prioridad_fuentes) if ctx.prioridad_fuentes else None
    return {
        "vease": "manifest.json",
        "aviso": (
            "Alias de v1 (F1.12). La fuente de verdad es manifest.json de la carpeta del "
            "estándar: parametros, tiempos_por_fase, metricas, insumos (huella SHA-256) y "
            f"version. Este alias desaparece en rues-linker {VERSION_RETIRO_ALIAS_V1}."
        ),
        "version": str(__version__),
        "marca_tiempo": datetime.now().isoformat(timespec="seconds"),
        "parametros": parametros_motor(ctx.config, prioridad_fuentes=prioridad).a_dict(),
        "tiempos_por_fase": dict(ctx.phase_times),
        "metricas": dict(ctx.metrics),
    }
