"""
pipeline.config_manager — record_linkage_pipeline

Componentes:
    - class ConfigurationManager  (origen: notebook celda [143])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from ..utils.logger import CustomLogger
from ._internal import ALL_PROFILES, DEFAULT_CONFIG


class ConfigurationManager:
    """
    Gestor de configuración del sistema.
    Permite crear, validar y personalizar configuraciones.
    """

    def __init__(self):
        self.profiles = ALL_PROFILES
        self.default_config = DEFAULT_CONFIG.copy()
        self.logger = CustomLogger("ConfigurationManager")

    def create_config(
        self, profile: str = "standard", custom_params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Crear configuración completa basada en perfil.

        Args:
            profile: Nombre del perfil base
            custom_params: Parámetros personalizados para sobrescribir

        Returns:
            Configuración completa
        """
        if profile not in self.profiles:
            raise ValueError(
                f"Perfil no válido: {profile}. Disponibles: {list(self.profiles.keys())}"
            )

        # Iniciar con configuración por defecto
        config = self.default_config.copy()

        # Agregar perfil
        config["profile"] = profile
        config["profiles"] = {profile: self.profiles[profile].copy()}

        # Aplicar parámetros personalizados
        if custom_params:
            self._apply_custom_params(config, custom_params)

        # Validar configuración
        self._validate_config(config)

        return config

    def _apply_custom_params(self, config: dict[str, Any], custom_params: dict[str, Any]):
        """Aplicar parámetros personalizados a la configuración."""
        for key, value in custom_params.items():
            if "." in key:  # Notación anidada (ej: 'validation_rules.min_nit_length')
                parts = key.split(".")
                target = config
                for part in parts[:-1]:
                    if part not in target:
                        target[part] = {}
                    target = target[part]
                target[parts[-1]] = value
            else:
                config[key] = value

    def _validate_config(self, config: dict[str, Any]):
        """Validar que la configuración sea coherente."""
        profile_name = config.get("profile")
        if profile_name and profile_name in config.get("profiles", {}):
            profile = config["profiles"][profile_name]

            # Validar pesos
            weights = profile.get("weights", {})
            total_weight = sum(weights.values())
            if abs(total_weight - 1.0) > 0.01:
                self.logger.warning(f"Pesos no suman 1.0: {total_weight:.3f}")

            # Validar thresholds
            if profile.get("score_threshold", 0) < profile.get("lsh_threshold", 0):
                self.logger.warning(
                    "score_threshold < lsh_threshold puede resultar en pocos matches"
                )

    def get_profile_comparison(self) -> pd.DataFrame:
        """Obtener comparación de todos los perfiles disponibles."""
        comparison_data = []

        params_to_compare = [
            "lsh_permutations",
            "lsh_threshold",
            "score_threshold",
            "min_name_similarity",
            "batch_size",
            "remove_top_words",
        ]

        for profile_name, profile in self.profiles.items():
            row = {"Perfil": profile_name}
            row["Descripción"] = profile.get("description", "")

            for param in params_to_compare:
                row[param] = profile.get(param, "N/A")

            comparison_data.append(row)

        return pd.DataFrame(comparison_data)

    def recommend_profile(
        self, dataset_size: int, memory_gb: float, priority: str = "balanced"
    ) -> str:
        """
        Recomendar perfil basado en características del dataset.

        Args:
            dataset_size: Número de registros
            memory_gb: Memoria disponible en GB
            priority: 'precision', 'recall', o 'balanced'

        Returns:
            Nombre del perfil recomendado
        """
        # Lógica de recomendación
        if memory_gb < 10:
            return "memory_constrained"

        if dataset_size > 3_000_000:
            return "large_scale"

        if priority == "precision":
            return "high_precision"
        elif priority == "recall":
            return "high_recall"

        return "standard"

    def export_config(self, config: dict[str, Any], filepath: str):
        """Exportar configuración a archivo JSON."""
        with open(filepath, "w") as f:
            json.dump(config, f, indent=2, default=str)

        self.logger.info(f"Configuración exportada a: {filepath}")

    def import_config(self, filepath: str) -> dict[str, Any]:
        """Importar configuración desde archivo JSON."""
        with open(filepath) as f:
            config = json.load(f)

        # Validar
        self._validate_config(config)

        return config
