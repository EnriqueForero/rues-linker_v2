"""
evaluation.ground_truth — record_linkage_pipeline

Componentes:
    - class GroundTruthGenerator  (origen: notebook celda [131])
    - class GroundTruthEvaluator  (origen: notebook celda [165])

NOTA: Lógica de negocio preservada exactamente como en el notebook
fuente. Solo se agregan imports, docstring de módulo y se eliminan
directivas de Jupyter (%%time, !pip, etc.). Ver MIGRATION_LOG.md.
"""

from __future__ import annotations

import tempfile
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from ..exporters._spreadsheet import prepare_spreadsheet_data
from ..optimization.engine import OptimizationEngine
from ..optimization.parameters import ParameterSpace
from ..utils.logger import CustomLogger
from ..utils.performance import track_performance


class GroundTruthGenerator:
    """
    Genera muestras estratificadas de pares para crear ground truth.
    Implementa muestreo inteligente para cubrir casos fáciles, difíciles y ambiguos.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.logger = CustomLogger("GroundTruthGenerator")
        self.similarity_calc = None  # Lazy loading

    @property
    def similarity_calculator(self):
        """Obtener calculador de similitud (lazy loading)."""
        if self.similarity_calc is None:
            # Reutilizar el calculador de la sección 3
            self.similarity_calc = {
                "name": lambda x, y: fuzz.token_set_ratio(x, y) / 100.0,
                "nit": lambda x, y: (
                    1 - (Levenshtein.distance(x, y) / max(len(x), len(y))) if x and y else 0
                ),
            }
        return self.similarity_calc

    @track_performance("Generación de ground truth")
    def create_intelligent_sample(
        self, df: pd.DataFrame, n_samples: int = 1000, output_path: str | None = None
    ) -> pd.DataFrame:
        """
        Crear ground truth con casos representativos estratificados.

        Args:
            df: DataFrame con datos preprocesados
            n_samples: Número de pares a generar
            output_path: Ruta para guardar el ground truth

        Returns:
            DataFrame con pares para evaluación manual
        """
        self.logger.info(f"Generando {n_samples} pares para ground truth")

        samples = []

        # Distribución de casos
        distributions = {
            "easy_positive": 0.20,  # Casos fáciles positivos (mismo NIT)
            "difficult_positive": 0.30,  # Casos difíciles (alta similitud, NITs diferentes)
            "ambiguous": 0.30,  # Casos ambiguos (similitud media)
            "easy_negative": 0.20,  # Negativos claros (baja similitud)
        }

        # Generar cada tipo de casos
        for case_type, proportion in distributions.items():
            n_cases = int(n_samples * proportion)
            self.logger.info(f"Generando {n_cases} casos tipo '{case_type}'")

            if case_type == "easy_positive":
                cases = self._generate_easy_positives(df, n_cases)
            elif case_type == "difficult_positive":
                cases = self._generate_difficult_positives(df, n_cases)
            elif case_type == "ambiguous":
                cases = self._generate_ambiguous_cases(df, n_cases)
            else:  # easy_negative
                cases = self._generate_easy_negatives(df, n_cases)

            samples.append(cases)

        # Combinar todos los casos
        ground_truth = pd.concat(samples, ignore_index=True)

        # Agregar columnas para revisión manual
        ground_truth["IS_MATCH"] = None  # Para completar manualmente
        ground_truth["REVIEWER"] = None
        ground_truth["REVIEW_DATE"] = None
        ground_truth["CONFIDENCE"] = None  # Alta/Media/Baja
        ground_truth["NOTES"] = None

        # Mezclar aleatoriamente para evitar sesgo en revisión
        ground_truth = ground_truth.sample(frac=1, random_state=42).reset_index(drop=True)

        # Guardar si se especifica ruta
        if output_path:
            self._save_ground_truth(ground_truth, output_path)

        self.logger.info(f"Ground truth generado con {len(ground_truth)} pares")

        return ground_truth

    def _generate_easy_positives(self, df: pd.DataFrame, n_cases: int) -> pd.DataFrame:
        """Generar casos fáciles positivos (mismo NIT)."""
        # Encontrar grupos con mismo NIT
        if "NIT_OK" not in df.columns:
            return pd.DataFrame()

        # Agrupar por NIT
        nit_groups = (
            df[df["NIT_OK"].notna()]
            .groupby("NIT_OK")
            .filter(
                lambda x: len(x) >= 2 and len(x) <= 10  # Grupos manejables
            )
        )

        if len(nit_groups) == 0:
            return pd.DataFrame()

        # Generar pares
        pairs = []
        nit_values = nit_groups["NIT_OK"].value_counts().index[: n_cases * 2]

        for nit in nit_values:
            group = nit_groups[nit_groups["NIT_OK"] == nit]
            if len(group) >= 2:
                # Tomar 2 registros aleatorios
                sample = group.sample(n=min(2, len(group)), random_state=42)
                if len(sample) == 2:
                    pair = self._create_pair_record(sample.iloc[0], sample.iloc[1], "easy_positive")
                    pairs.append(pair)

                    if len(pairs) >= n_cases:
                        break

        return pd.DataFrame(pairs)

    def _generate_difficult_positives(self, df: pd.DataFrame, n_cases: int) -> pd.DataFrame:
        """Generar casos difíciles (alta similitud de nombre, NITs diferentes)."""
        # Muestrear registros para eficiencia
        sample_size = min(10000, len(df))
        df_sample = df.sample(n=sample_size, random_state=42)

        pairs: list[dict[str, Any]] = []
        processed: set[Any] = set()

        # Buscar pares con alta similitud de nombre
        for i, record1 in df_sample.iterrows():
            if i in processed or len(pairs) >= n_cases:
                break

            # Calcular similitudes con otros registros
            for j, record2 in df_sample.iterrows():
                if i >= j or j in processed:
                    continue

                # Verificar que tengan NITs diferentes
                if record1.get("NIT_OK") == record2.get("NIT_OK") and pd.notna(
                    record1.get("NIT_OK")
                ):
                    continue

                # Calcular similitud de nombre
                name1 = record1.get("NOMBRE_LIMPIO", "")
                name2 = record2.get("NOMBRE_LIMPIO", "")

                if name1 and name2:
                    similarity = self.similarity_calculator["name"](name1, name2)

                    # Alta similitud (0.85+)
                    if similarity >= 0.85:
                        pair = self._create_pair_record(record1, record2, "difficult_positive")
                        pairs.append(pair)
                        processed.add(i)
                        processed.add(j)

                        if len(pairs) >= n_cases:
                            break

        return pd.DataFrame(pairs)

    def _generate_ambiguous_cases(self, df: pd.DataFrame, n_cases: int) -> pd.DataFrame:
        """Generar casos ambiguos (similitud media)."""
        # Similar a difficult_positives pero con rango de similitud 0.60-0.85
        sample_size = min(10000, len(df))
        df_sample = df.sample(n=sample_size, random_state=42)

        pairs: list[dict[str, Any]] = []
        processed: set[Any] = set()

        for i, record1 in df_sample.iterrows():
            if i in processed or len(pairs) >= n_cases:
                break

            for j, record2 in df_sample.iterrows():
                if i >= j or j in processed:
                    continue

                name1 = record1.get("NOMBRE_LIMPIO", "")
                name2 = record2.get("NOMBRE_LIMPIO", "")

                if name1 and name2:
                    similarity = self.similarity_calculator["name"](name1, name2)

                    # Similitud media (0.60-0.85)
                    if 0.60 <= similarity < 0.85:
                        pair = self._create_pair_record(record1, record2, "ambiguous")
                        pairs.append(pair)
                        processed.add(i)
                        processed.add(j)

                        if len(pairs) >= n_cases:
                            break

        return pd.DataFrame(pairs)

    def _generate_easy_negatives(self, df: pd.DataFrame, n_cases: int) -> pd.DataFrame:
        """Generar negativos claros (baja similitud)."""
        # Tomar pares aleatorios que probablemente no matcheen
        pairs = []

        # Estrategia: tomar registros de diferentes fuentes con nombres muy diferentes
        if "SRC" in df.columns:
            sources = df["SRC"].unique()
            if len(sources) >= 2:
                source1, source2 = sources[:2]
                df1 = df[df["SRC"] == source1].sample(n=min(n_cases, len(df[df["SRC"] == source1])))
                df2 = df[df["SRC"] == source2].sample(n=min(n_cases, len(df[df["SRC"] == source2])))

                for i in range(min(len(df1), len(df2), n_cases)):
                    pair = self._create_pair_record(df1.iloc[i], df2.iloc[i], "easy_negative")
                    pairs.append(pair)

        # Si no hay suficientes, completar con pares aleatorios
        while len(pairs) < n_cases:
            idx1, idx2 = np.random.choice(len(df), 2, replace=False)
            pair = self._create_pair_record(df.iloc[idx1], df.iloc[idx2], "easy_negative")
            pairs.append(pair)

        return pd.DataFrame(pairs[:n_cases])

    def _create_pair_record(
        self, record1: pd.Series, record2: pd.Series, case_type: str
    ) -> dict[str, Any]:
        """Crear registro de par para ground truth."""
        # Calcular similitudes
        name_sim = 0.0
        if "NOMBRE_LIMPIO" in record1 and "NOMBRE_LIMPIO" in record2:
            name1 = str(record1["NOMBRE_LIMPIO"])
            name2 = str(record2["NOMBRE_LIMPIO"])
            if name1 and name2:
                name_sim = self.similarity_calculator["name"](name1, name2)

        nit_sim = 0.0
        if "NIT_OK" in record1 and "NIT_OK" in record2:
            nit1 = str(record1["NIT_OK"])
            nit2 = str(record2["NIT_OK"])
            if nit1 and nit2 and nit1 != "nan" and nit2 != "nan":
                nit_sim = self.similarity_calculator["nit"](nit1, nit2)

        return {
            "ID_1": record1.name,
            "ID_2": record2.name,
            "NIT_1": record1.get("NIT", ""),
            "NIT_2": record2.get("NIT", ""),
            "RAZON_SOCIAL_1": record1.get("RAZON_SOCIAL", ""),
            "RAZON_SOCIAL_2": record2.get("RAZON_SOCIAL", ""),
            "SRC_1": record1.get("SRC", ""),
            "SRC_2": record2.get("SRC", ""),
            "CASE_TYPE": case_type,
            "NAME_SIMILARITY": round(name_sim, 4),
            "NIT_SIMILARITY": round(nit_sim, 4),
        }

    def _save_ground_truth(self, ground_truth: pd.DataFrame, output_path: str):
        """Guardar ground truth con formato adecuado para revisión."""
        # Ordenar columnas para facilitar revisión
        cols_order = [
            "ID_1",
            "ID_2",
            "IS_MATCH",
            "RAZON_SOCIAL_1",
            "RAZON_SOCIAL_2",
            "NIT_1",
            "NIT_2",
            "NAME_SIMILARITY",
            "NIT_SIMILARITY",
            "CASE_TYPE",
            "SRC_1",
            "SRC_2",
            "CONFIDENCE",
            "REVIEWER",
            "REVIEW_DATE",
            "NOTES",
        ]

        ground_truth = ground_truth[cols_order]
        safe_ground_truth = prepare_spreadsheet_data(ground_truth)

        # Guardar en Excel para facilitar revisión manual
        if output_path.endswith(".xlsx"):
            safe_ground_truth.to_excel(output_path, index=False)
        else:
            safe_ground_truth.to_csv(output_path, index=False)

        self.logger.info(f"Ground truth guardado en: {output_path}")


class GroundTruthEvaluator:
    """
    Evalúa los resultados de deduplicación contra un ground truth.

    Calcula métricas a nivel de pares de registros, que es la forma
    estándar de evaluar algoritmos de entity resolution.
    """

    def __init__(self):
        self.last_evaluation = {}

    def evaluate(
        self,
        predicted_df: pd.DataFrame,
        ground_truth_df: pd.DataFrame,
        group_col: str = "ID_GRUPO",
        truth_col: str = "ID_GRUPO_ESPERADO",
    ) -> dict[str, float]:
        """
        Evalúa las predicciones contra el ground truth.

        Args:
            predicted_df: DataFrame con los grupos predichos
            ground_truth_df: DataFrame con la verdad absoluta
            group_col: Columna con los grupos predichos
            truth_col: Columna con los grupos reales (ID_GRUPO_ESPERADO)

        Returns:
            Diccionario con métricas de evaluación
        """
        # Crear ground truth basado en ID_GRUPO_ESPERADO
        # Dos registros son del mismo grupo si tienen el mismo ID_GRUPO_ESPERADO
        ground_truth_groups: dict[str, list[Any]] = {}
        for idx, row in ground_truth_df.iterrows():
            truth_val = str(row[truth_col]).strip() if pd.notna(row[truth_col]) else None
            if truth_val:
                if truth_val not in ground_truth_groups:
                    ground_truth_groups[truth_val] = []
                ground_truth_groups[truth_val].append(idx)

        # Calcular métricas a nivel de pares
        metrics = self._calculate_pairwise_metrics(
            predicted_df[group_col].to_dict(), ground_truth_groups
        )

        # Calcular métricas adicionales
        metrics.update(
            self._calculate_clustering_metrics(predicted_df, ground_truth_groups, group_col)
        )

        self.last_evaluation = metrics
        return metrics

    def _calculate_pairwise_metrics(
        self, predicted_groups: dict[Any, Any], ground_truth_groups: dict[str, list[Any]]
    ) -> dict[str, float]:
        """
        Calcula precision, recall y F1 a nivel de pares.
        """
        # Convertir ground truth a formato de índices
        true_pairs: set[tuple[Any, Any]] = set()
        for group_indices in ground_truth_groups.values():
            if len(group_indices) > 1:
                for pair in combinations(sorted(group_indices), 2):
                    true_pairs.add(pair)

        # Extraer pares predichos
        pred_pairs: set[tuple[Any, Any]] = set()
        groups_by_id: dict[Any, list[Any]] = {}
        for idx, group_id in predicted_groups.items():
            if group_id not in groups_by_id:
                groups_by_id[group_id] = []
            groups_by_id[group_id].append(idx)

        for group_indices in groups_by_id.values():
            if len(group_indices) > 1:
                for pair in combinations(sorted(group_indices), 2):
                    pred_pairs.add(pair)

        # Calcular métricas
        tp = len(true_pairs & pred_pairs)  # True positives
        fp = len(pred_pairs - true_pairs)  # False positives
        fn = len(true_pairs - pred_pairs)  # False negatives

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        return {
            "precision": precision,
            "recall": recall,
            "f1_score": f1,
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "total_true_pairs": len(true_pairs),
            "total_pred_pairs": len(pred_pairs),
        }

    def _calculate_clustering_metrics(
        self, predicted_df: pd.DataFrame, ground_truth_groups: dict[str, list[Any]], group_col: str
    ) -> dict[str, float]:
        """
        Calcula métricas adicionales sobre la calidad del clustering.
        """
        # Número de grupos
        n_true_groups = len(ground_truth_groups)
        n_pred_groups = predicted_df[group_col].nunique()

        # Tamaño promedio de grupos
        pred_group_sizes = predicted_df[group_col].value_counts()
        avg_pred_size = pred_group_sizes.mean()

        true_group_sizes = [len(indices) for indices in ground_truth_groups.values()]
        avg_true_size = np.mean(true_group_sizes) if true_group_sizes else 0

        # Distribución de tamaños
        pred_singles = (pred_group_sizes == 1).sum()
        true_singles = sum(1 for size in true_group_sizes if size == 1)

        return {
            "n_true_groups": float(n_true_groups),
            "n_pred_groups": float(n_pred_groups),
            "avg_true_group_size": float(avg_true_size),
            "avg_pred_group_size": float(avg_pred_size),
            "pred_singleton_groups": float(pred_singles),
            "true_singleton_groups": float(true_singles),
            "group_count_ratio": float(n_pred_groups / n_true_groups if n_true_groups > 0 else 0),
        }

    def analyze_errors(
        self,
        predicted_df: pd.DataFrame,
        ground_truth_df: pd.DataFrame,
        group_col: str = "ID_GRUPO",
        truth_col: str = "ID_GRUPO_ESPERADO",
        n_examples: int = 5,
    ) -> dict[str, pd.DataFrame]:
        """
        Analiza los errores de predicción y devuelve ejemplos.
        """
        # Preparar datos
        analysis_df = ground_truth_df.copy()
        analysis_df["PREDICTED_GROUP"] = predicted_df[group_col]

        # Crear grupos verdaderos.
        # Vectorización: notna + astype(str).str.strip() en lugar de apply
        # (equivalencia validada en test_vectorization_equivalence::test_true_group_*)
        _truth_series = analysis_df[truth_col]
        _mask_notna = _truth_series.notna()
        analysis_df["TRUE_GROUP"] = "UNKNOWN"
        if _mask_notna.any():
            analysis_df.loc[_mask_notna, "TRUE_GROUP"] = (
                _truth_series.loc[_mask_notna].astype(str).str.strip()
            )

        errors: dict[str, list[pd.DataFrame]] = {
            "false_positives": [],
            "false_negatives": [],
        }

        # Buscar falsos positivos (unidos incorrectamente)
        for _pred_group, group_df in analysis_df.groupby("PREDICTED_GROUP"):
            true_groups = group_df["TRUE_GROUP"].unique()
            if len(true_groups) > 1:
                # Este grupo predicho contiene registros de múltiples grupos reales
                errors["false_positives"].append(group_df)

        # Buscar falsos negativos (no unidos cuando deberían)
        for true_group, group_df in analysis_df.groupby("TRUE_GROUP"):
            pred_groups = group_df["PREDICTED_GROUP"].unique()
            if len(pred_groups) > 1 and true_group != "UNKNOWN":
                # Este grupo real está dividido en múltiples grupos predichos
                errors["false_negatives"].append(group_df)

        # Preparar ejemplos
        error_examples: dict[str, pd.DataFrame] = {}

        if errors["false_positives"]:
            fp_df = pd.concat(errors["false_positives"][:n_examples])
            error_examples["false_positives"] = fp_df[
                ["NIT", "RAZON_SOCIAL", "TRUE_GROUP", "PREDICTED_GROUP"]
            ]

        if errors["false_negatives"]:
            fn_df = pd.concat(errors["false_negatives"][:n_examples])
            error_examples["false_negatives"] = fn_df[
                ["NIT", "RAZON_SOCIAL", "TRUE_GROUP", "PREDICTED_GROUP"]
            ]

        return error_examples

    def cross_validate(
        self,
        ground_truth_df: pd.DataFrame,
        parameter_space: ParameterSpace,
        params: dict[str, Any],
        n_folds: int = 5,
    ) -> dict[str, float]:
        """
        Realiza validación cruzada para obtener métricas más robustas.

        Los artefactos de cada fold se escriben en directorios creados por el
        sistema bajo una raíz temporal privada. Esto evita reutilizar o borrar
        un ``cv_fold_N`` preexistente en el directorio de trabajo.
        """
        from sklearn.model_selection import KFold

        # Preparar folds
        kf = KFold(n_splits=n_folds, shuffle=True, random_state=42)
        indices = np.arange(len(ground_truth_df))

        cv_scores = []

        with tempfile.TemporaryDirectory(prefix="rues_linker_cv_") as cv_workspace:
            for fold, (_train_idx, test_idx) in enumerate(kf.split(indices)):
                # Dividir datos
                test_df = ground_truth_df.iloc[test_idx].copy()

                # Cada contexto es propietario exclusivo de la ruta que
                # elimina. El sufijo aleatorio impide colisiones entre
                # ejecuciones concurrentes y TemporaryDirectory no sigue un
                # symlink que un componente interno deje en el workspace.
                with tempfile.TemporaryDirectory(
                    prefix=f"cv_fold_{fold}_", dir=cv_workspace
                ) as fold_output_dir:
                    engine = OptimizationEngine(
                        ground_truth_df=test_df,
                        parameter_space=parameter_space,
                        output_dir=fold_output_dir,
                        enable_checkpoints=False,
                    )

                    metrics = engine.evaluate_single_config(params)
                    cv_scores.append(metrics["f1_score"])

        return {
            "cv_mean_f1": np.mean(cv_scores),
            "cv_std_f1": np.std(cv_scores),
            "cv_scores": cv_scores,
        }
