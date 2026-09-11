"""
deduplication.nit_assignment — record_linkage_pipeline

Componentes:
    - function get_final_nit_for_group  (origen: notebook celda [151])
    - function calc_dv  (origen: notebook celda [151])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache

import pandas as pd


def get_final_nit_for_group(group_df: pd.DataFrame, col_nit_ok: str = "NIT_OK") -> str:
    """
    VERSIÓN DEFINITIVA Y CORREGIDA de get_final_nit_for_group v2.0

    Determina el NIT final para un grupo de registros duplicados, tratando
    correctamente los NITs de empresa (9/10 dígitos) y otros documentos (cédulas, etc.).

    Esta es la implementación EXACTA del código original corregido.
    """
    nits_in_group = [str(nit) for nit in group_df[col_nit_ok].dropna() if str(nit).strip()]
    if not nits_in_group:
        return ""

    # Determinar el tipo de grupo basado en la longitud más común
    length_counts = Counter(len(nit) for nit in nits_in_group)
    if not length_counts:
        return nits_in_group[0] if nits_in_group else ""

    most_common_len, _ = length_counts.most_common(1)[0]

    # REGLA CLAVE: Solo si la longitud más común es 9 o 10, tratamos el grupo
    # como un grupo de empresas
    if most_common_len in (9, 10):
        # ESTRATEGIA PARA EMPRESAS: Votación posicional y cálculo de DV

        # Votación posicional para determinar el NIT base de 9 dígitos
        base_nit_digits = []
        for i in range(9):
            position_votes = Counter(
                nit[i] for nit in nits_in_group if nit.isdigit() and len(nit) > i
            )
            if position_votes:
                base_nit_digits.append(position_votes.most_common(1)[0][0])
            else:
                # Si no hay votos, usar estrategia de mayoría simple
                return max(set(nits_in_group), key=lambda nit: (nits_in_group.count(nit), len(nit)))

        base_nit = "".join(base_nit_digits)

        # Calcular DV correcto
        dv = calc_dv(base_nit)  # Usar la función del código original
        return f"{base_nit}{dv}" if dv else base_nit

    else:
        # ESTRATEGIA PARA OTROS DOCUMENTOS: Votación por mayoría
        # Nunca añadir dígito de verificación
        return max(set(nits_in_group), key=lambda nit: (nits_in_group.count(nit), len(nit)))


@lru_cache(maxsize=1_000)
def calc_dv(nit9: str) -> str:
    """
    Calcula el dígito de verificación para un NIT base de 9 dígitos.
    Implementación exacta del código original.
    """
    if not isinstance(nit9, str) or not nit9.isdigit() or len(nit9) != 9:
        return ""

    pesos = [3, 7, 13, 17, 19, 23, 29, 37, 41]
    s = sum(int(nit9[8 - i]) * pesos[i] for i in range(9))
    r = s % 11

    if r < 2:
        dv = r
    else:
        dv = 11 - r

    return str(dv)
