"""evaluation.pairwise — métricas de calidad de linkage a nivel de pares.

Nuevo en v2.2.0. Este módulo cierra el agujero más grande del paquete:
hasta v2.1.0 NINGÚN test medía si el sistema agrupa bien. Aquí se calcula
la métrica estándar de record linkage (pairwise precision / recall / F1)
comparando los clusters predichos contra una verdad conocida (ground truth).

Definición:
    Un "par positivo" es un par de registros que pertenecen a la misma
    entidad real. El sistema acierta (TP) si predice juntos dos registros
    que la verdad dice que van juntos.

    precision = TP / (TP + FP)   ¿de lo que uní, cuánto estaba bien unido?
    recall    = TP / (TP + FN)   ¿de lo que debía unir, cuánto uní?
    F1        = media armónica de ambos

Contexto: Google Colab Free (~12 GB RAM). El cálculo de pares es O(Σ kᵢ²)
sobre el tamaño de cada grupo, no O(n²) global, por lo que es viable para
los tamaños de validación (miles de registros). Para validar a escala de
millones se debe muestrear la verdad por bloques.

Author: Auditoría v2.2.0  Date: 2026-05-21  Version: 2.2.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PairwiseMetrics:
    """Resultado de la evaluación de calidad de linkage por pares.

    Attributes:
        true_pairs: Nº de pares que la verdad dice que van juntos.
        pred_pairs: Nº de pares que el sistema unió.
        tp: Verdaderos positivos (pares correctamente unidos).
        fp: Falsos positivos (pares unidos que NO debían — sobre-fusión).
        fn: Falsos negativos (pares que debían unirse y no — fragmentación).
        precision: TP / (TP + FP).
        recall: TP / (TP + FN).
        f1: Media armónica de precision y recall.
        n_records: Nº de registros evaluados.
        n_true_groups: Nº de grupos en la verdad.
        n_pred_groups: Nº de grupos predichos.
    """

    true_pairs: int
    pred_pairs: int
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    n_records: int
    n_true_groups: int
    n_pred_groups: int
    extra: dict[str, float] = field(default_factory=dict)

    def resumen(self) -> str:
        """Devuelve un reporte legible de una sola pieza."""
        ancho = 55
        linea = "=" * ancho
        return (
            f"{linea}\n"
            f"  CALIDAD DE LINKAGE — pairwise\n"
            f"{linea}\n"
            f"  Registros:            {self.n_records}\n"
            f"  Grupos verdad:        {self.n_true_groups}\n"
            f"  Grupos predichos:     {self.n_pred_groups}\n"
            f"{'-' * ancho}\n"
            f"  Pares verdaderos:     {self.true_pairs}\n"
            f"  Pares predichos:      {self.pred_pairs}\n"
            f"  TP (aciertos):        {self.tp}\n"
            f"  FP (sobre-fusión):    {self.fp}\n"
            f"  FN (fragmentación):   {self.fn}\n"
            f"{'-' * ancho}\n"
            f"  PRECISION:            {self.precision:.3f}\n"
            f"  RECALL:               {self.recall:.3f}\n"
            f"  F1:                   {self.f1:.3f}\n"
            f"{linea}"
        )


def _same_group_pairs(labels: np.ndarray) -> set[tuple[int, int]]:
    """Genera el conjunto de pares (i, j) con i < j que comparten etiqueta.

    Agrupa por etiqueta primero (O(n)) y solo combina dentro de cada grupo,
    de modo que el coste es O(Σ kᵢ²) y no O(n²). Para grupos de tamaño
    razonable esto es muy inferior al producto cartesiano completo.

    Args:
        labels: Array de etiquetas de grupo, una por registro (posición = id).

    Returns:
        Conjunto de tuplas (i, j) con i < j que están en el mismo grupo.
    """
    por_grupo: dict[object, list[int]] = {}
    for idx, etiqueta in enumerate(labels):
        por_grupo.setdefault(etiqueta, []).append(idx)
    pares: set[tuple[int, int]] = set()
    for indices in por_grupo.values():
        if len(indices) > 1:
            pares.update(combinations(indices, 2))
    return pares


def evaluar_pares(
    truth_labels: pd.Series | np.ndarray | list,
    pred_labels: pd.Series | np.ndarray | list,
) -> PairwiseMetrics:
    """Calcula precision/recall/F1 de linkage a nivel de pares.

    Ambos argumentos deben estar alineados posición a posición: el registro i
    tiene etiqueta de verdad ``truth_labels[i]`` y predicha ``pred_labels[i]``.

    Args:
        truth_labels: Etiqueta de grupo real (ground truth) por registro.
        pred_labels: Etiqueta de grupo predicha por el sistema, por registro.

    Returns:
        PairwiseMetrics con todos los conteos y ratios.

    Raises:
        ValueError: Si las longitudes no coinciden o están vacías.
    """
    truth = np.asarray(list(truth_labels))
    pred = np.asarray(list(pred_labels))
    if len(truth) != len(pred):
        raise ValueError(
            f"truth_labels ({len(truth)}) y pred_labels ({len(pred)}) "
            f"deben tener la misma longitud."
        )
    if len(truth) == 0:
        raise ValueError("No hay registros para evaluar (entrada vacía).")

    pares_verdad = _same_group_pairs(truth)
    pares_pred = _same_group_pairs(pred)

    tp = len(pares_verdad & pares_pred)
    fp = len(pares_pred - pares_verdad)
    fn = len(pares_verdad - pares_pred)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return PairwiseMetrics(
        true_pairs=len(pares_verdad),
        pred_pairs=len(pares_pred),
        tp=tp,
        fp=fp,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        n_records=len(truth),
        n_true_groups=len(set(truth.tolist())),
        n_pred_groups=len(set(pred.tolist())),
    )
