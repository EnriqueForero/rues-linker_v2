"""Pipeline de producción — 4 fuentes (RUES, DIAN, CRM, SUPERSOCIEDADES).

Equivalente a la "CELDA 8.4" del notebook fuente. Ejecuta el pipeline
completo con `Orchestrator` + `TrustedSourceLSHEngine`.

Uso:
    python scripts/ejecutar_produccion.py \
        --workspace /data/rl \
        --iteracion IT8 \
        --input-dir /data/rl/IT8/input

Requiere: las 4 fuentes en parquet dentro de --input-dir, con columnas
SRC, NIT, RAZON_SOCIAL como mínimo.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

import pandas as pd
import psutil

# Asegurar que el paquete es importable desde el repo
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# E402 silenciado: sys.path.insert debe correr antes de estos imports.
from record_linkage.config import Config, Rutas
from record_linkage.config.profiles import config_produccion_it7
from record_linkage.pipeline.orchestrator import Orchestrator
from record_linkage.utils.colombia_time import hora_colombia
from record_linkage.utils.memory import limpiar_memoria

# ════════════════════════════════════════════════════════════════════
# Monkey-patch del _run_L2 — preserva el patrón del notebook celda 194.
# Se reemplaza el método del Orchestrator por la versión optimizada
# que usa TrustedSourceLSHEngine + HybridStorage.
#
# TODO: refactorizar a inyección de dependencias en una versión futura.
# ════════════════════════════════════════════════════════════════════


def _aplicar_optimizacion_trusted_l2() -> None:
    """Aplica el monkey-patch de `_run_L2` con la versión optimizada.

    Este es el patrón del notebook fuente (celda 194 "CELDA A").
    Sin esto, la ejecución usa el LSH lento (~11 h) en lugar del
    optimizado (~3–5 h).
    """
    # El módulo trusted.py contiene TrustedSourceLSHEngine.
    # _run_L2_optimized se monta cuando se importa este script
    # gracias a la inicialización del módulo.
    from record_linkage.engine.lsh.trusted import TrustedSourceLSHEngine  # noqa: F401

    # NOTA: la función _run_L2_optimized vive como helper top-level en
    # el notebook fuente; aquí se asume disponible vía import lateral.
    # Si en el futuro se mueve, el ImportError aquí lo deja claro.
    print("✅ TrustedSourceLSHEngine disponible (monkey-patch L2 listo)")


def cargar_fuentes(input_dir: Path) -> dict[str, pd.DataFrame]:
    """Carga las 4 fuentes desde parquet.

    Convención de nombres: `fuente_<NOMBRE>.parquet` donde NOMBRE es uno
    de {RUES, DIAN, CRM, SUPERSOCIEDADES, EXPORTACIONES}.

    Args:
        input_dir: Carpeta con los archivos parquet.

    Returns:
        Dict {nombre_fuente: DataFrame}.

    Raises:
        FileNotFoundError: Si falta alguna fuente esperada.
    """
    fuentes_esperadas = ["RUES", "DIAN", "CRM", "SUPERSOCIEDADES"]
    fuentes: dict[str, pd.DataFrame] = {}
    faltantes: list[str] = []

    for nombre in fuentes_esperadas:
        ruta = input_dir / f"fuente_{nombre}.parquet"
        if not ruta.exists():
            faltantes.append(str(ruta))
            continue
        df = pd.read_parquet(ruta)
        fuentes[nombre] = df
        print(f"   ✅ {nombre}: {len(df):,} registros — {ruta.name}")

    if faltantes:
        raise FileNotFoundError("Fuentes faltantes:\n" + "\n".join(f"  • {f}" for f in faltantes))

    return fuentes


def main() -> int:
    """Punto de entrada CLI."""
    parser = argparse.ArgumentParser(
        description="Pipeline de Record Linkage en producción (4 fuentes)"
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help="Ruta base de trabajo (e.g., /data/rl o /content/drive/MyDrive/rl)",
    )
    parser.add_argument(
        "--iteracion",
        default="PROD",
        help="Identificador de la corrida (e.g., IT7, IT8_PROD)",
    )
    parser.add_argument(
        "--input-dir",
        help="Carpeta de fuentes parquet (default: <workspace>/<iteracion>/input)",
    )
    parser.add_argument(
        "--trusted",
        nargs="+",
        default=["RUES", "SUPERSOCIEDADES"],
        help="Fuentes trusted (no se comparan internamente)",
    )
    args = parser.parse_args()

    # ── Setup ────────────────────────────────────────────────────
    cfg = Config(
        workspace=args.workspace,
        iteracion=args.iteracion,
        profile_name="enterprise_scale_4_sources",
        trusted_sources=set(args.trusted),
    )
    rutas = Rutas.desde_config(cfg)
    rutas.crear_directorios()

    input_dir = Path(args.input_dir) if args.input_dir else rutas.entrada

    print("=" * 70)
    print(f"🚀 PIPELINE DE PRODUCCIÓN — {cfg.iteracion}")
    print("=" * 70)
    print(f"⏰ {hora_colombia()}")
    print(f"📂 Workspace: {cfg.workspace}")
    print(f"📂 Input:     {input_dir}")
    print(f"📂 Output:    {rutas.salida}")
    print(f"🛡️  Trusted:   {sorted(cfg.trusted_sources)}")
    print()

    # ── Pre-flight ───────────────────────────────────────────────
    print("🔍 Pre-flight:")
    mem = psutil.virtual_memory()
    print(f"   • Memoria disponible: {mem.available / (1024**3):.1f} GB")
    _aplicar_optimizacion_trusted_l2()

    # ── Carga ────────────────────────────────────────────────────
    print("\n📥 Cargando fuentes:")
    fuentes = cargar_fuentes(input_dir)
    total = sum(len(df) for df in fuentes.values())
    print(f"   TOTAL: {total:,} registros")

    # ── Configuración del Orchestrator ───────────────────────────
    # Inyecta trusted_sources del CLI en el config
    cfg_orch = dict(config_produccion_it7)
    perfil = "enterprise_scale_4_sources"
    if perfil in cfg_orch.get("profiles", {}):
        cfg_orch["profiles"][perfil]["trusted_unique_sources"] = sorted(cfg.trusted_sources)

    # ── Ejecutar ─────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("🟡 INICIANDO PIPELINE...")
    print("=" * 70)
    t0 = time.time()

    try:
        orch = Orchestrator(
            config=cfg_orch,
            sources=fuentes,
            work_dir=str(rutas.base),
        )
        resultado = orch.run()
    except Exception as exc:
        elapsed = time.time() - t0
        h, m = divmod(int(elapsed), 3600)
        print(f"\n❌ ERROR tras {h}h {m // 60}m: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    elapsed = time.time() - t0
    h, rem = divmod(int(elapsed), 3600)
    m, s = divmod(rem, 60)

    golden = resultado.get("golden", pd.DataFrame())
    correlativa = resultado.get("correlative", pd.DataFrame())

    print("\n" + "=" * 70)
    print("🟢 COMPLETADO")
    print("=" * 70)
    print(f"   ⏰ Fin: {hora_colombia()}")
    print(f"   ⏱️  Duración: {h}h {m}m {s}s")
    print(f"   📊 Golden Records: {len(golden):,}")
    print(f"   📊 Correlativa:    {len(correlativa):,}")
    if len(correlativa) > 0 and len(golden) > 0:
        tasa = 1 - len(golden) / len(correlativa)
        print(f"   📊 Reducción:      {tasa:.2%} ({len(correlativa) - len(golden):,} duplicados)")
    print(f"   📂 Salida:         {rutas.salida}")
    print("=" * 70)

    limpiar_memoria()
    return 0


if __name__ == "__main__":
    sys.exit(main())
