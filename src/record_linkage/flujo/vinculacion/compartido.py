"""Piezas compartidas por las hojas de salida: unir valores en una celda y nombrar columnas.

Son las únicas funciones que usan a la vez varias hojas (EXPORTADORES y DETALLE_<destino>), así que viven
aquí y no en ninguna de ellas: ninguna hoja importa a otra y el orquestador (``tablas``) las re-exporta.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from record_linkage.flujo.vinculacion.config import Fuente

__all__ = ["nombre_columna_ancla", "prefijo_destino", "unir_valores"]


def unir_valores(valores: Iterable[object], maximo: int = 12, sep: str = " | ") -> str:
    """Une en una sola celda los valores distintos y no vacíos, en el orden en que llegan.

    Descarta ``None``, ``NaN``, los textos ``'nan'`` y ``'none'`` (sin importar mayúsculas) y los vacíos;
    cada valor se recorta de espacios y se repite una sola vez. Si hay más de ``maximo`` valores deja los
    primeros ``maximo`` y agrega ``… (+N)`` con cuántos quedaron fuera.
    """
    # Hallazgo F3: ``pd.NA`` y ``pd.NaT`` no se tratan como vacíos (``str(pd.NA)`` es '<NA>' y entra a la celda);
    # solo ``None`` y el ``NaN`` de coma flotante de Python se descartan.
    vistos: set[str] = set()
    salida: list[str] = []
    for v in valores:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        s = str(v).strip()
        if s and s.lower() not in {"nan", "none"} and s not in vistos:
            vistos.add(s)
            salida.append(s)
    if len(salida) > maximo:
        return sep.join(salida[:maximo]) + f"{sep}… (+{len(salida) - maximo})"
    return sep.join(salida)


def nombre_columna_ancla(ancla: Fuente, columna: str) -> str:
    """Nombre con que una métrica o columna de contexto de la ancla sale en las hojas.

    Es el nombre legible de ``ancla.nombres_contexto`` (o la columna misma) en MAYÚSCULAS y con guiones
    bajos en lugar de espacios: ``'Valor U$S'`` con el renombre ``'VALOR_USD'`` da ``'VALOR_USD'``.
    """
    return ancla.nombres_contexto.get(columna, columna).upper().replace(" ", "_")


def prefijo_destino(destino: Fuente) -> str:
    """Prefijo de las columnas de un destino en EXPORTADORES: la primera palabra de su etiqueta en MAYÚSCULAS.

    ``'FDI MARKETS'`` da ``'FDI'`` (columnas ``FDI_NIVEL``, ``FDI_NOMBRE``…).
    """
    # Hallazgo F3: dos destinos cuyas etiquetas empiezan por la misma palabra ('FDI MARKETS' y 'FDI NORTE')
    # comparten prefijo y el segundo bloque PISA al primero en EXPORTADORES (sin aviso); una etiqueta en
    # blanco revienta con IndexError, ya terminado todo el cruce.
    return destino.etiqueta.split()[0].upper()
