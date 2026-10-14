"""engine.candidatos.hibrido — el generador de candidatos híbrido de L2 (Fase 2, 2026-10-13).

Compone estrategias puras y escribe exactamente lo que L3 consume: ``candidates.db`` con ``candidate_pairs(idx_0, idx_1)``.

* ``por_identificador``: NIT base exacto y a distancia 1 (``engine.lsh.nit_blocking``, el mismo que usa el LSH).
* ``por_token``: tokens distintivos con tolerancia de una errata (``engine.candidatos.nombre``).
* ``por_tfidf``: coseno TF-IDF de trigramas, umbral y top-k (``engine.candidatos.nombre``).
* ``por_llave``: igualdad exacta de una columna canónica (teléfono, correo, documento…), con cubeta acotada.

Por qué existe: el LSH de MinHash gastaba 23–65 candidatos por par válido y perdía el 28 % de los pares verdaderos sin
identificador (`docs/DIAGNOSTICO_Y_RUTA_2026-10-13.md`, H3). Aquí cada estrategia rinde lotes que se escriben y se sueltan,
la política de fuentes se aplica al emitir (no después), y las cubetas tienen cota dura: el costo es proporcional a los
pares emitidos, nunca al cuadrado de los registros.

Se activa con la perilla de perfil ``motor_candidatos = "hibrido"`` (el defecto sigue siendo ``"lsh"``: cambio declarado,
no silencioso) y se afina con ``candidatos_hibrido = {...}`` (ver :class:`ConfigCandidatosHibrido`).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, fields
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from record_linkage.engine.candidatos.nombre import (
    pares_por_nombre_tfidf,
    pares_por_token_distintivo,
)
from record_linkage.engine.lsh.nit_blocking import NitBlockingConfig, iter_pares_por_nit
from record_linkage.engine.lsh.politica_pares import pares_por_bloque
from record_linkage.matching.genericos import genericos

__all__ = [
    "ConfigCandidatosHibrido",
    "EstadisticasCandidatos",
    "escribir_candidatos_sqlite",
    "generar_candidatos_hibrido",
    "matriz_politica_fuentes",
    "recall_de_pares",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConfigCandidatosHibrido:
    """Perillas del generador híbrido. Los defectos son los medidos en el banco de 30k (2026-10-13).

    Attributes:
        nit, nit_vecinos, nit_radio, nit_max_bucket: bloqueo por identificador (los mismos valores que el LSH).
        tokens, tokens_k, tokens_max_bucket, tokens_min_len, tokens_borrados: tokens distintivos.
        tfidf, tfidf_umbral, tfidf_top_k, tfidf_ngram, tfidf_max_df, tfidf_max_filas: TF-IDF de trigramas.
        llaves, llaves_max_bucket: columnas canónicas cuya igualdad exacta propone un par.
        columna_nombre, columna_nit, columna_fuente: dónde leer cada cosa en el DataFrame de L1.
    """

    nit: bool = True
    nit_vecinos: bool = True
    nit_radio: int = 1
    nit_max_bucket: int = 200
    tokens: bool = True
    tokens_k: int = 2
    tokens_max_bucket: int = 300
    tokens_min_len: int = 4
    # Apagadas por defecto: medido en el banco (docs/evidencia/fase2_candidatos_hibridos.md, hallazgo 4), las
    # variantes por borrado agregan 138.000 candidatos y 8 s de L2 sin cambiar un solo resultado: los trigramas
    # de TF-IDF ya cubren las erratas de una letra.
    tokens_borrados: bool = False
    tfidf: bool = True
    tfidf_umbral: float = 0.4
    tfidf_top_k: int = 50
    tfidf_ngram: int = 3
    tfidf_max_df: float = 0.05
    tfidf_max_filas: int = 1_500_000
    llaves: tuple[str, ...] = ()
    llaves_max_bucket: int = 200
    columna_nombre: str = "NOMBRE_LIMPIO"
    columna_nit: str = "NIT_BASE"
    columna_fuente: str = "FUENTE"

    @classmethod
    def desde_perfil(cls, perfil: Mapping[str, Any]) -> ConfigCandidatosHibrido:
        """Lee ``candidatos_hibrido`` del perfil y hereda las perillas de NIT que el LSH ya tenía.

        Raises:
            ValueError: una clave desconocida en ``candidatos_hibrido`` (fail-fast, como los overrides del perfil).
        """
        propios = dict(perfil.get("candidatos_hibrido") or {})
        conocidas = {f.name for f in fields(cls)}
        raras = sorted(set(propios) - conocidas)
        if raras:
            raise ValueError(
                f"Qué pasó: candidatos_hibrido trae claves desconocidas {raras}.\n"
                f"Qué hacer: use una de {sorted(conocidas)}."
            )
        heredadas: dict[str, Any] = {}
        if "enable_nit_blocking" in perfil:
            heredadas["nit"] = bool(perfil["enable_nit_blocking"])
        if "nit_blocking_neighbors" in perfil:
            heredadas["nit_vecinos"] = bool(perfil["nit_blocking_neighbors"])
        if "nit_blocking_radio" in perfil:
            heredadas["nit_radio"] = int(perfil["nit_blocking_radio"])
        if "nit_blocking_max_bucket" in perfil:
            heredadas["nit_max_bucket"] = int(perfil["nit_blocking_max_bucket"])
        if "nit_blocking_column" in perfil:
            heredadas["columna_nit"] = str(perfil["nit_blocking_column"])
        if perfil.get("llaves_bloqueo"):
            heredadas["llaves"] = tuple(str(x) for x in perfil["llaves_bloqueo"])
        if "llaves" in propios:
            propios["llaves"] = tuple(propios["llaves"])
        return cls(**{**heredadas, **propios})


@dataclass
class EstadisticasCandidatos:
    """Qué emitió cada estrategia y cuánto costó; va al registro y al manifiesto."""

    registros: int = 0
    emitidos: dict[str, int] = field(default_factory=dict)
    nuevos: dict[str, int] = field(default_factory=dict)
    segundos: dict[str, float] = field(default_factory=dict)
    total: int = 0
    segundos_total: float = 0.0

    def como_dict(self) -> dict[str, Any]:
        """Serializable (manifiesto, evidencia)."""
        return {
            "registros": self.registros,
            "emitidos": dict(self.emitidos),
            "nuevos": dict(self.nuevos),
            "segundos": {k: round(v, 3) for k, v in self.segundos.items()},
            "total": self.total,
            "por_registro": round(self.total / self.registros, 2) if self.registros else 0.0,
            "segundos_total": round(self.segundos_total, 3),
        }


def matriz_politica_fuentes(
    nombres_fuente: np.ndarray,
    *,
    cross_source_only: bool,
    fuentes_confiables: frozenset[str] = frozenset(),
) -> np.ndarray:
    """Matriz k×k de combinaciones de fuente permitidas, con la misma semántica que los motores LSH.

    Con ``fuentes_confiables`` (``TrustedSourceLSHEngine``) se veta solo el par interno de una fuente confiable;
    si no, ``cross_source_only`` veta todo par interno; si no, todo se permite.
    """
    k = len(nombres_fuente)
    permitido = np.ones((k, k), dtype=bool)
    if fuentes_confiables:
        for i, nombre in enumerate(nombres_fuente):
            if str(nombre) in fuentes_confiables:
                permitido[i, i] = False
    elif cross_source_only:
        np.fill_diagonal(permitido, False)
    return permitido


def _codigos_de_fuente(
    df: pd.DataFrame, columna: str, *, cross_source_only: bool, fuentes_confiables: frozenset[str]
) -> tuple[np.ndarray, np.ndarray]:
    if columna not in df.columns:
        return np.zeros(len(df), dtype=np.int16), np.ones((1, 1), dtype=bool)
    nombres, codigos = np.unique(df[columna].astype(str).to_numpy(), return_inverse=True)
    return codigos.astype(np.int16, copy=False), matriz_politica_fuentes(
        nombres, cross_source_only=cross_source_only, fuentes_confiables=fuentes_confiables
    )


def _pares_por_llave(
    valores: pd.Series, codigos_fuente: np.ndarray, politica: np.ndarray, *, max_bucket: int
) -> Iterator[np.ndarray]:
    """Pares con el mismo valor no vacío de una columna canónica."""
    texto = valores.fillna("").astype(str).str.strip().str.upper()
    con_valor = (texto != "") & (~texto.str.lower().isin(["nan", "none", "null", "n/a"])).to_numpy()
    if not con_valor.any():
        return
    posiciones = np.flatnonzero(con_valor)
    codigos = pd.factorize(texto.to_numpy()[con_valor])[0].astype(np.int64)
    yield from pares_por_bloque(
        codigos, posiciones, codigos_fuente[con_valor], politica, tam_maximo=max_bucket
    )


def generar_candidatos_hibrido(
    df: pd.DataFrame,
    config: ConfigCandidatosHibrido | None = None,
    *,
    cross_source_only: bool = False,
    fuentes_confiables: frozenset[str] = frozenset(),
    genericos_extra: frozenset[str] = frozenset(),
    registrador: logging.Logger | None = None,
) -> Iterator[tuple[str, np.ndarray]]:
    """Rinde ``(estrategia, lote)`` con los pares candidatos de cada estrategia activa.

    ``df`` es el DataFrame de L1 con índice posicional ``0..n-1`` (``columna_nombre``, ``columna_nit``, ``columna_fuente``).
    Los lotes son ``(m, 2)`` int64 ``(menor, mayor)``; pueden repetir pares entre estrategias (el escritor los funde).
    """
    cfg = config or ConfigCandidatosHibrido()
    log = registrador or logger
    df = df.reset_index(drop=True)
    codigos, politica = _codigos_de_fuente(
        df,
        cfg.columna_fuente,
        cross_source_only=cross_source_only,
        fuentes_confiables=fuentes_confiables,
    )
    if cfg.nit and cfg.columna_nit in df.columns:
        nit_cfg = NitBlockingConfig(
            enable_exact=True,
            enable_neighbors=cfg.nit_vecinos,
            radio_vecindad=cfg.nit_radio,
            max_bucket_size=cfg.nit_max_bucket,
            min_nit_length=6,
        )
        for lote in iter_pares_por_nit(
            df, nit_column=cfg.columna_nit, config=nit_cfg, codigos_fuente=codigos,
            politica_fuentes=politica, registrador=log,
        ):  # fmt: skip
            yield "por_identificador", lote
    elif cfg.nit:
        log.info(
            f"[hibrido] sin columna {cfg.columna_nit!r}: se omite el bloqueo por identificador"
        )
    nombres = df[cfg.columna_nombre] if cfg.columna_nombre in df.columns else None
    if nombres is None:
        log.warning(f"[hibrido] sin columna {cfg.columna_nombre!r}: no hay candidatos por nombre")
    else:
        if cfg.tokens:
            for lote in pares_por_token_distintivo(
                nombres, codigos_fuente=codigos, politica_fuentes=politica, k_tokens=cfg.tokens_k,
                max_bucket=cfg.tokens_max_bucket, min_len=cfg.tokens_min_len,
                genericos=genericos(extra=genericos_extra), con_borrados=cfg.tokens_borrados, registrador=log,
            ):  # fmt: skip
                yield "por_token", lote
        if cfg.tfidf:
            for lote in pares_por_nombre_tfidf(
                nombres, codigos_fuente=codigos, politica_fuentes=politica, umbral=cfg.tfidf_umbral,
                top_k=cfg.tfidf_top_k, ngram=cfg.tfidf_ngram, max_df=cfg.tfidf_max_df,
                max_filas=cfg.tfidf_max_filas, registrador=log,
            ):  # fmt: skip
                yield "por_tfidf", lote
    for columna in cfg.llaves:
        if columna not in df.columns:
            log.warning(f"[hibrido] llave {columna!r} no está en la base: se omite")
            continue
        for lote in _pares_por_llave(
            df[columna], codigos, politica, max_bucket=cfg.llaves_max_bucket
        ):
            yield f"por_llave:{columna}", lote


def _huella(df: pd.DataFrame, cfg: ConfigCandidatosHibrido) -> str:
    h = hashlib.sha256()
    h.update(f"n={len(df)};cfg={cfg!r};".encode())
    for columna in (cfg.columna_nombre, cfg.columna_nit, cfg.columna_fuente):
        if columna in df.columns:
            h.update(
                pd.util.hash_pandas_object(df[columna].astype(str), index=False)
                .to_numpy()
                .tobytes()
            )
    return h.hexdigest()


def escribir_candidatos_sqlite(
    lotes: Iterator[tuple[str, np.ndarray]],
    ruta: str | Path,
    *,
    registros: int,
    huella: str = "",
    registrador: logging.Logger | None = None,
) -> EstadisticasCandidatos:
    """Escribe los lotes en ``candidates.db`` con el esquema que L3 lee; los pares repetidos se funden en la clave primaria.

    Crea el archivo de cero (borra uno previo). ``_progress`` queda con ``completed_bands = 0`` y la ``huella``: es la
    tabla que el LSH usa para reanudar; aquí solo documenta de qué corrida salió el archivo.
    """
    log = registrador or logger
    ruta = Path(ruta)
    for sufijo in ("", "-wal", "-shm", "-journal"):
        p = Path(str(ruta) + sufijo)
        if p.exists():
            p.unlink()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    estadisticas = EstadisticasCandidatos(registros=registros)
    t_total = time.perf_counter()
    # autocommit: las transacciones se manejan a mano (BEGIN/COMMIT por lote); con el modo por defecto de
    # sqlite3 el INSERT en _progress abre una transacción implícita y el BEGIN siguiente falla.
    con = sqlite3.connect(str(ruta), isolation_level=None)
    try:
        cur = con.cursor()
        cur.execute("PRAGMA journal_mode=OFF")
        cur.execute("PRAGMA synchronous=OFF")
        cur.execute(
            "CREATE TABLE candidate_pairs (idx_0 INTEGER NOT NULL, idx_1 INTEGER NOT NULL, "
            "PRIMARY KEY (idx_0, idx_1)) WITHOUT ROWID"
        )
        cur.execute(
            "CREATE TABLE _progress (id INTEGER PRIMARY KEY CHECK (id = 1), "
            "completed_bands INTEGER DEFAULT 0, fingerprint TEXT NOT NULL)"
        )
        cur.execute("INSERT INTO _progress VALUES (1, 0, ?)", (huella or "hibrido",))
        total = 0
        t_estrategia = time.perf_counter()
        actual = ""
        for estrategia, lote in lotes:
            if estrategia != actual:
                if actual:
                    estadisticas.segundos[actual] = estadisticas.segundos.get(actual, 0.0) + (
                        time.perf_counter() - t_estrategia
                    )
                actual, t_estrategia = estrategia, time.perf_counter()
            if lote.size == 0:
                continue
            pares = np.unique(
                np.sort(np.asarray(lote, dtype=np.int64).reshape(-1, 2), axis=1), axis=0
            )
            pares = pares[pares[:, 0] != pares[:, 1]]
            cur.execute("BEGIN")
            cur.executemany(
                "INSERT OR IGNORE INTO candidate_pairs VALUES (?, ?)", map(tuple, pares.tolist())
            )
            cur.execute("COMMIT")
            nuevo_total = cur.execute("SELECT COUNT(*) FROM candidate_pairs").fetchone()[0]
            estadisticas.emitidos[estrategia] = estadisticas.emitidos.get(estrategia, 0) + len(
                pares
            )
            estadisticas.nuevos[estrategia] = estadisticas.nuevos.get(estrategia, 0) + (
                nuevo_total - total
            )
            total = nuevo_total
        if actual:
            estadisticas.segundos[actual] = estadisticas.segundos.get(actual, 0.0) + (
                time.perf_counter() - t_estrategia
            )
        estadisticas.total = total
    finally:
        con.close()
    estadisticas.segundos_total = time.perf_counter() - t_total
    log.info(
        f"[hibrido] {estadisticas.total:,} candidatos ({estadisticas.como_dict()['por_registro']} por registro) "
        f"en {estadisticas.segundos_total:.1f}s: "
        + ", ".join(
            f"{k} {estadisticas.nuevos.get(k, 0):,} nuevos de {v:,}"
            for k, v in estadisticas.emitidos.items()
        )
    )
    return estadisticas


def recall_de_pares(
    pares: np.ndarray, grupos_verdad: np.ndarray, *, max_grupo: int = 2000
) -> float:
    """Fracción de los pares verdaderos (mismo grupo) que están entre los candidatos. Para medir, no para producir."""
    n = len(grupos_verdad)
    orden = np.argsort(grupos_verdad, kind="stable")
    g = grupos_verdad[orden]
    cortes = np.flatnonzero(np.r_[True, g[1:] != g[:-1], True])
    verdaderos: set[tuple[int, int]] = set()
    for a, b in pairwise(cortes):
        miembros = np.sort(orden[a:b])
        if 1 < len(miembros) <= max_grupo:
            ii, jj = np.triu_indices(len(miembros), 1)
            verdaderos.update(zip(miembros[ii].tolist(), miembros[jj].tolist(), strict=True))
    if not verdaderos:
        return 0.0
    p = np.sort(np.asarray(pares, dtype=np.int64).reshape(-1, 2), axis=1)
    candidatos = set(zip(p[:, 0].tolist(), p[:, 1].tolist(), strict=True))
    return len(verdaderos & candidatos) / len(verdaderos) if n else 0.0
