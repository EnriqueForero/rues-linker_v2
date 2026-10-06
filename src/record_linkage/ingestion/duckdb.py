"""Ingestión de texto y colapso exacto con un conjunto de trabajo en disco.

Este módulo es deliberadamente independiente del flujo de enlace. Su contrato
termina en dos archivos Parquet: representantes exactos en orden de primera
aparición y un mapa que permite expandir cada representante a las filas de la
fuente original. Así el consumidor decide cuándo materializar datos en pandas.

La primera versión soporta CSV/TXT (directos, ZIP o GZIP), columnas de texto e
identificadores en modo ``digits``. Los tipos numéricos localizados y los
identificadores alfanuméricos se rechazan explícitamente hasta contar con una
implementación SQL cuya paridad Unicode esté demostrada.
"""

from __future__ import annotations

import codecs
import contextlib
import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from numbers import Integral
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from .errors import ArchiveSafetyError, IngestionError, SchemaError
from .readers import (
    _build_column_plan,
    _detect_delimiter,
    _detect_encoding,
    _detect_input_format,
    _materialize_input,
    _read_text_headers,
)
from .specs import (
    ColumnType,
    Compression,
    IdentifierFormat,
    InputFormat,
    InvalidValuePolicy,
    SourceSpec,
)

_TEXT_FORMATS = {InputFormat.CSV, InputFormat.TXT}
_PARQUET_COMPRESSIONS = {"brotli", "gzip", "lz4", "snappy", "uncompressed", "zstd"}
_SAFE_PREFIX = re.compile(r"[^A-Za-z0-9._-]+")
_INTERNAL_COLUMNS = {
    "__compact_record_id",
    "__representative_source_row_id",
    "__source_row_id",
}
_PUBLICATION_RESERVED_COLUMNS = {
    "compact_record_id",
    "id_grupo",
    "original_index",
    "representative_source_row_id",
    "source_name",
    "source_row_id",
    "src",
}
_MANIFEST_SCHEMA_VERSION = 1
_SOURCE_SPEC_DEFAULT_CHUNKSIZE = 250_000
_SOURCE_SPEC_DEFAULT_TEXT_BLOCK_BYTES = 8 * 1024**2


@dataclass(frozen=True)
class DuckDBIngestionSettings:
    """Límites explícitos del proceso DuckDB.

    ``memory_limit`` limita el buffer manager de DuckDB; no sustituye el
    límite de memoria del proceso Python. ``temp_directory`` debe apuntar a
    disco local para que los operadores puedan derramar sin usar RAM o FUSE.
    ``previous_generations_to_keep`` conserva la generación actual más ese
    número de generaciones anteriores después de cada commit exitoso. Las
    referencias devueltas por corridas más antiguas dejan de ser válidas al
    superar esa retención.
    """

    memory_limit: str = "512MB"
    threads: int = 2
    temp_directory: str | Path | None = None
    max_temp_directory_size: str | None = None
    preserve_insertion_order: bool = True
    encoding_chunk_bytes: int = 8 * 1024**2
    parquet_compression: str = "zstd"
    previous_generations_to_keep: int = 1

    def __post_init__(self) -> None:
        memory_limit = str(self.memory_limit).strip()
        if not memory_limit:
            raise ValueError("memory_limit no puede ser vacío.")
        if self.threads < 1:
            raise ValueError("threads debe ser >= 1.")
        if self.encoding_chunk_bytes < 64 * 1024:
            raise ValueError("encoding_chunk_bytes debe ser >= 65536.")
        if isinstance(self.previous_generations_to_keep, bool) or not isinstance(
            self.previous_generations_to_keep, Integral
        ):
            raise TypeError("previous_generations_to_keep debe ser un entero entre 0 y 100.")
        if not 0 <= self.previous_generations_to_keep <= 100:
            raise ValueError("previous_generations_to_keep debe estar entre 0 y 100.")
        if not self.preserve_insertion_order:
            raise ValueError(
                "El colapso exacto requiere preserve_insertion_order=True para conservar orden."
            )
        max_temp = (
            None
            if self.max_temp_directory_size is None
            else str(self.max_temp_directory_size).strip()
        )
        if self.max_temp_directory_size is not None and not max_temp:
            raise ValueError("max_temp_directory_size no puede ser vacío.")
        compression = str(self.parquet_compression).strip().casefold()
        if compression not in _PARQUET_COMPRESSIONS:
            raise ValueError(
                f"parquet_compression debe ser uno de {sorted(_PARQUET_COMPRESSIONS)}."
            )
        temp_directory = (
            None if self.temp_directory is None else Path(self.temp_directory).expanduser()
        )
        object.__setattr__(self, "memory_limit", memory_limit)
        object.__setattr__(self, "max_temp_directory_size", max_temp)
        object.__setattr__(self, "parquet_compression", compression)
        object.__setattr__(self, "temp_directory", temp_directory)
        object.__setattr__(
            self,
            "previous_generations_to_keep",
            int(self.previous_generations_to_keep),
        )


@dataclass(frozen=True)
class MaterializedTextSource:
    """Vista UTF-8 persistente de una fuente de texto.

    Si ``owns_path`` es falso, ``utf8_path`` es el archivo original, que ya
    era UTF-8 y no estaba comprimido. En los demás casos el llamador es dueño
    del archivo creado dentro de ``output_directory``.
    """

    source_name: str
    source_path: Path
    utf8_path: Path
    logical_name: str
    original_encoding: str
    delimiter: str
    compression: Compression
    archive_member: str | None
    source_bytes: int
    utf8_bytes: int
    transcoded: bool
    owns_path: bool


