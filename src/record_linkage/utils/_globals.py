"""Constantes y helpers internos del notebook fuente.

Estos símbolos eran globales del notebook. Aquí se preservan
para mantener compatibilidad con clases que los referencian.
"""

from __future__ import annotations

from .performance import PerformanceTracker

# ────────────────────────────────────────────────────────────
# performance_tracker  (origen: notebook celda [109], tipo: assign)
# ────────────────────────────────────────────────────────────
performance_tracker = PerformanceTracker()
