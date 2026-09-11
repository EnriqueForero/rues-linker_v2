"""
engine.disk_based — record_linkage_pipeline

Componentes:
    - class DiskBasedLSHEngine  (origen: notebook celda [120])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import gc
import hashlib
import json
import logging
import os
import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import (
    Any,
)

import h5py
import numpy as np
import pandas as pd
from datasketch import MinHash
from tqdm import tqdm

from ...utils.almacenamiento import copiar_si_existe, dir_trabajo_seguro, es_ruta_fuse
from .defaults import LSHDefaults
from .metrics import EngineMetrics
from .politica_pares import pares_permitidos
from .state import EngineState
from .vectorized_minhash import VectorizedMinHasher


def _hash_rows_stable(band_data: np.ndarray) -> np.ndarray:
    """Hash determinista de 64 bits para cada fila de una banda LSH.

    Reemplaza ``hash(row.tobytes())`` (randomizado por PYTHONHASHSEED, inestable
    entre procesos). Usa FNV-1a de 64 bits acumulado sobre las columnas uint64
    de la banda — vectorizado sobre todas las filas a la vez. Determinista, así
    que el índice LSH es consistente tras reinicios de sesión.

    Args:
        band_data: Array (n_filas, rows_per_band) de uint64 con los mínimos de
            la banda para cada registro.

    Returns:
        Array (n_filas,) de int64 con el hash estable de cada fila.
    """
    if band_data.ndim == 1:
        band_data = band_data.reshape(-1, 1)
    n_rows, n_cols = band_data.shape
    # FNV-1a de 64 bits, vectorizado por columna sobre todas las filas.
    fnv_offset = np.uint64(14695981039346656037)
    fnv_prime = np.uint64(1099511628211)
    acc = np.full(n_rows, fnv_offset, dtype=np.uint64)
    cols = band_data.astype(np.uint64, copy=False)
    with np.errstate(over="ignore"):  # el overflow uint64 es el wrap deseado
        for c in range(n_cols):
            acc = (acc ^ cols[:, c]) * fnv_prime
    # A int64 con signo para almacenar en SQLite (rango de 64 bits con signo).
    return acc.astype(np.int64)


_TABLA_CONFUSABLES_OCR = str.maketrans(
    {"0": "O", "1": "I", "2": "Z", "3": "E", "4": "A", "5": "S", "6": "G", "7": "T", "8": "B"}
)


def _normalizar_confusables_ocr(textos: np.ndarray) -> np.ndarray:
    """Mapea dígitos OCR-confundibles a su letra en textos de firma.

    "S01UCIONES B0G0TA" → "SOIUCIONES BOGOTA": los n-gramas de la firma dejan
    de fragmentarse por dígitos intrusos. El mapeo es global e idéntico en
    ambos lados de cualquier par, así que no introduce asimetrías; los NIT no
    pasan por aquí (esto es solo el texto de nombre que firma LSH).
    """
    serie = pd.Series(textos, copy=False).astype("string").fillna("")
    return serie.str.translate(_TABLA_CONFUSABLES_OCR).to_numpy(dtype=object)


def _content_fingerprint(texts: np.ndarray, num_perm: int, ngram: int) -> str:
    """Huella de contenido del corpus para validar checkpoints de firmas.

    v0.7.4 (cierre de deuda): ``_validate_signatures_file`` validaba solo por
    ``(n_records, num_perm, ngram)``. Dos datasets DISTINTOS con el mismo número
    de filas reusaban firmas incorrectas — bug observado dos veces (baseline
    Sprint 0.9.0 con output truncado a 1131/12427). Desde v0.13 la huella
    recorre todas las filas y el valor completo por chunks, además de incluir
    los parámetros del hashing.

    Args:
        texts: Array de nombres limpios (``NOMBRE_LIMPIO``).
        num_perm: Permutaciones del MinHash.
        ngram: Tamaño de n-grama.

    Returns:
        Huella hexadecimal de 16 caracteres.
    """
    import hashlib

    from ...pipeline.fingerprints import fingerprint_dataframe

    # La huella anterior muestreaba 1/1000 filas y truncaba cada valor a 50
    # caracteres: cambiar cualquier otra fila reutilizaba firmas obsoletas.
    # El helper común recorre el corpus completo por chunks acotados.
    frame = pd.DataFrame({"NOMBRE_LIMPIO": pd.Series(texts, copy=False)})
    content = fingerprint_dataframe(frame)
    h = hashlib.sha256()
    h.update(f"perm={num_perm};ngram={ngram};".encode())
    h.update(content.encode("ascii"))
    return h.hexdigest()[:16]


class DiskBasedLSHEngine:
    """
    Motor LSH de producción con almacenamiento en disco para Google Colab.

    Diseñado para procesar datasets de millones de registros en entornos
    con memoria limitada mediante:

    - Almacenamiento de firmas MinHash en HDF5 comprimido
    - Índice LSH en SQLite con configuración de alto rendimiento
    - Procesamiento por bandas para evitar explosión de memoria
    - Checkpointing transaccional para recuperación ante interrupciones

    Parameters
    ----------
    profile : Dict[str, Any], optional
        Perfil de configuración con parámetros LSH.
    config : Dict[str, Any], optional
        Configuración global del pipeline.

    Examples
    --------
    >>> engine = DiskBasedLSHEngine(profile, config)
    >>> candidates = engine.find_candidates(df, output_dir='/tmp/output')
    >>> print(engine.get_stats())
    >>> engine.cleanup()
    """

    VERSION: str = "4.0.0"

    # ═══════════════════════════════════════════════════════════════════════════
    # INICIALIZACIÓN Y CONFIGURACIÓN
    # ═══════════════════════════════════════════════════════════════════════════

    def __init__(
        self, profile: dict[str, Any] | None = None, config: dict[str, Any] | None = None
    ) -> None:
        """Inicializa el motor LSH."""
        self.logger = logging.getLogger("DiskBasedLSHEngine")

        # Configuración
        self.profile: dict[str, Any] = profile.copy() if profile else {}
        self.config: dict[str, Any] = config.copy() if config else {}

        # Estado
        self.state: EngineState = EngineState.UNINITIALIZED
        self.metrics: EngineMetrics = EngineMetrics()

        # Rutas (se configuran en find_candidates)
        self._storage_dir: Path | None = None
        #: Destino definitivo (puede estar en Drive); ver política anti-FUSE.
        self._final_storage_dir: Path | None = None
        #: True solo si el destino final está montado por FUSE.
        self._sync_final: bool = False
        self._signatures_file: Path | None = None
        self._index_db_file: Path | None = None
        self._candidates_db_file: Path | None = None
        self._checkpoint_file: Path | None = None
        self._content_fp: str | None = None
        self._index_fp: str | None = None
        self._candidate_fp: str | None = None

        # Conexiones activas
        self._active_connections: list[sqlite3.Connection] = []

        # Extraer parámetros
        self._extract_and_validate_parameters()

        # Configuración SQLite optimizada
        self._sqlite_config: dict[str, Any] = {
            "journal_mode": "DELETE",  # <--- CRÍTICO: Cambiar de WAL a DELETE u OFF
            "synchronous": "OFF",  # Optimización para velocidad
            "cache_size": -512000,  # ~500MB RAM
            "temp_store": "MEMORY",  # Temporales en RAM, no en disco lento
            "mmap_size": 0,  # Desactivar mmap en sistemas de red, antes estaba:  536870912,
            "page_size": 32768,  # Bloques grandes para menos I/O
            "locking_mode": "EXCLUSIVE",  # ANtes estaba: 'NORMAL',
            # 'wal_autocheckpoint': 1000, <-- BORRAR ESTA LÍNEA (No aplica en modo DELETE)
        }

        self.state = EngineState.INITIALIZED
        self.logger.info(
            f"DiskBasedLSHEngine v{self.VERSION} inicializado | "
            f"perm={self._num_perm}, th={self._threshold:.3f}, "
            f"ngram={self._ngram}, chunk={self._chunk_size:,}"
        )

    def _extract_and_validate_parameters(self) -> None:
        """Extrae y valida parámetros de configuración."""
        defaults = LSHDefaults()
        active_params: dict[str, Any] = {}

        # Prioridad: config global > profile directo > defaults
        if "profiles" in self.config:
            profile_name = self.config.get("profile", "")
            if profile_name and profile_name in self.config["profiles"]:
                active_params = self.config["profiles"][profile_name].copy()

        if self.profile:
            active_params.update(self.profile)

        # Extraer con validación
        self._num_perm = self._validate_int(
            active_params.get("lsh_permutations", defaults.PERMUTATIONS),
            16,
            512,
            "lsh_permutations",
        )
        self._threshold = self._validate_float(
            active_params.get("lsh_threshold", defaults.THRESHOLD), 0.1, 0.99, "lsh_threshold"
        )
        self._ngram = self._validate_int(
            active_params.get("lsh_ngram", defaults.NGRAM_SIZE), 1, 5, "lsh_ngram"
        )
        self._chunk_size = self._validate_int(
            active_params.get("lsh_chunk_size", defaults.CHUNK_SIZE),
            1000,
            500_000,
            "lsh_chunk_size",
        )
        self._batch_size = self._validate_int(
            active_params.get("sqlite_batch_size", defaults.BATCH_SIZE),
            1000,
            200_000,
            "sqlite_batch_size",
        )
        self._memory_threshold = self._validate_int(
            active_params.get("memory_threshold_candidates", defaults.MEMORY_THRESHOLD_CANDIDATES),
            0,
            50_000_000,
            "memory_threshold_candidates",
        )
        self._max_bucket_size = self._validate_int(
            active_params.get("max_bucket_size", defaults.MAX_BUCKET_SIZE),
            10,
            5000,
            "max_bucket_size",
        )
        self._force_disk = bool(active_params.get("force_disk_results", False))

        # ── P0-1 (v2.5.0): bloqueo por NIT base como complemento al LSH ──
        # Por defecto activo: ataca la causa raíz del cuello de recall
        # (pares con mismo NIT pero nombres incomparables por LSH).
        self._enable_nit_blocking = bool(active_params.get("enable_nit_blocking", True))
        # v0.17.0 — candidatos robustos a OCR: aplica un mapeo determinista de
        # confusables dígito→letra SOLO al texto que se firma (LSH); el score,
        # el veto y los nombres persistidos siguen usando NOMBRE_LIMPIO
        # intacto, así que esta perilla solo puede AGREGAR candidatos, que
        # luego pasan el filtro completo de L3. Default False = paridad.
        self._ocr_confusables_en_firma = bool(active_params.get("ocr_confusables_en_firma", False))
        self._nit_blocking_neighbors = bool(active_params.get("nit_blocking_neighbors", True))
        # v0.17.4 — radio de la vecindad por borrados. Bloquear más
        # estrecho que la tolerancia que el scorer va a aceptar pone un
        # techo al recall que ningún umbral posterior puede levantar, así
        # que el perfil lo sube junto con tolerancia_digitacion.
        self._nit_blocking_radio = self._validate_int(
            active_params.get("nit_blocking_radio", 1), 0, 3, "nit_blocking_radio"
        )
        self._nit_blocking_max_bucket = self._validate_int(
            active_params.get("nit_blocking_max_bucket", 200),
            2,
            5000,
            "nit_blocking_max_bucket",
        )
        self._nit_blocking_column = str(active_params.get("nit_blocking_column", "NIT_BASE"))

        # ── C31 (v0.20.0): bloqueo por llaves declaradas ──────────────────
        # El LSH de nombre y el bloqueo por identificador no alcanzan cuando
        # el nombre viene muy corrupto o falta el identificador. Medido sobre
        # el conjunto institucional, el recall de BLOQUEO era 0,730 en el
        # estrato de ruido: casi un tercio de los pares verdaderos no llegaba
        # a ser candidato, y ahí ningún umbral posterior los recupera.
        # Formato: "COLUMNA:canonicalizador[:max_bloque]".
        self._llaves_bloqueo = self._parsear_llaves(active_params.get("llaves_bloqueo", ()))
        # C31b: bloqueo por token raro. Formato:
        # "COLUMNA[:frecuencia_maxima[:max_bloque]]".
        self._tokens_bloqueo = self._parsear_tokens(active_params.get("tokens_bloqueo", ()))

        # ── v0.7.2 (Sprint 0.8.2, Tarea 2.2): Cache persistente de firmas MinHash ──
        # Si ``minhash_cache_dir`` está en el perfil/config, las firmas se cachean
        # entre corridas indexadas por hash del contenido. Acelera iteraciones
        # de calibración (Optuna, ajuste de umbrales) donde el dataset no cambia
        # pero los parámetros posteriores sí. NO interfiere con la reutilización
        # HDF5 dentro de UNA corrida (que sigue funcionando como antes).
        # Default: None (deshabilitado, comportamiento previo a v0.7.2).
        cache_dir_raw = active_params.get("minhash_cache_dir", None)
        self._minhash_cache_dir: Path | None = Path(cache_dir_raw) if cache_dir_raw else None
        self._minhash_cache_max_gb = float(active_params.get("minhash_cache_max_gb", 5.0))

    @staticmethod
    def _parsear_llaves(valor: Any) -> tuple[Any, ...]:
        """Normaliza la declaración de llaves de bloqueo a objetos validados.

        Admite ``LlaveBloqueo`` ya construidos o cadenas
        ``"COLUMNA:canonicalizador[:max_bloque]"``, para que la perilla se
        pueda escribir igual en un perfil de Python que en la línea de órdenes.
        """
        from .llaves_extra import LlaveBloqueo

        if not valor:
            return ()
        if isinstance(valor, LlaveBloqueo):
            valor = [valor]
        elif isinstance(valor, str):
            # Varias llaves separadas por coma, para que la perilla se pueda
            # escribir igual desde la línea de órdenes que desde un perfil.
            valor = [t for t in (x.strip() for x in valor.split(",")) if t]
        salida = []
        for elemento in valor:
            salida.append(
                elemento
                if isinstance(elemento, LlaveBloqueo)
                else LlaveBloqueo.desde_texto(str(elemento))
            )
        return tuple(salida)

    @staticmethod
    def _parsear_tokens(valor: Any) -> tuple[Any, ...]:
        """Normaliza la declaración de bloqueo por tokens raros."""
        from .llaves_extra import LlaveTokens

        if not valor:
            return ()
        if isinstance(valor, LlaveTokens):
            return (valor,)
        crudos = (
            [t for t in (x.strip() for x in valor.split(",")) if t]
            if isinstance(valor, str)
            else list(valor)
        )
        salida = []
        for elemento in crudos:
            if isinstance(elemento, LlaveTokens):
                salida.append(elemento)
                continue
            partes = [x.strip() for x in str(elemento).split(":")]
            salida.append(
                LlaveTokens(
                    columna=partes[0],
                    frecuencia_maxima=int(partes[1]) if len(partes) > 1 and partes[1] else 50,
                    max_bloque=int(partes[2]) if len(partes) > 2 and partes[2] else 100,
                )
            )
        return tuple(salida)

    @staticmethod
    def _validate_int(value: Any, min_val: int, max_val: int, name: str) -> int:
        """Valida y convierte un valor a entero dentro de un rango."""
        try:
            val = int(value)
            return max(min_val, min(val, max_val))
        except (TypeError, ValueError):
            return min_val

    @staticmethod
    def _validate_float(value: Any, min_val: float, max_val: float, name: str) -> float:
        """Valida y convierte un valor a float dentro de un rango."""
        try:
            val = float(value)
            return max(min_val, min(val, max_val))
        except (TypeError, ValueError):
            return min_val

    # ═══════════════════════════════════════════════════════════════════════════
    # API PÚBLICA REQUERIDA POR EL PIPELINE
    # ═══════════════════════════════════════════════════════════════════════════

    def update_config(self, new_config: dict[str, Any]) -> None:
        """
        Actualiza la configuración del motor en tiempo de ejecución.

        Requerido por RecordLinkageEngine._update_profile().

        Parameters
        ----------
        new_config : Dict[str, Any]
            Nuevos parámetros de configuración.
        """
        if not new_config:
            return

        self.logger.info(f"♻️ Actualizando config: {list(new_config.keys())}")
        self.profile.update(new_config)
        self._extract_and_validate_parameters()

    def find_candidates(
        self,
        df: pd.DataFrame,
        output_dir: str | None = None,
        cross_source_only: bool = False,
        trusted_unique_sources: set | None = None,
    ) -> set[tuple[int, int]] | str:
        """
        Encuentra pares candidatos usando MinHash-LSH.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame con columna 'NOMBRE_LIMPIO'.
        output_dir : str
            Directorio de trabajo (OBLIGATORIO).
        cross_source_only : bool
            Si True, solo cruza entre diferentes fuentes.
        trusted_unique_sources : set | None
            **Aceptado por compatibilidad con `RecordLinkageEngine.link()`,
            pero IGNORADO en este motor base.** El soporte real de fuentes
            confiables vive en `TrustedSourceLSHEngine`, que recibe el set
            en `__init__` (no acá). Si se pasa un set no vacío a este motor
            base, se emite warning. v2.10.0 bug-fix: antes este kwarg
            provocaba TypeError en el path disk_based del pipeline. Ver
            MIGRATION_LOG §21.

        Returns
        -------
        Union[Set[Tuple[int, int]], str]
            Set de pares o ruta a DB SQLite.
        """
        if trusted_unique_sources:
            self.logger.warning(
                "DiskBasedLSHEngine recibió trusted_unique_sources=%s pero "
                "este motor no las procesa. Use TrustedSourceLSHEngine si "
                "necesita el comportamiento de fuentes confiables.",
                sorted(trusted_unique_sources),
            )
        # Validaciones
        if not output_dir:
            raise ValueError("output_dir es OBLIGATORIO")

        if "NOMBRE_LIMPIO" not in df.columns:
            raise ValueError("DataFrame requiere columna 'NOMBRE_LIMPIO'")

        if cross_source_only and "FUENTE" not in df.columns:
            self.logger.warning("cross_source_only=True pero sin columna 'FUENTE'")
            cross_source_only = False

        n_records = len(df)
        if n_records == 0:
            return set()

        self.logger.info(f"═══ LSH para {n_records:,} registros ═══")

        # Configurar rutas
        # ── v0.14.0: política anti-FUSE (Google Drive) ─────────────────────
        # Trabajar directo sobre Drive es la causa clásica de que una corrida
        # de horas muera con "transport endpoint is not connected". Si el
        # destino está montado por FUSE, se procesa en disco local de la VM y
        # se sincroniza por fase; si no, no se paga ninguna copia.
        self._final_storage_dir = Path(output_dir) / "lsh_disk_cache"
        self._final_storage_dir.mkdir(parents=True, exist_ok=True)
        self._sync_final = es_ruta_fuse(self._final_storage_dir)
        if self._sync_final:
            self._storage_dir = dir_trabajo_seguro(self._final_storage_dir)
            self.logger.info(
                f"   💾 Destino en Drive/FUSE: se procesa en local "
                f"({self._storage_dir}) y se sincroniza por fase"
            )
        else:
            self._storage_dir = self._final_storage_dir
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        self._signatures_file = self._storage_dir / "signatures.h5"
        self._index_db_file = self._storage_dir / "lsh_index.db"
        self._candidates_db_file = self._storage_dir / "candidates.db"
        self._checkpoint_file = self._storage_dir / "checkpoint.json"

        total_start = time.time()

        # Reanudación: recupera del destino final lo que una VM caída dejó allí.
        if self._sync_final:
            for nombre in ("signatures.h5", "lsh_index.db", "candidates.db", "checkpoint.json"):
                copiar_si_existe(
                    self._final_storage_dir / nombre,
                    self._storage_dir / nombre,
                    self.logger,
                    "Drive → local",
                )

        try:
            # 1. Firmas
            self._generate_signatures(df)
            self._push_a_destino_final("signatures.h5")
            # v0.17.3 — la firma de bloqueo solo hacía falta para el MinHash.
            # A 4,4 M de filas esa columna pesa cientos de MB que quedaban
            # retenidos durante toda la indexación y la fase de candidatos, que
            # es el tramo donde la corrida de Colab se quedaba sin memoria.
            if "NOMBRE_BLOQUEO" in df.columns:
                del df["NOMBRE_BLOQUEO"]
                gc.collect()

            # 2. Índice. La política se resuelve ANTES para que la
            # indexación anote de paso qué buckets tienen semilla y la fase de
            # candidatos no tenga que recorrer 184 M de filas para saberlo.
            codigos_fuente, politica_fuentes = self._preparar_politica(df, cross_source_only)
            self._semillas = self._semillas_de_cobertura(codigos_fuente, politica_fuentes)
            n_bands, _rows_per_band = self._build_lsh_index(n_records)
            self._push_a_destino_final("lsh_index.db", "checkpoint.json")

            # 3. Candidatos
            self._find_candidate_pairs(
                df, n_bands, cross_source_only, codigos_fuente, politica_fuentes
            )
            if self._enable_nit_blocking:
                # Complementar el LSH de nombre con identidad/vecindad de NIT.
                # INSERT OR IGNORE hace esta fusión idempotente al reanudar o
                # repetir una corrida sobre el mismo candidates.db.
                self._merge_nit_blocking_pairs(df, cross_source_only)
            if self._llaves_bloqueo or self._tokens_bloqueo:
                # C31: teléfono, correo, geografía o cualquier columna
                # declarada. Mismo patrón idempotente que el bloqueo por NIT.
                self._merge_llaves_extra_pairs(df, cross_source_only)
            self._push_a_destino_final("candidates.db", "checkpoint.json")

            self.state = EngineState.CANDIDATES_READY
            self.logger.info(
                f"═══ LSH completado: {self.metrics.candidates_found:,} candidatos "
                f"en {(time.time() - total_start) / 60:.1f} min ═══"
            )

            return self._decide_return_format()

        except Exception as e:
            self.state = EngineState.ERROR
            self.logger.error(f"Error en LSH: {e}", exc_info=True)
            raise RuntimeError(f"Error en LSH: {e}") from e

    def _push_a_destino_final(self, *nombres: str) -> None:
        """Sincroniza artefactos al destino final tras completar una fase.

        No-op cuando el destino no está en un montaje FUSE. Un fallo de Drive
        se degrada a advertencia: perder la copia de respaldo nunca debe tumbar
        una corrida cuyo resultado ya está en disco local.

        Args:
            *nombres: nombres de archivo dentro del directorio de trabajo.
        """
        if not self._sync_final:
            return
        for nombre in nombres:
            copiar_si_existe(
                self._storage_dir / nombre,
                self._final_storage_dir / nombre,
                self.logger,
                "local → Drive",
            )

    def cleanup(self, force: bool = False) -> None:
        """
        Limpia archivos temporales y libera recursos.

        Parameters
        ----------
        force : bool
            Si True, limpia aunque el proceso no haya completado.
        """
        self.logger.info("🧹 Limpiando recursos LSH...")

        # Cerrar conexiones
        for conn in self._active_connections:
            with suppress(Exception):
                conn.close()
        self._active_connections.clear()
        gc.collect()

        if not force and self.state not in (EngineState.CANDIDATES_READY, EngineState.CLEANED):
            self.logger.warning("Use cleanup(force=True) para forzar limpieza")
            return

        # Eliminar archivos
        files_to_remove: list[Path] = []
        if self._signatures_file:
            files_to_remove.append(self._signatures_file)
        if self._index_db_file:
            files_to_remove.extend(
                [
                    self._index_db_file,
                    self._index_db_file.with_suffix(".db-wal"),
                    self._index_db_file.with_suffix(".db-shm"),
                ]
            )
        if self._candidates_db_file:
            files_to_remove.extend(
                [
                    self._candidates_db_file,
                    self._candidates_db_file.with_suffix(".db-wal"),
                    self._candidates_db_file.with_suffix(".db-shm"),
                ]
            )
        if self._checkpoint_file:
            files_to_remove.append(self._checkpoint_file)

        removed = 0
        for f in files_to_remove:
            if f and f.exists():
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass

        if self._storage_dir and self._storage_dir.exists():
            with suppress(OSError):
                self._storage_dir.rmdir()

        self.state = EngineState.CLEANED
        self.logger.info(f"✅ Limpieza: {removed} archivos eliminados")
        gc.collect()

    def get_stats(self) -> dict[str, Any]:
        """Obtiene estadísticas del motor."""
        stats = {
            "engine": f"DiskBasedLSHEngine v{self.VERSION}",
            "version": self.VERSION,
            "state": self.state.name,
            "config": {
                "permutations": self._num_perm,
                "threshold": self._threshold,
                "ngram": self._ngram,
                "chunk_size": self._chunk_size,
            },
            "metrics": {
                "signatures_generated": self.metrics.signatures_generated,
                "index_entries": self.metrics.index_entries,
                "candidates_found": self.metrics.candidates_found,
                "bands_processed": self.metrics.bands_processed,
                "time_signatures_sec": round(self.metrics.time_signatures, 2),
                "time_indexing_sec": round(self.metrics.time_indexing, 2),
                "time_candidates_sec": round(self.metrics.time_candidates, 2),
            },
        }
        return stats

    def should_use_disk_processing(self, n_records: int) -> bool:
        """Determina si usar procesamiento en disco."""
        estimated_gb = (n_records * self._num_perm * 8) / (1024**3)

        try:
            import psutil

            available_gb = psutil.virtual_memory().available / (1024**3)
        except ImportError:
            available_gb = 8.0

        return estimated_gb > available_gb * 0.7 or n_records > 500_000 or self._force_disk

    # ═══════════════════════════════════════════════════════════════════════════
    # GENERACIÓN DE FIRMAS
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _columna_firma(df: pd.DataFrame) -> str:
        """Columna sobre la que se calcula la firma MinHash.

        Prefiere ``NOMBRE_BLOQUEO`` (v0.17.1): el nombre podado de vocabulario
        genérico, que es lo que hace selectivos los buckets. Cae a
        ``NOMBRE_LIMPIO`` cuando el frame viene de una ruta que no la produce,
        de modo que motores y checkpoints anteriores siguen funcionando.
        """
        return "NOMBRE_BLOQUEO" if "NOMBRE_BLOQUEO" in df.columns else "NOMBRE_LIMPIO"

    def _generate_signatures(self, df: pd.DataFrame) -> None:
        """Genera y almacena firmas MinHash en HDF5.

        v0.7.2 (Sprint 0.8.2, Tarea 2.2): Si ``minhash_cache_dir`` está
        configurado, intenta primero cargar las firmas desde el cache
        persistente (cross-corrida). Cache hit → 0.5s; miss → comportamiento
        original (~6 min en 1.97M) + escritura asíncrona al cache.
        """
        # v0.7.4: huella de contenido para validar checkpoints por CONTENIDO,
        # no solo por número de filas. Cierra el bug de checkpoints stale.
        _texts_all = df[self._columna_firma(df)].to_numpy(dtype=object)
        if self._ocr_confusables_en_firma:
            _texts_all = _normalizar_confusables_ocr(_texts_all)
        content_fp = _content_fingerprint(_texts_all, self._num_perm, self._ngram)
        self._content_fp = content_fp

        if self._signatures_file.exists():
            if self._validate_signatures_file(len(df), content_fingerprint=content_fp):
                self.logger.info("📝 Reutilizando firmas existentes (huella validada)")
                self.state = EngineState.SIGNATURES_READY
                return
            self.logger.info("♻️  Firmas existentes no coinciden con el corpus; regenerando")
            self._signatures_file.unlink()

        n_records = len(df)

        # ── v0.7.2: intento de carga desde cache persistente ───────────
        cached_sigs = None
        cache_instance = None
        # El cache persistente hashea NOMBRE_LIMPIO sin transformar: con el
        # mapeo OCR activo, un hit devolvería firmas del texto equivocado.
        # Hasta que la llave del cache incluya la perilla, el mapeo lo omite.
        if self._minhash_cache_dir is not None and not self._ocr_confusables_en_firma:
            from .cache import MinHashCache  # import local: opt-in

            cache_instance = MinHashCache(
                cache_dir=self._minhash_cache_dir,
                max_size_gb=self._minhash_cache_max_gb,
            )
            cached_sigs = cache_instance.get(
                df,
                num_perm=self._num_perm,
                ngram=self._ngram,
                seed=42,
            )

        if cached_sigs is not None:
            # CACHE HIT: escribir directo al HDF5 sin recalcular nada.
            self.logger.info(
                "✅ Firmas recuperadas del cache (%d registros, shape=%s)",
                n_records,
                cached_sigs.shape,
            )
            start_time = time.time()
            with h5py.File(str(self._signatures_file), "w") as hf:
                chunk_rows = min(self._chunk_size, n_records)
                hf.create_dataset(
                    "signatures",
                    data=cached_sigs,
                    chunks=(chunk_rows, self._num_perm),
                    compression="gzip",
                    compression_opts=1,
                )
                hf.attrs["n_records"] = n_records
                hf.attrs["num_perm"] = self._num_perm
                hf.attrs["ngram"] = self._ngram
                hf.attrs["version"] = self.VERSION
                hf.attrs["content_fp"] = content_fp  # v0.7.4: validación por contenido
            self.metrics.signatures_generated = n_records
            self.metrics.time_signatures = time.time() - start_time
            self.state = EngineState.SIGNATURES_READY
            return

        # ── CACHE MISS o cache deshabilitado: ruta original ────────────
        self.logger.info(f"📝 Generando {n_records:,} firmas MinHash...")
        start_time = time.time()

        with h5py.File(str(self._signatures_file), "w") as hf:
            chunk_rows = min(self._chunk_size, n_records)
            signatures_ds = hf.create_dataset(
                "signatures",
                shape=(n_records, self._num_perm),
                dtype="uint64",
                chunks=(chunk_rows, self._num_perm),
                compression="gzip",
                compression_opts=1,
            )

            hf.attrs["n_records"] = n_records
            hf.attrs["num_perm"] = self._num_perm
            hf.attrs["ngram"] = self._ngram
            hf.attrs["version"] = self.VERSION
            hf.attrs["content_fp"] = content_fp  # v0.7.4: validación por contenido

            texts = df[self._columna_firma(df)].values
            if self._ocr_confusables_en_firma:
                texts = _normalizar_confusables_ocr(np.asarray(texts, dtype=object))

            # v2.3.0: firmas vectorizadas con NumPy en vez de un objeto MinHash
            # de datasketch por registro. Mismo esquema de hashing (familia
            # lineal sobre el primo de Mersenne) y determinista. Ver
            # MIGRATION_LOG §13.1. El hasher se construye una sola vez.
            hasher = VectorizedMinHasher(num_perm=self._num_perm, ngram=self._ngram, seed=42)

            with tqdm(total=n_records, desc="Generando firmas", unit="rec") as pbar:
                for start in range(0, n_records, self._chunk_size):
                    end = min(start + self._chunk_size, n_records)

                    batch_sigs = hasher.signatures_batch(texts[start:end])

                    signatures_ds[start:end] = batch_sigs
                    pbar.update(end - start)

                    if (start // self._chunk_size) % LSHDefaults.GC_INTERVAL == 0:
                        del batch_sigs
                        gc.collect()

        self.metrics.signatures_generated = n_records
        self.metrics.time_signatures = time.time() - start_time
        self.state = EngineState.SIGNATURES_READY
        self.logger.info(f"✅ Firmas generadas en {self.metrics.time_signatures:.1f}s")

        # ── v0.7.2: guardar en cache para la próxima corrida ───────────
        if cache_instance is not None:
            try:
                # Leemos las firmas que acabamos de escribir al HDF5.
                # Esto es marginalmente más caro que mantenerlas en RAM
                # durante la generación, pero evita duplicar memoria para
                # datasets grandes (1.97M × 128 × 8 bytes = ~2 GB).
                with h5py.File(str(self._signatures_file), "r") as hf:
                    sigs_to_cache = hf["signatures"][:]
                key = cache_instance.put(
                    df,
                    num_perm=self._num_perm,
                    ngram=self._ngram,
                    seed=42,
                    signatures=sigs_to_cache,
                )
                self.logger.info("💾 Firmas guardadas en cache (key=%s)", key)
            except Exception as exc:
                # El cache es opt-in y no-crítico. Un fallo aquí no debe
                # romper el pipeline — solo se loggea y la próxima corrida
                # generará de nuevo.
                self.logger.warning("⚠️  No se pudo guardar cache MinHash: %s", exc)

    def _create_minhash(self, text: Any, max_val: int) -> np.ndarray:
        """Crea firma MinHash para un texto (un registro).

        DEPRECADO en v2.3.0: ya no se usa internamente. La generación de firmas
        ahora es vectorizada vía ``VectorizedMinHasher`` (ver ``_generate_signatures``
        y MIGRATION_LOG §13.1). Se conserva por compatibilidad con código externo
        que pudiera invocarlo. Para nuevas firmas usa ``VectorizedMinHasher``.
        """
        if pd.isna(text):
            text = ""
        elif not isinstance(text, str):
            text = str(text)

        if len(text) < self._ngram:
            return np.full(self._num_perm, max_val, dtype=np.uint64)

        try:
            minhash = MinHash(num_perm=self._num_perm)
            for i in range(len(text) - self._ngram + 1):
                minhash.update(text[i : i + self._ngram].encode("utf-8"))
            return minhash.hashvalues
        except Exception:
            return np.full(self._num_perm, max_val, dtype=np.uint64)

    def _validate_signatures_file(
        self, expected_records: int, content_fingerprint: str | None = None
    ) -> bool:
        """Valida archivo de firmas existente.

        v0.7.4: además de ``(n_records, num_perm, ngram)``, valida la huella
        de contenido si se proporciona. Esto evita reusar firmas de un corpus
        DISTINTO que casualmente tenga el mismo número de registros.
        Checkpoints viejos sin huella (``content_fp`` ausente) se consideran
        inválidos cuando se exige huella — fuerza regeneración una vez, segura.
        """
        try:
            with h5py.File(str(self._signatures_file), "r") as hf:
                basic_ok = (
                    "signatures" in hf
                    and hf.attrs.get("n_records", 0) == expected_records
                    and hf.attrs.get("num_perm", 0) == self._num_perm
                    and hf.attrs.get("ngram", 0) == self._ngram
                    and str(hf.attrs.get("version", "")) == self.VERSION
                )
                if not basic_ok:
                    return False
                if content_fingerprint is not None:
                    stored_fp = hf.attrs.get("content_fp", None)
                    # Si el checkpoint no trae huella (formato viejo) o no
                    # coincide, NO reusar — regenerar es barato y seguro.
                    if stored_fp != content_fingerprint:
                        return False
                return True
        except Exception:
            return False

    # ═══════════════════════════════════════════════════════════════════════════
    # CONSTRUCCIÓN DE ÍNDICE LSH
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_lsh_index(self, n_records: int) -> tuple[int, int]:
        """
        Construye el índice LSH con validación robusta y eficiente.

        Versión V4.4 - Combina eficiencia, robustez y principio DRY.

        Parameters
        ----------
        n_records : int
            Número total de registros a indexar.

        Returns
        -------
        Tuple[int, int]
            (número_de_bandas, filas_por_banda)
        """
        n_bands, rows_per_band = self._calculate_optimal_bands()
        self._index_fp = self._index_run_fingerprint(n_records, n_bands, rows_per_band)
        eff_thresh = (1.0 / n_bands) ** (1.0 / rows_per_band)

        self.logger.info(
            f"📊 Config: {n_bands} bandas × {rows_per_band} filas "
            f"(threshold efectivo: {eff_thresh:.3f})"
        )

        # 1. Verificar si ya está completo y válido (con n_records para umbral preciso)
        if self._is_index_complete(n_bands, n_records, self._index_fp):
            self.logger.info("📊 Índice existente verificado (tamaño + metadatos + datos)")
            self.state = EngineState.INDEX_READY
            return n_bands, rows_per_band

        # 2. Cargar cualquier espejo JSON ANTES de limpiar, pero usar como
        # autoridad el progreso guardado en la misma transacción SQLite que
        # inserta cada chunk. Un JSON separado deja una ventana COMMIT→JSON:
        # tras una caída, el chunk ya existe en la tabla pero parece pendiente
        # y se duplica al reanudar. SQLite elimina esa ambigüedad.
        checkpoint = self._load_checkpoint()
        total_chunks = max(1, (n_records + self._chunk_size - 1) // self._chunk_size)
        start_chunk = self._load_index_progress(
            n_records=n_records,
            n_bands=n_bands,
            rows_per_band=rows_per_band,
            total_chunks=total_chunks,
            index_fingerprint=self._index_fp,
        )

        if start_chunk > 0:
            self.logger.info(f"♻️ Reanudando indexación desde chunk {start_chunk}/{total_chunks}")
            self.metrics.resumed_from_checkpoint = True
            json_chunk = checkpoint.get("completed_chunks") if checkpoint else None
            if json_chunk is not None and int(json_chunk) != start_chunk:
                self.logger.warning(
                    "Checkpoint JSON atrasado o adelantado (%s vs SQLite=%s); "
                    "se usa el progreso transaccional de SQLite.",
                    json_chunk,
                    start_chunk,
                )
        else:
            self.logger.info("🧹 Índice no reanudable; limpiando archivos previos...")
            self._clean_index_files()
            with self._get_sqlite_connection(self._index_db_file) as conn:
                self._configure_transactional_index_connection(conn)
                self._init_index_schema(conn, n_bands, rows_per_band, n_records, self._index_fp)

        # 3. Indexación chunk-major: el HDF5 almacena firmas por filas y ancho
        # completo. Leer una banda por vez descomprime el corpus completo tantas
        # veces como bandas haya. Aquí cada chunk se lee una sola vez y de ese
        # buffer se derivan todas sus bandas.
        start_time = time.time()

        with self._get_sqlite_connection(self._index_db_file) as conn:
            self._configure_transactional_index_connection(conn)
            with h5py.File(str(self._signatures_file), "r") as hf:
                signatures = hf["signatures"]

                with tqdm(
                    total=total_chunks,
                    initial=start_chunk,
                    desc="Indexando",
                    unit="chunk",
                ) as pbar:
                    for chunk_idx in range(start_chunk, total_chunks):
                        self._index_chunk(
                            conn,
                            signatures,
                            chunk_idx,
                            n_bands,
                            rows_per_band,
                            n_records,
                        )

                        # Espejo humano/Drive solamente. Si no puede escribirse,
                        # el progreso durable ya quedó en SQLite y la corrida no
                        # debe perder horas por un archivo diagnóstico.
                        try:
                            self._save_checkpoint(
                                {
                                    "completed_chunks": chunk_idx + 1,
                                    "total_chunks": total_chunks,
                                    "n_records": n_records,
                                    "index_fp": self._index_fp,
                                    "index_layout": "chunk-major-v1",
                                }
                            )
                        except (OSError, TypeError, ValueError) as exc:
                            self.logger.warning("No se pudo actualizar checkpoint.json: %s", exc)

                        pbar.update(1)
                        self.metrics.bands_processed = n_bands * (chunk_idx + 1) // total_chunks

                        if chunk_idx % LSHDefaults.GC_INTERVAL == 0:
                            gc.collect()

            # 4. Un único índice compuesto. Los índices parciales por banda
            # re-escaneaban una tabla creciente y volvían cuadrática esta fase.
            # El sort usa disco para no competir con MinHash y pandas por RAM.
            index_started = time.time()
            conn.execute("PRAGMA temp_store = FILE")
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_buckets_band_hash "
                    "ON lsh_buckets (band_id, hash_value)"
                )
                conn.execute("INSERT OR REPLACE INTO metadata VALUES ('is_complete', '1')")
                conn.execute(
                    "INSERT OR REPLACE INTO metadata VALUES ('semillas_fp', ?)",
                    (self._huella_semillas(),),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO metadata VALUES ('total_bands', ?)",
                    (str(n_bands),),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO metadata VALUES ('completed_chunks', ?)",
                    (str(total_chunks),),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                with suppress(sqlite3.Error):
                    conn.execute("PRAGMA temp_store = MEMORY")
            self.logger.info(
                f"   🗂️ Índice compuesto construido en {time.time() - index_started:.1f}s"
            )
            self.metrics.bands_processed = n_bands

            try:
                self._save_checkpoint(
                    {
                        "completed_chunks": total_chunks,
                        "total_chunks": total_chunks,
                        "n_records": n_records,
                        "index_fp": self._index_fp,
                        "index_layout": "chunk-major-v1",
                    }
                )
            except (OSError, TypeError, ValueError) as exc:
                self.logger.warning("No se pudo publicar checkpoint.json final: %s", exc)

            with suppress(Exception):
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        # 7. Sincronización y espera para Google Drive
        # v0.14.0: `os.sync()` vacía los buffers de TODO el sistema y la espera
        # de 2 s se pagaba en cada corrida. Con la política anti-FUSE el índice
        # vive en disco local, así que ambos solo tienen sentido cuando el
        # archivo está realmente sobre un montaje FUSE.
        if es_ruta_fuse(self._index_db_file):
            with suppress(AttributeError, OSError):
                sync = getattr(os, "sync", None)
                if callable(sync):
                    sync()
            time.sleep(2)

        # 8. Validación final (DRY: reutiliza _is_index_complete)
        if not self._is_index_complete(n_bands, n_records, self._index_fp):
            # Limpiar para que el próximo intento empiece limpio
            self._clean_index_files()
            raise RuntimeError(
                "❌ Error crítico: El índice se construyó pero falló la validación final. "
                "Los archivos corruptos fueron eliminados. Por favor re-ejecute."
            )

        self.metrics.time_indexing = time.time() - start_time
        self.state = EngineState.INDEX_READY

        file_size = self._index_db_file.stat().st_size
        self.logger.info(
            f"✅ Índice construido: {file_size / (1024 * 1024):.1f} MB "
            f"en {self.metrics.time_indexing:.1f}s"
        )

        return n_bands, rows_per_band

    @staticmethod
    def _configure_transactional_index_connection(conn: sqlite3.Connection) -> None:
        """Activa transacciones recuperables para el índice LSH.

        El context manager histórico usa ``journal_mode=OFF`` para varias BD
        temporales. Eso impide prometer atomicidad si una indexación se corta
        a mitad de chunk. El índice LSH exige un rollback journal real porque
        filas y progreso forman una sola unidad exactly-once.
        """

        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("PRAGMA synchronous = NORMAL")

    def _init_index_schema(
        self,
        conn: sqlite3.Connection,
        n_bands: int,
        rows_per_band: int,
        n_records: int,
        index_fingerprint: str,
    ) -> None:
        """Inicializa schema del índice."""
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lsh_buckets (
                band_id INTEGER NOT NULL,
                hash_value INTEGER NOT NULL,
                record_id INTEGER NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS semilla_buckets (
                band_id INTEGER NOT NULL,
                hash_value INTEGER NOT NULL,
                PRIMARY KEY (band_id, hash_value)
            ) WITHOUT ROWID
        """)
        cursor.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        cursor.execute("INSERT OR REPLACE INTO metadata VALUES ('n_bands', ?)", (str(n_bands),))
        cursor.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('rows_per_band', ?)", (str(rows_per_band),)
        )
        total_chunks = max(1, (n_records + self._chunk_size - 1) // self._chunk_size)
        metadata = {
            "n_records": str(n_records),
            "content_fp": str(self._content_fp),
            "index_fp": index_fingerprint,
            "engine_version": self.VERSION,
            "index_layout": "chunk-major-v1",
            "completed_chunks": "0",
            "total_chunks": str(total_chunks),
            "total_bands": str(n_bands),
            "is_complete": "0",
        }
        cursor.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", metadata.items())
        conn.commit()

    def _index_chunk(
        self,
        conn: sqlite3.Connection,
        signatures: h5py.Dataset,
        chunk_idx: int,
        n_bands: int,
        rows_per_band: int,
        n_records: int,
    ) -> None:
        """Indexa todas las bandas de un chunk en una transacción exactly-once.

        ``completed_chunks`` se actualiza antes del mismo COMMIT que publica
        las filas. Por tanto una caída deja visibles ambas cosas o ninguna.
        Reinvocar un chunk ya comprometido es un no-op; saltarse uno falla de
        inmediato en lugar de producir un índice incompleto silencioso.
        """

        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        try:
            progress_row = cursor.execute(
                "SELECT value FROM metadata WHERE key = 'completed_chunks'"
            ).fetchone()
            if progress_row is None:
                raise RuntimeError("El índice no tiene progreso transaccional inicializado.")
            completed_chunks = int(progress_row[0])
            if completed_chunks > chunk_idx:
                conn.rollback()
                return
            if completed_chunks < chunk_idx:
                raise RuntimeError(
                    "Progreso LSH no contiguo: "
                    f"SQLite={completed_chunks}, chunk solicitado={chunk_idx}."
                )

            start_row = chunk_idx * self._chunk_size
            end_row = min(start_row + self._chunk_size, n_records)
            chunk = signatures[start_row:end_row, :]
            record_ids = list(range(start_row, end_row))
            entries = 0

            # v0.17.4 — de paso se anota qué buckets contienen un registro
            # semilla. Cuesta indexar 19.407 posiciones más por chunk; saberlo
            # después obliga a recorrer las 184 M de filas del índice (5,5 min
            # medidos sobre el universo real).
            semillas = getattr(self, "_semillas", None)
            locales: np.ndarray | None = None
            if semillas is not None and len(semillas):
                corte_ini = int(np.searchsorted(semillas, start_row, side="left"))
                corte_fin = int(np.searchsorted(semillas, end_row, side="left"))
                if corte_fin > corte_ini:
                    locales = semillas[corte_ini:corte_fin] - start_row

            for band_idx in range(n_bands):
                start_col = band_idx * rows_per_band
                band_data = chunk[:, start_col : start_col + rows_per_band]
                hashes = _hash_rows_stable(band_data)
                cursor.executemany(
                    "INSERT INTO lsh_buckets VALUES (?, ?, ?)",
                    zip(
                        [band_idx] * len(record_ids),
                        hashes.tolist(),
                        record_ids,
                        strict=True,
                    ),
                )
                if locales is not None:
                    cursor.executemany(
                        "INSERT OR IGNORE INTO semilla_buckets VALUES (?, ?)",
                        ((band_idx, int(h)) for h in hashes[locales]),
                    )
                entries += len(record_ids)

            cursor.execute(
                "UPDATE metadata SET value = ? WHERE key = 'completed_chunks'",
                (str(chunk_idx + 1),),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

        self.metrics.index_entries += entries

    def _calculate_optimal_bands(self) -> tuple[int, int]:
        """Calcula configuración óptima de bandas."""
        best_b, best_r = 1, self._num_perm
        min_error = float("inf")

        for b in range(1, self._num_perm + 1):
            if self._num_perm % b == 0:
                r = self._num_perm // b
                effective = (1.0 / b) ** (1.0 / r)
                error = abs(effective - self._threshold)

                if effective <= self._threshold:
                    error *= 0.85

                if error < min_error:
                    min_error = error
                    best_b, best_r = b, r

        return best_b, best_r

    def _is_index_complete(
        self,
        expected_bands: int,
        n_records: int | None = None,
        expected_fingerprint: str | None = None,
    ) -> bool:
        """
        Verifica si el índice existe, está completo Y tiene datos válidos.

        Versión V4.4 - Optimizada para eficiencia O(1) y precisión.

        Parameters
        ----------
        expected_bands : int
            Número de bandas esperado según configuración LSH.
        n_records : int, optional
            Número de registros del dataset. Si no se proporciona, usa
            heurística basada en tamaño de archivo.

        Returns
        -------
        bool
            True solo si el índice pasa TODAS las validaciones.
        """
        # 1. Verificar existencia del archivo
        if not self._index_db_file or not self._index_db_file.exists():
            return False

        # 2. Verificar que al menos pueda contener un SQLite real. La antigua
        # heurística fija de 100 KiB rechazaba índices pequeños pero válidos.
        file_size = self._index_db_file.stat().st_size

        if file_size < 4096:
            self.logger.warning(f"⚠️ Índice SQLite truncado: {file_size:,} bytes. Se reconstruirá.")
            return False

        # 3. Validaciones SQL (solo si pasó verificación de tamaño)
        try:
            with self._get_sqlite_connection(self._index_db_file, readonly=True) as conn:
                cursor = conn.cursor()

                # 3a. Verificar metadato is_complete
                cursor.execute("SELECT value FROM metadata WHERE key = 'is_complete'")
                result = cursor.fetchone()
                if not result or result[0] != "1":
                    self.logger.info("📋 Índice marcado como incompleto en metadatos")
                    return False

                # 3b. Verificar número de bandas
                cursor.execute("SELECT value FROM metadata WHERE key = 'total_bands'")
                result = cursor.fetchone()
                if not result or int(result[0]) != expected_bands:
                    self.logger.info(
                        f"📋 Bandas no coinciden: {result[0] if result else 'N/A'} "
                        f"vs esperado {expected_bands}"
                    )
                    return False

                # 3c. Atar el índice al contenido, configuración y código.
                if expected_fingerprint is not None:
                    cursor.execute("SELECT value FROM metadata WHERE key = 'index_fp'")
                    result = cursor.fetchone()
                    if not result or result[0] != expected_fingerprint:
                        self.logger.info("📋 Huella del índice no coincide; se reconstruirá")
                        return False

                if n_records is not None:
                    cursor.execute("SELECT value FROM metadata WHERE key = 'n_records'")
                    result = cursor.fetchone()
                    if not result or int(result[0]) != n_records:
                        self.logger.info("📋 Número de registros del índice no coincide")
                        return False

                # 3d. El layout y su progreso transaccional deben estar
                # completos. Esto invalida índices band-major antiguos aunque
                # por casualidad compartan número de bandas.
                progress = dict(
                    cursor.execute(
                        "SELECT key, value FROM metadata WHERE key IN ("
                        "'index_layout', 'completed_chunks', 'total_chunks')"
                    ).fetchall()
                )
                if progress.get("index_layout") != "chunk-major-v1":
                    self.logger.info("📋 Layout del índice no es chunk-major-v1")
                    return False
                if progress.get("completed_chunks") != progress.get("total_chunks"):
                    self.logger.info("📋 Índice con chunks pendientes")
                    return False

                cursor.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'index' "
                    "AND name = 'idx_buckets_band_hash'"
                )
                if cursor.fetchone() is None:
                    self.logger.info("📋 Falta el índice compuesto de buckets")
                    return False

                # 3e. Verificar que lsh_buckets tenga datos (O(1) con LIMIT 1)
                cursor.execute("SELECT 1 FROM lsh_buckets LIMIT 1")
                if not cursor.fetchone():
                    self.logger.warning(
                        "⚠️ Índice marcado completo pero tabla lsh_buckets vacía. Se reconstruirá."
                    )
                    return False

                # ✅ Todas las validaciones pasaron
                self.logger.info(
                    f"📊 Índice verificado: {file_size / (1024 * 1024):.1f} MB, "
                    f"{expected_bands} bandas"
                )
                return True

        except (sqlite3.DatabaseError, sqlite3.OperationalError) as e:
            self.logger.warning(f"⚠️ Error SQL verificando índice: {e}. Se reconstruirá.")
            return False
        except Exception as e:
            self.logger.warning(f"⚠️ Error inesperado: {e}")
            return False

    def _index_run_fingerprint(self, n_records: int, n_bands: int, rows_per_band: int) -> str:
        """Huella completa de los insumos que determinan el índice LSH."""

        from ...pipeline.fingerprints import fingerprint_config, package_code_fingerprint

        if self._content_fp is None:
            raise RuntimeError("No se calculó la huella del corpus antes del índice")
        return fingerprint_config(
            {
                "protocol": "lsh-index-v3-chunk-major",
                "engine": self.VERSION,
                "code": package_code_fingerprint(),
                "content_fp": self._content_fp,
                "n_records": n_records,
                "num_perm": self._num_perm,
                "ngram": self._ngram,
                "threshold": self._threshold,
                "n_bands": n_bands,
                "rows_per_band": rows_per_band,
                # El tamaño no cambia los buckets, pero sí la interpretación
                # de ``completed_chunks`` al reanudar.
                "chunk_size": self._chunk_size,
                "index_layout": "chunk-major-v1",
            }
        )

    def _load_index_progress(
        self,
        *,
        n_records: int,
        n_bands: int,
        rows_per_band: int,
        total_chunks: int,
        index_fingerprint: str,
    ) -> int:
        """Lee progreso authoritative de SQLite y rechaza estados incompatibles."""

        if not self._index_db_file or not self._index_db_file.is_file():
            return 0
        try:
            with self._get_sqlite_connection(self._index_db_file, readonly=True) as conn:
                rows = dict(
                    conn.execute(
                        "SELECT key, value FROM metadata WHERE key IN ("
                        "'index_layout', 'completed_chunks', 'total_chunks', "
                        "'n_records', 'n_bands', 'rows_per_band', 'index_fp')"
                    ).fetchall()
                )
                expected = {
                    "index_layout": "chunk-major-v1",
                    "total_chunks": str(total_chunks),
                    "n_records": str(n_records),
                    "n_bands": str(n_bands),
                    "rows_per_band": str(rows_per_band),
                    "index_fp": index_fingerprint,
                }
                if any(rows.get(key) != value for key, value in expected.items()):
                    return 0
                completed = int(rows.get("completed_chunks", "0"))
                if not 0 <= completed <= total_chunks:
                    return 0
                if completed > 0:
                    has_rows = conn.execute("SELECT 1 FROM lsh_buckets LIMIT 1").fetchone()
                    if has_rows is None:
                        return 0
                return completed
        except (OSError, ValueError, sqlite3.DatabaseError, sqlite3.OperationalError):
            return 0

    def _candidate_run_fingerprint(
        self, df: pd.DataFrame, n_bands: int, cross_source_only: bool
    ) -> str:
        """Huella de índice, filtros de fuente y parámetros de candidatos."""

        from ...pipeline.fingerprints import fingerprint_config, fingerprint_dataframe

        context: dict[str, pd.Series] = {}
        if cross_source_only and "FUENTE" in df.columns:
            context["FUENTE"] = df["FUENTE"].reset_index(drop=True)
        context_fp = fingerprint_dataframe(pd.DataFrame(context)) if context else "sin-contexto"
        trusted = sorted(str(value) for value in getattr(self, "_trusted_sources", ()))
        return fingerprint_config(
            {
                "protocol": "lsh-candidates-v2",
                "index_fp": self._index_fp,
                "n_bands": n_bands,
                "cross_source_only": cross_source_only,
                "source_context_fp": context_fp,
                "trusted_sources": trusted,
                "max_bucket_size": self._max_bucket_size,
                "min_bucket_size": LSHDefaults.MIN_BUCKET_SIZE,
                # El complemento por NIT muta la misma tabla candidates.db.
                # Si cualquiera de estas palancas cambia, reutilizar el
                # checkpoint anterior dejaría pares sobrantes o faltantes.
                "enable_nit_blocking": self._enable_nit_blocking,
                "nit_blocking_neighbors": self._nit_blocking_neighbors,
                "nit_blocking_radio": self._nit_blocking_radio,
                "nit_blocking_max_bucket": self._nit_blocking_max_bucket,
                "nit_blocking_column": self._nit_blocking_column,
            }
        )

    def _clean_index_files(self) -> None:
        """
        Elimina archivos de índice LSH y archivos asociados.

        Limpia: lsh_index.db, .db-wal, .db-shm, .db-journal, checkpoint.json
        """
        if not self._index_db_file:
            return

        # Archivos de base de datos SQLite
        for ext in ["", "-wal", "-shm", "-journal"]:
            f_path = self._index_db_file.parent / (self._index_db_file.name + ext)
            if f_path.exists():
                try:
                    f_path.unlink()
                    self.logger.debug(f"🧹 Eliminado: {f_path.name}")
                except OSError as e:
                    self.logger.warning(f"No se pudo eliminar {f_path.name}: {e}")

        # Checkpoint
        if self._checkpoint_file and self._checkpoint_file.exists():
            try:
                self._checkpoint_file.unlink()
                self.logger.debug("🧹 Eliminado: checkpoint.json")
            except OSError:
                pass

    # ═══════════════════════════════════════════════════════════════════════════
    # BÚSQUEDA DE CANDIDATOS
    # ═══════════════════════════════════════════════════════════════════════════

    def _find_candidate_pairs(
        self,
        df: pd.DataFrame,
        n_bands: int,
        cross_source_only: bool,
        codigos_fuente: np.ndarray | None = None,
        politica_fuentes: np.ndarray | None = None,
    ) -> None:
        """Genera pares candidatos con limpieza preventiva de archivos corruptos."""
        self.logger.info(f"🔍 Buscando candidatos (cross_source={cross_source_only})...")
        start_time = time.time()

        # v0.17.3 — la fuente de cada registro viaja como CÓDIGO en un arreglo
        # NumPy indexado por record_id (que son posiciones 0..n-1), no como un
        # dict {int: str}. A 4,4 M de registros ese dict pesaba ~558 MB dentro
        # de L2, justo donde la corrida de Colab se quedaba sin memoria; el
        # arreglo int16 equivalente pesa 8,4 MB.
        if codigos_fuente is None or politica_fuentes is None:
            codigos_fuente, politica_fuentes = self._preparar_politica(df, cross_source_only)
        if politica_fuentes is not None:
            self.logger.info(
                f"   🎛️ Política de fuentes: {politica_fuentes.shape[0]} fuentes, "
                f"{int(politica_fuentes.sum())} de {politica_fuentes.size} "
                f"combinaciones permitidas"
            )

        self._candidate_fp = self._candidate_run_fingerprint(df, n_bands, cross_source_only)
        cand_checkpoint = self._load_candidates_checkpoint(self._candidate_fp)
        start_band = cand_checkpoint.get("completed_bands", 0) if cand_checkpoint else 0

        if start_band > 0:
            self.logger.info(f"♻️ Reanudando búsqueda desde banda {start_band}")
        else:
            # === INICIO AGREGADO: Limpieza preventiva para candidates.db ===
            # Si empezamos de cero, aseguramos que no existan residuos corruptos
            for ext in ["", "-wal", "-shm", "-journal"]:
                f_path = self._candidates_db_file.parent / (self._candidates_db_file.name + ext)
                if f_path.exists():
                    try:
                        f_path.unlink()
                        self.logger.info(f"🧹 Limpieza preventiva: eliminado {f_path.name}")
                    except OSError:
                        pass
            # === FIN AGREGADO ===

        # Aquí ya usa su nuevo _get_sqlite_connection robusto
        with self._get_sqlite_connection(self._candidates_db_file) as conn_cand:
            self._init_candidates_schema(conn_cand, self._candidate_fp)

            with self._get_sqlite_connection(self._index_db_file, readonly=True) as conn_lsh:
                semillas = getattr(self, "_semillas", None)
                if semillas is None:
                    semillas = self._semillas_de_cobertura(codigos_fuente, politica_fuentes)
                hashes_utiles = (
                    self._hashes_con_semilla(conn_lsh, semillas) if semillas is not None else None
                )
                with tqdm(
                    total=n_bands, initial=start_band, desc="Candidatos", unit="banda"
                ) as pbar:
                    for band_idx in range(start_band, n_bands):
                        self._process_band_candidates(
                            conn_lsh,
                            conn_cand,
                            band_idx,
                            codigos_fuente,
                            politica_fuentes,
                            None if hashes_utiles is None else hashes_utiles.get(band_idx, []),
                        )
                        self._save_candidates_checkpoint(conn_cand, band_idx + 1)
                        pbar.update(1)

                        if band_idx % 10 == 0:
                            pbar.set_postfix(pairs=f"{self._count_candidates(conn_cand):,}")

                        if band_idx % LSHDefaults.GC_INTERVAL == 0:
                            gc.collect()

            self.metrics.candidates_found = self._count_candidates(conn_cand)

        self.metrics.time_candidates = time.time() - start_time
        self.logger.info(
            f"✅ {self.metrics.candidates_found:,} candidatos en {self.metrics.time_candidates:.1f}s"
        )

    def _preparar_politica(
        self, df: pd.DataFrame, cross_source_only: bool
    ) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Códigos de fuente por registro y matriz de política, o (None, None).

        Se calcula UNA vez por corrida y lo comparten la construcción del
        índice —que aprovecha para anotar los buckets con semilla— y la fase
        de candidatos.
        """
        if not cross_source_only or "FUENTE" not in df.columns:
            return None, None
        nombres_fuente, codigos = np.unique(
            df["FUENTE"].astype(str).to_numpy(), return_inverse=True
        )
        return (
            codigos.astype(np.int16, copy=False),
            self._matriz_politica_fuentes(nombres_fuente, cross_source_only=cross_source_only),
        )

    def _semillas_de_cobertura(
        self, codigos_fuente: np.ndarray | None, politica_fuentes: np.ndarray | None
    ) -> np.ndarray | None:
        """Registros que TIENEN que estar en un bucket para que produzca pares.

        Si la política prohíbe los pares internos de RUES, un bucket formado
        solo por registros de RUES no puede producir nada — y sin embargo la
        fase de candidatos lo leía, lo concatenaba en SQL y lo convertía a
        enteros en Python, banda por banda. Con 4,37 M de registros de RUES y
        19.407 de exportaciones eso es leer 4,4 M de identificadores por banda
        para trabajar sobre 19.407.

        Formalmente se busca un **recubrimiento por vértices** del grafo de la
        política: un conjunto C de fuentes tal que toda combinación permitida
        tenga al menos un extremo en C. Todo bucket útil contiene entonces al
        menos un registro de C. Con dos o tres fuentes el óptimo se encuentra
        por fuerza bruta sobre los 2^k subconjuntos.

        Returns:
            Posiciones de los registros semilla, o ``None`` cuando el
            recubrimiento no ahorra nada (todas las fuentes se deduplican
            entre sí, que es el caso de un dedupe clásico).
        """
        if codigos_fuente is None or politica_fuentes is None:
            return None
        k = int(politica_fuentes.shape[0])
        if k > 12:  # 2^k dejaría de ser gratis; no vale la pena optimizar
            return None
        conteos = np.bincount(codigos_fuente, minlength=k)
        aristas = [(a, b) for a in range(k) for b in range(a, k) if politica_fuentes[a, b]]
        if not aristas:
            return np.empty(0, dtype=np.int64)

        mejor: tuple[int, ...] | None = None
        mejor_costo = None
        for mascara in range(1 << k):
            conjunto = {i for i in range(k) if mascara & (1 << i)}
            if any(a not in conjunto and b not in conjunto for a, b in aristas):
                continue
            costo = int(sum(conteos[i] for i in conjunto))
            if mejor_costo is None or costo < mejor_costo:
                mejor, mejor_costo = tuple(sorted(conjunto)), costo
        if mejor is None or mejor_costo is None:
            return None
        # Filtrar solo paga si recorta de verdad: por debajo de un tercio.
        if mejor_costo * 3 >= len(codigos_fuente):
            return None
        self.logger.info(
            f"   🎯 Cobertura de la política: fuentes {mejor} · {mejor_costo:,} de "
            f"{len(codigos_fuente):,} registros son semilla "
            f"({100 * mejor_costo / len(codigos_fuente):.2f} %)"
        )
        return np.flatnonzero(np.isin(codigos_fuente, np.asarray(mejor)))

    def _huella_semillas(self) -> str:
        """Huella del conjunto de semillas, para no reutilizar una anotación ajena."""
        semillas = getattr(self, "_semillas", None)
        if semillas is None:
            return "sin-semillas"
        datos = np.asarray(semillas, dtype=np.int64)
        resumen = hashlib.sha256(datos.tobytes()).hexdigest()[:16]
        return f"{len(datos)}:{resumen}"

    def _hashes_con_semilla(
        self, conn_lsh: sqlite3.Connection, semillas: np.ndarray
    ) -> dict[int, list[int]]:
        """Buckets (band_id, hash) que contienen al menos un registro semilla.

        Camino normal: la tabla ``semilla_buckets`` que la indexación dejó
        anotada, si su huella corresponde a estas mismas semillas. Camino de
        respaldo —índice de una versión anterior, o reanudado a mitad—: un
        único recorrido del índice, que sigue siendo mucho más barato que uno
        por banda pero cuesta minutos sobre 184 M de filas.
        """
        inicio = time.time()
        cursor = conn_lsh.cursor()
        anotado = False
        with suppress(sqlite3.Error):
            fila = cursor.execute("SELECT value FROM metadata WHERE key = 'semillas_fp'").fetchone()
            anotado = bool(fila) and fila[0] == self._huella_semillas()

        por_banda: dict[int, list[int]] = {}
        if anotado:
            for band_id, hash_value in cursor.execute(
                "SELECT band_id, hash_value FROM semilla_buckets"
            ):
                por_banda.setdefault(int(band_id), []).append(int(hash_value))
            origen = "anotados en la indexación"
        else:
            cursor.execute(
                "CREATE TEMP TABLE IF NOT EXISTS _semillas (record_id INTEGER PRIMARY KEY)"
            )
            cursor.execute("DELETE FROM _semillas")
            cursor.executemany(
                "INSERT OR IGNORE INTO _semillas VALUES (?)", ((int(x),) for x in semillas)
            )
            for band_id, hash_value in cursor.execute(
                "SELECT DISTINCT band_id, hash_value FROM lsh_buckets "
                "WHERE record_id IN (SELECT record_id FROM _semillas)"
            ):
                por_banda.setdefault(int(band_id), []).append(int(hash_value))
            origen = "recorriendo el índice"
        total = sum(len(v) for v in por_banda.values())
        self.logger.info(
            f"   🎯 {total:,} buckets con semilla en {len(por_banda)} bandas "
            f"({origen}, {time.time() - inicio:.1f}s)"
        )
        return por_banda

    def _merge_nit_blocking_pairs(self, df: pd.DataFrame, cross_source_only: bool) -> None:
        """Fusiona los pares de bloqueo por NIT con los candidatos del LSH.

        Ataca la causa del cuello de recall (P0-1): pares cuyas razones
        sociales no comparten n-gramas pero sí comparten NIT —o un NIT a
        distancia 1—. Esos pares nunca entrarían por el LSH de nombre.

        v0.17.4 — reescrito por una regresión de memoria que mataba la sesión.
        La versión anterior pedía al bloqueo un ``set`` de Python con TODOS
        los pares y filtraba después por política: sobre 4,37 M de registros
        el índice de vecindad solo (19 cadenas de Python por registro, 83
        millones de objetos) medía entre 6 y 10 GB y tardaba ~3 min, y la
        corrida moría justo ahí, después de terminar las bandas del LSH.
        Ahora el bloqueo recibe la política, no enumera lo que va a
        descartar, y rinde los pares por lotes que se escriben y se sueltan.

        Args:
            df: DataFrame con índice posicional 0..n-1 y columnas NIT_BASE
                (o la configurada) y FUENTE (si hay política de fuentes).
            cross_source_only: política pedida al motor; la interpretación
                concreta la da :meth:`_source_pair_mask`, que las subclases
                pueden redefinir.
        """
        from .nit_blocking import NitBlockingConfig, iter_pares_por_nit

        nit_col = self._nit_blocking_column
        if nit_col not in df.columns:
            self.logger.info(
                f"[nit_blocking] Columna '{nit_col}' no presente, se omite el "
                f"bloqueo por NIT (esto es esperado en algunos modos)."
            )
            return

        start_time = time.time()
        cfg = NitBlockingConfig(
            enable_exact=True,
            enable_neighbors=self._nit_blocking_neighbors,
            radio_vecindad=self._nit_blocking_radio,
            max_bucket_size=self._nit_blocking_max_bucket,
            min_nit_length=6,
        )

        codigos_fuente: np.ndarray | None = None
        politica_fuentes: np.ndarray | None = None
        if cross_source_only and "FUENTE" in df.columns:
            nombres_fuente, codigos = np.unique(
                df["FUENTE"].astype(str).to_numpy(), return_inverse=True
            )
            codigos_fuente = codigos.astype(np.int16, copy=False)
            politica_fuentes = self._matriz_politica_fuentes(
                nombres_fuente, cross_source_only=cross_source_only
            )

        lotes = iter_pares_por_nit(
            df,
            nit_column=nit_col,
            config=cfg,
            codigos_fuente=codigos_fuente,
            politica_fuentes=politica_fuentes,
            registrador=self.logger,
        )

        insertados = 0
        with self._get_sqlite_connection(self._candidates_db_file) as conn:
            cursor = conn.cursor()
            before = cursor.execute("SELECT COUNT(*) FROM candidate_pairs").fetchone()[0]
            for lote in lotes:
                if not len(lote):
                    continue
                cursor.execute("BEGIN TRANSACTION")
                cursor.executemany(
                    "INSERT OR IGNORE INTO candidate_pairs VALUES (?, ?)",
                    map(tuple, lote.tolist()),
                )
                cursor.execute("COMMIT")
                insertados += len(lote)
                del lote
            after = cursor.execute("SELECT COUNT(*) FROM candidate_pairs").fetchone()[0]
            self.metrics.candidates_found = after

        elapsed = time.time() - start_time
        self.logger.info(
            f"[nit_blocking] Fusión: {before:,} LSH + {insertados:,} por NIT → "
            f"{after:,} totales (+{after - before:,} nuevos) en {elapsed:.2f}s"
        )

    def _merge_llaves_extra_pairs(self, df: pd.DataFrame, cross_source_only: bool) -> None:
        """Fusiona los pares por llave declarada con los candidatos existentes (C31).

        Hermano de :meth:`_merge_nit_blocking_pairs`: mismo contrato, misma
        idempotencia por ``INSERT OR IGNORE`` y la misma disciplina de memoria
        —códigos enteros, bloques filtrados antes de enumerar y lotes que se
        sueltan—. Una llave que pide una columna ausente NO tumba la corrida:
        se avisa y se sigue, porque un cruce real llega con las columnas que
        tiene, no con las que el perfil imaginó.

        Args:
            df: DataFrame con índice posicional 0..n-1.
            cross_source_only: política de fuentes pedida al motor.
        """
        from .llaves_extra import iter_pares_por_llaves, iter_pares_por_tokens

        declaradas = tuple(self._llaves_bloqueo) + tuple(self._tokens_bloqueo)
        presentes = tuple(ll for ll in self._llaves_bloqueo if ll.columna in df.columns)
        por_tokens = tuple(ll for ll in self._tokens_bloqueo if ll.columna in df.columns)
        ausentes = [ll.columna for ll in declaradas if ll.columna not in df.columns]
        if ausentes:
            self.logger.warning(
                f"[llaves_extra] Columnas declaradas y ausentes, se omiten: {ausentes}. "
                f"Disponibles: {sorted(df.columns)[:12]}"
            )
        if not presentes and not por_tokens:
            return

        start_time = time.time()
        codigos_fuente: np.ndarray | None = None
        politica_fuentes: np.ndarray | None = None
        if cross_source_only and "FUENTE" in df.columns:
            nombres_fuente, codigos = np.unique(
                df["FUENTE"].astype(str).to_numpy(), return_inverse=True
            )
            codigos_fuente = codigos.astype(np.int16, copy=False)
            politica_fuentes = self._matriz_politica_fuentes(
                nombres_fuente, cross_source_only=cross_source_only
            )

        insertados = 0
        with self._get_sqlite_connection(self._candidates_db_file) as conn:
            cursor = conn.cursor()
            before = cursor.execute("SELECT COUNT(*) FROM candidate_pairs").fetchone()[0]
            flujos = []
            if presentes:
                flujos.append(
                    iter_pares_por_llaves(
                        df,
                        llaves=presentes,
                        codigos_fuente=codigos_fuente,
                        politica_fuentes=politica_fuentes,
                        registrador=self.logger,
                    )
                )
            flujos.extend(
                iter_pares_por_tokens(
                    df,
                    llave=llave,
                    codigos_fuente=codigos_fuente,
                    politica_fuentes=politica_fuentes,
                    registrador=self.logger,
                )
                for llave in por_tokens
            )
            for flujo in flujos:
                for lote in flujo:
                    if not len(lote):
                        continue
                    cursor.execute("BEGIN TRANSACTION")
                    cursor.executemany(
                        "INSERT OR IGNORE INTO candidate_pairs VALUES (?, ?)",
                        map(tuple, lote.tolist()),
                    )
                    cursor.execute("COMMIT")
                    insertados += len(lote)
                    del lote
            after = cursor.execute("SELECT COUNT(*) FROM candidate_pairs").fetchone()[0]
            self.metrics.candidates_found = after

        self.logger.info(
            f"[llaves_extra] Fusión: {before:,} previos + {insertados:,} por llave → "
            f"{after:,} totales (+{after - before:,} nuevos) en "
            f"{time.time() - start_time:.2f}s"
        )

    def _init_candidates_schema(self, conn: sqlite3.Connection, candidate_fingerprint: str) -> None:
        """Inicializa schema de candidatos."""
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS candidate_pairs (
                idx_0 INTEGER NOT NULL,
                idx_1 INTEGER NOT NULL,
                PRIMARY KEY (idx_0, idx_1)
            ) WITHOUT ROWID
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS _progress (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                completed_bands INTEGER DEFAULT 0,
                fingerprint TEXT NOT NULL
            )
        """)
        columns = {str(row[1]) for row in cursor.execute("PRAGMA table_info(_progress)").fetchall()}
        if "fingerprint" not in columns:
            cursor.execute("ALTER TABLE _progress ADD COLUMN fingerprint TEXT")
        cursor.execute(
            "INSERT OR IGNORE INTO _progress (id, completed_bands, fingerprint) VALUES (1, 0, ?)",
            (candidate_fingerprint,),
        )
        cursor.execute(
            "UPDATE _progress SET fingerprint = ? WHERE id = 1",
            (candidate_fingerprint,),
        )
        conn.commit()

    def _process_band_candidates(
        self,
        conn_lsh: sqlite3.Connection,
        conn_cand: sqlite3.Connection,
        band_idx: int,
        codigos_fuente: np.ndarray | None,
        politica_fuentes: np.ndarray | None,
        hashes_utiles: list[int] | None = None,
    ) -> None:
        """Procesa candidatos de una banda.

        ``hashes_utiles`` (v0.17.4) restringe la consulta a los buckets que
        contienen al menos un registro semilla — los únicos que pueden
        producir un par permitido. Sin esa restricción la banda se leía
        entera: 4,4 M de identificadores concatenados en SQL y convertidos a
        entero en Python, 42 veces, para trabajar sobre 19.407.
        """
        cursor_lsh = conn_lsh.cursor()
        cursor_cand = conn_cand.cursor()
        limites = (LSHDefaults.MIN_BUCKET_SIZE, self._max_bucket_size)

        if hashes_utiles is None:
            cursor_lsh.execute(
                "SELECT hash_value, GROUP_CONCAT(record_id) FROM lsh_buckets "
                "WHERE band_id = ? GROUP BY hash_value "
                "HAVING COUNT(record_id) BETWEEN ? AND ?",
                (band_idx, *limites),
            )
        elif not hashes_utiles:
            return
        else:
            cursor_lsh.execute("CREATE TEMP TABLE IF NOT EXISTS _hashes (h INTEGER PRIMARY KEY)")
            cursor_lsh.execute("DELETE FROM _hashes")
            cursor_lsh.executemany(
                "INSERT OR IGNORE INTO _hashes VALUES (?)", ((h,) for h in hashes_utiles)
            )
            cursor_lsh.execute(
                "SELECT b.hash_value, GROUP_CONCAT(b.record_id) FROM lsh_buckets b "
                "JOIN _hashes ON b.hash_value = _hashes.h "
                "WHERE b.band_id = ? GROUP BY b.hash_value "
                "HAVING COUNT(b.record_id) BETWEEN ? AND ?",
                (band_idx, *limites),
            )

        cursor_cand.execute("BEGIN TRANSACTION")
        for _, records_str in cursor_lsh:
            # El parseo lo hace NumPy en C. La comprensión de lista en
            # Python costaba 4,4 M de llamadas a int() por banda, 42 veces.
            record_ids = np.array(records_str.split(","), dtype=np.int64)
            self.metrics.buckets_processed += 1
            pairs = self._generate_bucket_pairs(record_ids, codigos_fuente, politica_fuentes)
            if pairs:
                cursor_cand.executemany(
                    "INSERT OR IGNORE INTO candidate_pairs VALUES (?, ?)", pairs
                )
        cursor_cand.execute("COMMIT")

    def _source_pair_mask(
        self,
        left_sources: np.ndarray,
        right_sources: np.ndarray,
        *,
        cross_source_only: bool,
    ) -> np.ndarray:
        """Máscara de pares permitidos por la política de fuentes.

        Es el único punto de extensión usado tanto por los buckets LSH como
        por el bloqueo complementario de NIT. Así ambos generadores toman la
        misma decisión para un par de fuentes.
        """

        if not cross_source_only:
            return np.ones(len(left_sources), dtype=bool)
        return left_sources != right_sources

    def _matriz_politica_fuentes(
        self, nombres_fuente: np.ndarray, *, cross_source_only: bool
    ) -> np.ndarray:
        """Matriz k×k que dice si un par entre dos fuentes está permitido.

        Se construye llamando UNA vez a :meth:`_source_pair_mask` con todas
        las combinaciones de fuentes — k es el número de FUENTES (2 o 3), no
        de registros — así la política sigue viviendo en un solo método y las
        subclases (p. ej. ``TrustedSourceLSHEngine``) la siguen gobernando
        sin que este generador conozca sus reglas.

        Args:
            nombres_fuente: nombres únicos, en el orden de sus códigos.
            cross_source_only: política pedida por el caller.

        Returns:
            Matriz booleana ``[i, j]`` = ¿se permite un par entre la fuente
            con código i y la del código j?
        """
        k = len(nombres_fuente)
        izquierda = np.repeat(nombres_fuente, k)
        derecha = np.tile(nombres_fuente, k)
        permitido = self._source_pair_mask(izquierda, derecha, cross_source_only=cross_source_only)
        return np.asarray(permitido, dtype=bool).reshape(k, k)

    def _generate_bucket_pairs(
        self,
        record_ids: list[int],
        codigos_fuente: np.ndarray | None = None,
        politica_fuentes: np.ndarray | None = None,
    ) -> list[tuple[int, int]]:
        """Genera los pares PERMITIDOS de un bucket, sin enumerar los vetados.

        v0.17.3 — antes se materializaban los ``n(n-1)/2`` pares del bucket
        con ``np.triu_indices`` y DESPUÉS se filtraban por política. En este
        cruce el 99,56 % de los registros son RUES (fuente confiable, sin
        deduplicación interna), así que un bucket lleno enumeraba 124.750
        pares para conservar unos 997: **125x de trabajo desperdiciado**, y
        era el costo dominante de la fase de candidatos (36 min medidos sobre
        el universo real).

        Ahora el bucket se parte por fuente y solo se materializan los
        bloques permitidos: el producto cruzado entre fuentes distintas y —
        cuando la política lo permite — los pares internos de cada fuente. El
        costo pasa a ser proporcional a los pares EMITIDOS.

        La semántica es idéntica: la decisión de qué combinaciones valen la
        toma ``politica_fuentes``, derivada de :meth:`_source_pair_mask`.

        Args:
            record_ids: posiciones de los registros del bucket.
            codigos_fuente: código de fuente por record_id, o None si no hay
                política de fuentes que aplicar.
            politica_fuentes: matriz k×k de :meth:`_matriz_politica_fuentes`.

        Returns:
            Lista de pares ``(menor, mayor)``.
        """
        if isinstance(codigos_fuente, dict):
            raise TypeError(
                "codigos_fuente cambió en v0.17.3: era un dict {record_id: nombre} "
                "y ahora es un np.ndarray de códigos indexado por record_id, con "
                "la política en una matriz aparte. El dict pesaba ~558 MB a 4,4 M "
                "de registros. Use _matriz_politica_fuentes() para la política."
            )
        lo, hi = pares_permitidos(
            np.asarray(record_ids, dtype=np.int64),
            None
            if codigos_fuente is None
            else codigos_fuente[np.asarray(record_ids, dtype=np.int64)],
            politica_fuentes,
        )
        return list(zip(lo.tolist(), hi.tolist(), strict=True))

    def _count_candidates(self, conn: sqlite3.Connection) -> int:
        """Cuenta candidatos."""
        return conn.execute("SELECT COUNT(*) FROM candidate_pairs").fetchone()[0]

    def _save_candidates_checkpoint(self, conn: sqlite3.Connection, completed_bands: int) -> None:
        """Guarda checkpoint de candidatos."""
        conn.execute(
            "UPDATE _progress SET completed_bands = ?, fingerprint = ? WHERE id = 1",
            (completed_bands, self._candidate_fp),
        )
        conn.commit()

    def _load_candidates_checkpoint(self, expected_fingerprint: str) -> dict[str, Any] | None:
        """Carga checkpoint de candidatos."""
        if not self._candidates_db_file or not self._candidates_db_file.exists():
            return None
        try:
            with self._get_sqlite_connection(self._candidates_db_file, readonly=True) as conn:
                columns = {
                    str(row[1]) for row in conn.execute("PRAGMA table_info(_progress)").fetchall()
                }
                if "fingerprint" not in columns:
                    return None
                result = conn.execute(
                    "SELECT completed_bands, fingerprint FROM _progress WHERE id = 1"
                ).fetchone()
                if not result or result[1] != expected_fingerprint:
                    return None
                return {"completed_bands": int(result[0]), "fingerprint": result[1]}
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════════════════════
    # DECISIÓN DE RETORNO
    # ═══════════════════════════════════════════════════════════════════════════

    def _decide_return_format(self) -> set[tuple[int, int]] | str:
        """Decide formato de retorno."""
        n = self.metrics.candidates_found

        if self._force_disk or n > self._memory_threshold:
            self.logger.info(f"💾 Retornando DB ({n:,} pares)")
            return str(self._candidates_db_file)

        self.logger.info(f"📥 Cargando {n:,} candidatos a memoria...")
        return self._load_candidates_to_memory()

    def _load_candidates_to_memory(self) -> set[tuple[int, int]]:
        """Carga candidatos a memoria."""
        candidates: set[tuple[int, int]] = set()

        with self._get_sqlite_connection(self._candidates_db_file, readonly=True) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT idx_0, idx_1 FROM candidate_pairs")
            while True:
                rows = cursor.fetchmany(50_000)
                if not rows:
                    break
                candidates.update((r[0], r[1]) for r in rows)

        return candidates

    # ═══════════════════════════════════════════════════════════════════════════
    # CHECKPOINTING
    # ═══════════════════════════════════════════════════════════════════════════

    def _save_checkpoint(self, data: dict[str, Any]) -> None:
        """Guarda checkpoint."""
        if not self._checkpoint_file:
            return

        data["timestamp"] = time.time()
        data["version"] = self.VERSION

        temp = self._checkpoint_file.with_suffix(".tmp")
        with open(temp, "w") as f:
            json.dump(data, f)
        temp.replace(self._checkpoint_file)

    def _load_checkpoint(self) -> dict[str, Any] | None:
        """Carga checkpoint."""
        if not self._checkpoint_file or not self._checkpoint_file.exists():
            return None
        try:
            with open(self._checkpoint_file) as f:
                data = json.load(f)
            return data if data.get("version") == self.VERSION else None
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════════════════════
    # UTILIDADES
    # ═══════════════════════════════════════════════════════════════════════════

    @contextmanager
    def _get_sqlite_connection(
        self, db_path: Path, readonly: bool = False
    ) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager para conexiones SQLite con manejo de corrupción y latencia de Drive.
        Versión corregida V4.2 - Incluye lógica de reintento (Retry) para lectura.
        """
        # Asegurar que sea un objeto Path
        db_path = Path(db_path)

        # Intentar conectar
        try:
            if readonly:
                # --- CORRECCIÓN: Lógica de Reintento para Google Drive ---
                # Si el archivo no aparece inmediatamente, esperar hasta 10 segundos
                if not db_path.exists():
                    self.logger.warning(
                        f"⏳ Esperando sincronización de archivo en disco: {db_path.name}..."
                    )
                    for i in range(5):
                        time.sleep(2)  # Esperar 2 segundos
                        if db_path.exists():
                            self.logger.info(f"✅ Archivo detectado tras {(i + 1) * 2}s.")
                            break

                # Si después de esperar sigue sin existir, lanzar error
                if not db_path.exists():
                    raise FileNotFoundError(f"Base de datos no encontrada tras espera: {db_path}")

                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            else:
                conn = sqlite3.connect(str(db_path))
        except sqlite3.DatabaseError as e:
            self.logger.error(f"❌ BD Corrupta detectada al conectar: {db_path} - {e}")
            # Si estamos en modo escritura, intentar eliminar y reintentar
            if not readonly:
                try:
                    if db_path.exists():
                        db_path.unlink()
                    shm = db_path.with_suffix(".db-shm")
                    wal = db_path.with_suffix(".db-wal")
                    if shm.exists():
                        shm.unlink()
                    if wal.exists():
                        wal.unlink()
                    self.logger.warning(
                        "🧹 Archivos corruptos eliminados. Reintentando conexión nueva..."
                    )
                    conn = sqlite3.connect(str(db_path))
                except Exception as cleanup_error:
                    raise RuntimeError(
                        f"No se pudo recuperar la BD corrupta: {cleanup_error}"
                    ) from e
            else:
                raise

        try:
            cursor = conn.cursor()
            # CONFIGURACIÓN OPTIMIZADA PERO SEGURA
            optimizations = {
                "journal_mode": "OFF",  # Mantener OFF para ahorrar espacio en Drive
                "synchronous": "NORMAL",  # ← CAMBIO: NORMAL es más lento pero seguro en Drive
                "cache_size": -512000,  # ~500MB RAM cache
                "temp_store": "MEMORY",  # Temporales en RAM
                "mmap_size": 268435456,  # 256MB mmap
                "locking_mode": "EXCLUSIVE",  # Evita overhead de locks
            }

            for pragma, value in optimizations.items():
                with suppress(sqlite3.Error):
                    cursor.execute(f"PRAGMA {pragma} = {value}")

            self._active_connections.append(conn)
            yield conn

        except sqlite3.DatabaseError as e:
            self.logger.error(f"❌ Error de base de datos durante operación: {e}")
            raise
        finally:
            if conn in self._active_connections:
                self._active_connections.remove(conn)
            # Cerrar conexión de forma segura
            with suppress(Exception):
                conn.close()
