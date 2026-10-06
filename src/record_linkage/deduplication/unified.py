"""
deduplication.unified — record_linkage_pipeline

Componentes:
    - function deduplicate_unified  (origen: notebook celda [153])
    - function _build_deduplication_config  (origen: notebook celda [153])
    - function _prepare_for_deduplication  (origen: notebook celda [153])
    - function _generate_non_trivial_connections  (origen: notebook celda [153])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..exporters.smart import SmartExporter
from ..pipeline._internal import DEDUPLICATION_PROFILES
from ..pipeline.errores import mensaje_accionable
from ..pipeline.linkage_pipeline import RecordLinkagePipeline
from ..processing.nit import AdvancedNitProcessor
from ..processing.text import EnhancedTextProcessor
from ..utils.logger import setup_logger

MOTORES_LSH = ("default", "disk_based")


@dataclass(frozen=True)
class AjustesDeduplicacion:
    """Ajustes puntuales sobre la configuración que arma ``deduplicate_unified``.

    F2.9: los scripts de calibración (``medir_con_ground_truth``,
    ``replicar_v2_8_0``) y la prueba del camino en disco construían
    ``RecordLinkagePipeline`` a mano solo para forzar el motor o una perilla
    del perfil. Esta clase es esa perilla, en la función que se conserva.

    Attributes:
        motor: ``"default"`` (en memoria) o ``"disk_based"``; ``None`` deja
            la decisión por tamaño (> 1M registros → disco).
        perfil: claves que sobreescriben el perfil activo después de que
            ``_build_deduplication_config`` lo eligió (p. ej.
            ``{"score_threshold": 0.0}``). Se aplican antes de la preparación,
            así que ``remove_top_words`` también cuenta.
    """

    motor: str | None = None
    perfil: dict[str, Any] = field(default_factory=dict)

    def aplicar(self, config: dict[str, Any]) -> None:
        """Escribe los ajustes en ``config`` (en el sitio). Falla si el motor no existe."""
        if self.motor is not None:
            if self.motor not in MOTORES_LSH:
                raise ValueError(
                    mensaje_accionable(
                        f"AjustesDeduplicacion.motor = {self.motor!r} no es un motor LSH.",
                        "un motor desconocido se ignoraría o fallaría a mitad de L2 sin "
                        "decir por qué.",
                        f"use uno de {list(MOTORES_LSH)} o deje motor=None para que se "
                        "decida por tamaño.",
                    )
                )
            config["linkage_engine_class"] = self.motor
        if self.perfil:
            config["profiles"][config["profile"]].update(self.perfil)


def deduplicate_unified(
    df_input: pd.DataFrame,
    col_nit: str = "NIT",
    col_name: str = "RAZON_SOCIAL",
    mode: str = "BALANCEADO",
    profile: str = "deduplication_standard",
    output_dir: str = "resultados_deduplicacion",
    validate_against_legacy: bool = False,
    extra_features: list[dict[str, Any]] | None = None,
    ajustes: AjustesDeduplicacion | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Función principal unificada que encapsula toda la complejidad
    del pipeline de deduplicación.

    ⚠️  ADVERTENCIA CRÍTICA (auditoría v2.15.0)
    ────────────────────────────────────────────────────────────
    NO USAR esta función sobre datasets que mezclan registros provenientes
    de fuentes distintas, en particular cuando hay mezcla de regímenes
    CON_NIT y SIN_NIT. En ese escenario, usar `Orchestrator` con
    `trusted_sources` declarado.

    Evidencia medida sobre el ground truth grande (12.427 registros,
    CON_NIT + SIN_NIT mezclados), profile produccion_calibrada, v0.7.4:

        deduplicate_unified (este método):   F1 global = 0.563
            - CON_NIT: F1 = 0.961 (P=0.972, R=0.950)  ← confiable
            - SIN_NIT: F1 = 0.217 (P=0.123, R=0.921)  ← SOBRE-FUSIÓN
        Orchestrator + produccion_calibrada: F1 global = 0.842
            - CON_NIT: F1 = 0.960 (P=1.000, R=0.922)
            - SIN_NIT: F1 = 0.000 (P=0.000, R=0.000)  ← NO FUSIONA NADA

    Conclusión honesta: NINGUNA de las dos configuraciones resuelve SIN_NIT.
    deduplicate_unified sobre-fusiona (precision 0.12); el perfil calibrado
    con nit_empty_passes_filter=False no fusiona nada sin NIT (recall 0).
    El régimen SIN_NIT (importadores extranjeros sin identificador estable,
    nombres y ciudades inconsistentes) es deuda técnica conocida sin fix por
    parámetros — ver docs/DEUDA_SIN_NIT.md. Para CON_NIT ambos caminos rinden
    F1≈0.96; Orchestrator tiene precision perfecta. Usar Orchestrator con
    trusted_sources para datos multi-fuente con NIT.
    ────────────────────────────────────────────────────────────

    VERSIÓN FINAL:
    - Ya no depende de 'df_linked' después de la ejecución del pipeline.
    - Utiliza directamente 'tabla_correlativa' como el resultado principal.
    - Se ha simplificado la lógica post-procesamiento.

    Args:
        df_input: DataFrame de entrada. Debe contener al menos ``col_nit`` y
            ``col_name``. Las columnas adicionales (CIUDAD, TELEFONO, ...) se
            preservan a través del pipeline y pueden usarse vía ``extra_features``.
        col_nit: nombre de la columna de NIT. Default "NIT".
        col_name: nombre de la columna de razón social. Default "RAZON_SOCIAL".
        mode: modo de limpieza ("BALANCEADO", "CONSERVADOR", "AGRESIVO").
        profile: perfil de deduplicación. Default "deduplication_standard".
        output_dir: directorio de salida.
        validate_against_legacy: si True, valida contra el motor legacy.
        extra_features: lista opcional de variables adicionales a incorporar al
            scoring. Cada elemento es un dict con claves:
            ``{"column": str, "weight": float, "type": str}``. Los tipos
            firmados (``categorical_signed``, ``exact_signed``,
            ``token_set_ratio_signed``) PENALIZAN discrepancias —son los que
            permiten separar empresas distintas con NIT/nombre parecidos pero,
            p.ej., ciudad distinta. Ejemplo::

                extra_features=[
                    {"column": "CIUDAD", "weight": 0.15, "type": "categorical_signed"},
                    {"column": "TELEFONO", "weight": 0.10, "type": "exact_signed"},
                ]

            Si es None o lista vacía, el comportamiento es idéntico a v2.6.0
            (paridad bit-a-bit). Default None.
        ajustes: ``AjustesDeduplicacion`` para forzar el motor LSH o
            sobreescribir perillas del perfil activo (F2.9). ``None`` = sin
            ajustes, comportamiento idéntico.

    Returns:
        Tupla ``(tabla_correlativa, estadisticas)``.

    Raises:
        ValueError: si el DataFrame está vacío o faltan columnas requeridas.
        ValueError: si alguna columna referida en ``extra_features`` no existe
            en ``df_input`` (fail-fast: mejor un error claro que un feature
            silenciosamente inerte).
    """
    logger = setup_logger("deduplicate_unified")
    start_time = time.time()
    logger.info(f"Iniciando deduplicación unificada: {len(df_input):,} registros")

    # 1. Validaciones y Configuración (sin cambios)
    if df_input.empty:
        raise ValueError("DataFrame de entrada está vacío")
    if col_nit not in df_input.columns or col_name not in df_input.columns:
        raise ValueError(f"Columnas requeridas {col_nit}, {col_name} no encontradas")

    # v0.7.4 (cierre de deuda): advertir sobre uso en mezcla CON_NIT/SIN_NIT.
    # Evidencia medida: en datos mixtos este método da F1 global 0.563 por
    # sobre-fusión del régimen SIN_NIT (ver docs/DEUDA_SIN_NIT.md). El docstring
    # ya lo advierte, pero una advertencia en runtime es más difícil de ignorar.
    # Heurística: si una fracción significativa de filas tiene NIT vacío Y otra
    # fracción significativa lo tiene presente, es una mezcla de regímenes.
    _nit_col = df_input[col_nit].astype(str).str.strip()
    _empty_frac = (_nit_col.isin(["", "nan", "None", "<NA>"]) | df_input[col_nit].isna()).mean()
    if 0.05 < _empty_frac < 0.95:
        import warnings

        warnings.warn(
            f"deduplicate_unified detectó mezcla de regímenes: {_empty_frac:.0%} de "
            f"las filas tienen NIT vacío y el resto lo tienen presente. En datos "
            f"mixtos CON_NIT/SIN_NIT este método sobre-fusiona el régimen sin NIT "
            f"(F1 global ~0.56 medido). Para multi-fuente con NIT, considera "
            f"Orchestrator con trusted_sources. Ver docs/DEUDA_SIN_NIT.md.",
            UserWarning,
            stacklevel=2,
        )

    # 1b. Validación fail-fast de extra_features (v2.7.0).
    # Mejor un error explícito ahora que un feature silenciosamente inerte
    # (que es justo el modo de falla que escondió el bug en v2.6.1.dev0).
    extra_features = extra_features or []
    if extra_features:
        _validate_extra_features(extra_features, df_input.columns)

    config = _build_deduplication_config(profile, mode, len(df_input))
    if ajustes is not None:
        ajustes.aplicar(config)

    # Inyectar extra_features en el perfil activo para que el VectorizedScorer
    # las lea. El scorer busca profile["extra_features"]; lo propagamos al
    # perfil base que _build_deduplication_config eligió por tamaño.
    if extra_features:
        active = config["profile"]
        config["profiles"][active]["extra_features"] = extra_features

    # 2. Preparación del DataFrame.
    # v2.10.0 (Fix #2): lee `remove_top_words` del perfil activo para
    # eliminar palabras genéricas frecuentes (INVERSIONES, SAS, GRUPO).
    active_profile = config["profiles"][config["profile"]]
    _top_words = int(active_profile.get("remove_top_words", 0))
    df_prepared = _prepare_for_deduplication(
        df_input, col_nit, col_name, mode, remove_top_words=_top_words
    )

    # v2.10.0 (Fix #2): poblar `_generic_tokens` con el top-N de palabras
    # del corpus + una lista CURADA de genéricos conocidos del dominio
    # RUES/empresarial colombiano. La lista curada cubre el caso de
    # corpora pequeños donde "INVERSIONES" o "GRUPO" no entran al top-N
    # estadístico pero claramente son genéricos.
    _generic_top_n = int(active_profile.get("generic_name_top_n", 30))
    if active_profile.get("generic_name_penalty", 0.0) > 0.0:
        from record_linkage.processing.text import EnhancedTextProcessor

        # Stop-words curadas del dominio empresarial colombiano. Estas
        # NUNCA discriminan identidad por sí solas.
        # Ampliable en el perfil vía `generic_name_extra_tokens`.
        _generic_curated = {
            "INVERSIONES",
            "GRUPO",
            "HOLDING",
            "COMPAÑIA",
            "COMPANIA",
            "COMPANY",
            "EMPRESA",
            "EMPRESAS",
            "SOCIEDAD",
            "SOCIEDADES",
            "SAS",
            "SA",
            "LTDA",
            "LIMITADA",
            "EU",
            "SC",
            "ESAL",
            "SCA",
            "CIA",
            "CO",
            "CORP",
            "LLC",
            "LLP",
            "INC",
            "PLC",
            "COLOMBIA",
            "COLOMBIANA",
            "COLOMBIANO",
            "ANDINA",
            "BOGOTA",
            "MEDELLIN",
            "CALI",
            "BARRANQUILLA",
            "CONSULTORES",
            "CONSULTORIA",
            "ASESORES",
            "ASESORIA",
            "SERVICIOS",
            "COMERCIAL",
            "COMERCIALIZADORA",
            "INTERNACIONAL",
            "GLOBAL",
            "NACIONAL",
            "REGIONAL",
            "PRODUCTOS",
            "PRODUCCIONES",
            "FABRICA",
            "FABRICACION",
            "DISTRIBUCIONES",
            "DISTRIBUIDORA",
            "CONSTRUCCIONES",
            "CONSTRUCCION",
            "CONSTRUCTORA",
            "ZONA",
            "FRANCA",
            "ZF",
            "EN",
            "LIQUIDACION",
            "REORGANIZACION",
        }
        # Permite al usuario añadir más vía perfil.
        extras = active_profile.get("generic_name_extra_tokens", [])
        _generic_curated.update(str(t).upper() for t in extras)

        _generic_set = set(_generic_curated)
        if _generic_top_n > 0:
            try:
                _tp = EnhancedTextProcessor(mode)
                _freq = _tp.extract_words_frequency(
                    df_prepared["NOMBRE_LIMPIO"], top_n=_generic_top_n
                )
                _generic_set.update(_freq["palabra"].astype(str).str.upper().tolist())
            except Exception:
                pass
        active_profile["_generic_tokens"] = _generic_set

    # v2.12.0 (Fix #3): poblar `_token_idf` con el IDF de cada token del
    # corpus. IDF(t) = log(N / df(t)), donde df(t) = nº de nombres que
    # contienen t. Tokens muy frecuentes (CO, LTD, ELITE, CORPORATION)
    # tienen IDF ≈ 0; tokens raros (NENOVA, ARES3) tienen IDF alto. El
    # scorer usa esto para atenuar coincidencias por tokens genéricos.
    # No-op si `idf_weight_blend == 0.0`.
    if active_profile.get("idf_weight_blend", 0.0) > 0.0:
        import math
        import re
        from collections import Counter

        nombres = df_prepared["NOMBRE_LIMPIO"].astype(str)
        n_docs = max(1, len(nombres))
        doc_freq: Counter[str] = Counter()
        for nm in nombres:
            toks = {t for t in re.split(r"[^A-Z0-9]+", nm.upper()) if len(t) >= 2}
            doc_freq.update(toks)
        token_idf = {t: math.log(n_docs / df) for t, df in doc_freq.items() if df > 0}
        # IDF por defecto para tokens no vistos: el de un token que aparece
        # una sola vez (máximo IDF observado), para no penalizar rarezas.
        active_profile["_token_idf"] = token_idf
        active_profile["idf_default"] = math.log(n_docs / 1)

    # 3. Crear y ejecutar pipeline.
    # NOTA (v2.0.1): No pasamos `keep_intermediate_results=True`. El pipeline
    # ahora retorna un PipelineResult con carga lazy desde checkpoints parquet,
    # así que la tabla correlativa siempre está disponible vía `result.get(...)`
    # sin requerir que el pipeline retenga DataFrames en memoria.
    # F2.9: construcción interna, sin el DeprecationWarning que sí recibe quien
    # instancia RecordLinkagePipeline por su cuenta.
    pipeline = RecordLinkagePipeline(config, profile=profile, _uso_interno=True)
    result = pipeline.run(
        sources={"DEDUP_SOURCE": df_prepared},
        output_dir=output_dir,
        source_priority=["DEDUP_SOURCE"],
        validate_data=False,  # La validación ya se hace dentro, simplificar aquí
        generate_visualizations=False,
        show_progress=False,
        # El pipeline heredado carece de manifiesto/huella para sus
        # checkpoints. Reutilizarlos por mera existencia puede devolver datos
        # de una ejecución anterior cuando se repite output_dir.
        force_rerun=True,
    )

    # 4. Extraer y procesar resultados via el contrato dict-like de
    # PipelineResult (los DataFrames se leen lazy desde parquet).
    correlativa_df = result.get("correlative_table")
    if correlativa_df is None or correlativa_df.empty:
        raise RuntimeError(
            "El pipeline no generó la tabla correlativa. El proceso falló internamente."
        )

    # 5. Generar conexiones no triviales a partir de la tabla final
    conexiones_no_triviales = _generate_non_trivial_connections(correlativa_df)

    # 6. Exportación automática (sin cambios)
    exporter = SmartExporter(config)
    correlativa_path = exporter.export(correlativa_df, "correlativa_unificada", format="parquet")
    conexiones_path = exporter.export(
        conexiones_no_triviales, "conexiones_no_triviales", format="parquet"
    )

    elapsed_time = time.time() - start_time
    logger.info(
        f"Deduplicación completada en {elapsed_time:.1f}s. "
        f"Archivos guardados: {os.path.basename(correlativa_path)}, {os.path.basename(conexiones_path)}"
    )

    return correlativa_df, conexiones_no_triviales


