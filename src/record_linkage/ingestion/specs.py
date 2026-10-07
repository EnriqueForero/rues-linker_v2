"""Contratos declarativos por fuente para ingestión local y reproducible."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any


class InputFormat(str, Enum):
    """Formatos tabulares soportados."""

    AUTO = "auto"
    CSV = "csv"
    TXT = "txt"
    XLSX = "xlsx"
    PARQUET = "parquet"


class Compression(str, Enum):
    """Contenedores de compresión soportados."""

    AUTO = "auto"
    NONE = "none"
    ZIP = "zip"
    GZIP = "gzip"


class ColumnType(str, Enum):
    """Tipos que requieren coerción controlada durante la entrada."""

    STRING = "string"
    IDENTIFIER = "identifier"
    NUMBER = "number"


class InvalidValuePolicy(str, Enum):
    """Tratamiento de valores no nulos que no cumplen su tipo declarado."""

    COERCE = "coerce"
    RAISE = "raise"


@dataclass(frozen=True)
class IdentifierFormat:
    """Reglas de normalización de un identificador.

    ``digits`` es adecuado para NIT/documentos colombianos. ``alphanumeric``
    conserva letras para identificadores internacionales.
    """

    mode: str = "digits"
    min_length: int = 6
    max_length: int = 64

    def __post_init__(self) -> None:
        mode = str(self.mode).strip().lower()
        aliases = {"digitos": "digits", "alfanumerico": "alphanumeric"}
        mode = aliases.get(mode, mode)
        if mode not in {"digits", "alphanumeric"}:
            raise ValueError("IdentifierFormat.mode debe ser 'digits' o 'alphanumeric'.")
        if self.min_length < 1:
            raise ValueError("IdentifierFormat.min_length debe ser >= 1.")
        if self.max_length < self.min_length:
            raise ValueError("IdentifierFormat.max_length debe ser >= min_length.")
        object.__setattr__(self, "mode", mode)


@dataclass(frozen=True)
class NumericFormat:
    """Convención explícita de separadores para números localizados."""

    decimal_separator: str | None = None
    thousands_separator: str | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("decimal_separator", self.decimal_separator),
            ("thousands_separator", self.thousands_separator),
        ):
            if value is not None and len(value) != 1:
                raise ValueError(f"NumericFormat.{label} debe tener exactamente un carácter.")
        if (
            self.decimal_separator is not None
            and self.decimal_separator == self.thousands_separator
        ):
            raise ValueError("Los separadores decimal y de miles deben ser distintos.")


@dataclass(frozen=True)
class ZipSafetyLimits:
    """Límites preventivos para ZIP/GZIP, apropiados para Colab gratuito.

    Los límites se verifican antes y durante la descompresión. Se pueden
    reducir por fuente cuando se conocen tamaños máximos más estrictos.
    """

    max_members: int = 64
    max_member_bytes: int = 4 * 1024**3
    max_total_uncompressed_bytes: int = 4 * 1024**3
    max_compression_ratio: float = 250.0
    copy_chunk_bytes: int = 1024**2

    def __post_init__(self) -> None:
        if self.max_members < 1:
            raise ValueError("ZipSafetyLimits.max_members debe ser >= 1.")
        if self.max_member_bytes < 1 or self.max_total_uncompressed_bytes < 1:
            raise ValueError("Los límites de bytes deben ser positivos.")
        if self.max_compression_ratio <= 1:
            raise ValueError("ZipSafetyLimits.max_compression_ratio debe ser > 1.")
        if self.copy_chunk_bytes < 4096:
            raise ValueError("ZipSafetyLimits.copy_chunk_bytes debe ser >= 4096.")


def _freeze_mapping(mapping: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    copied: dict[str, Any] = {}
    for raw_key, value in mapping.items():
        key = str(raw_key).strip()
        if not key:
            raise ValueError(f"{label} contiene una clave vacía.")
        copied[key] = value
    return MappingProxyType(copied)


@dataclass(frozen=True)
class SourceSpec:
    """Contrato completo de una fuente local.

    ``column_mapping`` se expresa como ``{columna_canónica: columna_fuente}``.
    Esa dirección es intencional: dos archivos con encabezados distintos
    pueden producir el mismo esquema sin compartir configuración accidental.

    Para minimizar RAM, por defecto solo se leen columnas mapeadas y
    ``passthrough_columns``. Active ``keep_unmapped`` únicamente si realmente
    necesita toda la tabla.
    """

    name: str
    path: str | Path
    column_mapping: Mapping[str, str] = field(default_factory=dict)
    optional_column_mapping: Mapping[str, str] = field(default_factory=dict)
    format: InputFormat | str = InputFormat.AUTO
    compression: Compression | str = Compression.AUTO
    encoding: str = "auto"
    delimiter: str | None = None
    text_engine: str = "auto"
    newlines_in_values: bool = True
    text_block_size_bytes: int = 8 * 1024**2
    archive_member: str | None = None
    sheet_name: str | int = 0
    header: int = 0
    passthrough_columns: tuple[str, ...] = ()
    keep_unmapped: bool = False
    column_types: Mapping[str, ColumnType | str] = field(default_factory=dict)
    identifier_formats: Mapping[str, IdentifierFormat | Mapping[str, Any]] = field(
        default_factory=dict
    )
    numeric_formats: Mapping[str, NumericFormat | Mapping[str, Any]] = field(default_factory=dict)
    null_values: Mapping[str, tuple[str, ...] | list[str] | set[str]] = field(default_factory=dict)
    global_null_values: tuple[str, ...] = ("", "nan", "none", "null", "<na>")
    chunksize: int = 250_000
    invalid_values: InvalidValuePolicy | str = InvalidValuePolicy.COERCE
    safety_limits: ZipSafetyLimits = field(default_factory=ZipSafetyLimits)
    temp_dir: str | Path | None = None

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValueError("SourceSpec.name no puede ser vacío.")
        if self.header < 0:
            raise ValueError("SourceSpec.header debe ser >= 0.")
        if self.chunksize < 1:
            raise ValueError("SourceSpec.chunksize debe ser >= 1.")
        if self.delimiter is not None and len(self.delimiter) != 1:
            raise ValueError("SourceSpec.delimiter debe tener exactamente un carácter.")
        text_engine = str(self.text_engine).strip().lower()
        if text_engine not in {"auto", "pyarrow", "pandas"}:
            raise ValueError("SourceSpec.text_engine debe ser auto, pyarrow o pandas.")
        if self.text_block_size_bytes < 64 * 1024:
            raise ValueError("SourceSpec.text_block_size_bytes debe ser >= 65536.")

        source_path = Path(self.path).expanduser()
        temp_dir = Path(self.temp_dir).expanduser() if self.temp_dir is not None else None
        input_format = InputFormat(self.format)
        compression = Compression(self.compression)
        policy = InvalidValuePolicy(self.invalid_values)

        raw_mapping = dict(self.column_mapping)
        mapping: dict[str, str] = {}
        for canonical, source in raw_mapping.items():
            canonical_clean = str(canonical).strip()
            source_clean = str(source).strip()
            if not canonical_clean or not source_clean:
                raise ValueError("column_mapping no admite nombres vacíos.")
            mapping[canonical_clean] = source_clean
        if len(set(mapping.values())) != len(mapping):
            raise ValueError("column_mapping no puede reutilizar una columna fuente.")
        optional_mapping: dict[str, str] = {}
        for canonical, source in self.optional_column_mapping.items():
            canonical_clean = str(canonical).strip()
            source_clean = str(source).strip()
            if not canonical_clean or not source_clean:
                raise ValueError("optional_column_mapping no admite nombres vacíos.")
            optional_mapping[canonical_clean] = source_clean
        overlap = set(mapping) & set(optional_mapping)
        if overlap:
            raise ValueError(
                f"Columnas simultáneamente obligatorias y opcionales: {sorted(overlap)}."
            )
        all_source_names = [*mapping.values(), *optional_mapping.values()]
        if len(set(all_source_names)) != len(all_source_names):
            raise ValueError(
                "Los mapeos obligatorios/opcionales no pueden reutilizar una columna fuente."
            )
        if (
            not mapping
            and not optional_mapping
            and not self.keep_unmapped
            and not self.passthrough_columns
        ):
            raise ValueError(
                "Declare column_mapping/optional_column_mapping/passthrough_columns "
                "o active keep_unmapped."
            )

        passthrough = tuple(str(value).strip() for value in self.passthrough_columns)
        if any(not value for value in passthrough):
            raise ValueError("passthrough_columns no admite nombres vacíos.")
        if len(set(passthrough)) != len(passthrough):
            raise ValueError("passthrough_columns contiene duplicados.")

        types: dict[str, ColumnType] = {
            str(column).strip(): ColumnType(value) for column, value in self.column_types.items()
        }
        id_formats: dict[str, IdentifierFormat] = {}
        for column, id_value in self.identifier_formats.items():
            key = str(column).strip()
            id_formats[key] = (
                id_value
                if isinstance(id_value, IdentifierFormat)
                else IdentifierFormat(**dict(id_value))
            )
            if key in types and types[key] is not ColumnType.IDENTIFIER:
                raise ValueError(f"identifier_formats['{key}'] requiere tipo IDENTIFIER.")
            types.setdefault(key, ColumnType.IDENTIFIER)

        numeric_formats: dict[str, NumericFormat] = {}
        for column, numeric_value in self.numeric_formats.items():
            key = str(column).strip()
            numeric_formats[key] = (
                numeric_value
                if isinstance(numeric_value, NumericFormat)
                else NumericFormat(**dict(numeric_value))
            )
            if key in types and types[key] is not ColumnType.NUMBER:
                raise ValueError(f"numeric_formats['{key}'] requiere tipo NUMBER.")
            types.setdefault(key, ColumnType.NUMBER)

        null_values = {
            str(column).strip(): tuple(str(value) for value in values)
            for column, values in self.null_values.items()
        }
        available_outputs = set(mapping) | set(optional_mapping) | set(passthrough)
        if not self.keep_unmapped:
            unknown_typed = set(types) - available_outputs
            unknown_nulls = set(null_values) - available_outputs
            if unknown_typed or unknown_nulls:
                raise ValueError(
                    "Configuración para columnas que no se cargarán: "
                    f"types={sorted(unknown_typed)}, null_values={sorted(unknown_nulls)}."
                )

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "path", source_path)
        object.__setattr__(self, "temp_dir", temp_dir)
        object.__setattr__(self, "format", input_format)
        object.__setattr__(self, "compression", compression)
        object.__setattr__(self, "invalid_values", policy)
        object.__setattr__(self, "text_engine", text_engine)
        object.__setattr__(self, "column_mapping", MappingProxyType(mapping))
        object.__setattr__(self, "optional_column_mapping", MappingProxyType(optional_mapping))
        object.__setattr__(self, "passthrough_columns", passthrough)
        object.__setattr__(self, "column_types", MappingProxyType(types))
        object.__setattr__(self, "identifier_formats", MappingProxyType(id_formats))
        object.__setattr__(self, "numeric_formats", MappingProxyType(numeric_formats))
        object.__setattr__(self, "null_values", MappingProxyType(null_values))
        object.__setattr__(
            self, "global_null_values", tuple(str(value) for value in self.global_null_values)
        )


@dataclass(frozen=True)
class SourceLoadReport:
    """Trazabilidad verificable de una lectura completada."""

    source_name: str
    source_path: Path
    input_format: InputFormat
    compression: Compression
    encoding: str | None
    delimiter: str | None
    engine: str | None
    archive_member: str | None
    rows: int
    columns: tuple[str, ...]
    resolved_mapping: Mapping[str, str]
    invalid_values: Mapping[str, int]
    warnings: tuple[str, ...] = ()
    #: Columnas canónicas de ``optional_column_mapping`` que la fuente NO tiene
    #: (F1.8). Es la misma regla que produce el aviso «Columna opcional …
    #: ausente»; se expone como dato para que el flujo la declare sin recalcularla.
    missing_optional: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoadedSource:
    """Datos junto con su reporte de lectura."""

    data: Any
    report: SourceLoadReport
