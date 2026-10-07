"""record_linkage.salida — Del resultado del motor a las tablas del estándar (F1).

``completar`` añade a la correlativa y al golden lo que el contrato 1.0 exige
y el motor no produce (``ID_REGISTRO``, ``ID_ENTIDAD``, ``SCORE_PAR``,
``METODO_UNION``, ``CONFIANZA``), ordena las columnas y retira las técnicas.
``tecnicas`` las vuelve a pegar desde ``<dir_trabajo>/L5_golden/correlative.parquet``
cuando un consumidor (scripts de verificación) las necesita.
"""

from .completar import (
    ReporteCompletar,
    anexar_score_par,
    completar_correlativa,
    score_par_desde_scored_db,
)
from .tecnicas import RUTA_CORRELATIVA_L5, ReporteTecnicas, adjuntar_tecnicas, leer_tecnicas

__all__ = [
    "RUTA_CORRELATIVA_L5",
    "ReporteCompletar",
    "ReporteTecnicas",
    "adjuntar_tecnicas",
    "anexar_score_par",
    "completar_correlativa",
    "leer_tecnicas",
    "score_par_desde_scored_db",
]
