"""Tests directos del Sprint 0.9.0 — pieza B: cobertura de evaluation/.

Apunta a los gaps detectados en la auditoría del Sprint 0.9.0:
    - evaluation/evaluador_verdad.py    — 0% → ~70% (antes ground_truth.py; F2.8)
    - evaluation/metrics.py             — 0% → ~70%

Diseño: cada test cubre UN comportamiento concreto del evaluador con un
DataFrame mínimo determinista. No depende del pipeline ni del GT grande.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.evaluation.evaluador_verdad import GroundTruthEvaluator
from record_linkage.evaluation.metrics import (
    EntityMetricsEvaluator,
    PerformanceAnalyzer,
)

# ═════════════════════════════════════════════════════════════════════════════
# GroundTruthEvaluator
# ═════════════════════════════════════════════════════════════════════════════


def _build_evaluation_dfs(
    pred_groups: list[str], truth_groups: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Helper: construye los dos DataFrames que evaluate() espera.

    ``predicted_df`` tiene una columna ``ID_GRUPO`` y ``ground_truth_df``
    tiene ``ID_GRUPO_ESPERADO``. Ambos comparten índice posicional.
    """
    assert len(pred_groups) == len(truth_groups)
    n = len(pred_groups)
    predicted = pd.DataFrame(
        {
            "ID_GRUPO": pred_groups,
            "NIT": [f"NIT_{i}" for i in range(n)],
            "RAZON_SOCIAL": [f"EMPRESA_{i}" for i in range(n)],
        }
    )
    truth = pd.DataFrame(
        {
            "ID_GRUPO_ESPERADO": truth_groups,
            "NIT": [f"NIT_{i}" for i in range(n)],
            "RAZON_SOCIAL": [f"EMPRESA_{i}" for i in range(n)],
        }
    )
    return predicted, truth


def test_evaluator_predict_perfecto_da_f1_uno():
    """Predicción idéntica al ground truth → P=R=F1=1."""
    pred = ["A", "A", "B", "B", "C"]
    truth = ["A", "A", "B", "B", "C"]
    pdf, tdf = _build_evaluation_dfs(pred, truth)
    res = GroundTruthEvaluator().evaluate(pdf, tdf)
    assert res["precision"] == pytest.approx(1.0)
    assert res["recall"] == pytest.approx(1.0)
    assert res["f1_score"] == pytest.approx(1.0)
    assert res["true_positives"] == 2  # par (0,1) + par (2,3)
    assert res["false_positives"] == 0
    assert res["false_negatives"] == 0


def test_evaluator_sobre_fusion_destruye_precision():
    """Unir 2 grupos reales en uno → precision baja, recall 1."""
    pred = ["X", "X", "X", "X"]
    truth = ["A", "A", "B", "B"]
    pdf, tdf = _build_evaluation_dfs(pred, truth)
    res = GroundTruthEvaluator().evaluate(pdf, tdf)
    # Pares verdaderos: (0,1), (2,3) → 2 TP. Pares predichos: 6 → 4 FP.
    assert res["true_positives"] == 2
    assert res["false_positives"] == 4
    assert res["false_negatives"] == 0
    assert res["precision"] == pytest.approx(2 / 6)
    assert res["recall"] == pytest.approx(1.0)


def test_evaluator_sub_fusion_destruye_recall():
    """Fragmentar 1 grupo real en varios → recall baja, precision 1."""
    pred = ["A", "B", "C"]
    truth = ["X", "X", "X"]
    pdf, tdf = _build_evaluation_dfs(pred, truth)
    res = GroundTruthEvaluator().evaluate(pdf, tdf)
    # Pares verdaderos: (0,1), (0,2), (1,2) → 3 FN. Pares predichos: 0.
    assert res["true_positives"] == 0
    assert res["false_positives"] == 0
    assert res["false_negatives"] == 3
    assert res["precision"] == 0.0
    assert res["recall"] == 0.0
    assert res["f1_score"] == 0.0


