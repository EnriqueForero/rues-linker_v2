"""
optimization.parameters — record_linkage_pipeline

Componentes:
    - class ParameterDefinition  (origen: notebook celda [160])
    - class ParameterSpace  (origen: notebook celda [160])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class ParameterDefinition:
    """
    Define un parámetro individual para la optimización.

    Attributes:
        name: Nombre del parámetro
        param_type: Tipo de parámetro ('float', 'int', 'categorical', 'fixed')
        value: Valor fijo (solo para tipo 'fixed')
        range: Tupla (min, max) para tipos numéricos
        choices: Lista de opciones para tipo categorical
        default: Valor por defecto
    """

    name: str
    param_type: str
    value: Any | None = None
    range: tuple[float, float] | None = None
    choices: list[Any] | None = None
    default: Any | None = None

    def validate(self):
        """Valida que la definición del parámetro sea coherente."""
        if self.param_type == "fixed":
            if self.value is None:
                raise ValueError(f"Parámetro fijo '{self.name}' debe tener un valor")
        elif self.param_type in ["float", "int"]:
            if self.range is None or len(self.range) != 2:
                raise ValueError(f"Parámetro numérico '{self.name}' debe tener rango [min, max]")
        elif self.param_type == "categorical" and not self.choices:
            raise ValueError(f"Parámetro categórico '{self.name}' debe tener opciones")


class ParameterSpace:
    """
    Gestiona el espacio completo de parámetros para la optimización.
    """

    def __init__(self):
        self.parameters: dict[str, ParameterDefinition] = {}
        self._init_default_parameters()

    def _init_default_parameters(self):
        """Inicializa los parámetros por defecto del sistema."""
        # Parámetros de preprocesamiento
        self.add_parameter(
            "cleaning_mode",
            "categorical",
            choices=["CONSERVADOR", "BALANCEADO", "AGRESIVO"],
            default="BALANCEADO",
        )
        self.add_parameter("remove_top_words", "int", range=(0, 50), default=20)

        # Parámetros LSH
        self.add_parameter("lsh_permutations", "int", range=(64, 512), default=128)
        self.add_parameter("lsh_threshold", "float", range=(0.5, 0.95), default=0.75)
        self.add_parameter("lsh_ngram", "int", range=(2, 5), default=3)

        # Parámetros de scoring
        self.add_parameter("score_threshold", "float", range=(0.5, 0.95), default=0.85)
        self.add_parameter("max_nit_distance", "int", range=(0, 5), default=3)
        self.add_parameter("min_name_similarity", "float", range=(0.3, 0.9), default=0.65)

        # Pesos (se normalizarán automáticamente)
        self.add_parameter("weight_name", "float", range=(0.0, 1.0), default=0.6)
        self.add_parameter("weight_nit", "float", range=(0.0, 1.0), default=0.35)
        self.add_parameter("weight_phonetic", "float", range=(0.0, 1.0), default=0.05)

    def add_parameter(self, name: str, param_type: str, **kwargs):
        """Añade un parámetro al espacio de búsqueda."""
        param = ParameterDefinition(name=name, param_type=param_type, **kwargs)
        param.validate()
        self.parameters[name] = param

    def set_fixed(self, name: str, value: Any):
        """Convierte un parámetro a tipo fijo con el valor especificado."""
        if name not in self.parameters:
            raise ValueError(f"Parámetro '{name}' no existe")
        self.parameters[name] = ParameterDefinition(name=name, param_type="fixed", value=value)

    def set_range(self, name: str, min_val: float, max_val: float):
        """Actualiza el rango de un parámetro numérico."""
        if name not in self.parameters:
            raise ValueError(f"Parámetro '{name}' no existe")
        param = self.parameters[name]
        if param.param_type not in ["float", "int"]:
            raise ValueError(f"Parámetro '{name}' no es numérico")
        param.range = (min_val, max_val)

    def get_optuna_params(self, trial) -> dict[str, Any]:
        """
        Genera los parámetros para un trial de Optuna.

        Args:
            trial: Objeto trial de Optuna

        Returns:
            Diccionario con los valores de parámetros sugeridos
        """
        params = {}

        for name, param_def in self.parameters.items():
            if param_def.param_type == "fixed":
                params[name] = param_def.value
            elif param_def.param_type == "float":
                params[name] = trial.suggest_float(name, *param_def.range)
            elif param_def.param_type == "int":
                params[name] = trial.suggest_int(name, *param_def.range)
            elif param_def.param_type == "categorical":
                params[name] = trial.suggest_categorical(name, param_def.choices)

        return params

    def normalize_weights(self, params: dict[str, Any]) -> dict[str, Any]:
        """Normaliza los pesos para que sumen 1.0."""
        weight_keys = ["weight_name", "weight_nit", "weight_phonetic"]
        weights = {k: params.get(k, 0.0) for k in weight_keys if k in params}

        if weights:
            total = sum(weights.values())
            if total > 0:
                for k in weights:
                    params[k] = weights[k] / total

        return params

    def to_pipeline_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Convierte los parámetros al formato esperado por RecordLinkagePipeline.
        """
        # Normalizar pesos
        params = self.normalize_weights(params)

        # Crear configuración para el pipeline
        config = {
            "cleaning_mode": params.get("cleaning_mode", "BALANCEADO"),
            "remove_top_words": params.get("remove_top_words", 20),
            "lsh_permutations": params.get("lsh_permutations", 128),
            "lsh_threshold": params.get("lsh_threshold", 0.75),
            "lsh_ngram": params.get("lsh_ngram", 3),
            "score_threshold": params.get("score_threshold", 0.85),
            "max_nit_distance": params.get("max_nit_distance", 3),
            "min_name_similarity": params.get("min_name_similarity", 0.65),
            "weights": {
                "name": params.get("weight_name", 0.6),
                "nit": params.get("weight_nit", 0.40),
                "phonetic": params.get("weight_phonetic", 0.00),
            },
        }

        return config

    def save(self, filepath: str):
        """Guarda la configuración del espacio de parámetros."""
        config = {
            "parameters": {
                name: {
                    "type": p.param_type,
                    "value": p.value,
                    "range": p.range,
                    "choices": p.choices,
                    "default": p.default,
                }
                for name, p in self.parameters.items()
            }
        }
        with open(filepath, "w") as f:
            json.dump(config, f, indent=2)

    def load(self, filepath: str):
        """Carga la configuración del espacio de parámetros."""
        with open(filepath) as f:
            config = json.load(f)

        self.parameters = {}
        for name, param_config in config["parameters"].items():
            self.add_parameter(
                name,
                param_config["type"],
                value=param_config.get("value"),
                range=param_config.get("range"),
                choices=param_config.get("choices"),
                default=param_config.get("default"),
            )
