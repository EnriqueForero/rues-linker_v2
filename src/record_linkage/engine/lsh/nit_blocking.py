"""engine.lsh.nit_blocking — Bloqueo de candidatos por NIT base.

Contexto: Google Colab Free (~12 GB RAM, 2–4 M registros).

El bloqueo LSH por n-gramas de NOMBRE_LIMPIO captura solo pares cuyos nombres
comparten n-gramas. Pero existen pares verdaderos donde **solo el NIT puede
unirlos** porque los nombres no comparten tokens:

    - "EY COLOMBIA"      ↔ "ERNST & YOUNG EN LIQUIDACION"  (mismo NIT)
    - "ACCENTURE SL"     ↔ "DISTRIBUIDORA ANDERSEN CONSULTING"
    - "PINTUCO GRUPO ORBIS" ↔ "AKZONOBEL PINTUCO"

En el ground truth exhaustivo (1456 regs), el 25 % de pares verdaderos
comparte NIT base exacto, y el 61 % comparte NIT base a distancia
Levenshtein ≤ 1. Este módulo genera esos pares de forma vectorizada y los
fusiona con los del LSH antes del scoring, atacando directamente la causa
raíz del cuello de recall (P0-1 del ROADMAP).

Estrategia (vectorizada, RAM-segura):
    1. Bloquear por NIT base exacto: `groupby('NIT_BASE')` y emitir pares
       intra-grupo solo si el grupo tiene ≥ 2 registros y ≤ `max_bucket_size`
       (cota dura contra explosión cuadrática). NITs vacíos se descartan.
    2. (Opcional) Bloquear por NIT base a distancia ≤ 1: cada NIT se conecta
       con los NITs que difieren en exactamente un dígito (sustitución,
       inserción o borrado de un dígito al final). Esto captura los típicos
       errores del RUES: dígito de verificación distinto, transposición o
       dígito extra. Implementado generando "vecinos" canónicos sin
       comparación O(n²).

Output: set de tuplas `(idx_0, idx_1)` con `idx_0 < idx_1`, listo para
fusionar con los candidatos del LSH.

Author: Claude (auditor)  Date: 2026-05-22  Version: 2.5.0
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from .politica_pares import indices_de_grupos, pares_por_bloque

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NitBlockingConfig:
    """Parámetros del bloqueo por NIT base.

    Attributes:
        enable_exact: Activa el bloqueo por NIT base exacto. Default True.
        radio_vecindad: Radio de la vecindad por borrados (v0.17.4). 1
            captura un dígito cambiado, sobrante o faltante; 2 captura
            dos. Conviene igualarlo a la tolerancia que el scorer va a
            aceptar: bloquear más estrecho que el scorer pone un techo
            al recall que ningún umbral posterior puede levantar.
        enable_neighbors: Activa el bloqueo por NITs a distancia 1
            (sustitución/inserción/borrado de un dígito). Default True.
            Aumenta el recall a costa de más pares candidatos.
        max_bucket_size: Tamaño máximo de bucket emitido. Si un NIT tiene
            más registros que esto, se descarta para evitar explosión
            cuadrática (un NIT con 1000 registros genera ~500k pares).
            Default 200.
        min_nit_length: Longitud mínima del NIT base para considerarlo.
            Default 6 (corta NITs claramente truncados).
        min_name_similarity: Filtro post-bloqueo. Si > 0 y la columna
            ``name_column`` está disponible, descarta pares cuyo
            ``token_set_ratio`` entre nombres está por debajo de este
            valor (0..1). Vectorizado con rapidfuzz. Default 0.0 (sin
            filtro). Recomendado 0.30 según calibración v2.7.0.
        name_column: Nombre de la columna con la razón social para el
            filtro ``min_name_similarity``. Solo se usa si dicho filtro
            está activo. Default ``"RAZON_SOCIAL"``.
    """

    enable_exact: bool = True
    enable_neighbors: bool = True
    radio_vecindad: int = 1
    max_bucket_size: int = 200
    min_nit_length: int = 6
    min_name_similarity: float = 0.0
    name_column: str = "RAZON_SOCIAL"

    def __post_init__(self) -> None:
        if self.radio_vecindad < 0:
            raise ValueError(f"radio_vecindad={self.radio_vecindad} debe ser >= 0")
        if self.max_bucket_size < 2:
            raise ValueError(f"max_bucket_size={self.max_bucket_size} debe ser ≥ 2")
        if self.min_nit_length < 1:
            raise ValueError(f"min_nit_length={self.min_nit_length} debe ser ≥ 1")
        if not (0.0 <= self.min_name_similarity <= 1.0):
            raise ValueError(f"min_name_similarity={self.min_name_similarity} debe estar en [0, 1]")


def _pairs_from_indices(indices: list[int]) -> Iterable[tuple[int, int]]:
    """Genera pares (i, j) con i < j desde una lista de índices."""
    return combinations(sorted(indices), 2)


def _generate_one_digit_neighbors(nit: str) -> set[str]:
    """Genera vecinos canónicos a distancia de edición 1 sobre un NIT.

    Esto evita una comparación O(n²) entre NITs: si dos NITs están a
    distancia ≤ 1, ambos comparten al menos un vecino canónico. Para cada
    NIT emitimos sus vecinos; dos NITs que comparten al menos un vecino
    están a distancia ≤ 1.

    Esquema:
        - Sustitución (mismo length): se emite el NIT con el dígito en `i`
          reemplazado por "*". Dos NITs de igual longitud que difieren en un
          dígito comparten ese patrón.
        - Inserción / borrado (length difiere en 1): el NIT más corto se
          emite tal cual con el prefijo "=" (longitud = len(nit)), y el
          más largo emite cada uno de sus borrados con el prefijo "=" de
          longitud len(nit)−1. Si alguna posición del NIT largo, una vez
          borrada, coincide con el corto, ambos comparten esa clave "=".

    Args:
        nit: NIT base (solo dígitos).

    Returns:
        Conjunto de "vecinos canónicos" del NIT que se usarán como buckets.
    """
    neighbors: set[str] = set()
    n = len(nit)
    # Sustituciones: posición i con '*' (mismo length).
    for i in range(n):
        neighbors.add("*" + nit[:i] + "_" + nit[i + 1 :])
    # Borrados: posición i eliminada (compara con NITs de longitud n−1).
    # El prefijo "=L" indica "este NIT canonicalizado a longitud L".
    for i in range(n):
        # Borrado: contribuye a buckets de longitud n−1.
        neighbors.add(f"=L{n - 1}:" + nit[:i] + nit[i + 1 :])
    # Identidad: el propio NIT como bucket de su longitud, para que NITs
    # más largos puedan caer en este bucket vía su borrado.
    neighbors.add(f"=L{n}:" + nit)
    return neighbors


def block_by_nit_base(
    df: pd.DataFrame,
    nit_column: str = "NIT_BASE",
    config: NitBlockingConfig | None = None,
) -> set[tuple[int, int]]:
    """Genera pares candidatos por bloqueo de NIT base.

    El bloqueo es complementario al LSH por nombre: produce pares que el LSH
    no puede capturar porque los nombres no comparten n-gramas. Vectorizado
    con `groupby` y operaciones sobre arrays, sin bucles por fila.

    Args:
        df: DataFrame con índice posicional (0..n-1) y la columna `nit_column`.
            Debe ser el mismo índice que se usa para los pares LSH (`_idx` o
            la posición de fila).
        nit_column: Nombre de la columna con el NIT base canonicalizado.
        config: Configuración del bloqueo. Si es None, se usa el default.

    Returns:
        Conjunto de pares `(idx_0, idx_1)` con `idx_0 < idx_1` candidatos
        para scoring.

    Raises:
        ValueError: Si la columna NIT no existe o el DataFrame está vacío.
    """
    if config is None:
        config = NitBlockingConfig()

    if df.empty:
        return set()
    if nit_column not in df.columns:
        raise ValueError(
            f"Columna '{nit_column}' no encontrada en DataFrame "
            f"(columnas disponibles: {list(df.columns)})"
        )

    # 1. Normalizar la columna NIT: string, sin nulos, solo dígitos.
    nit_series = df[nit_column].astype(str).str.strip()
    # Filtrar nulos, "nan", "" y NITs cortos.
    valid_mask = (
        (nit_series != "")
        & (nit_series.str.lower() != "nan")
        & (nit_series.str.len() >= config.min_nit_length)
    )
    n_total = len(df)
    n_valid = int(valid_mask.sum())
    if n_valid == 0:
        logger.info("[nit_blocking] Ningún NIT válido para bloquear (todos vacíos o cortos).")
        return set()

    # Trabajar solo con índices posicionales válidos. Usamos np.arange y la
    # máscara para garantizar índices 0..n-1 coherentes con el resto del
    # pipeline (NO el índice del DataFrame, que puede haberse reordenado).
    positional_idx = np.arange(n_total)[valid_mask.to_numpy()]
    nits_valid = nit_series.to_numpy()[valid_mask.to_numpy()]

    pairs: set[tuple[int, int]] = set()

    # 2. Bloqueo por NIT EXACTO (groupby vectorizado).
    if config.enable_exact:
        # Agrupar por NIT. groupby es O(n log n) en pandas pero internamente
        # usa C; no es un bucle Python sobre filas.
        nit_series_valid = pd.Series(nits_valid, index=positional_idx)
        groups = nit_series_valid.groupby(nit_series_valid.values).groups
        n_buckets_emitted = 0
        n_buckets_skipped = 0
        for nit_val, idx_array in groups.items():
            sz = len(idx_array)
            if sz < 2:
                continue
            if sz > config.max_bucket_size:
                n_buckets_skipped += 1
                logger.debug(
                    f"[nit_blocking] Bucket '{nit_val}' descartado: {sz} > "
                    f"max_bucket_size={config.max_bucket_size}"
                )
                continue
            n_buckets_emitted += 1
            indices_list = [int(x) for x in idx_array]
            pairs.update(_pairs_from_indices(indices_list))
        logger.info(
            f"[nit_blocking] Exacto: {n_buckets_emitted:,} buckets emitidos "
            f"({n_buckets_skipped} descartados por tamaño) → {len(pairs):,} pares"
        )

    # 3. Bloqueo por VECINOS (distancia ≤ 1, opcional).
    if config.enable_neighbors:
        # Construir un mapa neighbor_key -> [positional_idx, ...].
        # Cada NIT contribuye múltiples claves (sustituciones + borrados).
        # Pares: misma clave => NITs a distancia ≤ 1. Se descartan pares ya
        # añadidos por el bloqueo exacto.
        from collections import defaultdict

        neighbor_index: dict[str, list[int]] = defaultdict(list)
        for pos_idx, nit_val in zip(positional_idx, nits_valid, strict=True):
            # Generar vecinos canónicos. Solo NITs cortos (≤ 15 dígitos)
            # — los más largos son atípicos y dispararían el costo.
            if len(nit_val) > 15:
                continue
            for key in _generate_one_digit_neighbors(nit_val):
                neighbor_index[key].append(int(pos_idx))

        # Emitir pares por bucket de vecinos, con la misma cota max_bucket_size.
        pairs_before = len(pairs)
        n_neighbor_buckets = 0
        for _, idx_list in neighbor_index.items():
            sz = len(idx_list)
            if sz < 2 or sz > config.max_bucket_size:
                continue
            n_neighbor_buckets += 1
            pairs.update(_pairs_from_indices(idx_list))
        pairs_added = len(pairs) - pairs_before
        logger.info(
            f"[nit_blocking] Vecinos (dist≤1): {n_neighbor_buckets:,} buckets "
            f"→ +{pairs_added:,} pares nuevos"
        )

    logger.info(f"[nit_blocking] Total pares por bloqueo de NIT: {len(pairs):,}")

    # ── FILTRO POST-BLOQUEO: name_sim ≥ min_name_similarity ─────────────
    # v2.7.0 (P2 Camino #2 — re-pesado por origen del par): descarta pares
    # del bloqueo NIT cuyos nombres son muy distintos. Esto sube la
    # precision del bloqueo NIT sin tocar el scorer downstream, atacando
    # exactamente los casos negativos diseñados del ground truth
    # (NITs adyacentes con nombres totalmente distintos como
    # 800999001/TRANSPORTES SUR ↔ 800999002/INDUSTRIAS NORTE).
    if config.min_name_similarity > 0.0 and pairs:
        if config.name_column not in df.columns:
            logger.warning(
                f"[nit_blocking] min_name_similarity={config.min_name_similarity} "
                f"pero columna '{config.name_column}' no existe; se omite el filtro."
            )
        else:
            try:
                from rapidfuzz import fuzz
            except ImportError:
                logger.warning(
                    "[nit_blocking] rapidfuzz no disponible; se omite el filtro de name_sim."
                )
            else:
                names = df[config.name_column].astype(str).str.upper().to_numpy()
                pairs_list = list(pairs)
                n_before = len(pairs_list)
                # Calcular token_set_ratio vectorizadamente.
                # rapidfuzz no tiene una API vectorizada cross-pair sin matriz,
                # pero process_arrays_cdist es overkill aquí. Para n pares
                # con n grande (hasta ~1M en producción) la iteración Python
                # sobre rapidfuzz es aceptable: ~500k ops/s en CPython.
                thr_pct = config.min_name_similarity * 100.0
                pairs_kept: list[tuple[int, int]] = []
                for a, b in pairs_list:
                    if fuzz.token_set_ratio(names[a], names[b]) >= thr_pct:
                        pairs_kept.append((a, b))
                pairs = set(pairs_kept)
                n_dropped = n_before - len(pairs)
                logger.info(
                    f"[nit_blocking] Filtro name_sim ≥ {config.min_name_similarity}: "
                    f"{n_before:,} → {len(pairs):,} (−{n_dropped:,} pares)"
                )

    return pairs


# ══════════════════════════════════════════════════════════════════════════
#  v0.17.4 — Camino vectorizado, consciente de la política y acotado en RAM
# ══════════════════════════════════════════════════════════════════════════
# El camino de arriba (`block_by_nit_base`) devuelve un `set` de tuplas de
# Python. Medido: sobre 4,37 M de registros el índice de vecindad de ese
# camino ocupa entre 6 y 10 GB y tarda ~3 min en construirse, porque crea
# 19 cadenas de Python por registro (83 millones de objetos) antes de emitir
# un solo par. Ésa fue la causa exacta del OOM en Colab.
#
# Lo que sigue lo reemplaza con tres decisiones:
#
#   1. **El NIT se codifica como entero**, no como cadena. Un NIT es dígitos;
#      `(longitud, valor)` cabe holgado en un int64. 4,37 M de códigos son
#      35 MB en vez de ~500 MB, y las variantes a distancia 1 salen de
#      aritmética vectorizada en vez de concatenar cadenas en un bucle.
#   2. **La política se aplica ANTES de materializar**, con el mismo módulo
#      que usa el bloqueo LSH (`politica_pares`). Con RUES confiable, los
#      bloques puramente internos de RUES se descartan sin enumerarse.
#   3. **Los pares se rinden por lotes**, nunca se acumulan. El consumidor
#      los escribe a SQLite y los suelta.


#: Variantes de NIT que se materializan a la vez. 10 M de int64 son 80 MB;
#: con el arreglo de posiciones y el orden de agrupación el pico ronda los
#: 400 MB, que es el presupuesto que un Colab gratuito puede pagar sin
#: acercarse al límite.
MAX_VARIANTES_POR_LOTE = 10_000_000

#: Multiplicador que separa la longitud del valor dentro del código entero.
#: Soporta NITs de hasta 15 dígitos más una inserción (16), que es más de lo
#: que cualquier identificador colombiano usa.
_BASE_LONGITUD = 10**16


def codificar_nits(nits: np.ndarray | pd.Series) -> np.ndarray:
    """Codifica NITs de dígitos como enteros ``longitud * 10**16 + valor``.

    La longitud entra en el código a propósito: "0123456789" y "123456789"
    son NITs distintos y deben caer en bloques distintos, cosa que el valor
    numérico por sí solo no distingue.

    Vectorizado de punta a punta: recorrer 4,4 M de cadenas en un bucle de
    Python para validarlas costaba más que todo el bloqueo posterior.

    Args:
        nits: arreglo o serie de cadenas de dígitos.

    Returns:
        Arreglo int64 de códigos; ``-1`` donde el NIT no es codificable
        (vacío, con no-dígitos o de más de 15 dígitos).
    """
    serie = nits if isinstance(nits, pd.Series) else pd.Series(np.asarray(nits, dtype=object))
    texto = serie.astype("string")
    largos = texto.str.len().fillna(0).astype("int64")
    utilizable = texto.str.isdigit().fillna(False).to_numpy() & (largos.to_numpy() <= 15)
    valores = pd.to_numeric(texto.where(pd.Series(utilizable, index=texto.index)), errors="coerce")
    valores = valores.fillna(-1).astype("int64").to_numpy()
    codigos = np.where(
        utilizable & (valores >= 0), largos.to_numpy() * _BASE_LONGITUD + valores, -1
    )
    return codigos.astype(np.int64, copy=False)


def _partes(codigos: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Separa un código en (longitud, valor)."""
    return codigos // _BASE_LONGITUD, codigos % _BASE_LONGITUD


