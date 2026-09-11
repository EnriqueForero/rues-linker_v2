"""
golden.selector — record_linkage_pipeline

Componentes:
    - class AdvancedValueSelector  (origen: notebook celda [125])
    - class CompanyNameSelector  (origen: notebook celda [150])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

import pandas as pd

from ..utils.logger import CustomLogger


class AdvancedValueSelector:
    """
    Implementa heurísticas avanzadas para seleccionar el NIT y la Razón Social
    óptimos dentro de un grupo, respetando la integridad de las fuentes.

    v3.2.5 (FASE 2): acepta `source_quality_weights` opcional con pesos
    numéricos por fuente (0.0–1.0). Cuando se proporciona, los pesos se
    usan como criterio adicional de desempate al elegir el representante.
    Si no se proporciona, el comportamiento es idéntico a v3.2.4 (orden
    de prioridad por keys del mapa).
    """

    def __init__(
        self,
        source_priority_map: dict[str, int],
        source_quality_weights: dict[str, float] | None = None,
    ):
        """
        Args:
            source_priority_map: dict {SRC: prioridad_int}. Menor = más prioritaria.
                Construido típicamente con `{src: i for i, src in enumerate(priority_list)}`.
            source_quality_weights: dict {SRC: peso_float}. Mayor = más confiable.
                Opcional. v3.2.5 lo usa para desempates en `select_best_name`
                cuando varios registros de la misma prioridad ofrecen nombres
                con consenso empatado.
        """
        self.source_priority_map = source_priority_map
        self.source_quality_weights = source_quality_weights or {}
        self.SOCIETARY_PATTERNS_REGEX = re.compile(
            r"\b(S\.?A\.?S\.?|LTDA\.?|S\.?A\.?|LIMITADA|E\.?U\.?|CIA|INC)\b", re.IGNORECASE
        )
        self.TRIM_PUNCTUATION_REGEX = re.compile(r"^[\s\.:\-]+|[\s\.:\-]+$")

    def _get_fingerprint(self, name: str) -> str:
        """Crea una huella digital normalizada para agrupar nombres similares."""
        if not isinstance(name, str):
            return ""
        fp = unicodedata.normalize("NFKD", name.upper()).encode("ascii", "ignore").decode("utf-8")
        fp = self.SOCIETARY_PATTERNS_REGEX.sub("", fp)
        fp = re.sub(r"[^A-Z0-9]", "", fp)
        return fp

    def _consensus_name(self, names_list: list[str]) -> str:
        """Aplica un consenso para elegir el mejor nombre de una lista.

        v2.4.0: el desempate final es determinista. Antes se usaba
        max(set(candidates), key=...), pero ``set`` no tiene orden estable, así
        que en empates exactos de (frecuencia, longitud) el resultado variaba
        entre ejecuciones. Ahora, en empate, se desempata alfabéticamente
        ascendente. Esto coincide bit-a-bit con select_best_name_batch.
        """
        if not names_list:
            return ""
        # Cuenta la frecuencia de cada huella digital
        fp_counts = Counter(self._get_fingerprint(n) for n in names_list)
        # Encuentra la huella más común
        best_fp = fp_counts.most_common(1)[0][0]
        # Filtra los nombres que coinciden con la mejor huella
        candidates = [n for n in names_list if self._get_fingerprint(n) == best_fp]
        if not candidates:
            return names_list[0]
        # Entre los candidatos: mayor (frecuencia, longitud); empate → alfabético.
        # min con clave negada para freq/len y nombre ascendente como desempate.
        return min(
            set(candidates),
            key=lambda n: (-names_list.count(n), -len(n), n),
        )

    def select_best_name(self, group_df: pd.DataFrame) -> str:
        """
        Selecciona la Razón Social final aplicando reglas de negocio.

        v3.2.5 (FASE 2): cuando `source_quality_weights` está disponible y
        hay un empate en la fuente de mayor prioridad (varios registros), se
        desempata por peso de calidad de fuente antes de aplicar consenso.
        """
        # Regla 1: Singleton
        if len(group_df) == 1:
            return group_df["RAZON_SOCIAL"].iloc[0]

        # Regla 2: Fuente Única (Deduplicación interna)
        if group_df["SRC"].nunique() == 1:
            return self._consensus_name(group_df["RAZON_SOCIAL"].tolist())

        # Regla 3 y 4: Múltiples Fuentes (Prioridad)
        # v2.2.0: calculamos el score de prioridad sin MUTAR group_df (el patrón
        # anterior `group_df["priority_score"] = ...` dentro de un apply dispara
        # SettingWithCopyWarning y copias defensivas costosas por grupo). La
        # lógica de selección es idéntica.
        priority_scores = group_df["SRC"].map(self.source_priority_map)
        highest_priority_score = priority_scores.min()
        # Filtra los registros que pertenecen a la fuente de mayor prioridad
        priority_records = group_df[priority_scores == highest_priority_score]

        if len(priority_records) == 1:
            # Caso ideal: un solo registro de la fuente más confiable
            return priority_records["RAZON_SOCIAL"].iloc[0]
        else:
            # v3.2.5: aprovechamiento de source_quality_weights en desempate.
            # Si hay pesos definidos, dentro de los registros de la fuente
            # prioritaria preferimos aquellos cuya fuente tiene mayor peso
            # numérico. Solo aplica si DENTRO del subconjunto hay >1 fuente
            # (puede pasar si dos fuentes empatan en prioridad por orden).
            if self.source_quality_weights and priority_records["SRC"].nunique() > 1:
                quality_scores = (
                    priority_records["SRC"].map(self.source_quality_weights).fillna(0.0)
                )
                max_quality = quality_scores.max()
                priority_records = priority_records[quality_scores == max_quality]
                if len(priority_records) == 1:
                    return priority_records["RAZON_SOCIAL"].iloc[0]
            # Desempate final: consenso entre los nombres restantes
            return self._consensus_name(priority_records["RAZON_SOCIAL"].tolist())

    def select_best_nit(self, group_df: pd.DataFrame) -> str:
        """Selecciona el NIT final (la lógica existente es adecuada).

        v2.4.0: desempate determinista (alfabético) en empate de (frecuencia,
        longitud), consistente con select_best_nit_batch.
        """
        nits_series = group_df["NIT_OK" if "NIT_OK" in group_df.columns else "NIT"]
        valid_nits = nits_series.dropna().astype(str)
        valid_nits = valid_nits[valid_nits.str.isdigit()]
        if valid_nits.empty:
            return ""
        nits_list = valid_nits.tolist()
        return min(set(nits_list), key=lambda n: (-nits_list.count(n), -len(n), n))

    # ══════════════════════════════════════════════════════════════════════════
    # API VECTORIZADA POR LOTES (v2.4.0)
    #
    # Procesan TODOS los grupos de un DataFrame de una sola pasada, sin
    # groupby().apply() ni bucle Python por grupo. Reproducen bit-a-bit la
    # lógica de select_best_name / select_best_nit (probado en
    # tests/test_golden_selector_paridad.py contra el oráculo individual).
    #
    # Idea: precomputar las claves de decisión como columnas vectorizadas y
    # resolver el "argmax por (frecuencia, longitud)" con operaciones de groupby.
    # ══════════════════════════════════════════════════════════════════════════

    def _fingerprint_series(self, names: pd.Series) -> pd.Series:
        """Huella digital vectorizada (equivalente a _get_fingerprint por fila).

        unicodedata.normalize no es vectorizable directamente, pero el resto sí.
        Aplicamos la normalización NFKD→ascii por elemento (inevitable) y el
        resto de transformaciones con métodos .str vectorizados.
        """
        import unicodedata

        def _nfkd_ascii(s: object) -> str:
            if not isinstance(s, str):
                return ""
            return (
                unicodedata.normalize("NFKD", s.upper()).encode("ascii", "ignore").decode("utf-8")
            )

        # Normalización por elemento (única parte no vectorizable).
        norm = names.map(_nfkd_ascii)
        # Quitar sufijos societarios y no-alfanuméricos (vectorizado).
        norm = norm.str.replace(self.SOCIETARY_PATTERNS_REGEX, "", regex=True)
        norm = norm.str.replace(r"[^A-Z0-9]", "", regex=True)
        return norm

    def select_best_name_batch(self, df: pd.DataFrame, group_col: str = "ID_GRUPO") -> pd.Series:
        """Selecciona el mejor nombre de CADA grupo, vectorizado.

        Reproduce exactamente select_best_name:
          - singleton  → su único nombre
          - fuente única → consenso por fingerprint sobre todos los nombres
          - multi-fuente → consenso por fingerprint sobre los nombres de la
            fuente de mayor prioridad (sólo esos)

        Args:
            df: DataFrame con columnas [group_col, 'RAZON_SOCIAL', 'SRC'].
            group_col: Columna que identifica el grupo.

        Returns:
            Series indexada por group_col con el nombre elegido por grupo.
        """
        work = df[[group_col, "RAZON_SOCIAL", "SRC"]].copy()
        work["__prio"] = work["SRC"].map(self.source_priority_map)

        # nunique de fuentes por grupo → distinguir fuente única vs multi.
        src_nunique = work.groupby(group_col)["SRC"].transform("nunique")
        # prioridad mínima por grupo (la fuente más confiable presente).
        min_prio = work.groupby(group_col)["__prio"].transform("min")

        # Conjunto de filas elegibles para el consenso:
        #   - fuente única (src_nunique == 1): todas las filas del grupo
        #   - multi-fuente: sólo las filas con prioridad == min del grupo
        elegible = (src_nunique == 1) | (work["__prio"] == min_prio)
        cand = work[elegible].copy()

        # Fingerprint de los candidatos.
        cand["__fp"] = self._fingerprint_series(cand["RAZON_SOCIAL"])

        # Paso A: fingerprint ganador por grupo = el más frecuente
        # (most_common(1) de Counter: en empate, el primero insertado, que con
        # value_counts de pandas es estable por orden de aparición → desempate
        # por mayor frecuencia; replicamos con sort estable).
        fp_freq = cand.groupby([group_col, "__fp"], sort=False).size().rename("fp_n").reset_index()
        # idxmax estable: ordenar por fp_n desc manteniendo orden de aparición.
        fp_freq["__order"] = range(len(fp_freq))
        fp_freq = fp_freq.sort_values(["fp_n", "__order"], ascending=[False, True])
        best_fp = fp_freq.drop_duplicates(group_col, keep="first").set_index(group_col)["__fp"]

        # Paso B: entre los nombres con el fingerprint ganador, elegir por
        # (frecuencia en la lista de candidatos, longitud), ambos desc.
        cand = cand.join(best_fp.rename("__best_fp"), on=group_col)
        winners_rows = cand[cand["__fp"] == cand["__best_fp"]].copy()

        # Frecuencia de cada nombre dentro de su grupo (sobre la lista candidata).
        name_freq = (
            winners_rows.groupby([group_col, "RAZON_SOCIAL"], sort=False)
            .size()
            .rename("name_n")
            .reset_index()
        )
        name_freq["__len"] = name_freq["RAZON_SOCIAL"].str.len()
        # max por (name_n, __len): ordenar desc y tomar el primero por grupo.
        # En empate exacto de (frecuencia, longitud), desempate ALFABÉTICO
        # ascendente (determinista). El oráculo original usaba max(set(...)) que
        # en estos empates devolvía un elemento NO determinista (orden de hash
        # del set); aquí lo hacemos reproducible. Ver test de paridad.
        name_freq = name_freq.sort_values(
            [group_col, "name_n", "__len", "RAZON_SOCIAL"],
            ascending=[True, False, False, True],
        )
        best_name = name_freq.drop_duplicates(group_col, keep="first").set_index(group_col)[
            "RAZON_SOCIAL"
        ]

        # Reindexar al universo completo de grupos (por si algún grupo quedó sin
        # candidatos elegibles, cae al primer nombre del grupo como el original).
        todos = df.groupby(group_col)["RAZON_SOCIAL"].first()
        return best_name.reindex(todos.index).fillna(todos)

    def select_best_nit_batch(self, df: pd.DataFrame, group_col: str = "ID_GRUPO") -> pd.Series:
        """Selecciona el mejor NIT de CADA grupo, vectorizado.

        Reproduce select_best_nit: entre los NITs válidos (sólo dígitos), el de
        mayor (frecuencia, longitud). Si el grupo no tiene NITs válidos → "".
        """
        nit_col = "NIT_OK" if "NIT_OK" in df.columns else "NIT"
        work = df[[group_col, nit_col]].copy()
        work[nit_col] = work[nit_col].astype("string")
        # Sólo NITs compuestos enteramente de dígitos (str.isdigit del original).
        valid = work[work[nit_col].str.fullmatch(r"\d+", na=False)].copy()

        freq = valid.groupby([group_col, nit_col], sort=False).size().rename("nit_n").reset_index()
        freq["__len"] = freq[nit_col].str.len()
        # Empate de (frecuencia, longitud) → desempate alfabético ascendente.
        freq = freq.sort_values(
            [group_col, "nit_n", "__len", nit_col], ascending=[True, False, False, True]
        )
        best_nit = freq.drop_duplicates(group_col, keep="first").set_index(group_col)[nit_col]

        # Grupos sin NIT válido → cadena vacía.
        todos = df.groupby(group_col).size()
        return best_nit.reindex(todos.index).fillna("").astype(str)


class CompanyNameSelector:
    """
    Implementa la lógica sofisticada de select_best_company_name
    con consenso por huellas digitales y validación de calidad.

    Migración completa del algoritmo original.
    """

    def __init__(self):
        self.fingerprint_cache = {}
        self.societary_patterns = re.compile(r"\b(S\.?A\.?S\.?|LTDA\.?|S\.?A\.?)\b")
        self.commercial_terms = re.compile(r"\b(GROUP|GRUPO|HOLDING)\b")
        self.logger = CustomLogger("CompanyNameSelector")

        # Patterns del código original
        self.SOCIETARY_PATTERNS_REGEX = r"\b(S\.?A\.?S\.?|S\.?A\.?|LTDA\.?|LIMITADA|E\.?U\.?|S\.?EN\.?C\.?|S\.?C\.?A\.?|CIA|INC|LLC|CORP|S\.?R\.?L\.?)\b"
        self.COMMERCIAL_TERMS_REGEX = r"\b(GROUP|GRUPO|HOLDING|COMPANY|INTERNACIONAL|INTERNATIONAL|COMERCIALIZADORA|DISTRIBUIDORA|SOLUTIONS|SERVICES|TECHNOLOGY|TRADING)\b"
        self.COMMON_STOPWORDS_REGEX = r"\b(DE|LA|EL|LOS|LAS|Y|E|O|U|DEL|EN|CON|AND|OF|THE|FOR)\b"
        self.TRIM_PUNCTUATION_REGEX = r"^[\s\.,;:-]+|[\s\.,;:-]+$"

    def select_optimal_name(self, group_df: pd.DataFrame, col_name: str = "RAZON_SOCIAL") -> str:
        """
        Migración completa del algoritmo de consenso por huellas digitales
        con todas las validaciones y fallbacks del código original.

        Esta es una implementación EXACTA del método original.
        """
        if (
            not isinstance(group_df, pd.DataFrame)
            or group_df.empty
            or col_name not in group_df.columns
        ):
            return None

        all_names_list = [
            str(name).strip() for name in group_df[col_name].dropna() if str(name).strip()
        ]
        if not all_names_list:
            return None

        unique_names = list(set(all_names_list))

        # Helper interno para limpieza final
        def _final_clean(name: str) -> str:
            """Aplica la limpieza final: elimina sufijos societarios y puntuación residual."""
            cleaned_name = re.sub(self.SOCIETARY_PATTERNS_REGEX, "", name, flags=re.IGNORECASE)
            cleaned_name = re.sub(self.TRIM_PUNCTUATION_REGEX, "", cleaned_name.strip())
            return " ".join(cleaned_name.split()).strip()

        # Caso simple: un solo nombre único
        if len(unique_names) == 1:
            name = unique_names[0]
            final_name = _final_clean(name)
            return final_name if len(final_name) >= 3 else name

        # Generación de huellas digitales para consenso
        def _get_fingerprint(name: str) -> str:
            """Crea una huella normalizada agresiva para encontrar el consenso."""
            fp = (
                unicodedata.normalize("NFKD", name.upper())
                .encode("ascii", "ignore")
                .decode("utf-8")
            )
            fp = re.sub(self.SOCIETARY_PATTERNS_REGEX, "", fp)
            fp = re.sub(self.COMMERCIAL_TERMS_REGEX, "", fp)
            fp = re.sub(self.COMMON_STOPWORDS_REGEX, "", fp)
            fp = re.sub(r"[^A-Z0-9]", "", fp)
            return fp

        fingerprint_map = {name: _get_fingerprint(name) for name in unique_names}
        all_fingerprints = [fp for fp in (_get_fingerprint(name) for name in all_names_list) if fp]

        # Selección de candidatos por consenso
        candidate_names = unique_names

        if all_fingerprints:
            fingerprint_counts = Counter(all_fingerprints)
            if fingerprint_counts:
                consensus_fingerprint = fingerprint_counts.most_common(1)[0][0]
                if consensus_fingerprint:
                    consensus_candidates = [
                        name for name, fp in fingerprint_map.items() if fp == consensus_fingerprint
                    ]
                    if consensus_candidates:
                        candidate_names = consensus_candidates

        # Selección del mejor representante por frecuencia y longitud
        best_original_name = max(
            candidate_names, key=lambda name: (all_names_list.count(name), len(name))
        )

        # Validación de calidad y fallback
        final_name = _final_clean(best_original_name)

        if len(final_name) < 10 and len(all_names_list) > 1:
            longest_name_in_group = max(all_names_list, key=len)
            return _final_clean(longest_name_in_group)

        return final_name
