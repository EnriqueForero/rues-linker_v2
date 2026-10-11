"""Hoja REVISION: lo que una persona debe mirar, de lo más urgente a lo menos.

Mezcla dos clases de fila, en este orden: los VÍNCULOS (pares con banda B o C, o con una decisión) y los
posibles DUPLICADOS entre grafías de la ancla (los pendientes de la deduplicación y los ya decididos).
Cada fila lleva una ``PRIORIDAD`` numérica del 1 al 6 que al final se reemplaza por su texto; los textos
son contrato (el Excel y quien llena la hoja los leen):

1. confirmar vínculo automático B · 2. validar decisión de revisión asistida · 3. candidato para empresa
sin vínculo · 4. candidato adicional · 5. ¿duplicado interno? · 6. decidido.

Una decisión de revisión ASISTIDA (cuyo ``AUTOR`` lleva la marca ``marca_sin_validar``) solo pide
validación si CAMBIA lo que el motor había decidido de forma automática con alta confianza (A):
confirmar un A no agrega riesgo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.tabla_exportadores import columna_de_operaciones

if TYPE_CHECKING:
    from record_linkage.flujo.vinculacion.tablas import EstadoTablas

__all__ = ["construir_revision"]

_TEXTO_PRIORIDAD = {
    1: "1 · confirmar vínculo automático B",
    2: "2 · validar decisión de revisión asistida",
    3: "3 · candidato para empresa sin vínculo",
    4: "4 · candidato adicional",
    5: "5 · ¿duplicado interno?",
    6: "6 · decidido",
}

_DECISIONES_DE_VINCULO = ("EMPRESA", "GRUPO", "RELACION_PROPIEDAD")


# ── filas de vínculos ────────────────────────────────────────────────────────
def _prioridad_de_vinculo(r: Any, marca: str, resultado_de_ent: dict) -> tuple[int, str]:
    """``(prioridad, estado)`` de un par: decidido (2 o 6), banda B (1) o candidato (3 o 4)."""
    decidido = bool(r.DECISION)
    asistida = decidido and bool(marca) and marca in str(r.AUTOR)
    # Solo pide firma lo que la decisión asistida CAMBIA respecto del automático de alta confianza (A):
    # confirmar un A no agrega riesgo.
    igual_a_auto_a = r.BANDA_AUTO == "A" and r.NIVEL_AUTO == r.DECISION
    cambia = (r.DECISION in _DECISIONES_DE_VINCULO and not igual_a_auto_a) or (
        r.DECISION == "NO_VINCULAR" and r.BANDA_AUTO in ("A", "B")
    )
    if decidido and asistida and cambia:
        return 2, "VALIDAR"
    if decidido:
        return 6, "DECIDIDO"
    if r.BANDA_AUTO == "B":
        return 1, "PENDIENTE"
    if resultado_de_ent.get(r.ENT) != "VINCULADO":
        return 3, "PENDIENTE"
    return 4, "PENDIENTE"


def _fila_de_vinculo(
    r: Any, prio: int, estado_rv: str, etiquetas: dict[str, str], operaciones: dict
) -> dict[str, object]:
    """La fila de REVISION de un par empresa × registro destino."""
    sug = f"{r.NIVEL_AUTO} ({r.BANDA_AUTO})" if r.NIVEL_AUTO else "—"
    # Hallazgo F3: si la empresa no trae país, la pregunta dice «( )» vacío: «¿«Mirelda Chocolates» () es…».
    return {
        "PRIORIDAD": prio,
        "ESTADO_REVISION": estado_rv,
        "PREGUNTA": (
            f"¿«{r.NOMBRE_EXPORTADOR}» ({r.PAIS_EXPORTADOR}) es la misma empresa (EMPRESA), del mismo grupo (GRUPO) "
            f"o no tiene relación (NO_VINCULAR) con {etiquetas[r.FUENTE_DESTINO]} «{r.NOMBRE_DESTINO}» ({r.PAIS_DESTINO})?"
        ),
        "SUGERENCIA_MOTOR": sug,
        "MOTIVO": r.MOTIVO,
        "ESTADO_ACTUAL": r.ESTADO,
        "TIPO": "VINCULO",
        "FUENTE": r.FUENTE_DESTINO,
        "CLAVE_A": r.CLAVE_GRAFIA,
        "NOMBRE_A": r.GRAFIA_ANCLA,
        "CLAVE_B": r.ID_DESTINO,
        "NOMBRE_B": r.NOMBRE_DESTINO,
        "DECISION": r.DECISION,
        "AUTOR": r.AUTOR,
        "FECHA": r.FECHA,
        "RAZON": r.RAZON,
        "ID_EXPORTADOR": r.ID_EXPORTADOR,
        "OPERACIONES_EXPORTADOR": operaciones.get(r.ENT, np.nan),
        "PAIS_EXPORTADOR": r.PAIS_EXPORTADOR,
        "PAIS_DESTINO": r.PAIS_DESTINO,
        "MISMO_PAIS": r.MISMO_PAIS,
        "CIUDAD_DESTINO": r.CIUDAD,
        "WEB_DESTINO": r.WEB,
        "MATRIZ_DESTINO": r.MATRIZ,
        "COMPARTIDAS": r.COMPARTIDAS,
        "SOBRA_EXPORTADOR": r.SOBRA_EXPORTADOR,
        "SOBRA_DESTINO": r.SOBRA_DESTINO,
        "SCORE_RUES_LINKER": r.SCORE_RUES_LINKER,
        "EVIDENCIA_EXTRA": r.EVIDENCIA_EXTRA,
    }


def _filas_de_vinculos(
    estado: EstadoTablas, resultado_de_ent: dict, operaciones: dict
) -> list[dict[str, object]]:
    """Una fila por par con banda B o C, o con una decisión, en el orden de la tabla de pares."""
    pares = estado.pares
    rv = (
        pares[(pares["BANDA_AUTO"].isin(["B", "C"])) | (pares["DECISION"] != "")].copy()
        if len(pares)
        else pares.copy()
    )
    marca = estado.cfg.marca_sin_validar
    filas: list[dict[str, object]] = []
    r: Any  # fila de itertuples(): sus campos son las columnas de ``pares``
    for r in rv.itertuples(index=False):
        prio, estado_rv = _prioridad_de_vinculo(r, marca, resultado_de_ent)
        filas.append(_fila_de_vinculo(r, prio, estado_rv, estado.etiquetas, operaciones))
    return filas


# ── filas de duplicados ──────────────────────────────────────────────────────
def _decisiones_de_duplicado(estado: EstadoTablas) -> dict[frozenset, Any]:
    """Las decisiones DUPLICADO de la ancla por par de claves; si un par se decidió dos veces, queda la última."""
    dec = estado.interno["dec"]
    por_par: dict[frozenset, Any] = {}
    if len(dec):
        duplicados = dec[(dec.TIPO == "DUPLICADO") & (estado.cfg.ancla.clave == dec.FUENTE)]
        r: Any  # fila de itertuples(): sus campos son las columnas de ``dec``
        for r in duplicados.itertuples():
            por_par[frozenset((r.CLAVE_A, r.CLAVE_B))] = r
    return por_par


def _pares_de_duplicados(
    estado: EstadoTablas, decisiones: dict[frozenset, Any]
) -> dict[tuple[int, int], str]:
    """Pares de grafías de la ancla a mostrar: los pendientes y los decididos, con su motivo.

    Las claves son posiciones ``(pos_a, pos_b)`` en ``grafias``. Los pendientes traen el motivo de la
    deduplicación; los decididos que no eran pendientes, el motivo ``decisión de revisión``.
    """
    pos_clave = {k: i for i, k in enumerate(estado.empresas.grafias["CLAVE"])}
    pares: dict[tuple[int, int], str] = {}
    r: Any  # fila de itertuples(): sus campos son las columnas de ``pend_a``
    for r in estado.interno["pend_a"].itertuples(index=False):
        pares[(int(r.pos_a), int(r.pos_b))] = r.MOTIVO
    for claves in decisiones:
        ks = sorted(claves)
        if len(ks) == 2 and all(k in pos_clave for k in ks):
            a, b = sorted((pos_clave[ks[0]], pos_clave[ks[1]]))
            pares.setdefault((a, b), "decisión de revisión")
    return pares


def _prioridad_de_duplicado(d0: Any, marca: str) -> tuple[int, str]:
    """``(prioridad, estado)`` de un par de grafías: sin decisión (5), asistida por validar (2) o decidido (6)."""
    if d0 is None:
        return 5, "PENDIENTE"
    if marca and marca in str(d0.AUTOR) and d0.DECISION == "MISMA_ENTIDAD":
        return 2, "VALIDAR"
    return 6, "DECIDIDO"


def _fila_de_duplicado(
    estado: EstadoTablas,
    pos_a: int,
    pos_b: int,
    motivo: str,
    decisiones: dict[frozenset, Any],
) -> dict[str, object] | None:
    """La fila de REVISION de un par de grafías de la ancla; ``None`` si ya están unidas y nadie lo decidió."""
    emp = estado.empresas
    g = emp.grafias
    ka, kb = g["CLAVE"].iat[pos_a], g["CLAVE"].iat[pos_b]
    d0 = decisiones.get(frozenset((ka, kb)))
    ent = estado.interno["ent"]
    ea, eb = int(ent[pos_a]), int(ent[pos_b])
    if ea == eb and d0 is None:
        return None  # quedaron unidas por otra vía
    prio, estado_rv = _prioridad_de_duplicado(d0, estado.cfg.marca_sin_validar)
    return {
        "PRIORIDAD": prio,
        "ESTADO_REVISION": estado_rv,
        "PREGUNTA": f"¿«{g['NOMBRE_ORIGINAL'].iat[pos_a]}» y «{g['NOMBRE_ORIGINAL'].iat[pos_b]}» son el MISMO exportador "
        f"(MISMA_ENTIDAD) o distintos (DISTINTA)?",
        "SUGERENCIA_MOTOR": "¿MISMA_ENTIDAD?",
        "MOTIVO": motivo,
        "ESTADO_ACTUAL": "unidas" if ea == eb else "separadas",
        "TIPO": "DUPLICADO",
        "FUENTE": estado.cfg.ancla.clave,
        "CLAVE_A": ka,
        "NOMBRE_A": g["NOMBRE_ORIGINAL"].iat[pos_a],
        "CLAVE_B": kb,
        "NOMBRE_B": g["NOMBRE_ORIGINAL"].iat[pos_b],
        "DECISION": d0.DECISION if d0 is not None else "",
        "AUTOR": d0.AUTOR if d0 is not None else "",
        "FECHA": d0.FECHA if d0 is not None else "",
        "RAZON": d0.RAZON if d0 is not None else "",
        "ID_EXPORTADOR": f"{emp.id_ent[ea]} / {emp.id_ent[eb]}" if ea != eb else emp.id_ent[ea],
        "OPERACIONES_EXPORTADOR": np.nan,
        "PAIS_EXPORTADOR": g["PAIS_ORIGINAL"].iat[pos_a],
    }


def _filas_de_duplicados(estado: EstadoTablas) -> list[dict[str, object]]:
    """Una fila por par de grafías de la ancla pendiente o decidido, ordenadas por posición."""
    decisiones = _decisiones_de_duplicado(estado)
    filas: list[dict[str, object]] = []
    for (pos_a, pos_b), motivo in sorted(_pares_de_duplicados(estado, decisiones).items()):
        fila = _fila_de_duplicado(estado, pos_a, pos_b, motivo, decisiones)
        if fila is not None:
            filas.append(fila)
    return filas


# ── hoja ─────────────────────────────────────────────────────────────────────
def construir_revision(estado: EstadoTablas, exportadores: pd.DataFrame) -> pd.DataFrame:
    """Hoja REVISION: vínculos primero, luego duplicados; ordenada por prioridad.

    Ordena por ``PRIORIDAD`` (1 a 6), operaciones de la empresa de mayor a menor e ``ID_EXPORTADOR`` (el
    orden es estable) y deja la prioridad con su texto. Sin filas, devuelve una tabla sin columnas.

    Args:
        estado: estado común de las secciones.
        exportadores: hoja EXPORTADORES con la columna auxiliar ``_ENT`` (de ella salen el resultado y
            las operaciones de cada empresa).
    """
    # Hallazgo F3: las columnas de la hoja salen de las claves de las filas: sin ningún par de vínculos (p. ej.
    # un cruce sin destinos) solo quedan las 19 de los duplicados, y sin filas no queda ninguna.
    operaciones_col = columna_de_operaciones(exportadores)
    operaciones = (
        exportadores.set_index("_ENT")[operaciones_col].to_dict() if operaciones_col else {}
    )
    resultado_de_ent = dict(zip(exportadores["_ENT"], exportadores["RESULTADO"], strict=True))
    filas = _filas_de_vinculos(estado, resultado_de_ent, operaciones)
    filas += _filas_de_duplicados(estado)
    revision = pd.DataFrame(filas)
    if len(revision):
        revision = revision.sort_values(
            ["PRIORIDAD", "OPERACIONES_EXPORTADOR", "ID_EXPORTADOR"],
            ascending=[True, False, True],
            kind="stable",
        ).reset_index(drop=True)
        revision["PRIORIDAD"] = revision["PRIORIDAD"].map(_TEXTO_PRIORIDAD)
    return revision
