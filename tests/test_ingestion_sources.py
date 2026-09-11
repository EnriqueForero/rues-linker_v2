"""Pruebas focalizadas de ingestión heterogénea, memoria y seguridad."""

from __future__ import annotations

import gzip
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.ingestion import (
    ArchiveSafetyError,
    ColumnType,
    Compression,
    IdentifierFormat,
    InputFormat,
    NumericFormat,
    SchemaError,
    SourceSpec,
    ZipSafetyLimits,
    iter_source_chunks,
    load_source,
    load_sources,
)


def _basic_spec(path: Path, **kwargs: object) -> SourceSpec:
    return SourceSpec(
        name=path.stem,
        path=path,
        column_mapping={"NIT": "Identificación", "NOMBRE": "Razón Social"},
        column_types={"NIT": ColumnType.IDENTIFIER},
        **kwargs,
    )


def test_cp1252_txt_mapping_projection_types_nulls_and_locale(tmp_path: Path) -> None:
    path = tmp_path / "fuente_arbitraria.dat"
    contents = (
        "Identificación\tRazón Social\tValor COP\tIGNORAR\n"
        "001234\t“Compañía” \N{EN DASH} SAS\t1.234,56\tgrande\n"
        "-1\tNO DEFINIDO\t2.000,00\tcolumna\n"
    )
    path.write_bytes(contents.encode("cp1252"))
    spec = SourceSpec(
        name="exportaciones",
        path=path,
        format=InputFormat.TXT,
        column_mapping={
            "NIT": "identificacion",
            "NOMBRE": "razon_social",
            "VALOR": "valor cop",
        },
        column_types={"NIT": "identifier", "VALOR": "number"},
        identifier_formats={"NIT": IdentifierFormat(min_length=6)},
        numeric_formats={"VALOR": NumericFormat(decimal_separator=",", thousands_separator=".")},
        null_values={"NIT": ("-1",), "NOMBRE": ("NO DEFINIDO",)},
        chunksize=1,
    )

    loaded = load_source(spec)

    assert loaded.data.columns.tolist() == ["NIT", "NOMBRE", "VALOR"]
    assert loaded.data.loc[0, "NIT"] == "001234"
    assert loaded.data.loc[0, "NOMBRE"] == "“Compañía” \N{EN DASH} SAS"
    assert pd.isna(loaded.data.loc[1, "NIT"])
    assert pd.isna(loaded.data.loc[1, "NOMBRE"])
    assert loaded.data["VALOR"].tolist() == [1234.56, 2000.0]
    assert loaded.report.encoding == "cp1252"
    assert loaded.report.delimiter == "\t"
    assert loaded.report.engine == "pyarrow"
    assert loaded.report.rows == 2
    assert not any(
        0x80 <= ord(character) <= 0x9F
        for value in loaded.data["NOMBRE"].dropna()
        for character in value
    )


def test_mapping_is_per_source_and_produces_same_canonical_schema(tmp_path: Path) -> None:
    rues = tmp_path / "rues.csv"
    exports = tmp_path / "exports.txt"
    rues.write_text("NUMERO_IDENTIFICACION,RAZON_SOCIAL\n001234,ACME\n", encoding="utf-8")
    exports.write_text("Nit Exportador\tRazon Social\n001234\tACME\n", encoding="utf-8")
    specs = [
        SourceSpec(
            name="rues",
            path=rues,
            column_mapping={"NIT": "NUMERO_IDENTIFICACION", "NOMBRE": "RAZON_SOCIAL"},
        ),
        SourceSpec(
            name="exports",
            path=exports,
            column_mapping={"NIT": "Nit Exportador", "NOMBRE": "Razon Social"},
        ),
    ]

    loaded = load_sources(specs)

    assert loaded["rues"].data.columns.tolist() == ["NIT", "NOMBRE"]
    assert loaded["exports"].data.columns.tolist() == ["NIT", "NOMBRE"]
    assert loaded["rues"].data.iloc[0].tolist() == loaded["exports"].data.iloc[0].tolist()


def test_optional_column_mapping_loads_when_present_and_warns_when_absent(
    tmp_path: Path,
) -> None:
    with_city = tmp_path / "with_city.csv"
    without_city = tmp_path / "without_city.csv"
    with_city.write_text("id,name,city\n001234,ACME,BOGOTA\n", encoding="utf-8")
    without_city.write_text("id,name\n001234,ACME\n", encoding="utf-8")
    common = {
        "column_mapping": {"NIT": "id", "NOMBRE": "name"},
        "optional_column_mapping": {"CIUDAD": "city", "EMAIL": "email"},
    }

    present = load_source(SourceSpec(name="present", path=with_city, **common))
    absent = load_source(SourceSpec(name="absent", path=without_city, **common))

    assert present.data.columns.tolist() == ["NIT", "NOMBRE", "CIUDAD"]
    assert absent.data.columns.tolist() == ["NIT", "NOMBRE"]
    assert present.report.resolved_mapping["CIUDAD"] == "city"
    assert any("EMAIL" in warning for warning in present.report.warnings)
    assert any("CIUDAD" in warning for warning in absent.report.warnings)
    assert any("EMAIL" in warning for warning in absent.report.warnings)


