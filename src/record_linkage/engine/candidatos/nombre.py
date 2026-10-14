"""engine.candidatos.nombre — candidatos por NOMBRE: tokens distintivos y TF-IDF de trigramas (Fase 2, 2026-10-13).

Dos estrategias que se complementan y escalan de forma distinta:

* ``pares_por_token_distintivo``: cada registro entra en la cubeta de sus ``k`` tokens más raros (mayor IDF) que no sean
  genéricos; con ``con_borrados`` entra también en las cubetas de las variantes con una letra borrada (SymSpell), así que dos
  tokens a una errata de distancia caen juntos. Costo lineal en registros × tokens; cubetas acotadas por ``max_bucket``.
  Es la estrategia para millones de registros.
* ``pares_por_nombre_tfidf``: coseno entre vectores TF-IDF de trigramas de caracteres, por lotes de filas contra todo el
  corpus, con umbral y top-k por fila. Recupera lo que los tokens no ven (erratas múltiples, palabras pegadas o partidas). En
  el banco de 30k recupera el 94 % de los pares sin identificador con 17 candidatos por registro (el LSH actual: 72 % con
  32). Costo superlineal: se limita a ``max_filas`` y se puede partir la base antes.

Las dos rinden lotes ``(m, 2)`` int64 de pares ``(menor, mayor)`` sobre POSICIONES del DataFrame, ya filtrados por la
política de fuentes (``politica_fuentes``: matriz k×k; ``codigos_fuente``: código por registro), para que el consumidor los
escriba a disco y los suelte: nunca se acumula el total en memoria (regla de Colab).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import numpy as np
import pandas as pd

from record_linkage.engine.lsh.politica_pares import pares_por_bloque
from record_linkage.matching.idf import construir_idf

__all__ = ["pares_por_nombre_tfidf", "pares_por_token_distintivo", "variantes_por_borrado"]

logger = logging.getLogger(__name__)


def _politica_trivial(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Sin política de fuentes: una sola fuente y todo permitido."""
    return np.zeros(n, dtype=np.int16), np.ones((1, 1), dtype=bool)


def variantes_por_borrado(tokens: pd.Series, *, largo_minimo: int = 5) -> pd.DataFrame:
    """Variantes con UNA letra borrada de cada token (vectorizado por posición), más el token mismo.

    Devuelve un DataFrame con ``fila`` (posición en ``tokens``) y ``variante``. Un token de largo < ``largo_minimo``
    solo aporta su forma exacta (borrarle una letra lo haría demasiado ambiguo).
    """
    t = tokens.astype(str)
    filas = [np.arange(len(t))]
    variantes = [t.to_numpy()]
    largos = t.str.len().to_numpy()
    elegibles = largos >= largo_minimo
    if elegibles.any():
        sub = t[elegibles]
        posiciones = np.flatnonzero(elegibles)
        for p in range(int(largos[elegibles].max())):
            con_letra = sub.str.len() > p
            if not con_letra.any():
                break
            v = sub[con_letra]
            variantes.append((v.str[:p] + v.str[p + 1 :]).to_numpy())
            filas.append(posiciones[con_letra.to_numpy()])
    return pd.DataFrame({"fila": np.concatenate(filas), "variante": np.concatenate(variantes)})


