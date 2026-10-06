"""Muestreo estratificado para los reportes L6 — una regla, un solo sitio.

Hasta F1.3 el muestreo vivía tres veces (``suite``, ``reports``,
``visualizer``) y las tres copias tenían la misma familia de defecto:

* ``groupby("SRC", group_keys=False).apply(lambda x: x.sample(...))`` — con
  pandas 3 la columna de agrupación desaparece del marco y las filas con
  ``SRC`` NaN se descartan. El golden trae ``SRC`` NaN en el 99,9 % (F1.1), así
  que con más de 30.000 filas la muestra quedaba VACÍA.
* ``value_counts()`` + ``df[df[col] == estrato]`` en bucle — ``value_counts``
  omite NaN y la igualdad con NaN nunca acierta: el estrato NaN se perdía y,
  si era el único, ``pd.concat([])`` reventaba y un ``except`` devolvía una
  muestra aleatoria simple sin decirlo.

Aquí la regla es una y vectorizada: proporción por estrato, **piso de 1 fila
por estrato (NaN incluido)**, tope ``n`` sobre el total, todas las columnas del
insumo y semilla fija. No hay bucles de Python por fila ni por estrato.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..pipeline.errores import MuestreoReportesError, mensaje_accionable

SEMILLA_MUESTREO = 42
"""Semilla heredada de v1 (``random_state=42`` en las tres copias)."""


def muestra_estratificada(
    df: pd.DataFrame, columna: str, n: int, semilla: int = SEMILLA_MUESTREO
) -> pd.DataFrame:
    """Devuelve a lo sumo ``n`` filas de ``df`` manteniendo la proporción de ``columna``.

    Garantías:

    * si ``len(df) <= n`` se devuelve ``df`` tal cual (misma identidad: sin copia);
    * la muestra conserva TODAS las columnas y el índice original de las filas
      elegidas (trazabilidad), en el orden del insumo;
    * cada estrato de ``columna`` —NaN incluido— aporta al menos 1 fila;
    * ``len(muestra) <= n``; cuando el piso de 1 por estrato supera la cuota
      proporcional, el exceso lo pagan los estratos más grandes (nivelación);
    * si ``columna`` no existe, muestra aleatoria simple de ``n`` filas;
    * determinista para la misma ``semilla``.

    Falla (:class:`MuestreoReportesError`, un ``ErrorPipeline`` que los
    consumidores de L6 relanzan en vez de tragar) si ``n <= 0`` o si hay más
    estratos que ``n``: ahí piso y tope son incompatibles y se prefiere decirlo
    a devolver una muestra que no representa a todos los estratos.
    """
    if n <= 0:
        raise MuestreoReportesError(
            mensaje_accionable(
                que_paso=f"se pidió una muestra de n={n} filas",
                por_que_importa="una muestra de tamaño cero o negativo vacía los reportes",
                que_hacer="pase n >= 1 (la suite lo deriva de max_memory_mb; revise ese valor)",
            )
        )
    if len(df) <= n:
        return df
    if columna not in df.columns:
        return df.sample(n=n, random_state=semilla)

    # NaN recibe su propio código (use_na_sentinel=False): es un estrato más.
    codigos, _ = pd.factorize(df[columna], use_na_sentinel=False)
    tamanos = np.bincount(codigos)
    objetivo = _cuotas_por_estrato(tamanos, n, columna)

    # Rango aleatorio dentro de cada estrato sin bucles: ordenar por (estrato,
    # clave aleatoria) y quedarse con las primeras `objetivo[estrato]` filas.
    claves = np.random.default_rng(semilla).random(len(df))
    orden = np.lexsort((claves, codigos))
    codigos_ordenados = codigos[orden]
    inicio_estrato = np.concatenate(([0], np.cumsum(tamanos)[:-1]))
    posicion = np.arange(len(df)) - inicio_estrato[codigos_ordenados]
    elegidos = orden[posicion < objetivo[codigos_ordenados]]
    return df.iloc[np.sort(elegidos)]


def _cuotas_por_estrato(tamanos: np.ndarray, n: int, columna: str) -> np.ndarray:
    """Cuota de filas por estrato: proporcional, con piso 1 y suma ≤ ``n``."""
    k = len(tamanos)
    if k > n:
        raise MuestreoReportesError(
            mensaje_accionable(
                que_paso=(
                    f"la columna '{columna}' tiene {k:,} estratos (NaN incluido) y la "
                    f"muestra admite n={n:,} filas"
                ),
                por_que_importa=(
                    "con piso de 1 fila por estrato y tope n no hay forma de representar a "
                    "todos los estratos sin superar el tope"
                ),
                que_hacer=(
                    "suba n (en la suite sale de max_memory_mb) o estratifique por una "
                    "columna con menos valores distintos"
                ),
            )
        )
    total = int(tamanos.sum())
    objetivo = np.minimum(tamanos, np.maximum(1, (n * tamanos) // total))
    if objetivo.sum() <= n:
        return objetivo

    # El piso empujó la suma por encima de n: nivelar por arriba. Buscamos el
    # mayor nivel L tal que sum(min(objetivo, L)) <= n; con L=1 la suma es k <= n.
    bajo, alto = 1, int(objetivo.max())
    while bajo < alto:
        medio = (bajo + alto + 1) // 2
        if np.minimum(objetivo, medio).sum() <= n:
            bajo = medio
        else:
            alto = medio - 1
    return np.minimum(objetivo, bajo)
