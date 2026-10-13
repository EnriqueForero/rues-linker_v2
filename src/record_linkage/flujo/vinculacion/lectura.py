"""Lectura de las fuentes del motor de vinculación: del archivo a una tabla con filas trazables.

``leer_fuente`` abre la base que declara una ``Fuente`` (Excel, CSV/TXT/TSV o Parquet), verifica que
estén todas las columnas declaradas, descarta las filas completamente vacías y pone tres columnas al
frente: ``FILA_EXCEL`` (la fila real de la hoja, con el encabezado en la 1), ``ID_REGISTRO`` (el
identificador nativo o ``<prefijo>-F<fila>``) y ``FUENTE`` (la clave de la base).

``sha256_archivo`` da la huella de cualquier archivo de entrada, para dejarla en la hoja de parámetros.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.config import Fuente

__all__ = ["TRANSFORMACIONES", "formatear_hs6", "leer_fuente", "leer_tabla", "sha256_archivo"]

#: Bytes del comienzo de un CSV/TXT/TSV que se miran para decidir el separador.
_BYTES_MUESTRA_SEPARADOR = 4096


def sha256_archivo(ruta: str | Path) -> str:
    """SHA-256 (hexadecimal) del contenido de un archivo, leído por bloques de 1 MiB."""
    h = hashlib.sha256()
    with Path(ruta).open("rb") as fh:
        for bloque in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _detectar_separador(texto: str) -> str:
    """Tabulador, punto y coma o coma: el que más aparece en ``texto`` (en empate, la coma)."""
    tabuladores, puntos_y_coma, comas = texto.count("\t"), texto.count(";"), texto.count(",")
    if tabuladores > puntos_y_coma and tabuladores > comas:
        return "\t"
    return ";" if puntos_y_coma > comas else ","


def _lineas_en_blanco_iniciales(muestra: str) -> int:
    """Cuántas líneas en blanco hay antes del encabezado (en la muestra); no cuentan para ``FILA_EXCEL``."""
    n = 0
    for linea in muestra.splitlines():
        if linea.strip():
            break
        n += 1
    return n


def _leer_csv(ruta: Path) -> pd.DataFrame:
    """Lee un CSV/TXT/TSV como texto: separador detectado, UTF-8 (con o sin BOM) y si no, cp1252.

    Los textos 'NA' (Namibia), 'N/A', 'None', 'null'… se conservan tal cual: solo la celda vacía es ausente.
    Las líneas en blanco del medio se leen como filas vacías (``leer_fuente`` las descarta conservando la
    numeración de las demás); las que van antes del encabezado se ignoran.
    """
    # Solo se leen los primeros bytes para decidir el separador, no el archivo entero.
    with ruta.open("rb") as fh:
        muestra = fh.read(_BYTES_MUESTRA_SEPARADOR).decode("utf-8", errors="ignore")
    opciones = {
        "sep": _detectar_separador(muestra),
        "dtype": object,
        "keep_default_na": False,
        "na_values": [""],
        "skip_blank_lines": False,
        "skiprows": _lineas_en_blanco_iniciales(muestra),
    }
    try:
        return pd.read_csv(ruta, encoding="utf-8-sig", **opciones)
    except UnicodeDecodeError:
        return pd.read_csv(ruta, encoding="cp1252", **opciones)


def leer_tabla(ruta: str | Path, hoja: str | int = 0) -> pd.DataFrame:
    """Lee una tabla según la extensión del archivo.

    * ``.xlsx``, ``.xlsm``, ``.xls``: la ``hoja`` (nombre o posición), todo como ``object``.
    * ``.parquet``: tal cual viene.
    * ``.csv``, ``.txt``, ``.tsv``: todo como texto, con el separador detectado (``hoja`` no aplica).

    Raises:
        FileNotFoundError: la ruta no existe.
        ValueError: la extensión no es una de las anteriores.
    """
    p = Path(ruta)
    if not p.exists():
        raise FileNotFoundError(
            f"Qué pasó: no existe {p}.\nQué hacer: corrija la ruta en la configuración."
        )
    suf = p.suffix.lower()
    if suf in (".xlsx", ".xlsm", ".xls"):
        try:
            return pd.read_excel(
                p, sheet_name=hoja, dtype=object, keep_default_na=False, na_values=[""]
            )
        except ImportError as e:
            if suf != ".xls":
                raise
            raise ImportError(
                f"Qué pasó: no se pudo leer {p.name}: el formato .xls antiguo necesita el paquete xlrd, que no viene instalado.\n"
                "Qué hacer: guarde el archivo como .xlsx (Excel → Guardar como) o instale xlrd."
            ) from e
    if suf == ".parquet":
        return pd.read_parquet(p)
    if suf in (".csv", ".txt", ".tsv"):
        return _leer_csv(p)
    raise ValueError(f"Formato no soportado: {p.suffix}")


def formatear_hs6(x: object) -> str:
    """Partida arancelaria a 6 dígitos: ``1905.9`` → ``'190590'``; ``901.11`` → ``'090111'``.

    Excel guarda la partida como número y le quita los ceros de los extremos. Los valores vacíos dan
    ``''`` (también ``None``, ``NaN``, ``pd.NA`` y los textos 'nan'/'inf'); un texto de 6 dígitos, que no es un
    número o que es negativo queda como llegó; un número de 10000 o más se toma como el entero de 5-6 dígitos
    ya completo.
    """
    if not isinstance(x, str) and pd.isna(x):
        return ""
    s = str(x).strip()
    if s == "":
        return ""
    if re.fullmatch(r"\d{6}", s):
        return s
    try:
        v = float(s.replace(",", "."))
    except ValueError:
        return s
    if not math.isfinite(v):
        return ""
    if v < 0:
        return s
    if v >= 10000:  # ya viene como entero de 5-6 dígitos
        return f"{round(v):06d}"
    return f"{v:.2f}".replace(".", "").zfill(6)


#: Transformaciones que una ``Fuente`` puede pedir por nombre (``transformaciones=(("HS6", "hs6"),)``).
TRANSFORMACIONES: dict[str, Callable[[object], str]] = {"hs6": formatear_hs6}


def _exigir_columnas(df: pd.DataFrame, f: Fuente) -> None:
    """Falla con la lista de las columnas declaradas por ``f`` (y sus ``claves_repeticion``) que no están en ``df``."""
    faltan = [c for c in f.columnas_declaradas() if c not in df.columns]
    claves = [c for c in f.claves_repeticion if c not in df.columns]
    if claves:
        raise KeyError(
            f"Qué pasó: a la fuente {f.clave} (hoja {f.hoja!r}) le faltan las columnas de claves_repeticion {claves}.\n"
            "Por qué importa: sin ellas la cifra «sin repetidos» deduplicaría solo por empresa y valor y subestimaría.\n"
            f"Qué hacer: corrija el nombre en la configuración. Columnas disponibles: {list(df.columns)}"
        )
    if faltan:
        raise KeyError(
            f"Qué pasó: a la fuente {f.clave} (hoja {f.hoja!r}) le faltan las columnas {faltan}.\n"
            f"Por qué importa: son las que declara su Fuente.\n"
            f"Qué hacer: corrija la configuración. Columnas disponibles: {list(df.columns)}"
        )


#: Columnas que el motor agrega al frente de cada fuente: la base no puede traerlas con ese nombre.
_COLUMNAS_RESERVADAS = ("FILA_EXCEL", "ID_REGISTRO", "FUENTE")


def _exigir_sin_reservadas(df: pd.DataFrame, f: Fuente) -> None:
    """Falla si la base trae una columna con un nombre que el motor usa para trazar las filas."""
    choques = [c for c in _COLUMNAS_RESERVADAS if c in df.columns]
    if choques:
        raise ValueError(
            f"Qué pasó: la base de {f.clave} trae columnas con nombres reservados del motor: {choques}.\n"
            "Por qué importa: el motor agrega FILA_EXCEL, ID_REGISTRO y FUENTE para trazar cada fila hasta su origen.\n"
            "Qué hacer: renombre esas columnas en el archivo."
        )


def _numerar_y_descartar_vacias(df: pd.DataFrame, f: Fuente) -> pd.DataFrame:
    """Pone ``FILA_EXCEL`` al frente y descarta las filas sin ningún dato en las columnas declaradas.

    ``FILA_EXCEL`` se asigna ANTES de descartar, así que conserva la fila real de la hoja (o la línea del
    CSV: ``_leer_csv`` no omite las líneas en blanco).
    """
    df = df.reset_index(drop=True).copy()
    df.insert(0, "FILA_EXCEL", np.arange(2, len(df) + 2))
    vacias = df[f.columnas_declaradas()].isna().all(axis=1)
    return df[~vacias].reset_index(drop=True)


def _aplicar_transformaciones(df: pd.DataFrame, f: Fuente) -> None:
    """Aplica en ``df`` (sin copiarlo) las ``transformaciones`` que pide la fuente."""
    for col, nombre in f.transformaciones:
        if nombre not in TRANSFORMACIONES:
            raise KeyError(
                f"Transformación desconocida {nombre!r}. Disponibles: {sorted(TRANSFORMACIONES)}"
            )
        if col not in df.columns:
            raise KeyError(
                f"Qué pasó: la transformación {nombre!r} de la fuente {f.clave} se aplica a la columna {col!r}, "
                "que no está en la base.\n"
                f"Qué hacer: corrija el nombre en la configuración. Columnas disponibles: {list(df.columns)}"
            )
        df[col] = df[col].map(TRANSFORMACIONES[nombre])


def _ids_nativos(df: pd.DataFrame, f: Fuente) -> np.ndarray:
    """Los identificadores de ``f.col_id``, sin espacios; fallan si hay vacíos o repetidos."""
    ids = df[f.col_id].astype("string").fillna("").str.strip()
    if (ids == "").any():
        filas = df.loc[ids == "", "FILA_EXCEL"].tolist()[:15]
        raise ValueError(
            f"Qué pasó: {int((ids == '').sum())} filas de {f.clave} sin '{f.col_id}' (filas {filas}).\n"
            "Qué hacer: suelen ser filas de continuación de campos multivalor; exporte una fila por empresa."
        )
    if ids.duplicated().any():
        raise ValueError(
            f"Qué pasó: '{f.col_id}' no es único en {f.clave} (p. ej. {ids[ids.duplicated()].head(3).tolist()}).\n"
            "Qué hacer: declare col_id=None para usar la fila como identificador."
        )
    return ids.to_numpy()


def _asignar_ids(df: pd.DataFrame, f: Fuente) -> None:
    """Inserta ``ID_REGISTRO`` (columna 1) en ``df``: el nativo, o ``<prefijo>-F<fila:04d>`` si no hay."""
    if f.col_id:
        df.insert(1, "ID_REGISTRO", _ids_nativos(df, f))
    else:
        # F = fila de Excel
        df.insert(1, "ID_REGISTRO", [f"{f.prefijo_id}-F{n:04d}" for n in df["FILA_EXCEL"]])


def leer_fuente(f: Fuente) -> pd.DataFrame:
    """Lee la hoja, verifica el contrato y asigna ``FILA_EXCEL``, ``ID_REGISTRO`` y ``FUENTE``.

    Los encabezados se recortan de espacios. La tabla devuelta no tiene filas completamente vacías
    (en las columnas declaradas) y trae al frente ``FILA_EXCEL``, ``ID_REGISTRO`` y ``FUENTE``.

    Raises:
        FileNotFoundError: el archivo no existe.
        ValueError: formato no soportado; la base trae una columna reservada (``FILA_EXCEL``, ``ID_REGISTRO``,
            ``FUENTE``); o ``col_id`` con vacíos o con repetidos.
        KeyError: faltan columnas declaradas, o una transformación desconocida o sobre una columna que no está.
    """
    df = leer_tabla(f.archivo, f.hoja).copy()
    df.columns = [str(c).strip() for c in df.columns]
    _exigir_columnas(df, f)
    _exigir_sin_reservadas(df, f)
    df = _numerar_y_descartar_vacias(df, f)
    _aplicar_transformaciones(df, f)
    _asignar_ids(df, f)
    df.insert(2, "FUENTE", f.clave)
    return df
