"""Métricas del golden: la regla se escribe UNA vez (F1.1).

Hasta la 0.22.x las métricas de un grupo vivían inline en
``GoldenRecordGeneratorV7._process_batch_vectorized`` (SOURCES_LIST,
SOURCES_COUNT, RECORD_COUNT, NAME_VARIATIONS, NIT_VARIATIONS, PRIMARY_SOURCE,
CONFIANZA) y en el SQL de ``_add_quality_metrics`` (CONFIDENCE_SCORE,
REQUIRES_REVIEW). La consolidación por NIT (``golden/containment.py``) no las
conocía: reconstruía los grupos fusionados con ``groupby().first()`` sobre la
correlativa, y el golden salía con 27 columnas ajenas y las métricas en NaN
en cada fila fusionada (medido sobre el banco de 30.486: 40 columnas en vez
de 13).

Aquí viven las dos reglas como funciones puras de módulo:

* :func:`metricas_de_grupo` — lo que el generador calcula por lote; la
  consolidación la llama sobre el subconjunto de la correlativa del grupo
  fusionado.
* :func:`confianza_de_grupo` — ALTA · MEDIA · BAJA desde ``NIT_VARIATIONS``,
  ``SOURCES_COUNT`` y ``RECORD_COUNT``; es la regla que :func:`metricas_de_grupo`
  aplica y la que ``contrato.py`` documenta como definición de ``CONFIANZA``
  (F1.9: una sola copia, aquí). Su vocabulario es ``contrato.NIVELES_CONFIANZA``.
  F2.12: la misma función la aplican los cinco caminos — ``linkage()``,
  ``link()`` y ``ejecutar_cruce`` a través del golden; ``dedupe()`` la hereda
  cuando hay golden (sin golden en memoria la correlativa la deja nula y el
  manifiesto lo declara); ``flujo.importadores`` la llama con sus propias
  métricas (``NIT_VARIATIONS = 0``, ``SOURCES_COUNT = 1``, ``RECORD_COUNT`` =
  filas del grupo); los ``enlaces`` de vinculación (F3) la documentan con el
  mismo texto del contrato. Ninguna otra copia: ``GoldenRecordGeneratorV7``
  perdió en F2.12 la suya fila a fila (``_calcular_confianza``).
* :func:`metricas_de_calidad` — la MISMA fórmula que el ``UPDATE`` SQL de
  ``_add_quality_metrics``, incluido el redondeo de SQLite
  (``tests/test_golden_consolidacion_nit.py`` prueba la paridad valor a valor
  sobre una malla de entradas). ``REQUIRES_REVIEW`` sale como 0/1 (``int64``)
  igual que del SQL; el tipo ``bool`` del contrato lo pone ``golden.tipos``
  (F1.14) o ``salida.completar`` (F1.9), no esta función.

y la red final :func:`verificar_golden`, que el orquestador llama tras la
consolidación: ninguna métrica nula, ninguna columna de la correlativa.
``salida.completar`` (F1.9) usa :func:`metricas_de_grupo` y
:func:`metricas_de_calidad` como red para reparar —y declarar en el
manifiesto— cualquier fila del golden que llegue sin métricas; con F1.1 en
el motor esa reparación es un no-op.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from ..contrato import NIVELES_CONFIANZA
from ..pipeline.errores import GoldenInvalidoError, mensaje_accionable

#: Las 13 columnas del golden de v1, en el orden en que las crea
#: ``GoldenRecordGeneratorV7._prepare_output_database_v2``.
COLUMNAS_GOLDEN: tuple[str, ...] = (
    "ID_GRUPO",
    "NIT_FINAL",
    "RAZON_SOCIAL_FINAL",
    "PRIMARY_SOURCE",
    "SOURCES_LIST",
    "SOURCES_COUNT",
    "RECORD_COUNT",
    "NAME_VARIATIONS",
    "NIT_VARIATIONS",
    "CONFIDENCE_SCORE",
    "CONFIANZA",
    "REQUIRES_REVIEW",
    "CREATED_AT",
)

#: Métricas que se calculan por grupo sobre la correlativa (camino del lote).
COLUMNAS_METRICAS_GRUPO: tuple[str, ...] = (
    "SOURCES_LIST",
    "SOURCES_COUNT",
    "RECORD_COUNT",
    "NAME_VARIATIONS",
    "NIT_VARIATIONS",
    "PRIMARY_SOURCE",
    "CONFIANZA",
)

#: Métricas de calidad que se derivan de las anteriores (SQL de
#: ``_add_quality_metrics`` ↔ :func:`metricas_de_calidad`).
COLUMNAS_METRICAS_CALIDAD: tuple[str, ...] = ("CONFIDENCE_SCORE", "REQUIRES_REVIEW")

#: Todas las columnas de métrica: ninguna puede quedar nula en el golden.
COLUMNAS_METRICAS: tuple[str, ...] = COLUMNAS_METRICAS_GRUPO + COLUMNAS_METRICAS_CALIDAD

#: Prioridad que recibe una fuente que no está declarada en la lista.
PRIORIDAD_FUENTE_DESCONOCIDA = 999

#: Decimales del redondeo de CONFIDENCE_SCORE (``ROUND(..., 4)`` en el SQL).
DECIMALES_CONFIDENCE_SCORE = 4


def mapa_prioridad(prioridad: Sequence[str] | Mapping[str, int]) -> dict[str, int]:
    """Normaliza la prioridad de fuentes a ``{fuente: rango}`` (menor = gana).

    Acepta la lista ordenada que recibe ``GoldenRecordGeneratorV7`` (la
    posición es el rango) o un mapeo explícito como el de
    ``golden._priorities.obtener_prioridades_fuentes``.
    """
    if isinstance(prioridad, Mapping):
        return {str(fuente): int(rango) for fuente, rango in prioridad.items()}
    if isinstance(prioridad, str):
        raise TypeError(
            mensaje_accionable(
                f"prioridad_fuentes recibió la cadena {prioridad!r} en vez de una lista de fuentes.",
                "una cadena se iteraría letra a letra y ninguna fuente real tendría prioridad.",
                "pase una lista ordenada de nombres de fuente (p. ej. ['RUES', 'CRM']) "
                "o un mapeo {fuente: rango}.",
            )
        )
    return {str(fuente): rango for rango, fuente in enumerate(prioridad)}


def metricas_de_grupo(
    correl: pd.DataFrame, prioridad: Sequence[str] | Mapping[str, int]
) -> pd.DataFrame:
    """Métricas por ``ID_GRUPO`` sobre un subconjunto de la correlativa.

    Es la regla de ``_process_batch_vectorized`` extraída tal cual (v2.4.0):

    * ``SOURCES_LIST``: fuentes distintas ordenadas y unidas por ``|``.
    * ``SOURCES_COUNT`` / ``RECORD_COUNT``: fuentes distintas / filas.
    * ``NAME_VARIATIONS``: razones sociales distintas en ``RAZON_SOCIAL``
      (la columna ORIGINAL, no la limpia, para reflejar la variación real).
    * ``NIT_VARIATIONS``: identificadores distintos en ``NIT_OK`` si existe,
      si no en ``NIT``.
    * ``PRIMARY_SOURCE``: la fuente de menor rango según ``prioridad``; una
      fuente no declarada recibe :data:`PRIORIDAD_FUENTE_DESCONOCIDA`; el
      empate lo gana la primera fila del grupo en el orden de ``correl``.
    * ``CONFIANZA``: ALTA si NIT único y ≥2 fuentes; MEDIA si ≤2 NIT y ≤5
      filas; BAJA en el resto.

    Returns:
        DataFrame indexado por ``ID_GRUPO`` (ordenado) con
        :data:`COLUMNAS_METRICAS_GRUPO`.

    Raises:
        KeyError: si falta ``ID_GRUPO``, ``SRC``, ``RAZON_SOCIAL`` o el
            identificador (``NIT_OK`` o ``NIT``).
    """
    faltantes = [col for col in ("ID_GRUPO", "SRC", "RAZON_SOCIAL") if col not in correl.columns]
    if "NIT_OK" not in correl.columns and "NIT" not in correl.columns:
        faltantes.append("NIT (o NIT_OK)")
    if faltantes:
        raise KeyError(
            mensaje_accionable(
                f"la correlativa no trae las columnas {faltantes} que las métricas del golden necesitan.",
                "sin ellas no se puede contar fuentes, nombres ni identificadores del grupo "
                "y el golden saldría con métricas nulas.",
                "pase la correlativa completa que produce GoldenRecordGeneratorV7 "
                "(conserva SRC, RAZON_SOCIAL y NIT/NIT_OK de cada registro).",
            )
        )
    columna_nit = "NIT_OK" if "NIT_OK" in correl.columns else "NIT"
    rangos = mapa_prioridad(prioridad)

    # PRIMARY_SOURCE: la fila de menor rango por grupo (orden estable → el
    # empate lo gana la primera fila del grupo). Equivalente exacto al
    # ``min(key=...)`` original.
    bm = correl[["ID_GRUPO", "SRC"]].copy()
    bm["__prio"] = bm["SRC"].map(rangos).fillna(PRIORIDAD_FUENTE_DESCONOCIDA)
    bm = bm.sort_values(["ID_GRUPO", "__prio"], kind="stable")
    primary_source = bm.drop_duplicates("ID_GRUPO", keep="first").set_index("ID_GRUPO")["SRC"]
    del bm

    metricas = correl.groupby("ID_GRUPO").agg(
        SOURCES_LIST=("SRC", lambda s: "|".join(sorted(s.unique()))),
        SOURCES_COUNT=("SRC", "nunique"),
        RECORD_COUNT=("SRC", "size"),
        NAME_VARIATIONS=("RAZON_SOCIAL", "nunique"),
        NIT_VARIATIONS=(columna_nit, "nunique"),
    )
    metricas["PRIMARY_SOURCE"] = primary_source

    metricas["CONFIANZA"] = confianza_de_grupo(metricas)
    return metricas[list(COLUMNAS_METRICAS_GRUPO)]


#: Umbrales de la regla de CONFIANZA. Viven aquí y en ningún otro sitio; el
#: texto del contrato (``contrato._DEFINICION_CONFIANZA``) los repite para el
#: diccionario y ``tests/test_contrato_salida.py`` comprueba que coinciden.
MAX_IDENTIFICADORES_MEDIA = 2
MAX_REGISTROS_MEDIA = 5


def confianza_de_grupo(metricas: pd.DataFrame) -> np.ndarray:
    """ALTA · MEDIA · BAJA desde ``NIT_VARIATIONS``, ``SOURCES_COUNT`` y ``RECORD_COUNT``.

    Es LA regla de ``CONFIANZA`` del estándar de salida (paso 1.5 del plan
    maestro; F2.12 la deja como única copia para los cinco caminos),
    vectorizada con ``np.select``:

    * ALTA: identificador único (``NIT_VARIATIONS == 1``) confirmado por dos o
      más fuentes (``SOURCES_COUNT >= 2``).
    * MEDIA: hasta :data:`MAX_IDENTIFICADORES_MEDIA` identificadores y grupo
      pequeño (``RECORD_COUNT <= MAX_REGISTROS_MEDIA``). Un registro solo sin
      identificador (``NIT_VARIATIONS = 0``) cae aquí.
    * BAJA: el resto.

    Los nombres de los niveles salen de ``contrato.NIVELES_CONFIANZA``: la
    función no tiene vocabulario propio. Un camino sin identificador ni
    varias fuentes (``flujo.importadores``) la llama con ``NIT_VARIATIONS = 0``
    y ``SOURCES_COUNT = 1``: la regla solo distingue entonces por tamaño del
    grupo (MEDIA hasta 5 filas, BAJA después), y eso es lo que se declara.

    No rellena nulos: las métricas de grupo nunca los traen (las produce
    :func:`metricas_de_grupo`) y un nulo aquí sería un defecto a detectar, no
    a tapar.

    Args:
        metricas: DataFrame con las tres columnas (una fila por grupo).

    Returns:
        Arreglo de cadenas alineado con ``metricas.index``.
    """
    alta, media, baja = NIVELES_CONFIANZA
    nit_vars = metricas["NIT_VARIATIONS"]
    fuentes = metricas["SOURCES_COUNT"]
    miembros = metricas["RECORD_COUNT"]
    return np.select(
        [
            (nit_vars == 1) & (fuentes >= 2),
            (nit_vars <= MAX_IDENTIFICADORES_MEDIA) & (miembros <= MAX_REGISTROS_MEDIA),
        ],
        [alta, media],
        default=baja,
    )


def _redondear_como_sqlite(valores: np.ndarray) -> np.ndarray:
    """``ROUND(x, 4)`` de SQLite, reproducido bit a bit en numpy.

    SQLite redondea el valor binario EXACTO del double y resuelve los
    empates alejándose de cero (``ROUND(0.53125, 4) = 0.5313``). ``np.round``
    hace ``x·10⁴ → rint → /10⁴``, que es empate-al-par y además se equivoca
    cuando la multiplicación pierde precisión (``np.round(0.12345, 4) =
    0.1234`` aunque el double es 0.12345000000000000417 > el empate). Por eso:

    1. ``'%.4f' % x`` da el redondeo correcto del valor exacto (empate al par);
    2. un empate exacto a 4 decimales exige que ``x·10⁴`` tenga parte
       fraccionaria ½ exacta, y como el double es un racional diádico eso
       ocurre si y solo si ``x = impar / 32``; esos valores se corrigen
       alejándolos de cero. Medido en ``sqlite 3.45.1``.
    """
    x = np.asarray(valores, dtype=np.float64)
    if x.size == 0:
        return x.copy()
    escala = 10.0**DECIMALES_CONFIDENCE_SCORE
    redondeado = np.char.mod(f"%.{DECIMALES_CONFIDENCE_SCORE}f", x).astype(np.float64)
    treintaidosavos = x * 32.0
    es_empate = (treintaidosavos == np.floor(treintaidosavos)) & (
        np.abs(np.floor(treintaidosavos)) % 2 == 1
    )
    if es_empate.any():
        lejos_de_cero = np.sign(x) * np.floor(np.abs(x) * escala + 0.5) / escala
        redondeado = np.where(es_empate, lejos_de_cero, redondeado)
    return redondeado


def metricas_de_calidad(golden: pd.DataFrame) -> pd.DataFrame:
    """``CONFIDENCE_SCORE`` y ``REQUIRES_REVIEW`` de cada fila del golden.

    Es la MISMA fórmula que el ``UPDATE golden_records`` de
    ``GoldenRecordGeneratorV7._add_quality_metrics`` (que sigue existiendo
    porque el camino normal la ejecuta dentro de SQLite sin traer el golden a
    memoria); si una cambia, cambia la otra, y la prueba de paridad
    ``test_paridad_sql_pandas_metricas_de_calidad`` lo detecta::

        CONFIDENCE_SCORE = ROUND(
            CASE WHEN NIT_VARIATIONS = 1 AND NAME_VARIATIONS = 1 THEN 1.0
                 WHEN SOURCES_COUNT = 1 THEN 0.85
                 ELSE 0.5 / MAX(1.0, NIT_VARIATIONS)
                    + 0.4 / MAX(1.0, NAME_VARIATIONS)
                    + 0.1 * MIN(1.0, SOURCES_COUNT / 3.0)
            END, 4)
        REQUIRES_REVIEW = CASE WHEN NIT_VARIATIONS > 3 OR NAME_VARIATIONS > 5 THEN 1
                               WHEN RECORD_COUNT > 20 THEN 1
                               WHEN LENGTH(NIT_FINAL) < 6 THEN 1
                               ELSE 0 END

    ``LENGTH(NULL)`` es NULL en SQLite y la rama no aplica: un ``NIT_FINAL``
    nulo NO marca revisión (sí lo hace la cadena vacía, de longitud 0).

    Returns:
        DataFrame con el índice de ``golden`` y las columnas
        :data:`COLUMNAS_METRICAS_CALIDAD` (``float64`` e ``int64``).
    """
    necesarias = ("NIT_FINAL", "SOURCES_COUNT", "RECORD_COUNT", "NAME_VARIATIONS", "NIT_VARIATIONS")
    faltantes = [col for col in necesarias if col not in golden.columns]
    if faltantes:
        raise KeyError(
            mensaje_accionable(
                f"el golden no trae las columnas {faltantes}.",
                "CONFIDENCE_SCORE y REQUIRES_REVIEW se derivan de ellas; sin ellas quedarían nulas.",
                "calcule primero metricas_de_grupo() y adjunte NIT_FINAL antes de pedir la calidad.",
            )
        )
    nit_var = golden["NIT_VARIATIONS"].to_numpy(dtype=np.float64)
    name_var = golden["NAME_VARIATIONS"].to_numpy(dtype=np.float64)
    fuentes = golden["SOURCES_COUNT"].to_numpy(dtype=np.float64)
    filas = golden["RECORD_COUNT"].to_numpy(dtype=np.float64)

    # Mismo orden de evaluación que el SQL: (0.5/a + 0.4/b) + 0.1*min(1, s/3).
    formula = (
        0.5 / np.maximum(1.0, nit_var)
        + 0.4 / np.maximum(1.0, name_var)
        + 0.1 * np.minimum(1.0, fuentes / 3.0)
    )
    confianza = np.select(
        [(nit_var == 1) & (name_var == 1), fuentes == 1],
        [1.0, 0.85],
        default=formula,
    )
    confianza = _redondear_como_sqlite(confianza)

    largo_nit = golden["NIT_FINAL"].astype("string").str.len()
    nit_corto = (largo_nit < 6).fillna(False).to_numpy(dtype=bool)
    revision = np.select(
        [(nit_var > 3) | (name_var > 5), filas > 20, nit_corto],
        [1, 1, 1],
        default=0,
    ).astype(np.int64)

    return pd.DataFrame(
        {"CONFIDENCE_SCORE": confianza, "REQUIRES_REVIEW": revision}, index=golden.index
    )


def verificar_golden(golden: pd.DataFrame, columnas_correlativa: Iterable[str] = ()) -> None:
    """Red final del golden antes de persistirlo (F1.1).

    Exige las 13 columnas de :data:`COLUMNAS_GOLDEN`, ``ID_GRUPO`` único y no
    nulo, ninguna métrica nula y ninguna columna de la correlativa que no sea
    del golden (``columnas_correlativa`` son las columnas de la correlativa de
    la misma corrida). Es una red, no un detector: la consolidación por NIT ya
    construye cada fila fusionada completa; si esto salta, hay una regresión
    en ``golden/containment.py`` o en el generador.

    Raises:
        GoldenInvalidoError: con el detalle (columnas, conteos y ejemplos).
    """
    faltantes = [col for col in COLUMNAS_GOLDEN if col not in golden.columns]
    if faltantes:
        raise GoldenInvalidoError(
            mensaje_accionable(
                f"al golden le faltan las columnas {faltantes}.",
                "el entregable golden.parquet tiene 13 columnas fijas; un consumidor que las "
                "lea por nombre fallará.",
                "revise GoldenRecordGeneratorV7._prepare_output_database_v2 y la consolidación "
                "por NIT: ninguna de las dos debe perder columnas.",
            )
        )
    permitidas = set(COLUMNAS_GOLDEN)
    intrusas = [
        col for col in golden.columns if col in set(columnas_correlativa) and col not in permitidas
    ]
    if intrusas:
        raise GoldenInvalidoError(
            mensaje_accionable(
                f"el golden trae {len(intrusas)} columna(s) de la correlativa: {intrusas[:10]}.",
                "un golden con columnas de registro individual mezcla dos niveles (grupo y "
                "fila) y duplica el entregable; es el defecto medido en F1.1.",
                "consolide con consolidate_groups_by_nit_balanced de la 0.23+ "
                "(_concat_filtrado_por_columnas solo conserva columnas del golden).",
            )
        )
    grupos = golden["ID_GRUPO"]
    if grupos.isna().any() or grupos.duplicated().any():
        ejemplos = grupos[grupos.duplicated(keep=False)].head(5).tolist()
        raise GoldenInvalidoError(
            mensaje_accionable(
                f"ID_GRUPO nulo o repetido en el golden (nulos={int(grupos.isna().sum())}, "
                f"ejemplos repetidos={ejemplos}).",
                "el golden tiene exactamente una fila por grupo; la correlativa no podría "
                "re-adjuntar sus campos finales.",
                "revise la consolidación por NIT: el mapa de fusiones debe apuntar a raíces únicas.",
            )
        )
    nulos = {
        col: int(golden[col].isna().sum()) for col in COLUMNAS_METRICAS if golden[col].isna().any()
    }
    if nulos:
        primera = next(iter(nulos))
        ejemplos = golden.loc[golden[primera].isna(), "ID_GRUPO"].head(5).tolist()
        raise GoldenInvalidoError(
            mensaje_accionable(
                f"métricas nulas en el golden: {nulos} (ejemplos de ID_GRUPO: {ejemplos}).",
                "una métrica nula convierte la fila en inservible para la revisión "
                "(CONFIANZA, REQUIRES_REVIEW) y la salida deja de ser contrato.",
                "recalcule las métricas del grupo con golden.metricas.metricas_de_grupo y "
                "metricas_de_calidad sobre su subconjunto de la correlativa.",
            )
        )