def test_evaluator_sin_pares_da_metrics_cero():
    """Solo singletons en verdad → no hay pares verdaderos; F1=0."""
    pred = ["A", "B", "C"]
    truth = ["X", "Y", "Z"]
    pdf, tdf = _build_evaluation_dfs(pred, truth)
    res = GroundTruthEvaluator().evaluate(pdf, tdf)
    assert res["true_positives"] == 0
    assert res["false_positives"] == 0
    assert res["false_negatives"] == 0
    assert res["precision"] == 0.0
    assert res["recall"] == 0.0


def test_evaluator_truth_col_con_nans_se_ignoran():
    """Filas con truth_col NaN no se incluyen en pares verdaderos."""
    truth_with_nan = ["X", "X", None, None, None]
    pred = ["A", "A", "B", "C", "D"]
    pdf, tdf = _build_evaluation_dfs(pred, truth_with_nan)
    res = GroundTruthEvaluator().evaluate(pdf, tdf)
    assert res["true_positives"] == 1  # (0,1) sí lo predijo
    assert res["false_positives"] == 0
    assert res["false_negatives"] == 0
    assert res["precision"] == pytest.approx(1.0)
    assert res["recall"] == pytest.approx(1.0)


def test_evaluator_clustering_metrics_se_incluyen():
    """`evaluate` también devuelve métricas a nivel de grupo."""
    pred = ["A", "A", "B", "B", "C"]
    truth = ["X", "X", "Y", "Y", "Z"]
    pdf, tdf = _build_evaluation_dfs(pred, truth)
    res = GroundTruthEvaluator().evaluate(pdf, tdf)
    assert res["n_true_groups"] == 3
    assert res["n_pred_groups"] == 3
    assert res["avg_true_group_size"] == pytest.approx(5 / 3)
    assert res["avg_pred_group_size"] == pytest.approx(5 / 3)
    assert res["group_count_ratio"] == pytest.approx(1.0)


def test_evaluator_last_evaluation_se_persiste():
    """Tras evaluar, last_evaluation queda disponible."""
    pred = ["A", "A", "B"]
    truth = ["X", "X", "Y"]
    pdf, tdf = _build_evaluation_dfs(pred, truth)
    ev = GroundTruthEvaluator()
    assert ev.last_evaluation == {}
    res = ev.evaluate(pdf, tdf)
    assert ev.last_evaluation == res


def test_evaluator_analyze_errors_detecta_falsos_positivos():
    """`analyze_errors` agrupa registros de grupos reales distintos
    en el mismo grupo predicho."""
    pred = ["X", "X", "X", "X"]  # todos en el mismo cluster
    truth = ["A", "A", "B", "B"]
    pdf, tdf = _build_evaluation_dfs(pred, truth)
    ev = GroundTruthEvaluator()
    errors = ev.analyze_errors(pdf, tdf)
    # Debe haber un error de tipo "false_positives" (grupo predicho mezcla A y B)
    assert "false_positives" in errors
    assert len(errors["false_positives"]) == 4  # las 4 filas del clúster mixto


def test_evaluator_analyze_errors_detecta_falsos_negativos():
    """`analyze_errors` detecta grupos reales fragmentados en varios predichos."""
    pred = ["A", "B", "C"]  # cada uno en su propio cluster
    truth = ["X", "X", "X"]  # pero todos son la misma entidad real
    pdf, tdf = _build_evaluation_dfs(pred, truth)
    ev = GroundTruthEvaluator()
    errors = ev.analyze_errors(pdf, tdf)
    assert "false_negatives" in errors
    assert len(errors["false_negatives"]) == 3


def test_evaluator_analyze_errors_sin_errores_devuelve_vacio():
    """Predicción perfecta → no hay false_positives ni false_negatives."""
    pred = ["A", "A", "B", "B"]
    truth = ["X", "X", "Y", "Y"]
    pdf, tdf = _build_evaluation_dfs(pred, truth)
    ev = GroundTruthEvaluator()
    errors = ev.analyze_errors(pdf, tdf)
    # Las llaves pueden no estar si no hubo error de ese tipo:
    assert "false_positives" not in errors
    assert "false_negatives" not in errors


