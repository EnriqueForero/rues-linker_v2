"""
deduplication.dataframe — record_linkage_pipeline

Componentes:
    - function deduplicate_dataframe  (origen: notebook celda [154])
    - function analyze_duplicates  (origen: notebook celda [154])
    - function deduplicate_file  (origen: notebook celda [154])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from ..processing.text import EnhancedTextProcessor
from ..utils.output import safe_print as print
from .unified import deduplicate_unified


def deduplicate_dataframe(
    df: pd.DataFrame,
    nit_column: str = "NIT",
    name_column: str = "RAZON_SOCIAL",
    mode: str = "auto",
    output_folder: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Interfaz simplificada para deduplicar un DataFrame – VERSIÓN CORREGIDA.

    ▸ Detecta automáticamente un perfil óptimo según el tamaño del DataFrame
    ▸ Ajusta el modo de limpieza si se solicita 'auto'
    ▸ Garantiza que los perfiles se ejecuten siempre con cross_source_only=False
    ▸ Ejecuta la deduplicación mediante `deduplicate_unified`

    Args
    ----
    df : pd.DataFrame
        DataFrame con los registros a deduplicar.
    nit_column : str, default 'NIT'
        Nombre de la columna con identificadores NIT.
    name_column : str, default 'RAZON_SOCIAL'
        Nombre de la columna con la razón social o nombre de la empresa.
    mode : str, default 'auto'
        Modo de limpieza (‘auto’, ‘CONSERVADOR’, ‘BALANCEADO’, ‘AGRESIVO’).
    output_folder : str | None
        Carpeta donde se almacenarán los resultados.
        Si es None, se crea automáticamente con marca de tiempo.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        (tabla_correlativa, conexiones_no_triviales)
    """

    # ──────────────────────────────────────────────────────────────
    # 1) Auto-detección del modo de limpieza (cuando mode == 'auto')
    # ──────────────────────────────────────────────────────────────
    if mode == "auto":
        avg_name_length = df[name_column].str.len().mean()
        has_many_suffixes = (
            df[name_column].str.contains(r"(SAS|LTDA|SA|EU)", case=False, na=False).sum()
            > len(df) * 0.30
        )

        if avg_name_length > 50 or has_many_suffixes:
            mode = "AGRESIVO"
        elif avg_name_length > 30:
            mode = "BALANCEADO"
        else:
            mode = "CONSERVADOR"

    # ──────────────────────────────────────────────────────────────
    # 2) Auto-detección del perfil de deduplicación según el tamaño
    # ──────────────────────────────────────────────────────────────
    n_records = len(df)
    if n_records > 3_000_000:
        profile = "deduplication_colab_3M"
    elif n_records > 1_000_000:
        profile = "deduplication_colab_1M"
    else:
        profile = "deduplication_standard"

    # ──────────────────────────────────────────────────────────────
    # 3) Carpeta de salida
    # ──────────────────────────────────────────────────────────────
    if output_folder is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_folder = f"deduplicacion_{timestamp}"

    # ──────────────────────────────────────────────────────────────
    # 5) Logging de inicio
    # ──────────────────────────────────────────────────────────────
    print("🔄 Iniciando deduplicación")
    print(f"   • Registros: {n_records:,}")
    print(f"   • Modo de limpieza: {mode}")
    print(f"   • Perfil de linkage: {profile}")
    print(f"   • Carpeta de salida: {output_folder}")

    # ──────────────────────────────────────────────────────────────
    # 6) Ejecución de la deduplicación
    # ──────────────────────────────────────────────────────────────
    return deduplicate_unified(
        df,
        col_nit=nit_column,
        col_name=name_column,
        mode=mode,
        profile=profile,
        output_dir=output_folder,
        validate_against_legacy=False,  # ejecución más rápida
    )


def analyze_duplicates(
    df: pd.DataFrame, nit_column: str = "NIT", name_column: str = "RAZON_SOCIAL"
) -> pd.DataFrame:
    """
    Analizar potenciales duplicados en un DataFrame sin ejecutar deduplicación.

    Útil para evaluar la calidad de los datos antes de procesar.

    Returns:
        DataFrame con estadísticas de duplicación
    """
    stats = []

    # Duplicados exactos
    exact_dups = df.duplicated(subset=[nit_column, name_column], keep=False).sum()
    stats.append(
        {
            "Tipo": "Duplicados exactos (NIT + Nombre)",
            "Cantidad": exact_dups,
            "Porcentaje": f"{exact_dups / len(df) * 100:.1f}%",
        }
    )

    # Duplicados por NIT
    nit_dups = df[nit_column].value_counts()
    nit_dups_count = (nit_dups > 1).sum()
    stats.append(
        {
            "Tipo": "NITs con múltiples registros",
            "Cantidad": nit_dups_count,
            "Porcentaje": f"{nit_dups_count / df[nit_column].nunique() * 100:.1f}%",
        }
    )

    # Duplicados por nombre (aproximado)
    name_processor = EnhancedTextProcessor()
    df["_nombre_limpio"] = df[name_column].apply(name_processor.enhanced_clean_name)
    name_dups = df["_nombre_limpio"].value_counts()
    name_dups_count = (name_dups > 1).sum()
    stats.append(
        {
            "Tipo": "Nombres similares (limpieza básica)",
            "Cantidad": name_dups_count,
            "Porcentaje": f"{name_dups_count / df['_nombre_limpio'].nunique() * 100:.1f}%",
        }
    )

    # Limpiar columna temporal
    df.drop("_nombre_limpio", axis=1, inplace=True)

    return pd.DataFrame(stats)


def deduplicate_file(
    path: str | pd.DataFrame,
    sheet: int | str | None = 0,
    col_name: str = "RAZON_SOCIAL",
    col_nit: str = "NIT",
    **kwargs,
) -> tuple[pd.DataFrame, list[set[int]]]:
    """
    Función de compatibilidad con la interfaz original deduplicate_file.

    Mantiene la misma firma pero usa el sistema unificado internamente.
    """
    # Cargar datos si es necesario
    if isinstance(path, str):
        if path.endswith(".csv"):
            df = pd.read_csv(path, **kwargs.get("read_kwargs", {}))
        elif path.endswith((".xlsx", ".xls")):
            df = pd.read_excel(path, sheet_name=sheet, **kwargs.get("read_kwargs", {}))
        else:
            raise ValueError(f"Formato no soportado: {path}")
    else:
        df = path.copy()

    # Extraer parámetros del kwargs
    mode = kwargs.get("cleaning_mode", "BALANCEADO")
    export_xlsx = kwargs.get("export_xlsx")

    # Ejecutar deduplicación unificada
    correlativa, _conexiones = deduplicate_unified(
        df,
        col_nit=col_nit,
        col_name=col_name,
        mode=mode,
        output_dir=export_xlsx if export_xlsx else "resultados_dedup",
    )

    # Convertir a formato de clusters para compatibilidad
    clusters = []
    for group_id in correlativa["ID_GRUPO"].unique():
        group_indices = correlativa[correlativa["ID_GRUPO"] == group_id].index
        clusters.append(set(group_indices))

    return correlativa, clusters
