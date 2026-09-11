"""
engine.state — record_linkage_pipeline

Componentes:
    - class EngineState  (origen: notebook celda [120])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from enum import Enum, auto


class EngineState(Enum):
    """Estados del motor LSH."""

    UNINITIALIZED = auto()
    INITIALIZED = auto()
    SIGNATURES_READY = auto()
    INDEX_READY = auto()
    CANDIDATES_READY = auto()
    ERROR = auto()
    CLEANED = auto()