def _tipos_de_comparador_validos() -> frozenset[str]:
    """Tipos de comparación admitidos, leídos del registro único.

    v0.18.0 — antes esta lista estaba escrita a mano aquí, y el registro de
    comparadores la repetía. Dos listas que deben coincidir terminan sin
    coincidir: registrar un comparador nuevo lo dejaba funcionando en el
    scorer pero rechazado por esta validación. La única lista real es el
    registro.
    """
    from ..matching.comparadores_extra import tipos_disponibles

    return frozenset(tipos_disponibles())


def _validate_extra_features(extra_features: list[dict[str, Any]], columns: pd.Index) -> None:
    """Valida la especificación de variables adicionales (fail-fast).

    Args:
        extra_features: lista de dicts ``{"column", "weight", "type"}``.
        columns: columnas disponibles en el DataFrame de entrada.

    Raises:
        ValueError: si un elemento no es dict, le falta "column", la columna no
            existe, el peso no es numérico positivo, o el tipo es desconocido.
    """
    available = set(columns)
    for i, feat in enumerate(extra_features):
        if not isinstance(feat, dict):
            raise ValueError(f"extra_features[{i}] debe ser un dict, no {type(feat).__name__}.")
        col = feat.get("column")
        if not col:
            raise ValueError(f"extra_features[{i}] no tiene 'column'.")
        if col not in available:
            raise ValueError(
                f"extra_features[{i}]: la columna '{col}' no existe en el "
                f"DataFrame. Columnas disponibles: {sorted(available)}."
            )
        weight = feat.get("weight", 0.0)
        if not isinstance(weight, int | float) or weight <= 0.0:
            raise ValueError(
                f"extra_features[{i}] ('{col}'): 'weight' debe ser un número > 0, "
                f"recibido {weight!r}."
            )
        ftype = feat.get("type", "exact_or_zero")
        validos = _tipos_de_comparador_validos()
        if ftype not in validos:
            raise ValueError(
                f"extra_features[{i}] ('{col}'): tipo '{ftype}' desconocido. "
                f"Válidos: {sorted(validos)}."
            )