def patrones_de_borrado(largo_max: int, radio: int) -> list[tuple[int, ...]]:
    """Posiciones a borrar para generar la vecindad de un NIT.

    Método de vecindad por borrados (SymSpell, Garbe 2012): **si dos cadenas
    están a distancia de edición ≤ r, existe una forma común obtenida
    borrando a lo sumo r caracteres de cada una**. Vale para sustitución,
    inserción, borrado y transposición, y por eso basta con generar borrados
    —no hay que enumerar los 10 dígitos posibles en cada posición—.

    Con NITs de 9 dígitos y radio 1 son 10 claves por registro en vez de las
    199 que exigía enumerar sustituciones e inserciones; con radio 2, 46.

    Referencia: https://wolfgarbe.medium.com/1000x-faster-spelling-correction-algorithm-2012-8701fcd87a5f

    Args:
        largo_max: longitud máxima de NIT presente.
        radio: radio de la vecindad (0 = solo identidad).

    Returns:
        Lista de tuplas de posiciones a borrar, de mayor a menor dentro de
        cada tupla, empezando por la tupla vacía (identidad).
    """
    from itertools import combinations as _combinaciones

    patrones: list[tuple[int, ...]] = [()]
    for cuantos in range(1, radio + 1):
        for posiciones in _combinaciones(range(largo_max), cuantos):
            patrones.append(tuple(sorted(posiciones, reverse=True)))
    return patrones


