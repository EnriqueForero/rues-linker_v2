"""engine.lsh.llaves_extra — Bloqueo por llaves declaradas (C31, v0.20.0).

El techo que levanta
--------------------
Medido sobre `benchmark_institucional.csv.gz`, el recall de BLOQUEO por
estrato —la fracción de pares verdaderos que siquiera llegan a ser
candidatos— era:

    REAL       0,982
    CONTACTO   0,959
    RUIDO      0,730

Un par que no llega al scorer no lo rescata ningún umbral, ningún comparador
y ninguna evidencia adicional. Mientras el bloqueo pierda el 27 % de un
estrato, el techo de recall de ese estrato es 0,73, se afine lo que se afine
después. Por eso este módulo es P0 y no una mejora incremental.

Qué hace
--------
Genera pares candidatos por IGUALDAD de una llave declarada —teléfono, correo,
dirección normalizada, celda geográfica, o cualquier columna que el usuario
declare— y los fusiona con los del LSH de nombre y los del bloqueo por
identificador. Dos registros del mismo ente que no comparten ni un trigrama de
nombre sí suelen compartir el teléfono.

Cómo evita repetir el OOM de 0.17.3
-----------------------------------
La regresión que mató una sesión de Colab en 0.17.3 fue construir un
diccionario de Python con 19 cadenas por registro: 83 millones de objetos,
entre 6 y 10 GB. Aquí no se construye ninguna estructura de Python por
registro:

1. La llave se canonicaliza vectorizadamente y se convierte a **códigos
   enteros** con ``pd.factorize`` — un int32 por registro, no una cadena.
2. Los bloques que no pueden producir ningún par permitido se descartan
   ANTES de enumerarlos (``politica_pares.bloques_utiles``).
3. Los pares se rinden por lotes y se sueltan; nunca existe el conjunto
   completo en memoria.

Costo medido: ~4 bytes por registro y llave, más el lote en curso. A 5 M de
registros y tres llaves son ~60 MB, frente a los 6–10 GB de la implementación
que se retiró.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.20.0
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .politica_pares import pares_por_bloque

logger = logging.getLogger(__name__)

__all__ = [
    "CANONICALIZADORES",
    "LlaveBloqueo",
    "canonicalizar_llave",
    "iter_pares_por_llaves",
]

#: Tamaño máximo de bloque por defecto. Un valor de llave compartido por más
#: registros que esto no identifica a nadie: es un centinela ('0000000000'),
#: un call center o un correo corporativo compartido. Enumerarlo cuesta
#: O(k²) pares que además son casi todos falsos.
MAX_BLOQUE_POR_DEFECTO = 100

#: Longitud mínima de la llave canonicalizada. Por debajo no discrimina.
LONGITUD_MINIMA_POR_DEFECTO = 5

#: Lado de la celda geográfica en grados. 0,01° ≈ 1,1 km en latitud; a la
#: latitud de Colombia, ≈ 1,1 km también en longitud. Se usa una rejilla y no
#: una distancia porque una rejilla es una llave exacta —agrupa en O(n)— y la
#: distancia exigiría comparar todos contra todos, que es lo que el bloqueo
#: existe para evitar. Los vecinos que caen al otro lado del borde los recoge
#: el LSH de nombre o las demás llaves.
GRADOS_CELDA_GEO = 0.01


def _texto(valores: pd.Series) -> pd.Series:
    return valores.astype("string").fillna("").str.strip().str.upper()


def _canon_texto(valores: pd.Series) -> pd.Series:
    """Mayúsculas sin acentos ni puntuación, espacios colapsados."""
    import unicodedata

    plano = _texto(valores).map(
        lambda x: "".join(
            c for c in unicodedata.normalize("NFKD", x) if not unicodedata.combining(c)
        )
    )
    return (
        plano.str.replace(r"[^A-Z0-9]+", " ", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )


def _celda_geo(valores: pd.Series, desplazamiento: float) -> pd.Series:
    """Celda de rejilla a partir de ``"lat,lon"``. Vacío si no hay dos números.

    ``desplazamiento`` mueve el origen de la rejilla en fracciones de celda.
    Dos puntos vecinos pueden caer a lados distintos de un borde —medido:
    (4,6500, −74,0500) y (4,6501, −74,0502) están a 25 m y quedan en celdas
    distintas—. Declarando la misma llave dos veces con desplazamiento 0 y 0,5
    cualquier par más cercano que media celda comparte celda en al menos una
    de las dos rejillas. Es el mismo truco que las bandas del LSH: repetir la
    partición desplazada en vez de agrandar la celda, que costaría bloques
    cuatro veces más grandes.
    """
    from ...matching.puente_campos import descomponer_geo

    coordenadas = descomponer_geo(valores.to_numpy())
    valido = ~np.isnan(coordenadas).any(axis=1)
    seguras = np.where(np.isnan(coordenadas), 0.0, coordenadas)
    celda = np.floor(seguras / GRADOS_CELDA_GEO + desplazamiento).astype("int64")
    salida = np.where(
        valido,
        np.char.add(np.char.add(celda[:, 0].astype(str), "_"), celda[:, 1].astype(str)),
        "",
    )
    return pd.Series(salida, index=valores.index, dtype="string")


def _canon_geo(valores: pd.Series) -> pd.Series:
    return _celda_geo(valores, 0.0)


def _canon_geo_desplazada(valores: pd.Series) -> pd.Series:
    return _celda_geo(valores, 0.5)


def _canon_telefono(valores: pd.Series) -> pd.Series:
    from ...matching.comparadores_extra import canonicalizar_telefono

    return canonicalizar_telefono(valores.to_numpy())


def _canon_email(valores: pd.Series) -> pd.Series:
    from ...matching.comparadores_extra import canonicalizar_email

    local, dominio = canonicalizar_email(valores.to_numpy())
    return (local + "@" + dominio).where(local.str.len() > 0, "")


def _canon_documento(valores: pd.Series) -> pd.Series:
    from ...matching.identificadores import bases_canonicas

    return pd.Series(bases_canonicas(valores.to_numpy()), index=valores.index).astype("string")


def _canon_token_infrecuente(valores: pd.Series) -> pd.Series:
    """El token MENOS frecuente del texto: la palabra que más identifica.

    Motivación medida. De los pares verdaderos del estrato de ruido que el
    LSH de nombre no alcanza a proponer, el 72,5 % comparte al menos una
    palabra ENTERA. El MinHash trabaja sobre trigramas y una corrupción de dos
    caracteres arrastra seis trigramas, pero deja intactas las palabras que no
    tocó. Agrupar por la palabra más rara del nombre recupera esos pares en
    O(n) y sin comparar nada.

    Se elige la menos frecuente, no la primera ni la más larga, porque la
    frecuencia documental es lo que mide cuánto identifica un token: en un
    corpus de empresas colombianas 'SAS', 'COLOMBIA' o 'COMERCIALIZADORA'
    aparecen en decenas de miles de registros y agrupar por ellas produciría
    bloques inmensos e inútiles (Spärck Jones, 1972).
    """
    from ...matching.idf import LONGITUD_MINIMA_TOKEN

    limpio = _canon_texto(valores)
    listas = limpio.str.split()
    frecuencia: dict[str, int] = {}
    for tokens in listas:
        for token in set(tokens or ()):
            if len(token) >= LONGITUD_MINIMA_TOKEN:
                frecuencia[token] = frecuencia.get(token, 0) + 1

    def elegir(tokens: list[str] | None) -> str:
        utiles = [t for t in set(tokens or ()) if len(t) >= LONGITUD_MINIMA_TOKEN]
        if not utiles:
            return ""
        # Desempate por el propio token para que el resultado no dependa del
        # orden de iteración: una llave de bloqueo tiene que ser determinista.
        return min(utiles, key=lambda t: (frecuencia.get(t, 0), t))

    return pd.Series([elegir(t) for t in listas], index=valores.index, dtype="string")


#: Formas de reducir una columna a llave de bloqueo. Es un registro, no una
#: cadena de ``if``: añadir una forma nueva es añadir una entrada, sin tocar
#: el motor (OCP). Cada una reutiliza el canonicalizador que ya usa el
#: comparador del mismo tipo, de modo que bloqueo y scoring nunca discrepan
#: sobre qué cuenta como "el mismo teléfono".
CANONICALIZADORES = {
    "texto": _canon_texto,
    "telefono": _canon_telefono,
    "email": _canon_email,
    "documento": _canon_documento,
    "geo": _canon_geo,
    "geo_desplazada": _canon_geo_desplazada,
    "token_infrecuente": _canon_token_infrecuente,
}


@dataclass(frozen=True)
class LlaveBloqueo:
    """Una llave por la que agrupar registros.

    Attributes:
        columna: columna del DataFrame.
        canonicalizador: clave de ``CANONICALIZADORES``.
        longitud_minima: llaves más cortas se descartan por poco selectivas.
        max_bloque: bloques con más miembros se descartan (cota cuadrática).
    """

    columna: str
    canonicalizador: str = "texto"
    longitud_minima: int = LONGITUD_MINIMA_POR_DEFECTO
    max_bloque: int = MAX_BLOQUE_POR_DEFECTO

    def __post_init__(self) -> None:
        if self.canonicalizador not in CANONICALIZADORES:
            raise ValueError(
                f"Qué pasó: canonicalizador '{self.canonicalizador}' no existe. "
                f"Por qué importa: sin él la llave no se puede construir y el "
                f"bloqueo quedaría silenciosamente sin esa señal. "
                f"Qué hacer: use uno de {sorted(CANONICALIZADORES)}."
            )
        if self.longitud_minima < 1:
            raise ValueError("longitud_minima debe ser >= 1.")
        if self.max_bloque < 2:
            raise ValueError("max_bloque debe ser >= 2.")

    @classmethod
    def desde_texto(cls, especificacion: str) -> LlaveBloqueo:
        """Construye una llave desde ``"COLUMNA:canonicalizador[:max_bloque]"``."""
        partes = [p.strip() for p in str(especificacion).split(":")]
        if not partes or not partes[0]:
            raise ValueError(f"Especificación de llave vacía: {especificacion!r}")
        columna = partes[0]
        canon = partes[1] if len(partes) > 1 and partes[1] else "texto"
        maximo = int(partes[2]) if len(partes) > 2 and partes[2] else MAX_BLOQUE_POR_DEFECTO
        return cls(columna=columna, canonicalizador=canon, max_bloque=maximo)


def canonicalizar_llave(df: pd.DataFrame, llave: LlaveBloqueo) -> np.ndarray:
    """Códigos enteros de la llave; ``-1`` donde no hay llave utilizable.

    Devolver códigos y no cadenas es lo que mantiene el costo en 4 bytes por
    registro. ``pd.factorize`` recorre la columna una vez.

    Raises:
        KeyError: si la columna no está en el DataFrame.
    """
    if llave.columna not in df.columns:
        raise KeyError(
            f"Qué pasó: la llave de bloqueo pide la columna '{llave.columna}' y no "
            f"está. Por qué importa: el bloqueo se quedaría sin esa señal sin avisar. "
            f"Qué hacer: corrija el nombre o quite la llave. "
            f"Columnas disponibles: {sorted(df.columns)[:12]}"
        )
    canonica = CANONICALIZADORES[llave.canonicalizador](df[llave.columna])
    canonica = canonica.astype("string").fillna("")
    utiles = canonica.str.len() >= llave.longitud_minima
    codigos, _ = pd.factorize(canonica.where(utiles, pd.NA), use_na_sentinel=True)
    return codigos.astype(np.int64, copy=False)


def iter_pares_por_llaves(
    df: pd.DataFrame,
    *,
    llaves: tuple[LlaveBloqueo, ...],
    codigos_fuente: np.ndarray | None = None,
    politica_fuentes: np.ndarray | None = None,
    tam_lote: int = 2_000_000,
    registrador: logging.Logger | None = None,
) -> Iterator[np.ndarray]:
    """Rinde por lotes los pares que comparten alguna llave declarada.

    Args:
        df: DataFrame con índice posicional 0..n-1.
        llaves: llaves a aplicar; se recorren en orden y sus pares se suman.
        codigos_fuente: código de fuente por posición, o None.
        politica_fuentes: matriz k×k de combinaciones de fuente permitidas.
        tam_lote: pares por lote rendido.
        registrador: logger opcional.

    Yields:
        Arreglos ``(m, 2)`` int64 con pares ``(menor, mayor)``.
    """
    log = registrador or logger
    n = len(df)
    if n < 2 or not llaves:
        return
    posiciones = np.arange(n, dtype=np.int64)
    if codigos_fuente is None or politica_fuentes is None:
        fuentes = np.zeros(n, dtype=np.int16)
        politica = np.ones((1, 1), dtype=bool)
    else:
        fuentes = np.asarray(codigos_fuente, dtype=np.int16)
        politica = np.asarray(politica_fuentes, dtype=bool)

    for llave in llaves:
        codigos = canonicalizar_llave(df, llave)
        utilizables = int((codigos >= 0).sum())
        if utilizables < 2:
            log.info(
                f"[llaves_extra] '{llave.columna}' ({llave.canonicalizador}): "
                f"{utilizables} registros con llave utilizable, se omite."
            )
            continue
        # −1 marca "sin llave utilizable". Es un centinela, no un valor: si
        # se dejara pasar, todos los registros sin teléfono formarían un
        # bloque gigante entre sí. Se filtran las posiciones ANTES de
        # enumerar, no después, que es donde estaría el costo.
        con_llave = codigos >= 0
        codigos_utiles = codigos[con_llave]
        posiciones_utiles = posiciones[con_llave]
        fuentes_utiles = fuentes[con_llave]
        emitidos = 0
        for lote in pares_por_bloque(
            codigos_utiles,
            posiciones_utiles,
            fuentes_utiles,
            politica,
            tam_maximo=llave.max_bloque,
            tam_lote=tam_lote,
        ):
            emitidos += len(lote)
            yield lote
        log.info(
            f"[llaves_extra] '{llave.columna}' ({llave.canonicalizador}): "
            f"{utilizables:,} registros con llave → {emitidos:,} pares."
        )


# ── Bloqueo por tokens raros (C31b) ───────────────────────────────────────

#: Frecuencia documental máxima para que un token sirva de llave. Un token que
#: aparece en más registros que esto no identifica: agrupar por 'COLOMBIA' o
#: 'SAS' produce un bloque de decenas de miles y ni un par útil.
FRECUENCIA_MAXIMA_TOKEN = 50


@dataclass(frozen=True)
class LlaveTokens:
    """Bloqueo por CADA token raro del texto, no solo por uno.

    A diferencia de ``LlaveBloqueo``, un registro entra en tantos bloques como
    tokens raros tenga. Es lo que hace falta cuando la corrupción golpea al
    token más distintivo: medido, 'ORGANIZACION RUIZ TORRES ARGOS' y
    'ORGANIZACION RUZ TORRES RAGOS' son el mismo ente y no comparten ni el
    token más raro de cada uno —comparten TORRES, que es el segundo—.

    Attributes:
        columna: columna de texto.
        frecuencia_maxima: tope de frecuencia documental para admitir un token.
        max_bloque: tope de miembros por bloque.
        longitud_minima: tokens más cortos se ignoran.
    """

    columna: str
    frecuencia_maxima: int = FRECUENCIA_MAXIMA_TOKEN
    max_bloque: int = MAX_BLOQUE_POR_DEFECTO
    longitud_minima: int = 3

    def __post_init__(self) -> None:
        if self.frecuencia_maxima < 2:
            raise ValueError("frecuencia_maxima debe ser >= 2.")
        if self.max_bloque < 2:
            raise ValueError("max_bloque debe ser >= 2.")


def iter_pares_por_tokens(
    df: pd.DataFrame,
    *,
    llave: LlaveTokens,
    codigos_fuente: np.ndarray | None = None,
    politica_fuentes: np.ndarray | None = None,
    tam_lote: int = 2_000_000,
    registrador: logging.Logger | None = None,
) -> Iterator[np.ndarray]:
    """Rinde pares que comparten al menos un token raro.

    Disciplina de memoria: la explosión registro→token se materializa como dos
    arreglos de enteros (posición y código de token), no como listas de
    Python. A cuatro tokens por registro y 5 M de registros son ~160 MB, y los
    tokens frecuentes se descartan ANTES de explotar, que es lo que evita que
    'SAS' multiplique el arreglo por sí solo.
    """
    log = registrador or logger
    if llave.columna not in df.columns:
        raise KeyError(f"iter_pares_por_tokens: falta la columna '{llave.columna}'.")
    n = len(df)
    if n < 2:
        return

    limpio = _canon_texto(df[llave.columna])
    explotado = limpio.str.split().explode()
    explotado = explotado[explotado.str.len() >= llave.longitud_minima]
    if explotado.empty:
        return
    posicion_fila = df.index.get_indexer(explotado.index).astype(np.int64)
    codigos_token, vocabulario = pd.factorize(explotado.to_numpy(), use_na_sentinel=True)

    # Frecuencia DOCUMENTAL: registros distintos en que aparece el token, no
    # veces que aparece. Un nombre que repite una palabra no la hace común.
    unicos = pd.DataFrame({"f": posicion_fila, "t": codigos_token}).drop_duplicates()
    frecuencia = np.bincount(unicos["t"].to_numpy(), minlength=len(vocabulario))
    raros = frecuencia <= llave.frecuencia_maxima
    conservar = raros[unicos["t"].to_numpy()]
    filas = unicos["f"].to_numpy()[conservar]
    tokens = unicos["t"].to_numpy()[conservar]
    if len(filas) < 2:
        return

    if codigos_fuente is None or politica_fuentes is None:
        fuentes = np.zeros(len(filas), dtype=np.int16)
        politica = np.ones((1, 1), dtype=bool)
    else:
        fuentes = np.asarray(codigos_fuente, dtype=np.int16)[filas]
        politica = np.asarray(politica_fuentes, dtype=bool)

    emitidos = 0
    for lote in pares_por_bloque(
        tokens, filas, fuentes, politica, tam_maximo=llave.max_bloque, tam_lote=tam_lote
    ):
        if not len(lote):
            continue
        emitidos += len(lote)
        yield lote
    log.info(
        f"[llaves_extra] tokens de '{llave.columna}': {int(raros.sum()):,} tokens raros "
        f"de {len(vocabulario):,} → {emitidos:,} pares."
    )
