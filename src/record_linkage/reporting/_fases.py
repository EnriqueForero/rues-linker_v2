"""Tiempos por fase para los reportes de L6: una sola lectura, una sola etiqueta.

Por qué existe (F1.6)
---------------------
El orquestador cronometra cada fase y entrega ``metrics["phase_times"]`` con
las claves de ``Phase`` (``L1_prep``, ``L2_lsh_candidates``, ``L3_scoring``,
``L4_clustering``, ``L5_golden``; ``L6_reporting`` solo cuando ya terminó).
Hasta la 0.22.x el dashboard, los reportes Excel, el visualizador y la suite
buscaban cada uno OTRAS claves (``load_validate``, ``preprocessing_time``,
``scoring_time``…) que nadie producía, y al no encontrarlas el dashboard
repartía el tiempo total con porcentajes fijos (15/10/25/15/10/15/10 %) y el
visualizador devolvía ``None``: nunca se escribió ``performance_timeline.png``.

Regla (CLAUDE.md §2.3, «una regla se escribe una sola vez»): todo consumidor
lee los tiempos con :func:`tiempos_por_fase` y rotula con
:data:`ETIQUETAS_FASE`. Si no hay tiempos, no se inventa nada: el consumidor
muestra :data:`MENSAJE_SIN_TIEMPOS` u omite el artefacto, y lo dice en el log.
"""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Real
from typing import Any

from ..pipeline.errores import TiemposPorFaseError, mensaje_accionable

#: Etiqueta humana de cada fase, en el orden de ejecución L1 → L6.
#: Las claves son los ``Phase.value`` de ``reporting.strategies``; se escriben
#: literales porque ``strategies`` importa ``reports`` y ``reports`` importa
#: este módulo (un ``from .strategies import Phase`` aquí sería circular).
#: ``tests/test_reportes_tiempos.py`` exige que coincidan con ``Phase``.
ETIQUETAS_FASE: dict[str, str] = {
    "L1_prep": "L1 · Preparación",
    "L2_lsh_candidates": "L2 · Candidatos (LSH)",
    "L3_scoring": "L3 · Scoring",
    "L4_clustering": "L4 · Clustering",
    "L5_golden": "L5 · Registro consolidado",
    "L6_reporting": "L6 · Reportes",
}

#: Texto que muestran los gráficos cuando ``phase_times`` no trae nada.
MENSAJE_SIN_TIEMPOS = "Sin tiempos por fase"


def tiempos_por_fase(metrics: Mapping[str, Any] | None) -> dict[str, float]:
    """Segundos por fase tal como los cronometró el orquestador.

    Lee ``metrics["phase_times"]`` y devuelve ``{clave_de_fase: segundos}`` en
    el orden L1 → L6, SOLO con las fases presentes. No escala, no reparte, no
    completa: si falta ``phase_times`` o viene vacío, devuelve ``{}``.

    Raises:
        TiemposPorFaseError: si ``phase_times`` no es un mapeo, si una clave no
            está en :data:`ETIQUETAS_FASE` (una fase nueva sin etiqueta, o un
            productor que sigue usando nombres viejos: se añade a
            ``ETIQUETAS_FASE`` en vez de ignorarla) o si un valor no es
            numérico. Es un ``ErrorPipeline``: los consumidores lo relanzan.
    """
    if not metrics:
        return {}
    crudos = metrics.get("phase_times")
    if crudos is None:
        return {}
    if not isinstance(crudos, Mapping):
        raise TiemposPorFaseError(
            mensaje_accionable(
                f"metrics['phase_times'] debe ser un mapeo fase → segundos y llegó "
                f"{type(crudos).__name__}.",
                "sin él los reportes de L6 no pueden mostrar tiempos por fase.",
                "revise quién construyó `metrics` (Orchestrator._build_metrics).",
            )
        )
    desconocidas = sorted(set(crudos) - set(ETIQUETAS_FASE))
    if desconocidas:
        raise TiemposPorFaseError(
            mensaje_accionable(
                f"fases sin etiqueta en phase_times: {desconocidas}; las conocidas son "
                f"{list(ETIQUETAS_FASE)}.",
                "ignorarlas dejaría fuera del reporte un tiempo que sí se midió, o "
                "entraría un nombre que nadie cronometra.",
                "si es una fase nueva, añádala a record_linkage.reporting._fases."
                "ETIQUETAS_FASE; si es un nombre viejo (load_validate, scoring_time…), "
                "el productor debe usar las claves de Phase.",
            )
        )
    salida: dict[str, float] = {}
    for clave in ETIQUETAS_FASE:
        if clave not in crudos:
            continue
        valor = crudos[clave]
        if isinstance(valor, bool) or not isinstance(valor, Real):
            raise TiemposPorFaseError(
                mensaje_accionable(
                    f"phase_times['{clave}'] debe ser un número de segundos y llegó "
                    f"{valor!r} ({type(valor).__name__}).",
                    "un tiempo que no es número no se puede sumar, graficar ni comparar.",
                    "revise quién construyó `metrics` (Orchestrator._build_metrics).",
                )
            )
        salida[clave] = float(valor)
    return salida


def etiquetar(tiempos: Mapping[str, float]) -> dict[str, float]:
    """``{etiqueta humana: segundos}`` a partir de la salida de :func:`tiempos_por_fase`."""
    return {ETIQUETAS_FASE[clave]: segundos for clave, segundos in tiempos.items()}


def formatear_segundos(segundos: float) -> str:
    """Formato corto y legible: ``850 ms``, ``12.5 s``, ``3.2 min``."""
    if segundos < 1:
        return f"{segundos * 1000:.0f} ms"
    if segundos < 60:
        return f"{segundos:.1f} s"
    return f"{segundos / 60:.1f} min"