@dataclass(frozen=True)
class DuckDBCompactionResult:
    """Artefactos auditables de una compactación exacta."""

    source_name: str
    source_path: Path
    compact_path: Path
    expansion_map_path: Path
    input_rows: int
    compact_rows: int
    collapsed_rows: int
    columns: tuple[str, ...]
    resolved_mapping: Mapping[str, str]
    invalid_values: Mapping[str, int]
    source_encoding: str
    delimiter: str
    compression: Compression
    archive_member: str | None
    transcoded_to_utf8: bool
    payload_path: Path | None = None
    payload_columns: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    #: Columnas canónicas de ``optional_column_mapping`` que la fuente NO tiene
    #: (F1.8); misma regla que ``SourceLoadReport.missing_optional``.
    missing_optional: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "resolved_mapping", MappingProxyType(dict(self.resolved_mapping)))
        object.__setattr__(self, "invalid_values", MappingProxyType(dict(self.invalid_values)))


def _canonical_encoding(encoding: str) -> str:
    try:
        return codecs.lookup(encoding).name
    except LookupError as exc:  # pragma: no cover - validado por _detect_encoding
        raise IngestionError(f"Codificación desconocida: {encoding!r}.") from exc


def _materialized_filename(logical_name: str) -> str:
    logical = Path(logical_name)
    suffix = (
        logical.suffix.lower() if logical.suffix.lower() in {".csv", ".tsv", ".txt"} else ".txt"
    )
    stem = _SAFE_PREFIX.sub("_", logical.stem).strip("._") or "source"
    return f"{stem}.{uuid.uuid4().hex}.utf8{suffix}"


def _write_utf8_copy(
    source_path: Path,
    target_path: Path,
    *,
    source_encoding: str,
    chunk_bytes: int,
) -> tuple[int, bool]:
    """Copia o transcodifica por bloques y elimina parciales ante error."""

    canonical = _canonical_encoding(source_encoding)
    transcode = canonical != "utf-8"
    source_bytes = source_path.stat().st_size
    # UTF-8 nunca necesita más de cuatro bytes por byte/unidad de entrada en
    # las codificaciones admitidas. El límite también contiene archivos con
    # una declaración de encoding equivocada antes de llenar el disco.
    max_output_bytes = max(4, source_bytes * 4)
    free_bytes = shutil.disk_usage(target_path.parent).free
    written = 0

    try:
        with source_path.open("rb") as source, target_path.open("xb") as target:
            if not transcode:
                while chunk := source.read(chunk_bytes):
                    written += len(chunk)
                    if written > max_output_bytes or written > free_bytes:
                        raise ArchiveSafetyError(
                            "La copia UTF-8 excedería el espacio o el límite de expansión."
                        )
                    target.write(chunk)
            else:
                decoder = codecs.getincrementaldecoder(canonical)(errors="strict")
                while chunk := source.read(chunk_bytes):
                    encoded = decoder.decode(chunk, final=False).encode("utf-8")
                    written += len(encoded)
                    if written > max_output_bytes or written > free_bytes:
                        raise ArchiveSafetyError(
                            "La transcodificación UTF-8 excedería el espacio o "
                            "el límite de expansión."
                        )
                    target.write(encoded)
                tail = decoder.decode(b"", final=True).encode("utf-8")
                written += len(tail)
                if written > max_output_bytes or written > free_bytes:
                    raise ArchiveSafetyError(
                        "La transcodificación UTF-8 excedería el espacio o el límite de expansión."
                    )
                target.write(tail)
    except Exception:
        target_path.unlink(missing_ok=True)
        raise
    return written, transcode


def materialize_text_source_utf8(
    spec: SourceSpec,
    output_directory: str | Path,
    *,
    chunk_bytes: int = 8 * 1024**2,
) -> MaterializedTextSource:
    """Materializa de forma segura un CSV/TXT como UTF-8 en disco.

    La selección y extracción ZIP/GZIP reutiliza las validaciones de seguridad
    de la ingestión pública. La conversión usa un decodificador incremental:
    nunca retiene el archivo completo ni una cadena de su tamaño en memoria.
    """

    if chunk_bytes < 64 * 1024:
        raise ValueError("chunk_bytes debe ser >= 65536.")
    output_dir = Path(output_directory).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise NotADirectoryError(output_dir)

    with _materialize_input(spec) as materialized:
        input_format = _detect_input_format(
            materialized.path,
            materialized.logical_name,
            InputFormat(spec.format),
        )
        if input_format not in _TEXT_FORMATS:
            raise IngestionError(
                "DuckDBSourceCompactor sólo admite SourceSpec de texto CSV/TXT; "
                f"se detectó {input_format.value!r}."
            )
        encoding = _detect_encoding(materialized.path, spec.encoding)
        delimiter = _detect_delimiter(
            materialized.path,
            encoding,
            spec.delimiter,
            spec.header,
        )
        canonical = _canonical_encoding(encoding)
        is_direct_utf8 = materialized.compression is Compression.NONE and canonical == "utf-8"
        source_bytes = materialized.path.stat().st_size
        if is_direct_utf8:
            return MaterializedTextSource(
                source_name=spec.name,
                source_path=Path(spec.path),
                utf8_path=materialized.path,
                logical_name=materialized.logical_name,
                original_encoding=encoding,
                delimiter=delimiter,
                compression=materialized.compression,
                archive_member=materialized.archive_member,
                source_bytes=source_bytes,
                utf8_bytes=source_bytes,
                transcoded=False,
                owns_path=False,
            )

        target = output_dir / _materialized_filename(materialized.logical_name)
        written, transcoded = _write_utf8_copy(
            materialized.path,
            target,
            source_encoding=encoding,
            chunk_bytes=chunk_bytes,
        )
        return MaterializedTextSource(
            source_name=spec.name,
            source_path=Path(spec.path),
            utf8_path=target,
            logical_name=materialized.logical_name,
            original_encoding=encoding,
            delimiter=delimiter,
            compression=materialized.compression,
            archive_member=materialized.archive_member,
            source_bytes=source_bytes,
            utf8_bytes=written,
            transcoded=transcoded,
            owns_path=True,
        )


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _missing_expression(column_sql: str, sentinels: tuple[str, ...]) -> str:
    normalized = sorted({str(value).strip().casefold() for value in sentinels})
    if not normalized:
        return f"({column_sql} IS NULL)"
    literals = ", ".join(_quote_literal(value) for value in normalized)
    return f"({column_sql} IS NULL OR lower(trim({column_sql})) IN ({literals}))"


