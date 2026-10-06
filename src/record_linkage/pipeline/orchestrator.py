"""
pipeline.orchestrator — record_linkage_pipeline

Componentes:
    - class Orchestrator  (origen: notebook celda [192])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import contextlib
import gc
import json
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import psutil

from ..engine.cannot_link import aplicar_cannot_link_identificador
from ..engine.clusterer import OptimizedClusterer
from ..engine.lsh.disk_based import DiskBasedLSHEngine
from ..engine.lsh.trusted import TrustedSourceLSHEngine
from ..engine.scorer import VectorizedScorer
from ..evaluation.banco import _contar_filas_sqlite
from ..golden.containment import consolidate_groups_by_nit_balanced
from ..golden.generator import ConsumableDataFrame, GoldenRecordGeneratorV7
from ..processing.dtypes import optimizar_dtypes_categoricos
from ..reporting.strategies import (
    BaseReportingStrategy,
    ConfigAuditStrategy,
    DashboardStrategy,
    DataExportStrategy,
    EnhancedInsightsStrategy,
    ExcelReportsStrategy,
    Phase,
    ReportingContext,
    VisualizationsStrategy,
)
from ..utils.almacenamiento import es_ruta_fuse
from ..utils.memory import RSSSampler
from ._internal import _fmt_time, _get_logger, _phase_cleanup, _validate_sources
from ._phase_constants import PHASE_TIMES, PHASES_ORDER
from .fingerprints import fingerprint_sources
from .linkage_pipeline import RecordLinkagePipeline
from .state_manager import StateManager
from .storage import HybridStorageManager


class Orchestrator:
    """
    Orquestador Enterprise v8.5 para Record Linkage.

    VERSIÓN DEFINITIVA que combina:

    ✅ CORRECCIONES LÓGICAS:
       - IS_VALID → NIT_VALID (mapeo correcto de NitProcessor)
       - reset_index() antes de mapear clusters
       - Validación de fuentes al inicio
       - Fallback para columna SRC
       - Manejo de registros sin grupo

    ✅ INTEGRIDAD DE DATOS I/O:
       - _safe_move_sqlite() mueve BD + WAL + SHM juntos
       - _safe_sqlite_checkpoint() fuerza flush de WAL
       - _verify_sqlite_integrity() valida BD después de mover
       - Previene corrupción de base de datos

    ✅ ROBUSTEZ:
       - Validación de fuentes antes de ejecutar
       - Manejo de errores con logging detallado
       - Checkpointing automático por fase
       - Reutilización inteligente de resultados

    ✅ EXTENSIBILIDAD:
       - Estrategias de reporting pluggables
       - Sistema de experimentos
       - Configuración por profiles

    Uso básico:
        orch = Orchestrator(config, sources, "workspace")
        results = orch.run()

    Uso avanzado:
        orch = Orchestrator(config, sources, "workspace")
        orch.add_reporting_strategy(CustomStrategy())
        results = orch.run(experiment="exp_001", skip_reporting=False)
    """

    VERSION = "8.5"

    def __init__(
        self,
        config: dict,
        sources: dict[str, pd.DataFrame],
        work_dir: str,
        *,
        consume_sources: bool = False,
    ):
        """
        Inicializa el orquestador.

        Args:
            config: Configuración completa del pipeline (debe incluir 'profile' y 'profiles')
            sources: Dict de DataFrames por nombre de fuente (ej: {'RUES': df_rues, 'CRM': df_crm})
            work_dir: Directorio de trabajo para checkpoints y resultados
            consume_sources: Si True, el llamador transfiere la propiedad del
                diccionario ``sources``. Tras completar L1 se vacía ese mismo
                diccionario para liberar sus referencias. Default False.
        """
        self.config = config
        self._owned_source_mapping = sources if consume_sources else None
        self._consume_sources = bool(consume_sources)
        self.sources = sources
        self.log = _get_logger()
        self._start_time: float | None = None
        self._phase_times: dict[str, float] = {}
        self._phase_peak_rss_mib: dict[str, float] = {}
        self._sources_released_after_l1 = False

        # ── v0.12.0: mapeo de columnas de usuario → canónicas ──────────────
        # `column_mapping` viene de crear_config_orchestrator (col_name=/
        # col_nit=/col_ciudad=). Renombramos aquí, ANTES de validar y de la
        # firma de datos, para que TODO el pipeline vea columnas canónicas.
        # df.rename comparte los buffers de datos (no copia valores).
        self.sources = self._aplicar_column_mapping(sources, (config or {}).get("column_mapping"))

        # ── v0.12.0: validación fail-fast de extra_features del perfil ─────
        # Reutiliza el validador de deduplicate_unified (import en runtime
        # para no crear ciclo pipeline ↔ deduplication).
        _perfil_activo = (config or {}).get("profiles", {}).get((config or {}).get("profile"), {})
        _extra = _perfil_activo.get("extra_features") or []
        if _extra:
            from ..deduplication.unified import _validate_extra_features

            _cols_union = pd.Index(
                sorted({c for df_src in self.sources.values() for c in df_src.columns})
            )
            _validate_extra_features(_extra, _cols_union)
            self.log.info(
                f"   🧩 extra_features activas: "
                f"{[f['column'] for f in _extra]} (validadas contra las fuentes)"
            )

        # Configurar directorio de trabajo (path absoluto para evitar problemas)
        self.work_dir = Path(work_dir).resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # Crear subdirectorios para cada fase
        self.dirs = {p: self.work_dir / p.value for p in Phase}
        for _phase, dir_path in self.dirs.items():
            dir_path.mkdir(parents=True, exist_ok=True)

        # Directorio de experimentos
        (self.work_dir / "experiments").mkdir(parents=True, exist_ok=True)

        # Huella completa para invalidación de caché. Incluye contenido,
        # esquema, dtypes, nombres/orden de fuentes y orden de filas. Se calcula
        # sobre las fuentes ya mapeadas: representa exactamente lo que L1 ve.
        self.data_sig = fingerprint_sources(self.sources)

        # Gestor de estado para checkpointing
        self.state = StateManager(self.work_dir)
        # ═══════════════════════════════════════════════════════════════════
        # Inicializar HybridStorageManager
        # Usa disco local NVMe para procesamiento SQLite rápido.
        # Solo se activa si /content existe (entorno Colab).
        # En otros entornos, storage será None y se usa work_dir directamente.
        # ═══════════════════════════════════════════════════════════════════
        # v0.14.0: la condición correcta es "¿el work_dir está en Drive?", no
        # "¿existe /content?". Con work_dir ya local (p. ej. /content/trabajo)
        # el gestor híbrido solo añadía una copia local→local inútil; con
        # work_dir en Drive es imprescindible. `forzar_almacenamiento_hibrido`
        # en el perfil permite imponer cualquiera de los dos comportamientos.
        self.storage = None
        forzado = self.profile.get("forzar_almacenamiento_hibrido")
        usar_hibrido = es_ruta_fuse(self.work_dir) if forzado is None else bool(forzado)
        if usar_hibrido and Path("/content").exists():
            try:
                self.storage = HybridStorageManager(self.work_dir)
                self.log.info(
                    f"   💾 HybridStorage activado: procesamiento local en {self.storage.local_dir}"
                )
            except Exception as e:
                self.log.warning(f"   ⚠️ HybridStorage no disponible ({e}), usando Drive directo")
                self.storage = None

        # Cache de datos procesados
        self._df_clean: pd.DataFrame | None = None
        self._pipeline_instance = None

        # (v2.1.0) Set de fases explícitamente forzadas a re-ejecutar.
        # Lo manipula `run()` con el kwarg `force_rerun_phases`.
        # Se inicializa vacío para que `_exec_phase` siempre encuentre
        # el atributo, incluso si alguien llama un método interno sin
        # haber invocado `run()` (escenario de testing/debug).
        self._force_rerun_phases: set[Phase] = set()

        # Estrategias de reporting (orden de ejecución)
        self._reporting_strategies: list[BaseReportingStrategy] = [
            DataExportStrategy(),
            ExcelReportsStrategy(),
            VisualizationsStrategy(),
            DashboardStrategy(),
            EnhancedInsightsStrategy(),
            ConfigAuditStrategy(),
        ]

    def _aplicar_column_mapping(
        self,
        sources: dict[str, pd.DataFrame],
        mapping: dict[str, str] | None,
    ) -> dict[str, pd.DataFrame]:
        """Renombra columnas de usuario a canónicas según ``column_mapping``.

        v0.12.0 (cierre del hallazgo H1 de la auditoría): hasta 0.11.x los
        kwargs col_name/col_nit/col_ciudad de la API se descartaban y el
        pipeline exigía los nombres canónicos cableados.

        Args:
            sources: fuentes originales {nombre: DataFrame}.
            mapping: {columna_canónica: columna_del_usuario} o None.

        Returns:
            Fuentes con columnas canónicas. Si no hay nada que renombrar,
            devuelve exactamente el dict de entrada (cero costo).

        Raises:
            ValueError: si en una fuente conviven la columna canónica y la
                del usuario (mapeo ambiguo: no se adivina cuál manda).
        """
        if not mapping:
            return sources

        renombradas: dict[str, pd.DataFrame] = {}
        aplicado: dict[str, list[str]] = {}
        for nombre, df in sources.items():
            renames: dict[str, str] = {}
            for canonico, usuario in mapping.items():
                if usuario == canonico or usuario not in df.columns:
                    continue
                if canonico in df.columns:
                    raise ValueError(
                        f"Qué pasó: la fuente '{nombre}' tiene la columna "
                        f"'{usuario}' (mapeada a '{canonico}') Y también "
                        f"'{canonico}'. "
                        f"Por qué importa: el pipeline no puede adivinar cuál "
                        f"de las dos es la verdadera sin riesgo de mezclar datos. "
                        f"Qué hacer: elimine o renombre una de las dos columnas "
                        f"en esa fuente antes de llamar al pipeline."
                    )
                renames[usuario] = canonico
            renombradas[nombre] = df.rename(columns=renames) if renames else df
            if renames:
                aplicado[nombre] = [f"{u}→{c}" for u, c in renames.items()]

        if aplicado:
            self.log.info(f"   🔁 Mapeo de columnas aplicado: {aplicado}")
        return renombradas

    # ==========================================================================
    # PROPIEDADES
    # ==========================================================================

    @property
    def pipeline(self):
        """
        Lazy initialization del RecordLinkagePipeline subyacente.

        Se crea solo cuando se necesita, permitiendo que el Orchestrator
        se inicialice sin cargar todas las dependencias.
        """
        if not self._pipeline_instance:
            self._pipeline_instance = RecordLinkagePipeline(
                self.config, profile=self.config.get("profile")
            )
        return self._pipeline_instance

    @property
    def profile(self) -> dict:
        """Acceso rápido al profile activo de la configuración."""
        return self.config.get("profiles", {}).get(self.config.get("profile", ""), {})

    @property
    def output_dir(self) -> Path:
        """Directorio de salida principal (L6_reporting)."""
        return self.dirs[Phase.L6_REPORTING]

    def _release_source_frames(self) -> None:
        """Libera buffers fuente conservando claves, columnas y dtypes."""
        self.sources = {
            # ``iloc[0:0]`` sin copy conserva como ``base`` los bloques
            # originales; la copia vacía rompe esa referencia masiva.
            name: frame.iloc[0:0].copy() if isinstance(frame, pd.DataFrame) else frame
            for name, frame in self.sources.items()
        }
        if self._owned_source_mapping is not None:
            self._owned_source_mapping.clear()
            self._owned_source_mapping = None
        self._sources_released_after_l1 = True
        gc.collect()

    # ==========================================================================
    # MÉTODOS DE INTEGRIDAD DE DATOS (SQLITE)
    # ==========================================================================

    def _safe_sqlite_checkpoint(self, db_path: Path) -> bool:
        """
        Fuerza un checkpoint de WAL en la base de datos SQLite.

        Esto asegura que todos los datos en el WAL se escriban al archivo
        principal de la base de datos, permitiendo un movimiento seguro.

        Args:
            db_path: Path a la base de datos SQLite

        Returns:
            True si el checkpoint fue exitoso
        """
        if not db_path.exists():
            return False

        try:
            conn = sqlite3.connect(str(db_path))
            # TRUNCATE mode: Checkpoints and truncates WAL file
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            self.log.debug(f"   🔄 Checkpoint WAL completado: {db_path.name}")
            return True
        except Exception as e:
            self.log.warning(f"   ⚠️ Checkpoint WAL falló para {db_path.name}: {e}")
            return False

    def _verify_sqlite_integrity(self, db_path: Path) -> bool:
        """
        Verifica la integridad de una base de datos SQLite.

        Args:
            db_path: Path a la base de datos SQLite

        Returns:
            True si la base de datos está íntegra
        """
        if not db_path.exists():
            return False

        try:
            conn = sqlite3.connect(str(db_path))
            result = conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()

            is_ok = result[0] == "ok"
            if not is_ok:
                self.log.error(f"   ❌ Integridad fallida para {db_path.name}: {result[0]}")
            return is_ok

        except Exception as e:
            self.log.error(f"   ❌ Error verificando integridad de {db_path.name}: {e}")
            return False

    def _safe_move_sqlite(self, src: Path, dst: Path) -> bool:
        """
        Mueve una base de datos SQLite y sus archivos auxiliares de forma segura.

        SQLite en modo WAL (Write-Ahead Logging) crea archivos auxiliares:
        - .db-wal: Write-Ahead Log
        - .db-shm: Shared Memory file

        Si se mueve solo el .db sin estos archivos, la base de datos
        puede quedar corrupta o con datos incompletos.

        Este método:
        1. Hace checkpoint del WAL (flush todos los datos al archivo principal)
        2. Mueve el archivo principal
        3. Mueve los archivos auxiliares (si existen)
        4. Verifica la integridad de la BD destino

        Args:
            src: Path origen de la base de datos
            dst: Path destino

        Returns:
            True si el movimiento fue exitoso y la BD está íntegra
        """
        src = Path(src)
        dst = Path(dst)

        if not src.exists():
            self.log.warning(f"   ⚠️ Archivo origen no existe: {src}")
            return False

        # Paso 1: Checkpoint para asegurar que WAL esté sincronizado
        self._safe_sqlite_checkpoint(src)

        # Paso 2: Definir archivos a mover (principal + auxiliares)
        suffixes = ["", "-wal", "-shm"]

        # Paso 3: Limpiar destino si existe
        for suffix in suffixes:
            dst_file = dst.parent / (dst.name + suffix)
            if dst_file.exists():
                dst_file.unlink()
                self.log.debug(f"   🗑️ Eliminado archivo destino existente: {dst_file.name}")

        # Paso 4: Mover archivos
        files_moved = []
        for suffix in suffixes:
            src_file = src.parent / (src.name + suffix)
            dst_file = dst.parent / (dst.name + suffix)

            if src_file.exists():
                try:
                    shutil.move(str(src_file), str(dst_file))
                    files_moved.append(suffix if suffix else "main")
                    self.log.debug(f"   🚚 Movido: {src_file.name} → {dst_file.name}")
                except Exception as e:
                    self.log.error(f"   ❌ Error moviendo {src_file.name}: {e}")
                    return False

        self.log.debug(f"   📦 Archivos movidos: {files_moved}")

        # Paso 5: Verificar integridad del destino
        if not self._verify_sqlite_integrity(dst):
            self.log.error("   ❌ La BD movida falló verificación de integridad")
            return False

        self.log.debug(f"   ✅ BD movida y verificada: {dst.name}")
        return True

    # ==========================================================================
    # MÉTODOS AUXILIARES
    # ==========================================================================

    def _ensure_directories(self) -> None:
        """
        Verifica y crea todos los directorios necesarios.

        Llamado al inicio de run() para garantizar estructura completa.
        """
        self.work_dir.mkdir(parents=True, exist_ok=True)

        for _phase, dir_path in self.dirs.items():
            dir_path.mkdir(parents=True, exist_ok=True)

        (self.work_dir / "experiments").mkdir(parents=True, exist_ok=True)

    # ==========================================================================
    # MÉTODOS PÚBLICOS PRINCIPALES
    # ==========================================================================

    def run(
        self,
        from_phase: Phase = None,
        experiment: str | None = None,
        skip_reporting: bool | None = None,
        force_rerun_phases: set[Phase] | list[Phase] | None = None,
    ) -> dict[str, Any]:
        """
        Ejecuta el pipeline completo de Record Linkage.

        Flujo de ejecución:
        1. Validar fuentes de datos
        2. Ejecutar fases L1-L5 (con checkpointing)
        3. Ejecutar fase L6 (reporting) si no se omite
        4. Guardar experimento si se especifica

        Args:
            from_phase: Fase desde la cual comenzar (invalida posteriores).
                       Útil para re-ejecutar con cambios de configuración.
            experiment: Nombre del experimento para guardar snapshot.
                       Permite comparar diferentes configuraciones.
            skip_reporting: Si ``True``, omite la fase L6.
                Útil para ejecuciones de prueba rápida o para producción
                cuando los reportes no se necesitan (ahorra ~4 min en el
                pipeline calibrado de 1.97M registros).

                **v0.7.1** (Sprint 0.8.1, Tarea 1.3): si se pasa ``None``
                (default), se lee ``profile["skip_reporting"]``; si tampoco
                está, se usa ``False``. Esto permite configurar el comportamiento
                desde el perfil sin tocar el sitio de llamada. Precedencia:
                kwarg explícito > profile > default False.
            force_rerun_phases: (v2.1.0) Set/list de fases específicas a
                forzar re-ejecución incluso si el checkpoint es válido.
                Útil para debugging fino: re-correr solo L3 sin invalidar
                L4-L5 que se reciclan en el siguiente intento.
                Si una fase F está en este set y existen fases posteriores
                con checkpoint válido, esas posteriores se invalidan en
                cascada automáticamente para preservar consistencia.

        Returns:
            Dict con:
            - 'golden': DataFrame de Golden Records
            - 'correlative': DataFrame de Tabla Correlativa
            - 'report_files': Lista de archivos generados (si no se omitió L6)

        Raises:
            ValueError: Si las fuentes son inválidas
            Exception: Si ocurre un error crítico en alguna fase

        Example:
            >>> # Re-correr solo L3 (scoring) tras tunear umbrales:
            >>> orch.run(force_rerun_phases={Phase.L3_SCORING})
            >>> # Equivale a from_phase=L3_SCORING (cascada hacia adelante).
        """
        # v0.7.1 (Tarea 1.3): resolver skip_reporting con precedencia
        #   kwarg explícito > profile["skip_reporting"] > False
        if skip_reporting is None:
            skip_reporting = bool(self.profile.get("skip_reporting", False))

        self._start_time = time.time()
        self._phase_peak_rss_mib = {}
        self._ensure_directories()

        # Normalizar force_rerun_phases a set para lookup O(1)
        self._force_rerun_phases = set(force_rerun_phases or [])

        # Header de inicio
        self.log.info("=" * 70)
        self.log.info(f"🏆 ORCHESTRATOR v{self.VERSION} - ENTERPRISE EDITION")
        self.log.info(f"   📂 Workspace: {self.work_dir}")
        self.log.info(f"   📊 Fuentes: {', '.join(self.sources.keys())}")
        total_records = sum(len(df) for df in self.sources.values())
        self.log.info(f"   📈 Total registros: {total_records:,}")
        self.log.info("=" * 70)

        if self._sources_released_after_l1:
            # El modo opt-in de baja RAM convierte la instancia en consumidora
            # de sus fuentes. Las claves se conservan para la prioridad, pero
            # una segunda corrida solo puede apoyarse en el checkpoint L1.
            self.log.info("♻️ Fuentes ya liberadas; se validará el checkpoint L1")
        else:
            # Validar fuentes antes de comenzar
            self.log.info("🔍 Validando fuentes de datos...")
            es_valido, errores = _validate_sources(self.sources, self.log)
            if not es_valido:
                raise ValueError(f"Fuentes inválidas. Errores: {errores}")
            self.log.info("   ✅ Todas las fuentes validadas correctamente")

            # Los DataFrames se conservan por referencia para no duplicar RAM.
            # La huella se recalcula porque el caller puede mutarlos.
            current_data_sig = fingerprint_sources(self.sources)
            if current_data_sig != self.data_sig:
                self.log.info(
                    "🔄 Las fuentes cambiaron desde la corrida anterior; invalidando caché"
                )
                self.data_sig = current_data_sig
                self._df_clean = None
                self._pipeline_instance = None

        # Invalidar fases si se especifica punto de inicio
        if from_phase:
            self.state.invalidate_from(from_phase)
            self.log.info(f"🔄 Invalidando fases desde {from_phase.value}")

        # (v2.1.0) Invalidar fases explícitamente forzadas + sus posteriores
        # (cascada hacia adelante: si fuerzo L3, debo invalidar L4 y L5 que
        #  dependen de su output).
        if self._force_rerun_phases:
            earliest = min(self._force_rerun_phases, key=lambda p: PHASES_ORDER.index(p))
            self.state.invalidate_from(earliest)
            forced_str = ", ".join(sorted(p.value for p in self._force_rerun_phases))
            self.log.info(
                f"🔧 force_rerun_phases={{{forced_str}}} → invalidando desde {earliest.value}"
            )

        if self._sources_released_after_l1:
            l1_hash = self.state.compute_hash(self.config, Phase.L1_PREP, self.data_sig, "")
            if not self.state.is_valid(Phase.L1_PREP, l1_hash):
                raise RuntimeError(
                    "Las fuentes se liberaron tras L1 y su checkpoint ya no es reutilizable. "
                    "Cree un Orchestrator nuevo con las fuentes originales."
                )

        try:
            prev_hash = ""

            # =====================
            # FASE L1: Preparación
            # =====================
            self._df_clean, prev_hash = self._exec_phase(Phase.L1_PREP, self._run_L1, prev_hash)

            if self._consume_sources or bool(self.profile.get("liberar_fuentes_tras_l1", False)):
                self._release_source_frames()

            # =====================
            # FASE L2: Candidatos LSH
            # =====================
            cand_path, prev_hash = self._exec_phase(
                Phase.L2_LSH_CANDIDATES, self._run_L2, prev_hash, self._df_clean
            )

            # =====================
            # FASE L3: Scoring
            # =====================
            score_path, prev_hash = self._exec_phase(
                Phase.L3_SCORING, self._run_L3, prev_hash, self._df_clean, cand_path
            )

            # =====================
            # FASE L4: Clustering
            # =====================
            clusters, prev_hash = self._exec_phase(
                Phase.L4_CLUSTERING, self._run_L4, prev_hash, self._df_clean, score_path
            )

            # =====================
            # FASE L5: Golden Records
            # =====================
            if self._df_clean is None:
                raise RuntimeError("L5 no recibió el consolidado producido por L1")
            entrada_l5 = ConsumableDataFrame(self._df_clean)
            self._df_clean = None
            try:
                results_data, prev_hash = self._exec_phase(
                    Phase.L5_GOLDEN,
                    self._run_L5,
                    prev_hash,
                    entrada_l5,
                    clusters,
                )
            finally:
                # También cubre reutilización de checkpoint y excepciones antes
                # de que _run_L5 llegue a consumir la entrada.
                entrada_l5.release()
                gc.collect()

            # =====================
            # FASE L6: Reporting
            # =====================
            if not skip_reporting:
                report_files, prev_hash = self._exec_phase(
                    Phase.L6_REPORTING, self._run_L6, prev_hash, results_data
                )
                results_data["report_files"] = report_files
            else:
                self.log.info("⏭️ Fase L6_REPORTING omitida (skip_reporting=True)")

            # Guardar experimento si se especificó
            if experiment:
                self._save_experiment(experiment, results_data)

            # Resumen final
            total_time = time.time() - self._start_time
            self.log.info("=" * 70)
            self.log.info("🏁 PROCESO FINALIZADO EXITOSAMENTE")
            self.log.info(f"   ⏱️ Tiempo total: {_fmt_time(total_time)}")
            self.log.info(f"   📊 Golden Records: {len(results_data['golden']):,}")
            self.log.info(f"   📊 Tabla Correlativa: {len(results_data['correlative']):,}")

            # Calcular tasa de reducción
            if len(results_data["correlative"]) > 0:
                reduction = 1 - (len(results_data["golden"]) / len(results_data["correlative"]))
                self.log.info(f"   📉 Tasa de reducción: {reduction:.2%}")

            self.log.info(f"   📂 Resultados en: {self.output_dir}")
            self.log.info("=" * 70)

            return results_data

        except Exception as e:
            self.log.error(f"❌ ERROR CRÍTICO: {type(e).__name__}: {e}")
            import traceback

            self.log.error(traceback.format_exc())
            raise

    def estimate(self, from_phase: Phase = None) -> dict[str, Any]:
        """
        Estima tiempo de ejecución y muestra estado de fases.

        Útil para planificar ejecuciones y verificar qué fases están
        guardadas en caché.

        Args:
            from_phase: Fase desde la cual comenzar (para simulación)

        Returns:
            Dict con:
            - 'pending': Lista de fases pendientes
            - 'saved': Lista de fases guardadas en caché
            - 'estimated_min': Tiempo estimado en minutos
            - 'saved_min': Tiempo ahorrado por caché en minutos
            - 'total_phases': Número total de fases
            - 'phases_done': Número de fases completadas
        """
        start_idx = PHASES_ORDER.index(from_phase) if from_phase else 0
        pending, saved = [], []

        prev_hash = ""
        for i, p in enumerate(PHASES_ORDER):
            # Encadenar los hashes esperados de la configuración actual. Usar
            # get_prev_hash() mezclaba el manifest anterior con el run nuevo.
            ph_hash = self.state.compute_hash(self.config, p, self.data_sig, prev_hash)
            prev_hash = ph_hash

            forced_by_from_phase = from_phase is not None and i >= start_idx
            if not forced_by_from_phase and self.state.is_valid(p, ph_hash):
                saved.append(p)
            else:
                pending.append(p)

        return {
            "pending": [p.value for p in pending],
            "saved": [p.value for p in saved],
            "estimated_min": sum(PHASE_TIMES[p] for p in pending),
            "saved_min": sum(PHASE_TIMES[p] for p in saved),
            "total_phases": len(PHASES_ORDER),
            "phases_done": len(saved),
        }

    def status(self) -> dict[str, dict[str, Any]]:
        """
        Retorna estado actual de todas las fases.

        Returns:
            Dict con estado de cada fase:
            - 'done': Si la fase está completada
            - 'hash': Hash de configuración (primeros 6 chars)
            - 'timestamp': Cuándo se completó
            - 'duration': Duración en segundos
        """
        return {
            p.value: {
                "done": self.state.manifest.get(p.value, {}).get("status") == "DONE",
                "hash": self.state.manifest.get(p.value, {}).get("hash", "-")[:6],
                "timestamp": self.state.manifest.get(p.value, {}).get("timestamp", "-"),
                "duration": self.state.manifest.get(p.value, {}).get("meta", {}).get("duration", 0),
                "peak_rss_mib": self.state.manifest.get(p.value, {})
                .get("meta", {})
                .get("peak_rss_mib", 0),
            }
            for p in Phase
        }

    def export_reports(self, results: dict | None = None) -> list[Path]:
        """
        Genera reportes manualmente.

        Útil cuando se ejecutó con skip_reporting=True y luego se quieren
        generar los reportes sin re-ejecutar todo el pipeline.

        Args:
            results: Dict con 'golden' y 'correlative'.
                    Si None, carga de los archivos de L5.

        Returns:
            Lista de archivos generados
        """
        if results is None:
            gold_path = self.dirs[Phase.L5_GOLDEN] / "golden.parquet"
            corr_path = self.dirs[Phase.L5_GOLDEN] / "correlative.parquet"

            if not gold_path.exists():
                raise ValueError(
                    "No hay resultados previos. Ejecute run() primero o "
                    "proporcione el parámetro 'results'."
                )

            results = {
                "golden": pd.read_parquet(gold_path),
                "correlative": pd.read_parquet(corr_path),
            }

        files, _ = self._run_L6(results)
        return files

    def add_reporting_strategy(self, strategy: BaseReportingStrategy) -> None:
        """
        Agrega una estrategia de reporting personalizada.

        Permite extender la generación de reportes sin modificar el
        código del Orchestrator (Open/Closed Principle).

        Args:
            strategy: Instancia de clase que hereda de BaseReportingStrategy
        """
        self._reporting_strategies.append(strategy)
        self.log.info(f"   ➕ Estrategia agregada: {strategy.name}")

    # ==========================================================================
    # EJECUCIÓN DE FASES
    # ==========================================================================

    def _exec_phase(self, phase: Phase, func, prev_hash: str, *args) -> tuple[Any, str]:
        """
        Ejecuta una fase con validación de hash y checkpointing.

        Implementa el patrón Template Method:
        1. Calcular hash de configuración
        2. Verificar si se puede reutilizar resultado anterior
        3. Si no, ejecutar la fase
        4. Guardar estado y resultado

        Args:
            phase: Fase a ejecutar
            func: Función que implementa la fase
            prev_hash: Hash de la fase anterior
            *args: Argumentos adicionales para la función

        Returns:
            Tupla (resultado, hash_de_esta_fase)
        """
        # Calcular hash de esta fase
        ph_hash = self.state.compute_hash(self.config, phase, self.data_sig, prev_hash)

        # (v2.1.0) Si esta fase está en force_rerun_phases, NO reutilizar
        # el checkpoint aunque el hash sea válido. La invalidación en cascada
        # hacia adelante ya se aplicó en run(); aquí solo blindamos contra
        # el caso edge de llamadas directas a _exec_phase.
        forced = phase in self._force_rerun_phases

        # Verificar si podemos reutilizar resultado anterior
        if not forced and self.state.is_valid(phase, ph_hash):
            self.log.info(
                f"♻️  {phase.value}: REUTILIZANDO resultado guardado (hash: {ph_hash[:6]})"
            )
            meta = self.state.manifest.get(phase.value, {}).get("meta", {})
            peak = meta.get("peak_rss_mib")
            if isinstance(peak, (int, float)):
                self._phase_peak_rss_mib[phase.value] = float(peak)
            return self._load_phase_result(phase), ph_hash

        # Ejecutar fase
        prefix = "🔧 (forzada)" if forced else "▶️ "
        self.log.info(f"{prefix} {phase.value}: Ejecutando...")
        t0 = time.time()
        sampler = RSSSampler()
        phase_error: BaseException | None = None
        try:
            sampler.start()
            with _phase_cleanup():
                result, files = func(*args)
        except BaseException as exc:
            phase_error = exc
            raise
        finally:
            try:
                # El finally anidado garantiza join incluso si stop cambiara o
                # fallara: ninguna fase deja un hilo de telemetría huérfano.
                try:
                    sampler.stop()
                finally:
                    sampler.join()
            except Exception as sampler_error:
                if phase_error is None:
                    raise
                self.log.warning(
                    f"   ⚠️ No se pudo cerrar limpiamente el sampler RSS: {sampler_error}"
                )
            finally:
                self._phase_peak_rss_mib[phase.value] = sampler.peak_mib

        duration = time.time() - t0
        self._phase_times[phase.value] = duration

        # Persistir estado
        self.state.mark_done(
            phase,
            ph_hash,
            files,
            {
                "duration": duration,
                "peak_rss_mib": round(self._phase_peak_rss_mib[phase.value], 3),
            },
        )
        self.log.info(
            f"✅ {phase.value}: Completado en {_fmt_time(duration)} (hash: {ph_hash[:6]})"
        )

        return result, ph_hash

    def _load_phase_result(self, phase: Phase) -> Any:
        """
        Carga resultado de una fase desde disco.

        Cada fase tiene su propio formato de almacenamiento:
        - L1: Parquet (DataFrame)
        - L2, L3: SQLite (Path)
        - L4: Parquet (dict)
        - L5: Parquet x2 (dict con 'golden' y 'correlative')
        - L6: Lista de Paths

        Args:
            phase: Fase de la cual cargar resultado

        Returns:
            Resultado en el formato apropiado para la fase
        """
        files = self.state.manifest[phase.value]["files"]

        if phase == Phase.L1_PREP:
            return pd.read_parquet(files[0])

        elif phase in [Phase.L2_LSH_CANDIDATES, Phase.L3_SCORING]:
            return Path(files[0])

        elif phase == Phase.L4_CLUSTERING:
            df = pd.read_parquet(files[0])
            return dict(zip(df["record_id"], df["ID_GRUPO"], strict=False))

        elif phase == Phase.L5_GOLDEN:
            return {"golden": pd.read_parquet(files[0]), "correlative": pd.read_parquet(files[1])}

        elif phase == Phase.L6_REPORTING:
            return [Path(f) for f in files]

        return None

    # ==========================================================================
    # IMPLEMENTACIÓN DE FASES
    # ==========================================================================

    def _run_L1(self) -> tuple[pd.DataFrame, list[Path]]:
        """
        L1: Preprocesamiento de datos.

        Pasos:
        1. Cargar y consolidar fuentes
        2. Limpiar nombres (NOMBRE_LIMPIO)
        3. Procesar NITs (NIT_OK, NIT_BASE, NIT_VALID)
        4. Agregar columna SRC si no existe
        5. Guardar resultado en Parquet

        Returns:
            Tupla (DataFrame procesado, [Path al archivo guardado])
        """
        phase_dir = self.dirs[Phase.L1_PREP]
        outfile = phase_dir / "data.parquet"

        self.log.info(f"   📁 Directorio: {phase_dir}")

        # Cargar y consolidar fuentes
        self.log.info("   📥 Cargando fuentes...")
        loaded, load_report = self.pipeline.data_handler.load_sources(self.sources)

        if load_report.get("errors"):
            for err in load_report["errors"]:
                self.log.warning(f"   ⚠️ {err}")

        self.log.info("   🔄 Consolidando fuentes...")
        df = self.pipeline.data_handler.consolidate_sources(loaded)
        self.log.info(f"   📊 Registros consolidados: {len(df):,}")

        # Limpiar nombres
        self.log.info("   🧹 Limpiando nombres...")
        df["NOMBRE_LIMPIO"] = self.pipeline.text_processor.process_series(df["RAZON_SOCIAL"])
        # v0.17.1 — firma de bloqueo separada del nombre de decisión. El LSH
        # la usa para que los tokens genéricos no llenen los buckets; el
        # scorer sigue viendo NOMBRE_LIMPIO íntegro. Derivar cuesta un filtro
        # de tokens cacheado por valor único, no una segunda limpieza.
        df["NOMBRE_BLOQUEO"] = self.pipeline.text_processor.derivar_nombre_bloqueo(
            df["NOMBRE_LIMPIO"]
        )

        # Procesar NITs
        self.log.info("   🔢 Procesando NITs...")
        nit_res = self.pipeline.nit_processor.process_series(df["NIT"])

        # ✅ CORRECCIÓN CRÍTICA: Mapear columnas correctamente
        # NitProcessor retorna IS_VALID, no NIT_VALID
        nit_columns_map = {
            "NIT_OK": "NIT_OK",
            "NIT_BASE": "NIT_BASE",
            "IS_VALID": "NIT_VALID",  # Mapeo correcto
        }

        for src_col, dst_col in nit_columns_map.items():
            if src_col in nit_res.columns:
                df[dst_col] = nit_res[src_col]

        # Fallback si NIT_OK no se generó
        if "NIT_OK" not in df.columns:
            df["NIT_OK"] = df["NIT"].astype(str).str.strip()
            self.log.warning("   ⚠️ NIT_OK no generado por NitProcessor, usando NIT original")

        # ✅ CORRECCIÓN: Asegurar columna SRC existe
        if "SRC" not in df.columns:
            if "FUENTE" in df.columns:
                df["SRC"] = df["FUENTE"]
                self.log.debug("   📝 Columna SRC creada desde FUENTE")
            else:
                self.log.warning("   ⚠️ Columna SRC no encontrada, algunas funciones pueden fallar")

        # Optimización de RAM: columnas de texto pesadas a string[pyarrow]
        # (centralizado en processing.dtypes). A escala 2M en Colab Free, el
        # backend Arrow reduce ~30-60% la RAM de estas columnas frente a object.
        from ..processing.dtypes import optimizar_dtypes_texto

        optimizar_dtypes_texto(df, ["NOMBRE_LIMPIO", "NOMBRE_BLOQUEO", "NIT_OK", "CIUDAD", "SRC"])

        # Paso 1.4: Clave fonética eliminada.
        # NOMBRE_LIMPIO.str[:10] NO es una clave fonética real.
        # Redistribuir peso a name + nit (ver Cambio B).
        # Se mantiene la columna con valor vacío para no romper downstream.
        df["PHONETIC_KEY1"] = ""

        # Los procesadores pueden haber creado otras columnas ``object`` con
        # millones de strings Python. Compactarlas todas a Arrow en L1 evita
        # que ese piso de memoria acompañe las fases L2-L5.
        for column in df.columns:
            if df[column].dtype == object:
                try:
                    df[column] = df[column].astype("string[pyarrow]")
                except (ImportError, TypeError, ValueError):
                    continue
        gc.collect()
        with contextlib.suppress(Exception):
            import ctypes

            ctypes.CDLL("libc.so.6").malloc_trim(0)

        # ── v0.13.0 (T1 dian-comercio): dtypes categóricos OPT-IN ──────────
        # Ahorro medido ~80% en columnas de baja cardinalidad (SRC, CIUDAD,
        # FUENTE). Desactivado por defecto: no cambia comportamiento salvo que
        # el perfil declare `use_categorical_dtypes: True`.
        if bool(self.profile.get("use_categorical_dtypes", False)):
            optimizar_dtypes_categoricos(df, ["SRC", "CIUDAD", "FUENTE"])

        # Guardar resultado
        df.to_parquet(outfile, index=False)
        self.log.info(f"   💾 Guardado: {outfile.name} ({len(df):,} registros)")

        return df, [outfile]

    # ══════════════════════════════════════════════════════════════════════════════
    # MÉTODO AUXILIAR - Agregar a la clase Orchestrator
    # ══════════════════════════════════════════════════════════════════════════════

    def _wait_for_file_stable(
        self, filepath: Path, timeout: int = 60, stability_seconds: int = 3
    ) -> bool:
        """
        Espera a que un archivo aparezca Y se estabilice en Google Drive.

        Args:
            filepath: Ruta al archivo a esperar
            timeout: Tiempo máximo de espera total en segundos
            stability_seconds: Segundos que el tamaño debe permanecer constante

        Returns:
            True si el archivo existe y está estable, False si timeout
        """
        filepath = Path(filepath)
        start_time = time.time()
        check_interval = 2
        last_size = -1
        stable_count = 0
        required_stable = max(1, stability_seconds // check_interval)

        while time.time() - start_time < timeout:
            if filepath.exists():
                try:
                    current_size = filepath.stat().st_size

                    if current_size > 0 and current_size == last_size:
                        stable_count += 1
                        if stable_count >= required_stable:
                            return True  # Archivo estable
                    else:
                        stable_count = 0
                        if current_size != last_size and last_size > 0:
                            self.log.info(
                                f"   ⏳ Archivo sincronizando: {current_size / 1024 / 1024:.1f} MB..."
                            )

                    last_size = current_size

                except OSError:
                    stable_count = 0
            else:
                elapsed = int(time.time() - start_time)
                if elapsed % 10 == 0 and elapsed > 0:
                    self.log.info(f"   ⏳ Esperando archivo ({elapsed}s)...")
                stable_count = 0

            time.sleep(check_interval)

        return False

    # ══════════════════════════════════════════════════════════════════════════════
    # MÉTODO PRINCIPAL CORREGIDO - Reemplazar _run_L2 completo
    # ══════════════════════════════════════════════════════════════════════════════

    def _run_L2(self, df: pd.DataFrame) -> tuple[Path, list[Path]]:
        """
        L2: Generación de candidatos LSH — VERSIÓN OPTIMIZADA.

        Tres mejoras respecto a la versión original:
        ─────────────────────────────────────────────
        1. HybridStorage: Procesa TODO en disco local NVMe (/content/temp_work).
           Firmas MinHash, índice LSH y candidates.db se escriben en local.
           Ahorro: ~1-2h por eliminación de latencia de red en SQLite.

        2. TrustedSourceLSHEngine: Bloquea comparaciones intra-RUES e
           intra-SUPERSOCIEDADES durante generación de candidatos.
           Ahorro: >93% de candidatos eliminados → L3 mucho más rápido.

        3. Mapeo SRC→FUENTE: Garantiza compatibilidad con el motor LSH
           que internamente busca columna 'FUENTE'.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame limpio de L1 con columnas: NOMBRE_LIMPIO, NIT_OK,
            NIT_BASE, NIT_VALID, SRC, PHONETIC_KEY1.

        Returns
        -------
        tuple[Path, list[Path]]
            (Ruta a candidates.db en Drive, [ruta]).

        Raises
        ------
        RuntimeError
            Si no se genera archivo de candidatos.
        """
        phase_dir = self.dirs[Phase.L2_LSH_CANDIDATES]
        outfile = phase_dir / "candidates.db"

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 1: Directorio de trabajo (local NVMe vs Drive)
        # ═══════════════════════════════════════════════════════════════════════
        use_local = False
        if self.storage and self.storage.is_local_available():
            work_dir = self.storage.local_dir / "lsh_work"
            work_dir.mkdir(parents=True, exist_ok=True)
            self.log.info(f"   💾 L2 procesará en disco LOCAL: {work_dir}")
            use_local = True
        else:
            work_dir = phase_dir
            self.log.info("   📂 L2 procesará en Drive directamente")

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 2: Limpieza inteligente de archivos previos
        # ═══════════════════════════════════════════════════════════════════════
        checkpoint_file = phase_dir / "checkpoint.json"
        should_clean = True

        # Solo preservar si BD + checkpoint válidos (recuperación)
        if outfile.exists() and checkpoint_file.exists():
            try:
                conn_test = sqlite3.connect(str(outfile))
                conn_test.execute("SELECT count(*) FROM sqlite_master")
                conn_test.close()
                import json

                with open(checkpoint_file) as f:
                    json.load(f)
                self.log.info("   ♻️ Estado previo válido. Preservando para reanudación.")
                should_clean = False
            except Exception as e:
                self.log.warning(f"   🗑️ Archivos previos corruptos ({e}). Limpiando.")

        if should_clean:
            for suffix in ["", "-wal", "-shm", "-journal"]:
                for base_dir in [phase_dir, work_dir]:
                    p = base_dir / ("candidates.db" + suffix)
                    if p.exists():
                        with contextlib.suppress(OSError):
                            p.unlink()
            # Limpiar cache LSH local si existe
            cache_dir = work_dir / "lsh_disk_cache"
            if cache_dir.exists():
                shutil.rmtree(cache_dir, ignore_errors=True)
            if checkpoint_file.exists():
                with contextlib.suppress(OSError):
                    checkpoint_file.unlink()

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 3: Crear motor LSH (TrustedSource si hay fuentes trusted)
        # ═══════════════════════════════════════════════════════════════════════
        gc.collect()

        trusted_cfg = set(self.profile.get("trusted_unique_sources", []))
        cross_source_cfg = self.profile.get("cross_source_only", False)

        engine: TrustedSourceLSHEngine | DiskBasedLSHEngine
        if trusted_cfg:
            engine = TrustedSourceLSHEngine(self.profile, self.config, trusted_sources=trusted_cfg)
            self.log.info(f"   🛡️ Motor: TrustedSourceLSHEngine (trusted={sorted(trusted_cfg)})")
        else:
            engine = DiskBasedLSHEngine(self.profile, self.config)
            self.log.info("   🔧 Motor: DiskBasedLSHEngine (sin trusted sources)")

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 4: Preparar DataFrame (garantizar FUENTE)
        # ═══════════════════════════════════════════════════════════════════════
        if "FUENTE" not in df.columns and "SRC" in df.columns:
            df = df.copy()
            df["FUENTE"] = df["SRC"]
            self.log.info("   📝 Columna FUENTE creada desde SRC")

        self.log.info(f"   📊 Registros de entrada: {len(df):,}")

        # Reporte de composición por fuente
        if "FUENTE" in df.columns:
            for src, cnt in df["FUENTE"].value_counts().items():
                tag = " 🛡️" if src in trusted_cfg else ""
                self.log.info(f"      • {src}: {cnt:,}{tag}")

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 5: Ejecutar motor LSH (en disco local si disponible)
        # ═══════════════════════════════════════════════════════════════════════
        result = engine.find_candidates(
            df, output_dir=str(work_dir), cross_source_only=bool(trusted_cfg) or cross_source_cfg
        )

        # Métricas
        candidates_reported = 0
        if hasattr(engine, "metrics") and hasattr(engine.metrics, "candidates_found"):
            candidates_reported = engine.metrics.candidates_found

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 6: Mover resultado a Drive (ANTES de cleanup)
        # ═══════════════════════════════════════════════════════════════════════
        archivo_ok = False

        if isinstance(result, str | Path):
            result_path = Path(result)

            if result_path != outfile:
                # Esperar archivo si es necesario
                if not result_path.exists() and hasattr(self, "_wait_for_file_stable"):
                    self.log.info("   ⏳ Esperando sincronización...")
                    self._wait_for_file_stable(result_path, timeout=60)

                if result_path.exists():
                    self.log.info("   🚚 Moviendo BD al destino final...")
                    try:
                        if use_local:
                            # Desde local a Drive: copiar (no mover, para que cleanup funcione)
                            shutil.copy2(str(result_path), str(outfile))
                            size_mb = result_path.stat().st_size / (1024 * 1024)
                            self.log.info(f"   📤 Copiado local → Drive ({size_mb:.1f} MB)")
                        else:
                            shutil.move(str(result_path), str(outfile))

                        archivo_ok = True

                        # Archivos auxiliares SQLite
                        for suffix in ["-wal", "-shm", "-journal"]:
                            aux_src = result_path.parent / (result_path.name + suffix)
                            aux_dst = outfile.parent / (outfile.name + suffix)
                            if aux_src.exists():
                                with contextlib.suppress(OSError):
                                    shutil.copy2(str(aux_src), str(aux_dst))

                    except Exception as e:
                        self.log.error(f"   ❌ Error moviendo: {e}")
                else:
                    self.log.error(f"   ❌ Archivo no encontrado en: {result_path}")
            else:
                archivo_ok = result_path.exists()

        elif hasattr(result, "__iter__"):
            # Resultado en memoria (set de pares) — guardar a disco
            self.log.info("   💾 Guardando candidatos a disco...")
            conn = sqlite3.connect(str(outfile))
            import pandas as pd

            pd.DataFrame(list(result), columns=["idx_0", "idx_1"]).to_sql(
                "candidate_pairs", conn, index=False, if_exists="replace"
            )
            conn.close()
            archivo_ok = True

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 7: Cleanup del motor + disco local
        # ═══════════════════════════════════════════════════════════════════════
        if hasattr(engine, "cleanup"):
            with contextlib.suppress(Exception):
                engine.cleanup(force=True)

        # Limpiar disco local NVMe (liberar espacio para L3)
        if use_local:
            lsh_cache = work_dir / "lsh_disk_cache"
            if lsh_cache.exists():
                shutil.rmtree(lsh_cache, ignore_errors=True)
                self.log.info("   🧹 Cache LSH local limpiado")

        del engine
        gc.collect()

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 8: Verificación final (Trust but Verify)
        # ═══════════════════════════════════════════════════════════════════════
        if archivo_ok and not outfile.exists():
            self.log.info("   ⏳ Verificando sincronización final...")
            if hasattr(self, "_wait_for_file_stable"):
                self._wait_for_file_stable(outfile, timeout=30)

        if outfile.exists():
            size_mb = outfile.stat().st_size / (1024 * 1024)
            self.log.info(f"   💾 Candidatos: {outfile.name} ({size_mb:.1f} MB)")

            try:
                conn = sqlite3.connect(str(outfile))
                count = conn.execute("SELECT COUNT(*) FROM candidate_pairs").fetchone()[0]
                conn.close()
                self.log.info(f"   ✅ Verificación OK: {count:,} pares")

                if candidates_reported > 0 and abs(count - candidates_reported) > 100:
                    self.log.warning(
                        f"   ⚠️ Discrepancia: motor={candidates_reported:,}, BD={count:,}"
                    )
            except Exception as e:
                self.log.warning(f"   ⚠️ No se pudo verificar integridad: {e}")
        else:
            self.log.error(f"   ❌ Archivo no encontrado: {outfile}")
            if phase_dir.exists():
                self.log.error(f"   📋 Contenido de {phase_dir.name}/:")
                for item in phase_dir.rglob("*"):
                    if item.is_file():
                        try:
                            sz = item.stat().st_size / (1024 * 1024)
                            self.log.error(f"      - {item.relative_to(phase_dir)}: {sz:.1f} MB")
                        except Exception:
                            pass
            raise RuntimeError(
                f"No se generó archivo de candidatos: {outfile}\n"
                f"Motor reportó {candidates_reported:,} candidatos.\n"
                f"Verifique: {phase_dir}"
            )

        return outfile, [outfile]

    # ════════════════════════════════════════════════════════════════════
    # MÉTODO LEGACY — preservado para auditoría, NO se invoca en producción.
    # La versión activa es _run_L2 (más arriba), que incluye HybridStorage
    # y selección automática entre DiskBasedLSHEngine y TrustedSourceLSHEngine.
    # Eliminar tras 2 ciclos completos de producción exitosos.
    # ════════════════════════════════════════════════════════════════════
    def _run_L2_legacy(self, df: pd.DataFrame) -> tuple[Path, list[Path]]:
        """
        L2: Generación de candidatos LSH.

        VERSIÓN CORREGIDA: Mueve el archivo ANTES de llamar cleanup().

        Returns:
            Tupla (Path a la BD de candidatos, [Path])
        """
        import sqlite3  # Fix: evitar UnboundLocalError por import condicional en línea posterior

        phase_dir = self.dirs[Phase.L2_LSH_CANDIDATES]
        outfile = phase_dir / "candidates.db"
        phase_dir / "lsh_disk_cache"

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 1: Limpieza de archivos previos (ANTERIOR)
        # ═══════════════════════════════════════════════════════════════════════
        # for suffix in ['', '-wal', '-shm', '-journal']:
        #     p = phase_dir / (outfile.name + suffix)
        #     if p.exists():
        #         try:
        #             p.unlink()
        #             self.log.debug(f"   🗑️ Eliminado: {p.name}")
        #         except OSError:
        #             pass

        # self.log.info(f"   📊 Registros de entrada: {len(df):,}")

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 1: Verificación Inteligente + Checkpoint (ROBUSTO)
        # ═══════════════════════════════════════════════════════════════════════

        # Ruta al archivo de checkpoint que acompaña a la BD
        checkpoint_file = phase_dir / "checkpoint.json"

        should_delete = True

        # Solo intentamos salvar si existen AMBOS: la BD y el Checkpoint
        if outfile.exists() and checkpoint_file.exists():
            try:
                import sqlite3

                # 1. Verificar integridad básica de SQLite
                conn = sqlite3.connect(str(outfile))
                conn.execute("SELECT count(*) FROM sqlite_master")  # Query ligera
                conn.close()

                # 2. Verificar que el checkpoint sea JSON válido
                import json

                with open(checkpoint_file) as f:
                    json.load(f)

                self.log.info(
                    "   ♻️ Estado previo válido detectado (DB + Checkpoint). NO SE BORRARÁ."
                )
                should_delete = False

            except Exception as e:
                self.log.warning(
                    f"   🗑️ Archivos existentes corruptos o incompletos ({e}). Se eliminarán."
                )
                should_delete = True
        else:
            # Si falta alguno, es mejor borrar para evitar estados zombies
            should_delete = True

        if should_delete:
            # (El bloque de borrado original sigue aquí)
            for suffix in ["", "-wal", "-shm", "-journal"]:
                phase_dir / (outfile.name + suffix)
                # ... resto del código de borrado ...
            if checkpoint_file.exists():
                with contextlib.suppress(BaseException):
                    checkpoint_file.unlink()

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 2: Ejecutar motor LSH
        # ═══════════════════════════════════════════════════════════════════════
        gc.collect()

        engine = DiskBasedLSHEngine(self.profile, self.config)
        result = engine.find_candidates(
            df,
            output_dir=str(phase_dir),
            cross_source_only=self.profile.get("cross_source_only", False),
        )

        # Guardar métricas ANTES de cualquier otra operación
        candidates_reported = 0
        if hasattr(engine, "metrics") and hasattr(engine.metrics, "candidates_found"):
            candidates_reported = engine.metrics.candidates_found

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 3: MOVER ARCHIVO PRIMERO (antes de cleanup!)
        # ═══════════════════════════════════════════════════════════════════════
        # ⚠️ CRÍTICO: Mover el archivo ANTES de llamar cleanup()
        #    porque cleanup() ELIMINA candidates.db

        archivo_movido = False

        if isinstance(result, str | Path):
            result_path = Path(result)

            if result_path != outfile:
                # Esperar a que el archivo sea visible en Drive
                if not result_path.exists():
                    self.log.info("   ⏳ Esperando sincronización de Drive...")
                    if self._wait_for_file_stable(result_path, timeout=60):
                        self.log.info("   ✅ Archivo detectado")

                # MOVER INMEDIATAMENTE si existe
                if result_path.exists():
                    self.log.info("   🚚 Moviendo BD al destino final...")
                    try:
                        shutil.move(str(result_path), str(outfile))
                        archivo_movido = True

                        # Mover archivos auxiliares
                        for suffix in ["-wal", "-shm", "-journal"]:
                            aux_src = result_path.parent / (result_path.name + suffix)
                            aux_dst = outfile.parent / (outfile.name + suffix)
                            if aux_src.exists():
                                with contextlib.suppress(OSError):
                                    shutil.move(str(aux_src), str(aux_dst))

                        self.log.info("   ✅ Archivo movido exitosamente")

                    except Exception as e:
                        self.log.error(f"   ❌ Error moviendo: {e}")
            else:
                # result_path == outfile, ya está en el lugar correcto
                archivo_movido = result_path.exists()

        elif hasattr(result, "__iter__"):
            # Resultado en memoria
            self.log.info("   💾 Guardando candidatos a disco...")
            conn = sqlite3.connect(str(outfile))
            pd.DataFrame(list(result), columns=["idx_0", "idx_1"]).to_sql(
                "candidate_pairs", conn, index=False, if_exists="replace"
            )
            conn.close()
            archivo_movido = True

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 4: AHORA sí limpiar el engine (el archivo ya fue movido)
        # ═══════════════════════════════════════════════════════════════════════
        # El cleanup ya no encontrará candidates.db para eliminar

        if hasattr(engine, "cleanup"):
            with contextlib.suppress(Exception):
                engine.cleanup(force=True)

        del engine
        gc.collect()

        # ═══════════════════════════════════════════════════════════════════════
        # PASO 5: Verificación final
        # ═══════════════════════════════════════════════════════════════════════

        # Esperar sincronización si se acaba de mover
        if archivo_movido and not outfile.exists():
            self.log.info("   ⏳ Verificando sincronización final...")
            self._wait_for_file_stable(outfile, timeout=30)

        # Verificar resultado
        if outfile.exists():
            size_mb = outfile.stat().st_size / (1024 * 1024)
            self.log.info(f"   💾 Candidatos: {outfile.name} ({size_mb:.1f} MB)")

            # Verificar integridad
            try:
                conn = sqlite3.connect(str(outfile))
                cursor = conn.cursor()
                cursor.execute("PRAGMA integrity_check").fetchone()[0]
                count = cursor.execute("SELECT COUNT(*) FROM candidate_pairs").fetchone()[0]
                conn.close()

                self.log.info(f"   ✅ Verificación OK: {count:,} pares")

                if candidates_reported > 0 and abs(count - candidates_reported) > 100:
                    self.log.warning(
                        f"   ⚠️ Discrepancia: motor={candidates_reported:,}, BD={count:,}"
                    )

            except Exception as e:
                self.log.warning(f"   ⚠️ No se pudo verificar: {e}")
        else:
            # Diagnóstico detallado
            self.log.error(f"   ❌ Archivo no encontrado: {outfile}")
            self.log.error(f"   📊 Motor reportó: {candidates_reported:,} candidatos")

            # Listar contenido
            if phase_dir.exists():
                self.log.error(f"   📋 Contenido de {phase_dir.name}/:")
                for item in phase_dir.rglob("*"):
                    if item.is_file():
                        try:
                            size = item.stat().st_size / (1024 * 1024)
                            self.log.error(f"      - {item.relative_to(phase_dir)}: {size:.1f} MB")
                        except:
                            pass

            raise RuntimeError(
                f"No se generó archivo de candidatos: {outfile}\n"
                f"Motor reportó {candidates_reported:,} candidatos.\n"
                f"Verifique: {phase_dir}"
            )

        return outfile, [outfile]

    def _run_L3(self, df: pd.DataFrame, cand_path: Path) -> tuple[Path, list[Path]]:
        """
        L3: Scoring de pares candidatos.

        Calcula similitud detallada para cada par candidato usando
        VectorizedScorer.

        FASE 2 - Paso 2.1: Si HybridStorage está disponible, procesa SQLite
        en disco local NVMe para evitar latencia de red con Google Drive.

        Returns:
            Tupla (Path a la BD de scores, [Path])
        """
        phase_dir = self.dirs[Phase.L3_SCORING]
        outfile = phase_dir / "scored.db"

        # Limpiar archivo previo
        if outfile.exists():
            outfile.unlink()

        self.log.info(f"   📊 Scoring desde: {cand_path.name}")

        # ═══════════════════════════════════════════════════════════════════
        # Procesamiento en disco local si disponible
        # ═══════════════════════════════════════════════════════════════════
        if self.storage:
            # Pull: candidatos de Drive → local
            import shutil

            local_cand = self.storage.local_path("candidates.db")
            shutil.copy2(str(cand_path), str(local_cand))
            local_scored = self.storage.local_path("scored.db")
            if local_scored.exists():
                local_scored.unlink()

            # Ejecutar scorer en disco local (SQLite mucho más rápido)
            scorer = VectorizedScorer(self.profile, self.config)
            result = scorer.score_pairs_with_streaming(
                str(local_cand),
                df,
                score_threshold=self.profile.get("score_threshold"),
                output_db_path=str(local_scored),
            )

            # Normalizar resultado
            if isinstance(result, str):
                result_path = Path(result)
                if result_path != local_scored and result_path.exists():
                    import shutil

                    shutil.move(str(result_path), str(local_scored))

            # Push: scored.db de local → Drive
            if local_scored.exists():
                self.storage.push_to_drive("scored.db")
                # Copiar al directorio de fase final
                import shutil

                shutil.copy2(str(local_scored), str(outfile))
        else:
            # Sin HybridStorage: procesamiento directo en Drive
            scorer = VectorizedScorer(self.profile, self.config)
            result = scorer.score_pairs_with_streaming(
                str(cand_path),
                df,
                score_threshold=self.profile.get("score_threshold"),
                output_db_path=str(outfile),
            )

            # ✅ Normalizar resultado con manejo seguro de SQLite
            if isinstance(result, str):
                result_path = Path(result)
                if result_path != outfile and result_path.exists():
                    success = self._safe_move_sqlite(result_path, outfile)
                    if not success:
                        raise RuntimeError(
                            f"Error moviendo BD de scores: {result_path} → {outfile}"
                        )

        # Verificar resultado
        if outfile.exists():
            if not self._verify_sqlite_integrity(outfile):
                raise RuntimeError(f"BD de scores corrupta: {outfile}")

            size_mb = outfile.stat().st_size / (1024 * 1024)
            self.log.info(f"   💾 Scores: {outfile.name} ({size_mb:.1f} MB)")
        else:
            raise RuntimeError(f"No se generó archivo de scores: {outfile}")

        return outfile, [outfile]

    def _run_L4(self, df: pd.DataFrame, score_path: Path) -> tuple[dict, list[Path]]:
        """
        L4: Clustering de entidades.

        Agrupa registros relacionados en clusters usando
        OptimizedClusterer.

        FASE 2 - Paso 2.1: Si HybridStorage está disponible, lee scored.db
        desde disco local para acelerar la lectura de SQLite.

        Returns:
            Tupla (dict de mapeo record_id → ID_GRUPO, [Path])
        """
        phase_dir = self.dirs[Phase.L4_CLUSTERING]
        outfile = phase_dir / "clusters.parquet"

        # ═══════════════════════════════════════════════════════════════════
        # Leer desde disco local si disponible
        # ═══════════════════════════════════════════════════════════════════
        read_path = score_path
        if self.storage:
            local_scored = self.storage.pull_from_drive(score_path.name)
            if local_scored.exists():
                read_path = local_scored
                self.log.info("   📥 Leyendo scores desde disco local (más rápido)")

        # Contar sin materializar. El clusterer recibe la ruta y recorre
        # ``scored_pairs`` por lotes con Union-Find; antes esta fase llamada
        # "en disco" cargaba todos los pares a pandas, luego a NumPy/CSR y, en
        # modo estricto, también a NetworkX.
        conn = sqlite3.connect(f"file:{read_path}?mode=ro", uri=True)
        try:
            n_scored = int(conn.execute("SELECT COUNT(*) FROM scored_pairs").fetchone()[0])
        finally:
            conn.close()

        self.log.info(f"   📊 Pares para clustering: {n_scored:,}")

        # Ejecutar clustering directamente desde SQLite
        clusterer = OptimizedClusterer(self.profile, self.config)
        cluster_map = clusterer.cluster_entities(str(read_path), df_full=df)

        # Normalizar a dict
        if isinstance(cluster_map, pd.Series):
            cluster_map = cluster_map.to_dict()

        # Guardar resultado
        df_clusters = pd.DataFrame(
            {"record_id": list(cluster_map.keys()), "ID_GRUPO": list(cluster_map.values())}
        )
        df_clusters.to_parquet(outfile, index=False)

        n_clusters = len(set(cluster_map.values()))
        self.log.info(f"   📊 Clusters generados: {n_clusters:,}")

        return cluster_map, [outfile]

    def _run_L5(
        self,
        df: pd.DataFrame | ConsumableDataFrame,
        clusters: dict,
    ) -> tuple[dict, list[Path]]:
        """
        L5: Generación de Golden Records.

        Consolida clusters en Golden Records únicos usando
        GoldenRecordGeneratorV7 y consolidate_groups_by_nit_balanced.

        Returns:
            Tupla (dict con 'golden' y 'correlative', [Path, Path])
        """
        phase_dir = self.dirs[Phase.L5_GOLDEN]
        out_gold = phase_dir / "golden.parquet"
        out_corr = phase_dir / "correlative.parquet"

        # Compatibilidad: las llamadas directas conservan el DataFrame; run()
        # transfiere explícitamente la propiedad mediante ConsumableDataFrame.
        input_frame = df.take() if isinstance(df, ConsumableDataFrame) else df

        # reset_index ya produce el frame de trabajo requerido. La copia
        # profunda adicional duplicaba el consolidado completo en el pico L5.
        df_clustered = input_frame.reset_index(drop=True)
        del input_frame

        # Mapear por una Serie posicional efímera, nunca por una columna del
        # usuario. Antes, una fuente con encabezado ``_original_idx`` podía
        # controlar el mapa y fusionar entidades no relacionadas.
        positions = pd.Series(range(len(df_clustered)), index=df_clustered.index, dtype="int64")
        mapped_groups = positions.map(clusters)
        df_clustered["ID_GRUPO"] = mapped_groups.where(mapped_groups.notna(), positions)
        del mapped_groups, positions

        # Convertir a int de forma segura
        df_clustered["ID_GRUPO"] = (
            pd.to_numeric(df_clustered["ID_GRUPO"], errors="coerce").fillna(-1).astype(int)
        )

        # ✅ CORRECCIÓN: Asignar grupo único a registros sin cluster
        mask_sin_grupo = df_clustered["ID_GRUPO"] == -1
        if mask_sin_grupo.any():
            n_sin_grupo = mask_sin_grupo.sum()
            max_grupo = df_clustered["ID_GRUPO"].max()
            nuevos_grupos = range(max_grupo + 1, max_grupo + 1 + n_sin_grupo)
            df_clustered.loc[mask_sin_grupo, "ID_GRUPO"] = list(nuevos_grupos)
            self.log.info(f"   ⚠️ {n_sin_grupo:,} registros sin cluster asignados a grupos únicos")

        self.log.info(f"   📊 Clusters únicos: {df_clustered['ID_GRUPO'].nunique():,}")

        # ── v0.14.0: cannot-link por identificador válido ───────────────────
        # El veto de pares del scorer no impide que un registro SIN NIT una por
        # transitividad dos NIT válidos distintos. Aquí se corrige sobre la
        # partición ya construida, en O(n). Desactivable con
        # `cannot_link_identificador: False` en el perfil.
        if bool(self.profile.get("cannot_link_identificador", True)):
            # v0.17.0: la misma tolerancia a digitación del veto L3 aplica al
            # cannot-link — un identificador corrupto por captura no debe
            # partir el grupo que el score unió con nombre cohesivo.
            df_clustered, reporte_cl = aplicar_cannot_link_identificador(
                df_clustered,
                tolerancia_digitacion=int(
                    self.profile.get("tolerancia_digitacion_identificador", 0)
                ),
                similitud_nombre_rescate=float(
                    self.profile.get("similitud_nombre_rescate_identificador", 0.90)
                ),
                canonicalizar_dv=bool(self.profile.get("dv_es_mismo_identificador", True)),
            )
            if reporte_cl.hubo_cambios:
                self.log.warning(f"   ⚠️ {reporte_cl.resumen()}")

        # Prioridad de fuentes
        priority = list(self.profile.get("source_quality_weights", {}).keys())
        if not priority:
            priority = list(self.sources.keys())
        self.log.debug(f"   📝 Prioridad de fuentes: {priority}")

        # Generar Golden Records
        generator = GoldenRecordGeneratorV7(priority, self.config)
        golden_input = ConsumableDataFrame(df_clustered)
        del df_clustered
        try:
            golden, correl = generator.generate(golden_input)
        finally:
            golden_input.release()
        gc.collect()

        # Consolidación final por NIT
        try:
            # El orquestador ya registra esta fase. Evitar ``print`` directos
            # impide que una consola Windows CP1252 convierta un emoji en una
            # excepción y active el fallback funcional de consolidación.
            golden_final, correl_final = consolidate_groups_by_nit_balanced(
                golden,
                correl,
                verbose=False,
                copiar_correlativa=False,
            )
            if correl_final is not correl:
                del correl
            if golden_final is not golden:
                del golden
            self.log.info("   ✅ Consolidación por NIT completada")
        except Exception as e:
            self.log.warning(f"   ⚠️ Consolidación falló, usando resultados directos: {e}")
            golden_final, correl_final = golden, correl

        # Guardar resultados
        golden_final.to_parquet(out_gold, index=False)
        correl_final.to_parquet(out_corr, index=False)

        self.log.info(f"   📊 Golden Records: {len(golden_final):,}")
        self.log.info(f"   📊 Tabla Correlativa: {len(correl_final):,}")

        return {"golden": golden_final, "correlative": correl_final}, [out_gold, out_corr]

    def _run_L6(self, results_data: dict) -> tuple[list[Path], list[Path]]:
        """
        L6: Generación de reportes con GESTIÓN PROACTIVA DE MEMORIA.

        MEJORAS:
        - Liberación agresiva de RAM antes de comenzar
        - Reducción automática de DataFrames si RAM > 85%
        - GC entre cada estrategia
        """
        # 1. LIBERACIÓN PROACTIVA DE MEMORIA
        gc.collect()

        mem_info = psutil.virtual_memory()
        mem_percent = mem_info.percent
        self.log.info(f"   💾 Memoria antes de L6: {mem_percent:.1f}%")

        # 2. MODO CONSERVADOR SI MEMORIA CRÍTICA
        MEMORY_CRITICAL_THRESHOLD = 85.0
        SAMPLE_SIZE = 50_000

        # Nunca se recortan los resultados devueltos ni los artefactos de
        # DataExportStrategy. Bajo presión de RAM se usan vistas muestreadas
        # exclusivamente para los reportes analíticos. Esto también cubre
        # resultados postprocesados (matcher/expansión), para los que no es
        # válido leer los Parquet anteriores de L5.
        full_golden_df = results_data["golden"]
        full_correl_df = results_data["correlative"]
        golden_df = full_golden_df
        correl_df = full_correl_df
        reporting_sampled = False
        full_metrics = self._build_metrics(golden_df, correl_df)

        if mem_percent > MEMORY_CRITICAL_THRESHOLD:
            self.log.warning(f"   ⚠️ MEMORIA CRÍTICA ({mem_percent:.1f}%). Modo conservador...")

            if len(golden_df) > SAMPLE_SIZE:
                orig = len(golden_df)
                golden_df = golden_df.head(SAMPLE_SIZE)
                reporting_sampled = True
                self.log.info(f"      📉 golden_df: {orig:,} → {SAMPLE_SIZE:,}")

            if len(correl_df) > SAMPLE_SIZE:
                orig = len(correl_df)
                correl_df = correl_df.head(SAMPLE_SIZE)
                reporting_sampled = True
                self.log.info(f"      📉 correlative_df: {orig:,} → {SAMPLE_SIZE:,}")

            gc.collect()
            self.log.info(f"      ✅ Memoria después: {psutil.virtual_memory().percent:.1f}%")

        # 3. PREPARAR DIRECTORIOS
        output_dir = self.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "visualizaciones").mkdir(parents=True, exist_ok=True)

        # 4. OBTENER DATAFRAMES Y MÉTRICAS
        metrics = full_metrics
        if reporting_sampled:
            metrics["reporting_sampled"] = True
            metrics["reporting_sample_size"] = SAMPLE_SIZE

        # 5. CREAR CONTEXTO
        ctx = ReportingContext(
            golden_df=golden_df,
            correlative_df=correl_df,
            config=self.config,
            output_dir=output_dir,
            metrics=metrics,
            start_time=self._start_time or time.time(),
            phase_times=self._phase_times.copy(),
        )
        export_ctx = ReportingContext(
            golden_df=full_golden_df,
            correlative_df=full_correl_df,
            config=self.config,
            output_dir=output_dir,
            metrics=metrics,
            start_time=self._start_time or time.time(),
            phase_times=self._phase_times.copy(),
        )

        # 6. EJECUTAR ESTRATEGIAS CON PROTECCIÓN
        all_files: list[Path] = []
        self.log.info(f"📊 Ejecutando {len(self._reporting_strategies)} estrategias...")

        for strategy in self._reporting_strategies:
            current_mem = psutil.virtual_memory().percent
            is_data_export = isinstance(strategy, DataExportStrategy)

            # La exportación es un artefacto contractual, no una visualización
            # opcional: incluso bajo presión crítica debe intentarse. La
            # estrategia ya usa streaming desde checkpoints cuando es válido
            # y evita Excel completo con RAM alta. Solo se omite analítica.
            if current_mem > 95 and not is_data_export:
                self.log.error(f"   🛑 RAM crítica ({current_mem:.1f}%), saltando {strategy.name}")
                continue

            try:
                self.log.info(f"   ▶️ {strategy.name}...")
                strategy_ctx = export_ctx if is_data_export else ctx
                files = strategy.execute(strategy_ctx, self.log)

                # BaseReportingStrategy es deliberadamente tolerante y
                # convierte errores en []. Para la estrategia contractual
                # incorporada eso ocultaría una corrida sin sus dos salidas.
                if type(strategy) is DataExportStrategy:
                    names = [Path(path).name for path in files]
                    missing_exports = [
                        prefix
                        for prefix in ("golden_records", "tabla_correlativa")
                        if not any(name.startswith(prefix) for name in names)
                    ]
                    if missing_exports:
                        raise RuntimeError(
                            "DataExportStrategy no generó artefactos para: "
                            + ", ".join(missing_exports)
                        )
                all_files.extend(files)
                self.log.info(f"   ✅ {strategy.name}: {len(files)} archivo(s)")
            except Exception as e:
                self.log.error(f"   ❌ {strategy.name} falló: {e}")
                if is_data_export:
                    raise RuntimeError(
                        "Falló la exportación contractual de resultados; "
                        "no se marcará L6 como completada."
                    ) from e

            gc.collect()

        # 7. RESUMEN
        self.log.info(f"   📁 Total: {len(all_files)} archivos generados")
        self.log.info(f"   💾 Memoria final: {psutil.virtual_memory().percent:.1f}%")

        return all_files, all_files

    def _contar_filas_fase(self, fase: Phase, archivo: str, tabla: str) -> int | None:
        """Filas de una tabla SQLite escrita por una fase, sin cargarla.

        Devuelve ``None`` —nunca 0— cuando la base o la tabla no están (p. ej.
        una instancia parcial sin ``dirs`` o un L6 relanzado tras limpiar el
        directorio de trabajo): un conteo ausente se muestra como «N/A», un 0
        sería una cifra falsa. Reutiliza ``evaluation.banco._contar_filas_sqlite``
        (privada en ese módulo; es la misma regla que usa el banco y se escribe
        una sola vez).
        """
        carpeta = getattr(self, "dirs", {}).get(fase)
        if carpeta is None:
            return None
        ruta = Path(carpeta) / archivo
        total = _contar_filas_sqlite(ruta, tabla)
        if total is None and hasattr(self, "log"):
            self.log.warning(
                f"   ⚠️ No se pudo contar {tabla} en {ruta}: el resumen ejecutivo "
                "mostrará N/A en vez de un número."
            )
        return total

    def _build_metrics(self, golden_df: pd.DataFrame, correl_df: pd.DataFrame) -> dict[str, Any]:
        """
        Construye diccionario de métricas para reportes (fuente única de L6).

        Claves de volumen de trabajo del motor (F1.7), leídas de la verdad en
        disco y no de contadores en memoria:

        - ``candidatos``: filas de ``L2_lsh_candidates/candidates.db``
          (tabla ``candidate_pairs``); ``None`` si la base no está.
        - ``pares_puntuados``: filas de ``L3_scoring/scored.db``
          (tabla ``scored_pairs``); ``None`` si la base no está.
        - ``rss_pico_mib``: máximo de ``peak_rss_mib_by_phase`` (RSS pico del
          proceso entre las fases ya cerradas; L6 aún no lo está cuando se
          construyen las métricas); ``None`` si ninguna fase registró RSS.

        ``candidates_found``, ``pairs_scored`` y ``max_memory_gb`` son alias
        heredados de los mismos valores para los consumidores que siguen en
        inglés (``visualizer``, ``suite``, ``dashboard``); con ``None`` se
        entregan como 0 porque esos consumidores dividen por ellos. El resumen
        ejecutivo lee las claves en español.

        Args:
            golden_df: DataFrame de Golden Records
            correl_df: DataFrame de Tabla Correlativa

        Returns:
            Dict con métricas calculadas
        """
        phase_peak_rss_mib = getattr(self, "_phase_peak_rss_mib", {})
        if not isinstance(phase_peak_rss_mib, dict):
            raise TypeError("_phase_peak_rss_mib debe ser un diccionario")

        candidatos = self._contar_filas_fase(
            Phase.L2_LSH_CANDIDATES, "candidates.db", "candidate_pairs"
        )
        pares_puntuados = self._contar_filas_fase(Phase.L3_SCORING, "scored.db", "scored_pairs")
        rss_pico_mib = max(phase_peak_rss_mib.values()) if phase_peak_rss_mib else None

        metrics: dict[str, Any] = {
            "total_records": len(correl_df),
            "unique_groups": len(golden_df),
            "execution_time": time.time() - self._start_time if self._start_time else 0,
            "phase_times": self._phase_times.copy(),
            "peak_rss_mib_by_phase": phase_peak_rss_mib.copy(),
            "candidatos": candidatos,
            "pares_puntuados": pares_puntuados,
            "rss_pico_mib": rss_pico_mib,
            # Alias heredados (ver docstring).
            "candidates_found": candidatos or 0,
            "pairs_scored": pares_puntuados or 0,
            "max_memory_gb": (rss_pico_mib or 0.0) / 1024,
        }

        # Tasa de linkage/reducción
        if len(correl_df) > 0:
            metrics["linkage_rate"] = 1 - (len(golden_df) / len(correl_df))
            metrics["reduction_rate"] = 1 - (len(golden_df) / len(correl_df))
        else:
            metrics["linkage_rate"] = 0
            metrics["reduction_rate"] = 0

        # Grupos multi-fuente
        for col in ["SOURCES_COUNT", "SOURCE_COUNT"]:
            if col in golden_df.columns:
                metrics["multi_source_groups"] = int((golden_df[col] > 1).sum())
                break

        # Métricas de confianza
        if "CONFIDENCE_SCORE" in golden_df.columns:
            scores = golden_df["CONFIDENCE_SCORE"]
            metrics["avg_confidence"] = float(scores.mean())
            metrics["median_confidence"] = float(scores.median())
            metrics["high_confidence_count"] = int((scores > 0.9).sum())
            metrics["low_confidence_count"] = int((scores < 0.75).sum())

        # Por fuente
        if "SRC" in correl_df.columns:
            metrics["sources_count"] = correl_df["SRC"].nunique()
            metrics["records_per_source"] = correl_df["SRC"].value_counts().to_dict()

        return metrics

    # ==========================================================================
    # UTILIDADES DE EXPERIMENTOS
    # ==========================================================================

    def _save_experiment(self, name: str, results: dict) -> Path:
        """
        Guarda snapshot de un experimento para comparación posterior.

        Args:
            name: Nombre del experimento
            results: Dict con resultados ('golden', 'correlative')

        Returns:
            Path al directorio del experimento
        """
        from ..exporters._spreadsheet import validate_leaf_name

        safe_name = validate_leaf_name(name, "experiment")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        experiments_root = (self.work_dir / "experiments").resolve()
        exp_dir = experiments_root / f"{safe_name}_{timestamp}"
        if exp_dir.parent != experiments_root:  # defensa en profundidad
            raise ValueError("experiment intenta escapar el directorio de experimentos")
        exp_dir.mkdir(parents=True, exist_ok=True)

        # Guardar datos
        results["golden"].to_parquet(exp_dir / "golden.parquet")
        results["correlative"].to_parquet(exp_dir / "correlative.parquet")

        # Guardar config y metadatos
        config_snapshot = {
            "name": safe_name,
            "timestamp": timestamp,
            "orchestrator_version": self.VERSION,
            "golden_count": len(results["golden"]),
            "correlative_count": len(results["correlative"]),
            "reduction_rate": 1 - (len(results["golden"]) / len(results["correlative"]))
            if len(results["correlative"]) > 0
            else 0,
            "phase_times": self._phase_times,
            "peak_rss_mib_by_phase": self._phase_peak_rss_mib,
            "config": self.config,
        }

        with open(exp_dir / "experiment.json", "w", encoding="utf-8") as f:
            json.dump(config_snapshot, f, indent=2, default=str, ensure_ascii=False)

        self.log.info(f"💾 Experimento guardado: {exp_dir}")
        return exp_dir
