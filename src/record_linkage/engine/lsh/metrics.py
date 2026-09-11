"""
engine.metrics — record_linkage_pipeline

Componentes:
    - class EngineMetrics  (origen: notebook celda [120])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EngineMetrics:
    """Métricas de rendimiento del motor."""

    signatures_generated: int = 0
    index_entries: int = 0
    candidates_found: int = 0
    time_signatures: float = 0.0
    time_indexing: float = 0.0
    time_candidates: float = 0.0
    peak_memory_mb: float = 0.0
    bands_processed: int = 0
    buckets_processed: int = 0
    skipped_large_buckets: int = 0
    resumed_from_checkpoint: bool = False
