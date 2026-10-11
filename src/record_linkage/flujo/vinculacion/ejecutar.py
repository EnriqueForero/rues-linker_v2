"""Orquestador de la vinculación: lee, normaliza, deduplica, compara, clasifica y arma las tablas.

``ejecutar`` corre el cruce completo en memoria y devuelve un ``Resultado``; no escribe archivos (el Excel lo
escribe ``flujo.vinculacion_excel``). Corre el motor DOS veces sobre los mismos candidatos: una sin
decisiones humanas (el motor automático puro, que sirve para medirlo contra una referencia) y otra con ellas.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.candidatos import generar_candidatos
from record_linkage.flujo.vinculacion.clasificar import clasificar, evidencia_extra
from record_linkage.flujo.vinculacion.comparar import Comparador
from record_linkage.flujo.vinculacion.config import ConfigVinculacion, Fuente
from record_linkage.flujo.vinculacion.decisiones import clave_ancla, leer_decisiones
from record_linkage.flujo.vinculacion.deduplicar import deduplicar_ancla, deduplicar_destino
from record_linkage.flujo.vinculacion.lectura import leer_fuente
from record_linkage.flujo.vinculacion.normalizar import Normalizador, preparar_fuente
from record_linkage.flujo.vinculacion.resultado import Resultado
from record_linkage.flujo.vinculacion.tablas import construir_tablas
from record_linkage.flujo.vinculacion.validacion import validar_contra_referencia
from record_linkage.flujo.vinculacion.vinculos import (
    agregar_por_entidad,
    aplicar_decisiones,
    fusionar_por_empresa_comun,
    mapear_decisiones,
)
from record_linkage.flujo.vinculacion.web import propagar_por_web

__all__ = ["ejecutar"]

logger = logging.getLogger(__name__)

_COLUMNAS_PARES_VACIOS = [
    "g",
    "pos_b",
    "FUENTE_DESTINO",
    "NIVEL_AUTO",
    "BANDA_AUTO",
    "SCORE_RUES_LINKER",
]
_COLUMNAS_QUE_SUBEN_A_LA_GRAFIA = (
    "ID_COMPARABLE",
    "DOMINIO_WEB",
    "DOMINIO_EMAIL",
    "TEL_DIGITOS",
    "CIUDAD_NORM",
)

Emisor = Callable[[str], None]


def _emisor(verboso: bool, registro: Emisor | None) -> Emisor:
    """Dónde van los mensajes de avance: a ``registro`` si se da; si no, al logger del módulo (INFO)."""
    if not verboso:
        return lambda _mensaje: None
    return registro or logger.info


def _cargar(cfg: ConfigVinculacion, nz: Normalizador, log: Emisor) -> tuple[dict, dict]:
    fuentes = {f.clave: f for f in [cfg.ancla, *cfg.destinos]}
    crudos = {k: leer_fuente(f) for k, f in fuentes.items()}
    for k, v in crudos.items():
        log(f"📥 {k:<14} {len(v):>6,} filas · {Path(fuentes[k].archivo).name} [{fuentes[k].hoja}]")
    prep = {k: preparar_fuente(crudos[k], fuentes[k], nz) for k in fuentes}
    return crudos, prep


def _leer_decisiones_y_referencia(
    cfg: ConfigVinculacion, log: Emisor
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dec = leer_decisiones(cfg.archivos_decisiones)
    ref_cfg = cfg.archivo_referencia
    refs = [ref_cfg] if isinstance(ref_cfg, str) else list(ref_cfg or [])
    ref = leer_decisiones([r for r in refs if r])
    log(
        f"🧾 decisiones: {len(dec):,} filas de {len(cfg.archivos_decisiones)} archivo(s) · referencia: {len(ref):,} filas"
    )
    return dec, ref


def _comparador(prep: dict, nz: Normalizador, cfg: ConfigVinculacion) -> Comparador:
    corpus = pd.concat(
        [prep[k][c] for k in prep for c in ("NOMBRE_NORM", "ALIAS_NORM", "GRUPO_NORM")]
    ).tolist()
    return Comparador(nz, corpus, cfg)


def _deduplicar_la_ancla(
    cfg: ConfigVinculacion, prep: dict, nz: Normalizador, comp: Comparador, dec: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Grafías de la ancla con decisiones y sin ellas (esta última sirve para medir el motor automático)."""
    ancla = cfg.ancla
    graf, un_a, pend_a = deduplicar_ancla(prep[ancla.clave], nz, comp, cfg, dec)
    pa = prep[ancla.clave].assign(
        CLAVE=clave_ancla(
            prep[ancla.clave]["PAIS_ORIGINAL"], prep[ancla.clave]["NOMBRE_ORIGINAL"]
        ).to_numpy()
    )
    for c in _COLUMNAS_QUE_SUBEN_A_LA_GRAFIA:
        m = pa[pa[c] != ""].groupby("CLAVE")[c].first()
        graf[c] = graf["CLAVE"].map(m).fillna("").to_numpy()
    graf_auto, _, _ = deduplicar_ancla(prep[ancla.clave], nz, comp, cfg, leer_decisiones([]))
    if not (graf_auto["CLAVE"].to_numpy() == graf["CLAVE"].to_numpy()).all():
        raise RuntimeError(
            "Qué pasó: las grafías de la ancla cambiaron de orden entre la corrida con decisiones y la automática.\n"
            "Por qué importa: la validación contra la referencia compara empresas por posición.\n"
            "Qué hacer: es un defecto del motor, no de sus datos; repórtelo con la configuración usada."
        )
    return graf, un_a, pend_a, graf_auto


