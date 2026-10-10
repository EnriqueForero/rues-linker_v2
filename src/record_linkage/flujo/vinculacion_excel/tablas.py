"""Volcado de un ``DataFrame`` en una hoja: celdas, formato de columnas, filtro y colores condicionales."""

from __future__ import annotations

import re
from collections.abc import Sequence

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from record_linkage.flujo.vinculacion_excel.estilos import (
    ALINEACION_ENCABEZADO,
    COLORES_PESTANA,
    Estilos,
    Pestana,
)

__all__ = ["colorear_columna", "escribir_tabla", "formatear_tabla", "hoja_desde_tabla"]

#: Caracteres de control que Excel no admite (se conservan el tabulador, el salto de línea y el retorno).
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
#: Excel limita el nombre de una hoja a 31 caracteres.
_LARGO_MAXIMO_NOMBRE_HOJA = 31


def _valor_de_celda(valor: object) -> object:
    """Lo que se escribe en la celda: nativos de Python, ``SÍ``/``NO`` y texto sin caracteres de control."""
    if isinstance(valor, np.integer):
        return int(valor)
    if isinstance(valor, np.floating):
        return None if np.isnan(valor) else float(valor)
    if isinstance(valor, (np.bool_, bool)):
        return "SÍ" if bool(valor) else "NO"
    if isinstance(valor, str):
        return _CONTROL.sub(" ", valor)
    return valor


def escribir_tabla(hoja: Worksheet, tabla: pd.DataFrame, fila0: int = 1, col0: int = 1) -> None:
    """Escribe ``tabla`` desde la celda (``fila0``, ``col0``): el encabezado y luego una fila por registro.

    Los tipos de numpy pasan a nativos, un ausente queda como celda vacía, un booleano se escribe
    ``SÍ``/``NO``, los caracteres de control se cambian por un espacio y un texto que empieza con ``=``
    se marca como texto (no es una fórmula).
    """
    # Hallazgo F3: el encabezado no se limpia: un nombre con «=» inicial sería fórmula y uno con control, un error.
    for j, c in enumerate(tabla.columns):
        hoja.cell(row=fila0, column=col0 + j, value=str(c))
    valores = tabla.astype(object).where(tabla.notna(), None).to_numpy()
    for i in range(len(tabla)):
        for j in range(len(tabla.columns)):
            valor = _valor_de_celda(valores[i, j])
            celda = hoja.cell(row=fila0 + 1 + i, column=col0 + j, value=valor)
            if isinstance(valor, str) and valor.startswith("="):
                celda.data_type = "s"  # texto que empieza con '=' no es una fórmula


def _largos_de_columnas(tabla: pd.DataFrame) -> list[int]:
    """Largo de cada columna: el mayor entre su nombre y el percentil 90 de sus primeros 300 valores."""
    largos = [len(str(c)) for c in tabla.columns]
    muestra = tabla.head(300).astype(str)
    for j, c in enumerate(tabla.columns):
        if len(muestra):
            # Hallazgo F3: el `else 0` es inalcanzable. Con pandas 3 `astype(str)` deja los ausentes en NaN: una
            # columna sin ningún valor en esas 300 filas da `int(NaN)` (ValueError) y el ancho cambia con la versión.
            largos[j] = max(
                largos[j], int(muestra[c].str.len().quantile(0.9)) if len(muestra) else 0
            )
    return largos


def _formato_numerico(nombre: str, serie: pd.Series) -> str | None:
    """Formato de número de una columna numérica según su nombre (``None`` si no es numérica)."""
    es_numerica = pd.api.types.is_numeric_dtype(serie) and not pd.api.types.is_bool_dtype(serie)
    if not es_numerica:
        return None
    if (
        nombre.startswith("PCT")
        or nombre in ("PRECISION", "RECALL", "F1")
        or nombre.startswith("RECALL_")
    ):
        return "0.0%"
    if any(k in nombre for k in ("SCORE", "SIM_", "JARO")):
        return "0.00"
    if "ANIO" in nombre or nombre.startswith("FILA") or nombre.endswith("_ID"):
        return "0"
    if serie.dropna().apply(lambda v: float(v).is_integer()).all():
        return "#,##0"
    return "#,##0.00"


