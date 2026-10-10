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

from record_linkage.flujo.vinculacion.config import ConfigVinculacion, Fuente
from record_linkage.flujo.vinculacion.resultado import Resultado

__all__ = ["ConfigVinculacion", "Fuente", "Resultado"]
