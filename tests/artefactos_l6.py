"""Ayuda de pruebas: deja en disco los artefactos OBLIGATORIOS de L6.

Una estrategia de captura (que sustituye a ``DataExportStrategy`` en una
prueba) tiene que dejar los obligatorios por nombre exacto para que
``Orchestrator._run_L6`` cuente la corrida como válida. La lista NO se
reescribe aquí: se deriva de ``reporting.contrato_l6.ARTEFACTOS_OBLIGATORIOS``
(regla 3: una regla se escribe una sola vez). Desde F1.12 ningún obligatorio
lleva comodín (``config_auditoria_*.json`` dejó de ser obligatorio: su
contenido vive en ``manifest.json``); si alguno volviera a llevarlo, ``*``
se concreta como ``prueba``.

Este módulo no es una prueba (no empieza por ``test_``): pytest lo importa
desde ``tests/`` porque el directorio no es un paquete y queda en ``sys.path``.
"""

from __future__ import annotations

from pathlib import Path

from record_linkage.reporting.contrato_l6 import ARTEFACTOS_OBLIGATORIOS

__all__ = ["NOMBRES_OBLIGATORIOS_L6", "escribir_obligatorios_l6"]

NOMBRES_OBLIGATORIOS_L6: tuple[str, ...] = tuple(
    a.patron.replace("*", "prueba") for a in ARTEFACTOS_OBLIGATORIOS
)


def escribir_obligatorios_l6(carpeta: Path) -> list[Path]:
    """Escribe un archivo no vacío por cada obligatorio y devuelve sus rutas."""

    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    rutas: list[Path] = []
    for nombre in NOMBRES_OBLIGATORIOS_L6:
        ruta = carpeta / nombre
        ruta.write_bytes(b"x")
        rutas.append(ruta)
    return rutas
