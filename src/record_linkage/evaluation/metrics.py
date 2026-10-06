"""
evaluation.metrics — record_linkage_pipeline

Componentes:
    - class PerformanceAnalyzer  (origen: notebook celda [133])
    - class EntityMetricsEvaluator  (origen: notebook celda [164])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..reporting._fases import ETIQUETAS_FASE, tiempos_por_fase
from ..utils.logger import CustomLogger


class PerformanceAnalyzer:
    """
    Analizador de rendimiento del pipeline completo.
    Trackea tiempos, uso de memoria y genera reportes de optimización.
    """

    def __init__(self):
        self.logger = CustomLogger("PerformanceAnalyzer")
        self.metrics_history = []
        self.baseline_metrics = None

    def analyze_run(
        self, pipeline_results: dict[str, Any], run_name: str = "default"
    ) -> dict[str, Any]:
        """
        Analizar una ejecución del pipeline.

        Args:
            pipeline_results: Resultados del pipeline incluyendo métricas
            run_name: Nombre identificador de la ejecución

        Returns:
            Análisis completo del rendimiento
        """
        self.logger.info(f"Analizando rendimiento de ejecución '{run_name}'")

        # Extraer métricas de tiempo
        time_metrics = self._extract_time_metrics(pipeline_results)

        # Extraer métricas de memoria
        memory_metrics = self._extract_memory_metrics(pipeline_results)

        # Extraer métricas de calidad
        quality_metrics = self._extract_quality_metrics(pipeline_results)

        # Calcular métricas derivadas
        derived_metrics = self._calculate_derived_metrics(
            time_metrics, memory_metrics, quality_metrics, pipeline_results
        )

        # Crear análisis completo
        analysis = {
            "run_name": run_name,
            "timestamp": pd.Timestamp.now(),
            "time_metrics": time_metrics,
            "memory_metrics": memory_metrics,
            "quality_metrics": quality_metrics,
            "derived_metrics": derived_metrics,
            "bottlenecks": self._identify_bottlenecks(time_metrics),
            "recommendations": self._generate_recommendations(
                time_metrics, memory_metrics, quality_metrics
            ),
        }

        # Comparar con baseline si existe
        if self.baseline_metrics:
            analysis["comparison"] = self._compare_with_baseline(analysis)

        # Guardar en historial
        self.metrics_history.append(analysis)

        return analysis

    def _extract_time_metrics(self, results: dict[str, Any]) -> dict[str, float]:
        """Tiempos por fase EXACTAMENTE como los cronometró el orquestador.

        F1.6: se leen con :func:`reporting._fases.tiempos_por_fase` (la única
        lectura de ``metrics["phase_times"]`` en la casa) y se exponen como
        ``<clave de fase>_time`` (``L2_lsh_candidates_time``…) con su
        ``<clave>_time_pct``, más ``total_time`` (``execution_time`` o, si no
        viene, la suma de las fases medidas). Las claves viejas
        (``preprocessing_time``, ``scoring_time``…) nadie las producía: el
        analizador leía ceros y nunca encontraba un cuello de botella.
        """
        metrics = results.get("metrics", {})
        por_fase = tiempos_por_fase(metrics)
        total = metrics.get("execution_time", 0) or sum(por_fase.values())

        time_metrics: dict[str, float] = {"total_time": total}
        time_metrics.update({f"{clave}_time": segundos for clave, segundos in por_fase.items()})
        if total > 0:
            for clave, segundos in por_fase.items():
                time_metrics[f"{clave}_time_pct"] = (segundos / total) * 100

        return time_metrics

    def _extract_memory_metrics(self, results: dict[str, Any]) -> dict[str, float]:
        """Extraer métricas de uso de memoria."""
        metrics = results.get("metrics", {})

        return {
            "peak_memory_gb": metrics.get("max_memory_gb", 0),
            "avg_memory_gb": metrics.get("avg_memory_gb", 0),
            "memory_efficiency": metrics.get("memory_efficiency", 0),
            "gc_collections": metrics.get("gc_collections", 0),
        }

    def _extract_quality_metrics(self, results: dict[str, Any]) -> dict[str, float]:
        """Extraer métricas de calidad del linkage."""
        metrics = results.get("metrics", {})
        golden_records = results.get("golden_records", pd.DataFrame())

        quality_metrics = {
            "total_records": metrics.get("total_records", 0),
            "unique_groups": metrics.get("unique_groups", 0),
            "linkage_rate": metrics.get("linkage_rate", 0),
            "reduction_rate": metrics.get("reduction_rate", 0),
            "multi_source_groups": metrics.get("multi_source_groups", 0),
        }

        # Agregar métricas de golden records si están disponibles
        if not golden_records.empty:
            quality_metrics.update(
                {
                    "avg_confidence_score": golden_records["CONFIDENCE_SCORE"].mean(),
                    "high_confidence_rate": (golden_records["CONFIDENCE_SCORE"] > 0.9).mean(),
                    "review_required_rate": (golden_records["CONFIDENCE_SCORE"] < 0.75).mean(),
                }
            )

        return quality_metrics

    def _calculate_derived_metrics(
        self,
        time_metrics: dict[str, float],
        memory_metrics: dict[str, float],
        quality_metrics: dict[str, float],
        results: dict[str, Any],
    ) -> dict[str, float]:
        """Calcular métricas derivadas y ratios de eficiencia."""
        total_records = quality_metrics.get("total_records", 1)
        total_time = time_metrics.get("total_time", 1)

        derived = {
            # Throughput
            "records_per_second": total_records / total_time if total_time > 0 else 0,
            "records_per_minute": (total_records / total_time) * 60 if total_time > 0 else 0,
            # Eficiencia
            "time_per_1k_records": (total_time / total_records) * 1000 if total_records > 0 else 0,
            "memory_per_1k_records": (memory_metrics.get("peak_memory_gb", 0) / total_records)
            * 1000
            if total_records > 0
            else 0,
            # Calidad vs Velocidad
            "quality_speed_ratio": quality_metrics.get("linkage_rate", 0) * (3600 / total_time)
            if total_time > 0
            else 0,  # linkage_rate * records_per_hour
        }

        return derived

    def _identify_bottlenecks(self, time_metrics: dict[str, float]) -> list[str]:
        """Identificar cuellos de botella en el pipeline."""
        bottlenecks = []

        # Fases que toman más del 30% del tiempo total
        total_time = time_metrics.get("total_time", 1)
        for phase, time in time_metrics.items():
            if phase.endswith("_time") and phase != "total_time":
                pct = (time / total_time) * 100 if total_time > 0 else 0
                if pct > 30:
                    clave = phase.removesuffix("_time")
                    bottlenecks.append(
                        f"{ETIQUETAS_FASE.get(clave, clave)}: {pct:.1f}% del tiempo total"
                    )

        # Golden records es conocido por ser lento
        if time_metrics.get("L5_golden_time", 0) > 0.5 * total_time:
            bottlenecks.append(
                "⚠️ Golden Records toma más del 50% del tiempo - optimización crítica"
            )

        return bottlenecks

    def _generate_recommendations(
        self,
        time_metrics: dict[str, float],
        memory_metrics: dict[str, float],
        quality_metrics: dict[str, float],
    ) -> list[str]:
        """Generar recomendaciones de optimización."""
        recommendations = []

        # Basadas en tiempo
        if time_metrics.get("L5_golden_time_pct", 0) > 40:
            recommendations.append(
                "🎯 Implementar procesamiento paralelo en golden records "
                "(puede reducir tiempo en 60-70%)"
            )

        if time_metrics.get("L2_lsh_candidates_time_pct", 0) > 30:
            recommendations.append(
                "🔍 Optimizar parámetros LSH: reducir permutations o aumentar threshold"
            )

        # Basadas en memoria
        peak_memory = memory_metrics.get("peak_memory_gb", 0)
        if peak_memory > 8:
            recommendations.append(
                f"💾 Uso de memoria alto ({peak_memory:.1f}GB). "
                "Considerar: reducir batch_size o habilitar modo streaming"
            )

        # Basadas en calidad
        linkage_rate = quality_metrics.get("linkage_rate", 0)
        if linkage_rate < 0.05:
            recommendations.append(
                f"📊 Tasa de linkage baja ({linkage_rate:.1%}). "
                "Considerar: reducir thresholds o ajustar parámetros de similitud"
            )

        review_rate = quality_metrics.get("review_required_rate", 0)
        if review_rate > 0.3:
            recommendations.append(
                f"⚠️ Alta tasa de revisión requerida ({review_rate:.1%}). "
                "Revisar calidad de datos de entrada"
            )

        return recommendations if recommendations else ["✅ Rendimiento óptimo"]

    def _compare_with_baseline(self, current: dict[str, Any]) -> dict[str, Any]:
        """Comparar métricas actuales con baseline."""
        comparison = {
            "time_improvement": self._calculate_improvement(
                self.baseline_metrics["time_metrics"]["total_time"],
                current["time_metrics"]["total_time"],
            ),
            "memory_improvement": self._calculate_improvement(
                self.baseline_metrics["memory_metrics"]["peak_memory_gb"],
                current["memory_metrics"]["peak_memory_gb"],
            ),
            "quality_change": self._calculate_improvement(
                self.baseline_metrics["quality_metrics"].get("linkage_rate", 0),
                current["quality_metrics"].get("linkage_rate", 0),
                higher_is_better=True,
            ),
        }

        return comparison

    def _calculate_improvement(
        self, baseline: float, current: float, higher_is_better: bool = False
    ) -> dict[str, float]:
        """Calcular porcentaje de mejora."""
        if baseline == 0:
            return {"pct_change": 0, "interpretation": "No comparable"}

        pct_change = ((current - baseline) / baseline) * 100

        if higher_is_better:
            improvement = pct_change
        else:
            improvement = -pct_change  # Negativo porque menos es mejor

        interpretation = (
            "Mejora" if improvement > 0 else "Sin cambio" if improvement == 0 else "Deterioro"
        )

        return {
            "baseline": baseline,
            "current": current,
            "pct_change": round(pct_change, 2),
            "improvement_pct": round(improvement, 2),
            "interpretation": interpretation,
        }

    def set_baseline(self, run_name: str):
        """Establecer una ejecución como baseline para comparaciones."""
        for run in self.metrics_history:
            if run["run_name"] == run_name:
                self.baseline_metrics = run
                self.logger.info(f"Baseline establecido: {run_name}")
                return

        raise ValueError(f"No se encontró ejecución con nombre '{run_name}'")

    def generate_performance_report(self) -> pd.DataFrame:
        """Generar reporte consolidado de todas las ejecuciones."""
        if not self.metrics_history:
            return pd.DataFrame()

        # Convertir historia a DataFrame
        rows = []
        for run in self.metrics_history:
            row = {
                "run_name": run["run_name"],
                "timestamp": run["timestamp"],
                "total_time_min": run["time_metrics"]["total_time"] / 60,
                "records_per_min": run["derived_metrics"]["records_per_minute"],
                "peak_memory_gb": run["memory_metrics"]["peak_memory_gb"],
                "linkage_rate": run["quality_metrics"]["linkage_rate"],
                "avg_confidence": run["quality_metrics"].get("avg_confidence_score", 0),
                "bottlenecks": len(run["bottlenecks"]),
            }
            rows.append(row)

        report_df = pd.DataFrame(rows)
        report_df = report_df.sort_values("timestamp")

        return report_df


class EntityMetricsEvaluator:
    """Evalúa los resultados clasificando cada entidad real."""

    def calculate_metrics(self, df_merged_results: pd.DataFrame) -> dict[str, Any]:
        pureza_map = df_merged_results.groupby("ID_GRUPO")["NIT_FINAL_truth"].nunique()
        fragmentacion_map = df_merged_results.groupby("NIT_FINAL_truth")["ID_GRUPO"].nunique()
        clasificaciones = {"perfectas": 0, "contaminadas": 0, "fragmentadas": 0, "mixtas": 0}
        for nit_final_truth, n_fragmentos in fragmentacion_map.items():
            grupos_donde_aparece = df_merged_results[
                df_merged_results["NIT_FINAL_truth"] == nit_final_truth
            ]["ID_GRUPO"].unique()
            es_impuro = any(pureza_map.get(gid, 1) > 1 for gid in grupos_donde_aparece)
            esta_fragmentada = n_fragmentos > 1
            if not esta_fragmentada and not es_impuro:
                clasificaciones["perfectas"] += 1
            elif esta_fragmentada and not es_impuro:
                clasificaciones["fragmentadas"] += 1
            elif not esta_fragmentada and es_impuro:
                clasificaciones["contaminadas"] += 1
            else:
                clasificaciones["mixtas"] += 1
        total_entidades = len(fragmentacion_map)
        return {
            "counts": clasificaciones,
            "total_entidades_reales": total_entidades,
            "porcentajes": {
                "perfect_entities_pct": (clasificaciones["perfectas"] / total_entidades)
                if total_entidades > 0
                else 0,
                "contaminated_entities_pct": (
                    (clasificaciones["contaminadas"] + clasificaciones["mixtas"]) / total_entidades
                )
                if total_entidades > 0
                else 0,
                "fragmented_entities_pct": (
                    (clasificaciones["fragmentadas"] + clasificaciones["mixtas"]) / total_entidades
                )
                if total_entidades > 0
                else 0,
            },
        }