def _identifier_expression(
    column_sql: str,
    *,
    missing_sql: str,
    identifier_format: IdentifierFormat,
) -> str:
    trimmed = f"trim({column_sql})"
    zero_decimal = f"regexp_full_match({trimmed}, '^\\+?[0-9]+[\\.,]0$')"
    candidate = (
        f"CASE WHEN {zero_decimal} "
        f"THEN regexp_extract({trimmed}, '^\\+?([0-9]+)[\\.,]0$', 1) "
        f"ELSE {trimmed} END"
    )
    digits = f"regexp_replace({candidate}, '[^0-9]+', '', 'g')"
    valid = (
        f"length({digits}) BETWEEN {identifier_format.min_length} "
        f"AND {identifier_format.max_length} "
        f"AND NOT regexp_full_match({digits}, '0+')"
    )
    return f"CASE WHEN {missing_sql} THEN NULL WHEN {valid} THEN {digits} ELSE NULL END"


def _source_columns(plan: Any) -> list[tuple[str, str]]:
    source_by_output = {canonical: source for canonical, source in plan.resolved_mapping.items()}
    return [(source_by_output.get(output, output), output) for output in plan.output_order]


def _validate_reserved_columns(columns: Sequence[str]) -> None:
    for column in columns:
        normalized_column = column.casefold()
        if (
            normalized_column in _INTERNAL_COLUMNS
            or normalized_column in _PUBLICATION_RESERVED_COLUMNS
            or normalized_column.startswith("__invalid_")
        ):
            raise SchemaError(
                f"La columna de salida {column!r} está reservada por rues-linker para "
                "identidad, expansión o publicación. Renómbrela en column_mapping o "
                "exclúyala de passthrough_columns antes de usar el motor DuckDB."
            )


def _validate_supported_contract(spec: SourceSpec, columns: tuple[str, ...]) -> None:
    _validate_reserved_columns(columns)
    for column in columns:
        column_type = spec.column_types.get(column, ColumnType.STRING)
        if column_type is ColumnType.NUMBER:
            raise IngestionError(
                f"La fase DuckDB inicial no admite NUMBER en {column!r}; "
                "use load_source o declare la columna como texto."
            )
        if column_type is ColumnType.IDENTIFIER:
            identifier_format = cast(
                IdentifierFormat,
                spec.identifier_formats.get(column, IdentifierFormat()),
            )
            if identifier_format.mode != "digits":
                raise IngestionError(
                    f"La fase DuckDB inicial sólo admite identificadores digits; "
                    f"{column!r} usa {identifier_format.mode!r}."
                )

    sentinels = [*spec.global_null_values]
    for values in spec.null_values.values():
        sentinels.extend(values)
    non_ascii = sorted({str(value) for value in sentinels if not str(value).isascii()})
    if non_ascii:
        raise IngestionError(
            "El motor DuckDB sólo admite sentinels nulos ASCII porque lower() no "
            "equivale a casefold() para todo Unicode. Use sentinels ASCII, normalice "
            f"la fuente previamente o use load_source. Valores no admitidos: {non_ascii[:5]}."
        )


def _validate_declared_reserved_columns(spec: SourceSpec) -> None:
    """Falla antes de materializar una fuente cuyo contrato ya es ambiguo."""

    declared = (
        *spec.column_mapping.keys(),
        *spec.optional_column_mapping.keys(),
        *spec.passthrough_columns,
    )
    _validate_reserved_columns(tuple(dict.fromkeys(str(value) for value in declared)))
    sentinels = [*spec.global_null_values]
    for values in spec.null_values.values():
        sentinels.extend(values)
    non_ascii = sorted({str(value) for value in sentinels if not str(value).isascii()})
    if non_ascii:
        raise IngestionError(
            "El motor DuckDB sólo admite sentinels nulos ASCII porque lower() no "
            "equivale a casefold() para todo Unicode. Use sentinels ASCII, normalice "
            f"la fuente previamente o use load_source. Valores no admitidos: {non_ascii[:5]}."
        )


def _validate_backend_specific_options(spec: SourceSpec) -> None:
    """Rechaza knobs del lector pandas que DuckDB no puede honrar.

    Aceptarlos y serializarlos como si fueran efectivos es peor que fallar:
    ``DuckDBIngestionSettings`` posee los equivalentes explícitos en bytes y
    rutas para esta ruta de ingestión.
    """

    unsupported: list[str] = []
    if spec.chunksize != _SOURCE_SPEC_DEFAULT_CHUNKSIZE:
        unsupported.append(
            "SourceSpec.chunksize (use DuckDBIngestionSettings.encoding_chunk_bytes)"
        )
    if spec.temp_dir is not None:
        unsupported.append(
            "SourceSpec.temp_dir (DuckDB materializa dentro de output_directory; "
            "use DuckDBIngestionSettings.temp_directory sólo para spill SQL)"
        )
    if spec.text_engine != "auto":
        unsupported.append("SourceSpec.text_engine (el backend ya es DuckDB)")
    if spec.text_block_size_bytes != _SOURCE_SPEC_DEFAULT_TEXT_BLOCK_BYTES:
        unsupported.append(
            "SourceSpec.text_block_size_bytes (use DuckDBIngestionSettings.encoding_chunk_bytes)"
        )
    if unsupported:
        raise IngestionError(
            "El motor DuckDB no acepta parámetros exclusivos del lector pandas: "
            + "; ".join(unsupported)
            + ". Quite esos valores y use los controles indicados."
        )


