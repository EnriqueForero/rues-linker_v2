"""record_linkage.salida.completar — Completa el contrato de salida 1.0 (F1.9).

Qué hace
--------
Recibe la correlativa y el golden tal como salen del motor (L5) y devuelve
las dos tablas con la forma del estándar (``contrato.py``):

* ``ID_REGISTRO`` = ``<SRC>-<id nativo>`` si ``col_id`` nombra una columna
  única y no vacía por fila en la fuente; si no, ``<SRC>-F<ORIGINAL_INDEX>``.
  La regla aplicada por fuente, y el motivo cuando se cae a la fila, van en
  el reporte (y de ahí al manifiesto): nada se decide en silencio.
* ``ID_ENTIDAD`` = ``NIT-<base sin DV>`` si el grupo adopta un identificador
  válido (``matching.identificadores.base_canonica`` con al menos
  ``LONGITUD_MINIMA_BASE`` dígitos); si no, ``ENT-<16 hex de SHA-256 de los
  ID_REGISTRO del grupo ordenados>``: determinista por contenido. La
  herencia entre corridas (``entidades_ids.parquet``) la añade F2.4.
  Medido (F1.9): el motor puede dejar DOS grupos con el mismo ``NIT_FINAL``
  (en ``dataset_sintetico_p2_extra_features.csv`` pasa con 900111222 y
  800333444: nombres que no alcanzan el umbral). Como el contrato exige un
  ``ID_GRUPO`` ↔ un ``ID_ENTIDAD``, solo el grupo principal de cada base (el
  de más registros; empate: menor ``ID_REGISTRO``) lleva ``NIT-…``; los
  demás reciben ``ENT-…`` y el reporte los cuenta en ``id_entidad``.
* ``SCORE_PAR`` = mayor puntaje en ``L3_scoring/scored.db`` entre los pares
  que conectan el registro con otro de su grupo. Se lee por lotes con SQL y
  se reduce con NumPy (``np.maximum.at``): sin bucles de Python por fila ni
  por par. Si no hay ``scored.db`` (ruta ``dedupe``), queda nulo y el reporte
  lo dice.
* ``METODO_UNION`` ∈ {``identificador``, ``nombre``, ``sin_pareja``}:
  ``sin_pareja`` si el grupo tiene un registro; ``identificador`` si el
  registro trae identificador válido cuya base es la de ``NIT_FINAL`` del
  grupo Y otro miembro del grupo comparte esa base (sin pareja de base, el
  dueño de ``NIT_FINAL`` se declara ``nombre``: así lo unió el motor; el
  reporte cuenta esos casos en ``identificador_sin_pareja_de_base``);
  ``nombre`` en el resto. La regla de «base válida» es UNA y es la
  del MOTOR (NitProcessor, L1): la base del registro es ``NIT_BASE`` donde
  ``NIT_VALID``, sin ninguna reducción propia, y la base del grupo es el
  ``NIT_BASE`` de la fila cuyo ``NIT_OK == NIT_FINAL`` (``NIT_FINAL`` es por
  construcción el ``NIT_OK`` de un miembro del grupo:
  ``golden.selector.select_best_nit_batch``). Así un NIT que la fuente trae
  como flotante (``900111222.0``) o con prefijo se compara como lo vio el
  motor, y lo que el motor unió por nombre (una cédula de 8 dígitos frente al
  NIT de 10 que la contiene) no se declara unido por identificador. Medido en
  el banco (r5): reducir ``NIT_OK`` con ``bases_validas`` discrepaba de
  ``NIT_BASE`` en 3.573 de 22.455 filas válidas. Solo cuando la correlativa
  no trae las técnicas (no viene del motor) se recalcula desde la columna de
  identificador con ``bases_validas``, y el reporte lo dice
  (``identificador``). El mismo cálculo cuenta los grupos con dos bases
  válidas distintas (``grupos_con_bases_distintas``), que es el QA de
  identificador del flujo (``flujo/cruce.py`` lo lee del manifiesto).
  ``bases_validas`` (base canónica del preámbulo) queda SOLO para
  ``ID_ENTIDAD``; por eso un identificador de 6 dígitos que el motor validó
  une por ``identificador`` y recibe ``ENT-…``.
* ``CONFIANZA`` = la del grupo en el golden. Sin golden (``dedupe``) queda
  nula y el reporte lo dice; F2.12 unifica la regla.
* Columnas: las 12 fijas primero, después TODAS las de la fuente, después las
  extra del motor (``REGIMEN_AUTO``…). Las técnicas (``COLUMNAS_TECNICAS``)
  salen del entregable: quedan en ``_trabajo/`` (L1/L5 del ``dir_trabajo``).
* Colisiones: una columna de la fuente que se llame como una de las que aquí
  se añaden se conserva renombrada ``<col>_FUENTE``; el renombre va al
  reporte y al diccionario. Si se llama como una columna que el motor ya
  escribió (``SRC``, ``ID_GRUPO``…), el motor la sobrescribió antes de
  llegar aquí: se reporta como no recuperable.
* Golden: ``ID_ENTIDAD``, conteos enteros, ``REQUIRES_REVIEW`` booleano (solo
  si hace falta: si ya vienen así, no se tocan), sin columnas de la
  correlativa pegadas y sin filas huérfanas (``ID_GRUPO`` que no está en la
  correlativa; se retiran y se cuentan). Un grupo de la correlativa SIN fila
  en el golden es un defecto del motor y se levanta ``ContratoSalidaError``.
* ``ID_GRUPO`` de texto (``dedupe`` lo etiqueta ``C<n>``/``S<n>`` por
  régimen) se recodifica a entero por orden de primera aparición en
  ``ORIGINAL_INDEX`` — determinista por contenido — y el reporte lo declara.
  En ``linkage()`` ya es entero y no se toca.

Lo que NO hace
--------------
No cambia ninguna decisión del motor: ni grupos, ni identidad adoptada, ni
métricas. Esta fase (F1) no toca L1…L5.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-10-06  ·  Version: 0.23.0
"""

