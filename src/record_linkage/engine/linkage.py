"""
engine.linkage — record_linkage_pipeline

Componentes:
    - class RecordLinkageEngine  (origen: notebook celda [118])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import gc
import os
import sqlite3
import time
from typing import (
    Any,
)

import pandas as pd

from ..pipeline._internal import PROFILES
from ..utils.logger import CustomLogger
from ..utils.memory import (
    AdaptiveMemoryManager,
)
from ..utils.performance import (
    track_performance,
)
from .candidates import CandidateFinder
from .clusterer import EntityClusterer, OptimizedClusterer
from .lsh.legacy import OptimizedLSHEngine
from .scorer import PairScorer, VectorizedScorer


class RecordLinkageEngine:
    """
    Motor principal de Record Linkage con arquitectura modular y optimizada.

    Este motor orquesta el proceso completo de vinculación usando componentes
    intercambiables para cada fase del proceso.
    """

    def __init__(self, profile: str = "standard", config: dict[str, Any] | None = None):
        """
        Inicializar motor con perfil y configuración.

        Args:
            profile: Nombre del perfil a usar ('standard', 'large_scale', etc.)
            config: Configuración completa del sistema
        """
        self.profile_name = profile
        self.config = config or {}
        self.logger = CustomLogger("RecordLinkageEngine")

        # --- ARQUITECTURA CORREGIDA ---
        # Extraer el perfil del objeto 'config' que se pasa, no de una variable global.
        # Esto asegura que la configuración del pipeline sea la que se use.
        if config and "profiles" in config and profile in config.get("profiles", {}):
            # Prioridad 1: Usar el perfil del diccionario de configuración explícito.
            self.profile = config["profiles"][profile]
            self.logger.info(
                f"Perfil '{profile}' cargado desde el objeto de configuración del pipeline."
            )
        elif "PROFILES" in globals() and profile in PROFILES:
            # Prioridad 2: Usar el perfil de la variable global (para compatibilidad)
            self.profile = PROFILES.get(profile)
            self.logger.warning(
                f"Perfil '{profile}' cargado desde la variable global 'PROFILES'. Se recomienda pasarlo en el config."
            )
        else:
            # Fallback: Usar un perfil por defecto si todo lo demás falla.
            self.profile = {
                "lsh_permutations": 128,
                "lsh_threshold": 0.75,
                "score_threshold": 0.85,
                "weights": {"name": 0.7, "nit": 0.25, "phonetic": 0.05},
            }
            self.logger.error(f"Perfil '{profile}' no encontrado. Usando perfil de emergencia.")

        # Componentes del motor (se inicializan lazy)
        self._candidate_finder = None
        self._pair_scorer = None
        self._entity_clusterer = None

        # Métricas y estadísticas
        self.metrics = {
            "preprocessing_time": 0,
            "candidate_generation_time": 0,
            "scoring_time": 0,
            "clustering_time": 0,
            "total_time": 0,
            "candidates_found": 0,
            "pairs_scored": 0,
            "clusters_created": 0,
        }

        # Configuración de memoria adaptativa (v0.12.0: umbrales porcentuales
        # de RAM disponible; los GB absolutos invertidos eran el bug H4).
        self.memory_manager = AdaptiveMemoryManager(warning_pct=25.0, critical_pct=12.0)
        # Importante: El AdaptiveMemoryManager ahora usará el 'self.profile' correcto.
        if hasattr(self.memory_manager, "original_config"):
            self.memory_manager.original_config = self.profile.copy()

        self.logger.info(f"RecordLinkageEngine inicializado con perfil '{profile}'")

    @property
    def candidate_finder(self) -> CandidateFinder:
        """Obtener componente de búsqueda de candidatos (lazy loading)."""
        if self._candidate_finder is None:
            # Verificar si la clase está disponible
            if "OptimizedLSHEngine" in globals():
                self._candidate_finder = OptimizedLSHEngine(self.profile, self.config)
            else:
                raise RuntimeError(
                    "OptimizedLSHEngine no está definido. "
                    "Asegúrese de ejecutar la celda 3.2 antes de usar el pipeline."
                )
        return self._candidate_finder

    @property
    def pair_scorer(self) -> PairScorer:
        """Obtener componente de scoring (lazy loading)."""
        if self._pair_scorer is None:
            if "VectorizedScorer" in globals():
                self._pair_scorer = VectorizedScorer(self.profile, self.config)
            else:
                raise RuntimeError(
                    "VectorizedScorer no está definido. "
                    "Asegúrese de ejecutar la celda 3.3 antes de usar el pipeline."
                )
        return self._pair_scorer

    @property
    def entity_clusterer(self) -> EntityClusterer:
        """Obtener componente de clustering (lazy loading)."""
        if self._entity_clusterer is None:
            if "OptimizedClusterer" in globals():
                self._entity_clusterer = OptimizedClusterer(self.profile, self.config)
            else:
                raise RuntimeError(
                    "OptimizedClusterer no está definido. "
                    "Asegúrese de ejecutar la celda 3.4 antes de usar el pipeline."
                )
        return self._entity_clusterer

    @track_performance("Record Linkage completo")
    def link(
        self,
        df: pd.DataFrame,
        output_dir: str | None = None,
        cross_source_only: bool | None = None,  # None → autodetección
        return_diagnostics: bool = False,
    ) -> pd.DataFrame | tuple[pd.DataFrame, dict[str, Any]]:
        """
        Ejecuta el proceso completo de record linkage o deduplicación con
        detección automática del modo de operación.

        Parámetros
        ----------
        df : pd.DataFrame
            Registros a enlazar o deduplicar (idealmente con columna 'SRC').
        output_dir : str, opcional
            Carpeta donde se guardan archivos intermedios cuando se trabaja en disco.
        cross_source_only : bool | None, opcional
            True  → solo cruces entre fuentes distintas.
            False → cruces dentro de la misma fuente.
            None  → se detecta automáticamente (valor por defecto).
        return_diagnostics : bool, opcional
            Si es True, devuelve además un dict con métricas y bitácora.
        """
        start_time = time.time()

        # ────────────────────────────
        # 1. DETECCIÓN AUTOMÁTICA DEL MODO (Paso 1.6: Trusted Sources)
        # ────────────────────────────
        # Leer trusted_unique_sources del perfil (Paso 1.6)
        trusted_sources = set(self.profile.get("trusted_unique_sources", []))

        if cross_source_only is None:
            if trusted_sources:
                # Paso 1.6: Si hay fuentes confiables, usar modo selectivo
                cross_source_only = False  # No bloquear todo, solo las trusted
                self.logger.info(
                    f"Modo TRUSTED SOURCES: bloqueando dedup interna de {trusted_sources}"
                )
            elif "SRC" in df.columns:
                n_sources = df["SRC"].nunique()
                if n_sources == 1:
                    cross_source_only = False  # Modo deduplicación
                    self.logger.info(
                        f"Modo DEDUPLICACIÓN detectado (1 fuente: '{df['SRC'].iloc[0]}')"
                    )
                else:
                    cross_source_only = True  # Modo linkage
                    self.logger.info(f"Modo LINKAGE detectado ({n_sources} fuentes)")
            else:
                cross_source_only = True  # Default: linkage
                self.logger.warning(
                    "Columna 'SRC' no encontrada; se usará modo linkage por defecto"
                )

        self.logger.info(
            f"Iniciando linkage de {len(df):,} registros (cross_source_only={cross_source_only})"
        )

        # ────────────────────────────
        # 2. VALIDACIONES Y AJUSTES
        # ────────────────────────────
        self._validate_input(df)

        adapted_config = self.memory_manager.monitor_and_adapt(self.profile.copy())
        self._update_profile(adapted_config)

        df_work = df.copy()
        df_work["_idx"] = range(len(df_work))

        try:
            # ===== FASE 1: GENERACIÓN DE CANDIDATOS =====
            self.logger.info("=== FASE 1: Generación de candidatos ===")
            t1 = time.time()

            candidates = self.candidate_finder.find_candidates(
                df_work,
                output_dir=output_dir,  # ← corrección incluida
                cross_source_only=cross_source_only,
                trusted_unique_sources=trusted_sources,  # ✅ Paso 1.6
            )
            self.metrics["candidate_generation_time"] = time.time() - t1

            # ════════════════════════════════════════════════════════════
            # (v2.5.0 P0-1) BLOQUEO POR NIT BASE — fusión de candidatos.
            # Ataque a la causa raíz del cuello de recall: pares cuyas
            # razones sociales no comparten n-gramas pero sí comparten NIT
            # (o NIT a distancia 1). Estos pares NUNCA entran por el LSH
            # de nombre. El bloqueo es complementario, no sustitutivo.
            # ════════════════════════════════════════════════════════════
            candidates = self._merge_nit_blocking_into_candidates(
                candidates=candidates,
                df=df_work,
                cross_source_only=cross_source_only,
            )

            # ════════════════════════════════════════════════════════════
            # (v2.6.0 P0-1 Paso 1.2) BLOQUEO MULTI-PASADA POR NOMBRE.
            # Complementario al LSH de n-gramas: captura pares cuyos
            # sufijos societarios difieren (ECOPETROL SA ↔ ECOPETROL LTDA)
            # o que comparten un token significativo largo (COLANTA ↔
            # COOPERATIVA COLANTA). Ataca el 28.9 % de pares verdaderos
            # que el bloqueo NIT no logra capturar.
            # ════════════════════════════════════════════════════════════
            candidates = self._merge_name_blocking_into_candidates(
                candidates=candidates,
                df=df_work,
                cross_source_only=cross_source_only,
            )

            # Manejo candidatos memoria/disco
            if isinstance(candidates, str):
                self.logger.info(f"Candidatos generados en disco: {os.path.basename(candidates)}")
                try:
                    conn = sqlite3.connect(candidates)
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM candidate_pairs")
                    self.metrics["candidates_found"] = cursor.fetchone()[0]
                    conn.close()
                    self.logger.info(
                        f"Total candidatos en BD: {self.metrics['candidates_found']:,}"
                    )
                except Exception:
                    self.metrics["candidates_found"] = -1
            else:
                self.metrics["candidates_found"] = len(candidates)
                self.logger.info(f"Candidatos encontrados: {len(candidates):,}")
                if not candidates:
                    self.logger.warning("No se encontraron candidatos para vincular")
                    df_work["ID_GRUPO"] = df_work.index
                    return self._prepare_output(df_work, return_diagnostics)

            # ===== FASE 2: CÁLCULO DE SCORES =====
            self.logger.info("=== FASE 2: Cálculo de scores ===")
            t2 = time.time()

            scored_pairs = self.pair_scorer.score_pairs(
                candidates, df_work, score_threshold=adapted_config.get("score_threshold", 0.85)
            )
            self.metrics["scoring_time"] = time.time() - t2

            if isinstance(scored_pairs, str):
                self.logger.info(f"Scores calculados en disco: {os.path.basename(scored_pairs)}")
                try:
                    conn = sqlite3.connect(scored_pairs)
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM scored_pairs")
                    self.metrics["pairs_scored"] = cursor.fetchone()[0]
                    conn.close()
                except Exception:
                    self.metrics["pairs_scored"] = -1
            else:
                self.metrics["pairs_scored"] = len(scored_pairs)

            self.logger.info(f"Pares con score alto: {self.metrics['pairs_scored']:,}")

            if isinstance(candidates, set):
                del candidates
            gc.collect()

            # ===== FASE 3: CLUSTERING DE ENTIDADES =====
            self.logger.info("=== FASE 3: Clustering de entidades ===")
            t3 = time.time()

            cluster_mapping_result = self.entity_clusterer.cluster_entities(
                scored_pairs,
                df_full=df_work,  # <- se pasa el DataFrame completo
            )
            self.metrics["clustering_time"] = time.time() - t3

            if isinstance(cluster_mapping_result, str):
                self.logger.info("Clustering en disco. Cargando mapeo final…")
                id_to_group_map = self._load_clusters_from_db(cluster_mapping_result)
                df_work["ID_GRUPO"] = (
                    df_work["_idx"].map(id_to_group_map).fillna(df_work["_idx"]).astype(int)
                )
                self.metrics["clusters_created"] = pd.Series(id_to_group_map).nunique()
                del id_to_group_map
            else:
                self.logger.info("Clustering en memoria.")
                self.metrics["clusters_created"] = len(set(cluster_mapping_result.values()))
                df_work["ID_GRUPO"] = (
                    df_work["_idx"].map(cluster_mapping_result).fillna(df_work["_idx"]).astype(int)
                )
                del cluster_mapping_result

            if isinstance(scored_pairs, pd.DataFrame):
                del scored_pairs
            gc.collect()

            # ────────── MÉTRICAS FINALES ──────────
            self.metrics["total_time"] = time.time() - start_time
            self._log_final_metrics(df_work)

            return self._prepare_output(df_work, return_diagnostics)

        except Exception as e:
            self.logger.error(f"Error en proceso de linkage: {e}")
            import traceback

            traceback.print_exc()
            raise

        finally:
            gc.collect()

    def cleanup_files_manually(self):
        """Método para limpiar archivos manualmente cuando sea seguro"""
        if hasattr(self, "_candidate_finder") and self._candidate_finder:
            self._candidate_finder.cleanup()
            self.logger.info("Archivos LSH eliminados manualmente")

    def _validate_input(self, df: pd.DataFrame):
        """Validar que el DataFrame tenga las columnas requeridas."""
        required_columns = ["NOMBRE_LIMPIO"]
        optional_columns = ["NIT_OK", "NIT_BASE", "SRC", "PHONETIC_KEY1"]

        # Verificar columnas requeridas
        missing = set(required_columns) - set(df.columns)
        if missing:
            raise ValueError(f"Columnas requeridas faltantes: {missing}")

        # Advertir sobre columnas opcionales faltantes
        missing_optional = set(optional_columns) - set(df.columns)
        if missing_optional:
            self.logger.warning(f"Columnas opcionales faltantes: {missing_optional}")

        # Verificar que no esté vacío
        if df.empty:
            raise ValueError("DataFrame vacío")

        # Verificar tipos de datos básicos
        if df["NOMBRE_LIMPIO"].dtype != "object":
            self.logger.warning("NOMBRE_LIMPIO no es tipo string, convirtiendo...")
            df["NOMBRE_LIMPIO"] = df["NOMBRE_LIMPIO"].astype(str)

    def _update_profile(self, new_config: dict[str, Any]):
        """Actualizar configuración del perfil preservando parámetros críticos."""
        # Preservar parámetros críticos si existen
        if hasattr(self, "critical_params") and self.critical_params:
            for param, value in self.critical_params.items():
                if value is not None and param in new_config:
                    self.logger.debug(
                        f"Preservando {param}={value} (ignorando {new_config[param]})"
                    )
                    new_config[param] = value

        self.profile.update(new_config)

        # Propagar cambios a componentes si ya están inicializados
        if self._candidate_finder is not None:
            self._candidate_finder.update_config(new_config)
        if self._pair_scorer is not None:
            self._pair_scorer.update_config(new_config)
        if self._entity_clusterer is not None:
            self._entity_clusterer.update_config(new_config)

    def _prepare_output(
        self, df_work: pd.DataFrame, return_diagnostics: bool
    ) -> pd.DataFrame | tuple[pd.DataFrame, dict[str, Any]]:
        """Preparar salida del motor."""
        # Eliminar columnas temporales
        df_output = df_work.drop(["_idx"], axis=1, errors="ignore")

        if return_diagnostics:
            diagnostics = {
                "metrics": self.metrics.copy(),
                "profile_used": self.profile_name,
                "config": self.profile.copy(),
                "groups_summary": self._get_groups_summary(df_output),
            }
            return df_output, diagnostics

        return df_output

    def _get_groups_summary(self, df: pd.DataFrame) -> dict[str, Any]:
        """Obtener resumen de los grupos creados."""
        group_sizes = df["ID_GRUPO"].value_counts()

        return {
            "total_groups": len(group_sizes),
            "singleton_groups": (group_sizes == 1).sum(),
            "multi_record_groups": (group_sizes > 1).sum(),
            "largest_group_size": group_sizes.max(),
            "avg_group_size": group_sizes.mean(),
            "size_distribution": {
                "1": (group_sizes == 1).sum(),
                "2-5": ((group_sizes >= 2) & (group_sizes <= 5)).sum(),
                "6-10": ((group_sizes >= 6) & (group_sizes <= 10)).sum(),
                "11-50": ((group_sizes >= 11) & (group_sizes <= 50)).sum(),
                ">50": (group_sizes > 50).sum(),
            },
        }

    def _log_final_metrics(self, df: pd.DataFrame):
        """Log métricas finales del proceso."""
        unique_groups = df["ID_GRUPO"].nunique()
        linkage_rate = (len(df) - unique_groups) / len(df) if len(df) > 0 else 0

        self.logger.info("=== MÉTRICAS FINALES ===")
        self.logger.info(f"Total registros: {len(df):,}")
        self.logger.info(f"Grupos únicos: {unique_groups:,}")
        self.logger.info(f"Tasa de linkage: {linkage_rate:.2%}")
        self.logger.info(f"Reducción: {(1 - unique_groups / len(df)):.2%}")
        self.logger.info(f"Tiempo total: {self.metrics['total_time']:.1f}s")

        # Desglose de tiempos
        total_time = self.metrics["total_time"]
        if total_time > 0:
            self.logger.info("Distribución de tiempo:")
            self.logger.info(
                f"  - Candidatos: {self.metrics['candidate_generation_time'] / total_time:.1%}"
            )
            self.logger.info(f"  - Scoring: {self.metrics['scoring_time'] / total_time:.1%}")
            self.logger.info(f"  - Clustering: {self.metrics['clustering_time'] / total_time:.1%}")

    def _cleanup_resources(self):
        """Limpiar recursos y liberar memoria."""
        # Limpiar cachés en componentes
        if self._candidate_finder is not None:
            self._candidate_finder.cleanup()
        if self._pair_scorer is not None:
            self._pair_scorer.cleanup()

        # Garbage collection
        gc.collect()

    def get_metrics(self) -> dict[str, Any]:
        """Obtener métricas del último proceso ejecutado."""
        return self.metrics.copy()

    def reset(self):
        """Resetear el motor para un nuevo proceso."""
        self.metrics = {key: 0 for key in self.metrics}
        self._cleanup_resources()
        self.logger.info("Motor reseteado")

    def cleanup(self):
        """Limpiar recursos del motor."""
        self._cleanup_resources()

    def _load_clusters_from_db(self, db_path: str) -> pd.Series:
        """
        Carga el mapeo de clusters desde una base de datos SQLite.

        Este método es crucial para la escalabilidad del sistema, permitiendo
        procesar datasets que no caben en memoria al mantener los resultados
        intermedios en disco.

        Args:
            db_path: Ruta al archivo SQLite con los clusters

        Returns:
            pd.Series con el mapeo entity_id -> parent_id (grupo)
        """
        self.logger.info(f"Cargando mapeo de clusters desde: {db_path}")

        # Verificar que el archivo existe
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Archivo de clusters no encontrado: {db_path}")

        try:
            # Conectar en modo solo lectura para seguridad
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

            # Configurar para lectura eficiente
            cursor = conn.cursor()
            cursor.execute("PRAGMA cache_size = -1000000")  # 1GB cache
            cursor.execute("PRAGMA temp_store = MEMORY")

            # Obtener información de metadatos si existe
            try:
                cursor.execute("SELECT value FROM metadata WHERE key = 'unique_clusters'")
                n_clusters = cursor.fetchone()
                if n_clusters:
                    self.logger.info(f"Base de datos contiene {n_clusters[0]} clusters únicos")
            except:
                pass  # La tabla metadata puede no existir

            # Usar read_sql_query para eficiencia máxima
            # Esto crea una Serie donde el índice es 'entity_id' y el valor es 'parent_id'
            self.logger.info("Leyendo mapeo de clusters...")
            cluster_map_series = pd.read_sql_query(
                "SELECT entity_id, parent_id FROM entity_clusters", conn, index_col="entity_id"
            )["parent_id"]

            self.logger.info(f"Mapeo cargado: {len(cluster_map_series):,} entidades")

            return cluster_map_series

        except Exception as e:
            self.logger.error(f"Error cargando clusters desde BD: {e!s}")
            raise

        finally:
            if "conn" in locals() and conn:
                conn.close()

    # ════════════════════════════════════════════════════════════════════════
    # (v2.5.0 P0-1) BLOQUEO POR NIT BASE
    # ════════════════════════════════════════════════════════════════════════

    def _merge_nit_blocking_into_candidates(
        self,
        candidates: set[tuple[int, int]] | str | None,
        df: pd.DataFrame,
        cross_source_only: bool,
    ) -> set[tuple[int, int]] | str | None:
        """Fusiona pares de bloqueo por NIT base con los candidatos del LSH.

        Es una operación aditiva y opcional gobernada por el perfil
        ('enable_nit_blocking'). Maneja los tres formatos de retorno del
        ``find_candidates``: ``set[tuple]``, ``str`` (ruta SQLite) o ``None``.

        El bloqueo por NIT captura pares verdaderos cuyos nombres no
        comparten n-gramas (p. ej. "EY COLOMBIA" ↔ "ERNST & YOUNG") pero sí
        comparten NIT (o NIT a distancia ≤ 1). El criterio de aceptación del
        ROADMAP P0-1 Paso 1.1 es: recall +≥ 0.05 sin que la precision baje
        más de 0.02.

        Args:
            candidates: Salida de ``CandidateFinder.find_candidates``.
            df: DataFrame de trabajo con índice posicional 0..n-1, columnas
                NIT_BASE (o la configurada) y FUENTE (si cross_source).
            cross_source_only: Si True, descarta pares de la misma fuente.

        Returns:
            La misma estructura de entrada, con los pares de NIT fusionados:
            - set[tuple]: unión con los nuevos pares.
            - str (ruta SQLite): se hace INSERT OR IGNORE en la tabla
              ``candidate_pairs``.
            - None / falsy: si había 0 candidatos LSH, devuelve un set con
              solo los pares de NIT (caso borde poco común).
        """
        if not self._is_nit_blocking_enabled():
            return candidates

        from .lsh.nit_blocking import NitBlockingConfig, block_by_nit_base

        nit_col = self.profile.get("nit_blocking_column", "NIT_BASE")
        if nit_col not in df.columns:
            self.logger.info(
                f"[nit_blocking] Columna '{nit_col}' no presente, se omite el "
                f"bloqueo por NIT (esto es esperado en algunos modos)."
            )
            return candidates

        start = time.time()
        cfg = NitBlockingConfig(
            enable_exact=True,
            enable_neighbors=bool(self.profile.get("nit_blocking_neighbors", True)),
            max_bucket_size=int(self.profile.get("nit_blocking_max_bucket", 200)),
            min_nit_length=int(self.profile.get("nit_blocking_min_length", 6)),
            # v2.7.0: filtro por name_sim sobre los pares del bloqueo NIT.
            # 0.0 = sin filtro (default v2.5.0). 0.30 = recomendado v2.7.0
            # (mejora F1 +0.012 sin tocar el scorer; medido y documentado).
            min_name_similarity=float(self.profile.get("nit_blocking_min_name_sim", 0.0)),
            name_column=str(self.profile.get("nit_blocking_name_column", "RAZON_SOCIAL")),
        )
        pairs = block_by_nit_base(df, nit_column=nit_col, config=cfg)
        if not pairs:
            self.logger.info("[nit_blocking] 0 pares por bloqueo de NIT — se omite.")
            return candidates

        # Filtro cross-source: descartar pares de la misma fuente.
        if cross_source_only and "FUENTE" in df.columns:
            sources = df["FUENTE"].to_numpy()
            n = len(sources)
            pairs = {
                (a, b) for (a, b) in pairs if 0 <= a < n and 0 <= b < n and sources[a] != sources[b]
            }
            if not pairs:
                self.logger.info("[nit_blocking] 0 pares cross-source tras filtro — se omite.")
                return candidates

        # Caso A: candidatos como set en memoria → unión directa.
        if isinstance(candidates, set):
            before = len(candidates)
            merged = candidates | pairs
            added = len(merged) - before
            self.logger.info(
                f"[nit_blocking] Memoria: {before:,} LSH + {len(pairs):,} NIT → "
                f"{len(merged):,} totales (+{added:,} nuevos) en {time.time() - start:.2f}s"
            )
            self.metrics["nit_blocking_pairs"] = len(pairs)
            self.metrics["nit_blocking_added"] = added
            return merged

        # Caso B: candidatos como ruta SQLite → INSERT OR IGNORE.
        if isinstance(candidates, str):
            try:
                conn = sqlite3.connect(candidates)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM candidate_pairs")
                before = cursor.fetchone()[0]
                cursor.execute("BEGIN TRANSACTION")
                pairs_list = list(pairs)
                batch = 50_000
                for i in range(0, len(pairs_list), batch):
                    cursor.executemany(
                        "INSERT OR IGNORE INTO candidate_pairs VALUES (?, ?)",
                        pairs_list[i : i + batch],
                    )
                cursor.execute("COMMIT")
                cursor.execute("SELECT COUNT(*) FROM candidate_pairs")
                after = cursor.fetchone()[0]
                added = after - before
                conn.close()
                self.logger.info(
                    f"[nit_blocking] SQLite: {before:,} LSH + {len(pairs):,} NIT → "
                    f"{after:,} totales (+{added:,} nuevos) en {time.time() - start:.2f}s"
                )
                self.metrics["nit_blocking_pairs"] = len(pairs)
                self.metrics["nit_blocking_added"] = added
            except Exception as e:
                self.logger.error(
                    f"[nit_blocking] Falló la inserción en SQLite ({e!r}); "
                    f"se continúa con los candidatos LSH originales."
                )
            return candidates

        # Caso C: candidatos vacíos / None → devuelve solo los del bloqueo.
        if not candidates:
            self.logger.info(
                f"[nit_blocking] LSH sin candidatos; usando solo {len(pairs):,} "
                f"pares del bloqueo por NIT."
            )
            self.metrics["nit_blocking_pairs"] = len(pairs)
            self.metrics["nit_blocking_added"] = len(pairs)
            return set(pairs)

        # Formato desconocido: no rompemos el pipeline.
        self.logger.warning(
            f"[nit_blocking] Formato de candidatos desconocido ({type(candidates).__name__}); "
            f"se omite el bloqueo por NIT."
        )
        return candidates

    def _is_nit_blocking_enabled(self) -> bool:
        """Decide si activar el bloqueo por NIT según el perfil (default: True)."""
        # Lo gobierna el perfil; default True (es lo que ataca el cuello P0-1).
        if "enable_nit_blocking" in self.profile:
            return bool(self.profile["enable_nit_blocking"])
        # Fallback: config global del pipeline.
        return bool(self.config.get("enable_nit_blocking", True))

    # ════════════════════════════════════════════════════════════════════════
    # (v2.6.0 P0-1 Paso 1.2) BLOQUEO MULTI-PASADA POR NOMBRE
    # ════════════════════════════════════════════════════════════════════════

    def _merge_name_blocking_into_candidates(
        self,
        candidates: set[tuple[int, int]] | str | None,
        df: pd.DataFrame,
        cross_source_only: bool,
    ) -> set[tuple[int, int]] | str | None:
        """Fusiona pares de bloqueo multi-pasada por nombre con los candidatos.

        Complementario al LSH de n-gramas y al bloqueo por NIT. Captura
        pares cuyos sufijos societarios difieren o que comparten un token
        significativo largo (ROADMAP P0-1 Paso 1.2).

        Args:
            candidates: Salida del paso previo (puede ya incluir el bloqueo NIT).
            df: DataFrame con índice posicional 0..n-1 y la columna RAZON_SOCIAL
                (o la configurada).
            cross_source_only: Si True, descarta pares de la misma fuente.

        Returns:
            La misma estructura de entrada con los pares de nombre fusionados.
        """
        if not self._is_name_blocking_enabled():
            return candidates

        from .lsh.name_blocking import NameBlockingConfig, block_by_name_multipass

        name_col = self.profile.get("name_blocking_column", "RAZON_SOCIAL")
        if name_col not in df.columns:
            self.logger.info(
                f"[name_blocking] Columna '{name_col}' no presente, se omite el "
                f"bloqueo multi-pasada por nombre."
            )
            return candidates

        start = time.time()
        cfg = NameBlockingConfig(
            enable_fingerprint=bool(self.profile.get("name_blocking_fingerprint", True)),
            enable_significant_token=bool(
                self.profile.get("name_blocking_significant_token", True)
            ),
            min_token_length=int(self.profile.get("name_blocking_min_token_length", 4)),
            max_bucket_size=int(self.profile.get("name_blocking_max_bucket", 100)),
        )
        pairs = block_by_name_multipass(df, name_column=name_col, config=cfg)
        if not pairs:
            self.logger.info("[name_blocking] 0 pares — se omite.")
            return candidates

        # Filtro cross-source.
        if cross_source_only and "FUENTE" in df.columns:
            sources = df["FUENTE"].to_numpy()
            n = len(sources)
            pairs = {
                (a, b) for (a, b) in pairs if 0 <= a < n and 0 <= b < n and sources[a] != sources[b]
            }
            if not pairs:
                self.logger.info("[name_blocking] 0 pares cross-source — se omite.")
                return candidates

        # Caso A: set en memoria → unión.
        if isinstance(candidates, set):
            before = len(candidates)
            merged = candidates | pairs
            added = len(merged) - before
            self.logger.info(
                f"[name_blocking] Memoria: {before:,} previos + {len(pairs):,} nombre → "
                f"{len(merged):,} totales (+{added:,} nuevos) en {time.time() - start:.2f}s"
            )
            self.metrics["name_blocking_pairs"] = len(pairs)
            self.metrics["name_blocking_added"] = added
            return merged

        # Caso B: ruta SQLite → INSERT OR IGNORE.
        if isinstance(candidates, str):
            try:
                conn = sqlite3.connect(candidates)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM candidate_pairs")
                before = cursor.fetchone()[0]
                cursor.execute("BEGIN TRANSACTION")
                pairs_list = list(pairs)
                batch = 50_000
                for i in range(0, len(pairs_list), batch):
                    cursor.executemany(
                        "INSERT OR IGNORE INTO candidate_pairs VALUES (?, ?)",
                        pairs_list[i : i + batch],
                    )
                cursor.execute("COMMIT")
                cursor.execute("SELECT COUNT(*) FROM candidate_pairs")
                after = cursor.fetchone()[0]
                added = after - before
                conn.close()
                self.logger.info(
                    f"[name_blocking] SQLite: {before:,} previos + {len(pairs):,} nombre → "
                    f"{after:,} totales (+{added:,} nuevos) en {time.time() - start:.2f}s"
                )
                self.metrics["name_blocking_pairs"] = len(pairs)
                self.metrics["name_blocking_added"] = added
            except Exception as e:
                self.logger.error(
                    f"[name_blocking] Falló la inserción en SQLite ({e!r}); "
                    f"se continúa con los candidatos previos."
                )
            return candidates

        # Caso C: candidatos vacíos.
        if not candidates:
            self.logger.info(
                f"[name_blocking] No había candidatos previos; usando solo "
                f"{len(pairs):,} pares del bloqueo de nombre."
            )
            self.metrics["name_blocking_pairs"] = len(pairs)
            self.metrics["name_blocking_added"] = len(pairs)
            return set(pairs)

        self.logger.warning(
            f"[name_blocking] Formato de candidatos desconocido ({type(candidates).__name__}); "
            f"se omite el bloqueo de nombre."
        )
        return candidates

    def _is_name_blocking_enabled(self) -> bool:
        """Decide si activar el bloqueo multi-pasada por nombre (default: False).

        Default OFF en v2.6.0: empíricamente no mueve F1 en los datasets de
        prueba (el scorer filtra el ruido y no se aprovechan los TP nuevos
        que aporta). Se mantiene implementado y testeado como infraestructura
        para futuras mejoras del scorer (pesos por origen del par,
        re-scoring específico para pares de bloqueo, etc.). Opt-in vía
        perfil o config global.
        """
        if "enable_name_blocking" in self.profile:
            return bool(self.profile["enable_name_blocking"])
        return bool(self.config.get("enable_name_blocking", False))
