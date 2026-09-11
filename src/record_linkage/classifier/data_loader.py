"""
classifier.data_loader — record_linkage_pipeline

Componentes:
    - function cargar_datos_entrenamiento  (origen: notebook celda [98])
    - function cargar_archivo_bronce  (origen: notebook celda [98])
    - function preparar_crm  (origen: notebook celda [98])
    - function agregar_prefijo_cc  (origen: notebook celda [98])
    - function guardar_archivo_plata  (origen: notebook celda [98])
    - function procesar_fuente_completa  (origen: notebook celda [98])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pandas as pd

from ..exporters._spreadsheet import prepare_spreadsheet_data
from ..utils.output import safe_print as print
from .runner import ejecutar_proceso_clasificacion_directo


def cargar_datos_entrenamiento(
    ruta_empresas: str, ruta_personas: str, columna: str = "RAZON_SOCIAL"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Carga archivos de entrenamiento."""
    print("=" * 70)
    print("📥 CARGANDO DATOS DE ENTRENAMIENTO")
    print("=" * 70)

    print("📂 Cargando empresas...")
    df_empresas = pd.read_excel(ruta_empresas, engine="openpyxl")
    if columna not in df_empresas.columns:
        cols = [c for c in df_empresas.columns if "RAZON" in c.upper() or "NOMBRE" in c.upper()]
        if cols:
            df_empresas = df_empresas.rename(columns={cols[0]: columna})
    print(f"   ✅ {len(df_empresas):,} registros")

    print("📂 Cargando personas...")
    df_personas = pd.read_excel(ruta_personas, engine="openpyxl")
    if columna not in df_personas.columns:
        cols = [c for c in df_personas.columns if "RAZON" in c.upper() or "NOMBRE" in c.upper()]
        if cols:
            df_personas = df_personas.rename(columns={cols[0]: columna})
    print(f"   ✅ {len(df_personas):,} registros")

    return df_empresas, df_personas


def cargar_archivo_bronce(
    ruta: str,
    separador: str = ",",
    encoding: str = "utf-8",
    renombrar: dict | None = None,
    muestra: int | None = None,
) -> pd.DataFrame:
    """Carga archivo bronce."""
    print(f"\n📂 Cargando: {os.path.basename(ruta)}")

    params = {"sep": separador, "encoding": encoding}
    if muestra:
        params["nrows"] = muestra
    if ruta.endswith(".zip"):
        params["compression"] = "zip"

    df = pd.read_csv(ruta, **params)

    if renombrar:
        df = df.rename(columns=renombrar)

    print(f"   ✅ {len(df):,} registros, {len(df.columns)} columnas")
    return df


def preparar_crm(rutas_crm: list[dict], columnas: list[str] | None = None) -> pd.DataFrame:
    """Concatena y limpia archivos CRM."""
    if columnas is None:
        columnas = ["NIT", "RAZON_SOCIAL"]
    print("=" * 70)
    print("📥 PREPARANDO ARCHIVOS CRM")
    print("=" * 70)

    dfs = []
    for config in rutas_crm:
        try:
            df = cargar_archivo_bronce(
                ruta=config["archivo"],
                separador=config.get("separador", ","),
                encoding=config.get("encoding", "utf-8"),
                renombrar=config.get("renombrar"),
            )
            cols = [c for c in columnas if c in df.columns]
            dfs.append(df[cols])
        except Exception as e:
            print(f"   ❌ Error: {e}")

    df_crm = pd.concat(dfs, ignore_index=True)
    print(f"\n📊 Total concatenado: {len(df_crm):,}")

    # Corregir vacíos
    mask_vacio = df_crm["RAZON_SOCIAL"].isna() | (
        df_crm["RAZON_SOCIAL"].astype(str).str.strip() == ""
    )
    if mask_vacio.sum() > 0:
        df_crm.loc[mask_vacio, "RAZON_SOCIAL"] = df_crm.loc[mask_vacio, "NIT"].astype(str)

    # Eliminar duplicados
    n_antes = len(df_crm)
    df_crm = df_crm.drop_duplicates(subset=columnas, ignore_index=True)
    print(f"🧹 Duplicados eliminados: {n_antes - len(df_crm):,}")
    print(f"✅ CRM preparado: {len(df_crm):,} registros únicos")

    return df_crm


