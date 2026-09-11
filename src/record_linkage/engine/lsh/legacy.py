"""
engine.legacy — record_linkage_pipeline

Componentes:
    - class OptimizedLSHEngine  (origen: notebook celda [119])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import contextlib
import gc
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from typing import Any

import pandas as pd
from datasketch import MinHash, MinHashLSH
from tqdm import tqdm

from ...utils.logger import CustomLogger
from ...utils.memory import MemoryManager
from ...utils.performance import track_performance

# Esquema SQLite del motor LSH legacy. Centralizado como constante de módulo
# para garantizar que `_init_database` cree EXACTAMENTE las tablas que el
# resto del motor consume (candidates, stats, index_data). Cualquier nueva
# tabla que un método del motor referencie debe añadirse aquí.
_LSH_LEGACY_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    idx_0 INTEGER NOT NULL,
    idx_1 INTEGER NOT NULL,
    score REAL,
    PRIMARY KEY (idx_0, idx_1)
);
CREATE INDEX IF NOT EXISTS idx_candidates_0 ON candidates(idx_0);
CREATE INDEX IF NOT EXISTS idx_candidates_1 ON candidates(idx_1);

CREATE TABLE IF NOT EXISTS stats (
    key TEXT PRIMARY KEY,
    value INTEGER
);

-- Tabla de mapeo idx -> fuente del registro. Usada por _process_with_sqlite
-- para filtrar candidatos cuando hay trusted_unique_sources configuradas
-- (un par de la misma fuente confiable se bloquea para evitar dedup interna).
CREATE TABLE IF NOT EXISTS index_data (
    idx INTEGER PRIMARY KEY,
    source TEXT
);
"""


