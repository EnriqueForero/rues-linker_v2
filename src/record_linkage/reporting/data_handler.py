"""
reporting.data_handler — record_linkage_pipeline

Componentes:
    - class DataHandler  (origen: notebook celda [140])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pandas as pd

from ..utils.logger import CustomLogger
from ..utils.performance import track_performance


class DataHandler:
    """
    Manejador para cargar, validar y consolidar múltiples fuentes de datos.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.logger = CustomLogger("DataHandler")

    @track_performance("Carga de fuentes")
    def load_sources(
        self,
        sources: dict[str, str | pd.DataFrame],
        column_mapping: dict[str, dict[str, str]] | None = None,
        strict: bool | None = None,
    ) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
        """
        Cargar múltiples fuentes de datos.

        Args:
            sources: Diccionario {nombre_fuente: ruta_archivo o DataFrame}
            column_mapping: Mapeo de columnas por fuente

        Returns:
            Tupla con (fuentes_cargadas, reporte_carga). Por defecto cualquier
            fuente fallida aborta la carga completa; configure
            ``strict_source_loading=False`` o pase ``strict=False`` solo si
            acepta explícitamente un resultado parcial.
        """
        if strict is None:
            strict = bool(self.config.get("strict_source_loading", True))
        loaded_sources = {}
        load_report = {"sources_loaded": 0, "total_records": 0, "errors": [], "warnings": []}

        for source_name, source_data in sources.items():
            try:
                self.logger.info(f"Cargando fuente: {source_name}")

                # Cargar datos según el tipo
                if isinstance(source_data, pd.DataFrame):
                    df = source_data.copy()
                    self.logger.info(f"  DataFrame directo: {len(df):,} registros")
                elif isinstance(source_data, str):
                    df = self._load_from_file(source_data)
                    self.logger.info(f"  Archivo cargado: {len(df):,} registros")
                else:
                    raise ValueError(f"Tipo de fuente no soportado: {type(source_data)}")

                # Aplicar mapeo de columnas si existe
                if column_mapping and source_name in column_mapping:
                    mapping = column_mapping[source_name]
                    df = df.rename(columns=mapping)
                    self.logger.info(
                        f"  Columnas mapeadas: {list(mapping.keys())} -> {list(mapping.values())}"
                    )

                # Validaciones básicas
                self._validate_required_columns(df, source_name)

                # Limpiar datos básicos
                df = self._basic_cleanup(df)

                # Guardar fuente cargada
                loaded_sources[source_name] = df
                load_report["sources_loaded"] += 1
                load_report["total_records"] += len(df)

                self.logger.info(f"✅ {source_name} cargado exitosamente: {len(df):,} registros")

            except Exception as e:
                error_msg = f"Error cargando {source_name}: {e!s}"
                self.logger.error(error_msg)
                load_report["errors"].append(error_msg)

        if strict and load_report["errors"]:
            details = " | ".join(load_report["errors"])
            raise RuntimeError(
                "Carga de fuentes abortada para evitar un resultado parcial: " + details
            )

        return loaded_sources, load_report

    def _load_from_file(self, file_path: str) -> pd.DataFrame:
        """Cargar DataFrame desde archivo."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Archivo no encontrado: {file_path}")

        # Determinar tipo de archivo y cargar
        if file_path.endswith(".csv"):
            try:
                # Intenta primero con utf-8, que es el estándar
                df = pd.read_csv(file_path, dtype=str, encoding="utf-8")
            except UnicodeDecodeError:
                self.logger.warning(
                    f"Fallo al leer {os.path.basename(file_path)} como UTF-8. Intentando con 'latin1'."
                )
                # Si falla, intenta con latin1, que es la causa común del problema
                df = pd.read_csv(file_path, dtype=str, encoding="latin1")

        elif file_path.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file_path, dtype=str)
        elif file_path.endswith(".parquet"):
            df = pd.read_parquet(file_path)
        elif file_path.endswith(".txt"):
            # Intentar determinar separador
            df = pd.read_csv(file_path, dtype=str, sep=None, engine="python")
        else:
            raise ValueError(f"Formato de archivo no soportado: {file_path}")

        return df

    def _validate_required_columns(self, df: pd.DataFrame, source_name: str):
        """Validar que existan las columnas mínimas requeridas."""
        required_columns = ["NIT", "RAZON_SOCIAL"]
        missing_columns = set(required_columns) - set(df.columns)

        if missing_columns:
            # Intentar encontrar columnas similares
            suggestions = self._suggest_column_names(df.columns, missing_columns)
            error_msg = f"Columnas requeridas faltantes en {source_name}: {missing_columns}"
            if suggestions:
                error_msg += f". Posibles equivalentes: {suggestions}"
            raise ValueError(error_msg)

    def _suggest_column_names(
        self, available_columns: list[str], missing_columns: set[str]
    ) -> dict[str, str]:
        """Sugerir nombres de columnas similares."""
        suggestions = {}

        # Patrones comunes para cada columna requerida
        patterns = {
            "NIT": ["numero_identificacion", "identificacion", "nit_empresa", "documento"],
            "RAZON_SOCIAL": ["nombre", "razon_social", "empresa", "denominacion", "nombre_empresa"],
        }

        for missing in missing_columns:
            if missing in patterns:
                for col in available_columns:
                    col_lower = col.lower().replace("_", "").replace(" ", "")
                    for pattern in patterns[missing]:
                        pattern_clean = pattern.replace("_", "").replace(" ", "")
                        if pattern_clean in col_lower:
                            suggestions[missing] = col
                            break
                    if missing in suggestions:
                        break

        return suggestions

    def _basic_cleanup(self, df: pd.DataFrame) -> pd.DataFrame:
        """Limpieza básica de datos."""
        # Convertir a string
        for col in ["NIT", "RAZON_SOCIAL"]:
            if col in df.columns:
                df[col] = df[col].astype(str)

        # Eliminar espacios extra
        # (v2.1.0) Explicitar `object` y `str` separadamente para pandas 3.0:
        # en pandas 2.x `include=["object"]` selecciona ambos por compat, pero
        # esto se elimina en 3.0. Listar ambos preserva el comportamiento
        # actual y silencia Pandas4Warning.
        for col in df.select_dtypes(include=["object", "string"]).columns:
            df[col] = df[col].str.strip()

        # Reemplazar valores nulos comunes. Se fija explícitamente el
        # comportamiento de downcast para conservar la semántica de pandas 2.x
        # sin depender del valor por defecto que cambia en pandas 3.
        with pd.option_context("future.no_silent_downcasting", True):
            df = df.replace(["nan", "None", "NULL", ""], np.nan).infer_objects(copy=False)

        return df

    @track_performance("Consolidación de fuentes")
    def consolidate_sources(self, loaded_sources: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """
        Consolidar múltiples fuentes en un DataFrame único.

        VERSIÓN CORREGIDA V5.0:
        - Ahora crea explícitamente la columna 'ORIGINAL_INDEX', que es crucial
          para la trazabilidad en la fase de Golden Records.
        - El índice corresponde a la posición de la fila en el DataFrame consolidado.
        """
        if not loaded_sources:
            raise ValueError("No hay fuentes para consolidar")

        self.logger.info(f"Consolidando {len(loaded_sources)} fuentes")

        consolidated_dfs = []

        for source_name, df in loaded_sources.items():
            df_temp = df.copy()
            df_temp["SRC"] = source_name
            consolidated_dfs.append(df_temp)
            self.logger.info(f"  {source_name}: {len(df_temp):,} registros")

        # Combinar todas las fuentes
        df_consolidated = pd.concat(consolidated_dfs, ignore_index=True, sort=False)

        # ✅ CORRECCIÓN CLAVE: Crear la columna 'ORIGINAL_INDEX' a partir del índice numérico.
        # Esta línea resuelve el KeyError.
        df_consolidated["ORIGINAL_INDEX"] = df_consolidated.index

        self.logger.info(f"✅ Consolidación completada: {len(df_consolidated):,} registros totales")
        self.logger.info("   Columna 'ORIGINAL_INDEX' creada exitosamente.")

        return df_consolidated

    def get_data_profile(self, df: pd.DataFrame) -> dict[str, Any]:
        """Generar perfil de datos consolidados."""
        profile = {
            "total_records": len(df),
            "total_columns": len(df.columns),
            "sources": df["SRC"].value_counts().to_dict() if "SRC" in df.columns else {},
            "missing_data": {
                "NIT": df["NIT"].isna().sum() if "NIT" in df.columns else 0,
                "RAZON_SOCIAL": df["RAZON_SOCIAL"].isna().sum()
                if "RAZON_SOCIAL" in df.columns
                else 0,
            },
            "memory_usage_mb": df.memory_usage(deep=True).sum() / (1024**2),
        }

        return profile
