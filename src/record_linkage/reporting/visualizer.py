"""
reporting.visualizer — record_linkage_pipeline

Componentes:
    - class DataVisualizer  (origen: notebook celda [137])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import gc
import os
import time
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.gridspec import GridSpec

from ..utils.logger import CustomLogger
from ._sqlite import open_readonly_sqlite, quote_existing_table, validate_row_limit
from ._text_utils import strip_emojis as _strip_emojis


class DataVisualizer:
    """
    Generador de visualizaciones V4.0 - Versión final con todas las mejoras.

    Características principales:
    - Manejo eficiente de memoria para datasets grandes
    - Soporte para datos en memoria y archivos (SQLite, Parquet, CSV)
    - Generación robusta de visualizaciones con fallback elegante
    - Estilos modernos y profesionales
    - Detección automática de datos disponibles
    - Optimización para Google Colab
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
        Inicializa el visualizador con configuración robusta.

        Args:
            correlative_data: DataFrame o ruta a archivo con tabla correlativa
            golden_records_data: DataFrame o ruta a archivo con golden records
            metrics: Diccionario con métricas del proceso
            pipeline_start_time: Timestamp de inicio del pipeline
            style: Estilo de matplotlib a usar
            config: Configuración adicional
        """
        self.metrics = metrics or {}
        self.pipeline_start_time = pipeline_start_time
        self.config = config or {}
        self.logger = CustomLogger("DataVisualizer")

        # Límites de memoria
        self.sample_size = self.config.get("viz_sample_size", 50000)
        self.chunk_size = self.config.get("viz_chunk_size", 10000)

        # Logging de diagnóstico
        self.logger.info("=" * 60)
        self.logger.info("Inicializando DataVisualizer V4.0 - VERSIÓN FINAL")
        self.logger.info(f"Tipo correlative_data: {type(correlative_data)}")
        self.logger.info(f"Tipo golden_records_data: {type(golden_records_data)}")
        self.logger.info(f"Métricas disponibles: {list(metrics.keys())[:10]}...")
        self.logger.info(f"Límite de muestra: {self.sample_size:,} registros")
        self.logger.info("=" * 60)

        # Guardar referencias
        self.correlative_data_ref = correlative_data
        self.golden_records_data_ref = golden_records_data

        # Cargar muestras optimizadas para visualización
        self.correlative_table = self._load_visualization_data(
            correlative_data, "correlative_table"
        )
        self.golden_records = self._load_visualization_data(golden_records_data, "golden_records")

        # Configurar estilo y colores
        self._setup_style(style)
        self.colors = self._setup_color_palette()

        # Validar datos cargados
        self._validate_data()

        self.logger.info(f"Datos listos - Correlativa: {len(self.correlative_table):,}")
        self.logger.info(f"Datos listos - Golden: {len(self.golden_records):,}")

    def _validate_data(self):
        """Valida que los datos tengan estructura mínima necesaria."""
        # Validar correlative
        if not self.correlative_table.empty:
            required = ["ID_GRUPO"]
            missing = set(required) - set(self.correlative_table.columns)
            if missing:
                self.logger.warning(f"Columnas faltantes en correlativa: {missing}")

        # Validar golden records
        if not self.golden_records.empty:
            required = ["ID_GRUPO"]
            missing = set(required) - set(self.golden_records.columns)
            if missing:
                self.logger.warning(f"Columnas faltantes en golden: {missing}")

    def _load_visualization_data(
        self, data_ref: pd.DataFrame | str, table_name: str
    ) -> pd.DataFrame:
        """
        Carga datos optimizados para visualización con muestreo inteligente.
        """
        try:
            # DataFrame en memoria
            if isinstance(data_ref, pd.DataFrame):
                if len(data_ref) > self.sample_size:
                    self.logger.info(
                        f"Dataset grande detectado ({len(data_ref):,} registros). "
                        f"Tomando muestra de {self.sample_size:,}"
                    )
                    # Muestreo estratificado si es posible
                    if "SRC" in data_ref.columns and table_name == "correlative_table":
                        return self._stratified_sample(data_ref, "SRC")
                    # Para golden records, priorizar casos de baja confianza
                    elif "CONFIDENCE_SCORE" in data_ref.columns and table_name == "golden_records":
                        return self._weighted_sample(data_ref, "CONFIDENCE_SCORE")
                    else:
                        return data_ref.sample(n=self.sample_size, random_state=42)
                return data_ref

            # Archivo
            if isinstance(data_ref, str) and os.path.exists(data_ref):
                self.logger.info(f"Cargando desde archivo: {os.path.basename(data_ref)}")

                # SQLite
                if data_ref.endswith(".db"):
                    return self._load_from_sqlite(data_ref, table_name)

                # Parquet
                elif data_ref.endswith(".parquet"):
                    df = pd.read_parquet(data_ref)
                    return self._apply_sample_limit(df, table_name)

                # CSV
                elif data_ref.endswith((".csv", ".csv.gz", ".csv.zip")):
                    # Para archivos grandes, usar chunks
                    if os.path.getsize(data_ref) > 50 * 1024 * 1024:  # > 50MB
                        return self._load_csv_chunked(data_ref)
                    df = pd.read_csv(data_ref)
                    return self._apply_sample_limit(df, table_name)

                # HDF5
                elif data_ref.endswith(".h5"):
                    df = pd.read_hdf(data_ref, key=table_name)
                    return self._apply_sample_limit(df, table_name)

        except Exception as e:
            self.logger.error(f"Error cargando datos para visualización: {e!s}")
            self.logger.debug("Stack trace:", exc_info=True)

        return pd.DataFrame()

    def _load_from_sqlite(self, db_path: str, table_name: str) -> pd.DataFrame:
        """Carga muestra optimizada desde SQLite."""
        try:
            with open_readonly_sqlite(db_path) as conn:
                quoted_table = quote_existing_table(conn, table_name)
                sample_size = validate_row_limit(self.sample_size)
                # Para visualización, queremos variedad en los datos
                if table_name == "correlative_table":
                    # Muestra que incluya todos los tipos de grupos
                    query = f"""
                    WITH group_sizes AS (
                        SELECT ID_GRUPO, COUNT(*) as size
                        FROM {quoted_table}
                        GROUP BY ID_GRUPO
                    ),
                    sampled_groups AS (
                        SELECT ID_GRUPO
                        FROM group_sizes
                        ORDER BY RANDOM()
                        LIMIT ?
                    )
                    SELECT t.*
                    FROM {quoted_table} t
                    WHERE t.ID_GRUPO IN (SELECT ID_GRUPO FROM sampled_groups)
                    LIMIT ?
                    """
                    params = (sample_size // 10, sample_size)
                else:
                    # Para golden records, incluir variedad de confidence scores
                    query = f"""
                    SELECT * FROM (
                        SELECT * FROM {quoted_table}
                        WHERE CONFIDENCE_SCORE < 0.75
                        ORDER BY RANDOM()
                        LIMIT ?
                    )
                    UNION ALL
                    SELECT * FROM (
                        SELECT * FROM {quoted_table}
                        WHERE CONFIDENCE_SCORE >= 0.75
                        ORDER BY RANDOM()
                        LIMIT ?
                    )
                    """
                    params = (sample_size // 3, sample_size * 2 // 3)

                return pd.read_sql_query(query, conn, params=params)

        except Exception as e:
            self.logger.error(f"Error en carga desde SQLite: {e!s}")
            return pd.DataFrame()

    def _stratified_sample(self, df: pd.DataFrame, stratify_col: str) -> pd.DataFrame:
        """Muestreo estratificado preservando proporciones."""
        try:
            # Calcular tamaños por estrato
            strata_sizes = df[stratify_col].value_counts()
            total_size = len(df)

            samples = []
            for stratum, size in strata_sizes.items():
                # Calcular proporción y número de muestras
                prop = size / total_size
                n_samples = max(1, int(self.sample_size * prop))

                # Obtener muestra del estrato
                stratum_df = df[df[stratify_col] == stratum]
                if len(stratum_df) <= n_samples:
                    samples.append(stratum_df)
                else:
                    samples.append(stratum_df.sample(n=n_samples, random_state=42))

            result = pd.concat(samples, ignore_index=True)
            return result.head(self.sample_size)  # Asegurar límite

        except Exception as e:
            self.logger.warning(f"Error en muestreo estratificado: {e!s}")
            return df.sample(n=min(self.sample_size, len(df)), random_state=42)

    def _weighted_sample(self, df: pd.DataFrame, weight_col: str) -> pd.DataFrame:
        """Muestreo ponderado para incluir más casos problemáticos."""
        try:
            # Invertir scores para dar más peso a casos problemáticos
            weights = 1 - df[weight_col].fillna(0.5)
            weights = weights / weights.sum()

            indices = np.random.choice(
                df.index, size=min(self.sample_size, len(df)), p=weights, replace=False
            )

            return df.loc[indices]

        except Exception as e:
            self.logger.warning(f"Error en muestreo ponderado: {e!s}")
            return df.sample(n=min(self.sample_size, len(df)), random_state=42)

    def _load_csv_chunked(self, csv_path: str) -> pd.DataFrame:
        """Carga CSV grande por chunks."""
        chunks = []
        rows_loaded = 0

        try:
            for chunk in pd.read_csv(csv_path, chunksize=self.chunk_size):
                # Tomar muestra del chunk
                if len(chunk) > self.chunk_size // 10:
                    chunk = chunk.sample(n=self.chunk_size // 10, random_state=42)

                chunks.append(chunk)
                rows_loaded += len(chunk)

                if rows_loaded >= self.sample_size:
                    break

            if chunks:
                return pd.concat(chunks, ignore_index=True).head(self.sample_size)

        except Exception as e:
            self.logger.error(f"Error cargando CSV por chunks: {e!s}")

        return pd.DataFrame()

    def _apply_sample_limit(self, df: pd.DataFrame, table_name: str) -> pd.DataFrame:
        """Aplica límite de muestra con lógica específica por tabla."""
        if len(df) <= self.sample_size:
            return df

        self.logger.info(f"Aplicando límite de muestra a {table_name}")

        if table_name == "correlative_table" and "SRC" in df.columns:
            return self._stratified_sample(df, "SRC")
        elif table_name == "golden_records" and "CONFIDENCE_SCORE" in df.columns:
            return self._weighted_sample(df, "CONFIDENCE_SCORE")
        else:
            return df.sample(n=self.sample_size, random_state=42)

    def _setup_style(self, style: str):
        """Configura el estilo de matplotlib de forma robusta."""
        try:
            # Resetear configuración
            plt.rcdefaults()

            # Intentar aplicar estilo solicitado
            available_styles = plt.style.available

            if style in available_styles:
                plt.style.use(style)
            elif "seaborn" in available_styles:
                plt.style.use("seaborn")
            elif "seaborn-v0_8" in available_styles:
                plt.style.use("seaborn-v0_8")
            else:
                plt.style.use("default")

            # Configuración adicional para mejor calidad
            plt.rcParams.update(
                {
                    "figure.facecolor": "white",
                    "axes.facecolor": "white",
                    "axes.edgecolor": "#333333",
                    "axes.labelcolor": "#333333",
                    "text.color": "#333333",
                    "xtick.color": "#333333",
                    "ytick.color": "#333333",
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
                    "savefig.facecolor": "white",
                }
            )

        except Exception as e:
            self.logger.warning(f"Error configurando estilo: {e!s}")
            plt.style.use("default")

    def _setup_color_palette(self) -> dict[str, str]:
        """Define paleta de colores profesional y accesible."""
        return {
            "primary": "#2E86AB",  # Azul profesional
            "secondary": "#A23B72",  # Morado/Rosa
            "success": "#4CAF50",  # Verde éxito
            "warning": "#FF9800",  # Naranja advertencia
            "danger": "#F44336",  # Rojo peligro
            "info": "#2196F3",  # Azul información
            "light": "#F5F5F5",  # Gris muy claro
            "dark": "#212121",  # Gris muy oscuro
            "muted": "#6C757D",  # Gris medio
            # Colores para gráficos
            "chart1": "#1f77b4",  # Azul
            "chart2": "#ff7f0e",  # Naranja
            "chart3": "#2ca02c",  # Verde
            "chart4": "#d62728",  # Rojo
            "chart5": "#9467bd",  # Morado
            "chart6": "#8c564b",  # Marrón
            "chart7": "#e377c2",  # Rosa
            "chart8": "#7f7f7f",  # Gris
        }

    def save_all_visualizations(self, output_dir: str) -> dict[str, str]:
        """
        Genera y guarda todas las visualizaciones disponibles.
        Retorna diccionario con rutas de archivos generados.
        """
        os.makedirs(output_dir, exist_ok=True)
        self.logger.info(f"Generando visualizaciones en: {output_dir}")

        # Lista de visualizaciones con metadata
        visualizations = [
            {
                "filename": "confidence_distribution.png",
                "function": self.plot_confidence_distribution,
                "required_data": "golden_records",
                "required_cols": ["CONFIDENCE_SCORE"],
            },
            {
                "filename": "linkage_overview.png",
                "function": self.plot_linkage_overview,
                "required_data": None,  # Usa métricas
                "required_cols": [],
            },
            {
                "filename": "source_comparison.png",
                "function": self.plot_source_comparison,
                "required_data": "correlative_table",
                "required_cols": ["SRC"],
            },
            {
                "filename": "quality_heatmap.png",
                "function": self.plot_quality_heatmap,
                "required_data": "both",
                "required_cols": ["ID_GRUPO", "SRC", "CONFIDENCE_SCORE"],
            },
            {
                "filename": "group_size_analysis.png",
                "function": self.plot_group_size_analysis,
                "required_data": "correlative_table",
                "required_cols": ["ID_GRUPO"],
            },
            {
                "filename": "performance_timeline.png",
                "function": self.plot_performance_timeline,
                "required_data": None,  # Usa métricas
                "required_cols": [],
            },
        ]

        generated_files = {}
        successful = 0
        failed = 0
        skipped = 0

        for viz_config in visualizations:
            try:
                # Verificar si tenemos los datos necesarios
                if not self._has_required_data(viz_config):
                    self.logger.info(f"⏭️  {viz_config['filename']}: Omitido (datos insuficientes)")
                    skipped += 1
                    continue

                self.logger.info(f"Generando: {viz_config['filename']}")

                # Generar visualización
                fig = viz_config["function"]()

                if fig is not None:
                    # Guardar
                    filepath = os.path.join(output_dir, viz_config["filename"])
                    fig.savefig(
                        filepath, dpi=300, bbox_inches="tight", facecolor="white", edgecolor="none"
                    )
                    plt.close(fig)

                    generated_files[viz_config["filename"]] = filepath
                    successful += 1
                    self.logger.info(f"✓ Guardado: {viz_config['filename']}")
                else:
                    self.logger.warning(f"✗ Sin datos para: {viz_config['filename']}")
                    failed += 1

            except Exception as e:
                failed += 1
                self.logger.error(f"✗ Error en {viz_config['filename']}: {e!s}")
                self.logger.debug("Stack trace:", exc_info=True)
                plt.close("all")  # Limpiar cualquier figura abierta

        # Liberar memoria
        gc.collect()

        self.logger.info(
            f"Visualizaciones completadas: {successful} exitosas, "
            f"{failed} fallidas, {skipped} omitidas"
        )

        return generated_files

    def _has_required_data(self, viz_config: dict) -> bool:
        """Verifica si tenemos los datos necesarios para una visualización."""
        required_data = viz_config["required_data"]
        required_cols = viz_config["required_cols"]

        if required_data is None:
            return True  # Solo usa métricas

        if required_data == "golden_records":
            if self.golden_records.empty:
                return False
            return all(col in self.golden_records.columns for col in required_cols)

        elif required_data == "correlative_table":
            if self.correlative_table.empty:
                return False
            return all(col in self.correlative_table.columns for col in required_cols)

        elif required_data == "both":
            if self.golden_records.empty or self.correlative_table.empty:
                return False
            # Verificar columnas en ambos datasets
            golden_cols = [col for col in required_cols if col in ["CONFIDENCE_SCORE"]]
            corr_cols = [col for col in required_cols if col in ["ID_GRUPO", "SRC"]]

            return all(col in self.golden_records.columns for col in golden_cols) and all(
                col in self.correlative_table.columns for col in corr_cols
            )

        return False

    def plot_confidence_distribution(self) -> plt.Figure | None:
        """
        Visualiza distribución de confidence scores con análisis detallado.
        """
        try:
            if self.golden_records.empty or "CONFIDENCE_SCORE" not in self.golden_records.columns:
                self.logger.warning("No hay datos de CONFIDENCE_SCORE")
                return None

            scores = self.golden_records["CONFIDENCE_SCORE"].dropna()
            if scores.empty:
                return None

            # Crear figura con subplots
            fig, axes = plt.subplots(2, 2, figsize=(15, 10))
            fig.suptitle("Análisis de Confidence Scores", fontsize=16, fontweight="bold")

            # 1. Histograma con KDE
            ax1 = axes[0, 0]
            ax1.hist(
                scores,
                bins=30,
                density=True,
                alpha=0.7,
                color=self.colors["primary"],
                edgecolor="white",
                linewidth=0.5,
            )

            # Agregar KDE si hay suficientes datos
            if len(scores) > 10:
                scores.plot.kde(ax=ax1, color=self.colors["danger"], linewidth=2)

            # Líneas de referencia
            ax1.axvline(
                scores.mean(),
                color=self.colors["danger"],
                linestyle="--",
                linewidth=2,
                label=f"Media: {scores.mean():.3f}",
            )
            ax1.axvline(
                scores.median(),
                color=self.colors["warning"],
                linestyle=":",
                linewidth=2,
                label=f"Mediana: {scores.median():.3f}",
            )
            ax1.axvline(
                0.75,
                color=self.colors["success"],
                linestyle="--",
                linewidth=2,
                alpha=0.5,
                label="Umbral: 0.75",
            )

            ax1.set_xlabel("Confidence Score")
            ax1.set_ylabel("Densidad")
            ax1.set_title("Distribución de Confidence Scores")
            ax1.legend()
            ax1.grid(True, alpha=0.3)

            # 2. Box plot por fuente (si está disponible)
            ax2 = axes[0, 1]
            if "PRIMARY_SOURCE" in self.golden_records.columns:
                plot_data = self.golden_records[["CONFIDENCE_SCORE", "PRIMARY_SOURCE"]].dropna()
                if not plot_data.empty and plot_data["PRIMARY_SOURCE"].nunique() > 1:
                    # Ordenar fuentes por mediana de confidence
                    source_order = (
                        plot_data.groupby("PRIMARY_SOURCE")["CONFIDENCE_SCORE"]
                        .median()
                        .sort_values()
                        .index
                    )

                    sns.boxplot(
                        data=plot_data,
                        y="PRIMARY_SOURCE",
                        x="CONFIDENCE_SCORE",
                        order=source_order,
                        palette="viridis",
                        ax=ax2,
                    )
                    ax2.axvline(0.75, color=self.colors["warning"], linestyle="--", alpha=0.5)
                    ax2.set_xlabel("Confidence Score")
                    ax2.set_ylabel("Fuente Primaria")
                    ax2.set_title("Confidence por Fuente")
                else:
                    self._show_no_data_message(ax2, "Datos insuficientes por fuente")
            else:
                self._show_no_data_message(ax2, "PRIMARY_SOURCE no disponible")

            # 3. Distribución acumulada
            ax3 = axes[1, 0]
            sorted_scores = np.sort(scores)
            cumulative = np.arange(1, len(sorted_scores) + 1) / len(sorted_scores)

            ax3.plot(sorted_scores, cumulative, color=self.colors["primary"], linewidth=2)
            ax3.axvline(
                0.75, color=self.colors["warning"], linestyle="--", alpha=0.5, label="Umbral: 0.75"
            )
            ax3.axhline(
                0.5, color=self.colors["muted"], linestyle=":", alpha=0.5, label="50% de los datos"
            )

            # Agregar áreas coloreadas
            ax3.fill_between(
                sorted_scores[sorted_scores < 0.75],
                cumulative[sorted_scores < 0.75],
                alpha=0.2,
                color=self.colors["danger"],
                label=f"Bajo umbral: {(scores < 0.75).sum():,}",
            )

            ax3.set_xlabel("Confidence Score")
            ax3.set_ylabel("Proporción Acumulada")
            ax3.set_title("Distribución Acumulada")
            ax3.legend()
            ax3.grid(True, alpha=0.3)

            # 4. Estadísticas resumen
            ax4 = axes[1, 1]
            ax4.axis("off")

            # Calcular estadísticas
            stats_text = f"""ESTADÍSTICAS DE CONFIDENCE SCORES

