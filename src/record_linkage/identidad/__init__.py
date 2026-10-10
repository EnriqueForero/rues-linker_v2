"""record_linkage.identidad — Identidad pública estable de las entidades (F2.4).

``estable`` asigna ``ID_ENTIDAD`` (``NIT-<base>`` / ``ENT-…``), hereda los
``ENT-…`` desde el crosswalk ``entidades_ids.parquet`` de la corrida anterior y
produce el crosswalk de esta corrida (con los retiros). ADR-0012.
"""

from .estable import (
    COLUMNA_REGISTROS,
    NOMBRE_CROSSWALK,
    SEPARADOR_REGISTROS,
    CorridaAnterior,
    ResultadoIdentidad,
    asignar_id_entidad,
    cargar_corrida_anterior,
    crosswalk_de_corrida,
    localizar_crosswalk,
)

__all__ = [
    "COLUMNA_REGISTROS",
    "NOMBRE_CROSSWALK",
    "SEPARADOR_REGISTROS",
    "CorridaAnterior",
    "ResultadoIdentidad",
    "asignar_id_entidad",
    "cargar_corrida_anterior",
    "crosswalk_de_corrida",
    "localizar_crosswalk",
]
