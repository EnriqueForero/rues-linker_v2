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
from rapidfuzz.distance import Levenshtein

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
    # Hallazgo F3: con max_ed=2 una sustitución más una inserción (MIRVANA↔MIRVAXAS) pasa en palabras cortas.
    return len(t) != len(u) or min(len(t), len(u)) >= 8


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
    """
    libres = list(tb)
    sobra_a: list[str] = []
    pares: list[tuple[str, str, str]] = []
    for k, t in enumerate(ta):
        hit: str | None
        hit, tipo = (t, "exacta") if t in libres else (None, "")
        if hit is None and len(t) >= 4 and max_ed > 0:
            cand = sorted(
                (Levenshtein.distance(t, u), u) for u in libres if misma_palabra(t, u, max_ed)
            )
            if cand:
                hit, tipo = cand[0][1], "errata"
        if hit is None and trunc_a and k == len(ta) - 1 and len(t) >= 2:
            cand_u = [u for u in libres if u.startswith(t) and u != t]
            if cand_u:
                hit, tipo = cand_u[0], "truncada"
        if hit is None and trunc_b and len(t) >= 3:
            cand_u = [u for u in libres if t.startswith(u) and u == tb[-1] and len(u) >= 2]
            if cand_u:
                hit, tipo = cand_u[0], "truncada"
        if hit is None:
            sobra_a.append(t)
        else:
            libres.remove(hit)
            pares.append((t, hit, tipo))
    sobra_b = libres
    # Pérdida por truncamiento: si la ÚLTIMA palabra del nombre truncado quedó emparejada, lo
    # que al otro le sobra DESPUÉS de esa posición se perdió en el corte (no es diferencia).
    # Hallazgo F3: ``index`` toma la PRIMERA aparición: con palabras repetidas el corte se mide en otra posición.
    if trunc_a and sobra_b and ta and pares and pares[-1][0] == ta[-1]:
        corte = tb.index(pares[-1][1])
        sobra_b = [u for u in sobra_b if tb.index(u) < corte]
    if trunc_b and sobra_a and tb and pares:
        ult_b = [p for p in pares if p[1] == tb[-1]]
        if ult_b:
            corte = ta.index(ult_b[0][0])
            sobra_a = [u for u in sobra_a if ta.index(u) < corte]
    return sobra_a, sobra_b, pares


def es_subsecuencia(corta: str, larga: str) -> bool:
    """'PLVK' ⊂ 'PELUVIKA', 'GRDL' ⊂ 'GARANDEL': abreviaturas de aduana que quitan vocales."""
    if len(corta) < 3 or len(corta) >= len(larga) or corta[0] != larga[0]:
        return False
    it = iter(larga)
    return all(c in it for c in corta)


def _cubre(
    a: list[str],
    b: list[str],
    genericos: frozenset[str],
    subsecuencia: bool,
    i: int,
    j: int,
    abrevio: bool,
    distintiva: bool,
) -> bool:
    """¿Las palabras ``a[i:]`` abrevian ``b[j:]``? Búsqueda recursiva de ``es_abreviatura``.

    ``abrevio`` y ``distintiva`` acumulan lo ya visto: si alguna palabra se acortó de verdad y si
    alguna de las cubiertas no es genérica. Las cuatro salidas se prueban en este orden: prefijo,
    subsecuencia (solo con ``subsecuencia``), sigla de varias palabras y saltar una palabra de ``b``.
    """
    # Hallazgo F3: sin memoización; el costo crece ~16 veces por cada dos palabras más (12 contra 24 ≈ 4 s).
    if i == len(a):
        return abrevio and distintiva
    if j >= len(b):
        return False
    t = a[i]
    if b[j].startswith(t) and _cubre(
        a,
        b,
        genericos,
        subsecuencia,
        i + 1,
        j + 1,
        abrevio or len(t) < len(b[j]),
        distintiva or (len(t) < len(b[j]) and b[j] not in genericos),
    ):
        return True
    if (
        subsecuencia
        and es_subsecuencia(t, b[j])
        and _cubre(
            a, b, genericos, subsecuencia, i + 1, j + 1, True, distintiva or b[j] not in genericos
        )
    ):
        return True
    n = len(t)
    if 2 <= n <= len(b) - j and all(b[j + k][0] == t[k] for k in range(n)):
        if _cubre(
            a,
            b,
            genericos,
            subsecuencia,
            i + 1,
            j + n,
            True,
            distintiva or any(x not in genericos for x in b[j : j + n]),
        ):
            return True
    return i > 0 and _cubre(a, b, genericos, subsecuencia, i, j + 1, abrevio, distintiva)


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
    return (len(a) >= 2 or len(a[0]) >= 3) and _cubre(
        a, b, genericos, subsecuencia, 0, 0, False, False
    )


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
