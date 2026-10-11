"""``DataHandler._basic_cleanup`` no inventa el texto «<NA>» para un nulo.

En pandas 2, ``Series(["a", pd.NA], dtype="string").astype(str)`` deja el
literal ``"<NA>"`` y la limpieza posterior solo reconoce «nan», «None», «NULL»
y «»: una razón social vacía pasaba a llamarse «<NA>» (y dos filas sin nombre
quedaban con el mismo nombre) y un NIT vacío salía como el TEXTO «<NA>» en el
entregable. Las pruebas del camino ``cruce`` lo vieron en la matriz de Python
3.10 del CI. pandas 3 conserva el nulo; la regla tiene que ser la misma en las
dos versiones.
"""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage.reporting.data_handler import DataHandler

#: Lo que un nulo nunca debe llegar a ser como texto.
LITERALES_DE_NULO = {"<NA>", "nan", "NaN", "None", "NULL", ""}


@pytest.mark.parametrize("dtype", ["string", "object"])
def test_un_nulo_no_se_convierte_en_texto(dtype: str) -> None:
    nulo = pd.NA if dtype == "string" else None
    df = pd.DataFrame(
        {
            "NIT": pd.Series(["800200000", nulo, "  ", "900100200"], dtype=dtype),
            "RAZON_SOCIAL": pd.Series(["ACME SAS", nulo, None, "OTRA LTDA"], dtype=dtype),
            "CIUDAD": ["BOGOTA", "CALI", "BOGOTA", "CALI"],
        }
    )

    limpio = DataHandler()._basic_cleanup(df)

    for columna in ("NIT", "RAZON_SOCIAL"):
        textos = set(limpio[columna].dropna().astype(str))
        assert not (textos & LITERALES_DE_NULO), (columna, textos)
    assert limpio["NIT"].isna().tolist() == [False, True, True, False]
    assert limpio["RAZON_SOCIAL"].isna().tolist() == [False, True, True, False]
    assert limpio["NIT"].dropna().tolist() == ["800200000", "900100200"]
    assert limpio["RAZON_SOCIAL"].dropna().tolist() == ["ACME SAS", "OTRA LTDA"]


def test_el_valor_con_texto_no_se_toca() -> None:
    """La conversión a texto sigue haciendo lo de siempre con lo que no es nulo."""
    df = pd.DataFrame({"NIT": [800200000, 900100200], "RAZON_SOCIAL": ["  Acme  ", "Otra"]})

    limpio = DataHandler()._basic_cleanup(df)

    assert limpio["NIT"].tolist() == ["800200000", "900100200"]
    assert limpio["RAZON_SOCIAL"].tolist() == ["Acme", "Otra"]
