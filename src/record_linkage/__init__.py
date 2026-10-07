"""record_linkage — Pipeline de deduplicación y record linkage para
fuentes empresariales colombianas (RUES, DIAN, CRM, SUPERSOCIEDADES).

Refactor del notebook `2026_02_15_DEDUPLICAR_Y_RECORD_LINKAGE_.ipynb`
a paquete .py estructurado. Lógica de negocio intacta.

Componentes principales:
    - config:        Config y Rutas centralizadas, perfiles LSH
    - utils:         Tiempo, memoria, logging, performance
    - classifier:    Clasificador híbrido (Sección 2)
    - processing:    Limpieza de texto y NIT
    - engine:        Motor de record linkage (LSH + scoring + clustering)
    - golden:        Generación de golden records
    - evaluation:    banco, conformidad, métricas y Optuna (OrchestratorOptimizer)
    - reporting:     Reportes, visualizaciones, dashboards
    - pipeline:      Orchestrator de producción
    - deduplication: Modo deduplicación específico
    - exporters:     Exportación multi-formato

La versión es única y vive en `pyproject.toml`; aquí se lee dinámicamente
con `importlib.metadata` para que no exista una segunda fuente de verdad
que pueda desincronizarse.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("rues-linker")
except PackageNotFoundError:  # pragma: no cover - checkout sin instalar
    __version__ = "0.0.0+sin.instalar"  # centinela: nunca una versión real

# ── API pública de alto nivel ─────────────────────────────────────────
# `linkage()` es el punto de entrada recomendado. Las demás se exponen para
# casos específicos. Los componentes centrales son imports directos: un error
# interno debe fallar de inmediato con su traceback, no publicarse como un
# símbolo ``None`` que rompe mucho después y oculta la causa raíz.
from .api import ResultadoLinkage, dedupe, dedupe_esquema, link, linkage
from .config.paths import Rutas
from .config.profiles import crear_config_orchestrator, get_profile
from .config.settings import Config
from .deduplication.auto import deduplicate_auto
from .deduplication.unified import AjustesDeduplicacion, deduplicate_unified
from .engine.cobertura import ResultadoCobertura, cobertura_estrella
from .evaluation.pairwise import evaluar_pares
from .exporters.escritor import Manifiesto, escribir_resultado, exportar_vistas, leer_resultado
from .flujo.importadores import (
    ConfigImportadores,
    ResultadoImportadores,
    deduplicar_importadores,
)
from .ingestion import (
    ColumnType,
    Compression,
    IdentifierFormat,
    InputFormat,
    InvalidValuePolicy,
    LoadedSource,
    NumericFormat,
    SourceLoadReport,
    SourceSpec,
    ZipSafetyLimits,
    iter_source_chunks,
    load_source,
    load_sources,
)

# Matching multi-variable: el módulo `matching` existía pero no estaba
# conectado ni exportado en la API pública. Este bloque lo expone.
from .matching import (
    MatcherPostProcessor,
    MatchingProfile,
    VariableMatcher,
    VariableSpec,
    default_colombia_profile,
    default_international_profile,
)
from .matching.campos import (
    CampoSpec,
    CorroboracionVeto,
    EsquemaCampos,
    PoliticaFaltante,
    TipoCampo,
    esquema_multicampo_completo,
    esquema_rues,
)
from .matching.genericos import (
    GENERICOS_ESTRUCTURALES,
    GENERICOS_GEOGRAFIA,
    GENERICOS_SECTOR,
    SUFIJOS_INTERNACIONALES,
)
from .matching.inferencia import sugerir_esquema, sugerir_esquema_detallado
from .matching.motor_multicampo import (
    ResultadoMulticampo,
    clusters_desde_decisiones,
    evaluar_esquema,
)
from .matching.nombre_idf import SimilitudNombre, neutralizar_genericos
from .paises import CATALOGO_PAISES, ResultadoPaises, canonizar_pais, sugerir_alias_pais
from .pipeline.orchestrator import Orchestrator
from .processing.saneamiento import ascii_mayusculas, sanear_texto, unir_iniciales
from .resultado import ReporteValidacion

__all__ = [
    "CATALOGO_PAISES",
    "GENERICOS_ESTRUCTURALES",
    "GENERICOS_GEOGRAFIA",
    "GENERICOS_SECTOR",
    "SUFIJOS_INTERNACIONALES",
    "AjustesDeduplicacion",
    "CampoSpec",
    "ColumnType",
    "Compression",
    "Config",
    "ConfigImportadores",
    "CorroboracionVeto",
    "EsquemaCampos",
    "IdentifierFormat",
    "InputFormat",
    "InvalidValuePolicy",
    "LoadedSource",
    "Manifiesto",
    "MatcherPostProcessor",
    "MatchingProfile",
    "NumericFormat",
    "Orchestrator",
    "PoliticaFaltante",
    "ReporteValidacion",
    "ResultadoCobertura",
    "ResultadoImportadores",
    "ResultadoLinkage",
    "ResultadoMulticampo",
    "ResultadoPaises",
    "Rutas",
    "SimilitudNombre",
    "SourceLoadReport",
    "SourceSpec",
    "TipoCampo",
    "VariableMatcher",
    "VariableSpec",
    "ZipSafetyLimits",
    "__version__",
    "ascii_mayusculas",
    "canonizar_pais",
    "clusters_desde_decisiones",
    "cobertura_estrella",
    "crear_config_orchestrator",
    "dedupe",
    "deduplicar_importadores",
    "deduplicate_auto",
    "deduplicate_unified",
    "default_colombia_profile",
    "default_international_profile",
    "escribir_resultado",
    "esquema_multicampo_completo",
    "esquema_rues",
    "evaluar_esquema",
    "evaluar_pares",
    "exportar_vistas",
    "get_profile",
    "iter_source_chunks",
    "leer_resultado",
    "link",
    "linkage",
    "load_source",
    "load_sources",
    "neutralizar_genericos",
    "sanear_texto",
    "sugerir_alias_pais",
    "sugerir_esquema",
    "sugerir_esquema_detallado",
    "unir_iniciales",
]
