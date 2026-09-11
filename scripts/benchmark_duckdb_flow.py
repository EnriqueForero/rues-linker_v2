r"""Benchmark reproducible del flujo RUES × exportaciones con límites tempranos.

Ejemplo (PowerShell)::

    python scripts/benchmark_duckdb_flow.py `
      --rues "C:\datos\rues.zip" `
      --exportaciones "C:\datos\exportaciones.zip" `
      --output-dir "C:\benchmarks\rues_linker" `
      --limit-rues 5000 --limit-exportaciones 5000

Ejecute otra vez con ``--motor pandas`` para comparar la ruta histórica en un
proceso limpio. Por defecto solo se proyectan NIT y razón social, que son los
campos que puntúan en esta configuración. ``--include-unused-fields`` reproduce
explícitamente el desperdicio previo a la auditoría para medir su costo. Las
huellas ``input_fingerprint`` y ``partition_fingerprint`` permiten verificar
paridad sin depender de los números concretos de grupo.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psutil

from record_linkage import ColumnType, IdentifierFormat, SourceSpec
from record_linkage.flujo import ConfigCruce, ejecutar_cruce
from record_linkage.ingestion import DuckDBIngestionSettings


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("se requiere un entero >= 1")
    return parsed


@dataclass
class _RSSSampler:
    """Muestrea el RSS del proceso sin interferir con el pipeline."""

    interval_seconds: float = 0.05

    def __post_init__(self) -> None:
        self._process = psutil.Process(os.getpid())
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.baseline_bytes = 0
        self.peak_bytes = 0

    def start(self) -> None:
        gc.collect()
        self.baseline_bytes = self._process.memory_info().rss
        self.peak_bytes = self.baseline_bytes

        def sample() -> None:
            while not self._stop.wait(self.interval_seconds):
                try:
                    self.peak_bytes = max(
                        self.peak_bytes,
                        self._process.memory_info().rss,
                    )
                except psutil.Error:
                    return

        self._thread = threading.Thread(target=sample, name="rss-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with suppress(psutil.Error):
            self.peak_bytes = max(self.peak_bytes, self._process.memory_info().rss)


def _source_specs(
    rues: Path,
    exportaciones: Path,
    *,
    include_unused_fields: bool = False,
) -> list[SourceSpec]:
    return [
        SourceSpec(
            name="RUES",
            path=rues,
            column_mapping={
                "NIT": "NUMERO_IDENTIFICACION",
                "RAZON_SOCIAL": "RAZON_SOCIAL",
            },
            optional_column_mapping=(
                {
                    "CIUDAD": "CODIGO_MUNICIPIO_COMERCIAL",
                    "TELEFONO": "TELEFONO_COMERCIAL_1",
                    "EMAIL": "CORREO_ELECTRONICO_COMERCIAL",
                }
                if include_unused_fields
                else {}
            ),
            column_types={
                "NIT": ColumnType.IDENTIFIER,
                "RAZON_SOCIAL": ColumnType.STRING,
            },
            identifier_formats={
                "NIT": IdentifierFormat(mode="digits", min_length=6, max_length=12)
            },
            null_values={
                "NIT": ("0000000000000",),
                "RAZON_SOCIAL": ("SIN DATO", "NO DEFINIDO"),
            },
        ),
        SourceSpec(
            name="EXPORTACIONES",
            path=exportaciones,
            column_mapping={
                "NIT": "Nit Exportador",
                "RAZON_SOCIAL": "Razon Social",
            },
            optional_column_mapping=(
                {"DEPARTAMENTO": "Departamento Origen"} if include_unused_fields else {}
            ),
            column_types={
                "NIT": ColumnType.IDENTIFIER,
                "RAZON_SOCIAL": ColumnType.STRING,
            },
            identifier_formats={
                "NIT": IdentifierFormat(mode="digits", min_length=6, max_length=12)
            },
            null_values={
                "NIT": ("-1", "0", "00"),
                "RAZON_SOCIAL": ("NO DEFINIDO",),
            },
        ),
    ]


def _fingerprints(correlative: pd.DataFrame) -> tuple[str, str]:
    ordered = correlative.reset_index(drop=True)
    visible = [
        column
        for column in ("SRC", "NIT", "RAZON_SOCIAL", "ORIGINAL_INDEX")
        if column in ordered.columns
    ]
    input_hashes = pd.util.hash_pandas_object(ordered[visible], index=False).to_numpy(
        dtype=np.uint64,
        copy=False,
    )
    input_fingerprint = hashlib.sha256(input_hashes.astype("<u8").tobytes()).hexdigest()
    canonical_groups = pd.factorize(
        ordered["ID_GRUPO"],
        sort=False,
        use_na_sentinel=False,
    )[0].astype("<i8", copy=False)
    partition_fingerprint = hashlib.sha256(canonical_groups.tobytes()).hexdigest()
    return input_fingerprint, partition_fingerprint


def _stable_frame_fingerprint(frame: pd.DataFrame, *, exclude: set[str]) -> str:
    """Huella lógica que omite columnas volátiles como ``CREATED_AT``."""

    stable = frame.drop(columns=[column for column in exclude if column in frame.columns])
    hashes = pd.util.hash_pandas_object(stable, index=False).to_numpy(
        dtype=np.uint64,
        copy=False,
    )
    return hashlib.sha256(hashes.astype("<u8").tobytes()).hexdigest()


def _file_sizes(paths: dict[str, Path]) -> dict[str, int]:
    return {name: path.stat().st_size for name, path in paths.items() if path.is_file()}


def _artifact_sizes(reports: dict[str, Any]) -> dict[str, dict[str, int]]:
    sizes: dict[str, dict[str, int]] = {}
    for source, report in reports.items():
        per_source: dict[str, int] = {}
        for label, key in (
            ("compact_parquet_bytes", "compact_path"),
            ("expansion_map_parquet_bytes", "expansion_map_path"),
        ):
            raw_path = report.get(key)
            if raw_path and Path(raw_path).is_file():
                per_source[label] = Path(raw_path).stat().st_size
        if per_source:
            sizes[source] = per_source
    return sizes


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rues", type=Path, required=True, help="ZIP/CSV del RUES")
    parser.add_argument(
        "--exportaciones",
        type=Path,
        required=True,
        help="ZIP/TXT de exportaciones",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directorio padre; cada ejecución crea una subcarpeta nueva",
    )
    parser.add_argument("--limit-rues", type=_positive_int, default=5_000)
    parser.add_argument("--limit-exportaciones", type=_positive_int, default=5_000)
    parser.add_argument("--motor", choices=("duckdb", "pandas"), default="duckdb")
    parser.add_argument("--profile", default="prueba_rapida")
    parser.add_argument("--smoke-rows", type=int, default=0)
    parser.add_argument("--memory-limit", default="512MB")
    parser.add_argument("--threads", type=_positive_int, default=2)
    parser.add_argument("--max-temp-directory-size", default="8GB")
    parser.add_argument(
        "--include-unused-fields",
        action="store_true",
        help="Reproduce la proyección antigua de campos que no participan en el score.",
    )
    return parser


def _configure_utf8_console() -> None:
    """Evita que una consola Windows CP1252 altere la lógica por un emoji."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def run(args: argparse.Namespace) -> dict[str, Any]:
    for label, path in (("RUES", args.rues), ("exportaciones", args.exportaciones)):
        if not path.is_file():
            raise FileNotFoundError(f"{label}: no existe {path}")
    if args.smoke_rows < 0:
        raise ValueError("--smoke-rows debe ser >= 0")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = args.output_dir.expanduser() / f"{args.motor}-{run_id}"
    workspace = run_dir / "results"
    work_dir = run_dir / "work"
    settings = (
        DuckDBIngestionSettings(
            memory_limit=args.memory_limit,
            threads=args.threads,
            temp_directory=run_dir / "duckdb-spill",
            max_temp_directory_size=args.max_temp_directory_size,
        )
        if args.motor == "duckdb"
        else None
    )
    config = ConfigCruce(
        fuentes=_source_specs(
            args.rues,
            args.exportaciones,
            include_unused_fields=args.include_unused_fields,
        ),
        workspace=workspace,
        confiables={"RUES"},
        dir_trabajo=work_dir,
        perfil=args.profile,
        filas_smoke=args.smoke_rows,
        exportar_excel=False,
        reusar_checkpoints=False,
        colapsar_duplicados_exactos=True,
        forzar_relectura=True,
        limite_filas={
            "RUES": args.limit_rues,
            "EXPORTACIONES": args.limit_exportaciones,
        },
        motor_ingesta=args.motor,
        duckdb_settings=settings,
    )

    sampler = _RSSSampler()
    sampler.start()
    started = time.perf_counter()
    try:
        result = ejecutar_cruce(config)
    finally:
        elapsed = time.perf_counter() - started
        sampler.stop()

    source_counts = {
        str(source): int(count)
        for source, count in result.correlativa["SRC"].value_counts(sort=False).items()
    }
    source_report_rows = {
        source: int(report["rows"]) for source, report in result.reportes_carga.items()
    }
    processed_rows = int(result.metricas["filas_entrada"])
    groups_correlative = set(result.correlativa["ID_GRUPO"].unique())
    groups_golden = set(result.golden["ID_GRUPO"].unique())
    invariants = {
        "correlative_rows_equal_processed_rows": (len(result.correlativa) == processed_rows),
        "metric_rows_equal_correlative_rows": (
            result.metricas["filas_entrada"] == len(result.correlativa)
        ),
        "no_null_group_ids": not result.correlativa["ID_GRUPO"].isna().any(),
        "golden_and_correlative_groups_equal": groups_golden == groups_correlative,
        "source_counts_within_requested_limits": (
            source_counts.get("RUES", 0) <= args.limit_rues
            and source_counts.get("EXPORTACIONES", 0) <= args.limit_exportaciones
        ),
    }
    if not all(invariants.values()):
        raise RuntimeError(f"Benchmark violó invariantes: {invariants}")

    input_fingerprint, partition_fingerprint = _fingerprints(result.correlativa)
    summary: dict[str, Any] = {
        "run_id": run_id,
        "motor": args.motor,
        "profile": args.profile,
        "include_unused_fields": bool(args.include_unused_fields),
        "inputs": {
            "rues": str(args.rues.resolve()),
            "exportaciones": str(args.exportaciones.resolve()),
        },
        "limits": {
            "RUES": args.limit_rues,
            "EXPORTACIONES": args.limit_exportaciones,
        },
        "elapsed_seconds": round(elapsed, 3),
        "rss": {
            "baseline_mib": round(sampler.baseline_bytes / 1024**2, 3),
            "peak_mib": round(sampler.peak_bytes / 1024**2, 3),
            "delta_mib": round((sampler.peak_bytes - sampler.baseline_bytes) / 1024**2, 3),
        },
        "rows": {
            "read_or_staged_by_source": source_report_rows,
            "processed": processed_rows,
            "correlative": len(result.correlativa),
            "golden": len(result.golden),
            "groups": int(result.correlativa["ID_GRUPO"].nunique()),
            "by_source": source_counts,
            "compact_by_source": {
                source: (int(report["processed_rows"]) if "processed_rows" in report else None)
                for source, report in result.reportes_carga.items()
            },
        },
        "invalid_values": {
            source: report.get("invalid_values", {})
            for source, report in result.reportes_carga.items()
        },
        "invariants": invariants,
        "input_fingerprint": input_fingerprint,
        "partition_fingerprint": partition_fingerprint,
        "golden_fingerprint_without_created_at": _stable_frame_fingerprint(
            result.golden,
            exclude={"CREATED_AT"},
        ),
        "pipeline_metrics": result.metricas,
        "output_sizes_bytes": _file_sizes(result.rutas),
        "ingestion_artifact_sizes_bytes": _artifact_sizes(result.reportes_carga),
        "run_directory": str(run_dir.resolve()),
    }
    summary_path = run_dir / "benchmark_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    summary["summary_path"] = str(summary_path.resolve())
    return summary


def main() -> int:
    _configure_utf8_console()
    summary = run(_build_parser().parse_args())
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