def _deduplicar_los_destinos(
    cfg: ConfigVinculacion, prep: dict, nz: Normalizador, dec: pd.DataFrame
) -> tuple[dict, dict]:
    ent_dest, un_dest = {}, {}
    for d in cfg.destinos:
        ent_dest[d.clave], un_dest[d.clave] = deduplicar_destino(prep[d.clave], d, nz, dec)
    return ent_dest, un_dest


def _pares_de_un_destino(
    d: Fuente,
    graf: pd.DataFrame,
    prep: dict,
    nz: Normalizador,
    comp: Comparador,
    cfg: ConfigVinculacion,
) -> pd.DataFrame | None:
    x = generar_candidatos(graf, prep[d.clave], nz, comp, cfg)
    if x.empty:
        return None
    x = clasificar(x, graf, prep[d.clave], nz, comp, cfg)
    x = evidencia_extra(x, graf, prep[d.clave])
    x["FUENTE_DESTINO"] = d.clave
    return x


def _candidatos_clasificados(
    cfg: ConfigVinculacion,
    graf: pd.DataFrame,
    prep: dict,
    nz: Normalizador,
    comp: Comparador,
    log: Emisor,
) -> pd.DataFrame:
    piezas = []
    for d in cfg.destinos:
        x = _pares_de_un_destino(d, graf, prep, nz, comp, cfg)
        if x is None:
            continue
        piezas.append(x)
        log(
            f"🔗 {d.clave:<14} candidatos clasificados: {len(x):,} "
            f"(A={int((x.BANDA_AUTO == 'A').sum())}, B={int((x.BANDA_AUTO == 'B').sum())}, C={int((x.BANDA_AUTO == 'C').sum())})"
        )
    pares = (
        pd.concat(piezas, ignore_index=True)
        if piezas
        else pd.DataFrame(columns=_COLUMNAS_PARES_VACIOS)
    )
    pares["CLAVE_GRAFIA"] = (
        graf["CLAVE"].to_numpy()[pares["g"].to_numpy().astype(int)] if len(pares) else []
    )
    return pares


def _vinculos_automaticos(
    cfg: ConfigVinculacion, pares: pd.DataFrame, graf: pd.DataFrame, graf_auto: pd.DataFrame, prep: dict,
    nz: Normalizador, comp: Comparador,
) -> tuple[pd.DataFrame, np.ndarray]:  # fmt: skip
    """Motor automático puro (sin decisiones): la base contra la que se mide la validación."""
    destinos = [d.clave for d in cfg.destinos]
    ent_auto = graf_auto["_COMP"].to_numpy()
    sin_dec = mapear_decisiones(leer_decisiones([]), graf, ent_auto, prep, destinos)[0]
    l_auto = aplicar_decisiones(agregar_por_entidad(pares, ent_auto, graf), sin_dec)
    if cfg.propagar_por_web and len(l_auto):
        l_auto = aplicar_decisiones(
            propagar_por_web(l_auto, graf, ent_auto, prep, destinos, nz, comp), sin_dec
        )
    return l_auto, ent_auto


