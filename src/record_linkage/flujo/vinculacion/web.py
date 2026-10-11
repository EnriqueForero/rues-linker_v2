"""Propagación por sitio web: un registro destino con el MISMO dominio que un vínculo EMPRESA se propone.

Una empresa suele estar en varias bases con nombres distintos pero con el mismo sitio web. Si un registro
destino ya es EMPRESA de una empresa ancla, los demás registros (de cualquier destino) que comparten su
dominio web se proponen como vínculo de esa misma empresa, con la evidencia combinada de nombre, web, país
y ciudad. ``propagar_por_web`` SUBE los pares que ya existían como candidatos (nunca los baja) y crea los
que no existían, con ``g = -1`` y ``TIPO_SUP_B = "WEB"``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.clasificar import etiqueta_mismo_pais, posicion_columna
from record_linkage.flujo.vinculacion.constantes import ORDEN_BANDA, ORDEN_NIVEL

if TYPE_CHECKING:
    from record_linkage.flujo.vinculacion.comparar import Comparador
    from record_linkage.flujo.vinculacion.normalizar import Normalizador

__all__ = ["propagar_por_web"]

#: Rango de un par: ``(ORDEN_NIVEL, ORDEN_BANDA)``; menor es mejor.
_Rango = tuple[int, int]

#: Clave de un par por empresa: ``(ENT, FUENTE_DESTINO, pos_b)``.
_ClavePar = tuple[int, str, int]

#: Una superficie de la ancla: ``(NOMBRE_NORM, TRUNCADO, PAIS_ISO3, NOMBRE_ORIGINAL, CLAVE)`` de una grafía.
_Superficie = tuple[str, bool, str, str, str]

#: Por destino, las posiciones de los registros que tienen cada dominio web.
_IndiceDominios = dict[str, defaultdict[str, list[int]]]


class _Propuesta(NamedTuple):
    """La mejor lectura de un registro destino contra las grafías de una empresa ancla."""

    rango: _Rango
    nivel: str
    banda: str
    rasgos: dict[str, Any]
    grafia: str  # NOMBRE_ORIGINAL de la grafía ancla elegida
    sup_a: str  # NOMBRE_NORM de la grafía ancla elegida
    mismo_pais: bool | None
    clave: str  # CLAVE de la grafía ancla elegida


class _Vinculo(NamedTuple):
    """Un vínculo EMPRESA con sitio web, desde el que se propaga."""

    ent: int
    fuente: str
    dominio: str
    nombre: str  # NOMBRE_ORIGINAL del registro vinculado
    ciudad: str  # CIUDAD_NORM del registro vinculado


class _Contexto(NamedTuple):
    """Lo que la propagación lee y no cambia."""

    prep: dict
    destinos: list[str]
    nz: Normalizador
    comp: Comparador
    dominio: dict[str, np.ndarray]  # DOMINIO_WEB de cada registro, por destino
    por_dominio: _IndiceDominios
    superficies: defaultdict[int, list[_Superficie]]  # grafías de cada empresa ancla, en orden


@dataclass
class _Cambios:
    """Lo que la propagación modifica: la copia de trabajo de ``L`` y las filas nuevas, en orden de creación."""

    L: pd.DataFrame
    fila_de: dict[_ClavePar, int]
    nuevas: dict[_ClavePar, dict[str, Any]] = field(default_factory=dict)

    def subir(self, k: int, nota: str, propuesta: _Propuesta, motivo: str) -> None:
        """Agrega la ``nota`` a la evidencia del par ``k`` y lo SUBE si la propuesta es mejor; nunca lo baja."""
        L = self.L
        actual = (
            ORDEN_NIVEL.get(cast("str", L["NIVEL_AUTO"].iat[k]), 9),
            ORDEN_BANDA.get(cast("str", L["BANDA_AUTO"].iat[k]), 9),
        )
        ev = cast("str", L["EVIDENCIA_EXTRA"].iat[k])
        if nota not in ev:
            L.iat[k, posicion_columna(L, "EVIDENCIA_EXTRA")] = "; ".join(x for x in (ev, nota) if x)
        if propuesta.rango < actual:
            L.iat[k, posicion_columna(L, "NIVEL_AUTO")] = propuesta.nivel
            L.iat[k, posicion_columna(L, "BANDA_AUTO")] = propuesta.banda
            L.iat[k, posicion_columna(L, "MOTIVO")] = motivo + f" (antes: {L['MOTIVO'].iat[k]})"

    def ya_hay_igual_o_mejor(self, clave: _ClavePar, rango: _Rango) -> bool:
        """¿Ya se creó una fila nueva para ``clave`` con un rango igual o mejor?"""
        previa = self.nuevas.get(clave)
        if previa is None:
            return False
        return (ORDEN_NIVEL[previa["NIVEL_AUTO"]], ORDEN_BANDA[previa["BANDA_AUTO"]]) <= rango


def _indice_de_dominios(
    prep: dict, destinos: list[str]
) -> tuple[dict[str, np.ndarray], _IndiceDominios]:
    """El dominio web de cada registro, por destino, y las posiciones de los registros con cada dominio."""
    dom = {f: prep[f]["DOMINIO_WEB"].to_numpy() for f in destinos}
    idx_dom: _IndiceDominios = {f: defaultdict(list) for f in destinos}
    for f in destinos:
        for p, d in enumerate(dom[f]):
            if d:
                idx_dom[f][d].append(p)
    return dom, idx_dom


def _superficies_por_entidad(
    graf: pd.DataFrame, ent_de_g: np.ndarray
) -> defaultdict[int, list[_Superficie]]:
    """Las grafías de cada empresa ancla (nombre normalizado, truncado, país, nombre original, clave)."""
    sup_ent: defaultdict[int, list[_Superficie]] = defaultdict(list)
    for gi, e in enumerate(ent_de_g):
        sup_ent[int(e)].append(
            (
                cast("str", graf["NOMBRE_NORM"].iat[gi]),
                bool(graf["TRUNCADO"].iat[gi]),
                cast("str", graf["PAIS_ISO3"].iat[gi]),
                cast("str", graf["NOMBRE_ORIGINAL"].iat[gi]),
                cast("str", graf["CLAVE"].iat[gi]),
            )
        )
    return sup_ent


def _nivel_y_banda_web(
    rs: dict[str, Any], mp: bool | None, sobra_gen: bool, misma_ciudad: bool
) -> tuple[str, str]:
    """Lo que propone el sitio web común para un par, según nombre, país y ciudad.

    * EMPRESA B: comparte palabra, mismo país, sin sobrante genérico en el registro nuevo, y las palabras
      están alineadas o la ciudad coincide;
    * GRUPO B: comparte palabra distintiva;
    * GRUPO C: no comparte ninguna (a revisión).

    Un sobrante genérico en el registro nuevo (USA, HOLDING…) delata una filial: GRUPO aunque comparta web y ciudad.
    """
    if (
        rs["N_COMPARTIDAS"]
        and mp
        and not sobra_gen
        and ((rs["SIN_SOBRAS"] and rs["MARCAS_OK"]) or misma_ciudad)
    ):
        return "EMPRESA", "B"
    if rs["N_COMPARTIDAS"]:
        return "GRUPO", "B"
    return "GRUPO", "C"


def _mejor_superficie(
    superficies: list[_Superficie],
    nb: str,
    evid: set[str],
    misma_ciudad: bool,
    ctx: _Contexto,
) -> _Propuesta | None:
    """La mejor lectura del registro (nombre ``nb``, países ``evid``) contra las grafías de la empresa.

    Gana el menor ``(nivel, banda)``; a igual rango, la primera grafía (``<`` estricto).
    """
    mejor: _Propuesta | None = None
    for na, tr, iso, graf_nom, clave in superficies:
        rs = ctx.comp.rasgos(na, nb, tr)
        mp = (iso in evid) if (iso and evid) else None
        # Un sobrante genérico en el registro nuevo (USA, DEUTSCHLAND, HOLDING…) delata una filial: GRUPO
        # aunque comparta web y ciudad (en fDi la ciudad es la de la MATRIZ). Medido: Vantiko USA.
        sobra_gen = any(t in ctx.nz.genericos for t in rs["SOBRA_B"].split())
        nivel, banda = _nivel_y_banda_web(rs, mp, sobra_gen, misma_ciudad)
        rango = (ORDEN_NIVEL[nivel], ORDEN_BANDA[banda])
        if mejor is None or rango < mejor.rango:
            mejor = _Propuesta(rango, nivel, banda, rs, graf_nom, na, mp, clave)
    return mejor


def _textos_web(v: _Vinculo, misma_ciudad: bool, rs: dict[str, Any]) -> tuple[str, str]:
    """``(nota, motivo)``: la nota va a ``EVIDENCIA_EXTRA`` y el motivo a ``MOTIVO``."""
    nota = f"mismo sitio web ({v.dominio}) que {v.fuente} «{v.nombre}»" + (
        " y misma ciudad" if misma_ciudad else ""
    )
    motivo = (
        f"Mismo sitio web ({v.dominio}){' y ciudad' if misma_ciudad else ''} que {v.fuente} «{v.nombre}», "
        f"vinculado como EMPRESA"
        + (
            f"; comparte {rs['COMPARTIDAS']}"
            if rs["COMPARTIDAS"]
            else "; no comparte palabras del nombre"
        )
    )
    return nota, motivo


def _fila_nueva(
    ent: int,
    f: str,
    p: int,
    nb: str,
    propuesta: _Propuesta,
    motivo: str,
    nota: str,
    punt: pd.Series,
) -> dict[str, Any]:
    """La fila de un par que el motor no había propuesto: ``g = -1`` y ``TIPO_SUP_B = "WEB"``."""
    rs = propuesta.rasgos
    return {
        "ENT": ent,
        "FUENTE_DESTINO": f,
        "pos_b": p,
        "g": -1,
        "GRAFIA_ANCLA": propuesta.grafia,
        "CLAVE_GRAFIA": propuesta.clave,
        "SUP_A": propuesta.sup_a,
        "SUP_B": nb,
        "TIPO_SUP_A": "NOMBRE",
        "TIPO_SUP_B": "WEB",
        "NIVEL_AUTO": propuesta.nivel,
        "BANDA_AUTO": propuesta.banda,
        "MOTIVO": motivo,
        "MISMO_PAIS": etiqueta_mismo_pais(propuesta.mismo_pais),
        "COMPARTIDAS": rs["COMPARTIDAS"],
        "SOBRA_EXPORTADOR": rs["SOBRA_A"],
        "SOBRA_DESTINO": rs["SOBRA_B"],
        "EVIDENCIA_EXTRA": nota,
        **cast("dict[str, Any]", punt.to_dict()),
    }


def _registros_con_dominio(v: _Vinculo, pb: int, ctx: _Contexto) -> Iterator[tuple[str, int]]:
    """``(fuente, posición)`` de los demás registros de cualquier destino con el dominio del vínculo."""
    for f in ctx.destinos:
        for p in ctx.por_dominio[f].get(v.dominio, []):
            if f == v.fuente and p == pb:
                continue
            yield f, p


def _proponer_registro(v: _Vinculo, f: str, p: int, ctx: _Contexto, cambios: _Cambios) -> None:
    """Propone el registro ``p`` de ``f`` como vínculo de la empresa de ``v``: sube el par o crea uno nuevo."""
    nb = ctx.prep[f]["NOMBRE_NORM"].iat[p]
    evid = set(ctx.prep[f]["PAISES_EVIDENCIA"].iat[p].split("|")) - {""}
    misma_ciudad = bool(v.ciudad) and v.ciudad == ctx.prep[f]["CIUDAD_NORM"].iat[p]
    mejor = _mejor_superficie(ctx.superficies[v.ent], nb, evid, misma_ciudad, ctx)
    # Una empresa sin grafías (``ent_de_g`` no corresponde a ``L``) deja ``mejor`` en None: TypeError, como en el original.
    rango, _, _, rs, _, na, _, _ = mejor
    nota, motivo = _textos_web(v, misma_ciudad, rs)
    k = cambios.fila_de.get((v.ent, f, p))
    if k is not None:
        cambios.subir(k, nota, mejor, motivo)
        return
    clave_n = (v.ent, f, p)
    if cambios.ya_hay_igual_o_mejor(clave_n, rango):
        return
    punt = ctx.comp.puntajes(np.array([na], dtype=object), np.array([nb], dtype=object)).iloc[0]
    cambios.nuevas[clave_n] = _fila_nueva(v.ent, f, p, nb, mejor, motivo, nota, punt)


def propagar_por_web(
    L: pd.DataFrame,
    graf: pd.DataFrame,
    ent_de_g: np.ndarray,
    prep: dict,
    destinos: list[str],
    nz: Normalizador,
    comp: Comparador,
) -> pd.DataFrame:
    """Registros destino con el MISMO sitio web que un vínculo EMPRESA se proponen (o suben) como vínculo.

    Evidencia combinada (nombre + web + país + ciudad), del registro nuevo contra la empresa ancla:
      * mismo dominio + comparte palabra distintiva + mismo país + misma ciudad → EMPRESA (B);
      * mismo dominio + palabras alineadas + mismo país → EMPRESA (B);
      * mismo dominio + comparte palabra distintiva → GRUPO (B);
      * mismo dominio sin palabra compartida → GRUPO (C, a revisión).
    Si el par ya existía como candidato de menor nivel, se SUBE; nunca se baja.
    Ejemplo: VANTIKO CHOCOLATES SA (Orbis, vantiko.es, Valencia) → FDI «Vantiko Sweets»
    (vantiko.es, Valencia) = EMPRESA.

    ``L`` son los pares por empresa tras ``aplicar_decisiones``; sin ningún vínculo EMPRESA se devuelve
    ``L`` tal cual. Si no, devuelve una copia con ``EVIDENCIA_EXTRA`` (sin nulos), los pares subidos y,
    al final, los pares nuevos (``g = -1``, ``TIPO_SUP_B = "WEB"``) en el orden en que se crearon.
    """
    # Hallazgo F3: se propaga desde CUALQUIER vínculo EMPRESA (también automático B y revisión asistida sin validar),
    # mientras que ``fusionar_por_empresa_comun`` exige confianza ALTA: un vínculo dudoso arrastra a todo su dominio.
    base = L[(L["NIVEL_FINAL"] == "EMPRESA")]
    if base.empty:
        return L
    L = L.copy()
    if "EVIDENCIA_EXTRA" not in L.columns:
        L["EVIDENCIA_EXTRA"] = ""
    L["EVIDENCIA_EXTRA"] = L["EVIDENCIA_EXTRA"].fillna("")
    dom, idx_dom = _indice_de_dominios(prep, destinos)
    # ``strict=False`` como el ``zip`` a secas del original: no se endurece a ``strict=True`` para no cambiar el
    # comportamiento con tablas mal formadas (p. ej., una columna con la etiqueta repetida).
    fila_de = {
        (int(e), f, int(p)): k
        for k, (e, f, p) in enumerate(zip(L["ENT"], L["FUENTE_DESTINO"], L["pos_b"], strict=False))
    }
    ctx = _Contexto(
        prep, destinos, nz, comp, dom, idx_dom, _superficies_por_entidad(graf, ent_de_g)
    )
    cambios = _Cambios(L, fila_de)
    r: Any  # fila de itertuples(): sus campos son las columnas de ``base``
    for r in base.itertuples(index=False):
        fb, pb = r.FUENTE_DESTINO, int(r.pos_b)
        d = dom[fb][pb]
        if not d:
            continue
        v = _Vinculo(
            int(r.ENT), fb, d, prep[fb]["NOMBRE_ORIGINAL"].iat[pb], prep[fb]["CIUDAD_NORM"].iat[pb]
        )
        for f, p in _registros_con_dominio(v, pb, ctx):
            _proponer_registro(v, f, p, ctx, cambios)
    if cambios.nuevas:
        L = pd.concat([L, pd.DataFrame(list(cambios.nuevas.values()))], ignore_index=True)
    return L
