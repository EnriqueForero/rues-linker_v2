"""
processing.utilities — record_linkage_pipeline

Componentes:
    - function preprocess_dataframe  (origen: notebook celda [116])
    - function remove_common_words_from_dataframe  (origen: notebook celda [116])
    - function consolidate_data_sources  (origen: notebook celda [116])
    - function create_blocking_keys  (origen: notebook celda [116])
    - function sample_data_for_testing  (origen: notebook celda [116])
    - function prepare_data_for_linkage  (origen: notebook celda [116])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import gc
import time
from typing import Any

import pandas as pd

from ..engine.similarity import BasicSimilarityCalculator, SimilarityCalculator
from ..utils.logger import setup_logger
from .nit import NitProcessor
from .text import TextProcessor
from .validator import DataValidator


def preprocess_dataframe(
    df: pd.DataFrame,
    text_processor: TextProcessor | None = None,
    nit_processor: NitProcessor | None = None,
    config: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """
    Preprocesar DataFrame completo para record linkage.

    Args:
        df: DataFrame a procesar
        text_processor: Instancia de TextProcessor (se crea una si no se proporciona)
        nit_processor: Instancia de NitProcessor (se crea una si no se proporciona)
        config: Configuración opcional

    Returns:
        DataFrame preprocesado con columnas adicionales
    """
    logger = setup_logger("preprocessing")
    logger.info(f"Iniciando preprocesamiento de {len(df):,} registros")

    # Crear procesadores si no se proporcionaron
    if text_processor is None:
        text_processor = TextProcessor(
            cleaning_mode=config.get("cleaning_mode", "BALANCEADO") if config else "BALANCEADO"
        )

    if nit_processor is None:
        nit_processor = NitProcessor(config)

    # Copiar DataFrame para no modificar el original
    df_processed = df.copy()

    # 1. Procesar nombres/razón social
    if "RAZON_SOCIAL" in df_processed.columns:
        logger.info("Procesando nombres...")
        df_processed["NOMBRE_LIMPIO"] = text_processor.process_series(
            df_processed["RAZON_SOCIAL"], column_name="RAZON_SOCIAL"
        )

    # 2. Procesar NITs
    if "NIT" in df_processed.columns:
        logger.info("Procesando NITs...")
        nit_results = nit_processor.process_series(df_processed["NIT"])

        # Agregar columnas de NIT
        df_processed["NIT_BASE"] = nit_results["NIT_BASE"]
        df_processed["NIT_OK"] = nit_results["NIT_OK"]
        df_processed["NIT_VALID"] = nit_results["IS_VALID"]
        df_processed["NIT_TYPE"] = nit_results["NIT_TYPE"]

    # 3. Generar claves fonéticas si hay SimilarityCalculator disponible
    if "NOMBRE_LIMPIO" in df_processed.columns:
        logger.info("Generando claves fonéticas...")
        try:
            # Intentar usar la versión completa si ya se ejecutó la celda correspondiente
            if "SimilarityCalculator" in globals():
                sim_calc = SimilarityCalculator()
            else:
                sim_calc = BasicSimilarityCalculator()
                logger.warning("Usando SimilarityCalculator básico")

            phonetic_keys = df_processed["NOMBRE_LIMPIO"].apply(sim_calc.phonetic_keys)
            df_processed["PHONETIC_KEY1"] = phonetic_keys.str[0]
            df_processed["PHONETIC_KEY2"] = phonetic_keys.str[1]
        except Exception as e:
            logger.warning(f"No se pudieron generar claves fonéticas: {e}")

    # 4. Agregar metadatos
    df_processed["PROCESSED_AT"] = pd.Timestamp.now()

    logger.info("Preprocesamiento completado")
    return df_processed


def remove_common_words_from_dataframe(
    df: pd.DataFrame, text_column: str = "NOMBRE_LIMPIO", top_n: int = 25
) -> pd.DataFrame:
    """
    Remover las palabras más comunes de una columna de texto.

    Args:
        df: DataFrame con la columna de texto
        text_column: Nombre de la columna a procesar
        top_n: Número de palabras comunes a remover

    Returns:
        DataFrame con columna procesada
    """
    if text_column not in df.columns:
        raise ValueError(f"Columna '{text_column}' no encontrada en DataFrame")

    logger = setup_logger("preprocessing")
    logger.info(f"Removiendo top {top_n} palabras comunes de '{text_column}'")

    # Crear procesador temporal
    text_processor = TextProcessor()

    # Procesar
    df_processed = df.copy()
    df_processed[f"{text_column}_FILTERED"] = text_processor.remove_common_words(
        df_processed[text_column], threshold=top_n
    )

    # Estadísticas
    empty_after = (df_processed[f"{text_column}_FILTERED"] == "").sum()
    if empty_after > 0:
        logger.warning(
            f"{empty_after:,} textos quedaron vacíos después de filtrar palabras comunes"
        )

    return df_processed


def consolidate_data_sources(
    sources_dict: dict[str, pd.DataFrame],
    column_mapping: dict[str, dict[str, str]] | None = None,
    add_source_column: bool = True,
) -> pd.DataFrame:
    """
    Consolidar múltiples fuentes de datos en un DataFrame único.

    VERSIÓN MEJORADA:
    - Asegura la creación de la columna 'ORIGINAL_INDEX' que es vital para la Fase 4.
    - El 'ORIGINAL_INDEX' corresponde a la posición de la fila en el DataFrame
      consolidado justo después de la unión de todas las fuentes.

    Args:
        sources_dict: Diccionario {nombre_fuente: DataFrame}
        column_mapping: Mapeo de columnas por fuente
        add_source_column: Si agregar columna 'SRC' con nombre de fuente

    Returns:
        DataFrame consolidado
    """
    logger = setup_logger("preprocessing")
    logger.info(f"Consolidando {len(sources_dict)} fuentes de datos")

    consolidated_dfs = []

    for source_name, df in sources_dict.items():
        df_temp = df.copy()

        # Aplicar mapeo de columnas si existe
        if column_mapping and source_name in column_mapping:
            mapping = column_mapping[source_name]
            # Usar 'get' para evitar errores si una columna no existe en el mapeo
            safe_mapping = {k: v for k, v in mapping.items() if k in df_temp.columns}
            df_temp = df_temp.rename(columns=safe_mapping)
            logger.debug(f"{source_name}: Columnas mapeadas según configuración")

        # Agregar columna de fuente
        if add_source_column:
            df_temp["SRC"] = source_name

        consolidated_dfs.append(df_temp)
        logger.info(f"{source_name}: {len(df_temp):,} registros")

    # Consolidar sin ignorar el índice
    df_consolidated = pd.concat(consolidated_dfs, sort=False)

    # CORRECCIÓN CLAVE: Crear la columna 'ORIGINAL_INDEX' a partir del índice del DataFrame consolidado.
    # Usamos reset_index() para convertir el índice en una columna.
    df_consolidated.reset_index(
        drop=True, inplace=True
    )  # Primero, asegurar un índice limpio de 0 a N-1
    df_consolidated["ORIGINAL_INDEX"] = df_consolidated.index

    logger.info(f"Total consolidado: {len(df_consolidated):,} registros")
    logger.info("Columna 'ORIGINAL_INDEX' creada exitosamente.")

    return df_consolidated


def create_blocking_keys(df: pd.DataFrame, strategies: list[str] | None = None) -> pd.DataFrame:
    """
    Crear claves de bloqueo para optimizar la búsqueda de candidatos.

    Args:
        df: DataFrame con datos preprocesados
        strategies: Lista de estrategias de bloqueo a aplicar

    Returns:
        DataFrame con columnas de claves de bloqueo adicionales
    """
    if strategies is None:
        strategies = ["first_3_chars", "first_word"]
    logger = setup_logger("preprocessing")
    logger.info(f"Creando claves de bloqueo con estrategias: {strategies}")

    df_with_keys = df.copy()

    # Estrategia: Primeros 3 caracteres del nombre limpio
    if "first_3_chars" in strategies and "NOMBRE_LIMPIO" in df.columns:
        df_with_keys["BLOCK_FIRST3"] = df["NOMBRE_LIMPIO"].str[:3]

    # Estrategia: Primera palabra del nombre
    if "first_word" in strategies and "NOMBRE_LIMPIO" in df.columns:
        df_with_keys["BLOCK_FIRSTWORD"] = df["NOMBRE_LIMPIO"].str.split().str[0]

    # Estrategia: Primeros 3 dígitos del NIT
    if "nit_prefix" in strategies and "NIT_BASE" in df.columns:
        df_with_keys["BLOCK_NIT3"] = df["NIT_BASE"].str[:3]

    # Estrategia: Clave fonética
    if "phonetic" in strategies and "PHONETIC_KEY1" in df.columns:
        df_with_keys["BLOCK_PHONETIC"] = df["PHONETIC_KEY1"]

    # Estrategia: Combinación nombre + NIT
    if "combined" in strategies and "NOMBRE_LIMPIO" in df.columns and "NIT_BASE" in df.columns:
        df_with_keys["BLOCK_COMBINED"] = df["NOMBRE_LIMPIO"].str[:2].fillna("") + df[
            "NIT_BASE"
        ].str[:2].fillna("")

    keys_created = [col for col in df_with_keys.columns if col.startswith("BLOCK_")]
    logger.info(f"Claves de bloqueo creadas: {keys_created}")

    return df_with_keys


def sample_data_for_testing(
    df: pd.DataFrame,
    sample_size: int | float = 10000,
    stratify_by: str | None = "SRC",
    random_state: int = 42,
) -> pd.DataFrame:
    """
    Crear muestra estratificada para testing.

    Args:
        df: DataFrame completo
        sample_size: Tamaño de la muestra (int) o fracción (float)
        stratify_by: Columna para estratificar
        random_state: Semilla para reproducibilidad

    Returns:
        DataFrame muestreado
    """
    logger = setup_logger("preprocessing")

    # Determinar tamaño de muestra
    if isinstance(sample_size, float) and sample_size <= 1.0:
        n_samples = int(len(df) * sample_size)
    else:
        n_samples = min(int(sample_size), len(df))

    logger.info(f"Creando muestra de {n_samples:,} registros de {len(df):,} totales")

    # Muestreo estratificado si se especifica
    if stratify_by and stratify_by in df.columns:
        # Calcular proporción por estrato
        strata_counts = df[stratify_by].value_counts()

        sampled_dfs = []
        for stratum, count in strata_counts.items():
            # Proporción de este estrato
            prop = count / len(df)
            stratum_samples = max(1, int(n_samples * prop))

            # Muestrear del estrato
            stratum_df = df[df[stratify_by] == stratum]
            if len(stratum_df) <= stratum_samples:
                sampled_dfs.append(stratum_df)
            else:
                sampled_dfs.append(stratum_df.sample(n=stratum_samples, random_state=random_state))

        sample_df = pd.concat(sampled_dfs, ignore_index=True)

        # Ajustar si nos pasamos del tamaño deseado
        if len(sample_df) > n_samples:
            sample_df = sample_df.sample(n=n_samples, random_state=random_state)

    else:
        # Muestreo simple
        sample_df = df.sample(n=n_samples, random_state=random_state)

    logger.info(f"Muestra creada con {len(sample_df):,} registros")

    # Verificar distribución si se estratificó
    if stratify_by and stratify_by in df.columns:
        logger.info(f"Distribución en muestra por {stratify_by}:")
        for value, count in sample_df[stratify_by].value_counts().items():
            pct = count / len(sample_df) * 100
            logger.info(f"  {value}: {count:,} ({pct:.1f}%)")

    return sample_df


def prepare_data_for_linkage(
    df: pd.DataFrame, config: dict[str, Any] | None = None
) -> pd.DataFrame:
    """
    Función wrapper que ejecuta todo el preprocesamiento necesario para record linkage.

    Args:
        df: DataFrame crudo
        config: Configuración completa del sistema

    Returns:
        DataFrame completamente preprocesado y listo para linkage
    """
    logger = setup_logger("preprocessing")
    logger.info("=== PREPARACIÓN COMPLETA DE DATOS PARA LINKAGE ===")

    start_time = time.time()

    # 1. Validación inicial
    validator = DataValidator(config)
    df_validated, _quality_report = validator.run_quality_checks(
        df,
        source_name="input",
        fix_issues=True,  # Aplicar correcciones automáticas
    )

    # 2. Preprocesamiento
    text_processor = TextProcessor(
        cleaning_mode=config.get("cleaning_mode", "BALANCEADO") if config else "BALANCEADO"
    )
    nit_processor = NitProcessor(config)

    df_preprocessed = preprocess_dataframe(
        df_validated, text_processor=text_processor, nit_processor=nit_processor, config=config
    )

    # 3. Remover palabras comunes si está configurado
    if config and "remove_top_words" in config:
        top_words = config["remove_top_words"]
        if top_words > 0 and "NOMBRE_LIMPIO" in df_preprocessed.columns:
            df_preprocessed["NOMBRE_LIMPIO"] = text_processor.remove_common_words(
                df_preprocessed["NOMBRE_LIMPIO"], threshold=top_words
            )

    # 4. Crear claves de bloqueo
    df_preprocessed = create_blocking_keys(
        df_preprocessed, strategies=["first_3_chars", "first_word", "nit_prefix"]
    )

    # 5. Estadísticas finales
    elapsed_time = time.time() - start_time
    logger.info(f"Preparación completada en {elapsed_time:.1f} segundos")
    logger.info(f"Registros finales: {len(df_preprocessed):,}")
    logger.info(f"Columnas agregadas: {set(df_preprocessed.columns) - set(df.columns)}")

    # Liberar memoria
    del df_validated
    gc.collect()

    return df_preprocessed
