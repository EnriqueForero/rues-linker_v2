"""
evaluation.evaluador_verdad — agrupaciones predichas contra una verdad conocida.

Componentes:
    - class GroundTruthEvaluator  (origen: notebook celda [165])

Historia (F2.8, 6 de octubre de 2026): ``GroundTruthEvaluator`` vivía en
``evaluation/ground_truth.py`` junto a ``GroundTruthGenerator`` (2 pruebas humo,
retirado) y traía ``cross_validate``, que dependía de ``OptimizationEngine``
(deprecado desde v3.2.7, retirado). Se separó aquí lo que se usa: ``evaluate``
y ``analyze_errors``. Al moverlo se tradujeron docstrings y se quitó el
``iterrows`` de ``evaluate`` (las pruebas de ``test_evaluation_coverage.py``
fijan los resultados).

Diferencia con ``evaluation.pairwise.evaluar_pares``: aquí la verdad puede traer
etiquetas nulas o vacías; esos registros no forman pares verdaderos, pero los
pares predichos que los incluyen sí cuentan como falsos positivos. Por eso no
delega en ``evaluar_pares`` (que exige etiquetas alineadas y completas).
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd


class GroundTruthEvaluator:
    """
    Evalúa el resultado de una deduplicación contra una verdad conocida.

    Calcula métricas a nivel de pares de registros, que es la forma estándar
    de evaluar algoritmos de resolución de entidades, más conteos de grupos.
    """

    def __init__(self) -> None:
        self.last_evaluation: dict[str, float] = {}

    def evaluate(
        self,
        predicted_df: pd.DataFrame,
        ground_truth_df: pd.DataFrame,
        group_col: str = "ID_GRUPO",
        truth_col: str = "ID_GRUPO_ESPERADO",
    ) -> dict[str, float]:
        """
        Evalúa las predicciones contra la verdad.

        Args:
            predicted_df: DataFrame con los grupos predichos.
            ground_truth_df: DataFrame con la verdad (mismo índice que ``predicted_df``).
            group_col: Columna con los grupos predichos.
            truth_col: Columna con los grupos reales (``ID_GRUPO_ESPERADO``).

        Returns:
            Diccionario con métricas de evaluación.
        """
        # Dos registros son del mismo grupo verdadero si comparten truth_col.
        # Un valor nulo o vacío no forma grupo (ese registro no aporta pares
        # verdaderos). Sin iterrows: máscara + strip + groupby.
        grupos_verdad = self._grupos_verdad(ground_truth_df[truth_col])

        metrics = self._calculate_pairwise_metrics(predicted_df[group_col].to_dict(), grupos_verdad)
        metrics.update(self._calculate_clustering_metrics(predicted_df, grupos_verdad, group_col))

        self.last_evaluation = metrics
        return metrics

    @staticmethod
    def _grupos_verdad(verdad: pd.Series) -> dict[str, list[Any]]:
        """Índices de ``verdad`` por etiqueta (str sin espacios), sin nulos ni vacíos."""
        presentes = verdad.notna()
        if not presentes.any():
            return {}
        etiquetas = verdad.loc[presentes].astype(str).str.strip()
        etiquetas = etiquetas.loc[etiquetas != ""]
        return {
            str(etiqueta): list(indices)
            for etiqueta, indices in etiquetas.groupby(etiquetas, sort=False).groups.items()
        }

    def _calculate_pairwise_metrics(
        self, predicted_groups: dict[Any, Any], ground_truth_groups: dict[str, list[Any]]
    ) -> dict[str, float]:
        """
        Calcula precisión, recall y F1 a nivel de pares.
        """
        # Pares verdaderos: combinaciones dentro de cada grupo de la verdad.
        true_pairs: set[tuple[Any, Any]] = set()
        for group_indices in ground_truth_groups.values():
            if len(group_indices) > 1:
                true_pairs.update(combinations(sorted(group_indices), 2))

        # Pares predichos: combinaciones dentro de cada grupo predicho.
        pred_pairs: set[tuple[Any, Any]] = set()
        groups_by_id: dict[Any, list[Any]] = {}
        for idx, group_id in predicted_groups.items():
            groups_by_id.setdefault(group_id, []).append(idx)
        for group_indices in groups_by_id.values():
            if len(group_indices) > 1:
                pred_pairs.update(combinations(sorted(group_indices), 2))

        tp = len(true_pairs & pred_pairs)
        fp = len(pred_pairs - true_pairs)
        fn = len(true_pairs - pred_pairs)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        return {
            "precision": precision,
            "recall": recall,
            "f1_score": f1,
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "total_true_pairs": len(true_pairs),
            "total_pred_pairs": len(pred_pairs),
        }

    def _calculate_clustering_metrics(
        self, predicted_df: pd.DataFrame, ground_truth_groups: dict[str, list[Any]], group_col: str
    ) -> dict[str, float]:
        """
        Calcula conteos y tamaños de grupo de la predicción y de la verdad.
        """
        n_true_groups = len(ground_truth_groups)
        n_pred_groups = predicted_df[group_col].nunique()

        pred_group_sizes = predicted_df[group_col].value_counts()
        avg_pred_size = pred_group_sizes.mean()

        true_group_sizes = [len(indices) for indices in ground_truth_groups.values()]
        avg_true_size = np.mean(true_group_sizes) if true_group_sizes else 0

        pred_singles = (pred_group_sizes == 1).sum()
        true_singles = sum(1 for size in true_group_sizes if size == 1)

        return {
            "n_true_groups": float(n_true_groups),
            "n_pred_groups": float(n_pred_groups),
            "avg_true_group_size": float(avg_true_size),
            "avg_pred_group_size": float(avg_pred_size),
            "pred_singleton_groups": float(pred_singles),
            "true_singleton_groups": float(true_singles),
            "group_count_ratio": float(n_pred_groups / n_true_groups if n_true_groups > 0 else 0),
        }

    def analyze_errors(
        self,
        predicted_df: pd.DataFrame,
        ground_truth_df: pd.DataFrame,
        group_col: str = "ID_GRUPO",
        truth_col: str = "ID_GRUPO_ESPERADO",
        n_examples: int = 5,
    ) -> dict[str, pd.DataFrame]:
        """
        Analiza los errores de predicción y devuelve ejemplos de cada tipo.

        Returns:
            ``false_positives``: grupos predichos que mezclan grupos verdaderos
            distintos; ``false_negatives``: grupos verdaderos repartidos en
            varios grupos predichos. Cada valor trae ``NIT``, ``RAZON_SOCIAL``,
            ``TRUE_GROUP`` y ``PREDICTED_GROUP``; la clave falta si no hay errores.
        """
        analysis_df = ground_truth_df.copy()
        analysis_df["PREDICTED_GROUP"] = predicted_df[group_col]

        # TRUE_GROUP: strip de la etiqueta, UNKNOWN si es nula. Vectorizado
        # (equivalencia fijada en test_vectorization_equivalence::test_true_group_*).
        _truth_series = analysis_df[truth_col]
        _mask_notna = _truth_series.notna()
        analysis_df["TRUE_GROUP"] = "UNKNOWN"
        if _mask_notna.any():
            analysis_df.loc[_mask_notna, "TRUE_GROUP"] = (
                _truth_series.loc[_mask_notna].astype(str).str.strip()
            )

        errors: dict[str, list[pd.DataFrame]] = {
            "false_positives": [],
            "false_negatives": [],
        }

        # Falsos positivos: un grupo predicho con más de un grupo verdadero.
        for _pred_group, group_df in analysis_df.groupby("PREDICTED_GROUP"):
            if group_df["TRUE_GROUP"].nunique() > 1:
                errors["false_positives"].append(group_df)

        # Falsos negativos: un grupo verdadero partido en varios predichos.
        for true_group, group_df in analysis_df.groupby("TRUE_GROUP"):
            if group_df["PREDICTED_GROUP"].nunique() > 1 and true_group != "UNKNOWN":
                errors["false_negatives"].append(group_df)

        columnas = ["NIT", "RAZON_SOCIAL", "TRUE_GROUP", "PREDICTED_GROUP"]
        error_examples: dict[str, pd.DataFrame] = {}
        if errors["false_positives"]:
            error_examples["false_positives"] = pd.concat(errors["false_positives"][:n_examples])[
                columnas
            ]
        if errors["false_negatives"]:
            error_examples["false_negatives"] = pd.concat(errors["false_negatives"][:n_examples])[
                columnas
            ]

        return error_examples
