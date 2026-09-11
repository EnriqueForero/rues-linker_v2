"""Flujo de alto nivel para cruces reproducibles.

Contexto: Google Colab Free (~12 GB RAM, sesión ~12 h, Drive por FUSE).
Este subpaquete existe para que los notebooks sean DELGADOS: toda la lógica
—preflight, copia anti-Drive, smoke test, invariantes, exportes, metadatos—
vive aquí, con pruebas, en vez de repetirse en celdas que nadie testea.

Uso típico::

    from record_linkage.flujo import ConfigCruce, ejecutar_cruce

    cfg = ConfigCruce(fuentes=[spec_a, spec_b], workspace="/content/salida")
    resultado = ejecutar_cruce(cfg)
    resultado.resumen()

Para una base SIN identificador —solo nombre y país— el flujo es
``importadores``::

    from record_linkage.flujo import ConfigImportadores, deduplicar_importadores

    resultado = deduplicar_importadores(df, ConfigImportadores(...))
    assert resultado.todo_ok
    resultado.correlativa.head()
"""

from .cruce import (
    ConfigCruce,
    ControlCalidad,
    ResultadoCruce,
    ResultadoCruceDisco,
    ejecutar_cruce,
    exportar_sin_pareja,
    pares_enlazados,
    resolver_motor,
)
from .diagnostico import DiagnosticoIdentificador, diagnosticar_identificadores
from .importadores import (
    ConfigImportadores,
    PaisesSinClasificar,
    ResultadoImportadores,
    cobertura_paises,
    construir_entregables,
    decisiones_con_nombres,
    deduplicar_importadores,
    exigir_cobertura_paises,
    grafias_aisladas,
    metricas,
    muestra_para_revision,
    preparar,
    recall_del_bloqueo,
    registrar_locale,
    sensibilidad_umbral,
    similitud_nombre_desde_score,
    verificar_invariantes,
)
from .insumos import filas_en_cache, preparar_insumo_local
from .reportes import reportar_composicion
from .resultados_disco import PublicacionResultadosDisco, TablaParquet

__all__ = [
    "ConfigCruce",
    "ConfigImportadores",
    "ControlCalidad",
    "DiagnosticoIdentificador",
    "PaisesSinClasificar",
    "PublicacionResultadosDisco",
    "ResultadoCruce",
    "ResultadoCruceDisco",
    "ResultadoImportadores",
    "TablaParquet",
    "cobertura_paises",
    "construir_entregables",
    "decisiones_con_nombres",
    "deduplicar_importadores",
    "diagnosticar_identificadores",
    "ejecutar_cruce",
    "exigir_cobertura_paises",
    "exportar_sin_pareja",
    "filas_en_cache",
    "grafias_aisladas",
    "metricas",
    "muestra_para_revision",
    "pares_enlazados",
    "preparar",
    "preparar_insumo_local",
    "recall_del_bloqueo",
    "registrar_locale",
    "reportar_composicion",
    "resolver_motor",
    "sensibilidad_umbral",
    "similitud_nombre_desde_score",
    "verificar_invariantes",
]
