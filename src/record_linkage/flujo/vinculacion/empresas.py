"""Las empresas de la ancla: qué grafía da nombre a cada una, su número (``ID_EXPORTADOR``) y sus filas.

Tras deduplicar, cada empresa (``ENT``) agrupa una o varias GRAFÍAS (país + nombre tal como viene). Aquí se
elige la grafía que la representa (``representante``), se numeran las empresas en orden de país y nombre
(``numerar_empresas``) y se arma la tabla de las filas de la ancla con su empresa, sus métricas y su
contexto (``filas_de_la_ancla``). Todas las hojas parten de ese resultado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.comparar import es_abreviatura, fichas_abreviatura
from record_linkage.flujo.vinculacion.decisiones import clave_ancla
from record_linkage.flujo.vinculacion.normalizar import serie_numerica

if TYPE_CHECKING:
    from record_linkage.flujo.vinculacion.comparar import Comparador
    from record_linkage.flujo.vinculacion.config import ConfigVinculacion
    from record_linkage.flujo.vinculacion.normalizar import Normalizador

__all__ = ["Empresas", "filas_de_la_ancla", "numerar_empresas", "representante"]


@dataclass(frozen=True)
class Empresas:
    """Las empresas de la ancla ya numeradas y nombradas.

    Todos los diccionarios van indexados por ``ENT`` (la empresa tras deduplicar y fusionar). En el motor
    original eran variables sueltas de ``construir_tablas``: ``g``, ``rep``, ``orden``, ``id_ent``,
    ``nombre_ent``, ``norm_ent``, ``pais_ent``, ``iso_ent`` y ``ent_de_clave``.

    Attributes:
        grafias: una fila por grafía de la ancla (las de ``graf``) con la columna ``ENT`` añadida.
        posicion: posición, en ``grafias``, de la grafía que representa a cada empresa.
        orden: las empresas ordenadas por país (ISO3), nombre normalizado y nombre original; el lugar
            de cada una en esta lista es el número de su ``ID_EXPORTADOR``.
        id_ent: ``ID_EXPORTADOR`` (``<prefijo>-0001``…).
        nombre_ent: nombre original de la grafía representante.
        norm_ent: nombre normalizado de la grafía representante.
        pais_ent: país original de la grafía representante.
        iso_ent: ISO3 de la grafía representante (``''`` si no hay país).
        ent_de_clave: empresa de cada grafía, por su ``CLAVE`` (``país|nombre``).
    """

    grafias: pd.DataFrame
    posicion: dict[int, int]
    orden: list[int]
    id_ent: dict[int, str]
    nombre_ent: dict[int, str]
    norm_ent: dict[int, str]
    pais_ent: dict[int, str]
    iso_ent: dict[int, str]
    ent_de_clave: dict[str, int]


def _abreviaturas(idx: list, noms: dict, nz: Normalizador) -> dict:
    """Por grafía: ¿es abreviatura de otra de la misma empresa, o no tiene palabras útiles?"""
    fichas = {k: fichas_abreviatura(noms[k], nz) for k in idx}
    return {
        k: any(
            es_abreviatura(fa, fb, nz.genericos, True)
            and not es_abreviatura(fb, fa, nz.genericos, True)
            for q in idx
            if q != k
            for fa in fichas[k]
            for fb in fichas[q]
        )
        or not nz.utiles(noms[k])
        for k in idx
    }


def _parecido_medio(idx: list, noms: dict, comp: Comparador) -> np.ndarray:
    """Por grafía, su ``SCORE_RUES_LINKER`` medio contra las demás grafías de la empresa."""
    a = np.array([noms[k] for k in idx for q in idx if q != k], dtype=object)
    b = np.array([noms[q] for k in idx for q in idx if q != k], dtype=object)
    puntajes = comp.puntajes(a, b)["SCORE_RUES_LINKER"].to_numpy()
    return puntajes.reshape(len(idx), len(idx) - 1).mean(axis=1)


def representante(
    graf_e: pd.DataFrame, nz: Normalizador, comp: Comparador, con_empresa: set
) -> int:
    """Grafía que da nombre a la empresa. Orden de preferencia:

    1. la que coincide literalmente con un registro destino (vínculo automático EMPRESA);
    2. la que NO es abreviatura de otra grafía de la misma empresa (PLVK ⊂ PELUVIKA, GRDL ⊂ GARANDEL);
    3. la más central: la más parecida, en promedio, a las demás grafías (comparador de rues-linker);
    4. con forma legal y sin truncar; 5. la más frecuente; 6. orden alfabético.

    ``graf_e`` son las filas de ``graf`` de UNA empresa y ``con_empresa`` las posiciones de las grafías
    con un candidato EMPRESA automático. Devuelve la etiqueta de índice de la grafía elegida.
    """
    idx = list(graf_e.index)
    if len(idx) == 1:
        return int(idx[0])
    noms = graf_e["NOMBRE_NORM"].to_dict()
    abrev = _abreviaturas(idx, noms, nz)
    sc = _parecido_medio(idx, noms, comp)
    orden = sorted(
        range(len(idx)),
        key=lambda t: (
            idx[t] not in con_empresa,
            abrev[idx[t]],
            -round(float(sc[t]), 2),
            not (graf_e["FORMA_LEGAL"].at[idx[t]] and not graf_e["TRUNCADO"].at[idx[t]]),
            -int(cast("int", graf_e["N_FILAS"].at[idx[t]])),
            graf_e["NOMBRE_ORIGINAL"].at[idx[t]],
        ),
    )
    return int(idx[orden[0]])


def _grafias_con_empresa(candidatos: pd.DataFrame) -> set:
    """Posiciones de las grafías con un candidato EMPRESA de banda A o B (vínculo automático)."""
    if not len(candidatos):
        return set()
    es_empresa = (candidatos["NIVEL_AUTO"] == "EMPRESA") & candidatos["BANDA_AUTO"].isin(["A", "B"])
    return set(candidatos.loc[es_empresa, "g"].astype(int))


def numerar_empresas(cfg: ConfigVinculacion, interno: dict) -> Empresas:
    """Elige la grafía representante de cada empresa de la ancla y las numera.

    Args:
        cfg: configuración del cruce (usa ``ancla.prefijo_id``).
        interno: estado de la corrida (usa ``graf``, ``ent``, ``P``, ``nz`` y ``comp``).

    Returns:
        Las empresas con su ``ID_EXPORTADOR`` (``<prefijo>-0001``…, en orden de país ISO3, nombre
        normalizado y nombre original de la grafía representante) y sus nombres.
    """
    g = interno["graf"].copy()
    g["ENT"] = interno["ent"]
    con_empresa = _grafias_con_empresa(interno["P"])
    rep: dict[int, int] = {}
    e: Any  # etiqueta del grupo: la empresa (un entero)
    for e, sub in g.groupby("ENT", sort=True):
        rep[int(e)] = representante(sub, interno["nz"], interno["comp"], con_empresa)
    orden = sorted(
        rep,
        key=lambda e: (
            g["PAIS_ISO3"].iat[rep[e]],
            g["NOMBRE_NORM"].iat[rep[e]],
            g["NOMBRE_ORIGINAL"].iat[rep[e]],
        ),
    )
    return Empresas(
        grafias=g,
        posicion=rep,
        orden=orden,
        id_ent={e: f"{cfg.ancla.prefijo_id}-{k + 1:04d}" for k, e in enumerate(orden)},
        nombre_ent={e: g["NOMBRE_ORIGINAL"].iat[rep[e]] for e in rep},
        norm_ent={e: g["NOMBRE_NORM"].iat[rep[e]] for e in rep},
        pais_ent={e: g["PAIS_ORIGINAL"].iat[rep[e]] for e in rep},
        iso_ent={e: g["PAIS_ISO3"].iat[rep[e]] for e in rep},
        ent_de_clave=dict(zip(g["CLAVE"], g["ENT"], strict=True)),
    )


def filas_de_la_ancla(
    cfg: ConfigVinculacion, interno: dict, ent_de_clave: dict[str, int]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Las filas de la ancla con su empresa, sus métricas numéricas y sus columnas de contexto.

    Returns:
        ``(filas, crudo)``: ``filas`` es la tabla ``prep`` de la ancla con las columnas ``CLAVE`` (de la
        grafía), ``ENT`` (la empresa), cada métrica como número (``0`` donde no es un número) y cada columna
        de contexto tal como vino; ``crudo`` es la ancla tal como se leyó, con índice 0..n-1.
    """
    ancla = cfg.ancla
    pa = interno["prep"][ancla.clave].copy()
    ca = interno["crudos"][ancla.clave].reset_index(drop=True)
    pa["CLAVE"] = clave_ancla(pa["PAIS_ORIGINAL"], pa["NOMBRE_ORIGINAL"]).to_numpy()
    pa["ENT"] = pa["CLAVE"].map(ent_de_clave).to_numpy()
    for c in ancla.cols_metricas:
        pa[c] = serie_numerica(ca[c]).fillna(0).to_numpy()
    for c in ancla.cols_contexto:
        pa[c] = ca[c].to_numpy()
    return pa, ca