def _build_deduplication_config(profile: str, mode: str, n_records: int) -> dict[str, Any]:
    """Construir configuración específica para deduplicación.

    Selección de perfil:
        - Si el usuario pasa un perfil EXPLÍCITO distinto de
          ``deduplication_standard``, se respeta tal cual a cualquier escala.
          Esto es clave para producción: un perfil calibrado a mano (p. ej.
          ``deduplication_sin_nit_conservador``) NO debe ser sobrescrito por
          la autoselección por tamaño.
        - Si el usuario deja el default (``deduplication_standard``), se
          autoselecciona ``colab_1M`` / ``colab_3M`` según el tamaño.

    El motor on-disk se decide SIEMPRE por tamaño (> 1M registros), con
    independencia del perfil, porque es una decisión de memoria, no de
    calibración.
    """
    # Selección de perfil base
    if profile != "deduplication_standard":
        # Perfil explícito del usuario: se respeta a cualquier escala.
        base_profile = profile
    elif n_records > 3_000_000:
        base_profile = "deduplication_colab_3M"
    elif n_records > 1_000_000:
        base_profile = "deduplication_colab_1M"
    else:
        base_profile = profile

    # Configuración base
    config = {
        "profile": base_profile,
        "profiles": {base_profile: DEDUPLICATION_PROFILES[base_profile].copy()},
        "cleaning_mode": mode,
        "output_directory": "resultados_deduplicacion",
        # El motor on-disk se decide por tamaño, no por perfil (memoria).
        "linkage_engine_class": "disk_based" if n_records > 1_000_000 else "default",
        "cross_source_only": False,  # Crítico para deduplicación
        # Validación más permisiva para deduplicación
        "validation_rules": {
            "min_nit_length": 6,
            "max_nit_length": 15,
            "min_name_length": 3,
            "max_name_length": 500,
            "remove_test_data": True,
            "remove_invalid_nits": False,
        },
    }

    return config