class OptimizedLSHEngine:
    """
    Motor LSH optimizado para búsqueda eficiente de candidatos.
    Soporta procesamiento por lotes y almacenamiento híbrido memoria/disco.
    """

    def __init__(self, profile: dict[str, Any], config: dict[str, Any] | None = None):
        self.profile = profile
        self.config = config or {}
        self.logger = CustomLogger("OptimizedLSHEngine")

        # Parámetros LSH
        self.num_perm = profile.get("lsh_permutations", 128)
        self.threshold = profile.get("lsh_threshold", 0.75)
        self.ngram = profile.get("lsh_ngram", 3)
        self.batch_size = profile.get("lsh_batch_size", 10_000)

        # Configuración de almacenamiento
        self.use_memory_only = self._should_use_memory_only()
        self.candidates_store = None

        # Estadísticas
        self.stats = {
            "total_processed": 0,
            "candidates_found": 0,
            "batches_processed": 0,
            "memory_switches": 0,
        }

    def _should_use_memory_only(self) -> bool:
        """Determinar si usar solo memoria según recursos disponibles."""
        memory_status = MemoryManager.get_memory_status()
        available_gb = memory_status["available_gb"]

        # Usar memoria si hay más de 6GB disponibles
        return available_gb > 6.0

    @track_performance("Búsqueda de candidatos LSH")
    def find_candidates(
        self,
        df: pd.DataFrame,
        output_dir: str | None = None,
        cross_source_only: bool = False,
        trusted_unique_sources: set | None = None,
    ) -> set[tuple[int, int]]:
        """
        Encontrar pares candidatos usando LSH.

        Args:
            df: DataFrame con datos preprocesados
            output_dir: Directorio para archivos temporales
            cross_source_only: DEPRECADO — usar trusted_unique_sources
            trusted_unique_sources: Set de fuentes cuya dedup interna se bloquea (Paso 1.6)

        Returns:
            Conjunto de tuplas (idx1, idx2) de candidatos
        """
        # ✅ Paso 1.6: Construir set de fuentes confiables
        _trusted = set(trusted_unique_sources) if trusted_unique_sources else set()
        # Backward compat: si cross_source_only=True y no hay trusted, bloquear TODAS
        if cross_source_only and not _trusted and "SRC" in df.columns:
            _trusted = set(df["SRC"].unique())

        self.logger.info(
            f"Iniciando búsqueda LSH: {len(df):,} registros, "
            f"threshold={self.threshold:.2f}, permutations={self.num_perm}"
            + (f", trusted_sources={_trusted}" if _trusted else "")
        )

        # [DIAG-2] Verificar parámetros del LSH Engine en memoria (Fase 3)
        if os.environ.get("OPTUNA_DIAGNOSTICS"):
            print(
                f"[DIAG-2] OptimizedLSH: threshold={self.threshold}, "
                f"num_perm={self.num_perm}, ngram={self.ngram}"
            )

        # Inicializar almacenamiento
        if self.use_memory_only:
            self.logger.info("Usando almacenamiento en memoria")
            return self._find_candidates_memory(df, _trusted)
        else:
            self.logger.info("Usando almacenamiento híbrido con SQLite")
            return self._find_candidates_hybrid(df, output_dir, _trusted)

    def _find_candidates_memory(
        self, df: pd.DataFrame, trusted_sources: set
    ) -> set[tuple[int, int]]:
        """Búsqueda de candidatos completamente en memoria."""
        # Inicializar índice LSH
        lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)
        candidates = set()

        # Procesar por lotes
        (len(df) + self.batch_size - 1) // self.batch_size

        with tqdm(total=len(df), desc="Procesando LSH") as pbar:
            for batch_idx, batch_data in enumerate(self._generate_batches(df)):
                # Procesar batch
                batch_candidates = self._process_batch_memory(batch_data, lsh, trusted_sources)

                candidates.update(batch_candidates)
                pbar.update(len(batch_data))

                # Monitorear memoria cada 5 batches
                if batch_idx % 5 == 0:
                    if not MemoryManager.check_memory_availability(1.0):
                        self.logger.warning("Memoria baja detectada, cambiando a modo híbrido")
                        self.stats["memory_switches"] += 1
                        # Continuar con el resto en modo híbrido
                        remaining_df = df.iloc[batch_idx * self.batch_size :]
                        hybrid_candidates = self._find_candidates_hybrid(
                            remaining_df, None, trusted_sources
                        )
                        candidates.update(hybrid_candidates)
                        break

                self.stats["batches_processed"] += 1

        self.stats["candidates_found"] = len(candidates)
        self.logger.info(f"Candidatos encontrados: {len(candidates):,}")

        return candidates

    def _find_candidates_hybrid(
        self, df: pd.DataFrame, output_dir: str | None, trusted_sources: set
    ) -> set[tuple[int, int]]:
        """Búsqueda de candidatos con almacenamiento híbrido."""
        # Crear base de datos temporal
        if output_dir:
            db_path = os.path.join(output_dir, "lsh_candidates.db")
        else:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
            db_path = temp_file.name
            temp_file.close()

        # Inicializar SQLite
        conn = sqlite3.connect(db_path)
        self._init_database(conn)

        try:
            # Procesar con SQLite
            candidates = self._process_with_sqlite(df, conn, trusted_sources)

            self.stats["candidates_found"] = len(candidates)
            self.logger.info(f"Candidatos encontrados: {len(candidates):,}")

            return candidates

        finally:
            conn.close()
            # Limpiar archivo temporal si se creó
            if not output_dir and os.path.exists(db_path):
                with contextlib.suppress(BaseException):
                    os.unlink(db_path)

    def _init_database(self, conn: sqlite3.Connection) -> None:
        """Inicializar esquema de la base de datos SQLite del motor.

        Aplica el esquema completo (`_LSH_LEGACY_SQLITE_SCHEMA`) en una sola
        operación atómica vía `executescript`. Esto garantiza que TODAS las
        tablas usadas por el motor existan antes de cualquier INSERT/SELECT
        — incluyendo `index_data`, que era omitida en v2.0.0 y producía
        OperationalError al activarse trusted sources.

        Args:
            conn: Conexión SQLite a inicializar. La función comitea
                  inmediatamente para asegurar que el esquema persista.
        """
        conn.executescript(_LSH_LEGACY_SQLITE_SCHEMA)
        conn.commit()

    def _generate_batches(self, df: pd.DataFrame) -> Iterator[pd.DataFrame]:
        """Generar batches del DataFrame."""
        for start_idx in range(0, len(df), self.batch_size):
            end_idx = min(start_idx + self.batch_size, len(df))
            yield df.iloc[start_idx:end_idx]

    def _process_batch_memory(
        self, batch: pd.DataFrame, lsh: MinHashLSH, trusted_sources: set
    ) -> set[tuple[int, int]]:
        """Procesar un batch en memoria — Paso 1.6: Trusted Sources."""
        batch_candidates = set()

        # ✅ Paso 1.6: Mapa de fuentes para filtrado selectivo
        if trusted_sources and not hasattr(self, "_source_map"):
            self._source_map = {}

        for idx, row in batch.iterrows():
            # Generar MinHash para el registro
            minhash = self._create_minhash(row["NOMBRE_LIMPIO"])
            if minhash is None:
                continue

            # Buscar candidatos similares ya indexados
            similar_indices = lsh.query(minhash)

            if similar_indices:
                # Fuente del registro actual
                source_actual = row.get("SRC", "UNKNOWN")

                for candidate_idx in similar_indices:
                    # ✅ Paso 1.6: Filtrado selectivo por Trusted Sources
                    if trusted_sources:
                        candidate_source = self._source_map.get(candidate_idx, "UNKNOWN")
                        # Bloquear SOLO si ambos son de la misma fuente confiable
                        if source_actual == candidate_source and source_actual in trusted_sources:
                            continue

                    # Par válido
                    pair = tuple(sorted((idx, candidate_idx)))
                    batch_candidates.add(pair)

            # Agregar al índice LSH
            lsh.insert(idx, minhash)

            # Guardar fuente para comparaciones futuras
            if trusted_sources:
                self._source_map[idx] = row.get("SRC", "UNKNOWN")

        self.stats["total_processed"] += len(batch)
        return batch_candidates

    def _process_with_sqlite(
        self, df: pd.DataFrame, conn: sqlite3.Connection, trusted_sources: set
    ) -> set[tuple[int, int]]:
        """Procesar usando SQLite para gestión eficiente de memoria."""
        cursor = conn.cursor()
        lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)

        # Procesar por batches
        batch_insert = []
        batch_size_db = 10_000

        with tqdm(total=len(df), desc="Procesando LSH (SQLite)") as pbar:
            for batch in self._generate_batches(df):
                for idx, row in batch.iterrows():
                    # Crear MinHash
                    minhash = self._create_minhash(row["NOMBRE_LIMPIO"])
                    if minhash is None:
                        continue

                    # Buscar similares
                    similar_indices = lsh.query(minhash)

                    if similar_indices:
                        source_actual = row.get("SRC", "UNKNOWN")

                        for candidate_idx in similar_indices:
                            # ✅ Paso 1.6: Filtrado selectivo por Trusted Sources
                            if trusted_sources:
                                # Obtener fuente del candidato de la base de datos
                                src_query = """
                                    SELECT source FROM index_data WHERE idx = ?
                                """
                                result = cursor.execute(src_query, (candidate_idx,)).fetchone()
                                candidate_source = result[0] if result else "UNKNOWN"
                                # Bloquear SOLO si ambos son de la misma fuente confiable
                                if (
                                    source_actual == candidate_source
                                    and source_actual in trusted_sources
                                ):
                                    continue

                            # Orden consistente
                            if idx < candidate_idx:
                                pair = (idx, candidate_idx, 0.0)  # score placeholder
                            else:
                                pair = (candidate_idx, idx, 0.0)

                            batch_insert.append(pair)

                    # Agregar al índice
                    lsh.insert(idx, minhash)

                    # Guardar metadata en DB
                    cursor.execute(
                        "INSERT OR IGNORE INTO index_data (idx, source) VALUES (?, ?)",
                        (idx, row.get("SRC", "default")),
                    )

                    # Insertar batch si está lleno
                    if len(batch_insert) >= batch_size_db:
                        self._insert_candidates_batch(cursor, batch_insert)
                        batch_insert = []
                        conn.commit()

                pbar.update(len(batch))
                self.stats["batches_processed"] += 1

        # Insertar últimos candidatos
        if batch_insert:
            self._insert_candidates_batch(cursor, batch_insert)
            conn.commit()

        # Obtener todos los candidatos
        candidates = set()
        cursor.execute("SELECT idx_0, idx_1 FROM candidates")
        for row in cursor:
            candidates.add((row[0], row[1]))

        return candidates

    def _create_minhash(self, text: str) -> MinHash | None:
        """Crear MinHash de un texto."""
        if not text or not isinstance(text, str) or len(text) < self.ngram:
            return None

        # Crear MinHash
        minhash = MinHash(num_perm=self.num_perm)

        # Generar n-gramas
        for i in range(len(text) - self.ngram + 1):
            ngram = text[i : i + self.ngram]
            minhash.update(ngram.encode("utf-8"))

        return minhash

    def _insert_candidates_batch(
        self, cursor: sqlite3.Cursor, candidates: list[tuple[int, int, float]]
    ):
        """Insertar batch de candidatos en SQLite."""
        cursor.executemany(
            "INSERT OR IGNORE INTO candidates (idx_0, idx_1, score) VALUES (?, ?, ?)", candidates
        )

    def update_config(self, new_config: dict[str, Any]):
        """Actualizar configuración del motor."""
        if "lsh_threshold" in new_config:
            self.threshold = new_config["lsh_threshold"]
        if "lsh_permutations" in new_config:
            self.num_perm = new_config["lsh_permutations"]
        if "lsh_batch_size" in new_config:
            self.batch_size = new_config["lsh_batch_size"]

        self.logger.debug("Configuración actualizada")

    def cleanup(self):
        """Limpiar recursos."""
        # Limpiar mapas en memoria
        if hasattr(self, "_source_map"):
            del self._source_map

        # Limpiar estadísticas
        self.stats = {key: 0 for key in self.stats}

        gc.collect()

    def get_stats(self) -> dict[str, Any]:
        """Obtener estadísticas del proceso."""
        return self.stats.copy()
