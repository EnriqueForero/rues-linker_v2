"""record_linkage.golden.tipos — Los tipos que el golden promete (F1.14).

Qué garantiza
-------------
Que las métricas del golden salgan con el tipo del estándar de salida:

    SOURCES_COUNT, RECORD_COUNT, NAME_VARIATIONS, NIT_VARIATIONS   int64
    REQUIRES_REVIEW                                                 bool

Por qué existe
--------------
El golden se construye en SQLite, que no tiene booleano: ``REQUIRES_REVIEW``
llegaba al parquet publicado como ``0/1`` entero, y cualquier nulo que entrara
después (dos filas huérfanas de la consolidación por NIT, medidas en el banco
de 30.486) convertía los cuatro conteos en ``float64``. Un consumidor que
filtra ``REQUIRES_REVIEW == True`` o suma ``RECORD_COUNT`` no debería tener
que adivinar el tipo según la corrida.

La regla se escribe una vez aquí y la llaman los dos puntos por donde sale un
golden: el generador (``_load_results_from_db``) y el orquestador, justo antes
de persistir ``golden.parquet``.

Política
--------
No se repara en silencio. Un conteo con nulos no se rellena: en modo estricto
es :class:`GoldenSinTiparError` con mensaje accionable; en modo tolerante la
columna queda como venía y se registra una advertencia que la nombra.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..pipeline.errores import GoldenSinTiparError, mensaje_accionable

logger = logging.getLogger(__name__)

__all__ = ["TIPOS_METRICAS_GOLDEN", "GoldenSinTiparError", "tipar_golden"]

#: Tipo del contrato por columna de métrica del golden.
TIPOS_METRICAS_GOLDEN: dict[str, str] = {
    "SOURCES_COUNT": "int64",
    "RECORD_COUNT": "int64",
    "NAME_VARIATIONS": "int64",
    "NIT_VARIATIONS": "int64",
    "REQUIRES_REVIEW": "bool",
}


def _motivo_sin_tipar(serie: pd.Series, tipo: str) -> str | None:
    """Por qué una columna NO se puede llevar al tipo del contrato, o None."""
    nulos = int(serie.isna().sum())
    if nulos:
        return f"{nulos} nulo(s)"
    if tipo == "bool":
        if serie.dtype == np.dtype("bool"):
            return None
        valores = pd.to_numeric(serie, errors="coerce")
        if valores.isna().any() or not valores.isin([0, 1]).all():
            ajenos = sorted(serie[~valores.isin([0, 1])].astype(str).unique())[:5]
            return f"valores fuera de 0/1: {ajenos}"
        return None
    valores = pd.to_numeric(serie, errors="coerce")
    if valores.isna().any():
        ajenos = sorted(serie[valores.isna()].astype(str).unique())[:5]
        return f"valores no numéricos: {ajenos}"
    if not np.array_equal(
        valores.to_numpy(dtype=np.float64), np.floor(valores.to_numpy(dtype=np.float64))
    ):
        return "valores con parte decimal"
    return None


def tipar_golden(
    golden: pd.DataFrame,
    *,
    estricto: bool = True,
    registrador: logging.Logger | None = None,
) -> pd.DataFrame:
    """Devuelve el golden con las métricas en el tipo del contrato.

    Args:
        golden: una fila por entidad. Las columnas de
            :data:`TIPOS_METRICAS_GOLDEN` que no estén se ignoran; las demás
            columnas no se tocan.
        estricto: True → una columna que no admite su tipo levanta
            :class:`GoldenSinTiparError`. False → se deja como venía y se
            registra una advertencia con el motivo (es lo que hace el
            orquestador tras la consolidación por NIT, cuya fuente de nulos
            cierra F1.1).
        registrador: logger opcional para la advertencia.

    Returns:
        Copia del golden con los tipos aplicados (o el mismo objeto si no
        había nada que convertir).

    Raises:
        GoldenSinTiparError: en modo estricto, si una métrica trae nulos,
            valores no numéricos, decimales, o un ``REQUIRES_REVIEW`` que no
            es 0/1.
    """
    log = registrador or logger
    pendientes = {
        columna: tipo
        for columna, tipo in TIPOS_METRICAS_GOLDEN.items()
        if columna in golden.columns and golden[columna].dtype != np.dtype(tipo)
    }
    if not pendientes:
        return golden

    motivos = {
        columna: motivo
        for columna, tipo in pendientes.items()
        if (motivo := _motivo_sin_tipar(golden[columna], tipo)) is not None
    }
    if motivos:
        detalle = "; ".join(f"{c}: {m}" for c, m in motivos.items())
        if estricto:
            raise GoldenSinTiparError(
                mensaje_accionable(
                    f"el golden trae métricas que no admiten su tipo ({detalle}).",
                    "el estándar de salida promete conteos int64 y REQUIRES_REVIEW "
                    "booleano; rellenar un nulo sería inventar un dato del golden.",
                    "revise el registro de la fase L5: las métricas se calculan en "
                    "golden.generator._process_batch_vectorized y _add_quality_metrics; "
                    "un nulo ahí es un grupo sin filas en la correlativa.",
                )
            )
        log.warning(
            "⚠️ Golden sin tipar en %s. Se dejan como vienen (no se inventa un "
            "valor); el estándar promete conteos int64 y REQUIRES_REVIEW booleano.",
            detalle,
        )

    convertibles = {c: t for c, t in pendientes.items() if c not in motivos}
    if not convertibles:
        return golden

    salida = golden.copy()
    for columna, tipo in convertibles.items():
        numerica = pd.to_numeric(salida[columna], errors="raise")
        salida[columna] = numerica.astype("int64") if tipo == "int64" else numerica.astype("bool")
    return salida
