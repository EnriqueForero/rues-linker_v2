"""Hojas de detalle: DETALLE_EXPORTADORES, DETALLE_<destino> y DEDUPLICACION.

* DETALLE_EXPORTADORES conserva TODAS las filas de la ancla tal como se leyeron y les agrega la empresa a la
  que quedaron asignadas, su resultado y las marcas de calidad (truncado, agente de carga, métrica repetida).
* DETALLE_<destino> conserva TODOS los registros del destino con los exportadores que se vincularon a cada
  uno y el nivel más alto de esos vínculos.
* DEDUPLICACION explica qué grafías (ancla) y qué registros (destinos) se unieron en una misma entidad y por
  qué regla.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.compartido import prefijo_destino, unir_valores
from record_linkage.flujo.vinculacion.pares import reglas_de_union

if TYPE_CHECKING:
    from record_linkage.flujo.vinculacion.config import Fuente
    from record_linkage.flujo.vinculacion.tablas import EstadoTablas

__all__ = ["construir_deduplicacion", "construir_detalle_ancla", "construir_detalles_destino"]

#: Columnas con que empieza cada DETALLE_<destino>, en este orden (después van las demás, en su orden).
_COLUMNAS_INICIALES_DETALLE = [
    "ID_DESTINO",
    "FILA_EXCEL",
    "NOMBRE_DESTINO",
    "NOMBRE_NORMALIZADO",
    "PAIS_DESTINO",
    "PAIS_ISO3",
    "IDS_EXPORTADOR",
    "EXPORTADORES_VINCULADOS",
    "NIVEL_MAS_ALTO",
    "ID_ENTIDAD",
]

_COLUMNAS_DEDUPLICACION = [
    "FUENTE",
    "ENTIDAD",
    "NOMBRE_ENTIDAD",
    "REGISTRO",
    "NOMBRE_REGISTRO",
    "N_FILAS",
    "FORMA_LEGAL",
    "COMO_SE_UNIO",
]


# ── DETALLE_EXPORTADORES ─────────────────────────────────────────────────────
def _columnas_de_repetido(ancla: Fuente, filas: pd.DataFrame) -> dict[str, np.ndarray]:
    """``<METRICA>_REPETIDO``: ``SÍ`` en las filas cuyo valor ya apareció antes para la misma empresa y claves."""
    # Hallazgo F3: el nombre sale de ``c.upper()`` y no del nombre legible de ``nombres_contexto`` (que usa
    # EXPORTADORES): una métrica renombrada ('Valor U$S' → VALOR_USD) saldría como 'VALOR U$S_REPETIDO'.
    claves = ["ENT", *[k for k in ancla.claves_repeticion if k in filas.columns]]
    return {
        f"{c.upper()}_REPETIDO": np.where(filas.duplicated([*claves, c], keep="first"), "SÍ", "NO")
        for c in ancla.metricas_repetidas
    }


def construir_detalle_ancla(estado: EstadoTablas, exportadores: pd.DataFrame) -> pd.DataFrame:
    """DETALLE_EXPORTADORES: la ancla tal como se leyó, con su empresa, su resultado y las marcas de calidad.

    Agrega al frente ``ID_EXPORTADOR`` y ``NOMBRE_EXPORTADOR`` y al final ``NOMBRE_NORMALIZADO``, ``TRUNCADO``,
    ``AGENTE_DE_CARGA``, las columnas ``RESULTADO``, ``NIVEL_VINCULO``, ``CONFIANZA`` y ``FUENTES_VINCULADAS``
    de EXPORTADORES, el nombre del vínculo principal de cada destino y ``<METRICA>_REPETIDO`` por cada
    métrica repetida. Quita la columna ``FUENTE`` si la trae.

    Args:
        estado: estado común de las secciones.
        exportadores: hoja EXPORTADORES con la columna auxiliar ``_ENT``.
    """
    ancla, emp = estado.cfg.ancla, estado.empresas
    pa = estado.filas_ancla
    exp_idx = exportadores.set_index("_ENT")
    det_a = estado.crudo_ancla.copy()
    det_a.insert(0, "ID_EXPORTADOR", pa["ENT"].map(emp.id_ent).to_numpy())
    det_a.insert(1, "NOMBRE_EXPORTADOR", pa["ENT"].map(emp.nombre_ent).to_numpy())
    det_a["NOMBRE_NORMALIZADO"] = pa["NOMBRE_NORM"].to_numpy()
    det_a["TRUNCADO"] = np.where(pa["TRUNCADO"], "SÍ", "NO")
    det_a["AGENTE_DE_CARGA"] = np.where(pa["INTERMEDIARIO"], "SÍ", "NO")
    for c in ("RESULTADO", "NIVEL_VINCULO", "CONFIANZA", "FUENTES_VINCULADAS"):
        det_a[c] = pa["ENT"].map(exp_idx[c]).to_numpy()
    for d in estado.destinos:
        pre = prefijo_destino(d)
        det_a[f"{pre}_NOMBRE"] = pa["ENT"].map(exp_idx[f"{pre}_NOMBRE"]).to_numpy()
    if ancla.metricas_repetidas:
        for nombre, marcas in _columnas_de_repetido(ancla, pa).items():
            det_a[nombre] = marcas
    return det_a.drop(columns=["FUENTE"], errors="ignore")


# ── DETALLE_<destino> ────────────────────────────────────────────────────────
def _vinculados_por_registro(lv: pd.DataFrame) -> pd.DataFrame | None:
    """Por registro destino (``pos_b``): exportadores vinculados, sus IDs y el nivel más alto; ``None`` sin vínculos."""
    if not len(lv):
        return None
    return lv.groupby("pos_b").agg(
        EXPORTADORES_VINCULADOS=("NOMBRE_EXPORTADOR", lambda s: unir_valores(s, 6)),
        IDS_EXPORTADOR=("ID_EXPORTADOR", lambda s: unir_valores(s, 6)),
        NIVEL=("NIVEL_FINAL", lambda s: "EMPRESA" if (s == "EMPRESA").any() else "GRUPO"),
    )


def _detalle_de_un_destino(estado: EstadoTablas, destino: Fuente) -> pd.DataFrame:
    """DETALLE_<destino>: ``info`` del destino más lo que lo vincula con la ancla, con columnas renombradas."""
    inf = estado.info[destino.clave].copy()
    pr = estado.interno["prep"][destino.clave]
    inf.insert(3, "NOMBRE_NORMALIZADO", pr["NOMBRE_NORM"].to_numpy())
    inf.insert(5, "PAIS_ISO3", pr["PAIS_ISO3"].to_numpy())
    vinc = estado.vinculos
    lv = vinc[vinc["FUENTE_DESTINO"] == destino.clave] if len(vinc) else vinc
    agg = _vinculados_por_registro(lv)
    for c in ("EXPORTADORES_VINCULADOS", "IDS_EXPORTADOR", "NIVEL"):
        inf[c] = inf.index.map(agg[c]).fillna("") if agg is not None else ""
    inf = inf.rename(columns={"NIVEL": "NIVEL_MAS_ALTO"})
    inf = inf[
        _COLUMNAS_INICIALES_DETALLE
        + [c for c in inf.columns if c not in _COLUMNAS_INICIALES_DETALLE]
    ]
    return inf.rename(
        columns={"ID_DESTINO": "ID_REGISTRO", "NOMBRE_DESTINO": "NOMBRE", "PAIS_DESTINO": "PAIS"}
    )


def construir_detalles_destino(estado: EstadoTablas) -> dict[str, pd.DataFrame]:
    """Una hoja DETALLE_<destino> por destino, con todos sus registros en el orden de ``prep``."""
    return {d.clave: _detalle_de_un_destino(estado, d) for d in estado.destinos}


# ── DEDUPLICACION ────────────────────────────────────────────────────────────
def _filas_de_deduplicacion_ancla(estado: EstadoTablas) -> list[dict[str, object]]:
    """Una fila por grafía de cada empresa de la ancla con MÁS de una grafía, con las reglas que las unieron."""
    interno, emp = estado.interno, estado.empresas
    reglas_g = reglas_de_union(interno["un_a"])
    ent_uniones: dict[int, list[str]] = defaultdict(list)
    for a, b, regla in interno["uniones_emp"]:
        ent_uniones[a].append(regla)
        ent_uniones[b].append(regla)
    ent0 = interno["graf"]["_COMP"].to_numpy()
    filas: list[dict[str, object]] = []
    e: Any  # etiqueta del grupo: la empresa (un entero)
    gi: Any  # etiqueta de la fila en ``grafias`` (su posición)
    for e, sub in emp.grafias.groupby("ENT", sort=False):
        if len(sub) < 2:
            continue
        for gi, rr in sub.iterrows():
            reglas = sorted(set(reglas_g.get(gi, []) + ent_uniones.get(int(ent0[gi]), [])))
            filas.append(
                {
                    "FUENTE": estado.cfg.ancla.etiqueta,
                    "ENTIDAD": emp.id_ent[int(e)],
                    "NOMBRE_ENTIDAD": emp.nombre_ent[int(e)],
                    "REGISTRO": rr["CLAVE"],
                    "NOMBRE_REGISTRO": rr["NOMBRE_ORIGINAL"],
                    "N_FILAS": int(rr["N_FILAS"]),
                    "FORMA_LEGAL": rr["FORMA_LEGAL"],
                    "COMO_SE_UNIO": "; ".join(reglas),
                }
            )
    return filas


def _filas_de_deduplicacion_destino(
    estado: EstadoTablas, destino: Fuente
) -> list[dict[str, object]]:
    """Una fila por registro de cada entidad del destino con MÁS de un registro, con las reglas que los unieron."""
    ed = estado.interno["ent_dest"][destino.clave]
    multi = ed[ed["N_EN_ENTIDAD"] > 1]
    reg = reglas_de_union(estado.interno["un_dest"][destino.clave])
    filas: list[dict[str, object]] = []
    rr: Any  # fila de iterrows(): sus campos son las columnas de la entidad
    for pos, rr in multi.iterrows():
        filas.append(
            {
                "FUENTE": destino.etiqueta,
                "ENTIDAD": rr["ID_ENTIDAD"],
                "NOMBRE_ENTIDAD": "",
                "REGISTRO": rr["ID_REGISTRO"],
                "NOMBRE_REGISTRO": rr["NOMBRE_ORIGINAL"],
                "N_FILAS": 1,
                "FORMA_LEGAL": rr["FORMA_LEGAL"],
                "COMO_SE_UNIO": "; ".join(sorted(set(reg.get(pos, [])))),
            }
        )
    return filas


def construir_deduplicacion(estado: EstadoTablas) -> pd.DataFrame:
    """Hoja DEDUPLICACION: primero las grafías de la ancla y luego los registros de cada destino.

    En los destinos ``NOMBRE_ENTIDAD`` sale vacío al armar las filas y se completa con el nombre del primer
    registro de la entidad.
    """
    filas = _filas_de_deduplicacion_ancla(estado)
    for d in estado.destinos:
        filas += _filas_de_deduplicacion_destino(estado, d)
    ded = pd.DataFrame(filas, columns=_COLUMNAS_DEDUPLICACION)
    if len(ded):
        ded["NOMBRE_ENTIDAD"] = (
            ded.groupby("ENTIDAD")["NOMBRE_REGISTRO"]
            .transform("first")
            .where(ded["NOMBRE_ENTIDAD"] == "", ded["NOMBRE_ENTIDAD"])
        )
    return ded
