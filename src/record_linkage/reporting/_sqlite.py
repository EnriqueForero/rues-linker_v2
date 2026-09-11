"""Shared SQLite safety primitives for reporting readers.

Reporting accepts database paths and, in a few internal extension points,
table names. SQLite does not support binding identifiers, so an identifier is
first resolved against ``sqlite_master`` and only then quoted. Values and
limits remain bound parameters.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from numbers import Integral
from pathlib import Path


class SQLiteTableNotFoundError(ValueError):
    """Raised when a requested reporting table is absent from the catalog."""


def validate_row_limit(value: object, label: str = "sample_size") -> int:
    """Return a non-negative integer suitable for a bound ``LIMIT`` value."""

    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{label} debe ser un entero, recibido {type(value).__name__}")
    result = int(value)
    if result < 0:
        raise ValueError(f"{label} no puede ser negativo: {result}")
    return result


def quote_sqlite_identifier(identifier: str) -> str:
    """Quote one SQLite identifier after basic structural validation."""

    if not isinstance(identifier, str) or not identifier or "\x00" in identifier:
        raise ValueError(f"Identificador SQLite inválido: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def quote_existing_table(conn: sqlite3.Connection, table_name: str) -> str:
    """Resolve an exact regular-table name and return its quoted identifier."""

    if not isinstance(table_name, str) or not table_name or "\x00" in table_name:
        raise ValueError(f"Nombre de tabla SQLite inválido: {table_name!r}")
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    if row is None:
        raise SQLiteTableNotFoundError(f"Tabla SQLite no encontrada: {table_name!r}")
    return quote_sqlite_identifier(str(row[0]))


@contextmanager
def open_readonly_sqlite(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a local SQLite file read-only and always close the connection.

    ``Path.as_uri`` percent-encodes ``?`` and ``#`` in filenames, preventing a
    path from injecting SQLite URI query parameters. ``query_only`` is an
    independent defense if connection flags change in a future refactor.
    """

    path = Path(db_path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise FileNotFoundError(f"Base SQLite no es un archivo: {path}")

    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        # Reduce the attack surface of schemas from untrusted local files.
        try:
            conn.execute("PRAGMA trusted_schema = OFF")
        except sqlite3.DatabaseError:
            # Compatibility with older SQLite builds that lack this pragma.
            pass
        yield conn
    finally:
        conn.close()