Total grupos: {len(scores):,}
Media: {scores.mean():.4f}
Mediana: {scores.median():.4f}
Desv. Estándar: {scores.std():.4f}
Mínimo: {scores.min():.4f}
Máximo: {scores.max():.4f}

Percentiles:
  25%: {scores.quantile(0.25):.4f}
  50%: {scores.quantile(0.50):.4f}
  75%: {scores.quantile(0.75):.4f}
  95%: {scores.quantile(0.95):.4f}

Distribución por umbral:
  Alta confianza (≥0.9): {(scores >= 0.9).sum():,} ({(scores >= 0.9).sum() / len(scores) * 100:.1f}%)
  Confianza media (0.75-0.9): {((scores >= 0.75) & (scores < 0.9)).sum():,} ({((scores >= 0.75) & (scores < 0.9)).sum() / len(scores) * 100:.1f}%)
  Revisión requerida (<0.75): {(scores < 0.75).sum():,} ({(scores < 0.75).sum() / len(scores) * 100:.1f}%)
"""

            ax4.text(
                0.1,
                0.9,
                stats_text,
                transform=ax4.transAxes,
                fontsize=10,
                verticalalignment="top",
                fontfamily="monospace",
                bbox=dict(boxstyle="round", facecolor=self.colors["light"], alpha=0.8),
            )

            plt.tight_layout()
            return fig

        except Exception as e:
            self.logger.error(f"Error en plot_confidence_distribution: {e!s}")
            return None

    def plot_linkage_overview(self) -> plt.Figure | None:
        """
        Genera dashboard general del proceso de linkage.
        """
        try:
            fig = plt.figure(figsize=(16, 10))
            gs = GridSpec(3, 3, figure=fig, hspace=0.3, wspace=0.3)

            # Panel 1: Métricas principales (ocupa 2 columnas)
            ax1 = fig.add_subplot(gs[0, :2])
            self._plot_main_metrics(ax1)

            # Panel 2: Flujo del proceso
            ax2 = fig.add_subplot(gs[0, 2])
            self._plot_process_flow(ax2)

            # Panel 3: Distribución de tamaños de grupo
            ax3 = fig.add_subplot(gs[1, 0])
            self._plot_group_size_distribution(ax3)

            # Panel 4: Distribución por fuente
            ax4 = fig.add_subplot(gs[1, 1])
            self._plot_source_distribution(ax4)

            # Panel 5: Eficiencia del proceso
            ax5 = fig.add_subplot(gs[1, 2])
            self._plot_efficiency_metrics(ax5)

            # Panel 6: Resumen de calidad (ocupa toda la fila)
            ax6 = fig.add_subplot(gs[2, :])
            self._plot_quality_summary(ax6)

            plt.suptitle("Dashboard de Record Linkage", fontsize=16, fontweight="bold")

            return fig

        except Exception as e:
            self.logger.error(f"Error en plot_linkage_overview: {e!s}")
            return None

    def _plot_main_metrics(self, ax):
        """Panel de métricas principales."""
        ax.axis("off")

        # Calcular tiempo total
        exec_time = self.metrics.get("execution_time", 0)
        if exec_time == 0 and self.pipeline_start_time:
            exec_time = time.time() - self.pipeline_start_time

        # Formato de tiempo
        if exec_time < 60:
            time_str = f"{exec_time:.1f} segundos"
        elif exec_time < 3600:
            time_str = f"{exec_time / 60:.1f} minutos"
        else:
            time_str = f"{exec_time / 3600:.1f} horas"

        # Texto de métricas
        # v0.7.1 (Tarea 1.4): los emojis se mantienen en el f-string para
        # legibilidad del código fuente, pero se eliminan antes del render
        # porque las fuentes de Linux no los renderizan.
        metrics_text = f"""MÉTRICAS PRINCIPALES DE RECORD LINKAGE

