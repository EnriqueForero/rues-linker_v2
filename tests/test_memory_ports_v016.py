"""Regresiones focales de los puertos de memoria incorporados en 0.16."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage import SourceSpec
from record_linkage.api import _expand_exact_correlative
from record_linkage.config.profiles import (
    PERFILES_BASE,
    config_produccion_it7,
    crear_config_orchestrator,
)
from record_linkage.flujo import insumos


def test_expansion_no_convierte_src_a_object_ni_muta_la_correlativa(monkeypatch) -> None:
    pytest.importorskip("pyarrow")
    compacta = pd.DataFrame(
        {
            "SRC": pd.array(["A", "B", "A"], dtype="string[pyarrow]"),
            "ORIGINAL_INDEX": [1, 0, 0],
            "ID_GRUPO": [20, 30, 10],
        },
        index=[7, 8, 9],
    )
    original = compacta.copy(deep=True)
    plan = {
        "codes": {"A": np.array([0, 1, 0]), "B": np.array([0, 0])},
        "stats": {
            "A": {"processed_rows": 2},
            "B": {"processed_rows": 1},
        },
    }

    astype_original = pd.Series.astype

    def rechazar_astype_str(self, dtype, *args, **kwargs):
        if dtype is str:
            raise AssertionError("SRC no debe materializarse mediante astype(str)")
        return astype_original(self, dtype, *args, **kwargs)

    monkeypatch.setattr(pd.Series, "astype", rechazar_astype_str)
    expandida = _expand_exact_correlative(compacta, ["A", "B"], plan)

    assert isinstance(expandida.index, pd.RangeIndex)
    assert expandida["ORIGINAL_INDEX"].tolist() == [0, 1, 2, 3, 4]
    assert expandida["ID_GRUPO"].tolist() == [10, 20, 10, 30, 30]
    assert isinstance(expandida["SRC"].dtype, pd.StringDtype)
    assert expandida["SRC"].dtype.storage == "pyarrow"
    pd.testing.assert_frame_equal(compacta, original)


def test_leer_cache_restituye_string_pyarrow_en_columnas_object(tmp_path, monkeypatch) -> None:
    pytest.importorskip("pyarrow")
    origen = tmp_path / "fuente.csv"
    origen.write_text("NIT,RAZON_SOCIAL\n1,ACME\n", encoding="utf-8")
    spec = SourceSpec(
        name="FUENTE",
        path=origen,
        column_mapping={"NIT": "NIT", "RAZON_SOCIAL": "RAZON_SOCIAL"},
    )
    ruta_cache = insumos.ruta_en_cache(spec, tmp_path / "cache")
    ruta_cache.parent.mkdir()
    ruta_cache.touch()
    ruta_cache.with_suffix(".json").write_text(json.dumps({}), encoding="utf-8")

    leida = pd.DataFrame(
        {
            "NIT": pd.Series(["1", None], dtype=object),
            "RAZON_SOCIAL": pd.Series(["ACME", "BETA"], dtype=object),
            "FILA": pd.Series([1, 2], dtype="int64"),
        }
    )
    monkeypatch.setattr(insumos, "_estado_del_archivo", lambda _spec: None)
    monkeypatch.setattr(pd, "read_parquet", lambda _ruta: leida)

    encontrada = insumos.leer_cache(spec, ruta_cache.parent)

    assert encontrada is not None
    datos, _reporte = encontrada
    assert datos is leida
    for columna in ("NIT", "RAZON_SOCIAL"):
        assert isinstance(datos[columna].dtype, pd.StringDtype)
        assert datos[columna].dtype.storage == "pyarrow"
    assert str(datos["FILA"].dtype) == "int64"


def test_liberacion_de_fuentes_es_opt_in_en_todos_los_perfiles() -> None:
    assert PERFILES_BASE
    for nombre, perfil in PERFILES_BASE.items():
        assert perfil.get("liberar_fuentes_tras_l1") is False, nombre
    assert config_produccion_it7["liberar_fuentes_tras_l1"] is False

    config = crear_config_orchestrator(
        perfil="prueba_rapida",
        validate=False,
        liberar_fuentes_tras_l1=True,
    )
    assert config["profiles"]["prueba_rapida"]["liberar_fuentes_tras_l1"] is True
