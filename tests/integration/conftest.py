"""tests/integration/conftest.py

Fixtures compartidas por los tests de integración end-to-end
(`test_deduplicate_unified.py` y `test_orchestrator.py`).

Estas fixtures construyen DataFrames sintéticos pequeños (≤10 filas) con
ground truth sembrado explícitamente, de modo que las aserciones de los tests
sean inequívocas y las corridas terminen en segundos.
"""

from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture
def df_empty() -> pd.DataFrame:
    """DataFrame vacío pero con las columnas esperadas.

    Se usa para verificar que el pipeline levante ``ValueError`` explícito
    ante entrada vacía, en lugar de fallar silenciosamente aguas abajo.
    """
    return pd.DataFrame({"NIT": [], "RAZON_SOCIAL": []})


@pytest.fixture
def df_single_source_with_duplicates() -> pd.DataFrame:
    """8 registros de una sola fuente con 2 pares de duplicados sembrados.

    Ground truth (6 grupos finales esperados):
        - NIT 900123456 → 2 registros (variación de sufijo)      [par 1]
        - NIT 800999111 → 2 registros (variación de espaciado)   [par 2]
        - 4 registros restantes con NITs únicos                  [singletons]

    Reducción esperada: 8 → 6 grupos (25%). Los NITs idénticos DEBEN
    colapsarse; el resto debe permanecer separado.
    """
    return pd.DataFrame(
        {
            "NIT": [
                "900123456",
                "900123456",  # par 1 (mismo NIT)
                "800999111",
                "800999111",  # par 2 (mismo NIT)
                "901111222",  # singleton
                "800333444",  # singleton
                "901555666",  # singleton
                "830777888",  # singleton
            ],
            "RAZON_SOCIAL": [
                "TEXTILES DEL PACIFICO SAS",
                "TEXTILES DEL PACIFICO S.A.S.",
                "AGROINDUSTRIAL MANUELITA SA",
                "AGRO INDUSTRIAL MANUELITA SA",
                "CONFECCIONES ARCOIRIS SAS",
                "METALES DEL ORIENTE SAS",
                "LOGISTICA GLOBAL ANDINA SAS",
                "EDITORIAL LETRAS DORADAS SAS",
            ],
        }
    )


@pytest.fixture
def df_two_sources_with_cross_matches() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Dos fuentes de 5 registros cada una con 3 NITs en común (cross-match).

    Ground truth:
        - NITs 900123456, 800999111, 901234567 aparecen en AMBAS fuentes
          → 3 grupos cross-source.
        - 2 NITs exclusivos por fuente → singletons.
        - Total esperado: 5 + 5 - 3 = 7 entidades golden.
    """
    source_a = pd.DataFrame(
        {
            "NIT": ["900123456", "800999111", "901234567", "901111000", "800222000"],
            "RAZON_SOCIAL": [
                "TEXTILES DEL PACIFICO SAS",
                "AGROINDUSTRIAL MANUELITA SA",
                "CONFECCIONES ARCOIRIS SAS",
                "SERVICIOS INTEGRALES DEL SUR SAS",  # solo en A
                "TECNOLOGIA E INNOVACION SAS",  # solo en A
            ],
        }
    )
    source_b = pd.DataFrame(
        {
            "NIT": ["900123456", "800999111", "901234567", "830444000", "900555000"],
            "RAZON_SOCIAL": [
                "TEXTILES DEL PACIFICO S.A.S.",  # match A (sufijo)
                "AGRO INDUSTRIAL MANUELITA SA",  # match A (espaciado)
                "CONFECCIONES ARCOIRIS SOCIEDAD POR ACCIONES SIMPLIFICADA",  # match A
                "EDITORIAL LETRAS DORADAS SAS",  # solo en B
                "CLINICA VISION TOTAL SAS",  # solo en B
            ],
        }
    )
    return source_a, source_b
