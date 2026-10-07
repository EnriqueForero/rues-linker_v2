"""record_linkage.salida.tecnicas — Recuperar las columnas técnicas que el contrato retiró (F1).

Qué es
------
Desde F1.9 ``NOMBRE_LIMPIO``, ``NOMBRE_BLOQUEO``, ``NIT_OK``, ``NIT_BASE``,
``NIT_VALID`` y ``PHONETIC_KEY1`` no viajan en la correlativa entregada
(``contrato.COLUMNAS_TECNICAS``): quedan en el checkpoint de L5 del
Orchestrator, ``<dir_trabajo>/L5_golden/correlative.parquet``, y el resultado
lo señala (``ResultadoLinkage.dir_trabajo``, ``ResultadoCruce.rutas
["dir_trabajo"]``, ``manifiesto["columnas_tecnicas"]["quedan_en"]``). Este
módulo es la única forma de volver a pegarlas a una correlativa del contrato;
los scripts de verificación (``scripts/verify_real_archives.py``,
``scripts/verificar_rues_x_exportaciones.py``) lo usan en vez de suponer que
siguen en el entregable.

Alineación (la parte que no es obvia)
-------------------------------------
* Sin colapso de duplicados exactos, ``ORIGINAL_INDEX`` de la correlativa es
  la posición de L1 y el parquet trae una fila por registro: se alinea por
  ``ORIGINAL_INDEX`` y se comprueba, vectorizado, que ``SRC``, ``NIT`` y
  ``RAZON_SOCIAL`` coinciden fila a fila. Sin esa comprobación, un
  ``head(n)`` de una correlativa expandida pasaría como alineable.
* Con ``collapse_exact_duplicates=True`` (``linkage``) o
  ``colapsar_duplicados_exactos=True`` (``ejecutar_cruce``, su valor por
  defecto) el parquet es COMPACTO (un representante por fila idéntica) y
  ``api._expand_exact_correlative`` renumera ``ORIGINAL_INDEX`` de la
  correlativa entregada a la posición ORIGINAL: la fila 3 entregada ya no es
  la fila 3 del parquet. Medido en F1 con 10 filas y 2 colapsadas: alinear por
  ``ORIGINAL_INDEX`` pegaba el ``NIT_BASE`` de BETA LTDA a un registro sin NIT.
  Aquí se alinea por contenido —``SRC``, ``NIT``, ``RAZON_SOCIAL``— porque las
  técnicas son funciones de ese contenido dentro de una corrida (NitProcessor
  sobre ``NIT``; TextProcessor sobre ``RAZON_SOCIAL``) y cada fila colapsada
  era idéntica a su representante. La propiedad se comprueba en el parquet
  antes de usarla; si no se cumple, o alguna fila entregada no encuentra
  pareja, se falla con mensaje accionable: no se adivina.
* ``dedupe()`` no pasa por el Orchestrator: sus técnicas quedan en
  ``correlativa.parquet`` de ``metricas["output_dir"]`` (por régimen,
  ``con_nit/`` y ``sin_nit/`` si el dataset es mixto; F2.10: lo escribe la
  primitiva del escritor único y ``metricas["stats_pipeline"]`` trae las
  rutas). Ese camino no se cubre aquí; el error lo dice.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-10-06  ·  Version: 0.23.0
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from ..pipeline.errores import ColumnasTecnicasError, mensaje_accionable

__all__ = [
    "CLAVE_CONTENIDO",
    "RUTA_CORRELATIVA_L5",
    "ReporteTecnicas",
    "adjuntar_tecnicas",
    "leer_tecnicas",
    "ruta_tecnicas",
]

#: Checkpoint de L5 del Orchestrator, relativo al ``dir_trabajo``: la última
#: tabla con las técnicas antes de que ``salida.completar`` las retire.
RUTA_CORRELATIVA_L5 = Path("L5_golden") / "correlative.parquet"

#: Columnas que identifican el contenido de un registro para alinear cuando
#: ``ORIGINAL_INDEX`` ya no es la posición (correlativa expandida tras colapsar).
CLAVE_CONTENIDO: tuple[str, ...] = ("SRC", "NIT", "RAZON_SOCIAL")

_EJEMPLOS = 3


@dataclass(frozen=True)
class ReporteTecnicas:
    """Qué se pegó, de dónde y cómo se alineó. Nada de esto ocurre en silencio.

    Attributes:
        origen: ruta del parquet leído.
        columnas: técnicas pegadas, en el orden pedido.
        alineacion: ``ORIGINAL_INDEX`` (posición de L1, sin colapso) o
            ``contenido`` (``SRC``·``NIT``·``RAZON_SOCIAL``, correlativa
            expandida tras colapsar duplicados exactos).
        filas_parquet: filas del checkpoint (compacto si hubo colapso).
        filas_correlativa: filas de la correlativa a la que se pegaron.
    """

    origen: Path
    columnas: tuple[str, ...]
    alineacion: str
    filas_parquet: int
    filas_correlativa: int

    def a_dict(self) -> dict[str, Any]:
        return {
            "origen": str(self.origen),
            "columnas": list(self.columnas),
            "alineacion": self.alineacion,
            "filas_parquet": self.filas_parquet,
            "filas_correlativa": self.filas_correlativa,
        }


def ruta_tecnicas(dir_trabajo: Path | str | None) -> Path:
    """Ruta del checkpoint con las técnicas; falla si no hay de dónde leer."""
    if dir_trabajo is None:
        raise ColumnasTecnicasError(
            mensaje_accionable(
                "el resultado no tiene dir_trabajo.",
                "las columnas técnicas (NIT_BASE, NIT_VALID…) no viajan en el entregable "
                "desde F1.9: solo están en el checkpoint de L5 de esa carpeta.",
                "use el resultado de linkage()/link()/ejecutar_cruce() (res.dir_trabajo, "
                "res.rutas['dir_trabajo']) o pase la carpeta work_dir de la corrida.",
            )
        )
    ruta = Path(dir_trabajo) / RUTA_CORRELATIVA_L5
    if not ruta.is_file():
        raise ColumnasTecnicasError(
            mensaje_accionable(
                f"no existe {ruta}.",
                "sin el checkpoint de L5 no hay de dónde recuperar las columnas técnicas "
                "que el contrato de salida retiró del entregable.",
                "conserve el dir_trabajo de la corrida (no lo borre antes de verificar) y "
                "use un resultado de linkage()/link()/ejecutar_cruce(); dedupe() no pasa "
                "por el Orchestrator y deja las técnicas en correlativa.parquet de "
                "metricas['output_dir'] (por régimen; rutas en "
                "metricas['stats_pipeline']), que este módulo no lee.",
            )
        )
    return ruta


def leer_tecnicas(dir_trabajo: Path | str | None, columnas: Sequence[str]) -> pd.DataFrame:
    """Lee del checkpoint ``ORIGINAL_INDEX``, la clave de contenido y ``columnas``.

    Falla con la lista de columnas disponibles si falta alguna pedida (o
    alguna de las que la alineación necesita).
    """
    ruta = ruta_tecnicas(dir_trabajo)
    disponibles = list(pq.read_schema(ruta).names)
    necesarias = ["ORIGINAL_INDEX", *CLAVE_CONTENIDO, *columnas]
    faltan = [c for c in necesarias if c not in disponibles]
    if faltan:
        raise ColumnasTecnicasError(
            mensaje_accionable(
                f"{ruta} no trae {faltan}; trae {disponibles}.",
                "sin esas columnas no se puede pegar ni alinear lo que se pidió.",
                "pida columnas de contrato.COLUMNAS_TECNICAS que el motor produzca en esta "
                "ruta (linkage()/link()/ejecutar_cruce()), o revise que el dir_trabajo sea "
                "el de ESTA corrida.",
            )
        )
    return pd.read_parquet(ruta, columns=list(dict.fromkeys(necesarias)))


def _clave(df: pd.DataFrame) -> pd.DataFrame:
    """La clave de contenido como texto, para comparar sin pelear con dtypes."""
    return df[list(CLAVE_CONTENIDO)].astype("string")


def _misma_clave(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    """¿Coinciden ``SRC``·``NIT``·``RAZON_SOCIAL`` fila a fila? (NA == NA)."""
    x, y = _clave(a).reset_index(drop=True), _clave(b).reset_index(drop=True)
    # ``==`` entre columnas ``string`` deja <NA> donde falta un lado; se cuenta
    # como igual solo si faltan los dos.
    iguales = (x == y).fillna(False) | (x.isna() & y.isna())
    return bool(iguales.to_numpy(dtype=bool).all())


def _por_original_index(correl: pd.DataFrame, tecnicas: pd.DataFrame) -> pd.DataFrame | None:
    """Filas del parquet en el orden de ``correl`` si ``ORIGINAL_INDEX`` es la posición."""
    if not tecnicas["ORIGINAL_INDEX"].is_unique:
        return None
    por_posicion = tecnicas.set_index("ORIGINAL_INDEX")
    posiciones = por_posicion.index.get_indexer(correl["ORIGINAL_INDEX"].to_numpy(dtype=np.int64))
    if (posiciones < 0).any():
        return None
    candidato = por_posicion.iloc[posiciones].reset_index(drop=True)
    if not _misma_clave(correl.reset_index(drop=True), candidato):
        return None
    return candidato


def _por_contenido(
    correl: pd.DataFrame, tecnicas: pd.DataFrame, columnas: Sequence[str], origen: Path
) -> pd.DataFrame:
    """Filas del parquet en el orden de ``correl`` por ``SRC``·``NIT``·``RAZON_SOCIAL``."""
    claves = list(CLAVE_CONTENIDO)
    tabla = pd.concat([_clave(tecnicas), tecnicas[list(columnas)]], axis=1).drop_duplicates()
    ambiguas = tabla.duplicated(subset=claves, keep=False)
    if ambiguas.any():
        ejemplos = tabla.loc[ambiguas, claves].head(_EJEMPLOS).to_dict("records")
        raise ColumnasTecnicasError(
            mensaje_accionable(
                f"{origen} trae filas con el mismo SRC·NIT·RAZON_SOCIAL y técnicas distintas "
                f"(p. ej. {ejemplos}).",
                "la correlativa se expandió tras colapsar duplicados exactos, ORIGINAL_INDEX ya "
                "no es la posición del checkpoint y la única alineación posible es por "
                "contenido; ese contenido no determina las técnicas, así que pegarlas sería "
                "adivinar.",
                "corra sin colapso (collapse_exact_duplicates=False en linkage(); "
                "colapsar_duplicados_exactos=False en ConfigCruce) para alinear por "
                "ORIGINAL_INDEX, y reporte el caso: las técnicas de L1 deberían ser función "
                "del registro.",
            )
        )
    unido = _clave(correl).merge(
        tabla, on=claves, how="left", validate="many_to_one", indicator=True
    )
    sin_pareja = unido["_merge"] != "both"
    if sin_pareja.any():
        ejemplos = unido.loc[sin_pareja, claves].head(_EJEMPLOS).to_dict("records")
        raise ColumnasTecnicasError(
            mensaje_accionable(
                f"{int(sin_pareja.sum())} fila(s) de la correlativa sin pareja en {origen} "
                f"(p. ej. {ejemplos}).",
                "o el checkpoint no es el de esta corrida, o la correlativa no es la que "
                "entregó el motor (columnas NIT/RAZON_SOCIAL modificadas); pegar técnicas de "
                "otra corrida produciría un QA falso.",
                "use el dir_trabajo que señala ESTE resultado y la correlativa tal como la "
                "entregó linkage()/ejecutar_cruce().",
            )
        )
    return unido


def adjuntar_tecnicas(
    correlativa: pd.DataFrame,
    dir_trabajo: Path | str | None,
    columnas: Sequence[str] = ("NIT_BASE", "NIT_VALID"),
) -> tuple[pd.DataFrame, ReporteTecnicas]:
    """Devuelve ``correlativa`` + ``columnas`` leídas del checkpoint de L5, alineadas.

    Args:
        correlativa: la del contrato (con ``SRC``, ``ORIGINAL_INDEX``, ``NIT``,
            ``RAZON_SOCIAL``), tal como la entregó el motor. No se muta.
        dir_trabajo: ``res.dir_trabajo`` / ``res.rutas["dir_trabajo"]``.
        columnas: técnicas a pegar (``contrato.COLUMNAS_TECNICAS``).

    Returns:
        ``(correlativa con las columnas al final, reporte)``; el reporte dice
        cómo se alineó (``ORIGINAL_INDEX`` o ``contenido``).

    Raises:
        ColumnasTecnicasError: sin ``dir_trabajo``, sin parquet, sin alguna
            columna, si ``correlativa`` ya trae alguna de las pedidas, o si no
            se puede alinear sin adivinar.
    """
    columnas = tuple(columnas)
    if not columnas:
        raise ColumnasTecnicasError(
            mensaje_accionable(
                "no se pidió ninguna columna.",
                "no hay nada que pegar.",
                "pase al menos una de contrato.COLUMNAS_TECNICAS (p. ej. NIT_BASE).",
            )
        )
    ya = [c for c in columnas if c in correlativa.columns]
    if ya:
        raise ColumnasTecnicasError(
            mensaje_accionable(
                f"la correlativa ya trae {ya}.",
                "pegarlas otra vez dejaría dos columnas con el mismo nombre y valores que "
                "podrían no coincidir.",
                "adjunte solo las que faltan, o parta de la correlativa del contrato.",
            )
        )
    faltan = [c for c in ("ORIGINAL_INDEX", *CLAVE_CONTENIDO) if c not in correlativa.columns]
    if faltan:
        raise ColumnasTecnicasError(
            mensaje_accionable(
                f"la correlativa no trae {faltan}.",
                "sin ORIGINAL_INDEX y SRC·NIT·RAZON_SOCIAL no hay con qué alinear el checkpoint.",
                "pase la correlativa del contrato tal como la entregó linkage()/ejecutar_cruce().",
            )
        )
    origen = ruta_tecnicas(dir_trabajo)
    tecnicas = leer_tecnicas(dir_trabajo, columnas)

    alineadas = _por_original_index(correlativa, tecnicas)
    alineacion = "ORIGINAL_INDEX"
    if alineadas is None:
        alineadas = _por_contenido(correlativa, tecnicas, columnas, origen)
        alineacion = "contenido"

    valores = alineadas[list(columnas)].copy()
    valores.index = correlativa.index
    reporte = ReporteTecnicas(
        origen=origen,
        columnas=columnas,
        alineacion=alineacion,
        filas_parquet=len(tecnicas),
        filas_correlativa=len(correlativa),
    )
    return pd.concat([correlativa, valores], axis=1), reporte
