"""Deduplicación interna de cada base: qué filas son la misma entidad antes de cruzar.

La ANCLA se agrupa por GRAFÍA (país + nombre tal como viene) y las grafías se unen por reglas
automáticas o se proponen a revisión (``deduplicar_ancla``); un DESTINO se agrupa por la raíz de su
identificador o, sin identificador, por el mismo nombre exacto en los mismos países
(``deduplicar_destino``). En ambos caminos las decisiones humanas ``TIPO=DUPLICADO`` mandan sobre las
reglas: ``MISMA_ENTIDAD`` une y ``DISTINTA`` es un veto.

``componentes`` (union-find determinista) y las familias de forma legal ('SL' ≠ 'S. COOP.') son piezas
comunes de los dos caminos.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.decisiones import clave_ancla, clave_texto
from record_linkage.processing.saneamiento import ascii_mayusculas, sanear_texto

if TYPE_CHECKING:
    from record_linkage.flujo.vinculacion.comparar import Comparador
    from record_linkage.flujo.vinculacion.config import ConfigVinculacion, Fuente
    from record_linkage.flujo.vinculacion.normalizar import Normalizador

__all__ = [
    "FAMILIAS_LEGALES",
    "FAMILIAS_RE",
    "componentes",
    "deduplicar_ancla",
    "deduplicar_destino",
    "familias_compatibles",
    "familias_legales",
    "raiz_id",
]

#: (posición de una grafía/fila, posición de la otra, motivo o regla que las une o propone unir).
_Union = tuple[int, int, str]

# Forma legal (familias): dos formas de familias distintas = personas jurídicas distintas.
# F3.1b (g3-O6): se agregan las formas latinoamericanas (Ltda, Lda, S. en C., E.U., EIRL) y `\b` inicial a LIMITED/LIMITADA
# (antes «Unlimited» caía en LTD e «Ilimitada» en SL, y las formas sin familia eran compatibles con cualquiera).
# El orden de la tabla es la prioridad: la primera familia cuyo patrón aparece en el nombre.
FAMILIAS_LEGALES = [
    ("STIFTUNG_KG", r"STIFTUNG (?:&|\+|UND)? ?CO KG"),
    ("SE_KG", r"\bSE (?:&|\+|UND)? ?CO KG"),
    ("GMBH_KG", r"(?:GMBH|AG) (?:&|\+|UND)? ?CO (?:KG|OHG)"),
    ("KGAA", r"\bKGAA\b"),
    ("OHG", r"\bOHG\b"),
    ("KG", r"\bKG\b"),
    ("GMBH", r"\bGMBH\b|BESCHRA?E?NKTER HAFTUNG"),
    ("AG", r"\bAG\b|AKTIENGESELLSCHAFT"),
    ("SE", r"\bSE\b"),
    ("COOP", r"\bS ?COOP\b|COOPERATIV|\bEG\b|GENOSSENSCHAFT|\bSAT\b"),
    ("SAS", r"\bSASU?\b"),
    (
        "SL",
        r"\bSLU?\b|\bSRLS?\b|\bSARL\b|\bEURL\b|\bKFT\b|\bSP ZOO\b|\bLIMITADA\b|RESPONSABILITA LIMITATA"
        r"|\bLTDA\b|\bLDA\b",
    ),
    ("SCA", r"\bSCA\b"),
    ("SCS", r"\bS EN C\b|\bSCS\b|COMANDITA SIMPLE"),
    ("SA", r"\bSAU?\b|\bSPA\b|ANONIMA|PER AZIONI"),
    ("BV", r"\bBV\b"),
    ("NV", r"\bNV\b"),
    ("LTD", r"\bLTD\b|\bLIMITED\b"),
    ("INC", r"\bINC\b|\bCORP\b"),
    ("LLC", r"\bLLC\b"),
    ("EU", r"\bEU$|\bEIRL\b|UNIPERSONAL"),
]
FAMILIAS_RE = [(k, re.compile(p)) for k, p in FAMILIAS_LEGALES]


# ── Union-find ───────────────────────────────────────────────────────────────
def componentes(n: int, a: Iterable[int], b: Iterable[int]) -> np.ndarray:
    """Componentes conexas numeradas 0..k-1 en orden de primera aparición (determinista)."""
    padre = list(range(n))

    def raiz(x: int) -> int:
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    # strict=False: como el original, un extremo más largo que el otro se recorta en silencio.
    for x, y in zip(a, b, strict=False):
        rx, ry = raiz(int(x)), raiz(int(y))
        if rx != ry:
            padre[max(rx, ry)] = min(rx, ry)
    return pd.factorize(np.array([raiz(i) for i in range(n)]))[0]


# ── Forma legal ──────────────────────────────────────────────────────────────
def _familia_de(texto: str) -> str:
    """Primera familia legal cuyo patrón aparece en ``texto`` ('' si ninguna)."""
    for k, rx in FAMILIAS_RE:
        if rx.search(texto):
            return k
    return ""


def familias_legales(nombres: pd.Series) -> np.ndarray:
    """Familia legal de cada nombre ('' si no se reconoce), tras sanear, quitar paréntesis y puntuación."""
    x = ascii_mayusculas(
        sanear_texto(pd.Series(nombres, dtype="object").fillna("").astype(str).astype("string"))
    )
    x = x.str.replace(r"\([^()]*\)?", " ", regex=True).str.replace(r"[.,;:/\-]", " ", regex=True)
    x = (
        x.str.replace(r"\s+", " ", regex=True)
        .str.replace(r"\b([A-Z]) (?=[A-Z]\b)", r"\1", regex=True)
        .str.strip()
    )
    return np.array([_familia_de(v) for v in x.astype(object).fillna("")], dtype=object)


def familias_compatibles(fa: str, fb: str, estricto: bool = False) -> bool:
    """¿Pueden ser la misma persona jurídica? Familias iguales o una desconocida (``estricto``: solo iguales)."""
    if estricto:
        return fa == fb
    return fa == fb or not fa or not fb


# ── Deduplicación interna ────────────────────────────────────────────────────
def raiz_id(x: str) -> str:
    """Raíz de un BvD ID: sin sufijo de sucursal (DE…-1003) y, en Francia, el SIREN de un SIRET."""
    # Hallazgo F3: corta en el primer «-»: un ID con guion en la raíz ('FDI-F0002') queda en 'FDI' y se fusiona.
    x = str(x).split("-")[0].strip()
    m = re.fullmatch(r"FR(\d{9})\d{5}", x)
    return f"FR{m.group(1)}" if m else x


class _Grafias(NamedTuple):
    """Columnas de ``graf`` que usa la comparación, leídas una sola vez como arreglos."""

    iso: np.ndarray
    norm: np.ndarray
    alias: np.ndarray
    forma: np.ndarray
    trunc: np.ndarray
    util: np.ndarray


def _agrupar_grafias(prep: pd.DataFrame) -> pd.DataFrame:
    """Una fila por GRAFÍA (país + nombre tal como viene), ordenada por su ``CLAVE``, con su forma legal."""
    g = prep.assign(CLAVE=clave_ancla(prep["PAIS_ORIGINAL"], prep["NOMBRE_ORIGINAL"]).to_numpy())
    graf = (
        g.groupby("CLAVE", sort=True)
        .agg(
            PAIS_ORIGINAL=("PAIS_ORIGINAL", "first"),
            NOMBRE_ORIGINAL=("NOMBRE_ORIGINAL", "first"),
            PAIS_ISO3=("PAIS_ISO3", "first"),
            NOMBRE_NORM=("NOMBRE_NORM", "first"),
            ALIAS_NORM=("ALIAS_NORM", "first"),
            TRUNCADO=("TRUNCADO", "max"),
            INTERMEDIARIO=("INTERMEDIARIO", "max"),
            N_FILAS=("ID_REGISTRO", "size"),
        )
        .reset_index()
    )
    graf["FORMA_LEGAL"] = familias_legales(graf["NOMBRE_ORIGINAL"])
    return graf


def _rasgos_nombre_identico() -> dict[str, Any]:
    """Rasgos de dos superficies idénticas: no hace falta alinear."""
    return {
        "SIN_SOBRAS": True,
        "N_COMPARTIDAS": 1,
        "MARCAS_OK": True,
        "COMPACTO_IGUAL": True,
        "SOBRAS_GENERICAS": True,
        "ABREVIATURA": False,
        "ERRATA": False,
        "TRUNCADA": False,
    }


def _forma_distinta(forma_p: str, forma_q: str) -> str:
    """Texto que nombra las dos familias legales que no son compatibles."""
    return f"forma legal distinta ({forma_p or '—'} ≠ {forma_q or '—'})"


def _motivo_de_union(
    x: str,
    y: str,
    por_alias: bool,
    r: dict[str, Any],
    sc: float,
    compatibles: bool,
    forma_p: str,
    forma_q: str,
    cfg: ConfigVinculacion,
) -> tuple[str, bool]:
    """Motivo por el que dos superficies de nombre son la misma entidad, y si la unión es automática.

    Devuelve ``(motivo, automatica)``; un ``motivo`` vacío significa que no hay nada que proponer.
    Es una cadena ``if/elif`` y el ORDEN de las ramas ES la prioridad: decide la primera que se cumple.
    Solo las cuatro primeras pueden ser automáticas, y solo si la forma legal es compatible; el resto
    va a pendientes para que lo decida una persona.
    """
    motivo, auto = "", False
    if x == y and not por_alias:
        auto, motivo = (
            compatibles,
            "mismo nombre normalizado"
            if compatibles
            else f"mismo nombre, {_forma_distinta(forma_p, forma_q)}",
        )
    elif por_alias and (r["SIN_SOBRAS"] or r["COMPACTO_IGUAL"]):
        motivo = "coincide con el nombre entre paréntesis (¿marca?)"
    elif r["COMPACTO_IGUAL"]:
        if r["MARCAS_OK"]:
            auto, motivo = (
                compatibles,
                "mismas letras, otros espacios"
                if compatibles
                else "mismas letras, forma legal distinta",
            )
        else:
            # F3.1b (B1): el original no miraba MARCAS_OK y 'J MARTINEZ' = 'A MARTINEZ' (o 'KRAMENTO 2' = '… 3') se unían
            # solas. Ahora las iniciales o los números distintos mandan el par a pendientes: lo decide una persona.
            motivo = "mismas letras, pero difieren las iniciales o los números (¿otra persona o sucursal?)"
    elif (
        r["SIN_SOBRAS"]
        and r["N_COMPARTIDAS"]
        and r["MARCAS_OK"]
        and (sc >= cfg.umbral_dedup or (r["TRUNCADA"] and not r["ERRATA"] and sc >= 0.75))
    ):
        auto = compatibles
        tipo = "truncamiento" if r["TRUNCADA"] else "errata"
        motivo = (
            f"mismas palabras con {tipo}"
            if compatibles
            else f"mismas palabras, {_forma_distinta(forma_p, forma_q)}"
        )
    elif r["SIN_SOBRAS"] and r["N_COMPARTIDAS"]:
        motivo = f"mismas palabras (score {sc:.2f})"
    elif r["N_COMPARTIDAS"] and r["SOBRAS_GENERICAS"]:
        motivo = "un nombre contiene al otro; lo que sobra es genérico"
    elif r["ABREVIATURA"]:
        motivo = "posible sigla o abreviatura"
    elif r["N_COMPARTIDAS"] and sc >= 0.75:
        motivo = f"comparten palabra distintiva ({r.get('COMPARTIDAS', '')})"
    elif sc >= cfg.umbral_parecido:
        motivo = f"nombres muy parecidos (score {sc:.2f})"
    return motivo, auto


def _clasificar_par(
    x: str,
    y: str,
    por_alias: bool,
    sc: float,
    p: int,
    q: int,
    datos: _Grafias,
    comp: Comparador,
    cfg: ConfigVinculacion,
) -> tuple[str, bool] | None:
    """Clasifica un par de superficies de las grafías ``p`` y ``q``.

    Devuelve ``(motivo, automatica)``: ``automatica=True`` es una unión sin revisión y ``False`` un
    pendiente para una persona. Devuelve ``None`` si no hay nada que proponer: el par no cumple
    ninguna regla o alguna de las dos grafías no tiene palabras útiles y los nombres no son idénticos.
    """
    if x == y:
        r = _rasgos_nombre_identico()
    else:
        r = comp.rasgos(x, y, bool(datos.trunc[p]), bool(datos.trunc[q]), subs=True)
    compatibles = familias_compatibles(datos.forma[p], datos.forma[q])
    if not (datos.util[p] and datos.util[q]) and x != y:
        return None
    motivo, auto = _motivo_de_union(
        x, y, por_alias, r, sc, compatibles, datos.forma[p], datos.forma[q], cfg
    )
    if not motivo:
        return None
    return motivo, auto


def _comparar_pais(
    idx: np.ndarray, datos: _Grafias, comp: Comparador, cfg: ConfigVinculacion
) -> dict[tuple[int, int], tuple[int, str, str]]:
    """Compara las superficies (nombre y alias) de las grafías ``idx``, todas de un mismo país.

    Devuelve, por par de grafías ``(p, q)`` con ``p < q``, ``(rango, motivo, "auto" | "pend")``. Los
    pares se recorren en el orden de ``np.triu_indices`` y un par solo se reemplaza si llega con un
    rango MENOR (automático antes que pendiente); en la práctica manda la primera superficie visitada.
    Ese orden es el desempate entre superficies. El diccionario conserva la posición del primer par
    visto, y con ella el orden de salida.
    """
    norm, alias = datos.norm, datos.alias
    sa = [(i, norm[i], "N") for i in idx if norm[i]] + [(i, alias[i], "A") for i in idx if alias[i]]
    a_i = np.array([x[0] for x in sa])
    a_s = np.array([x[1] for x in sa], dtype=object)
    a_t = np.array([x[2] for x in sa])
    ii, jj = np.triu_indices(len(sa), k=1)
    keep = a_i[ii] != a_i[jj]
    ii, jj = ii[keep], jj[keep]
    vistos: dict[tuple[int, int], tuple[int, str, str]] = {}
    if not len(ii):
        return vistos
    punt = comp.puntajes(a_s[ii], a_s[jj])
    for k in range(len(ii)):
        p, q = int(a_i[ii[k]]), int(a_i[jj[k]])
        x, y = a_s[ii[k]], a_s[jj[k]]
        por_alias = a_t[ii[k]] == "A" or a_t[jj[k]] == "A"
        sc = float(cast("float", punt["SCORE_RUES_LINKER"].iat[k]))
        veredicto = _clasificar_par(x, y, por_alias, sc, p, q, datos, comp, cfg)
        if veredicto is None:
            continue
        motivo, auto = veredicto
        clave = (min(p, q), max(p, q))
        rango = 0 if auto else 1
        # Hallazgo F3: ``rango < …`` nunca se cumple (nombre↔nombre se visita antes que los alias): gana el primero.
        if clave not in vistos or rango < vistos[clave][0]:
            vistos[clave] = (rango, motivo, "auto" if auto else "pend")
    return vistos


def _comparar_grafias(
    graf: pd.DataFrame, nz: Normalizador, comp: Comparador, cfg: ConfigVinculacion
) -> tuple[list[_Union], list[_Union]]:
    """Recorre los países de ``graf`` y devuelve ``(uniones automáticas, pendientes)`` por reglas de nombre."""
    norm = graf["NOMBRE_NORM"].to_numpy()
    datos = _Grafias(
        iso=graf["PAIS_ISO3"].to_numpy(),
        norm=norm,
        alias=graf["ALIAS_NORM"].to_numpy(),
        forma=graf["FORMA_LEGAL"].to_numpy(),
        trunc=graf["TRUNCADO"].to_numpy(),
        util=np.array([bool(nz.utiles(x)) for x in norm]),
    )
    uniones: list[_Union] = []
    pend: list[_Union] = []
    for pais in pd.unique(datos.iso):
        idx = np.flatnonzero(datos.iso == pais)
        if len(idx) < 2:
            continue
        vistos = _comparar_pais(idx, datos, comp, cfg)
        for (p, q), (_, motivo, tipo) in vistos.items():
            (uniones if tipo == "auto" else pend).append((p, q, motivo))
    return uniones, pend


def _filas_duplicado(dec: pd.DataFrame, clave_fuente: str) -> pd.DataFrame:
    """Decisiones ``TIPO=DUPLICADO`` de una fuente, en el orden de los archivos (el último manda)."""
    d = dec[(dec["TIPO"] == "DUPLICADO") & (dec["FUENTE"] == clave_fuente)] if len(dec) else dec
    return d.sort_values("ORDEN", kind="stable")


def _decisiones_ancla(
    graf: pd.DataFrame, dec: pd.DataFrame, clave_fuente: str
) -> tuple[list[_Union], set[tuple[int, int]], list[tuple[str, str]]]:
    """Aplica las decisiones DUPLICADO de la ancla sobre las grafías de ``graf``.

    Devuelve ``(uniones, vetos, desconocidas)``: ``MISMA_ENTIDAD`` agrega una unión y levanta un veto
    anterior del mismo par; cualquier otra decisión (``DISTINTA``) pone el veto. Las claves que no son
    una grafía de ``graf`` no se aplican y se devuelven en ``desconocidas``.
    """
    # Hallazgo F3: aquí MISMA_ENTIDAD levanta un veto previo (el último manda) y las claves desconocidas se
    # reportan; ``_decisiones_destino`` no hace ninguna de las dos cosas (DISTINTA y luego MISMA deja el veto).
    pos = {k: i for i, k in enumerate(graf["CLAVE"])}
    vetos: set[tuple[int, int]] = set()
    uniones: list[_Union] = []
    desconocidas: list[tuple[str, str]] = []
    r: Any  # fila de itertuples(): sus campos son las columnas de ``dec``
    for r in _filas_duplicado(dec, clave_fuente).itertuples():
        if r.CLAVE_A not in pos or r.CLAVE_B not in pos:
            desconocidas.append((r.CLAVE_A, r.CLAVE_B))
            continue
        p, q = sorted((pos[r.CLAVE_A], pos[r.CLAVE_B]))
        if r.DECISION == "MISMA_ENTIDAD":
            uniones.append((p, q, f"decisión: {r.AUTOR or r.ARCHIVO}"))
            vetos.discard((p, q))
        else:
            vetos.add((p, q))
    return uniones, vetos, desconocidas


def _filtrar_pendientes(
    pend: list[_Union], etiquetas: np.ndarray, vetos: set[tuple[int, int]]
) -> pd.DataFrame:
    """Pendientes que siguen en pie: los que ya quedaron en la misma empresa o fueron vetados se descartan."""
    pe = pd.DataFrame(pend, columns=["pos_a", "pos_b", "MOTIVO"])
    if len(pe):
        pe = pe[etiquetas[pe.pos_a.to_numpy()] != etiquetas[pe.pos_b.to_numpy()]]
        pe = pe[
            [(min(a, b), max(a, b)) not in vetos for a, b in zip(pe.pos_a, pe.pos_b, strict=True)]
        ].reset_index(drop=True)
    return pe


def deduplicar_ancla(
    prep: pd.DataFrame,
    nz: Normalizador,
    comp: Comparador,
    cfg: ConfigVinculacion,
    dec: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Agrupa las filas de la ancla en empresas. Devuelve (grafias, uniones, pendientes).

    Unidad de trabajo: la GRAFÍA (país + nombre tal como viene). Reglas automáticas,
    todas con la forma legal compatible (SL ≠ S. COOP.):
      1. mismo nombre normalizado en el mismo país;
      2. mismas letras con otros espacios (KRA MENTO = KRAMENTO);
      3. mismas palabras con una errata o un truncamiento y SCORE ≥ umbral_dedup.
    Lo que casi cumple (contención genérica, sigla, subsecuencia, palabra distintiva
    compartida) va a pendientes: lo decide un humano con TIPO=DUPLICADO.

    ``grafias`` trae además la columna ``_COMP`` (la empresa de cada grafía) y deja en
    ``grafias.attrs["vetos"]`` (pares de posiciones separados por una decisión DISTINTA) y en
    ``grafias.attrs["desconocidas"]`` (claves de decisiones DUPLICADO que no son una grafía) lo que
    leen después ``fusionar_por_empresa_comun`` y las invariantes. ``uniones`` tiene las columnas
    ``pos_a, pos_b, REGLA`` y ``pendientes`` ``pos_a, pos_b, MOTIVO``.
    """
    graf = _agrupar_grafias(prep)
    n = len(graf)
    uniones, pend = _comparar_grafias(graf, nz, comp, cfg)
    # Decisiones humanas TIPO=DUPLICADO sobre la ancla
    por_decision, vetos, desconocidas = _decisiones_ancla(graf, dec, cfg.ancla.clave)
    uniones = [
        (p, q, m) for p, q, m in [*uniones, *por_decision] if (min(p, q), max(p, q)) not in vetos
    ]
    etiquetas = componentes(n, [u[0] for u in uniones], [u[1] for u in uniones])
    graf["_COMP"] = etiquetas
    un = pd.DataFrame(uniones, columns=["pos_a", "pos_b", "REGLA"])
    pe = _filtrar_pendientes(pend, etiquetas, vetos)
    graf.attrs["vetos"] = vetos
    graf.attrs["desconocidas"] = desconocidas
    return graf, un, pe


