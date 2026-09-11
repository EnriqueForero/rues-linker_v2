"""
evaluation.quality — record_linkage_pipeline

Componentes:
    - class QualityEvaluator  (origen: notebook celda [132])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..utils.logger import CustomLogger
from ..utils.performance import track_performance


class QualityEvaluator:
    """
    Evalúa la calidad del record linkage comparando con ground truth.
    Calcula métricas estándar: precision, recall, F1, F2.
    """

    def __init__(self, ground_truth_path: str | None = None):
        self.logger = CustomLogger("QualityEvaluator")
        self.ground_truth = None
        self.true_matches = set()
        self.true_non_matches = set()

        if ground_truth_path:
            self.load_ground_truth(ground_truth_path)

    def load_ground_truth(self, path: str):
        """Cargar ground truth desde archivo."""
        self.logger.info(f"Cargando ground truth desde: {path}")

        if path.endswith(".xlsx"):
            self.ground_truth = pd.read_excel(path)
        else:
            self.ground_truth = pd.read_csv(path)

        # Filtrar solo registros revisados
        reviewed = self.ground_truth[self.ground_truth["IS_MATCH"].notna()]

        # Crear sets de matches y non-matches
        for _, row in reviewed.iterrows():
            pair = frozenset([row["ID_1"], row["ID_2"]])
            if row["IS_MATCH"] == 1:
                self.true_matches.add(pair)
            else:
                self.true_non_matches.add(pair)

        self.logger.info(
            f"Ground truth cargado: {len(self.true_matches)} matches, "
            f"{len(self.true_non_matches)} non-matches"
        )

    @track_performance("Evaluación de calidad")
    def evaluate(self, df_linked: pd.DataFrame, detailed: bool = True) -> dict[str, Any]:
        """
        Evaluar resultados del linkage contra ground truth.

        Args:
            df_linked: DataFrame con resultados del linkage (columna ID_GRUPO)
            detailed: Si incluir análisis detallado por tipo de caso

        Returns:
            Diccionario con métricas de evaluación
        """
        if not self.true_matches and not self.true_non_matches:
            raise ValueError("No hay ground truth cargado")

        self.logger.info("Evaluando resultados contra ground truth")

        # Extraer pares encontrados por el algoritmo
        found_matches = self._extract_pairs_from_clusters(df_linked)

        # Calcular métricas básicas
        metrics = self._calculate_basic_metrics(found_matches)

        # Análisis detallado si se solicita
        if detailed:
            detailed_analysis = self._analyze_by_case_type(found_matches, df_linked)
            metrics["detailed_analysis"] = detailed_analysis

        # Agregar resumen
        metrics["summary"] = self._create_evaluation_summary(metrics)

        return metrics

    def _extract_pairs_from_clusters(self, df_linked: pd.DataFrame) -> set[frozenset]:
        """Extraer todos los pares de registros que fueron vinculados."""
        found_matches = set()

        # Agrupar por ID_GRUPO
        groups = df_linked.groupby("ID_GRUPO").groups

        for _group_id, indices in groups.items():
            if len(indices) > 1:
                # Generar todos los pares posibles en el grupo
                indices_list = list(indices)
                for i in range(len(indices_list)):
                    for j in range(i + 1, len(indices_list)):
                        pair = frozenset([indices_list[i], indices_list[j]])
                        found_matches.add(pair)

        self.logger.info(f"Extraídos {len(found_matches)} pares de los clusters")

        return found_matches

    def _calculate_basic_metrics(self, found_matches: set[frozenset]) -> dict[str, float]:
        """Calcular métricas básicas de evaluación."""
        # Intersecciones
        true_positives = len(found_matches.intersection(self.true_matches))
        false_positives = len(found_matches.difference(self.true_matches))
        false_negatives = len(self.true_matches.difference(found_matches))
        true_negatives = len(self.true_non_matches.difference(found_matches))

        # Métricas
        precision = (
            true_positives / (true_positives + false_positives)
            if (true_positives + false_positives) > 0
            else 0
        )
        recall = (
            true_positives / (true_positives + false_negatives)
            if (true_positives + false_negatives) > 0
            else 0
        )
        f1_score = (
            2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
        )

        # F2 score (mayor peso al recall)
        beta = 2
        f2_score = (
            (1 + beta**2) * (precision * recall) / (beta**2 * precision + recall)
            if (beta**2 * precision + recall) > 0
            else 0
        )

        # Accuracy
        total = true_positives + true_negatives + false_positives + false_negatives
        accuracy = (true_positives + true_negatives) / total if total > 0 else 0

        return {
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "true_negatives": true_negatives,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1_score, 4),
            "f2_score": round(f2_score, 4),
            "accuracy": round(accuracy, 4),
        }

    def _analyze_by_case_type(
        self, found_matches: set[frozenset], df_linked: pd.DataFrame
    ) -> dict[str, dict[str, float]]:
        """Analizar rendimiento por tipo de caso."""
        if self.ground_truth is None:
            return {}

        analysis = {}

        # Agrupar ground truth por tipo de caso
        for case_type in self.ground_truth["CASE_TYPE"].unique():
            case_data = self.ground_truth[self.ground_truth["CASE_TYPE"] == case_type]

            # Extraer matches y non-matches de este tipo
            case_matches = set()
            case_non_matches = set()

            for _, row in case_data.iterrows():
                if pd.notna(row["IS_MATCH"]):
                    pair = frozenset([row["ID_1"], row["ID_2"]])
                    if row["IS_MATCH"] == 1:
                        case_matches.add(pair)
                    else:
                        case_non_matches.add(pair)

            # Calcular métricas para este tipo
            if case_matches or case_non_matches:
                tp = len(found_matches.intersection(case_matches))
                fp = len(found_matches.intersection(case_non_matches))
                fn = len(case_matches.difference(found_matches))

                precision = tp / (tp + fp) if (tp + fp) > 0 else 0
                recall = tp / (tp + fn) if (tp + fn) > 0 else 0

                analysis[case_type] = {
                    "precision": round(precision, 4),
                    "recall": round(recall, 4),
                    "total_cases": len(case_data),
                    "true_positives": tp,
                    "false_positives": fp,
                    "false_negatives": fn,
                }

        return analysis

    def _create_evaluation_summary(self, metrics: dict[str, Any]) -> str:
        """Crear resumen textual de la evaluación."""
        summary_parts = [
            f"Precisión: {metrics['precision']:.2%}",
            f"Recall: {metrics['recall']:.2%}",
            f"F1-Score: {metrics['f1_score']:.2%}",
            f"F2-Score: {metrics['f2_score']:.2%}",
        ]

        # Agregar interpretación
        if metrics["precision"] >= 0.95:
            summary_parts.append("✅ Excelente precisión")
        elif metrics["precision"] >= 0.90:
            summary_parts.append("👍 Buena precisión")
        else:
            summary_parts.append("⚠️ Precisión mejorable")

        if metrics["recall"] >= 0.85:
            summary_parts.append("✅ Excelente recall")
        elif metrics["recall"] >= 0.70:
            summary_parts.append("👍 Buen recall")
        else:
            summary_parts.append("⚠️ Recall bajo - se están perdiendo matches")

        return " | ".join(summary_parts)

    def plot_confusion_matrix(self, metrics: dict[str, Any], save_path: str | None = None):
        """Visualizar matriz de confusión."""
        import matplotlib.pyplot as plt
        import seaborn as sns

        # Crear matriz
        matrix = np.array(
            [
                [metrics["true_positives"], metrics["false_positives"]],
                [metrics["false_negatives"], metrics["true_negatives"]],
            ]
        )

        # Crear plot
        plt.figure(figsize=(8, 6))
        sns.heatmap(
            matrix,
            annot=True,
            fmt="d",
            cmap="Blues",
            xticklabels=["Match Predicho", "No-Match Predicho"],
            yticklabels=["Match Real", "No-Match Real"],
        )
        plt.title("Matriz de Confusión - Record Linkage")
        plt.ylabel("Valor Real")
        plt.xlabel("Predicción")

        if save_path:
            plt.savefig(save_path, dpi=300, bbox_inches="tight")

        plt.show()
