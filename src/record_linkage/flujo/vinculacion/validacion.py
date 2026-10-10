"""Validación del motor AUTOMÁTICO contra una referencia de decisiones: precisión, recall, F1 y discrepancias.

La referencia son decisiones humanas ya tomadas que se usan SOLO para medir al motor (no se aplican).
``validar_contra_referencia`` compara, por cada destino, los vínculos automáticos A/B con los de la referencia
y devuelve una fila de métricas por destino y la lista de falsos positivos y falsos negativos con su causa.
``wilson`` da el intervalo de confianza de una proporción.
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple

import pandas as pd

__all__ = ["validar_contra_referencia", "wilson"]

#: Niveles de vínculo: una decisión de referencia o un par automático con otro nivel no cuenta como vínculo.
_NIVELES_VINCULO = ("EMPRESA", "GRUPO")

#: Un par: ``(empresa ancla, posición del registro destino)``.
_Par = tuple[int, int]


class _Automaticos(NamedTuple):
    """Lo que el motor automático dice de los pares de UN destino."""

    vinculos: set[_Par]  # banda A o B con nivel EMPRESA o GRUPO
    candidatos: set[_Par]  # todo par propuesto, vinculado o no
    mismo_pais: dict[_Par, str]
    nivel: dict[_Par, str]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de confianza de Wilson (95 % con ``z = 1,96``) de ``k`` aciertos en ``n`` intentos.

    Devuelve ``(inferior, superior)`` redondeados a 3 decimales y acotados a [0, 1]; con ``n = 0`` ambos
    son ``NaN``.
    """
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (round(max(0.0, c - h), 3), round(min(1.0, c + h), 3))


def _pares_automaticos(a: pd.DataFrame) -> _Automaticos:
    """Los vínculos, los candidatos y, por par, el ``MISMO_PAIS`` y el ``NIVEL_AUTO`` de los pares ``a``."""
    # ``strict=False`` como el ``zip`` a secas del original: no se endurece a ``strict=True`` para no cambiar el
    # comportamiento con tablas mal formadas (p. ej., una columna con la etiqueta repetida).
    es_vinculo = a["BANDA_AUTO"].isin(["A", "B"]) & a["NIVEL_AUTO"].isin(_NIVELES_VINCULO)
    pos_auto = set(zip(a.loc[es_vinculo, "ENT"], a.loc[es_vinculo, "pos_b"], strict=False))
    cand = set(zip(a["ENT"], a["pos_b"], strict=False))
    # Hallazgo F3: sin ningún par candidato en toda la corrida ``L_auto`` no trae la columna MISMO_PAIS y, si hay
    # una referencia, esta línea falla con KeyError('MISMO_PAIS') en vez de dar métricas en cero.
    mismo = dict(zip(zip(a["ENT"], a["pos_b"], strict=False), a["MISMO_PAIS"], strict=False))
    nivel_auto = dict(zip(zip(a["ENT"], a["pos_b"], strict=False), a["NIVEL_AUTO"], strict=False))
    return _Automaticos(pos_auto, cand, mismo, nivel_auto)


def _pares_de_referencia(r: pd.DataFrame) -> tuple[dict[_Par, str], set[_Par]]:
    """``(ref_pos, ref_neg)``: los vínculos de la referencia (con su nivel) y los pares que descarta."""
    # ``strict=False``: ver ``_pares_automaticos``.
    decididos = list(zip(r["ENT"], r["pos_b"], r["DECISION"], strict=False))
    ref_pos = {(e, p): d for e, p, d in decididos if d in _NIVELES_VINCULO}
    ref_neg = {(e, p) for e, p, d in decididos if d not in _NIVELES_VINCULO}
    return ref_pos, ref_neg


def _fila_de_metricas(
    f: str,
    auto: _Automaticos,
    ref_pos: dict[_Par, str],
    ref_neg: set[_Par],
    auto_eval: set[_Par],
    tp: set[_Par],
    fn_c: set[_Par],
) -> dict[str, Any]:
    """La fila de métricas de un destino: precisión, recall, F1, sus intervalos y los conteos."""
    clase_ok = sum(1 for k in tp if auto.nivel.get(k) == ref_pos[k])
    prec = len(tp) / len(auto_eval) if auto_eval else float("nan")
    rec = len(tp) / len(ref_pos) if ref_pos else float("nan")
    rec_c = (len(ref_pos) - len(fn_c)) / len(ref_pos) if ref_pos else float("nan")
    # Hallazgo F3: con precisión o recall en 0 el F1 sale NaN (``prec and rec`` es falso); debería ser 0,0
    # cuando ambos están definidos (hay vínculos automáticos y de referencia, y ningún acierto).
    f1 = (
        2 * prec * rec / (prec + rec)
        if prec and rec and not math.isnan(prec + rec)
        else float("nan")
    )
    ic_p, ic_r = wilson(len(tp), len(auto_eval)), wilson(len(tp), len(ref_pos))
    return {
        "FUENTE_DESTINO": f,
        "VINCULOS_AUTOMATICOS_A_B": len(auto_eval),
        "VINCULOS_REFERENCIA": len(ref_pos),
        "ACIERTOS": len(tp),
        "PRECISION": round(prec, 4),
        "PRECISION_IC95": f"{ic_p[0]:.2f}–{ic_p[1]:.2f}",
        "RECALL": round(rec, 4),
        "RECALL_IC95": f"{ic_r[0]:.2f}–{ic_r[1]:.2f}",
        "F1": round(f1, 4),
        "RECALL_CONTANDO_CANDIDATOS_C": round(rec_c, 4),
        "NIVEL_CORRECTO_EN_ACIERTOS": f"{clase_ok}/{len(tp)}",
        "AUTOMATICOS_ENTRE_PAISES": len(
            {k for k in auto.vinculos if auto.mismo_pais.get(k) == "NO"}
        ),
        "DESCARTES_EN_REFERENCIA": len(ref_neg),
    }