def _vinculos_con_decisiones(
    cfg: ConfigVinculacion, pares: pd.DataFrame, graf: pd.DataFrame, prep: dict, dec: pd.DataFrame,
    nz: Normalizador, comp: Comparador, log: Emisor,
) -> tuple[pd.DataFrame, np.ndarray, list, list]:  # fmt: skip
    """Vínculos con las decisiones humanas: primero se fusionan las grafías de una misma empresa y luego se aplican."""
    destinos = [d.clave for d in cfg.destinos]
    ent0 = graf["_COMP"].to_numpy()
    l0 = agregar_por_entidad(pares, ent0, graf)
    dmap0, _ = mapear_decisiones(dec, graf, ent0, prep, destinos)
    l0 = aplicar_decisiones(l0, dmap0, cfg.marca_sin_validar)
    ent1, uniones_emp = fusionar_por_empresa_comun(l0, ent0, graf, prep)
    vinculos = agregar_por_entidad(pares, ent1, graf)
    dmap, avisos = mapear_decisiones(dec, graf, ent1, prep, destinos)
    vinculos = aplicar_decisiones(vinculos, dmap, cfg.marca_sin_validar)
    if cfg.propagar_por_web and len(vinculos):
        n0 = len(vinculos)
        ev0 = int(
            vinculos.get("EVIDENCIA_EXTRA", pd.Series(dtype=str))
            .fillna("")
            .str.contains("sitio web")
            .sum()
        )
        vinculos = propagar_por_web(vinculos, graf, ent1, prep, destinos, nz, comp)
        vinculos = aplicar_decisiones(vinculos, dmap, cfg.marca_sin_validar)
        ev1 = int(vinculos["EVIDENCIA_EXTRA"].fillna("").str.contains("sitio web").sum())
        log(
            f"🌐 propagación por sitio web: {ev1 - ev0} pares con evidencia web ({len(vinculos) - n0} nuevos)"
        )
    return vinculos, ent1, uniones_emp, avisos


def _validar(
    cfg: ConfigVinculacion, ref: pd.DataFrame, l_auto: pd.DataFrame, graf_auto: pd.DataFrame, ent_auto: np.ndarray,
    prep: dict, avisos: list,
) -> tuple[pd.DataFrame, pd.DataFrame]:  # fmt: skip
    """Precisión y recall del motor automático contra la referencia (vacíos si no hay referencia)."""
    if not len(ref):
        return pd.DataFrame(), pd.DataFrame()
    destinos = [d.clave for d in cfg.destinos]
    ref_map, av_ref = mapear_decisiones(ref, graf_auto, ent_auto, prep, destinos)
    avisos += [f"referencia: {a}" for a in av_ref]
    return validar_contra_referencia(l_auto, ref_map, prep, destinos)


def ejecutar(
    cfg: ConfigVinculacion, verboso: bool = True, registro: Emisor | None = None
) -> Resultado:
    """Corre el cruce completo y devuelve todas las tablas. No escribe archivos (ver ``exportar_excel``).

    Args:
        cfg: configuración del cruce (la ancla, los destinos, los umbrales y las decisiones).
        verboso: si es False no se emite ningún mensaje de avance.
        registro: función que recibe cada mensaje de avance; por defecto va al logger
            ``record_linkage.flujo.vinculacion.ejecutar`` en nivel INFO.
    """
    t0 = time.time()
    log = _emisor(verboso, registro)
    nz = Normalizador(cfg)
    crudos, prep = _cargar(cfg, nz, log)
    dec, ref = _leer_decisiones_y_referencia(cfg, log)
    comp = _comparador(prep, nz, cfg)

    graf, un_a, pend_a, graf_auto = _deduplicar_la_ancla(cfg, prep, nz, comp, dec)
    ent_dest, un_dest = _deduplicar_los_destinos(cfg, prep, nz, dec)
    log(
        f"🧩 {cfg.ancla.clave}: {len(prep[cfg.ancla.clave]):,} filas → {len(graf):,} grafías → "
        f"{graf['_COMP'].nunique():,} empresas (antes de vínculos)"
    )

    pares = _candidatos_clasificados(cfg, graf, prep, nz, comp, log)
    l_auto, ent_auto = _vinculos_automaticos(cfg, pares, graf, graf_auto, prep, nz, comp)
    vinculos, ent1, uniones_emp, avisos = _vinculos_con_decisiones(
        cfg, pares, graf, prep, dec, nz, comp, log
    )
    avisos += [
        f"{cfg.ancla.clave}: decisión DUPLICADO con grafía inexistente {a} ↔ {b}"
        for a, b in graf.attrs.get("desconocidas", [])
    ]
    val, disc = _validar(cfg, ref, l_auto, graf_auto, ent_auto, prep, avisos)

    internos = {
        "nz": nz, "comp": comp, "prep": prep, "crudos": crudos, "graf": graf, "ent": ent1, "ent_auto": ent_auto,
        "L": vinculos, "L_auto": l_auto, "P": pares, "un_a": un_a, "pend_a": pend_a, "uniones_emp": uniones_emp,
        "ent_dest": ent_dest, "un_dest": un_dest, "dec": dec, "ref": ref, "disc": disc,
    }  # fmt: skip
    res = construir_tablas(cfg, internos, val, avisos, time.time() - t0)
    log(
        f"✅ listo en {res.segundos:.1f} s · invariantes {int(res.invariantes['CUMPLE'].sum())}/{len(res.invariantes)}"
    )
    return res
