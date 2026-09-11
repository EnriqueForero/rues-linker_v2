"""Tests del contrato universal de columnas y extra_features (v0.12.0, H1).

Cubren el hallazgo H1 de la auditoría 2026-08-26: hasta v0.11.x, la ruta
``linkage()``/``link()`` DESCARTABA en silencio ``col_name``, ``col_nit``,
``col_ciudad`` y ``extra_features`` (crear_config_orchestrator los ignoraba
con un print y el pipeline exigía RAZON_SOCIAL/NIT cableados).

Contrato v0.12.0:
    1. Los kwargs documentados de la API rigen de verdad (mapeo en ingesta).
    2. Un override desconocido lanza ValueError con sugerencia (fail-fast).
    3. extra_features llegan al perfil del scorer, validadas contra columnas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.config.profiles import crear_config_orchestrator
from record_linkage.pipeline.orchestrator import Orchestrator


def _fuente_demo(n: int = 24, seed: int = 42) -> pd.DataFrame:
    """Mini-fuente sintética con duplicados obvios (determinista)."""
    rng = np.random.default_rng(seed)
    bases = [
        "COMERCIALIZADORA ANDINA",
        "TEXTILES DEL PACIFICO",
        "CAFE DE COLOMBIA EXPORT",
        "FERRETERIA CENTRAL",
    ]
    filas = []
    for i in range(n):
        nombre = f"{bases[i % len(bases)]} {i // len(bases)} S A S"
        nit = str(800100200 + i)
        filas.append((nombre, nit, "BOGOTA", f"30012345{i:02d}"))
        if i % 3 == 0:  # duplicado con variación de sufijo
            filas.append((nombre.replace(" S A S", " SAS"), nit, "BOGOTA", f"30012345{i:02d}"))
    df = pd.DataFrame(filas, columns=["RAZON_SOCIAL", "NIT", "CIUDAD", "TELEFONO"])
    return df.sample(frac=1.0, random_state=int(rng.integers(0, 1000))).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────
# 1. Mapeo de columnas end-to-end (paridad renombrado vs canónico)
# ─────────────────────────────────────────────────────────────────────


def test_linkage_respeta_columnas_renombradas(tmp_path):
    """linkage(col_name=..., col_nit=...) produce la MISMA correlativa que
    la corrida con nombres canónicos. Este test falla en v0.11.x (H1)."""
    from record_linkage import linkage

    df = _fuente_demo()
    df_renombrado = df.rename(
        columns={"RAZON_SOCIAL": "COMPANY_NAME", "NIT": "TAX_ID", "CIUDAD": "CITY"}
    )

    res_canonico = linkage(
        sources={"SRC_A": df},
        work_dir=str(tmp_path / "canonico"),
        profile="prueba_rapida",
    )
    res_renombrado = linkage(
        sources={"SRC_A": df_renombrado},
        col_name="COMPANY_NAME",
        col_nit="TAX_ID",
        col_ciudad="CITY",
        work_dir=str(tmp_path / "renombrado"),
        profile="prueba_rapida",
    )

    corr_a = res_canonico["correlative"].sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    corr_b = res_renombrado["correlative"].sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    assert len(corr_a) == len(corr_b) == len(df)
    # Paridad de la PARTICIÓN: mismos grupos (los IDs concretos pueden variar
    # de corrida a corrida solo si el pipeline no fuera determinista; se exige
    # igualdad exacta de particiones vía co-pertenencia).
    grupos_a = corr_a.groupby("ID_GRUPO").groups
    grupos_b = corr_b.groupby("ID_GRUPO").groups
    particion_a = sorted(tuple(sorted(v)) for v in grupos_a.values())
    particion_b = sorted(tuple(sorted(v)) for v in grupos_b.values())
    assert particion_a == particion_b


def test_mapping_ambiguo_lanza_error(tmp_path):
    """Fuente con la columna del usuario Y la canónica a la vez → error accionable."""
    df = _fuente_demo().rename(columns={"NIT": "TAX_ID"})
    df["NIT"] = "999999999"  # ambigüedad: TAX_ID (mapeada) y NIT conviven
    config = crear_config_orchestrator(
        perfil="prueba_rapida",
        workspace=str(tmp_path),
        validate=False,
        col_nit="TAX_ID",
    )
    with pytest.raises(ValueError, match="no puede adivinar"):
        Orchestrator(config=config, sources={"A": df}, work_dir=str(tmp_path))


# ─────────────────────────────────────────────────────────────────────
# 2. Overrides desconocidos: fail-fast con sugerencia
# ─────────────────────────────────────────────────────────────────────


def test_override_desconocido_lanza_con_sugerencia():
    with pytest.raises(ValueError, match="lsh_threshold"):
        crear_config_orchestrator(
            perfil="prueba_rapida",
            validate=False,
            lsh_treshold=0.5,  # typo deliberado
        )


def test_override_valido_sigue_funcionando():
    cfg = crear_config_orchestrator(perfil="prueba_rapida", validate=False, lsh_threshold=0.61)
    assert cfg["profiles"]["prueba_rapida"]["lsh_threshold"] == 0.61


def test_alias_trusted_sources_se_mapea():
    """El alias legado trusted_sources= (usado por scripts/stress_test.py)
    se traduce a trusted_unique_sources en vez de ignorarse."""
    cfg = crear_config_orchestrator(
        perfil="prueba_rapida", validate=False, trusted_sources=["RUES"]
    )
    assert cfg["profiles"]["prueba_rapida"]["trusted_unique_sources"] == ["RUES"]


# ─────────────────────────────────────────────────────────────────────
# 3. extra_features: llegan al perfil, se validan, y el scorer las ve
# ─────────────────────────────────────────────────────────────────────


def test_extra_features_string_se_normaliza_e_inyecta():
    cfg = crear_config_orchestrator(
        perfil="prueba_rapida", validate=False, extra_features=["TELEFONO"]
    )
    feats = cfg["profiles"]["prueba_rapida"]["extra_features"]
    assert feats == [{"column": "TELEFONO", "weight": 0.05, "type": "categorical_signed"}]
    assert cfg["column_mapping"] == {}


def test_extra_features_dict_se_respeta_tal_cual():
    spec = {"column": "EMAIL", "weight": 0.10, "type": "exact_or_zero"}
    cfg = crear_config_orchestrator(perfil="prueba_rapida", validate=False, extra_features=[spec])
    assert cfg["profiles"]["prueba_rapida"]["extra_features"] == [spec]


def test_extra_features_tipo_invalido_lanza():
    with pytest.raises(ValueError, match="extra_features\\[0\\]"):
        crear_config_orchestrator(perfil="prueba_rapida", validate=False, extra_features=[42])


def test_extra_features_columna_inexistente_lanza_en_orchestrator(tmp_path):
    df = _fuente_demo()
    config = crear_config_orchestrator(
        perfil="prueba_rapida",
        workspace=str(tmp_path),
        validate=False,
        extra_features=["COLUMNA_QUE_NO_EXISTE"],
    )
    with pytest.raises(ValueError, match="COLUMNA_QUE_NO_EXISTE"):
        Orchestrator(config=config, sources={"A": df}, work_dir=str(tmp_path))


def test_extra_features_llegan_al_scorer():
    """El VectorizedScorer del perfil generado VE las extra_features."""
    from record_linkage.engine.scorer import VectorizedScorer

    cfg = crear_config_orchestrator(
        perfil="prueba_rapida", validate=False, extra_features=["TELEFONO"]
    )
    scorer = VectorizedScorer(profile=cfg["profiles"]["prueba_rapida"])
    assert scorer.extra_features and scorer.extra_features[0]["column"] == "TELEFONO"