def claves_por_borrado(codigos: np.ndarray, patron: tuple[int, ...]) -> np.ndarray:
    """Aplica un patrón de borrado a códigos de NIT, vectorizado.

    Devuelve ``-1`` donde el patrón no aplica (el NIT es más corto que la
    posición pedida), para que quien llama lo descarte sin ramificar.
    """
    largos, valores = _partes(codigos)
    validos = np.ones(codigos.shape, dtype=bool)
    for posicion in patron:
        validos &= largos > posicion
        potencia = np.int64(10**posicion)
        alto = valores // (potencia * 10)
        bajo = valores % potencia
        valores = np.where(validos, alto * potencia + bajo, 0)
        largos = np.where(validos, largos - 1, largos)
    return np.where(validos & (largos >= 1), largos * _BASE_LONGITUD + valores, -1)


def _nits_utilizables(
    df: pd.DataFrame, nit_column: str, min_nit_length: int
) -> tuple[np.ndarray, np.ndarray]:
    """(posiciones, códigos) de los registros con NIT utilizable."""
    serie = df[nit_column].astype("string").str.strip()
    codigos = codificar_nits(serie)
    utilizable = (codigos >= 0) & (serie.str.len().fillna(0).to_numpy() >= min_nit_length)
    posiciones = np.arange(len(df), dtype=np.int64)[utilizable]
    return posiciones, codigos[utilizable]


