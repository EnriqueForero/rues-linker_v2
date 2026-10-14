"""Candidatos ancla × destino: los pares de nombres que merecen clasificarse.

El cruce es EXHAUSTIVO en la práctica: se consideran todas las superficies de nombre de la ancla (el nombre
y el alias de cada grafía) contra todas las del destino (nombre, alias y matriz reportada) y solo se
descartan los pares que no cumplen ninguna de tres vías:

1. comparten una palabra distintiva, con tolerancia de una letra (índice invertido de variantes con una
   letra borrada, al estilo SymSpell);
2. se parecen como cadena (Jaro-Winkler ≥ 0,85, todas contra todas, en C);
3. el nombre de un lado es la sigla o la abreviatura del nombre del otro (en los dos sentidos).

``generar_candidatos`` junta las tres vías, puntúa cada par con el ``Comparador`` y lo expande a las grafías
y a los registros que comparten esa superficie. Aquí no se decide nada sobre el vínculo: eso es de
``clasificar``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from rapidfuzz import process as rf_process
from rapidfuzz.distance import JaroWinkler

from record_linkage.flujo.vinculacion.comparar import es_abreviatura, fichas_abreviatura
from record_linkage.flujo.vinculacion.constantes import CONECTORES

if TYPE_CHECKING:
    from pandas.api.extensions import ExtensionArray

    from record_linkage.flujo.vinculacion.comparar import Comparador
    from record_linkage.flujo.vinculacion.config import ConfigVinculacion
    from record_linkage.flujo.vinculacion.normalizar import Normalizador

    #: Superficies únicas de un lado: ``pd.unique`` da un ``ndarray`` (pandas 2) o un arreglo de cadenas (pandas 3).
    _Unicas = np.ndarray | ExtensionArray

__all__ = ["claves_borrosas", "generar_candidatos", "superficies_destino"]

#: Pares de posiciones ``(superficie de la ancla, superficie del destino)`` en las listas de únicas.
_Pares = set[tuple[int, int]]

#: Parecido mínimo (Jaro-Winkler) para que dos nombres sean candidatos solo por cómo se escriben.
_JARO_WINKLER_MINIMO = 0.85

#: Una sigla se busca solo si tiene hasta este número de fichas y alguna de ellas es corta.
_FICHAS_MAXIMAS_SIGLA = 5
_LARGO_FICHA_CORTA = 4

#: (columna de ``prep`` del destino, ``TIPO_SUP_B``): las superficies de un registro destino.
_SUPERFICIES_DESTINO = (
    ("NOMBRE_NORM", "NOMBRE"),
    ("ALIAS_NORM", "ALIAS"),
    ("GRUPO_NORM", "MATRIZ"),
)


def claves_borrosas(nombre: str, nz: Normalizador) -> set[str]:
    """Palabras distintivas + sus variantes con una letra borrada (SymSpell): capturan erratas de una letra.

    Una palabra útil que no sea genérica y tenga 3 o más letras entra tal cual; si además tiene 4 o más
    letras entran también todas las palabras que resultan de borrarle una letra. Dos nombres comparten
    una palabra «con tolerancia de una letra» cuando comparten alguna de estas claves.
    """
    out: set[str] = set()
    for t in nz.utiles(nombre):
        if t in nz.genericos or len(t) < 3:
            continue
        out.add(t)
        if len(t) >= 4:
            out.update(t[:i] + t[i + 1 :] for i in range(len(t)))
    return out


def superficies_destino(prep: pd.DataFrame) -> pd.DataFrame:
    """Una fila por superficie no vacía de cada registro destino: ``pos``, ``SUP_B`` y ``TIPO_SUP_B``.

    ``pos`` es la posición del registro en ``prep``. Las superficies son el nombre normalizado
    (``NOMBRE``), el alias entre paréntesis (``ALIAS``) y la matriz reportada (``MATRIZ``), en ese orden
    por bloques: primero todos los nombres, luego todos los alias, luego todas las matrices.
    """
    piezas = [
        pd.DataFrame({"pos": np.arange(len(prep)), "SUP_B": prep[c].to_numpy(), "TIPO_SUP_B": t})
        for c, t in _SUPERFICIES_DESTINO
    ]
    s = pd.concat(piezas, ignore_index=True)
    return s[s["SUP_B"] != ""].reset_index(drop=True)


def _superficies_ancla(graf: pd.DataFrame) -> pd.DataFrame:
    """Una fila por superficie no vacía de cada grafía de la ancla: ``g``, ``SUP_A`` y ``TIPO_SUP_A``."""
    sa = pd.concat(
        [
            pd.DataFrame(
                {
                    "g": np.arange(len(graf)),
                    "SUP_A": graf["NOMBRE_NORM"].to_numpy(),
                    "TIPO_SUP_A": "NOMBRE",
                }
            ),
            pd.DataFrame(
                {
                    "g": np.arange(len(graf)),
                    "SUP_A": graf["ALIAS_NORM"].to_numpy(),
                    "TIPO_SUP_A": "ALIAS",
                }
            ),
        ],
        ignore_index=True,
    )
    return sa[sa["SUP_A"] != ""].reset_index(drop=True)


def _pares_por_palabra(ua: _Unicas, ub: _Unicas, nz: Normalizador) -> _Pares:
    """Vía 1: pares que comparten una palabra distintiva, con una letra de tolerancia (índice invertido)."""
    inv: dict[str, set[int]] = defaultdict(set)
    for j, s in enumerate(ub):
        for k in claves_borrosas(s, nz):
            inv[k].add(j)
    pares: _Pares = set()
    for i, s in enumerate(ua):
        js: set[int] = set()
        for k in claves_borrosas(s, nz):
            js |= inv.get(k, set())
        pares.update((i, j) for j in js)
    return pares


def _pares_por_cadena(ua: _Unicas, ub: _Unicas) -> _Pares:
    """Vía 2: pares con Jaro-Winkler ≥ 0,85, todas las superficies contra todas (rapidfuzz, en C)."""
    if not (len(ua) and len(ub)):
        return set()
    m = rf_process.cdist(
        list(ua),
        list(ub),
        scorer=JaroWinkler.normalized_similarity,
        score_cutoff=_JARO_WINKLER_MINIMO,
        dtype=np.float32,
        workers=-1,
    )
    ii, jj = np.nonzero(m >= _JARO_WINKLER_MINIMO)
    return set(zip(ii.tolist(), jj.tolist(), strict=True))


def _fichas_por_inicial(
    unicas: _Unicas, nz: Normalizador
) -> tuple[dict[str, list[int]], list[list[str]]]:
    """Fichas de cada superficie (sin formas legales ni conectores) e índice por inicial.

    Devuelve ``(primeras, fichas)``: ``fichas[k]`` son las fichas de la superficie ``k`` y
    ``primeras[letra]`` las posiciones de las superficies cuya primera ficha empieza por esa letra.
    """
    primeras: dict[str, list[int]] = defaultdict(list)
    fichas: list[list[str]] = []
    for k, s in enumerate(unicas):
        f = [t for t in s.split() if t not in nz.legales and t not in CONECTORES]
        fichas.append(f)
        if f:
            primeras[f[0][0]].append(k)
    return primeras, fichas


def _siglas_contra(
    siglas: _Unicas,
    primeras: dict[str, list[int]],
    fichas: list[list[str]],
    nz: Normalizador,
) -> list[tuple[int, int]]:
    """Pares ``(k, m)``: la superficie ``k`` de ``siglas`` abrevia a la superficie ``m`` del otro lado.

    Solo se busca para variantes de hasta 5 fichas, con alguna de 4 letras o menos; el otro lado se toma por
    su primera inicial y ``es_abreviatura`` decide.
    """
    out: list[tuple[int, int]] = []
    for k, s in enumerate(siglas):
        for fa in fichas_abreviatura(s, nz):
            if (
                not fa
                or len(fa) > _FICHAS_MAXIMAS_SIGLA
                or not any(len(t) <= _LARGO_FICHA_CORTA for t in fa)
            ):
                continue
            out.extend(
                (k, m)
                for m in primeras.get(fa[0][0], [])
                if es_abreviatura(fa, fichas[m], nz.genericos)
            )
    return out


def _agregar_pares_por_sigla(pares: _Pares, ua: _Unicas, ub: _Unicas, nz: Normalizador) -> None:
    """Vía 3: agrega a ``pares`` los que son sigla o abreviatura, en los dos sentidos.

    Se busca tanto la sigla del lado de la ancla (``KEV`` contra ``KRONOS EMPRESARIAL VALDORIA`` del destino)
    como la del lado del destino (la ancla trae el nombre largo y el destino la sigla): el motor original solo
    miraba el primer sentido y perdía el par, aunque ``Comparador.rasgos`` reconoce la abreviatura en los dos
    (arreglo C3 de F3.1b).
    """
    primeras_a, fichas_a = _fichas_por_inicial(ua, nz)
    primeras_b, fichas_b = _fichas_por_inicial(ub, nz)
    pares.update(_siglas_contra(ua, primeras_b, fichas_b, nz))
    pares.update((i, j) for j, i in _siglas_contra(ub, primeras_a, fichas_a, nz))


def _expandir(
    sa: pd.DataFrame,
    sb: pd.DataFrame,
    ua: _Unicas,
    ub: _Unicas,
    pares: _Pares,
    comp: Comparador,
) -> pd.DataFrame:
    """Puntúa cada par de superficies únicas y lo expande a las grafías y registros que las comparten."""
    pi = np.array(sorted(pares))
    u = pd.DataFrame({"SUP_A": ua[pi[:, 0]], "SUP_B": ub[pi[:, 1]]})
    u = pd.concat([u, comp.puntajes(u["SUP_A"].to_numpy(), u["SUP_B"].to_numpy())], axis=1)
    x = sa.merge(u, on="SUP_A").merge(sb, on="SUP_B")
    x = x.rename(columns={"pos": "pos_b"})
    return x.reset_index(drop=True)


def generar_candidatos(
    graf: pd.DataFrame,
    prep_b: pd.DataFrame,
    nz: Normalizador,
    comp: Comparador,
    cfg: ConfigVinculacion,
) -> pd.DataFrame:
    """Pares (grafía ancla × registro destino) que merecen clasificarse.

    Exhaustivo en la práctica: se consideran TODOS los pares y se descartan solo los que no
    comparten ninguna palabra distintiva (ni con una errata), no se parecen como cadena
    (Jaro-Winkler < 0,85) y no son una sigla del otro.

    Devuelve una fila por (superficie de la ancla, superficie del destino) con las columnas ``g`` (posición
    de la grafía en ``graf``), ``SUP_A``, ``TIPO_SUP_A``, ``SUP_B``, las cinco de ``Comparador.puntajes``,
    ``pos_b`` (posición del registro en ``prep_b``) y ``TIPO_SUP_B``; o un ``DataFrame`` vacío, sin
    columnas, si ningún par cumple. ``cfg`` no se usa: se conserva en la firma del motor original.
    """
    # Hallazgo F3: ``cfg`` no se usa y el piso de Jaro-Winkler (0,85) está escrito en el código: bajar
    # ``umbral_parecido`` (que mide otro puntaje, ``SCORE_RUES_LINKER``) no amplía los candidatos más allá
    # de lo que ya entra por palabra compartida, por Jaro-Winkler ≥ 0,85 o por sigla.
    sa = _superficies_ancla(graf)
    sb = superficies_destino(prep_b)
    ua = pd.unique(sa["SUP_A"])
    ub = pd.unique(sb["SUP_B"])
    pares = _pares_por_palabra(ua, ub, nz)
    pares |= _pares_por_cadena(ua, ub)
    _agregar_pares_por_sigla(pares, ua, ub, nz)
    if not pares:
        return pd.DataFrame()
    return _expandir(sa, sb, ua, ub, pares, comp)
