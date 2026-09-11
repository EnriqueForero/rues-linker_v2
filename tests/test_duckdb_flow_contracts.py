"""Bordes contractuales de la integración DuckDB en el flujo raíz."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from record_linkage import SourceSpec
from record_linkage.flujo import ConfigCruce, ejecutar_cruce
from record_linkage.flujo.cruce import _normalizar_reporte_carga
from record_linkage.flujo.insumos import ruta_en_cache


def _missing_spec(tmp_path: Path) -> SourceSpec:
    return SourceSpec(
        name="AUSENTE",
        path=tmp_path / "no_existe.csv",
        column_mapping={"NIT": "id", "RAZON_SOCIAL": "name"},
        column_types={"NIT": "identifier"},
    )


@pytest.mark.parametrize("invalid_limit", [True, 2.5, "10"])
def test_config_rejects_non_integer_row_limits(
    tmp_path: Path,
    invalid_limit: object,
) -> None:
    with pytest.raises(TypeError, match="entero"):
        ConfigCruce(
            fuentes=[_missing_spec(tmp_path)],
            workspace=tmp_path / "out",
            limite_filas={"AUSENTE": invalid_limit},  # type: ignore[dict-item]
        )


def test_duckdb_preflight_does_not_claim_historical_cache_support(tmp_path: Path) -> None:
    spec = _missing_spec(tmp_path)
    cache_dir = tmp_path / "cache"
    cache_path = ruta_en_cache(spec, cache_dir)
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(b"cache-placeholder")
    cache_path.with_suffix(".json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="dir_procesados"):
        ConfigCruce(
            fuentes=[spec],
            workspace=tmp_path / "out",
            dir_trabajo=tmp_path / "work",
            dir_procesados=cache_dir,
            motor_ingesta="duckdb",
            filas_smoke=0,
            exportar_excel=False,
        )

    config = ConfigCruce(
        fuentes=[spec],
        workspace=tmp_path / "out",
        dir_trabajo=tmp_path / "work",
        motor_ingesta="duckdb",
        filas_smoke=0,
        exportar_excel=False,
    )
    with pytest.raises(FileNotFoundError, match=r"necesita el archivo.*original"):
        ejecutar_cruce(config)


def test_load_report_mappings_remain_json_objects() -> None:
    normalized = _normalizar_reporte_carga(
        {
            "resolved_mapping": MappingProxyType({"NIT": "id"}),
            "invalid_values": MappingProxyType({"NIT": 2}),
        }
    )

    assert normalized == {
        "resolved_mapping": {"NIT": "id"},
        "invalid_values": {"NIT": 2},
    }