📊 Total Registros: {self.metrics.get("total_records", 0):,}
🎯 Entidades Únicas: {self.metrics.get("unique_groups", 0):,}
🔗 Tasa de Linkage: {self.metrics.get("linkage_rate", 0):.2%}
📉 Reducción: {self.metrics.get("reduction_rate", 0):.2%}
⏱️ Tiempo Total: {time_str}
⚡ Throughput: {self.metrics.get("total_records", 0) / (exec_time + 1):.0f} rec/s
"""
        metrics_text = _strip_emojis(metrics_text)

        ax.text(
            0.5,
            0.5,
            metrics_text,
            fontsize=14,
            ha="center",
            va="center",
            transform=ax.transAxes,
            bbox=dict(
                boxstyle="round,pad=1",
                facecolor=self.colors["light"],
                edgecolor=self.colors["primary"],
                linewidth=2,
            ),
        )

    def _plot_process_flow(self, ax):
        """Flujo del proceso de linkage."""
        ax.axis("off")

        # Datos del flujo
        stages = ["Entrada", "Candidatos", "Scoring", "Grupos"]
        values = [
            self.metrics.get("total_records", 0),
            self.metrics.get("candidates_found", 0),
            self.metrics.get("pairs_scored", 0),
            self.metrics.get("unique_groups", 0),
        ]

        # Crear diagrama de flujo simple
        y_positions = np.linspace(0.8, 0.2, len(stages))

        for i, (stage, value) in enumerate(zip(stages, values, strict=False)):
            # Caja
            rect = plt.Rectangle(
                (0.3, y_positions[i] - 0.05),
                0.4,
                0.1,
                facecolor=self.colors["info"],
                alpha=0.7,
                edgecolor=self.colors["dark"],
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
            )

            # Flecha
            if i < len(stages) - 1:
                ax.arrow(
                    0.5,
                    y_positions[i] - 0.05,
                    0,
                    -0.08,
                    head_width=0.03,
                    head_length=0.02,
                    fc=self.colors["dark"],
                    ec=self.colors["dark"],
                )

        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_title("Flujo del Proceso", fontsize=12, fontweight="bold")

    def _plot_group_size_distribution(self, ax):
        """Distribución de tamaños de grupo."""
        if self.correlative_table.empty or "ID_GRUPO" not in self.correlative_table.columns:
            self._show_no_data_message(ax, "Sin datos de grupos")
            return

        # Calcular tamaños
        group_sizes = self.correlative_table.groupby("ID_GRUPO").size()

        # Categorizar
        bins = [0, 1, 2, 5, 10, 50, float("inf")]
        labels = ["1", "2", "3-5", "6-10", "11-50", ">50"]
        size_dist = pd.cut(group_sizes, bins=bins, labels=labels).value_counts().sort_index()

        # Gráfico
        colors = sns.color_palette("coolwarm", len(size_dist))
        bars = ax.bar(range(len(size_dist)), size_dist.values, color=colors)

        # Configuración
        ax.set_xticks(range(len(size_dist)))
        ax.set_xticklabels(size_dist.index, rotation=0)
        ax.set_xlabel("Tamaño del Grupo")
        ax.set_ylabel("Cantidad")
        ax.set_title("Distribución de Tamaños", fontweight="bold")
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
                    fontsize=9,
                )

    def _plot_source_distribution(self, ax):
        """Distribución por fuente."""
        if self.correlative_table.empty or "SRC" not in self.correlative_table.columns:
            self._show_no_data_message(ax, "Sin datos de fuentes")
            return

        source_counts = self.correlative_table["SRC"].value_counts()

        # Limitar a top 10 si hay muchas fuentes
        if len(source_counts) > 10:
            source_counts = source_counts.head(10)
            other_count = self.correlative_table["SRC"].value_counts()[10:].sum()
            if other_count > 0:
                source_counts["Otros"] = other_count

        # Gráfico de dona
        colors = sns.color_palette("husl", len(source_counts))
        _wedges, _texts, autotexts = ax.pie(
            source_counts.values,
            labels=source_counts.index,
            autopct="%1.1f%%",
            startangle=90,
            colors=colors,
            pctdistance=0.85,
        )

        # Hacer dona
        centre_circle = plt.Circle((0, 0), 0.70, fc="white")
        ax.add_artist(centre_circle)

        # Mejorar texto
        for autotext in autotexts:
            autotext.set_color("white")
            autotext.set_fontsize(9)
            autotext.set_weight("bold")

        ax.set_title("Distribución por Fuente", fontweight="bold")

    def _plot_efficiency_metrics(self, ax):
        """Métricas de eficiencia."""
        metrics_data = []

        # Eficiencia de candidatos
        candidates = self.metrics.get("candidates_found", 0)
        pairs_scored = self.metrics.get("pairs_scored", 0)
        if candidates > 0:
            efficiency = pairs_scored / candidates
            metrics_data.append(("Eficiencia\nCandidatos", efficiency))

        # Tasa de linkage
        linkage_rate = self.metrics.get("linkage_rate", 0)
        metrics_data.append(("Tasa de\nLinkage", linkage_rate))

        # Reducción
        reduction_rate = self.metrics.get("reduction_rate", 0)
        metrics_data.append(("Tasa de\nReducción", reduction_rate))

        if not metrics_data:
            self._show_no_data_message(ax, "Sin métricas de eficiencia")
            return

        # Gráfico de barras
        labels = [m[0] for m in metrics_data]
        values = [m[1] for m in metrics_data]

        bars = ax.bar(
            labels,
            values,
            color=[self.colors["success"], self.colors["info"], self.colors["warning"]],
        )

        # Formato porcentual en barras
        for bar, val in zip(bars, values, strict=False):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.01,
                f"{val:.1%}",
                ha="center",
                va="bottom",
                fontsize=10,
                fontweight="bold",
            )

        ax.set_ylim(0, max(values) * 1.2 if values else 1)
        ax.set_ylabel("Porcentaje")
        ax.set_title("Métricas de Eficiencia", fontweight="bold")
        ax.grid(True, alpha=0.3, axis="y")

    def _plot_quality_summary(self, ax):
        """Resumen de calidad."""
        ax.axis("off")

        # Recopilar información de calidad
        quality_items = []

        # Confidence promedio
        if not self.golden_records.empty and "CONFIDENCE_SCORE" in self.golden_records.columns:
            avg_conf = self.golden_records["CONFIDENCE_SCORE"].mean()
            quality_items.append(f"⭐ Confidence promedio: {avg_conf:.3f}")

            # Casos para revisión
            review_cases = (self.golden_records["CONFIDENCE_SCORE"] < 0.75).sum()
            quality_items.append(f"🔍 Casos para revisión: {review_cases:,}")

        # Multi-fuente
        multi_source = self.metrics.get("multi_source_groups", 0)
        quality_items.append(f"🔗 Grupos multi-fuente: {multi_source:,}")

        # Mostrar como lista
        # v0.7.4 (deuda 0.8.1): sanitizar emojis antes del render — esta era la
        # fuente del UserWarning 'Glyph 11088 (WHITE MEDIUM STAR) missing' que
        # quedó sin cerrar en el Sprint 0.8.1.
        quality_text = _strip_emojis("RESUMEN DE CALIDAD\n\n" + "\n".join(quality_items))

        ax.text(
            0.5,
            0.5,
            quality_text,
            fontsize=12,
            ha="center",
            va="center",
            transform=ax.transAxes,
            bbox=dict(boxstyle="round,pad=0.5", facecolor=self.colors["light"], alpha=0.8),
        )

    def plot_source_comparison(self) -> plt.Figure | None:
        """
        Compara métricas detalladas entre fuentes.
        """
        try:
            if self.correlative_table.empty or "SRC" not in self.correlative_table.columns:
                return None

            # Calcular métricas por fuente
            source_stats = (
                self.correlative_table.groupby("SRC")
                .agg(Total_Registros=("ID_GRUPO", "size"), Grupos_Unicos=("ID_GRUPO", "nunique"))
                .reset_index()
            )

            if source_stats.empty:
                return None

            # Calcular métricas adicionales
            source_stats["Registros_por_Grupo"] = (
                source_stats["Total_Registros"] / source_stats["Grupos_Unicos"]
            ).round(2)

            # Crear visualización
            fig, axes = plt.subplots(2, 2, figsize=(15, 10))
            fig.suptitle("Análisis Comparativo de Fuentes", fontsize=16, fontweight="bold")

            # 1. Total de registros
            ax1 = axes[0, 0]
            source_stats_sorted = source_stats.sort_values("Total_Registros", ascending=True)

            # Limitar a top 15 fuentes
            if len(source_stats_sorted) > 15:
                source_stats_sorted = source_stats_sorted.tail(15)

            bars1 = ax1.barh(
                source_stats_sorted["SRC"],
                source_stats_sorted["Total_Registros"],
                color=self.colors["primary"],
                alpha=0.7,
            )

            # Valores en barras
            for bar, val in zip(bars1, source_stats_sorted["Total_Registros"], strict=False):
                ax1.text(
                    val + 0.01 * source_stats_sorted["Total_Registros"].max(),
                    bar.get_y() + bar.get_height() / 2,
                    f"{val:,}",
                    va="center",
                    fontsize=9,
                )

            ax1.set_xlabel("Total Registros")
            ax1.set_title("Registros por Fuente", fontweight="bold")
            ax1.grid(True, alpha=0.3, axis="x")

            # 2. Grupos únicos
            ax2 = axes[0, 1]
            source_stats_sorted = source_stats.sort_values("Grupos_Unicos", ascending=True)
            if len(source_stats_sorted) > 15:
                source_stats_sorted = source_stats_sorted.tail(15)

            bars2 = ax2.barh(
                source_stats_sorted["SRC"],
                source_stats_sorted["Grupos_Unicos"],
                color=self.colors["secondary"],
                alpha=0.7,
            )

            for bar, val in zip(bars2, source_stats_sorted["Grupos_Unicos"], strict=False):
                ax2.text(
                    val + 0.01 * source_stats_sorted["Grupos_Unicos"].max(),
                    bar.get_y() + bar.get_height() / 2,
                    f"{val:,}",
                    va="center",
                    fontsize=9,
                )

            ax2.set_xlabel("Grupos Únicos")
            ax2.set_title("Cobertura por Fuente", fontweight="bold")
            ax2.grid(True, alpha=0.3, axis="x")

            # 3. Registros por grupo (promedio)
            ax3 = axes[1, 0]
            source_stats_sorted = source_stats.sort_values("Registros_por_Grupo", ascending=False)
            if len(source_stats_sorted) > 15:
                source_stats_sorted = source_stats_sorted.head(15)

            bars3 = ax3.bar(
                range(len(source_stats_sorted)),
                source_stats_sorted["Registros_por_Grupo"],
                color=self.colors["warning"],
                alpha=0.7,
            )

            ax3.set_xticks(range(len(source_stats_sorted)))
            ax3.set_xticklabels(source_stats_sorted["SRC"], rotation=45, ha="right")
            ax3.set_ylabel("Promedio Registros/Grupo")
            ax3.set_title("Densidad de Registros por Fuente", fontweight="bold")
            ax3.grid(True, alpha=0.3, axis="y")

            # Valores en barras
            for bar, val in zip(bars3, source_stats_sorted["Registros_por_Grupo"], strict=False):
                ax3.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.02,
                    f"{val:.1f}",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                )

            # 4. Matriz de correlación si hay golden records
            ax4 = axes[1, 1]
            if not self.golden_records.empty and "PRIMARY_SOURCE" in self.golden_records.columns:
                # Contar cuántos golden records tiene cada fuente como primaria
                primary_counts = self.golden_records["PRIMARY_SOURCE"].value_counts()

                # Merge con estadísticas
                comparison_df = source_stats.merge(
                    primary_counts.rename("Como_Primaria"),
                    left_on="SRC",
                    right_index=True,
                    how="left",
                ).fillna(0)

                # Calcular tasa de primaria
                comparison_df["Tasa_Primaria"] = (
                    comparison_df["Como_Primaria"] / comparison_df["Grupos_Unicos"]
                ).clip(0, 1)

                # Ordenar por tasa de primaria
                comparison_df = comparison_df.sort_values("Tasa_Primaria", ascending=False)
                if len(comparison_df) > 10:
                    comparison_df = comparison_df.head(10)

                # Gráfico
                bars4 = ax4.bar(
                    range(len(comparison_df)),
                    comparison_df["Tasa_Primaria"],
                    color=self.colors["success"],
                    alpha=0.7,
                )

                ax4.set_xticks(range(len(comparison_df)))
                ax4.set_xticklabels(comparison_df["SRC"], rotation=45, ha="right")
                ax4.set_ylabel("Tasa como Fuente Primaria")
                ax4.set_title("Confiabilidad por Fuente", fontweight="bold")
                ax4.set_ylim(0, 1.1)
                ax4.grid(True, alpha=0.3, axis="y")

                # Valores
                for bar, val in zip(bars4, comparison_df["Tasa_Primaria"], strict=False):
                    ax4.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.01,
                        f"{val:.1%}",
                        ha="center",
                        va="bottom",
                        fontsize=9,
                    )
            else:
                self._show_no_data_message(ax4, "Sin datos de fuente primaria")

            plt.tight_layout()
            return fig

        except Exception as e:
            self.logger.error(f"Error en plot_source_comparison: {e!s}")
            return None

    def plot_quality_heatmap(self) -> plt.Figure | None:
        """
        Genera heatmap de calidad mostrando relaciones entre fuentes.
        """
        try:
            if self.golden_records.empty or self.correlative_table.empty:
                self.logger.warning("Datos insuficientes para heatmap de calidad")
                return None

            if (
                "PRIMARY_SOURCE" not in self.golden_records.columns
                or "SRC" not in self.correlative_table.columns
            ):
                self.logger.warning("Columnas de fuente no disponibles")
                return None

            # Preparar datos
            merged_df = pd.merge(
                self.correlative_table[["ID_GRUPO", "SRC"]],
                self.golden_records[["ID_GRUPO", "CONFIDENCE_SCORE"]],
                on="ID_GRUPO",
                how="inner",
            )

            if merged_df.empty:
                return None

            # Crear tabla de pares de fuentes
            from itertools import combinations

            source_pairs = (
                merged_df.groupby("ID_GRUPO")["SRC"]
                .apply(
                    lambda x: (
                        list(combinations(sorted(x.unique()), 2)) if len(x.unique()) > 1 else []
                    )
                )
                .explode()
                .dropna()
            )

            if source_pairs.empty:
                self.logger.warning("No se encontraron grupos compartidos entre fuentes")
                return self._create_simple_source_matrix()

            # Unir con scores
            pair_scores = source_pairs.to_frame(name="source_pair").join(
                merged_df.groupby("ID_GRUPO")["CONFIDENCE_SCORE"].mean()
            )

            # Calcular score promedio por par
            avg_scores = pair_scores.groupby("source_pair")["CONFIDENCE_SCORE"].agg(
                ["mean", "count"]
            )

            # Crear matriz
            sources = sorted(self.correlative_table["SRC"].unique())
            n_sources = len(sources)

            # Limitar número de fuentes si son muchas
            if n_sources > 20:
                # Tomar las fuentes más frecuentes
                top_sources = self.correlative_table["SRC"].value_counts().head(20).index
                sources = sorted(top_sources)
                n_sources = len(sources)

            # Matrices para heatmap
            score_matrix = pd.DataFrame(np.nan, index=sources, columns=sources)
            count_matrix = pd.DataFrame(0, index=sources, columns=sources)

            # Llenar matrices
            for (src1, src2), data in avg_scores.iterrows():
                if src1 in sources and src2 in sources:
                    score_matrix.loc[src1, src2] = data["mean"]
                    score_matrix.loc[src2, src1] = data["mean"]
                    count_matrix.loc[src1, src2] = data["count"]
                    count_matrix.loc[src2, src1] = data["count"]

            # Diagonal: score promedio de cada fuente
            for src in sources:
                src_scores = merged_df[merged_df["SRC"] == src]["CONFIDENCE_SCORE"]
                if not src_scores.empty:
                    score_matrix.loc[src, src] = src_scores.mean()
                    count_matrix.loc[src, src] = len(src_scores)

            # Crear visualización
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 8))

            # Heatmap 1: Scores promedio
            mask1 = score_matrix.isna()
            sns.heatmap(
                score_matrix,
                mask=mask1,
                annot=True,
                fmt=".3f",
                cmap="RdYlGn",
                center=0.75,
                vmin=0.5,
                vmax=1.0,
                square=True,
                linewidths=0.5,
                cbar_kws={"label": "Confidence Score Promedio"},
                ax=ax1,
            )
            ax1.set_title(
                "Calidad de Linkage entre Fuentes\n(Confidence Score Promedio)",
                fontsize=14,
                fontweight="bold",
            )
            ax1.set_xlabel("Fuente")
            ax1.set_ylabel("Fuente")

            # Rotar etiquetas si son muchas
            if n_sources > 10:
                ax1.set_xticklabels(ax1.get_xticklabels(), rotation=45, ha="right")
                ax1.set_yticklabels(ax1.get_yticklabels(), rotation=0)

            # Heatmap 2: Cantidad de grupos compartidos
            # Usar escala logarítmica para mejor visualización
            count_matrix_log = np.log10(count_matrix + 1)

            sns.heatmap(
                count_matrix_log,
                annot=count_matrix,
                fmt="d",
                cmap="Blues",
                square=True,
                linewidths=0.5,
                cbar_kws={"label": "Grupos Compartidos (escala log10)"},
                ax=ax2,
            )
            ax2.set_title(
                "Cantidad de Grupos Compartidos entre Fuentes", fontsize=14, fontweight="bold"
            )
            ax2.set_xlabel("Fuente")
            ax2.set_ylabel("Fuente")

            if n_sources > 10:
                ax2.set_xticklabels(ax2.get_xticklabels(), rotation=45, ha="right")
                ax2.set_yticklabels(ax2.get_yticklabels(), rotation=0)

            plt.suptitle(
                "Análisis de Calidad de Linkage entre Fuentes", fontsize=16, fontweight="bold"
            )
            plt.tight_layout()

            return fig

        except Exception as e:
            self.logger.error(f"Error en plot_quality_heatmap: {e!s}")
            return None

    def _create_simple_source_matrix(self) -> plt.Figure | None:
        """Crea matriz simple de fuentes cuando no hay grupos compartidos."""
        try:
            sources = sorted(self.correlative_table["SRC"].unique())[:15]  # Limitar a 15

            fig, ax = plt.subplots(figsize=(10, 8))

            # Crear matriz de conteos
            matrix = pd.DataFrame(0, index=sources, columns=sources)

            # Diagonal: total de registros por fuente
            for src in sources:
                count = len(self.correlative_table[self.correlative_table["SRC"] == src])
                matrix.loc[src, src] = count

            # Heatmap
            sns.heatmap(
                matrix, annot=True, fmt="d", cmap="Blues", square=True, linewidths=0.5, ax=ax
            )

            ax.set_title(
                "Registros por Fuente (sin grupos compartidos)", fontsize=14, fontweight="bold"
            )

            return fig

        except Exception as e:
            self.logger.error(f"Error creando matriz simple: {e!s}")
            return None

    def plot_group_size_analysis(self) -> plt.Figure | None:
        """
        Análisis detallado de distribución de tamaños de grupo.
        """
        try:
            if self.correlative_table.empty or "ID_GRUPO" not in self.correlative_table.columns:
                return None

            # Calcular tamaños
            group_sizes = self.correlative_table.groupby("ID_GRUPO").size()

            fig, axes = plt.subplots(2, 2, figsize=(15, 10))
            fig.suptitle("Análisis de Tamaños de Grupo", fontsize=16, fontweight="bold")

            # 1. Histograma detallado
            ax1 = axes[0, 0]

            # Limitar a grupos de tamaño razonable para el histograma
            sizes_for_hist = group_sizes[group_sizes <= 50]
            if sizes_for_hist.empty:
                # v0.12.0 (E12): con solo grupos grandes el recorte dejaba la
                # serie vacía y bins=0 rompía matplotlib ("bins must be
                # positive") — el error se tragaba y el PNG se perdía.
                sizes_for_hist = group_sizes

            ax1.hist(
                sizes_for_hist,
                bins=max(1, min(50, len(sizes_for_hist.unique()))),
                color=self.colors["primary"],
                alpha=0.7,
                edgecolor="white",
            )
            ax1.axvline(
                sizes_for_hist.mean(),
                color=self.colors["danger"],
                linestyle="--",
                linewidth=2,
                label=f"Media: {sizes_for_hist.mean():.1f}",
            )
            ax1.axvline(
                sizes_for_hist.median(),
                color=self.colors["warning"],
                linestyle=":",
                linewidth=2,
                label=f"Mediana: {sizes_for_hist.median():.1f}",
            )

            ax1.set_xlabel("Tamaño del Grupo")
            ax1.set_ylabel("Frecuencia")
            ax1.set_title("Distribución de Tamaños (≤50 registros)")
            ax1.legend()
            ax1.grid(True, alpha=0.3)

            # 2. Distribución categorizada
            ax2 = axes[0, 1]

            bins = [0, 1, 2, 3, 5, 10, 20, 50, 100, float("inf")]
            labels = ["1", "2", "3", "4-5", "6-10", "11-20", "21-50", "51-100", ">100"]
            size_categories = pd.cut(group_sizes, bins=bins, labels=labels)
            size_dist = size_categories.value_counts().sort_index()

            # Gráfico de barras con gradiente de color
            colors = plt.colormaps["viridis"](np.linspace(0.3, 0.9, len(size_dist)))
            bars = ax2.bar(range(len(size_dist)), size_dist.values, color=colors)

            ax2.set_xticks(range(len(size_dist)))
            ax2.set_xticklabels(size_dist.index, rotation=45)
            ax2.set_xlabel("Categoría de Tamaño")
            ax2.set_ylabel("Cantidad de Grupos")
            ax2.set_title("Distribución Categorizada")
            ax2.grid(True, alpha=0.3, axis="y")

            # Valores en barras
            for bar, val in zip(bars, size_dist.values, strict=False):
                if val > 0:
                    ax2.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.5,
                        f"{val:,}",
                        ha="center",
                        va="bottom",
                        fontsize=9,
                    )

            # 3. Análisis de outliers
            ax3 = axes[1, 0]

            # Box plot con outliers
            box_data = [group_sizes.values]
            bp = ax3.boxplot(box_data, vert=False, patch_artist=True, showmeans=True, meanline=True)

            # Colorear
            bp["boxes"][0].set_facecolor(self.colors["info"])
            bp["boxes"][0].set_alpha(0.7)
            bp["medians"][0].set_color(self.colors["danger"])
            bp["means"][0].set_color(self.colors["warning"])

            # Identificar outliers extremos
            q1, q3 = group_sizes.quantile([0.25, 0.75])
            iqr = q3 - q1
            outlier_threshold = q3 + 3 * iqr
            extreme_outliers = group_sizes[group_sizes > outlier_threshold]

            ax3.set_xlabel("Tamaño del Grupo")
            ax3.set_title(
                f"Análisis de Outliers ({len(extreme_outliers)} grupos > {outlier_threshold:.0f})"
            )
            ax3.grid(True, alpha=0.3, axis="x")

            # 4. Estadísticas resumen
            ax4 = axes[1, 1]
            ax4.axis("off")

            # Calcular estadísticas
            total_groups = len(group_sizes)
            total_records = group_sizes.sum()

            stats_text = f"""ESTADÍSTICAS DE TAMAÑO DE GRUPO

