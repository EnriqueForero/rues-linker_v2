"""
utils.colombia_time — record_linkage_pipeline

Componentes:
    - function get_colombia_datetime  (origen: notebook celda [107])
    - function get_colombia_timestamp  (origen: notebook celda [107])
    - function get_colombia_display_time  (origen: notebook celda [107])
    - function hora_colombia  (origen: notebook celda [79])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from datetime import datetime

import pytz


def get_colombia_datetime():
    """Obtener datetime completo en zona horaria de Colombia."""
    colombia_tz = pytz.timezone("America/Bogota")
    return datetime.now(colombia_tz)


def get_colombia_timestamp():
    """Obtener timestamp en formato colombiano para nombres de carpetas."""
    colombia_tz = pytz.timezone("America/Bogota")
    return datetime.now(colombia_tz).strftime("%Y%m%d_%H%M")


def get_colombia_display_time():
    """Obtener tiempo formateado para mostrar en dashboard."""
    return get_colombia_datetime().strftime("%Y-%m-%d %H:%M:%S")


def hora_colombia() -> str:
    """Retorna la hora actual en Colombia formateada (YYYY-MM-DD HH:MM:SS)."""
    return datetime.now(pytz.timezone("America/Bogota")).strftime("%Y-%m-%d %H:%M:%S")
