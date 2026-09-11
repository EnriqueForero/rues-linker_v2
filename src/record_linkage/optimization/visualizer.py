"""
optimization.visualizer — record_linkage_pipeline

Componentes:
    - class OptimizationVisualizerLite  (origen: notebook celda [169])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from ..utils.output import safe_print as print
from .optuna_integration import OptunaIntegration


class OptimizationVisualizerLite:
    """
    Genera una única visualización clave: la convergencia de la métrica objetivo.
    Versión corregida que recibe el objeto OptunaIntegration.
    """

    def __init__(self, optimizer: OptunaIntegration, metric_to_plot: str):
        self.optimizer = optimizer
        self.metric = metric_to_plot

    def plot_convergence(self, save_dir: str):
        """Visualiza la historia de la métrica objetivo."""
        if not self.optimizer.engine.trial_results:
            print("No hay resultados de trials para visualizar.")
            return

        df = pd.DataFrame(self.optimizer.engine.trial_results)

        if self.metric not in df.columns:
            print(
                f"Advertencia: La métrica '{self.metric}' no se encontró. No se puede generar el gráfico."
            )
            return

        fig = go.Figure()

        # Puntos de cada trial
        fig.add_trace(
            go.Scatter(
                x=df["trial_number"], y=df[self.metric], mode="markers", name="Score del Trial"
            )
        )

        # Línea del mejor score
        if self.optimizer.study.direction == "maximize":
            best_scores = df[self.metric].cummax()
        else:
            best_scores = df[self.metric].cummin()

        fig.add_trace(
            go.Scatter(
                x=df["trial_number"],
                y=best_scores,
                mode="lines",
                name="Mejor Score Acumulado",
                line=dict(color="red", width=3),
            )
        )

        fig.update_layout(
            title=f"Convergencia de la Optimización: {self.metric}",
            xaxis_title="Número de Trial",
            yaxis_title="Valor de la Métrica",
            height=500,
            showlegend=True,
        )

        save_path = Path(save_dir) / "optimization_convergence.html"
        fig.write_html(str(save_path))
        print(f"✅ Gráfico de convergencia guardado en: {save_path}")
