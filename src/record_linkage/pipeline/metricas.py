"""Métricas de una corrida desde la verdad en disco: UNA regla, escrita una vez.

Qué resuelve
------------
Hasta F1.12 (ronda 2) había dos implementaciones de las mismas cifras:
``Orchestrator._build_metrics`` (lo que L6 entrega a dashboards, resumen
ejecutivo y al alias ``config_auditoria.json``) y ``exporters.escritor``
(``manifest.json → metricas``). Ya divergían (una aceptaba la columna
heredada ``SOURCE_COUNT``, la otra no; una coaccionaba ``CONFIDENCE_SCORE``
con ``to_numeric``, la otra no) y con el tiempo el alias y el manifiesto
habrían dicho cifras distintas para la misma corrida.

Aquí vive la regla única, ``metricas_de_corrida``: devuelve el bloque en
español (``CLAVES_METRICAS``). El escritor lo copia tal cual al manifiesto;
``Orchestrator._build_metrics`` lo incluye y DERIVA de él sus alias en inglés
(``reduction_rate``, ``multi_source_groups``, ``avg_confidence``…) para los
consumidores de v1.

También se declara aquí una sola vez dónde deja el motor sus bases SQLite
dentro del directorio de trabajo (``RUTA_CANDIDATES_DB``; ``RUTA_SCORED_DB``
viene de ``salida.completar``, que ya la necesitaba para ``SCORE_PAR``) y
cómo se cuentan sus filas sin cargarlas (``contar_filas_sqlite``, la regla
del banco: ``evaluation.banco`` la importa de aquí).

Convención: ``None`` —nunca 0— cuando una cifra no se puede saber (no hay
``_trabajo/``, la base no está, el golden no trae la columna). Un 0 sería
una cifra falsa; los reportes muestran «N/A».
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from ..salida.completar import RUTA_SCORED_DB

__all__ = [
    "CLAVES_METRICAS",
    "RUTA_CANDIDATES_DB",
    "RUTA_SCORED_DB",
    "contar_filas_sqlite",
    "metricas_de_corrida",
]

#: Pares candidatos de L2 dentro del directorio de trabajo (tabla
#: ``candidate_pairs``). Es el mismo archivo que cuenta el banco.
RUTA_CANDIDATES_DB = Path("L2_lsh_candidates") / "candidates.db"

#: Claves del bloque, en el orden en que se escriben.
CLAVES_METRICAS: tuple[str, ...] = (
    "candidatos",
    "pares_puntuados",
    "tasa_reduccion",
    "grupos_multifuente",
    "confianza_media",
    "confianza_mediana",
    "segundos_total",
    "rss_pico_mib",
)

#: Columna del golden con el número de fuentes por grupo: la del contrato y
#: la grafía heredada de v1, en ese orden de preferencia.
_COLUMNAS_FUENTES_POR_GRUPO: tuple[str, ...] = ("SOURCES_COUNT", "SOURCE_COUNT")


def contar_filas_sqlite(ruta: Path, tabla: str) -> int | None:
    """Cuenta filas de una tabla SQLite sin cargarla. ``None`` si no se puede."""
    if not ruta.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{ruta}?mode=ro", uri=True) as conexion:
            existe = conexion.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tabla,)
            ).fetchone()
            if not existe:
                return None
            return int(conexion.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0])
    except sqlite3.Error:
        return None


def metricas_de_corrida(
    golden: pd.DataFrame | None,
    correlativa: pd.DataFrame,
    dir_trabajo: Path | str | None,
    tiempos: Mapping[str, float],
    rss: Mapping[str, float],
) -> dict[str, Any]:
    """El bloque ``metricas`` de una corrida (``CLAVES_METRICAS``), desde la verdad en disco.

    - ``candidatos`` / ``pares_puntuados``: filas de ``RUTA_CANDIDATES_DB`` /
      ``RUTA_SCORED_DB`` bajo ``dir_trabajo``; ``None`` sin directorio o sin
      la base (``dedupe`` no deja ``scored.db``).
    - ``tasa_reduccion``: ``1 - grupos / filas`` de la correlativa; ``None``
      sin filas.
    - ``grupos_multifuente``: grupos del golden con más de una fuente
      (``SOURCES_COUNT``, o ``SOURCE_COUNT`` heredado); ``None`` sin la columna.
    - ``confianza_media`` / ``confianza_mediana``: de ``CONFIDENCE_SCORE`` del
      golden tal como viene (el contrato lo tipa; una columna rota debe fallar,
      no coaccionarse); ``None`` sin la columna o sin filas.
    - ``segundos_total``: suma de ``tiempos`` (segundos por fase); ``None`` si
      no hay fases.
    - ``rss_pico_mib``: máximo de ``rss`` (MiB por fase); ``None`` si no hay.

    Args:
        golden: golden de la corrida (``None`` en rutas que no lo producen).
        correlativa: correlativa con ``ID_GRUPO``.
        dir_trabajo: directorio L1…L5 (``_trabajo/`` o ``work_dir``).
        tiempos: segundos por fase (del manifiesto de trabajo o del Orchestrator).
        rss: pico de RSS por fase, misma fuente.
    """
    trabajo = Path(dir_trabajo) if dir_trabajo is not None else None
    filas = len(correlativa)
    grupos = int(correlativa["ID_GRUPO"].nunique()) if filas else 0
    metricas: dict[str, Any] = dict.fromkeys(CLAVES_METRICAS)
    if trabajo is not None:
        metricas["candidatos"] = contar_filas_sqlite(
            trabajo / RUTA_CANDIDATES_DB, "candidate_pairs"
        )
        metricas["pares_puntuados"] = contar_filas_sqlite(trabajo / RUTA_SCORED_DB, "scored_pairs")
    if filas:
        metricas["tasa_reduccion"] = 1 - grupos / filas
    if golden is not None:
        columna = next((c for c in _COLUMNAS_FUENTES_POR_GRUPO if c in golden.columns), None)
        if columna is not None:
            metricas["grupos_multifuente"] = int((golden[columna] > 1).sum())
        if "CONFIDENCE_SCORE" in golden.columns and len(golden):
            puntajes = golden["CONFIDENCE_SCORE"]
            metricas["confianza_media"] = float(puntajes.mean())
            metricas["confianza_mediana"] = float(puntajes.median())
    if tiempos:
        metricas["segundos_total"] = round(sum(tiempos.values()), 2)
    if rss:
        metricas["rss_pico_mib"] = max(rss.values())
    return metricas
