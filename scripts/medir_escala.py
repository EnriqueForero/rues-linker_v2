"""Medición empírica de la Fase 1: velocidad de limpieza y RAM de columnas de texto.

Mide, sobre un dataset sintético de tamaño configurable, las dos palancas de la
Fase 1, de forma reproducible y con números (no estimaciones):

1. VELOCIDAD de limpieza de nombres:
   - "por fila"  : ``series.apply(clean_name)`` — el patrón anterior (n llamadas).
   - "por únicos": ``TextProcessor.process_series`` — limpia cada valor único una
     vez y mapea (lo nuevo). El factor de aceleración crece con la duplicación.

2. RAM de la columna de texto limpia:
   - ``object``           : strings de Python (lo anterior).
   - ``string[pyarrow]``  : buffer Arrow (lo nuevo). Headline de ahorro de RAM.
   Se compara ``memory_usage(deep=True)`` sobre los MISMOS valores en ambos dtypes
   (medición determinista, sin ruido de RSS), y además el RSS pico del proceso
   durante una limpieza completa (la métrica "RSS pico" literal de la compuerta).

Uso (en Colab Free, escala real de la compuerta):
    python scripts/medir_escala.py --n 200000 --report escala_200k.md
    python scripts/medir_escala.py --n 500000 --report escala_500k.md

Salida: imprime un resumen, escribe ``metadata.json`` y, si se da --report, un .md.

Contexto: Google Colab Free (~12 GB RAM). Determinista: ``seed`` fija el dataset.
Autor: equipo rues-linker.  Versión de la medición: Fase 1.
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Permite ejecutar el script desde la raíz del repo sin instalar el paquete.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from record_linkage.processing.text import TextProcessor

try:
    import psutil

    _PROC = psutil.Process()
except ImportError:  # pragma: no cover
    psutil = None
    _PROC = None


def _rss_gb() -> float:
    """RSS actual del proceso en GB (0.0 si psutil no está disponible)."""
    return _PROC.memory_info().rss / 1024**3 if _PROC is not None else 0.0


class _MuestreadorRSS:
    """Muestrea el RSS en un hilo para capturar el pico durante una operación."""

    def __init__(self, intervalo_s: float = 0.05) -> None:
        self.intervalo_s = intervalo_s
        self.pico_gb = _rss_gb()
        self._activo = False
        self._hilo: threading.Thread | None = None

    def __enter__(self) -> _MuestreadorRSS:
        self._activo = True
        self._hilo = threading.Thread(target=self._loop, daemon=True)
        self._hilo.start()
        return self

    def _loop(self) -> None:
        while self._activo:
            self.pico_gb = max(self.pico_gb, _rss_gb())
            time.sleep(self.intervalo_s)

    def __exit__(self, *exc: object) -> None:
        self._activo = False
        if self._hilo is not None:
            self._hilo.join(timeout=1.0)
        self.pico_gb = max(self.pico_gb, _rss_gb())


def generar_razones_sociales(n: int, frac_unicos: float = 0.6, seed: int = 42) -> pd.Series:
    """Genera n razones sociales sintéticas con duplicación controlada.

    Simula el escenario real de record linkage multi-fuente, donde un mismo
    nombre aparece varias veces (la misma empresa en RUES, DIAN, CRM...).

    Args:
        n: Número total de filas.
        frac_unicos: Fracción aproximada de valores únicos (0.6 = 60% únicos).
        seed: Semilla para reproducibilidad.

    Returns:
        Serie de pandas (dtype object) con n razones sociales.
    """
    rng = np.random.default_rng(seed)
    n_unicos = max(1, int(n * frac_unicos))
    tipos = ["S.A.S.", "LTDA", "S.A.", "E.U.", "& CIA", ""]
    raices = [
        "COMERCIALIZADORA",
        "INVERSIONES",
        "DISTRIBUIDORA",
        "SERVICIOS",
        "GRUPO",
        "INDUSTRIAS",
        "CONSTRUCTORA",
        "AGROPECUARIA",
        "TECNOLOGIA",
        "SOLUCIONES",
    ]
    medios = [
        "ANDINA",
        "DEL CARIBE",
        "NACIONAL",
        "BOLIVAR",
        "DEL VALLE",
        "GLOBAL",
        "INTEGRAL",
        "LOS ANDES",
        "PACIFICO",
        "ORIENTAL",
    ]
    pool = [
        f"{rng.choice(raices)} {rng.choice(medios)} {i % 997} {rng.choice(tipos)}".strip()
        for i in range(n_unicos)
    ]
    idx = rng.integers(0, n_unicos, n)
    return pd.Series([pool[i] for i in idx], name="RAZON_SOCIAL")


def medir(n: int, seed: int = 42) -> dict:
    """Ejecuta las mediciones de velocidad y RAM para tamaño n."""
    print(f"\n{'=' * 60}\n  MEDICIÓN DE ESCALA — n = {n:,} registros\n{'=' * 60}")
    serie = generar_razones_sociales(n, seed=seed)
    n_unicos = int(serie.nunique())
    print(f"  Valores únicos: {n_unicos:,} ({n_unicos / n:.0%} del total)")

    tp = TextProcessor(cleaning_mode="BALANCEADO", cache_size=100_000)

    # ── 1. VELOCIDAD: por fila (apply) vs por únicos (process_series) ──────────
    tp.clean_name.cache_clear() if hasattr(tp.clean_name, "cache_clear") else None
    gc.collect()
    t0 = time.perf_counter()
    _ = serie.apply(tp.clean_name)  # patrón anterior: n llamadas (con caché LRU)
    t_apply = time.perf_counter() - t0

    tp.clean_name.cache_clear() if hasattr(tp.clean_name, "cache_clear") else None
    gc.collect()
    with _MuestreadorRSS() as m:
        t0 = time.perf_counter()
        limpio = tp.process_series(serie, "RAZON_SOCIAL")  # nuevo: por únicos + map
        t_unicos = time.perf_counter() - t0
    rss_pico_gb = m.pico_gb
    speedup = t_apply / t_unicos if t_unicos > 0 else float("nan")
    print("\n  ── Velocidad de limpieza ──")
    print(f"    por fila  (.apply)       : {t_apply:7.2f} s")
    print(f"    por únicos (process_series): {t_unicos:7.2f} s")
    print(f"    speedup                  : {speedup:6.2f}x")

    # ── 2. RAM: misma columna limpia en object vs string[pyarrow] ─────────────
    dtype_real = str(limpio.dtype)
    como_object = limpio.astype(object)
    mem_object_mb = como_object.memory_usage(deep=True) / 1024**2
    try:
        como_arrow = limpio.astype("string[pyarrow]")
        mem_arrow_mb = como_arrow.memory_usage(deep=True) / 1024**2
        arrow_disponible = True
    except Exception:  # pragma: no cover
        mem_arrow_mb = float("nan")
        arrow_disponible = False
    reduccion_pct = (1 - mem_arrow_mb / mem_object_mb) * 100 if mem_object_mb else float("nan")
    print("\n  ── RAM de la columna de texto limpia ──")
    print(f"    dtype real de process_series : {dtype_real}")
    print(f"    object         : {mem_object_mb:8.1f} MB")
    print(f"    string[pyarrow]: {mem_arrow_mb:8.1f} MB   (-{reduccion_pct:.1f}%)")
    print(f"    RSS pico durante limpieza    : {rss_pico_gb:.2f} GB")

    # Compuerta de la Fase 1: pyarrow < object
    compuerta_ram_ok = bool(arrow_disponible and mem_arrow_mb < mem_object_mb)
    print(f"\n  COMPUERTA RAM (pyarrow < object): {'✅ PASA' if compuerta_ram_ok else '❌ FALLA'}")

    return {
        "n_registros": n,
        "n_unicos": n_unicos,
        "frac_unicos": round(n_unicos / n, 4),
        "tiempo_apply_s": round(t_apply, 3),
        "tiempo_unicos_s": round(t_unicos, 3),
        "speedup_limpieza": round(speedup, 3),
        "dtype_real_process_series": dtype_real,
        "mem_columna_object_MB": round(mem_object_mb, 2),
        "mem_columna_pyarrow_MB": round(mem_arrow_mb, 2) if arrow_disponible else None,
        "reduccion_ram_pct": round(reduccion_pct, 2) if arrow_disponible else None,
        "rss_pico_GB": round(rss_pico_gb, 3),
        "compuerta_ram_pyarrow_menor_object": compuerta_ram_ok,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Medición de escala — Fase 1")
    parser.add_argument("--n", type=int, default=200_000, help="Número de registros sintéticos")
    parser.add_argument("--seed", type=int, default=42, help="Semilla (reproducibilidad)")
    parser.add_argument("--report", type=str, default=None, help="Ruta del reporte .md")
    parser.add_argument("--metadata", type=str, default="metadata.json", help="Ruta metadata.json")
    args = parser.parse_args()

    stats = medir(args.n, seed=args.seed)
    meta = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "fase": "1 - hacer factible la escala",
        "python_version": sys.version.split()[0],
        "pandas_version": pd.__version__,
        "stats": stats,
    }
    Path(args.metadata).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n📋 metadata → {args.metadata}")

    if args.report:
        s = stats
        md = (
            f"# Medición de escala — Fase 1 (n = {s['n_registros']:,})\n\n"
            f"**Fecha:** {meta['timestamp']} · **pandas:** {meta['pandas_version']}\n\n"
            f"## Velocidad de limpieza de nombres\n\n"
            f"| Método | Tiempo | Speedup |\n|---|---:|---:|\n"
            f"| `.apply` por fila (anterior) | {s['tiempo_apply_s']} s | — |\n"
            f"| `process_series` por únicos (nuevo) | {s['tiempo_unicos_s']} s | "
            f"**{s['speedup_limpieza']}x** |\n\n"
            f"Únicos: {s['n_unicos']:,} ({s['frac_unicos']:.0%}). El speedup crece con la "
            f"duplicación de nombres (mayor en datos multi-fuente reales).\n\n"
            f"## RAM de la columna de texto limpia\n\n"
            f"| dtype | Memoria | Reducción |\n|---|---:|---:|\n"
            f"| `object` (anterior) | {s['mem_columna_object_MB']} MB | — |\n"
            f"| `string[pyarrow]` (nuevo) | {s['mem_columna_pyarrow_MB']} MB | "
            f"**-{s['reduccion_ram_pct']}%** |\n\n"
            f"RSS pico durante la limpieza: {s['rss_pico_GB']} GB.\n\n"
            f"**Compuerta RAM (pyarrow < object):** "
            f"{'✅ PASA' if s['compuerta_ram_pyarrow_menor_object'] else '❌ FALLA'}\n"
        )
        Path(args.report).write_text(md, encoding="utf-8")
        print(f"📄 reporte → {args.report}")


if __name__ == "__main__":
    main()