Total de grupos: {total_groups:,}
Total de registros: {total_records:,}

Tamaño promedio: {group_sizes.mean():.2f}
Mediana: {group_sizes.median():.0f}
Desviación estándar: {group_sizes.std():.2f}

Mínimo: {group_sizes.min()}
Máximo: {group_sizes.max()}

Percentiles:
  10%: {group_sizes.quantile(0.10):.0f}
  25%: {group_sizes.quantile(0.25):.0f}
  50%: {group_sizes.quantile(0.50):.0f}
  75%: {group_sizes.quantile(0.75):.0f}
  90%: {group_sizes.quantile(0.90):.0f}
  95%: {group_sizes.quantile(0.95):.0f}
  99%: {group_sizes.quantile(0.99):.0f}

Grupos singleton: {(group_sizes == 1).sum():,} ({(group_sizes == 1).sum() / total_groups * 100:.1f}%)
Grupos grandes (>20): {(group_sizes > 20).sum():,} ({(group_sizes > 20).sum() / total_groups * 100:.1f}%)
"""

            ax4.text(
                0.1,
                0.9,
                stats_text,
                transform=ax4.transAxes,
                fontsize=10,
                verticalalignment="top",
                fontfamily="monospace",
                bbox=dict(boxstyle="round", facecolor=self.colors["light"], alpha=0.8),
            )

            plt.tight_layout()
            return fig

        except Exception as e:
            self.logger.error(f"Error en plot_group_size_analysis: {e!s}")
            return None

    def plot_performance_timeline(self) -> plt.Figure | None:
        """
        Visualiza línea de tiempo del rendimiento del proceso.
        """
        try:
            # Recopilar tiempos de fases
            phase_times = {
                "Carga y Validación": self.metrics.get("load_validate", 0),
                "Preprocesamiento": self.metrics.get("preprocessing_time", 0),
                "Generación Candidatos": self.metrics.get("candidate_generation_time", 0),
                "Scoring": self.metrics.get("scoring_time", 0),
                "Clustering": self.metrics.get("clustering_time", 0),
                "Golden Records": self.metrics.get("golden_records_time", 0),
                "Exportación": self.metrics.get("export_time", 0),
            }

            # Filtrar fases con tiempo > 0
            phase_times = {k: v for k, v in phase_times.items() if v > 0}

            if not phase_times:
                return None

            fig, (ax1, ax2) = plt.subplots(
                2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [3, 1]}
            )

            # 1. Gráfico de Gantt
            phases = list(phase_times.keys())
            times = list(phase_times.values())

            # Calcular posiciones
            positions = []
            current_pos = 0
            for time in times:
                positions.append(current_pos)
                current_pos += time

            # Colores por fase
            colors = plt.colormaps["Set3"](np.linspace(0, 1, len(phases)))

            # Dibujar barras
            for i, (phase, time, pos) in enumerate(zip(phases, times, positions, strict=False)):
                ax1.barh(
                    i,
                    time,
                    left=pos,
                    height=0.6,
                    color=colors[i],
                    alpha=0.8,
                    edgecolor="black",
                    linewidth=1,
                )

                # Etiqueta dentro de la barra
                if time > current_pos * 0.05:  # Solo si hay espacio
                    time_str = f"{time:.1f}s" if time < 60 else f"{time / 60:.1f}m"
                    ax1.text(
                        pos + time / 2,
                        i,
                        f"{phase}\n{time_str}",
                        ha="center",
                        va="center",
                        fontsize=9,
                        fontweight="bold",
                    )

            # Configuración
            ax1.set_yticks(range(len(phases)))
            ax1.set_yticklabels([])  # Ya están en las barras
            ax1.set_xlabel("Tiempo (segundos)")
            ax1.set_title("Línea de Tiempo del Proceso", fontsize=14, fontweight="bold")
            ax1.grid(True, alpha=0.3, axis="x")

            # Línea de tiempo total
            total_time = sum(times)
            ax1.axvline(total_time, color="red", linestyle="--", linewidth=2)
            ax1.text(
                total_time,
                len(phases),
                f"Total: {total_time:.1f}s",
                ha="right",
                va="bottom",
                color="red",
                fontweight="bold",
            )

            # 2. Gráfico de porcentajes
            percentages = [t / total_time * 100 for t in times]

            # Ordenar por porcentaje
            sorted_data = sorted(
                zip(phases, percentages, colors, strict=False), key=lambda x: x[1], reverse=True
            )

            phases_sorted = [d[0] for d in sorted_data]
            percentages_sorted = [d[1] for d in sorted_data]
            colors_sorted = [d[2] for d in sorted_data]

            bars = ax2.bar(
                range(len(phases_sorted)),
                percentages_sorted,
                color=colors_sorted,
                alpha=0.8,
                edgecolor="black",
                linewidth=1,
            )

            # Valores en barras
            for bar, pct in zip(bars, percentages_sorted, strict=False):
                ax2.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.5,
                    f"{pct:.1f}%",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                )

            ax2.set_xticks(range(len(phases_sorted)))
            ax2.set_xticklabels(phases_sorted, rotation=45, ha="right")
            ax2.set_ylabel("Porcentaje del Tiempo Total")
            ax2.set_title("Distribución del Tiempo por Fase", fontweight="bold")
            ax2.grid(True, alpha=0.3, axis="y")
            ax2.set_ylim(0, max(percentages_sorted) * 1.15)

            # Agregar métricas de rendimiento
            total_records = self.metrics.get("total_records", 0)
            if total_records > 0 and total_time > 0:
                throughput = total_records / total_time
                fig.text(
                    0.99,
                    0.01,
                    f"Throughput: {throughput:.0f} registros/segundo",
                    ha="right",
                    va="bottom",
                    fontsize=10,
                    style="italic",
                )

            plt.tight_layout()
            return fig

        except Exception as e:
            self.logger.error(f"Error en plot_performance_timeline: {e!s}")
            return None

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
            fontsize=11,
            style="italic",
            color=self.colors["dark"],
            alpha=0.7,
            bbox=dict(
                boxstyle="round,pad=0.5",
                facecolor=self.colors["light"],
                edgecolor=self.colors["muted"],
                alpha=0.5,
            ),
        )
