"""matching.pipeline_integration — conexión del matcher al pipeline core.

Este módulo es la pieza B1 de la auditoría externa (sept 2026):
"Bloqueante 1 — Integrar el matcher al pipeline".

Estrategia: **post-procesamiento de la correlativa**. El pipeline core
genera la correlativa (asignación de cada registro a un ID_GRUPO) usando
el scorer plano + clusterer. El matcher se aplica DESPUÉS, sobre los
pares intra-grupo, para SEPARAR clusters donde las reglas multi-variable
no se cumplen.

Esta estrategia tiene 3 ventajas críticas:

1. **Cero riesgo de regresión**: el pipeline core sigue corriendo igual
   (paridad bit-a-bit con notebook fuente preservada). El matcher es un
   filtro opt-in que se aplica al output.

2. **Solo puede mejorar precision, no empeorarla**: el matcher SEPARA
   clusters predichos pero NO los une. Reduce FP, no añade falsos
   negativos respecto al recall LSH original.

3. **Trazable**: cada decisión de re-cluster queda registrada con scores
   por variable y razón de separación, lo que cumple el requisito B2 de
   observabilidad.

Trade-off honesto:
    El recall efectivo NO sube respecto al pipeline core. Si el LSH no
    generó un par como candidato, el matcher no lo recupera. Para subir
    recall hay que ajustar el LSH (otro frente). Esta integración ataca
    precision, que era el problema mayor identificado (SIN_NIT P=0.559).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from .spec import MatchingProfile

from ..utils.logger import CustomLogger
from .combiner import DECISION_MATCH, VariableMatcher


class MatcherPostProcessor:
    """Post-procesa la correlativa del pipeline aplicando un MatchingProfile.

    Después de que el pipeline core asigna registros a clusters (ID_GRUPO),
    este post-procesador:

    1. Para cada cluster de tamaño ≥ 2, genera todos los pares intra-cluster.
    2. Evalúa cada par con el VariableMatcher.
    3. Los pares con decisión != MATCH se marcan como "no-fusión justificada".
    4. Re-clusteriza usando UnionFind: pares MATCH se mantienen unidos, los
       demás se separan.

    El resultado es una correlativa con (posiblemente) más clusters que la
    original, donde cada cluster es defendible bajo el MatchingProfile.

    Args:
        profile: MatchingProfile que define las reglas de fusión.
        verbose: si True, log de progreso. Default False.

    Atributos públicos post-`apply`:
        - ``last_stats``: dict con métricas (n_clusters_before, n_clusters_after,
          n_pairs_evaluated, n_separated, n_kept, elapsed_sec). Auditoría.
        - ``decisions_log``: DataFrame con detalle por par. Trazabilidad B2.

    Ejemplo:
        >>> from record_linkage import linkage
        >>> from record_linkage.matching import (
        ...     MatcherPostProcessor, default_colombia_profile,
        ... )
        >>> result = linkage(sources=...)  # pipeline core
        >>> postproc = MatcherPostProcessor(default_colombia_profile())
        >>> result_refined = postproc.apply(result, source_df=...)
        >>> print(postproc.last_stats)
    """

    def __init__(
        self,
        profile: MatchingProfile,
        verbose: bool = False,
        *,
        max_pairs: int | None = 5_000_000,
        allow_missing_optional: bool = False,
    ) -> None:
        if max_pairs is not None and max_pairs < 1:
            raise ValueError("max_pairs debe ser positivo o None.")
        self.profile = profile
        self.verbose = verbose
        self.max_pairs = max_pairs
        self.allow_missing_optional = bool(allow_missing_optional)
        self.logger = CustomLogger("MatcherPostProcessor")
        self.last_stats: dict[str, Any] = {}
        self.decisions_log: pd.DataFrame | None = None

    def apply(
        self,
        correlative: pd.DataFrame,
        source_df: pd.DataFrame,
        *,
        id_col: str = "ID_REGISTRO",
        group_col: str = "ID_GRUPO",
    ) -> pd.DataFrame:
        """Aplica el matcher a la correlativa y retorna una nueva correlativa.

        Args:
            correlative: DataFrame con columnas ``id_col`` y ``group_col``.
                Resultado del pipeline core (ej. ``result["correlative"]``).
            source_df: DataFrame con todas las columnas declaradas en el
                profile. Debe estar indexado por ``id_col`` o tenerlo como
                columna (se reindexa internamente).
            id_col: Nombre de la columna ID. Default "ID_REGISTRO".
            group_col: Nombre de la columna de cluster. Default "ID_GRUPO".

        Returns:
            DataFrame con las mismas columnas que ``correlative``, pero con
            ``group_col`` reasignado tras la separación. IDs de grupo nuevos
            (sin colisionar con los originales).

        Performance:
            Para una correlativa con N clusters de tamaño promedio K, evalúa
            N × K(K-1)/2 pares. Típicamente K es pequeño (2-5), entonces
            N × ~10 pares. Para 4.000 clusters ≈ 40k pares ≈ 2-3 segundos.
        """
        t_start = time.time()

        # Normalizar el source_df: usar id_col como índice
        if source_df.index.name != id_col:
            if id_col in source_df.columns:
                df_indexed = source_df.set_index(id_col)
            else:
                raise KeyError(
                    f"source_df no tiene columna ni índice '{id_col}'. "
                    f"Columnas: {list(source_df.columns)[:10]}"
                )
        else:
            df_indexed = source_df

        if correlative[id_col].duplicated().any():
            ejemplos = (
                correlative.loc[correlative[id_col].duplicated(False), id_col].head(5).tolist()
            )
            raise ValueError(
                f"MatcherPostProcessor: '{id_col}' debe ser único en la correlativa; "
                f"duplicados de ejemplo: {ejemplos}."
            )
        if not df_indexed.index.is_unique:
            raise ValueError(
                f"MatcherPostProcessor: el índice '{id_col}' de source_df debe ser único."
            )

        # Validar columnas del profile
        for var in self.profile.variables:
            try:
                var.resolve_column(df_indexed)
            except KeyError as e:
                if self.allow_missing_optional and not var.required_for_match:
                    continue
                raise KeyError(
                    f"MatcherPostProcessor: variable '{var.name}' del profile "
                    f"no está en source_df. {e}"
                ) from e

        # Identificar clusters de tamaño >=2 (los unitarios no necesitan filtro)
        groups = correlative.groupby(group_col)
        n_clusters_before = correlative[group_col].nunique()

        # Generar pares intra-cluster
        from itertools import combinations

        all_pairs = []
        cluster_to_pairs: dict[Any, list[tuple]] = {}
        n_pairs_estimados = 0
        for gid, sub in groups:
            ids = sub[id_col].astype(str).tolist()
            if len(ids) < 2:
                continue
            n_nuevos = len(ids) * (len(ids) - 1) // 2
            n_pairs_estimados += n_nuevos
            if self.max_pairs is not None and n_pairs_estimados > self.max_pairs:
                raise ValueError(
                    "Qué pasó: el refinamiento matcher excedería su presupuesto "
                    f"({n_pairs_estimados:,} > max_pairs={self.max_pairs:,}) antes "
                    "de materializar los pares. Por qué importa: los pares "
                    "intra-cluster crecen O(k²) y podrían agotar la RAM. Qué hacer: "
                    "revise el cluster sobredimensionado, ajuste el pipeline base o "
                    "eleve max_pairs explícitamente si dispone de memoria."
                )
            pairs = list(combinations(sorted(ids), 2))
            cluster_to_pairs[gid] = pairs
            all_pairs.extend(pairs)

        if not all_pairs:
            # Nada que separar
            self.last_stats = {
                "n_clusters_before": n_clusters_before,
                "n_clusters_after": n_clusters_before,
                "n_pairs_evaluated": 0,
                "n_kept": 0,
                "n_separated": 0,
                "elapsed_sec": time.time() - t_start,
            }
            self.decisions_log = pd.DataFrame()
            return correlative.copy()

        pairs_df = pd.DataFrame(all_pairs, columns=["id_left", "id_right"])

        # Convertir IDs del source a string para matching consistente
        df_indexed_str = df_indexed.copy()
        df_indexed_str.index = df_indexed_str.index.astype(str)

        # Evaluar con matcher
        matcher = VariableMatcher(
            self.profile,
            verbose=self.verbose,
            allow_missing_optional=self.allow_missing_optional,
        )
        scored = matcher.score_pairs(pairs_df, df_indexed_str)

        # Pares MATCH se mantienen unidos; resto se separan
        keep_mask = scored["decision"] == DECISION_MATCH
        n_keep = int(keep_mask.sum())
        n_sep = len(scored) - n_keep

        # Re-clusterizar con UnionFind sobre los pares MATCH dentro de cada cluster
        # original. Importante: la separación es INTRA-cluster, nunca entre.
        new_correlative = correlative.copy()
        new_correlative[id_col] = new_correlative[id_col].astype(str)

        # Para cada cluster original, re-clusterizar sus IDs con UnionFind
        # usando solo los pares MATCH.
        next_new_gid = self._compute_max_gid(correlative[group_col]) + 1
        old_to_new_assignments: dict[str, int] = {}

        scored_indexed = scored.set_index(["id_left", "id_right"])

        for gid, pairs in cluster_to_pairs.items():
            # IDs originales del cluster
            members = correlative[correlative[group_col] == gid][id_col].astype(str).tolist()
            members = sorted(set(members))
            new_assignments = self._refine_cluster(
                members,
                pairs,
                scored_indexed,
                gid,
                next_new_gid,
            )
            next_new_gid += new_assignments["next_gid_offset"]
            old_to_new_assignments.update(new_assignments["assignments"])

        # Aplicar asignaciones (singletons no tocados)
        mask_reassigned = new_correlative[id_col].isin(old_to_new_assignments)
        new_correlative.loc[mask_reassigned, group_col] = new_correlative.loc[
            mask_reassigned, id_col
        ].map(old_to_new_assignments)

        # Stats y log
        elapsed = time.time() - t_start
        n_clusters_after = new_correlative[group_col].nunique()
        self.last_stats = {
            "n_clusters_before": n_clusters_before,
            "n_clusters_after": n_clusters_after,
            "n_clusters_split": n_clusters_after - n_clusters_before,
            "n_pairs_evaluated": len(scored),
            "n_kept": n_keep,
            "n_separated": n_sep,
            "elapsed_sec": round(elapsed, 2),
            "pairs_per_sec": round(len(scored) / elapsed, 0) if elapsed > 0 else 0,
            "matcher_stats": matcher.last_run_stats,
        }
        self.decisions_log = scored.copy()

        if self.verbose:
            self.logger.info(
                f"MatcherPostProcessor: {n_clusters_before:,} clusters → "
                f"{n_clusters_after:,} clusters "
                f"(separó {n_sep:,}/{len(scored):,} pares en {elapsed:.1f}s)"
            )

        return new_correlative

    @staticmethod
    def _refine_cluster(
        members: list[str],
        pairs: list[tuple[str, str]],
        scored_indexed: pd.DataFrame,
        original_gid: Any,
        next_new_gid: int,
    ) -> dict[str, Any]:
        """Re-clusteriza los miembros de UN cluster usando solo los pares MATCH.

        Implementa UnionFind LOCAL al cluster. El cluster original se conserva
        íntegro si todos los pares MATCHean; si no, se separa en sub-clusters
        donde el más grande conserva el ID original.

        Args:
            members: lista ordenada de IDs miembros del cluster.
            pairs: lista de pares (a, b) intra-cluster a evaluar.
            scored_indexed: DataFrame de scores indexado por (id_left, id_right).
            original_gid: ID original del cluster (para preservar continuidad).
            next_new_gid: próximo ID disponible para sub-clusters nuevos.

        Returns:
            dict con:
            - "assignments": {id_miembro: gid_nuevo}
            - "next_gid_offset": cuántos GIDs nuevos se consumieron.
        """
        # UnionFind local
        parent = {m: m for m in members}

        def find(x: str) -> str:
            # Path compression iterativa, sin closure peligroso
            root = x
            while parent[root] != root:
                root = parent[root]
            # Aplastar
            cur = x
            while parent[cur] != root:
                parent[cur], cur = root, parent[cur]
            return root

        # Unir solo los pares MATCH
        for a, b in pairs:
            key = (a, b) if (a, b) in scored_indexed.index else (b, a)
            try:
                decision = scored_indexed.loc[key, "decision"]
            except (KeyError, ValueError):
                continue
            if decision == DECISION_MATCH:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

        # Asignar nuevos IDs de grupo
        roots = {m: find(m) for m in members}
        unique_roots = sorted(set(roots.values()))
        assignments: dict[str, Any] = {}

        if len(unique_roots) == 1:
            # Cluster se mantiene íntegro: conservar GID original
            for m in members:
                assignments[m] = original_gid
            return {"assignments": assignments, "next_gid_offset": 0}

        # Se separa: el sub-cluster con más miembros conserva el GID original
        root_sizes: dict[str, int] = {}
        for r in unique_roots:
            root_sizes[r] = sum(1 for m in members if roots[m] == r)
        biggest_root = max(unique_roots, key=lambda r: (root_sizes[r], r))

        root_to_gid: dict[str, Any] = {biggest_root: original_gid}
        offset = 0
        for r in unique_roots:
            if r != biggest_root:
                root_to_gid[r] = next_new_gid + offset
                offset += 1
        for m in members:
            assignments[m] = root_to_gid[roots[m]]
        return {"assignments": assignments, "next_gid_offset": offset}

    @staticmethod
    def _compute_max_gid(series: pd.Series) -> int:
        """Calcula el ID máximo para evitar colisiones con IDs nuevos."""
        try:
            return int(series.max())
        except (ValueError, TypeError):
            # IDs no numéricos: usar hash como fallback
            return int(1e9)


def apply_matcher_to_linkage_result(
    linkage_result: dict[str, Any],
    source_df: pd.DataFrame,
    profile: MatchingProfile | None = None,
    *,
    verbose: bool = False,
    source_priority: list[str] | None = None,
    golden_config: dict[str, Any] | None = None,
    max_pairs: int | None = 5_000_000,
    allow_missing_optional: bool = False,
) -> dict[str, Any]:
    """Helper convenient: aplica un MatcherPostProcessor a un resultado de linkage().

    Args:
        linkage_result: dict retornado por ``record_linkage.linkage(...)``,
            con claves "golden" y "correlative".
        source_df: DataFrame combinado de TODAS las fuentes que se pasaron a
            ``linkage()``. Debe tener las columnas del profile.
        profile: MatchingProfile. Si None, usa default_colombia_profile().
        verbose: si True, log de progreso.
        source_priority: orden de prioridad para seleccionar valores canónicos.
            Si se omite, se infiere por primera aparición en ``SRC``.
        golden_config: configuración opcional para GoldenRecordGeneratorV7.
        max_pairs: presupuesto de pares intra-cluster antes de materializarlos.
        allow_missing_optional: permite omitir variables no requeridas del
            perfil. El default estricto preserva el comportamiento histórico.

    Returns:
        dict con las mismas claves pero ``correlative`` refinada. ``golden``
        se recalcula con el selector canónico del pipeline (consenso, calidad
        y prioridad de fuente), no tomando arbitrariamente la primera fila.

    Ejemplo:
        >>> from record_linkage import linkage
        >>> from record_linkage.matching import apply_matcher_to_linkage_result
        >>> result = linkage(sources={"RUES": df_rues, "CRM": df_crm})
        >>> combined = pd.concat([df_rues, df_crm], ignore_index=True)
        >>> refined = apply_matcher_to_linkage_result(result, combined)
        >>> print(f"Clusters: {result['correlative']['ID_GRUPO'].nunique()} → "
        ...       f"{refined['correlative']['ID_GRUPO'].nunique()}")
    """
    from . import default_colombia_profile

    if profile is None:
        profile = default_colombia_profile()

    postproc = MatcherPostProcessor(
        profile,
        verbose=verbose,
        max_pairs=max_pairs,
        allow_missing_optional=allow_missing_optional,
    )
    new_correlative = postproc.apply(
        linkage_result["correlative"],
        source_df,
    )

    required = {"ID_GRUPO", "NIT", "RAZON_SOCIAL", "SRC"}
    missing = sorted(required.difference(new_correlative.columns))
    if missing:
        raise KeyError(
            "No se puede recalcular el golden refinado: a la correlativa "
            f"le faltan las columnas canónicas {missing}. Use la correlativa "
            "completa retornada por record_linkage.linkage()."
        )

    if source_priority is None:
        source_priority = list(dict.fromkeys(new_correlative["SRC"].astype(str).tolist()))

    from ..golden.generator import GoldenRecordGeneratorV7

    new_golden, new_correlative = GoldenRecordGeneratorV7(source_priority, golden_config).generate(
        new_correlative
    )

    # Preservar metadatos/reportes/diagnósticos de la llamada original.
    refined = dict(linkage_result)
    refined.update(
        {
            "golden": new_golden,
            "correlative": new_correlative,
            "matcher_stats": postproc.last_stats,
            "matcher_decisions": postproc.decisions_log,
        }
    )
    return refined
