"""matching.combiner — aplica un MatchingProfile a pares candidatos.

Esta clase es el "motor" del módulo matching. Recibe pares candidatos
(ya generados por blocking LSH del paquete) y un ``MatchingProfile``, y
retorna decisiones de fusión con trazabilidad completa por variable.

Diseño de performance:

- Para N pares candidatos y V variables, el cómputo es O(N×V) llamadas
  a comparadores vectorizados. Cada comparador procesa el batch entero
  con numpy/rapidfuzz en C++. **Sin bucles Python sobre pares.**
- Las columnas se leen una sola vez del DataFrame, se materializan a
  numpy, y se reusan en todos los comparadores.
- Memoria: O(N × (V + 5)) floats. Para N=1M pares y V=10 variables ≈
  120 MB en columnas float64. Manejable.

Decisión de fusión (orden de evaluación):

1. **Veto**: si alguna variable con ``vetoes_mismatch=True`` tiene
   similitud ≤ ``veto_threshold`` → decisión = ``VETO``.
2. **Required**: si alguna variable con ``required_for_match=True``
   no es válida en ambos lados → decisión = ``MISSING_REQUIRED``.
3. **Below K**: si ``concordances < min_concordances_*`` → ``BELOW_K``.
4. **Below threshold**: si ``score < score_threshold`` → ``BELOW_T``.
5. **Match**: pasa todos los filtros.

Este orden NO es arbitrario: los vetos son los más baratos de evaluar
(bool check) y los más informativos para FP. Aplicarlos primero ahorra
trabajo y aumenta precisión.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd

from ..utils.logger import CustomLogger
from .spec import MatchingProfile

# Constantes de decisión, públicas para que el usuario filtre.
DECISION_MATCH = "MATCH"
DECISION_VETO = "VETO"
DECISION_BELOW_K = "BELOW_K"
DECISION_BELOW_T = "BELOW_T"
DECISION_MISSING_REQUIRED = "MISSING_REQUIRED"


class VariableMatcher:
    """Aplica un ``MatchingProfile`` a pares de registros.

    Uso típico::

        matcher = VariableMatcher(profile)
        scored = matcher.score_pairs(candidate_pairs_df, source_df)
        matches = scored[scored["decision"] == "MATCH"]

    El DataFrame de pares candidatos debe tener al menos columnas
    ``id_left`` e ``id_right``. El DataFrame fuente debe tener todas las
    columnas declaradas en el ``MatchingProfile`` (o sus aliases).

    Args:
        profile: ``MatchingProfile`` que define qué variables comparar
            y con qué reglas.
        verbose: Si ``True``, log de timing por variable. Default False.
        nit_column: Nombre de la columna NIT para decidir entre
            ``min_concordances_with_nit`` y ``...without_nit``. Si la
            columna NIT no está en el profile, este argumento es ignorado.
            Default ``"NIT"``.

    Atributos públicos post-``score_pairs``:
        - ``last_run_stats``: dict con métricas de la última corrida
          (tiempos por variable, número de decisiones por tipo, etc.).
          Útil para observabilidad (item B del roadmap).
    """

    def __init__(
        self,
        profile: MatchingProfile,
        verbose: bool = False,
        nit_column: str = "NIT",
        *,
        allow_missing_optional: bool = False,
    ) -> None:
        self.profile = profile
        self.verbose = verbose
        self.nit_column = nit_column
        self.allow_missing_optional = bool(allow_missing_optional)
        self.logger = CustomLogger("VariableMatcher")
        self.last_run_stats: dict[str, Any] = {}

    def score_pairs(
        self,
        pairs: pd.DataFrame,
        df: pd.DataFrame,
        *,
        return_per_variable_scores: bool = True,
    ) -> pd.DataFrame:
        """Califica y decide pares según el profile.

        Args:
            pairs: DataFrame con columnas ``id_left``, ``id_right``. Se
                asume que los IDs son índice de ``df``.
            df: DataFrame fuente con las columnas declaradas en el profile.
            return_per_variable_scores: Si True, incluye columnas
                ``score_<var>`` y ``conc_<var>`` por variable. Default True
                porque sin estas columnas la observabilidad es ciega.

        Returns:
            DataFrame con columnas:
            - ``id_left``, ``id_right`` (preservadas)
            - ``score``: score combinado ponderado
            - ``concordances``: conteo de variables concordantes
            - ``vetoed``: bool
            - ``decision``: una de las constantes DECISION_*
            - ``score_<var>``, ``conc_<var>`` por variable (si flag activo)

        Performance:
            Para 100k pares × 6 variables: ~3-5 segundos.
            Para 1M pares × 10 variables: ~45-60 segundos.
        """
        t_start = time.time()
        n = len(pairs)
        if n == 0:
            return pd.DataFrame(
                columns=["id_left", "id_right", "score", "concordances", "vetoed", "decision"]
            )

        # Validación temprana
        for var in self.profile.variables:
            try:
                var.resolve_column(df)
            except KeyError as e:
                if self.allow_missing_optional and not var.required_for_match:
                    continue
                raise KeyError(
                    f"VariableMatcher: variable '{var.name}' del profile no "
                    f"se encuentra en el DataFrame fuente. {e}"
                ) from e

        # Materializar columnas una sola vez (lookup por id)
        ids_left = pairs["id_left"].to_numpy()
        ids_right = pairs["id_right"].to_numpy()

        # Por-variable: comparar
        per_var_scores: dict[str, np.ndarray] = {}
        per_var_concordances: dict[str, np.ndarray] = {}
        per_var_valid_both: dict[str, np.ndarray] = {}
        timings: dict[str, float] = {}
        missing_optional: list[str] = []

        for var in self.profile.variables:
            t0 = time.time()
            try:
                col = var.resolve_column(df)
            except KeyError:
                if not self.allow_missing_optional or var.required_for_match:
                    raise
                per_var_scores[var.name] = np.zeros(n, dtype=np.float64)
                per_var_concordances[var.name] = np.zeros(n, dtype=bool)
                per_var_valid_both[var.name] = np.zeros(n, dtype=bool)
                timings[var.name] = time.time() - t0
                missing_optional.append(var.name)
                continue
            # Lookup vectorizado: df.loc[ids, col] funciona si los ids son índice.
            try:
                left_vals = df.loc[ids_left, col].to_numpy()
                right_vals = df.loc[ids_right, col].to_numpy()
            except KeyError:
                # IDs no son índice: usar posicional con reindex.
                left_vals = df[col].reindex(ids_left).to_numpy()
                right_vals = df[col].reindex(ids_right).to_numpy()
            scores = var.comparator.compare(left_vals, right_vals)
            per_var_scores[var.name] = scores
            # Concordancia (γ): similitud >= threshold. Con threshold en (0,1]
            # una discrepancia firmada (score < 0) nunca concuerda; la rama
            # separada por signo de 0.11.x era idéntica en ambos lados
            # (código duplicado eliminado en v0.12.0).
            per_var_concordances[var.name] = scores >= var.concordance_threshold
            # ── Validez (v0.12.0, cierre del hallazgo C5) ──────────────────
            # Hasta 0.11.x la validez se INFERÍA de `scores != 0.0`. Eso
            # confundía dos cosas distintas en comparadores NO firmados
            # (NumericoRelativo, GeoHaversine, ExactOrZero): un 0.0 ahí
            # significa "muy distinto", no "faltante" — el par quedaba fuera
            # de la masa efectiva de pesos y el score normalizado se INFLABA.
            # Ahora cada comparador del paquete expone `valid_mask(left,
            # right)` con su criterio real de "ambos lados presentes".
            # Comparadores externos sin `valid_mask` conservan el fallback
            # 0.11.x (scores != 0.0) para no romperlos.
            valid_fn = getattr(var.comparator, "valid_mask", None)
            if callable(valid_fn):
                per_var_valid_both[var.name] = np.asarray(
                    valid_fn(left_vals, right_vals), dtype=bool
                )
            else:  # comparador de terceros sin contrato de validez
                per_var_valid_both[var.name] = scores != 0.0
            timings[var.name] = time.time() - t0

        # Combinar scores con pesos.
        # Normalización por masa efectiva: si una variable es no-válida en
        # ambos lados, su score=0 pero su peso seguía contribuyendo cero al
        # numerador. Para que el score combinado siga siendo comparable al
        # score_threshold independiente de cuántas variables están
        # disponibles, dividimos por la masa de pesos efectivamente válidos.
        #
        # Esto es CRÍTICO: sin esta normalización, un par SIN_NIT con
        # nombre+ciudad concordantes (peso disponible = 0.37) nunca pasaría
        # el threshold 0.55 aunque ambas variables disponibles concuerden
        # perfectamente. Con normalización, score=1.0 cuando todo lo
        # disponible concuerda.
        weights = self.profile.normalized_weights
        combined_num = np.zeros(n, dtype=np.float64)
        effective_mass = np.zeros(n, dtype=np.float64)
        for var in self.profile.variables:
            scores = per_var_scores[var.name]
            valid = per_var_valid_both[var.name]
            w = weights[var.name]
            combined_num += w * scores
            effective_mass += w * valid
        # Evitar división por cero: pares sin NINGUNA variable válida → score=0
        with np.errstate(divide="ignore", invalid="ignore"):
            combined = np.where(effective_mass > 0, combined_num / effective_mass, 0.0)

        # Contar concordancias
        concordances = np.zeros(n, dtype=np.int32)
        for _var_name, conc in per_var_concordances.items():
            concordances += conc.astype(np.int32)

        # Detectar vetos
        vetoed = np.zeros(n, dtype=bool)
        veto_reasons: list[np.ndarray] = []
        for var in self.profile.variables:
            if not var.vetoes_mismatch:
                continue
            scores = per_var_scores[var.name]
            valid = per_var_valid_both[var.name]
            # Veto solo si ambos lados válidos Y similitud ≤ veto_threshold
            this_veto = valid & (scores <= var.veto_threshold)
            vetoed |= this_veto
            veto_reasons.append(this_veto)

        # Detectar required ausentes
        missing_required = np.zeros(n, dtype=bool)
        for var in self.profile.variables:
            if not var.required_for_match:
                continue
            valid = per_var_valid_both[var.name]
            missing_required |= ~valid

        # Decidir umbral K según presencia de NIT
        has_nit = self._has_nit_both_sides(df, ids_left, ids_right)
        k_required = np.where(
            has_nit,
            self.profile.min_concordances_with_nit,
            self.profile.min_concordances_without_nit,
        )

        # Decisiones (orden importa)
        decision = np.full(n, DECISION_MATCH, dtype=object)
        decision[concordances < k_required] = DECISION_BELOW_K
        decision[combined < self.profile.score_threshold] = DECISION_BELOW_T
        # Required missing tiene prioridad sobre los anteriores en cuanto a
        # señalización, pero veto tiene la última palabra.
        decision[missing_required] = DECISION_MISSING_REQUIRED
        decision[vetoed] = DECISION_VETO

        # Construir DataFrame resultado
        result = pd.DataFrame(
            {
                "id_left": ids_left,
                "id_right": ids_right,
                "score": combined,
                "concordances": concordances,
                "vetoed": vetoed,
                "decision": decision,
            }
        )
        if return_per_variable_scores:
            for var_name in per_var_scores:
                result[f"score_{var_name}"] = per_var_scores[var_name]
                result[f"conc_{var_name}"] = per_var_concordances[var_name]

        # Stats para observabilidad
        elapsed = time.time() - t_start
        decision_counts = pd.Series(decision).value_counts().to_dict()
        self.last_run_stats = {
            "n_pairs": n,
            "elapsed_sec": elapsed,
            "pairs_per_sec": n / elapsed if elapsed > 0 else float("inf"),
            "timings_per_variable_sec": timings,
            "decision_counts": decision_counts,
            "n_matches": int((decision == DECISION_MATCH).sum()),
            "n_vetoes": int(vetoed.sum()),
            "n_below_k": int((decision == DECISION_BELOW_K).sum()),
            "n_below_t": int((decision == DECISION_BELOW_T).sum()),
            "n_missing_req": int(missing_required.sum()),
            "n_has_nit_both": int(has_nit.sum()),
            "n_no_nit": int((~has_nit).sum()),
            "missing_optional_variables": missing_optional,
        }

        if self.verbose:
            self.logger.info(
                f"VariableMatcher: {n:,} pares en {elapsed:.2f}s ({n / elapsed:,.0f} pares/s)"
            )
            self.logger.info(f"  Decisiones: {decision_counts}")

        return result

    def _has_nit_both_sides(
        self, df: pd.DataFrame, ids_left: np.ndarray, ids_right: np.ndarray
    ) -> np.ndarray:
        """¿Ambos registros del par tienen NIT no-nulo? Vectorizado."""
        n = len(ids_left)
        if self.nit_column not in df.columns:
            # Sin columna NIT → siempre "sin NIT"
            return np.zeros(n, dtype=bool)
        try:
            left = df.loc[ids_left, self.nit_column].to_numpy()
            right = df.loc[ids_right, self.nit_column].to_numpy()
        except KeyError:
            left = df[self.nit_column].reindex(ids_left).to_numpy()
            right = df[self.nit_column].reindex(ids_right).to_numpy()
        # Detectar nulls / strings vacíos
        sl = pd.Series(left).astype(str).str.strip()
        sr = pd.Series(right).astype(str).str.strip()
        from .comparators import _INVALID_VALUES

        valid_l = ~sl.str.upper().isin(_INVALID_VALUES) & sl.notna()
        valid_r = ~sr.str.upper().isin(_INVALID_VALUES) & sr.notna()
        return (valid_l & valid_r).to_numpy()
