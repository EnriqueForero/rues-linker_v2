"""Modelos de datos compartidos del subpaquete reporting.

Extraídos para romper el ciclo entre `time_estimator.py` y `_lsh_refs.py`.
Ambos archivos importan desde aquí. Sin dependencias internas.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RiskLevel(Enum):
    """Nivel de riesgo de una iteración del pipeline.

    Origen: notebook celda [193].
    """

    LOW = ("BAJO", "🟢", "Seguro para Colab gratuito (<6h)")
    MEDIUM = ("MEDIO", "🟡", "Viable con monitoreo (6-9h)")
    HIGH = ("ALTO", "🟠", "Precaución, proceso largo (9-11h)")
    CRITICAL = ("CRÍTICO", "🔴", "NO terminará en 12h")


@dataclass
class IterationData:
    """Datos descriptivos de una iteración (referencia o predicción).

    Origen: notebook celda [193].
    """

    name: str
    threshold_efectivo: float
    permutaciones: int
    bandas: int
    filas: int
    candidatos_M: float
    tiempo_L2_min: float
    tiempo_total_min: float
    memoria_max_GB: float
    tasa_reduccion: float
    estado: str
