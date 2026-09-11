"""Publicación y consulta de resultados Parquet sin expansión masiva en pandas.

La ingesta DuckDB produce representantes, mapas de expansión y, opcionalmente,
un payload por fila original. Este módulo es el *sink* de resultados: une esos
artefactos con la correlativa compacta, valida invariantes mediante SQL y
publica Parquet atómicos. La capa de matching no conoce detalles de exporte y
el notebook no necesita materializar millones de filas para hacer QA.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Integral
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from ..ingestion import DuckDBCompactionResult, DuckDBIngestionSettings
from ..ingestion.duckdb import (
    _cleanup_directory,
    _cleanup_file,
    _cleanup_pending_directories,
    _fsync_directory,
    _fsync_file,
    _garbage_collect_generations,
    _ManifestFileLock,
    _relative_manifest_path,
    _validate_compaction_artifacts,
    _validate_dense_ids,
    _write_manifest_atomic,
)

_MANIFEST_SCHEMA_VERSION = 1


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _configure_connection(connection: Any, settings: DuckDBIngestionSettings) -> None:
    connection.execute("SET memory_limit = ?", [settings.memory_limit])
    connection.execute("SET threads = ?", [settings.threads])
    # La correlativa lleva ORIGINAL_INDEX explícito. Forzar el orden físico de
    # casi un millón de filas anchas bloquea el spill del operador y puede
    # agotar incluso un memory_limit válido; las consultas que necesiten orden
    # deben usar esa columna, no depender del layout del Parquet.
    connection.execute("SET preserve_insertion_order = false")
    # Ver nota en ingestion/duckdb.py: en un kernel sin ipywidgets esta
    # opción lanza excepción. Es cosmética; nunca debe tumbar la corrida.
    with contextlib.suppress(Exception):
        connection.execute("SET enable_progress_bar = false")
    if settings.temp_directory is not None:
        temp_directory = Path(settings.temp_directory)
        temp_directory.mkdir(parents=True, exist_ok=True)
        connection.execute("SET temp_directory = ?", [str(temp_directory)])
    if settings.max_temp_directory_size is not None:
        connection.execute("SET max_temp_directory_size = ?", [settings.max_temp_directory_size])


@dataclass(frozen=True)
class TablaParquet:
    """Referencia inmutable a una tabla Parquet con materialización explícita."""

    path: Path
    rows: int
    columns: tuple[str, ...]

    def preview(self, rows: int = 100) -> pd.DataFrame:
        """Carga como máximo ``rows`` filas; nunca la tabla completa por accidente."""

        if isinstance(rows, bool) or not isinstance(rows, Integral):
            raise TypeError("rows debe ser un entero >= 1.")
        if rows < 1:
            raise ValueError("rows debe ser >= 1.")
        import duckdb

        with duckdb.connect(":memory:") as connection:
            return connection.execute(
                "SELECT * FROM read_parquet(?) LIMIT ?", [str(self.path), int(rows)]
            ).fetch_df()

    def to_pandas(self, *, max_rows: int | None = None) -> pd.DataFrame:
        """Materializa conscientemente y permite imponer un límite de seguridad."""

        if max_rows is not None:
            if isinstance(max_rows, bool) or not isinstance(max_rows, Integral):
                raise TypeError("max_rows debe ser un entero >= 1 o None.")
            if max_rows < 1:
                raise ValueError("max_rows debe ser >= 1 o None.")
            if self.rows > max_rows:
                raise MemoryError(
                    f"La tabla tiene {self.rows:,} filas y max_rows={max_rows:,}; "
                    "use preview() o una consulta DuckDB."
                )
        return pd.read_parquet(self.path)


@dataclass(frozen=True)
class PublicacionResultadosDisco:
    """Artefactos de una generación; rutas antiguas expiran según la retención."""

    golden: TablaParquet
    correlativa: TablaParquet
    entities: int
    source_rows: Mapping[str, int]
    manifest_path: Path
    generation_dir: Path
    generation: str

    def publicar_metadatos(self, payload: Mapping[str, Any]) -> Path:
        """Adjunta metadata JSON sólo si esta generación continúa siendo current."""

        if not isinstance(payload, Mapping):
            raise TypeError("payload de metadatos debe ser un Mapping.")
        lock = _ManifestFileLock(self.manifest_path)
        lock.acquire()
        pending: Path | None = None
        try:
            if not self.manifest_path.is_file():
                raise FileNotFoundError(self.manifest_path)
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("generation") != self.generation:
                raise RuntimeError(
                    "La generación ya no es la publicación actual; los metadatos no "
                    "pueden rebobinar el manifiesto a una corrida anterior."
                )
            artifacts = manifest.get("artifacts")
            if not isinstance(artifacts, dict):
                raise RuntimeError("El manifiesto actual no contiene un mapa de artifacts válido.")

            token = uuid.uuid4().hex
            metadata_path = self.generation_dir / "metadatos_corrida.json"
            pending = self.generation_dir / f".metadatos_corrida.{token}.pending"
            with pending.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, metadata_path)
            pending = None
            _fsync_file(metadata_path)
            _fsync_directory(self.generation_dir)

            updated = dict(manifest)
            updated_artifacts = dict(artifacts)
            updated_artifacts["metadata"] = _relative_manifest_path(
                metadata_path, self.manifest_path.parent
            )
            updated["artifacts"] = updated_artifacts
            _write_manifest_atomic(
                self.manifest_path,
                updated,
                token=token,
                overwrite=True,
                _lock_held=True,
            )
            return metadata_path
        finally:
            if pending is not None:
                _cleanup_file(pending, purpose="metadatos pendientes")
            lock.release()

    def matriz_presencia(self) -> pd.DataFrame:
        """Matriz N×N de entidades compartidas, calculada en DuckDB."""

        import duckdb

        with duckdb.connect(":memory:") as connection:
            return connection.execute(
                """
                WITH presence AS (
                    SELECT DISTINCT ID_GRUPO, SRC
                    FROM read_parquet(?)
                )
                SELECT a.SRC AS FUENTE_A, b.SRC AS FUENTE_B,
                       count(DISTINCT a.ID_GRUPO)::BIGINT AS ENTIDADES_COMPARTIDAS
                FROM presence a
                JOIN presence b USING (ID_GRUPO)
                GROUP BY a.SRC, b.SRC
                ORDER BY a.SRC, b.SRC
                """,
                [str(self.correlativa.path)],
            ).fetch_df()

    def entidades_ausentes_de(self, source: str, *, limit: int = 100) -> pd.DataFrame:
        """Muestra entidades presentes en otras fuentes pero ausentes de ``source``."""

        if source not in self.source_rows:
            raise ValueError(
                f"Fuente desconocida {source!r}; use una de {sorted(self.source_rows)}."
            )
        if isinstance(limit, bool) or not isinstance(limit, Integral):
            raise TypeError("limit debe ser un entero >= 1.")
        if limit < 1:
            raise ValueError("limit debe ser >= 1.")
        import duckdb

        with duckdb.connect(":memory:") as connection:
            return connection.execute(
                """
                WITH presence AS (
                    SELECT DISTINCT ID_GRUPO, SRC
                    FROM read_parquet(?)
                ), absent AS (
                    SELECT ID_GRUPO
                    FROM presence
                    GROUP BY ID_GRUPO
                    HAVING count(*) FILTER (WHERE SRC = ?) = 0
                )
                SELECT g.*
                FROM read_parquet(?) g
                JOIN absent USING (ID_GRUPO)
                ORDER BY ID_GRUPO
                LIMIT ?
                """,
                [str(self.correlativa.path), str(source), str(self.golden.path), int(limit)],
            ).fetch_df()


def _assign_compact_ids(
    correlativa: pd.DataFrame,
    source_order: Sequence[str],
    compactaciones: Mapping[str, DuckDBCompactionResult],
) -> None:
    required = {"SRC", "ORIGINAL_INDEX"}
    missing = required - set(correlativa.columns)
    if missing:
        raise RuntimeError(f"Correlativa compacta sin columnas requeridas: {sorted(missing)}.")
    source_values = correlativa["SRC"].astype("string")
    original_index = pd.to_numeric(correlativa["ORIGINAL_INDEX"], errors="raise").to_numpy()
    compact_ids: np.ndarray = np.full(len(correlativa), -1, dtype=np.int64)
    for source in source_order:
        if source not in compactaciones:
            raise KeyError(f"Falta compactación para la fuente {source!r}.")
        positions = np.flatnonzero(source_values.eq(source).fillna(False).to_numpy())
        if len(positions):
            positions = positions[np.argsort(original_index[positions], kind="stable")]
        expected = compactaciones[source].compact_rows
        if len(positions) != expected:
            raise RuntimeError(
                f"Fuente {source!r}: correlativa compacta={len(positions):,}, "
                f"representantes={expected:,}."
            )
        compact_ids[positions] = np.arange(expected, dtype=np.int64)
    if np.any(compact_ids < 0):
        unknown = correlativa.loc[compact_ids < 0, "SRC"].astype("string").unique().tolist()
        raise RuntimeError(f"La correlativa contiene fuentes no declaradas: {unknown}.")
    correlativa["__compact_record_id"] = compact_ids


def _union_expansion_sql(
    source_order: Sequence[str], compactaciones: Mapping[str, DuckDBCompactionResult]
) -> str:
    queries = []
    source_offset = 0
    for order, source in enumerate(source_order):
        result = compactaciones[source]
        queries.append(
            "SELECT "
            f"{order}::INTEGER AS source_order, {_quote_literal(source)} AS source_name, "
            f"{source_offset}::BIGINT AS source_offset, "
            "source_row_id::BIGINT AS source_row_id, "
            "compact_record_id::BIGINT AS compact_record_id "
            f"FROM read_parquet({_quote_literal(result.expansion_map_path)})"
        )
        source_offset += result.input_rows
    return " UNION ALL ".join(queries)


def _union_payload_sql(
    source_order: Sequence[str], compactaciones: Mapping[str, DuckDBCompactionResult]
) -> tuple[str | None, tuple[str, ...]]:
    queries: list[str] = []
    columns: list[str] = []
    for source in source_order:
        result = compactaciones[source]
        for column in result.payload_columns:
            if column not in columns:
                columns.append(column)
        if result.payload_path is not None:
            queries.append(
                "SELECT "
                f"{_quote_literal(source)} AS source_name, * "
                f"FROM read_parquet({_quote_literal(result.payload_path)})"
            )
    return (" UNION ALL BY NAME ".join(queries) if queries else None, tuple(columns))


def _validate_compaction_contracts(
    connection: Any,
    source_order: Sequence[str],
    compactaciones: Mapping[str, DuckDBCompactionResult],
) -> dict[str, int]:
    if isinstance(source_order, (str, bytes)):
        raise TypeError("source_order debe ser una secuencia de nombres, no un string.")
    ordered = [str(source) for source in source_order]
    if not ordered or len(set(ordered)) != len(ordered):
        raise ValueError("source_order debe contener nombres únicos y al menos una fuente.")
    expected_keys = set(ordered)
    actual_keys = set(compactaciones)
    if actual_keys != expected_keys:
        raise ValueError(
            "compactaciones debe coincidir exactamente con source_order; "
            f"faltan={sorted(expected_keys - actual_keys)}, "
            f"sobran={sorted(actual_keys - expected_keys)}."
        )

    source_rows: dict[str, int] = {}
    for source in ordered:
        result = compactaciones[source]
        if result.source_name != source:
            raise ValueError(
                f"La clave {source!r} contiene una compactación de {result.source_name!r}."
            )
        if result.payload_columns and result.payload_path is None:
            raise RuntimeError(
                f"La compactación de {source!r} declara payload_columns sin payload_path."
            )
        _validate_compaction_artifacts(
            connection,
            source_name=source,
            compact_path=result.compact_path,
            expansion_path=result.expansion_map_path,
            payload_path=result.payload_path,
            input_rows=result.input_rows,
            compact_rows=result.compact_rows,
        )
        source_rows[source] = result.input_rows
    return source_rows


def _validate_final_correlation(
    connection: Any,
    path: Path,
    *,
    expected_rows: int,
    expected_source_rows: Mapping[str, int],
) -> None:
    stats = connection.execute(
        """
        SELECT count(*)::BIGINT,
               count(DISTINCT ORIGINAL_INDEX)::BIGINT,
               min(ORIGINAL_INDEX)::BIGINT,
               max(ORIGINAL_INDEX)::BIGINT,
               count(*) FILTER (WHERE ORIGINAL_INDEX IS NULL)::BIGINT,
               count(*) FILTER (WHERE ID_GRUPO IS NULL)::BIGINT
        FROM read_parquet(?)
        """,
        [str(path)],
    ).fetchone()
    null_indices = int(stats[4])
    null_groups = int(stats[5])
    if null_indices or null_groups:
        raise RuntimeError(
            "Publicación correlativa inválida: "
            f"indices_nulos={null_indices}, grupos_nulos={null_groups}."
        )
    _validate_dense_ids(
        label="correlativa.ORIGINAL_INDEX",
        rows=int(stats[0]),
        distinct_ids=int(stats[1]),
        minimum=None if stats[2] is None else int(stats[2]),
        maximum=None if stats[3] is None else int(stats[3]),
        expected=expected_rows,
    )

    actual_source_rows = {
        str(source): int(rows)
        for source, rows in connection.execute(
            "SELECT SRC, count(*)::BIGINT FROM read_parquet(?) GROUP BY SRC",
            [str(path)],
        ).fetchall()
    }
    expected = dict(expected_source_rows)
    if actual_source_rows != expected:
        raise RuntimeError(
            "Publicación correlativa alteró los conteos por fuente: "
            f"actual={actual_source_rows}, esperado={expected}."
        )


def publicar_resultados_duckdb(
    golden: pd.DataFrame,
    correlativa_compacta: pd.DataFrame,
    *,
    source_order: Sequence[str],
    compactaciones: Mapping[str, DuckDBCompactionResult],
    output_directory: str | Path,
    settings: DuckDBIngestionSettings | None = None,
    overwrite: bool = True,
) -> PublicacionResultadosDisco:
    """Expande y publica una generación completa con un manifiesto atómico."""

    import duckdb

    if not isinstance(overwrite, bool):
        raise TypeError("overwrite debe ser bool.")
    effective_settings = settings or DuckDBIngestionSettings()
    output_dir = Path(output_directory).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "resultados.manifest.json"
    if manifest_path.exists() and not overwrite:
        raise FileExistsError(
            f"Ya existe el manifiesto autoritativo {manifest_path}; "
            "use overwrite=True para publicar una nueva generación."
        )

    manifest_lock = _ManifestFileLock(manifest_path)
    manifest_lock.acquire()
    pending_dir: Path | None = None
    try:
        if manifest_path.exists() and not overwrite:
            raise FileExistsError(
                f"Ya existe el manifiesto autoritativo {manifest_path}; "
                "use overwrite=True para publicar una nueva generación."
            )
        _cleanup_pending_directories(output_dir, "resultados")
        token = uuid.uuid4().hex
        generations_dir = output_dir / "resultados.generations"
        generations_dir.mkdir(parents=True, exist_ok=True)
        pending_dir = output_dir / f".resultados.{token}.generation.pending"
        pending_dir.mkdir()
        generation_dir = generations_dir / token
        golden_pending = pending_dir / "golden.parquet"
        correlativa_pending = pending_dir / "correlativa.parquet"
        compact_path = pending_dir / ".correlativa_compacta.parquet"
        golden_base_path = pending_dir / ".golden_base.parquet"
        golden_path = generation_dir / golden_pending.name
        correlativa_path = generation_dir / correlativa_pending.name
    except Exception:
        manifest_lock.release()
        raise

    connection: Any | None = None
    validated = False
    try:
        connection = duckdb.connect(":memory:")
        _configure_connection(connection, effective_settings)
        expected_source_rows = _validate_compaction_contracts(
            connection, source_order, compactaciones
        )
        expected_rows = sum(expected_source_rows.values())

        correlativa_trabajo = correlativa_compacta.copy(deep=False)
        _assign_compact_ids(correlativa_trabajo, source_order, compactaciones)
        correlativa_trabajo.to_parquet(compact_path, index=False)
        golden.to_parquet(golden_base_path, index=False)
        expansion_sql = _union_expansion_sql(source_order, compactaciones)
        payload_sql, payload_columns = _union_payload_sql(source_order, compactaciones)

        correlative_projection: list[str] = []
        for column in correlativa_trabajo.columns:
            if column == "__compact_record_id":
                continue
            if column == "ORIGINAL_INDEX":
                correlative_projection.append(
                    'CAST(e.source_offset + e.source_row_id AS BIGINT) AS "ORIGINAL_INDEX"'
                )
            else:
                correlative_projection.append(f"c.{_quote_identifier(column)}")
        for column in payload_columns:
            if column not in correlativa_trabajo.columns:
                correlative_projection.append(f"p.{_quote_identifier(column)}")

        payload_cte = f", payload AS ({payload_sql})" if payload_sql else ""
        payload_join = (
            "LEFT JOIN payload p ON p.source_name = e.source_name "
            "AND p.source_row_id = e.source_row_id"
            if payload_sql
            else ""
        )
        correlation_query = (
            f"WITH expansion AS ({expansion_sql}){payload_cte} "
            "SELECT "
            + ", ".join(correlative_projection)
            + f" FROM expansion e JOIN read_parquet({_quote_literal(compact_path)}) c "
            "ON c.SRC = e.source_name AND c.__compact_record_id = e.compact_record_id "
            + payload_join
        )

        compression = effective_settings.parquet_compression.upper()
        connection.execute(
            "COPY ("
            + correlation_query
            + ") TO "
            + _quote_literal(correlativa_pending)
            + f" (FORMAT PARQUET, COMPRESSION {compression})"
        )
        _validate_final_correlation(
            connection,
            correlativa_pending,
            expected_rows=expected_rows,
            expected_source_rows=expected_source_rows,
        )

        golden_columns = [str(column) for column in golden.columns]
        golden_projection: list[str] = []
        for column in golden_columns:
            if column == "INPUT_ROW_COUNT":
                golden_projection.append(
                    'coalesce(cnt.INPUT_ROW_COUNT, 0)::BIGINT AS "INPUT_ROW_COUNT"'
                )
            else:
                golden_projection.append(f"g.{_quote_identifier(column)}")
        if "INPUT_ROW_COUNT" not in golden_columns:
            golden_projection.append(
                'coalesce(cnt.INPUT_ROW_COUNT, 0)::BIGINT AS "INPUT_ROW_COUNT"'
            )
        golden_query = (
            "WITH cnt AS (SELECT ID_GRUPO, count(*)::BIGINT AS INPUT_ROW_COUNT "
            f"FROM read_parquet({_quote_literal(correlativa_pending)}) GROUP BY ID_GRUPO) "
            "SELECT "
            + ", ".join(golden_projection)
            + f" FROM read_parquet({_quote_literal(golden_base_path)}) g "
            "LEFT JOIN cnt USING (ID_GRUPO) ORDER BY g.ID_GRUPO"
        )
        connection.execute(
            "COPY ("
            + golden_query
            + ") TO "
            + _quote_literal(golden_pending)
            + f" (FORMAT PARQUET, COMPRESSION {compression})"
        )
        mismatch = int(
            connection.execute(
                """
                SELECT count(*) FROM (
                    (SELECT DISTINCT ID_GRUPO FROM read_parquet(?)
                     EXCEPT SELECT DISTINCT ID_GRUPO FROM read_parquet(?))
                    UNION ALL
                    (SELECT DISTINCT ID_GRUPO FROM read_parquet(?)
                     EXCEPT SELECT DISTINCT ID_GRUPO FROM read_parquet(?))
                )
                """,
                [
                    str(correlativa_pending),
                    str(golden_pending),
                    str(golden_pending),
                    str(correlativa_pending),
                ],
            ).fetchone()[0]
        )
        if mismatch:
            raise RuntimeError(f"Golden y correlativa difieren en {mismatch} grupos.")
        entities = int(
            connection.execute(
                "SELECT count(DISTINCT ID_GRUPO) FROM read_parquet(?)",
                [str(correlativa_pending)],
            ).fetchone()[0]
        )
        golden_stats = connection.execute(
            """
            SELECT count(*)::BIGINT,
                   count(DISTINCT ID_GRUPO)::BIGINT,
                   count(*) FILTER (WHERE ID_GRUPO IS NULL)::BIGINT
            FROM read_parquet(?)
            """,
            [str(golden_pending)],
        ).fetchone()
        golden_rows = int(golden_stats[0])
        golden_distinct = int(golden_stats[1])
        golden_nulls = int(golden_stats[2])
        if golden_nulls or golden_rows != golden_distinct or golden_distinct != entities:
            raise RuntimeError(
                "Golden inválido: debe contener exactamente una fila no nula por entidad; "
                f"filas={golden_rows}, IDs_distintos={golden_distinct}, "
                f"entidades={entities}, IDs_nulos={golden_nulls}."
            )
        source_rows = {
            str(source): int(rows)
            for source, rows in connection.execute(
                "SELECT SRC, count(*)::BIGINT FROM read_parquet(?) GROUP BY SRC ORDER BY SRC",
                [str(correlativa_pending)],
            ).fetchall()
        }
        golden_schema = tuple(
            str(row[0])
            for row in connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(golden_pending)]
            ).fetchall()
        )
        correlativa_schema = tuple(
            str(row[0])
            for row in connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(correlativa_pending)]
            ).fetchall()
        )
        validated = True
    finally:
        if connection is not None:
            connection.close()
        compact_path.unlink(missing_ok=True)
        golden_base_path.unlink(missing_ok=True)
        if not validated:
            if pending_dir is not None:
                _cleanup_directory(pending_dir, purpose="generación pendiente de resultados")
            manifest_lock.release()

    try:
        _fsync_file(golden_pending)
        _fsync_file(correlativa_pending)
        _fsync_directory(pending_dir)
        os.replace(pending_dir, generation_dir)
        _fsync_directory(generations_dir)
        manifest = {
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "kind": "duckdb_results",
            "generation": token,
            "artifacts": {
                "golden": _relative_manifest_path(golden_path, output_dir),
                "correlativa": _relative_manifest_path(correlativa_path, output_dir),
            },
            "metrics": {
                "golden_rows": golden_rows,
                "correlativa_rows": expected_rows,
                "entities": entities,
                "source_rows": source_rows,
            },
            "schemas": {
                "golden": list(golden_schema),
                "correlativa": list(correlativa_schema),
            },
            "retention": {
                "previous_generations_to_keep": (effective_settings.previous_generations_to_keep)
            },
        }
        _write_manifest_atomic(
            manifest_path,
            manifest,
            token=token,
            overwrite=overwrite,
            _lock_held=True,
        )
        _garbage_collect_generations(
            generations_dir,
            current_generation=generation_dir,
            previous_to_keep=effective_settings.previous_generations_to_keep,
        )
        return PublicacionResultadosDisco(
            golden=TablaParquet(golden_path, golden_rows, golden_schema),
            correlativa=TablaParquet(correlativa_path, expected_rows, correlativa_schema),
            entities=entities,
            source_rows=MappingProxyType(source_rows),
            manifest_path=manifest_path,
            generation_dir=generation_dir,
            generation=token,
        )
    finally:
        if pending_dir is not None:
            _cleanup_directory(pending_dir, purpose="generación pendiente de resultados")
        manifest_lock.release()


__all__ = [
    "PublicacionResultadosDisco",
    "TablaParquet",
    "publicar_resultados_duckdb",
]
