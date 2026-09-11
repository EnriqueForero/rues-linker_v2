"""Flags de disponibilidad de dependencias opcionales del subpaquete reporting.

Estos flags permiten que el código degrade graciosamente cuando una
dependencia opcional no está instalada.
"""

from __future__ import annotations

try:
    import pyarrow  # detección de disponibilidad
    import pyarrow.parquet  # noqa: F401  # detección de disponibilidad

    PYARROW_AVAILABLE = True
except ImportError:
    PYARROW_AVAILABLE = False
