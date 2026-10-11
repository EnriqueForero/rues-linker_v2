"""Los pares empresa × registro destino con los datos que se muestran, y el vínculo principal de cada empresa.

``enriquecer_pares`` pone en cada par los datos del registro destino y de la empresa ancla, y puntúa los
pares que solo existen por una decisión de revisión. ``vinculos_principales`` separa los pares vinculados
(EMPRESA o GRUPO) y marca UNO como principal por (empresa, destino); ``marcar_principales`` traslada esa
marca a la tabla completa de pares. ``duplicados_pendientes`` y ``reglas_de_union`` leen los resultados de
la deduplicación de la ancla para señalarlos en las hojas.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.constantes import ORDEN_NIVEL

if TYPE_CHECKING:
    from record_linkage.flujo.vinculacion.comparar import Comparador
    from record_linkage.flujo.vinculacion.config import ConfigVinculacion
    from record_linkage.flujo.vinculacion.empresas import Empresas

__all__ = [
    "duplicados_pendientes",
    "enriquecer_pares",
    "marcar_principales",
    "reglas_de_union",
    "vinculos_principales",
]

#: Columnas del registro destino que se copian a cada par desde ``info_destino``.
_COLUMNAS_DEL_DESTINO = (
    "ID_DESTINO",
    "NOMBRE_DESTINO",
    "PAIS_DESTINO",
    "CIUDAD",
    "WEB",
    "MATRIZ",
    "ID_ENTIDAD",
)

#: Columnas de texto de los pares: si faltan se crean vacías y los ausentes quedan como ``''``.
_COLUMNAS_DE_TEXTO = (
    "MOTIVO",
    "EVIDENCIA_EXTRA",
    "COMPARTIDAS",
    "SOBRA_EXPORTADOR",
    "SOBRA_DESTINO",
    "AUTOR",
    "RAZON",
    "FECHA",
    "ARCHIVO",
    "TIPO_SUP_A",
    "TIPO_SUP_B",
)

_CONFIANZA = {"ALTA": 0, "MEDIA": 1}
_MISMO_PAIS = {"SÍ": 0, "SIN DATO": 1, "NO": 2}


def _copiar_datos_del_destino(pares: pd.DataFrame, info: dict[str, pd.DataFrame]) -> None:
    """Copia a cada par los datos de su registro destino (``info`` trae uno por registro, en el orden de ``prep``)."""
    for c in _COLUMNAS_DEL_DESTINO:
        pares[c] = [
            info[f][c].iat[p] for f, p in zip(pares["FUENTE_DESTINO"], pares["pos_b"], strict=True)
        ]


def _puntuar_pares_de_decision(
    pares: pd.DataFrame, empresas: Empresas, prep: dict, comp: Comparador
) -> None:
    """Puntúa los pares sin ``SCORE_RUES_LINKER``: los que solo vienen de una decisión de revisión.

    Calcula el puntaje y los rasgos del nombre de la empresa contra el del registro y deja constancia en
    ``MOTIVO`` de que el vínculo lo aportó la revisión. ``MISMO_PAIS`` sale de si el ISO3 de la empresa
    está entre los países de evidencia del registro.
    """
    if "SCORE_RUES_LINKER" in pares.columns:
        falta = pares["SCORE_RUES_LINKER"].isna()
    else:
        falta = pd.Series(True, index=pares.index)
    if not falta.any():
        return
    # Hallazgo F3: se compara el nombre normalizado de la grafía REPRESENTANTE de la empresa, no el de la grafía
    # del par (la de la decisión), y los rasgos se piden con ``trunc_a=False`` aunque esa grafía esté truncada.
    na = pares.loc[falta, "ENT"].map(empresas.norm_ent).to_numpy()
    nb = np.array(
        [
            prep[f]["NOMBRE_NORM"].iat[p]
            for f, p in zip(
                pares.loc[falta, "FUENTE_DESTINO"], pares.loc[falta, "pos_b"], strict=True
            )
        ],
        dtype=object,
    )
    pu = comp.puntajes(na, nb)
    for c in pu.columns:
        pares.loc[falta, c] = pu[c].to_numpy()
    rs = [comp.rasgos(a, b, False) for a, b in zip(na, nb, strict=True)]
    pares.loc[falta, "COMPARTIDAS"] = [r["COMPARTIDAS"] for r in rs]
    pares.loc[falta, "SOBRA_EXPORTADOR"] = [r["SOBRA_A"] for r in rs]
    pares.loc[falta, "SOBRA_DESTINO"] = [r["SOBRA_B"] for r in rs]
    pares.loc[falta, "SUP_A"] = na
    pares.loc[falta, "SUP_B"] = nb
    pares.loc[falta, "MOTIVO"] = (
        "Sin evidencia automática suficiente por nombre; vínculo aportado por revisión"
    )
    evid = [
        set(prep[f]["PAISES_EVIDENCIA"].iat[p].split("|"))
        for f, p in zip(pares.loc[falta, "FUENTE_DESTINO"], pares.loc[falta, "pos_b"], strict=True)
    ]
    pares.loc[falta, "MISMO_PAIS"] = [
        "SÍ" if empresas.iso_ent[e] in ev else "NO"
        for e, ev in zip(pares.loc[falta, "ENT"], evid, strict=True)
    ]


def _completar_grafia_ancla(pares: pd.DataFrame, empresas: Empresas) -> None:
    """``CLAVE_GRAFIA`` y ``GRAFIA_ANCLA`` de cada par.

    Si el par no trae la clave de la grafía (viene de una decisión) se toma la de la decisión
    (``CLAVE_A_DEC``) y, si tampoco la hay, la de la grafía representante de su empresa.
    """
    if "CLAVE_GRAFIA" in pares.columns:
        clave = pares["CLAVE_GRAFIA"]
        pares["CLAVE_GRAFIA"] = clave.where(clave.notna() & (clave != ""), pares.get("CLAVE_A_DEC"))
    else:
        pares["CLAVE_GRAFIA"] = pares.get("CLAVE_A_DEC")
    g, rep = empresas.grafias, empresas.posicion
    rep_clave = {e: g["CLAVE"].iat[rep[e]] for e in rep}
    pares["CLAVE_GRAFIA"] = [
        c if isinstance(c, str) and c else rep_clave[e]
        for c, e in zip(pares["CLAVE_GRAFIA"], pares["ENT"], strict=True)
    ]
    pares["GRAFIA_ANCLA"] = [x.split("|", 1)[1] for x in pares["CLAVE_GRAFIA"]]


def _rellenar_textos(pares: pd.DataFrame) -> None:
    """Deja las columnas de texto de los pares completas: las que faltan vacías y los ausentes como ``''``."""
    for c in _COLUMNAS_DE_TEXTO:
        if c not in pares.columns:
            pares[c] = ""
        pares[c] = pares[c].fillna("").astype(str)


def enriquecer_pares(
    pares: pd.DataFrame,
    info: dict[str, pd.DataFrame],
    empresas: Empresas,
    prep: dict,
    comp: Comparador,
) -> pd.DataFrame:
    """Agrega a cada par los datos del registro destino y de la empresa ancla.

    Modifica ``pares`` en su sitio (es la copia que hace el llamador) y lo devuelve. Con ``pares`` vacío
    no hace nada. Agrega ``ID_DESTINO``, ``NOMBRE_DESTINO``, ``PAIS_DESTINO``, ``CIUDAD``, ``WEB``,
    ``MATRIZ`` e ``ID_ENTIDAD`` del registro, ``ID_EXPORTADOR``, ``NOMBRE_EXPORTADOR`` y
    ``PAIS_EXPORTADOR`` de la empresa, ``CLAVE_GRAFIA`` y ``GRAFIA_ANCLA``, y deja como texto las columnas
    de ``_COLUMNAS_DE_TEXTO``. Los pares que solo vienen de una decisión se puntúan para dejar constancia.

    Args:
        pares: tabla ``L`` del motor (un par por empresa, destino y registro).
        info: salida de ``info_destino`` por clave de destino.
        empresas: las empresas de la ancla (``numerar_empresas``).
        prep: tablas ``prep`` por fuente.
        comp: comparador de nombres.
    """
    if not len(pares):
        return pares
    pares["pos_b"] = pares["pos_b"].astype(int)
    pares["ENT"] = pares["ENT"].astype(int)
    _copiar_datos_del_destino(pares, info)
    pares["ID_EXPORTADOR"] = pares["ENT"].map(empresas.id_ent)
    pares["NOMBRE_EXPORTADOR"] = pares["ENT"].map(empresas.nombre_ent)
    pares["PAIS_EXPORTADOR"] = pares["ENT"].map(empresas.pais_ent)
    _puntuar_pares_de_decision(pares, empresas, prep, comp)
    _completar_grafia_ancla(pares, empresas)
    _rellenar_textos(pares)
    return pares


def vinculos_principales(pares: pd.DataFrame, prep: dict) -> pd.DataFrame:
    """Los pares vinculados (``NIVEL_FINAL`` EMPRESA o GRUPO) con ``ES_PRINCIPAL``: uno por (empresa, destino).

    Entre los vínculos de una empresa con un mismo destino gana, en este orden: el nivel (EMPRESA antes
    que GRUPO), la confianza (ALTA, MEDIA), el mismo país (SÍ, SIN DATO, NO), el MAYOR tamaño del registro
    destino (``TAMANO``; ``-1`` si la fuente no lo declara), los MENOS términos sobrantes del destino, el
    mayor ``SCORE_RUES_LINKER`` y, al final, el ``ID_DESTINO``. La tabla sale ordenada así por (empresa,
    destino), sin las columnas auxiliares; si no hay vínculos, devuelve la tabla vacía con
    ``ES_PRINCIPAL`` vacía.
    """
    vinc = (
        pares[pares["NIVEL_FINAL"].isin(["EMPRESA", "GRUPO"])].copy()
        if len(pares)
        else pares.copy()
    )
    if not len(vinc):
        vinc["ES_PRINCIPAL"] = []
        return vinc
    vinc["_k1"] = vinc["NIVEL_FINAL"].map(ORDEN_NIVEL)
    vinc["_k2"] = vinc["CONFIANZA"].map(_CONFIANZA).fillna(2)
    vinc["_k3"] = vinc["MISMO_PAIS"].map(_MISMO_PAIS).fillna(1)
    vinc["_k4"] = vinc["SOBRA_DESTINO"].str.split().str.len().fillna(0)
    vinc["_k5"] = [
        float(prep[f]["TAMANO"][p]) if "TAMANO" in prep[f] else -1.0
        for f, p in zip(vinc["FUENTE_DESTINO"], vinc["pos_b"], strict=True)
    ]
    vinc = vinc.sort_values(
        [
            "ENT",
            "FUENTE_DESTINO",
            "_k1",
            "_k2",
            "_k3",
            "_k5",
            "_k4",
            "SCORE_RUES_LINKER",
            "ID_DESTINO",
        ],
        ascending=[True, True, True, True, True, False, True, False, True],
        kind="stable",
    )
    vinc["ES_PRINCIPAL"] = ~vinc.duplicated(["ENT", "FUENTE_DESTINO"])
    return vinc.drop(columns=["_k1", "_k2", "_k3", "_k4", "_k5"])


def marcar_principales(pares: pd.DataFrame, vinc: pd.DataFrame) -> None:
    """Agrega ``ES_PRINCIPAL`` a la tabla completa de pares: ``True`` solo en el vínculo principal de ``vinc``."""
    pares["ES_PRINCIPAL"] = False
    if len(vinc):
        es_principal = vinc["ES_PRINCIPAL"]
        principal = set(
            zip(
                vinc.loc[es_principal, "ENT"],
                vinc.loc[es_principal, "FUENTE_DESTINO"],
                vinc.loc[es_principal, "pos_b"],
                strict=True,
            )
        )
        pares["ES_PRINCIPAL"] = [
            k in principal
            for k in zip(pares["ENT"], pares["FUENTE_DESTINO"], pares["pos_b"], strict=True)
        ]


def duplicados_pendientes(
    cfg: ConfigVinculacion, interno: dict, grafias: pd.DataFrame
) -> dict[int, list[str]]:
    """Por empresa, los nombres de otras grafías que podrían ser la misma y nadie ha decidido.

    Recorre los pendientes de la deduplicación de la ancla (``interno["pend_a"]``) y, si las dos grafías
    quedaron en empresas DISTINTAS y el par no tiene una decisión DUPLICADO, anota a cada empresa el nombre
    original de la grafía de la otra.
    """
    ent, dec = interno["ent"], interno["dec"]
    decididos: set[frozenset] = set()
    if len(dec):
        dd = dec[(dec.TIPO == "DUPLICADO") & (cfg.ancla.clave == dec.FUENTE)]
        decididos = {frozenset((a, b)) for a, b in zip(dd.CLAVE_A, dd.CLAVE_B, strict=True)}
    dup_pend: dict[int, list[str]] = defaultdict(list)
    r: Any  # fila de itertuples(): sus campos son las columnas de ``pend_a``
    for r in interno["pend_a"].itertuples(index=False):
        ea, eb = int(ent[r.pos_a]), int(ent[r.pos_b])
        if ea == eb:
            continue
        if frozenset((grafias["CLAVE"].iat[r.pos_a], grafias["CLAVE"].iat[r.pos_b])) in decididos:
            continue
        dup_pend[ea].append(cast("str", grafias["NOMBRE_ORIGINAL"].iat[r.pos_b]))
        dup_pend[eb].append(cast("str", grafias["NOMBRE_ORIGINAL"].iat[r.pos_a]))
    return dup_pend


def reglas_de_union(uniones: pd.DataFrame) -> dict[int, list[str]]:
    """Las reglas que unieron cada fila: ``{posición: [REGLA, …]}`` a partir de ``pos_a``, ``pos_b`` y ``REGLA``."""
    reglas: dict[int, list[str]] = defaultdict(list)
    r: Any  # fila de itertuples(): sus campos son las columnas de ``uniones``
    for r in uniones.itertuples(index=False):
        reglas[int(r.pos_a)].append(r.REGLA)
        reglas[int(r.pos_b)].append(r.REGLA)
    return reglas