def formatear_tabla(
    hoja: Worksheet,
    tabla: pd.DataFrame,
    estilos: Estilos,
    *,
    fila0: int = 1,
    anchos_max: int = 55,
    congelar: str | None = "A2",
    filtro: bool = True,
    envolver: bool = True,
) -> None:
    """Da formato a una tabla ya escrita desde ``fila0``: encabezado, anchos, números, panel y filtro.

    ``anchos_max`` topa el ancho de las columnas; ``envolver`` parte en líneas el texto de las que
    llegan al tope; ``congelar`` es la celda donde se inmovilizan los paneles (``None`` = no se
    inmoviliza); ``filtro`` pone el autofiltro sobre el encabezado.
    """
    ncol = len(tabla.columns)
    for j in range(1, ncol + 1):
        celda = hoja.cell(row=fila0, column=j)
        celda.font, celda.fill = estilos.encabezado, estilos.relleno_encabezado
        celda.alignment = ALINEACION_ENCABEZADO
    hoja.row_dimensions[fila0].height = 30
    largos = _largos_de_columnas(tabla)
    for j, c in enumerate(tabla.columns, 1):
        ancho = min(max(8, largos[j - 1] + 2), anchos_max)
        hoja.column_dimensions[get_column_letter(j)].width = ancho
        formato = _formato_numerico(str(c).upper(), tabla[c])
        alineacion = Alignment(wrap_text=envolver and ancho >= anchos_max, vertical="top")
        for i in range(fila0 + 1, fila0 + 1 + len(tabla)):
            celda = hoja.cell(row=i, column=j)
            celda.font = estilos.base
            if formato:
                celda.number_format = formato
            celda.alignment = alineacion
    if congelar:
        hoja.freeze_panes = congelar
    if filtro and len(tabla.columns):
        hoja.auto_filter.ref = f"A{fila0}:{get_column_letter(ncol)}{fila0 + max(1, len(tabla))}"


def colorear_columna(
    hoja: Worksheet,
    tabla: pd.DataFrame,
    columna: str,
    reglas: Sequence[tuple[str, PatternFill]],
    *,
    fila0: int = 1,
) -> None:
    """Pinta las celdas de ``columna`` cuyo texto EMPIEZA con cada ``texto`` de ``reglas`` (formato condicional).

    No hace nada si la tabla no tiene esa columna o no tiene filas.
    """
    if columna not in tabla.columns or not len(tabla):
        return
    letra = get_column_letter(list(tabla.columns).index(columna) + 1)
    rango = f"{letra}{fila0 + 1}:{letra}{fila0 + len(tabla)}"
    for texto, relleno in reglas:
        regla = FormulaRule(
            formula=[f'LEFT(${letra}{fila0 + 1},{len(texto)})="{texto}"'], fill=relleno
        )
        hoja.conditional_formatting.add(rango, regla)


def hoja_desde_tabla(
    libro: Workbook,
    estilos: Estilos,
    nombre: str,
    tabla: pd.DataFrame | None,
    pestana: Pestana,
    *,
    congelar: str | None = "A2",
    anchos_max: int = 55,
    envolver: bool = True,
) -> Worksheet:
    """Crea la hoja ``nombre`` (recortada a 31 caracteres) con ``tabla`` escrita y formateada.

    Una tabla ausente o sin columnas deja la hoja con la sola celda ``(sin datos)``.
    """
    # Hallazgo F3: dos nombres iguales en sus 31 primeros caracteres: openpyxl renombra el segundo a 32 (con aviso).
    hoja = libro.create_sheet(nombre[:_LARGO_MAXIMO_NOMBRE_HOJA])
    hoja.sheet_properties.tabColor = COLORES_PESTANA[pestana]
    if tabla is None or not len(tabla.columns):
        hoja["A1"] = "(sin datos)"
        hoja["A1"].font = estilos.base
        return hoja
    escribir_tabla(hoja, tabla)
    formatear_tabla(
        hoja, tabla, estilos, anchos_max=anchos_max, congelar=congelar, envolver=envolver
    )
    return hoja
