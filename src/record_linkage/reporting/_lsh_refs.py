"""Constantes y helpers internos del notebook fuente.

Estos símbolos eran globales del notebook. Aquí se preservan
para mantener compatibilidad con clases que los referencian.
"""

from __future__ import annotations

from ._models import IterationData

# ────────────────────────────────────────────────────────────
# CONFIGURACIONES_LSH  (origen: notebook celda [193], tipo: assign)
# ────────────────────────────────────────────────────────────
CONFIGURACIONES_LSH = {
    128: [(16, 8, 0.707), (32, 4, 0.595)],
    252: [(36, 7, 0.599), (42, 6, 0.536), (28, 9, 0.672)],
    256: [(32, 8, 0.648), (64, 4, 0.500), (16, 16, 0.778)],
}

# ────────────────────────────────────────────────────────────
# N_REF  (origen: notebook celda [193], tipo: assign)
# ────────────────────────────────────────────────────────────
N_REF = 2_196_915

# ────────────────────────────────────────────────────────────
# TIEMPO_POR_BANDA  (origen: notebook celda [193], tipo: assign)
# ────────────────────────────────────────────────────────────
TIEMPO_POR_BANDA = {
    16: 2.1,  # IT-2
    32: 2.2,  # IT-4
    36: 5.7,  # IT-7
    42: 37.0,  # IT-8 (CRÍTICO)
}

# ────────────────────────────────────────────────────────────
# ITERACIONES_REFERENCIA  (origen: notebook celda [193])
# ────────────────────────────────────────────────────────────
ITERACIONES_REFERENCIA: dict[str, IterationData] = {
    "IT-2": IterationData("IT-2 (Safe)", 0.707, 128, 16, 8, 44, 101, 157, 5.6, 5.23, "OK"),
    "IT-4": IterationData("IT-4 (Balanced)", 0.648, 256, 32, 8, 77, 189, 266, 7.6, 8.97, "OK"),
    "IT-5": IterationData("IT-5 (Deep)", 0.599, 252, 36, 7, 193, 419, 663, 12.0, 12.94, "LIMITE"),
    "IT-7": IterationData("IT-7 (Optimal)", 0.599, 252, 36, 7, 193, 342, 526, 6.1, 12.93, "OK"),
    "IT-8": IterationData("IT-8 (Fail)", 0.536, 252, 42, 6, 480, 1370, 1800, 12.0, 0, "TIMEOUT"),
}
