"""Bordes contractuales de la integración DuckDB en el flujo raíz."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from record_linkage import ColumnType, SourceSpec
from record_linkage.flujo import ConfigCruce, ResultadoCruceDisco, ejecutar_cruce
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


def _fuentes_sinteticas(tmp_path: Path) -> list[SourceSpec]:
    """Dos fuentes inventadas: ACME con NIT de 9 y de 10 dígitos (misma base)."""
    a = tmp_path / "padron.csv"
    a.write_text(
        "IDENT,NOMBRE_EMPRESA\n"
        "900111222,ACME COLOMBIA SAS\n"
        "800333444,BETA LTDA\n"
        "900555666,GAMA S.A.\n",
        encoding="utf-8",
    )
    b = tmp_path / "clientes.csv"
    b.write_text(
        "nit_cliente,razon\n9001112221,ACME COLOMBIA S.A.S.\n700999888,DELTA EU\n",
        encoding="utf-8",
    )
    return [
        SourceSpec(
            name="PADRON",
            path=a,
            column_mapping={"NIT": "IDENT", "RAZON_SOCIAL": "NOMBRE_EMPRESA"},
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
        SourceSpec(
            name="CLIENTES",
            path=b,
            column_mapping={"NIT": "nit_cliente", "RAZON_SOCIAL": "razon"},
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
    ]


def test_modo_disco_responde_el_qa_sin_columnas_tecnicas(tmp_path: Path) -> None:
    """F1.9: la correlativa publicada no trae NIT_BASE/NIT_VALID y el QA en DuckDB
    responde lo mismo que el camino en memoria."""
    config = ConfigCruce(
        fuentes=_fuentes_sinteticas(tmp_path),
        workspace=tmp_path / "salida",
        dir_trabajo=tmp_path / "trabajo",
        confiables={"PADRON"},
        filas_smoke=0,
        exportar_excel=False,
        motor_ingesta="duckdb",
        modo_resultado="disco",
    )
    resultado = ejecutar_cruce(config)

    assert isinstance(resultado, ResultadoCruceDisco)
    assert not {"NIT_BASE", "NIT_VALID"} & set(resultado.correlativa.columns)
    assert {"ID_REGISTRO", "ID_ENTIDAD", "METODO_UNION"} <= set(resultado.correlativa.columns)
    assert resultado.correlativa.rows == 5
    assert resultado.conflictos_identificador() == 0
    distribucion = resultado.distribucion_grupos()
    assert distribucion["grupos_1_fila"] == 3 and distribucion["grupos_2a5_filas"] == 1
    assert distribucion["max_filas_por_grupo"] == 2
    por_fuente = resultado.identificadores_por_fuente()
    assert set(por_fuente) == {"PADRON", "CLIENTES"}
    assert por_fuente["PADRON"].filas == 3 and por_fuente["CLIENTES"].filas == 2
    assert resultado.entidades_multifuente() == 1
    assert len(resultado.grupos_sospechosos()) == 0
