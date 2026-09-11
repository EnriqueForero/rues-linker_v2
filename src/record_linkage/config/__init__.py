"""record_linkage.config — Configuración central del proyecto."""

from .credentials import SnowflakeCredentials, get_snowflake_credentials
from .paths import Rutas
from .profiles import (
    DEAD_CONFIG_KEYS,
    DEPRECATED_CONFIG_KEYS,
    PARTIAL_CONFIG_KEYS,
    PERFILES_BASE,
    RESURRECTED_CONFIG_KEYS,
    config_produccion_it7,
    crear_config_orchestrator,
    validar_config,
)
from .settings import Config

__all__ = [
    "DEAD_CONFIG_KEYS",
    "DEPRECATED_CONFIG_KEYS",
    "PARTIAL_CONFIG_KEYS",
    "PERFILES_BASE",
    "RESURRECTED_CONFIG_KEYS",
    "Config",
    "Rutas",
    "SnowflakeCredentials",
    "config_produccion_it7",
    "crear_config_orchestrator",
    "get_snowflake_credentials",
    "validar_config",
]
