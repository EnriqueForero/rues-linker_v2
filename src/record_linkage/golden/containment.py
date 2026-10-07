"""
golden.containment — record_linkage_pipeline

Componentes:
    - function extract_primary_brand_name  (origen: notebook celda [127])
    - function robust_name_containment_balanced  (origen: notebook celda [127])
    - function consolidate_groups_by_nit_balanced  (origen: notebook celda [127])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from rapidfuzz.distance import Indel
from tqdm import tqdm

from ..pipeline.errores import GoldenInvalidoError, mensaje_accionable
from ..processing.text import TextProcessor
from ..utils.output import safe_print as print
from .metricas import (
    COLUMNAS_METRICAS_CALIDAD,
    mapa_prioridad,
    metricas_de_calidad,
    metricas_de_grupo,
)

#: Campos finales del golden que la correlativa lleva re-adjuntados por grupo,
#: con la columna de la correlativa que los suple si no vienen.
_CAMPOS_FINALES: tuple[tuple[str, str], ...] = (
    ("NIT_FINAL", "NIT"),
    ("RAZON_SOCIAL_FINAL", "RAZON_SOCIAL"),
)


def extract_primary_brand_name(name: str) -> str:
    """
    Extrae el nombre comercial principal usando TextProcessor TEMPORAL.

    MEJORA: Crea instancias temporales para evitar mutación de estado.

    Estrategia con fallback de 3 niveles:
    1. AGRESIVO: remueve todo (legal, códigos UAP, etc.)
    2. BALANCEADO: menos agresivo si el primero dejó vacío
    3. CONSERVADOR: mínimo si aún está vacío

    Args:
        name: Nombre completo sin limpiar

    Returns:
        Primera palabra significativa del nombre comercial

    Examples:
        >>> extract_primary_brand_name("STEPAN COLOMBIANA DE QUIMICOS SAS")
        'STEPAN'
        >>> extract_primary_brand_name("C.I. UNIFLOR S.A.S. COD UAP 642")
        'UNIFLOR'
        >>> extract_primary_brand_name("S.P.A. FUNDALCO S.A")
        'FUNDALCO'
    """
    if not name:
        return ""

    # NIVEL 1: Limpieza AGRESIVA
    # Remueve: prefijos/sufijos legales, códigos UAP/ALTEX, organizacionales
    tp_aggressive = TextProcessor(cleaning_mode="AGRESIVO", cache_size=1000)
    cleaned_aggressive = tp_aggressive.clean_name(name)

    if cleaned_aggressive and len(cleaned_aggressive.strip()) > 0:
        words = cleaned_aggressive.split()
        if words and len(words[0]) >= 3:  # Al menos 3 caracteres
            return words[0]

    # NIVEL 2: Limpieza BALANCEADA (si AGRESIVO fue muy agresivo)
    tp_balanced = TextProcessor(cleaning_mode="BALANCEADO", cache_size=1000)
    cleaned_balanced = tp_balanced.clean_name(name)

    if cleaned_balanced and len(cleaned_balanced.strip()) > 0:
        words = cleaned_balanced.split()
        if words and len(words[0]) >= 3:
            return words[0]

    # NIVEL 3: Limpieza CONSERVADORA (último recurso)
    tp_conservative = TextProcessor(cleaning_mode="CONSERVADOR", cache_size=1000)
    cleaned_conservative = tp_conservative.clean_name(name)

    if cleaned_conservative and len(cleaned_conservative.strip()) > 0:
        words = cleaned_conservative.split()
        if words and len(words[0]) >= 3:
            return words[0]

    # FALLBACK FINAL: Primera palabra del nombre original limpio
    fallback = re.sub(r"[^\w\s]", " ", str(name).upper())
    fallback = re.sub(r"\s+", " ", fallback).strip()
    words = fallback.split()

    # Saltar palabras muy cortas iniciales (C, I, S, A, etc.)
    for word in words:
        if len(word) >= 3:
            return word

    return words[0] if words else ""


def robust_name_containment_balanced(name1: str, name2: str, strict_mode: bool = False) -> bool:
    """
    Versión CORREGIDA - Valida por nombre comercial principal.

    MEJORA: No requiere pasar text_processor (evita mutación de estado).

    Criterios (en orden):
    0. Primera palabra significativa con alta similitud
    1. Contención directa
    2. Token Set Ratio >= 80
    3. Distancia de edición según longitud

    Args:
        name1, name2: Nombres a comparar
        strict_mode: Si True, más estricto

    Returns:
        True si los nombres son compatibles
    """
    if not name1 or not name2:
        return False

    n1 = str(name1).upper().strip()
    n2 = str(name2).upper().strip()

    # ============================================================
    # NIVEL 0: NOMBRE COMERCIAL PRINCIPAL (CRÍTICO)
    # ============================================================
    brand1 = extract_primary_brand_name(n1)
    brand2 = extract_primary_brand_name(n2)

    if brand1 and brand2 and len(brand1) >= 4 and len(brand2) >= 4:
        brand_ratio = Indel.normalized_similarity(brand1, brand2)

        # Thresholds según longitud
        if len(brand1) <= 6 or len(brand2) <= 6:
            threshold_brand = 0.82 if strict_mode else 0.78
        else:
            threshold_brand = 0.80 if strict_mode else 0.75

        if brand_ratio >= threshold_brand:
            # Validación adicional: palabras comunes o similitud global
            words1 = set(n1.split()) - {brand1}
            words2 = set(n2.split()) - {brand2}
            common_words = words1 & words2
            full_ratio = Indel.normalized_similarity(n1, n2)

            if len(common_words) >= 1 or full_ratio >= 0.40:
                return True

    # ============================================================
    # NIVEL 1: CONTENCIÓN DIRECTA
    # ============================================================
    if n1 == n2:
        return True

    if n1 in n2 or n2 in n1:
        shorter = min(n1, n2, key=len)
        if len(shorter) >= 5:
            return True

    # ============================================================
    # NIVEL 2: TOKEN SET RATIO
    # ============================================================
    token_score = fuzz.token_set_ratio(n1, n2)
    threshold_token = 85 if strict_mode else 80

    if token_score >= threshold_token:
        return True

    # ============================================================
    # NIVEL 3: DISTANCIA DE EDICIÓN
    # ============================================================
    edit_dist_ratio = Indel.normalized_similarity(n1, n2)

    if len(n1) <= 8 or len(n2) <= 8:
        min_ratio = 0.85 if strict_mode else 0.80
        if edit_dist_ratio >= min_ratio:
            return True

    elif len(n1) <= 15 or len(n2) <= 15:
        min_ratio = 0.83 if strict_mode else 0.78
        if edit_dist_ratio >= min_ratio:
            return True

    else:
        min_ratio = 0.72 if strict_mode else 0.67

        if edit_dist_ratio >= min_ratio:
            token_sort = fuzz.token_sort_ratio(n1, n2)
            if token_sort >= 55:
                return True

    return False


def consolidate_groups_by_nit_balanced(
    golden_df: pd.DataFrame,
    correlative_df: pd.DataFrame,
    strict_mode: bool = False,
    verbose: bool = True,
    copiar_correlativa: bool = True,
    prioridad_fuentes: Sequence[str] | Mapping[str, int] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Consolida grupos con el mismo NIT validando similitud de nombres.

    VERSIÓN 4 CORREGIDA - Sin mutación de estado, usa TextProcessor internamente.

    F1.1: el golden de salida conserva EXACTAMENTE las columnas y dtypes del
    golden de entrada. Cada grupo fusionado se reconstruye así:

    * métricas (``SOURCES_LIST``, ``SOURCES_COUNT``, ``RECORD_COUNT``,
      ``NAME_VARIATIONS``, ``NIT_VARIATIONS``, ``PRIMARY_SOURCE``,
      ``CONFIANZA``, ``CONFIDENCE_SCORE``, ``REQUIRES_REVIEW``): recalculadas
      sobre su subconjunto de la correlativa con la MISMA regla del camino
      normal (``golden.metricas``), por eso hace falta ``prioridad_fuentes``;
    * ``NIT_FINAL`` y ``RAZON_SOCIAL_FINAL``: regla histórica sin cambios —
      primer valor no nulo del subconjunto en el orden de la correlativa
      (``groupby().first()``); unificarla con el selector es decisión de F2;
    * el resto (``ID_GRUPO``, ``CREATED_AT`` y cualquier columna propia del
      golden): heredado de la fila del grupo raíz, que es el que sobrevive.

    Antes (hasta 0.22.x) el grupo fusionado era ``groupby().first()`` de la
    correlativa completa: el golden absorbía sus 27 columnas y las métricas
    quedaban en NaN (hecho medido en el banco de 30.486).

    Args:
        golden_df: DataFrame de golden records
        correlative_df: DataFrame correlativa
        strict_mode: Si True, aplica umbrales más estrictos
        verbose: Si True, muestra logs detallados
        copiar_correlativa: Si True conserva la entrada. El orquestador puede
            pasar False cuando transfiere su propiedad para evitar una copia
            completa de la tabla correlativa.
        prioridad_fuentes: lista de fuentes en orden de prioridad (la misma
            que recibe ``GoldenRecordGeneratorV7``) o mapeo ``{fuente: rango}``
            con menor = gana; decide ``PRIMARY_SOURCE`` del grupo fusionado.
            Obligatoria: sin ella la métrica no es reproducible.

    Returns:
        Tuple[golden_df_actualizado, correlative_df_actualizado]

    Raises:
        ValueError: si falta ``prioridad_fuentes`` o la invariante de unicidad
            de ``ID_GRUPO`` está rota.
        KeyError: si faltan columnas requeridas en golden o correlativa. Toda
            validación ocurre ANTES de la primera mutación de la correlativa.
    """
    if prioridad_fuentes is None:
        raise ValueError(
            mensaje_accionable(
                "consolidate_groups_by_nit_balanced se llamó sin prioridad_fuentes.",
                "PRIMARY_SOURCE de cada grupo fusionado se decide con esa prioridad; "
                "sin ella la métrica quedaría nula o inventada.",
                "pase la misma lista de fuentes que recibe GoldenRecordGeneratorV7 "
                "(p. ej. las claves de source_quality_weights del perfil).",
            )
        )
    rangos_fuentes = mapa_prioridad(prioridad_fuentes)
    required_golden = {"ID_GRUPO", "NIT_FINAL", "RAZON_SOCIAL_FINAL"}
    missing_golden = sorted(required_golden.difference(golden_df.columns))
    if missing_golden:
        raise KeyError(f"Faltan columnas requeridas en golden_df: {missing_golden}")
    if "ID_GRUPO" not in correlative_df.columns:
        raise KeyError("Falta ID_GRUPO en correlative_df")
    if golden_df["ID_GRUPO"].duplicated().any():
        raise ValueError(
            "La invariante de unicidad de ID_GRUPO está rota en golden_df antes de consolidar"
        )

    if verbose:
        print(
            f"🔗 Iniciando consolidación {'ESTRICTA' if strict_mode else 'BALANCEADA'} por NIT (v4 CORREGIDA)..."
        )
        print(f"   • Golden records: {len(golden_df):,}")
        print(f"   • Correlativa: {len(correlative_df):,}")
        print("   • Usando TextProcessor con fallback de 3 niveles")

    # ============================================================
    # 1. IDENTIFICAR NITs CON MÚLTIPLES GRUPOS
    # ============================================================
    nits_invalidos = {
        "222222222",
        "123456789",
        "000000000",
        "111111111",
        "999999999",
        "",
        "nan",
        "None",
    }

    try:
        nit_text = golden_df["NIT_FINAL"].astype("string[pyarrow]")
    except (ImportError, TypeError, ValueError):
        nit_text = golden_df["NIT_FINAL"].astype("string")
    valid_mask = nit_text.notna() & (nit_text != "") & ~nit_text.isin(nits_invalidos)
    valid_nits = pd.DataFrame(
        {
            "NIT_FINAL": nit_text[valid_mask].array,
            "ID_GRUPO": golden_df.loc[valid_mask, "ID_GRUPO"].array,
        }
    )
    duplicate_mask = valid_nits["NIT_FINAL"].duplicated(keep=False)
    nits_to_check = (
        valid_nits.loc[duplicate_mask].groupby("NIT_FINAL", sort=False)["ID_GRUPO"].agg(list)
    )

    if verbose:
        print(f"   • NITs únicos: {valid_nits['NIT_FINAL'].nunique():,}")
        print(f"   • NITs en múltiples grupos: {len(nits_to_check):,}")
    del duplicate_mask, valid_mask, valid_nits, nit_text

    if len(nits_to_check) == 0:
        if verbose:
            print("   ✓ No hay NITs duplicados")
        return golden_df, correlative_df

    # ============================================================
    # 2. UNION-FIND
    # ============================================================
    # Solo los grupos que comparten un NIT necesitan Union-Find y nombres.
    # Construir ambos diccionarios para todo el universo puede consumir varios
    # cientos de MiB aunque apenas haya unas pocas colisiones.
    grupos_implicados: set[Any] = set()
    for grupos in nits_to_check:
        grupos_implicados.update(grupos)
    parent = {grupo: grupo for grupo in grupos_implicados}

    def find(i):
        if parent[i] == i:
            return i
        parent[i] = find(parent[i])
        return parent[i]

    def union(i, j):
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            parent[root_j] = root_i
            return True
        return False

    nombres_implicados = golden_df.loc[
        golden_df["ID_GRUPO"].isin(grupos_implicados),
        ["ID_GRUPO", "RAZON_SOCIAL_FINAL"],
    ]
    name_map = dict(
        zip(
            nombres_implicados["ID_GRUPO"],
            nombres_implicados["RAZON_SOCIAL_FINAL"],
            strict=True,
        )
    )
    del nombres_implicados

    merges_count = 0
    rejected_count = 0
    rejected_examples: list[dict[str, Any]] = []
    merged_examples: list[dict[str, Any]] = []

    # ============================================================
    # 3. FUSIONAR CON VALIDACIÓN
    # ============================================================
    iterator = (
        tqdm(nits_to_check.items(), desc="Fusionando por NIT") if verbose else nits_to_check.items()
    )

    for nit, group_ids in iterator:
        sorted_groups = sorted(group_ids, key=lambda g: len(str(name_map.get(g, ""))), reverse=True)

        pivot_id = sorted_groups[0]
        pivot_name = name_map.get(pivot_id, "")

        for target_id in sorted_groups[1:]:
            target_name = name_map.get(target_id, "")

            # VALIDACIÓN (sin pasar text_processor)
            if robust_name_containment_balanced(pivot_name, target_name, strict_mode):
                if union(pivot_id, target_id):
                    merges_count += 1

                    if len(merged_examples) < 10:
                        brand1 = extract_primary_brand_name(pivot_name)
                        brand2 = extract_primary_brand_name(target_name)
                        if brand1 != brand2:
                            merged_examples.append(
                                {
                                    "NIT": nit,
                                    "Brand_1": brand1,
                                    "Brand_2": brand2,
                                    "Nombre_1": pivot_name[:50],
                                    "Nombre_2": target_name[:50],
                                }
                            )
            else:
                rejected_count += 1
                if len(rejected_examples) < 10:
                    rejected_examples.append(
                        {
                            "NIT": nit,
                            "Brand_1": extract_primary_brand_name(pivot_name),
                            "Brand_2": extract_primary_brand_name(target_name),
                            "Nombre_1": pivot_name[:50],
                            "Nombre_2": target_name[:50],
                        }
                    )

    # ============================================================
    # 4-8. APLICAR FUSIONES
    # ============================================================
    final_merge_map = {k: find(k) for k in parent if k != find(k)}

    if not final_merge_map:
        if verbose:
            print("   • No se encontraron fusiones válidas")
            print(f"   • Casos rechazados: {rejected_count}")
            if rejected_examples:
                print("\n   📋 Ejemplos de casos rechazados:")
                for ex in rejected_examples[:5]:
                    print(f"      NIT {ex['NIT']}: '{ex['Brand_1']}' vs '{ex['Brand_2']}'")
                    print(f"         '{ex['Nombre_1']}'")
                    print(f"         '{ex['Nombre_2']}'")
        return golden_df, correlative_df

    if verbose:
        print(f"\n   ✓ Fusiones aprobadas: {len(final_merge_map)} grupos")
        print(f"   ⚠️ Fusiones rechazadas: {rejected_count} casos")

        if merged_examples:
            print("\n   ✅ Ejemplos de fusiones exitosas:")
            for ex in merged_examples[:5]:
                print(f"      NIT {ex['NIT']}: '{ex['Brand_1']}' ≈ '{ex['Brand_2']}'")
                print(f"         '{ex['Nombre_1']}'")
                print(f"         '{ex['Nombre_2']}'")

        if rejected_examples:
            print("\n   ⚠️ Ejemplos de casos rechazados:")
            for ex in rejected_examples[:5]:
                print(f"      NIT {ex['NIT']}: '{ex['Brand_1']}' vs '{ex['Brand_2']}'")
                print(f"         '{ex['Nombre_1']}'")
                print(f"         '{ex['Nombre_2']}'")

    # Contrato de la correlativa para recalcular las métricas: se verifica
    # ANTES de la primera mutación, para que con ``copiar_correlativa=False``
    # un fallo deje la entrada intacta.
    faltan_correl = [col for col in ("SRC", "RAZON_SOCIAL") if col not in correlative_df.columns]
    if "NIT_OK" not in correlative_df.columns and "NIT" not in correlative_df.columns:
        faltan_correl.append("NIT (o NIT_OK)")
    if faltan_correl:
        raise KeyError(
            mensaje_accionable(
                f"hay {len(final_merge_map)} fusiones por NIT pero la correlativa no trae "
                f"{faltan_correl}.",
                "sin esas columnas no se pueden recalcular las métricas del grupo fusionado "
                "y el golden saldría con métricas nulas.",
                "pase la correlativa completa que produce GoldenRecordGeneratorV7.",
            )
        )

    # El llamador tradicional conserva semántica no mutante. El orquestador
    # puede transferir la propiedad y evitar esta copia del frame completo.
    if copiar_correlativa:
        correlative_df = correlative_df.copy()

    # Aplicar las pocas fusiones sobre un solo buffer int64 evita la cadena
    # map+fillna+astype (tres Series del tamaño total de la correlativa).
    ids = correlative_df["ID_GRUPO"].to_numpy(dtype="int64", copy=True)
    for viejo, nuevo in final_merge_map.items():
        ids[ids == viejo] = nuevo

    affected_ids = set(final_merge_map.values()) | set(final_merge_map.keys())
    affected_rows = np.isin(ids, list(affected_ids))
    df_sub = correlative_df.iloc[np.flatnonzero(affected_rows)].copy()
    df_sub["ID_GRUPO"] = ids[affected_rows]
    del affected_rows

    if verbose:
        print(f"\n   🔄 Recalculando golden records para {len(affected_ids)} grupos...")

    new_golden = _reconstruir_golden_fusionado(golden_df, df_sub, rangos_fuentes)
    del df_sub

    # Construcción por columnas: acota el pico a una columna en vez de
    # materializar el filtro y después otra copia completa en pd.concat.
    final_golden = _concat_filtrado_por_columnas(golden_df, new_golden, affected_ids)

    # Re-adjuntar finales por posición evita drop+merge, que construía dos
    # correlativas adicionales. Todas las validaciones se hacen antes de la
    # primera mutación cuando ``copiar_correlativa=False``: un fallback nunca
    # recibe una correlativa parcialmente modificada por un error contractual.
    final_columns = ("NIT_FINAL", "RAZON_SOCIAL_FINAL")

    if final_golden.empty:
        if not correlative_df.empty:
            raise ValueError(
                "No se pueden re-adjuntar campos finales: el golden quedó vacío "
                "mientras la correlativa conserva registros."
            )
        correlative_df["ID_GRUPO"] = ids
        del ids
        for col in final_columns:
            correlative_df[col] = pd.Series(dtype="string")
    else:
        missing_final = [col for col in final_columns if col not in final_golden.columns]
        if missing_final:
            raise ValueError(f"Faltan campos finales en el golden consolidado: {missing_final}")
        gid_gold = final_golden["ID_GRUPO"].to_numpy(dtype="int64")
        orden = np.argsort(gid_gold, kind="stable")
        gid_ordenado = gid_gold[orden]
        if gid_ordenado.size > 1 and not (np.diff(gid_ordenado) > 0).all():
            raise ValueError(
                "ID_GRUPO duplicado en el golden tras aplicar fusiones: "
                "invariante de unicidad rota antes de re-adjuntar los finales."
            )

        pos_ins = np.searchsorted(gid_ordenado, ids)
        pos_ins = np.minimum(pos_ins, gid_ordenado.size - 1)
        encontrado = gid_ordenado[pos_ins] == ids
        posiciones = orden[pos_ins]
        del gid_gold, gid_ordenado, pos_ins, orden

        correlative_df["ID_GRUPO"] = ids
        del ids
        for col in final_columns:
            if col in correlative_df.columns:
                del correlative_df[col]

        for col in final_columns:
            valores = pd.Series(final_golden[col].array.take(posiciones), name=col)
            if not encontrado.all():
                valores[~encontrado] = pd.NA
            correlative_df[col] = valores.array
            del valores
        del posiciones, encontrado

    if not (
        isinstance(correlative_df.index, pd.RangeIndex)
        and correlative_df.index.start == 0
        and correlative_df.index.step == 1
    ):
        correlative_df = correlative_df.reset_index(drop=True)
    final_correlative = correlative_df

    if verbose:
        print("\n   ✅ Consolidación completada exitosamente")
        print(f"   • Grupos finales: {len(final_golden):,}")
        print(f"   • Registros: {len(final_correlative):,}")

    return final_golden, final_correlative


