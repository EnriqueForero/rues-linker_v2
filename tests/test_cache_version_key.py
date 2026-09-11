"""Invalidación del cache MinHash por versión de esquema (F0.3, v0.8.0).

La clave del cache incluye las versiones de datasketch y de rues-linker:
firmas generadas bajo un esquema (p. ej. datasketch 1.x) jamás deben
reusarse bajo otro (p. ej. 2.x), porque el mismo input+seed produce
hashvalues distintas entre majors (incidente documentado en CHANGELOG
[0.7.6]). Estos tests congelan ese contrato.
"""

from __future__ import annotations

import datasketch
import pandas as pd
import pytest

from record_linkage.engine.lsh.cache import MinHashCache


@pytest.fixture()
def df_min() -> pd.DataFrame:
    return pd.DataFrame({"NOMBRE_LIMPIO": ["ACME SAS", "BETA LTDA", "GAMMA SA"]})


def test_key_estable_en_mismo_entorno(df_min: pd.DataFrame) -> None:
    """Mismo df + mismos parámetros + mismo entorno → misma key."""
    k1 = MinHashCache.compute_key(df_min, num_perm=128, ngram=3, seed=42)
    k2 = MinHashCache.compute_key(df_min, num_perm=128, ngram=3, seed=42)
    assert k1 == k2


def test_cambio_de_version_datasketch_invalida_key(
    df_min: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si cambia la versión de datasketch, la key DEBE cambiar."""
    k_actual = MinHashCache.compute_key(df_min, num_perm=128, ngram=3, seed=42)
    monkeypatch.setattr(datasketch, "__version__", "999.0.0")
    k_otra = MinHashCache.compute_key(df_min, num_perm=128, ngram=3, seed=42)
    assert k_actual != k_otra, (
        "La key del cache no cambió al cambiar la versión de datasketch: "
        "riesgo de reusar firmas de otro esquema (ver CHANGELOG [0.7.6])."
    )


def test_key_contiene_esquema_de_versiones_sin_romper_params(
    df_min: pd.DataFrame,
) -> None:
    """Los contratos previos de la key (params/contenido) siguen vigentes."""
    base = MinHashCache.compute_key(df_min, num_perm=128, ngram=3, seed=42)
    assert base != MinHashCache.compute_key(df_min, num_perm=256, ngram=3, seed=42)
    assert base != MinHashCache.compute_key(df_min, num_perm=128, ngram=4, seed=42)
    assert base != MinHashCache.compute_key(df_min, num_perm=128, ngram=3, seed=99)
    df_otro = df_min.assign(NOMBRE_LIMPIO=["OTRA SAS", "BETA LTDA", "GAMMA SA"])
    assert base != MinHashCache.compute_key(df_otro, num_perm=128, ngram=3, seed=42)


def test_key_detecta_cambio_en_fila_no_muestreada() -> None:
    df = pd.DataFrame({"NOMBRE_LIMPIO": [f"EMPRESA {i}" for i in range(3_000)]})
    otro = df.copy()
    otro.loc[1_337, "NOMBRE_LIMPIO"] = "CAMBIO FUERA DEL STRIDE HISTORICO"
    assert MinHashCache.compute_key(df, 128, 3, 42) != MinHashCache.compute_key(otro, 128, 3, 42)
