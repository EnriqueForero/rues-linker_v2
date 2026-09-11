"""Huellas deterministas para datos, configuración, archivos y código.

Un checkpoint es reutilizable solamente si representa exactamente los datos,
la configuración efectiva, los artefactos y la implementación que lo produjo.
Este módulo concentra ese contrato para evitar heurísticas incompatibles
(conteos de filas, ``mtime`` o muestras parciales) entre componentes.

Las funciones son internas y no cambian la API pública del paquete.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import platform
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

FINGERPRINT_PROTOCOL_VERSION = 1
"""Versión del formato canónico. Incrementar si cambia su semántica."""

_ROW_CHUNK_SIZE = 100_000
_FILE_CHUNK_SIZE = 8 * 1024 * 1024
_HASH_KEY = "0123456789123456"  # 16 bytes; requisito de pandas.


def _qualified_type(value: Any) -> str:
    cls = type(value)
    return f"{cls.__module__}.{cls.__qualname__}"


def _canonicalize(value: Any) -> Any:
    """Convierte ``value`` a una estructura JSON determinista y tipada.

    No usa ``default=str``: la representación de sets y objetos puede variar
    entre procesos y producir falsos hits o invalidaciones espurias.
    """

    if value is None or isinstance(value, (str, bool)):
        return value

    if isinstance(value, np.generic):
        return _canonicalize(value.item())

    if isinstance(value, int):
        return {"__int__": str(value)}

    if isinstance(value, float):
        if math.isnan(value):
            token = "nan"
        elif math.isinf(value):
            token = "+inf" if value > 0 else "-inf"
        else:
            token = value.hex()
        return {"__float__": token}

    if isinstance(value, bytes):
        return {"__bytes__": value.hex()}

    if isinstance(value, Path):
        return {"__path__": str(value)}

    if isinstance(value, Enum):
        return {
            "__enum__": _qualified_type(value),
            "value": _canonicalize(value.value),
        }

    if isinstance(value, (datetime, date)):
        return {"__datetime__": value.isoformat()}

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "__dataclass__": _qualified_type(value),
            "fields": {
                field.name: _canonicalize(getattr(value, field.name))
                for field in dataclasses.fields(value)
            },
        }

    if isinstance(value, Mapping):
        pairs = [(_canonicalize(key), _canonicalize(item)) for key, item in value.items()]
        pairs.sort(key=lambda pair: _canonical_json(pair[0]))
        return {"__mapping__": pairs}

    if isinstance(value, (set, frozenset)):
        items = [_canonicalize(item) for item in value]
        items.sort(key=_canonical_json)
        return {"__set__": items}

    if isinstance(value, (list, tuple)):
        return {
            "__sequence_type__": "tuple" if isinstance(value, tuple) else "list",
            "items": [_canonicalize(item) for item in value],
        }

    # Permite que perfiles/configuraciones tipadas sigan siendo auditables sin
    # acoplar este módulo a sus clases concretas.
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict):
        return {
            "__object__": _qualified_type(value),
            "attrs": _canonicalize(attrs),
        }

    # Último recurso explícito y tipado. Para configuración normal no se usa.
    return {"__object_repr__": _qualified_type(value), "repr": repr(value)}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_bytes(value: Any) -> bytes:
    """Serializa un objeto a bytes canónicos para hashing."""

    return _canonical_json(_canonicalize(value)).encode("utf-8")


def fingerprint_config(config: Mapping[str, Any]) -> str:
    """Devuelve la huella SHA-256 de toda la configuración efectiva."""

    hasher = hashlib.sha256()
    hasher.update(f"rues-config-v{FINGERPRINT_PROTOCOL_VERSION}\0".encode())
    hasher.update(canonical_bytes(config))
    return hasher.hexdigest()


def fingerprint_file(path: str | Path) -> str:
    """Devuelve la huella SHA-256 completa de un archivo con RAM constante."""

    file_path = Path(path)
    hasher = hashlib.sha256()
    hasher.update(f"rues-file-v{FINGERPRINT_PROTOCOL_VERSION}\0".encode())
    with file_path.open("rb") as source:
        for block in iter(lambda: source.read(_FILE_CHUNK_SIZE), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _dtype_descriptor(dtype: Any) -> dict[str, Any]:
    """Describe el dtype, incluida metadata que ``str(dtype)`` puede omitir."""

    descriptor: dict[str, Any] = {
        "type": _qualified_type(dtype),
        "str": str(dtype),
        "repr": repr(dtype),
    }
    if isinstance(dtype, pd.CategoricalDtype):
        descriptor["ordered"] = dtype.ordered
        descriptor["categories"] = None if dtype.categories is None else dtype.categories.tolist()
    return descriptor


def _update_dataframe_hash(hasher: Any, df: pd.DataFrame) -> None:
    """Incorpora un DataFrame completo sin serializarlo entero en memoria."""

    schema = {
        "rows": len(df),
        "columns": [
            {
                "position": position,
                "label": _canonicalize(column),
                "dtype": _dtype_descriptor(dtype),
            }
            for position, (column, dtype) in enumerate(zip(df.columns, df.dtypes, strict=False))
        ],
    }
    hasher.update(canonical_bytes(schema))
    hasher.update(b"\0rows\0")

    for start in range(0, len(df), _ROW_CHUNK_SIZE):
        chunk = df.iloc[start : start + _ROW_CHUNK_SIZE]
        try:
            row_hashes = pd.util.hash_pandas_object(
                chunk,
                index=False,
                categorize=True,
                hash_key=_HASH_KEY,
            ).to_numpy(dtype=np.uint64, copy=False)
            # Fijar little-endian hace la huella independiente de arquitectura.
            hasher.update(row_hashes.astype("<u8", copy=False).tobytes(order="C"))
        except (TypeError, ValueError):
            # Objetos no hashables (listas/dicts en celdas) son infrecuentes.
            # El fallback es más lento, pero completo y determinista; no muestrea.
            for row in chunk.itertuples(index=False, name=None):
                hasher.update(canonical_bytes(row))
                hasher.update(b"\n")


def fingerprint_dataframe(df: pd.DataFrame) -> str:
    """Devuelve la huella SHA-256 completa de un DataFrame individual."""

    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"fingerprint_dataframe requiere DataFrame, recibido {_qualified_type(df)}")
    hasher = hashlib.sha256()
    hasher.update(f"rues-dataframe-v{FINGERPRINT_PROTOCOL_VERSION}\0".encode())
    _update_dataframe_hash(hasher, df)
    return hasher.hexdigest()


def fingerprint_sources(sources: Mapping[str, pd.DataFrame]) -> str:
    """Huella de fuentes, contenido, esquema, dtypes y orden de filas.

    El orden de las fuentes se incluye porque ``consolidate_sources`` concatena
    en orden de inserción y ese orden define los identificadores posicionales.
    """

    hasher = hashlib.sha256()
    hasher.update(f"rues-sources-v{FINGERPRINT_PROTOCOL_VERSION}\0".encode())
    hasher.update(canonical_bytes({"source_count": len(sources)}))

    for position, (source_name, df) in enumerate(sources.items()):
        if not isinstance(df, pd.DataFrame):
            raise TypeError(
                "fingerprint_sources requiere DataFrames; "
                f"la fuente {source_name!r} es {_qualified_type(df)}"
            )
        hasher.update(b"\0source\0")
        hasher.update(canonical_bytes({"position": position, "name": source_name}))
        _update_dataframe_hash(hasher, df)

    return hasher.hexdigest()


@lru_cache(maxsize=1)
def package_code_fingerprint() -> str:
    """Huella de la implementación y del ABI de dependencias relevantes."""

    hasher = hashlib.sha256()
    hasher.update(f"rues-code-v{FINGERPRINT_PROTOCOL_VERSION}\0".encode())

    package_root = Path(__file__).resolve().parents[1]
    source_files = sorted(
        package_root.rglob("*.py"),
        key=lambda source_path: source_path.relative_to(package_root).as_posix(),
    )
    for source_path in source_files:
        relative = source_path.relative_to(package_root).as_posix()
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        with source_path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                hasher.update(block)

    versions: dict[str, str | None] = {"python": platform.python_version()}
    for distribution in (
        "rues-linker",
        "pandas",
        "numpy",
        "pyarrow",
        "scipy",
        "scikit-learn",
        "networkx",
        "datasketch",
        "rapidfuzz",
        "psutil",
        "pytz",
        "tqdm",
        "openpyxl",
        "h5py",
    ):
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            versions[distribution] = None
    hasher.update(canonical_bytes(versions))
    return hasher.hexdigest()


__all__ = [
    "FINGERPRINT_PROTOCOL_VERSION",
    "canonical_bytes",
    "fingerprint_config",
    "fingerprint_dataframe",
    "fingerprint_file",
    "fingerprint_sources",
    "package_code_fingerprint",
]