def _reconstruir_golden_fusionado(
    golden_df: pd.DataFrame,
    df_sub: pd.DataFrame,
    rangos_fuentes: Mapping[str, int],
) -> pd.DataFrame:
    """Una fila de golden por grupo fusionado, con las columnas de ``golden_df``.

    ``df_sub`` es el subconjunto de la correlativa de los grupos afectados, ya
    con ``ID_GRUPO`` remapeado a la raíz de cada fusión. Ver la regla por
    columna en :func:`consolidate_groups_by_nit_balanced`.
    """
    metricas = metricas_de_grupo(df_sub, rangos_fuentes)
    raices = metricas.index

    finales: dict[str, pd.Series] = {}
    agrupado = df_sub.groupby("ID_GRUPO")
    for col, alterna in _CAMPOS_FINALES:
        origen = col if col in df_sub.columns else alterna
        if origen not in df_sub.columns:
            raise KeyError(
                mensaje_accionable(
                    f"la correlativa no trae ni {col} ni {alterna}.",
                    f"el golden necesita {col} para cada grupo fusionado.",
                    "pase la correlativa completa que produce GoldenRecordGeneratorV7.",
                )
            )
        finales[col] = agrupado[origen].first()
    del agrupado

    en_golden = golden_df["ID_GRUPO"].isin(raices)
    heredado = golden_df.loc[en_golden].set_index("ID_GRUPO")
    if len(heredado) != len(raices):
        raise ValueError(
            mensaje_accionable(
                "una raíz de fusión no tiene fila en el golden de entrada.",
                "el grupo fusionado no tendría de dónde heredar sus campos no recalculados.",
                "las raíces del Union-Find deben ser ID_GRUPO del golden; revise final_merge_map.",
            )
        )
    heredado = heredado.reindex(raices)
    del en_golden

    calidad = metricas_de_calidad(
        pd.DataFrame({"NIT_FINAL": finales["NIT_FINAL"]}, index=raices).join(metricas)
    )

    columnas: dict[str, pd.Series] = {}
    for col in golden_df.columns:
        if col == "ID_GRUPO":
            columnas[col] = pd.Series(raices.to_numpy(), index=raices, name=col)
        elif col in metricas.columns:
            columnas[col] = metricas[col]
        elif col in finales:
            columnas[col] = finales[col]
        elif col in COLUMNAS_METRICAS_CALIDAD:
            columnas[col] = calidad[col]
        else:
            columnas[col] = heredado[col]

    nuevo = pd.DataFrame(columnas, index=raices).reset_index(drop=True)
    # Mismo dtype que el golden de entrada: el pd.concat posterior no debe
    # ascender enteros a float ni texto Arrow a object. Si la conversión deja
    # nulos NUEVOS (un Categorical cuyas categorías no incluyen el valor
    # recalculado, p. ej. SOURCES_LIST = 'CRM|EXPORTACIONES|RUES'), se dice
    # aquí: ``astype`` lo volvería NaN sin error y ``verificar_golden`` lo
    # vería después sin poder decir por qué.
    for col in golden_df.columns:
        if nuevo[col].dtype == golden_df[col].dtype:
            continue
        nulos_antes = int(nuevo[col].isna().sum())
        nuevo[col] = nuevo[col].astype(golden_df[col].dtype)
        nulos_nuevos = int(nuevo[col].isna().sum()) - nulos_antes
        if nulos_nuevos > 0:
            raise GoldenInvalidoError(
                mensaje_accionable(
                    f"al convertir la columna {col} de los grupos fusionados al dtype del "
                    f"golden de entrada ({golden_df[col].dtype}) quedaron {nulos_nuevos} "
                    "valor(es) nulo(s) nuevo(s).",
                    "el golden consolidado saldría con esa métrica nula en los grupos "
                    "fusionados (un Categorical sin la categoría nueva la vuelve NaN sin error).",
                    f"entregue {col} como texto o número plano en el golden de entrada (no como "
                    "category), o amplíe sus categorías antes de consolidar.",
                )
            )
    return nuevo


