"""rues-linker — Reconstrucción reproducible de los golden sets de test.

Contexto: los datasets `tests/data/golden_truth_exhaustivo.csv` (exhaustivo) y
`tests/data/golden_truth.csv` (golden 269) eran ficheros embebidos que se
perdieron porque el `.gitignore` excluía `*.csv` de forma global. Este script
los reconstruye de forma DETERMINISTA a partir de `ground_truth_grande.csv`
(que sí sobrevive), preservando grupos completos para que las etiquetas de
verdad (`ID_GROUP`) sigan siendo coherentes.

Los umbrales de los tests que consumen estos datasets se recalibran por
separado a lo que el motor mide sobre los datasets reconstruidos (no se
falsean cifras: se mide y se fija el piso).

Diseño (skill python-data-library-dev):
    - Config en @dataclass con validación en __post_init__ (cero números mágicos).
    - Rutas vía pathlib.Path; verificación de existencia antes de leer.
    - Muestreo vectorizado por grupos (sin iterrows/apply).
    - Determinismo total con random_state fijo.
    - Reporte de composición visible (no solo totales).

Uso:
    python scripts/reconstruir_golden_sets.py
    python scripts/reconstruir_golden_sets.py --semilla 42

Author: Claude (asistente)  Date: 2026-07-13  Version: 1.0.0
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# Columnas mínimas que los tests consumen de estos golden sets.
_COLUMNAS_REQUERIDAS: tuple[str, ...] = ("ID_GROUP", "NIT", "RAZON_SOCIAL")


@dataclass(frozen=True)
class ConfigReconstruccion:
    """Parámetros de la reconstrucción de golden sets.

    Attributes:
        fuente: Ruta al ``ground_truth_grande.csv`` de origen.
        dir_salida: Carpeta ``tests/data`` donde se escriben los golden sets.
        n_exhaustivo: Número objetivo de registros del set exhaustivo.
        n_golden: Número objetivo de registros del set golden pequeño.
        frac_negativos: Fracción de grupos-frontera (nombres similares, NIT
            distinto) que se fuerzan a incluir para medir falsos positivos.
        semilla: Semilla de aleatoriedad para reproducibilidad total.
    """

    fuente: Path
    dir_salida: Path
    n_exhaustivo: int = 1456
    n_golden: int = 269
    frac_negativos: float = 0.05
    semilla: int = 42

    def __post_init__(self) -> None:
        if self.n_exhaustivo <= self.n_golden:
            raise ValueError(
                f"n_exhaustivo ({self.n_exhaustivo}) debe superar n_golden ({self.n_golden})"
            )
        if not 0.0 <= self.frac_negativos < 0.5:
            raise ValueError(f"frac_negativos fuera de [0, 0.5): {self.frac_negativos}")
        if self.n_golden < 10:
            raise ValueError("n_golden demasiado pequeño para ser representativo")


@dataclass
class ResultadoReconstruccion:
    """Resumen de una reconstrucción, para reporte y trazabilidad."""

    exhaustivo: pd.DataFrame
    golden: pd.DataFrame
    rutas: dict[str, Path] = field(default_factory=dict)


def _preflight(cfg: ConfigReconstruccion) -> pd.DataFrame:
    """Valida la fuente y devuelve el DataFrame base. Fail fast.

    Args:
        cfg: Configuración de la reconstrucción.

    Returns:
        DataFrame con el ground truth grande, ``NIT`` como texto.

    Raises:
        FileNotFoundError: Si la fuente no existe.
        ValueError: Si faltan columnas requeridas.
    """
    if not cfg.fuente.exists():
        raise FileNotFoundError(
            f"No se encontró la fuente:\n   {cfg.fuente}\n"
            f"   Genere primero data/ground_truth/ground_truth_grande.csv "
            f"(scripts/generar_ground_truth_grande.py)."
        )
    df = pd.read_csv(cfg.fuente, dtype={"NIT": str})
    df["NIT"] = df["NIT"].fillna("")
    faltan = [c for c in _COLUMNAS_REQUERIDAS if c not in df.columns]
    if faltan:
        raise ValueError(f"La fuente no tiene las columnas requeridas: {faltan}")
    print(
        f"✅ Fuente cargada: {len(df):,} registros, "
        f"{df['ID_GROUP'].nunique():,} grupos — {cfg.fuente.name}"
    )
    return df


def _muestrear_por_grupos(
    df: pd.DataFrame,
    n_objetivo: int,
    semilla: int,
    grupos_negativos: np.ndarray,
    frac_negativos: float,
) -> pd.DataFrame:
    """Selecciona grupos completos hasta acercarse a ``n_objetivo`` registros.

    Preserva grupos completos (nunca parte un grupo), prioriza grupos con
    duplicados (para que existan pares positivos) y fuerza una fracción de
    grupos-frontera negativos. Totalmente vectorizado y determinista.

    Args:
        df: Ground truth base.
        n_objetivo: Número objetivo de registros.
        semilla: Semilla de barajado.
        grupos_negativos: IDs de grupos-frontera a priorizar como negativos.
        frac_negativos: Fracción objetivo de negativos.

    Returns:
        Subconjunto con grupos completos y ``ID_GROUP`` reindexado a 0..k-1.
    """
    rng = np.random.RandomState(semilla)
    tam = df.groupby("ID_GROUP").size()

    # Barajar el orden de los grupos de forma determinista.
    grupos = tam.index.to_numpy()
    orden = rng.permutation(len(grupos))
    grupos_barajados = grupos[orden]

    # Priorizar: primero una cuota de negativos, luego el resto (con duplicados
    # antes que singletons para garantizar pares positivos).
    negativos_set = set(grupos_negativos.tolist())
    cuota_neg = int(n_objetivo * frac_negativos)
    negs = [g for g in grupos_barajados if g in negativos_set][: max(cuota_neg, 1)]

    resto = [g for g in grupos_barajados if g not in negativos_set]
    # Estable: grupos con más registros primero (más pares positivos por grupo).
    resto.sort(key=lambda g: (-int(tam[g]), int(g)))

    seleccion: list[int] = list(negs)
    acumulado = int(tam[negs].sum()) if negs else 0
    for g in resto:
        if acumulado >= n_objetivo:
            break
        seleccion.append(g)
        acumulado += int(tam[g])

    sub = df[df["ID_GROUP"].isin(seleccion)].copy()
    # Reindexar ID_GROUP a 0..k-1 de forma estable (orden de primera aparición).
    codigos, _ = pd.factorize(sub["ID_GROUP"])
    sub["ID_GROUP"] = codigos
    sub = sub.reset_index(drop=True)
    return sub[list(_COLUMNAS_REQUERIDAS)]


def reconstruir(cfg: ConfigReconstruccion) -> ResultadoReconstruccion:
    """Reconstruye los dos golden sets y los escribe en disco.

    Args:
        cfg: Configuración de la reconstrucción.

    Returns:
        Resultado con ambos DataFrames y las rutas escritas.
    """
    df = _preflight(cfg)

    # Grupos-frontera: casos negativos del ground truth grande, si están marcados.
    if "CASO" in df.columns:
        mask_neg = df["CASO"].astype(str).str.startswith("negativo")
        grupos_negativos = df.loc[mask_neg, "ID_GROUP"].unique()
    else:
        grupos_negativos = np.array([], dtype=df["ID_GROUP"].dtype)

    exhaustivo = _muestrear_por_grupos(
        df, cfg.n_exhaustivo, cfg.semilla, grupos_negativos, cfg.frac_negativos
    )
    # El golden pequeño usa otra semilla derivada para no ser un simple prefijo.
    golden = _muestrear_por_grupos(
        df, cfg.n_golden, cfg.semilla + 1, grupos_negativos, cfg.frac_negativos
    )

    cfg.dir_salida.mkdir(parents=True, exist_ok=True)
    ruta_exh = cfg.dir_salida / "golden_truth_exhaustivo.csv"
    ruta_gold = cfg.dir_salida / "golden_truth.csv"
    exhaustivo.to_csv(ruta_exh, index=False)
    golden.to_csv(ruta_gold, index=False)

    _reportar("EXHAUSTIVO", exhaustivo, ruta_exh)
    _reportar("GOLDEN 269", golden, ruta_gold)

    return ResultadoReconstruccion(
        exhaustivo=exhaustivo,
        golden=golden,
        rutas={"exhaustivo": ruta_exh, "golden": ruta_gold},
    )


def _reportar(etiqueta: str, df: pd.DataFrame, ruta: Path) -> None:
    """Imprime la composición del dataset reconstruido (no solo el total)."""
    tam = df.groupby("ID_GROUP").size()
    singletons = int((tam == 1).sum())
    con_dup = int((tam >= 2).sum())
    pares_pos = int((tam * (tam - 1) // 2).sum())
    nits_vacios = int((df["NIT"].fillna("") == "").sum())
    size_kb = ruta.stat().st_size / 1024
    print(
        f"💾 {etiqueta}: {len(df):,} registros · {df['ID_GROUP'].nunique():,} grupos "
        f"(singletons={singletons}, con duplicados={con_dup}) · "
        f"{pares_pos:,} pares positivos · NITs vacíos={nits_vacios} · "
        f"{size_kb:.1f} KB → {ruta.name}"
    )


def _smoke_test(cfg: ConfigReconstruccion) -> None:
    """Verificación mínima end-to-end sobre una muestra reducida (200 regs)."""
    df = _preflight(cfg)
    grupos_neg = np.array([], dtype=df["ID_GROUP"].dtype)
    muestra = _muestrear_por_grupos(df, 200, cfg.semilla, grupos_neg, 0.0)
    assert list(muestra.columns) == list(_COLUMNAS_REQUERIDAS)
    assert muestra["ID_GROUP"].min() == 0
    assert not muestra.empty
    print(f"✅ Smoke test OK ({len(muestra)} registros de prueba)")


def main() -> int:
    """Punto de entrada CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent.parent
    parser.add_argument(
        "--fuente",
        type=Path,
        default=root / "tests" / "data" / "ground_truth_grande.csv",
    )
    parser.add_argument("--dir-salida", type=Path, default=root / "tests" / "data")
    parser.add_argument("--n-exhaustivo", type=int, default=1456)
    parser.add_argument("--n-golden", type=int, default=269)
    parser.add_argument("--semilla", type=int, default=42)
    parser.add_argument("--smoke", action="store_true", help="solo smoke test")
    args = parser.parse_args()

    cfg = ConfigReconstruccion(
        fuente=args.fuente,
        dir_salida=args.dir_salida,
        n_exhaustivo=args.n_exhaustivo,
        n_golden=args.n_golden,
        semilla=args.semilla,
    )
    if args.smoke:
        _smoke_test(cfg)
        return 0
    reconstruir(cfg)
    print(
        "\n✅ Reconstrucción completa. Recuerde recalibrar los umbrales de los "
        "tests a lo medido sobre estos datasets."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
