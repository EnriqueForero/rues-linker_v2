"""Contratos del sink DuckDB/Parquet de resultados 0.16.0."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pandas as pd
import pytest

from record_linkage.flujo import (
    ConfigCruce,
    ResultadoCruceDisco,
    ejecutar_cruce,
    resultados_disco as resultados_module,
)
from record_linkage.flujo.resultados_disco import publicar_resultados_duckdb
from record_linkage.ingestion import (
    ColumnType,
    DuckDBIngestionSettings,
    SourceSpec,
    compact_source_to_parquet,
)


def _settings(tmp_path: Path) -> DuckDBIngestionSettings:
    return DuckDBIngestionSettings(
        memory_limit="256MB",
        threads=1,
        temp_directory=tmp_path / "spill",
        max_temp_directory_size="1GB",
    )


def _compact(tmp_path: Path, name: str, rows: list[tuple[str, str, str]]):
    path = tmp_path / f"{name}.csv"
    path.write_text(
        "id,name,phone\n"
        + "".join(f"{identifier},{company},{phone}\n" for identifier, company, phone in rows),
        encoding="utf-8",
    )
    spec = SourceSpec(
        name=name,
        path=path,
        column_mapping={"NIT": "id", "RAZON_SOCIAL": "name", "TELEFONO": "phone"},
        column_types={"NIT": "identifier"},
    )
    return compact_source_to_parquet(
        spec,
        tmp_path / "stage",
        settings=_settings(tmp_path),
        matcher_columns=("NIT", "RAZON_SOCIAL"),
        preserve_payload=True,
    )


def test_disk_sink_expands_payload_and_qa_for_n_sources(tmp_path: Path) -> None:
    compact_a = _compact(
        tmp_path,
        "A",
        [("111111", "ACME", "a1"), ("111111", "ACME", "a2"), ("222222", "BETA", "a3")],
    )
    compact_b = _compact(
        tmp_path,
        "B",
        [("111111", "ACME", "b1"), ("333333", "GAMMA", "b2")],
    )
    correlativa_compacta = pd.DataFrame(
        {
            "NIT": pd.array(["111111", "222222", "111111", "333333"], dtype="string"),
            "RAZON_SOCIAL": pd.array(["ACME", "BETA", "ACME", "GAMMA"], dtype="string"),
            "SRC": pd.array(["A", "A", "B", "B"], dtype="string"),
            "ORIGINAL_INDEX": [0, 1, 2, 3],
            "ID_GRUPO": [0, 1, 0, 2],
        }
    )
    golden = pd.DataFrame(
        {
            "ID_GRUPO": [0, 1, 2],
            "NIT_FINAL": pd.array(["111111", "222222", "333333"], dtype="string"),
        }
    )

    result = publicar_resultados_duckdb(
        golden,
        correlativa_compacta,
        source_order=["A", "B"],
        compactaciones={"A": compact_a, "B": compact_b},
        output_directory=tmp_path / "results",
        settings=_settings(tmp_path),
    )

    assert result.correlativa.rows == 5
    assert result.golden.rows == 3
    assert result.entities == 3
    assert dict(result.source_rows) == {"A": 3, "B": 2}
    correlativa = result.correlativa.to_pandas(max_rows=5)
    assert correlativa["ORIGINAL_INDEX"].tolist() == [0, 1, 2, 3, 4]
    assert correlativa["ID_GRUPO"].tolist() == [0, 0, 1, 0, 2]
    assert correlativa["TELEFONO"].tolist() == ["a1", "a2", "a3", "b1", "b2"]
    assert result.golden.to_pandas(max_rows=3)["INPUT_ROW_COUNT"].tolist() == [3, 1, 1]

    matrix = result.matriz_presencia().set_index(["FUENTE_A", "FUENTE_B"])
    assert matrix.loc[("A", "A"), "ENTIDADES_COMPARTIDAS"] == 2
    assert matrix.loc[("A", "B"), "ENTIDADES_COMPARTIDAS"] == 1
    assert matrix.loc[("B", "A"), "ENTIDADES_COMPARTIDAS"] == 1
    assert matrix.loc[("B", "B"), "ENTIDADES_COMPARTIDAS"] == 2
    assert result.entidades_ausentes_de("A", limit=10)["ID_GRUPO"].tolist() == [2]
    with pytest.raises(ValueError, match="Fuente desconocida"):
        result.entidades_ausentes_de("NO_EXISTE")


def test_disk_table_requires_explicit_large_materialization(tmp_path: Path) -> None:
    compact = _compact(
        tmp_path,
        "A",
        [("111111", "ACME", "a1"), ("222222", "BETA", "a2")],
    )
    correlativa = pd.DataFrame(
        {
            "NIT": ["111111", "222222"],
            "RAZON_SOCIAL": ["ACME", "BETA"],
            "SRC": ["A", "A"],
            "ORIGINAL_INDEX": [0, 1],
            "ID_GRUPO": [0, 1],
        }
    )
    golden = pd.DataFrame({"ID_GRUPO": [0, 1]})
    result = publicar_resultados_duckdb(
        golden,
        correlativa,
        source_order=["A"],
        compactaciones={"A": compact},
        output_directory=tmp_path / "results",
        settings=_settings(tmp_path),
    )

    assert len(result.correlativa.preview(1)) == 1
    with pytest.raises(MemoryError, match="max_rows"):
        result.correlativa.to_pandas(max_rows=1)
    for invalid in (True, 1.5, 0):
        with pytest.raises((TypeError, ValueError)):
            result.correlativa.preview(invalid)


def test_flujo_productivo_propaga_config_y_preserva_payload(tmp_path: Path) -> None:
    source_a = tmp_path / "a.csv"
    source_b = tmp_path / "b.csv"
    source_a.write_text(
        "id;nombre;segmento\n900100001;ACME SAS;industrial\n900100001;ACME SAS;premium\n",
        encoding="utf-8",
    )
    source_b.write_text(
        "id;nombre;segmento\n900100001;ACME S.A.S.;exportador\n900200002;BETA LTDA;servicios\n",
        encoding="utf-8",
    )
    specs = [
        SourceSpec(
            name=name,
            path=path,
            delimiter=";",
            column_mapping={"NIT": "id", "RAZON_SOCIAL": "nombre"},
            passthrough_columns=("segmento",),
            column_types={"NIT": ColumnType.IDENTIFIER},
        )
        for name, path in (("A", source_a), ("B", source_b))
    ]
    config = ConfigCruce(
        fuentes=specs,
        workspace=tmp_path / "salida",
        dir_trabajo=tmp_path / "trabajo",
        perfil="prueba_rapida",
        filas_smoke=0,
        exportar_excel=False,
        motor_ingesta="duckdb",
        modo_resultado="disco",
        preservar_payload=True,
        duckdb_settings=DuckDBIngestionSettings(
            memory_limit="192MB",
            threads=1,
            temp_directory=tmp_path / "spill",
        ),
        limite_filas={"B": 1},
    )

    result = ejecutar_cruce(config)

    assert isinstance(result, ResultadoCruceDisco)
    assert result.correlativa.rows == 3
    assert result.metricas["por_fuente"] == {"A": 2, "B": 1}
    assert set(result.correlativa.preview(10)["segmento"]) == {
        "industrial",
        "premium",
        "exportador",
    }
    assert result.reportes_carga["A"]["payload_columns"] == ["segmento"]
    metadata = json.loads(result.rutas["metadatos"].read_text(encoding="utf-8"))
    parameters = metadata["parametros"]
    assert parameters["modo_resultado"] == "disco"
    assert parameters["preservar_payload"] is True
    assert parameters["limite_filas"] == {"B": 1}
    assert parameters["duckdb_settings"]["memory_limit"] == "192MB"
    assert parameters["duckdb_settings"]["threads"] == 1


def test_modo_disco_falla_rapido_si_contrato_materializa(tmp_path: Path) -> None:
    spec = SourceSpec(
        name="A",
        path=tmp_path / "a.csv",
        column_mapping={"NIT": "id", "RAZON_SOCIAL": "nombre"},
    )
    with pytest.raises(ValueError, match="requiere motor_ingesta='duckdb'"):
        ConfigCruce(fuentes=[spec], workspace=tmp_path, modo_resultado="disco")
    with pytest.raises(ValueError, match="exportar_excel=False"):
        ConfigCruce(
            fuentes=[spec],
            workspace=tmp_path,
            motor_ingesta="duckdb",
            modo_resultado="disco",
        )


def test_publicacion_invalida_no_deja_archivos_parciales(tmp_path: Path) -> None:
    compact = _compact(tmp_path, "A", [("111111", "ACME", "a1")])
    correlativa = pd.DataFrame(
        {
            "NIT": ["111111"],
            "RAZON_SOCIAL": ["ACME"],
            "SRC": ["A"],
            "ORIGINAL_INDEX": [0],
            "ID_GRUPO": [0],
        }
    )
    output = tmp_path / "results"

    with pytest.raises(RuntimeError, match="difieren"):
        publicar_resultados_duckdb(
            pd.DataFrame({"ID_GRUPO": [99]}),
            correlativa,
            source_order=["A"],
            compactaciones={"A": compact},
            output_directory=output,
            settings=_settings(tmp_path),
        )

    assert not (output / "golden.parquet").exists()
    assert not (output / "correlativa.parquet").exists()
    assert not list(output.glob("*.pending"))


@pytest.mark.parametrize("corruption", ["source_row_id", "compact_record_id"])
def test_publicacion_rechaza_mapa_duplicado_o_sin_cobertura(
    tmp_path: Path,
    corruption: str,
) -> None:
    compact = _compact(
        tmp_path,
        "A",
        [("111111", "ACME", "a1"), ("222222", "BETA", "a2")],
    )
    expansion = pd.read_parquet(compact.expansion_map_path)
    expansion.loc[1, corruption] = 0
    expansion.to_parquet(compact.expansion_map_path, index=False)
    correlativa = pd.DataFrame(
        {
            "NIT": ["111111", "222222"],
            "RAZON_SOCIAL": ["ACME", "BETA"],
            "SRC": ["A", "A"],
            "ORIGINAL_INDEX": [0, 1],
            "ID_GRUPO": [0, 1],
        }
    )

    with pytest.raises(RuntimeError, match=corruption):
        publicar_resultados_duckdb(
            pd.DataFrame({"ID_GRUPO": [0, 1]}),
            correlativa,
            source_order=["A"],
            compactaciones={"A": compact},
            output_directory=tmp_path / "results",
            settings=_settings(tmp_path),
        )

    assert not (tmp_path / "results" / "resultados.manifest.json").exists()


def test_publicacion_rechaza_payload_incompleto(tmp_path: Path) -> None:
    compact = _compact(
        tmp_path,
        "A",
        [("111111", "ACME", "a1"), ("222222", "BETA", "a2")],
    )
    assert compact.payload_path is not None
    payload = pd.read_parquet(compact.payload_path).iloc[:1]
    payload.to_parquet(compact.payload_path, index=False)
    correlativa = pd.DataFrame(
        {
            "NIT": ["111111", "222222"],
            "RAZON_SOCIAL": ["ACME", "BETA"],
            "SRC": ["A", "A"],
            "ORIGINAL_INDEX": [0, 1],
            "ID_GRUPO": [0, 1],
        }
    )

    with pytest.raises(RuntimeError, match=r"payload\.source_row_id"):
        publicar_resultados_duckdb(
            pd.DataFrame({"ID_GRUPO": [0, 1]}),
            correlativa,
            source_order=["A"],
            compactaciones={"A": compact},
            output_directory=tmp_path / "results",
            settings=_settings(tmp_path),
        )


def test_publicacion_rechaza_golden_con_entidad_duplicada(tmp_path: Path) -> None:
    compact = _compact(tmp_path, "A", [("111111", "ACME", "a1")])
    correlativa = pd.DataFrame(
        {
            "NIT": ["111111"],
            "RAZON_SOCIAL": ["ACME"],
            "SRC": ["A"],
            "ORIGINAL_INDEX": [0],
            "ID_GRUPO": [0],
        }
    )
    golden = pd.DataFrame({"ID_GRUPO": [0, 0], "LABEL": ["ACME", "DUPLICADA"]})

    with pytest.raises(RuntimeError, match="exactamente una fila no nula por entidad"):
        publicar_resultados_duckdb(
            golden,
            correlativa,
            source_order=["A"],
            compactaciones={"A": compact},
            output_directory=tmp_path / "results",
            settings=_settings(tmp_path),
        )

    assert not (tmp_path / "results" / "resultados.manifest.json").exists()


def _publish_marked_result(
    tmp_path: Path,
    compact,
    mark: str,
    *,
    overwrite: bool = True,
):
    correlativa = pd.DataFrame(
        {
            "NIT": ["111111"],
            "RAZON_SOCIAL": ["ACME"],
            "SRC": ["A"],
            "ORIGINAL_INDEX": [0],
            "ID_GRUPO": [0],
            "RUN": [mark],
        }
    )
    golden = pd.DataFrame({"ID_GRUPO": [0], "LABEL": [mark]})
    return publicar_resultados_duckdb(
        golden,
        correlativa,
        source_order=["A"],
        compactaciones={"A": compact},
        output_directory=tmp_path / "results",
        settings=_settings(tmp_path),
        overwrite=overwrite,
    )


@pytest.mark.parametrize("failure_boundary", ["generation", "manifest"])
def test_result_manifest_keeps_previous_generation_on_commit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_boundary: str,
) -> None:
    compact = _compact(tmp_path, "A", [("111111", "ACME", "a1")])
    previous = _publish_marked_result(tmp_path, compact, "OLD")
    output = tmp_path / "results"
    manifest_path = output / "resultados.manifest.json"
    previous_manifest = manifest_path.read_bytes()
    previous_golden = previous.golden.to_pandas(max_rows=1)
    previous_correlativa = previous.correlativa.to_pandas(max_rows=1)
    real_replace = os.replace

    def fail_at_boundary(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        target_path = Path(target)
        is_generation = target_path.parent.name == "resultados.generations"
        is_manifest = target_path == manifest_path
        if (failure_boundary == "generation" and is_generation) or (
            failure_boundary == "manifest" and is_manifest
        ):
            raise OSError(f"fallo inyectado en {failure_boundary}")
        real_replace(source, target)

    monkeypatch.setattr(resultados_module.os, "replace", fail_at_boundary)
    with pytest.raises(OSError, match="fallo inyectado"):
        _publish_marked_result(tmp_path, compact, "NEW")

    assert manifest_path.read_bytes() == previous_manifest
    pd.testing.assert_frame_equal(previous.golden.to_pandas(max_rows=1), previous_golden)
    pd.testing.assert_frame_equal(previous.correlativa.to_pandas(max_rows=1), previous_correlativa)
    assert not list(output.glob("*.generation.pending"))


def test_result_manifest_after_pointer_failure_is_new_and_both_generations_survive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compact = _compact(tmp_path, "A", [("111111", "ACME", "a1")])
    previous = _publish_marked_result(tmp_path, compact, "OLD")
    output = tmp_path / "results"
    manifest_path = output / "resultados.manifest.json"
    real_commit = resultados_module._write_manifest_atomic

    def commit_then_fail(*args, **kwargs) -> None:
        real_commit(*args, **kwargs)
        raise OSError("fallo después del puntero")

    monkeypatch.setattr(resultados_module, "_write_manifest_atomic", commit_then_fail)
    with pytest.raises(OSError, match="después del puntero"):
        _publish_marked_result(tmp_path, compact, "NEW")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current_golden = pd.read_parquet(output / manifest["artifacts"]["golden"])
    current_correlativa = pd.read_parquet(output / manifest["artifacts"]["correlativa"])
    assert current_golden["LABEL"].tolist() == ["NEW"]
    assert current_correlativa["RUN"].tolist() == ["NEW"]
    assert previous.golden.to_pandas(max_rows=1)["LABEL"].tolist() == ["OLD"]
    assert previous.correlativa.to_pandas(max_rows=1)["RUN"].tolist() == ["OLD"]


def test_result_manifest_is_authoritative_and_no_overwrite_is_explicit(tmp_path: Path) -> None:
    compact = _compact(tmp_path, "A", [("111111", "ACME", "a1")])
    result = _publish_marked_result(tmp_path, compact, "OLD")
    output = tmp_path / "results"
    manifest = json.loads((output / "resultados.manifest.json").read_text(encoding="utf-8"))

    assert output / manifest["artifacts"]["golden"] == result.golden.path
    assert output / manifest["artifacts"]["correlativa"] == result.correlativa.path
    with pytest.raises(FileExistsError, match="overwrite=True"):
        _publish_marked_result(tmp_path, compact, "NEW", overwrite=False)


def test_result_gc_retiene_current_mas_una_previa(tmp_path: Path) -> None:
    compact = _compact(tmp_path, "A", [("111111", "ACME", "a1")])

    latest = None
    for mark in ("ONE", "TWO", "THREE"):
        latest = _publish_marked_result(tmp_path, compact, mark)

    assert latest is not None
    generations_dir = tmp_path / "results" / "resultados.generations"
    generations = [path for path in generations_dir.iterdir() if path.is_dir()]
    assert len(generations) == 2
    assert latest.generation_dir in generations


def test_metadata_generacional_actualiza_manifest_atomico(tmp_path: Path) -> None:
    compact = _compact(tmp_path, "A", [("111111", "ACME", "a1")])
    publication = _publish_marked_result(tmp_path, compact, "CURRENT")

    metadata_path = publication.publicar_metadatos({"run": "CURRENT", "rows": 1})
    manifest = json.loads(publication.manifest_path.read_text(encoding="utf-8"))

    assert metadata_path == publication.generation_dir / "metadatos_corrida.json"
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["run"] == "CURRENT"
    assert publication.manifest_path.parent / manifest["artifacts"]["metadata"] == metadata_path
    assert manifest["generation"] == publication.generation


def test_metadata_de_generacion_anterior_no_rebobina_manifest(tmp_path: Path) -> None:
    compact = _compact(tmp_path, "A", [("111111", "ACME", "a1")])
    previous = _publish_marked_result(tmp_path, compact, "OLD")
    current = _publish_marked_result(tmp_path, compact, "NEW")

    with pytest.raises(RuntimeError, match="ya no es la publicación actual"):
        previous.publicar_metadatos({"run": "OLD-LATE"})

    manifest = json.loads(current.manifest_path.read_text(encoding="utf-8"))
    assert manifest["generation"] == current.generation
    assert "metadata" not in manifest["artifacts"]


def test_interleaving_metadata_y_nueva_generacion_nunca_rebobina(tmp_path: Path) -> None:
    compact = _compact(tmp_path, "A", [("111111", "ACME", "a1")])
    previous = _publish_marked_result(tmp_path, compact, "OLD")
    start = Barrier(2)

    def attach_metadata():
        start.wait(timeout=10)
        try:
            return previous.publicar_metadatos({"run": "OLD"})
        except RuntimeError:
            return None

    def publish_new():
        start.wait(timeout=10)
        return _publish_marked_result(tmp_path, compact, "NEW")

    with ThreadPoolExecutor(max_workers=2) as pool:
        metadata_future = pool.submit(attach_metadata)
        publication_future = pool.submit(publish_new)
        metadata_future.result(timeout=30)
        current = publication_future.result(timeout=30)

    manifest = json.loads(current.manifest_path.read_text(encoding="utf-8"))
    assert manifest["generation"] == current.generation
    current_golden = pd.read_parquet(current.manifest_path.parent / manifest["artifacts"]["golden"])
    assert current_golden["LABEL"].tolist() == ["NEW"]