def _uniones_por_identificador(prep: pd.DataFrame) -> list[_Union]:
    """Filas con la misma raíz de identificador: cada una se une a la primera del grupo."""
    raiz = prep["ID_REGISTRO"].map(raiz_id).to_numpy()
    uniones: list[_Union] = []
    for _, idx in pd.Series(np.arange(len(prep))).groupby(raiz, sort=True):
        miembros = list(idx)
        uniones += [(miembros[0], j, "mismo identificador raíz") for j in miembros[1:]]
    return uniones


def _uniones_por_nombre(prep: pd.DataFrame, nz: Normalizador) -> list[_Union]:
    """Sin identificador: filas con el mismo nombre exacto (salvo mayúsculas y espacios) y los mismos países.

    En registros de grupos (fDi Markets) dos nombres PARECIDOS suelen ser matriz y filial
    ('Zelvora' / 'Zelvora Inc'; 'Compagnie Orbelia' / 'Compagnie Orbelia (UK)'): solo se une la
    MISMA cadena. Los grupos sin nombre o sin palabras útiles no se unen.
    """
    crudo = clave_texto(prep["NOMBRE_ORIGINAL"]).str.casefold()
    llave = pd.Series(list(zip(crudo, prep["PAISES_EVIDENCIA"], strict=True)))
    uniones: list[_Union] = []
    for (nom, _), idx in pd.Series(np.arange(len(prep))).groupby(llave, sort=True):
        primero = cast("str", prep["NOMBRE_NORM"].iat[int(idx.iloc[0])])
        if not nom or not nz.utiles(primero):
            continue
        miembros = list(idx)
        uniones += [(miembros[0], j, "mismo nombre exacto y mismo país") for j in miembros[1:]]
    return uniones