from __future__ import annotations

import hashlib
import sqlite3
import warnings
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import contrato
from ..engine.scorer import _a_booleano
from ..golden.metricas import COLUMNAS_METRICAS_GRUPO, metricas_de_calidad, metricas_de_grupo
from ..matching.identificadores import bases_validas
from ..pipeline.errores import ContratoSalidaError, mensaje_accionable

__all__ = [
    "COLUMNAS_QUE_ANADE",
    "ReporteCompletar",
    "anexar_score_par",
    "bases_del_motor",
    "completar_correlativa",
    "grupos_con_bases_distintas",
    "score_par_desde_scored_db",
]

#: Columnas del contrato que este módulo produce. Si la fuente trae una con
#: el mismo nombre, se conserva renombrada ``<col>_FUENTE``.
COLUMNAS_QUE_ANADE: tuple[str, ...] = (
    "ID_REGISTRO",
    "ID_ENTIDAD",
    "SCORE_PAR",
    "METODO_UNION",
    "CONFIANZA",
)

#: Ruta relativa al ``dir_trabajo`` donde el Orchestrator deja los pares puntuados.
RUTA_SCORED_DB = Path("L3_scoring") / "scored.db"

#: Filas por lote al leer ``scored.db``. Un millón de pares son ~24 MB en
#: NumPy; mantiene el pico de RAM acotado con decenas de millones de pares.
PARES_POR_LOTE = 1_000_000

_SUFIJO_COLISION = "_FUENTE"
_COLUMNAS_ENTERAS_GOLDEN: tuple[str, ...] = (
    "SOURCES_COUNT",
    "RECORD_COUNT",
    "NAME_VARIATIONS",
    "NIT_VARIATIONS",
)


@dataclass(frozen=True)
class ReporteCompletar:
    """Qué hizo ``completar_correlativa`` y por qué. Viaja al manifiesto.

    Attributes:
        renombres: ``{columna_en_fuente: columna_en_salida}`` por colisión.
        renombres_canonicos: ``{columna_del_usuario: columna_canónica}`` que
            el motor aplicó en la ingesta (``col_name`` → ``RAZON_SOCIAL``…)
            y que por eso ya no aparece con su nombre original.
        colisiones_no_recuperables: columnas de la fuente que el motor ya
            había sobrescrito antes de llegar aquí.
        id_registro: por fuente, ``{"regla": "col_id" | "fila", ...}`` y el
            motivo cuando ``col_id`` no sirvió.
        id_grupo: si hubo que recodificar ``ID_GRUPO`` de texto a entero.
        id_entidad: cuántos grupos recibieron ``NIT-…`` y cuántos ``ENT-…``,
            y los grupos que comparten NIT con otro y por eso se desplazaron
            a ``ENT-…`` (el motor los dejó separados; ver docstring del módulo).
        identificador: de dónde salió la base de cada registro (``NIT_BASE``/
            ``NIT_VALID`` del motor, o la columna de la fuente si no vienen),
            cómo se eligió la base del grupo, ``grupos_sin_fila_de_nit_final``
            (grupos cuyo ``NIT_FINAL`` no es el ``NIT_OK`` de ningún miembro)
            y ``grupos_con_bases_distintas``: grupos que mezclan dos bases
            válidas distintas (el QA de identificador del flujo).
        score_par: origen (ruta de ``scored.db``) y conteos, o el motivo de
            que quede nulo.
        confianza: de dónde salió, o el motivo de que quede nula.
        golden: filas huérfanas retiradas y columnas pegadas retiradas.
        columnas_tecnicas_retiradas: las de ``COLUMNAS_TECNICAS`` presentes.
        columnas_fuente: las columnas de la fuente en el orden de salida.
        prioridad_fuentes: prioridad con la que se repara ``PRIMARY_SOURCE``
            (la del Orchestrator: ``source_quality_weights`` del perfil).
    """

    renombres: dict[str, str] = field(default_factory=dict)
    renombres_canonicos: dict[str, str] = field(default_factory=dict)
    colisiones_no_recuperables: list[str] = field(default_factory=list)
    id_registro: dict[str, dict[str, str]] = field(default_factory=dict)
    id_grupo: dict[str, Any] = field(default_factory=dict)
    id_entidad: dict[str, Any] = field(default_factory=dict)
    identificador: dict[str, Any] = field(default_factory=dict)
    score_par: dict[str, Any] = field(default_factory=dict)
    confianza: dict[str, Any] = field(default_factory=dict)
    golden: dict[str, Any] = field(default_factory=dict)
    columnas_tecnicas_retiradas: list[str] = field(default_factory=list)
    columnas_fuente: list[str] = field(default_factory=list)
    prioridad_fuentes: list[str] = field(default_factory=list)

    def a_dict(self) -> dict[str, Any]:
        return {
            "renombres": dict(self.renombres),
            "renombres_canonicos": dict(self.renombres_canonicos),
            "colisiones_no_recuperables": list(self.colisiones_no_recuperables),
            "id_registro": {k: dict(v) for k, v in self.id_registro.items()},
            "id_grupo": dict(self.id_grupo),
            "id_entidad": dict(self.id_entidad),
            "identificador": dict(self.identificador),
            "score_par": dict(self.score_par),
            "confianza": dict(self.confianza),
            "golden": dict(self.golden),
            "columnas_tecnicas_retiradas": list(self.columnas_tecnicas_retiradas),
            "columnas_fuente": list(self.columnas_fuente),
            "prioridad_fuentes": list(self.prioridad_fuentes),
        }


