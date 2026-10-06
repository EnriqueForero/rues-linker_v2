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
apóstrofo ante ``=``, ``+``, ``-``, ``@`` y sin caracteres de control, y desde
F1.11 un dict/list/bytes en una celda como el mismo texto que csv.gz; una
regla escrita una vez) y se escribe fila a fila con ``write_row``.

Lo que Excel no representa como xlsxwriter lo escribiría
--------------------------------------------------------
* un ``timedelta`` xlsxwriter lo escribe como FECHA (``1 days`` → 1900-01-01):
  aquí va como texto, el mismo que csv.gz (``1 days 00:00:00``);
* una fecha anterior a 1900-03-01 no tiene serial fiable en Excel: antes de
  1900 xlsxwriter escribe un serial negativo y la celda se lee un día antes, el
  1900-01-01 se vuelve «solo hora» y enero-febrero de 1900 cargan con el
  defecto del año bisiesto 1900. Esas celdas van como texto ISO
  (``1899-01-01 00:00:00``); las demás de la columna siguen siendo fechas;
* un objeto que ni como texto se representa NO sale como ``TypeError``: sale
  como ``EscrituraSalidaError`` que nombra fila, columna y tipo, sin dejar
  restos; el escritor del estándar lo deja en ``omitidos`` y publica el resto.

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
import datetime
import decimal
import fractions
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
    "miles",
    "motivo_no_cabe",
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

#: Lo que se puede hacer cuando un Excel no se escribe: el estándar y L6 lo
#: dejan en ``omitidos`` y publican la carpeta (el parquet está completo), así
#: que nunca hay que repetir la corrida de ``linkage()``.
_QUE_HACER_SIN_EXCEL = (
    "la tabla completa está en el parquet y la carpeta se publica sin este Excel "
    "(queda en omitidos del manifiesto). Si hace falta el Excel, léalo del parquet, "
    "corrija la columna y vuelva a escribirlo con escribir_excel_o_leeme():"
)

#: Primera fecha FIABLE del calendario de Excel. Antes de 1900-01-01 no hay
#: serial (xlsxwriter escribe un número negativo y la celda se lee un día
#: antes); el propio 1900-01-01 xlsxwriter lo escribe como serial 0, que Excel
#: toma por «solo hora»; y enero-febrero de 1900 cargan con el defecto del año
#: bisiesto 1900 (el serial 60 es un 29 de febrero que no existió). Desde el
#: serial 61 (1900-03-01) todo el mundo lee lo mismo.
_PRIMERA_FECHA_EXCEL = pd.Timestamp("1900-03-01")

#: Formato del texto ISO con que se escribe una fecha anterior a 1900 (el mismo
#: aspecto que ``default_date_format`` da a las fechas de Excel).
_FORMATO_FECHA_TEXTO = "%Y-%m-%d %H:%M:%S"

#: Tipos de Python que xlsxwriter admite en una celda tal cual (lo demás se
#: intenta como número y, si no, lanza ``TypeError``). Sirve para NOMBRAR la
#: columna culpable cuando eso pasa.
_TIPOS_DE_CELDA: tuple[type, ...] = (
    str,
    int,
    float,
    bool,
    decimal.Decimal,
    fractions.Fraction,
    datetime.date,
    datetime.time,
    datetime.timedelta,
    type(None),
)

_CODIGOS_XLSXWRITER = {
    -1: "fila o columna fuera del rango de la hoja (o fila ya volcada en constant_memory)",
    -2: "una cadena supera los 32.767 caracteres que admite una celda",
    -3: "una URL supera el largo que admite Excel",
    -4: "la hoja superó las 65.530 URL",
    -5: "una fecha es anterior a 1900",
}


def miles(n: int) -> str:
    """``1048576`` → ``'1.048.576'`` (puntos de millar, como se lee en español)."""
    return f"{n:,}".replace(",", ".")


def _limite(limite: int | None) -> int:
    """``None`` → ``LIMITE_FILAS_EXCEL`` resuelto AL LLAMAR (una prueba puede fijarlo en el módulo)."""
    return LIMITE_FILAS_EXCEL if limite is None else limite


def motivo_no_cabe(n_filas: int, nombre_leeme: str, *, limite: int | None = None) -> str:
    """El motivo, escrito UNA vez, con que ``<base>.xlsx`` queda en ``omitidos`` cuando no cabe.

    Lo usan el escritor del estándar (``escritor._excel``) y los alias de v1
    (``reporting/strategies.py``): el mismo texto y los mismos puntos de millar
    que la hoja LEEME.
    """
    return (
        f"{miles(n_filas)} filas superan el límite de Excel ({miles(_limite(limite))} de datos); "
        f"no se recorta, ver {nombre_leeme}."
    )


def hoja_de_lineas(lineas: Sequence[str]) -> pd.DataFrame:
    """Una hoja LEEME: una columna ``LEEME`` con una línea por fila."""
    return pd.DataFrame({HOJA_LEEME: list(lineas)})


