"""deduplication.auto — deduplicación con enrutamiento automático por régimen.

v0.7.4: pieza "lista para producción". Resuelve el problema de que el usuario
tenga que recordar qué perfil usar según el régimen.

Contexto
--------
El sistema rinde distinto según haya o no NIT:
    - CON_NIT: el perfil estándar/calibrado da F1≈0.96.
    - SIN_NIT: el perfil estándar SOBRE-FUSIONA (F1 0.22). Hay que usar
      `deduplication_sin_nit_conservador` (F1 0.65, P 0.91).

`deduplicate_auto` separa el DataFrame por régimen (NIT presente vs ausente),
aplica el perfil ÓPTIMO a cada subconjunto, y recombina los resultados con
IDs de grupo globalmente únicos. El usuario no tiene que decidir nada.

Ver docs/DEUDA_SIN_NIT.md para la calibración detrás de los perfiles.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from .unified import deduplicate_unified, rutas_salida

logger = logging.getLogger("deduplicate_auto")

#: Valores que se consideran "NIT ausente".
_EMPTY_NIT_VALUES = frozenset(["", "nan", "none", "<na>", "null"])

#: Perfiles óptimos por régimen (calibrados contra ground truth, v0.7.4).
_PROFILE_CON_NIT = "deduplication_standard"
_PROFILE_SIN_NIT = "deduplication_sin_nit_conservador"


def _is_nit_empty(series: pd.Series) -> pd.Series:
    """Máscara booleana: True donde el NIT está ausente/vacío."""
    normalized = series.astype(str).str.strip().str.lower()
    return normalized.isin(_EMPTY_NIT_VALUES) | series.isna()


def _estadisticas_regimen(
    correlativa: pd.DataFrame, conexiones: pd.DataFrame, profile: str, output_dir: str
) -> dict[str, Any]:
    """Estadísticas de una pasada de ``deduplicate_unified`` como dict serializable.

    ``deduplicate_unified`` devuelve ``(correlativa, conexiones)``: el segundo
    elemento es un DataFrame (la tabla ``contrato.CONEXIONES``), no un dict de
    estadísticas. Hasta F1.13 ``deduplicate_auto`` lo desempaquetaba como
    ``stats`` y lo expandía con ``{**stats}``: el dict resultante traía una
    ``Series`` por columna de las conexiones (``NIT``, ``RAZON_SOCIAL``, …) en
    el caso homogéneo, y DataFrames enteros bajo
    ``stats_con_nit``/``stats_sin_nit`` en el mixto. Nada lo leía, pero
    ``dedupe()`` lo guardaba en ``metricas['stats_pipeline']`` y no era
    serializable. F2.10 declaró el contrato real en ``deduplicate_unified``.

    ``rutas`` dice dónde quedaron ``correlativa.parquet`` (la tabla de trabajo
    con las técnicas de L1) y ``conexiones.parquet`` de este régimen
    (``unified.rutas_salida``), escritas por el escritor único: es lo que el
    manifiesto de ``dedupe()`` publica para que alguien las encuentre.
    """
    return {
        "profile": profile,
        "n_registros": len(correlativa),
        "n_grupos": int(correlativa["ID_GRUPO"].nunique()),
        "n_conexiones_no_triviales": len(conexiones),
        "rutas": {k: str(v) for k, v in rutas_salida(output_dir).items()},
    }


def deduplicate_auto(
    df_input: pd.DataFrame,
    col_nit: str = "NIT",
    col_name: str = "RAZON_SOCIAL",
    mode: str = "AGRESIVO",
    output_dir: str = "deduplicacion_auto",
    profile_con_nit: str = _PROFILE_CON_NIT,
    profile_sin_nit: str = _PROFILE_SIN_NIT,
    **kwargs: Any,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Deduplica enrutando cada régimen (CON_NIT / SIN_NIT) a su perfil óptimo.

    Esta es la entrada recomendada para producción cuando el dataset puede
    contener registros con y sin NIT mezclados. Internamente:

    1. Separa ``df_input`` en dos subconjuntos por presencia de NIT.
    2. Deduplica cada uno con su perfil calibrado:
         - CON_NIT  -> ``profile_con_nit``  (default deduplication_standard, F1≈0.96)
         - SIN_NIT  -> ``profile_sin_nit``  (default sin_nit_conservador,   F1≈0.65)
    3. Recombina, garantizando IDs de grupo globalmente únicos (los grupos
       SIN_NIT se desplazan para no colisionar con los CON_NIT).

    Si el dataset es homogéneo (todo con NIT o todo sin NIT), delega
    directamente a `deduplicate_unified` con el perfil correspondiente, sin
    el overhead de separar.

    Args:
        df_input: DataFrame de entrada.
        col_nit: Columna del NIT.
        col_name: Columna de la razón social.
        mode: Modo de limpieza ('CONSERVADOR'|'BALANCEADO'|'AGRESIVO').
        output_dir: Directorio base de salida. Cada régimen usa un subdir.
        profile_con_nit: Perfil para registros con NIT.
        profile_sin_nit: Perfil para registros sin NIT.
        **kwargs: Se pasan tal cual a `deduplicate_unified` (p.ej.
            ``extra_features``).

    Returns:
        Tupla ``(tabla_correlativa_combinada, estadisticas)``. La correlativa
        combinada preserva ``ORIGINAL_INDEX`` apuntando al índice original de
        ``df_input`` y trae una columna ``REGIMEN_AUTO`` ('CON_NIT'|'SIN_NIT')
        que indica por qué ruta pasó cada registro. ``estadisticas`` es un
        dict serializable (sin Series ni DataFrames) con ``routed``,
        ``n_con_nit``, ``n_sin_nit``, ``n_grupos_con_nit``,
        ``n_grupos_sin_nit``, ``profile_con_nit``, ``profile_sin_nit`` y, por
        régimen ejecutado, ``stats_con_nit``/``stats_sin_nit`` con
        ``n_registros``, ``n_grupos``, ``n_conexiones_no_triviales``,
        ``profile`` y ``rutas`` (``correlativa.parquet`` y
        ``conexiones.parquet`` de ese régimen, F2.10: en ``output_dir`` si el
        dataset es homogéneo, en ``output_dir/con_nit`` y ``output_dir/sin_nit``
        si es mixto).

    Raises:
        ValueError: si faltan columnas requeridas o el DataFrame está vacío.

    Example:
        >>> corr, stats = deduplicate_auto(df, col_nit="NIT", col_name="RAZON_SOCIAL")
        >>> stats["n_con_nit"], stats["n_sin_nit"]
    """
    if df_input.empty:
        raise ValueError("DataFrame de entrada está vacío")
    if col_nit not in df_input.columns or col_name not in df_input.columns:
        raise ValueError(f"Columnas requeridas {col_nit}, {col_name} no encontradas")

    # Preservar el índice original ANTES de separar, para recombinar.
    df = df_input.reset_index(drop=True).copy()
    df["_auto_orig_idx"] = df.index

    empty_mask = _is_nit_empty(df[col_nit])
    df_con = df[~empty_mask].copy()
    df_sin = df[empty_mask].copy()

    n_con, n_sin = len(df_con), len(df_sin)
    logger.info("deduplicate_auto: %d CON_NIT, %d SIN_NIT", n_con, n_sin)

    # ── Caso homogéneo: delegar directo (sin overhead de separar) ──────────
    if n_sin == 0:
        logger.info("Dataset homogéneo CON_NIT → %s", profile_con_nit)
        corr, conexiones = deduplicate_unified(
            df_input=df_input,
            col_nit=col_nit,
            col_name=col_name,
            mode=mode,
            profile=profile_con_nit,
            output_dir=output_dir,
            **kwargs,
        )
        corr["REGIMEN_AUTO"] = "CON_NIT"
        stats_con = _estadisticas_regimen(corr, conexiones, profile_con_nit, output_dir)
        stats: dict[str, Any] = {
            "routed": "homogeneo_con_nit",
            "n_con_nit": n_con,
            "n_sin_nit": 0,
            "n_grupos_con_nit": stats_con["n_grupos"],
            "n_grupos_sin_nit": 0,
            "profile_con_nit": profile_con_nit,
            "profile_sin_nit": profile_sin_nit,
            "stats_con_nit": stats_con,
        }
        return corr, stats
    if n_con == 0:
        logger.info("Dataset homogéneo SIN_NIT → %s", profile_sin_nit)
        corr, conexiones = deduplicate_unified(
            df_input=df_input,
            col_nit=col_nit,
            col_name=col_name,
            mode=mode,
            profile=profile_sin_nit,
            output_dir=output_dir,
            **kwargs,
        )
        corr["REGIMEN_AUTO"] = "SIN_NIT"
        stats_sin = _estadisticas_regimen(corr, conexiones, profile_sin_nit, output_dir)
        stats = {
            "routed": "homogeneo_sin_nit",
            "n_con_nit": 0,
            "n_sin_nit": n_sin,
            "n_grupos_con_nit": 0,
            "n_grupos_sin_nit": stats_sin["n_grupos"],
            "profile_con_nit": profile_con_nit,
            "profile_sin_nit": profile_sin_nit,
            "stats_sin_nit": stats_sin,
        }
        return corr, stats

    # ── Caso mixto: deduplicar cada régimen por separado ───────────────────
    logger.info("Dataset mixto → enrutamiento por régimen")
    corr_con, conexiones_con = deduplicate_unified(
        df_input=df_con.drop(columns=["_auto_orig_idx"]),
        col_nit=col_nit,
        col_name=col_name,
        mode=mode,
        profile=profile_con_nit,
        output_dir=f"{output_dir}/con_nit",
        **kwargs,
    )
    corr_sin, conexiones_sin = deduplicate_unified(
        df_input=df_sin.drop(columns=["_auto_orig_idx"]),
        col_nit=col_nit,
        col_name=col_name,
        mode=mode,
        profile=profile_sin_nit,
        output_dir=f"{output_dir}/sin_nit",
        **kwargs,
    )

    # Mapear ORIGINAL_INDEX (local a cada subconjunto) de vuelta al global.
    con_orig = df_con["_auto_orig_idx"].to_numpy()
    sin_orig = df_sin["_auto_orig_idx"].to_numpy()
    corr_con = corr_con.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    corr_sin = corr_sin.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    corr_con["ORIGINAL_INDEX"] = con_orig
    corr_sin["ORIGINAL_INDEX"] = sin_orig
    corr_con["REGIMEN_AUTO"] = "CON_NIT"
    corr_sin["REGIMEN_AUTO"] = "SIN_NIT"

    # Garantizar IDs de grupo globalmente únicos: prefijo por régimen.
    # (los grupos SIN_NIT podrían colisionar numéricamente con los CON_NIT).
    corr_con["ID_GRUPO"] = "C" + corr_con["ID_GRUPO"].astype(str)
    corr_sin["ID_GRUPO"] = "S" + corr_sin["ID_GRUPO"].astype(str)

    combined = (
        pd.concat([corr_con, corr_sin], ignore_index=True)
        .sort_values("ORIGINAL_INDEX")
        .reset_index(drop=True)
    )

    stats = {
        "routed": "mixto",
        "n_con_nit": n_con,
        "n_sin_nit": n_sin,
        "n_grupos_con_nit": corr_con["ID_GRUPO"].nunique(),
        "n_grupos_sin_nit": corr_sin["ID_GRUPO"].nunique(),
        "profile_con_nit": profile_con_nit,
        "profile_sin_nit": profile_sin_nit,
        "stats_con_nit": _estadisticas_regimen(
            corr_con, conexiones_con, profile_con_nit, f"{output_dir}/con_nit"
        ),
        "stats_sin_nit": _estadisticas_regimen(
            corr_sin, conexiones_sin, profile_sin_nit, f"{output_dir}/sin_nit"
        ),
    }
    logger.info(
        "deduplicate_auto OK: %d grupos CON_NIT + %d grupos SIN_NIT",
        stats["n_grupos_con_nit"],
        stats["n_grupos_sin_nit"],
    )
    return combined, stats
