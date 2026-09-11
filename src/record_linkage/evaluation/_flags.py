"""Flags de disponibilidad de dependencias opcionales del subpaquete evaluation."""

from __future__ import annotations

try:
    import optuna  # noqa: F401  # detección de disponibilidad

    OPTUNA_AVAILABLE = True
except ImportError:
    OPTUNA_AVAILABLE = False
