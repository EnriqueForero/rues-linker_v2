"""Neutralización común para datos destinados a hojas de cálculo."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")

#: Caracteres de control que una hoja de cálculo NO admite: openpyxl lanza
#: ``IllegalCharacterError`` y el archivo no se escribe. Es el mismo conjunto
#: que ``openpyxl.cell.cell.ILLEGAL_CHARACTERS_RE`` (tab, CR y LF sí se
#: admiten). Medido en 0.22.4: 10 razones sociales de la base real de
#: importadores traían ``\x1a`` —el sustituto que deja un decodificador ante un
#: byte inválido, mojibake de "Ñ"— y la exportación completa caía después de
#: 14 minutos de emparejamiento correcto.
CONTROL_CHARACTERS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def strip_control_characters(value: Any) -> Any:
    """Quita los caracteres de control que openpyxl rechaza; el resto, intacto."""

    if isinstance(value, str) and CONTROL_CHARACTERS_RE.search(value):
        return CONTROL_CHARACTERS_RE.sub("", value)
    return value


def escape_spreadsheet_value(value: Any) -> Any:
    """Neutraliza prefijos de fórmula y caracteres de control sin tocar valores no textuales."""

    value = strip_control_characters(value)
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def validate_leaf_name(value: str, label: str) -> str:
    """Valida un nombre de archivo portable que no pueda escapar su directorio."""

    if not isinstance(value, str):
        raise TypeError(f"{label} debe ser str, recibido {type(value).__name__}")
    if not value or not value.strip():
        raise ValueError(f"{label} no puede estar vacío")
    if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"{label} debe ser un nombre simple, sin rutas ni separadores: {value!r}")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} contiene caracteres de control")
    return value


def safe_sheet_name(value: str, used: set[str]) -> str:
    """Devuelve un nombre de hoja Excel válido y único (case-insensitive)."""

    clean = "".join("_" if char in "[]:*?/\\" or ord(char) < 32 else char for char in value)
    clean = clean.strip("'").strip() or "Sheet"
    base = clean[:31]
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        tail = f"_{suffix}"
        candidate = f"{base[: 31 - len(tail)]}{tail}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def prepare_spreadsheet_data(df: pd.DataFrame, *, include_index: bool = False) -> pd.DataFrame:
    """Devuelve una vista segura para CSV/XLSX sin mutar el DataFrame fuente.

    Solo copia las columnas que contienen texto peligroso: prefijos de fórmula
    (``=``, ``+``, …) o caracteres de control que openpyxl rechaza
    (:data:`CONTROL_CHARACTERS_RE`). Las columnas numéricas (incluidos números
    negativos legítimos) conservan su dtype y sus buffers siempre que sea
    posible. Si ``include_index`` es True también neutraliza valores y nombres
    del índice exportado a CSV.

    Nota de portabilidad (pandas 2 vs 3): al reescribir una columna ``object``
    con faltantes, pandas >= 3 normaliza ``None`` a ``NaN``. Es comportamiento
    del propio pandas —cualquier ruta de asignación lo hace— y es inocuo para
    una hoja de cálculo, donde ambos se escriben como celda vacía. El contrato
    que esta función sí garantiza es: el DataFrame de entrada nunca se muta, un
    faltante sigue siendo faltante y solo el texto peligroso cambia.
    """

    out = df
    if any(
        isinstance(column, str) and column.startswith(FORMULA_PREFIXES) for column in df.columns
    ):
        out = df.copy(deep=False)
        out.columns = [escape_spreadsheet_value(column) for column in df.columns]

    # El acceso posicional conserva correctamente etiquetas de columna
    # duplicadas. Categorical necesita object para aceptar el nuevo prefijo.
    for position in range(df.shape[1]):
        series = df.iloc[:, position]
        try:
            mask = series.str.startswith(FORMULA_PREFIXES, na=False)
            control = series.str.contains(CONTROL_CHARACTERS_RE.pattern, regex=True, na=False)
        except (AttributeError, TypeError):
            continue
        if not (bool(mask.any()) or bool(control.any())):
            continue
        if out is df:
            out = df.copy(deep=False)
        escaped = series.astype(object).copy()
        if bool(control.any()):
            # Primero los controles: un "\x1a=SUMA" queda "=SUMA" y ENTONCES
            # recibe el apóstrofo que lo neutraliza como fórmula.
            escaped.loc[control] = (
                escaped.loc[control]
                .astype(str)
                .str.replace(CONTROL_CHARACTERS_RE.pattern, "", regex=True)
            )
            mask = (
                escaped.astype(object)
                .map(lambda v: isinstance(v, str) and v.startswith(FORMULA_PREFIXES))
                .astype(bool)
            )
        if bool(mask.any()):
            escaped.loc[mask] = "'" + escaped.loc[mask].astype(str)
        out.isetitem(position, escaped.to_numpy(copy=False))

    if include_index:
        index_changed = False
        escaped_index: list[Any] = []
        for value in df.index:
            if isinstance(value, tuple):
                escaped_value = tuple(escape_spreadsheet_value(item) for item in value)
                index_changed |= any(
                    isinstance(item, str) and item.startswith(FORMULA_PREFIXES) for item in value
                )
            else:
                escaped_value = escape_spreadsheet_value(value)
                index_changed |= isinstance(value, str) and value.startswith(FORMULA_PREFIXES)
            escaped_index.append(escaped_value)

        escaped_names = [escape_spreadsheet_value(name) for name in df.index.names]
        index_changed |= any(
            isinstance(name, str) and name.startswith(FORMULA_PREFIXES) for name in df.index.names
        )
        if index_changed:
            if out is df:
                out = df.copy(deep=False)
            if isinstance(df.index, pd.MultiIndex):
                out.index = pd.MultiIndex.from_tuples(escaped_index, names=escaped_names)
            else:
                out.index = pd.Index(escaped_index, name=escaped_names[0])
    return out


__all__ = [
    "CONTROL_CHARACTERS_RE",
    "FORMULA_PREFIXES",
    "escape_spreadsheet_value",
    "prepare_spreadsheet_data",
    "safe_sheet_name",
    "strip_control_characters",
    "validate_leaf_name",
]
