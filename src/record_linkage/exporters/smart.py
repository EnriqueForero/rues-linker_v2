"""
exporters.smart — record_linkage_pipeline

Componentes:
    - class SmartExporter  (origen: notebook celda [111])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import gc
import gzip
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from tqdm import tqdm

from ..utils._globals import performance_tracker
from ..utils.logger import CustomLogger
from ..utils.performance import track_performance
from ._spreadsheet import (
    escape_spreadsheet_value,
    prepare_spreadsheet_data,
    safe_sheet_name,
    validate_leaf_name,
)


class SmartExporter:
    """
    Exportador inteligente que selecciona el formato óptimo según el tamaño
    y características de los datos.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        # Usamos un diccionario vacío como fallback seguro
        self.config = config or {}
        self.logger = CustomLogger("SmartExporter")

        # Extraer parámetros directamente de la configuración recibida
        # con valores por defecto claros si no se encuentran.
        self.max_excel_size_mb = self.config.get("max_file_size_mb", 100)
        self.compress_large_files = self.config.get("compress_large_files", True)
        self.escape_spreadsheet_formulas = self.config.get("escape_spreadsheet_formulas", True)

        # Lógica de prioridad explícita para el directorio de salida
        self.output_dir = self.config.get("output_directory", "resultados_por_defecto")

        # Log para confirmar la ruta que se está usando
        self.logger.info(f"SmartExporter inicializado. Directorio de salida: '{self.output_dir}'")

        # Crear directorio de salida si no existe
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

    @track_performance("Exportación de datos")
    def export(
        self,
        data: pd.DataFrame | dict[str, pd.DataFrame],
        filename: str,
        format: str = "auto",
        **kwargs,
    ) -> str | list[str]:
        """
        Exportar datos con formato óptimo.

        Args:
            data: DataFrame o diccionario de DataFrames
            filename: Nombre base del archivo (sin extensión)
            format: 'auto', 'excel', 'csv', 'parquet', 'csv.zip'
            **kwargs: Argumentos adicionales para los métodos de pandas

        Returns:
            Ruta(s) del archivo(s) exportado(s)
        """
        if isinstance(data, dict):
            return self._export_multiple(data, filename, format, **kwargs)
        else:
            return self._export_single(data, filename, format, **kwargs)

    def _export_single(self, df: pd.DataFrame | str, filename: str, format: str, **kwargs) -> str:
        """
        Exporta un DataFrame (o una ruta a BD) a disco en el formato óptimo.

        – Si *df* es una cadena, se asume que es una ruta a una base de datos y
          se delega a `export_from_db`, omitiendo la estimación de tamaño.
        – Si *df* es un DataFrame:
            * Estima su tamaño para decidir el formato (`auto`).
            * Convierte columnas categóricas a string antes de Parquet.
            * Fuerza columnas de texto a string antes de Parquet.
            * Maneja excepciones Parquet con _fallback_ a CSV comprimido.
            * Soporta CSV, CSV.zip, CSV.gz y Excel (con modo streaming).
        Devuelve la ruta absoluta del archivo exportado.
        """
        # ---------------------------------------------------------------------
        # 1. Ruta a BD → delegar a export_from_db
        # ---------------------------------------------------------------------
        if isinstance(df, str):
            self.logger.info(f"Exportando desde la base de datos: {df}")
            return self.export_from_db(df, filename, format=format)

        # ---------------------------------------------------------------------
        # 2. Estimación de tamaño y selección de formato
        # ---------------------------------------------------------------------
        size_mb = self._estimate_size_mb(df)
        self.logger.info(f"Exportando {filename}: {len(df):,} filas, ~{size_mb:.1f} MB")

        if format == "auto":
            format = self._determine_format(df, size_mb)
            self.logger.info(f"Formato seleccionado automáticamente: {format}")

        filepath = self._get_filepath(filename, format)

        # ---------------------------------------------------------------------
        # 3. Exportación según formato
        # ---------------------------------------------------------------------
        try:
            if format == "parquet":
                df_parquet = df.copy()

                # --- CAMBIO 1: columnas 'category' → 'object' ------------------
                for col in df_parquet.select_dtypes(include=["category"]).columns:
                    df_parquet[col] = df_parquet[col].astype("object")
                # ----------------------------------------------------------------

                # --- Limpieza de columnas de texto -----------------------------
                string_columns = [
                    "NIT_FINAL",
                    "NIT_OK",
                    "NIT_BASE",
                    "NIT",
                    "RAZON_SOCIAL_FINAL",
                    "RAZON_SOCIAL",
                    "NOMBRE_LIMPIO",
                    "PRIMARY_SOURCE",
                    "SOURCES_LIST",
                    "NIT_SOURCE",
                    "NAME_SOURCE",
                    "SELECTION_METHOD",
                    "SRC",
                ]
                for col in string_columns:
                    if col in df_parquet.columns:
                        df_parquet[col] = df_parquet[col].fillna("").astype(str).replace("nan", "")
                # ----------------------------------------------------------------

                # --- Intento principal con PyArrow -----------------------------
                try:
                    df_parquet.to_parquet(
                        filepath,
                        index=False,
                        engine="pyarrow",
                        coerce_timestamps="ms",
                        allow_truncated_timestamps=True,
                        **kwargs,
                    )
                except Exception as parquet_error:
                    self.logger.warning(f"Error Parquet: {parquet_error}", exc_info=True)
                    self.logger.info("Parquet falló; usando CSV.gz como alternativa…")
                    format = "csv.gz"
                    filepath = self._get_filepath(filename, format)
                    self._prepare_spreadsheet_data(df).to_csv(
                        filepath, index=False, compression="gzip"
                    )

            elif format == "csv.zip":
                compression_opts = {"method": "zip", "archive_name": f"{filename}.csv"}
                self._prepare_spreadsheet_data(df).to_csv(
                    filepath, index=False, compression=compression_opts, **kwargs
                )

            elif format == "csv.gz":
                self._prepare_spreadsheet_data(df).to_csv(
                    filepath, index=False, compression="gzip", **kwargs
                )

            elif format == "csv":
                self._prepare_spreadsheet_data(df).to_csv(filepath, index=False, **kwargs)

            elif format in ["excel", "xlsx"]:
                if size_mb > 50:
                    self._export_excel_chunked(df, filepath, **kwargs)
                else:
                    self._prepare_spreadsheet_data(df).to_excel(
                        filepath, index=False, engine="openpyxl", **kwargs
                    )

            else:
                raise ValueError(f"Formato no soportado: {format}")

            # -----------------------------------------------------------------
            # 4. Verificación de resultado
            # -----------------------------------------------------------------
            file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
            self.logger.info(f"✅ Exportado: {filepath} ({file_size_mb:.1f} MB en disco)")
            return filepath

        except Exception as e:
            self.logger.error(f"Error exportando {filename}: {e}", exc_info=True)
            raise

    def _export_multiple(
        self, dfs_dict: dict[str, pd.DataFrame], filename: str, format: str, **kwargs
    ) -> list[str]:
        """Exportar múltiples DataFrames."""
        total_size_mb = sum(self._estimate_size_mb(df) for df in dfs_dict.values())

        # Si el total es pequeño, usar Excel con múltiples hojas
        if format == "auto" and total_size_mb < self.max_excel_size_mb:
            return [self._export_excel_multisheet(dfs_dict, filename, **kwargs)]

        # Si no, exportar cada uno por separado
        exported_files = []
        for sheet_name, df in dfs_dict.items():
            individual_filename = f"{filename}_{sheet_name}"
            filepath = self._export_single(df, individual_filename, format, **kwargs)
            exported_files.append(filepath)

        return exported_files

    def _export_excel_multisheet(
        self, dfs_dict: dict[str, pd.DataFrame], filename: str, **kwargs
    ) -> str:
        """Exportar múltiples DataFrames en un Excel con varias hojas."""
        filepath = self._get_filepath(filename, "xlsx")

        with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
            used_sheet_names: set[str] = set()
            for sheet_name, df in dfs_dict.items():
                safe_sheet_name = self._safe_sheet_name(str(sheet_name), used_sheet_names)
                self._prepare_spreadsheet_data(df).to_excel(
                    writer, sheet_name=safe_sheet_name, index=False, **kwargs
                )

                # Ajustar anchos de columna
                worksheet = writer.sheets[safe_sheet_name]
                self._adjust_column_widths(worksheet, df)

        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        self.logger.info(
            f"✅ Excel multi-hoja exportado: {filepath} "
            f"({len(dfs_dict)} hojas, {file_size_mb:.1f} MB)"
        )

        return filepath

    def _export_excel_chunked(self, df: pd.DataFrame, filepath: str, **kwargs):
        """Exportar DataFrame grande a Excel por chunks para evitar problemas de memoria."""
        chunk_size = 50000

        with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
            if df.empty:
                self._prepare_spreadsheet_data(df).to_excel(
                    writer, sheet_name="Sheet1", index=False, **kwargs
                )
                return

            # Escribir por chunks
            for start_idx in range(0, len(df), chunk_size):
                end_idx = min(start_idx + chunk_size, len(df))
                self._prepare_spreadsheet_data(df.iloc[start_idx:end_idx]).to_excel(
                    writer,
                    sheet_name="Sheet1",
                    index=False,
                    startrow=0 if start_idx == 0 else start_idx + 1,
                    header=start_idx == 0,
                    **kwargs,
                )

                # Liberar memoria periódicamente
                if start_idx % (chunk_size * 5) == 0:
                    gc.collect()

    def _determine_format(self, df: pd.DataFrame, size_mb: float) -> str:
        """Determinar formato óptimo basado en características de los datos."""
        # Para DataFrames muy grandes
        if size_mb > 200:
            return "parquet" if self.compress_large_files else "csv.zip"

        # Para DataFrames grandes
        elif size_mb > self.max_excel_size_mb:
            return "csv.zip" if self.compress_large_files else "csv"

        # Para DataFrames con muchas columnas numéricas
        elif self._has_many_numeric_columns(df):
            return "parquet"  # Mejor compresión para datos numéricos

        # Por defecto, Excel para compatibilidad
        else:
            return "xlsx"

    def _estimate_size_mb(self, df: pd.DataFrame) -> float:
        """Estimar tamaño del DataFrame en MB."""
        return df.memory_usage(deep=True).sum() / (1024 * 1024)

    def _has_many_numeric_columns(self, df: pd.DataFrame) -> bool:
        """Verificar si el DataFrame tiene mayoría de columnas numéricas."""
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        return len(numeric_cols) > len(df.columns) * 0.7

    def _get_filepath(self, filename: str, format: str) -> str:
        """Build a path contained strictly inside ``output_dir``.

        ``filename`` is a leaf name, not a path. Rejecting separators instead
        of silently applying ``basename`` prevents overwrites outside the run
        directory and makes a caller mistake visible.
        """
        # Mapeo de formatos a extensiones
        extensions = {
            "excel": ".xlsx",
            "xlsx": ".xlsx",
            "csv": ".csv",
            "csv.zip": ".csv.zip",
            "csv.gz": ".csv.gz",
            "parquet": ".parquet",
        }

        extension = extensions.get(format, f".{format}")
        safe_name = self._validate_leaf_name(filename, "filename")
        root = Path(self.output_dir).expanduser().resolve()
        target = (root / f"{safe_name}{extension}").resolve()
        if target.parent != root:
            raise ValueError(f"filename sale del directorio de exportación: {filename!r}")
        return str(target)

    @staticmethod
    def _validate_leaf_name(value: str, label: str) -> str:
        """Validate a portable file/archive leaf name."""

        return validate_leaf_name(value, label)

    @staticmethod
    def _safe_sheet_name(value: str, used: set[str]) -> str:
        """Return a valid, unique Excel sheet name."""

        return safe_sheet_name(value, used)

    @staticmethod
    def _escape_spreadsheet_value(value: Any) -> Any:
        """Neutralize formula prefixes while preserving non-string values."""

        return escape_spreadsheet_value(value)

    def _prepare_spreadsheet_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Escape formula-like cells and headers with bounded extra memory.

        Numeric columns keep sharing their original buffers. Only a column
        containing a dangerous string is copied, which is important for free
        Colab runtimes and chunked database exports.
        """

        if not self.escape_spreadsheet_formulas:
            return df
        return prepare_spreadsheet_data(df)

    @classmethod
    def _sqlite_order_clause(cls, conn: sqlite3.Connection, table_name: str) -> str:
        """Return deterministic ordering for normal and WITHOUT ROWID tables."""

        ddl_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        ).fetchone()
        if not ddl_row or "WITHOUT ROWID" not in str(ddl_row[0]).upper():
            # Preserve the historical insertion order for ordinary tables.
            return " ORDER BY rowid"

        table_info = conn.execute("SELECT * FROM pragma_table_info(?)", (table_name,)).fetchall()
        primary_key = sorted(
            ((int(row[5]), str(row[1])) for row in table_info if int(row[5]) > 0),
            key=lambda item: item[0],
        )
        if primary_key:
            columns = ", ".join(cls._quote_sqlite_identifier(name) for _, name in primary_key)
            return f" ORDER BY {columns}"

        # SQLite itself rejects WITHOUT ROWID tables without a primary key;
        # this guard is defensive for malformed/exotic files.
        raise ValueError(f"Tabla WITHOUT ROWID sin clave primaria exportable: {table_name!r}")

    @staticmethod
    def _quote_sqlite_identifier(value: str) -> str:
        """Quote a validated SQLite identifier; values still use parameters."""

        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError(f"Identificador SQLite inválido: {value!r}")
        return '"' + value.replace('"', '""') + '"'

    @classmethod
    def _resolve_table_name(cls, conn: sqlite3.Connection, table_name: str | None) -> str:
        """Resolve an actual table name before interpolating it as identifier."""

        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT IN ('metadata', 'sqlite_sequence') "
            "ORDER BY name"
        ).fetchall()
        available = [str(row[0]) for row in rows]
        if table_name is None:
            if not available:
                raise ValueError("No se encontraron tablas exportables en la base SQLite")
            return available[0]
        if table_name not in available:
            raise ValueError(
                f"Tabla SQLite no encontrada: {table_name!r}. Disponibles: {available}"
            )
        return table_name

    def _adjust_column_widths(self, worksheet, df: pd.DataFrame):
        """Ajustar anchos de columna en Excel para mejor visualización."""
        from openpyxl.utils import get_column_letter

        for idx, col in enumerate(df.columns):
            # Acceso posicional: una etiqueta duplicada devolvería un
            # DataFrame con ``df[col]``. ``get_column_letter`` soporta AA, AB,
            # ... y no se limita a las 26 columnas de chr(65 + idx).
            lengths = df.iloc[:, idx].astype("string").str.len()
            observed = lengths.max(skipna=True)
            content_length = int(observed) if pd.notna(observed) else 0
            max_length = max(content_length, len(str(col)))
            # Limitar ancho máximo
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[get_column_letter(idx + 1)].width = adjusted_width

    def export_summary_report(self, results: dict[str, Any], filename: str = "resumen_ejecutivo"):
        """Exportar reporte resumen en formato optimizado."""
        summary_data = {
            "Metricas_Principales": self._create_metrics_summary(results),
            "Calidad_Datos": self._create_quality_summary(results),
            "Performance": performance_tracker.get_summary(),
        }

        # Agregar más hojas si existen en results
        if "validation_report" in results:
            summary_data["Validacion"] = results["validation_report"]

        if "coverage_report" in results:
            summary_data["Cobertura"] = results["coverage_report"]

        return self.export(summary_data, filename, format="auto")

    def _create_metrics_summary(self, results: dict[str, Any]) -> pd.DataFrame:
        """Crear resumen de métricas principales."""
        metrics = results.get("metrics", {})

        summary = {
            "Métrica": [
                "Total Registros",
                "Entidades Únicas",
                "Tasa de Linkage",
                "Tasa de Reducción",
                "Tiempo Total (min)",
                # El alias heredado `max_memory_gb` viene en GiB (rss_pico_mib / 1024).
                "Memoria Máxima (GiB)",
            ],
            "Valor": [
                f"{metrics.get('total_records', 0):,}",
                f"{metrics.get('unique_groups', 0):,}",
                f"{metrics.get('linkage_rate', 0):.2%}",
                f"{metrics.get('reduction_rate', 0):.2%}",
                f"{metrics.get('execution_time', 0) / 60:.1f}",
                f"{metrics.get('max_memory_gb', 0):.1f}",
            ],
        }

        return pd.DataFrame(summary)

    def _create_quality_summary(self, results: dict[str, Any]) -> pd.DataFrame:
        """Crear resumen de calidad de datos."""
        golden_records = results.get("golden_records", pd.DataFrame())

        if golden_records.empty:
            return pd.DataFrame()

        quality_summary = {
            "Indicador": [
                "Confidence Score Promedio",
                "Grupos Multi-fuente",
                "Grupos con Alta Confianza (>0.9)",
                "Grupos que Requieren Revisión",
                "Variaciones de NIT Promedio",
                "Variaciones de Nombre Promedio",
            ],
            "Valor": [
                f"{golden_records['CONFIDENCE_SCORE'].mean():.3f}",
                f"{(golden_records['SOURCES_COUNT'] > 1).sum():,}",
                f"{(golden_records['CONFIDENCE_SCORE'] > 0.9).sum():,}",
                f"{(golden_records['CONFIDENCE_SCORE'] < 0.75).sum():,}",
                f"{golden_records['NIT_VARIATIONS'].mean():.1f}",
                f"{golden_records['NAME_VARIATIONS'].mean():.1f}",
            ],
        }

        return pd.DataFrame(quality_summary)

    # Agregar estos métodos a la clase SmartExporter existente

    def export_from_db(
        self, db_path: str, filename: str, format: str = "auto", table_name: str | None = None
    ) -> str:
        """
        Exportar datos directamente desde una base de datos SQLite.

        Args:
            db_path: Ruta a la base de datos SQLite
            filename: Nombre base del archivo de salida
            format: Formato de exportación ('auto', 'csv', 'xlsx', etc.)
            table_name: Nombre de la tabla a exportar (auto-detecta si no se especifica)

        Returns:
            Ruta del archivo exportado
        """
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Base de datos no encontrada: {db_path}")

        filename = self._validate_leaf_name(filename, "filename")
        # URI read-only: exportar nunca debe poder modificar la base de origen.
        db_uri = Path(db_path).expanduser().resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True)
        try:
            cursor = conn.cursor()
            table_name = self._resolve_table_name(conn, table_name)
            quoted_table = self._quote_sqlite_identifier(table_name)

            cursor.execute(f"SELECT COUNT(*) FROM {quoted_table}")
            total_rows = int(cursor.fetchone()[0])

            cursor.execute("SELECT * FROM pragma_table_info(?)", (table_name,))
            n_columns = len(cursor.fetchall())
            estimated_size_mb = (total_rows * n_columns * 50) / (1024 * 1024)

            self.logger.info(
                f"Exportando tabla '{table_name}': {total_rows:,} filas "
                f"(~{estimated_size_mb:.1f} MB estimados)"
            )

            if format == "auto":
                format = self._determine_format_for_db(total_rows, estimated_size_mb)
                self.logger.info(f"Formato seleccionado automáticamente: {format}")

            if format in ["csv", "csv.gz", "csv.zip"]:
                return self._export_csv_from_db(conn, table_name, filename, format)
            if format in ["excel", "xlsx"]:
                return self._export_excel_from_db(conn, table_name, filename, total_rows)
            if format == "parquet":
                return self._export_parquet_from_db(conn, table_name, filename)
            raise ValueError(f"Formato no soportado para BD: {format}")
        finally:
            conn.close()

    def _determine_format_for_db(self, n_rows: int, size_mb: float) -> str:
        """Determinar formato óptimo para exportar desde BD."""
        # Para datasets muy grandes, usar CSV comprimido
        if n_rows > 5_000_000 or size_mb > 1000:
            return "csv.gz"

        # Para datasets que no caben en Excel
        elif n_rows > 1_048_576:
            return "csv.zip"

        # Para datasets medianos
        elif n_rows > 100_000 or size_mb > 50:
            return "csv"

        # Para datasets pequeños, Excel
        else:
            return "xlsx"

    def _export_csv_from_db(
        self, conn: sqlite3.Connection, table_name: str, filename: str, format: str
    ) -> str:
        """Exportar tabla a CSV con streaming."""
        # Determinar archivo y función de apertura
        quoted_table = self._quote_sqlite_identifier(table_name)
        if format == "csv.gz":
            filepath = self._get_filepath(filename, "csv.gz")

            def open_func(f):
                return gzip.open(f, "wt", encoding="utf-8", newline="")
        elif format == "csv.zip":
            filepath = self._get_filepath(filename, "csv.zip")
            # Para zip necesitamos un enfoque diferente
            return self._export_csv_zip_from_db(conn, table_name, filename)
        else:
            filepath = self._get_filepath(filename, "csv")

            def open_func(f):
                return open(f, "w", encoding="utf-8", newline="")

        cursor = conn.cursor()

        # Obtener total para barra de progreso
        cursor.execute(f"SELECT COUNT(*) FROM {quoted_table}")
        total_rows = cursor.fetchone()[0]

        # Exportar por chunks
        chunk_size = 50_000
        first_chunk = True
        order_clause = self._sqlite_order_clause(conn, table_name)
        query = f"SELECT * FROM {quoted_table}{order_clause}"

        with open_func(filepath) as f:
            with tqdm(total=total_rows, desc=f"Exportando {os.path.basename(filepath)}") as pbar:
                for chunk_df in pd.read_sql_query(query, conn, chunksize=chunk_size):
                    # Escribir chunk
                    self._prepare_spreadsheet_data(chunk_df).to_csv(
                        f, index=False, header=first_chunk
                    )
                    first_chunk = False

                    # Actualizar progreso
                    pbar.update(len(chunk_df))

                    # Liberar memoria
                    del chunk_df

        # Verificar archivo
        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        self.logger.info(f"✅ Exportado: {filepath} ({file_size_mb:.1f} MB)")

        return filepath

    def _export_csv_zip_from_db(
        self, conn: sqlite3.Connection, table_name: str, filename: str
    ) -> str:
        """Exportar a CSV dentro de un ZIP."""
        import zipfile

        filename = self._validate_leaf_name(filename, "filename")
        quoted_table = self._quote_sqlite_identifier(table_name)
        filepath = self._get_filepath(filename, "csv.zip")
        csv_filename = f"{filename}.csv"

        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {quoted_table}")
        total_rows = cursor.fetchone()[0]

        # Crear archivo temporal para CSV
        temp_handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", prefix="rues_export_", dir=self.output_dir, delete=False
        )
        temp_csv = temp_handle.name
        temp_handle.close()

        # Primero exportar a CSV temporal
        chunk_size = 50_000
        first_chunk = True
        order_clause = self._sqlite_order_clause(conn, table_name)
        query = f"SELECT * FROM {quoted_table}{order_clause}"

        try:
            with open(temp_csv, "w", encoding="utf-8", newline="") as f:
                with tqdm(total=total_rows, desc="Preparando CSV") as pbar:
                    for chunk_df in pd.read_sql_query(query, conn, chunksize=chunk_size):
                        self._prepare_spreadsheet_data(chunk_df).to_csv(
                            f, index=False, header=first_chunk
                        )
                        first_chunk = False
                        pbar.update(len(chunk_df))
                        del chunk_df

            self.logger.info("Comprimiendo CSV a ZIP...")
            with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(temp_csv, csv_filename)
        finally:
            Path(temp_csv).unlink(missing_ok=True)

        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        self.logger.info(f"✅ Exportado: {filepath} ({file_size_mb:.1f} MB)")

        return filepath

    def _export_excel_from_db(
        self, conn: sqlite3.Connection, table_name: str, filename: str, total_rows: int
    ) -> str:
        """Exportar a Excel con streaming usando openpyxl."""
        quoted_table = self._quote_sqlite_identifier(table_name)
        filepath = self._get_filepath(filename, "xlsx")

        # Limitar filas para Excel
        rows_to_export = min(total_rows, 1_048_576)

        if total_rows > rows_to_export:
            self.logger.warning(
                f"Excel tiene límite de 1,048,576 filas. "
                f"Exportando solo las primeras {rows_to_export:,} de {total_rows:,}"
            )

        # Usar openpyxl en modo write-only para eficiencia
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Font

        wb = Workbook(write_only=True)
        ws = wb.create_sheet(title="Data")

        cursor = conn.cursor()

        # Obtener nombres de columnas
        cursor.execute("SELECT * FROM pragma_table_info(?)", (table_name,))
        columns = [row[1] for row in cursor.fetchall()]

        # Escribir encabezados con formato
        header_cells = []
        for col_name in columns:
            cell = WriteOnlyCell(ws, value=self._escape_spreadsheet_value(col_name))
            cell.font = Font(bold=True)
            header_cells.append(cell)
        ws.append(header_cells)

        # Escribir datos por chunks
        chunk_size = 10_000
        rows_written_total = 0
        order_clause = self._sqlite_order_clause(conn, table_name)
        cursor.execute(f"SELECT * FROM {quoted_table}{order_clause} LIMIT ?", (rows_to_export,))

        with tqdm(total=rows_to_export, desc="Exportando a Excel") as pbar:
            while rows_written_total < rows_to_export:
                rows = cursor.fetchmany(min(chunk_size, rows_to_export - rows_written_total))
                if not rows:
                    break
                for row in rows:
                    ws.append(tuple(self._escape_spreadsheet_value(value) for value in row))
                rows_written_total += len(rows)
                pbar.update(len(rows))

                # Liberar memoria periódicamente
                if rows_written_total % 50_000 == 0:
                    gc.collect()

        # Guardar archivo
        self.logger.info("Guardando archivo Excel...")
        wb.save(filepath)
        wb.close()

        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        self.logger.info(f"✅ Exportado: {filepath} ({file_size_mb:.1f} MB)")

        return filepath

    def _export_parquet_from_db(
        self, conn: sqlite3.Connection, table_name: str, filename: str
    ) -> str:
        """Exportar a Parquet por chunks."""
        quoted_table = self._quote_sqlite_identifier(table_name)

        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {quoted_table}")
        total_rows = cursor.fetchone()[0]

        # Para Parquet, usar pyarrow si está disponible
        try:
            import pyarrow as pa  # noqa: F401  # detección de disponibilidad
            import pyarrow.parquet as pq  # noqa: F401  # detección de disponibilidad

            use_pyarrow = True
        except ImportError:
            use_pyarrow = False
            self.logger.warning("PyArrow no disponible, usando método alternativo")

        if use_pyarrow:
            # Método eficiente con PyArrow
            return self._export_parquet_pyarrow(conn, table_name, filename, total_rows)
        else:
            # Método con pandas (menos eficiente)
            return self._export_parquet_pandas(conn, table_name, filename, total_rows)

    def _export_parquet_pyarrow(
        self, conn: sqlite3.Connection, table_name: str, filename: str, total_rows: int
    ) -> str:
        """Exportar a Parquet usando PyArrow (más eficiente)."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        quoted_table = self._quote_sqlite_identifier(table_name)
        filepath = self._get_filepath(filename, "parquet")

        chunk_size = 100_000
        writer = None
        order_clause = self._sqlite_order_clause(conn, table_name)
        query = f"SELECT * FROM {quoted_table}{order_clause}"

        try:
            with tqdm(total=total_rows, desc="Exportando a Parquet") as pbar:
                for chunk_df in pd.read_sql_query(query, conn, chunksize=chunk_size):
                    table = pa.Table.from_pandas(chunk_df, preserve_index=False)
                    if writer is None:
                        writer = pq.ParquetWriter(filepath, table.schema, compression="snappy")
                    writer.write_table(table)
                    pbar.update(len(chunk_df))
                    del chunk_df, table
            if writer is None:
                empty_df = pd.read_sql_query(f"{query} LIMIT 0", conn)
                empty_table = pa.Table.from_pandas(empty_df, preserve_index=False)
                writer = pq.ParquetWriter(filepath, empty_table.schema, compression="snappy")
        finally:
            if writer:
                writer.close()

        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        self.logger.info(f"✅ Exportado: {filepath} ({file_size_mb:.1f} MB)")

        return filepath

    def _export_parquet_pandas(
        self, conn: sqlite3.Connection, table_name: str, filename: str, total_rows: int
    ) -> str:
        """Exportar a Parquet usando pandas (método alternativo)."""
        quoted_table = self._quote_sqlite_identifier(table_name)
        filepath = self._get_filepath(filename, "parquet")

        # Para datasets grandes, no es ideal pero funciona
        if total_rows > 1_000_000:
            self.logger.warning(
                "Exportando dataset grande a Parquet sin PyArrow. "
                "Considere instalar PyArrow para mejor rendimiento."
            )

        # Leer todo y exportar (no ideal para datasets muy grandes)
        df = pd.read_sql_query(f"SELECT * FROM {quoted_table}", conn)
        df.to_parquet(filepath, compression="snappy", index=False)

        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        self.logger.info(f"✅ Exportado: {filepath} ({file_size_mb:.1f} MB)")

        return filepath

    def export_with_auto_detection(self, data: pd.DataFrame | str, filename: str, **kwargs) -> str:
        """
        Método mejorado que detecta automáticamente el tipo de datos.

        Este método puede reemplazar al export() original para mantener
        compatibilidad hacia atrás.
        """
        if isinstance(data, str) and data.endswith(".db"):
            # Es una base de datos
            return self.export_from_db(data, filename, **kwargs)
        elif isinstance(data, pd.DataFrame):
            # Es un DataFrame, usar método original
            return self.export(data, filename, **kwargs)
        elif isinstance(data, dict):
            # Es un diccionario de DataFrames, usar método original
            return self.export(data, filename, **kwargs)
        else:
            raise ValueError(f"Tipo de datos no soportado: {type(data)}")
