"""engine.lsh.prescreen — Pre-screening por NIT antes de LSH.

Sprint 0.8.0 — Pre-screening LSH (objetivo 2-3× más rápido).

Motivación (basada en first-run del 27-05-2026):
    Sobre 1,965,732 registros reales, el LSH tardó 36m 56s (70% del tiempo
    total del pipeline). De 5,414,468 candidatos LSH, solo 43,636 (0.8%)
    pasaron los filtros de scoring. La mayoría de fusiones reales (38,545
    de 1.97M) tienen NIT idéntico — un filtro O(n) por NIT_BASE habría
    identificado esos pares SIN necesidad de LSH.

Estrategia:
    1. NITPrescreener.partition(df) agrupa registros por NIT_BASE
    2. Registros con NIT compartido → "high_confidence_pairs" (exact match)
    3. Registros con NIT único → "residual_df" (van al LSH normal)
    4. El consumidor combina ambos resultados

Garantías:
    - Componente PURO (sin side effects, sin dependencias del Orchestrator)
    - Opt-in (no se activa por default; preserva retrocompat 100%)
    - Determinístico (mismo input → mismo output)
    - O(n) en tiempo y O(n) en memoria

Uso típico:
    from record_linkage.engine.lsh.prescreen import NITPrescreener

    prescreener = NITPrescreener(min_group_size=2)
    result = prescreener.partition(df)

    print(f"Pares NIT-exact: {len(result.exact_match_pairs):,}")
    print(f"Registros residuales para LSH: {len(result.residual_df):,}")
    print(f"Reducción de input LSH: {result.reduction_pct:.1%}")

    # Combinar con LSH normal
    lsh_pairs = lsh_engine.find_candidates(result.residual_df)
    all_pairs = result.exact_match_pairs | lsh_pairs
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ...utils.logger import CustomLogger


@dataclass
class PrescreenResult:
    """Resultado del pre-screening por NIT.

    Attributes:
        exact_match_pairs: set de tuples (i, j) con i < j, índices del df
            original. Estos pares comparten NIT_BASE válido y van DIRECTO
            a scoring sin pasar por LSH.
        residual_df: subconjunto del df de entrada con registros que NO
            tienen NIT match — estos sí necesitan LSH para encontrar pares.
        n_input: tamaño del df original.
        n_residual: tamaño del residual_df.
        n_exact_pairs: cantidad de pares NIT-exact identificados.
        n_groups: cantidad de grupos NIT con ≥2 miembros.
        excluded_nits_empty: cantidad de registros con NIT vacío/inválido
            que fueron asignados a residual (no se pueden pre-screen).
        elapsed_s: tiempo de procesamiento.
    """

    exact_match_pairs: set[tuple[int, int]]
    residual_df: pd.DataFrame
    n_input: int
    n_residual: int
    n_exact_pairs: int
    n_groups: int
    excluded_nits_empty: int
    elapsed_s: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def reduction_pct(self) -> float:
        """% de registros que NO necesitan ir al LSH."""
        if self.n_input == 0:
            return 0.0
        return 1.0 - (self.n_residual / self.n_input)

    @property
    def speedup_estimate_lsh(self) -> float:
        """Estimación de speedup del LSH (LSH es O(n·log n) en pares).

        Reducir n a n_residual reduce el costo aproximadamente
        cuadráticamente para la fase de búsqueda de candidatos.
        """
        if self.n_residual == 0:
            return float("inf")
        # LSH es O(n × bandas) para indexación + O(n × bandas) para query
        # Pero los pares candidatos crecen ~cuadráticamente con n
        # Aproximación conservadora: linear
        return self.n_input / self.n_residual


class NITPrescreener:
    """Pre-screener por NIT antes de LSH.

    Identifica pares con NIT_BASE idéntico para evitar procesarlos en LSH.
    Diseño: componente puro, sin dependencias del Orchestrator.

    Args:
        nit_column: nombre de la columna de NIT a usar. Default "NIT_BASE"
            (resultado del procesamiento por NITProcessor del paquete).
        min_group_size: tamaño mínimo de grupo NIT para considerar pares.
            Default 2 (necesita ≥2 registros con mismo NIT para que haya
            un par). Para escenarios extremos, subir a 3 reduce más el
            residual pero pierde pares legítimos.
        max_group_size: si un NIT aparece en >N registros, NO generar
            todos los pares (sería explosión cuadrática). Default 50:
            grupos NIT muy grandes son sospechosos (NIT genérico, error
            de captura, persona natural usada por múltiples empresas)
            y deben ir al LSH normal para resolución más cuidadosa.
        require_valid_nit: si True (default), solo procesa registros con
            columna 'NIT_VALID' == True. Si no existe la columna, ignora
            el filtro.
        cross_source_only: si True, solo genera pares entre registros de
            FUENTES distintas (no fusión intra-fuente).
        trusted_unique_sources: set de fuentes confiables (singletons).
            Si está poblado, NO se generan pares intra-fuente para esas
            fuentes (consistente con TrustedSourceLSHEngine).
    """

    def __init__(
        self,
        nit_column: str = "NIT_BASE",
        min_group_size: int = 2,
        max_group_size: int = 50,
        require_valid_nit: bool = True,
        cross_source_only: bool = False,
        trusted_unique_sources: set[str] | None = None,
    ):
        if min_group_size < 2:
            raise ValueError(f"min_group_size debe ser ≥2: {min_group_size}")
        if max_group_size < min_group_size:
            raise ValueError(
                f"max_group_size ({max_group_size}) < min_group_size ({min_group_size})"
            )

        self.nit_column = nit_column
        self.min_group_size = min_group_size
        self.max_group_size = max_group_size
        self.require_valid_nit = require_valid_nit
        self.cross_source_only = cross_source_only
        self.trusted_unique_sources = trusted_unique_sources or set()
        self.logger = CustomLogger("NITPrescreener")

    def partition(self, df: pd.DataFrame) -> PrescreenResult:
        """Particiona df en (pares NIT-exact, residual para LSH).

        Args:
            df: DataFrame con al menos columna `nit_column`. Recomendado
                tener también 'SRC' y 'NIT_VALID' si quieres filtrar.

        Returns:
            PrescreenResult con pares exactos y residual.
        """
        t0 = time.perf_counter()
        n_input = len(df)

        if self.nit_column not in df.columns:
            self.logger.warning(
                f"Columna '{self.nit_column}' no existe — saltando prescreen. "
                f"Devolviendo df completo como residual."
            )
            return PrescreenResult(
                exact_match_pairs=set(),
                residual_df=df,
                n_input=n_input,
                n_residual=n_input,
                n_exact_pairs=0,
                n_groups=0,
                excluded_nits_empty=0,
                elapsed_s=time.perf_counter() - t0,
                metadata={"reason": "nit_column_missing"},
            )

        # ── 1. Identificar registros pre-screenable (NIT válido y no vacío) ──
        nit_series = df[self.nit_column].fillna("").astype(str)
        mask_valid_nit = nit_series.str.len() > 0

        if self.require_valid_nit and "NIT_VALID" in df.columns:
            mask_valid_nit &= df["NIT_VALID"].fillna(False).astype(bool)

        n_excluded_empty = int((~mask_valid_nit).sum())

        # ── 2. Agrupar por NIT_BASE ──────────────────────────────────────
        df_valid = df.loc[mask_valid_nit].copy()
        df_valid["_orig_idx"] = df_valid.index

        # Agrupar por NIT y contar tamaños
        groups = df_valid.groupby(nit_series.loc[mask_valid_nit], sort=False)
        group_sizes = groups.size()

        # Solo grupos válidos: ≥min_group_size y ≤max_group_size
        valid_nits = group_sizes[
            (group_sizes >= self.min_group_size) & (group_sizes <= self.max_group_size)
        ].index

        n_groups_used = len(valid_nits)

        # ── 3. Generar pares para grupos válidos ─────────────────────────
        exact_pairs: set[tuple[int, int]] = set()
        # Índices de registros que ENTRARON a un grupo (van fuera del residual)
        indices_consumed: set[int] = set()

        from itertools import combinations

        for nit in valid_nits:
            group = df_valid[nit_series.loc[mask_valid_nit] == nit]
            indices = group["_orig_idx"].values

            # Filtrar por cross_source_only si aplica
            if self.cross_source_only and "SRC" in group.columns:
                srcs = group["SRC"].values
                # Generar solo pares (i,j) donde srcs[pos_i] != srcs[pos_j]
                for pi, pj in combinations(range(len(indices)), 2):
                    if srcs[pi] != srcs[pj]:
                        i, j = sorted([int(indices[pi]), int(indices[pj])])
                        exact_pairs.add((i, j))
                        indices_consumed.update([i, j])
            elif self.trusted_unique_sources and "SRC" in group.columns:
                # NO generar pares intra-fuente para trusted sources
                srcs = group["SRC"].values
                for pi, pj in combinations(range(len(indices)), 2):
                    src_i, src_j = srcs[pi], srcs[pj]
                    # Saltar si AMBOS son la misma fuente trusted
                    if src_i == src_j and src_i in self.trusted_unique_sources:
                        continue
                    i, j = sorted([int(indices[pi]), int(indices[pj])])
                    exact_pairs.add((i, j))
                    indices_consumed.update([i, j])
            else:
                # Todos los pares
                for i, j in combinations(indices, 2):
                    pair = (int(min(i, j)), int(max(i, j)))
                    exact_pairs.add(pair)
                    indices_consumed.update(pair)

        # ── 4. Construir residual_df ─────────────────────────────────────
        # Residual = todos los registros que NO entraron en exact_pairs
        # (NITs únicos, NITs vacíos/inválidos, NITs en grupos demasiado grandes)
        residual_mask = ~df.index.isin(indices_consumed)
        residual_df = df.loc[residual_mask].copy()

        elapsed = time.perf_counter() - t0

        self.logger.info(
            f"🎯 Pre-screen completado en {elapsed:.1f}s: "
            f"{len(exact_pairs):,} pares NIT-exact "
            f"({n_groups_used:,} grupos), "
            f"residual {len(residual_df):,}/{n_input:,} "
            f"({(1 - len(residual_df) / max(1, n_input)) * 100:.1f}% pre-filtrado)"
        )

        # ── 5. Diagnóstico de grupos descartados ─────────────────────────
        n_oversize = int((group_sizes > self.max_group_size).sum())
        if n_oversize > 0:
            oversize_nits = group_sizes[group_sizes > self.max_group_size]
            self.logger.warning(
                f"⚠️  {n_oversize} NITs descartados por exceder max_group_size={self.max_group_size}. "
                f"Top 3 más grandes: {oversize_nits.nlargest(3).to_dict()}. "
                f"Estos registros van al LSH (resolución cuidadosa)."
            )

        return PrescreenResult(
            exact_match_pairs=exact_pairs,
            residual_df=residual_df,
            n_input=n_input,
            n_residual=len(residual_df),
            n_exact_pairs=len(exact_pairs),
            n_groups=n_groups_used,
            excluded_nits_empty=n_excluded_empty,
            elapsed_s=elapsed,
            metadata={
                "n_oversize_groups": n_oversize,
                "max_group_size_config": self.max_group_size,
                "min_group_size_config": self.min_group_size,
                "cross_source_only": self.cross_source_only,
                "trusted_unique_sources": sorted(self.trusted_unique_sources),
            },
        )


def prescreen_and_split(
    df: pd.DataFrame,
    **kwargs,
) -> PrescreenResult:
    """Atajo funcional: crea NITPrescreener y corre partition().

    Útil para usar dentro de notebooks sin importar la clase.
    """
    prescreener = NITPrescreener(**kwargs)
    return prescreener.partition(df)
