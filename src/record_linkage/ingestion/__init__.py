"""Ingestión local segura, tipada y eficiente por fuente."""

from .dimensionado import (
    EstimacionFilas,
    ResumenUniverso,
    estimar_filas,
    resumir_universo,
)
from .duckdb import (
    DuckDBCompactionResult,
    DuckDBIngestionSettings,
    DuckDBSourceCompactor,
    MaterializedTextSource,
    compact_source_to_parquet,
    materialize_text_source_utf8,
)
from .errors import ArchiveSafetyError, EncodingDetectionError, IngestionError, SchemaError
from .readers import iter_source_chunks, load_source, load_sources
from .specs import (
    ColumnType,
    Compression,
    IdentifierFormat,
    InputFormat,
    InvalidValuePolicy,
    LoadedSource,
    NumericFormat,
    SourceLoadReport,
    SourceSpec,
    ZipSafetyLimits,
)

__all__ = [
    "ArchiveSafetyError",
    "ColumnType",
    "Compression",
    "DuckDBCompactionResult",
    "DuckDBIngestionSettings",
    "DuckDBSourceCompactor",
    "EncodingDetectionError",
    "EstimacionFilas",
    "IdentifierFormat",
    "IngestionError",
    "InputFormat",
    "InvalidValuePolicy",
    "LoadedSource",
    "MaterializedTextSource",
    "NumericFormat",
    "ResumenUniverso",
    "SchemaError",
    "SourceLoadReport",
    "SourceSpec",
    "ZipSafetyLimits",
    "compact_source_to_parquet",
    "estimar_filas",
    "iter_source_chunks",
    "load_source",
    "load_sources",
    "materialize_text_source_utf8",
    "resumir_universo",
]