def iter_pares_por_nit(
    df: pd.DataFrame,
    *,
    nit_column: str = "NIT_BASE",
    config: NitBlockingConfig | None = None,
    codigos_fuente: np.ndarray | None = None,
    politica_fuentes: np.ndarray | None = None,
    max_variantes: int = MAX_VARIANTES_POR_LOTE,
    tam_lote: int = 2_000_000,
    registrador: logging.Logger | None = None,
) -> Iterator[np.ndarray]:
    """Rinde por lotes los pares candidatos por NIT, sin acumularlos.

    Args:
        df: DataFrame con índice posicional 0..n-1.
        nit_column: columna con el NIT base canonicalizado.
        config: parámetros del bloqueo.
        codigos_fuente: código de fuente por posición, o None si no hay
            política que aplicar.
        politica_fuentes: matriz k×k de combinaciones de fuente permitidas.
        max_variantes: tope de variantes materializadas por lote en el
            bloqueo por vecindad.
        tam_lote: pares por lote rendido.
        registrador: logger opcional para trazar el conteo por etapa.

    Yields:
        Arreglos ``(m, 2)`` int64 con pares ``(menor, mayor)``.
    """
    config = config or NitBlockingConfig()
    log = registrador or logger
    if nit_column not in df.columns:
        raise ValueError(f"Columna '{nit_column}' no encontrada en DataFrame.")
    if df.empty:
        return

    posiciones, codigos = _nits_utilizables(df, nit_column, config.min_nit_length)
    if posiciones.size == 0:
        log.info("[nit_blocking] Ningún NIT válido para bloquear.")
        return

    if codigos_fuente is None or politica_fuentes is None:
        fuentes = np.zeros(posiciones.size, dtype=np.int16)
        politica = np.ones((1, 1), dtype=bool)
    else:
        fuentes = np.asarray(codigos_fuente)[posiciones]
        politica = np.asarray(politica_fuentes, dtype=bool)

    if config.enable_exact:
        emitidos = 0
        for lote in pares_por_bloque(
            codigos,
            posiciones,
            fuentes,
            politica,
            tam_maximo=config.max_bucket_size,
            tam_lote=tam_lote,
        ):
            emitidos += len(lote)
            yield lote
        log.info(f"[nit_blocking] Exacto: {emitidos:,} pares permitidos")

    if config.enable_neighbors:
        emitidos = 0
        for lote in _pares_por_vecindad(
            codigos, posiciones, fuentes, politica, config, max_variantes, tam_lote, log
        ):
            emitidos += len(lote)
            yield lote
        log.info(f"[nit_blocking] Vecindad (dist≤1): {emitidos:,} pares permitidos")


