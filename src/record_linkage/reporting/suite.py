"""
reporting.suite — record_linkage_pipeline

Componentes:
    - class EnhancedReportingSuite  (origen: notebook celda [139])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import contextlib
import gc
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from ..exporters._spreadsheet import prepare_spreadsheet_data, safe_sheet_name
from ..pipeline.errores import MuestreoReportesError
from ..utils.logger import CustomLogger
from ._fases import MENSAJE_SIN_TIEMPOS, etiquetar, formatear_segundos, tiempos_por_fase
from ._muestreo import muestra_estratificada
from ._sqlite import open_readonly_sqlite, quote_existing_table, validate_row_limit


class EnhancedReportingSuite:
    """
    Suite mejorada de reportes V2.0 - Versión final con todas las correcciones.

    Características principales:
    - Generación de reportes avanzados para datasets grandes
    - Gestión inteligente de memoria
    - Visualizaciones profesionales de alta calidad
    - Detección automática de casos problemáticos
    - Integración perfecta con el pipeline principal
    """

    def __init__(
        self,
        correlative_data: pd.DataFrame | str,
        golden_records_data: pd.DataFrame | str,
        metrics: dict[str, Any],
        config: dict[str, Any] | None = None,
        max_memory_mb: int = 500,
        pipeline_start_time: float | None = None,
    ):
        """
        Inicializa la suite de reportes mejorada.

        Args:
            correlative_data: DataFrame o ruta a archivo con tabla correlativa
            golden_records_data: DataFrame o ruta a archivo con golden records
            metrics: Diccionario con métricas del proceso
            config: Configuración opcional del sistema
            max_memory_mb: Memoria máxima a usar para visualizaciones (MB)
            pipeline_start_time: Timestamp de inicio del pipeline
        """
        self.metrics = metrics or {}
        self.pipeline_start_time = pipeline_start_time
        self.config = config or {}
        self.max_memory_mb = max_memory_mb
        self.logger = CustomLogger("EnhancedReportingSuite")
        # F1.4: (nombre de archivo, motivo) de cada reporte que no se escribió.
        self.omitidos: list[tuple[str, str]] = []

        # Configuración de límites
        self.sample_size = min(50000, max_memory_mb * 100)

        # Logging inicial
        self.logger.info("=" * 60)
        self.logger.info("Inicializando EnhancedReportingSuite V2.0 - VERSIÓN FINAL")
        self.logger.info(f"Memoria máxima configurada: {max_memory_mb} MB")
        self.logger.info(f"Tamaño de muestra: {self.sample_size:,} registros")
        self.logger.info("=" * 60)

        # Referencias a datos
        self.correlative_data_ref = correlative_data
        self.golden_records_data_ref = golden_records_data

        # Cargar muestras inteligentes
        self.correlative_sample = self._load_smart_sample(
            correlative_data, "correlative_table", sample_size=self.sample_size
        )
        self.golden_records_sample = self._load_smart_sample(
            golden_records_data, "golden_records", sample_size=int(self.sample_size * 0.6)
        )

        # Configurar estilos de visualización
        self._setup_visualization_style()

        # Validar datos cargados
        self._validate_data()

        self.logger.info(f"Datos listos - Correlativa: {len(self.correlative_sample):,}")
        self.logger.info(f"Datos listos - Golden: {len(self.golden_records_sample):,}")

    def _setup_logger(self) -> logging.Logger:
        """Configura logger consistente con el pipeline."""
        logger = logging.getLogger("EnhancedReporting")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s | %(name)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def _validate_data(self):
        """Valida que las muestras representen a sus insumos y tengan la estructura mínima.

        Compuerta de F1.3: un insumo con filas que produce una muestra vacía, o
        una muestra a la que le faltan columnas del insumo, lanza
        :class:`MuestreoReportesError`. Antes la suite seguía con la muestra
        vacía y omitía tres artefactos con un WARNING.
        """
        self._verificar_muestra(
            "correlative_table", self.correlative_data_ref, self.correlative_sample
        )
        self._verificar_muestra(
            "golden_records", self.golden_records_data_ref, self.golden_records_sample
        )

        # Validar correlative
        if not self.correlative_sample.empty:
            required = ["ID_GRUPO"]
            missing = set(required) - set(self.correlative_sample.columns)
            if missing:
                self.logger.warning(f"Columnas faltantes en correlativa: {missing}")

        # Validar golden records
        if not self.golden_records_sample.empty:
            required = ["ID_GRUPO"]
            missing = set(required) - set(self.golden_records_sample.columns)
            if missing:
                self.logger.warning(f"Columnas faltantes en golden: {missing}")

    def _load_smart_sample(
        self, data_ref: pd.DataFrame | str, table_name: str, sample_size: int
    ) -> pd.DataFrame:
        """
        Carga una muestra inteligente de los datos optimizando memoria.

        Para datasets grandes, toma una muestra estratificada que preserva
        las características importantes de los datos.
        """
        try:
            # Si es DataFrame en memoria
            if isinstance(data_ref, pd.DataFrame):
                if len(data_ref) <= sample_size:
                    return data_ref

                # Muestra estratificada por fuente (F1.3): regla única en
                # reporting._muestreo — conserva columnas y el estrato NaN,
                # piso de 1 por fuente, tope sample_size. Sin SRC, muestra simple.
                return muestra_estratificada(data_ref, "SRC", sample_size)

            # Si es archivo SQLite
            if isinstance(data_ref, str) and data_ref.endswith(".db"):
                with open_readonly_sqlite(data_ref) as conn:
                    quoted_table = quote_existing_table(conn, table_name)
                    safe_sample_size = validate_row_limit(sample_size)
                    # Para correlative_table, muestra estratificada por fuente
                    if table_name == "correlative_table":
                        query = f"""
                        WITH sampled AS (
                            SELECT *, ROW_NUMBER() OVER (PARTITION BY SRC ORDER BY RANDOM()) as rn
                            FROM {quoted_table}
                        )
                        SELECT * FROM sampled
                        WHERE rn <= ?  -- Dividir entre fuentes estimadas
                        LIMIT ?
                        """
                        params = (safe_sample_size // 3, safe_sample_size)
                    else:
                        # Para golden_records, muestra aleatoria
                        query = f"""
                        SELECT * FROM {quoted_table}
                        ORDER BY RANDOM()
                        LIMIT ?
                        """
                        params = (safe_sample_size,)
                    return pd.read_sql_query(query, conn, params=params)

            # Otros formatos
            if isinstance(data_ref, str):
                if data_ref.endswith(".parquet"):
                    df = pd.read_parquet(data_ref)
                    return self._load_smart_sample(df, table_name, sample_size)
                elif data_ref.endswith(".csv") or data_ref.endswith(".csv.gz"):
                    # Para CSV grandes, leer solo sample
                    return pd.read_csv(data_ref, nrows=sample_size)

        except Exception as e:
            self.logger.error(f"Error cargando muestra de {table_name}: {e!s}")
            self._anotar_error_carga(table_name, e)

        # Retornar DataFrame vacío si falla; _validate_data decide si eso es
        # una degradación (insumo con filas) o un insumo legítimamente vacío.
        return pd.DataFrame()

    def _anotar_error_carga(self, table_name: str, error: BaseException) -> None:
        """Guarda la causa de una carga fallida para citarla en la compuerta."""
        errores = getattr(self, "_errores_carga", None)
        if errores is None:
            errores = self._errores_carga = {}
        errores[table_name] = f"{type(error).__name__}: {error}"

    def _verificar_muestra(
        self, table_name: str, data_ref: pd.DataFrame | str, muestra: pd.DataFrame
    ) -> None:
        """Falla si un insumo con filas dio muestra vacía o si se perdieron columnas."""
        n_origen, columnas_origen = self._describir_insumo(data_ref, table_name)
        perdidas: list[str] = []
        if columnas_origen is not None:
            perdidas = [c for c in columnas_origen if c not in muestra.columns]
        muestra_vacia = muestra.empty and n_origen is not None and n_origen > 0
        if muestra_vacia or (perdidas and not muestra.empty):
            raise MuestreoReportesError.desde_muestra(
                table_name,
                n_origen=n_origen,
                n_muestra=len(muestra),
                columnas_perdidas=perdidas,
                causa=getattr(self, "_errores_carga", {}).get(table_name),
            )

    @staticmethod
    def _describir_insumo(
        data_ref: pd.DataFrame | str, table_name: str
    ) -> tuple[int | None, list[str] | None]:
        """(filas, columnas) del insumo sin cargarlo; ``None`` donde no se puede saber."""
        if isinstance(data_ref, pd.DataFrame):
            return len(data_ref), list(data_ref.columns)
        if not isinstance(data_ref, str) or not os.path.isfile(data_ref):
            return None, None
        try:
            if data_ref.endswith(".db"):
                from ..pipeline.metricas import contar_filas_sqlite

                return contar_filas_sqlite(Path(data_ref), table_name), None
            if data_ref.endswith(".parquet"):
                import pyarrow.parquet as pq

                archivo = pq.ParquetFile(data_ref)
                return archivo.metadata.num_rows, list(archivo.schema_arrow.names)
            if data_ref.endswith((".csv", ".csv.gz")):
                return None, list(pd.read_csv(data_ref, nrows=0).columns)
        except Exception:
            # Describir el insumo es diagnóstico: si no se puede, no se juzga.
            return None, None
        return None, None

    def _setup_visualization_style(self):
        """Configura estilo moderno para visualizaciones."""
        # Paleta de colores profesional
        self.colors = {
            "primary": "#1f77b4",  # Azul profesional
            "secondary": "#ff7f0e",  # Naranja
            "success": "#2ca02c",  # Verde
            "danger": "#d62728",  # Rojo
            "warning": "#ff9800",  # Naranja warning
            "info": "#17a2b8",  # Cyan
            "dark": "#343a40",  # Gris oscuro
            "light": "#f8f9fa",  # Gris claro
            "gradient_start": "#667eea",
            "gradient_end": "#764ba2",
        }

        # Configurar matplotlib
        plt.rcParams.update(
            {
                "figure.facecolor": "white",
                "axes.facecolor": "white",
                "axes.edgecolor": "#cccccc",
                "axes.linewidth": 1,
                "grid.alpha": 0.3,
                "grid.linestyle": "-",
                "grid.linewidth": 0.5,
            }
        )

    def generate_all_enhanced_reports(self, output_dir: str) -> dict[str, str]:
        """
        Genera todos los reportes mejorados - VERSIÓN CORREGIDA.
        """
        self.logger.info("Iniciando generación de reportes mejorados...")
        generated_files = {}
        self.omitidos = []

        # F1.4: cada reporte que no sale queda en `omitidos` con motivo
        # (excepción, o «sin datos» cuando el generador devuelve None).
        generadores = [
            (
                "intersection_heatmap",
                "heatmap_interseccion_mejorado.png",
                "Heatmap de Intersección",
                self.generate_intersection_heatmap,
            ),
            (
                "enhanced_dashboard",
                "dashboard_ejecutivo_mejorado.png",
                "Dashboard Ejecutivo Mejorado",
                self.generate_enhanced_dashboard,
            ),
            (
                "quality_card",
                "tarjeta_calidad_datos.png",
                "Tarjeta de Calidad",
                self.generate_quality_card,
            ),
            (
                "problematic_cases",
                "casos_problematicos_detallado.xlsx",
                "Reporte de Casos Problemáticos",
                self.generate_problematic_cases_report,
            ),
        ]
        for clave, archivo, titulo, generador in generadores:
            try:
                self.logger.info(f"Generando {titulo}...")
                ruta = generador(output_dir)
            except Exception as e:
                self.logger.error(f"Error en {titulo}: {e!s}", exc_info=True)
                self.omitidos.append((archivo, f"{type(e).__name__}: {e!s}"))
                continue
            if ruta:
                generated_files[clave] = ruta
            else:
                self.omitidos.append((archivo, "datos insuficientes para generarlo"))

        # Liberar memoria
        gc.collect()

        self.logger.info(
            f"Reportes mejorados completados: {len(generated_files)} archivos generados"
        )
        return generated_files

    def generate_intersection_heatmap(self, output_dir: str) -> str | None:
        """
        Genera heatmap de intersección entre fuentes - VERSIÓN CORREGIDA.
        """
        if self.correlative_sample.empty or "SRC" not in self.correlative_sample.columns:
            self.logger.warning("No hay datos suficientes para generar heatmap de intersección")
            return None

        try:
            # Calcular matriz de intersección
            if isinstance(self.correlative_data_ref, str) and self.correlative_data_ref.endswith(
                ".db"
            ):
                intersection_matrix = self._calculate_intersection_from_db()
            else:
                intersection_matrix = self._calculate_intersection_from_df()

            if intersection_matrix is None or intersection_matrix.empty:
                self.logger.warning("La matriz de intersección no pudo ser calculada.")
                return None

            # Crear figura con dos subplots
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 7))

            # GRÁFICO 1: Conteos Absolutos
            # Asegurar que no hay NaN y convertir a enteros
            intersection_matrix_clean = intersection_matrix.fillna(0)
            intersection_matrix_int = intersection_matrix_clean.astype(np.int64)

            sns.heatmap(
                intersection_matrix_int,
                annot=True,
                fmt="d",
                cmap="YlOrRd",
                square=True,
                linewidths=0.5,
                cbar_kws={"label": "Entidades Compartidas"},
                ax=ax1,
            )
            ax1.set_title(
                "Matriz de Intersección - Conteos Absolutos", fontsize=14, fontweight="bold"
            )
            ax1.set_xlabel("Fuente", fontsize=12)
            ax1.set_ylabel("Fuente", fontsize=12)

            # GRÁFICO 2: Porcentajes de Overlap
            # Calcular porcentajes correctamente
            diagonal_values = np.diag(intersection_matrix_clean.values).copy()
            intersection_pct = pd.DataFrame(
                0.0, index=intersection_matrix.index, columns=intersection_matrix.columns
            )

            # Calcular porcentajes fila por fila
            for i, idx in enumerate(intersection_matrix.index):
                if diagonal_values[i] > 0:
                    for _j, col in enumerate(intersection_matrix.columns):
                        intersection_pct.loc[idx, col] = (
                            intersection_matrix_clean.loc[idx, col] / diagonal_values[i]
                        ) * 100

            # Asegurar que no hay NaN
            intersection_pct = intersection_pct.fillna(0).round(1)

            # Crear heatmap de porcentajes
            sns.heatmap(
                intersection_pct,
                annot=True,
                fmt=".1f",
                cmap="Blues",
                square=True,
                linewidths=0.5,
                cbar_kws={"label": "Porcentaje de Overlap (%)"},
                ax=ax2,
                vmin=0,
                vmax=100,
            )
            ax2.set_title(
                "Matriz de Intersección - Porcentajes de Overlap", fontsize=14, fontweight="bold"
            )
            ax2.set_xlabel("Fuente Base (100%)", fontsize=12)
            ax2.set_ylabel("Fuente Comparada", fontsize=12)

            plt.suptitle(
                "Análisis de Intersección entre Fuentes de Datos", fontsize=16, fontweight="bold"
            )
            plt.tight_layout(rect=[0, 0, 1, 0.96])

            # Guardar figura
            output_path = os.path.join(output_dir, "heatmap_interseccion_mejorado.png")
            plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
            plt.close(fig)

            self.logger.info(f"✓ Heatmap de intersección guardado: {output_path}")
            return output_path

        except Exception as e:
            self.logger.error(f"Error en generate_intersection_heatmap: {e!s}", exc_info=True)
            plt.close("all")
            raise

    def _calculate_intersection_from_db(self) -> pd.DataFrame:
        """Calcula matriz de intersección directamente desde SQLite."""
        try:
            with open_readonly_sqlite(self.correlative_data_ref) as conn:
                quoted_table = quote_existing_table(conn, "correlative_table")
                # Obtener lista de fuentes
                sources_query = f"SELECT DISTINCT SRC FROM {quoted_table} ORDER BY SRC"
                sources = pd.read_sql_query(sources_query, conn)["SRC"].tolist()

                # Crear matriz vacía
                matrix = pd.DataFrame(0, index=sources, columns=sources)

                # Calcular intersecciones
                for src1 in sources:
                    for src2 in sources:
                        if src1 == src2:
                            # Diagonal: total de grupos únicos en la fuente
                            query = f"""
                            SELECT COUNT(DISTINCT ID_GRUPO) as count
                            FROM {quoted_table}
                            WHERE SRC = ?
                            """
                            params = (src1,)
                        else:
                            # Intersección: grupos compartidos
                            query = f"""
                            SELECT COUNT(DISTINCT a.ID_GRUPO) as count
                            FROM {quoted_table} a
                            INNER JOIN {quoted_table} b ON a.ID_GRUPO = b.ID_GRUPO
                            WHERE a.SRC = ? AND b.SRC = ?
                            """
                            params = (src1, src2)

                        result = pd.read_sql_query(query, conn, params=params)
                        matrix.loc[src1, src2] = result["count"].iloc[0]

                return matrix

        except Exception as e:
            self.logger.error(f"Error calculando intersección desde BD: {e!s}")
            return None

    def _calculate_intersection_from_df(self) -> pd.DataFrame:
        """Calcula matriz de intersección de fuentes por ID_GRUPO.

        Versión vectorizada: O(n_grupos · n_fuentes) en lugar de O(n_fuentes²
        · n_grupos) del doble loop original. Equivalencia validada en
        test_vectorization_equivalence::test_cooccurrence_equivalente.

        El método se basa en construir una matriz de presencia binaria
        (grupos × fuentes) y multiplicarla por su transpuesta. La diagonal
        cuenta cuántos grupos contienen cada fuente; las celdas fuera de la
        diagonal cuentan co-ocurrencias.
        """
        sources = sorted(self.correlative_sample["SRC"].unique())

        # Matriz de presencia binaria: filas=ID_GRUPO, columnas=SRC
        presencia = (
            pd.crosstab(
                self.correlative_sample["ID_GRUPO"],
                self.correlative_sample["SRC"],
            )
            .reindex(columns=sources, fill_value=0)
            .clip(upper=1)  # presencia binaria, no conteo
        )
        # Producto matricial: co[i,j] = nº de grupos donde i y j coexisten
        co = presencia.T.values @ presencia.values
        return pd.DataFrame(co, index=sources, columns=sources)

    def _plot_quality_radar(self, ax: plt.Axes, metrics: dict[str, float]):
        """
        Dibuja un gráfico de radar para métricas de calidad - VERSIÓN CORREGIDA.
        """
        # Preparar datos
        labels = list(metrics.keys())
        values = list(metrics.values())

        # Asegurar que todos los valores estén entre 0 y 1
        values = [float(max(0, min(1, v))) for v in values]

        # Número de variables
        num_vars = len(labels)

        # Calcular ángulos para cada variable
        angles = np.linspace(0, 2 * np.pi, num_vars, endpoint=False).tolist()

        # Cerrar el polígono
        values_plot = values + values[:1]
        angles_plot = angles + angles[:1]

        # Configurar el gráfico radar
        ax.set_theta_offset(np.pi / 2)
        ax.set_theta_direction(-1)

        # Dibujar el polígono
        ax.plot(angles_plot, values_plot, "o-", linewidth=2, color=self.colors["primary"])
        ax.fill(angles_plot, values_plot, alpha=0.25, color=self.colors["primary"])

        # Configurar las etiquetas
        ax.set_xticks(angles)
        ax.set_xticklabels(labels, size=10)

        # Configurar los límites y grid
        ax.set_ylim(0, 1)
        ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
        ax.set_yticklabels(["0.2", "0.4", "0.6", "0.8", "1.0"], size=8)
        ax.grid(True, linestyle="--", alpha=0.7)

        # Agregar valores en cada punto
        for angle, value, _label in zip(angles, values, labels, strict=False):
            ax.text(
                angle,
                value + 0.05,
                f"{value:.2f}",
                ha="center",
                va="center",
                size=9,
                weight="bold",
            )

    def generate_quality_card(self, output_dir: str) -> str | None:
        """
        Genera tarjeta de calidad de datos - VERSIÓN COMPLETAMENTE CORREGIDA.
        """
        if self.golden_records_sample.empty:
            self.logger.warning("No hay datos de golden records para generar tarjeta de calidad.")
            return None

        try:
            # Calcular métricas
            quality_score = self._calculate_overall_quality_score()
            status = self._get_quality_status(quality_score)

            # Determinar color del status
            status_colors = {
                "EXCELENTE": self.colors["success"],
                "MUY BUENO": self.colors["success"],
                "BUENO": self.colors["info"],
                "ACEPTABLE": self.colors["warning"],
                "REQUIERE ATENCIÓN": self.colors["warning"],
                "CRÍTICO": self.colors["danger"],
            }
            status_color = status_colors.get(status, self.colors["dark"])

            # Métricas para el radar
            radar_metrics = {
                "Confianza": float(
                    self.golden_records_sample["CONFIDENCE_SCORE"].mean()
                    if "CONFIDENCE_SCORE" in self.golden_records_sample.columns
                    else 0.5
                ),
                "Linkage": float(min(1.0, self.metrics.get("linkage_rate", 0.0) * 2)),
                "Reducción": float(self.metrics.get("reduction_rate", 0.0)),
                "Cobertura": float(
                    min(
                        1.0,
                        self.metrics.get("multi_source_groups", 0)
                        / max(1, self.metrics.get("unique_groups", 1)),
                    )
                ),
                "Completitud": 0.8,
            }

            # Crear figura
            fig = plt.figure(figsize=(12, 16), facecolor="white")
            gs = fig.add_gridspec(
                4, 2, height_ratios=[0.15, 0.35, 0.25, 0.25], hspace=0.3, wspace=0.2
            )

            # 1. Encabezado
            ax_header = fig.add_subplot(gs[0, :])
            ax_header.axis("off")
            ax_header.text(
                0.5,
                0.7,
                "TARJETA DE CALIDAD DE DATOS",
                fontsize=24,
                fontweight="bold",
                ha="center",
                color=self.colors["dark"],
            )
            ax_header.text(
                0.5,
                0.3,
                f"Record Linkage Pipeline - {datetime.now().strftime('%Y-%m-%d')}",
                fontsize=12,
                ha="center",
                style="italic",
                alpha=0.7,
            )

            # 2. Score principal y status
            ax_score = fig.add_subplot(gs[1, 0])
            ax_score.axis("off")

            # Círculo de score
            circle = plt.Circle((0.5, 0.5), 0.35, color=status_color, alpha=0.2)
            ax_score.add_patch(circle)
            circle_border = plt.Circle(
                (0.5, 0.5), 0.35, fill=False, edgecolor=status_color, linewidth=3
            )
            ax_score.add_patch(circle_border)

            # Texto del score
            ax_score.text(
                0.5,
                0.55,
                f"{quality_score:.0%}",
                fontsize=48,
                fontweight="bold",
                ha="center",
                va="center",
                color=status_color,
            )
            ax_score.text(
                0.5, 0.35, "Quality Score", fontsize=14, ha="center", va="center", alpha=0.7
            )
            ax_score.text(
                0.5,
                0.1,
                status,
                fontsize=16,
                fontweight="bold",
                ha="center",
                va="center",
                color=status_color,
            )
            ax_score.set_xlim(0, 1)
            ax_score.set_ylim(0, 1)

            # 3. Gráfico radar
            ax_radar = fig.add_subplot(gs[1, 1], projection="polar")
            self._plot_quality_radar(ax_radar, radar_metrics)
            ax_radar.set_title("Dimensiones de Calidad", y=1.08, fontsize=14, fontweight="bold")

            # 4. Métricas clave
            ax_metrics = fig.add_subplot(gs[2, :])
            ax_metrics.axis("off")

            # Calcular tiempo correctamente
            exec_time = self.metrics.get("execution_time", 0)
            if exec_time == 0 and hasattr(self, "pipeline_start_time") and self.pipeline_start_time:
                exec_time = time.time() - self.pipeline_start_time

            # Crear grid de métricas
            metric_data = [
                ("Registros Procesados", f"{self.metrics.get('total_records', 0):,}", "📊"),
                ("Entidades Únicas", f"{self.metrics.get('unique_groups', 0):,}", "🎯"),
                ("Tasa de Linkage", f"{self.metrics.get('linkage_rate', 0):.1%}", "🔗"),
                ("Casos para Revisión", f"{self.metrics.get('review_cases', 0):,}", "🔍"),
                ("Tiempo Total", f"{exec_time / 60:.1f} min" if exec_time > 0 else "N/A", "⏱️"),
                (
                    "Throughput",
                    f"{self.metrics.get('total_records', 0) / (exec_time + 1):.0f} rec/s"
                    if exec_time > 0
                    else "N/A",
                    "⚡",
                ),
            ]

            # Dibujar métricas en grid 2x3
            for i, (label, value, icon) in enumerate(metric_data):
                row = i // 3
                col = i % 3
                x = 0.17 + col * 0.33
                y = 0.7 - row * 0.4

                # Caja de métrica
                rect = plt.Rectangle(
                    (x - 0.15, y - 0.15),
                    0.3,
                    0.3,
                    facecolor=self.colors["light"],
                    edgecolor=self.colors["primary"],
                    linewidth=1,
                    transform=ax_metrics.transAxes,
                )
                ax_metrics.add_patch(rect)

                # Contenido
                # v0.7.1 (Tarea 1.4): filtrar icono no-ASCII (emojis) que la
                # fuente del entorno no renderiza y emite UserWarning.
                _icon_safe = icon if (icon and icon.isascii()) else ""
                ax_metrics.text(x, y + 0.08, _icon_safe, fontsize=20, ha="center", va="center")
                ax_metrics.text(
                    x, y - 0.02, value, fontsize=14, fontweight="bold", ha="center", va="center"
                )
                ax_metrics.text(x, y - 0.1, label, fontsize=9, ha="center", va="center")

            # 5. Hallazgos y recomendaciones
            ax_insights = fig.add_subplot(gs[3, :])
            ax_insights.axis("off")

            findings = self._generate_quality_findings()[:3]
            recommendations = self._generate_quality_recommendations()[:2]

            # Panel de hallazgos
            # v0.7.1 (Tarea 1.4): sin emojis — las fuentes de Linux/Colab no los
            # renderizan y emiten UserWarning por cada uno.
            ax_insights.text(0.05, 0.9, "HALLAZGOS PRINCIPALES", fontsize=12, fontweight="bold")
            y_pos = 0.75
            for finding in findings:
                ax_insights.text(0.07, y_pos, f"• {finding['text']}", fontsize=10, wrap=True)
                y_pos -= 0.15

            # Panel de recomendaciones
            ax_insights.text(0.52, 0.9, "RECOMENDACIONES", fontsize=12, fontweight="bold")
            y_pos = 0.75
            for rec in recommendations:
                ax_insights.text(0.54, y_pos, f"• {rec}", fontsize=10, wrap=True)
                y_pos -= 0.15

            # Guardar
            output_path = os.path.join(output_dir, "tarjeta_calidad_datos.png")
            plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
            plt.close(fig)

            self.logger.info(f"✓ Tarjeta de calidad guardada: {output_path}")
            return output_path

        except Exception as e:
            self.logger.error(f"Error generando tarjeta de calidad: {e!s}", exc_info=True)
            plt.close("all")
            raise

    def _plot_performance_metrics(self, ax):
        """
        Tiempo por fase, tal como lo cronometró el orquestador (F1.6).

        Sin ``metrics["phase_times"]`` el panel dice «Sin tiempos por fase»;
        antes repartía el tiempo total con porcentajes fijos. F1.4: sin
        ``try/except``: un panel que falla relanza y el dashboard se omite con
        motivo en el manifiesto, nunca se pinta «Error generando».
        """
        phase_series = pd.Series(
            etiquetar(tiempos_por_fase(self.metrics)), dtype="float64"
        ).sort_values(ascending=True)

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
        if total > 0:
            ax.axvline(x=total, color="red", linestyle="--", alpha=0.5, linewidth=1)
            ax.text(
                total,
                len(phase_series) - 0.5,
                f"Total: {total:.1f}s",
                ha="right",
                va="center",
                fontsize=9,
                color="red",
            )

        # Limpiar bordes
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)

    def generate_enhanced_dashboard(self, output_dir: str) -> str | None:
        """
        Genera dashboard ejecutivo mejorado con layout profesional.
        """
        try:
            # Crear figura con grid complejo
            fig = plt.figure(figsize=(20, 24))
            gs = fig.add_gridspec(
                6, 4, hspace=0.3, wspace=0.25, height_ratios=[0.1, 0.15, 0.2, 0.2, 0.2, 0.15]
            )

            # Título principal
            ax_title = fig.add_subplot(gs[0, :])
            ax_title.axis("off")
            ax_title.text(
                0.5,
                0.5,
                "DASHBOARD EJECUTIVO - RECORD LINKAGE",
                fontsize=28,
                fontweight="bold",
                ha="center",
                va="center",
                color=self.colors["dark"],
            )

            # Timestamp y metadata
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            profile = self.config.get("profile", "N/A")
            ax_title.text(
                0.98,
                0.1,
                f"Generado: {timestamp} | Perfil: {profile}",
                fontsize=10,
                ha="right",
                va="bottom",
                style="italic",
                alpha=0.7,
            )

            # KPIs principales (fila 2)
            self._add_kpi_cards(fig, gs[1, :])

            # Gráficos principales (filas 3-4)
            self._add_main_charts(fig, gs[2:4, :])

            # Métricas detalladas (fila 5)
            self._add_detailed_metrics(fig, gs[4, :])

            # Insights y recomendaciones (fila 6)
            self._add_insights_section(fig, gs[5, :])

            # Guardar
            output_path = os.path.join(output_dir, "dashboard_ejecutivo_mejorado.png")
            plt.savefig(output_path, dpi=300, bbox_inches="tight", facecolor="white")
            plt.close()

            # Liberar memoria
            gc.collect()

            self.logger.info(f"✓ Dashboard ejecutivo guardado: {output_path}")
            return output_path

        except Exception as e:
            self.logger.error(f"Error generando dashboard: {e!s}", exc_info=True)
            plt.close("all")
            raise

    def _add_kpi_cards(self, fig, grid_area):
        """Agrega tarjetas KPI al dashboard."""
        # Crear subgrid para 5 KPIs
        kpi_gs = grid_area.subgridspec(1, 5, wspace=0.15)

        # Calcular KPIs
        total_records = self.metrics.get("total_records", 0)
        unique_groups = self.metrics.get("unique_groups", 0)
        linkage_rate = self.metrics.get("linkage_rate", 0)

        # Confidence promedio
        avg_confidence = 0
        confidence_color = self.colors["warning"]
        if (
            not self.golden_records_sample.empty
            and "CONFIDENCE_SCORE" in self.golden_records_sample.columns
        ):
            avg_confidence = self.golden_records_sample["CONFIDENCE_SCORE"].mean()
            if avg_confidence >= 0.9:
                confidence_color = self.colors["success"]
            elif avg_confidence >= 0.75:
                confidence_color = self.colors["info"]

        # Tiempo de ejecución
        exec_time = self.metrics.get("execution_time", 0)
        if exec_time == 0 and self.pipeline_start_time:
            exec_time = time.time() - self.pipeline_start_time

        if exec_time < 1:
            time_str = f"{exec_time * 1000:.0f} ms"
        elif exec_time < 60:
            time_str = f"{exec_time:.1f} seg"
        else:
            time_str = f"{exec_time / 60:.1f} min"

        if exec_time <= 0:
            time_str = "N/A"

        kpis = [
            {
                "value": f"{total_records:,}",
                "label": "REGISTROS\nPROCESADOS",
                "color": self.colors["primary"],
                "icon": "📊",
            },
            {
                "value": f"{unique_groups:,}",
                "label": "ENTIDADES\nÚNICAS",
                "color": self.colors["success"],
                "icon": "🎯",
            },
            {
                "value": f"{linkage_rate:.1%}",
                "label": "TASA DE\nLINKAGE",
                "color": self.colors["info"],
                "icon": "🔗",
            },
            {
                "value": f"{avg_confidence:.3f}",
                "label": "CONFIDENCE\nPROMEDIO",
                "color": confidence_color,
                "icon": "⭐",
            },
            {
                "value": time_str,
                "label": "TIEMPO\nTOTAL",
                "color": self.colors["secondary"],
                "icon": "⏱️",
            },
        ]

        # Dibujar cada KPI
        for i, kpi in enumerate(kpis):
            ax = fig.add_subplot(kpi_gs[0, i])
            ax.axis("off")

            # Fondo con gradiente simulado
            rect = plt.Rectangle(
                (0.05, 0.1), 0.9, 0.8, facecolor=kpi["color"], alpha=0.15, transform=ax.transAxes
            )
            ax.add_patch(rect)

            # Borde
            border = plt.Rectangle(
                (0.05, 0.1),
                0.9,
                0.8,
                facecolor="none",
                edgecolor=kpi["color"],
                linewidth=2,
                transform=ax.transAxes,
            )
            ax.add_patch(border)

            # Contenido
            # v0.7.1 (Tarea 1.4): filtrar icono no-ASCII para evitar UserWarning
            # de glyph faltante (Liberation Sans no trae emojis).
            _icon_raw = kpi.get("icon", "") or ""
            _icon_safe = _icon_raw if _icon_raw.isascii() else ""
            ax.text(0.5, 0.75, _icon_safe, fontsize=20, ha="center", va="center")
            ax.text(
                0.5,
                0.55,
                kpi["value"],
                fontsize=18,
                weight="bold",
                ha="center",
                va="center",
                color=kpi["color"],
            )
            ax.text(
                0.5,
                0.3,
                kpi["label"],
                fontsize=9,
                weight="bold",
                ha="center",
                va="center",
                color=self.colors["dark"],
            )

    def _add_main_charts(self, fig, grid_area):
        """Agrega gráficos principales al dashboard."""
        # Crear subgrid 2x3
        charts_gs = grid_area.subgridspec(2, 3, wspace=0.3, hspace=0.4)

        # 1. Distribución de Confidence Score
        ax1 = fig.add_subplot(charts_gs[0, 0])
        self._plot_confidence_distribution(ax1)

        # 2. Análisis por Fuente
        ax2 = fig.add_subplot(charts_gs[0, 1])
        self._plot_source_analysis(ax2)

        # 3. Tendencia de Linkage
        ax3 = fig.add_subplot(charts_gs[0, 2])
        self._plot_linkage_trend(ax3)

        # 4. Distribución de Grupos
        ax4 = fig.add_subplot(charts_gs[1, 0])
        self._plot_group_distribution(ax4)

        # 5. Casos Problemáticos
        ax5 = fig.add_subplot(charts_gs[1, 1])
        self._plot_problematic_cases(ax5)

        # 6. Performance Metrics
        ax6 = fig.add_subplot(charts_gs[1, 2])
        self._plot_performance_metrics(ax6)

    def _plot_confidence_distribution(self, ax):
        """Gráfico de distribución de confidence scores."""
        if (
            self.golden_records_sample.empty
            or "CONFIDENCE_SCORE" not in self.golden_records_sample.columns
        ):
            self._show_no_data_message(ax, "Sin datos de Confidence")
            return

        scores = self.golden_records_sample["CONFIDENCE_SCORE"].dropna()

        # Histograma con KDE
        ax.hist(scores, bins=20, alpha=0.7, color=self.colors["primary"], edgecolor="white")

        # Líneas de referencia
        ax.axvline(
            scores.mean(),
            color=self.colors["danger"],
            linestyle="--",
            linewidth=2,
            label=f"Media: {scores.mean():.3f}",
        )
        ax.axvline(
            0.75, color=self.colors["warning"], linestyle=":", linewidth=2, label="Umbral: 0.75"
        )

        ax.set_xlabel("Confidence Score")
        ax.set_ylabel("Frecuencia")
        ax.set_title("Distribución de Confidence Scores", fontweight="bold")
        ax.legend()
        ax.grid(True, alpha=0.3)

    def _plot_source_analysis(self, ax):
        """Análisis por fuente de datos."""
        if "SRC" not in self.correlative_sample.columns:
            self._show_no_data_message(ax, "Sin datos por fuente")
            return

        # Contar registros por fuente
        source_counts = self.correlative_sample["SRC"].value_counts()

        # Gráfico de barras horizontal
        colors = plt.colormaps["viridis"](np.linspace(0.3, 0.9, len(source_counts)))
        bars = ax.barh(range(len(source_counts)), source_counts.values, color=colors)

        # Etiquetas
        ax.set_yticks(range(len(source_counts)))
        ax.set_yticklabels(source_counts.index)
        ax.set_xlabel("Número de Registros")
        ax.set_title("Registros por Fuente", fontweight="bold")
        ax.grid(True, alpha=0.3, axis="x")

        # Valores en las barras
        for i, (_bar, val) in enumerate(zip(bars, source_counts.values, strict=False)):
            ax.text(val + 0.01 * source_counts.max(), i, f"{val:,}", va="center", fontsize=9)

    def _plot_linkage_trend(self, ax):
        """Visualización de métricas de linkage."""
        # Datos para el gráfico
        metrics_data = {
            "Total\nRegistros": self.metrics.get("total_records", 0),
            "Entidades\nÚnicas": self.metrics.get("unique_groups", 0),
            "Multi-fuente": self.metrics.get("multi_source_groups", 0),
            "Candidatos": self.metrics.get("candidates_found", 0),
            "Pares\nScoreados": self.metrics.get("pairs_scored", 0),
        }

        # Filtrar métricas con valores > 0
        metrics_data = {k: v for k, v in metrics_data.items() if v > 0}

        if not metrics_data:
            self._show_no_data_message(ax, "Sin métricas de linkage")
            return

        # Gráfico de línea con marcadores
        x = range(len(metrics_data))
        y = list(metrics_data.values())

        ax.plot(x, y, "o-", color=self.colors["primary"], linewidth=2, markersize=8)

        # Etiquetas de valores
        for i, (_label, value) in enumerate(metrics_data.items()):
            ax.text(i, value + 0.02 * max(y), f"{value:,}", ha="center", va="bottom", fontsize=9)

        ax.set_xticks(x)
        ax.set_xticklabels(metrics_data.keys(), rotation=0)
        ax.set_ylabel("Cantidad")
        ax.set_title("Flujo del Proceso de Linkage", fontweight="bold")
        ax.grid(True, alpha=0.3, axis="y")
        ax.set_yscale("log")  # Escala logarítmica para mejor visualización

    def _plot_group_distribution(self, ax):
        """Distribución de tamaños de grupo."""
        if "ID_GRUPO" not in self.correlative_sample.columns:
            self._show_no_data_message(ax, "Sin datos de grupos")
            return

        # Calcular tamaños de grupo
        group_sizes = self.correlative_sample.groupby("ID_GRUPO").size()

        # Categorizar
        bins = [1, 2, 3, 5, 10, 20, 50, float("inf")]
        labels = ["1", "2", "3-5", "6-10", "11-20", "21-50", ">50"]
        size_dist = pd.cut(group_sizes, bins=bins, labels=labels, right=False).value_counts()

        # Gráfico de barras
        colors = plt.colormaps["RdYlGn_r"](np.linspace(0.2, 0.8, len(size_dist)))
        bars = ax.bar(range(len(size_dist)), size_dist.values, color=colors)

        # Configuración
        ax.set_xticks(range(len(size_dist)))
        ax.set_xticklabels(size_dist.index)
        ax.set_xlabel("Tamaño del Grupo")
        ax.set_ylabel("Cantidad de Grupos")
        ax.set_title("Distribución de Tamaños de Grupo", fontweight="bold")
        ax.grid(True, alpha=0.3, axis="y")

        # Valores en barras
        for bar, val in zip(bars, size_dist.values, strict=False):
            if val > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    val + 0.5,
                    f"{val:,}",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                )

    def _plot_problematic_cases(self, ax):
        """Visualización de casos problemáticos."""
        # Identificar casos problemáticos
        problematic_stats = self._analyze_problematic_cases()

        if not problematic_stats:
            self._show_no_data_message(ax, "Sin casos problemáticos detectados")
            return

        # Preparar datos para visualización
        categories = list(problematic_stats.keys())
        values = list(problematic_stats.values())

        # Gráfico de barras horizontal con colores por severidad
        colors = [
            self.colors["danger"]
            if v > 50
            else self.colors["warning"]
            if v > 20
            else self.colors["info"]
            for v in values
        ]

        bars = ax.barh(categories, values, color=colors)

        # Configuración
        ax.set_xlabel("Número de Casos")
        ax.set_title("Casos Problemáticos Detectados", fontweight="bold")
        ax.grid(True, alpha=0.3, axis="x")

        # Valores en barras
        for bar, val in zip(bars, values, strict=False):
            ax.text(
                val + 0.5, bar.get_y() + bar.get_height() / 2, f"{val:,}", va="center", fontsize=9
            )

    def _add_detailed_metrics(self, fig, grid_area):
        """Agrega sección de métricas detalladas."""
        # Crear subgrid para 4 paneles
        metrics_gs = grid_area.subgridspec(1, 4, wspace=0.2)

        # Panel 1: Estadísticas de Calidad
        ax1 = fig.add_subplot(metrics_gs[0, 0])
        self._add_quality_stats(ax1)

        # Panel 2: Métricas de Cobertura
        ax2 = fig.add_subplot(metrics_gs[0, 1])
        self._add_coverage_metrics(ax2)

        # Panel 3: Análisis de Fuentes
        ax3 = fig.add_subplot(metrics_gs[0, 2])
        self._add_source_metrics(ax3)

        # Panel 4: Indicadores de Performance
        ax4 = fig.add_subplot(metrics_gs[0, 3])
        self._add_performance_indicators(ax4)

    def _add_quality_stats(self, ax):
        """Panel de estadísticas de calidad."""
        ax.axis("off")
        ax.set_title("Estadísticas de Calidad", fontweight="bold", fontsize=12, pad=20)

        stats_text = []

        # Calcular estadísticas
        if (
            not self.golden_records_sample.empty
            and "CONFIDENCE_SCORE" in self.golden_records_sample.columns
        ):
            scores = self.golden_records_sample["CONFIDENCE_SCORE"]
            stats_text.extend(
                [
                    f"• Score promedio: {scores.mean():.3f}",
                    f"• Score mediano: {scores.median():.3f}",
                    f"• Desv. estándar: {scores.std():.3f}",
                    f"• Alta confianza (>0.9): {(scores > 0.9).sum():,}",
                    f"• Requieren revisión (<0.75): {(scores < 0.75).sum():,}",
                ]
            )
        else:
            stats_text.append("Sin datos de calidad disponibles")

        # Mostrar texto
        ax.text(
            0.5,
            0.5,
            "\n".join(stats_text),
            ha="center",
            va="center",
            fontsize=10,
            transform=ax.transAxes,
            bbox=dict(boxstyle="round,pad=0.5", facecolor=self.colors["light"], alpha=0.8),
        )

    def _add_coverage_metrics(self, ax):
        """Panel de métricas de cobertura."""
        ax.axis("off")
        ax.set_title("Métricas de Cobertura", fontweight="bold", fontsize=12, pad=20)

        coverage_text = []

        # Calcular métricas
        total_records = self.metrics.get("total_records", 0)
        unique_groups = self.metrics.get("unique_groups", 0)
        multi_source = self.metrics.get("multi_source_groups", 0)

        if total_records > 0:
            coverage_text.extend(
                [
                    f"• Reducción: {(1 - unique_groups / total_records):.1%}",
                    f"• Entidades multi-fuente: {multi_source:,}",
                    f"• % Multi-fuente: {multi_source / unique_groups:.1%}"
                    if unique_groups > 0
                    else "",
                    f"• Entidades únicas: {unique_groups:,}",
                    f"• Registros por entidad: {total_records / unique_groups:.1f}"
                    if unique_groups > 0
                    else "",
                ]
            )
        else:
            coverage_text.append("Sin métricas de cobertura")

        ax.text(
            0.5,
            0.5,
            "\n".join(coverage_text),
            ha="center",
            va="center",
            fontsize=10,
            transform=ax.transAxes,
            bbox=dict(boxstyle="round,pad=0.5", facecolor=self.colors["light"], alpha=0.8),
        )

    def _add_source_metrics(self, ax):
        """Panel de métricas por fuente."""
        ax.axis("off")
        ax.set_title("Análisis de Fuentes", fontweight="bold", fontsize=12, pad=20)

        source_text = []

        if "SRC" in self.correlative_sample.columns:
            n_sources = self.correlative_sample["SRC"].nunique()
            source_text.append(f"• Total de fuentes: {n_sources}")

            # Top 3 fuentes
            top_sources = self.correlative_sample["SRC"].value_counts().head(3)
            source_text.append("\nTop 3 fuentes:")
            for src, count in top_sources.items():
                pct = count / len(self.correlative_sample) * 100
                source_text.append(f"  - {src}: {count:,} ({pct:.1f}%)")
        else:
            source_text.append("Sin análisis de fuentes")

        ax.text(
            0.5,
            0.5,
            "\n".join(source_text),
            ha="center",
            va="center",
            fontsize=10,
            transform=ax.transAxes,
            bbox=dict(boxstyle="round,pad=0.5", facecolor=self.colors["light"], alpha=0.8),
        )

    def _add_performance_indicators(self, ax):
        """Panel de indicadores de performance."""
        ax.axis("off")
        ax.set_title("Indicadores de Performance", fontweight="bold", fontsize=12, pad=20)

        perf_text = []

        # Tiempo total
        exec_time = self.metrics.get("execution_time", 0)
        if exec_time > 0:
            perf_text.append(f"• Tiempo total: {exec_time:.1f}s")

            # Throughput
            total_records = self.metrics.get("total_records", 0)
            if total_records > 0:
                throughput = total_records / exec_time
                perf_text.append(f"• Throughput: {throughput:.0f} reg/s")

        # Eficiencia de candidatos
        candidates = self.metrics.get("candidates_found", 0)
        pairs_scored = self.metrics.get("pairs_scored", 0)
        if candidates > 0 and pairs_scored > 0:
            efficiency = pairs_scored / candidates
            perf_text.append(f"• Eficiencia candidatos: {efficiency:.1%}")

        # Memoria estimada
        perf_text.append(f"\n• Memoria configurada: {self.max_memory_mb} MB")

        ax.text(
            0.5,
            0.5,
            "\n".join(perf_text),
            ha="center",
            va="center",
            fontsize=10,
            transform=ax.transAxes,
            bbox=dict(boxstyle="round,pad=0.5", facecolor=self.colors["light"], alpha=0.8),
        )

    def _add_insights_section(self, fig, grid_area):
        """Agrega sección de insights y recomendaciones."""
        # Un solo panel para insights
        ax = fig.add_subplot(grid_area)
        ax.axis("off")

        # Título
        ax.text(
            0.5,
            0.95,
            "INSIGHTS Y RECOMENDACIONES",
            fontsize=14,
            fontweight="bold",
            ha="center",
            va="top",
        )

        # Generar insights
        insights = self._generate_insights()

        # Mostrar insights en columnas
        n_insights = len(insights)
        if n_insights > 0:
            # Dividir en 3 columnas
            cols = 3
            (n_insights + cols - 1) // cols

            for i, insight in enumerate(insights):
                col = i % cols
                row = i // cols

                x = 0.15 + col * 0.3
                y = 0.8 - row * 0.15

                # Icono según tipo
                # v0.7.1 (Tarea 1.4): default ASCII '•' (no '💡'); si el insight
                # trae un icono emoji, se reemplaza por '•' para que renderice.
                icon = insight.get("icon", "•")
                if icon and not icon.isascii():
                    icon = "•"
                color = insight.get("color", self.colors["info"])

                ax.text(x - 0.02, y, icon, fontsize=14, ha="right", va="top")
                ax.text(
                    x, y, insight["text"], fontsize=10, ha="left", va="top", wrap=True, color=color
                )

    def generate_problematic_cases_report(self, output_dir: str) -> str | None:
        """
        Genera reporte Excel detallado de casos problemáticos.
        Optimizado para grandes datasets procesando por chunks.
        """
        self.logger.info("Identificando casos problemáticos...")

        problematic_dfs = {}

        # 1. Casos de baja confianza
        if (
            not self.golden_records_sample.empty
            and "CONFIDENCE_SCORE" in self.golden_records_sample.columns
        ):
            low_confidence = self.golden_records_sample[
                self.golden_records_sample["CONFIDENCE_SCORE"] < 0.75
            ].copy()

            if not low_confidence.empty:
                # Vectorización: np.where en lugar de apply (equivalencia validada
                # en tests/test_vectorization_equivalence.py::test_severidad_*)
                low_confidence["SEVERIDAD"] = np.where(
                    low_confidence["CONFIDENCE_SCORE"] < 0.5, "ALTA", "MEDIA"
                )
                low_confidence["TIPO_PROBLEMA"] = "BAJA_CONFIANZA"
                low_confidence["ACCION_SUGERIDA"] = "Revisión manual urgente"

                problematic_dfs["Baja_Confianza"] = low_confidence.head(
                    1000
                )  # Limitar para memoria

        # 2. Grupos excesivamente grandes
        if "ID_GRUPO" in self.correlative_sample.columns:
            group_sizes = self.correlative_sample.groupby("ID_GRUPO").size()
            large_groups = group_sizes[group_sizes > 20]

            if not large_groups.empty:
                # Obtener info de estos grupos
                large_groups_info = self.golden_records_sample[
                    self.golden_records_sample["ID_GRUPO"].isin(large_groups.index)
                ].copy()

                if not large_groups_info.empty:
                    large_groups_info["SEVERIDAD"] = "MEDIA"
                    large_groups_info["TIPO_PROBLEMA"] = "GRUPO_EXCESIVO"
                    large_groups_info["ACCION_SUGERIDA"] = "Verificar posible sobre-agrupación"

                    problematic_dfs["Grupos_Grandes"] = large_groups_info.head(500)

        # 3. Alta variabilidad en nombres/NITs
        if "NAME_VARIATIONS" in self.golden_records_sample.columns:
            high_variation = self.golden_records_sample[
                (self.golden_records_sample.get("NAME_VARIATIONS", 0) > 5)
                | (self.golden_records_sample.get("NIT_VARIATIONS", 0) > 3)
            ].copy()

            if not high_variation.empty:
                high_variation["SEVERIDAD"] = "MEDIA"
                high_variation["TIPO_PROBLEMA"] = "ALTA_VARIACION"
                high_variation["ACCION_SUGERIDA"] = "Verificar consistencia de datos"

                problematic_dfs["Alta_Variacion"] = high_variation.head(500)

        # 4. Resumen estadístico
        summary_data = []
        for problem_type, df in problematic_dfs.items():
            summary_data.append(
                {
                    "Tipo de Problema": problem_type.replace("_", " "),
                    "Casos Detectados": len(df),
                    "Severidad Promedio": df["SEVERIDAD"].mode()[0] if not df.empty else "N/A",
                    "Requiere Acción": "SÍ",
                }
            )

        if summary_data:
            problematic_dfs["Resumen"] = pd.DataFrame(summary_data)

        # Exportar a Excel con múltiples hojas
        if problematic_dfs:
            output_path = os.path.join(output_dir, "casos_problematicos_detallado.xlsx")

            try:
                with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
                    used_sheet_names: set[str] = set()
                    for sheet_name, df in problematic_dfs.items():
                        # Seleccionar columnas relevantes si existen
                        columns_to_export = []
                        possible_columns = [
                            "ID_GRUPO",
                            "NIT_FINAL",
                            "RAZON_SOCIAL_FINAL",
                            "CONFIDENCE_SCORE",
                            "SOURCES_COUNT",
                            "NAME_VARIATIONS",
                            "NIT_VARIATIONS",
                            "TIPO_PROBLEMA",
                            "SEVERIDAD",
                            "ACCION_SUGERIDA",
                        ]

                        for col in possible_columns:
                            if col in df.columns:
                                columns_to_export.append(col)

                        if columns_to_export:
                            safe_name = safe_sheet_name(str(sheet_name), used_sheet_names)
                            prepare_spreadsheet_data(df[columns_to_export]).to_excel(
                                writer,
                                sheet_name=safe_name,
                                index=False,
                            )

                            # Aplicar formato
                            worksheet = writer.sheets[safe_name]
                            for column in worksheet.columns:
                                max_length = 0
                                column_letter = column[0].column_letter
                                for cell in column:
                                    try:
                                        if len(str(cell.value)) > max_length:
                                            max_length = len(str(cell.value))
                                    except:
                                        pass
                                adjusted_width = min(max_length + 2, 50)
                                worksheet.column_dimensions[column_letter].width = adjusted_width

                self.logger.info(f"✓ Reporte de casos problemáticos guardado: {output_path}")
                return output_path

            except Exception as e:
                self.logger.error(f"Error guardando reporte Excel: {e!s}")
                # F1.4: no dejar un xlsx a medias ni perder el motivo.
                with contextlib.suppress(OSError):
                    os.unlink(output_path)
                raise
        else:
            self.logger.info("No se detectaron casos problemáticos significativos")
            return None

    def _show_no_data_message(self, ax, message):
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
            color=self.colors["dark"],
            alpha=0.7,
            bbox=dict(boxstyle="round,pad=0.5", facecolor=self.colors["light"], alpha=0.5),
        )

    def _calculate_overall_quality_score(self) -> float:
        """Calcula un score general de calidad basado en múltiples factores."""
        scores = []
        weights = []

        # Factor 1: Confidence promedio (peso 40%)
        if (
            not self.golden_records_sample.empty
            and "CONFIDENCE_SCORE" in self.golden_records_sample.columns
        ):
            avg_confidence = self.golden_records_sample["CONFIDENCE_SCORE"].mean()
            scores.append(avg_confidence)
            weights.append(0.4)

        # Factor 2: Tasa de linkage balanceada (peso 20%)
        linkage_rate = self.metrics.get("linkage_rate", 0)
        # Penalizar tanto valores muy bajos como muy altos
        if linkage_rate > 0:
            if linkage_rate < 0.05:
                linkage_score = linkage_rate * 20  # Escalar para que 0.05 = 1.0
            elif linkage_rate > 0.5:
                linkage_score = 1.0 - (linkage_rate - 0.5)  # Penalizar sobre-linkage
            else:
                linkage_score = 1.0  # Rango óptimo
            scores.append(linkage_score)
            weights.append(0.2)

        # Factor 3: Completitud de datos (peso 20%)
        total_records = self.metrics.get("total_records", 0)
        unique_groups = self.metrics.get("unique_groups", 0)
        if total_records > 0 and unique_groups > 0:
            completeness = min(unique_groups / total_records * 2, 1.0)  # Normalizar
            scores.append(completeness)
            weights.append(0.2)

        # Factor 4: Eficiencia del proceso (peso 20%)
        candidates = self.metrics.get("candidates_found", 0)
        pairs_scored = self.metrics.get("pairs_scored", 0)
        if candidates > 0 and pairs_scored > 0:
            efficiency = min(pairs_scored / candidates, 1.0)
            scores.append(efficiency)
            weights.append(0.2)

        # Calcular score ponderado
        if scores:
            total_weight = sum(weights)
            weighted_score = (
                sum(s * w for s, w in zip(scores, weights, strict=False)) / total_weight
            )
            return weighted_score
        else:
            return 0.5  # Score por defecto si no hay datos

    def _get_quality_status(self, score: float) -> str:
        """Determina el estado de calidad basado en el score."""
        if score >= 0.9:
            return "EXCELENTE"
        elif score >= 0.8:
            return "MUY BUENO"
        elif score >= 0.7:
            return "BUENO"
        elif score >= 0.6:
            return "ACEPTABLE"
        elif score >= 0.5:
            return "REQUIERE ATENCIÓN"
        else:
            return "CRÍTICO"

    def _generate_quality_findings(self) -> list[dict[str, Any]]:
        """Genera hallazgos principales sobre la calidad de datos."""
        findings = []

        # Analizar problemas de confidence
        problematic_cases = self._analyze_problematic_cases()

        # Hallazgo 1: Casos de baja confianza
        if "low_confidence" in problematic_cases and problematic_cases["low_confidence"] > 50:
            findings.append(
                {
                    "severity": "ALTA",
                    "text": f"{problematic_cases['low_confidence']:,} grupos con confidence < 0.75",
                }
            )

        # Hallazgo 2: Grupos excesivamente grandes
        if "large_groups" in problematic_cases and problematic_cases["large_groups"] > 10:
            findings.append(
                {
                    "severity": "MEDIA",
                    "text": f"{problematic_cases['large_groups']} grupos con más de 20 registros",
                }
            )

        # Hallazgo 3: Análisis de linkage rate
        linkage_rate = self.metrics.get("linkage_rate", 0)
        if linkage_rate < 0.05:
            findings.append(
                {"severity": "ALTA", "text": f"Tasa de linkage muy baja: {linkage_rate:.1%}"}
            )
        elif linkage_rate > 0.5:
            findings.append(
                {
                    "severity": "MEDIA",
                    "text": f"Tasa de linkage alta: {linkage_rate:.1%} (posible sobre-vinculación)",
                }
            )

        # Hallazgo 4: Fuentes problemáticas
        if "SRC" in self.correlative_sample.columns:
            source_quality = self._analyze_source_quality()
            for source, issues in source_quality.items():
                if issues:
                    findings.append(
                        {"severity": "BAJA", "text": f"Fuente '{source}': {', '.join(issues)}"}
                    )

        # Si no hay hallazgos negativos, agregar positivos
        if not findings:
            findings.append(
                {"severity": "BAJA", "text": "No se detectaron problemas significativos de calidad"}
            )

        return findings

    def _generate_quality_recommendations(self) -> list[str]:
        """Genera recomendaciones basadas en el análisis de calidad."""
        recommendations = []

        # Basadas en confidence
        if (
            not self.golden_records_sample.empty
            and "CONFIDENCE_SCORE" in self.golden_records_sample.columns
        ):
            avg_conf = self.golden_records_sample["CONFIDENCE_SCORE"].mean()
            low_conf_count = (self.golden_records_sample["CONFIDENCE_SCORE"] < 0.75).sum()

            if avg_conf < 0.7:
                recommendations.append(
                    "Revisar y mejorar la calidad de los datos de entrada, especialmente la estandarización de nombres"
                )

            if low_conf_count > 100:
                recommendations.append(
                    f"Priorizar revisión manual de los {low_conf_count:,} casos con confidence < 0.75"
                )

        # Basadas en linkage rate
        linkage_rate = self.metrics.get("linkage_rate", 0)
        if linkage_rate < 0.05:
            recommendations.append(
                "Considerar reducir los umbrales de similitud para capturar más coincidencias"
            )
        elif linkage_rate > 0.5:
            recommendations.append(
                "Aumentar los umbrales de similitud para evitar falsos positivos"
            )

        # Basadas en performance
        exec_time = self.metrics.get("execution_time", 0)
        if exec_time > 3600:
            recommendations.append(
                "Optimizar parámetros LSH o procesar datos en chunks más pequeños"
            )

        # Basadas en grupos grandes
        problematic = self._analyze_problematic_cases()
        if problematic.get("large_groups", 0) > 10:
            recommendations.append(
                "Investigar grupos con más de 20 registros para detectar posible sobre-agrupación"
            )

        # Recomendación general si no hay problemas específicos
        if not recommendations:
            recommendations.append("Mantener monitoreo regular de métricas de calidad")
            recommendations.append("Considerar análisis periódico de casos borderline")

        return recommendations

    def _analyze_problematic_cases(self) -> dict[str, int]:
        """Analiza y cuenta casos problemáticos por tipo."""
        problems = {}

        if not self.golden_records_sample.empty:
            # Baja confianza
            if "CONFIDENCE_SCORE" in self.golden_records_sample.columns:
                problems["low_confidence"] = (
                    self.golden_records_sample["CONFIDENCE_SCORE"] < 0.75
                ).sum()

            # Grupos grandes
            if "ID_GRUPO" in self.correlative_sample.columns:
                group_sizes = self.correlative_sample.groupby("ID_GRUPO").size()
                problems["large_groups"] = (group_sizes > 20).sum()

            # Alta variación
            if "NAME_VARIATIONS" in self.golden_records_sample.columns:
                problems["high_name_variation"] = (
                    self.golden_records_sample["NAME_VARIATIONS"] > 5
                ).sum()

            if "NIT_VARIATIONS" in self.golden_records_sample.columns:
                problems["high_nit_variation"] = (
                    self.golden_records_sample["NIT_VARIATIONS"] > 3
                ).sum()

        return problems

    def _analyze_source_quality(self) -> dict[str, list[str]]:
        """Analiza problemas de calidad por fuente."""
        source_issues = {}

        if "SRC" in self.correlative_sample.columns:
            for source in self.correlative_sample["SRC"].unique():
                issues = []

                # Analizar cobertura
                source_data = self.correlative_sample[self.correlative_sample["SRC"] == source]

                if len(source_data) < 100:
                    issues.append("pocos registros")

                # Más análisis podrían agregarse aquí

                if issues:
                    source_issues[source] = issues

        return source_issues

    def _generate_insights(self) -> list[dict[str, Any]]:
        """Genera insights basados en los datos."""
        insights = []

        # Análisis de linkage rate
        linkage_rate = self.metrics.get("linkage_rate", 0)
        if linkage_rate < 0.05:
            insights.append(
                {
                    "text": "Tasa de linkage muy baja. Revisar umbrales.",
                    "icon": "⚠️",
                    "color": self.colors["danger"],
                }
            )
        elif linkage_rate > 0.5:
            insights.append(
                {
                    "text": "Alta tasa de linkage. Verificar sobre-vinculación.",
                    "icon": "⚡",
                    "color": self.colors["warning"],
                }
            )
        else:
            insights.append(
                {
                    "text": f"Tasa de linkage balanceada: {linkage_rate:.1%}",
                    "icon": "✅",
                    "color": self.colors["success"],
                }
            )

        # Análisis de confidence
        if (
            not self.golden_records_sample.empty
            and "CONFIDENCE_SCORE" in self.golden_records_sample.columns
        ):
            avg_conf = self.golden_records_sample["CONFIDENCE_SCORE"].mean()
            low_conf = (self.golden_records_sample["CONFIDENCE_SCORE"] < 0.75).sum()

            if avg_conf < 0.7:
                insights.append(
                    {
                        "text": "Confidence promedio bajo. Mejorar calidad de datos.",
                        "icon": "📊",
                        "color": self.colors["danger"],
                    }
                )

            if low_conf > 100:
                insights.append(
                    {
                        "text": f"{low_conf:,} casos requieren revisión manual.",
                        "icon": "🔍",
                        "color": self.colors["warning"],
                    }
                )

        # Análisis de fuentes
        if "SRC" in self.correlative_sample.columns:
            n_sources = self.correlative_sample["SRC"].nunique()
            if n_sources > 5:
                insights.append(
                    {
                        "text": f"Múltiples fuentes ({n_sources}). Considerar priorización.",
                        "icon": "📚",
                        "color": self.colors["info"],
                    }
                )

        # Performance
        exec_time = self.metrics.get("execution_time", 0)
        if exec_time > 3600:
            insights.append(
                {
                    "text": "Proceso lento. Optimizar parámetros o usar chunks.",
                    "icon": "⏰",
                    "color": self.colors["warning"],
                }
            )

        # Si no hay insights problemáticos, agregar positivos
        if len(insights) < 3:
            insights.append(
                {
                    "text": "Proceso completado exitosamente.",
                    "icon": "🎯",
                    "color": self.colors["success"],
                }
            )

        return insights[:6]  # Máximo 6 insights
