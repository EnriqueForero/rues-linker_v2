"""
utils.validation — record_linkage_pipeline

Componentes:
    - function validar_dataframe  (origen: notebook celda [85])
    - function resumen_dataframe  (origen: notebook celda [85])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .output import safe_print as print


def validar_dataframe(df: pd.DataFrame, nombre: str = "DataFrame") -> dict[str, Any]:
    """
    Valida un DataFrame y retorna estadísticas básicas.

    Args:
        df: DataFrame a validar
        nombre: Nombre descriptivo

    Returns:
        Dict con estadísticas de validación
    """
    stats = {
        "nombre": nombre,
        "filas": len(df),
        "columnas": len(df.columns),
        "memoria_mb": df.memory_usage(deep=True).sum() / (1024**2),
        "columnas_lista": list(df.columns),
        "nulos_por_columna": df.isnull().sum().to_dict(),
        "tipos": df.dtypes.astype(str).to_dict(),
    }

    # Verificar columnas esperadas
    columnas_clave = ["NIT", "RAZON_SOCIAL"]
    stats["tiene_columnas_clave"] = all(col in df.columns for col in columnas_clave)

    return stats


def resumen_dataframe(df: pd.DataFrame, nombre: str = "DataFrame"):
    """Imprime resumen de un DataFrame."""
    stats = validar_dataframe(df, nombre)

    print(f"\n📊 {stats['nombre']}")
    print(f"   Filas: {stats['filas']:,}")
    print(f"   Columnas: {stats['columnas']}")
    print(f"   Memoria: {stats['memoria_mb']:.2f} MB")

    if not stats["tiene_columnas_clave"]:
        print("   ⚠️ Faltan columnas clave (NIT/RAZON_SOCIAL)")