def _discrepancias(
    f: str,
    prep_f: pd.DataFrame,
    auto: _Automaticos,
    ref_pos: dict[_Par, str],
    ref_neg: set[_Par],
    fp: set[_Par],
    fn: set[_Par],
) -> list[dict[str, Any]]:
    """Una fila por falso positivo y por falso negativo de un destino, en orden de (empresa, registro)."""
    ids = prep_f["ID_REGISTRO"].to_numpy()
    noms = prep_f["NOMBRE_ORIGINAL"].to_numpy()
    disc: list[dict[str, Any]] = []
    for k in sorted(fp):
        disc.append(
            {
                "FUENTE_DESTINO": f,
                "TIPO": "Falso positivo (el motor vincula, la referencia no)",
                "ENT": k[0],
                "ID_DESTINO": ids[k[1]],
                "NOMBRE_DESTINO": noms[k[1]],
                "NIVEL_AUTO": auto.nivel.get(k, ""),
                "REFERENCIA": "descartado" if k in ref_neg else "no incluido",
            }
        )
    for k in sorted(fn):
        disc.append(
            {
                "FUENTE_DESTINO": f,
                "TIPO": "Falso negativo (la referencia vincula, el motor no)"
                + (
                    " · el motor lo propuso como candidato C"
                    if k in auto.candidatos
                    else " · ni siquiera candidato"
                ),
                "ENT": k[0],
                "ID_DESTINO": ids[k[1]],
                "NOMBRE_DESTINO": noms[k[1]],
                "NIVEL_AUTO": auto.nivel.get(k, ""),
                "REFERENCIA": ref_pos[k],
            }
        )
    return disc


def validar_contra_referencia(
    L_auto: pd.DataFrame, ref_map: pd.DataFrame, prep: dict, destinos: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Precisión y recall del motor AUTOMÁTICO (sin decisiones) contra una referencia de decisiones.

    Universo: pares del mismo país (la referencia se construyó con esa regla). Un vínculo
    automático que la referencia no confirma cuenta como falso positivo; un vínculo de la
    referencia que el motor no propone (ni como candidato C) cuenta como falso negativo.

    ``L_auto`` son los pares por empresa del motor sin decisiones y ``ref_map`` la referencia ya traducida
    por ``mapear_decisiones``. Devuelve ``(metricas, discrepancias)``: una fila por destino con
    ``PRECISION``, ``RECALL``, ``F1`` y sus intervalos de Wilson, y una fila por falso positivo o negativo.
    """
    # Hallazgo F3: la docstring original dice que el universo son los pares del mismo país, pero el código
    # evalúa TODOS los vínculos automáticos A/B (los de otro país cuentan como falsos positivos salvo que la
    # referencia los incluya); ``AUTOMATICOS_ENTRE_PAISES`` solo los informa.
    filas: list[dict[str, Any]] = []
    disc: list[dict[str, Any]] = []
    for f in destinos:
        auto = _pares_automaticos(L_auto[L_auto["FUENTE_DESTINO"] == f])
        ref_pos, ref_neg = _pares_de_referencia(ref_map[ref_map["FUENTE_DESTINO"] == f])
        auto_eval = set(auto.vinculos)  # la referencia final cubre también los pares entre países
        tp = auto_eval & set(ref_pos)
        fp = auto_eval - set(ref_pos)
        fn = set(ref_pos) - auto.vinculos
        fn_c = set(ref_pos) - auto.candidatos
        filas.append(_fila_de_metricas(f, auto, ref_pos, ref_neg, auto_eval, tp, fn_c))
        disc += _discrepancias(f, prep[f], auto, ref_pos, ref_neg, fp, fn)
    return pd.DataFrame(filas), pd.DataFrame(disc)
