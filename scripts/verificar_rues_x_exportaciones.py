"""Verificación reproducible del cruce RUES × Exportaciones DANE (v0.14.0).

Mide sobre archivos reales lo que ninguna prueba sintética puede afirmar:
tiempo, pico de RAM, conservación de filas y —lo más importante— cuántos
grupos mezclan dos NIT válidos distintos, que debe ser **cero**.

Uso::

    python scripts/verificar_rues_x_exportaciones.py \\
        --rues  /ruta/rues.zip \\
        --dane  /ruta/exportaciones.zip \\
        --salida /ruta/resultados

Cifras obtenidas con este script (muestra RUES 57.186 × DANE 895.102, 2 vCPU):
tiempo 106 s · pico ~0,9 GB · 952.288 filas restituidas · 74.268 entidades ·
223.349 filas de exportación enlazadas (24,95 %) · 0 grupos en conflicto.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from record_linkage import ColumnType, Compression, IdentifierFormat, SourceSpec
from record_linkage.flujo import ConfigCruce, ejecutar_cruce


def _muestreador_rss() -> tuple[list[float], threading.Event]:
    """Hilo que muestrea el RSS del proceso cada 50 ms."""
    pico: list[float] = [0.0]
    parar = threading.Event()

    def _bucle() -> None:
        try:
            import psutil
        except ImportError:  # pragma: no cover - psutil es dependencia declarada
            return
        proceso = psutil.Process()
        while not parar.is_set():
            pico[0] = max(pico[0], proceso.memory_info().rss / 1024**2)
            time.sleep(0.05)

    threading.Thread(target=_bucle, daemon=True).start()
    return pico, parar


def especificaciones(ruta_rues: Path, ruta_dane: Path) -> list[SourceSpec]:
    """Contratos de las dos fuentes, con sus centinelas reales declarados."""
    identificador = IdentifierFormat(mode="digits", min_length=6, max_length=12)
    return [
        SourceSpec(
            name="RUES",
            path=ruta_rues,
            compression=Compression.ZIP,
            delimiter=",",
            encoding="cp1252",
            column_mapping={"NIT": "NUMERO_IDENTIFICACION", "RAZON_SOCIAL": "RAZON_SOCIAL"},
            column_types={"NIT": ColumnType.IDENTIFIER, "RAZON_SOCIAL": ColumnType.STRING},
            identifier_formats={"NIT": identificador},
        ),
        SourceSpec(
            name="EXPORTACIONES",
            path=ruta_dane,
            compression=Compression.ZIP,
            delimiter="\t",
            encoding="cp1252",
            column_mapping={"NIT": "Nit Exportador", "RAZON_SOCIAL": "Razon Social"},
            column_types={"NIT": ColumnType.IDENTIFIER, "RAZON_SOCIAL": ColumnType.STRING},
            identifier_formats={"NIT": identificador},
            null_values={"NIT": ("-1", "0"), "RAZON_SOCIAL": ("NO DEFINIDO",)},
        ),
    ]


def conflictos_de_identificador(correlativa: pd.DataFrame) -> int:
    """Grupos que contienen dos NIT válidos distintos. Debe ser 0."""
    validos = correlativa[
        correlativa["NIT_BASE"].notna()
        & (correlativa["NIT_BASE"] != "")
        & correlativa["NIT_VALID"].astype(str).isin(["True", "1", "true"])
    ]
    return int((validos.groupby("ID_GRUPO")["NIT_BASE"].nunique() > 1).sum())


def main() -> int:
    """Ejecuta la verificación y devuelve 0 si todas las invariantes pasan."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rues", required=True, type=Path)
    parser.add_argument("--dane", required=True, type=Path)
    parser.add_argument("--salida", required=True, type=Path)
    parser.add_argument("--trabajo", type=Path, default=None)
    args = parser.parse_args()

    pico, parar = _muestreador_rss()
    inicio = time.time()
    resultado = ejecutar_cruce(
        ConfigCruce(
            fuentes=especificaciones(args.rues, args.dane),
            workspace=args.salida,
            confiables={"RUES"},
            dir_trabajo=args.trabajo,
            filas_smoke=5_000,
            exportar_excel=False,
        )
    )
    parar.set()
    correlativa = resultado.correlativa
    conflictos = conflictos_de_identificador(correlativa)
    exportaciones = correlativa[correlativa["SRC"] == "EXPORTACIONES"]
    fuentes_por_grupo = correlativa.groupby("ID_GRUPO")["SRC"].transform("nunique")
    enlazadas = int((fuentes_por_grupo[exportaciones.index] > 1).sum())

    reporte = {
        "filas_entrada": resultado.metricas["filas_entrada"],
        "filas_correlativa": len(correlativa),
        "entidades": resultado.metricas["entidades"],
        "segundos_total": round(time.time() - inicio, 1),
        "pico_rss_mib": round(pico[0], 1),
        "exportaciones_enlazadas": enlazadas,
        "exportaciones_totales": len(exportaciones),
        "pct_exportaciones_enlazadas": round(enlazadas / max(len(exportaciones), 1), 4),
        "grupos_con_nit_valido_en_conflicto": conflictos,
    }
    print(resultado.resumen())
    print(json.dumps(reporte, indent=2, ensure_ascii=False))
    (args.salida / "verificacion_rues_x_exportaciones.json").write_text(
        json.dumps(reporte, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    fallos = []
    if reporte["filas_correlativa"] != reporte["filas_entrada"]:
        fallos.append("la correlativa no conserva todas las filas de entrada")
    if conflictos:
        fallos.append(f"{conflictos} grupos mezclan dos NIT válidos distintos")
    for fallo in fallos:
        print(f"❌ {fallo}")
    if not fallos:
        print("✅ Todas las invariantes pasaron")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
