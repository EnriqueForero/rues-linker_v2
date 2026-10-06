"""Constantes y helpers internos del notebook fuente.

Estos símbolos eran globales del notebook. Aquí se preservan
para mantener compatibilidad con clases que los referencian.
"""

from __future__ import annotations

import gc
import logging
from contextlib import contextmanager

import pandas as pd

from ..config.profiles import (  # noqa: F401 — reexport retrocompat (F1.2)
    PERFILES_DEDUPLICACION as DEDUPLICATION_PROFILES,
    PERFILES_MOTOR as PROFILES,
    REGISTRO_PERFILES as ALL_PROFILES,
)
from ..processing._constants import LEGAL_SUFFIXES, ORGANIZATIONAL_TERMS, STOPWORDS_BASIC

# ────────────────────────────────────────────────────────────
# COMMERCIAL_TERMS  (origen: notebook celda [147])
# ────────────────────────────────────────────────────────────
COMMERCIAL_TERMS = {
    "COMERCIALIZADORA",
    "TRADING",
    "SERVICIOS",
    "SOLUCIONES",
    "SOLUTIONS",
    "INVERSIONES",
    "INVESTMENT",
    "DISTRIBUIDORA",
    "CONSULTING",
    "CONSULTORES",
    "ASESORES",
    "TECHNOLOGIES",
    "TECH",
    "TECHNOLOGY",
    "SYSTEMS",
    "MANAGEMENT",
    "INVESTMENTS",
    "CAPITAL",
    "PRODUCTS",
    "EQUIPOS",
    "SUMINISTROS",
    "WHOLESALE",
    "RETAIL",
    "GROWTH",
    "DISTRIBUTION",
    "DISTRIBUIDOR",
}

# ────────────────────────────────────────────────────────────
# DEDUP_CLEANING_MODES  (origen: notebook celda [147], tipo: assign)
# ────────────────────────────────────────────────────────────
DEDUP_CLEANING_MODES = {
    "CONSERVADOR": STOPWORDS_BASIC | LEGAL_SUFFIXES,
    "BALANCEADO": STOPWORDS_BASIC | LEGAL_SUFFIXES | ORGANIZATIONAL_TERMS,
    "AGRESIVO": STOPWORDS_BASIC | LEGAL_SUFFIXES | ORGANIZATIONAL_TERMS | COMMERCIAL_TERMS,
}

# ────────────────────────────────────────────────────────────
# PROFILES  (origen: notebook celda [108], tipo: assign)
# ────────────────────────────────────────────────────────────
# ── Perfiles: MOVIDOS a config/profiles.py (F1.2, v0.9.0) ────────────────
# Reexport retrocompatible: son los MISMOS objetos (mutarlos aquí o allá
# es idéntico). No definir perfiles en este módulo nunca más.

# ────────────────────────────────────────────────────────────
# DEFAULT_CONFIG  (origen: notebook celda [108], tipo: assign)
# ────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "profile": "standard",
    "output_directory": "resultados_record_linkage",
    "max_file_size_mb": 100,
    "compress_large_files": True,
    "cleaning_mode": "BALANCEADO",
    "validation_rules": {
        "min_nit_length": 6,
        "max_nit_length": 15,
        "min_name_length": 5,
        "max_name_length": 300,
        "remove_test_data": True,
        "remove_invalid_nits": True,
    },
    "quality_thresholds": {
        "min_coverage": 0.05,
        "max_group_size": 100,
        "review_threshold": 0.75,
        "min_confidence": 0.50,
        "min_quality_score": 0.30,
    },
    "export_settings": {
        "csv_compression": "gzip",
        "parquet_compression": "snappy",
        "include_diagnostics": True,
    },
    "performance_settings": {
        "enable_profiling": False,
        "log_memory_usage": True,
        "save_intermediate_results": False,
    },
}


