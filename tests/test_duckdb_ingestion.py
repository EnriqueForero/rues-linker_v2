"""Paridad de la ingestión/compactación DuckDB con el camino pandas."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import numpy as np
import pandas as pd
import pytest

from record_linkage.api import _collapse_exact_sources
from record_linkage.ingestion import (
    ColumnType,
    DuckDBIngestionSettings,
    IngestionError,
    InvalidValuePolicy,
    SchemaError,
    SourceSpec,
    compact_source_to_parquet,
    duckdb as duckdb_module,
    load_source,
    materialize_text_source_utf8,
)

pytest.importorskip("duckdb")


def _settings(tmp_path: Path) -> DuckDBIngestionSettings:
    return DuckDBIngestionSettings(
        memory_limit="256MB",
        threads=2,
        temp_directory=tmp_path / "duckdb-spill",
        max_temp_directory_size="1GB",
    )


def test_settings_valida_retencion_generacional() -> None:
    assert DuckDBIngestionSettings().previous_generations_to_keep == 1
    for invalid in (True, 1.5, "1"):
        with pytest.raises(TypeError, match="previous_generations_to_keep"):
            DuckDBIngestionSettings(previous_generations_to_keep=invalid)  # type: ignore[arg-type]
    for invalid in (-1, 101):
        with pytest.raises(ValueError, match="previous_generations_to_keep"):
            DuckDBIngestionSettings(previous_generations_to_keep=invalid)


@pytest.mark.skipif(os.name != "nt", reason="regresión específica de _commit en Windows")
def test_fsync_file_usa_descriptor_escribible_en_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "durable.parquet"
    path.write_bytes(b"parquet")
    real_fsync = os.fsync
    completed: list[int] = []

    def observed_fsync(descriptor: int) -> None:
        real_fsync(descriptor)
        completed.append(descriptor)

    monkeypatch.setattr(duckdb_module.os, "fsync", observed_fsync)
    duckdb_module._fsync_file(path)

    assert completed, "rb falla con EBADF en Windows; rb+ debe completar _commit"


def test_fsync_file_no_silencia_error_de_durabilidad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "durable.parquet"
    path.write_bytes(b"parquet")

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("fsync inyectado")

    monkeypatch.setattr(duckdb_module.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="fsync inyectado"):
        duckdb_module._fsync_file(path)


@pytest.mark.skipif(os.name != "nt", reason="regresión específica de locks Windows")
def test_manifest_lock_se_libera_por_el_so_si_muere_el_proceso(tmp_path: Path) -> None:
    manifest = tmp_path / "crash.manifest.json"
    script = (
        "import os,sys; from pathlib import Path; "
        "sys.path.insert(0, str(Path.cwd() / 'src')); "
        "from record_linkage.ingestion.duckdb import _ManifestFileLock; "
        "lock=_ManifestFileLock(Path(sys.argv[1])); lock.acquire(); os._exit(0)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(manifest)],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0

    duckdb_module._write_manifest_atomic(
        manifest,
        {"generation": "after-crash"},
        token="after-crash",
        overwrite=False,
    )
    assert json.loads(manifest.read_text(encoding="utf-8"))["generation"] == "after-crash"


def _cp1252_zip_spec(tmp_path: Path) -> SourceSpec:
    contents = (
        "Identificación\tRazón Social\tExtra\n"
        "001234\t Compañía SAS \tx\n"
        "001234\tCompañía SAS\tx\n"
        "NULL\t NO DEFINIDO \ty\n"
        "\t\ty\n"
        "000000\t Cero \tz\n"
        "123456.0\tDecimal\tz\n"
        "123456\tDecimal\tz\n"
        "-1\t Faltante \tw\n"
        "890123\tCafé Norte\tq\n"
    )
    path = tmp_path / "fuente_cp1252.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("datos/exportaciones.txt", contents.encode("cp1252"))
    return SourceSpec(
        name="exportaciones",
        path=path,
        column_mapping={"NIT": "identificacion", "NOMBRE": "razon social"},
        passthrough_columns=("Extra",),
        column_types={"NIT": ColumnType.IDENTIFIER},
        null_values={"NIT": ("-1",), "NOMBRE": ("NO DEFINIDO",)},
    )


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"chunksize": 2}, "SourceSpec.chunksize"),
        ({"temp_dir": "legacy-temp"}, "SourceSpec.temp_dir"),
        ({"text_engine": "pandas"}, "SourceSpec.text_engine"),
        ({"text_block_size_bytes": 64 * 1024}, "SourceSpec.text_block_size_bytes"),
    ],
)
def test_duckdb_rechaza_parametros_mudos_del_lector_pandas(
    tmp_path: Path, extra: dict[str, object], expected: str
) -> None:
    path = tmp_path / "source.csv"
    path.write_text("id;name\n900100001;ACME\n", encoding="utf-8")
    spec = SourceSpec(
        name="source",
        path=path,
        delimiter=";",
        column_mapping={"NIT": "id", "NOMBRE": "name"},
        **extra,
    )

    with pytest.raises(IngestionError, match=expected):
        compact_source_to_parquet(spec, tmp_path / "out", settings=_settings(tmp_path))


def test_materialize_cp1252_zip_to_persistent_utf8_by_blocks(tmp_path: Path) -> None:
    spec = _cp1252_zip_spec(tmp_path)
    destination = tmp_path / "materialized"

    materialized = materialize_text_source_utf8(
        spec,
        destination,
        chunk_bytes=64 * 1024,
    )

    assert materialized.owns_path is True
    assert materialized.transcoded is True
    assert materialized.original_encoding == "cp1252"
    assert materialized.archive_member == "datos/exportaciones.txt"
    assert materialized.delimiter == "\t"
    assert materialized.utf8_path.parent == destination
    assert "Compañía" in materialized.utf8_path.read_text(encoding="utf-8")
    assert materialized.utf8_bytes == materialized.utf8_path.stat().st_size


def test_materialize_direct_utf8_reuses_original_file(tmp_path: Path) -> None:
    path = tmp_path / "direct.csv"
    path.write_text("id,name\n001234,ACME\n", encoding="utf-8")
    spec = SourceSpec(
        name="direct",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name"},
    )

    materialized = materialize_text_source_utf8(spec, tmp_path / "stage")

    assert materialized.utf8_path == path
    assert materialized.owns_path is False
    assert materialized.transcoded is False
    assert materialized.utf8_bytes == path.stat().st_size


def test_duckdb_compaction_matches_load_source_and_exact_collapse(
    tmp_path: Path,
) -> None:
    spec = _cp1252_zip_spec(tmp_path)
    loaded = load_source(spec)
    expected_sources, expected_plan = _collapse_exact_sources({spec.name: loaded.data})

    result = compact_source_to_parquet(
        spec,
        tmp_path / "out",
        settings=_settings(tmp_path),
    )

    actual_compact = pd.read_parquet(result.compact_path)
    actual_map = pd.read_parquet(result.expansion_map_path)
    expected_compact = expected_sources[spec.name]
    pd.testing.assert_frame_equal(
        actual_compact.astype("string"),
        expected_compact.astype("string"),
        check_dtype=False,
    )
    expected_codes = expected_plan["codes"][spec.name]
    np.testing.assert_array_equal(
        actual_map["compact_record_id"].to_numpy(),
        expected_codes,
    )
    first_by_code = {}
    for row_id, code in enumerate(expected_codes):
        first_by_code.setdefault(int(code), row_id)
    expected_representatives = np.asarray(
        [first_by_code[int(code)] for code in expected_codes],
        dtype=np.int64,
    )
    np.testing.assert_array_equal(
        actual_map["representative_source_row_id"].to_numpy(),
        expected_representatives,
    )
    np.testing.assert_array_equal(
        actual_map["source_row_id"].to_numpy(),
        np.arange(len(loaded.data), dtype=np.int64),
    )
    assert actual_map["source_name"].tolist() == [spec.name] * len(loaded.data)
    assert result.columns == tuple(loaded.data.columns)
    assert result.input_rows == len(loaded.data)
    assert result.compact_rows == len(expected_compact)
    assert result.collapsed_rows == len(loaded.data) - len(expected_compact)
    assert dict(result.invalid_values) == dict(loaded.report.invalid_values)
    assert result.transcoded_to_utf8 is True


def test_compaction_preserves_declared_column_and_first_occurrence_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "order.csv"
    path.write_text(
        "extra,name,id,ignored\nz, BETA ,222222,no\na, ACME ,111111,no\nz,BETA,222222,no\n",
        encoding="utf-8",
    )
    spec = SourceSpec(
        name="order",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name"},
        optional_column_mapping={"CIUDAD": "city"},
        passthrough_columns=("extra",),
        column_types={"NIT": "identifier"},
    )

    result = compact_source_to_parquet(
        spec,
        tmp_path / "out",
        settings=_settings(tmp_path),
    )
    compact = pd.read_parquet(result.compact_path)
    expansion = pd.read_parquet(result.expansion_map_path)

    assert compact.columns.tolist() == ["NIT", "NOMBRE", "extra"]
    assert compact.astype("string").values.tolist() == [
        ["222222", "BETA", "z"],
        ["111111", "ACME", "a"],
    ]
    assert expansion["compact_record_id"].tolist() == [0, 1, 0]
    assert expansion["representative_source_row_id"].tolist() == [0, 1, 0]
    assert any("CIUDAD" in warning for warning in result.warnings)


def test_row_limit_is_applied_before_normalization_and_collapse(tmp_path: Path) -> None:
    path = tmp_path / "limited.csv"
    path.write_text(
        "id,name\n111111,ACME\n111111,ACME\n222222,BETA\n333333,GAMA\n",
        encoding="utf-8",
    )
    spec = SourceSpec(
        name="limited",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name"},
        column_types={"NIT": "identifier"},
    )

    result = compact_source_to_parquet(
        spec,
        tmp_path / "out",
        settings=_settings(tmp_path),
        row_limit=3,
    )

    assert result.input_rows == 3
    assert result.compact_rows == 2
    assert pd.read_parquet(result.expansion_map_path)["compact_record_id"].tolist() == [
        0,
        0,
        1,
    ]

    with pytest.raises(ValueError, match=">= 1"):
        compact_source_to_parquet(
            spec,
            tmp_path / "bad-limit",
            settings=_settings(tmp_path),
            row_limit=0,
        )
    for invalid_limit in (True, 1.5):
        with pytest.raises(TypeError, match="entero"):
            compact_source_to_parquet(
                spec,
                tmp_path / f"bad-limit-{invalid_limit}",
                settings=_settings(tmp_path),
                row_limit=invalid_limit,
            )


def test_matcher_projection_collapses_without_losing_payload(tmp_path: Path) -> None:
    path = tmp_path / "payload.csv"
    path.write_text(
        "id,name,phone,department\n"
        "111111,ACME,3001,BOGOTA\n"
        "111111,ACME,3002,MEDELLIN\n"
        "222222,BETA,3003,CALI\n",
        encoding="utf-8",
    )
    spec = SourceSpec(
        name="payload",
        path=path,
        column_mapping={
            "NIT": "id",
            "NOMBRE": "name",
            "TELEFONO": "phone",
            "DEPARTAMENTO": "department",
        },
        column_types={"NIT": "identifier"},
    )

    result = compact_source_to_parquet(
        spec,
        tmp_path / "out",
        settings=_settings(tmp_path),
        matcher_columns=("NIT", "NOMBRE"),
        preserve_payload=True,
    )

    assert result.columns == ("NIT", "NOMBRE")
    assert result.payload_columns == ("TELEFONO", "DEPARTAMENTO")
    assert result.payload_path is not None
    assert result.input_rows == 3
    assert result.compact_rows == 2
    assert pd.read_parquet(result.compact_path).astype("string").values.tolist() == [
        ["111111", "ACME"],
        ["222222", "BETA"],
    ]
    assert pd.read_parquet(result.expansion_map_path)["compact_record_id"].tolist() == [
        0,
        0,
        1,
    ]
    payload = pd.read_parquet(result.payload_path)
    assert payload.columns.tolist() == ["source_row_id", "TELEFONO", "DEPARTAMENTO"]
    assert payload["source_row_id"].tolist() == [0, 1, 2]
    assert payload["TELEFONO"].tolist() == ["3001", "3002", "3003"]


def test_matcher_projection_rejects_unknown_required_column(tmp_path: Path) -> None:
    path = tmp_path / "unknown.csv"
    path.write_text("id,name\n111111,ACME\n", encoding="utf-8")
    spec = SourceSpec(
        name="unknown",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name"},
    )

    with pytest.raises(SchemaError, match="no producidas"):
        compact_source_to_parquet(
            spec,
            tmp_path / "out",
            settings=_settings(tmp_path),
            matcher_columns=("NIT", "NO_EXISTE"),
            preserve_payload=True,
        )


def test_invalid_identifier_raise_matches_public_ingestion_policy(tmp_path: Path) -> None:
    path = tmp_path / "invalid.csv"
    path.write_text("id,name\n000000,ACME\n", encoding="utf-8")
    spec = SourceSpec(
        name="invalid",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name"},
        column_types={"NIT": "identifier"},
        invalid_values=InvalidValuePolicy.RAISE,
    )

    with pytest.raises(SchemaError, match="tipo declarado"):
        compact_source_to_parquet(
            spec,
            tmp_path / "out",
            settings=_settings(tmp_path),
        )


def test_phase_one_rejects_numeric_contract_instead_of_silent_drift(tmp_path: Path) -> None:
    path = tmp_path / "number.csv"
    path.write_text('id,value\n001234,"1.234,56"\n', encoding="utf-8")
    spec = SourceSpec(
        name="numeric",
        path=path,
        column_mapping={"NIT": "id", "VALOR": "value"},
        column_types={"NIT": "identifier", "VALOR": "number"},
    )

    with pytest.raises(IngestionError, match="NUMBER"):
        compact_source_to_parquet(
            spec,
            tmp_path / "out",
            settings=_settings(tmp_path),
        )


@pytest.mark.parametrize(
    "reserved",
    [
        "SRC",
        "original_index",
        "ID_GRUPO",
        "source_name",
        "SOURCE_ROW_ID",
        "compact_record_id",
        "representative_source_row_id",
        "__invalid_7",
    ],
)
def test_duckdb_rejects_publication_reserved_columns_before_materializing(
    tmp_path: Path,
    reserved: str,
) -> None:
    path = tmp_path / "reserved.csv"
    path.write_text("id,name,payload\n111111,ACME,user-value\n", encoding="utf-8")
    spec = SourceSpec(
        name="reserved",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name", reserved: "payload"},
    )

    with pytest.raises(SchemaError, match=r"reservada.*Renómbrela"):
        compact_source_to_parquet(spec, tmp_path / "out", settings=_settings(tmp_path))

    assert not (tmp_path / "out").exists()


def test_duckdb_rejects_non_ascii_null_sentinel_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "unicode-sentinel.csv"
    path.write_text("id,name\n111111,Straße\n", encoding="utf-8")
    spec = SourceSpec(
        name="unicode-sentinel",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name"},
        global_null_values=("STRASSE", "SIN INFORMACIÓN"),
    )

    with pytest.raises(IngestionError, match="sentinels nulos ASCII"):
        compact_source_to_parquet(spec, tmp_path / "out", settings=_settings(tmp_path))


def test_duckdb_canonicalizes_embedded_newlines_like_public_ingestion(tmp_path: Path) -> None:
    path = tmp_path / "newlines.csv"
    path.write_bytes(b'id,name\r\n111111,"ACME\r\nSAS"\r\n111111,"ACME\nSAS"\r\n')
    spec = SourceSpec(
        name="newlines",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name"},
        column_types={"NIT": "identifier"},
    )

    loaded = load_source(spec)
    result = compact_source_to_parquet(
        spec,
        tmp_path / "out",
        settings=_settings(tmp_path),
    )
    compact = pd.read_parquet(result.compact_path)

    assert loaded.data["NOMBRE"].nunique() == 1
    assert result.compact_rows == 1
    assert compact["NOMBRE"].tolist() == ["ACME\nSAS"]


def test_duckdb_rechaza_fuente_sin_filas_con_mensaje_accionable(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("id,name\n", encoding="utf-8")
    spec = SourceSpec(
        name="empty",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name"},
    )

    with pytest.raises(ValueError, match="no contiene filas de datos"):
        compact_source_to_parquet(spec, tmp_path / "out", settings=_settings(tmp_path))


def test_output_prefix_es_estable_y_no_colisiona_tras_sanitizar() -> None:
    slash = duckdb_module._output_prefix("A/B", None)
    space = duckdb_module._output_prefix("A B", None)
    upper = duckdb_module._output_prefix("FUENTE", None)
    lower = duckdb_module._output_prefix("fuente", None)

    assert slash != space
    assert upper != lower
    assert slash == duckdb_module._output_prefix("A/B", None)
    assert len(slash.rsplit("-", 1)[-1]) == 12


def test_manifest_no_overwrite_serializa_escritores_con_lock_os(tmp_path: Path) -> None:
    manifest = tmp_path / "concurrent.manifest.json"
    start = Barrier(2)

    def commit(token: str) -> str:
        start.wait(timeout=10)
        duckdb_module._write_manifest_atomic(
            manifest,
            {"token": token},
            token=token,
            overwrite=False,
        )
        return token

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(commit, token) for token in ("one", "two")]
        successes = []
        failures = []
        for future in futures:
            try:
                successes.append(future.result(timeout=15))
            except FileExistsError as exc:
                failures.append(exc)

    assert len(successes) == 1
    assert len(failures) == 1
    assert json.loads(manifest.read_text(encoding="utf-8"))["token"] == successes[0]


@pytest.mark.parametrize("failure_boundary", ["generation", "manifest"])
def test_compaction_manifest_keeps_previous_generation_on_commit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_boundary: str,
) -> None:
    path = tmp_path / "atomic.csv"
    output = tmp_path / "out"
    path.write_text("id,name\n111111,OLD\n", encoding="utf-8")
    spec = SourceSpec(
        name="atomic",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name"},
        column_types={"NIT": "identifier"},
    )
    previous = compact_source_to_parquet(spec, output, settings=_settings(tmp_path))
    prefix = duckdb_module._output_prefix(spec.name, None)
    manifest_path = output / f"{prefix}.manifest.json"
    previous_manifest = manifest_path.read_bytes()
    previous_compact = pd.read_parquet(previous.compact_path)
    previous_expansion = pd.read_parquet(previous.expansion_map_path)

    path.write_text("id,name\n222222,NEW\n333333,NEW2\n", encoding="utf-8")
    real_replace = os.replace

    def fail_at_boundary(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        target_path = Path(target)
        is_generation = target_path.parent.name == f"{prefix}.generations"
        is_manifest = target_path == manifest_path
        if (failure_boundary == "generation" and is_generation) or (
            failure_boundary == "manifest" and is_manifest
        ):
            raise OSError(f"fallo inyectado en {failure_boundary}")
        real_replace(source, target)

    monkeypatch.setattr(duckdb_module.os, "replace", fail_at_boundary)
    with pytest.raises(OSError, match="fallo inyectado"):
        compact_source_to_parquet(
            spec,
            output,
            settings=_settings(tmp_path),
            overwrite=True,
        )

    assert manifest_path.read_bytes() == previous_manifest
    pd.testing.assert_frame_equal(pd.read_parquet(previous.compact_path), previous_compact)
    pd.testing.assert_frame_equal(pd.read_parquet(previous.expansion_map_path), previous_expansion)
    assert not list(output.glob("*.generation.pending"))


def test_compaction_manifest_is_authoritative_and_overwrite_is_explicit(tmp_path: Path) -> None:
    path = tmp_path / "manifest.csv"
    output = tmp_path / "out"
    path.write_text("id,name\n111111,ACME\n", encoding="utf-8")
    spec = SourceSpec(
        name="manifest",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name"},
    )

    result = compact_source_to_parquet(spec, output, settings=_settings(tmp_path))
    prefix = duckdb_module._output_prefix(spec.name, None)
    manifest_path = output / f"{prefix}.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert output / manifest["artifacts"]["compact"] == result.compact_path
    assert output / manifest["artifacts"]["expansion"] == result.expansion_map_path
    assert result.compact_path.parent.name == manifest["generation"]
    with pytest.raises(FileExistsError, match="overwrite=True"):
        compact_source_to_parquet(spec, output, settings=_settings(tmp_path))


def test_compaction_after_pointer_failure_keeps_both_generations_consumable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "after-pointer.csv"
    output = tmp_path / "out"
    path.write_text("id,name\n111111,OLD\n", encoding="utf-8")
    spec = SourceSpec(
        name="after-pointer",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name"},
        column_types={"NIT": "identifier"},
    )
    previous = compact_source_to_parquet(spec, output, settings=_settings(tmp_path))
    path.write_text("id,name\n222222,NEW\n", encoding="utf-8")
    real_result_type = duckdb_module.DuckDBCompactionResult

    def fail_after_pointer(*args, **kwargs):
        raise OSError("fallo después del puntero")

    monkeypatch.setattr(duckdb_module, "DuckDBCompactionResult", fail_after_pointer)
    with pytest.raises(OSError, match="después del puntero"):
        compact_source_to_parquet(
            spec,
            output,
            settings=_settings(tmp_path),
            overwrite=True,
        )
    monkeypatch.setattr(duckdb_module, "DuckDBCompactionResult", real_result_type)

    prefix = duckdb_module._output_prefix(spec.name, None)
    manifest = json.loads((output / f"{prefix}.manifest.json").read_text(encoding="utf-8"))
    current = pd.read_parquet(output / manifest["artifacts"]["compact"])
    assert current["NOMBRE"].tolist() == ["NEW"]
    assert pd.read_parquet(previous.compact_path)["NOMBRE"].tolist() == ["OLD"]
    assert previous.expansion_map_path.exists()


def test_compaction_gc_retiene_current_mas_n_previas_y_limpia_pending(tmp_path: Path) -> None:
    path = tmp_path / "retention.csv"
    output = tmp_path / "out"
    spec = SourceSpec(
        name="retention/A",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name"},
    )
    prefix = duckdb_module._output_prefix(spec.name, None)
    abandoned = output / f".{prefix}.abandoned.generation.pending"
    abandoned.mkdir(parents=True)
    settings = DuckDBIngestionSettings(
        memory_limit="256MB",
        threads=1,
        temp_directory=tmp_path / "spill",
        previous_generations_to_keep=1,
    )

    latest = None
    for run in range(3):
        path.write_text(f"id,name\n111111,RUN-{run}\n", encoding="utf-8")
        latest = compact_source_to_parquet(
            spec,
            output,
            settings=settings,
            overwrite=run > 0,
        )

    assert latest is not None
    generations = [path for path in (output / f"{prefix}.generations").iterdir() if path.is_dir()]
    assert len(generations) == 2
    assert latest.compact_path.parent in generations
    assert not abandoned.exists()


def test_cleanup_fallido_emite_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pending = tmp_path / "pending"
    pending.mkdir()

    def fail_cleanup(_path: Path) -> None:
        raise OSError("cleanup inyectado")

    monkeypatch.setattr(duckdb_module.shutil, "rmtree", fail_cleanup)
    with pytest.warns(RuntimeWarning, match="cleanup inyectado"):
        duckdb_module._cleanup_directory(pending, purpose="prueba")
