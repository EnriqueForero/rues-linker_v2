"""Regresiones de tipos que antes fabricaban identificadores o números."""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage.matching.campos import CampoSpec, TipoCampo
from record_linkage.matching.normalizadores import (
    normalizar_campo,
    normalizar_identificador,
    normalizar_numero,
)


def test_identificador_float_integral_does_not_gain_a_zero() -> None:
    values = pd.Series([890002474.0, 123.5, float(2**53 + 2)])

    normalized = normalizar_identificador(values)

    assert normalized.tolist() == ["890002474", "", ""]


def test_identificador_alphanumeric_preserves_letters() -> None:
    values = pd.Series(["AB-001-xy", "000000", None])

    normalized = normalizar_identificador(
        values, modo="alphanumeric", min_longitud=4, max_longitud=20
    )

    assert normalized.tolist() == ["AB001XY", "", ""]


def test_numeric_locale_handles_both_common_conventions_vectorized() -> None:
    european = normalizar_numero(
        pd.Series(["1.234,56", "2.000,00"]),
        separador_decimal=",",
        separador_miles=".",
    )
    american = normalizar_numero(
        pd.Series(["1,234.56", "2,000.00"]),
        separador_decimal=".",
        separador_miles=",",
    )

    assert european.tolist() == ["1234.56", "2000.0"]
    assert american.tolist() == ["1234.56", "2000.0"]


def test_campo_spec_dispatches_new_type_parameters() -> None:
    identifier = CampoSpec(
        "ID",
        TipoCampo.IDENTIFICADOR,
        params={"modo": "alphanumeric", "min_longitud": 3, "max_longitud": 12},
    )
    number = CampoSpec(
        "MONTO",
        TipoCampo.NUMERICO,
        params={"separador_decimal": ",", "separador_miles": "."},
    )

    assert normalizar_campo(pd.Series(["CO-1"]), identifier).iloc[0] == "CO1"
    assert normalizar_campo(pd.Series(["1.234,5"]), number).iloc[0] == "1234.5"


def test_campo_spec_rejects_incoherent_type_parameters_immediately() -> None:
    with pytest.raises(ValueError, match="min_longitud"):
        CampoSpec(
            "ID",
            TipoCampo.IDENTIFICADOR,
            params={"min_longitud": 10, "max_longitud": 4},
        )
    with pytest.raises(ValueError, match="distintos"):
        CampoSpec(
            "MONTO",
            TipoCampo.NUMERICO,
            params={"separador_decimal": ".", "separador_miles": "."},
        )
