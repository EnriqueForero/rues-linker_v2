"""
engine.defaults — record_linkage_pipeline

Componentes:
    - class LSHDefaults  (origen: notebook celda [120])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LSHDefaults:
    """Valores por defecto inmutables para configuración LSH."""

    PERMUTATIONS: int = 128
    THRESHOLD: float = 0.75
    NGRAM_SIZE: int = 3
    CHUNK_SIZE: int = 100_000
    BATCH_SIZE: int = 50_000
    MEMORY_THRESHOLD_CANDIDATES: int = 1_000_000
    MAX_BUCKET_SIZE: int = 500
    MIN_BUCKET_SIZE: int = 2
    COMMIT_INTERVAL: int = 200_000
    GC_INTERVAL: int = 5
