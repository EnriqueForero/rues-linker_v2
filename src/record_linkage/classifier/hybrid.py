"""
classifier.hybrid — record_linkage_pipeline

Componentes:
    - class ClasificadorHibridoOptimizado  (origen: notebook celda [94])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import Counter
from typing import Any

import numpy as np
import pandas as pd

from ..utils.output import safe_print as print


class ClasificadorHibridoOptimizado:
    """Clasificador híbrido para distinguir entre empresas y personas."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.empresa_strong_indicators = config.get("empresa_strong_indicators", {})
        self.persona_strong_indicators = config.get("persona_strong_indicators", {})
        self.golden_rule_terminos = set(config.get("golden_rule_terminos", []))
        self._initialize_indicators()
        self._compile_patterns()
        self.word_weights: dict[str, float] = {}
        self.is_trained: bool = False
        self._stat_score_cache: dict[str, float] = {}

    def _normalize_text(self, text: str) -> str:
        if not isinstance(text, str):
            return ""
        text = unicodedata.normalize("NFD", text.upper()).encode("ascii", "ignore").decode("utf-8")
        text = re.sub(r"[^\w\s\.]", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _initialize_indicators(self) -> None:
        self.norm_empresa_indicators = {
            self._normalize_text(k): v for k, v in self.empresa_strong_indicators.items()
        }
        self.norm_persona_indicators = {
            self._normalize_text(k): v for k, v in self.persona_strong_indicators.items()
        }
        self.norm_all_indicators = {**self.norm_empresa_indicators, **self.norm_persona_indicators}
        self.all_indicator_keys = sorted(self.norm_all_indicators.keys(), key=len, reverse=True)
        self.golden_rule_terminos_norm = {
            self._normalize_text(t) for t in self.golden_rule_terminos
        }

        if self.all_indicator_keys:
            self.strong_indicators_regex = re.compile(
                r"\b(" + "|".join(re.escape(term) for term in self.all_indicator_keys) + r")\b",
                re.IGNORECASE,
            )
        else:
            self.strong_indicators_regex = re.compile(r"(?!)")

    def _compile_patterns(self) -> None:
        self.empresa_patterns = self.config.get("empresa_patterns", {})
        self.compiled_empresa_patterns = {
            re.compile(p, re.IGNORECASE): w for p, w in self.empresa_patterns.items()
        }

    def _normalize_series_vectorized(self, series: pd.Series) -> pd.Series:
        series = series.fillna("")
        series = (
            series.str.upper()
            .str.normalize("NFD")
            .str.encode("ascii", errors="ignore")
            .str.decode("ascii")
            .str.replace(r"[^\w\s\.]", " ", regex=True)
            .str.replace(r"\s+", " ", regex=True)
            .str.strip()
        )
        return series

    def entrenar(
        self,
        df_empresas: pd.DataFrame,
        empresa_col: str,
        df_personas: pd.DataFrame,
        persona_col: str,
    ) -> None:
        print("🧠 Iniciando entrenamiento del motor estadístico...")
        start_time = time.time()

        empresas_norm = self._normalize_series_vectorized(df_empresas[empresa_col])
        personas_norm = self._normalize_series_vectorized(df_personas[persona_col])

        empresas_residual = empresas_norm.str.replace(self.strong_indicators_regex, " ", regex=True)
        personas_residual = personas_norm.str.replace(self.strong_indicators_regex, " ", regex=True)

        all_empresa_text = " ".join(empresas_residual.dropna())
        all_persona_text = " ".join(personas_residual.dropna())

        empresa_words = Counter(all_empresa_text.split())
        persona_words = Counter(all_persona_text.split())

        total_empresa_words = sum(empresa_words.values())
        total_persona_words = sum(persona_words.values())
        all_words = set(empresa_words.keys()) | set(persona_words.keys())
        valid_words = {w for w in all_words if len(w) > 2 and not w.isdigit()}

        for word in valid_words:
            p_word_empresa = (empresa_words.get(word, 0) + 1) / (
                total_empresa_words + len(all_words)
            )
            p_word_persona = (persona_words.get(word, 0) + 1) / (
                total_persona_words + len(all_words)
            )
            self.word_weights[word] = np.log(p_word_empresa / p_word_persona) * 0.5

        self.is_trained = True
        self._stat_score_cache.clear()
        elapsed = time.time() - start_time
        print(
            f"✅ Entrenamiento completado en {elapsed:.2f}s. {len(self.word_weights):,} pesos aprendidos."
        )

    def _calculate_stat_scores_vectorized(self, residual_texts: pd.Series) -> pd.Series:
        if not self.is_trained:
            return pd.Series(0.0, index=residual_texts.index)

        def calc_score(text: str) -> float:
            if not text or text in self._stat_score_cache:
                return self._stat_score_cache.get(text, 0.0)
            words = text.split()
            score = sum(self.word_weights.get(w, 0) for w in words)
            if len(self._stat_score_cache) < 10000:
                self._stat_score_cache[text] = score
            return score

        scores = residual_texts.apply(calc_score)
        max_stat = self.config.get("max_stat_score", 20)
        return scores.clip(lower=-max_stat, upper=max_stat)

    def clasificar_lote(
        self, names_series: pd.Series, return_debug_info: bool = False
    ) -> pd.DataFrame:
        n_records = len(names_series)
        print(f"🔄 Procesando lote de {n_records:,} registros...")
        start_time = time.time()

        df = pd.DataFrame({"original_name": names_series.values})  # .values para resetear índice
        df["normalized_name"] = self._normalize_series_vectorized(
            names_series.reset_index(drop=True)
        )

        # Indicadores fuertes
        df["found_terms_list"] = df["normalized_name"].str.findall(self.strong_indicators_regex)

        # Vectorización: str.len() funciona sobre Series de listas en pandas ≥1.0
        # (equivalencia validada en test_vectorization_equivalence::test_longitud_*)
        if df["found_terms_list"].str.len().sum() > 0:
            exploded = df[["found_terms_list"]].explode("found_terms_list")
            exploded["score"] = exploded["found_terms_list"].map(self.norm_all_indicators)
            rule_scores_indicators = exploded.groupby(exploded.index)["score"].sum()
            df["puntaje_indicadores"] = rule_scores_indicators.reindex(df.index, fill_value=0)
        else:
            df["puntaje_indicadores"] = 0

        # Patrones
        df["puntaje_patrones"] = 0
        for pattern, weight in self.compiled_empresa_patterns.items():
            matches = df["normalized_name"].str.contains(pattern, regex=True, na=False)
            df.loc[matches, "puntaje_patrones"] += weight

        df["puntaje_reglas"] = df["puntaje_indicadores"] + df["puntaje_patrones"]

        # Motor estadístico
        if self.is_trained:
            df["residual_name"] = (
                df["normalized_name"]
                .str.replace(self.strong_indicators_regex, " ", regex=True)
                .str.strip()
            )
            df["puntaje_estadistico"] = self._calculate_stat_scores_vectorized(df["residual_name"])
        else:
            df["puntaje_estadistico"] = 0

        # Regla de oro
        if self.golden_rule_terminos_norm:
            df["regla_oro_aplicada"] = df["found_terms_list"].apply(
                lambda terms: bool(self.golden_rule_terminos_norm.intersection(terms))
            )
        else:
            df["regla_oro_aplicada"] = False

        # Clasificación
        df["puntaje_total"] = df["puntaje_reglas"] + df["puntaje_estadistico"]
        df.loc[df["regla_oro_aplicada"], "puntaje_total"] += 100

        conditions = [
            df["puntaje_total"] > self.config["umbral_empresa"],
            df["puntaje_total"] < self.config["umbral_persona"],
            df["normalized_name"].str.len() < 2,
        ]
        choices = ["empresa", "persona", "invalido"]
        df["clasificacion"] = np.select(conditions, choices, default="incierto")

        # Formateo (vectorizado, igual razón que arriba)
        if df["found_terms_list"].str.len().sum() > 0:
            exploded_formatted = df[["found_terms_list"]].explode("found_terms_list")
            exploded_formatted["weight"] = exploded_formatted["found_terms_list"].map(
                self.norm_all_indicators
            )
            exploded_formatted["formatted"] = (
                exploded_formatted["found_terms_list"]
                + "("
                + exploded_formatted["weight"].astype(str)
                + ")"
            )
            terms_formatted = exploded_formatted.groupby(exploded_formatted.index)[
                "formatted"
            ].apply(list)
            df["terminos_encontrados"] = terms_formatted.reindex(df.index, fill_value=[])
        else:
            df["terminos_encontrados"] = [[] for _ in range(len(df))]

        output_columns = [
            "clasificacion",
            "puntaje_total",
            "terminos_encontrados",
            "puntaje_reglas",
            "puntaje_estadistico",
            "regla_oro_aplicada",
        ]

        result_df = df[output_columns].copy()
        for col in ["puntaje_total", "puntaje_reglas", "puntaje_estadistico"]:
            result_df[col] = result_df[col].round(2)

        elapsed = time.time() - start_time
        print(f"✅ Lote procesado en {elapsed:.2f}s ({n_records / elapsed:,.0f} registros/segundo)")

        return result_df