def _decisiones_destino(
    prep: pd.DataFrame, dec: pd.DataFrame, clave_fuente: str
) -> tuple[list[_Union], set[tuple[int, int]]]:
    """Aplica las decisiones DUPLICADO de un destino (claves = ``ID_REGISTRO``); devuelve ``(uniones, vetos)``.

    Las claves que no son un ``ID_REGISTRO`` del destino se ignoran en silencio.
    """
    pos = {k: i for i, k in enumerate(prep["ID_REGISTRO"])}
    vetos: set[tuple[int, int]] = set()
    uniones: list[_Union] = []
    r: Any  # fila de itertuples(): sus campos son las columnas de ``dec``
    for r in _filas_duplicado(dec, clave_fuente).itertuples():
        if r.CLAVE_A in pos and r.CLAVE_B in pos:
            p, q = sorted((pos[r.CLAVE_A], pos[r.CLAVE_B]))
            if r.DECISION == "MISMA_ENTIDAD":
                uniones.append((p, q, f"decisión: {r.AUTOR or r.ARCHIVO}"))
            else:
                vetos.add((p, q))
    return uniones, vetos


def deduplicar_destino(
    prep: pd.DataFrame, f: Fuente, nz: Normalizador, dec: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Entidades dentro de una base destino. Con identificador: SOLO por raíz del ID.

    Sin identificador: la MISMA cadena (salvo mayúsculas y espacios) y los mismos países de
    evidencia; en registros de grupos como fDi Markets, 'Zelvora' y 'Zelvora Inc' son matriz y filial.

    Devuelve ``(entidades, uniones)``: ``entidades`` es ``prep`` con las columnas ``ID_ENTIDAD``,
    ``N_EN_ENTIDAD`` y ``FORMA_LEGAL``; ``uniones`` tiene las columnas ``pos_a, pos_b, REGLA``.
    """
    # Hallazgo F3: el docstring original prometía «mismo nombre normalizado … y misma forma legal»; el código compara
    # la cadena original: 'Triventa SA' y 'Triventa S.A.' no se unen, y ``FORMA_LEGAL`` solo se informa.
    n = len(prep)
    forma = familias_legales(prep["NOMBRE_ORIGINAL"])
    uniones = _uniones_por_identificador(prep) if f.col_id else _uniones_por_nombre(prep, nz)
    por_decision, vetos = _decisiones_destino(prep, dec, f.clave)
    uniones = [
        (p, q, m) for p, q, m in [*uniones, *por_decision] if (min(p, q), max(p, q)) not in vetos
    ]
    et = componentes(n, [u[0] for u in uniones], [u[1] for u in uniones])
    out = prep.copy()
    out["ID_ENTIDAD"] = [f"{f.prefijo_id}-E{e + 1:04d}" for e in et]
    out["N_EN_ENTIDAD"] = out.groupby("ID_ENTIDAD")["ID_ENTIDAD"].transform("size").to_numpy()
    out["FORMA_LEGAL"] = forma
    un = pd.DataFrame(uniones, columns=["pos_a", "pos_b", "REGLA"])
    return out, un
