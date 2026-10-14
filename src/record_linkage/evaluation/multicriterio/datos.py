"""Datos de la prueba multicriterio: dónde está la plantilla, las etiquetas, los registros y los pares.

:func:`localizar_plantilla` encuentra la plantilla (un ``.xlsx`` o una carpeta con
``plantilla_fuente_*.csv``) o la descarga de ``main``; :func:`leer_etiquetas_de` lee
``ETIQUETAS_PARES`` de una ruta explícita, del CSV de la carpeta o de la hoja del Excel;
:func:`marco_registros` junta todas las fuentes indexadas por ``ID_REGISTRO_FUENTE``;
:func:`caso_por_id` da el caso de cada registro; :func:`pares_de_prueba` arma los positivos y los
negativos difíciles sobre los que se mide el aporte de cada variable. Parte del paquete
``evaluation.multicriterio``.
"""

from __future__ import annotations

import urllib.request
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pandas as pd

from record_linkage.evaluation.plantilla_base_real import COL_ID, LibroPlantilla, pares_verdaderos
from record_linkage.evaluation.verdad_real import leer_etiquetas

HOJA_ETIQUETAS = "ETIQUETAS_PARES"
CSV_ETIQUETAS = "plantilla_etiquetas_pares.csv"
#: De dónde se descarga la plantilla cuando no hay checkout ni copia local (CSV de ``data/ejemplos``).
URL_RAW_PLANTILLA = (
    "https://raw.githubusercontent.com/EnriqueForero/rues-linker_v2/main/data/ejemplos/"
)
#: Los CSV de la plantilla que hacen falta para medir (fuentes, verdad, casos y etiquetas).
ARCHIVOS_PLANTILLA: tuple[str, ...] = (
    "plantilla_fuente_crm.csv",
    "plantilla_fuente_rues.csv",
    "plantilla_fuente_aduanas.csv",
    "plantilla_verdad_grupos.csv",
    "plantilla_casos.csv",
    CSV_ETIQUETAS,
)


def leer_etiquetas_de(
    datos: str | Path, etiquetas: str | Path | None = None
) -> pd.DataFrame | None:
    """Las etiquetas de pares: la ruta explícita, el CSV de la carpeta o la hoja del Excel; ``None``
    si no hay ninguna (una base real sin etiquetar)."""
    datos = Path(datos)
    if etiquetas is not None:
        return leer_etiquetas(etiquetas)
    if datos.is_file():
        hojas = pd.ExcelFile(datos).sheet_names
        return leer_etiquetas(datos, hoja=HOJA_ETIQUETAS) if HOJA_ETIQUETAS in hojas else None
    ruta = datos / CSV_ETIQUETAS
    return leer_etiquetas(ruta) if ruta.is_file() else None


# ---------------------------------------------------------------------------
# Dónde está la plantilla de demostración
# ---------------------------------------------------------------------------


def _tiene_plantilla(candidato: Path) -> bool:
    if candidato.is_file():
        return candidato.suffix.lower() in (".xlsx", ".xlsm")
    return candidato.is_dir() and any(candidato.glob("plantilla_fuente_*.csv"))


def _descargar_plantilla(destino: Path) -> Path:
    """Baja los CSV de :data:`ARCHIVOS_PLANTILLA` desde ``main`` a ``destino`` (fail-fast)."""
    destino.mkdir(parents=True, exist_ok=True)
    for nombre in ARCHIVOS_PLANTILLA:
        url = URL_RAW_PLANTILLA + nombre
        if not url.startswith("https://"):
            raise ValueError(f"Solo se descarga por https: {url}")
        try:
            with urllib.request.urlopen(url, timeout=60) as respuesta:
                contenido = respuesta.read()
        except OSError as exc:
            raise ConnectionError(
                f"No se pudo descargar {url} ({exc}). Qué hacer: suba los CSV de data/ejemplos "
                f"(o el Excel plantilla_base_real.xlsx) a {destino.parent} y vuelva a correr."
            ) from exc
        (destino / nombre).write_bytes(contenido)
    return destino


