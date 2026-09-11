"""Aplicación de la política de fuentes al emitir pares (v0.17.4).

Por qué existe este módulo
--------------------------
Un bloque de candidatos —un bucket LSH, un grupo de NIT idéntico, un bucket de
vecindad— produce pares. **Cuáles de esos pares valen** lo decide la política de
fuentes: en un cruce con una fuente confiable (RUES) que no se deduplica contra
sí misma, los pares internos de esa fuente no existen.

Hasta 0.17.3 esa regla estaba escrita **dos veces**: una en el generador de
buckets LSH (corregida en 0.17.3) y otra en el bloqueo por NIT (no corregida).
La segunda copia enumeraba TODOS los pares en un ``set`` de Python y los
filtraba después: sobre 4,37 M de registros eso agotó los 12,7 GB de Colab. Una
regla escrita dos veces se corrige una vez y sigue rota; por eso vive aquí, y
los dos caminos la llaman.

Principio de diseño
-------------------
Nunca materializar un par que la política va a descartar. El costo debe ser
proporcional a los pares EMITIDOS, no a los pares posibles.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np

__all__ = [
    "bloques_utiles",
    "indices_de_grupos",
    "pares_permitidos",
    "pares_por_bloque",
]


def pares_permitidos(
    ids: np.ndarray,
    codigos_fuente: np.ndarray | None,
    politica_fuentes: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray]:
    """Pares (menor, mayor) permitidos dentro de UN bloque.

    El bloque se parte por fuente y solo se materializan las combinaciones que
    la política admite: el producto cruzado entre fuentes distintas y, cuando
    corresponde, los pares internos de cada fuente.

    Args:
        ids: posiciones de los registros del bloque.
        codigos_fuente: código de fuente de cada elemento de ``ids`` (mismo
            orden y longitud). ``None`` significa "sin política".
        politica_fuentes: matriz k×k booleana; ``[i, j]`` indica si se permite
            un par entre la fuente i y la j.

    Returns:
        Dos arreglos ``(lo, hi)`` de igual longitud con los pares emitidos.
    """
    n = len(ids)
    if n < 2:
        vacio = np.empty(0, dtype=np.int64)
        return vacio, vacio

    ids = np.asarray(ids, dtype=np.int64)
    if codigos_fuente is None or politica_fuentes is None:
        ii, jj = np.triu_indices(n, k=1)
        return np.minimum(ids[ii], ids[jj]), np.maximum(ids[ii], ids[jj])

    codigos = np.asarray(codigos_fuente)
    orden = np.argsort(codigos, kind="stable")
    ids_ordenados = ids[orden]
    codigos_ordenados = codigos[orden]
    cortes = np.flatnonzero(np.diff(codigos_ordenados)) + 1
    grupos = np.split(ids_ordenados, cortes)
    codigos_grupo = codigos_ordenados[np.concatenate(([0], cortes))]

    bloques_lo: list[np.ndarray] = []
    bloques_hi: list[np.ndarray] = []
    for a in range(len(grupos)):
        for b in range(a, len(grupos)):
            if not politica_fuentes[codigos_grupo[a], codigos_grupo[b]]:
                continue
            if a == b:
                if len(grupos[a]) < 2:
                    continue
                ii, jj = np.triu_indices(len(grupos[a]), k=1)
                izq, der = grupos[a][ii], grupos[a][jj]
            else:
                izq = np.repeat(grupos[a], len(grupos[b]))
                der = np.tile(grupos[b], len(grupos[a]))
            bloques_lo.append(np.minimum(izq, der))
            bloques_hi.append(np.maximum(izq, der))

    if not bloques_lo:
        vacio = np.empty(0, dtype=np.int64)
        return vacio, vacio
    return np.concatenate(bloques_lo), np.concatenate(bloques_hi)


def indices_de_grupos(inicio: np.ndarray, tam: np.ndarray) -> np.ndarray:
    """Índices de todos los miembros de varios grupos contiguos, sin bucle.

    Dado ``inicio=[3, 10]`` y ``tam=[2, 3]`` devuelve ``[3, 4, 10, 11, 12]``.
    Es el truco estándar de "ranges concatenados" con una sola ``cumsum``:
    sirve para no recorrer en Python millones de grupos diminutos.
    """
    total = int(tam.sum())
    if total == 0:
        return np.empty(0, dtype=np.int64)
    pasos = np.ones(total, dtype=np.int64)
    pasos[0] = inicio[0]
    if len(inicio) > 1:
        saltos = np.cumsum(tam)[:-1]
        pasos[saltos] = inicio[1:] - (inicio[:-1] + tam[:-1]) + 1
    return np.cumsum(pasos)


def bloques_utiles(
    codigos_bloque: np.ndarray,
    fuentes: np.ndarray,
    politica_fuentes: np.ndarray,
    *,
    tam_maximo: int,
    tam_minimo: int = 2,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Localiza los bloques que PUEDEN producir al menos un par permitido.

    Éste es el filtro que hace viable el bloqueo a escala. Con una fuente
    confiable que ocupa el 99,6 % de los registros, la inmensa mayoría de los
    bloques son puramente internos de esa fuente y no producen ningún par: si
    se descubre eso *después* de enumerarlos, ya se pagó la memoria. Aquí se
    descarta antes, con un conteo por fuente vectorizado.

    Args:
        codigos_bloque: clave de bloque de cada registro (enteros).
        fuentes: código de fuente de cada registro, mismo orden.
        politica_fuentes: matriz k×k de combinaciones permitidas.
        tam_maximo: bloques con más miembros se descartan (cota dura contra la
            explosión cuadrática).
        tam_minimo: bloques con menos miembros no producen pares.

    Returns:
        ``(orden, inicio, tam)``: el orden que agrupa los registros por bloque,
        y el inicio y tamaño —dentro de ese orden— de cada bloque útil.
    """
    orden = np.argsort(codigos_bloque, kind="stable")
    ordenados = codigos_bloque[orden]
    if ordenados.size == 0:
        vacio = np.empty(0, dtype=np.int64)
        return orden, vacio, vacio
    cortes = np.flatnonzero(np.concatenate(([True], ordenados[1:] != ordenados[:-1], [True])))
    inicio, tam = cortes[:-1], np.diff(cortes)
    vale = (tam >= tam_minimo) & (tam <= tam_maximo)
    inicio, tam = inicio[vale], tam[vale]
    if inicio.size == 0:
        vacio = np.empty(0, dtype=np.int64)
        return orden, vacio, vacio

    k = int(politica_fuentes.shape[0])
    posiciones = indices_de_grupos(inicio, tam)
    grupo_de = np.repeat(np.arange(inicio.size), tam)
    fuente_de = fuentes[orden][posiciones]
    conteo = np.zeros((inicio.size, k), dtype=np.int32)
    np.add.at(conteo, (grupo_de, fuente_de), 1)

    util = np.zeros(inicio.size, dtype=bool)
    for a in range(k):
        if politica_fuentes[a, a]:
            util |= conteo[:, a] >= 2
        for b in range(a + 1, k):
            if politica_fuentes[a, b]:
                util |= (conteo[:, a] > 0) & (conteo[:, b] > 0)
    return orden, inicio[util], tam[util]