# ─────────────────────────────────────────────────────────────────────────────
# SCORE_PAR desde scored.db
# ─────────────────────────────────────────────────────────────────────────────


def score_par_desde_scored_db(
    correl: pd.DataFrame,
    ruta_db: Path,
    *,
    pares_por_lote: int = PARES_POR_LOTE,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Mayor puntaje por registro entre los pares que lo unen a su grupo.

    ``scored_pairs(idx_0, idx_1, score)`` indexa por posición en el frame de
    L1, que es ``ORIGINAL_INDEX`` (verificado: ``L1_prep/data.parquet`` trae
    ``ORIGINAL_INDEX == posición``). Si se colapsaron duplicados exactos, hay
    que llamar ANTES de expandir la correlativa, cuando ``ORIGINAL_INDEX``
    todavía es la posición compacta (``api.linkage`` lo hace así).

    Returns:
        (arreglo float alineado con ``correl``, NaN donde no hay par; info).
    """
    pos = correl["ORIGINAL_INDEX"].to_numpy(dtype=np.int64)
    grupos = correl["ID_GRUPO"].to_numpy(dtype=np.int64)
    n = int(pos.max()) + 1 if len(pos) else 0
    grupo_por_pos = np.full(n, -1, dtype=np.int64)
    grupo_por_pos[pos] = grupos
    mejor = np.full(n, -np.inf, dtype=np.float64)
    pares = 0
    con = sqlite3.connect(f"file:{ruta_db}?mode=ro", uri=True)
    try:
        lotes = pd.read_sql_query(
            "SELECT idx_0, idx_1, score FROM scored_pairs", con, chunksize=pares_por_lote
        )
        for lote in lotes:
            pares += len(lote)
            i0 = lote["idx_0"].to_numpy(dtype=np.int64)
            i1 = lote["idx_1"].to_numpy(dtype=np.int64)
            s = lote["score"].to_numpy(dtype=np.float64)
            dentro = (i0 >= 0) & (i0 < n) & (i1 >= 0) & (i1 < n)
            i0, i1, s = i0[dentro], i1[dentro], s[dentro]
            g0 = grupo_por_pos[i0]
            mismo = (g0 >= 0) & (g0 == grupo_por_pos[i1])
            np.maximum.at(mejor, i0[mismo], s[mismo])
            np.maximum.at(mejor, i1[mismo], s[mismo])
    finally:
        con.close()
    salida = mejor[pos]
    salida[~np.isfinite(salida)] = np.nan
    info = {
        "origen": str(ruta_db),
        "pares_leidos": int(pares),
        "registros_con_score": int(np.isfinite(salida).sum()),
    }
    return salida, info


def anexar_score_par(
    correl: pd.DataFrame, dir_trabajo: Path | None
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Añade ``SCORE_PAR`` a la correlativa si existe ``scored.db``; si no, nulo y motivo."""
    ruta = None if dir_trabajo is None else Path(dir_trabajo) / RUTA_SCORED_DB
    if ruta is None or not ruta.is_file():
        correl["SCORE_PAR"] = np.nan
        donde = "sin dir_trabajo" if ruta is None else str(ruta)
        return correl, {
            "origen": None,
            "motivo": f"no existe {RUTA_SCORED_DB} ({donde}): SCORE_PAR queda nulo. "
            "La ruta dedupe() no puntúa pares en scored.db; linkage()/link() sí.",
        }
    try:
        valores, info = score_par_desde_scored_db(correl, ruta)
    except sqlite3.DatabaseError as exc:
        correl["SCORE_PAR"] = np.nan
        return correl, {
            "origen": None,
            "motivo": f"{ruta} no se pudo leer ({exc}): SCORE_PAR queda nulo.",
        }
    correl["SCORE_PAR"] = valores
    return correl, info


# ─────────────────────────────────────────────────────────────────────────────
# Piezas
# ─────────────────────────────────────────────────────────────────────────────


def _nombre_efectivo(col: str, presentes: Collection[str], canonicos: Mapping[str, str]) -> str:
    """Cómo se llama hoy en la correlativa una columna de la fuente.

    El Orchestrator renombra ``col_nit``/``col_name``/``col_ciudad`` a las
    canónicas (``NIT``…) en la ingesta; ``deduplicate_unified`` (dedupe) NO
    renombra: copia. El canónico se aplica solo si la columna original ya
    no está: así el entregable dice de verdad qué columnas son del usuario.
    """
    return col if col in presentes else canonicos.get(col, col)


def _columnas_de_fuentes(
    fuentes: Mapping[str, pd.DataFrame | Sequence[str]],
    canonicos: Mapping[str, str] | None,
    presentes: Collection[str],
) -> list[str]:
    """Unión de las columnas de las fuentes, en orden de primera aparición,
    con el nombre que tienen en la correlativa (``_nombre_efectivo``)."""
    canonicos = dict(canonicos or {})
    vistas: dict[str, None] = {}
    for fuente in fuentes.values():
        columnas = fuente.columns if isinstance(fuente, pd.DataFrame) else fuente
        for col in columnas:
            vistas.setdefault(_nombre_efectivo(str(col), presentes, canonicos), None)
    return list(vistas)


def _resolver_colisiones(
    correl: pd.DataFrame, columnas_fuente: list[str]
) -> tuple[pd.DataFrame, dict[str, str], list[str]]:
    renombres: dict[str, str] = {}
    no_recuperables: list[str] = []
    for col in columnas_fuente:
        if col not in contrato.COLUMNAS_CORRELATIVA:
            continue
        if col in COLUMNAS_QUE_ANADE and col in correl.columns:
            nuevo = f"{col}{_SUFIJO_COLISION}"
            if nuevo in correl.columns:
                raise ValueError(
                    mensaje_accionable(
                        f"la fuente trae '{col}' (choca con el contrato) y también '{nuevo}'.",
                        "no hay nombre libre para conservar la columna de la fuente.",
                        f"renombre '{col}' o '{nuevo}' en la fuente antes de correr.",
                    )
                )
            renombres[col] = nuevo
        else:
            no_recuperables.append(col)
    if renombres:
        correl = correl.rename(columns=renombres)
    if no_recuperables:
        sugerido = ", ".join(f"'{c}' → '{c}{_SUFIJO_COLISION}'" for c in no_recuperables)
        warnings.warn(
            mensaje_accionable(
                f"la fuente trae {no_recuperables}, columna(s) que el motor escribe con ese "
                f"mismo nombre y ya sobrescribió.",
                "el valor original de la fuente no está en la correlativa (queda anotado en "
                "manifiesto['completar']['colisiones_no_recuperables']).",
                f"si necesita conservarlo, renombre en la fuente antes de correr: {sugerido}.",
            ),
            UserWarning,
            stacklevel=4,
        )
    return correl, renombres, no_recuperables


def _recodificar_id_grupo(correl: pd.DataFrame) -> dict[str, Any]:
    """``ID_GRUPO`` entero. Si viene de texto, entero por primera aparición."""
    if pd.api.types.is_integer_dtype(correl["ID_GRUPO"]):
        return {"recodificado": False}
    etiquetas = correl["ID_GRUPO"].astype("string").to_numpy(dtype=object)
    orden = np.argsort(correl["ORIGINAL_INDEX"].to_numpy(dtype=np.int64), kind="stable")
    codigos, unicos = pd.factorize(etiquetas[orden], sort=False)
    nuevos = np.empty(len(correl), dtype=np.int64)
    nuevos[orden] = codigos
    correl["ID_GRUPO"] = nuevos
    return {
        "recodificado": True,
        "desde": "etiquetas de texto (p. ej. C<n>/S<n> por régimen en dedupe)",
        "regla": "entero por orden de primera aparición en ORIGINAL_INDEX",
        "n_grupos": len(unicos),
    }


def _texto(serie: pd.Series) -> pd.Series:
    """Texto sin espacios de borde; nulo se queda nulo (pandas 3 no convierte NaN a 'nan')."""
    return serie.astype("string").str.strip()


def _asignar_id_registro(
    correl: pd.DataFrame, col_id: str | None
) -> tuple[pd.Series, dict[str, dict[str, str]]]:
    src = correl["SRC"].astype("string")
    fila = src + "-F" + correl["ORIGINAL_INDEX"].astype("int64").astype("string")
    fuentes = [str(f) for f in src.dropna().unique()]
    if col_id is None:
        return fila, {f: {"regla": "fila", "motivo": "sin col_id"} for f in fuentes}
    if col_id not in correl.columns:
        raise ValueError(
            mensaje_accionable(
                f"col_id='{col_id}' no es una columna de ninguna fuente "
                f"(hay: {[c for c in correl.columns][:30]}).",
                "ID_REGISTRO debía construirse con el id nativo y no hay de dónde.",
                "pase col_id con el nombre real de la columna, o col_id=None para usar "
                "<SRC>-F<fila>.",
            )
        )
    valores = _texto(correl[col_id])
    vacio = valores.isna() | (valores == "")
    repetido = correl.duplicated(subset=["SRC", col_id], keep=False) & ~vacio
    n_vacios = vacio.groupby(src, sort=False).sum()
    n_repetidos = repetido.groupby(src, sort=False).sum()
    usable = (n_vacios == 0) & (n_repetidos == 0)
    mascara = src.map(usable).fillna(False).astype(bool).to_numpy()
    id_registro = (src + "-" + valores.fillna("")).where(mascara, fila)
    info: dict[str, dict[str, str]] = {}
    for f in fuentes:
        if bool(usable.get(f, False)):
            info[f] = {"regla": "col_id", "columna": col_id}
            continue
        motivos: list[str] = []
        if int(n_vacios.get(f, 0)):
            motivos.append(f"{int(n_vacios[f])} vacío(s)/ausente(s)")
        if int(n_repetidos.get(f, 0)):
            motivos.append(f"no es único por fila ({int(n_repetidos[f])} repetido(s))")
        info[f] = {
            "regla": "fila",
            "columna": col_id,
            "motivo": f"col_id='{col_id}' en la fuente '{f}': " + "; ".join(motivos),
        }
    return id_registro, info


def _sha16(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:16]


def bases_del_motor(nit_base: np.ndarray, nit_valid: np.ndarray) -> np.ndarray:
    """Base de cada registro según el MOTOR: ``NIT_BASE`` donde ``NIT_VALID``.

    ``NIT_BASE`` es la base que dejó NitProcessor (L1): sin sufijo ``.0`` de
    un flotante, sin prefijos, sin el DV que el motor reconoció. No se
    reduce de nuevo (``bases_validas`` encadena DV y exige longitud mínima:
    discrepa del motor en el 16 % de las filas válidas del banco).
    ``NIT_VALID`` viaja como bool, 0/1 o texto según la fase que lo escribió
    (``engine.scorer._a_booleano`` hace la coerción).

    Returns:
        Arreglo ``object`` con ``NIT_BASE``, o ``""`` si el motor no validó el
        identificador o no dejó base.
    """
    if len(nit_base) != len(nit_valid):
        raise ValueError(
            f"NIT_BASE y NIT_VALID con longitudes distintas: {len(nit_base)} vs {len(nit_valid)}"
        )
    validos = _a_booleano(np.asarray(nit_valid))
    base = pd.Series(pd.array(np.asarray(nit_base), dtype="string")).fillna("")
    return np.where(validos, base.to_numpy(dtype=object), "")


def grupos_con_bases_distintas(id_grupo: pd.Series, bases: np.ndarray | pd.Series) -> int:
    """Grupos que mezclan dos bases válidas distintas (``""`` no cuenta).

    Es el «conflicto de identificador» del QA del flujo
    (``ResultadoCruce.conflictos_identificador``), calculado una sola vez
    aquí, sobre las bases del motor, y publicado en el manifiesto.
    """
    serie = pd.Series(np.asarray(bases, dtype=object), index=id_grupo.index, dtype="string")
    validas = (serie != "").fillna(False)
    if not validas.any():
        return 0
    return int((serie[validas].groupby(id_grupo[validas]).nunique() > 1).sum())


#: Técnicas del motor de las que sale la regla única de «base válida».
_TECNICAS_IDENTIFICADOR = ("NIT_OK", "NIT_BASE", "NIT_VALID")


def _bases_de_registro(
    correl: pd.DataFrame, columna_nit: str, base_canonica_grupo: pd.Series
) -> tuple[pd.Series, pd.Series, dict[str, Any]]:
    """Base válida por registro y base del grupo para ``METODO_UNION``.

    Con las técnicas del motor: la del registro es ``NIT_BASE`` donde
    ``NIT_VALID`` y la del grupo es el ``NIT_BASE`` de la fila cuyo
    ``NIT_OK == NIT_FINAL`` (la que aportó el identificador adoptado). Sin
    ellas (correlativa que no viene del motor): ``bases_validas`` sobre la
    columna de identificador y sobre ``NIT_FINAL`` (``base_canonica_grupo``,
    la misma de ``ID_ENTIDAD``), y el reporte lo declara.

    Returns:
        ``(base_registro, base_grupo, info)``; las bases son ``string`` con
        ``""``/``<NA>`` donde no hay base válida.
    """
    if set(_TECNICAS_IDENTIFICADOR) <= set(correl.columns):
        base_registro = pd.Series(
            bases_del_motor(correl["NIT_BASE"].to_numpy(), correl["NIT_VALID"].to_numpy()),
            index=correl.index,
            dtype="string",
        )
        nit_ok = correl["NIT_OK"].astype("string").fillna("")
        nit_final = correl["NIT_FINAL"].astype("string").fillna("")
        base_motor = correl["NIT_BASE"].astype("string").fillna("")
        aporta = (nit_ok == nit_final) & (nit_final != "") & (base_motor != "")
        base_grupo = (
            base_motor.where(aporta, pd.NA)
            .groupby(correl["ID_GRUPO"], sort=False)
            .transform("first")
        )
        grupos_con_final = correl.loc[nit_final != "", "ID_GRUPO"].nunique()
        grupos_con_base = correl.loc[base_grupo.notna(), "ID_GRUPO"].nunique()
        info: dict[str, Any] = {
            "origen": "NIT_BASE/NIT_VALID del motor (NitProcessor, L1), sin reducción propia",
            "base_del_grupo": "NIT_BASE de la fila cuyo NIT_OK == NIT_FINAL",
            "grupos_sin_fila_de_nit_final": int(grupos_con_final - grupos_con_base),
        }
        return base_registro, base_grupo, info
    if columna_nit in correl.columns:
        base_registro = pd.Series(
            bases_validas(correl[columna_nit].to_numpy()), index=correl.index, dtype="string"
        )
        info = {
            "origen": f"columna {columna_nit} de la fuente (sin NIT_BASE/NIT_VALID)",
            "base_del_grupo": "bases_validas(NIT_FINAL), la misma de ID_ENTIDAD",
            "motivo": "la correlativa no trae las técnicas del motor; un identificador "
            "flotante o con prefijo puede no reducirse como lo hizo NitProcessor.",
        }
    else:
        base_registro = pd.Series("", index=correl.index, dtype="string")
        info = {
            "origen": None,
            "base_del_grupo": "bases_validas(NIT_FINAL), la misma de ID_ENTIDAD",
            "motivo": f"no hay columna {columna_nit!r} ni técnicas del motor.",
        }
    return base_registro, base_canonica_grupo, info


def _grupos_principales_por_nit(
    correl: pd.DataFrame, base_grupo: pd.Series
) -> tuple[np.ndarray, dict[str, Any]]:
    """Qué grupos reciben ``NIT-<base>``: uno por base, el principal.

    El motor puede dejar dos grupos con el mismo ``NIT_FINAL`` (nombres que
    no alcanzan el umbral, cannot-link, consolidación balanceada). El
    contrato exige un ``ID_GRUPO`` ↔ un ``ID_ENTIDAD``, así que solo el grupo
    principal de cada base —el de más registros; empate: el de menor
    ``ID_REGISTRO``— lleva ``NIT-<base>``; los demás reciben ``ENT-…`` (hash
    de sus ``ID_REGISTRO``, determinista) y quedan contados en el reporte.
    Fusionarlos no es decisión de esta fase (ver F2).
    """
    con_nit = base_grupo.notna()
    por_grupo = (
        pd.DataFrame(
            {
                "grupo": correl.loc[con_nit, "ID_GRUPO"].to_numpy(),
                "base": base_grupo[con_nit].to_numpy(dtype=object),
                "id": correl.loc[con_nit, "ID_REGISTRO"].to_numpy(dtype=object),
            }
        )
        .groupby("grupo", sort=False)
        .agg(base=("base", "first"), n=("id", "size"), id_min=("id", "min"))
        .sort_values(["base", "n", "id_min"], ascending=[True, False, True], kind="stable")
    )
    desplazados = por_grupo.index[por_grupo["base"].duplicated(keep="first")]
    principal = (con_nit & ~correl["ID_GRUPO"].isin(desplazados)).to_numpy()
    n_grupos = int(correl["ID_GRUPO"].nunique())
    info: dict[str, Any] = {
        "regla": "NIT-<base sin DV> si el grupo adopta un identificador válido; si no, "
        "ENT-<16 hex de SHA-256 de los ID_REGISTRO del grupo ordenados>",
        "grupos_nit": int(len(por_grupo) - len(desplazados)),
        "grupos_ent": int(n_grupos - len(por_grupo) + len(desplazados)),
        "nit_compartido": {
            "grupos_desplazados_a_ent": len(desplazados),
            "ejemplos": por_grupo.loc[desplazados, "base"].head(5).tolist(),
        },
    }
    return principal, info


def _id_entidad(correl: pd.DataFrame, base_grupo: pd.Series, grupo_valido: np.ndarray) -> pd.Series:
    """``NIT-<base>`` para grupos con identificador válido; ``ENT-<sha16>`` para el resto."""
    con_nit = pd.Series("NIT-", index=correl.index, dtype="string") + base_grupo.astype("string")
    sin = correl.loc[~grupo_valido, ["ID_GRUPO", "ID_REGISTRO"]]
    if len(sin):
        claves = (
            sin.sort_values(["ID_GRUPO", "ID_REGISTRO"], kind="stable")
            .groupby("ID_GRUPO", sort=False)["ID_REGISTRO"]
            .agg("|".join)
        )
        # Una llamada por grupo sin identificador, no por fila.
        ent = "ENT-" + claves.map(_sha16)
        surrogado = correl["ID_GRUPO"].map(ent).astype("string")
    else:
        surrogado = pd.Series(pd.NA, index=correl.index, dtype="string")
    return con_nit.where(grupo_valido, surrogado)


def _completar_golden(
    golden: pd.DataFrame,
    correl: pd.DataFrame,
    columnas_fuente: list[str],
    prioridad: Sequence[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    grupos_c = pd.Index(correl["ID_GRUPO"].unique())
    huerfanas = ~golden["ID_GRUPO"].isin(grupos_c)
    n_huerfanas = int(huerfanas.sum())
    g = golden.loc[~huerfanas].copy() if n_huerfanas else golden.copy()
    faltan = grupos_c.difference(pd.Index(g["ID_GRUPO"]))
    if len(faltan):
        raise ContratoSalidaError(
            [
                f"golden: {len(faltan)} ID_GRUPO de la correlativa sin fila en el golden "
                f"(p. ej. {faltan[:5].tolist()}); el motor (L5) no consolidó esos grupos."
            ]
        )
    entidad = correl.drop_duplicates("ID_GRUPO").set_index("ID_GRUPO")["ID_ENTIDAD"]
    g["ID_ENTIDAD"] = g["ID_GRUPO"].map(entidad).astype(str)
    reparadas = _reparar_metricas_golden(g, correl, prioridad)
    convertidas: list[str] = []
    for col in _COLUMNAS_ENTERAS_GOLDEN:
        if (
            col in g.columns
            and not pd.api.types.is_integer_dtype(g[col])
            and not g[col].isna().any()
        ):
            g[col] = pd.to_numeric(g[col]).astype("int64")
            convertidas.append(col)
    if "REQUIRES_REVIEW" in g.columns and not pd.api.types.is_bool_dtype(g["REQUIRES_REVIEW"]):
        if not g["REQUIRES_REVIEW"].isna().any():
            g["REQUIRES_REVIEW"] = pd.to_numeric(g["REQUIRES_REVIEW"]) != 0
            convertidas.append("REQUIRES_REVIEW")
    pegadas = [
        c
        for c in g.columns
        if c not in contrato.COLUMNAS_GOLDEN
        and (
            c in columnas_fuente
            or c in contrato.COLUMNAS_TECNICAS
            or c in contrato.COLUMNAS_CORRELATIVA
        )
    ]
    if pegadas:
        g = g.drop(columns=pegadas)
    fijas = [c for c in contrato.COLUMNAS_GOLDEN if c in g.columns]
    extras = [c for c in g.columns if c not in contrato.COLUMNAS_GOLDEN]
    g = g[fijas + extras].reset_index(drop=True)
    info = {
        "huerfanas_retiradas": n_huerfanas,
        "columnas_pegadas_retiradas": pegadas,
        "tipos_convertidos": convertidas,
        "metricas_reparadas": reparadas,
    }
    return g, info


def _reparar_metricas_golden(
    g: pd.DataFrame, correl: pd.DataFrame, prioridad: Sequence[str]
) -> dict[str, Any]:
    """Red, no detector: filas del golden sin métricas se recalculan y se declaran.

    ``consolidate_groups_by_nit_balanced`` deja las filas de los grupos que
    fusiona sin métricas (F1.1 corrige la causa en el motor). Mientras tanto,
    aquí se recalculan sobre el subconjunto de la correlativa con LA MISMA
    regla (``golden.metricas``), en el sitio, y el manifiesto dice cuántas
    y cuáles. Con la causa corregida no hay filas que reparar y esto es un
    no-op.
    """
    presentes = [m for m in contrato.COLUMNAS_METRICAS_GOLDEN if m in g.columns]
    if not presentes:
        return {"n": 0}
    sin_metricas = g[presentes].isna().any(axis=1)
    n = int(sin_metricas.sum())
    if not n:
        return {"n": 0}
    grupos = g.loc[sin_metricas, "ID_GRUPO"]
    sub = correl[correl["ID_GRUPO"].isin(grupos)]
    por_grupo = metricas_de_grupo(sub, prioridad)
    for col in COLUMNAS_METRICAS_GRUPO:
        g.loc[sin_metricas, col] = grupos.map(por_grupo[col]).to_numpy()
    calidad = metricas_de_calidad(g.loc[sin_metricas])
    g.loc[sin_metricas, "CONFIDENCE_SCORE"] = calidad["CONFIDENCE_SCORE"].to_numpy()
    # El motor deja REQUIRES_REVIEW como 0/1 (float con NaN); se escribe en esa
    # moneda y la conversión a booleano ocurre después, para todas las filas.
    revisar = calidad["REQUIRES_REVIEW"].to_numpy()
    if pd.api.types.is_numeric_dtype(g["REQUIRES_REVIEW"]):
        g.loc[sin_metricas, "REQUIRES_REVIEW"] = revisar.astype("float64")
    else:
        g.loc[sin_metricas, "REQUIRES_REVIEW"] = revisar
    if "CREATED_AT" in g.columns:
        ahora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        g.loc[sin_metricas & g["CREATED_AT"].isna(), "CREATED_AT"] = ahora
    return {
        "n": n,
        "grupos": grupos.head(20).tolist(),
        "motivo": "el motor (consolidate_groups_by_nit_balanced) dejó la fila sin métricas; "
        "se recalcularon sobre su subconjunto de la correlativa con golden.metricas (F1.1).",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Entrada principal
# ─────────────────────────────────────────────────────────────────────────────


def completar_correlativa(
    correl: pd.DataFrame,
    golden: pd.DataFrame | None,
    dir_trabajo: Path | None,
    col_id: str | None,
    fuentes: Mapping[str, pd.DataFrame | Sequence[str]],
    *,
    col_nit: str = "NIT",
    canonicos: Mapping[str, str] | None = None,
    score_par_previo: dict[str, Any] | None = None,
    prioridad_fuentes: Sequence[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame | None, ReporteCompletar]:
    """Lleva la correlativa (y el golden, si hay) al contrato 1.0.

    Args:
        correl: correlativa del motor (con ``SRC``, ``ORIGINAL_INDEX``,
            ``ID_GRUPO`` y las cuatro columnas finales).
        golden: golden del motor, o None (``dedupe``).
        dir_trabajo: carpeta L1…L5; de ahí se lee ``L3_scoring/scored.db``.
        col_id: columna de la fuente única por fila para ``ID_REGISTRO``.
        fuentes: ``{nombre: DataFrame}`` o ``{nombre: [columnas]}`` (cuando
            ``consume_sources`` ya vació los DataFrames). Solo se usan los
            nombres de columna: orden de salida y colisiones.
        col_nit: columna de identificador del registro (tras el renombre
            canónico del motor suele ser ``NIT``).
        canonicos: ``{columna_del_usuario: columna_canónica}`` que el motor
            aplicó en la ingesta (``col_name`` → ``RAZON_SOCIAL``…).
        score_par_previo: info de ``anexar_score_par`` si ``SCORE_PAR`` ya se
            añadió antes (colapso de duplicados exactos); no se recalcula.
        prioridad_fuentes: prioridad de fuentes del golden (la de L5), solo
            para reparar ``PRIMARY_SOURCE`` en filas del golden que lleguen
            sin métricas; por defecto, el orden de ``fuentes``.

    Returns:
        ``(correlativa, golden, reporte)``. No muta ``correl`` ni ``golden``:
        trabaja sobre una copia superficial (con Copy-on-Write de pandas 3 no
        duplica datos) y el llamador conserva sus DataFrames tal como los pasó.
    """
    faltantes = [c for c in ("SRC", "ORIGINAL_INDEX", "ID_GRUPO", "NIT_FINAL") if c not in correl]
    if faltantes:
        raise ContratoSalidaError(
            [f"correlativa del motor sin {faltantes}: no se puede completar el contrato."]
        )
    correl = correl.copy(deep=False)
    canonicos = dict(canonicos or {})
    # Renombres canónicos del motor que SÍ ocurrieron: la columna del usuario
    # ya no está y la canónica sí. Van al manifiesto y al diccionario.
    renombres_canonicos = {
        col: can
        for col, can in canonicos.items()
        if col != can and col not in correl.columns and can in correl.columns
    }
    columnas_fuente = _columnas_de_fuentes(fuentes, canonicos, set(correl.columns))
    correl, renombres, no_recuperables = _resolver_colisiones(correl, columnas_fuente)
    columnas_fuente = [renombres.get(c, c) for c in columnas_fuente]

    info_grupo = _recodificar_id_grupo(correl)

    # col_id se da con el nombre de la fuente; en la correlativa puede
    # llamarse distinto (renombre canónico del motor o <col>_FUENTE por
    # colisión). Se resuelve aquí; el reporte lleva el nombre efectivo.
    col_id_efectivo: str | None = None
    if col_id is not None:
        nombre = _nombre_efectivo(col_id, correl.columns, canonicos)
        col_id_efectivo = renombres.get(nombre, nombre)
    id_registro, info_id = _asignar_id_registro(correl, col_id_efectivo)
    repetidos = int(id_registro.duplicated().sum())
    if repetidos:
        raise ContratoSalidaError(
            [f"ID_REGISTRO no es único ({repetidos} repetido(s)) tras aplicar la regla {info_id}."]
        )
    correl["ID_REGISTRO"] = id_registro.astype(str)

    if "SCORE_PAR" in correl.columns and score_par_previo is not None:
        info_score = dict(score_par_previo)
    else:
        correl, info_score = anexar_score_par(correl, dir_trabajo)
    correl["SCORE_PAR"] = correl["SCORE_PAR"].astype("float64")

    # ID_ENTIDAD: base canónica de NIT_FINAL (decisión del preámbulo), una por grupo.
    base_fila = pd.Series(
        bases_validas(correl["NIT_FINAL"].to_numpy()), index=correl.index, dtype="string"
    )
    base_fila = base_fila.mask(base_fila == "", pd.NA)
    base_canonica_grupo = base_fila.groupby(correl["ID_GRUPO"], sort=False).transform("first")
    grupo_principal, info_entidad = _grupos_principales_por_nit(correl, base_canonica_grupo)
    correl["ID_ENTIDAD"] = _id_entidad(correl, base_canonica_grupo, grupo_principal).astype(str)

    # METODO_UNION y QA de identificador: la regla del motor (NIT_BASE donde
    # NIT_VALID; base del grupo = NIT_BASE de quien aportó NIT_FINAL).
    columna_nit = col_nit if col_nit in correl.columns else "NIT"
    base_registro, base_grupo, info_identificador = _bases_de_registro(
        correl, columna_nit, base_canonica_grupo
    )
    info_identificador["grupos_con_bases_distintas"] = grupos_con_bases_distintas(
        correl["ID_GRUPO"], base_registro.fillna("").to_numpy(dtype=object)
    )
    tamano = correl.groupby("ID_GRUPO", sort=False)["ID_GRUPO"].transform("size").to_numpy()
    registro_valido = (base_registro != "").fillna(False).to_numpy()
    misma_base = (base_registro == base_grupo).fillna(False).to_numpy()
    # «identificador» exige pareja: otro miembro del grupo con la misma base
    # válida. El dueño de NIT_FINAL sin pareja de base (p. ej. unido por
    # nombre a una cédula que no reduce a su base) se declara ``nombre``, que
    # es como lo unió el motor; el reporte cuenta esos casos.
    clave_base = base_registro.fillna("").astype("string")
    n_misma_base = (
        clave_base.groupby([correl["ID_GRUPO"], clave_base], sort=False)
        .transform("size")
        .to_numpy()
    )
    con_pareja = registro_valido & (n_misma_base >= 2)
    correl["METODO_UNION"] = np.where(
        tamano == 1,
        "sin_pareja",
        np.where(registro_valido & misma_base & con_pareja, "identificador", "nombre"),
    )
    info_identificador["identificador_sin_pareja_de_base"] = int(
        ((tamano > 1) & registro_valido & misma_base & ~con_pareja).sum()
    )

    prioridad = list(prioridad_fuentes or fuentes)
    info_golden: dict[str, Any] = {}
    if golden is None:
        correl["CONFIANZA"] = pd.Series(pd.NA, index=correl.index, dtype="string")
        info_confianza: dict[str, Any] = {
            "origen": None,
            "motivo": "sin golden en memoria (dedupe() lo escribe por régimen en output_dir): "
            "CONFIANZA queda nula.",
        }
    else:
        golden, info_golden = _completar_golden(golden, correl, columnas_fuente, prioridad)
        confianza = golden.set_index("ID_GRUPO")["CONFIANZA"]
        correl["CONFIANZA"] = correl["ID_GRUPO"].map(confianza)
        info_confianza = {"origen": "golden.CONFIANZA (la del grupo)"}

    tecnicas = [c for c in contrato.COLUMNAS_TECNICAS if c in correl.columns]
    if tecnicas:
        correl = correl.drop(columns=tecnicas)
    fijas = list(contrato.COLUMNAS_CORRELATIVA)
    # Una columna de la fuente que el motor sobrescribió (colisión no
    # recuperable, p. ej. `SRC` con separar_columnas_extra=False) ya está en
    # `fijas`: se lista una sola vez, en su posición fija, o la selección la
    # duplicaría (dos columnas `SRC` y validar() rompe).
    fuente_salida = [c for c in columnas_fuente if c in correl.columns and c not in fijas]
    resto = [c for c in correl.columns if c not in fijas and c not in fuente_salida]
    correl = correl[fijas + fuente_salida + resto]

    reporte = ReporteCompletar(
        renombres=renombres,
        renombres_canonicos=renombres_canonicos,
        colisiones_no_recuperables=no_recuperables,
        id_registro=info_id,
        id_grupo=info_grupo,
        id_entidad=info_entidad,
        identificador=info_identificador,
        score_par=info_score,
        confianza=info_confianza,
        golden=info_golden,
        columnas_tecnicas_retiradas=tecnicas,
        columnas_fuente=fuente_salida,
        prioridad_fuentes=prioridad,
    )
    return correl, golden, reporte
