"""tests/integration/test_nit_processor_parity.py

Test de regresión para F6.1: la vectorización de
`AdvancedNitProcessor.process_for_deduplication` debe producir resultados
bit-exact contra la implementación original (loop con `series.items()`).
"""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage.processing.nit import AdvancedNitProcessor


def _legacy_process_for_deduplication(
    proc: AdvancedNitProcessor, series: pd.Series
) -> pd.DataFrame:
    """Reimplementación literal de la versión pre-v2.1.0 (con loop).
    Se mantiene aquí como oracle de parity, NUNCA en código productivo.

    v2.10.0: enhanced_fix_nit ahora retorna (base, ok, dv_origen).
    """
    results = []
    for _idx, value in series.items():
        nit_base, nit_ok, dv_origen = proc.enhanced_fix_nit(value)
        results.append(
            {
                "NIT_ORIGINAL": value,
                "NIT_BASE": nit_base,
                "NIT_OK": nit_ok,
                "DV_ORIGEN": dv_origen,
            }
        )
    return pd.DataFrame(results, index=series.index)


@pytest.fixture
def diverse_nit_series() -> pd.Series:
    """Casos de NITs cubriendo cada rama de enhanced_fix_nit:
    - 9 dígitos limpios (cálculo de DV)
    - 9 dígitos con caracteres no-dígito (ejercita non_digit_regex)
    - 10 dígitos (preservación intacta)
    - Alfanuméricos (pasaporte)
    - Decimales (split por punto)
    - Vacíos y NaN
    - Repetidos (ejercita lru_cache)
    """
    return pd.Series(
        [
            "900123456",  # 9 dig limpio
            "900-123-456",  # 9 dig con guiones
            "900 123 456",  # 9 dig con espacios
            "900.123.456",  # con punto
            "1234567890",  # 10 dig
            "AB12345",  # alfanumérico
            "",  # vacío
            None,  # NaN
            "900123456",  # repetido (cache hit)
            "8001234567",  # otro 10 dig
            "  900111222  ",  # con whitespace
        ]
    )


def test_parity_with_legacy_implementation(diverse_nit_series: pd.Series):
    """La versión vectorizada debe producir EXACTAMENTE las mismas columnas
    y los mismos valores que la versión legacy con loop."""
    proc_new = AdvancedNitProcessor()
    proc_old = AdvancedNitProcessor()

    new_result = proc_new.process_for_deduplication(diverse_nit_series)
    old_result = _legacy_process_for_deduplication(proc_old, diverse_nit_series)

    # Mismas columnas en el mismo orden
    assert list(new_result.columns) == list(old_result.columns)

    # Mismo índice
    assert (new_result.index == old_result.index).all()

    # Mismos valores en cada columna (fillna para igualar None vs NaN)
    for col in ["NIT_ORIGINAL", "NIT_BASE", "NIT_OK", "DV_ORIGEN"]:
        eq = (new_result[col].fillna("") == old_result[col].fillna("")).all()
        assert eq, f"Parity rota en columna '{col}'"


def test_empty_series_returns_empty_dataframe():
    """Edge case: serie vacía no debe levantar excepción."""
    proc = AdvancedNitProcessor()
    result = proc.process_for_deduplication(pd.Series([], dtype=str))
    assert len(result) == 0
    assert list(result.columns) == ["NIT_ORIGINAL", "NIT_BASE", "NIT_OK", "DV_ORIGEN"]


def test_preserves_input_index(diverse_nit_series: pd.Series):
    """El índice del input debe preservarse exactamente (crítico para joins
    posteriores en el pipeline)."""
    custom_index = pd.Index([f"row_{i}" for i in range(len(diverse_nit_series))])
    s = pd.Series(diverse_nit_series.values, index=custom_index)
    proc = AdvancedNitProcessor()
    result = proc.process_for_deduplication(s)
    assert (result.index == custom_index).all()


def test_9_digit_nit_gets_verification_digit():
    """NIT de 9 dígitos debe recibir DV concatenado en NIT_OK."""
    proc = AdvancedNitProcessor()
    result = proc.process_for_deduplication(pd.Series(["900123456"]))
    assert result.loc[0, "NIT_BASE"] == "900123456"
    assert result.loc[0, "NIT_OK"] == "9001234568"  # DV = 8


def test_alphanumeric_preserved():
    """NIT alfanumérico (pasaporte) se preserva intacto."""
    proc = AdvancedNitProcessor()
    result = proc.process_for_deduplication(pd.Series(["AB12345"]))
    assert result.loc[0, "NIT_BASE"] == "AB12345"
    assert result.loc[0, "NIT_OK"] == "AB12345"
