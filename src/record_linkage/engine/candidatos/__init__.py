"""engine.candidatos — generación de candidatos por estrategias componibles (Fase 2, 2026-10-13).

``generar_candidatos_hibrido`` compone identificador + tokens distintivos + TF-IDF + llaves y ``escribir_candidatos_sqlite``
deja el ``candidates.db`` que L3 consume. Se activa con ``motor_candidatos = "hibrido"`` en el perfil.
"""

from record_linkage.engine.candidatos.hibrido import (
    ConfigCandidatosHibrido,
    EstadisticasCandidatos,
    escribir_candidatos_sqlite,
    generar_candidatos_hibrido,
    matriz_politica_fuentes,
    recall_de_pares,
)
from record_linkage.engine.candidatos.nombre import (
    pares_por_nombre_tfidf,
    pares_por_token_distintivo,
    variantes_por_borrado,
)

__all__ = [
    "ConfigCandidatosHibrido",
    "EstadisticasCandidatos",
    "escribir_candidatos_sqlite",
    "generar_candidatos_hibrido",
    "matriz_politica_fuentes",
    "pares_por_nombre_tfidf",
    "pares_por_token_distintivo",
    "recall_de_pares",
    "variantes_por_borrado",
]