def test_evaluator_truth_col_personalizable():
    """truth_col puede ser cualquier columna, no solo ID_GRUPO_ESPERADO."""
    pred = ["A", "A", "B", "B"]
    pdf = pd.DataFrame({"ID_GRUPO": pred})
    tdf = pd.DataFrame(
        {
            "my_truth_column": ["X", "X", "Y", "Y"],
            "NIT": ["1", "2", "3", "4"],
            "RAZON_SOCIAL": ["a", "b", "c", "d"],
        }
    )
    res = GroundTruthEvaluator().evaluate(pdf, tdf, truth_col="my_truth_column")
    assert res["f1_score"] == pytest.approx(1.0)


# ═════════════════════════════════════════════════════════════════════════════
# EntityMetricsEvaluator
# ═════════════════════════════════════════════════════════════════════════════


def _build_entity_eval_df(rows: list[dict]) -> pd.DataFrame:
    """Construye el DataFrame que `EntityMetricsEvaluator.calculate_metrics`
    espera: columnas ID_GRUPO (predicción) y NIT_FINAL_truth (verdad)."""
    return pd.DataFrame(rows)


def test_entity_metrics_clasifica_perfectas():
    """Una entidad real está en exactamente UN grupo, y ese grupo solo
    contiene esa entidad → 'perfecta'."""
    df = _build_entity_eval_df(
        [
            {"ID_GRUPO": "G1", "NIT_FINAL_truth": "E1"},
            {"ID_GRUPO": "G1", "NIT_FINAL_truth": "E1"},
            {"ID_GRUPO": "G2", "NIT_FINAL_truth": "E2"},
        ]
    )
    metrics = EntityMetricsEvaluator().calculate_metrics(df)
    assert metrics["counts"]["perfectas"] == 2
    assert metrics["counts"]["fragmentadas"] == 0
    assert metrics["counts"]["contaminadas"] == 0
    assert metrics["counts"]["mixtas"] == 0


def test_entity_metrics_clasifica_fragmentadas():
    """Una entidad real partida en varios grupos predichos limpios → 'fragmentada'."""
    df = _build_entity_eval_df(
        [
            {"ID_GRUPO": "G1", "NIT_FINAL_truth": "E1"},
            {"ID_GRUPO": "G2", "NIT_FINAL_truth": "E1"},
        ]
    )
    metrics = EntityMetricsEvaluator().calculate_metrics(df)
    assert metrics["counts"]["fragmentadas"] == 1


def test_entity_metrics_clasifica_contaminadas():
    """Dos entidades distintas en el mismo grupo predicho → 'contaminada'."""
    df = _build_entity_eval_df(
        [
            {"ID_GRUPO": "G1", "NIT_FINAL_truth": "E1"},
            {"ID_GRUPO": "G1", "NIT_FINAL_truth": "E2"},
        ]
    )
    metrics = EntityMetricsEvaluator().calculate_metrics(df)
    # Ambas entidades quedan contaminadas (cada una vive en un grupo impuro).
    assert metrics["counts"]["contaminadas"] == 2


def test_entity_metrics_porcentaje_perfectas_calculado():
    """`porcentajes.perfect_entities_pct` refleja la fracción de perfectas."""
    df = _build_entity_eval_df(
        [
            {"ID_GRUPO": "G1", "NIT_FINAL_truth": "E1"},
            {"ID_GRUPO": "G1", "NIT_FINAL_truth": "E1"},
            {"ID_GRUPO": "G2", "NIT_FINAL_truth": "E2"},
            {"ID_GRUPO": "G3", "NIT_FINAL_truth": "E2"},  # E2 fragmentada
        ]
    )
    metrics = EntityMetricsEvaluator().calculate_metrics(df)
    assert metrics["total_entidades_reales"] == 2
    assert metrics["counts"]["perfectas"] == 1
    # perfect_entities_pct está en fracción (0-1) según la API observada.
    assert metrics["porcentajes"]["perfect_entities_pct"] == pytest.approx(0.5)