def _tokens_elegidos(
    nombres: pd.Series,
    *,
    k_tokens: int,
    max_bucket: int,
    min_len: int,
    genericos: frozenset[str],
) -> tuple[np.ndarray, np.ndarray]:
    """``(fila, token)`` de los ``k_tokens`` más raros de cada nombre que no sean genéricos ni masivos."""
    idf = construir_idf(nombres, longitud_minima=min_len)
    if idf.vacio:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=object)
    incidencia: Any = idf.incidencia  # scipy.sparse.csr_matrix (tipado como object en PesosIDF)
    inc = incidencia.tocoo()
    fila, col = inc.row.astype(np.int64), inc.col.astype(np.int64)
    vocab = np.array(list(idf.vocabulario.keys()), dtype=object)
    frecuencia = np.asarray(incidencia.sum(axis=0)).ravel()
    es_generico = np.array([t in genericos for t in vocab], dtype=bool)
    # Un token único también entra: suele ser la errata (o la marca rara) y sus variantes por borrado son las
    # que lo juntan con su forma correcta. Las cubetas de un solo miembro se descartan después, sin costo.
    elegible = (frecuencia[col] <= max_bucket) & ~es_generico[col]
    fila, col = fila[elegible], col[elegible]
    if fila.size == 0:
        return fila, np.empty(0, dtype=object)
    orden = np.lexsort(
        (-idf.pesos[col], fila)
    )  # por fila, y dentro de la fila el IDF más alto primero
    fila, col = fila[orden], col[orden]
    inicio = np.flatnonzero(np.r_[True, fila[1:] != fila[:-1]])
    tam = np.diff(np.r_[inicio, fila.size])
    rango = np.arange(fila.size) - np.repeat(inicio, tam)
    quedan = rango < k_tokens
    return fila[quedan], vocab[col[quedan]]


def pares_por_token_distintivo(
    nombres: pd.Series,
    *,
    posiciones: np.ndarray | None = None,
    codigos_fuente: np.ndarray | None = None,
    politica_fuentes: np.ndarray | None = None,
    k_tokens: int = 2,
    max_bucket: int = 300,
    min_len: int = 4,
    genericos: frozenset[str] = frozenset(),
    con_borrados: bool = True,
    largo_minimo_borrado: int = 5,
    tam_lote: int = 2_000_000,
    registrador: logging.Logger | None = None,
) -> Iterator[np.ndarray]:
    """Pares que comparten uno de sus ``k_tokens`` tokens más distintivos, con tolerancia de una errata.

    Args:
        nombres: razones sociales limpias (una por registro, en el orden del DataFrame).
        posiciones: posición global de cada registro (por defecto ``0..n-1``).
        codigos_fuente, politica_fuentes: política de fuentes (``None`` = todo permitido).
        k_tokens: cuántos tokens raros por registro (2 basta: el nombre y el apellido de la marca).
        max_bucket: un token presente en más registros que esto no bloquea (es genérico de facto).
        min_len: tokens más cortos no cuentan.
        genericos: tokens declarados que nunca identifican (``matching.genericos``).
        con_borrados: indexar también las variantes con una letra borrada (erratas).
        largo_minimo_borrado: largo mínimo del token para generar variantes.
        tam_lote: pares por lote rendido.

    Yields:
        Arreglos ``(m, 2)`` int64 ``(menor, mayor)``.
    """
    log = registrador or logger
    n = len(nombres)
    if posiciones is None:
        posiciones = np.arange(n, dtype=np.int64)
    if codigos_fuente is None or politica_fuentes is None:
        codigos_fuente, politica_fuentes = _politica_trivial(n)
    fila, token = _tokens_elegidos(
        pd.Series(nombres).reset_index(drop=True),
        k_tokens=k_tokens,
        max_bucket=max_bucket,
        min_len=min_len,
        genericos=genericos,
    )
    if fila.size == 0:
        log.info("[tokens] ningún token distintivo: sin pares")
        return
    if con_borrados:
        var = variantes_por_borrado(pd.Series(token), largo_minimo=largo_minimo_borrado)
        fila = fila[var["fila"].to_numpy()]
        claves = var["variante"].to_numpy()
    else:
        claves = token
    codigos_bloque = pd.factorize(claves)[0].astype(np.int64)
    log.info(
        f"[tokens] {n:,} registros · {fila.size:,} entradas de índice · {int(codigos_bloque.max()) + 1:,} cubetas "
        f"(k={k_tokens}, max_bucket={max_bucket}, borrados={'sí' if con_borrados else 'no'})"
    )
    yield from pares_por_bloque(
        codigos_bloque,
        posiciones[fila],
        codigos_fuente[fila],
        politica_fuentes,
        tam_maximo=max_bucket,
        tam_lote=tam_lote,
    )