def _pares_por_vecindad(
    codigos: np.ndarray,
    posiciones: np.ndarray,
    fuentes: np.ndarray,
    politica: np.ndarray,
    config: NitBlockingConfig,
    max_variantes: int,
    tam_lote: int,
    log: logging.Logger,
) -> Iterator[np.ndarray]:
    """Pares a distancia ≤ radio, indexando SIEMPRE el lado más pequeño.

    Ésta es la decisión que cambia el orden de magnitud. Materializar la
    vecindad de los 4,37 M de registros de RUES es lo que agotaba la RAM;
    indexar la de los 19.407 exportadores cuesta menos de un megabyte, y el
    lado grande se recorre patrón por patrón sin acumular nada. El conjunto
    de pares es el mismo: "distancia ≤ r" es simétrica.
    """
    if config.radio_vecindad < 1:
        return
    k = int(politica.shape[0])
    largo_max = int(_partes(codigos)[0].max()) if codigos.size else 0
    patrones = patrones_de_borrado(largo_max, config.radio_vecindad)
    indices_por_fuente = {a: np.flatnonzero(fuentes == a) for a in range(k)}

    for a in range(k):
        for b in range(a, k):
            if not politica[a, b]:
                continue
            lado_a, lado_b = indices_por_fuente[a], indices_por_fuente[b]
            if lado_a.size == 0 or lado_b.size == 0:
                continue
            if a == b:
                if lado_a.size < 2:
                    continue
                indice, sonda = lado_a, lado_a
            elif lado_a.size <= lado_b.size:
                indice, sonda = lado_a, lado_b
            else:
                indice, sonda = lado_b, lado_a

            paso = max(1, max_variantes // max(1, len(patrones)))
            if indice.size > paso:
                log.info(
                    f"[nit_blocking] Vecindad {a}×{b}: el lado indexado tiene "
                    f"{indice.size:,} registros; se indexa en "
                    f"{-(-indice.size // paso)} lotes de {paso:,} para acotar la RAM."
                )
            for inicio in range(0, indice.size, paso):
                yield from _reunir_por_vecindad(
                    indice[inicio : inicio + paso],
                    sonda,
                    codigos,
                    posiciones,
                    patrones,
                    config,
                    tam_lote,
                    mismo=(a == b),
                )


def _reunir_por_vecindad(
    indice: np.ndarray,
    sonda: np.ndarray,
    codigos: np.ndarray,
    posiciones: np.ndarray,
    patrones: list[tuple[int, ...]],
    config: NitBlockingConfig,
    tam_lote: int,
    *,
    mismo: bool,
) -> Iterator[np.ndarray]:
    """Empareja por clave de borrado un lado indexado contra otro recorrido."""
    claves_indice: list[np.ndarray] = []
    duenos_indice: list[np.ndarray] = []
    codigos_indice = codigos[indice]
    for patron in patrones:
        claves = claves_por_borrado(codigos_indice, patron)
        validas = claves >= 0
        if not validas.any():
            continue
        claves_indice.append(claves[validas])
        duenos_indice.append(posiciones[indice][validas])
    if not claves_indice:
        return
    tabla_claves = np.concatenate(claves_indice)
    tabla_duenos = np.concatenate(duenos_indice)
    del claves_indice, duenos_indice
    orden = np.argsort(tabla_claves, kind="stable")
    tabla_claves, tabla_duenos = tabla_claves[orden], tabla_duenos[orden]

    acumulado: list[np.ndarray] = []
    acumulados = 0
    codigos_sonda = codigos[sonda]
    lugares_sonda = posiciones[sonda]
    for patron in patrones:
        claves = claves_por_borrado(codigos_sonda, patron)
        validas = claves >= 0
        if not validas.any():
            continue
        consultadas = claves[validas]
        lugares = lugares_sonda[validas]
        desde = np.searchsorted(tabla_claves, consultadas, side="left")
        hasta = np.searchsorted(tabla_claves, consultadas, side="right")
        cuantos = hasta - desde
        # Cota dura: una clave que engancha con medio índice no es evidencia,
        # es una explosión cuadrática esperando a ocurrir.
        dentro = (cuantos > 0) & (cuantos <= config.max_bucket_size)
        if not dentro.any():
            continue
        desde, cuantos, lugares = desde[dentro], cuantos[dentro], lugares[dentro]
        total = int(cuantos.sum())
        if total == 0:
            continue
        posiciones_tabla = indices_de_grupos(desde, cuantos)
        izquierda = np.repeat(lugares, cuantos)
        derecha = tabla_duenos[posiciones_tabla]
        lo = np.minimum(izquierda, derecha)
        hi = np.maximum(izquierda, derecha)
        distintos = lo != hi
        if not distintos.any():
            continue
        lote = np.unique(np.column_stack((lo[distintos], hi[distintos])), axis=0)
        acumulado.append(lote)
        acumulados += len(lote)
        if acumulados >= tam_lote:
            yield np.unique(np.concatenate(acumulado), axis=0)
            acumulado, acumulados = [], 0
    if acumulado:
        yield np.unique(np.concatenate(acumulado), axis=0)
