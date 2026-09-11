"""Optimización de dtypes para reducir RAM en columnas de texto.

Centraliza (DRY) el casteo de columnas de texto pesadas a ``string[pyarrow]``,
que almacena un buffer Arrow contiguo en lugar de un objeto Python por celda:
~30-60 % menos RAM (medido) frente a ``object``. Pensado para Colab Free a escala
de millones de filas. Si pyarrow no está disponible, deja las columnas intactas
(degradación silenciosa, sin romper el pipeline).
"""

from __future__ import annotations

import pandas as pd


def optimizar_dtypes_texto(df: pd.DataFrame, columnas: list[str]) -> pd.DataFrame:
    """Castea las columnas de texto indicadas a ``string[pyarrow]`` (in-place).

    Solo afecta a las columnas presentes; ignora las ausentes. No cambia los
    valores (mismo contenido textual), solo su representación en memoria. Es
    idempotente: una columna ya en ``string[pyarrow]`` se omite.

    Args:
        df: DataFrame a optimizar (modificado in-place y también devuelto).
        columnas: Nombres de columnas de texto a convertir.

    Returns:
        El mismo DataFrame, con las columnas convertidas cuando fue posible.
    """
    for col in columnas:
        if col not in df.columns:
            continue
        if str(df[col].dtype) == "string[pyarrow]":
            continue  # ya optimizada
        try:
            df[col] = df[col].astype("string[pyarrow]")
        except (ImportError, TypeError, ValueError):  # pragma: no cover - sin pyarrow
            pass
    return df


def optimizar_dtypes_categoricos(df: pd.DataFrame, columnas: list[str]) -> pd.DataFrame:
    """Castea columnas de BAJA cardinalidad a ``category`` (v0.13.0, T1 dian).

    Un ``category`` guarda un código entero por celda + un diccionario único:
    para columnas como SRC (4-6 fuentes) o CIUDAD (~1.100 municipios) el
    ahorro frente a object/string es de 30-80 bytes → 1-4 bytes por celda.
    Práctica transferida de ``dian_comercio.memoria.optimizar_categoricas``.

    ⚠️ Trampa documentada en dian-comercio: un ``groupby`` sobre columna
    categórica con ``observed=False`` genera una fila por CADA categoría del
    diccionario (también las ausentes del subconjunto) e inventa ceros. En
    pandas >= 2.1 el default ya es el correcto (``observed=True``); si su
    código hace groupby con ``observed=False`` explícito, revíselo antes de
    activar esta optimización (por eso es OPT-IN vía el perfil:
    ``use_categorical_dtypes: True``).

    Solo castea columnas PRESENTES cuya cardinalidad sea < 50% de las filas
    (en alta cardinalidad la categoría no ahorra, o gasta más). Idempotente.
    """
    n = len(df)
    if n == 0:
        return df
    for col in columnas:
        if col not in df.columns or isinstance(df[col].dtype, pd.CategoricalDtype):
            continue
        try:
            if df[col].nunique(dropna=True) <= max(1, n // 2):
                df[col] = df[col].astype("category")
        except (TypeError, ValueError):  # pragma: no cover - tipos exóticos
            continue
    return df
