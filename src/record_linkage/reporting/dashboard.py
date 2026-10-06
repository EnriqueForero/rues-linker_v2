"""
reporting.dashboard — record_linkage_pipeline

Componentes:
    - class ExecutiveDashboard  (origen: notebook celda [138])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any

import matplotlib.patches as patches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.gridspec import GridSpec, GridSpecFromSubplotSpec

from ..utils.logger import CustomLogger
from ._fases import MENSAJE_SIN_TIEMPOS, etiquetar, formatear_segundos, tiempos_por_fase
from ._sqlite import open_readonly_sqlite, quote_existing_table, validate_row_limit
from ._text_utils import strip_emojis as _strip_emojis


def _serie_tiempos(metrics: dict[str, Any] | None) -> pd.Series:
    """Serie que dibuja el panel «Tiempo por Fase»: etiqueta humana → segundos.

    Función pura (F1.6): son EXACTAMENTE los ``phase_times`` del orquestador,
    sin escalar ni repartir. Sin ``phase_times`` la serie es vacía y el panel
    dice «Sin tiempos por fase»; antes se inventaban porcentajes fijos sobre
    el tiempo total.
    """
    tiempos = etiquetar(tiempos_por_fase(metrics))
    return pd.Series(tiempos, dtype="float64")


class ExecutiveDashboard:
    """
    Dashboard ejecutivo V4.0 - Versión final con todas las mejoras.

    Características principales:
    - Diseño profesional y moderno
    - Manejo eficiente de memoria para datasets grandes
    - Soporte completo para datos en memoria y archivos
    - Métricas en tiempo real con fallback inteligente
    - Visualizaciones adaptativas según datos disponibles
    - Exportación en alta calidad
    """

    def __init__(
        self,
        correlative_data: pd.DataFrame | str,
        golden_records_data: pd.DataFrame | str,
        metrics: dict[str, Any],
        pipeline_start_time: float | None = None,
        style: str = "seaborn",
        config: dict[str, Any] | None = None,
    ):
        """
        Inicializa el dashboard con configuración robusta.

        Args:
            correlative_data: DataFrame o ruta a archivo con tabla correlativa
            golden_records_data: DataFrame o ruta a archivo con golden records
            metrics: Diccionario con métricas del proceso
            pipeline_start_time: Timestamp de inicio del pipeline
            style: Estilo visual a aplicar
            config: Configuración adicional
        """
        self.metrics = metrics or {}
        self.pipeline_start_time = pipeline_start_time
        self.config = config or {}
        self.logger = CustomLogger("ExecutiveDashboard")

        # Límites de memoria
        self.sample_size = self.config.get("dashboard_sample_size", 50000)

        # Logging inicial
        self.logger.info("=" * 60)
        self.logger.info("Inicializando ExecutiveDashboard V4.0 - VERSIÓN FINAL")
        self.logger.info(f"Tipo correlative_data: {type(correlative_data)}")
        self.logger.info(f"Tipo golden_records_data: {type(golden_records_data)}")
        self.logger.info(f"Métricas disponibles: {list(metrics.keys())[:10]}...")
        self.logger.info("=" * 60)

        # Cargar datos
        self.correlative_table = self._load_data(
            correlative_data, "correlative_table", self.sample_size
        )
        self.golden_records = self._load_data(
            golden_records_data, "golden_records", self.sample_size
        )

        # Configurar visualización
        self._setup_style(style)
        self.colors = self._setup_color_palette()

        # Validar datos
        self._validate_data()

        self.logger.info(
            f"Dashboard listo - Correlative: {len(self.correlative_table):,} | Golden: {len(self.golden_records):,}"
        )

    def _setup_logger(self) -> logging.Logger:
        """Configura logger con formato consistente."""
        logger = logging.getLogger("ExecutiveDashboard")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def _validate_data(self):
        """Valida integridad básica de los datos."""
        # Verificar columnas mínimas en correlative
        if not self.correlative_table.empty:
            required = ["ID_GRUPO"]
            missing = set(required) - set(self.correlative_table.columns)
            if missing:
                self.logger.warning(f"Columnas faltantes en correlative: {missing}")

        # Verificar columnas en golden records
        if not self.golden_records.empty:
            required = ["ID_GRUPO"]
            missing = set(required) - set(self.golden_records.columns)
            if missing:
                self.logger.warning(f"Columnas faltantes en golden: {missing}")

    def _load_data(
        self, data_ref: pd.DataFrame | str, table_name: str, sample_size: int
    ) -> pd.DataFrame:
        """
        Carga datos con gestión inteligente de memoria.
        """
        try:
            # DataFrame directo
            if isinstance(data_ref, pd.DataFrame):
                if len(data_ref) > sample_size:
                    self.logger.info(
                        f"Usando muestra de {sample_size:,} registros para {table_name}"
                    )
                    # Muestreo inteligente
                    if "CONFIDENCE_SCORE" in data_ref.columns and table_name == "golden_records":
                        # Priorizar casos problemáticos
                        return self._weighted_sample(data_ref, sample_size)
                    return data_ref.sample(n=sample_size, random_state=42)
                return data_ref

            # Archivo
            if isinstance(data_ref, str) and os.path.exists(data_ref):
                if data_ref.endswith(".db"):
                    return self._load_from_sqlite(data_ref, table_name, sample_size)
                elif data_ref.endswith(".parquet"):
                    df = pd.read_parquet(data_ref)
                    return self._apply_sample(df, sample_size, table_name)
                elif data_ref.endswith((".csv", ".csv.gz")):
                    # Para CSV grandes, leer por chunks
                    if os.path.getsize(data_ref) > 50 * 1024 * 1024:  # > 50MB
                        return self._load_csv_sample(data_ref, sample_size)
                    df = pd.read_csv(data_ref)
                    return self._apply_sample(df, sample_size, table_name)

        except Exception as e:
            self.logger.error(f"Error cargando {table_name}: {e}")

        # Retornar DataFrame vacío con estructura esperada
        return self._create_empty_dataframe(table_name)

    def _load_from_sqlite(self, db_path: str, table_name: str, sample_size: int) -> pd.DataFrame:
        """Carga muestra desde SQLite."""
        try:
            with open_readonly_sqlite(db_path) as conn:
                quoted_table = quote_existing_table(conn, table_name)
                safe_sample_size = validate_row_limit(sample_size)
                # Query optimizada para dashboard
                if table_name == "golden_records":
                    # Incluir variedad de confidence scores
                    query = f"""
                    SELECT * FROM (
                        SELECT * FROM {quoted_table}
                        WHERE CONFIDENCE_SCORE < 0.75
                        ORDER BY RANDOM() LIMIT ?
                    )
                    UNION ALL
                    SELECT * FROM (
                        SELECT * FROM {quoted_table}
                        WHERE CONFIDENCE_SCORE >= 0.75 AND CONFIDENCE_SCORE < 0.9
                        ORDER BY RANDOM() LIMIT ?
                    )
                    UNION ALL
                    SELECT * FROM (
                        SELECT * FROM {quoted_table}
                        WHERE CONFIDENCE_SCORE >= 0.9
                        ORDER BY RANDOM() LIMIT ?
                    )
                    """
                    per_band = safe_sample_size // 3
                    params = (per_band, per_band, per_band)
                else:
                    query = f"SELECT * FROM {quoted_table} ORDER BY RANDOM() LIMIT ?"
                    params = (safe_sample_size,)

                return pd.read_sql_query(query, conn, params=params)
        except Exception as e:
            self.logger.error(f"Error cargando desde SQLite: {e}")
            return pd.DataFrame()

    def _weighted_sample(self, df: pd.DataFrame, sample_size: int) -> pd.DataFrame:
        """Muestreo ponderado para incluir casos problemáticos."""
        try:
            # Dar más peso a casos con baja confianza
            if "CONFIDENCE_SCORE" in df.columns:
                weights = 1 - df["CONFIDENCE_SCORE"].fillna(0.5)
                weights = weights / weights.sum()

                indices = np.random.choice(
                    df.index, size=min(sample_size, len(df)), p=weights, replace=False
                )
                return df.loc[indices]
        except:
            pass

        return df.sample(n=min(sample_size, len(df)), random_state=42)

    def _load_csv_sample(self, csv_path: str, sample_size: int) -> pd.DataFrame:
        """Carga muestra de CSV grande."""
        chunks = []
        rows_read = 0

        for chunk in pd.read_csv(csv_path, chunksize=10000):
            if rows_read >= sample_size:
                break

            chunk_sample = chunk.sample(n=min(1000, len(chunk)), random_state=42)
            chunks.append(chunk_sample)
            rows_read += len(chunk_sample)

        if chunks:
            return pd.concat(chunks, ignore_index=True).head(sample_size)

        return pd.DataFrame()

    def _apply_sample(self, df: pd.DataFrame, sample_size: int, table_name: str) -> pd.DataFrame:
        """Aplica muestreo inteligente según el tipo de datos."""
        if len(df) <= sample_size:
            return df

        if table_name == "golden_records" and "CONFIDENCE_SCORE" in df.columns:
            return self._weighted_sample(df, sample_size)

        return df.sample(n=sample_size, random_state=42)

    def _create_empty_dataframe(self, table_name: str) -> pd.DataFrame:
        """Crea DataFrame vacío con estructura esperada."""
        if table_name == "correlative_table":
            return pd.DataFrame(columns=["ID_GRUPO", "SRC", "RECORD_ID"])
        else:
            return pd.DataFrame(columns=["ID_GRUPO", "CONFIDENCE_SCORE", "PRIMARY_SOURCE"])

    def _setup_style(self, style: str):
        """Configura estilo de matplotlib."""
        try:
            plt.rcdefaults()

            available = plt.style.available
            if style in available:
                plt.style.use(style)
            elif "seaborn-v0_8" in available:
                plt.style.use("seaborn-v0_8")
            elif "seaborn" in available:
                plt.style.use("seaborn")
            else:
                plt.style.use("default")

            # Configuración adicional para calidad profesional
            plt.rcParams.update(
                {
                    "figure.facecolor": "white",
                    "axes.facecolor": "white",
                    "axes.edgecolor": "#cccccc",
                    "axes.labelcolor": "#333333",
                    "text.color": "#333333",
                    "grid.alpha": 0.3,
                    "font.size": 10,
                    "axes.titlesize": 12,
                    "axes.labelsize": 10,
                    "xtick.labelsize": 9,
                    "ytick.labelsize": 9,
                    "legend.fontsize": 9,
                    "figure.dpi": 100,
                    "savefig.dpi": 300,
                    "savefig.bbox": "tight",
                }
            )

        except Exception as e:
            self.logger.warning(f"Error configurando estilo: {e}")
            plt.style.use("default")

    def _setup_color_palette(self) -> dict[str, str]:
        """Define paleta de colores profesional."""
        return {
            "primary": "#2E86AB",
            "secondary": "#A23B72",
            "success": "#4CAF50",
            "warning": "#FF9800",
            "danger": "#E74C3C",
            "info": "#2196F3",
            "light": "#F5F5F5",
            "dark": "#333333",
            "muted": "#6C757D",
            "white": "#FFFFFF",
            # Gradientes
            "gradient_start": "#667eea",
            "gradient_end": "#764ba2",
            # Colores adicionales para gráficos
            "chart_colors": [
                "#1f77b4",
                "#ff7f0e",
                "#2ca02c",
                "#d62728",
                "#9467bd",
                "#8c564b",
                "#e377c2",
                "#7f7f7f",
            ],
        }

    def generate_dashboard(
        self, output_path: str = "dashboard_ejecutivo.png", format: str = "png", dpi: int = 300
    ) -> plt.Figure:
        """
        Genera dashboard ejecutivo completo.

        Args:
            output_path: Ruta donde guardar el dashboard
            format: Formato de salida ('png', 'pdf', 'svg')
            dpi: Resolución en DPI

        Returns:
            Figure de matplotlib con el dashboard
        """
        self.logger.info("Generando dashboard ejecutivo...")

        # Verificar datos mínimos
        if self._insufficient_data():
            return self._generate_minimal_dashboard(output_path, format, dpi)

        try:
            # Crear figura principal con diseño optimizado
            fig = plt.figure(figsize=(20, 24), facecolor="white")

            # Grid principal con proporciones ajustadas
            gs = GridSpec(
                5, 1, figure=fig, hspace=0.25, height_ratios=[0.08, 0.18, 0.32, 0.32, 0.10]
            )

            # Secciones del dashboard
            self._create_header(fig, gs[0])
            self._create_kpi_section(fig, gs[1])
            self._create_main_visualizations(fig, gs[2])
            self._create_detailed_analysis(fig, gs[3])
            self._create_footer(fig, gs[4])

            # Guardar con alta calidad
            self._save_figure(fig, output_path, format, dpi)

            self.logger.info(f"✅ Dashboard guardado: {output_path}")
            return fig

        except Exception as e:
            # F1.4: nunca un PNG con el texto del error. Se cierra la figura
            # y la excepción sube; DashboardStrategy la convierte en una
            # omisión con motivo en el manifiesto.
            self.logger.error(f"Error generando dashboard: {e}")
            self.logger.debug("Stack trace:", exc_info=True)
            plt.close("all")
            raise

    def _insufficient_data(self) -> bool:
        """Verifica si hay datos suficientes para el dashboard."""
        return self.correlative_table.empty and self.golden_records.empty and not self.metrics

    def _create_header(self, fig, gs_area):
        """Crea encabezado profesional del dashboard."""
        ax = fig.add_subplot(gs_area)
        ax.axis("off")

        # Fondo con gradiente simulado
        gradient = np.linspace(0, 1, 256).reshape(1, -1)
        gradient = np.vstack((gradient, gradient))
        ax.imshow(
            gradient,
            extent=[0, 1, 0, 1],
            aspect="auto",
            cmap="Blues",
            alpha=0.3,
            transform=ax.transAxes,
        )

        # Título principal
        ax.text(
            0.5,
            0.65,
            "DASHBOARD EJECUTIVO - RECORD LINKAGE",
            fontsize=28,
            weight="bold",
            ha="center",
            va="center",
            color=self.colors["primary"],
            transform=ax.transAxes,
        )

        # Información contextual
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        profile = self.config.get("profile", "standard")

        info_text = f"Generado: {timestamp} | Perfil: {profile.upper()}"
        ax.text(
            0.5,
            0.25,
            info_text,
            fontsize=12,
            ha="center",
            va="center",
            style="italic",
            alpha=0.8,
            transform=ax.transAxes,
        )

    def _create_kpi_section(self, fig, gs_area):
        """Crea sección de KPIs principales con diseño mejorado."""
        kpi_gs = GridSpecFromSubplotSpec(1, 5, gs_area, wspace=0.15)

        # Calcular KPIs con manejo robusto
        kpis = self._calculate_kpis()

        # Crear tarjetas KPI con diseño moderno
        for i, kpi in enumerate(kpis):
            ax = fig.add_subplot(kpi_gs[0, i])
            self._draw_modern_kpi_card(ax, kpi)

    def _calculate_kpis(self) -> list[dict[str, Any]]:
        """Calcula KPIs principales con valores robustos."""
        kpis = []

        # 1. Total registros
        total_records = self.metrics.get("total_records", len(self.correlative_table))
        kpis.append(
            {
                "value": f"{total_records:,}",
                "label": "REGISTROS\nPROCESADOS",
                "icon": "📊",
                "color": self.colors["primary"],
                "trend": None,
            }
        )

        # 2. Entidades únicas
        unique_groups = self.metrics.get("unique_groups", 0)
        if unique_groups == 0 and not self.correlative_table.empty:
            unique_groups = self.correlative_table["ID_GRUPO"].nunique()

        kpis.append(
            {
                "value": f"{unique_groups:,}",
                "label": "ENTIDADES\nÚNICAS",
                "icon": "🎯",
                "color": self.colors["success"],
                "trend": "down" if total_records > 0 else None,
            }
        )

        # 3. Tasa de reducción
        reduction_rate = self.metrics.get("reduction_rate", 0)
        if reduction_rate == 0 and total_records > 0 and unique_groups > 0:
            reduction_rate = 1 - (unique_groups / total_records)

        kpis.append(
            {
                "value": f"{reduction_rate:.1%}",
                "label": "REDUCCIÓN\nDUPLICADOS",
                "icon": "📉",
                "color": self.colors["info"],
                "trend": "up" if reduction_rate > 0.3 else None,
            }
        )

        # 4. Confidence promedio
        avg_confidence = self._calculate_avg_confidence()
        confidence_color = self._get_confidence_color(avg_confidence)

        kpis.append(
            {
                "value": f"{avg_confidence:.3f}" if avg_confidence > 0 else "N/A",
                "label": "CONFIDENCE\nPROMEDIO",
                "icon": "⭐",
                "color": confidence_color,
                "trend": "up" if avg_confidence > 0.8 else "down" if avg_confidence < 0.7 else None,
            }
        )

        # 5. Tiempo de ejecución
        exec_time = self._calculate_execution_time()
        time_display = self._format_time(exec_time)

        kpis.append(
            {
                "value": time_display,
                "label": "TIEMPO\nTOTAL",
                "icon": "⏱️",
                "color": self.colors["secondary"],
                "trend": None,
            }
        )

        return kpis

    def _calculate_avg_confidence(self) -> float:
        """Calcula confidence promedio de forma robusta."""
        if not self.golden_records.empty and "CONFIDENCE_SCORE" in self.golden_records.columns:
            scores = self.golden_records["CONFIDENCE_SCORE"].dropna()
            if len(scores) > 0:
                return scores.mean()

        return self.metrics.get("avg_confidence", 0.0)

    def _get_confidence_color(self, confidence: float) -> str:
        """Determina color según nivel de confidence."""
        if confidence >= 0.9:
            return self.colors["success"]
        elif confidence >= 0.75:
            return self.colors["info"]
        elif confidence >= 0.6:
            return self.colors["warning"]
        else:
            return self.colors["danger"]

    def _calculate_execution_time(self) -> float:
        """Calcula tiempo de ejecución con fallback."""
        exec_time = self.metrics.get("execution_time", 0)

        # Fallback: calcular desde pipeline_start_time
        if exec_time == 0 and self.pipeline_start_time:
            exec_time = time.time() - self.pipeline_start_time

        # Fallback: sumar los tiempos por fase que sí cronometró el orquestador
        if exec_time == 0:
            exec_time = sum(tiempos_por_fase(self.metrics).values())

        return exec_time

    def _format_time(self, seconds: float) -> str:
        """Formatea tiempo de manera legible."""
        if seconds <= 0:
            return "N/A"
        elif seconds < 1:
            return f"{seconds * 1000:.0f} ms"
        elif seconds < 60:
            return f"{seconds:.1f} seg"
        elif seconds < 3600:
            return f"{seconds / 60:.1f} min"
        else:
            return f"{seconds / 3600:.1f} hrs"

    def _draw_modern_kpi_card(self, ax, kpi: dict[str, Any]):
        """Dibuja tarjeta KPI con diseño moderno."""
        ax.axis("off")

        # Fondo con borde redondeado
        fancy_box = patches.FancyBboxPatch(
            (0.05, 0.05),
            0.9,
            0.9,
            boxstyle="round,pad=0.1",
            facecolor="white",
            edgecolor=kpi["color"],
            linewidth=2,
            transform=ax.transAxes,
        )
        ax.add_patch(fancy_box)

        # Sombra sutil
        shadow = patches.FancyBboxPatch(
            (0.07, 0.03),
            0.9,
            0.9,
            boxstyle="round,pad=0.1",
            facecolor="gray",
            alpha=0.2,
            transform=ax.transAxes,
            zorder=0,
        )
        ax.add_patch(shadow)

        # Icono
        # v0.7.1 (Tarea 1.4): omitir el icono si tiene caracteres no-ASCII
        # (típicamente emojis que la fuente del entorno no soporta y generan
        # UserWarning por cada glifo faltante). Los iconos siguen vivos en el
        # diccionario porque otros consumidores (logs, exports) sí los renderizan
        # bien; solo aquí — donde matplotlib los pinta — se filtran.
        _icon_raw = kpi.get("icon", "") or ""
        _icon_safe = _icon_raw if _icon_raw.isascii() else ""
        ax.text(
            0.5, 0.75, _icon_safe, fontsize=24, ha="center", va="center", transform=ax.transAxes
        )

        # Valor principal
        ax.text(
            0.5,
            0.5,
            str(kpi["value"]),
            fontsize=20,
            weight="bold",
            ha="center",
            va="center",
            color=kpi["color"],
            transform=ax.transAxes,
        )

        # Label
        ax.text(
            0.5,
            0.25,
            kpi["label"],
            fontsize=9,
            weight="bold",
            ha="center",
            va="center",
            color=self.colors["dark"],
            alpha=0.8,
            transform=ax.transAxes,
        )

        # Indicador de tendencia
        if kpi.get("trend"):
            trend_symbol = "↑" if kpi["trend"] == "up" else "↓"
            trend_color = self.colors["success"] if kpi["trend"] == "up" else self.colors["danger"]
            ax.text(
                0.85,
                0.85,
                trend_symbol,
                fontsize=16,
                weight="bold",
                ha="center",
                va="center",
                color=trend_color,
                transform=ax.transAxes,
            )

    def _create_main_visualizations(self, fig, gs_area):
        """Crea visualizaciones principales del dashboard."""
        viz_gs = GridSpecFromSubplotSpec(2, 3, gs_area, wspace=0.25, hspace=0.3)

        # 1. Distribución de Confidence
        ax1 = fig.add_subplot(viz_gs[0, 0])
        self._plot_confidence_gauge(ax1)

        # 2. Análisis por Fuente
        ax2 = fig.add_subplot(viz_gs[0, 1])
        self._plot_source_breakdown(ax2)

        # 3. Métricas de Performance
        ax3 = fig.add_subplot(viz_gs[0, 2])
        self._plot_performance_metrics(ax3)

        # 4. Distribución de Grupos
        ax4 = fig.add_subplot(viz_gs[1, 0])
        self._plot_group_distribution(ax4)

        # 5. Matriz de Calidad
        ax5 = fig.add_subplot(viz_gs[1, 1])
        self._plot_quality_matrix(ax5)

        # 6. Tendencias del Proceso
        ax6 = fig.add_subplot(viz_gs[1, 2])
        self._plot_process_flow(ax6)

    def _plot_confidence_gauge(self, ax):
        """Gráfico tipo gauge para confidence score."""
        if self.golden_records.empty or "CONFIDENCE_SCORE" not in self.golden_records.columns:
            self._show_no_data_message(ax, "Sin datos de confidence")
            return

        scores = self.golden_records["CONFIDENCE_SCORE"].dropna()
        if scores.empty:
            self._show_no_data_message(ax, "Sin scores válidos")
            return

        # Calcular métricas
        avg_score = scores.mean()

        # Crear gauge circular
        theta = np.linspace(0, np.pi, 100)
        r_inner = 0.7
        r_outer = 1.0

        # Colores por segmento
        colors = [
            self.colors["danger"],
            self.colors["warning"],
            self.colors["info"],
            self.colors["success"],
        ]
        thresholds = [0, 0.5, 0.7, 0.85, 1.0]

        # Dibujar segmentos
        for i in range(len(thresholds) - 1):
            mask = (theta / np.pi >= thresholds[i]) & (theta / np.pi < thresholds[i + 1])
            theta_segment = theta[mask]

            if len(theta_segment) > 0:
                x_outer = r_outer * np.cos(theta_segment)
                y_outer = r_outer * np.sin(theta_segment)
                x_inner = r_inner * np.cos(theta_segment)
                y_inner = r_inner * np.sin(theta_segment)

                verts = list(zip(x_outer, y_outer, strict=False)) + list(
                    zip(x_inner[::-1], y_inner[::-1], strict=False)
                )
                poly = patches.Polygon(verts, facecolor=colors[i], alpha=0.3)
                ax.add_patch(poly)

        # Indicador
        angle = avg_score * np.pi
        x_indicator = [0, 0.9 * np.cos(angle)]
        y_indicator = [0, 0.9 * np.sin(angle)]
        ax.plot(x_indicator, y_indicator, "k-", linewidth=3)
        ax.plot(0, 0, "ko", markersize=10)

        # Texto central
        ax.text(
            0,
            -0.3,
            f"{avg_score:.3f}",
            fontsize=24,
            weight="bold",
            ha="center",
            va="center",
            color=self._get_confidence_color(avg_score),
        )

        # Configuración
        ax.set_xlim(-1.2, 1.2)
        ax.set_ylim(-0.5, 1.2)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title("Confidence Score Promedio", fontweight="bold", pad=20)

        # Leyenda
        legend_y = -0.15
        for i, (threshold, color) in enumerate(
            zip(["<0.5", "0.5-0.7", "0.7-0.85", ">0.85"], colors, strict=False)
        ):
            ax.text(
                -0.6 + i * 0.4,
                legend_y,
                threshold,
                fontsize=8,
                ha="center",
                color=color,
                weight="bold",
            )

    def _plot_source_breakdown(self, ax):
        """Análisis detallado por fuente."""
        if "SRC" not in self.correlative_table.columns:
            self._show_no_data_message(ax, "Sin datos de fuentes")
            return

        source_counts = self.correlative_table["SRC"].value_counts()

        # Limitar a top 8 fuentes
        if len(source_counts) > 8:
            top_sources = source_counts.head(7)
            other_count = source_counts[7:].sum()
            source_counts = pd.concat([top_sources, pd.Series({"Otros": other_count})])

        # Crear donut chart
        colors = sns.color_palette("husl", len(source_counts))
        _wedges, texts, autotexts = ax.pie(
            source_counts.values,
            labels=source_counts.index,
            autopct="%1.1f%%",
            startangle=90,
            colors=colors,
            pctdistance=0.85,
            wedgeprops=dict(width=0.5, edgecolor="white"),
        )

        # Centro del donut
        total = source_counts.sum()
        ax.text(0, 0, f"{total:,}\nRegistros", ha="center", va="center", fontsize=12, weight="bold")

        # Mejorar textos
        for text in texts:
            text.set_fontsize(9)
        for autotext in autotexts:
            autotext.set_color("white")
            autotext.set_fontsize(8)
            autotext.set_weight("bold")

        ax.set_title("Distribución por Fuente", fontweight="bold")

    def _plot_performance_metrics(self, ax):
        """
        Tiempo por fase del pipeline, tal como lo cronometró el orquestador.

        F1.6: la serie sale de ``_serie_tiempos`` (``metrics["phase_times"]``).
        Si no hay tiempos, el panel lo dice; no se estima ni se reparte.
        F1.4: sin ``try/except``: un panel que falla relanza y el dashboard se
        omite con motivo en el manifiesto, nunca se pinta «Error generando».
        """
        phase_series = _serie_tiempos(self.metrics).sort_values(ascending=True)

        if phase_series.empty:
            self.logger.warning(
                "Panel «Tiempo por Fase» omitido: metrics['phase_times'] no trae tiempos."
            )
            self._show_no_data_message(ax, MENSAJE_SIN_TIEMPOS)
            return

        # Crear gráfico de barras horizontales
        colors = plt.colormaps["viridis"](np.linspace(0.3, 0.9, len(phase_series)))
        bars = ax.barh(phase_series.index, phase_series.values, color=colors)

        # Añadir etiquetas con valores y porcentajes
        total = phase_series.sum()
        for bar, (_phase, time_val) in zip(bars, phase_series.items(), strict=False):
            width = bar.get_width()
            percentage = (time_val / total * 100) if total > 0 else 0

            # Etiqueta con tiempo y porcentaje
            label = f"{formatear_segundos(time_val)} ({percentage:.0f}%)"
            ax.text(
                width + 0.01 * phase_series.max(),
                bar.get_y() + bar.get_height() / 2,
                label,
                va="center",
                ha="left",
                fontsize=9,
            )

        # Configuración del gráfico
        ax.set_xlabel("Tiempo", fontsize=11)
        ax.set_title("Tiempo por Fase del Pipeline", fontweight="bold", fontsize=12)
        ax.grid(True, which="major", axis="x", linestyle="--", alpha=0.5)

        # Agregar línea de tiempo total
        ax.axvline(x=total, color="red", linestyle="--", alpha=0.5, linewidth=1)
        if total > 0:
            total_str = f"{total:.1f}s" if total < 60 else f"{total / 60:.1f}min"
            ax.text(
                total,
                len(phase_series) - 0.5,
                f"Total: {total_str}",
                ha="right",
                va="center",
                fontsize=9,
                color="red",
            )

        # Limpiar bordes
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)

    def _plot_group_distribution(self, ax):
        """Distribución de tamaños de grupo."""
        if "ID_GRUPO" not in self.correlative_table.columns:
            self._show_no_data_message(ax, "Sin datos de grupos")
            return

        # Calcular tamaños
        group_sizes = self.correlative_table.groupby("ID_GRUPO").size()

        # Categorizar
        bins = [0, 1, 2, 5, 10, 20, 50, float("inf")]
        labels = ["1", "2", "3-5", "6-10", "11-20", "21-50", ">50"]
        size_dist = pd.cut(group_sizes, bins=bins, labels=labels).value_counts().sort_index()

        # Colores con gradiente
        colors = plt.colormaps["coolwarm"](np.linspace(0.2, 0.8, len(size_dist)))

        # Gráfico de barras
        bars = ax.bar(
            range(len(size_dist)), size_dist.values, color=colors, edgecolor="white", linewidth=1
        )

        # Configuración
        ax.set_xticks(range(len(size_dist)))
        ax.set_xticklabels(size_dist.index)
        ax.set_xlabel("Tamaño del Grupo")
        ax.set_ylabel("Cantidad")
        ax.set_title("Distribución de Tamaños de Grupo", fontweight="bold")
        ax.grid(True, alpha=0.3, axis="y")

        # Valores en barras
        for bar, val in zip(bars, size_dist.values, strict=False):
            if val > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.5,
                    f"{val:,}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )

        # Línea de tendencia
        if len(size_dist) > 3:
            z = np.polyfit(range(len(size_dist)), size_dist.values, 2)
            p = np.poly1d(z)
            ax.plot(
                range(len(size_dist)),
                p(range(len(size_dist))),
                "--",
                color=self.colors["danger"],
                alpha=0.5,
                linewidth=2,
            )

    def _plot_quality_matrix(self, ax):
        """Matriz de calidad por diferentes dimensiones."""
        # Preparar datos de calidad
        quality_metrics = self._calculate_quality_metrics()

        if not quality_metrics:
            self._show_no_data_message(ax, "Sin métricas de calidad")
            return

        # Crear matriz de calidad
        categories = list(quality_metrics.keys())
        values = list(quality_metrics.values())

        # Normalizar valores a 0-1
        values_norm = np.array(values) / 100

        # Crear heatmap simple
        data = values_norm.reshape(-1, 1)

        im = ax.imshow(data, cmap="RdYlGn", aspect="auto", vmin=0, vmax=1)

        # Configurar ejes
        ax.set_yticks(range(len(categories)))
        ax.set_yticklabels(categories)
        ax.set_xticks([])

        # Añadir valores
        for i, (_cat, val) in enumerate(zip(categories, values, strict=False)):
            color = "white" if values_norm[i] < 0.5 else "black"
            ax.text(0, i, f"{val:.1f}%", ha="center", va="center", color=color, fontweight="bold")

        # Colorbar
        cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cbar.set_label("Nivel de Calidad", rotation=270, labelpad=15)

        ax.set_title("Métricas de Calidad", fontweight="bold")

    def _calculate_quality_metrics(self) -> dict[str, float]:
        """Calcula métricas de calidad en porcentajes."""
        metrics = {}

        # Completitud
        total_records = self.metrics.get("total_records", 1)
        unique_groups = self.metrics.get("unique_groups", 0)
        completeness = (unique_groups / total_records * 100) if total_records > 0 else 0
        metrics["Completitud"] = min(completeness * 2, 100)  # Escalar

        # Confianza
        avg_confidence = self._calculate_avg_confidence()
        metrics["Confianza"] = avg_confidence * 100

        # Cobertura
        multi_source = self.metrics.get("multi_source_groups", 0)
        coverage = multi_source / max(unique_groups, 1) * 100
        metrics["Cobertura Multi-fuente"] = min(coverage * 3, 100)  # Escalar

        # Eficiencia
        linkage_rate = self.metrics.get("linkage_rate", 0)
        metrics["Eficiencia de Linkage"] = linkage_rate * 100

        # Calidad de datos
        if not self.golden_records.empty and "CONFIDENCE_SCORE" in self.golden_records.columns:
            high_conf = (self.golden_records["CONFIDENCE_SCORE"] > 0.9).sum()
            quality_rate = high_conf / len(self.golden_records) * 100
            metrics["Calidad de Datos"] = quality_rate

        return metrics

    def _plot_process_flow(self, ax):
        """Diagrama de flujo del proceso."""
        ax.axis("off")

        # Datos del flujo
        stages = [
            ("Entrada", self.metrics.get("total_records", 0), self.colors["info"]),
            ("Candidatos", self.metrics.get("candidates_found", 0), self.colors["warning"]),
            ("Scoring", self.metrics.get("pairs_scored", 0), self.colors["secondary"]),
            ("Grupos", self.metrics.get("unique_groups", 0), self.colors["success"]),
        ]

        # Posiciones
        y_positions = np.linspace(0.8, 0.2, len(stages))
        box_width = 0.25
        box_height = 0.12

        for i, (stage, value, color) in enumerate(stages):
            # Caja principal
            rect = patches.FancyBboxPatch(
                (0.375, y_positions[i] - box_height / 2),
                box_width,
                box_height,
                boxstyle="round,pad=0.02",
                facecolor=color,
                alpha=0.7,
                edgecolor=color,
                linewidth=2,
            )
            ax.add_patch(rect)

            # Texto
            ax.text(
                0.5,
                y_positions[i],
                f"{stage}\n{value:,}",
                ha="center",
                va="center",
                fontsize=10,
                fontweight="bold",
                color="white",
            )

            # Flecha
            if i < len(stages) - 1:
                arrow = patches.FancyArrowPatch(
                    (0.5, y_positions[i] - box_height / 2 - 0.01),
                    (0.5, y_positions[i + 1] + box_height / 2 + 0.01),
                    arrowstyle="->,head_width=0.4,head_length=0.2",
                    color=self.colors["dark"],
                    linewidth=2,
                )
                ax.add_patch(arrow)

                # Porcentaje de reducción
                if value > 0 and stages[i + 1][1] > 0:
                    reduction = (1 - stages[i + 1][1] / value) * 100
                    ax.text(
                        0.65,
                        (y_positions[i] + y_positions[i + 1]) / 2,
                        f"-{reduction:.0f}%",
                        fontsize=9,
                        color=self.colors["danger"],
                        fontweight="bold",
                    )

        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_title("Flujo del Proceso", fontweight="bold", pad=20)

    def _create_detailed_analysis(self, fig, gs_area):
        """Crea sección de análisis detallado."""
        analysis_gs = GridSpecFromSubplotSpec(2, 3, gs_area, wspace=0.25, hspace=0.3)

        # 1. Top problemas
        ax1 = fig.add_subplot(analysis_gs[0, 0])
        self._plot_top_issues(ax1)

        # 2. Estadísticas de calidad
        ax2 = fig.add_subplot(analysis_gs[0, 1])
        self._plot_quality_stats(ax2)

        # 3. Recomendaciones
        ax3 = fig.add_subplot(analysis_gs[0, 2])
        self._plot_recommendations(ax3)

        # 4. Análisis de fuentes detallado
        ax4 = fig.add_subplot(analysis_gs[1, :2])
        self._plot_source_details(ax4)

        # 5. Métricas avanzadas
        ax5 = fig.add_subplot(analysis_gs[1, 2])
        self._plot_advanced_metrics(ax5)

    def _plot_top_issues(self, ax):
        """Muestra principales problemas detectados."""
        ax.axis("off")
        ax.set_title("Principales Problemas Detectados", fontweight="bold", fontsize=11)

        issues = []

        # Analizar confidence bajo
        if not self.golden_records.empty and "CONFIDENCE_SCORE" in self.golden_records.columns:
            low_conf = (self.golden_records["CONFIDENCE_SCORE"] < 0.75).sum()
            if low_conf > 0:
                severity = "ALTA" if low_conf > len(self.golden_records) * 0.3 else "MEDIA"
                issues.append(
                    {
                        "issue": f"{low_conf:,} grupos con confidence < 0.75",
                        "severity": severity,
                        "icon": "⚠️",
                    }
                )

        # Grupos muy grandes
        if not self.correlative_table.empty:
            group_sizes = self.correlative_table.groupby("ID_GRUPO").size()
            large_groups = (group_sizes > 50).sum()
            if large_groups > 0:
                issues.append(
                    {
                        "issue": f"{large_groups} grupos con >50 registros",
                        "severity": "MEDIA",
                        "icon": "📊",
                    }
                )

        # Tasa de linkage
        linkage_rate = self.metrics.get("linkage_rate", 0)
        if linkage_rate < 0.05:
            issues.append(
                {
                    "issue": f"Tasa de linkage muy baja ({linkage_rate:.1%})",
                    "severity": "ALTA",
                    "icon": "🔗",
                }
            )

        # Si no hay problemas
        if not issues:
            issues.append(
                {
                    "issue": "No se detectaron problemas significativos",
                    "severity": "OK",
                    "icon": "✅",
                }
            )

        # Mostrar issues
        # v0.7.1 (Tarea 1.4): filtrar icono si tiene caracteres no-ASCII; el
        # bullet '•' es la alternativa neutra cuando el icono real era un emoji.
        y_pos = 0.85
        for issue in issues[:5]:  # Máximo 5
            color = {
                "ALTA": self.colors["danger"],
                "MEDIA": self.colors["warning"],
                "OK": self.colors["success"],
            }.get(issue["severity"], self.colors["info"])

            _icon_raw = issue.get("icon", "") or ""
            _icon_safe = _icon_raw if _icon_raw.isascii() else "•"
            ax.text(0.05, y_pos, _icon_safe, fontsize=14, va="top")
            ax.text(0.15, y_pos, issue["issue"], fontsize=9, va="top", wrap=True)
            ax.text(
                0.9, y_pos, issue["severity"], fontsize=8, va="top", color=color, fontweight="bold"
            )

            y_pos -= 0.18

    def _plot_quality_stats(self, ax):
        """Estadísticas de calidad resumidas."""
        ax.axis("off")
        ax.set_title("Estadísticas de Calidad", fontweight="bold", fontsize=11)

        stats = []

        # Total procesado
        total = self.metrics.get("total_records", 0)
        stats.append(f"📊 Total procesado: {total:,}")

        # Reducción
        reduction = self.metrics.get("reduction_rate", 0)
        stats.append(f"📉 Reducción: {reduction:.1%}")

        # Confidence
        avg_conf = self._calculate_avg_confidence()
        if avg_conf > 0:
            stats.append(f"⭐ Confidence promedio: {avg_conf:.3f}")

        # Multi-fuente
        multi = self.metrics.get("multi_source_groups", 0)
        stats.append(f"🔗 Grupos multi-fuente: {multi:,}")

        # Throughput
        exec_time = self._calculate_execution_time()
        if exec_time > 0 and total > 0:
            throughput = total / exec_time
            stats.append(f"⚡ Throughput: {throughput:.0f} reg/s")

        # Mostrar estadísticas
        # v0.7.1 (Tarea 1.4): sanitizar emojis JUSTO antes del render para no
        # romper la lógica que arma `stats[]` con prefijos visuales.
        stats_text = _strip_emojis("\n\n".join(stats))
        ax.text(
            0.5,
            0.5,
            stats_text,
            ha="center",
            va="center",
            fontsize=10,
            transform=ax.transAxes,
            bbox=dict(boxstyle="round,pad=0.5", facecolor=self.colors["light"], alpha=0.8),
        )

    def _plot_recommendations(self, ax):
        """Muestra recomendaciones basadas en el análisis."""
        ax.axis("off")
        ax.set_title("Recomendaciones", fontweight="bold", fontsize=11)

        recommendations = self._generate_recommendations()

        # Mostrar recomendaciones
        # v0.7.1 (Tarea 1.4): bullet ASCII en lugar de 💡, que no renderiza.
        y_pos = 0.85
        for _i, rec in enumerate(recommendations[:4]):
            ax.text(0.05, y_pos, f"• {rec}", fontsize=9, va="top", wrap=True)
            y_pos -= 0.22

    def _generate_recommendations(self) -> list[str]:
        """Genera recomendaciones basadas en métricas."""
        recs = []

        # Basadas en linkage rate
        linkage_rate = self.metrics.get("linkage_rate", 0)
        if linkage_rate < 0.05:
            recs.append("Reducir umbrales de similitud para capturar más coincidencias")
        elif linkage_rate > 0.5:
            recs.append("Aumentar umbrales para evitar sobre-vinculación")

        # Basadas en confidence
        avg_conf = self._calculate_avg_confidence()
        if 0 < avg_conf < 0.7:
            recs.append("Mejorar calidad de datos de entrada")

        # Basadas en tiempo
        exec_time = self._calculate_execution_time()
        if exec_time > 3600:
            recs.append("Considerar procesamiento por chunks")

        # Casos de revisión
        if not self.golden_records.empty and "CONFIDENCE_SCORE" in self.golden_records.columns:
            review = (self.golden_records["CONFIDENCE_SCORE"] < 0.75).sum()
            if review > 100:
                recs.append(f"Priorizar revisión de {review:,} casos")

        # Recomendación por defecto
        if not recs:
            recs.append("Proceso completado exitosamente")
            recs.append("Considerar monitoreo periódico")

        return recs

    def _plot_source_details(self, ax):
        """Tabla detallada de análisis por fuente."""
        if "SRC" not in self.correlative_table.columns:
            self._show_no_data_message(ax, "Sin datos de fuentes")
            return

        # Calcular estadísticas por fuente
        source_stats = []
        sources = self.correlative_table["SRC"].value_counts().head(10)

        for source, count in sources.items():
            source_data = self.correlative_table[self.correlative_table["SRC"] == source]
            groups = source_data["ID_GRUPO"].nunique()

            # Confidence promedio si está disponible
            avg_conf = "N/A"
            if not self.golden_records.empty and "PRIMARY_SOURCE" in self.golden_records.columns:
                source_golden = self.golden_records[self.golden_records["PRIMARY_SOURCE"] == source]
                if not source_golden.empty and "CONFIDENCE_SCORE" in source_golden.columns:
                    avg_conf = f"{source_golden['CONFIDENCE_SCORE'].mean():.3f}"

            source_stats.append(
                [source, count, groups, count / groups if groups > 0 else 0, avg_conf]
            )

        # Crear tabla
        columns = ["Fuente", "Registros", "Grupos", "Reg/Grupo", "Conf. Prom."]

        # Limpiar ejes
        ax.clear()
        ax.axis("tight")
        ax.axis("off")

        # Crear tabla
        table = ax.table(
            cellText=source_stats,
            colLabels=columns,
            cellLoc="center",
            loc="center",
            colWidths=[0.3, 0.2, 0.2, 0.15, 0.15],
        )

        # Estilo de tabla
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(1, 1.5)

        # Colorear encabezados
        for i in range(len(columns)):
            table[(0, i)].set_facecolor(self.colors["primary"])
            table[(0, i)].set_text_props(weight="bold", color="white")

        # Alternar colores de filas
        for i in range(1, len(source_stats) + 1):
            color = self.colors["light"] if i % 2 == 0 else "white"
            for j in range(len(columns)):
                table[(i, j)].set_facecolor(color)

        ax.set_title("Análisis Detallado por Fuente", fontweight="bold", pad=20)

    def _plot_advanced_metrics(self, ax):
        """Métricas avanzadas del proceso."""
        ax.axis("off")
        ax.set_title("Métricas Avanzadas", fontweight="bold", fontsize=11)

        metrics = []

        # Eficiencia de candidatos
        candidates = self.metrics.get("candidates_found", 0)
        pairs_scored = self.metrics.get("pairs_scored", 0)
        if candidates > 0:
            efficiency = pairs_scored / candidates
            metrics.append(f"🎯 Eficiencia candidatos: {efficiency:.1%}")

        # Clusters creados
        clusters = self.metrics.get("clusters_created", 0)
        if clusters > 0:
            metrics.append(f"🔷 Clusters creados: {clusters:,}")

        # Memoria máxima
        max_memory = self.metrics.get("max_memory_gb", 0)
        if max_memory > 0:
            metrics.append(f"💾 Memoria máxima: {max_memory:.1f} GB")

        # Velocidad por fase (tiempos reales de L2 y L3, F1.6)
        phase_speeds = []
        tiempos = tiempos_por_fase(self.metrics)
        if candidates > 0 and tiempos.get("L2_lsh_candidates", 0) > 0:
            speed = candidates / tiempos["L2_lsh_candidates"]
            phase_speeds.append(f"LSH: {speed:.0f} cand/s")

        if pairs_scored > 0 and tiempos.get("L3_scoring", 0) > 0:
            speed = pairs_scored / tiempos["L3_scoring"]
            phase_speeds.append(f"Scoring: {speed:.0f} pares/s")

        if phase_speeds:
            metrics.append(f"⚡ Velocidad: {' | '.join(phase_speeds)}")

        # Mostrar métricas
        # v0.7.1 (Tarea 1.4): sanitizar emojis antes del render matplotlib.
        metrics_text = "\n\n".join(metrics) if metrics else "No hay métricas avanzadas disponibles"
        metrics_text = _strip_emojis(metrics_text)
        ax.text(
            0.5,
            0.5,
            metrics_text,
            ha="center",
            va="center",
            fontsize=10,
            transform=ax.transAxes,
            bbox=dict(boxstyle="round,pad=0.5", facecolor=self.colors["light"], alpha=0.8),
        )

    def _create_footer(self, fig, gs_area):
        """Crea pie de página con información adicional."""
        ax = fig.add_subplot(gs_area)
        ax.axis("off")

        # Información del sistema
        footer_items = []

        # Versión y configuración
        version = "4.0"
        profile = self.config.get("profile", "standard")
        footer_items.append(f"Dashboard v{version} | Perfil: {profile}")

        # Archivos generados
        output_dir = self.config.get("output_directory", "resultados")
        footer_items.append(f"Resultados en: {output_dir}")

        # Timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        footer_items.append(f"Generado: {timestamp}")

        # Mostrar footer
        footer_text = " | ".join(footer_items)
        ax.text(
            0.5,
            0.5,
            footer_text,
            ha="center",
            va="center",
            fontsize=10,
            style="italic",
            alpha=0.7,
            transform=ax.transAxes,
        )

    def _show_no_data_message(self, ax, message: str):
        """Muestra mensaje cuando no hay datos disponibles."""
        ax.clear()
        ax.axis("off")
        ax.text(
            0.5,
            0.5,
            message,
            ha="center",
            va="center",
            fontsize=12,
            style="italic",
            color=self.colors["muted"],
            bbox=dict(boxstyle="round,pad=0.5", facecolor=self.colors["light"], alpha=0.5),
            transform=ax.transAxes,
        )

    def _save_figure(self, fig, output_path: str, format: str, dpi: int):
        """Guarda la figura con configuración óptima."""
        try:
            fig.savefig(
                output_path,
                format=format,
                dpi=dpi,
                bbox_inches="tight",
                facecolor="white",
                edgecolor="none",
                pad_inches=0.1,
            )
            plt.close(fig)
        except Exception as e:
            self.logger.error(f"Error guardando figura: {e}")
            # Intento con configuración más simple
            try:
                fig.savefig(output_path, dpi=150)
                plt.close(fig)
            except:
                raise

    def _generate_minimal_dashboard(self, output_path: str, format: str, dpi: int):
        """Genera dashboard mínimo cuando no hay datos."""
        fig, ax = plt.subplots(figsize=(12, 8), facecolor="white")
        ax.axis("off")

        # Mensaje principal
        ax.text(
            0.5,
            0.6,
            "DASHBOARD EJECUTIVO",
            fontsize=24,
            weight="bold",
            ha="center",
            va="center",
            color=self.colors["primary"],
        )
        ax.text(
            0.5,
            0.4,
            "Datos insuficientes para generar visualizaciones",
            fontsize=14,
            ha="center",
            va="center",
            color=self.colors["warning"],
        )

        # Información disponible
        info_items = []
        if self.metrics:
            info_items.append(f"Métricas disponibles: {len(self.metrics)}")
        info_items.append(f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        info_text = " | ".join(info_items)
        ax.text(
            0.5, 0.25, info_text, fontsize=10, ha="center", va="center", style="italic", alpha=0.7
        )

        self._save_figure(fig, output_path, format, dpi)
        self.logger.warning("Dashboard mínimo generado por falta de datos")
        return fig