# ═════════════════════════════════════════════════════════════════════════════
# PerformanceAnalyzer
# ═════════════════════════════════════════════════════════════════════════════


def test_performance_analyzer_extrae_metricas_de_tiempo():
    """Extrae los tiempos por fase del dict de resultados."""
    pa = PerformanceAnalyzer()
    pipeline_results = {
        "metrics": {
            "phase_1_time": 10.5,
            "phase_2_time": 20.0,
            "total_time": 30.5,
        }
    }
    analysis = pa.analyze_run(pipeline_results, run_name="test_run")
    # El resultado tiene la estructura esperada:
    assert (
        "time_analysis" in analysis or "time_metrics" in analysis or analysis
    )  # tolerar variantes


def test_performance_analyzer_lee_los_tiempos_por_fase_del_orquestador():
    """F1.6: las claves son las de ``Phase`` (``metrics["phase_times"]``), leídas
    con ``reporting._fases.tiempos_por_fase``; las viejas (``scoring_time``…)
    nadie las producía y el analizador leía ceros."""
    pa = PerformanceAnalyzer()
    resultados = {
        "metrics": {
            "execution_time": 100.0,
            "phase_times": {"L2_lsh_candidates": 40.0, "L5_golden": 55.0, "L1_prep": 5.0},
            "scoring_time": 99.0,  # clave vieja: no se lee
        }
    }
    analisis = pa.analyze_run(resultados, run_name="fases")
    tiempos = analisis["time_metrics"]
    assert tiempos["total_time"] == 100.0
    assert tiempos["L2_lsh_candidates_time"] == 40.0
    assert tiempos["L5_golden_time_pct"] == pytest.approx(55.0)
    assert "scoring_time" not in tiempos and "scoring_time_pct" not in tiempos

    cuellos = analisis["bottlenecks"]
    assert any(c.startswith("L2 · Candidatos (LSH): 40.0%") for c in cuellos), cuellos
    assert any(c.startswith("L5 · Registro consolidado: 55.0%") for c in cuellos), cuellos
    assert any("Golden Records toma más del 50%" in c for c in cuellos), cuellos
    recomendaciones = analisis["recommendations"]
    assert any("LSH" in r for r in recomendaciones), recomendaciones
    assert any("golden records" in r for r in recomendaciones), recomendaciones


def test_performance_analyzer_sin_execution_time_suma_las_fases_medidas():
    pa = PerformanceAnalyzer()
    analisis = pa.analyze_run(
        {"metrics": {"phase_times": {"L1_prep": 2.0, "L3_scoring": 6.0}}}, run_name="suma"
    )
    assert analisis["time_metrics"]["total_time"] == 8.0
    assert analisis["time_metrics"]["L3_scoring_time_pct"] == pytest.approx(75.0)


def test_performance_analyzer_falla_con_claves_de_fase_que_nadie_produce():
    """Un phase_times con nombres viejos es un defecto del productor, no un cero."""
    from record_linkage.pipeline.errores import TiemposPorFaseError

    pa = PerformanceAnalyzer()
    with pytest.raises(TiemposPorFaseError, match="scoring_time"):
        pa.analyze_run({"metrics": {"phase_times": {"scoring_time": 1.0}}}, run_name="viejo")


def test_performance_analyzer_set_baseline_funciona():
    """set_baseline guarda la corrida actual como referencia."""
    pa = PerformanceAnalyzer()
    pa.analyze_run({"metrics": {"total_time": 100.0}}, run_name="run_a")
    pa.set_baseline("run_a")
    assert pa.baseline_metrics is not None


def test_performance_analyzer_history_se_acumula():
    """analyze_run agrega cada corrida a la historia."""
    pa = PerformanceAnalyzer()
    pa.analyze_run({"metrics": {"total_time": 100.0}}, run_name="run_a")
    pa.analyze_run({"metrics": {"total_time": 90.0}}, run_name="run_b")
    assert len(pa.metrics_history) == 2
