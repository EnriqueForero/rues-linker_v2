"""
pipeline.validator — record_linkage_pipeline

Componentes:
    - class PipelineValidator  (origen: notebook celda [144])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

from ..utils.logger import CustomLogger
from ..utils.output import safe_print as print


class PipelineValidator:
    """
    Validador de integridad del pipeline.
    Verifica que los resultados sean coherentes y correctos.
    """

    def __init__(self, results: dict[str, Any]):
        self.results = results
        self.logger = CustomLogger("PipelineValidator")
        self.validation_errors = []
        self.validation_warnings = []

    def validate_all(self) -> bool:
        """Ejecutar todas las validaciones."""
        self.logger.info("Iniciando validación de resultados del pipeline")

        # Lista de validaciones
        validations = [
            self._validate_no_records_lost,
            self._validate_group_consistency,
            self._validate_golden_records_completeness,
            self._validate_confidence_scores,
            self._validate_source_consistency,
            self._validate_output_files,
        ]

        # Ejecutar cada validación
        all_passed = True
        for validation in validations:
            try:
                if not validation():
                    all_passed = False
            except Exception as e:
                self.logger.error(f"Error en validación {validation.__name__}: {e}")
                self.validation_errors.append(f"Error en {validation.__name__}: {e!s}")
                all_passed = False

        # Resumen
        self._print_validation_summary()

        return all_passed

    def _validate_no_records_lost(self) -> bool:
        """Verificar que no se perdieron registros."""
        original_count = self.results["metrics"]["total_records"]
        linked_count = len(self.results.get("df_linked", []))

        if linked_count != original_count:
            self.validation_errors.append(
                f"Registros perdidos: original={original_count}, linked={linked_count}"
            )
            return False

        return True

    def _validate_group_consistency(self) -> bool:
        """Verificar consistencia de grupos."""
        df_linked = self.results.get("df_linked", pd.DataFrame())

        if df_linked.empty:
            return True

        # Verificar que todos los registros tengan ID_GRUPO
        if "ID_GRUPO" not in df_linked.columns:
            self.validation_errors.append("Columna ID_GRUPO faltante")
            return False

        null_groups = df_linked["ID_GRUPO"].isna().sum()
        if null_groups > 0:
            self.validation_errors.append(f"{null_groups} registros sin ID_GRUPO")
            return False

        return True

    def _validate_golden_records_completeness(self) -> bool:
        """Verificar que hay un golden record por grupo."""
        df_linked = self.results.get("df_linked", pd.DataFrame())
        golden_records = self.results.get("golden_records", pd.DataFrame())

        if df_linked.empty or golden_records.empty:
            return True

        groups_in_linked = df_linked["ID_GRUPO"].nunique()
        groups_in_golden = len(golden_records)

        if groups_in_linked != groups_in_golden:
            self.validation_errors.append(
                f"Inconsistencia en grupos: linked={groups_in_linked}, golden={groups_in_golden}"
            )
            return False

        return True

    def _validate_confidence_scores(self) -> bool:
        """Verificar que confidence scores están en rango válido."""
        golden_records = self.results.get("golden_records", pd.DataFrame())

        if golden_records.empty or "CONFIDENCE_SCORE" not in golden_records.columns:
            return True

        invalid_scores = golden_records[~golden_records["CONFIDENCE_SCORE"].between(0, 1)]

        if len(invalid_scores) > 0:
            self.validation_errors.append(
                f"{len(invalid_scores)} registros con confidence score inválido"
            )
            return False

        # Warning si muchos scores bajos
        low_confidence = (golden_records["CONFIDENCE_SCORE"] < 0.5).sum()
        if low_confidence > len(golden_records) * 0.3:
            self.validation_warnings.append(
                f"{low_confidence} grupos ({low_confidence / len(golden_records):.1%}) "
                f"con confidence < 0.5"
            )

        return True

    def _validate_source_consistency(self) -> bool:
        """Verificar consistencia de fuentes."""
        df_linked = self.results.get("df_linked", pd.DataFrame())
        golden_records = self.results.get("golden_records", pd.DataFrame())

        if "SRC" not in df_linked.columns:
            return True

        # Verificar que PRIMARY_SOURCE existe en las fuentes
        if not golden_records.empty and "PRIMARY_SOURCE" in golden_records.columns:
            valid_sources = set(df_linked["SRC"].unique())
            invalid_primary = golden_records[~golden_records["PRIMARY_SOURCE"].isin(valid_sources)]

            if len(invalid_primary) > 0:
                self.validation_errors.append(
                    f"{len(invalid_primary)} golden records con PRIMARY_SOURCE inválida"
                )
                return False

        return True

    def _validate_output_files(self) -> bool:
        """Verificar que se generaron los archivos de salida."""
        output_dir = self.results.get("config", {}).get("output_directory", "output")

        expected_files = ["golden_records", "tabla_correlativa", "dashboard_ejecutivo.png"]

        missing_files = []
        for base_name in expected_files:
            # Buscar archivos con diferentes extensiones
            found = False
            for ext in [".xlsx", ".csv", ".csv.zip", ".parquet", ".png"]:
                if os.path.exists(os.path.join(output_dir, base_name + ext)):
                    found = True
                    break

            if not found and base_name.endswith(".png"):
                if os.path.exists(os.path.join(output_dir, base_name)):
                    found = True

            if not found:
                missing_files.append(base_name)

        if missing_files:
            self.validation_warnings.append(f"Archivos de salida faltantes: {missing_files}")

        return True

    def _print_validation_summary(self):
        """Imprimir resumen de validación."""
        print("\n" + "=" * 60)
        print("RESUMEN DE VALIDACIÓN")
        print("=" * 60)

        if not self.validation_errors and not self.validation_warnings:
            print("✅ Todas las validaciones pasaron exitosamente")
        else:
            if self.validation_errors:
                print(f"\n❌ ERRORES ({len(self.validation_errors)}):")
                for error in self.validation_errors:
                    print(f"   • {error}")

            if self.validation_warnings:
                print(f"\n⚠️ ADVERTENCIAS ({len(self.validation_warnings)}):")
                for warning in self.validation_warnings:
                    print(f"   • {warning}")

        print("=" * 60)
