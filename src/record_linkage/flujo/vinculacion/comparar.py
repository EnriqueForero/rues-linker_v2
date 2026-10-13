"""Comparación de nombres de empresa: alineación palabra a palabra, siglas y rasgos de un par.

``Comparador.rasgos`` resume en un diccionario cómo se parecen dos nombres ya normalizados: qué palabras
comparten, cuáles sobran de cada lado, si la diferencia es una errata o un truncamiento del origen,
si coinciden las marcas (iniciales sueltas y números) y si uno es la sigla del otro. Con esos rasgos el
motor decide si un par es la misma empresa, el mismo grupo o solo un candidato a revisión.
``Comparador.puntajes`` agrega el ``SCORE_RUES_LINKER`` de la librería, vectorizado sobre pares.

Las funciones de este módulo son puras; el único estado es la memoización de ``Comparador.rasgos``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from rapidfuzz.distance import Indel, Levenshtein

from record_linkage.flujo.vinculacion.constantes import CONECTORES
from record_linkage.matching.genericos import GENERICOS_ESTRUCTURALES, GENERICOS_GEOGRAFIA
from record_linkage.matching.idf import construir_idf
from record_linkage.matching.nombre_idf import SimilitudNombre, neutralizar_genericos

if TYPE_CHECKING:
    from record_linkage.flujo.vinculacion.config import ConfigVinculacion
    from record_linkage.flujo.vinculacion.normalizar import Normalizador

__all__ = [
    "COLUMNAS_PUNTAJES",
    "Comparador",
    "alinear",
    "es_abreviatura",
    "es_subsecuencia",
    "fichas_abreviatura",
    "misma_palabra",
]

#: Columnas que devuelve ``Comparador.puntajes`` (también cuando no hay pares).
COLUMNAS_PUNTAJES = [
    "SCORE_RUES_LINKER",
    "SIM_TOKENS_IDF",
    "JARO_WINKLER",
    "DIF_INFORMATIVA",
    "USO_CONTENCION",
]


def misma_palabra(t: str, u: str, max_ed: int) -> bool:
    """Errata admitida: ≤ max_ed ediciones; una SUSTITUCIÓN solo en palabras de ≥ 8 letras.

    Medido en el piloto: Jaro-Winkler ≥ 0,92 unía ZORMETTI con ZOTTI; una sustitución
    corta unía TORBAL con TORGAL. Inserción/supresión sí: VELSAER/VELSAR, TORDEN/TORDENS.
    """
    if t == u:
        return True
    if max_ed <= 0 or min(len(t), len(u)) < 4 or Levenshtein.distance(t, u) > max_ed:
        return False
    if min(len(t), len(u)) >= 8:
        return True
    # En palabras cortas solo se admiten inserciones y supresiones (B8 de F3.1b): la distancia con sustituciones
    # (Levenshtein) debe valer lo mismo que sin ellas (Indel, que cuenta una sustitución como dos operaciones).
    # El original aceptaba cualquier palabra de largo distinto y con max_ed=2 dejaba pasar una sustitución más una
    # inserción (MIRVANA↔MIRVAXAS); con max_ed=1 el resultado es el mismo de siempre.
    return Indel.distance(t, u) == Levenshtein.distance(t, u)


def _pareja_de(
    t: str,
    k: int,
    ta: list[str],
    tb: list[str],
    libres: list[int],
    max_ed: int,
    trunc: tuple[bool, bool],
) -> tuple[int | None, str]:
    """La posición en ``tb`` (entre las ``libres``) que empareja a la palabra ``t`` (la ``k``-ésima de ``ta``).

    Prueba, en este orden: la palabra exacta, la errata (la de menor distancia), la truncada de ``a`` (la
    última palabra de ``a`` es prefijo de otra) y la truncada de ``b`` (la última de ``b`` es prefijo de ``t``).
    Devuelve ``(posición, tipo)``, o ``(None, "")`` si no hay pareja. Siempre se elige la primera posición libre
    con esa palabra.
    """
    trunc_a, trunc_b = trunc
    exacta = [j for j in libres if tb[j] == t]
    if exacta:
        return exacta[0], "exacta"
    if len(t) >= 4 and max_ed > 0:
        cand = sorted(
            (Levenshtein.distance(t, tb[j]), tb[j], j)
            for j in libres
            if misma_palabra(t, tb[j], max_ed)
        )
        if cand:
            return cand[0][2], "errata"
    if trunc_a and k == len(ta) - 1 and len(t) >= 2:
        truncadas = [j for j in libres if tb[j].startswith(t) and tb[j] != t]
        if truncadas:
            return truncadas[0], "truncada"
    ultima = len(tb) - 1
    if (
        trunc_b
        and len(t) >= 3
        and ultima in libres
        and len(tb[ultima]) >= 2
        and t.startswith(tb[ultima])
    ):
        return ultima, "truncada"
    return None, ""


def alinear(
    ta: list[str],
    tb: list[str],
    max_ed: int,
    trunc_a: bool = False,
    trunc_b: bool = False,
) -> tuple[list[str], list[str], list[tuple[str, str, str]]]:
    """Empareja palabra a palabra dos listas de palabras útiles.

    Returns:
        (sobra_a, sobra_b, pares) con pares = [(palabra_a, palabra_b, tipo)] y tipo en
        {"exacta", "errata", "truncada"}. Un nombre truncado admite que su ÚLTIMA palabra
        sea prefijo de una palabra del otro, y que al otro le sobren palabras DESPUÉS
        de la última emparejada (se perdieron en el corte).

    El corte se mide por POSICIÓN (B3 de F3.1b): con palabras repetidas el original usaba ``index()``, que toma
    la primera aparición, y daba por diferencia lo que en realidad se había perdido en el corte.
    """
    libres = list(range(len(tb)))
    sobra_a: list[int] = []
    pares: list[tuple[int, int, str]] = []  # (posición en ta, posición en tb, tipo)
    for k, t in enumerate(ta):
        j, tipo = _pareja_de(t, k, ta, tb, libres, max_ed, (trunc_a, trunc_b))
        if j is None:
            sobra_a.append(k)
        else:
            libres.remove(j)
            pares.append((k, j, tipo))
    sobra_b = libres
    # Pérdida por truncamiento: si la ÚLTIMA palabra del nombre truncado quedó emparejada, lo
    # que al otro le sobra DESPUÉS de esa posición se perdió en el corte (no es diferencia).
    if trunc_a and sobra_b and ta and pares and pares[-1][0] == len(ta) - 1:
        corte = pares[-1][1]
        sobra_b = [j for j in sobra_b if j < corte]
    if trunc_b and sobra_a and tb and pares:
        ult_b = [kj for kj in pares if kj[1] == len(tb) - 1]
        if ult_b:
            corte = ult_b[0][0]
            sobra_a = [k for k in sobra_a if k < corte]
    return (
        [ta[k] for k in sobra_a],
        [tb[j] for j in sobra_b],
        [(ta[k], tb[j], tipo) for k, j, tipo in pares],
    )


def es_subsecuencia(corta: str, larga: str) -> bool:
    """'PLVK' ⊂ 'PELUVIKA', 'GRDL' ⊂ 'GARANDEL': abreviaturas de aduana que quitan vocales."""
    if len(corta) < 3 or len(corta) >= len(larga) or corta[0] != larga[0]:
        return False
    it = iter(larga)
    return all(c in it for c in corta)


#: Un estado de la búsqueda de ``es_abreviatura``: ``(i, j, abrevio, distintiva)``.
_Estado = tuple[int, int, bool, bool]


def _pasos(
    a: list[str], b: list[str], genericos: frozenset[str], subsecuencia: bool, estado: _Estado
) -> list[_Estado]:
    """Los estados a los que se llega desde ``estado`` cubriendo ``a[i]`` con ``b[j:]``.

    Las cuatro salidas, en este orden: prefijo, subsecuencia (solo con ``subsecuencia``), sigla de varias
    palabras consecutivas de ``b`` y saltar una palabra de ``b`` (esta última no para la primera de ``a``).
    """
    i, j, abrevio, distintiva = estado
    t, w = a[i], b[j]
    salida: list[_Estado] = []
    if w.startswith(t):
        corta = len(t) < len(w)
        salida.append(
            (i + 1, j + 1, abrevio or corta, distintiva or (corta and w not in genericos))
        )
    if subsecuencia and es_subsecuencia(t, w):
        salida.append((i + 1, j + 1, True, distintiva or w not in genericos))
    n = len(t)
    if 2 <= n <= len(b) - j and all(b[j + k][0] == t[k] for k in range(n)):
        salida.append(
            (i + 1, j + n, True, distintiva or any(x not in genericos for x in b[j : j + n]))
        )
    if i > 0:
        salida.append((i, j + 1, abrevio, distintiva))
    return salida


def _cubre(a: list[str], b: list[str], genericos: frozenset[str], subsecuencia: bool) -> bool:
    """¿Las palabras de ``a`` abrevian las de ``b``? Búsqueda de ``es_abreviatura``, sin recursión.

    Cada palabra de ``a``, en orden, debe cubrir una o varias de ``b``. El estado es ``(i, j, abrevio,
    distintiva)``: la posición en cada lista y lo ya visto (si alguna palabra se acortó de verdad y si alguna
    de las cubiertas no es genérica). Se acepta al terminar ``a`` con las dos banderas encendidas. Como el
    resultado solo es «¿se alcanza un estado de aceptación?», cada estado se visita una vez: el costo es
    polinómico (el original recursivo era exponencial con palabras repetidas) y no hay límite de pila.
    """
    inicio: _Estado = (0, 0, False, False)
    vistos = {inicio}
    pendientes = [inicio]
    while pendientes:
        estado = pendientes.pop()
        i, j, abrevio, distintiva = estado
        if i == len(a):
            if abrevio and distintiva:
                return True
            continue
        if j >= len(b):
            continue
        for siguiente in _pasos(a, b, genericos, subsecuencia, estado):
            if siguiente not in vistos:
                vistos.add(siguiente)
                pendientes.append(siguiente)
    return False


def es_abreviatura(
    a: list[str], b: list[str], genericos: frozenset[str], subsecuencia: bool = False
) -> bool:
    """¿Las palabras de ``a`` son una abreviatura de ``b``? Solo alimenta la revisión.

    Cada palabra de ``a``, en orden, es prefijo de una palabra de ``b``, o la sigla de
    varias palabras consecutivas de ``b``, o una subsecuencia de consonantes; ancla en la
    primera palabra de ``b`` y al menos una abreviatura cubre una palabra distintiva.
    'KR EMP V ALIMENTACION' → KRONOS EMPRESARIAL VALDORIA ALIMENTACION; 'KEV' → K·E·V, las iniciales de
    KRONOS EMPRESARIAL VALDORIA.
    """
    if not a or not b or a[0][0] != b[0][0]:
        return False
    return (len(a) >= 2 or len(a[0]) >= 3) and _cubre(a, b, genericos, subsecuencia)


def fichas_abreviatura(nombre: str, nz: Normalizador) -> list[list[str]]:
    """Variantes de un nombre para buscar siglas: sin formas legales ni conectores, sin letra final
    suelta (fragmento de S.A./S.L. truncado) y, si la última palabra trae la forma legal pegada
    ('KEVSAU' = K·E·V + SAU), también sin ella."""
    t = [x for x in nombre.split() if x not in nz.legales and x not in CONECTORES]
    while len(t) > 1 and len(t[-1]) == 1:
        t = t[:-1]
    if not t:
        return []
    variantes = [t]
    ult = t[-1]
    for leg in sorted(nz.legales, key=lambda x: (-len(x), x)):
        if len(leg) >= 2 and ult.endswith(leg) and len(ult) - len(leg) >= 2:
            variantes.append([*t[:-1], ult[: -len(leg)]])
            break
    return variantes


def _pares_compartidos(
    pares: list[tuple[str, str, str]], genericos: frozenset[str]
) -> list[tuple[str, str]]:
    """Pares alineados cuyas dos palabras son distintivas (no genéricas) y de ≥ 3 letras."""
    return [
        (x, y)
        for x, y, _ in pares
        if x not in genericos and y not in genericos and len(x) >= 3 and len(y) >= 3
    ]


def _pares_truncados(
    pares: list[tuple[str, str, str]], genericos: frozenset[str]
) -> list[tuple[str, str]]:
    """Pares emparejados por truncamiento cuya palabra del otro lado no es genérica."""
    return [(x, y) for x, y, tp in pares if tp == "truncada" and y not in genericos]


def _pares_fuertes(
    pares: list[tuple[str, str, str]], genericos: frozenset[str], nombres_pila: frozenset[str]
) -> list[tuple[str, str]]:
    """Pares EXACTOS de una palabra distintiva de ≥ 3 letras que no es un nombre de pila."""
    return [
        (x, y)
        for x, y, tp in pares
        if tp == "exacta" and x not in genericos and len(x) >= 3 and x not in nombres_pila
    ]


def _rasgos_de_alineacion(
    ua: list[str],
    ub: list[str],
    sa: list[str],
    sb: list[str],
    pares: list[tuple[str, str, str]],
    genericos: frozenset[str],
    nombres_pila: frozenset[str],
) -> dict[str, Any]:
    """Los rasgos que salen de la alineación: sobrantes, palabras compartidas y tipo de diferencia."""
    comp = _pares_compartidos(pares, genericos)
    comp_trunc = _pares_truncados(pares, genericos)
    fuertes = _pares_fuertes(pares, genericos, nombres_pila)
    tipos = {tp for _, _, tp in pares}
    return {
        "UTILES_A": len(ua),
        "UTILES_B": len(ub),
        "SOBRA_A": " ".join(sa),
        "SOBRA_B": " ".join(sb),
        "COMPARTIDAS": " ".join(sorted({y for _, y in comp})),
        "N_COMPARTIDAS": len(comp) + (len(comp_trunc) if not comp else 0),
        "N_FUERTES": len(fuertes),
        "SOLO_NOMBRE_PILA": bool(comp) and all(x in nombres_pila for x, _ in comp),
        "SOBRAS_GENERICAS": all(t in genericos for t in sa + sb),
        "SIN_SOBRAS": not sa and not sb,
        "ERRATA": "errata" in tipos,
        "TRUNCADA": "truncada" in tipos,
    }


def _rasgos_de_forma(
    nz: Normalizador, a: str, b: str, trunc_a: bool, trunc_b: bool
) -> dict[str, Any]:
    """Los rasgos que no dependen de la alineación: marcas (iniciales y números) y letras compactas."""
    return {
        "MARCAS_OK": nz.marcas(a, trunc_a) == nz.marcas(b, trunc_b),
        "COMPACTO_IGUAL": bool(nz.compacto(a)) and nz.compacto(a) == nz.compacto(b),
    }


def _hay_abreviatura(nz: Normalizador, a: str, b: str, subs: bool) -> bool:
    """¿Alguna variante de un nombre es sigla o abreviatura de alguna variante del otro (en cualquier sentido)?"""
    va, vb = fichas_abreviatura(a, nz), fichas_abreviatura(b, nz)
    return any(
        es_abreviatura(x, y, nz.genericos, subs) or es_abreviatura(y, x, nz.genericos, subs)
        for x in va
        for y in vb
    )


def _frecuencia_documental(vocab: pd.Series) -> dict[str, int]:
    """En cuántos nombres del vocabulario aparece cada palabra."""
    df_tok: dict[str, int] = defaultdict(int)
    for n in vocab:
        for t in set(n.split()):
            df_tok[t] += 1
    return dict(df_tok)


class Comparador:
    """Rasgos de un par de nombres: alineación, palabras compartidas, puntaje de la librería."""

    def __init__(self, nz: Normalizador, corpus: Iterable[str], cfg: ConfigVinculacion) -> None:
        self.nz, self.cfg = nz, cfg
        vocab = pd.Series(sorted(set(corpus) - {""}))
        pesos = construir_idf(vocab.reset_index(drop=True))
        pesos = neutralizar_genericos(pesos, nz.genericos, peso=1.0)
        genericos_ruido = frozenset(
            set(GENERICOS_ESTRUCTURALES) | set(GENERICOS_GEOGRAFIA) | set(cfg.genericos_extra)
        )
        # Comparador de la librería con sus defectos (los de deduplicar_importadores).
        self.lib = SimilitudNombre(pesos, vocab.to_numpy(), genericos_estructurales=genericos_ruido)
        self.vocab = set(vocab)
        # Frecuencia documental de cada palabra (para decidir si una palabra sola es "común").
        self.df_tok = _frecuencia_documental(vocab)
        self._cache: dict[tuple[str, str, bool, bool, bool], dict[str, Any]] = {}

    def puntajes(self, a: np.ndarray, b: np.ndarray) -> pd.DataFrame:
        """SCORE_RUES_LINKER (0–1) y su desglose, vectorizado sobre pares."""
        if len(a) == 0:
            return pd.DataFrame(columns=COLUMNAS_PUNTAJES)
        d = self.lib.partes(np.asarray(a, dtype=object), np.asarray(b, dtype=object))
        score = (
            self.lib.compare(np.asarray(a, dtype=object), np.asarray(b, dtype=object)) + 1.0
        ) / 2.0
        return pd.DataFrame(
            {
                "SCORE_RUES_LINKER": np.round(score, 4),
                "SIM_TOKENS_IDF": np.round(d["sim_tokens"].to_numpy(), 4),
                "JARO_WINKLER": np.round(d["jaro_winkler"].to_numpy(), 4),
                "DIF_INFORMATIVA": d["dif_informativa"].to_numpy(),
                "USO_CONTENCION": d["uso_contencion"].to_numpy(),
            }
        )

    def rasgos(
        self, a: str, b: str, trunc_a: bool, trunc_b: bool = False, subs: bool = False
    ) -> dict[str, Any]:
        """Rasgos del par ``(a, b)`` de nombres normalizados; memoizados por ``(a, b, trunc_a, trunc_b, subs)``.

        ``trunc_a`` / ``trunc_b`` dicen que el nombre pudo cortarse en el origen (aduana: 35 letras) y
        ``subs`` activa la subsecuencia de consonantes al buscar siglas. Claves del diccionario, en orden:
        ``UTILES_A``, ``UTILES_B``, ``SOBRA_A``, ``SOBRA_B``, ``COMPARTIDAS``, ``N_COMPARTIDAS``,
        ``N_FUERTES``, ``SOLO_NOMBRE_PILA``, ``SOBRAS_GENERICAS``, ``SIN_SOBRAS``, ``ERRATA``,
        ``TRUNCADA``, ``MARCAS_OK``, ``COMPACTO_IGUAL`` y ``ABREVIATURA``. Devuelve el MISMO objeto que
        guarda la caché: quien lo reciba no debe modificarlo.
        """
        clave = (a, b, trunc_a, trunc_b, subs)
        if clave in self._cache:
            return self._cache[clave]
        nz = self.nz
        ua, ub = nz.utiles(a), nz.utiles(b)
        sa, sb, pares = alinear(ua, ub, self.cfg.max_ediciones_palabra, trunc_a, trunc_b)
        r = _rasgos_de_alineacion(ua, ub, sa, sb, pares, nz.genericos, self.cfg.nombres_pila)
        r.update(_rasgos_de_forma(nz, a, b, trunc_a, trunc_b))
        r["ABREVIATURA"] = False
        if not r["N_COMPARTIDAS"] and not r["COMPACTO_IGUAL"]:
            r["ABREVIATURA"] = _hay_abreviatura(nz, a, b, subs)
        self._cache[clave] = r
        return r

    def palabra_comun(self, nombre: str) -> bool:
        """Nombre de UNA sola palabra útil que además aparece en varios nombres del corpus."""
        u = self.nz.utiles(nombre)
        return len(u) == 1 and self.df_tok.get(u[0], 0) >= 3
