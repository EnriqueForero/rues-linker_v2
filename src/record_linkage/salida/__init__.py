"""record_linkage.salida — Del resultado del motor a las tablas del estándar (F1).

``completar`` añade a la correlativa y al golden lo que el contrato 1.0 exige
y el motor no produce (``ID_REGISTRO``, ``ID_ENTIDAD``, ``SCORE_PAR``,
``METODO_UNION``, ``CONFIANZA``), ordena las columnas y retira las técnicas.
"""

from .completar import (
    ReporteCompletar,
    anexar_score_par,
    completar_correlativa,
    score_par_desde_scored_db,
)

__all__ = [
    "ReporteCompletar",
    "anexar_score_par",
    "completar_correlativa",
    "score_par_desde_scored_db",
]
