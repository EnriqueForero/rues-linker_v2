"""engine.lsh.name_blocking — Bloqueo de candidatos por nombre multi-pasada.

Contexto: Google Colab Free (~12 GB RAM, 2–4 M registros).

El LSH actual usa una sola pasada: n-gramas de tamaño 3 sobre
``NOMBRE_LIMPIO``. Esto deja fuera pares verdaderos donde:

    1. **Sufijos societarios distintos**: ``ECOPETROL LIMITADA`` ↔
       ``ECOPETROL SA``. El n-grama=3 ve sufijos distintos y separa los
       buckets aunque la raíz del nombre sea idéntica.
    2. **Token corto compartido**: ``COLANTA`` ↔ ``cooperativa colanta``.
       El nombre corto puede no compartir suficientes n-gramas con el largo.
    3. **Orden de tokens distinto**: ``BOLIVAR CONSTRUCTORA`` ↔
       ``constructora bolivar``. Los n-gramas comparten algo pero la firma
       LSH difiere lo suficiente para perder el bucket.
    4. **Prefijo común que el LSH descarta**: ``YAMAHA DE COLOMBIA`` ↔
       ``INDUSTRIAS YAMAHA DE COLOMBIA``.

Diagnóstico sobre el ground truth exhaustivo (v2.5.0): 28.9 % de los pares
verdaderos NO los captura el bloqueo de NIT. De esos, una fracción
sustancial es atacable con bloqueo por fingerprint (sin sufijos societarios)
y por token significativo más largo.

Estrategia (vectorizada, RAM-segura, multi-pasada):
    Pasada A — **Fingerprint** (sin sufijos societarios, sin no-alfanuméricos,
        mayúsculas, sin acentos). Reutiliza el mismo concepto que
        ``AdvancedValueSelector._get_fingerprint`` (consistencia con la
        lógica de consenso del golden record). Dos nombres con fingerprint
        idéntico se emiten como par.
    Pasada B — **Token significativo más largo** (≥ 4 chars, sin stopwords
        del dominio: ``DE LA Y EL EN COLOMBIA SA SAS LTDA``). Captura
        ``COLANTA`` ↔ ``cooperativa colanta`` y ``CROWN COLOMBIA`` ↔
        ``PRODENVASES CROWN``.

Output: set de tuplas ``(idx_0, idx_1)`` con ``idx_0 < idx_1``, complementario
al bloqueo LSH por n-gramas y al bloqueo por NIT.

Author: Claude (auditor)  Date: 2026-05-22  Version: 2.6.0
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# Stopwords del dominio: tokens sin valor discriminante en razones sociales
# colombianas. La lista es conservadora y NO incluye nombres reales de
# empresas (p. ej. "COLOMBIA" se trata como token, no como stopword, porque
# muchas empresas tienen nombres que solo contienen "X COLOMBIA").
_DEFAULT_DOMAIN_STOPWORDS: frozenset[str] = frozenset(
    {
        "DE",
        "LA",
        "EL",
        "LOS",
        "LAS",
        "Y",
        "EN",
        "DEL",
        "AL",
        "POR",
        "PARA",
        "CON",
        "SIN",
        # Sufijos / formas societarias ya las quita el fingerprint, pero las
        # repetimos aquí para el bloqueo por token (defensa en profundidad).
        "SA",
        "SAS",
        "LTDA",
        "LIMITADA",
        "EU",
        "CIA",
        "INC",
        "LLC",
        "CORP",
        "SRL",
        "SCA",
        "SENC",
    }
)


# Patrón de sufijos societarios (mismo que usa el AdvancedValueSelector,
# replicado aquí para no acoplarnos al ciclo de imports del módulo golden/).
_SOCIETARY_PATTERNS_REGEX = re.compile(
    r"\b(S\.?A\.?S\.?|LTDA\.?|S\.?A\.?|LIMITADA|E\.?U\.?|S\.?EN\.?C\.?|"
    r"S\.?C\.?A\.?|CIA|INC|LLC|CORP|S\.?R\.?L\.?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NameBlockingConfig:
    """Parámetros del bloqueo multi-pasada por nombre.

    Attributes:
        enable_fingerprint: Activa la pasada A (fingerprint sin sufijos).
            Default True.
        enable_significant_token: Activa la pasada B (token largo no-stopword).
            Default True.
        min_token_length: Longitud mínima del token significativo para
            considerarlo como clave de bucket. Default 4 (más corto produce
            buckets demasiado grandes, p. ej. "PWC" tiene 3 chars y aún así
            queremos capturarlo — ver test).
        max_bucket_size: Tamaño máximo de bucket emitido. Cota dura contra
            explosión cuadrática. Default 200.
        domain_stopwords: Stopwords del dominio (no producen buckets por sí
            mismas en la pasada B). Default: lista conservadora colombiana.
    """

    enable_fingerprint: bool = True
    enable_significant_token: bool = True
    min_token_length: int = 4
    max_bucket_size: int = 200
    domain_stopwords: frozenset[str] = field(default_factory=lambda: _DEFAULT_DOMAIN_STOPWORDS)

    def __post_init__(self) -> None:
        if self.max_bucket_size < 2:
            raise ValueError(f"max_bucket_size={self.max_bucket_size} debe ser ≥ 2")
        if self.min_token_length < 2:
            raise ValueError(f"min_token_length={self.min_token_length} debe ser ≥ 2")


def _pairs_from_indices(indices: list[int]) -> Iterable[tuple[int, int]]:
    """Genera pares (i, j) con i < j desde una lista de índices."""
    return combinations(sorted(indices), 2)


def _normalize_to_ascii_upper(s: object) -> str:
    """Normaliza un nombre a ASCII mayúsculas, sin acentos.

    No vectorizable nativamente porque ``unicodedata.normalize`` opera por
    string. Se aplica con ``Series.map`` (única parte no vectorizada del
    módulo).
    """
    if not isinstance(s, str):
        return ""
    return unicodedata.normalize("NFKD", s.upper()).encode("ascii", "ignore").decode("utf-8")


def _fingerprint_series(names: pd.Series) -> pd.Series:
    """Calcula el fingerprint vectorizado de una serie de nombres.

    Procedimiento (idéntico a ``AdvancedValueSelector._get_fingerprint`` pero
    aplicado por Series para todo el lote):

    1. Normalizar a ASCII mayúsculas (sin acentos).
    2. Eliminar sufijos societarios (S.A., S.A.S., LTDA., etc.).
    3. Eliminar todo carácter no alfanumérico.

    Args:
        names: Serie con los nombres originales.

    Returns:
        Serie del mismo índice con los fingerprints.
    """
    norm = names.map(_normalize_to_ascii_upper)
    norm = norm.str.replace(_SOCIETARY_PATTERNS_REGEX, "", regex=True)
    norm = norm.str.replace(r"[^A-Z0-9]", "", regex=True)
    return norm


def _significant_token_series(
    names: pd.Series,
    min_length: int,
    stopwords: frozenset[str],
) -> pd.Series:
    """Devuelve el token significativo más largo de cada nombre.

    "Significativo" = ASCII mayúsculas, longitud ≥ ``min_length`` y no es
    stopword del dominio. Se elige el más LARGO (mayor poder discriminante);
    en empate de longitud, el primero alfabéticamente (determinista).

    Si ningún token cumple, devuelve "" (no entra al bloqueo).

    Args:
        names: Serie con los nombres.
        min_length: Longitud mínima del token para considerarlo.
        stopwords: Conjunto de tokens a ignorar.

    Returns:
        Serie del mismo índice con el token significativo (o "").
    """
    norm = names.map(_normalize_to_ascii_upper)
    # Mantener solo letras y números, separar por cualquier no-alfanumérico.
    # Limpiamos sufijos societarios antes de tokenizar.
    norm = norm.str.replace(_SOCIETARY_PATTERNS_REGEX, " ", regex=True)

    def _pick_token(s: str) -> str:
        if not s:
            return ""
        tokens = [t for t in re.split(r"[^A-Z0-9]+", s) if t]
        # Filtrar por longitud y stopwords.
        candidates = [t for t in tokens if len(t) >= min_length and t not in stopwords]
        if not candidates:
            return ""
        # Elegir el más largo; desempate alfabético ascendente (determinista).
        candidates.sort(key=lambda t: (-len(t), t))
        return candidates[0]

    return norm.map(_pick_token)


def block_by_name_multipass(
    df: pd.DataFrame,
    name_column: str = "RAZON_SOCIAL",
    config: NameBlockingConfig | None = None,
) -> set[tuple[int, int]]:
    """Genera pares candidatos por bloqueo multi-pasada de nombre.

    El bloqueo es complementario al LSH por n-gramas y al bloqueo por NIT:
    produce pares que el LSH pierde por sufijos societarios distintos,
    tokens cortos compartidos o reordenamientos.

    Args:
        df: DataFrame con índice posicional 0..n-1 y la columna ``name_column``.
        name_column: Nombre de la columna con la razón social.
        config: Configuración del bloqueo. Si es None, se usa el default.

    Returns:
        Conjunto de pares ``(idx_0, idx_1)`` con ``idx_0 < idx_1``.

    Raises:
        ValueError: Si la columna no existe.
    """
    if config is None:
        config = NameBlockingConfig()

    if df.empty:
        return set()
    if name_column not in df.columns:
        raise ValueError(
            f"Columna '{name_column}' no encontrada en DataFrame "
            f"(columnas disponibles: {list(df.columns)})"
        )

    n_total = len(df)
    positional_idx = np.arange(n_total)

    pairs: set[tuple[int, int]] = set()

    # ── PASADA A: FINGERPRINT (sin sufijos societarios) ─────────────────
    if config.enable_fingerprint:
        fp = _fingerprint_series(df[name_column])
        valid = (fp != "") & (fp.str.len() >= config.min_token_length)
        if int(valid.sum()) > 0:
            fp_valid = fp[valid]
            idx_valid = positional_idx[valid.to_numpy()]
            fp_series = pd.Series(fp_valid.to_numpy(), index=idx_valid)
            groups = fp_series.groupby(fp_series.values).groups
            n_buckets, n_skipped = 0, 0
            pairs_before = len(pairs)
            for fp_val, idx_array in groups.items():
                sz = len(idx_array)
                if sz < 2:
                    continue
                if sz > config.max_bucket_size:
                    n_skipped += 1
                    logger.debug(
                        f"[name_blocking/fingerprint] Bucket '{fp_val}' descartado: "
                        f"{sz} > max_bucket_size={config.max_bucket_size}"
                    )
                    continue
                n_buckets += 1
                pairs.update(_pairs_from_indices([int(x) for x in idx_array]))
            pairs_added = len(pairs) - pairs_before
            logger.info(
                f"[name_blocking/fingerprint] {n_buckets:,} buckets emitidos "
                f"({n_skipped} descartados) → +{pairs_added:,} pares"
            )

    # ── PASADA B: TOKEN SIGNIFICATIVO MÁS LARGO ─────────────────────────
    if config.enable_significant_token:
        tok = _significant_token_series(
            df[name_column], config.min_token_length, config.domain_stopwords
        )
        valid = tok != ""
        if int(valid.sum()) > 0:
            tok_valid = tok[valid]
            idx_valid = positional_idx[valid.to_numpy()]
            tok_series = pd.Series(tok_valid.to_numpy(), index=idx_valid)
            groups = tok_series.groupby(tok_series.values).groups
            n_buckets, n_skipped = 0, 0
            pairs_before = len(pairs)
            for tok_val, idx_array in groups.items():
                sz = len(idx_array)
                if sz < 2:
                    continue
                if sz > config.max_bucket_size:
                    n_skipped += 1
                    logger.debug(
                        f"[name_blocking/token] Bucket '{tok_val}' descartado: "
                        f"{sz} > max_bucket_size={config.max_bucket_size}"
                    )
                    continue
                n_buckets += 1
                pairs.update(_pairs_from_indices([int(x) for x in idx_array]))
            pairs_added = len(pairs) - pairs_before
            logger.info(
                f"[name_blocking/token] {n_buckets:,} buckets emitidos "
                f"({n_skipped} descartados) → +{pairs_added:,} pares nuevos"
            )

    logger.info(f"[name_blocking] Total pares por bloqueo de nombre: {len(pairs):,}")
    return pairs
