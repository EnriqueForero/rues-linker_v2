"""
utils.timer — record_linkage_pipeline

Componentes:
    - class Timer  (origen: notebook celda [85])
    - function medir_tiempo  (origen: notebook celda [85])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import time
from datetime import timedelta

from .output import safe_print as print


class Timer:
    """Medidor de tiempo con contexto."""

    def __init__(self, nombre: str = ""):
        self.nombre = nombre
        self.inicio = None
        self.fin = None

    def __enter__(self):
        self.inicio = time.time()
        if self.nombre:
            print(f"🔵 Iniciando: {self.nombre}...")
        return self

    def __exit__(self, *args):
        self.fin = time.time()
        duracion = self.fin - self.inicio
        tiempo_fmt = str(timedelta(seconds=int(duracion)))
        if self.nombre:
            print(f"✅ {self.nombre}: {tiempo_fmt}")
        return False

    @property
    def duracion(self) -> float:
        if self.inicio and self.fin:
            return self.fin - self.inicio
        return 0


def medir_tiempo(nombre_seccion: str = ""):
    """
    Crea un medidor de tiempo simple (compatibilidad con código anterior).

    Uso:
        fin = medir_tiempo("mi proceso")
        # ... código ...
        fin()
    """
    tiempo_inicio = time.time()

    def imprimir_tiempo():
        duracion = time.time() - tiempo_inicio
        tiempo_fmt = str(timedelta(seconds=int(duracion)))
        seccion = f" - {nombre_seccion}" if nombre_seccion else ""
        print(f"⏱️ Tiempo{seccion}: {tiempo_fmt}")

    return imprimir_tiempo