def _concat_filtrado_por_columnas(
    golden_df: pd.DataFrame,
    new_golden: pd.DataFrame,
    affected_ids: set[Any],
) -> pd.DataFrame:
    """Equivalente acotado en memoria al filtro seguido de ``pd.concat``.

    Solo conserva las columnas de ``golden_df`` (F1.1): una columna que
    ``new_golden`` traiga de más se ignora, y una que le falte es error, para
    que ninguna fila del golden salga con NaN ni con dtype ascendido.
    """
    columnas = list(golden_df.columns)
    faltantes = [col for col in columnas if col not in new_golden.columns]
    if faltantes:
        raise ValueError(
            mensaje_accionable(
                f"a los grupos fusionados les faltan las columnas {faltantes} del golden.",
                "concatenarlos rellenaría esas columnas con NaN y ascendería su dtype.",
                "reconstruya cada grupo fusionado con todas las columnas del golden "
                "(_reconstruir_golden_fusionado).",
            )
        )
    keep_pos = np.flatnonzero(~golden_df["ID_GRUPO"].isin(affected_ids).to_numpy())

    piezas: dict[str, pd.Series] = {}
    for col in columnas:
        arriba = pd.Series(golden_df[col].array.take(keep_pos), name=col)
        abajo = new_golden[col].reset_index(drop=True)
        piezas[col] = pd.concat([arriba, abajo], ignore_index=True)
        del arriba, abajo
        if piezas[col].dtype != golden_df[col].dtype:
            raise ValueError(
                mensaje_accionable(
                    f"la columna {col} cambió de dtype al consolidar "
                    f"({golden_df[col].dtype} → {piezas[col].dtype}).",
                    "el golden consolidado debe leerse igual que el del generador.",
                    "convierta la columna de los grupos fusionados al dtype del golden de entrada.",
                )
            )

    resultado = pd.DataFrame(piezas)
    piezas.clear()
    return resultado