def _prepare_for_deduplication(
    df: pd.DataFrame,
    col_nit: str,
    col_name: str,
    mode: str,
    remove_top_words: int = 0,
) -> pd.DataFrame:
    """Preparar DataFrame específicamente para deduplicación.

    v2.10.0 (Fix #2): se añadió el parámetro `remove_top_words` para que
    el camino de deduplicación elimine las N palabras más frecuentes del
    corpus antes del scoring. Esto ataca el modo de falla "nombre
    genérico" (tres 'INVERSIONES SAS' distintas se fusionan) eliminando
    los tokens que NO discriminan (INVERSIONES, SAS, GRUPO, COLOMBIA,
    LTDA, ...). Default 0 = no-op (paridad v2.9.0).

    Implementa "IDF simplificado" usando la frecuencia de tokens en este
    mismo corpus. Para corpora pequeños (<1000 regs) puede no haber
    suficiente evidencia estadística — usar con cuidado o solo activar
    cuando N >= 5000.
    """
    logger = setup_logger("dedup_preparation")
    df_work = df.copy()

    # Usar procesadores especializados
    text_processor = EnhancedTextProcessor(mode)
    nit_processor = AdvancedNitProcessor()

    # Procesar nombres con lógica enhanced
    logger.info("Procesando nombres con EnhancedTextProcessor...")
    df_work["NOMBRE_LIMPIO"] = text_processor.process_for_deduplication(df[col_name])

    # v2.10.0 (Fix #2): eliminar palabras más frecuentes del corpus.
    # Esto ataca los FP por tokens genéricos repetidos en muchas razones
    # sociales distintas (INVERSIONES, SAS, GRUPO, COMPAÑIA, COLOMBIA).
    # Sin esto, el token_set_ratio entre 'INVERSIONES SAS' y otra
    # 'INVERSIONES SAS' es 1.0 aunque sean entidades distintas.
    if remove_top_words and remove_top_words > 0:
        logger.info(f"Removiendo top-{remove_top_words} palabras del corpus...")
        df_work["NOMBRE_LIMPIO"] = text_processor.remove_common_words(
            df_work["NOMBRE_LIMPIO"], threshold=remove_top_words
        )

    # Procesar NITs con lógica advanced
    logger.info("Procesando NITs con AdvancedNitProcessor...")
    nit_results = nit_processor.process_for_deduplication(df[col_nit])
    df_work["NIT_BASE"] = nit_results["NIT_BASE"]
    df_work["NIT_OK"] = nit_results["NIT_OK"]
    # v2.10.0 (Fix #1): propagar el origen del DV para que el scorer pueda
    # diferenciar boost según evidencia (declarado=fuerte, calculado=débil).
    df_work["DV_ORIGEN"] = nit_results["DV_ORIGEN"]

    # Generar claves fonéticas básicas
    df_work["PHONETIC_KEY1"] = df_work["NOMBRE_LIMPIO"].apply(
        lambda x: "".join(c for c in x if c.isalpha() and c not in "AEIOU")[:10]
    )
    df_work["PHONETIC_KEY2"] = df_work["NOMBRE_LIMPIO"].apply(
        lambda x: "".join(w[0] for w in x.split() if w)[:6]
    )

    # Agregar columna SRC requerida por el pipeline
    df_work["SRC"] = "DEDUP_SOURCE"

    # Renombrar columnas originales si es necesario
    if col_nit != "NIT":
        df_work["NIT"] = df_work[col_nit]
    if col_name != "RAZON_SOCIAL":
        df_work["RAZON_SOCIAL"] = df_work[col_name]

    return df_work


def _generate_non_trivial_connections(correlativa_df: pd.DataFrame) -> pd.DataFrame:
    """
    Genera DataFrame solo con conexiones no triviales (grupos > 1)
    a partir de la tabla correlativa final.
    """
    if "RECORD_COUNT" not in correlativa_df.columns:
        # Calcular si no está presente
        group_sizes = correlativa_df.groupby("ID_GRUPO")["ID_GRUPO"].transform("size")
        non_trivial_mask = group_sizes > 1
    else:
        non_trivial_mask = correlativa_df["RECORD_COUNT"] > 1

    conexiones = correlativa_df[non_trivial_mask].copy()
    conexiones = conexiones.sort_values(["ID_GRUPO", "NIT_OK"])
    return conexiones
