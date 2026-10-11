"""Vinculación de empresas entre bases (ancla × destinos) por nombre, país y variables adicionales.

Toma una base ANCLA (p. ej. exportadores) y la cruza con N bases DESTINO (p. ej. Orbis, FDI Markets).
Por cada empresa ancla entrega con qué registros destino es la MISMA EMPRESA o el MISMO GRUPO
empresarial, con qué confianza y por qué. Bandas: A = automático de alta confianza · B = automático,
revisión rápida recomendada · C = candidato, NO se vincula sin decisión humana.

Qué viene de la librería (no se reimplementa): saneamiento de texto, ASCII/mayúsculas, retiro de formas
legales, canonización de país (ISO 3166), IDF del corpus y el comparador ``SimilitudNombre``.

Qué agrega este paquete para un CRUCE entre bases: comparación exhaustiva ancla × destino, alineación
palabra a palabra (EMPRESA exige que TODAS las palabras no legales se correspondan), país como evidencia
y no como bloqueo, alias entre paréntesis y matriz reportada, propagación por sitio web, decisiones
humanas que se aplican en la siguiente corrida, deduplicación interna, validación contra una referencia,
invariantes y Excel de entrega.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from record_linkage.flujo.vinculacion.config import ConfigVinculacion, Fuente
from record_linkage.flujo.vinculacion.decisiones import leer_decisiones, plantilla_decisiones
from record_linkage.flujo.vinculacion.ejecutar import ejecutar
from record_linkage.flujo.vinculacion.resultado import Resultado

if TYPE_CHECKING:
    from record_linkage.flujo.vinculacion_excel import exportar_excel, exportar_vinculacion_excel

__all__ = [
    "ConfigVinculacion",
    "Fuente",
    "Resultado",
    "ejecutar",
    "exportar_excel",
    "exportar_vinculacion_excel",
    "leer_decisiones",
    "plantilla_decisiones",
]

#: El Excel de entrega vive en ``flujo.vinculacion_excel``, que a su vez importa este paquete: se resuelve
#: al pedirlo (PEP 562) para no crear un ciclo de importación.
_DEL_EXCEL = frozenset({"exportar_excel", "exportar_vinculacion_excel"})


def __getattr__(nombre: str) -> Any:
    if nombre in _DEL_EXCEL:
        from record_linkage.flujo import vinculacion_excel

        return getattr(vinculacion_excel, nombre)
    raise AttributeError(f"module {__name__!r} has no attribute {nombre!r}")
