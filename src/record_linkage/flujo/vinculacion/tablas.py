"""Tablas de salida del motor de vinculación: arma las hojas del Excel a partir del estado de la corrida.

``construir_tablas`` es el orquestador: numera las empresas de la ancla (``empresas``), enriquece los pares
(``pares``), arma cada hoja (``tabla_*``; son 13 más un DETALLE por destino), calcula los controles y el
resumen, y junta todo en un ``Resultado``. El estado que las secciones comparten es ``EstadoTablas``. El orden de las columnas, el de
las filas y los tipos de cada hoja son contrato: el Excel, ``invariantes`` y ``resumen`` los leen por nombre
y por posición.

Deja en ``interno`` dos claves que otros leen después: ``id_ent`` (el ``ID_EXPORTADOR`` de cada empresa) y
``vinc`` (los pares vinculados, con su vínculo principal).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.calidad import calidad_datos
from record_linkage.flujo.vinculacion.compartido import unir_valores
from record_linkage.flujo.vinculacion.config import ConfigVinculacion, Fuente
from record_linkage.flujo.vinculacion.controles import comparar_fuentes_decision, invariantes
from record_linkage.flujo.vinculacion.decisiones import COLUMNAS_DECISION
from record_linkage.flujo.vinculacion.empresas import (
    Empresas,
    filas_de_la_ancla,
    numerar_empresas,
)
from record_linkage.flujo.vinculacion.normalizar import serie_numerica
from record_linkage.flujo.vinculacion.pares import (
    duplicados_pendientes,
    enriquecer_pares,
    marcar_principales,
    reglas_de_union,
    vinculos_principales,
)
from record_linkage.flujo.vinculacion.resultado import Resultado
from record_linkage.flujo.vinculacion.resumen import parametros, resumen
from record_linkage.flujo.vinculacion.tabla_detalles import (
    construir_deduplicacion,
    construir_detalle_ancla,
    construir_detalles_destino,
)
from record_linkage.flujo.vinculacion.tabla_enlaces import construir_contactos, construir_enlaces
from record_linkage.flujo.vinculacion.tabla_exportadores import construir_exportadores
from record_linkage.flujo.vinculacion.tabla_revision import construir_revision

__all__ = ["EstadoTablas", "construir_tablas", "info_destino", "unir_valores"]

_VALORES_SIN_DATO = {"n.a.": None, "n.s.": None, "-": None, "": None}
#: Fracción mínima de los valores con contenido que deben ser números para tratar una columna como numérica.
_FRACCION_NUMERICA = 0.8


@dataclass(frozen=True)
class EstadoTablas:
    """Lo que las secciones comparten al armar las hojas.

    En el motor original eran variables sueltas de ``construir_tablas``: ``I``, ``g`` y sus derivados
    (``empresas``), ``pa``, ``ca``, ``info``, ``L``, ``vinc``, ``dup_pend`` y ``reglas_union``.

    Attributes:
        cfg: configuración del cruce.
        interno: estado interno de la corrida (``prep``, ``crudos``, ``nz``, ``comp``, ``graf``, ``ent``…).
        empresas: las empresas de la ancla ya numeradas y nombradas.
        filas_ancla: las filas de la ancla normalizadas, con su empresa (``ENT``), métricas y contexto.
        crudo_ancla: la ancla tal como se leyó, con índice 0..n-1.
        info: por destino, lo que se muestra de cada registro (``info_destino``).
        pares: todos los pares empresa × registro destino, enriquecidos y con ``ES_PRINCIPAL``.
        vinculos: los pares vinculados (EMPRESA o GRUPO), con su vínculo principal marcado.
        dup_pend: por empresa, nombres de otras grafías que podrían ser la misma y nadie ha decidido.
        reglas_union: por posición de grafía, las reglas que la unieron con otras.
    """

    cfg: ConfigVinculacion
    interno: dict
    empresas: Empresas
    filas_ancla: pd.DataFrame
    crudo_ancla: pd.DataFrame
    info: dict[str, pd.DataFrame]
    pares: pd.DataFrame
    vinculos: pd.DataFrame
    dup_pend: dict[int, list[str]]
    reglas_union: dict[int, list[str]]

    @property
    def destinos(self) -> list[Fuente]:
        """Los destinos de la configuración, en su orden."""
        return list(self.cfg.destinos)

    @property
    def etiquetas(self) -> dict[str, str]:
        """Etiqueta de cada destino por su clave."""
        return {d.clave: d.etiqueta for d in self.destinos}


def _valores_de_contexto(valores: pd.Series) -> np.ndarray:
    """Una columna de contexto del destino: números (4 decimales) si casi todo lo útil lo es, si no texto."""
    # Hallazgo F3: con 80 % o más de números, los valores que no lo son ('1,2 M', 'ND', 'aprox. 50') se
    # vuelven NaN sin aviso: se pierde el dato en la hoja DETALLE_<destino> y en EXPORTADORES.
    num = serie_numerica(valores)
    utiles = valores.astype(object).where(valores.notna(), None).replace(_VALORES_SIN_DATO).notna()
    if utiles.any() and num.notna().sum() >= _FRACCION_NUMERICA * utiles.sum():
        return num.round(4).to_numpy()
    return valores.astype(object).where(valores.notna(), "").astype(str).to_numpy()


def info_destino(
    f: Fuente, prep: pd.DataFrame, crudo: pd.DataFrame, ent: pd.DataFrame
) -> pd.DataFrame:
    """Una fila por registro destino con lo que se muestra al usuario (en el orden de ``prep``).

    Columnas: ``ID_DESTINO``, ``FILA_EXCEL``, ``NOMBRE_DESTINO``, ``PAIS_DESTINO``, ``CIUDAD``, ``WEB``,
    ``EMAIL``, ``TELEFONO``, ``DIRECCION``, ``MATRIZ``, ``ID_ENTIDAD`` y después una por cada columna de
    contexto de la fuente (con su nombre legible; si choca con una columna anterior, ``<nombre>_<clave>``).

    Args:
        f: la fuente destino.
        prep: su tabla ``prep``.
        crudo: la fuente tal como se leyó, con índice 0..n-1.
        ent: las entidades del destino (``deduplicar_destino``); de aquí sale ``ID_ENTIDAD``.
    """
    out = pd.DataFrame(
        {
            "ID_DESTINO": prep["ID_REGISTRO"].to_numpy(),
            "FILA_EXCEL": prep["FILA_EXCEL"].to_numpy(),
            "NOMBRE_DESTINO": prep["NOMBRE_ORIGINAL"].to_numpy(),
            "PAIS_DESTINO": prep["PAIS_ORIGINAL"].to_numpy(),
            "CIUDAD": prep["CIUDAD"].to_numpy(),
            "WEB": prep["WEB"].to_numpy(),
            "EMAIL": prep["EMAIL"].to_numpy(),
            "TELEFONO": prep["TELEFONO"].to_numpy(),
            "DIRECCION": prep["DIRECCION"].to_numpy(),
            "MATRIZ": prep["GRUPO_ORIGINAL"].to_numpy(),
            "ID_ENTIDAD": ent["ID_ENTIDAD"].to_numpy(),
        }
    )
    for c in f.cols_contexto:
        nombre = f.nombres_contexto.get(c, c)
        if nombre in out.columns:
            nombre = f"{nombre}_{f.clave}"
        out[nombre] = _valores_de_contexto(crudo[c].reset_index(drop=True))
    return out


def _preparar_estado(cfg: ConfigVinculacion, interno: dict) -> EstadoTablas:
    """Las empresas, las filas de la ancla, los pares enriquecidos con su vínculo principal y las marcas."""
    prep, crudos = interno["prep"], interno["crudos"]
    empresas = numerar_empresas(cfg, interno)
    filas_ancla, crudo_ancla = filas_de_la_ancla(cfg, interno, empresas.ent_de_clave)
    info = {
        d.clave: info_destino(
            d, prep[d.clave], crudos[d.clave].reset_index(drop=True), interno["ent_dest"][d.clave]
        )
        for d in cfg.destinos
    }
    pares = enriquecer_pares(interno["L"].copy(), info, empresas, prep, interno["comp"])
    vinculos = vinculos_principales(pares, prep)
    marcar_principales(pares, vinculos)
    return EstadoTablas(
        cfg=cfg,
        interno=interno,
        empresas=empresas,
        filas_ancla=filas_ancla,
        crudo_ancla=crudo_ancla,
        info=info,
        pares=pares,
        vinculos=vinculos,
        dup_pend=duplicados_pendientes(cfg, interno, empresas.grafias),
        reglas_union=reglas_de_union(interno["un_a"]),
    )


def _discrepancias(interno: dict, etiquetas: dict[str, str]) -> pd.DataFrame:
    """VALIDACION_DETALLE: las discrepancias con la referencia (exportador por nombre, destino por etiqueta)."""
    disc = interno["disc"].copy() if len(interno["disc"]) else pd.DataFrame()
    if len(disc):
        nombres: dict[int, list[str]] = {}
        for gi, e in enumerate(interno["ent_auto"]):
            nombres.setdefault(int(e), []).append(interno["graf"]["NOMBRE_ORIGINAL"].iat[gi])
        disc.insert(1, "EXPORTADOR", disc["ENT"].map(lambda e: " | ".join(nombres[int(e)])))
        disc = disc.drop(columns=["ENT"])
        disc["FUENTE_DESTINO"] = (
            disc["FUENTE_DESTINO"].map(etiquetas).fillna(disc["FUENTE_DESTINO"])
        )
    return disc


def _validacion(validacion: pd.DataFrame, etiquetas: dict[str, str]) -> pd.DataFrame:
    """VALIDACION: las métricas contra la referencia, con el destino por su etiqueta."""
    val = validacion.copy()
    if len(val):
        val["FUENTE_DESTINO"] = val["FUENTE_DESTINO"].map(etiquetas).fillna(val["FUENTE_DESTINO"])
    return val


def _decisiones(dec: pd.DataFrame) -> pd.DataFrame:
    """DECISIONES: las decisiones leídas con su archivo de origen (``ARCHIVO_ORIGEN``)."""
    if len(dec):
        return dec[[*COLUMNAS_DECISION, "ARCHIVO"]].rename(columns={"ARCHIVO": "ARCHIVO_ORIGEN"})
    return pd.DataFrame(columns=[*COLUMNAS_DECISION, "ARCHIVO_ORIGEN"])


def construir_tablas(
    cfg: ConfigVinculacion, interno: dict, val: pd.DataFrame, avisos: list, segundos: float
) -> Resultado:
    """Arma las tablas de salida y el resumen de una corrida.

    Args:
        cfg: configuración del cruce.
        interno: estado interno de la corrida (``nz``, ``comp``, ``prep``, ``crudos``, ``graf``, ``ent``,
            ``ent_auto``, ``L``, ``P``, ``un_a``, ``pend_a``, ``uniones_emp``, ``ent_dest``, ``un_dest``,
            ``dec``, ``ref`` y ``disc``). Se le agregan ``id_ent`` y ``vinc``.
        val: métricas contra la referencia (vacía si no hay referencia).
        avisos: avisos acumulados por la corrida.
        segundos: duración de la corrida hasta aquí.

    Returns:
        El ``Resultado`` con las tablas (en el orden del Excel: EXPORTADORES, ENLACES, REVISION, DECISIONES,
        CONTACTOS, DETALLE_EXPORTADORES, un DETALLE_<destino> por destino, DEDUPLICACION, VALIDACION,
        VALIDACION_DETALLE, COMPARACION_REVISIONES, CALIDAD_DATOS, CONTROL_QA y PARAMETROS), el resumen y los
        controles.
    """
    estado = _preparar_estado(cfg, interno)
    exportadores = construir_exportadores(estado)
    enlaces = construir_enlaces(estado)
    revision = construir_revision(estado, exportadores)
    contactos = construir_contactos(estado)
    detalle_ancla = construir_detalle_ancla(estado, exportadores)
    detalles = construir_detalles_destino(estado)
    deduplicacion = construir_deduplicacion(estado)

    # Controles, resumen y parámetros leen EXPORTADORES con su columna auxiliar ``_ENT``.
    pa, pares, vinc = estado.filas_ancla, estado.pares, estado.vinculos
    calidad = calidad_datos(cfg, interno, pa, estado.crudo_ancla, exportadores)
    controles = invariantes(
        cfg, interno, pa, exportadores, pares, vinc, enlaces, estado.empresas.grafias, detalles
    )
    indicadores, por_pais = resumen(
        cfg, interno, exportadores, pares, vinc, val, calidad, controles, pa
    )
    parametros_corrida = parametros(cfg, interno, segundos, enlaces)

    validacion = _validacion(val, estado.etiquetas)
    tablas = {
        "EXPORTADORES": exportadores.drop(columns=["_ENT"]),
        "ENLACES": enlaces,
        "REVISION": revision,
        "DECISIONES": _decisiones(interno["dec"]),
        "CONTACTOS": contactos,
        "DETALLE_EXPORTADORES": detalle_ancla,
    }
    for d in estado.destinos:
        tablas[f"DETALLE_{d.clave}"] = detalles[d.clave]
    tablas.update(
        {
            "DEDUPLICACION": deduplicacion,
            "VALIDACION": validacion,
            "VALIDACION_DETALLE": _discrepancias(interno, estado.etiquetas),
            "COMPARACION_REVISIONES": comparar_fuentes_decision(interno["dec"]),
            "CALIDAD_DATOS": calidad,
            "CONTROL_QA": controles,
            "PARAMETROS": parametros_corrida,
        }
    )
    interno["id_ent"], interno["vinc"] = estado.empresas.id_ent, vinc
    return Resultado(
        cfg=cfg,
        tablas=tablas,
        resumen=indicadores,
        por_pais=por_pais,
        validacion=validacion,
        invariantes=controles,
        calidad=calidad,
        avisos=avisos,
        parametros=parametros_corrida,
        segundos=segundos,
        internos=interno,
    )
