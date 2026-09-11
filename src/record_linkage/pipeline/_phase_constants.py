"""Constantes adicionales extraídas del notebook fuente."""

from __future__ import annotations

from ..reporting.strategies import Phase

# ────────────────────────────────────────────────────────────
# PHASES_ORDER  (origen: notebook celda [192])
# ────────────────────────────────────────────────────────────
PHASES_ORDER: list[Phase] = list(Phase)

# ────────────────────────────────────────────────────────────
# PHASE_PARAMS  (origen: notebook celda [192])
# ────────────────────────────────────────────────────────────
PHASE_PARAMS: dict[Phase, list[str]] = {
    Phase.L1_PREP: ["cleaning_mode", "remove_top_words", "min_nit_length"],
    Phase.L2_LSH_CANDIDATES: [
        "lsh_permutations",
        "lsh_ngram",
        "lsh_threshold",
        "cross_source_only",
    ],
    Phase.L3_SCORING: ["score_threshold", "min_name_similarity", "max_nit_distance", "weights"],
    Phase.L4_CLUSTERING: ["score_threshold"],
    Phase.L5_GOLDEN: ["source_quality_weights"],
    Phase.L6_REPORTING: [],  # Sin parámetros propios, siempre se regenera
}

# ────────────────────────────────────────────────────────────
# PHASE_TIMES  (origen: notebook celda [192])
# ────────────────────────────────────────────────────────────
PHASE_TIMES: dict[Phase, int] = {
    Phase.L1_PREP: 5,
    Phase.L2_LSH_CANDIDATES: 400,  # Fase más larga (MinHash + LSH + Candidatos)
    Phase.L3_SCORING: 150,
    Phase.L4_CLUSTERING: 2,
    Phase.L5_GOLDEN: 30,
    Phase.L6_REPORTING: 10,
}