def leeme_no_cabe(
    base: str,
    n_filas: int,
    *,
    limite: int | None = None,
    ruta_parquet: str | None = None,
) -> pd.DataFrame:
    """Hoja LEEME de «no cabe»: filas, nombre del parquet y cómo abrirlo.

    Args:
        base: tronco del parquet (``<base>.parquet``).
        n_filas: filas de datos de la tabla.
        limite: filas de datos que caben; ``None`` usa ``LIMITE_FILAS_EXCEL``.
        ruta_parquet: la ruta del parquet RELATIVA a la carpeta del LEEME, para
            que ``pd.read_parquet(...)`` funcione tal cual desde ahí (el
            estándar escribe el LEEME en ``excel/`` y el parquet un nivel
            arriba: ``../<base>.parquet``). ``None`` cita ``<base>.parquet``
            (los alias de v1 tienen el parquet al lado).
    """
    parquet = f"{base}.parquet"
    abrir = parquet if ruta_parquet is None else ruta_parquet
    return hoja_de_lineas(
        [
            f"{parquet} tiene {miles(n_filas)} filas y una hoja de Excel admite "
            f"{miles(_limite(limite) + 1)} (incluido el encabezado).",
            "No se escribe un recorte: la tabla completa está en el parquet.",
            f"pandas: pd.read_parquet('{abrir}')",
            f"DuckDB: SELECT * FROM '{abrir}'",
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
            f"xlsxwriter no pudo escribir la fila {miles(fila)} de {ruta.name}: {motivo}.",
            "el Excel quedaría incompleto y nadie lo sabría.",
            f"{_QUE_HACER_SIN_EXCEL} acorte o retire la columna con ese valor.",
        )
    )


def _fallo_tipo(
    ruta: Path, fila: int, columnas: Sequence[Any], celdas: Sequence[Any], exc: TypeError
) -> EscrituraSalidaError:
    """xlsxwriter lanzó ``TypeError`` en ``write_row``: se nombra fila, columna y tipo."""
    culpable = next(
        (
            (c, v)
            for c, v in zip(columnas, celdas, strict=False)
            if not isinstance(v, _TIPOS_DE_CELDA)
        ),
        None,
    )
    donde = (
        f"en la columna {culpable[0]} un valor de tipo {type(culpable[1]).__name__}"
        if culpable is not None
        else "un valor"
    )
    return EscrituraSalidaError(
        mensaje_accionable(
            f"{ruta.name}: la fila {miles(fila)} trae {donde} que Excel no admite ({exc}).",
            "el Excel quedaría incompleto y nadie lo sabría.",
            f"{_QUE_HACER_SIN_EXCEL} convierta esa columna a texto "
            "(df[col] = df[col].astype('string')) o retírela.",
        )
    )


def _fallo_al_volcar(ruta: Path, exc: TypeError) -> EscrituraSalidaError:
    """xlsxwriter lanzó ``TypeError`` al VOLCAR la hoja (``close``), sin fila a la vista."""
    return EscrituraSalidaError(
        mensaje_accionable(
            f"xlsxwriter no pudo volcar {ruta.name}: un valor que Excel no admite ({exc}).",
            "el Excel quedaría incompleto y nadie lo sabría.",
            f"{_QUE_HACER_SIN_EXCEL} convierta las columnas de objetos a texto "
            "(df[col] = df[col].astype('string')) o retírelas.",
        )
    )


def _fallo_zona_horaria(ruta: Path, columnas: list[str]) -> EscrituraSalidaError:
    return EscrituraSalidaError(
        mensaje_accionable(
            f"{ruta.name}: las columnas {columnas} traen fechas con zona horaria.",
            "Excel no admite zona horaria en una celda y quitarla en silencio cambiaría "
            "la hora que el lector ve.",
            f"{_QUE_HACER_SIN_EXCEL} convierta esas columnas "
            "(df[col].dt.tz_convert('UTC').dt.tz_localize(None) o astype('string')).",
        )
    )


def _columnas_con_zona_horaria(lote: pd.DataFrame) -> list[str]:
    return [str(c) for c, d in lote.dtypes.items() if isinstance(d, pd.DatetimeTZDtype)]


def _escribir_hoja_leeme(libro: Any, leeme: pd.DataFrame) -> None:
    hoja = libro.add_worksheet(HOJA_LEEME)
    for i, fila in enumerate(leeme.to_numpy(dtype=object).tolist()):
        hoja.write_row(i, 0, fila)


