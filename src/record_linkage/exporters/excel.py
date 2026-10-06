"""record_linkage.exporters.excel — Excel completo hasta 1.048.576 filas o ``<base>_LEEME.xlsx`` (F1.11).

La regla (decisión de Enrique, estándar de salida): ``correlativa.xlsx`` y
``golden.xlsx`` se escriben COMPLETOS hasta ``LIMITE_FILAS_EXCEL`` filas de
datos (1.048.576 filas de hoja menos el encabezado). Si la tabla no cabe, NO se
escribe un recorte: se escribe ``<base>_LEEME.xlsx`` con una sola hoja que dice
cuántas filas tiene la tabla, en qué parquet está y cómo abrirla (pandas,
DuckDB, Power Query). Nunca más la muestra recortada de v1. Esta regla se escribe UNA
vez, aquí; el escritor del estándar (``exporters.escritor``) y los alias de v1
de L6 (``reporting/strategies.py``) la llaman.

Cómo se escribe (en flujo, sin duplicar la tabla en RAM)
--------------------------------------------------------
Con ``xlsxwriter`` en modo ``constant_memory``: cada fila se vuelca a un
archivo temporal en cuanto se escribe la siguiente, así que la RAM no crece con
la tabla (un millón de filas con openpyxl, que arma el libro entero en memoria,
superaba los 2 GB). La tabla se recorre por lotes de ``FILAS_POR_LOTE`` filas;
cada lote pasa por ``prepare_spreadsheet_data`` (la neutralización de 0.22.4:
apóstrofo ante ``=``, ``+``, ``-``, ``@`` y sin caracteres de control, una
regla escrita una vez) y se escribe fila a fila con ``write_row``.

Por qué no ``pd.ExcelWriter(engine="xlsxwriter", constant_memory=True)``
-------------------------------------------------------------------------
Era lo previsto en la especificación de F1.11 y se descartó al medirlo:
``DataFrame.to_excel`` entrega las celdas COLUMNA a columna, y en modo
``constant_memory`` xlsxwriter descarta toda celda de una fila que ya volcó;
el libro quedaba con una sola columna con datos (3 columnas → solo la última).
Las filas hay que escribirlas en orden, y eso obliga a llamar a xlsxwriter
directamente. El bucle por filas es inevitable en cualquier escritor xlsx en
Python puro (pandas también itera celda a celda por debajo); lo que sí se
cuida es que cada lote sea pequeño y que la conversión a objetos de Python se
haga por lote con numpy, no celda a celda.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-10-06  ·  Version: 0.23.0
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import xlsxwriter

from ..pipeline.errores import EscrituraSalidaError, mensaje_accionable
from ._spreadsheet import escape_spreadsheet_value, prepare_spreadsheet_data

__all__ = [
    "FILAS_POR_LOTE",
    "LIMITE_FILAS_EXCEL",
    "SUFIJO_LEEME",
    "escribir_excel_o_leeme",
    "hoja_de_lineas",
    "leeme_no_cabe",
]

#: Filas de DATOS que caben en una hoja de Excel: 1.048.576 filas de hoja
#: menos una de encabezado. Por encima no se recorta: se escribe el LEEME.
LIMITE_FILAS_EXCEL = 1_048_575

#: Filas por lote al volcar una tabla a Excel (y al leer un parquet por lotes).
FILAS_POR_LOTE = 50_000

#: Sufijo del libro que sustituye al Excel que no cabe: ``<base>_LEEME.xlsx``.
SUFIJO_LEEME = "_LEEME"

#: Nombre de la hoja LEEME (va primero cuando existe).
HOJA_LEEME = "LEEME"

#: Opciones del libro xlsxwriter. ``constant_memory`` vuelca cada fila al
#: escribir la siguiente; ``strings_to_formulas``/``strings_to_urls`` en False
#: para que ningún texto se interprete (la neutralización ya pone el apóstrofo,
#: esto es la segunda red). Los ausentes (NaN/NaT/None) se escriben como celda
#: vacía y un ±inf como el texto ``inf``/``-inf`` (lo mismo que ``to_csv``)
#: ANTES de llegar aquí: ``nan_inf_to_errors`` de xlsxwriter los escribiría
#: como la FÓRMULA ``=1/0``, y en el entregable no entra ninguna fórmula.
_OPCIONES_LIBRO: dict[str, Any] = {
    "constant_memory": True,
    "strings_to_formulas": False,
    "strings_to_urls": False,
    "default_date_format": "yyyy-mm-dd hh:mm:ss",
}

_CODIGOS_XLSXWRITER = {
    -1: "fila o columna fuera del rango de la hoja (o fila ya volcada en constant_memory)",
    -2: "una cadena supera los 32.767 caracteres que admite una celda",
    -3: "una URL supera el largo que admite Excel",
    -4: "la hoja superó las 65.530 URL",
    -5: "una fecha es anterior a 1900",
}


def _miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def hoja_de_lineas(lineas: Sequence[str]) -> pd.DataFrame:
    """Una hoja LEEME: una columna ``LEEME`` con una línea por fila."""
    return pd.DataFrame({HOJA_LEEME: list(lineas)})


def leeme_no_cabe(base: str, n_filas: int, *, limite: int = LIMITE_FILAS_EXCEL) -> pd.DataFrame:
    """Hoja LEEME de «no cabe»: filas, nombre del parquet y cómo abrirlo."""
    parquet = f"{base}.parquet"
    return hoja_de_lineas(
        [
            f"{parquet} tiene {_miles(n_filas)} filas y una hoja de Excel admite "
            f"{_miles(limite + 1)} (incluido el encabezado).",
            "No se escribe un recorte: la tabla completa está en el parquet.",
            f"pandas: pd.read_parquet('{parquet}')",
            f"DuckDB: SELECT * FROM '{parquet}'",
            "Power Query (Excel 365): Datos → Obtener datos → De archivo → Parquet.",
        ]
    )


def _lotes_de_df(df: pd.DataFrame, filas_por_lote: int) -> Iterator[pd.DataFrame]:
    for inicio in range(0, len(df), filas_por_lote):
        yield df.iloc[inicio : inicio + filas_por_lote]


def _lotes_de_parquet(archivo: pq.ParquetFile, filas_por_lote: int) -> Iterator[pd.DataFrame]:
    for lote in archivo.iter_batches(batch_size=filas_por_lote):
        yield lote.to_pandas()


def _columnas_de_parquet(archivo: pq.ParquetFile) -> list[Any]:
    return list(archivo.schema_arrow.empty_table().to_pandas().columns)


def _fallo(ruta: Path, codigo: int, fila: int) -> EscrituraSalidaError:
    motivo = _CODIGOS_XLSXWRITER.get(codigo, f"código {codigo} de xlsxwriter")
    return EscrituraSalidaError(
        mensaje_accionable(
            f"xlsxwriter no pudo escribir la fila {_miles(fila)} de {ruta.name}: {motivo}.",
            "el Excel quedaría incompleto y nadie lo sabría.",
            "la tabla completa está en el parquet; si hace falta el Excel, acorte o retire "
            "la columna con ese valor antes de llamar a linkage().",
        )
    )


def _escribir_hoja_leeme(libro: Any, leeme: pd.DataFrame) -> None:
    hoja = libro.add_worksheet(HOJA_LEEME)
    for i, fila in enumerate(leeme.to_numpy(dtype=object).tolist()):
        hoja.write_row(i, 0, fila)


def _celdas(lote: pd.DataFrame) -> np.ndarray:
    """Matriz de objetos lista para ``write_row``: ausente → ``None``, ±inf → texto."""
    valores = lote.to_numpy(dtype=object)
    valores[pd.isna(valores)] = None  # ausente → celda vacía (nunca "nan")
    for i, dtipo in enumerate(lote.dtypes):
        if not pd.api.types.is_float_dtype(dtipo):
            continue
        columna = lote.iloc[:, i].to_numpy(dtype=float, na_value=np.nan)
        infinitos = np.isinf(columna)
        if infinitos.any():
            valores[infinitos, i] = [str(v) for v in columna[infinitos]]
    return valores


def _escribir_datos(
    libro: Any, hoja: str, columnas: Sequence[Any], lotes: Iterator[pd.DataFrame], ruta: Path
) -> int:
    """Encabezado + lotes neutralizados, fila a fila. Devuelve las filas de datos."""
    ws = libro.add_worksheet(hoja)
    encabezado = [str(escape_spreadsheet_value(c)) for c in columnas]
    codigo = ws.write_row(0, 0, encabezado)
    if codigo:
        raise _fallo(ruta, codigo, 0)
    fila = 1
    for lote in lotes:
        valores = _celdas(prepare_spreadsheet_data(lote))
        for celdas in valores.tolist():
            codigo = ws.write_row(fila, 0, celdas)
            if codigo:
                raise _fallo(ruta, codigo, fila)
            fila += 1
        del valores
    return fila - 1


def escribir_excel_o_leeme(
    df: pd.DataFrame | pq.ParquetFile,
    ruta: Path,
    *,
    limite: int | None = None,
    hoja: str = "DATOS",
    leeme: pd.DataFrame | None = None,
    filas_por_lote: int = FILAS_POR_LOTE,
) -> Path:
    """Escribe ``df`` completo en ``ruta`` o, si no cabe, ``<base>_LEEME.xlsx``.

    Args:
        df: la tabla, en memoria o como ``pq.ParquetFile`` (se lee por lotes;
            el conteo sale de los metadatos, sin leerla entera).
        ruta: ``<carpeta>/<base>.xlsx``. ``<base>`` nombra también el parquet
            que el LEEME cita (``<base>.parquet``): el estándar y los alias de
            v1 escriben parquet y xlsx con el mismo tronco.
        limite: filas de datos que caben; ``None`` usa ``LIMITE_FILAS_EXCEL``
            (resuelto al llamar, no al definir, para que una prueba pueda
            fijarlo en el módulo).
        hoja: nombre de la hoja de datos (``DATOS`` por defecto).
        leeme: hoja LEEME que va PRIMERO (los alias de v1 remiten al archivo
            nuevo). Si la tabla no cabe, sus líneas preceden a las de «no cabe».
        filas_por_lote: tamaño del lote de escritura.

    Returns:
        La ruta escrita: ``ruta`` o ``ruta.with_name(f"{base}_LEEME.xlsx")``.

    Raises:
        EscrituraSalidaError: si xlsxwriter rechaza un valor (p. ej. una cadena
            de más de 32.767 caracteres); el archivo parcial se borra.
    """
    ruta = Path(ruta)
    tope = LIMITE_FILAS_EXCEL if limite is None else limite
    if filas_por_lote <= 0:
        raise ValueError("filas_por_lote debe ser > 0.")
    es_parquet = isinstance(df, pq.ParquetFile)
    n_filas = df.metadata.num_rows if es_parquet else len(df)
    base = ruta.stem

    if n_filas > tope:
        destino = ruta.with_name(f"{base}{SUFIJO_LEEME}{ruta.suffix}")
        hoja_leeme = leeme_no_cabe(base, n_filas, limite=tope)
        if leeme is not None:
            hoja_leeme = pd.concat([leeme, hoja_leeme], ignore_index=True)
        _con_libro(destino, hoja_leeme, None, (), iter(()))
        return destino

    columnas = _columnas_de_parquet(df) if es_parquet else list(df.columns)
    lotes = (
        _lotes_de_parquet(df, filas_por_lote) if es_parquet else _lotes_de_df(df, filas_por_lote)
    )
    _con_libro(ruta, leeme, hoja, columnas, lotes)
    return ruta


def _con_libro(
    ruta: Path,
    leeme: pd.DataFrame | None,
    hoja: str | None,
    columnas: Sequence[Any],
    lotes: Iterator[pd.DataFrame],
) -> None:
    """Abre el libro, escribe LEEME (si hay) y datos (si ``hoja``); si falla, no deja restos."""
    libro = xlsxwriter.Workbook(str(ruta), _OPCIONES_LIBRO)
    try:
        if leeme is not None:
            _escribir_hoja_leeme(libro, leeme)
        if hoja is not None:
            _escribir_datos(libro, hoja, columnas, lotes, ruta)
        libro.close()
    except BaseException:
        with contextlib.suppress(Exception):
            libro.close()  # cierra el temporal de constant_memory
        ruta.unlink(missing_ok=True)
        raise