def _top_k_por_fila(fila: np.ndarray, col: np.ndarray, sim: np.ndarray, top_k: int) -> np.ndarray:
    """Máscara que conserva, por fila, las ``top_k`` columnas de mayor similitud."""
    if fila.size == 0:
        return np.zeros(0, dtype=bool)
    orden = np.lexsort((-sim, fila))
    f = fila[orden]
    inicio = np.flatnonzero(np.r_[True, f[1:] != f[:-1]])
    tam = np.diff(np.r_[inicio, f.size])
    rango = np.arange(f.size) - np.repeat(inicio, tam)
    quedan = np.zeros(fila.size, dtype=bool)
    quedan[orden[rango < top_k]] = True
    return quedan


def pares_por_nombre_tfidf(
    nombres: pd.Series,
    *,
    posiciones: np.ndarray | None = None,
    codigos_fuente: np.ndarray | None = None,
    politica_fuentes: np.ndarray | None = None,
    umbral: float = 0.4,
    top_k: int = 50,
    ngram: int = 3,
    min_df: int = 2,
    max_df: float = 0.05,
    lote_filas: int = 2000,
    max_filas: int = 1_500_000,
    tam_lote: int = 2_000_000,
    registrador: logging.Logger | None = None,
) -> Iterator[np.ndarray]:
    """Pares con coseno TF-IDF de trigramas de caracteres ≥ ``umbral``, a lo sumo ``top_k`` por registro.

    Se calcula por lotes de ``lote_filas`` filas contra todo el corpus (producto disperso); ``max_df`` deja fuera los
    trigramas que están en más de esa fracción de nombres (son los que harían denso el producto). Con más de ``max_filas``
    registros la estrategia se omite y lo dice: parta la base (por país, por régimen) o confíe en los tokens.

    Yields:
        Arreglos ``(m, 2)`` int64 ``(menor, mayor)``.
    """
    log = registrador or logger
    n = len(nombres)
    if posiciones is None:
        posiciones = np.arange(n, dtype=np.int64)
    if codigos_fuente is None or politica_fuentes is None:
        codigos_fuente, politica_fuentes = _politica_trivial(n)
    if n < 2:
        return
    if n > max_filas:
        log.warning(
            f"[tfidf] {n:,} registros superan max_filas={max_filas:,}: se omite el TF-IDF de nombre. "
            "Parta la base (país, régimen) o suba candidatos_hibrido.tfidf_max_filas si cabe en memoria."
        )
        return
    from sklearn.feature_extraction.text import TfidfVectorizer

    texto = pd.Series(nombres).fillna("").astype(str).to_numpy()
    try:
        vectorizador = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(ngram, ngram),
            min_df=min_df,
            max_df=max_df,
            dtype=np.float32,
        )
        x = vectorizador.fit_transform(texto).tocsr()
    except ValueError as e:  # vocabulario vacío (nombres vacíos o todos genéricos)
        log.info(f"[tfidf] sin vocabulario útil: {e}")
        return
    xt = x.T.tocsr()
    log.info(
        f"[tfidf] {n:,} registros · {x.shape[1]:,} trigramas (umbral={umbral}, top_k={top_k}, max_df={max_df})"
    )
    acumulado: list[np.ndarray] = []
    acumulados = 0
    for a in range(0, n, lote_filas):
        b = min(a + lote_filas, n)
        s = (x[a:b] @ xt).tocoo()
        fila, col, sim = s.row.astype(np.int64) + a, s.col.astype(np.int64), s.data
        vale = (sim >= umbral) & (col > fila)
        fila, col, sim = fila[vale], col[vale], sim[vale]
        if fila.size == 0:
            continue
        vale = politica_fuentes[codigos_fuente[fila], codigos_fuente[col]]
        fila, col, sim = fila[vale], col[vale], sim[vale]
        quedan = _top_k_por_fila(fila, col, sim, top_k)
        pares = np.column_stack((posiciones[fila[quedan]], posiciones[col[quedan]]))
        pares.sort(axis=1)
        acumulado.append(pares)
        acumulados += len(pares)
        if acumulados >= tam_lote:
            yield np.concatenate(acumulado)
            acumulado, acumulados = [], 0
    if acumulado:
        yield np.concatenate(acumulado)
