"""Smoke funcional del wheel instalado, ejecutable con ``python -I``.

No importa desde el checkout: verifica versión/origen y recorre la ruta pública
DuckDB -> resultado disco -> payload -> manifiesto/metadatos.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
import tempfile
from pathlib import Path

import record_linkage
from record_linkage import ColumnType, SourceSpec
from record_linkage.flujo import ConfigCruce, ResultadoCruceDisco, ejecutar_cruce
from record_linkage.ingestion import DuckDBIngestionSettings


def run(expected_version: str) -> None:
    """Ejecuta el smoke y levanta una excepción ante cualquier contrato roto."""

    installed_version = importlib.metadata.version("rues-linker")
    if installed_version != expected_version or record_linkage.__version__ != expected_version:
        raise RuntimeError(
            f"Versión instalada/importada inesperada: {installed_version}/"
            f"{record_linkage.__version__}; se esperaba {expected_version}."
        )
    package_path = Path(record_linkage.__file__).resolve()
    prefix = Path(sys.prefix).resolve()
    if not package_path.is_relative_to(prefix):
        raise RuntimeError(f"El paquete no proviene de la venv instalada: {package_path}")

    with tempfile.TemporaryDirectory(prefix="rues-linker-wheel-smoke-") as raw_tmp:
        root = Path(raw_tmp)
        source_a = root / "a.csv"
        source_b = root / "b.csv"
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
        settings = DuckDBIngestionSettings(
            memory_limit="192MB",
            threads=1,
            temp_directory=root / "spill",
            max_temp_directory_size="1GB",
        )
        config = ConfigCruce(
            fuentes=specs,
            workspace=root / "output",
            dir_trabajo=root / "work",
            perfil="prueba_rapida",
            filas_smoke=0,
            exportar_excel=False,
            reusar_checkpoints=False,
            limite_filas={"B": 1},
            motor_ingesta="duckdb",
            duckdb_settings=settings,
            modo_resultado="disco",
            preservar_payload=True,
        )
        result = ejecutar_cruce(config)
        if not isinstance(result, ResultadoCruceDisco):
            raise TypeError(f"Salida inesperada: {type(result).__name__}")
        if result.correlativa.rows != 3 or result.metricas["por_fuente"] != {"A": 2, "B": 1}:
            raise RuntimeError(f"Conteos inesperados: {result.metricas}")
        payload = set(result.correlativa.preview(10)["segmento"])
        if payload != {"industrial", "premium", "exportador"}:
            raise RuntimeError(f"Payload inesperado: {sorted(payload)}")

        generation_dir = Path(result.rutas["generation_dir"])
        manifest_path = Path(result.rutas["manifest"])
        metadata_path = Path(result.rutas["metadatos"])
        for path in (result.golden.path, result.correlativa.path, manifest_path, metadata_path):
            if not Path(path).is_file():
                raise FileNotFoundError(path)
        if not generation_dir.is_dir():
            raise FileNotFoundError(generation_dir)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("generation") != generation_dir.name:
            raise RuntimeError("El manifiesto no apunta a la generación publicada.")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        parameters = metadata["parametros"]
        expected_parameters = {
            "limite_filas": {"B": 1},
            "modo_resultado": "disco",
            "preservar_payload": True,
        }
        for key, value in expected_parameters.items():
            if parameters.get(key) != value:
                raise RuntimeError(f"Parámetro no propagado {key}: {parameters.get(key)!r}")
        if parameters["duckdb_settings"]["memory_limit"] != "192MB":
            raise RuntimeError("memory_limit no quedó registrado.")
        if parameters["duckdb_settings"]["threads"] != 1:
            raise RuntimeError("threads no quedó registrado.")

    print("WHEEL-SMOKE OK: versión/origen, DuckDB, disco, límite, payload, manifiesto y metadatos")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-version", default="0.16.0")
    args = parser.parse_args()
    run(args.expected_version)


if __name__ == "__main__":
    main()
