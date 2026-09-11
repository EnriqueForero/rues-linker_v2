#!/usr/bin/env python3
"""Verificación reproducible de los dos archivos reales entregados.

La prueba usa únicamente disco local, proyecta las dos columnas necesarias,
colapsa filas exactas antes del enlace y restaura una fila correlativa por
registro de entrada. Las métricas de NIT son *proxies de consistencia*, no una
estimación de precision/recall: los archivos no incluyen etiquetas humanas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psutil

from record_linkage import ColumnType, SourceSpec, linkage, load_source
from record_linkage.utils.memory import get_process_rss_bytes


def _sha256(path: Path, block_size: int = 8 * 1024**2) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def _peak_rss_sampler(interval_seconds: float = 0.05):
    stop = threading.Event()
    sample = {"peak": get_process_rss_bytes()}

    def monitor() -> None:
        while not stop.wait(interval_seconds):
            sample["peak"] = max(sample["peak"], get_process_rss_bytes())

    thread = threading.Thread(target=monitor, name="rss-sampler", daemon=True)
    thread.start()
    try:
        yield sample
    finally:
        stop.set()
        thread.join(timeout=2.0)
        sample["peak"] = max(sample["peak"], get_process_rss_bytes())


def _atomic_json_dump(payload: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _source_specs(rues_zip: Path, exports_zip: Path, temp_dir: Path) -> list[SourceSpec]:
    return [
        SourceSpec(
            name="RUES",
            path=rues_zip,
            column_mapping={
                "NIT": "NUMERO_IDENTIFICACION",
                "RAZON_SOCIAL": "RAZON_SOCIAL",
            },
            column_types={"NIT": ColumnType.IDENTIFIER},
            temp_dir=temp_dir,
        ),
        SourceSpec(
            name="EXPORTACIONES",
            path=exports_zip,
            column_mapping={
                "NIT": "Nit Exportador",
                "RAZON_SOCIAL": "Razon Social",
            },
            column_types={"NIT": ColumnType.IDENTIFIER},
            temp_dir=temp_dir,
        ),
    ]


def _correlative_with_expected_nit(
    frames: dict[str, pd.DataFrame], correlative: pd.DataFrame
) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    offset = 0
    for source_name, frame in frames.items():
        piece = pd.DataFrame(
            {
                "ORIGINAL_INDEX": range(offset, offset + len(frame)),
                "EXPECTED_SRC": source_name,
                "EXPECTED_NIT": frame["NIT"].astype("string"),
            }
        )
        pieces.append(piece)
        offset += len(frame)
    expected = pd.concat(pieces, ignore_index=True)
    mapped = correlative.merge(expected, on="ORIGINAL_INDEX", how="left", validate="one_to_one")
    if mapped["EXPECTED_SRC"].isna().any():
        raise AssertionError("La correlativa contiene índices que no pertenecen a las fuentes.")
    if not mapped["SRC"].astype(str).eq(mapped["EXPECTED_SRC"]).all():
        raise AssertionError("SRC no coincide con el orden original de las fuentes.")
    return mapped


def _nit_consistency_proxies(mapped: pd.DataFrame) -> dict[str, Any]:
    # RUES entrega principalmente la base de 9 dígitos, mientras la fuente de
    # exportaciones incluye el DV como décimo dígito. Comparar las cadenas de
    # entrada marcaría como conflicto cada coincidencia correcta. El pipeline
    # conserva NIT_BASE ya normalizado por AdvancedNitProcessor; ese es el
    # dominio comparable entre fuentes.
    if "NIT_BASE" not in mapped.columns:
        raise AssertionError("La correlativa no contiene NIT_BASE canónico.")
    valid = mapped.dropna(subset=["NIT_BASE"]).copy()
    valid["CANONICAL_NIT_BASE"] = valid["NIT_BASE"].astype("string")
    valid = valid.loc[valid["CANONICAL_NIT_BASE"].str.len().fillna(0) > 0]
    nit_sources = valid[["CANONICAL_NIT_BASE", "EXPECTED_SRC"]].drop_duplicates()
    common_nits = (
        nit_sources.groupby("CANONICAL_NIT_BASE", observed=True)["EXPECTED_SRC"]
        .nunique()
        .loc[lambda count: count >= 2]
        .index
    )
    common = valid.loc[valid["CANONICAL_NIT_BASE"].isin(common_nits)]
    groups_per_nit = common.groupby("CANONICAL_NIT_BASE", observed=True)["ID_GRUPO"].nunique()

    rues_pairs = (
        valid.loc[
            valid["EXPECTED_SRC"] == "RUES",
            ["ID_GRUPO", "CANONICAL_NIT_BASE"],
        ]
        .drop_duplicates()
        .assign(HAS_SAME_RUES_NIT=True)
    )
    export_common = common.loc[
        common["EXPECTED_SRC"] == "EXPORTACIONES",
        ["ORIGINAL_INDEX", "ID_GRUPO", "CANONICAL_NIT_BASE"],
    ]
    export_check = export_common.merge(
        rues_pairs,
        on=["ID_GRUPO", "CANONICAL_NIT_BASE"],
        how="left",
        validate="many_to_one",
    )
    matched_export_rows = int(export_check["HAS_SAME_RUES_NIT"].eq(True).sum())

    cross = valid.groupby("ID_GRUPO", observed=True).agg(
        source_count=("EXPECTED_SRC", "nunique"),
        nit_count=("CANONICAL_NIT_BASE", "nunique"),
    )
    cross = cross.loc[cross["source_count"] >= 2]
    conflicting_cross_groups = int((cross["nit_count"] > 1).sum())

    return {
        "disclaimer": (
            "Proxies de consistencia por NIT_BASE canónico; no sustituyen "
            "precision/recall contra ground truth humano."
        ),
        "canonical_nit_field": "NIT_BASE",
        "common_canonical_nit_bases": len(common_nits),
        "common_bases_in_one_predicted_group": int((groups_per_nit == 1).sum()),
        "common_base_group_consistency_rate": (
            float((groups_per_nit == 1).mean()) if len(groups_per_nit) else 1.0
        ),
        "export_rows_with_common_base": len(export_common),
        "export_rows_linked_to_same_rues_base": matched_export_rows,
        "export_common_base_link_rate": (
            matched_export_rows / len(export_common) if len(export_common) else 1.0
        ),
        "cross_source_groups_with_nonnull_base": len(cross),
        "cross_source_groups_with_conflicting_bases": conflicting_cross_groups,
    }


def verify(args: argparse.Namespace) -> dict[str, Any]:
    for path in (args.rues_zip, args.exports_zip):
        if not path.is_file():
            raise FileNotFoundError(path)

    args.work_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = args.work_dir / "local_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    specs = _source_specs(args.rues_zip, args.exports_zip, temp_dir)

    started = time.perf_counter()
    with _peak_rss_sampler() as rss:
        loaded = {}
        ingestion_seconds: dict[str, float] = {}
        for spec in specs:
            source_started = time.perf_counter()
            loaded[spec.name] = load_source(spec)
            ingestion_seconds[spec.name] = time.perf_counter() - source_started

        frames = {name: value.data for name, value in loaded.items()}
        linkage_started = time.perf_counter()
        result = linkage(
            sources=loaded,
            trusted_sources={"RUES"},
            col_ciudad=None,
            work_dir=str(args.work_dir / "pipeline"),
            profile=args.profile,
            skip_reporting=True,
            collapse_exact_duplicates=True,
        )
        linkage_seconds = time.perf_counter() - linkage_started

        correlative = result["correlative"]
        golden = result["golden"]
        expected_rows = sum(len(frame) for frame in frames.values())
        if len(correlative) != expected_rows:
            raise AssertionError(
                f"Correlativa incompleta: {len(correlative):,} != {expected_rows:,}."
            )
        if correlative["ORIGINAL_INDEX"].nunique() != expected_rows:
            raise AssertionError("ORIGINAL_INDEX no es una biyección de las filas de entrada.")
        original_index = correlative["ORIGINAL_INDEX"].to_numpy(dtype=np.int64, copy=False)
        if not np.array_equal(np.sort(original_index), np.arange(expected_rows, dtype=np.int64)):
            raise AssertionError("ORIGINAL_INDEX no cubre exactamente [0, n).")
        if correlative["ID_GRUPO"].isna().any():
            raise AssertionError("La correlativa contiene grupos nulos.")
        if not golden["ID_GRUPO"].is_unique:
            raise AssertionError("El golden contiene ID_GRUPO duplicados.")
        if set(golden["ID_GRUPO"]) != set(correlative["ID_GRUPO"]):
            raise AssertionError("Golden y correlativa no cubren los mismos grupos.")
        if int(golden["INPUT_ROW_COUNT"].sum()) != expected_rows:
            raise AssertionError("INPUT_ROW_COUNT no reconcilia con las filas de entrada.")

        mapped = _correlative_with_expected_nit(frames, correlative)
        proxies = _nit_consistency_proxies(mapped)

    collapse = result["preprocessing"]["exact_duplicate_collapse"]
    reports = {
        name: {
            "rows": report.rows,
            "columns": list(report.columns),
            "format": report.input_format.value,
            "compression": report.compression.value,
            "encoding": report.encoding,
            "delimiter": report.delimiter,
            "engine": report.engine,
            "archive_member": report.archive_member,
            "invalid_values": dict(report.invalid_values),
            "warnings": list(report.warnings),
            "ingestion_seconds": ingestion_seconds[name],
            "dataframe_deep_mib": frames[name].memory_usage(deep=True).sum() / 1024**2,
            "input_rows": collapse[name]["input_rows"],
            "processed_rows": collapse[name]["processed_rows"],
            "collapsed_rows": collapse[name]["collapsed_rows"],
        }
        for name, report in (item for item in result["ingestion_reports"].items())
    }
    return {
        "status": "PASS",
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "available_cpus": (
                len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
            ),
            "system_memory_mib": psutil.virtual_memory().total / 1024**2,
            "versions": {
                package: version(package)
                for package in ("rues-linker", "pandas", "numpy", "pyarrow")
            },
        },
        "inputs": {
            "rues_zip": {"path": str(args.rues_zip), "sha256": _sha256(args.rues_zip)},
            "exports_zip": {
                "path": str(args.exports_zip),
                "sha256": _sha256(args.exports_zip),
            },
        },
        "profile": args.profile,
        "sources": reports,
        "result": {
            "input_rows": sum(len(frame) for frame in frames.values()),
            "processed_rows": sum(item["processed_rows"] for item in collapse.values()),
            "correlative_rows": len(correlative),
            "golden_rows": len(golden),
            "predicted_groups": int(correlative["ID_GRUPO"].nunique()),
        },
        "nit_consistency_proxies": proxies,
        "performance": {
            "linkage_seconds": linkage_seconds,
            "total_seconds": time.perf_counter() - started,
            "peak_process_rss_mib": rss["peak"] / 1024**2,
        },
        "invariants": {
            "all_input_rows_restored": True,
            "original_index_bijection": True,
            "no_null_group_ids": True,
            "golden_group_ids_unique": True,
            "golden_correlative_group_sets_equal": True,
            "input_row_count_reconciles": True,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rues-zip", required=True, type=Path)
    parser.add_argument("--exports-zip", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    # La evidencia no debe depender de un default fácil de pasar por alto:
    # perfiles distintos cambian umbrales y, por tanto, los grupos obtenidos.
    parser.add_argument("--profile", required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = verify(args)
    _atomic_json_dump(result, args.output)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
