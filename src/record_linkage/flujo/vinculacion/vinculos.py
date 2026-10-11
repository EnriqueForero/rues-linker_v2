"""Vínculos por empresa ancla: el mejor par por registro, las decisiones humanas y la fusión de grafías.

Después de clasificar, cada par (grafía ancla, registro destino) trae un nivel y una banda AUTOMÁTICOS. Este
módulo los lleva al nivel de la EMPRESA ancla y les aplica la revisión humana:

* ``agregar_por_entidad`` deja el mejor par por (empresa, fuente, registro) entre todas las grafías de la empresa;
* ``mapear_decisiones`` traduce las decisiones ``VINCULO`` de los archivos a (empresa, fuente, registro) y verifica
  que el registro sea el que la persona vio;
* ``aplicar_decisiones`` calcula el nivel final, el estado, la confianza y el origen de cada par; es idempotente;
* ``fusionar_por_empresa_comun`` une dos empresas ancla que son la MISMA EMPRESA que un mismo registro destino.

Los conjuntos y diccionarios que recorren estas funciones (``miembros``, los grupos de ``groupby``…) se recorren
en un orden que decide empates: no se sustituyen por otra estructura.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.constantes import ORDEN_BANDA, ORDEN_NIVEL
from record_linkage.flujo.vinculacion.decisiones import clave_texto

__all__ = [
    "agregar_por_entidad",
    "aplicar_decisiones",
    "fusionar_por_empresa_comun",
    "mapear_decisiones",
]

#: Columnas de la tabla que devuelve ``mapear_decisiones`` (el orden es parte del contrato).
_COLUMNAS_MAPA = (
    "ENT",
    "FUENTE_DESTINO",
    "pos_b",
    "DECISION",
    "AUTOR",
    "FECHA",
    "RAZON",
    "ARCHIVO",
    "ORDEN",
    "CLAVE_A_DEC",
)

# Hallazgo F3: la lista no incluye NIVEL_FINAL, ESTADO, CONFIANZA ni ORIGEN (se reescriben, pero no se descartan):
# al aplicar dos veces los valores coinciden, pero esas columnas quedan antes de las de decisión y cambia el orden.
#: Columnas de decisión que ``aplicar_decisiones`` descarta de ``L`` antes de volver a aplicarlas.
_COLUMNAS_DE_DECISION = [
    "DECISION",
    "AUTOR",
    "FECHA",
    "RAZON",
    "ARCHIVO",
    "ORDEN",
    "CLAVE_A_DEC",
    "DECISION_VALIDADA",
]

#: Cuántos de los registros con otro nombre muestra el error de ``mapear_decisiones``.
_ERRORES_MOSTRADOS = 10


def agregar_por_entidad(
    pares: pd.DataFrame, ent_de_g: np.ndarray, graf: pd.DataFrame
) -> pd.DataFrame:
    """Mejor par por (empresa ancla, fuente, registro destino) entre todas sus grafías.

    ``ent_de_g[g]`` es la empresa de la grafía ``g``. Agrega las columnas ``ENT`` y ``GRAFIA_ANCLA`` (la
    grafía del mejor par). El mejor es el de mejor nivel y banda automáticos y, a igual rango, el de
    mayor ``SCORE_RUES_LINKER``; el desempate es ESTABLE.
    """
    if pares.empty:
        # Hallazgo F3: la tabla vacía no trae ``GRAFIA_ANCLA`` (la que no está vacía sí): el esquema depende de los datos.
        return pares.assign(ENT=pd.Series(dtype=int))
    x = pares.copy()
    x["ENT"] = ent_de_g[x["g"].to_numpy()]
    x["GRAFIA_ANCLA"] = graf["NOMBRE_ORIGINAL"].to_numpy()[x["g"].to_numpy()]
    x["_r"] = x["NIVEL_AUTO"].map(ORDEN_NIVEL).fillna(9) * 10 + x["BANDA_AUTO"].map(
        ORDEN_BANDA
    ).fillna(9)
    x = x.sort_values(
        ["ENT", "FUENTE_DESTINO", "pos_b", "_r", "SCORE_RUES_LINKER"],
        ascending=[True, True, True, True, False],
        kind="stable",
    )
    return (
        x.drop_duplicates(["ENT", "FUENTE_DESTINO", "pos_b"])
        .drop(columns="_r")
        .reset_index(drop=True)
    )


# ── mapear_decisiones ────────────────────────────────────────────────────────
def _indices_de_registros(
    prep: dict, destinos: list[str]
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, int]]]:
    """Por fuente destino: el nombre actual de cada ``ID_REGISTRO`` (sin espacios de más y en minúsculas
    plegadas) y su posición en ``prep``."""
    # ``strict=False`` como el ``zip`` a secas del original: no se endurece a ``strict=True`` para no cambiar el
    # comportamiento con tablas mal formadas (p. ej., una columna con la etiqueta repetida).
    nombre_ok = {
        f: dict(
            zip(
                prep[f]["ID_REGISTRO"],
                clave_texto(prep[f]["NOMBRE_ORIGINAL"]).str.casefold(),
                strict=False,
            )
        )
        for f in destinos
    }
    pos_b = {f: {k: i for i, k in enumerate(prep[f]["ID_REGISTRO"])} for f in destinos}
    return nombre_ok, pos_b


def _aviso_de_omision(
    r: Any, pos_g: dict[str, int], pos_b: dict[str, dict[str, int]]
) -> str | None:
    """El aviso por el que una decisión se omite (fuente, grafía o registro que no existen), o ``None``."""
    if r.FUENTE not in pos_b:
        return f"decisión con FUENTE desconocida {r.FUENTE!r}: {r.CLAVE_A} ↔ {r.CLAVE_B}"
    if r.CLAVE_A not in pos_g:
        return (
            f"{r.FUENTE}: la grafía del exportador '{r.CLAVE_A}' no existe en esta base (se omite)"
        )
    if r.CLAVE_B not in pos_b[r.FUENTE]:
        return f"{r.FUENTE}: el registro '{r.CLAVE_B}' no existe en esta base (se omite)"
    return None


def _traducir_decisiones(
    d: pd.DataFrame,
    pos_g: dict[str, int],
    ent_de_g: np.ndarray,
    nombre_ok: dict[str, dict[str, str]],
    pos_b: dict[str, dict[str, int]],
) -> tuple[list[tuple], list[str], list[str]]:
    """Traduce cada decisión a una fila ``(ENT, FUENTE, pos_b, DECISION, … , CLAVE_A, orden de lectura)``.

    Devuelve ``(filas, avisos, errores)``. Una decisión con fuente, grafía o registro inexistente se omite
    con un aviso; una cuyo ``NOMBRE_B`` no es el del registro actual (``CLAVE_B`` apunta a otra empresa)
    va a ``errores``. Un ``NOMBRE_B`` vacío no se verifica.
    """
    avisos: list[str] = []
    filas: list[tuple] = []
    errores: list[str] = []
    r: Any  # fila de itertuples(): sus campos son las columnas de ``d``
    for k, r in enumerate(d.itertuples(index=False)):
        aviso = _aviso_de_omision(r, pos_g, pos_b)
        if aviso is not None:
            avisos.append(aviso)
            continue
        actual = nombre_ok[r.FUENTE][r.CLAVE_B]
        # Hallazgo F3: arma una Serie de pandas por cada decisión; con muchas decisiones debería normalizar
        # la columna NOMBRE_B entera de una vez (la regla de «nada de bucles sobre filas» del repositorio).
        visto = clave_texto(pd.Series([r.NOMBRE_B])).str.casefold().iat[0]
        if visto and visto != actual:
            errores.append(
                f"{r.FUENTE} · {r.CLAVE_B}: la decisión dice {visto!r}, el archivo dice {actual!r}"
            )
            continue
        filas.append(
            (
                int(ent_de_g[pos_g[r.CLAVE_A]]),
                r.FUENTE,
                pos_b[r.FUENTE][r.CLAVE_B],
                r.DECISION,
                r.AUTOR,
                r.FECHA,
                r.RAZON,
                r.ARCHIVO,
                int(r.ORDEN),
                r.CLAVE_A,
                k,
            )
        )
    return filas, avisos, errores


def _exigir_nombres_iguales(errores: list[str]) -> None:
    """Falla si alguna decisión apunta a un registro destino con otro nombre (muestra las primeras)."""
    if errores:
        raise ValueError(
            f"Qué pasó: {len(errores)} decisiones apuntan a un registro destino con OTRO nombre (primeras):\n  "
            + "\n  ".join(errores[:_ERRORES_MOSTRADOS])
            + "\nPor qué importa: sin identificador nativo, la clave es la fila del Excel; "
            "si la hoja cambió de orden, la decisión aplicaría a otra empresa.\n"
            "Qué hacer: use decisiones hechas sobre ESTOS archivos o corrija CLAVE_B."
        )


def _avisos_de_conflicto(m: pd.DataFrame, prep: dict) -> list[str]:
    """Un aviso por cada (empresa, fuente, registro) con decisiones distintas; manda la última."""
    avisos: list[str] = []
    for (_, f, p), grp in m.groupby(["ENT", "FUENTE_DESTINO", "pos_b"]):
        if grp["DECISION"].nunique() > 1:
            ult = grp.iloc[-1]
            historial = " → ".join(
                f"{a}: {d}" for a, d in zip(grp["ARCHIVO"], grp["DECISION"], strict=False)
            )
            avisos.append(
                f"{f}: «{ult['CLAVE_A_DEC'].split('|', 1)[-1]}» ↔ {prep[f]['ID_REGISTRO'].iat[p]} "
                f"«{prep[f]['NOMBRE_ORIGINAL'].iat[p]}» tiene decisiones distintas "
                f"({historial}); se aplica la última"
            )
    return avisos


def mapear_decisiones(
    dec: pd.DataFrame,
    graf: pd.DataFrame,
    ent_de_g: np.ndarray,
    prep: dict,
    destinos: list[str],
) -> tuple[pd.DataFrame, list[str]]:
    """Traduce las decisiones VINCULO a (ENT, FUENTE, pos_b). Verifica que el destino sea el mismo registro.

    ``dec`` es la tabla de ``leer_decisiones``. Devuelve ``(mapa, avisos)``: ``mapa`` tiene las columnas de
    ``_COLUMNAS_MAPA``, una fila por (empresa, fuente, registro) con la ÚLTIMA decisión (orden de archivo y de
    fila), y ``avisos`` las decisiones omitidas y los conflictos entre archivos.

    Raises:
        ValueError: alguna decisión apunta a un registro destino con OTRO nombre (muestra las primeras 10).
    """
    if dec.empty:
        return pd.DataFrame(columns=list(_COLUMNAS_MAPA)), []
    d = dec[dec["TIPO"] == "VINCULO"].copy()
    pos_g = {k: i for i, k in enumerate(graf["CLAVE"])}
    nombre_ok, pos_b = _indices_de_registros(prep, destinos)
    filas, avisos, errores = _traducir_decisiones(d, pos_g, ent_de_g, nombre_ok, pos_b)
    _exigir_nombres_iguales(errores)
    m = pd.DataFrame(filas, columns=[*_COLUMNAS_MAPA, "_fila"])
    # La última decisión manda (orden de archivo y de fila); los conflictos se informan.
    m = m.sort_values(["ORDEN", "_fila"], kind="stable")
    avisos += _avisos_de_conflicto(m, prep)
    m = m.drop_duplicates(["ENT", "FUENTE_DESTINO", "pos_b"], keep="last").drop(columns="_fila")
    return m.reset_index(drop=True), avisos


# ── aplicar_decisiones ───────────────────────────────────────────────────────
def _asistida(x: pd.DataFrame, tiene: pd.Series, marca: str) -> pd.Series:
    """Decisiones de revisión ASISTIDA: las que traen una decisión y cuyo AUTOR contiene ``marca``."""
    if marca:
        return tiene & x["AUTOR"].astype(str).str.contains(re.escape(marca), regex=True)
    return tiene & False


def _estado(
    x: pd.DataFrame,
    tiene: pd.Series,
    vinc: np.ndarray,
    asistida: pd.Series,
    auto_a_igual: pd.Series,
    auto_vinc: pd.Series,
) -> np.ndarray:
    """ESTADO de cada par; el ORDEN de las condiciones es la prioridad (``np.select`` toma la primera)."""
    return np.select(
        [
            tiene & vinc & ~asistida,
            tiene & vinc & asistida & auto_a_igual,
            tiene & vinc & asistida,
            tiene & (x["DECISION"] == "NO_VINCULAR"),
            tiene & (x["DECISION"] == "RELACION_PROPIEDAD"),
            ~tiene & (x["BANDA_AUTO"] == "A") & auto_vinc,
            ~tiene & (x["BANDA_AUTO"] == "B") & auto_vinc,
        ],
        [
            "VINCULADO (revisión)",
            "VINCULADO (automático A)",
            "VINCULADO (revisión asistida · validar)",
            "DESCARTADO (revisión)",
            "RELACIÓN DE PROPIEDAD (revisión)",
            "VINCULADO (automático A)",
            "VINCULADO (automático B · revisar)",
        ],
        "CANDIDATO (pendiente de revisión)",
    )


def _confianza(
    vinc: np.ndarray, auto_a_igual: pd.Series, tiene: pd.Series, asistida: pd.Series
) -> np.ndarray:
    """CONFIANZA de los pares vinculados.

    ALTA: automático A (con o sin confirmación) o decisión validada por una persona.
    MEDIA: automático B sin revisar, o decisión de revisión asistida aún sin validar.
    """
    return np.select(
        [vinc & (auto_a_igual.to_numpy() | (tiene & ~asistida).to_numpy()), vinc],
        ["ALTA", "MEDIA"],
        "",
    )


def _origen(x: pd.DataFrame, tiene: pd.Series, auto_vinc: pd.Series) -> np.ndarray:
    """ORIGEN de cada par: si lo propuso el motor y qué hizo la revisión con él."""
    # Hallazgo F3: un candidato (no vinculado por el motor) que una persona RECHAZA con NO_VINCULAR cae en
    # la última condición y queda como «agregado por revisión», aunque no se agregó nada.
    return np.select(
        [
            tiene & auto_vinc & (x["DECISION"] == x["NIVEL_AUTO"]),
            tiene & auto_vinc & (x["DECISION"] == "NO_VINCULAR"),
            tiene & auto_vinc,
            tiene,
        ],
        [
            "automático, confirmado por revisión",
            "automático, descartado por revisión",
            "automático, nivel corregido por revisión",
            "agregado por revisión",
        ],
        np.where(auto_vinc, "automático", "candidato"),
    )


def aplicar_decisiones(
    L: pd.DataFrame, dmap: pd.DataFrame, marca: str = "(verificar)"
) -> pd.DataFrame:
    """Nivel final, estado, confianza y origen de cada par (idempotente: parte siempre del automático).

    ``L`` son los pares por empresa (``agregar_por_entidad``) y ``dmap`` el mapa de ``mapear_decisiones``.
    Descarta de ``L`` las columnas de decisión que ya trajera, une con ``dmap`` (``how="outer"``: una
    decisión sobre un par que el motor no propuso agrega la fila) y agrega ``DECISION_VALIDADA``,
    ``NIVEL_FINAL``, ``ESTADO``, ``CONFIANZA`` y ``ORIGEN``. Una decisión cuyo AUTOR contiene ``marca``
    es de revisión asistida (no validada por una persona); ``marca=""`` desactiva esa distinción.
    """
    L = L.drop(columns=[c for c in _COLUMNAS_DE_DECISION if c in L.columns])
    x = L.merge(dmap, on=["ENT", "FUENTE_DESTINO", "pos_b"], how="outer", indicator=True)
    x["NIVEL_AUTO"] = x["NIVEL_AUTO"].fillna("")
    x["BANDA_AUTO"] = x["BANDA_AUTO"].fillna("")
    x["DECISION"] = x["DECISION"].fillna("")
    auto_vinc = x["BANDA_AUTO"].isin(["A", "B"]) & x["NIVEL_AUTO"].isin(["EMPRESA", "GRUPO"])
    tiene = x["DECISION"] != ""
    x["AUTOR"] = x["AUTOR"].fillna("") if "AUTOR" in x.columns else ""
    asistida = _asistida(x, tiene, marca)
    x["DECISION_VALIDADA"] = np.where(tiene, np.where(asistida, "NO (revisión asistida)", "SÍ"), "")
    nivel = np.where(tiene, x["DECISION"], np.where(auto_vinc, x["NIVEL_AUTO"], ""))
    x["NIVEL_FINAL"] = np.where(pd.Series(nivel).isin(["NO_VINCULAR"]), "", nivel)
    vinc = pd.Series(x["NIVEL_FINAL"]).isin(["EMPRESA", "GRUPO"]).to_numpy()
    auto_a_igual = (x["BANDA_AUTO"] == "A") & (x["NIVEL_AUTO"] == pd.Series(x["NIVEL_FINAL"]))
    x["ESTADO"] = _estado(x, tiene, vinc, asistida, auto_a_igual, auto_vinc)
    x["CONFIANZA"] = _confianza(vinc, auto_a_igual, tiene, asistida)
    x["ORIGEN"] = _origen(x, tiene, auto_vinc)
    return x.drop(columns="_merge")


# ── fusionar_por_empresa_comun ───────────────────────────────────────────────
def _raiz(padre: list[int], v: int) -> int:
    """Raíz de ``v`` en el union-find ``padre`` (con compresión por mitades)."""
    while padre[v] != v:
        padre[v] = padre[padre[v]]
        v = padre[v]
    return v


def _miembros_por_entidad(ent_de_g: np.ndarray) -> defaultdict[int, set[int]]:
    """Las grafías (posiciones en ``graf``) de cada empresa."""
    miembros: defaultdict[int, set[int]] = defaultdict(set)
    for gi, e in enumerate(ent_de_g):
        miembros[int(e)].add(gi)
    return miembros


def _cruza_un_veto(vetos: set[tuple[int, int]], miembros_a: set[int], miembros_b: set[int]) -> bool:
    """¿Alguna grafía de un conjunto fue separada por una decisión DISTINTA de alguna del otro?"""
    return any((min(x, y), max(x, y)) in vetos for x in miembros_a for y in miembros_b)


def fusionar_por_empresa_comun(
    L: pd.DataFrame, ent_de_g: np.ndarray, graf: pd.DataFrame, prep: dict
) -> tuple[np.ndarray, list[tuple[int, int, str]]]:
    """Dos grafías que son la MISMA EMPRESA que un mismo registro destino son la misma empresa.

    Solo con vínculos EMPRESA de confianza ALTA (automático A o decidido) y dentro del mismo
    país; nunca une grafías que una decisión DISTINTA separó.

    Devuelve ``(ent_nueva, uniones)``: la empresa de cada grafía tras fusionar (numeradas en orden de
    primera aparición) y una ``(empresa, otra_empresa, motivo)`` por cada fusión hecha. Los vetos salen de
    ``graf.attrs["vetos"]``.
    """
    vetos = graf.attrs.get("vetos", set())
    ok = L[(L["NIVEL_FINAL"] == "EMPRESA") & (L["CONFIANZA"] == "ALTA")]
    iso_ent = pd.Series(graf["PAIS_ISO3"].to_numpy()).groupby(ent_de_g).first().to_dict()
    n = int(ent_de_g.max()) + 1 if len(ent_de_g) else 0
    padre = list(range(n))
    miembros = _miembros_por_entidad(ent_de_g)
    uniones: list[tuple[int, int, str]] = []
    for (f, p), grp in ok.groupby(["FUENTE_DESTINO", "pos_b"], sort=True):
        ents = sorted({int(e) for e in grp["ENT"]})
        for e2 in ents[1:]:
            a, b = _raiz(padre, ents[0]), _raiz(padre, e2)
            if a == b or iso_ent.get(ents[0]) != iso_ent.get(e2):
                continue
            if _cruza_un_veto(vetos, miembros[a], miembros[b]):
                continue
            padre[max(a, b)] = min(a, b)
            miembros[min(a, b)] |= miembros[max(a, b)]
            uniones.append((ents[0], e2, f"misma empresa que {f} {prep[f]['ID_REGISTRO'].iat[p]}"))
    nuevo = pd.factorize(np.array([_raiz(padre, int(e)) for e in ent_de_g]))[0]
    return nuevo, uniones
