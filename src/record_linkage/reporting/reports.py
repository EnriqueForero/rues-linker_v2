"""
reporting.reports — record_linkage_pipeline

Componentes:
    - class ReportGenerator  (origen: notebook celda [136])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import gc
import os
from typing import Any

import pandas as pd

from ..utils.logger import CustomLogger
from ._sqlite import open_readonly_sqlite, quote_existing_table, validate_row_limit


class ReportGenerator:
    """
    Generador de reportes V6.0 - Versión final con todas las mejoras.

    Características principales:
    - Manejo inteligente de memoria para datasets grandes
    - Soporte completo para DataFrames en memoria y archivos
    - Generación robusta de reportes con manejo granular de errores
    - Optimización para Google Colab y entornos con memoria limitada
    - Logging detallado para debugging y monitoreo
    """

    def __init__(
        self,
        correlative_data: pd.DataFrame | str,
        golden_records_data: pd.DataFrame | str,
        metrics: dict[str, Any],
        config: dict[str, Any] | None = None,
    ):
        """
        Inicializa el generador con datos explícitos y configuración robusta.

        Args:
            correlative_data: DataFrame o ruta a archivo con tabla correlativa
            golden_records_data: DataFrame o ruta a archivo con golden records
            metrics: Diccionario con métricas del proceso
            config: Configuración opcional del sistema
        """
        self.metrics = metrics or {}
        self.config = config or {}
        self.logger = CustomLogger("ReportGenerator")

        # Configuración de límites de memoria
        self.sample_size = config.get("report_sample_size", 100000)
        self.chunk_size = config.get("report_chunk_size", 50000)

        # Logging de diagnóstico mejorado
        self.logger.info("=" * 60)
        self.logger.info("Inicializando ReportGenerator V6.0 - VERSIÓN FINAL")
        self.logger.info(f"Tipo correlative_data: {type(correlative_data)}")
        self.logger.info(f"Tipo golden_records_data: {type(golden_records_data)}")
        self.logger.info(f"Métricas recibidas: {list(metrics.keys())}")
        self.logger.info(f"Límite de muestra: {self.sample_size:,} registros")
        self.logger.info("=" * 60)

        # Guardar referencias originales
        self.correlative_data_ref = correlative_data
        self.golden_records_data_ref = golden_records_data

        # Cargar datos con gestión inteligente de memoria
        self.correlative_sample = self._load_data_sample(correlative_data, "correlative_table")
        self.golden_records = self._load_data_sample(golden_records_data, "golden_records")

        # Validar datos cargados
        self._validate_loaded_data()

        self.logger.info(f"Datos listos - Correlativa: {len(self.correlative_sample):,} registros")
        self.logger.info(f"Datos listos - Golden: {len(self.golden_records):,} registros")

    # def _setup_logger(self) -> logging.Logger:
    #     """Configura logger con formato consistente."""
    #     logger = logging.getLogger('ReportGenerator')
    #     if not logger.handlers:
    #         handler = logging.StreamHandler()
    #         formatter = logging.Formatter(
    #             '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    #         )
    #         handler.setFormatter(formatter)
    #         logger.addHandler(handler)
    #         logger.setLevel(logging.INFO)
    #     return logger

    def _validate_loaded_data(self):
        """Valida que los datos cargados tengan las columnas mínimas necesarias."""
        # Validar tabla correlativa
        if not self.correlative_sample.empty:
            required_cols = ["ID_GRUPO", "SRC"]
            missing = set(required_cols) - set(self.correlative_sample.columns)
            if missing:
                self.logger.warning(f"Columnas faltantes en correlativa: {missing}")

        # Validar golden records
        if not self.golden_records.empty:
            required_cols = ["ID_GRUPO", "CONFIDENCE_SCORE"]
            missing = set(required_cols) - set(self.golden_records.columns)
            if missing:
                self.logger.warning(f"Columnas faltantes en golden records: {missing}")

    def _load_data_sample(self, data_ref: pd.DataFrame | str, table_name: str) -> pd.DataFrame:
        """
        Carga datos de forma inteligente con manejo robusto de errores.
        Versión mejorada con soporte para más formatos.
        """
        try:
            # Si ya es DataFrame, gestionar muestra
            if isinstance(data_ref, pd.DataFrame):
                self.logger.debug(f"Usando DataFrame en memoria para '{table_name}'")
                if len(data_ref) > self.sample_size:
                    self.logger.info(
                        f"Dataset grande detectado. Tomando muestra de {self.sample_size:,} registros"
                    )
                    # Muestra estratificada si es posible
                    if "SRC" in data_ref.columns and table_name == "correlative_table":
                        return self._stratified_sample(data_ref, "SRC", self.sample_size)
                    return data_ref.sample(n=self.sample_size, random_state=42)
                return data_ref

            # Si es una ruta, cargar según el tipo
            if isinstance(data_ref, str) and os.path.exists(data_ref):
                self.logger.info(
                    f"Cargando '{table_name}' desde archivo: {os.path.basename(data_ref)}"
                )

                # SQLite
                if data_ref.endswith(".db"):
                    return self._load_from_sqlite(data_ref, table_name)

                # Parquet
                elif data_ref.endswith(".parquet"):
                    df = pd.read_parquet(data_ref)
                    return self._apply_sample_limit(df, table_name)

                # CSV (normal o comprimido)
                elif data_ref.endswith((".csv", ".csv.gz", ".csv.zip")):
                    # Para archivos grandes, usar chunks
                    if os.path.getsize(data_ref) > 100 * 1024 * 1024:  # > 100MB
                        return self._load_csv_chunked(data_ref)
                    df = pd.read_csv(data_ref)
                    return self._apply_sample_limit(df, table_name)

                # Excel
                elif data_ref.endswith((".xlsx", ".xls")):
                    df = pd.read_excel(data_ref)
                    return self._apply_sample_limit(df, table_name)

                # HDF5
                elif data_ref.endswith(".h5"):
                    df = pd.read_hdf(data_ref, key=table_name)
                    return self._apply_sample_limit(df, table_name)

        except Exception as e:
            self.logger.error(f"Error cargando '{table_name}': {e!s}")
            self.logger.debug("Stack trace:", exc_info=True)

        # Retornar DataFrame vacío con estructura mínima si falla
        self.logger.warning(f"No se pudo cargar '{table_name}', usando DataFrame vacío")
        return self._create_empty_dataframe(table_name)

    def _load_from_sqlite(self, db_path: str, table_name: str) -> pd.DataFrame:
        """Carga datos desde SQLite con consulta optimizada."""
        try:
            with open_readonly_sqlite(db_path) as conn:
                quoted_table = quote_existing_table(conn, table_name)
                sample_size = validate_row_limit(self.sample_size)

                # Cargar muestra con consulta optimizada
                if table_name == "correlative_table":
                    # Para correlativa, muestra estratificada por fuente
                    query = f"""
                    WITH sampled AS (
                        SELECT *, ROW_NUMBER() OVER (PARTITION BY SRC ORDER BY RANDOM()) as rn
                        FROM {quoted_table}
                    )
                    SELECT * FROM sampled
                    WHERE rn <= ?
                    LIMIT ?
                    """
                    params = (sample_size // 10, sample_size)
                else:
                    # Para otras tablas, muestra aleatoria simple
                    query = f"SELECT * FROM {quoted_table} LIMIT ?"
                    params = (sample_size,)

                return pd.read_sql_query(query, conn, params=params)

        except Exception as e:
            self.logger.error(f"Error en consulta SQLite: {e!s}")
            return self._create_empty_dataframe(table_name)

    def _load_csv_chunked(self, csv_path: str) -> pd.DataFrame:
        """Carga CSV grande por chunks para optimizar memoria."""
        chunks = []
        rows_loaded = 0

        for chunk in pd.read_csv(csv_path, chunksize=self.chunk_size):
            chunks.append(chunk)
            rows_loaded += len(chunk)
            if rows_loaded >= self.sample_size:
                break

        if chunks:
            df = pd.concat(chunks, ignore_index=True)
            return df.head(self.sample_size)

        return pd.DataFrame()

    def _stratified_sample(
        self, df: pd.DataFrame, stratify_col: str, n_samples: int
    ) -> pd.DataFrame:
        """Realiza muestreo estratificado preservando proporciones."""
        try:
            # Calcular proporción de cada estrato
            strata_props = df[stratify_col].value_counts(normalize=True)

            # Muestrear de cada estrato
            samples = []
            for stratum, prop in strata_props.items():
                stratum_df = df[df[stratify_col] == stratum]
                n_stratum_samples = max(1, int(n_samples * prop))
                n_stratum_samples = min(n_stratum_samples, len(stratum_df))

                samples.append(stratum_df.sample(n=n_stratum_samples, random_state=42))

            return pd.concat(samples, ignore_index=True)

        except Exception as e:
            self.logger.warning(f"Error en muestreo estratificado: {e!s}")
            return df.sample(n=min(n_samples, len(df)), random_state=42)

    def _apply_sample_limit(self, df: pd.DataFrame, table_name: str) -> pd.DataFrame:
        """Aplica límite de muestra a un DataFrame."""
        if len(df) > self.sample_size:
            self.logger.info(f"Aplicando límite de {self.sample_size:,} registros a {table_name}")
            if "SRC" in df.columns and table_name == "correlative_table":
                return self._stratified_sample(df, "SRC", self.sample_size)
            return df.sample(n=self.sample_size, random_state=42)
        return df

    def _create_empty_dataframe(self, table_name: str) -> pd.DataFrame:
        """Crea DataFrame vacío con estructura esperada."""
        if table_name == "correlative_table":
            return pd.DataFrame(columns=["ID_GRUPO", "SRC", "NIT", "RAZON_SOCIAL"])
        elif table_name == "golden_records":
            return pd.DataFrame(
                columns=["ID_GRUPO", "CONFIDENCE_SCORE", "NIT_FINAL", "RAZON_SOCIAL_FINAL"]
            )
        else:
            return pd.DataFrame()

    def generate_all_reports(self) -> dict[str, pd.DataFrame]:
        """
        Genera todos los reportes disponibles con manejo robusto de errores.
        Versión mejorada con más reportes y mejor gestión de memoria.
        """
        self.logger.info("Iniciando generación de reportes...")

        # Definir todos los reportes disponibles (orden optimizado)
        report_generators = [
            ("resumen_ejecutivo", self._generate_executive_summary, True),  # Siempre generar
            ("metricas_calidad", self._generate_quality_metrics, True),  # Siempre generar
            ("analisis_fuentes", self._generate_source_analysis, False),  # Solo si hay datos
            ("casos_revision", self._generate_review_cases, False),  # Solo si hay datos
            ("estadisticas_grupos", self._generate_group_statistics, False),  # Solo si hay datos
            ("metricas_performance", self._generate_performance_metrics, True),  # Siempre generar
        ]

        reports = {}
        successful = 0
        failed = 0
        skipped = 0

        # Generar cada reporte
        for report_name, generator_func, always_generate in report_generators:
            try:
                # Verificar si debe generarse
                if not always_generate and self._should_skip_report(report_name):
                    self.logger.info(f"⏭️  {report_name}: Omitido (datos insuficientes)")
                    skipped += 1
                    continue

                self.logger.info(f"Generando reporte: {report_name}...")

                # Generar reporte
                report_df = generator_func()

                # Validar resultado
                if report_df is not None and not report_df.empty:
                    reports[report_name] = report_df
                    successful += 1
                    self.logger.info(f"✓ {report_name}: {len(report_df)} filas generadas")
                else:
                    self.logger.warning(f"✗ {report_name}: Sin datos para generar")
                    failed += 1

            except Exception as e:
                failed += 1
                self.logger.error(f"✗ {report_name}: Error - {e!s}", exc_info=True)
                # Crear reporte de error
                reports[report_name] = pd.DataFrame({"Error": [f"Error generando reporte: {e!s}"]})

        # Liberar memoria después de generar reportes
        gc.collect()

        self.logger.info(
            f"Generación completada: {successful} exitosos, {failed} fallidos, {skipped} omitidos"
        )

        return reports

    def _should_skip_report(self, report_name: str) -> bool:
        """Determina si un reporte debe omitirse por falta de datos."""
        if report_name in ["analisis_fuentes", "estadisticas_grupos"]:
            return self.correlative_sample.empty
        elif report_name in ["metricas_calidad", "casos_revision"]:
            return self.golden_records.empty
        return False

    def _generate_executive_summary(self) -> pd.DataFrame:
        """
        Genera resumen ejecutivo con métricas principales.
        Versión mejorada con más métricas y formato profesional.
        """
        try:
            summary_data = []

            # --- MÉTRICAS DE VOLUMEN ---
            total_records = self.metrics.get("total_records", 0)
            unique_groups = self.metrics.get("unique_groups", 0)

            # Contar fuentes si están disponibles
            num_sources = 0
            if not self.correlative_sample.empty and "SRC" in self.correlative_sample.columns:
                num_sources = self.correlative_sample["SRC"].nunique()

            summary_data.extend(
                [
                    {
                        "Categoría": "VOLUMEN",
                        "Métrica": "Total Registros Procesados",
                        "Valor": f"{total_records:,}",
                        "Descripción": "Número total de registros de entrada",
                    },
                    {
                        "Categoría": "VOLUMEN",
                        "Métrica": "Entidades Únicas Identificadas",
                        "Valor": f"{unique_groups:,}",
                        "Descripción": "Grupos únicos después del linkage",
                    },
                    {
                        "Categoría": "VOLUMEN",
                        "Métrica": "Fuentes de Datos",
                        "Valor": str(num_sources) if num_sources > 0 else "N/A",
                        "Descripción": "Número de fuentes procesadas",
                    },
                    {
                        "Categoría": "VOLUMEN",
                        "Métrica": "Entidades Multi-fuente",
                        "Valor": f"{self.metrics.get('multi_source_groups', 0):,}",
                        "Descripción": "Grupos con registros de múltiples fuentes",
                    },
                ]
            )

            # --- MÉTRICAS DE EFICIENCIA ---
            linkage_rate = self.metrics.get("linkage_rate", 0)
            reduction_rate = self.metrics.get("reduction_rate", 0)

            summary_data.extend(
                [
                    {
                        "Categoría": "EFICIENCIA",
                        "Métrica": "Tasa de Linkage",
                        "Valor": f"{linkage_rate:.2%}",
                        "Descripción": "Porcentaje de registros vinculados",
                    },
                    {
                        "Categoría": "EFICIENCIA",
                        "Métrica": "Tasa de Reducción",
                        "Valor": f"{reduction_rate:.2%}",
                        "Descripción": "Reducción lograda por deduplicación",
                    },
                    {
                        "Categoría": "EFICIENCIA",
                        "Métrica": "Candidatos Encontrados",
                        "Valor": f"{self.metrics.get('candidates_found', 0):,}",
                        "Descripción": "Pares candidatos identificados por LSH",
                    },
                    {
                        "Categoría": "EFICIENCIA",
                        "Métrica": "Pares Evaluados",
                        "Valor": f"{self.metrics.get('pairs_scored', 0):,}",
                        "Descripción": "Pares que pasaron scoring detallado",
                    },
                ]
            )

            # --- MÉTRICAS DE CALIDAD ---
            if not self.golden_records.empty and "CONFIDENCE_SCORE" in self.golden_records.columns:
                scores = self.golden_records["CONFIDENCE_SCORE"].dropna()
                if len(scores) > 0:
                    summary_data.extend(
                        [
                            {
                                "Categoría": "CALIDAD",
                                "Métrica": "Confidence Promedio",
                                "Valor": f"{scores.mean():.3f}",
                                "Descripción": "Score promedio de confianza",
                            },
                            {
                                "Categoría": "CALIDAD",
                                "Métrica": "Registros Alta Confianza (>0.9)",
                                "Valor": f"{(scores > 0.9).sum():,}",
                                "Descripción": "Grupos con confidence superior a 0.9",
                            },
                            {
                                "Categoría": "CALIDAD",
                                "Métrica": "Casos para Revisión (<0.75)",
                                "Valor": f"{(scores < 0.75).sum():,}",
                                "Descripción": "Grupos que requieren revisión manual",
                            },
                        ]
                    )

            # --- MÉTRICAS DE RENDIMIENTO ---
            exec_time = self.metrics.get("execution_time", 0)
            if exec_time > 0:
                throughput = total_records / exec_time if total_records > 0 else 0

                # Formato de tiempo adaptativo
                if exec_time < 60:
                    time_str = f"{exec_time:.1f} segundos"
                elif exec_time < 3600:
                    time_str = f"{exec_time / 60:.1f} minutos"
                else:
                    time_str = f"{exec_time / 3600:.1f} horas"

                summary_data.extend(
                    [
                        {
                            "Categoría": "RENDIMIENTO",
                            "Métrica": "Tiempo Total",
                            "Valor": time_str,
                            "Descripción": "Duración total del proceso",
                        },
                        {
                            "Categoría": "RENDIMIENTO",
                            "Métrica": "Throughput",
                            "Valor": f"{throughput:.0f} reg/s",
                            "Descripción": "Velocidad de procesamiento",
                        },
                        {
                            "Categoría": "RENDIMIENTO",
                            "Métrica": "Memoria Máxima",
                            "Valor": f"{self.metrics.get('max_memory_gb', 0):.1f} GB",
                            "Descripción": "Uso máximo de memoria",
                        },
                    ]
                )

            return pd.DataFrame(summary_data)

        except Exception as e:
            self.logger.error(f"Error en resumen ejecutivo: {e!s}")
            return pd.DataFrame(
                {
                    "Categoría": ["ERROR"],
                    "Métrica": ["Error generando resumen"],
                    "Valor": [str(e)],
                    "Descripción": ["Revisar logs para más detalles"],
                }
            )

    def _generate_quality_metrics(self) -> pd.DataFrame:
        """
        Genera reporte detallado de métricas de calidad.
        Incluye análisis de distribución de confidence scores.
        """
        try:
            if self.golden_records.empty or "CONFIDENCE_SCORE" not in self.golden_records.columns:
                return pd.DataFrame({"Mensaje": ["No hay datos de confidence score disponibles"]})

            scores = self.golden_records["CONFIDENCE_SCORE"].dropna()
            if scores.empty:
                return pd.DataFrame({"Mensaje": ["Todos los confidence scores son nulos"]})

            # Definir rangos de confianza mejorados
            bins = [0, 0.5, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0]
            labels = [
                "Muy Baja (<0.5)",
                "Baja (0.5-0.7)",
                "Media (0.7-0.75)",
                "Aceptable (0.75-0.8)",
                "Buena (0.8-0.85)",
                "Alta (0.85-0.9)",
                "Muy Alta (0.9-0.95)",
                "Excelente (0.95-1.0)",
            ]

            # Categorizar scores
            categories = pd.cut(scores, bins=bins, labels=labels, include_lowest=True)
            distribution = categories.value_counts().sort_index()

            # Construir reporte detallado
            quality_data = []
            total = len(scores)
            cumulative = 0

            for label in labels:
                count = distribution.get(label, 0)
                cumulative += count
                pct = (count / total * 100) if total > 0 else 0
                cum_pct = (cumulative / total * 100) if total > 0 else 0

                # Determinar acción recomendada según el nivel
                if "Muy Baja" in label or "Baja" in label:
                    accion = "Revisión urgente"
                elif "Media" in label:
                    accion = "Revisión recomendada"
                elif "Aceptable" in label:
                    accion = "Revisión opcional"
                else:
                    accion = "No requiere revisión"

                quality_data.append(
                    {
                        "Nivel de Confianza": label,
                        "Cantidad": count,
                        "Porcentaje": f"{pct:.1f}%",
                        "Acumulado": f"{cum_pct:.1f}%",
                        "Acción Recomendada": accion,
                    }
                )

            # Agregar estadísticas adicionales al final
            quality_data.extend(
                [
                    {
                        "Nivel de Confianza": "--- ESTADÍSTICAS ---",
                        "Cantidad": "",
                        "Porcentaje": "",
                        "Acumulado": "",
                        "Acción Recomendada": "",
                    },
                    {
                        "Nivel de Confianza": "Media",
                        "Cantidad": f"{scores.mean():.3f}",
                        "Porcentaje": "",
                        "Acumulado": "",
                        "Acción Recomendada": "",
                    },
                    {
                        "Nivel de Confianza": "Mediana",
                        "Cantidad": f"{scores.median():.3f}",
                        "Porcentaje": "",
                        "Acumulado": "",
                        "Acción Recomendada": "",
                    },
                    {
                        "Nivel de Confianza": "Desviación Estándar",
                        "Cantidad": f"{scores.std():.3f}",
                        "Porcentaje": "",
                        "Acumulado": "",
                        "Acción Recomendada": "",
                    },
                ]
            )

            return pd.DataFrame(quality_data)

        except Exception as e:
            self.logger.error(f"Error en métricas de calidad: {e!s}")
            return pd.DataFrame({"Error": [f"Error generando métricas: {e!s}"]})

    def _generate_source_analysis(self) -> pd.DataFrame:
        """
        Analiza contribución y calidad por fuente de datos.
        Versión optimizada para datasets grandes.
        """
        try:
            # Para datasets grandes, intentar consulta directa si es posible
            if isinstance(self.correlative_data_ref, str) and self.correlative_data_ref.endswith(
                ".db"
            ):
                return self._generate_source_analysis_from_db()

            # Análisis con datos en memoria
            if self.correlative_sample.empty or "SRC" not in self.correlative_sample.columns:
                return pd.DataFrame({"Mensaje": ["No hay datos de fuentes disponibles"]})

            # Análisis básico por fuente
            source_stats = (
                self.correlative_sample.groupby("SRC")
                .agg(Total_Registros=("ID_GRUPO", "count"), Grupos_Unicos=("ID_GRUPO", "nunique"))
                .reset_index()
            )

            # Calcular métricas adicionales
            source_stats["Registros_por_Grupo"] = (
                source_stats["Total_Registros"] / source_stats["Grupos_Unicos"]
            ).round(2)

            # Calcular porcentaje del total
            total_registros = source_stats["Total_Registros"].sum()
            source_stats["Porcentaje_Total"] = (
                source_stats["Total_Registros"] / total_registros * 100
            ).round(1).astype(str) + "%"

            # Si tenemos golden records, agregar confidence promedio por fuente
            if not self.golden_records.empty and "PRIMARY_SOURCE" in self.golden_records.columns:
                confidence_by_source = (
                    self.golden_records.groupby("PRIMARY_SOURCE")["CONFIDENCE_SCORE"]
                    .mean()
                    .round(3)
                )

                source_stats = source_stats.merge(
                    confidence_by_source.rename("Confidence_Promedio"),
                    left_on="SRC",
                    right_index=True,
                    how="left",
                )

            # Ordenar por total de registros
            source_stats = source_stats.sort_values("Total_Registros", ascending=False)

            # Renombrar columnas para presentación
            source_stats = source_stats.rename(columns={"SRC": "Fuente"})

            return source_stats

        except Exception as e:
            self.logger.error(f"Error en análisis de fuentes: {e!s}")
            return pd.DataFrame({"Error": [f"Error analizando fuentes: {e!s}"]})

    def _generate_source_analysis_from_db(self) -> pd.DataFrame:
        """Genera análisis de fuentes directamente desde base de datos."""
        try:
            with open_readonly_sqlite(self.correlative_data_ref) as conn:
                quoted_table = quote_existing_table(conn, "correlative_table")
                # Consulta optimizada con todas las métricas
                query = f"""
                WITH source_stats AS (
                    SELECT
                        SRC as Fuente,
                        COUNT(*) as Total_Registros,
                        COUNT(DISTINCT ID_GRUPO) as Grupos_Unicos,
                        CAST(COUNT(*) AS FLOAT) / COUNT(DISTINCT ID_GRUPO) as Registros_por_Grupo
                    FROM {quoted_table}
                    GROUP BY SRC
                ),
                totals AS (
                    SELECT SUM(Total_Registros) as total_general
                    FROM source_stats
                )
                SELECT
                    s.*,
                    ROUND(s.Registros_por_Grupo, 2) as Registros_por_Grupo,
                    ROUND(100.0 * s.Total_Registros / t.total_general, 1) || '%' as Porcentaje_Total
                FROM source_stats s
                CROSS JOIN totals t
                ORDER BY s.Total_Registros DESC
                """

                return pd.read_sql_query(query, conn)

        except Exception as e:
            self.logger.error(f"Error en consulta SQL de fuentes: {e!s}")
            # Fallback a análisis en memoria
            return self._generate_source_analysis()

    def _generate_review_cases(self) -> pd.DataFrame:
        """
        Genera lista priorizada de casos para revisión manual.
        Incluye múltiples criterios de priorización.
        """
        try:
            if self.golden_records.empty:
                return pd.DataFrame({"Mensaje": ["No hay golden records para analizar"]})

            # Copiar para no modificar original
            review_df = self.golden_records.copy()

            # Calcular score de prioridad compuesto
            review_df["REVIEW_PRIORITY"] = 0

            # Factor 1: Baja confianza (peso 40%)
            if "CONFIDENCE_SCORE" in review_df.columns:
                review_df["LOW_CONFIDENCE_FACTOR"] = (
                    1 - review_df["CONFIDENCE_SCORE"].fillna(1)
                ) * 40
                review_df["REVIEW_PRIORITY"] += review_df["LOW_CONFIDENCE_FACTOR"]

            # Factor 2: Muchas variaciones de nombres (peso 25%)
            if "NAME_VARIATIONS" in review_df.columns:
                review_df["NAME_VAR_FACTOR"] = (review_df["NAME_VARIATIONS"].fillna(0) / 10).clip(
                    0, 1
                ) * 25
                review_df["REVIEW_PRIORITY"] += review_df["NAME_VAR_FACTOR"]

            # Factor 3: Variaciones de NIT (peso 20%)
            if "NIT_VARIATIONS" in review_df.columns:
                review_df["NIT_VAR_FACTOR"] = (review_df["NIT_VARIATIONS"].fillna(0) / 5).clip(
                    0, 1
                ) * 20
                review_df["REVIEW_PRIORITY"] += review_df["NIT_VAR_FACTOR"]

            # Factor 4: Tamaño del grupo (peso 15%)
            if "RECORD_COUNT" in review_df.columns:
                review_df["SIZE_FACTOR"] = (review_df["RECORD_COUNT"].fillna(0) / 50).clip(
                    0, 1
                ) * 15
                review_df["REVIEW_PRIORITY"] += review_df["SIZE_FACTOR"]

            # Filtrar casos que requieren revisión (score > 20)
            review_cases = review_df[review_df["REVIEW_PRIORITY"] > 20].copy()

            if review_cases.empty:
                return pd.DataFrame(
                    {
                        "Mensaje": ["✅ No hay casos que requieran revisión urgente"],
                        "Detalle": ["Todos los grupos tienen alta confianza"],
                    }
                )

            # Clasificar severidad
            review_cases["SEVERIDAD"] = pd.cut(
                review_cases["REVIEW_PRIORITY"],
                bins=[0, 30, 50, 70, 100],
                labels=["BAJA", "MEDIA", "ALTA", "CRÍTICA"],
            )

            # Ordenar por prioridad descendente
            review_cases = review_cases.sort_values("REVIEW_PRIORITY", ascending=False)

            # Seleccionar columnas relevantes para el reporte
            columns_to_include = []

            # Columnas básicas
            basic_cols = [
                "ID_GRUPO",
                "NIT_FINAL",
                "RAZON_SOCIAL_FINAL",
                "CONFIDENCE_SCORE",
                "SEVERIDAD",
                "REVIEW_PRIORITY",
            ]
            columns_to_include.extend([col for col in basic_cols if col in review_cases.columns])

            # Columnas de detalle
            detail_cols = [
                "SOURCES_COUNT",
                "RECORD_COUNT",
                "NAME_VARIATIONS",
                "NIT_VARIATIONS",
                "PRIMARY_SOURCE",
            ]
            columns_to_include.extend([col for col in detail_cols if col in review_cases.columns])

            # Limitar a top 1000 casos para no sobrecargar
            final_report = review_cases[columns_to_include].head(1000)

            # Agregar columna de razón principal
            final_report["RAZON_PRINCIPAL"] = final_report.apply(
                self._determine_main_review_reason, axis=1
            )

            return final_report

        except Exception as e:
            self.logger.error(f"Error en casos de revisión: {e!s}")
            return pd.DataFrame({"Error": [f"Error generando casos de revisión: {e!s}"]})

    def _determine_main_review_reason(self, row) -> str:
        """Determina la razón principal para revisar un caso."""
        reasons = []

        if "LOW_CONFIDENCE_FACTOR" in row and row["LOW_CONFIDENCE_FACTOR"] > 15:
            reasons.append("Baja confianza")
        if "NAME_VAR_FACTOR" in row and row["NAME_VAR_FACTOR"] > 10:
            reasons.append("Múltiples nombres")
        if "NIT_VAR_FACTOR" in row and row["NIT_VAR_FACTOR"] > 8:
            reasons.append("Múltiples NITs")
        if "SIZE_FACTOR" in row and row["SIZE_FACTOR"] > 7:
            reasons.append("Grupo grande")

        return " | ".join(reasons) if reasons else "Revisar detalles"

    def _generate_group_statistics(self) -> pd.DataFrame:
        """
        Genera estadísticas detalladas sobre la distribución de grupos.
        Optimizado para grandes volúmenes.
        """
        try:
            # Intentar consulta directa a BD si está disponible
            if isinstance(self.correlative_data_ref, str) and self.correlative_data_ref.endswith(
                ".db"
            ):
                return self._generate_group_statistics_from_db()

            # Análisis con datos en memoria
            if self.correlative_sample.empty or "ID_GRUPO" not in self.correlative_sample.columns:
                return pd.DataFrame({"Mensaje": ["No hay datos de grupos disponibles"]})

            # Calcular tamaños de grupos
            group_sizes = self.correlative_sample.groupby("ID_GRUPO").size()

            # Categorizar tamaños con rangos más detallados
            bins = [0, 1, 2, 3, 5, 10, 20, 50, 100, float("inf")]
            labels = [
                "1 (Singleton)",
                "2 (Par)",
                "3",
                "4-5",
                "6-10",
                "11-20",
                "21-50",
                "51-100",
                ">100",
            ]

            # 1. Guardar las categorías de cada grupo
            size_categories = pd.cut(group_sizes, bins=bins, labels=labels)

            # 2. Contar cuántos hay en cada categoría
            size_dist = size_categories.value_counts().sort_index()

            # Construir DataFrame de estadísticas
            stats_data = []
            total_groups = size_dist.sum()
            total_records = group_sizes.sum()
            cumulative_groups = 0
            cumulative_records = 0

            for label, count in size_dist.items():
                if count == 0:
                    continue

                # Calcular registros en esta categoría
                if label == "1 (Singleton)":
                    records_in_category = count * 1
                elif label == "2 (Par)":
                    records_in_category = count * 2
                else:
                    # ✅ CORRECCIÓN: Usar la variable `size_categories` para filtrar correctamente
                    range_groups = group_sizes[size_categories == label]
                    records_in_category = range_groups.sum() if len(range_groups) > 0 else 0

                cumulative_groups += count
                cumulative_records += records_in_category

                pct_groups = (count / total_groups * 100) if total_groups > 0 else 0
                pct_records = (
                    (records_in_category / total_records * 100) if total_records > 0 else 0
                )
                cum_pct_groups = (cumulative_groups / total_groups * 100) if total_groups > 0 else 0

                stats_data.append(
                    {
                        "Tamaño": label,
                        "Cantidad_Grupos": count,
                        "Porcentaje_Grupos": f"{pct_groups:.1f}%",
                        "Registros_Estimados": records_in_category,
                        "Porcentaje_Registros": f"{pct_records:.1f}%",
                        "Acumulado_Grupos": f"{cum_pct_groups:.1f}%",
                    }
                )

            # Agregar resumen al final
            stats_data.append(
                {
                    "Tamaño": "--- TOTALES ---",
                    "Cantidad_Grupos": total_groups,
                    "Porcentaje_Grupos": "100.0%",
                    "Registros_Estimados": total_records,
                    "Porcentaje_Registros": "100.0%",
                    "Acumulado_Grupos": "100.0%",
                }
            )

            return pd.DataFrame(stats_data)

        except Exception as e:
            self.logger.error(f"Error en estadísticas de grupos: {e!s}")
            return pd.DataFrame({"Error": [f"Error generando estadísticas: {e!s}"]})

    def _generate_group_statistics_from_db(self) -> pd.DataFrame:
        """Genera estadísticas de grupos directamente desde base de datos."""
        try:
            with open_readonly_sqlite(self.correlative_data_ref) as conn:
                quoted_table = quote_existing_table(conn, "correlative_table")
                # Consulta SQL optimizada para estadísticas de grupos
                query = f"""
                WITH group_sizes AS (
                    SELECT ID_GRUPO, COUNT(*) as size
                    FROM {quoted_table}
                    GROUP BY ID_GRUPO
                ),
                categorized AS (
                    SELECT
                        CASE
                            WHEN size = 1 THEN '1 (Singleton)'
                            WHEN size = 2 THEN '2 (Par)'
                            WHEN size = 3 THEN '3'
                            WHEN size <= 5 THEN '4-5'
                            WHEN size <= 10 THEN '6-10'
                            WHEN size <= 20 THEN '11-20'
                            WHEN size <= 50 THEN '21-50'
                            WHEN size <= 100 THEN '51-100'
                            ELSE '>100'
                        END as Tamaño,
                        COUNT(*) as Cantidad_Grupos,
                        SUM(size) as Registros_Totales
                    FROM group_sizes
                    GROUP BY Tamaño
                ),
                totals AS (
                    SELECT
                        SUM(Cantidad_Grupos) as total_grupos,
                        SUM(Registros_Totales) as total_registros
                    FROM categorized
                )
                SELECT
                    c.Tamaño,
                    c.Cantidad_Grupos,
                    ROUND(100.0 * c.Cantidad_Grupos / t.total_grupos, 1) || '%' as Porcentaje_Grupos,
                    c.Registros_Totales as Registros_Estimados,
                    ROUND(100.0 * c.Registros_Totales / t.total_registros, 1) || '%' as Porcentaje_Registros
                FROM categorized c
                CROSS JOIN totals t
                ORDER BY
                    CASE c.Tamaño
                        WHEN '1 (Singleton)' THEN 1
                        WHEN '2 (Par)' THEN 2
                        WHEN '3' THEN 3
                        WHEN '4-5' THEN 4
                        WHEN '6-10' THEN 5
                        WHEN '11-20' THEN 6
                        WHEN '21-50' THEN 7
                        WHEN '51-100' THEN 8
                        ELSE 9
                    END
                """

                df = pd.read_sql_query(query, conn)

                # Calcular acumulados
                df["Acumulado_Grupos"] = df["Cantidad_Grupos"].cumsum()
                total = df["Cantidad_Grupos"].sum()
                df["Acumulado_Grupos"] = (df["Acumulado_Grupos"] / total * 100).round(1).astype(
                    str
                ) + "%"

                return df

        except Exception as e:
            self.logger.error(f"Error en consulta SQL de grupos: {e!s}")
            return self._generate_group_statistics()

    def _generate_performance_metrics(self) -> pd.DataFrame:
        """
        Genera reporte de métricas de rendimiento del proceso.
        Nuevo reporte que no estaba en la versión original.
        """
        try:
            perf_data = []

            # Tiempos por fase
            phase_times = {
                "Carga y Validación": self.metrics.get("load_validate", 0),
                "Preprocesamiento": self.metrics.get("preprocessing_time", 0),
                "Generación Candidatos": self.metrics.get("candidate_generation_time", 0),
                "Scoring": self.metrics.get("scoring_time", 0),
                "Clustering": self.metrics.get("clustering_time", 0),
                "Golden Records": self.metrics.get("golden_records_time", 0),
                "Exportación": self.metrics.get("export_time", 0),
            }

            total_time = sum(phase_times.values())

            for phase, time_val in phase_times.items():
                if time_val > 0:
                    percentage = (time_val / total_time * 100) if total_time > 0 else 0

                    # Formato adaptativo de tiempo
                    if time_val < 1:
                        time_str = f"{time_val * 1000:.0f} ms"
                    elif time_val < 60:
                        time_str = f"{time_val:.1f} seg"
                    else:
                        time_str = f"{time_val / 60:.1f} min"

                    perf_data.append(
                        {
                            "Fase": phase,
                            "Tiempo": time_str,
                            "Porcentaje": f"{percentage:.1f}%",
                            "Velocidad": self._calculate_phase_speed(phase, time_val),
                        }
                    )

            # Agregar métricas generales
            perf_data.extend(
                [
                    {
                        "Fase": "--- TOTALES ---",
                        "Tiempo": f"{total_time:.1f} seg"
                        if total_time < 60
                        else f"{total_time / 60:.1f} min",
                        "Porcentaje": "100.0%",
                        "Velocidad": f"{self.metrics.get('total_records', 0) / (total_time + 1):.0f} reg/seg",
                    }
                ]
            )

            return pd.DataFrame(perf_data)

        except Exception as e:
            self.logger.error(f"Error en métricas de performance: {e!s}")
            return pd.DataFrame({"Error": [f"Error generando métricas de performance: {e!s}"]})

    def _calculate_phase_speed(self, phase: str, time_val: float) -> str:
        """Calcula velocidad específica por fase."""
        if time_val == 0:
            return "N/A"

        total_records = self.metrics.get("total_records", 0)

        if phase == "Generación Candidatos":
            items = self.metrics.get("candidates_found", 0)
            speed = items / time_val
            return f"{speed:.0f} candidatos/seg"
        elif phase == "Scoring":
            items = self.metrics.get("pairs_scored", 0)
            speed = items / time_val
            return f"{speed:.0f} pares/seg"
        elif phase == "Golden Records":
            items = self.metrics.get("unique_groups", 0)
            speed = items / time_val
            return f"{speed:.0f} grupos/seg"
        else:
            speed = total_records / time_val
            return f"{speed:.0f} reg/seg"