def pares_por_bloque(
    codigos_bloque: np.ndarray,
    posiciones: np.ndarray,
    fuentes: np.ndarray,
    politica_fuentes: np.ndarray,
    *,
    tam_maximo: int,
    tam_lote: int = 2_000_000,
) -> Iterator[np.ndarray]:
    """Emite, por lotes, los pares permitidos de todos los bloques.

    Nunca acumula el total: rinde arreglos ``(m, 2)`` de a lo sumo ``tam_lote``
    pares, de modo que el consumidor los escriba a disco y los suelte. Sobre
    4,37 M de registros la versión anterior acumulaba un ``set`` de Python de
    decenas de millones de tuplas —entre 6 y 10 GB medidos— antes de escribir
    el primero.

    Args:
        codigos_bloque: clave de bloque por registro.
        posiciones: identificador global (posición en el DataFrame) por registro.
        fuentes: código de fuente por registro.
        politica_fuentes: matriz k×k de combinaciones permitidas.
        tam_maximo: cota dura de tamaño de bloque.
        tam_lote: pares por lote rendido.

    Yields:
        Arreglos ``(m, 2)`` int64 con columnas ``(menor, mayor)``.
    """
    orden, inicio, tam = bloques_utiles(
        codigos_bloque, fuentes, politica_fuentes, tam_maximo=tam_maximo
    )
    if inicio.size == 0:
        return
    posiciones_ordenadas = posiciones[orden]
    fuentes_ordenadas = fuentes[orden]

    acumulado: list[np.ndarray] = []
    acumulados = 0
    for ini, largo in zip(inicio.tolist(), tam.tolist(), strict=True):
        rebanada = slice(ini, ini + largo)
        lo, hi = pares_permitidos(
            posiciones_ordenadas[rebanada], fuentes_ordenadas[rebanada], politica_fuentes
        )
        if lo.size == 0:
            continue
        acumulado.append(np.column_stack((lo, hi)))
        acumulados += lo.size
        if acumulados >= tam_lote:
            yield np.concatenate(acumulado)
            acumulado, acumulados = [], 0
    if acumulado:
        yield np.concatenate(acumulado)