def _fsync_directory(path: Path) -> None:
    """Persiste metadatos de rename donde la plataforma permite fsync de carpetas."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _fsync_file(path: Path) -> None:
    """Fuerza a disco un artefacto completo antes de publicar su generación."""

    # Windows implementa fsync mediante _commit(), que rechaza descriptores
    # abiertos sólo para lectura. ``rb+`` funciona en Windows y POSIX; un error
    # de durabilidad debe abortar el commit, no quedar silenciado.
    with path.open("rb+") as stream:
        os.fsync(stream.fileno())


class _ManifestFileLock:
    """Lock interproceso liberado automáticamente al cerrar o morir el proceso."""

    def __init__(self, manifest_path: Path) -> None:
        self.path = manifest_path.parent / f".{manifest_path.name}.lock"
        self._stream: Any | None = None

    def acquire(self) -> None:
        if self._stream is not None:
            raise RuntimeError("El lock de manifiesto ya está adquirido.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if sys.platform == "win32":  # mypy solo tipa msvcrt bajo esta guardia
                import msvcrt

                while True:
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as exc:
                        if getattr(exc, "winerror", None) not in {33, 36} and exc.errno != 13:
                            raise
                        time.sleep(0.05)
            else:
                fcntl: Any = importlib.import_module("fcntl")
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        except Exception:
            stream.close()
            raise
        self._stream = stream

    def release(self) -> None:
        stream = self._stream
        if stream is None:
            return
        self._stream = None
        try:
            stream.seek(0)
            if sys.platform == "win32":  # mypy solo tipa msvcrt bajo esta guardia
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl: Any = importlib.import_module("fcntl")
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            warnings.warn(
                f"No se pudo liberar explícitamente el lock '{self.path}': {exc}; "
                "el cierre del descriptor lo liberará el sistema operativo.",
                RuntimeWarning,
                stacklevel=2,
            )
        finally:
            stream.close()


def _cleanup_directory(path: Path, *, purpose: str) -> None:
    """Limpia un directorio y hace visible el fallo sin tapar la causa original."""

    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError as exc:
        warnings.warn(
            f"No se pudo limpiar {purpose} '{path}': {exc}",
            RuntimeWarning,
            stacklevel=2,
        )


def _cleanup_file(path: Path, *, purpose: str) -> None:
    """Elimina un archivo auxiliar sin ocultar mediante silencio un fallo."""

    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        warnings.warn(
            f"No se pudo limpiar {purpose} '{path}': {exc}",
            RuntimeWarning,
            stacklevel=2,
        )


def _cleanup_pending_directories(output_dir: Path, prefix: str) -> None:
    for pending in output_dir.glob(f".{prefix}.*.generation.pending"):
        _cleanup_directory(pending, purpose="generación pendiente abandonada")


def _garbage_collect_generations(
    generations_dir: Path,
    *,
    current_generation: Path,
    previous_to_keep: int,
) -> None:
    """Retiene current+N anteriores; debe llamarse bajo el lock del manifiesto."""

    candidates: list[tuple[int, str, Path]] = []
    for path in generations_dir.iterdir():
        if not path.is_dir() or path == current_generation:
            continue
        try:
            candidates.append((path.stat().st_mtime_ns, path.name, path))
        except OSError as exc:
            warnings.warn(
                f"No se pudo inspeccionar la generación '{path}' durante GC: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
    candidates.sort(reverse=True)
    for _, _, obsolete in candidates[previous_to_keep:]:
        _cleanup_directory(obsolete, purpose="generación obsoleta")


def _write_manifest_atomic(
    manifest_path: Path,
    payload: Mapping[str, Any],
    *,
    token: str,
    overwrite: bool,
    _lock_held: bool = False,
) -> None:
    """Publica un manifiesto completo mediante un único replace en el mismo disco."""

    lock = None if _lock_held else _ManifestFileLock(manifest_path)
    if lock is not None:
        lock.acquire()
    pending = manifest_path.parent / f".{manifest_path.name}.{token}.pending"
    try:
        with pending.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Segunda comprobación: evita reemplazar una publicación concurrente que
        # apareció mientras se construía la generación en modo no-overwrite.
        if not overwrite and manifest_path.exists():
            raise FileExistsError(
                f"Ya existe el manifiesto autoritativo {manifest_path}; "
                "use overwrite=True para publicar una nueva generación."
            )
        # os.replace y no Path.replace: en Python 3.10 pathlib enlaza os.replace
        # al importar, y las pruebas de fallo en la frontera de commit (que
        # parchean os.replace) no lo interceptarían. Se conserva hasta que la
        # matriz deje 3.10.
        os.replace(pending, manifest_path)  # noqa: PTH105
        _fsync_directory(manifest_path.parent)
    finally:
        _cleanup_file(pending, purpose="manifiesto pendiente")
        if lock is not None:
            lock.release()


def _relative_manifest_path(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def _validate_dense_ids(
    *,
    label: str,
    rows: int,
    distinct_ids: int,
    minimum: int | None,
    maximum: int | None,
    expected: int,
) -> None:
    expected_min = 0 if expected else None
    expected_max = expected - 1 if expected else None
    if (
        rows != expected
        or distinct_ids != expected
        or minimum != expected_min
        or maximum != expected_max
    ):
        raise RuntimeError(
            f"Artefacto {label} inválido: filas={rows}/{expected}, "
            f"IDs_distintos={distinct_ids}/{expected}, rango=({minimum}, {maximum})/"
            f"({expected_min}, {expected_max})."
        )


def _validate_compaction_artifacts(
    connection: Any,
    *,
    source_name: str,
    compact_path: Path,
    expansion_path: Path,
    payload_path: Path | None,
    input_rows: int,
    compact_rows: int,
) -> None:
    """Comprueba cobertura uno-a-uno antes de que un artefacto sea consumible."""

    actual_compact_rows = int(
        connection.execute(
            "SELECT count(*)::BIGINT FROM read_parquet(?)", [str(compact_path)]
        ).fetchone()[0]
    )
    if actual_compact_rows != compact_rows:
        raise RuntimeError(
            f"Compactación de {source_name!r} incompleta: "
            f"compact={actual_compact_rows}/{compact_rows}."
        )

    expansion_stats = connection.execute(
        """
        SELECT count(*)::BIGINT,
               count(DISTINCT source_row_id)::BIGINT,
               min(source_row_id)::BIGINT,
               max(source_row_id)::BIGINT,
               count(DISTINCT compact_record_id)::BIGINT,
               min(compact_record_id)::BIGINT,
               max(compact_record_id)::BIGINT,
               count(*) FILTER (
                   WHERE source_row_id IS NULL OR compact_record_id IS NULL
                      OR representative_source_row_id IS NULL
                      OR source_name IS NULL OR source_name <> ?
               )::BIGINT
        FROM read_parquet(?)
        """,
        [source_name, str(expansion_path)],
    ).fetchone()
    invalid_expansion_rows = int(expansion_stats[7])
    if invalid_expansion_rows:
        raise RuntimeError(
            f"Mapa de expansión de {source_name!r} contiene "
            f"{invalid_expansion_rows} filas nulas o de otra fuente."
        )
    _validate_dense_ids(
        label=f"expansion.source_row_id[{source_name}]",
        rows=int(expansion_stats[0]),
        distinct_ids=int(expansion_stats[1]),
        minimum=None if expansion_stats[2] is None else int(expansion_stats[2]),
        maximum=None if expansion_stats[3] is None else int(expansion_stats[3]),
        expected=input_rows,
    )
    compact_distinct = int(expansion_stats[4])
    compact_min = None if expansion_stats[5] is None else int(expansion_stats[5])
    compact_max = None if expansion_stats[6] is None else int(expansion_stats[6])
    expected_compact_min = 0 if compact_rows else None
    expected_compact_max = compact_rows - 1 if compact_rows else None
    if (
        compact_distinct != compact_rows
        or compact_min != expected_compact_min
        or compact_max != expected_compact_max
    ):
        raise RuntimeError(
            f"Mapa de expansión de {source_name!r}: compact_record_id no cubre "
            "los representantes: "
            f"IDs_distintos={compact_distinct}/{compact_rows}, "
            f"rango=({compact_min}, {compact_max})/"
            f"({expected_compact_min}, {expected_compact_max})."
        )

    if payload_path is not None:
        payload_stats = connection.execute(
            """
            SELECT count(*)::BIGINT,
                   count(DISTINCT source_row_id)::BIGINT,
                   min(source_row_id)::BIGINT,
                   max(source_row_id)::BIGINT,
                   count(*) FILTER (WHERE source_row_id IS NULL)::BIGINT
            FROM read_parquet(?)
            """,
            [str(payload_path)],
        ).fetchone()
        if int(payload_stats[4]):
            raise RuntimeError(
                f"Payload de {source_name!r} contiene {int(payload_stats[4])} IDs nulos."
            )
        _validate_dense_ids(
            label=f"payload.source_row_id[{source_name}]",
            rows=int(payload_stats[0]),
            distinct_ids=int(payload_stats[1]),
            minimum=None if payload_stats[2] is None else int(payload_stats[2]),
            maximum=None if payload_stats[3] is None else int(payload_stats[3]),
            expected=input_rows,
        )


def _file_contains(path: Path, needle: bytes, chunk_bytes: int) -> bool:
    overlap = b""
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_bytes):
            combined = overlap + chunk
            if needle in combined:
                return True
            overlap = combined[-max(0, len(needle) - 1) :]
    return False


def _unused_null_sentinel(path: Path, chunk_bytes: int) -> str:
    # DuckDB convierte cadenas vacías a NULL por defecto. Un nullstr ausente
    # conserva tanto campos vacíos como "" para que SourceSpec decida después.
    for _ in range(8):
        sentinel = f"__RUES_LINKER_NULL_{uuid.uuid4().hex}__"
        if not _file_contains(path, sentinel.encode("ascii"), chunk_bytes):
            return sentinel
    raise IngestionError("No fue posible reservar un marcador CSV interno ausente de la fuente.")


def _output_prefix(source_name: str, requested: str | None) -> str:
    if requested is not None:
        if (
            not requested
            or Path(requested).name != requested
            or "/" in requested
            or "\\" in requested
        ):
            raise ValueError("output_prefix debe ser un nombre simple, no una ruta.")
        raw = requested
    else:
        raw = source_name
    readable = _SAFE_PREFIX.sub("_", raw).strip("._")
    if not readable:
        raise ValueError("output_prefix no contiene caracteres utilizables.")
    # Dos nombres distintos pueden producir el mismo texto saneado (A/B y
    # A B), y Windows puede plegar mayúsculas/minúsculas. El hash del nombre
    # solicitado conserva unicidad estable sin depender del filesystem.
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{readable[:64]}-{digest}"


class DuckDBSourceCompactor:
    """Adapta ``SourceSpec`` a artefactos Parquet sin materializar pandas."""

    def __init__(
        self,
        settings: DuckDBIngestionSettings | None = None,
        *,
        materializer: Callable[..., MaterializedTextSource] = materialize_text_source_utf8,
    ) -> None:
        self.settings = settings or DuckDBIngestionSettings()
        self._materializer = materializer

    def _configure_connection(self, connection: Any, temp_directory: Path) -> None:
        temp_directory.mkdir(parents=True, exist_ok=True)
        connection.execute("SET memory_limit = ?", [self.settings.memory_limit])
        connection.execute("SET threads = ?", [self.settings.threads])
        connection.execute("SET temp_directory = ?", [str(temp_directory)])
        connection.execute(
            "SET preserve_insertion_order = ?",
            [self.settings.preserve_insertion_order],
        )
        # DuckDB enruta esta opción por el renderizador de Jupyter: dentro de
        # un kernel SIN ipywidgets, cambiarla lanza InvalidInputException y
        # tumba la corrida por un detalle cosmético. Se intenta y se sigue.
        with contextlib.suppress(Exception):
            connection.execute("SET enable_progress_bar = false")
        if self.settings.max_temp_directory_size is not None:
            connection.execute(
                "SET max_temp_directory_size = ?",
                [self.settings.max_temp_directory_size],
            )

    def _create_tables(
        self,
        connection: Any,
        *,
        spec: SourceSpec,
        materialized: MaterializedTextSource,
        plan: Any,
        matcher_columns: tuple[str, ...],
        row_limit: int | None,
    ) -> tuple[dict[str, int], int, int]:
        source_columns = _source_columns(plan)
        columns = tuple(output for _, output in source_columns)
        projection = ", ".join(
            f"{_quote_identifier(source)} AS {_quote_identifier(output)}"
            for source, output in source_columns
        )
        null_sentinel = _unused_null_sentinel(
            materialized.utf8_path,
            self.settings.encoding_chunk_bytes,
        )
        source_sql = (
            f"SELECT {projection} "
            "FROM read_csv(?, delim=?, header=true, skip=?, all_varchar=true, "
            "encoding='utf-8', parallel=false, nullstr=?, strict_mode=true)"
        )
        parameters: list[Any] = [
            str(materialized.utf8_path),
            materialized.delimiter,
            spec.header,
            null_sentinel,
        ]
        if row_limit is not None:
            source_sql += " LIMIT ?"
            parameters.append(row_limit)
        raw_sql = (
            "CREATE TABLE _rues_raw AS "
            "SELECT CAST(row_number() OVER () - 1 AS BIGINT) AS __source_row_id, "
            f"* FROM ({source_sql}) AS _rues_source"
        )
        # La asignación de row_number es la única sección monohilo: los IDs
        # representan el orden físico y son parte del contrato de expansión.
        connection.execute("SET threads = 1")
        try:
            connection.execute(raw_sql, parameters)
        finally:
            connection.execute("SET threads = ?", [self.settings.threads])

        normalized: list[str] = ["__source_row_id"]
        invalid_flags: dict[str, str] = {}
        invalid_conditions: dict[str, str] = {}
        for position, column in enumerate(columns):
            quoted = _quote_identifier(column)
            sentinels = (*spec.global_null_values, *spec.null_values.get(column, ()))
            missing = _missing_expression(quoted, sentinels)
            column_type = spec.column_types.get(column, ColumnType.STRING)
            if column_type is ColumnType.IDENTIFIER:
                identifier_format = cast(
                    IdentifierFormat,
                    spec.identifier_formats.get(column, IdentifierFormat()),
                )
                expression = _identifier_expression(
                    quoted,
                    missing_sql=missing,
                    identifier_format=identifier_format,
                )
                flag = f"__invalid_{position}"
                invalid_flags[column] = flag
                invalid_conditions[column] = f"(NOT {missing} AND ({expression}) IS NULL)"
                normalized.append(f"{expression} AS {quoted}")
                normalized.append(f"{invalid_conditions[column]} AS {_quote_identifier(flag)}")
            else:
                canonical_newlines = (
                    f"replace(replace({quoted}, chr(13) || chr(10), chr(10)), chr(13), chr(10))"
                )
                normalized.append(
                    f"CASE WHEN {missing} THEN NULL ELSE trim({canonical_newlines}) END AS {quoted}"
                )

        connection.execute(
            "CREATE TABLE _rues_normalized AS SELECT " + ", ".join(normalized) + " FROM _rues_raw"
        )
        invalid_values: dict[str, int] = {}
        for column, flag in invalid_flags.items():
            count = int(
                connection.execute(
                    "SELECT count(*) FROM _rues_normalized WHERE " + _quote_identifier(flag)
                ).fetchone()[0]
            )
            if count:
                invalid_values[column] = count
                if spec.invalid_values is InvalidValuePolicy.RAISE:
                    samples = [
                        row[0]
                        for row in connection.execute(
                            "SELECT "
                            + _quote_identifier(column)
                            + " FROM _rues_raw WHERE "
                            + invalid_conditions[column]
                            + " LIMIT 5"
                        ).fetchall()
                    ]
                    raise SchemaError(
                        f"Fuente '{spec.name}', columna {column!r}: {count:,} valores "
                        f"no cumplen el tipo declarado. Muestra: {samples}."
                    )

        partition = ", ".join(_quote_identifier(column) for column in matcher_columns)
        connection.execute(
            "CREATE TABLE _rues_labelled AS SELECT *, "
            f"min(__source_row_id) OVER (PARTITION BY {partition}) "
            "AS __representative_source_row_id FROM _rues_normalized"
        )
        selected_columns = ", ".join(_quote_identifier(column) for column in matcher_columns)
        connection.execute(
            "CREATE TABLE _rues_compact AS SELECT "
            "CAST(row_number() OVER (ORDER BY __source_row_id) - 1 AS BIGINT) "
            "AS __compact_record_id, __source_row_id AS __representative_source_row_id, "
            f"{selected_columns} FROM _rues_labelled "
            "WHERE __source_row_id = __representative_source_row_id "
            "ORDER BY __source_row_id"
        )
        connection.execute(
            "CREATE TABLE _rues_expansion AS SELECT __source_row_id, "
            "__representative_source_row_id, "
            "CAST(dense_rank() OVER (ORDER BY __representative_source_row_id) - 1 "
            "AS BIGINT) AS __compact_record_id FROM _rues_labelled "
            "ORDER BY __source_row_id"
        )
        input_rows = int(connection.execute("SELECT count(*) FROM _rues_raw").fetchone()[0])
        compact_rows = int(connection.execute("SELECT count(*) FROM _rues_compact").fetchone()[0])
        return invalid_values, input_rows, compact_rows

    # Complejidad ciclomática 22 heredada del notebook. El noqa la exime de la
    # compuerta por archivo del CI (máximo 15 en código tocado); el trinquete
    # (scripts/deuda.py, --ignore-noqa) la sigue contando en deuda_f0.json
    # hasta que F5 la descomponga. No añada ramas aquí: extraiga funciones.
    def compact(  # noqa: C901
        self,
        spec: SourceSpec,
        output_directory: str | Path,
        *,
        output_prefix: str | None = None,
        overwrite: bool = False,
        row_limit: int | None = None,
        matcher_columns: Sequence[str] | None = None,
        preserve_payload: bool = False,
    ) -> DuckDBCompactionResult:
        """Crea Parquet compacto y mapa de expansión en orden de fuente.

        El número de fila del Parquet compacto es ``compact_record_id``. El
        mapa contiene una fila por registro original y las columnas
        ``source_name``, ``source_row_id``, ``representative_source_row_id`` y
        ``compact_record_id``. ``row_limit`` se aplica dentro de la consulta de
        lectura, antes de normalizar y colapsar; así una muestra no materializa
        primero el archivo completo. ``matcher_columns`` limita las columnas
        que definen el representante y entran a L1-L5. Con
        ``preserve_payload=True``, las restantes se publican aparte por
        ``source_row_id`` y no aumentan el estado en memoria del matcher.
        """

        _validate_declared_reserved_columns(spec)
        _validate_backend_specific_options(spec)
        if row_limit is not None:
            if isinstance(row_limit, bool) or not isinstance(row_limit, Integral):
                raise TypeError("row_limit debe ser un entero >= 1 o None.")
            if row_limit < 1:
                raise ValueError("row_limit debe ser >= 1 o None.")
            row_limit = int(row_limit)

        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover - dependencia declarada
            raise ImportError("DuckDBSourceCompactor requiere duckdb>=1.4,<2.") from exc

        output_dir = Path(output_directory).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        if not output_dir.is_dir():
            raise NotADirectoryError(output_dir)
        prefix = _output_prefix(spec.name, output_prefix)
        manifest_path = output_dir / f"{prefix}.manifest.json"
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
            _cleanup_pending_directories(output_dir, prefix)
            token = uuid.uuid4().hex
            generations_dir = output_dir / f"{prefix}.generations"
            generations_dir.mkdir(parents=True, exist_ok=True)
            pending_dir = output_dir / f".{prefix}.{token}.generation.pending"
            pending_dir.mkdir()
            generation_dir = generations_dir / token
            compact_pending = pending_dir / "compact.parquet"
            expansion_pending = pending_dir / "expansion.parquet"
            payload_pending = pending_dir / "payload.parquet"
            compact_path = generation_dir / compact_pending.name
            expansion_path = generation_dir / expansion_pending.name
            payload_path = generation_dir / payload_pending.name
        except Exception:
            manifest_lock.release()
            raise
        connection: Any | None = None
        try:
            with tempfile.TemporaryDirectory(prefix=".rues-linker-duckdb-", dir=output_dir) as work:
                work_dir = Path(work)
                materialized = self._materializer(
                    spec,
                    work_dir,
                    chunk_bytes=self.settings.encoding_chunk_bytes,
                )
                headers = _read_text_headers(
                    materialized.utf8_path,
                    "utf-8",
                    materialized.delimiter,
                    spec.header,
                )
                plan = _build_column_plan(headers, spec)
                columns = tuple(str(column) for column in plan.output_order)
                _validate_supported_contract(spec, columns)
                if matcher_columns is None:
                    effective_matcher_columns = columns
                else:
                    requested: list[str] = []
                    for raw_column in matcher_columns:
                        column = str(raw_column).strip()
                        if not column:
                            raise ValueError("matcher_columns no admite nombres vacíos.")
                        if column not in requested:
                            requested.append(column)
                    if not requested:
                        raise ValueError("matcher_columns debe contener al menos una columna.")
                    missing = set(requested) - set(columns)
                    missing_required = missing - set(spec.optional_column_mapping)
                    if missing_required:
                        raise SchemaError(
                            "matcher_columns contiene columnas no producidas por la fuente: "
                            f"{sorted(missing_required)}."
                        )
                    effective_matcher_columns = tuple(
                        column for column in requested if column in columns
                    )
                    if not effective_matcher_columns:
                        raise SchemaError(
                            "Ninguna matcher_column está presente después de resolver el esquema."
                        )
                payload_columns = tuple(
                    column for column in columns if column not in effective_matcher_columns
                )
                warnings = tuple(
                    f"Columna opcional {canonical!r} "
                    f"(fuente {spec.optional_column_mapping[canonical]!r}) ausente en "
                    f"'{spec.name}'; se omitió."
                    for canonical in plan.missing_optional
                )

                database_path = work_dir / "staging.duckdb"
                temp_directory = (
                    Path(self.settings.temp_directory)
                    if self.settings.temp_directory is not None
                    else work_dir / "duckdb-temp"
                )
                connection = duckdb.connect(str(database_path))
                self._configure_connection(connection, temp_directory)
                connection.execute("BEGIN TRANSACTION")
                try:
                    invalid_values, input_rows, compact_rows = self._create_tables(
                        connection,
                        spec=spec,
                        materialized=materialized,
                        plan=plan,
                        matcher_columns=effective_matcher_columns,
                        row_limit=row_limit,
                    )
                    if input_rows == 0:
                        raise ValueError(
                            f"La fuente {spec.name!r} no contiene filas de datos. "
                            "Verifique el miembro del archivo, encabezado, delimitador "
                            "y filtros antes de ejecutar el linkage."
                        )
                    selected_columns = ", ".join(
                        _quote_identifier(column) for column in effective_matcher_columns
                    )
                    compression = self.settings.parquet_compression.upper()
                    connection.execute(
                        "COPY (SELECT "
                        + selected_columns
                        + " FROM _rues_compact ORDER BY __compact_record_id) TO "
                        + _quote_literal(compact_pending)
                        + f" (FORMAT PARQUET, COMPRESSION {compression})"
                    )
                    connection.execute(
                        "COPY (SELECT "
                        + _quote_literal(spec.name)
                        + " AS source_name, __source_row_id AS source_row_id, "
                        "__representative_source_row_id AS representative_source_row_id, "
                        "__compact_record_id AS compact_record_id "
                        "FROM _rues_expansion ORDER BY __source_row_id) TO "
                        + _quote_literal(expansion_pending)
                        + f" (FORMAT PARQUET, COMPRESSION {compression})"
                    )
                    if preserve_payload and payload_columns:
                        payload_projection = ", ".join(
                            _quote_identifier(column) for column in payload_columns
                        )
                        connection.execute(
                            "COPY (SELECT __source_row_id AS source_row_id, "
                            + payload_projection
                            + " FROM _rues_normalized ORDER BY __source_row_id) TO "
                            + _quote_literal(payload_pending)
                            + f" (FORMAT PARQUET, COMPRESSION {compression})"
                        )
                    _validate_compaction_artifacts(
                        connection,
                        source_name=spec.name,
                        compact_path=compact_pending,
                        expansion_path=expansion_pending,
                        payload_path=(
                            payload_pending if preserve_payload and payload_columns else None
                        ),
                        input_rows=input_rows,
                        compact_rows=compact_rows,
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
                finally:
                    connection.close()
                    connection = None

                # La carpeta pasa de invisible/incompleta a una generación
                # inmutable mediante un único rename. El manifiesto se conmuta
                # después; nunca apunta a archivos publicados a medias.
                _fsync_file(compact_pending)
                _fsync_file(expansion_pending)
                if preserve_payload and payload_columns:
                    _fsync_file(payload_pending)
                _fsync_directory(pending_dir)
                os.replace(pending_dir, generation_dir)  # noqa: PTH105 (ver _write_manifest_atomic)
                _fsync_directory(generations_dir)
                published_payload = payload_path if preserve_payload and payload_columns else None
                manifest = {
                    "schema_version": _MANIFEST_SCHEMA_VERSION,
                    "kind": "duckdb_compaction",
                    "generation": token,
                    "source_name": spec.name,
                    "artifacts": {
                        "compact": _relative_manifest_path(compact_path, output_dir),
                        "expansion": _relative_manifest_path(expansion_path, output_dir),
                        "payload": (
                            _relative_manifest_path(published_payload, output_dir)
                            if published_payload is not None
                            else None
                        ),
                    },
                    "metrics": {
                        "input_rows": input_rows,
                        "compact_rows": compact_rows,
                        "collapsed_rows": input_rows - compact_rows,
                    },
                    "columns": list(effective_matcher_columns),
                    "payload_columns": (
                        list(payload_columns) if published_payload is not None else []
                    ),
                    "retention": {
                        "previous_generations_to_keep": (self.settings.previous_generations_to_keep)
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
                    previous_to_keep=self.settings.previous_generations_to_keep,
                )
                return DuckDBCompactionResult(
                    source_name=spec.name,
                    source_path=Path(spec.path),
                    compact_path=compact_path,
                    expansion_map_path=expansion_path,
                    input_rows=input_rows,
                    compact_rows=compact_rows,
                    collapsed_rows=input_rows - compact_rows,
                    columns=effective_matcher_columns,
                    resolved_mapping=dict(plan.resolved_mapping),
                    invalid_values=invalid_values,
                    source_encoding=materialized.original_encoding,
                    delimiter=materialized.delimiter,
                    compression=materialized.compression,
                    archive_member=materialized.archive_member,
                    transcoded_to_utf8=materialized.transcoded,
                    payload_path=published_payload,
                    payload_columns=payload_columns if published_payload is not None else (),
                    warnings=warnings,
                    missing_optional=tuple(plan.missing_optional),
                )
        finally:
            if connection is not None:
                connection.close()
            if pending_dir is not None:
                _cleanup_directory(pending_dir, purpose="generación pendiente de compactación")
            manifest_lock.release()


def compact_source_to_parquet(
    spec: SourceSpec,
    output_directory: str | Path,
    *,
    settings: DuckDBIngestionSettings | None = None,
    output_prefix: str | None = None,
    overwrite: bool = False,
    row_limit: int | None = None,
    matcher_columns: Sequence[str] | None = None,
    preserve_payload: bool = False,
) -> DuckDBCompactionResult:
    """Atajo funcional para :class:`DuckDBSourceCompactor`."""

    return DuckDBSourceCompactor(settings).compact(
        spec,
        output_directory,
        output_prefix=output_prefix,
        overwrite=overwrite,
        row_limit=row_limit,
        matcher_columns=matcher_columns,
        preserve_payload=preserve_payload,
    )


__all__ = [
    "DuckDBCompactionResult",
    "DuckDBIngestionSettings",
    "DuckDBSourceCompactor",
    "MaterializedTextSource",
    "compact_source_to_parquet",
    "materialize_text_source_utf8",
]
