"""Reportes de composición legibles para el operador (v0.14.0)."""

from __future__ import annotations

import pandas as pd

__all__ = ["reportar_composicion", "reportar_cruce_por_fuente"]

_ANCHO = 62


def reportar_composicion(
    filas_entrada: int,
    correlativa: pd.DataFrame,
    *,
    columna_grupo: str = "ID_GRUPO",
    columna_fuente: str = "SRC",
) -> str:
    """Devuelve el reporte visual de composición de una corrida.

    Muestra qué pasó con cada registro, no solo el total: cuántas entidades
    quedaron, cuánto se redujo y cómo se reparten las fuentes. Devolver texto
    (en vez de imprimirlo) permite probarlo y también guardarlo en el log.

    Args:
        filas_entrada: filas que entraron al proceso.
        correlativa: tabla correlativa resultante.
        columna_grupo: columna con el identificador de entidad.
        columna_fuente: columna con el nombre de la fuente.

    Returns:
        Bloque de texto listo para imprimir.
    """
    lineas = ["=" * _ANCHO, "📊 REPORTE DE COMPOSICIÓN", "=" * _ANCHO]
    salida = len(correlativa)
    grupos = int(correlativa[columna_grupo].nunique()) if salida else 0
    lineas.append(f"  Entrada        : {filas_entrada:>12,}")
    lineas.append(f"  Correlativa    : {salida:>12,}  Δ {salida - filas_entrada:>+,}")
    lineas.append(f"  Entidades      : {grupos:>12,}")
    if filas_entrada:
        reduccion = 1 - grupos / filas_entrada
        lineas.append(f"  Reducción      : {reduccion:>11.2%}")
    if columna_fuente in correlativa.columns and salida:
        lineas.append("-" * _ANCHO)
        for fuente, n in correlativa[columna_fuente].value_counts().items():
            barra = "█" * int(n / salida * 20)
            lineas.append(f"  {fuente!s:<18} {n:>10,}  {n / salida:>6.1%}  {barra}")
    lineas.append("=" * _ANCHO)
    return "\n".join(lineas)


def reportar_cruce_por_fuente(
    correlativa: pd.DataFrame,
    *,
    columna_grupo: str = "ID_GRUPO",
    columna_fuente: str = "SRC",
) -> pd.DataFrame:
    """Cuántos registros de cada fuente quedaron enlazados con otra fuente.

    Es la pregunta de negocio del record linkage multi-fuente ("¿cuántos de mis
    exportadores están en el RUES?"), y conviene responderla con una tabla
    reutilizable en vez de con un print.

    Args:
        correlativa: tabla correlativa resultante.
        columna_grupo: columna con el identificador de entidad.
        columna_fuente: columna con el nombre de la fuente.

    Returns:
        DataFrame con una fila por fuente: registros, enlazados y porcentaje.

    Raises:
        KeyError: si falta alguna de las columnas requeridas.
    """
    for requerida in (columna_grupo, columna_fuente):
        if requerida not in correlativa.columns:
            raise KeyError(f"reportar_cruce_por_fuente requiere la columna '{requerida}'.")
    fuentes_por_grupo = correlativa.groupby(columna_grupo)[columna_fuente].transform("nunique")
    enlazado = fuentes_por_grupo > 1
    tabla = (
        correlativa.assign(_enlazado=enlazado)
        .groupby(columna_fuente)["_enlazado"]
        .agg(registros="size", enlazados="sum")
        .reset_index()
    )
    tabla["pct_enlazado"] = tabla["enlazados"] / tabla["registros"]
    return tabla.sort_values("registros", ascending=False).reset_index(drop=True)
