"""stress_test.py — Stress test del pipeline sobre fracción de un dataset grande.

Mide tiempo, throughput y comportamiento de memoria del pipeline sobre una
fracción configurable del input. Útil para responder: "¿cuánto tarda esto
sobre N% del RUES?" antes de comprometerse a un volumen mayor.

USO
    python scripts/stress_test.py \\
        --input data/rues_completo.parquet \\
        --fraction 0.10 \\
        --modo orchestrator \\
        --trusted RUES \\
        --output reporte_stress.md
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    import psutil

    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


def _ram_libre_gb() -> float:
    if not _HAS_PSUTIL:
        return -1.0
    return psutil.virtual_memory().available / (1024**3)


def stress_orchestrator(df: pd.DataFrame, trusted: set[str], col_fuente: str) -> dict:
    """Stress vía Orchestrator multi-fuente."""
    from record_linkage.config.profiles import crear_config_orchestrator
    from record_linkage.pipeline.orchestrator import Orchestrator

    if col_fuente not in df.columns:
        raise ValueError(f"Para modo orchestrator se necesita columna `{col_fuente}` en el df")

    fuentes = {f: df[df[col_fuente] == f].reset_index(drop=True) for f in df[col_fuente].unique()}
    ram_inicio = _ram_libre_gb()

    buf = io.StringIO()
    t0 = time.time()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        cfg = crear_config_orchestrator(perfil="produccion_estandar", trusted_sources=trusted)
        orch = Orchestrator(config=cfg, sources=fuentes, work_dir="/tmp/stress_orch")
        res = orch.run()
    elapsed = time.time() - t0
    ram_fin = _ram_libre_gb()

    corr = res["correlative"]
    golden = res["golden"]
    return {
        "modo": "orchestrator",
        "n_input": len(df),
        "n_fuentes": len(fuentes),
        "fuentes": list(fuentes.keys()),
        "trusted": list(trusted),
        "n_correlativa": len(corr),
        "n_golden": len(golden),
        "n_grupos_predichos": int(corr["ID_GRUPO"].nunique()),
        "tiempo_s": round(elapsed, 1),
        "throughput_regs_por_s": round(len(df) / elapsed, 1) if elapsed > 0 else None,
        "ram_libre_inicio_gb": round(ram_inicio, 2),
        "ram_libre_fin_gb": round(ram_fin, 2),
        "ram_consumida_gb": round(ram_inicio - ram_fin, 2) if ram_inicio > 0 else None,
    }


def stress_deduplicate_unified(df: pd.DataFrame, col_nit: str, col_name: str) -> dict:
    """Stress vía deduplicate_unified (modo single-source)."""
    from record_linkage.deduplication.unified import deduplicate_unified

    ram_inicio = _ram_libre_gb()
    buf = io.StringIO()
    t0 = time.time()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        corr, _ = deduplicate_unified(
            df_input=df.copy(),
            col_nit=col_nit,
            col_name=col_name,
            mode="BALANCEADO",
            profile="deduplication_standard",
            output_dir="/tmp/stress_dedup",
        )
    elapsed = time.time() - t0
    ram_fin = _ram_libre_gb()

    return {
        "modo": "deduplicate_unified",
        "n_input": len(df),
        "n_correlativa": len(corr),
        "n_grupos_predichos": int(corr["ID_GRUPO"].nunique()),
        "tiempo_s": round(elapsed, 1),
        "throughput_regs_por_s": round(len(df) / elapsed, 1) if elapsed > 0 else None,
        "ram_libre_inicio_gb": round(ram_inicio, 2),
        "ram_libre_fin_gb": round(ram_fin, 2),
        "ram_consumida_gb": round(ram_inicio - ram_fin, 2) if ram_inicio > 0 else None,
    }


def _formatear_reporte_md(stats: dict) -> str:
    lines = [
        "# Reporte de stress test",
        "",
        f"**Fecha:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Modo:** `{stats['modo']}`",
        f"**Input:** {stats['n_input']:,} registros",
        "",
        "## Resultados",
        "",
        "| Métrica | Valor |",
        "|---|---:|",
        f"| Registros de entrada | {stats['n_input']:,} |",
        f"| Registros en correlativa | {stats['n_correlativa']:,} |",
    ]
    if "n_golden" in stats:
        lines.append(f"| Golden records | {stats['n_golden']:,} |")
    lines.extend(
        [
            f"| Grupos predichos | {stats['n_grupos_predichos']:,} |",
            f"| Tiempo total | {stats['tiempo_s']} s |",
            f"| Throughput | {stats['throughput_regs_por_s']} regs/s |",
        ]
    )
    if stats.get("ram_consumida_gb") is not None:
        lines.extend(
            [
                f"| RAM libre al inicio | {stats['ram_libre_inicio_gb']} GB |",
                f"| RAM libre al final | {stats['ram_libre_fin_gb']} GB |",
                f"| RAM consumida (estimada) | {stats['ram_consumida_gb']} GB |",
            ]
        )

    # Extrapolación a 2M
    if stats["throughput_regs_por_s"]:
        eta_2m_min = 2_000_000 / stats["throughput_regs_por_s"] / 60
        lines.extend(
            [
                "",
                "## Extrapolación a 2M registros (lineal)",
                "",
                f"- ETA optimista: **{eta_2m_min:.0f} min** ({eta_2m_min / 60:.1f} h)",
                f"- ETA realista (x1.5 por degradación O(n log n)): **{eta_2m_min * 1.5:.0f} min**",
                f"- ETA pesimista (x2): **{eta_2m_min * 2:.0f} min**",
                "",
                "> ⚠️ La extrapolación lineal NO es confiable más allá de un orden",
                "> de magnitud sobre el input medido. Tomar como cota inferior.",
            ]
        )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--input", required=True)
    parser.add_argument("--fraction", type=float, default=0.10, help="Fracción del input a usar")
    parser.add_argument(
        "--modo", choices=["orchestrator", "deduplicate_unified"], default="orchestrator"
    )
    parser.add_argument("--trusted", nargs="*", default=[])
    parser.add_argument("--col-name", default="RAZON_SOCIAL")
    parser.add_argument("--col-nit", default="NIT")
    parser.add_argument("--col-fuente", default="FUENTE")
    parser.add_argument("--output", default="reporte_stress.md")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"❌ No existe: {input_path}", file=sys.stderr)
        return 1

    print(f"📥 Cargando {input_path}...")
    df_full = (
        pd.read_csv(input_path, dtype=str)
        if input_path.suffix == ".csv"
        else pd.read_parquet(input_path)
    )
    print(f"   Total: {len(df_full):,} registros")

    n_sample = int(len(df_full) * args.fraction)
    df = df_full.sample(n=n_sample, random_state=args.seed).reset_index(drop=True)
    print(f"📊 Muestra ({args.fraction:.1%}): {len(df):,} registros")

    if args.col_nit in df.columns:
        df[args.col_nit] = df[args.col_nit].fillna("")

    print(f"🚀 Modo: {args.modo}")
    print(f"   RAM libre antes: {_ram_libre_gb():.2f} GB")

    if args.modo == "orchestrator":
        stats = stress_orchestrator(df, set(args.trusted), args.col_fuente)
    else:
        stats = stress_deduplicate_unified(df, args.col_nit, args.col_name)

    print("\n📊 Resultados:")
    print(json.dumps(stats, indent=2, default=str))

    reporte = _formatear_reporte_md(stats)
    Path(args.output).write_text(reporte, encoding="utf-8")
    print(f"\n💾 Reporte guardado en: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
