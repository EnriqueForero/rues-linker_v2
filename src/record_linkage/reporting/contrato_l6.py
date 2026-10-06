"""Contrato de L6: qué artefactos son obligatorios, cuáles opcionales y cómo
se verifica que una corrida los dejó.

Por qué existe (regla 6 del plan: nada se repara en silencio)
-------------------------------------------------------------
Hasta F1.4, L6 convertía cualquier fallo en «nada»: una estrategia que
lanzaba devolvía ``[]``; un reporte Excel que fallaba se escribía con una sola
celda «Error generando reporte: …»; un dashboard que fallaba se escribía como
PNG con el texto del error; y el orquestador verificaba la exportación
contractual por PREFIJO (``golden_records``, ``tabla_correlativa``), de modo
que ``golden_records_MUESTRA_100k.xlsx`` bastaba para dar por presente a
``golden_records.parquet``. Tres fallos internos llegaron así al usuario días
después.

Este módulo DECLARA la lista de artefactos (una sola vez) y la hace
verificable:

* **Obligatorios**: sin ellos la corrida no tiene entregable y FALLA con
  :class:`~record_linkage.pipeline.errores.ArtefactoObligatorioError`.
  Son los dos cuerpos del resultado en los dos formatos que la librería
  promete (parquet para programas, csv.gz para cualquier herramienta).
* **Opcionales**: si fallan, el archivo NO se escribe y la omisión queda en
  ``manifest.json → L6_reporting.meta.omitidos`` como
  ``{artefacto, estrategia, motivo}``. Son los Excel (comodidad: el parquet
  es el entregable), los reportes analíticos, las figuras, el dashboard, los
  insights y el alias ``config_auditoria.json``.

Hasta F1.12 la auditoría de configuración (``config_auditoria_<ts>.json``)
era obligatoria porque era lo que permitía reproducir la corrida. Ya no: lo
que era su contenido vive en el ``manifest.json`` de la carpeta del estándar
(``exporters.escritor.Manifiesto``: ``parametros`` con perfil, LSH, scoring,
pesos y prioridad de fuentes real, ``tiempos_por_fase``, ``rss_por_fase``,
``metricas``, ``version`` real e ``insumos`` con huella), que
``linkage(carpeta_salida=...)`` escribe siempre. ``config_auditoria.json``
(nombre estable, sin marca de tiempo) queda como alias opcional que remite al
manifiesto; el ``.txt`` desapareció.

Los nombres son los de v1 (decisión de Enrique: se conservan los nombres de
columna y de archivo; el estándar nuevo de ``ESTANDAR_SALIDA.md`` los
renombra en F1.9–F1.14 y esta lista se actualizará ahí). Los patrones son
explícitos (``fnmatch``): los Excel de muestra llevan el límite de filas en
el nombre.

Uso
---
``verificar_artefactos(output_dir, generados)`` devuelve un
:class:`ReporteL6` con los obligatorios faltantes (por nombre exacto o patrón
explícito, y exigiendo que el archivo exista y no esté vacío) y los
opcionales omitidos. ``Orchestrator._run_L6`` lo llama al terminar las
estrategias y falla si ``not reporte.ok``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path


@dataclass(frozen=True)
class ArtefactoDeclarado:
    """Un artefacto que L6 promete (obligatorio) u ofrece (opcional).

    Attributes:
        patron: nombre exacto o patrón ``fnmatch`` relativo a la carpeta de
            L6 (``visualizaciones/*.png`` incluye el subdirectorio).
        estrategia: nombre de la clase de ``reporting.strategies`` que lo
            produce.
        significado: para qué sirve (documentación de la lista; nadie lo
            consume todavía).
    """

    patron: str
    estrategia: str
    significado: str

    def coincide(self, nombre_relativo: str) -> bool:
        return fnmatchcase(nombre_relativo, self.patron)


@dataclass(frozen=True)
class ArtefactoOmitido:
    """Un artefacto opcional que no se escribió, con su estrategia y motivo.

    Es lo que viaja a ``manifest.json → L6_reporting.meta.omitidos``.
    """

    artefacto: str
    estrategia: str
    motivo: str

    def como_dict(self) -> dict[str, str]:
        return {"artefacto": self.artefacto, "estrategia": self.estrategia, "motivo": self.motivo}


ARTEFACTOS_OBLIGATORIOS: tuple[ArtefactoDeclarado, ...] = (
    ArtefactoDeclarado(
        "tabla_correlativa.parquet",
        "DataExportStrategy",
        "LA tabla: una fila por registro de entrada con lo que el motor decidió",
    ),
    ArtefactoDeclarado(
        "tabla_correlativa.csv.gz",
        "DataExportStrategy",
        "la correlativa en texto comprimido, legible sin pandas",
    ),
    ArtefactoDeclarado(
        "golden_records.parquet",
        "DataExportStrategy",
        "una fila por entidad consolidada",
    ),
    ArtefactoDeclarado(
        "golden_records.csv.gz",
        "DataExportStrategy",
        "el golden en texto comprimido, legible sin pandas",
    ),
)

ARTEFACTOS_OPCIONALES: tuple[ArtefactoDeclarado, ...] = (
    ArtefactoDeclarado("tabla_correlativa.xlsx", "DataExportStrategy", "correlativa en Excel"),
    ArtefactoDeclarado(
        "tabla_correlativa_MUESTRA_*.xlsx",
        "DataExportStrategy",
        "muestra de la correlativa en Excel cuando supera el límite de filas",
    ),
    ArtefactoDeclarado("golden_records.xlsx", "DataExportStrategy", "golden en Excel"),
    ArtefactoDeclarado(
        "golden_records_MUESTRA_*.xlsx",
        "DataExportStrategy",
        "muestra del golden en Excel cuando supera el límite de filas",
    ),
    ArtefactoDeclarado(
        "reporte_resumen_ejecutivo.xlsx", "ExcelReportsStrategy", "resumen ejecutivo"
    ),
    ArtefactoDeclarado(
        "reporte_metricas_calidad.xlsx", "ExcelReportsStrategy", "métricas de calidad"
    ),
    ArtefactoDeclarado(
        "reporte_analisis_fuentes.xlsx", "ExcelReportsStrategy", "contribución por fuente"
    ),
    ArtefactoDeclarado(
        "reporte_casos_revision.xlsx", "ExcelReportsStrategy", "casos que requieren revisión"
    ),
    ArtefactoDeclarado(
        "reporte_estadisticas_grupos.xlsx", "ExcelReportsStrategy", "estadísticas de grupos"
    ),
    ArtefactoDeclarado(
        "reporte_metricas_performance.xlsx", "ExcelReportsStrategy", "tiempos y velocidad"
    ),
    ArtefactoDeclarado(
        "visualizaciones/confidence_distribution.png",
        "VisualizationsStrategy",
        "distribución de confianza",
    ),
    ArtefactoDeclarado(
        "visualizaciones/linkage_overview.png", "VisualizationsStrategy", "vista general"
    ),
    ArtefactoDeclarado(
        "visualizaciones/source_comparison.png",
        "VisualizationsStrategy",
        "comparación de fuentes",
    ),
    ArtefactoDeclarado(
        "visualizaciones/quality_heatmap.png", "VisualizationsStrategy", "mapa de calor"
    ),
    ArtefactoDeclarado(
        "visualizaciones/group_size_analysis.png",
        "VisualizationsStrategy",
        "tamaño de grupos",
    ),
    ArtefactoDeclarado(
        "visualizaciones/performance_timeline.png",
        "VisualizationsStrategy",
        "línea de tiempo por fase",
    ),
    ArtefactoDeclarado("dashboard_ejecutivo.png", "DashboardStrategy", "dashboard ejecutivo"),
    ArtefactoDeclarado(
        "dashboard_ejecutivo_mejorado.png",
        "EnhancedInsightsStrategy",
        "dashboard ejecutivo mejorado",
    ),
    ArtefactoDeclarado(
        "heatmap_interseccion_mejorado.png",
        "EnhancedInsightsStrategy",
        "intersección entre fuentes",
    ),
    ArtefactoDeclarado(
        "tarjeta_calidad_datos.png", "EnhancedInsightsStrategy", "tarjeta de calidad"
    ),
    ArtefactoDeclarado(
        "casos_problematicos_detallado.xlsx",
        "EnhancedInsightsStrategy",
        "casos problemáticos con detalle",
    ),
    ArtefactoDeclarado(
        "config_auditoria.json",
        "ConfigAuditStrategy",
        "alias de v1 (F1.12): remite a manifest.json, donde vive la configuración efectiva",
    ),
)

ESTRATEGIAS_INCORPORADAS: tuple[str, ...] = (
    "DataExportStrategy",
    "ExcelReportsStrategy",
    "VisualizationsStrategy",
    "DashboardStrategy",
    "EnhancedInsightsStrategy",
    "ConfigAuditStrategy",
)

ESTRATEGIAS_OBLIGATORIAS: frozenset[str] = frozenset(a.estrategia for a in ARTEFACTOS_OBLIGATORIOS)


def clase_declarada(estrategia: object) -> str:
    """Nombre con el que el contrato conoce a una estrategia.

    Recorre la jerarquía de clases: una subclase de ``DataExportStrategy``
    (p. ej. una de captura en pruebas, o una personalizada) hereda su
    contrato. Si ninguna clase de la jerarquía está declarada, devuelve el
    nombre de la clase concreta.
    """

    tipo = estrategia if isinstance(estrategia, type) else type(estrategia)
    for clase in tipo.__mro__:
        if clase.__name__ in ESTRATEGIAS_INCORPORADAS:
            return clase.__name__
    return tipo.__name__


def es_estrategia_obligatoria(estrategia: object) -> bool:
    """Una estrategia es obligatoria si produce algún artefacto obligatorio
    (ella o una clase de la que hereda). Acepta la instancia, la clase o el
    nombre de la clase."""

    nombre = estrategia if isinstance(estrategia, str) else clase_declarada(estrategia)
    return nombre in ESTRATEGIAS_OBLIGATORIAS


def artefactos_de(estrategia: object) -> tuple[str, ...]:
    """Patrones (obligatorios y opcionales) que produce una estrategia
    (instancia, clase o nombre de clase; las subclases heredan la lista).

    Para una estrategia no declarada (añadida con
    ``Orchestrator.add_reporting_strategy``) devuelve una tupla vacía: sus
    omisiones se registran con el nombre de la estrategia como artefacto.
    """

    nombre = estrategia if isinstance(estrategia, str) else clase_declarada(estrategia)
    return tuple(
        a.patron for a in ARTEFACTOS_OBLIGATORIOS + ARTEFACTOS_OPCIONALES if a.estrategia == nombre
    )


def obligatorios_de(estrategia: object) -> tuple[str, ...]:
    """Solo los patrones OBLIGATORIOS que produce una estrategia (instancia,
    clase o nombre de clase; las subclases heredan la lista).

    Es lo que ``Orchestrator._run_L6`` nombra como faltante cuando una
    estrategia obligatoria lanza: sus Excel opcionales no faltan, se omiten.
    Para una estrategia sin obligatorios devuelve una tupla vacía.
    """

    nombre = estrategia if isinstance(estrategia, str) else clase_declarada(estrategia)
    return tuple(a.patron for a in ARTEFACTOS_OBLIGATORIOS if a.estrategia == nombre)


@dataclass(frozen=True)
class ReporteL6:
    """Resultado de :func:`verificar_artefactos`.

    Attributes:
        obligatorios_faltantes: patrones obligatorios sin archivo presente
            (en la lista de generados, existente y no vacío), en el orden de
            la declaración.
        opcionales_omitidos: patrones opcionales sin ningún archivo generado.
        generados: nombres relativos de lo que sí se generó.
        no_declarados: generados que no coinciden con ningún patrón; no es un
            error (estrategias personalizadas), pero se informa.
    """

    obligatorios_faltantes: tuple[str, ...]
    opcionales_omitidos: tuple[str, ...]
    generados: tuple[str, ...]
    no_declarados: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.obligatorios_faltantes


def _nombre_relativo(ruta: Path, output_dir: Path) -> str:
    ruta = Path(ruta)
    try:
        return ruta.resolve().relative_to(output_dir.resolve()).as_posix()
    except ValueError:
        return ruta.name


def _presente(ruta: Path) -> bool:
    try:
        return ruta.is_file() and ruta.stat().st_size > 0
    except OSError:
        return False


def verificar_artefactos(output_dir: Path, generados: Iterable[Path]) -> ReporteL6:
    """Compara lo generado contra la lista declarada.

    Un obligatorio cuenta como presente solo si (1) alguna ruta de
    ``generados`` coincide con su patrón por nombre exacto o patrón explícito
    —nunca por prefijo— y (2) ese archivo existe en disco y no está vacío.

    Args:
        output_dir: carpeta de L6 (``<work_dir>/L6_reporting``).
        generados: rutas que las estrategias devolvieron.
    """

    output_dir = Path(output_dir)
    rutas = [Path(p) for p in generados]
    nombres = [_nombre_relativo(p, output_dir) for p in rutas]
    presentes = {n: _presente(p) for n, p in zip(nombres, rutas, strict=True)}

    faltantes = tuple(
        a.patron
        for a in ARTEFACTOS_OBLIGATORIOS
        if not any(a.coincide(n) and presentes[n] for n in nombres)
    )
    omitidos = tuple(
        a.patron
        for a in ARTEFACTOS_OPCIONALES
        if not any(a.coincide(n) and presentes[n] for n in nombres)
    )
    declarados = ARTEFACTOS_OBLIGATORIOS + ARTEFACTOS_OPCIONALES
    no_declarados = tuple(n for n in nombres if not any(a.coincide(n) for a in declarados))
    return ReporteL6(
        obligatorios_faltantes=faltantes,
        opcionales_omitidos=omitidos,
        generados=tuple(nombres),
        no_declarados=no_declarados,
    )
