"""Hoja EXPORTADORES: una fila por empresa de la ancla con su resultado, sus métricas y sus vínculos.

Cada fila se arma por bloques de columnas, en este orden (el orden de columnas ES contrato: ``invariantes``,
``resumen`` y el Excel las leen por nombre y por posición):

1. identificación: ``ID_EXPORTADOR``, ``NOMBRE_EXPORTADOR``, ``PAIS``;
2. diagnóstico: ``RESULTADO``, ``NIVEL_VINCULO``, ``CONFIANZA``, ``FUENTES_VINCULADAS``,
   ``REQUIERE_REVISION``, ``QUE_REVISAR``, ``OBSERVACIONES``;
3. métricas de la ancla (con su cifra ``_SIN_REPETIDOS`` primero cuando la fuente repite esa métrica);
4. ``N_FILAS``, ``N_GRAFIAS``, ``GRAFIAS`` y las columnas de contexto de la ancla;
5. un bloque por destino, con el prefijo de su etiqueta (``ORBIS_NIVEL``, ``ORBIS_NOMBRE``…);
6. contacto consolidado, ``NOMBRE_ESTANDARIZADO`` y la columna auxiliar ``_ENT``.

La tabla sale ordenada por ``RESULTADO`` (VINCULADO, CANDIDATO POR REVISAR, SIN COINCIDENCIA) y, dentro de cada
resultado, por la columna de operaciones de mayor a menor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.compartido import (
    nombre_columna_ancla,
    prefijo_destino,
    unir_valores,
)

if TYPE_CHECKING:
    from record_linkage.flujo.vinculacion.config import Fuente
    from record_linkage.flujo.vinculacion.tablas import EstadoTablas

__all__ = ["columna_de_operaciones", "construir_exportadores"]

_ORDEN_RESULTADO = {"VINCULADO": 0, "CANDIDATO POR REVISAR": 1, "SIN COINCIDENCIA": 2}

#: (sufijo de la columna, columna del vínculo principal) del bloque de cada destino, en orden de salida.
_COLUMNAS_DEL_PRINCIPAL = (
    ("NIVEL", "NIVEL_FINAL"),
    ("CONFIANZA", "CONFIANZA"),
    ("NOMBRE", "NOMBRE_DESTINO"),
    ("ID", "ID_DESTINO"),
    ("PAIS", "PAIS_DESTINO"),
    ("CIUDAD", "CIUDAD"),
    ("WEB", "WEB"),
    ("MATRIZ", "MATRIZ"),
)


def columna_de_operaciones(tabla: pd.DataFrame) -> str | None:
    """La primera columna cuyo nombre empieza por ``OPERACIONES`` (mayúsculas o no); ``None`` si no hay."""
    return next((c for c in tabla.columns if c.upper().startswith("OPERACIONES")), None)


# ── diagnóstico de la empresa ────────────────────────────────────────────────
def _resultado(lv: pd.DataFrame, pend_c: pd.DataFrame) -> str:
    """VINCULADO si tiene algún vínculo; si no, CANDIDATO POR REVISAR si tiene candidatos C; si no, SIN COINCIDENCIA."""
    if len(lv):
        return "VINCULADO"
    if len(pend_c):
        return "CANDIDATO POR REVISAR"
    return "SIN COINCIDENCIA"


def _nivel_vinculo(lv: pd.DataFrame) -> str:
    """El nivel más alto entre sus vínculos: EMPRESA si alguno lo es, GRUPO si solo hay GRUPO, ``''`` si no hay."""
    if len(lv) and (lv["NIVEL_FINAL"] == "EMPRESA").any():
        return "EMPRESA"
    return "GRUPO" if len(lv) else ""


def _confianza(lv: pd.DataFrame) -> str:
    """ALTA si TODOS los vínculos principales son de confianza ALTA, MEDIA si no, ``''`` si no hay vínculos."""
    principales = lv[lv["ES_PRINCIPAL"]] if len(lv) else lv
    if len(principales) and (principales["CONFIANZA"] == "ALTA").all():
        return "ALTA"
    return "MEDIA" if len(principales) else ""


def _que_revisar_vinculos(pend_b: pd.DataFrame, pend_c: pd.DataFrame) -> list[str]:
    """Frases sobre los vínculos B (y los de revisión asistida) por confirmar y los candidatos C por decidir."""
    que: list[str] = []
    if len(pend_b):
        n_asis = int(pend_b["ESTADO"].str.contains("asistida").sum())
        if len(pend_b) - n_asis:
            que.append(f"{len(pend_b) - n_asis} vínculo(s) automático(s) B por confirmar")
        if n_asis:
            que.append(f"{n_asis} vínculo(s) de revisión asistida por validar")
    if len(pend_c):
        que.append(f"{len(pend_c)} candidato(s) C por decidir")
    return que


def _que_revisar_grafias(estado: EstadoTablas, e: int, gsub: pd.DataFrame) -> list[str]:
    """Frases sobre la unión de sus grafías: una unión asistida por validar y los posibles duplicados sin decidir."""
    que: list[str] = []
    marca = estado.cfg.marca_sin_validar
    reglas = estado.reglas_union
    if any(marca and marca in rg for gi in gsub.index for rg in reglas.get(int(gi), [])):
        que.append("validar la unión de sus grafías (revisión asistida)")
    if estado.dup_pend.get(e):
        nombres = sorted(set(estado.dup_pend[e]))[:3]
        que.append("¿duplicado de " + " / ".join(f"«{x}»" for x in nombres) + "?")
    return que


def _observaciones(
    estado: EstadoTablas, e: int, gsub: pd.DataFrame, iso_destinos: dict[str, set[str]]
) -> list[str]:
    """Advertencias sobre los datos de la empresa: país sin cobertura, agente de carga, truncado, sin nombre útil."""
    ancla = estado.cfg.ancla
    iso = estado.empresas.iso_ent[e]
    claves = [d.clave for d in estado.destinos]
    sin_cob = [estado.etiquetas[d] for d in claves if iso and iso not in iso_destinos[d]]
    obs: list[str] = []
    if len(sin_cob) == len(claves) and claves:
        obs.append("país sin registros en " + " ni ".join(sin_cob))
    if gsub["INTERMEDIARIO"].any():
        obs.append("agente de carga: la empresa exportadora real no está identificada")
    if gsub["TRUNCADO"].any():
        obs.append(f"nombre truncado a {ancla.largo_truncamiento} caracteres en el origen")
    nz = estado.interno["nz"]
    if (gsub["NOMBRE_NORM"].map(lambda x: not nz.utiles(x))).all():
        obs.append("nombre sin palabras identificables")
    return obs


def _diagnostico(
    estado: EstadoTablas,
    e: int,
    gsub: pd.DataFrame,
    lv: pd.DataFrame,
    le: pd.DataFrame,
    iso_destinos: dict[str, set[str]],
) -> dict[str, object]:
    """Columnas ``RESULTADO`` … ``OBSERVACIONES`` de la empresa ``e``.

    ``lv`` son sus vínculos (EMPRESA o GRUPO) y ``le`` todos sus pares; ``gsub`` sus grafías.
    """
    pend_c = le[le["ESTADO"].str.startswith("CANDIDATO")] if len(le) else le
    pend_b = lv[lv["CONFIANZA"] == "MEDIA"] if len(lv) else lv
    fuentes_v = [
        estado.etiquetas[f]
        for f in (d.clave for d in estado.destinos)
        if len(lv) and (lv["FUENTE_DESTINO"] == f).any()
    ]
    que = _que_revisar_vinculos(pend_b, pend_c)
    que += _que_revisar_grafias(estado, e, gsub)
    obs = _observaciones(estado, e, gsub, iso_destinos)
    return {
        "RESULTADO": _resultado(lv, pend_c),
        "NIVEL_VINCULO": _nivel_vinculo(lv),
        "CONFIANZA": _confianza(lv),
        "FUENTES_VINCULADAS": " + ".join(fuentes_v),
        "REQUIERE_REVISION": "SÍ" if que else "NO",
        "QUE_REVISAR": "; ".join(que),
        "OBSERVACIONES": "; ".join(obs),
    }


# ── columnas de la ancla ─────────────────────────────────────────────────────
def _columnas_de_metricas(ancla: Fuente, sub: pd.DataFrame) -> dict[str, object]:
    """Una columna por métrica con su suma sobre las filas de la empresa (``sub``).

    Si la fuente repite la métrica en varias filas (``metricas_repetidas``), antes va la columna
    ``<METRICA>_SIN_REPETIDOS``: la suma tras quitar las filas con la misma empresa, las mismas
    ``claves_repeticion`` y el mismo valor.
    """
    fila: dict[str, object] = {}
    for c in ancla.cols_metricas:
        nombre = nombre_columna_ancla(ancla, c)
        if c in ancla.metricas_repetidas:  # la cifra recomendada va primero
            # Hallazgo F3: una clave de ``claves_repeticion`` que no es columna de las filas de la ancla se
            # ignora SIN aviso (``if k in sub.columns``); con una errata ('HS' por 'HS6') la cifra «sin
            # repetidos» deduplica solo por (empresa, valor) y subestima. Debería fallar al validar la fuente.
            claves = ["ENT", *[k for k in ancla.claves_repeticion if k in sub.columns], c]
            fila[f"{nombre}_SIN_REPETIDOS"] = float(sub.drop_duplicates(claves)[c].sum())
        fila[nombre] = float(sub[c].sum())
    return fila


def _columnas_de_conteo_y_contexto(
    ancla: Fuente, sub: pd.DataFrame, gsub: pd.DataFrame
) -> dict[str, object]:
    """``N_FILAS``, ``N_GRAFIAS``, ``GRAFIAS`` y una columna por cada columna de contexto de la ancla."""
    fila: dict[str, object] = {
        "N_FILAS": len(sub),
        "N_GRAFIAS": len(gsub),
        # Hallazgo F3 (menor): ``sort_values`` usa el algoritmo por defecto (quicksort), que no garantiza
        # estabilidad: el orden de las grafías con el mismo N_FILAS depende de numpy; kind="stable" lo fijaría.
        "GRAFIAS": unir_valores(gsub.sort_values("N_FILAS", ascending=False)["NOMBRE_ORIGINAL"]),
    }
    for c in ancla.cols_contexto:
        fila[nombre_columna_ancla(ancla, c)] = unir_valores(sub[c], maximo=8)
    return fila


# ── bloque de cada destino ───────────────────────────────────────────────────
def _columna_de_contexto(inf: pd.DataFrame, destino: Fuente, nom: str) -> str:
    """Columna de ``info_destino`` que trae el contexto ``nom``: ``nom`` o ``<nom>_<clave>`` si chocó."""
    # Hallazgo F3: ``info_destino`` renombra a ``<nom>_<clave>`` cuando ``nom`` choca con una de sus columnas
    # base (CIUDAD, WEB, PAIS_DESTINO…); aquí se pregunta si ``nom`` está en la tabla YA completa, y la
    # columna base lo está: el bloque muestra la columna base en lugar del contexto declarado.
    return nom if nom in inf.columns else f"{nom}_{destino.clave}"


def _bloque_de_destino(destino: Fuente, lv: pd.DataFrame, inf: pd.DataFrame) -> dict[str, object]:
    """Columnas ``<PREFIJO>_…`` de un destino: su vínculo principal, el contexto, cuántos vínculos y los otros.

    Sin vínculos con este destino, los textos quedan en ``''`` y las columnas numéricas de contexto en ``NaN``.
    """
    pre = prefijo_destino(destino)
    ld = lv[lv["FUENTE_DESTINO"] == destino.clave] if len(lv) else lv
    p0 = ld[ld["ES_PRINCIPAL"]].iloc[0] if len(ld) else None
    fila: dict[str, object] = {}
    for sufijo, columna in _COLUMNAS_DEL_PRINCIPAL:
        fila[f"{pre}_{sufijo}"] = p0[columna] if p0 is not None else ""
    for c in destino.cols_contexto:
        nom = destino.nombres_contexto.get(c, c)
        col = _columna_de_contexto(inf, destino, nom)
        vacio = np.nan if pd.api.types.is_numeric_dtype(inf[col]) else ""
        fila[f"{pre}_{nom}"] = inf[col].iat[int(p0["pos_b"])] if p0 is not None else vacio
    fila[f"{pre}_N_VINCULOS"] = len(ld)
    otros = ld[~ld["ES_PRINCIPAL"]] if len(ld) else ld
    fila[f"{pre}_OTROS_VINCULOS"] = (
        unir_valores(otros["NOMBRE_DESTINO"] + " [" + otros["NIVEL_FINAL"] + "]", maximo=10)
        if len(otros)
        else ""
    )
    return fila


def _contacto_consolidado(lv: pd.DataFrame, prep: dict, claves: list[str]) -> dict[str, object]:
    """Web, teléfono y correo de los registros destino de nivel EMPRESA, hasta 3 de cada uno.

    Prioriza el orden de los destinos en la configuración y, dentro de cada uno, el vínculo principal.
    """
    webs: list[object] = []
    tels: list[object] = []
    mails: list[object] = []
    if len(lv):
        orden_destino = {f: i for i, f in enumerate(claves)}
        lv2 = lv[lv["NIVEL_FINAL"] == "EMPRESA"].assign(
            _o=lambda x: x["FUENTE_DESTINO"].map(orden_destino)
        )
        ordenado = lv2.sort_values(["_o", "ES_PRINCIPAL"], ascending=[True, False])
        for f, p in zip(ordenado["FUENTE_DESTINO"], ordenado["pos_b"], strict=True):
            webs.append(prep[f]["WEB"].iat[p])
            tels.append(prep[f]["TELEFONO"].iat[p])
            mails.append(prep[f]["EMAIL"].iat[p])
    return {
        "CONTACTO_WEB": unir_valores(webs, 3),
        "CONTACTO_TELEFONO": unir_valores(tels, 3),
        "CONTACTO_EMAIL": unir_valores(mails, 3),
    }


# ── fila y hoja ──────────────────────────────────────────────────────────────
def _fila_de_empresa(
    estado: EstadoTablas, e: int, sub: pd.DataFrame, iso_destinos: dict[str, set[str]]
) -> dict[str, object]:
    """La fila de EXPORTADORES de la empresa ``e``; ``sub`` son sus filas en la ancla."""
    # Hallazgo F3: tres filtros completos por empresa (``g``, ``vinc`` y ``L`` == e) vuelven la hoja
    # cuadrática: O(empresas × pares). Con miles de empresas conviene agrupar una vez (``groupby``).
    emp, ancla = estado.empresas, estado.cfg.ancla
    gsub = emp.grafias[emp.grafias["ENT"] == e]
    vinc, pares = estado.vinculos, estado.pares
    lv = vinc[vinc["ENT"] == e] if len(vinc) else vinc
    le = pares[pares["ENT"] == e] if len(pares) else pares
    fila: dict[str, object] = {
        "ID_EXPORTADOR": emp.id_ent[e],
        "NOMBRE_EXPORTADOR": emp.nombre_ent[e],
        "PAIS": emp.pais_ent[e],
    }
    fila.update(_diagnostico(estado, e, gsub, lv, le, iso_destinos))
    fila.update(_columnas_de_metricas(ancla, sub))
    fila.update(_columnas_de_conteo_y_contexto(ancla, sub, gsub))
    for d in estado.destinos:
        fila.update(_bloque_de_destino(d, lv, estado.info[d.clave]))
    fila.update(
        _contacto_consolidado(lv, estado.interno["prep"], [d.clave for d in estado.destinos])
    )
    fila["NOMBRE_ESTANDARIZADO"] = emp.norm_ent[e]
    fila["_ENT"] = e
    return fila


def construir_exportadores(estado: EstadoTablas) -> pd.DataFrame:
    """Hoja EXPORTADORES, ordenada por ``RESULTADO`` y por operaciones de mayor a menor.

    La tabla conserva la columna auxiliar ``_ENT`` (la empresa), con la que se unen las demás hojas;
    el orquestador la quita al final. El orden entre empresas con el mismo resultado y las mismas
    operaciones es el de su ``ID_EXPORTADOR`` (el orden es estable).
    """
    prep = estado.interno["prep"]
    iso_destinos = {
        d.clave: set("|".join(prep[d.clave]["PAISES_EVIDENCIA"]).split("|")) - {""}
        for d in estado.destinos
    }
    grupos = estado.filas_ancla.groupby("ENT", sort=False)
    filas = [
        _fila_de_empresa(estado, e, grupos.get_group(e), iso_destinos)
        for e in estado.empresas.orden
    ]
    exportadores = pd.DataFrame(filas)
    operaciones = columna_de_operaciones(exportadores)
    return exportadores.sort_values(
        ["RESULTADO", operaciones] if operaciones else ["RESULTADO"],
        key=lambda s: s.map(_ORDEN_RESULTADO) if s.name == "RESULTADO" else -s,
        kind="stable",
    ).reset_index(drop=True)
