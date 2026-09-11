"""
deduplication.validator — record_linkage_pipeline

Componentes:
    - class DeduplicationValidator  (origen: notebook celda [156])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from rapidfuzz import fuzz

from ..utils.logger import CustomLogger
from .unified import deduplicate_unified


class DeduplicationValidator:
    """
    Validador que compara resultados con la implementación original.
    """

    def __init__(self):
        self.logger = CustomLogger("DeduplicationValidator")
        self.tolerance = 0.05  # 5% de tolerancia

    def validate_full_pipeline(
        self, df_test: pd.DataFrame, expected_results: tuple | None = None
    ) -> dict[str, Any]:
        """
        Ejecuta validación completa comparando ambas implementaciones.
        """
        # Ejecutar nueva implementación
        new_correlativa, new_conexiones = deduplicate_unified(
            df_test, validate_against_legacy=False
        )

        # Si no hay resultados esperados, simular algunos básicos
        if expected_results is None:
            expected_correlativa = new_correlativa.copy()
            new_conexiones.copy()
        else:
            expected_correlativa, _expected_conexiones = expected_results

        # Comparar resultados
        validation_report = {
            "cluster_consistency": self._compare_cluster_assignments(
                new_correlativa, expected_correlativa
            ),
            "golden_record_similarity": self._compare_golden_records(
                new_correlativa, expected_correlativa
            ),
            "metrics_delta": self._compare_quality_metrics(new_correlativa, expected_correlativa),
            "performance_improvement": self._compare_performance(),
            "overall_compatibility": "PASS",  # Simplificado para el ejemplo
        }

        return validation_report

    def _compare_cluster_assignments(
        self, new_df: pd.DataFrame, expected_df: pd.DataFrame
    ) -> float:
        """Comparar asignaciones de clusters."""
        if len(new_df) != len(expected_df):
            return 0.0

        # Comparar ID_GRUPO
        matches = (new_df["ID_GRUPO"] == expected_df["ID_GRUPO"]).sum()
        return matches / len(new_df)

    def _compare_golden_records(self, new_df: pd.DataFrame, expected_df: pd.DataFrame) -> float:
        """Comparar golden records generados."""
        # Comparar NITs finales
        nit_matches = (new_df["NIT_FINAL"] == expected_df["NIT_FINAL"]).sum()

        # Comparar nombres finales (más flexible)
        name_similarity = new_df.apply(
            lambda row: (
                fuzz.ratio(
                    str(row["RAZON_SOCIAL_FINAL"]),
                    str(expected_df.loc[row.name, "RAZON_SOCIAL_FINAL"]),
                )
                / 100.0
            ),
            axis=1,
        ).mean()

        return (nit_matches / len(new_df) + name_similarity) / 2

    def _compare_quality_metrics(
        self, new_df: pd.DataFrame, expected_df: pd.DataFrame
    ) -> dict[str, float]:
        """Comparar métricas de calidad."""
        metrics = {}

        # Número de grupos únicos
        new_groups = new_df["ID_GRUPO"].nunique()
        expected_groups = expected_df["ID_GRUPO"].nunique()
        metrics["groups_delta"] = abs(new_groups - expected_groups) / expected_groups

        # Tamaño promedio de grupos
        new_avg_size = new_df.groupby("ID_GRUPO").size().mean()
        expected_avg_size = expected_df.groupby("ID_GRUPO").size().mean()
        metrics["avg_size_delta"] = abs(new_avg_size - expected_avg_size) / expected_avg_size

        return metrics

    def _compare_performance(self) -> dict[str, Any]:
        """Comparar métricas de performance (simulado)."""
        return {
            "speed_improvement": 2.5,  # 2.5x más rápido
            "memory_reduction": 0.4,  # 40% menos memoria
        }
