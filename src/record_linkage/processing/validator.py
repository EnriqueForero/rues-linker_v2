"""
processing.validator — record_linkage_pipeline

Componentes:
    - class DataValidator  (origen: notebook celda [115])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..utils.logger import CustomLogger
from ..utils.performance import track_performance
from .nit import NitProcessor
from .text import TextProcessor


class DataValidator:
    """
    Validador de calidad de datos con reglas configurables y reportes detallados.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.logger = CustomLogger("DataValidator")

        # Reglas de validación
        self.validation_rules = self.config.get(
            "validation_rules",
            {
                "min_nit_length": 6,
                "max_nit_length": 15,
                "min_name_length": 5,
                "max_name_length": 300,
                "remove_test_data": True,
                "remove_invalid_nits": True,
            },
        )

        # Umbrales de calidad
        self.quality_thresholds = self.config.get(
            "quality_thresholds",
            {
                "min_coverage": 0.05,
                "max_group_size": 100,
                "review_threshold": 0.75,
                "min_confidence": 0.50,
                "min_quality_score": 0.30,
            },
        )

        # Procesadores auxiliares
        self.nit_processor = NitProcessor(config)
        self.text_processor = TextProcessor()

    @track_performance("Validación de esquema")
    def validate_schema(self, df: pd.DataFrame, required_columns: list[str]) -> dict[str, Any]:
        """
        Validar que el DataFrame tenga las columnas requeridas.

        Args:
            df: DataFrame a validar
            required_columns: Lista de columnas requeridas

        Returns:
            Diccionario con resultados de validación
        """
        validation_result = {
            "is_valid": True,
            "missing_columns": [],
            "extra_columns": [],
            "column_types": {},
            "recommendations": [],
        }

        # Verificar columnas faltantes
        df_columns = set(df.columns)
        required_set = set(required_columns)

        missing = required_set - df_columns
        if missing:
            validation_result["is_valid"] = False
            validation_result["missing_columns"] = list(missing)
            validation_result["recommendations"].append(
                f"Agregar columnas faltantes: {', '.join(missing)}"
            )

        # Identificar columnas extra
        extra = df_columns - required_set
        if extra:
            validation_result["extra_columns"] = list(extra)

        # Analizar tipos de datos
        for col in df.columns:
            validation_result["column_types"][col] = str(df[col].dtype)

        return validation_result

    @track_performance("Validación de calidad de datos")
    def validate_data_quality(self, df: pd.DataFrame, source_name: str = "data") -> dict[str, Any]:
        """
        Realizar validación completa de calidad de datos.

        Args:
            df: DataFrame a validar
            source_name: Nombre de la fuente para el reporte

        Returns:
            Diccionario con métricas de calidad
        """
        self.logger.info(f"Validando calidad de {len(df):,} registros de '{source_name}'")

        quality_report = {
            "source": source_name,
            "total_records": len(df),
            "valid_records": 0,
            "quality_score": 0.0,
            "issues": [],
            "warnings": [],
            "statistics": {},
            "recommendations": [],
        }

        # Validar NITs si existe la columna
        if "NIT" in df.columns:
            nit_validation = self._validate_nits(df)
            quality_report["statistics"]["nit"] = nit_validation

            # Agregar issues si hay problemas
            if nit_validation["invalid_rate"] > 0.2:
                quality_report["issues"].append(
                    f"Alto porcentaje de NITs inválidos: {nit_validation['invalid_rate']:.1%}"
                )

            if nit_validation["null_rate"] > 0.3:
                quality_report["warnings"].append(
                    f"Muchos NITs nulos: {nit_validation['null_rate']:.1%}"
                )

        # Validar nombres si existe la columna
        if "RAZON_SOCIAL" in df.columns:
            name_validation = self._validate_names(df)
            quality_report["statistics"]["name"] = name_validation

            # Agregar issues si hay problemas
            if name_validation["invalid_rate"] > 0.2:
                quality_report["issues"].append(
                    f"Alto porcentaje de nombres inválidos: {name_validation['invalid_rate']:.1%}"
                )

        # Detectar duplicados
        duplicate_analysis = self._analyze_duplicates(df)
        quality_report["statistics"]["duplicates"] = duplicate_analysis

        if duplicate_analysis["exact_duplicate_rate"] > 0.1:
            quality_report["warnings"].append(
                f"Alta tasa de duplicados exactos: {duplicate_analysis['exact_duplicate_rate']:.1%}"
            )

        # Calcular registros válidos
        valid_mask = self._get_valid_records_mask(df)
        quality_report["valid_records"] = valid_mask.sum()

        # Calcular quality score global
        quality_report["quality_score"] = self._calculate_quality_score(quality_report, len(df))

        # Generar recomendaciones
        quality_report["recommendations"] = self._generate_recommendations(quality_report)

        self.logger.info(
            f"Validación completada. Quality Score: {quality_report['quality_score']:.2f}"
        )

        return quality_report

    def _validate_nits(self, df: pd.DataFrame) -> dict[str, Any]:
        """Validar columna de NITs."""
        nit_stats = {
            "total": len(df),
            "non_null": df["NIT"].notna().sum(),
            "null": df["NIT"].isna().sum(),
            "unique": df["NIT"].nunique(),
            "valid": 0,
            "invalid": 0,
            "test_nits": 0,
            "length_distribution": {},
        }

        # Procesar NITs
        if "NIT" in df.columns:
            nit_results = self.nit_processor.process_series(df["NIT"])

            nit_stats["valid"] = nit_results["IS_VALID"].sum()
            nit_stats["invalid"] = (~nit_results["IS_VALID"]).sum()
            nit_stats["test_nits"] = (nit_results["NIT_TYPE"] == "TEST").sum()

            # Distribución de longitudes
            lengths = nit_results["NIT_ORIGINAL"].str.len()
            nit_stats["length_distribution"] = lengths.value_counts().to_dict()

        # Calcular tasas
        nit_stats["null_rate"] = nit_stats["null"] / nit_stats["total"]
        nit_stats["invalid_rate"] = (
            nit_stats["invalid"] / nit_stats["non_null"] if nit_stats["non_null"] > 0 else 0
        )
        nit_stats["uniqueness_rate"] = (
            nit_stats["unique"] / nit_stats["non_null"] if nit_stats["non_null"] > 0 else 0
        )

        return nit_stats

    def _validate_names(self, df: pd.DataFrame) -> dict[str, Any]:
        """Validar columna de nombres/razón social."""
        name_stats = {
            "total": len(df),
            "non_null": df["RAZON_SOCIAL"].notna().sum(),
            "null": df["RAZON_SOCIAL"].isna().sum(),
            "unique": df["RAZON_SOCIAL"].nunique(),
            "valid": 0,
            "invalid": 0,
            "too_short": 0,
            "too_long": 0,
            "length_stats": {},
        }

        # Validar longitudes
        if "RAZON_SOCIAL" in df.columns:
            lengths = df["RAZON_SOCIAL"].fillna("").str.len()

            min_len = self.validation_rules["min_name_length"]
            max_len = self.validation_rules["max_name_length"]

            name_stats["too_short"] = (lengths < min_len).sum()
            name_stats["too_long"] = (lengths > max_len).sum()
            name_stats["valid"] = ((lengths >= min_len) & (lengths <= max_len)).sum()
            name_stats["invalid"] = name_stats["total"] - name_stats["valid"]

            # Estadísticas de longitud
            name_stats["length_stats"] = {
                "mean": lengths.mean(),
                "median": lengths.median(),
                "min": lengths.min(),
                "max": lengths.max(),
                "std": lengths.std(),
            }

        # Calcular tasas
        name_stats["null_rate"] = name_stats["null"] / name_stats["total"]
        name_stats["invalid_rate"] = name_stats["invalid"] / name_stats["total"]
        name_stats["uniqueness_rate"] = (
            name_stats["unique"] / name_stats["non_null"] if name_stats["non_null"] > 0 else 0
        )

        return name_stats

    def _analyze_duplicates(self, df: pd.DataFrame) -> dict[str, Any]:
        """Analizar duplicados en el DataFrame."""
        duplicate_stats = {
            "total_records": len(df),
            "exact_duplicates": 0,
            "nit_duplicates": 0,
            "name_duplicates": 0,
            "cross_duplicates": 0,
        }

        # Duplicados exactos (todas las columnas)
        duplicate_stats["exact_duplicates"] = df.duplicated().sum()

        # Duplicados por NIT
        if "NIT" in df.columns:
            duplicate_stats["nit_duplicates"] = df.duplicated(subset=["NIT"], keep=False).sum()

        # Duplicados por nombre
        if "RAZON_SOCIAL" in df.columns:
            duplicate_stats["name_duplicates"] = df.duplicated(
                subset=["RAZON_SOCIAL"], keep=False
            ).sum()

        # Duplicados cruzados (mismo NIT Y nombre)
        if "NIT" in df.columns and "RAZON_SOCIAL" in df.columns:
            duplicate_stats["cross_duplicates"] = df.duplicated(
                subset=["NIT", "RAZON_SOCIAL"], keep=False
            ).sum()

        # Calcular tasas
        total = duplicate_stats["total_records"]
        duplicate_stats["exact_duplicate_rate"] = duplicate_stats["exact_duplicates"] / total
        duplicate_stats["nit_duplicate_rate"] = duplicate_stats["nit_duplicates"] / total
        duplicate_stats["name_duplicate_rate"] = duplicate_stats["name_duplicates"] / total
        duplicate_stats["cross_duplicate_rate"] = duplicate_stats["cross_duplicates"] / total

        return duplicate_stats

    def _get_valid_records_mask(self, df: pd.DataFrame) -> pd.Series:
        """Obtener máscara de registros válidos."""
        valid_mask = pd.Series(True, index=df.index)

        # Validar NITs si existe la columna
        if "NIT" in df.columns:
            # Procesar NITs para obtener validez
            nit_results = self.nit_processor.process_series(df["NIT"])
            valid_nit_mask = nit_results["IS_VALID"]

            # Excluir NITs de prueba si está configurado
            if self.validation_rules.get("remove_test_data", True):
                valid_nit_mask &= nit_results["NIT_TYPE"] != "TEST"

            valid_mask &= valid_nit_mask

        # Validar nombres si existe la columna
        if "RAZON_SOCIAL" in df.columns:
            lengths = df["RAZON_SOCIAL"].fillna("").str.len()
            valid_name_mask = (lengths >= self.validation_rules["min_name_length"]) & (
                lengths <= self.validation_rules["max_name_length"]
            )
            valid_mask &= valid_name_mask

        # Al menos NIT o nombre debe ser válido
        if "NIT" in df.columns and "RAZON_SOCIAL" in df.columns:
            at_least_one = (df["NIT"].notna() & valid_nit_mask) | (
                df["RAZON_SOCIAL"].notna() & valid_name_mask
            )
            valid_mask &= at_least_one

        return valid_mask

    def _calculate_quality_score(self, quality_report: dict[str, Any], total_records: int) -> float:
        """Calcular score de calidad global."""
        score = 1.0

        # Penalizar por registros inválidos
        valid_rate = quality_report["valid_records"] / total_records if total_records > 0 else 0
        score *= valid_rate

        # Penalizar por NITs inválidos
        if "nit" in quality_report["statistics"]:
            nit_stats = quality_report["statistics"]["nit"]
            if nit_stats["non_null"] > 0:
                nit_valid_rate = 1 - nit_stats["invalid_rate"]
                score *= 0.7 + 0.3 * nit_valid_rate  # Peso del 30%

        # Penalizar por nombres inválidos
        if "name" in quality_report["statistics"]:
            name_stats = quality_report["statistics"]["name"]
            name_valid_rate = 1 - name_stats["invalid_rate"]
            score *= 0.7 + 0.3 * name_valid_rate  # Peso del 30%

        # Penalizar por duplicados
        if "duplicates" in quality_report["statistics"]:
            dup_stats = quality_report["statistics"]["duplicates"]
            no_dup_rate = 1 - dup_stats["exact_duplicate_rate"]
            score *= 0.9 + 0.1 * no_dup_rate  # Peso del 10%

        return round(score, 4)

    def _generate_recommendations(self, quality_report: dict[str, Any]) -> list[str]:
        """Generar recomendaciones basadas en el análisis de calidad."""
        recommendations = []

        # CORRECCIÓN: Obtener total_records del quality_report
        total_records = quality_report.get(
            "total_records", 1
        )  # Usar 1 como default para evitar división por cero

        # Basadas en quality score
        if quality_report["quality_score"] < 0.3:
            recommendations.append(
                "⚠️ Calidad muy baja. Considerar revisar fuente de datos o criterios de validación."
            )
        elif quality_report["quality_score"] < 0.7:
            recommendations.append("📊 Calidad mejorable. Revisar los issues identificados.")

        # Basadas en NITs
        if "nit" in quality_report["statistics"]:
            nit_stats = quality_report["statistics"]["nit"]

            if nit_stats["invalid_rate"] > 0.3:
                recommendations.append("🔢 Revisar formato de NITs. Muchos no cumplen validación.")

            if nit_stats["test_nits"] > 100:
                recommendations.append(
                    "🧪 Detectados muchos NITs de prueba. Considerar filtrarlos."
                )

            if nit_stats["uniqueness_rate"] < 0.8:
                recommendations.append("🔄 Muchos NITs duplicados. Verificar si es esperado.")

        # Basadas en nombres
        if "name" in quality_report["statistics"]:
            name_stats = quality_report["statistics"]["name"]

            if name_stats["too_short"] > total_records * 0.1:  # AHORA FUNCIONA
                recommendations.append("📝 Muchos nombres muy cortos. Revisar calidad de datos.")

            if name_stats["null_rate"] > 0.3:
                recommendations.append(
                    "❓ Alta tasa de nombres nulos. Verificar completitud de datos."
                )

        # Basadas en duplicados
        if "duplicates" in quality_report["statistics"]:
            dup_stats = quality_report["statistics"]["duplicates"]

            if dup_stats["exact_duplicate_rate"] > 0.1:
                recommendations.append(
                    "♻️ Considerar eliminar duplicados exactos antes del procesamiento."
                )

        return recommendations if recommendations else ["✅ Calidad de datos aceptable"]

    def run_quality_checks(
        self, df: pd.DataFrame, source_name: str = "data", fix_issues: bool = False
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """
        Ejecutar verificaciones de calidad y opcionalmente corregir issues.

        Args:
            df: DataFrame a verificar
            source_name: Nombre de la fuente
            fix_issues: Si se deben aplicar correcciones automáticas

        Returns:
            Tupla con DataFrame (posiblemente corregido) y reporte de calidad
        """
        # Ejecutar validación
        quality_report = self.validate_data_quality(df, source_name)

        if not fix_issues:
            return df, quality_report

        # Aplicar correcciones si está habilitado
        df_fixed = df.copy()
        fixes_applied = []

        # Filtrar registros inválidos si la calidad es muy baja
        if quality_report["quality_score"] < self.quality_thresholds["min_quality_score"]:
            valid_mask = self._get_valid_records_mask(df_fixed)
            records_before = len(df_fixed)
            df_fixed = df_fixed[valid_mask].copy()
            records_after = len(df_fixed)

            if records_before > records_after:
                fixes_applied.append(
                    f"Filtrados {records_before - records_after:,} registros inválidos"
                )

        # Eliminar duplicados exactos
        if "duplicates" in quality_report["statistics"]:
            dup_stats = quality_report["statistics"]["duplicates"]
            if dup_stats["exact_duplicate_rate"] > 0.05:
                records_before = len(df_fixed)
                df_fixed = df_fixed.drop_duplicates()
                records_after = len(df_fixed)

                if records_before > records_after:
                    fixes_applied.append(
                        f"Eliminados {records_before - records_after:,} duplicados exactos"
                    )

        # Agregar fixes al reporte
        quality_report["fixes_applied"] = fixes_applied

        if fixes_applied:
            self.logger.info(f"Correcciones aplicadas: {'; '.join(fixes_applied)}")

            # Recalcular quality score después de fixes
            quality_report_after = self.validate_data_quality(df_fixed, source_name)
            quality_report["quality_score_after_fixes"] = quality_report_after["quality_score"]

        return df_fixed, quality_report