def _fechas_antiguas(columna: pd.Series) -> np.ndarray:
    """Máscara de las celdas anteriores a 1900-03-01 (sin serial fiable en Excel); NaT → False.

    Cubre las columnas ``datetime64`` y las ``object`` de ``datetime.date`` /
    ``datetime.datetime`` (una ``date32`` de parquet llega por lotes así).
    """
    if pd.api.types.is_datetime64_dtype(columna.dtype):
        return (columna < _PRIMERA_FECHA_EXCEL).to_numpy()
    if pd.api.types.is_object_dtype(columna.dtype) and pd.api.types.infer_dtype(
        columna, skipna=True
    ) in ("date", "datetime"):
        try:
            fechas = pd.to_datetime(columna, errors="coerce")
        except (ValueError, TypeError):
            return np.zeros(len(columna), dtype=bool)  # mezcla de zonas: xlsxwriter dirá
        return (fechas < _PRIMERA_FECHA_EXCEL).to_numpy()
    return np.zeros(len(columna), dtype=bool)


def _texto_iso(valor: Any) -> str:
    if isinstance(valor, pd.Timestamp | datetime.datetime):
        return valor.strftime(_FORMATO_FECHA_TEXTO)
    return valor.isoformat()  # datetime.date


def _celdas(lote: pd.DataFrame) -> np.ndarray:
    """Matriz de objetos lista para ``write_row``.

    Ausente → ``None`` (celda vacía); ±inf → texto; ``timedelta`` → texto (el de
    csv.gz; xlsxwriter lo escribiría como la fecha 1900-01-01); fecha anterior a
    1900-03-01 → texto ISO (sin serial fiable en Excel: se leería un día antes).

    ``copy=True`` es obligatorio: con pandas 3 ``to_numpy`` devuelve un arreglo
    de SOLO LECTURA cuando el frame tiene un único bloque (todas las columnas
    int, todas float, una sola de texto…) y la asignación siguiente lanzaría
    ``ValueError: assignment destination is read-only``.
    """
    valores = lote.to_numpy(dtype=object, copy=True)
    valores[pd.isna(valores)] = None  # ausente → celda vacía (nunca "nan")
    for i, dtipo in enumerate(lote.dtypes):
        columna = lote.iloc[:, i]
        if pd.api.types.is_float_dtype(dtipo):
            flotantes = columna.to_numpy(dtype=float, na_value=np.nan)
            infinitos = np.isinf(flotantes)
            if infinitos.any():
                valores[infinitos, i] = [str(v) for v in flotantes[infinitos]]
        elif pd.api.types.is_timedelta64_dtype(dtipo):
            presentes = columna.notna().to_numpy()
            if presentes.any():
                valores[presentes, i] = columna[presentes].astype("string").to_numpy(dtype=object)
        else:
            antiguas = _fechas_antiguas(columna)
            if antiguas.any():
                valores[antiguas, i] = [_texto_iso(v) for v in columna[antiguas]]
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
        if con_zona := _columnas_con_zona_horaria(lote):
            raise _fallo_zona_horaria(ruta, con_zona)
        valores = _celdas(prepare_spreadsheet_data(lote))
        for celdas in valores.tolist():
            try:
                codigo = ws.write_row(fila, 0, celdas)
            except TypeError as exc:  # un objeto que ni como texto se representa
                raise _fallo_tipo(ruta, fila, columnas, celdas, exc) from exc
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
    ruta_parquet: str | None = None,
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
        ruta_parquet: ruta del parquet relativa a la carpeta del LEEME (ver
            ``leeme_no_cabe``); ``None`` cita ``<base>.parquet``.

    Returns:
        La ruta escrita: ``ruta`` o ``ruta.with_name(f"{base}_LEEME.xlsx")``.

    Raises:
        EscrituraSalidaError: si xlsxwriter rechaza un valor (una cadena de más
            de 32.767 caracteres; un objeto que ni como texto se representa,
            nombrando la columna) o una columna trae fechas con zona horaria
            (Excel no la admite y no se quita en silencio); el archivo parcial
            se borra y nunca sale un ``TypeError``. El Excel es opcional en el
            estándar: quien llama lo deja en ``omitidos`` con este mensaje y
            publica el resto. Un dict/list/bytes en una celda NO falla: va como
            texto (``prepare_spreadsheet_data``), igual que en csv.gz.
    """
    ruta = Path(ruta)
    tope = _limite(limite)
    if filas_por_lote <= 0:
        raise ValueError("filas_por_lote debe ser > 0.")
    es_parquet = isinstance(df, pq.ParquetFile)
    n_filas = df.metadata.num_rows if es_parquet else len(df)
    base = ruta.stem

    if n_filas > tope:
        destino = ruta.with_name(f"{base}{SUFIJO_LEEME}{ruta.suffix}")
        hoja_leeme = leeme_no_cabe(base, n_filas, limite=tope, ruta_parquet=ruta_parquet)
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
        try:
            libro.close()  # en constant_memory aquí se vuelca la hoja y puede rechazar una celda
        except TypeError as exc:
            raise _fallo_al_volcar(ruta, exc) from exc
    except BaseException:
        with contextlib.suppress(Exception):
            libro.close()  # cierra el temporal de constant_memory
        ruta.unlink(missing_ok=True)
        raise