def test_gzip_and_zip_are_streamed_from_local_disk(tmp_path: Path) -> None:
    payload = "Identificación,Razón Social\n001234,ACME\n".encode()
    gzip_path = tmp_path / "source.csv.gz"
    zip_path = tmp_path / "source.zip"
    with gzip.open(gzip_path, "wb") as stream:
        stream.write(payload)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("nested/source.csv", payload)

    gzip_loaded = load_source(_basic_spec(gzip_path))
    zip_loaded = load_source(_basic_spec(zip_path))

    assert gzip_loaded.data.loc[0, "NIT"] == "001234"
    assert gzip_loaded.report.compression is Compression.GZIP
    assert zip_loaded.data.loc[0, "NIT"] == "001234"
    assert zip_loaded.report.compression is Compression.ZIP
    assert zip_loaded.report.archive_member == "nested/source.csv"


def test_zip_rejects_traversal_before_reading_member(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../escape.csv", "Identificación,Razón Social\n1,A\n")

    with pytest.raises(ArchiveSafetyError, match="insegura"):
        load_source(_basic_spec(path))


def test_zip_requires_member_when_multiple_data_files(tmp_path: Path) -> None:
    path = tmp_path / "multiple.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a.csv", "Identificación,Razón Social\n001234,A\n")
        archive.writestr("b.csv", "Identificación,Razón Social\n005678,B\n")

    with pytest.raises(SchemaError, match="archive_member"):
        load_source(_basic_spec(path))

    selected = load_source(_basic_spec(path, archive_member="b.csv"))
    assert selected.data.loc[0, "NIT"] == "005678"


def test_zip_enforces_declared_compression_ratio(tmp_path: Path) -> None:
    path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("source.csv", b"0" * 200_000)
    limits = ZipSafetyLimits(max_compression_ratio=2.0)

    with pytest.raises(ArchiveSafetyError, match="ratio"):
        load_source(_basic_spec(path, safety_limits=limits))


def test_xlsx_streams_chunks_and_does_not_append_dot_zero_to_identifier(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "source.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["ID real", "Nombre", "Extra"])
    sheet.append([890002474, "ACME", "x"])
    sheet.append(["001234", "BETA", "y"])
    workbook.save(path)
    workbook.close()
    spec = SourceSpec(
        name="xlsx",
        path=path,
        column_mapping={"NIT": "ID real", "NOMBRE": "Nombre"},
        column_types={"NIT": "identifier"},
        chunksize=1,
    )

    chunks = list(iter_source_chunks(spec))
    loaded = load_source(spec)

    assert [len(chunk) for chunk in chunks] == [1, 1]
    assert loaded.data["NIT"].tolist() == ["890002474", "001234"]
    assert "Extra" not in loaded.data
    assert loaded.report.engine == "openpyxl-read-only"
    assert loaded.report.warnings


def test_parquet_projects_columns_and_coerces_invalid_identifier(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    path = tmp_path / "source.parquet"
    pd.DataFrame(
        {
            "id real": pd.Series([890002474.0, 123.5], dtype="float64"),
            "nombre": ["ACME", "BETA"],
            "muy_grande": ["x" * 10_000, "y" * 10_000],
        }
    ).to_parquet(path, index=False)
    spec = SourceSpec(
        name="parquet",
        path=path,
        column_mapping={"NIT": "id real", "NOMBRE": "nombre"},
        column_types={"NIT": "identifier"},
    )

    loaded = load_source(spec)

    assert loaded.data.loc[0, "NIT"] == "890002474"
    assert pd.isna(loaded.data.loc[1, "NIT"])
    assert "muy_grande" not in loaded.data
    assert loaded.report.invalid_values == {"NIT": 1}
    assert loaded.report.engine == "pyarrow-parquet"


def test_invalid_value_policy_raise_is_fail_fast(tmp_path: Path) -> None:
    path = tmp_path / "invalid.csv"
    path.write_text("id,name\n123.5,ACME\n", encoding="utf-8")
    spec = SourceSpec(
        name="invalid",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name"},
        column_types={"NIT": "identifier"},
        invalid_values="raise",
    )

    with pytest.raises(SchemaError, match="tipo declarado"):
        load_source(spec)


@pytest.mark.parametrize("text_engine", ["pandas", "pyarrow"])
def test_multiline_quoted_tsv_is_one_logical_record(tmp_path: Path, text_engine: str) -> None:
    path = tmp_path / "multiline.txt"
    path.write_text(
        'id\tname\tnote\n001234\tACME\t"primera línea\nsegunda línea"\n',
        encoding="utf-8",
    )
    spec = SourceSpec(
        name="multiline",
        path=path,
        column_mapping={"NIT": "id", "NOMBRE": "name", "NOTA": "note"},
        newlines_in_values=True,
        text_engine=text_engine,
    )

    loaded = load_source(spec)

    assert len(loaded.data) == 1
    assert loaded.data.loc[0, "NOTA"] == "primera línea\nsegunda línea"
