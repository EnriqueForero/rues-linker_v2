"""Constantes y helpers internos del notebook fuente.

Estos símbolos eran globales del notebook. Aquí se preservan
para mantener compatibilidad con clases que los referencian.
"""

from __future__ import annotations


# ────────────────────────────────────────────────────────────
# obtener_prioridades_fuentes  (origen: notebook celda [231], tipo: function)
# ────────────────────────────────────────────────────────────
def obtener_prioridades_fuentes() -> dict[str, int]:
    """
    Define la jerarquía de confianza de las fuentes de datos.
    Menor número = Mayor prioridad (Gold Standard).

    Esta función es requerida por el Orchestrator para resolver conflictos
    de Golden Records.
    """
    return {
        "RUES": 1,  # Fuente Oficial / Maestra
        "SUPERSOCIEDADES": 2,  # Alta calidad financiera
        "EXPORTACIONES": 3,  # DIAN / Aduanas
        "CRM": 4,  # Datos internos manuales
        "TEST": 99,  # Datos de prueba
    }
