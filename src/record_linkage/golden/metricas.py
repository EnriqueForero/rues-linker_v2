"""record_linkage.golden.metricas — Las métricas del golden, escritas una vez (F1).

Qué hay aquí
------------
Las reglas con las que el golden describe a su grupo, como funciones puras de
módulo, para que todo camino que produzca o repare un golden use LA MISMA:

* ``metricas_de_grupo(correl, prioridad)``: por ``ID_GRUPO``,
  ``SOURCES_LIST``, ``SOURCES_COUNT``, ``RECORD_COUNT``, ``NAME_VARIATIONS``,
  ``NIT_VARIATIONS``, ``PRIMARY_SOURCE`` y ``CONFIANZA``. Es la regla de
  ``GoldenRecordGeneratorV7._process_batch_vectorized`` (generator.py).
* ``metricas_de_calidad(golden)``: ``CONFIDENCE_SCORE`` y ``REQUIRES_REVIEW``.
  Es la fórmula del SQL de ``GoldenRecordGeneratorV7._add_quality_metrics``.

Por qué existe
--------------
``consolidate_groups_by_nit_balanced`` (golden/containment.py) reconstruye los
grupos que fusiona con ``groupby("ID_GRUPO").first()`` sobre la correlativa y
deja esas filas del golden SIN métricas (NaN) y con las columnas de la
correlativa pegadas: medido en el banco de 30.486, dos filas de 13.069. F1.1
corrige la causa en el motor usando estas funciones; ``salida.completar`` las
usa como red para reparar —y declarar en el manifiesto— cualquier fila que
llegue sin métricas. Con F1.1 integrada la reparación es un no-op.

Paridad con el motor (F1.9, revisión): ``tests/test_contrato_salida.py::
test_paridad_metricas_con_el_motor_*`` compara el golden que deja L5 (antes de
completar) con estas funciones, columna a columna, sobre el sintético y sobre
``dataset_sintetico_p2_extra_features.csv``. Hoy la regla sigue escrita dos
veces (aquí y en ``generator.py``): F1.1 debe hacer que
``_process_batch_vectorized`` y ``_add_quality_metrics`` importen de AQUÍ,
para que quede una sola copia. No se toca en F1.9 (regla 4: nada en L1…L5).

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-10-06  ·  Version: 0.23.0
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

__all__ = ["confianza_de_grupo", "metricas_de_calidad", "metricas_de_grupo"]

#: Columnas que ``metricas_de_grupo`` produce, en este orden.
COLUMNAS_METRICAS_GRUPO: tuple[str, ...] = (
    "SOURCES_LIST",
    "SOURCES_COUNT",
    "RECORD_COUNT",
    "NAME_VARIATIONS",
    "NIT_VARIATIONS",
    "PRIMARY_SOURCE",
    "CONFIANZA",
)


def confianza_de_grupo(metricas: pd.DataFrame) -> np.ndarray:
    """ALTA · MEDIA · BAJA desde ``NIT_VARIATIONS``, ``SOURCES_COUNT`` y ``RECORD_COUNT``.

    ALTA: identificador único (``NIT_VARIATIONS == 1``) confirmado por dos o
    más fuentes. MEDIA: hasta dos identificadores y grupo pequeño (≤ 5).
    BAJA: el resto. (Paso 1.5 del plan maestro; ``_calcular_confianza``.)
    """
    nit_vars = metricas["NIT_VARIATIONS"].fillna(1)
    fuentes = metricas["SOURCES_COUNT"].fillna(1)
    miembros = metricas["RECORD_COUNT"].fillna(1)
    return np.select(
        [(nit_vars == 1) & (fuentes >= 2), (nit_vars <= 2) & (miembros <= 5)],
        ["ALTA", "MEDIA"],
        default="BAJA",
    )


def metricas_de_grupo(correl: pd.DataFrame, prioridad: Sequence[str]) -> pd.DataFrame:
    """Métricas por ``ID_GRUPO`` sobre un subconjunto de la correlativa.

    Args:
        correl: filas de la correlativa (con ``ID_GRUPO``, ``SRC``,
            ``RAZON_SOCIAL`` y ``NIT_OK`` o ``NIT``).
        prioridad: fuentes de mayor a menor prioridad; ``PRIMARY_SOURCE`` es
            la fuente presente en el grupo con menor posición aquí (las que
            no aparecen van al final, en orden estable).

    Returns:
        DataFrame indexado por ``ID_GRUPO`` con ``COLUMNAS_METRICAS_GRUPO``.
    """
    nombre = "RAZON_SOCIAL"
    nit = "NIT_OK" if "NIT_OK" in correl.columns else "NIT"
    mapa_prioridad = {fuente: i for i, fuente in enumerate(prioridad)}
    bm = correl[["ID_GRUPO", "SRC"]].copy()
    bm["__prio"] = bm["SRC"].map(mapa_prioridad).fillna(999)
    bm = bm.sort_values(["ID_GRUPO", "__prio"], kind="stable")
    primaria = bm.drop_duplicates("ID_GRUPO", keep="first").set_index("ID_GRUPO")["SRC"]
    metricas = correl.groupby("ID_GRUPO").agg(
        SOURCES_LIST=("SRC", lambda s: "|".join(sorted(s.unique()))),
        SOURCES_COUNT=("SRC", "nunique"),
        RECORD_COUNT=("SRC", "size"),
        NAME_VARIATIONS=(nombre, "nunique"),
        NIT_VARIATIONS=(nit, "nunique"),
    )
    metricas["PRIMARY_SOURCE"] = primaria
    metricas["CONFIANZA"] = confianza_de_grupo(metricas)
    return metricas[list(COLUMNAS_METRICAS_GRUPO)]


def metricas_de_calidad(golden: pd.DataFrame) -> pd.DataFrame:
    """``CONFIDENCE_SCORE`` (0–1, 4 decimales) y ``REQUIRES_REVIEW`` (bool) por fila.

    Misma fórmula que el ``UPDATE golden_records`` de ``_add_quality_metrics``:
    1,0 si un solo identificador y un solo nombre; 0,85 si una sola fuente;
    si no, 0,5/NIT_VARIATIONS + 0,4/NAME_VARIATIONS + 0,1·min(1, fuentes/3).
    Revisión si más de 3 identificadores, más de 5 nombres, más de 20
    registros o ``NIT_FINAL`` de menos de 6 caracteres.
    """
    nit_vars = golden["NIT_VARIATIONS"].astype("float64")
    name_vars = golden["NAME_VARIATIONS"].astype("float64")
    fuentes = golden["SOURCES_COUNT"].astype("float64")
    registros = golden["RECORD_COUNT"].astype("float64")
    puntaje = np.select(
        [(nit_vars == 1) & (name_vars == 1), fuentes == 1],
        [1.0, 0.85],
        default=(
            0.5 / np.maximum(1.0, nit_vars)
            + 0.4 / np.maximum(1.0, name_vars)
            + 0.1 * np.minimum(1.0, fuentes / 3.0)
        ),
    )
    largo_nit = golden["NIT_FINAL"].astype("string").fillna("").str.len()
    revisar = (nit_vars > 3) | (name_vars > 5) | (registros > 20) | (largo_nit < 6)
    return pd.DataFrame(
        {
            "CONFIDENCE_SCORE": np.round(puntaje, 4),
            "REQUIRES_REVIEW": revisar.to_numpy(dtype=bool),
        },
        index=golden.index,
    )
