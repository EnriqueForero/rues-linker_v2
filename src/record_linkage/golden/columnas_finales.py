"""record_linkage.golden.columnas_finales — La entrega mínima del cruce (v0.21.0).

Qué garantiza
-------------
Que la correlativa SIEMPRE salga con las cuatro columnas que son el resultado
de negocio del proceso:

    NIT_FINAL              el identificador que el grupo adopta
    RAZON_SOCIAL_FINAL     la razón social que el grupo adopta
    NAME_SIMILARITY_SCORE  cuánto se parece el nombre de la fila al adoptado,
                           normalizado contra normalizado (C38, F1.14)
    NIT_DISTANCE           a qué distancia está su identificador del adoptado

Las dos primeras son *el* entregable: sin ellas, la correlativa dice a qué
grupo pertenece cada fila pero no qué quedó como identidad de ese grupo, que
es justamente lo que alguien va a consumir aguas abajo. Las dos últimas son lo
que permite auditar una fusión sin volver a correr nada: una fila con
``NAME_SIMILARITY_SCORE`` de 0,3 dentro de un grupo es exactamente lo que un
revisor humano quiere ver primero.

Por qué existe este módulo
--------------------------
Hasta 0.20.0 las cuatro columnas se producían, pero **no estaban garantizadas**:
dependían de dos bloques ``try/except`` que ante un fallo escribían una
advertencia y seguían adelante, y de una comprobación previa que devolvía el
DataFrame intacto si no encontraba lo que buscaba.

    generator.py:1269   except Exception: logger.warning("No se pudieron
                        añadir métricas de diagnóstico")
    generator.py:1341   if missing_cols: ... return df
    orchestrator.py     except Exception: log.warning("Consolidación falló,
                        usando resultados directos")

Tres caminos por los que la entrega se degrada sin que nadie se entere, en una
corrida de cuarenta minutos cuyo registro nadie lee entero. Y ninguna
invariante del flujo las exigía: ``flujo/cruce.py`` no mencionaba
``NIT_FINAL`` ni una vez.

La política aquí es distinta y deliberada: **reparar y decirlo**, no fallar ni
callar. Fallar castiga al usuario por un defecto interno después de cuarenta
minutos de cómputo; callar es lo que produjo el problema. Reparar es posible
siempre, porque el golden — que se construye antes — lleva ``ID_GRUPO``,
``NIT_FINAL`` y ``RAZON_SOCIAL_FINAL`` por construcción. Solo cuando la
reparación es imposible se levanta un error, y entonces sí es un fallo real.

Cómo se compara el nombre (C38, F1.14 — ADR-0010)
-------------------------------------------------
Hasta F1.14, ``NAME_SIMILARITY_SCORE`` comparaba ``NOMBRE_LIMPIO`` —el nombre
normalizado para el motor— contra ``RAZON_SOCIAL_FINAL`` —el adoptado, crudo—.
Dos filas con el mismo nombre real daban 0,69 solo por eso: en el banco de
30.486 filas apenas el 15,6 % puntuaba 1,0 y el 74 % quedaba por debajo de
0,9. Ahora los DOS lados pasan por la misma normalización, la huella del
selector de golden (``golden.selector.huella_de_nombre``: mayúsculas sin
tildes, sin forma societaria ni puntuación), que es la lente bajo la cual el
selector adoptó ese nombre. ``NOMBRE_LIMPIO`` no sirve de base porque es una
normalización para bloquear y puntuar, con poda de palabras frecuentes y
residuos como «S S» para «S.A.S.», que harían que «ACME S.A.S.» y «ACME SAS»
no midieran 1,0. La columna cambia; la partición no (la huella del banco es
la misma).

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-30  ·  Version: 0.21.0
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .selector import huella_de_nombre

logger = logging.getLogger(__name__)

__all__ = [
    "COLUMNAS_DIAGNOSTICO",
    "COLUMNAS_FINALES",
    "COLUMNAS_IDENTIDAD",
    "ReporteColumnasFinales",
    "faltantes",
    "faltantes_en",
    "garantizar_columnas_finales",
]

#: Identidad que el grupo adopta. Es el resultado de negocio del cruce.
COLUMNAS_IDENTIDAD: tuple[str, ...] = ("NIT_FINAL", "RAZON_SOCIAL_FINAL")

#: Trazabilidad de cada fila frente a la identidad adoptada. Sin esto, una
#: revisión humana tiene que reconstruir a mano por qué se unió cada fila.
COLUMNAS_DIAGNOSTICO: tuple[str, ...] = ("NAME_SIMILARITY_SCORE", "NIT_DISTANCE")

#: El contrato completo de salida de la correlativa.
COLUMNAS_FINALES: tuple[str, ...] = COLUMNAS_IDENTIDAD + COLUMNAS_DIAGNOSTICO


@dataclass(frozen=True)
class ReporteColumnasFinales:
    """Cómo se obtuvo cada columna del contrato. Viaja a los metadatos.

    Distingue dos cosas que no son lo mismo y conviene no confundir:

    * **calculadas** — las de diagnóstico se derivan aquí en el curso normal
      del proceso. Es trabajo esperado, no una anomalía.
    * **reparadas** — la identidad tuvo que reconstruirse desde el golden
      porque no llegó. Eso sí es una anomalía y merece una advertencia: algo
      falló antes y no se dijo.

    Attributes:
        ya_estaban: columnas que venían correctas.
        calculadas: columnas de diagnóstico derivadas aquí (curso normal).
        reparadas: columnas de identidad reconstruidas (anomalía).
        origen: de dónde salió cada columna que no venía.
    """

    ya_estaban: tuple[str, ...] = ()
    calculadas: tuple[str, ...] = ()
    reparadas: tuple[str, ...] = ()
    origen: dict[str, str] = field(default_factory=dict)

    @property
    def hubo_anomalia(self) -> bool:
        """True solo si hubo que reconstruir la IDENTIDAD del grupo."""
        return bool(self.reparadas)

    @property
    def completo(self) -> bool:
        return len(self.ya_estaban) + len(self.calculadas) + len(self.reparadas) == len(
            COLUMNAS_FINALES
        )

    def a_dict(self) -> dict[str, object]:
        return {
            "ya_estaban": list(self.ya_estaban),
            "calculadas": list(self.calculadas),
            "reparadas": list(self.reparadas),
            "origen": dict(self.origen),
            "hubo_anomalia": self.hubo_anomalia,
            "completo": self.completo,
        }

    def resumen(self) -> str:
        partes = [f"{len(self.ya_estaban) + len(self.calculadas) + len(self.reparadas)}/4"]
        if self.calculadas:
            partes.append(f"calculadas: {', '.join(self.calculadas)}")
        if self.reparadas:
            detalle = ", ".join(f"{c} ← {self.origen.get(c, '?')}" for c in self.reparadas)
            partes.append(f"RECONSTRUIDAS desde el golden: {detalle}")
        return "columnas finales " + " · ".join(partes)


def faltantes_en(columnas: Iterable[str]) -> tuple[str, ...]:
    """Cuáles del contrato no están, dada una lista de nombres de columna.

    Existe aparte de `faltantes` porque el camino en disco solo tiene el
    esquema del Parquet, nunca el DataFrame: preguntar por las columnas no
    debería obligar a materializar millones de filas.
    """
    presentes = set(columnas)
    return tuple(c for c in COLUMNAS_FINALES if c not in presentes)


def faltantes(marco: pd.DataFrame) -> tuple[str, ...]:
    """Cuáles de las cuatro columnas del contrato no están."""
    return faltantes_en(marco.columns)


def _huella_o_crudo(nombres: pd.Series) -> pd.Series:
    """Huella normalizada; si queda vacía y el nombre no lo estaba, el crudo en mayúsculas."""
    crudo = nombres.astype("string").fillna("").str.strip().str.upper()
    huella = huella_de_nombre(nombres).astype("string").fillna("")
    return huella.where(huella != "", crudo)


def _similitud_de_nombre(nombres_fila: pd.Series, nombres_grupo: pd.Series) -> np.ndarray:
    """Similitud de Levenshtein entre las huellas normalizadas de los dos nombres.

    Los dos lados pasan por :func:`huella_de_nombre` (la normalización del
    selector de golden), así que forma societaria, puntuación, mayúsculas y
    tildes no cuentan como diferencia (C38). Vacíos e idénticos se resuelven
    sin llamar a nadie; solo las diferencias reales llegan a rapidfuzz, en
    C++. Sobre millones de filas, la mayoría son idénticas —es lo que
    significa que el cruce funcionó— y calcularlas una a una sería trabajo
    tirado. Un lado vacío (sin nombre) da 0.

    Si un nombre es SOLO forma societaria («LTDA») su huella queda vacía; en
    ese caso se compara el texto crudo en mayúsculas, para que una fila cuyo
    nombre es idéntico al adoptado nunca puntúe 0 por la normalización.
    """
    izquierda = _huella_o_crudo(nombres_fila)
    derecha = _huella_o_crudo(nombres_grupo)
    vacios = (izquierda.str.strip() == "") | (derecha.str.strip() == "")
    iguales = (izquierda == derecha) & ~vacios

    salida = np.zeros(len(izquierda), dtype=np.float64)
    salida[iguales.to_numpy(dtype=bool, na_value=False)] = 1.0
    restantes = (~vacios & ~iguales).to_numpy(dtype=bool, na_value=False)
    if not restantes.any():
        return salida

    from rapidfuzz import process as rf_process
    from rapidfuzz.distance import Levenshtein as rf_levenshtein

    posiciones = np.flatnonzero(restantes)
    salida[posiciones] = rf_process.cpdist(
        izquierda.iloc[posiciones].tolist(),
        derecha.iloc[posiciones].tolist(),
        scorer=rf_levenshtein.normalized_similarity,
        dtype=np.float64,
        workers=-1,
    )
    return salida


def _distancia_de_identificador(
    identificadores_fila: pd.Series, identificadores_grupo: pd.Series
) -> np.ndarray:
    """Distancia de edición entre el identificador de la fila y el del grupo.

    0 cuando coinciden o cuando falta alguno de los dos: la ausencia no es
    distancia, y contarla como tal ensuciaría cualquier filtro posterior.
    """
    izquierda = identificadores_fila.astype("string").fillna("")
    derecha = identificadores_grupo.astype("string").fillna("")
    ambos = ((izquierda != "") & (derecha != "")).to_numpy(dtype=bool, na_value=False)
    distintos = ambos & (izquierda != derecha).to_numpy(dtype=bool, na_value=False)

    salida = np.zeros(len(izquierda), dtype=np.int64)
    if not distintos.any():
        return salida

    from rapidfuzz import process as rf_process
    from rapidfuzz.distance import Levenshtein as rf_levenshtein

    posiciones = np.flatnonzero(distintos)
    salida[posiciones] = rf_process.cpdist(
        izquierda.iloc[posiciones].tolist(),
        derecha.iloc[posiciones].tolist(),
        scorer=rf_levenshtein.distance,
        dtype=np.int64,
        workers=-1,
    )
    return salida


def garantizar_columnas_finales(
    correlativa: pd.DataFrame,
    golden: pd.DataFrame | None = None,
    *,
    columna_nombre: str | None = None,
    registrador: logging.Logger | None = None,
) -> tuple[pd.DataFrame, ReporteColumnasFinales]:
    """Devuelve la correlativa con las cuatro columnas del contrato, sí o sí.

    Args:
        correlativa: una fila por registro de entrada, con ``ID_GRUPO``.
        golden: una fila por entidad, con ``ID_GRUPO``, ``NIT_FINAL`` y
            ``RAZON_SOCIAL_FINAL``. Es de donde sale la reparación de la
            identidad; sin él solo se pueden reparar las de diagnóstico.
        columna_nombre: columna de la correlativa con el nombre de la fila.
            None → ``RAZON_SOCIAL`` si existe (es de donde sale el adoptado y
            los dos lados se normalizan igual), si no ``NOMBRE_LIMPIO``.
        registrador: logger opcional.

    Returns:
        Tupla ``(correlativa, reporte)``. La correlativa se devuelve tal cual
        si no hizo falta reparar nada; si hubo reparación, es una copia.

    Raises:
        KeyError: si falta ``ID_GRUPO``, sin el cual no hay nada que reparar.
        RuntimeError: si la identidad falta y el golden no permite reconstruirla.
            Ahí sí es un fallo real y callarlo sería peor.
    """
    log = registrador or logger
    if "ID_GRUPO" not in correlativa.columns:
        raise KeyError(
            "Qué pasó: la correlativa no trae ID_GRUPO. Por qué importa: sin la "
            "etiqueta de grupo no se puede adjudicar identidad a ninguna fila. "
            "Qué hacer: no use este resultado; la fase L5 no terminó bien."
        )

    ausentes = faltantes(correlativa)
    presentes = tuple(c for c in COLUMNAS_FINALES if c not in ausentes)
    if not ausentes:
        return correlativa, ReporteColumnasFinales(ya_estaban=presentes)

    # Que falte la IDENTIDAD es una anomalía: el generador debió haberla
    # adjuntado. Que falte el DIAGNÓSTICO es normal: se deriva justo aquí.
    identidad_ausente = [c for c in COLUMNAS_IDENTIDAD if c in ausentes]
    if identidad_ausente:
        log.warning(
            "⚠️ La correlativa llegó sin %s. Se reconstruyen desde el golden; "
            "revise el registro de la fase L5, porque significa que algo falló "
            "antes en silencio.",
            ", ".join(identidad_ausente),
        )

    trabajo = correlativa.copy()
    origen: dict[str, str] = {}

    # ── 1. Identidad: sale del golden, que la tiene por construcción ──────
    if identidad_ausente:
        if golden is None or not {"ID_GRUPO", *identidad_ausente} <= set(golden.columns):
            disponibles = sorted(golden.columns)[:12] if golden is not None else "sin golden"
            raise RuntimeError(
                f"Qué pasó: la correlativa no trae {identidad_ausente} y el golden "
                f"no permite reconstruirlas. Por qué importa: sin NIT_FINAL y "
                f"RAZON_SOCIAL_FINAL el resultado no dice qué identidad adoptó "
                f"cada grupo, que es el entregable del cruce. Qué hacer: no use "
                f"este resultado; reporte el caso con el registro de la fase L5. "
                f"Columnas del golden: {disponibles}"
            )
        trabajo = trabajo.merge(
            golden[["ID_GRUPO", *identidad_ausente]].drop_duplicates("ID_GRUPO"),
            on="ID_GRUPO",
            how="left",
        )
        for columna in identidad_ausente:
            origen[columna] = "golden por ID_GRUPO"

    # ── 2. Diagnóstico: se calcula, ya con la identidad disponible ────────
    if columna_nombre is None:
        columna_nombre = "RAZON_SOCIAL" if "RAZON_SOCIAL" in trabajo.columns else "NOMBRE_LIMPIO"

    if "NAME_SIMILARITY_SCORE" in ausentes:
        if columna_nombre in trabajo.columns:
            trabajo["NAME_SIMILARITY_SCORE"] = _similitud_de_nombre(
                trabajo[columna_nombre], trabajo["RAZON_SOCIAL_FINAL"]
            )
            origen["NAME_SIMILARITY_SCORE"] = (
                f"calculada sobre {columna_nombre} normalizada contra "
                "RAZON_SOCIAL_FINAL normalizada (huella del selector, C38)"
            )
        else:
            # Sin columna de nombre no hay similitud posible. Se emite en cero
            # y se dice de dónde salió, en vez de omitir la columna: un
            # consumidor que espera cuatro columnas no debe recibir tres.
            trabajo["NAME_SIMILARITY_SCORE"] = 0.0
            origen["NAME_SIMILARITY_SCORE"] = "cero: no hay columna de nombre"
            log.warning(
                "No hay columna de nombre ('%s'): NAME_SIMILARITY_SCORE queda en 0.",
                columna_nombre,
            )

    if "NIT_DISTANCE" in ausentes:
        columna_identificador = "NIT" if "NIT" in trabajo.columns else None
        if columna_identificador:
            trabajo["NIT_DISTANCE"] = _distancia_de_identificador(
                trabajo[columna_identificador], trabajo["NIT_FINAL"]
            )
            origen["NIT_DISTANCE"] = f"calculada sobre {columna_identificador}"
        else:
            trabajo["NIT_DISTANCE"] = 0
            origen["NIT_DISTANCE"] = "cero: no hay columna de identificador"

    reporte = ReporteColumnasFinales(
        ya_estaban=presentes,
        calculadas=tuple(c for c in COLUMNAS_DIAGNOSTICO if c in ausentes),
        reparadas=tuple(identidad_ausente),
        origen=origen,
    )
    nivel = log.warning if reporte.hubo_anomalia else log.info
    nivel("   %s %s", "⚠️" if reporte.hubo_anomalia else "✅", reporte.resumen())
    return trabajo, reporte
