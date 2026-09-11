"""Tests del opt-in de dtypes categóricos (v0.13.0, N6 — T1 de dian-comercio)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from record_linkage.config.profiles import PERFILES_BASE, crear_config_orchestrator
from record_linkage.processing.dtypes import optimizar_dtypes_categoricos


def test_convierte_baja_cardinalidad_y_respeta_alta():
    n = 1_000
    rng = np.random.default_rng(0)
    df = pd.DataFrame(
        {
            "SRC": rng.choice(["RUES", "CRM", "DIAN"], n),
            "CIUDAD": rng.choice(["BOGOTA", "CALI", "MEDELLIN"], n),
            "NIT_OK": [str(800000000 + i) for i in range(n)],  # alta cardinalidad
        }
    )
    optimizar_dtypes_categoricos(df, ["SRC", "CIUDAD", "NIT_OK", "NO_EXISTE"])
    assert isinstance(df["SRC"].dtype, pd.CategoricalDtype)
    assert isinstance(df["CIUDAD"].dtype, pd.CategoricalDtype)
    assert not isinstance(df["NIT_OK"].dtype, pd.CategoricalDtype), (
        "alta cardinalidad no debe castearse (no ahorra)"
    )


def test_ahorro_de_memoria_real():
    n = 50_000
    df = pd.DataFrame({"SRC": np.random.default_rng(1).choice(["A", "B", "C", "D"], n)})
    antes = int(df.memory_usage(deep=True).sum())
    optimizar_dtypes_categoricos(df, ["SRC"])
    despues = int(df.memory_usage(deep=True).sum())
    assert despues < antes * 0.25, f"ahorro insuficiente: {antes:,} → {despues:,} bytes"


def test_idempotente_y_valores_intactos():
    df = pd.DataFrame({"SRC": ["A", "B", "A"]})
    valores = df["SRC"].tolist()
    optimizar_dtypes_categoricos(df, ["SRC"])
    optimizar_dtypes_categoricos(df, ["SRC"])  # segunda pasada: no-op
    assert df["SRC"].astype(str).tolist() == valores


def test_clave_de_perfil_existe_y_es_opt_in():
    """La clave viaja en todos los perfiles base (requisito para override) y
    su default es False: cero cambio de comportamiento sin activarla."""
    for nombre, perfil in PERFILES_BASE.items():
        assert perfil.get("use_categorical_dtypes") is False, nombre
    cfg = crear_config_orchestrator(
        perfil="prueba_rapida", validate=False, use_categorical_dtypes=True
    )
    assert cfg["profiles"]["prueba_rapida"]["use_categorical_dtypes"] is True
