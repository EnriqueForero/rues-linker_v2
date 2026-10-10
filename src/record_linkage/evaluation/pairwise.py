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

from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

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


# ─────────────────────────────────────────────────────────────────────────
#  Verdad por PARES etiquetados (F2.15)
# ─────────────────────────────────────────────────────────────────────────
#: Columnas mínimas del archivo de revisión que produce ``GroundTruthGenerator``.
COLUMNAS_PARES_ETIQUETADOS: tuple[str, ...] = ("ID_1", "ID_2", "IS_MATCH")


@dataclass(frozen=True)
class MetricasParesEtiquetados:
    """Precisión, recall y F1 sobre los pares que una persona etiquetó.

    A diferencia de :class:`PairwiseMetrics`, aquí el universo no son todos los
    pares de una partición sino SOLO los pares etiquetados: un par positivo
    cuenta como TP si la predicción pone a sus dos registros en el mismo
    grupo; uno negativo cuenta como FP si los junta. Los pares entre registros
    que nadie etiquetó no se miran, que es lo honesto cuando la verdad es una
    muestra de pares y no una partición completa.

    Attributes:
        tp, fp, fn: conteos sobre los pares evaluables.
        precision, recall, f1: las de siempre (0.0 cuando no hay denominador).
        n_pares_etiquetados: pares con ``IS_MATCH`` informado.
        n_pares_evaluados: de esos, los que tienen ambos registros en la predicción.
        n_pares_sin_registro: etiquetados cuyo registro no aparece en la predicción
            (una fuente recortada, un id que cambió): se excluyen y se cuentan.
        n_registros: registros distintos que participan en los pares evaluados.
    """

    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    n_pares_etiquetados: int
    n_pares_evaluados: int
    n_pares_sin_registro: int
    n_registros: int

    def como_dict(self) -> dict[str, float | int]:
        """Los campos como ``dict`` plano (para historiales y JSON)."""
        return {campo: getattr(self, campo) for campo in self.__dataclass_fields__}


def esta_revisado(valores: pd.Series) -> pd.Series:
    """Máscara de los ``IS_MATCH`` que una persona llenó.

    Un par sin revisar llega como ``NaN``/``None`` (el archivo recién leído)
    o como ``""`` (releído con ``dtype=str`` y ``fillna("")``, como el
    notebook lee su base). Ninguno de los dos es una etiqueta: se ignoran.
    La regla vive aquí una vez; el optimizador la usa para validar la verdad
    antes del primer trial.
    """
    return valores.notna() & (valores.astype(str).str.strip() != "")


def validar_etiquetas(valores: pd.Series) -> np.ndarray:
    """``IS_MATCH`` como booleanos; acepta 1/0, "1"/"0", True/False, 1.0/0.0.

    Falla rápido si algún valor no es ninguno de los dos (``"si"``, ``"x"``):
    el mensaje dice cuántos y cuáles, para corregir el archivo de etiquetas.
    Pública para que quien construya un estudio valide la verdad ANTES de
    gastar trials (``OrchestratorOptimizer`` lo hace en el constructor).
    """
    texto = valores.astype(str).str.strip().str.lower()
    positivos = texto.isin({"1", "1.0", "true"})
    negativos = texto.isin({"0", "0.0", "false"})
    invalidas = ~(positivos | negativos)
    if invalidas.any():
        ejemplos = sorted(set(valores[invalidas].astype(str)))[:5]
        raise ValueError(
            f"IS_MATCH debe ser 1 (misma empresa) o 0 (distinta); hay {int(invalidas.sum())} "
            f"valor(es) que no son ninguno de los dos: {ejemplos}. Sin eso no se sabe qué "
            "quiso decir quien revisó: corrija el archivo de etiquetas."
        )
    return positivos.to_numpy()


def evaluar_pares_etiquetados(
    pares: pd.DataFrame, grupos: pd.Series | Mapping[Any, Any]
) -> MetricasParesEtiquetados:
    """Mide una partición predicha contra pares etiquetados a mano.

    Args:
        pares: DataFrame con ``ID_1``, ``ID_2`` e ``IS_MATCH`` (el archivo de
            ``GroundTruthGenerator`` ya revisado). Las filas con ``IS_MATCH``
            vacío (``NaN`` o ``""``) son pares sin revisar y se ignoran.
        grupos: etiqueta de grupo predicha por registro, indexada por el id
            del registro (``correlativa.set_index("ID")["ID_GRUPO"]``). Los
            ids se comparan como texto.

    Returns:
        :class:`MetricasParesEtiquetados`.

    Raises:
        ValueError: faltan columnas, una etiqueta no es 0/1, un id está
            repetido en ``grupos`` o ningún par etiquetado es evaluable.
    """
    faltan = [c for c in COLUMNAS_PARES_ETIQUETADOS if c not in pares.columns]
    if faltan:
        raise ValueError(
            f"A los pares etiquetados les faltan las columnas {faltan}; se esperan "
            f"{list(COLUMNAS_PARES_ETIQUETADOS)} (la salida de GroundTruthGenerator con "
            "IS_MATCH llenado)."
        )
    serie = grupos if isinstance(grupos, pd.Series) else pd.Series(dict(grupos))
    indice = pd.Index(serie.index.astype(str))
    if indice.has_duplicates:
        repetidos = sorted(set(indice[indice.duplicated()]))[:5]
        raise ValueError(
            f"La predicción tiene ids repetidos: {repetidos}. Cada registro debe tener "
            "UN grupo; use la correlativa (una fila por registro) indexada por su id."
        )
    serie = pd.Series(serie.to_numpy().astype(str), index=indice)

    revisados = pares.loc[esta_revisado(pares["IS_MATCH"])]
    n_etiquetados = len(revisados)
    g1 = revisados["ID_1"].astype(str).map(serie)
    g2 = revisados["ID_2"].astype(str).map(serie)
    evaluable = (g1.notna() & g2.notna()).to_numpy()
    if not evaluable.any():
        raise ValueError(
            f"Ningún par etiquetado es evaluable: de {n_etiquetados} pares revisados, en "
            "ninguno aparecen sus dos registros en la predicción. Los ids de ID_1/ID_2 "
            "deben ser los mismos que identifican cada fila en la correlativa."
        )
    positivo = validar_etiquetas(revisados["IS_MATCH"])[evaluable]
    mismo = (g1.to_numpy()[evaluable] == g2.to_numpy()[evaluable]).astype(bool)

    tp = int((positivo & mismo).sum())
    fp = int((~positivo & mismo).sum())
    fn = int((positivo & ~mismo).sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    ids = pd.concat([revisados["ID_1"], revisados["ID_2"]]).astype(str)[
        np.concatenate([evaluable, evaluable])
    ]
    return MetricasParesEtiquetados(
        tp=tp,
        fp=fp,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        n_pares_etiquetados=n_etiquetados,
        n_pares_evaluados=int(evaluable.sum()),
        n_pares_sin_registro=n_etiquetados - int(evaluable.sum()),
        n_registros=int(ids.nunique()),
    )