def localizar_plantilla(
    candidatos: Sequence[str | Path], descargar_a: str | Path | None = None
) -> Path:
    """La primera ruta de ``candidatos`` que tiene la plantilla (un ``.xlsx`` o una carpeta con
    ``plantilla_fuente_*.csv``). Si ninguna la tiene y ``descargar_a`` viene, descarga los CSV del
    repositorio (``main``) allí y devuelve esa carpeta; si no, ``FileNotFoundError`` con qué hacer."""
    rutas = [Path(c) for c in candidatos]
    for ruta in rutas:
        if _tiene_plantilla(ruta):
            return ruta
    if descargar_a is not None:
        return _descargar_plantilla(Path(descargar_a))
    raise FileNotFoundError(
        "No se encontró la plantilla en " + ", ".join(str(r) for r in rutas) + ". Qué hacer: "
        "copie data/ejemplos/plantilla_*.csv (o plantilla_base_real.xlsx) a una de esas rutas, "
        "o pase descargar_a= para bajarla del repositorio."
    )


# ---------------------------------------------------------------------------
# Los registros de todas las fuentes y los pares de prueba (positivos y negativos difíciles)
# ---------------------------------------------------------------------------


def marco_registros(libro: LibroPlantilla) -> pd.DataFrame:
    """Todas las fuentes en un solo marco indexado por ``ID_REGISTRO_FUENTE`` (nombres de columna
    de la plantilla: MUNICIPIO, no CIUDAD)."""
    marco = pd.concat(libro.fuentes.values(), ignore_index=True)
    return marco.set_index(pd.Index(marco[COL_ID].astype(str)))


def caso_por_id(verdad: pd.DataFrame | None) -> pd.Series | None:
    """Serie ``ID_REGISTRO_FUENTE → CASO`` de VERDAD_GRUPOS; ``None`` sin verdad o sin la columna
    ``CASO`` (una VERDAD_GRUPOS mínima solo trae el id y el grupo)."""
    if verdad is None or "CASO" not in verdad.columns:
        return None
    return pd.Series(
        verdad["CASO"].astype(str).to_numpy(), index=pd.Index(verdad[COL_ID].astype(str))
    )


def pares_de_prueba(
    verdad: pd.DataFrame, casos: pd.DataFrame, etiquetas: pd.DataFrame | None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(positivos, negativos)`` con columnas ``ID_1`` y ``ID_2`` (``ID_1 < ID_2``).

    Positivos: todos los pares de un mismo grupo verdadero (``plantilla_base_real.pares_verdaderos``,
    la misma regla que mide precisión y recall). Negativos difíciles: los ``IS_MATCH=0`` de las
    etiquetas más los pares entre grupos verdaderos distintos dentro de cada caso ``no unir`` (menos
    ``singleton``, que no es un negativo diseñado sino registros sueltos). Exige la columna ``CASO``.
    """
    if "CASO" not in verdad.columns:
        raise ValueError(
            "VERDAD_GRUPOS no trae la columna CASO: sin ella no hay negativos difíciles por caso. "
            "Añádala (un caso por grupo) o prescinda del aporte por variable."
        )
    marco = verdad[[COL_ID, "ID_GRUPO", "CASO"]].astype(str)
    positivos = pd.DataFrame(
        sorted(pares_verdaderos(verdad)), columns=[f"{COL_ID}_1", f"{COL_ID}_2"]
    )
    negativos_caso = set(casos.loc[casos["QUE_DEBE_HACER_EL_MOTOR"] == "no unir", "CASO"]) - {
        "singleton"
    }
    sub = marco[marco["CASO"].isin(negativos_caso)]
    cruce = sub.merge(sub, on="CASO", suffixes=("_1", "_2"))
    cruce = cruce[
        (cruce[f"{COL_ID}_1"] < cruce[f"{COL_ID}_2"]) & (cruce["ID_GRUPO_1"] != cruce["ID_GRUPO_2"])
    ]
    partes = [cruce[[f"{COL_ID}_1", f"{COL_ID}_2"]]]
    if etiquetas is not None:
        neg = etiquetas.loc[etiquetas["IS_MATCH"] == 0, ["ID_1", "ID_2"]].astype(str)
        a, b = neg["ID_1"].to_numpy(dtype=object), neg["ID_2"].to_numpy(dtype=object)
        partes.append(
            pd.DataFrame({f"{COL_ID}_1": np.minimum(a, b), f"{COL_ID}_2": np.maximum(a, b)})
        )
    negativos = pd.concat(partes, ignore_index=True).drop_duplicates()
    renombre = {f"{COL_ID}_1": "ID_1", f"{COL_ID}_2": "ID_2"}
    return (
        positivos[[f"{COL_ID}_1", f"{COL_ID}_2"]].rename(columns=renombre).reset_index(drop=True),
        negativos.rename(columns=renombre).reset_index(drop=True),
    )
