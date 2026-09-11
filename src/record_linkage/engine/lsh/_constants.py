"""Constantes y helpers internos del notebook fuente.

Estos símbolos eran globales del notebook. Aquí se preservan
para mantener compatibilidad con clases que los referencian.
"""

from __future__ import annotations

import numpy as np

# ────────────────────────────────────────────────────────────
# _MERSENNE_PRIME  (origen: notebook celda [195], tipo: assign)
# ────────────────────────────────────────────────────────────
_MERSENNE_PRIME = np.uint64((1 << 61) - 1)