def agregar_prefijo_cc(
    df: pd.DataFrame, col_clasificacion: str = "clasificacion", col_nit: str = "NIT"
) -> pd.DataFrame:
    """Agrega prefijo CC a personas."""
    df = df.copy()
    mask = df[col_clasificacion].astype(str).str.lower().eq("persona")
    ya_cc = df[col_nit].astype(str).str.upper().str.startswith("CC")
    to_prefix = mask & ~ya_cc

    if to_prefix.sum() > 0:
        df.loc[to_prefix, col_nit] = "CC" + df.loc[to_prefix, col_nit].astype(str)
        print(f"✅ Prefijo CC agregado a {to_prefix.sum():,} registros")

    return df


def guardar_archivo_plata(
    df: pd.DataFrame,
    ruta: str,
    columnas: list[str] | None = None,
    separador: str = ",",
    encoding: str = "utf-8",
) -> str:
    """Guarda archivo procesado."""
    if columnas is None:
        columnas = ["NIT", "RAZON_SOCIAL"]
    print(f"💾 Guardando: {os.path.basename(ruta)}")
    os.makedirs(os.path.dirname(ruta), exist_ok=True)

    cols = [c for c in columnas if c in df.columns]
    prepare_spreadsheet_data(df[cols]).to_csv(
        ruta,
        sep=separador,
        encoding=encoding,
        index=False,
        compression="zip",
    )

    size_mb = os.path.getsize(ruta) / (1024**2)
    print(f"   ✅ {len(df):,} registros ({size_mb:.1f} MB)")
    return ruta


def procesar_fuente_completa(
    nombre_fuente: str,
    df_bronce: pd.DataFrame,
    df_empresas: pd.DataFrame,
    df_personas: pd.DataFrame,
    config: dict,
    ruta_salida: str,
    separador_salida: str = ",",
    encoding_salida: str = "utf-8",
    ruta_modelo: str | None = None,
    entrenar: bool = True,
) -> tuple[pd.DataFrame, Any]:
    """Procesa una fuente completa."""
    print("\n" + "█" * 70)
    print(f"█ PROCESANDO: {nombre_fuente}")
    print("█" * 70)

    inicio = time.time()
    n_entrada = len(df_bronce)
    print(f"📊 Registros de entrada: {n_entrada:,}")

    # Clasificar
    df_clasificado, modelo = ejecutar_proceso_clasificacion_directo(
        df_a_clasificar=df_bronce,
        columna_razon_social="RAZON_SOCIAL",
        realizar_entrenamiento=entrenar,
        config_modelo=config,
        df_entrenamiento_empresas=df_empresas,
        col_entrenamiento_empresas="RAZON_SOCIAL",
        df_entrenamiento_personas=df_personas,
        col_entrenamiento_personas="RAZON_SOCIAL",
        ruta_guardado_modelo=ruta_modelo,
    )

    # Verificar integridad
    if len(df_clasificado) != n_entrada:
        raise ValueError(f"❌ Error: entrada={n_entrada}, salida={len(df_clasificado)}")

    # Agregar CC
    print("\n🔧 Agregando prefijo CC...")
    df_con_cc = agregar_prefijo_cc(df_clasificado)

    # Guardar
    print()
    guardar_archivo_plata(
        df=df_con_cc, ruta=ruta_salida, separador=separador_salida, encoding=encoding_salida
    )

    tiempo = time.time() - inicio
    print(f"\n✅ {nombre_fuente} procesado en {tiempo:.1f}s")

    return df_con_cc[["NIT", "RAZON_SOCIAL"]].copy(), modelo
