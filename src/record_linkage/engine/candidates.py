"""
engine.candidates — record_linkage_pipeline

Componentes:
    - class CandidateFinder  (origen: notebook celda [118])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class CandidateFinder(Protocol):
    """Protocolo para encontrar candidatos de vinculación."""

    def find_candidates(self, df: pd.DataFrame) -> set[tuple[int, int]]: ...