# ────────────────────────────────────────────────────────────
# _class_exists  (origen: notebook celda [192], tipo: function)
# ────────────────────────────────────────────────────────────
def _class_exists(class_name: str) -> bool:
    """
    Verifica si una clase existe y es importable de forma segura.

    v3.2.7 (FASE 4): el comportamiento previo sobre el contexto global de
    _internal.py retornaba False para clases que SÍ
    existen pero no estaban importadas aquí (caso: ReportGenerator,
    DataVisualizer, ExecutiveDashboard, EnhancedReportingSuite). Esto
    causaba que Orchestrator emitiera warnings "no disponible, omitiendo"
    aun cuando las dependencias estaban instaladas.

    Nueva implementación: intenta importar la clase desde su módulo
    conocido en `record_linkage.reporting`. Si el import falla (caso
    real de dependencia ausente), retorna False sin warnings ruidosos.

    Args:
        class_name: Nombre de la clase a verificar.

    Returns:
        True si la clase existe y es importable, False en caso contrario.
    """
    # Mapeo de clases conocidas a sus módulos (basado en auditoría Fase 4)
    _CLASS_MODULE_MAP = {
        "ReportGenerator": "record_linkage.reporting.reports",
        "DataVisualizer": "record_linkage.reporting.visualizer",
        "ExecutiveDashboard": "record_linkage.reporting.dashboard",
        "EnhancedReportingSuite": "record_linkage.reporting.suite",
    }
    if class_name in _CLASS_MODULE_MAP:
        try:
            import importlib

            module = importlib.import_module(_CLASS_MODULE_MAP[class_name])
            cls = getattr(module, class_name, None)
            return cls is not None
        except (ImportError, ModuleNotFoundError):
            # Dependencia opcional ausente — silencioso, correcto
            return False
        except Exception:
            return False
    # No interpretar texto recibido como código. Las únicas capacidades
    # opcionales soportadas son las declaradas explícitamente arriba.
    return False


# ────────────────────────────────────────────────────────────
# _fmt_time  (origen: notebook celda [192], tipo: function)
# ────────────────────────────────────────────────────────────
def _fmt_time(seconds: float) -> str:
    """
    Formatea segundos a string legible.

    Args:
        seconds: Tiempo en segundos

    Returns:
        String formateado (ej: "2h 30m", "45m 12s", "3.5s")
    """
    if seconds >= 3600:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"
    elif seconds >= 60:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    return f"{seconds:.1f}s"


# ────────────────────────────────────────────────────────────
# _get_logger  (origen: notebook celda [192], tipo: function)
# ────────────────────────────────────────────────────────────
def _get_logger(name: str = "Orchestrator") -> logging.Logger:
    """
    Configura y retorna logger con formato consistente.

    El logger usa formato con timestamp y nivel, ideal para
    seguimiento de procesos largos.

    Args:
        name: Nombre del logger

    Returns:
        Logger configurado
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-5s | %(message)s", "%H:%M:%S")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    return logger


# ────────────────────────────────────────────────────────────
# _phase_cleanup  (origen: notebook celda [192], tipo: function)
# ────────────────────────────────────────────────────────────
@contextmanager
def _phase_cleanup():
    """
    Context manager para limpieza de memoria entre fases.

    Ejecuta gc.collect() al finalizar cada fase (incluso si la fase
    lanza excepción) para liberar memoria y evitar acumulación en
    procesos largos.

    Uso:
        with _phase_cleanup():
            resultado = ejecutar_fase()
    """
    try:
        yield
    finally:
        gc.collect()


# ────────────────────────────────────────────────────────────
# _validate_sources  (origen: notebook celda [192], tipo: function)
# ────────────────────────────────────────────────────────────
def _validate_sources(
    sources: dict[str, pd.DataFrame], logger: logging.Logger
) -> tuple[bool, list[str]]:
    """
    Valida que las fuentes de datos tengan la estructura esperada.

    Verifica:
    - Que existan fuentes definidas
    - Que cada fuente sea un DataFrame válido y no vacío
    - Que cada fuente tenga las columnas requeridas (NIT, RAZON_SOCIAL)

    Args:
        sources: Diccionario de fuentes {nombre: DataFrame}
        logger: Logger para reportar errores

    Returns:
        Tupla (es_valido, lista_de_errores)
    """
    errores = []
    columnas_requeridas = ["NIT", "RAZON_SOCIAL"]

    if not sources:
        return False, ["No hay fuentes definidas"]

    for nombre, df in sources.items():
        if df is None:
            errores.append(f"{nombre}: DataFrame es None")
            continue

        if not isinstance(df, pd.DataFrame):
            errores.append(f"{nombre}: No es un DataFrame válido (tipo: {type(df).__name__})")
            continue

        if len(df) == 0:
            errores.append(f"{nombre}: DataFrame vacío (0 registros)")
            continue

        # Verificar columnas requeridas
        faltantes = [col for col in columnas_requeridas if col not in df.columns]
        if faltantes:
            errores.append(f"{nombre}: Faltan columnas requeridas: {faltantes}")
            disponibles = list(df.columns)[:10]
            errores.append(f"   Columnas disponibles (primeras 10): {disponibles}")

    # Reportar todos los errores encontrados
    if errores:
        for err in errores:
            logger.error(f"   ❌ {err}")

    return len(errores) == 0, errores
