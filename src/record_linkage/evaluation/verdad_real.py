"""evaluation.verdad_real — precisión y recall sobre pares etiquetados por personas (Fase 1, 2026-10-13).

Por qué existe: toda la calidad del repositorio se midió sobre datos sintéticos. Cuando una persona etiqueta pares de una
base REAL, la verdad es una MUESTRA de pares, no una partición completa: el universo evaluable son esos pares, cada cifra
lleva su intervalo de confianza y se reporta por régimen (con identificador en los dos registros, sin identificador en los
dos, o mixto), porque el motor se comporta distinto en cada uno.

Un solo formato de etiquetas para toda la librería (``COLUMNAS_ETIQUETAS``): ``ID_1``, ``ID_2`` (los ``ID_REGISTRO`` de la
correlativa) e ``IS_MATCH`` (1 = misma empresa, 0 = distinta). ``leer_etiquetas`` acepta además los textos que usan los
anotadores (SAME/DIFFERENT, MISMA/DISTINTA, SI/NO) y descarta ``UNCLEAR``/``DUDA`` contándolos. ``ANOTADOR`` es opcional:
con dos anotadores, ``acuerdo_entre_anotadores`` da el kappa de Cohen y los desacuerdos para la sesión de resolución.

Cómo se usa (también desde ``scripts/medir_verdad_real.py``)::

    etiquetas = leer_etiquetas("etiquetas_revisadas.xlsx")          # fuera del repositorio: datos reales
    res = medir_verdad_real(etiquetas, correlativa, col_nit="NIT")   # correlativa de linkage()/dedupe()
    res.tabla()                                                      # DataFrame: régimen × (n, P, IC, R, IC, F1)

El protocolo de etiquetado (muestra, dos anotadores, kappa ≥ 0,8, dónde viven los archivos) está en
``docs/PROTOCOLO_GROUND_TRUTH.md``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from record_linkage.evaluation.pairwise import (
    MetricasParesEtiquetados,
    evaluar_pares_etiquetados,
)

__all__ = [
    "COLUMNAS_ETIQUETAS",
    "REGIMENES",
    "Acuerdo",
    "ResultadoVerdadReal",
    "acuerdo_entre_anotadores",
    "intervalo_wilson",
    "leer_etiquetas",
    "medir_verdad_real",
    "regimen_de_los_pares",
]

#: El formato único de etiquetas de la librería.
COLUMNAS_ETIQUETAS = ("ID_1", "ID_2", "IS_MATCH")

#: Regímenes de un par según si sus dos registros traen identificador.
REGIMENES = ("CON_ID", "SIN_ID", "MIXTO")

#: Textos que una persona escribe y lo que significan (en minúsculas, sin espacios).
_POSITIVAS = frozenset(
    {
        "1",
        "1.0",
        "true",
        "same",
        "misma",
        "mismo",
        "si",
        "sí",
        "yes",
        "igual",
        "mismaentidad",
        "empresa",
    }
)
_NEGATIVAS = frozenset(
    {
        "0",
        "0.0",
        "false",
        "different",
        "distinta",
        "distinto",
        "no",
        "diferente",
        "novincular",
        "no_vincular",
    }
)
_DUDOSAS = frozenset(
    {"unclear", "duda", "dudoso", "?", "revisar", "nose", "nosé", "nan", "none", ""}
)


def intervalo_wilson(exitos: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de Wilson (95 % con ``z = 1.96``) para una proporción; ``(0.0, 0.0)`` sin observaciones.

    Es el intervalo adecuado para proporciones con pocos casos o cerca de 0 y 1 (Wilson, 1927), que es
    exactamente la situación de una muestra de pares etiquetados a mano.
    """
    if total <= 0:
        return (0.0, 0.0)
    p = exitos / total
    denominador = 1 + z * z / total
    centro = (p + z * z / (2 * total)) / denominador
    margen = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominador
    return (max(0.0, centro - margen), min(1.0, centro + margen))


def _normalizar_etiqueta(valor: object) -> int | None:
    """``1``, ``0`` o ``None`` (dudosa o vacía) a partir de lo que escribió la persona."""
    texto = str(valor).strip().lower().replace(" ", "")
    if texto in _POSITIVAS:
        return 1
    if texto in _NEGATIVAS:
        return 0
    if texto in _DUDOSAS or valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    raise ValueError(
        f"Etiqueta {valor!r} no reconocida. Use 1/0, SAME/DIFFERENT, MISMA/DISTINTA o SI/NO; "
        "UNCLEAR/DUDA para un par que no se puede decidir."
    )


def leer_etiquetas(ruta: str | Path, hoja: str | int = 0) -> pd.DataFrame:
    """Lee un archivo de etiquetas (CSV o Excel) y lo deja en el formato único.

    Acepta las columnas ``ID_1``/``ID_2``/``IS_MATCH`` o sus equivalentes de las herramientas de la librería
    (``id_left``/``id_right``/``etiqueta_humana`` de ``scripts/active_labeling.py``; ``ID_A``/``ID_B``/``MISMO_GRUPO`` de
    ``scripts/medir_kappa.py``). Conserva ``ANOTADOR`` si viene. Los pares dudosos o vacíos se descartan y se cuentan en
    ``df.attrs["descartados"]``.

    Raises:
        ValueError: faltan las columnas, o una etiqueta no es ninguno de los textos admitidos.
    """
    p = Path(ruta)
    crudo = (
        pd.read_excel(p, sheet_name=hoja, dtype=str)
        if p.suffix.lower() in (".xlsx", ".xlsm")
        else pd.read_csv(p, dtype=str)
    )
    crudo.columns = [str(c).strip() for c in crudo.columns]
    alias = {
        "ID_1": ("ID_1", "id_1", "id_left", "ID_LEFT", "ID_A", "id_a", "ID_REGISTRO_A"),
        "ID_2": ("ID_2", "id_2", "id_right", "ID_RIGHT", "ID_B", "id_b", "ID_REGISTRO_B"),
        "IS_MATCH": (
            "IS_MATCH",
            "is_match",
            "etiqueta_humana",
            "ETIQUETA",
            "MISMO_GRUPO",
            "etiqueta",
            "DECISION",
        ),
        "ANOTADOR": ("ANOTADOR", "anotador", "labeler", "AUTOR", "autor"),
    }
    columnas: dict[str, str] = {}
    for destino, candidatas in alias.items():
        for c in candidatas:
            if c in crudo.columns:
                columnas[destino] = c
                break
    faltan = [c for c in COLUMNAS_ETIQUETAS if c not in columnas]
    if faltan:
        raise ValueError(
            f"Al archivo de etiquetas {p.name} le faltan {faltan}. Se esperan ID_1, ID_2 e IS_MATCH "
            f"(o sus alias: id_left/id_right/etiqueta_humana, ID_A/ID_B/MISMO_GRUPO). Columnas: {list(crudo.columns)}"
        )
    out = pd.DataFrame(
        {
            "ID_1": crudo[columnas["ID_1"]].astype(str).str.strip(),
            "ID_2": crudo[columnas["ID_2"]].astype(str).str.strip(),
        }
    )
    etiquetas = [_normalizar_etiqueta(v) for v in crudo[columnas["IS_MATCH"]].to_numpy()]
    out["IS_MATCH"] = etiquetas
    if "ANOTADOR" in columnas:
        out["ANOTADOR"] = crudo[columnas["ANOTADOR"]].astype(str).str.strip()
    conservar = out["IS_MATCH"].notna().to_numpy()
    descartados = int((~conservar).sum())
    out = out.loc[conservar].reset_index(drop=True)
    out["IS_MATCH"] = out["IS_MATCH"].astype(int)
    out.attrs["descartados"] = descartados
    out.attrs["archivo"] = str(p)
    return out


def regimen_de_los_pares(etiquetas: pd.DataFrame, identificador: pd.Series) -> pd.Series:
    """``CON_ID`` si los dos registros del par traen identificador, ``SIN_ID`` si ninguno, ``MIXTO`` si uno.

    ``identificador`` es una serie indexada por ``ID_REGISTRO`` (texto) con el identificador de cada registro
    (vacío/ausente = sin identificador). Un par cuyo registro no está en la serie se marca ``MIXTO``: no se sabe.
    """
    serie = pd.Series(identificador.to_numpy(), index=pd.Index(identificador.index.astype(str)))
    tiene = (
        serie.notna()
        & (serie.astype(str).str.strip() != "")
        & (serie.astype(str).str.lower() != "nan")
    )
    t1 = etiquetas["ID_1"].astype(str).map(tiene)
    t2 = etiquetas["ID_2"].astype(str).map(tiene)
    ambos = (t1 == True) & (t2 == True)  # noqa: E712 — comparación con NaN posibles
    ninguno = (t1 == False) & (t2 == False)  # noqa: E712
    return pd.Series(
        np.where(ambos, "CON_ID", np.where(ninguno, "SIN_ID", "MIXTO")), index=etiquetas.index
    )


@dataclass(frozen=True)
class ResultadoVerdadReal:
    """Lo que se reporta de una medición contra pares etiquetados.

    Attributes:
        global_: métricas sobre todos los pares evaluables.
        por_regimen: métricas por ``CON_ID`` / ``SIN_ID`` / ``MIXTO`` (solo los regímenes con pares).
        intervalos: ``{régimen o "GLOBAL": {"precision": (lo, hi), "recall": (lo, hi)}}`` (Wilson 95 %).
        n_etiquetados, n_descartados: pares con etiqueta y pares dudosos/vacíos descartados al leer.
        archivo: de dónde salieron las etiquetas (para el historial).
    """

    global_: MetricasParesEtiquetados
    por_regimen: dict[str, MetricasParesEtiquetados]
    intervalos: dict[str, dict[str, tuple[float, float]]]
    n_etiquetados: int
    n_descartados: int
    archivo: str = ""
    notas: list[str] = field(default_factory=list)

    def tabla(self) -> pd.DataFrame:
        """Una fila por régimen (y GLOBAL): n, precisión con su intervalo, recall con el suyo, F1."""
        filas = []
        for nombre, m in [("GLOBAL", self.global_), *self.por_regimen.items()]:
            ic = self.intervalos.get(nombre, {})
            filas.append(
                {
                    "REGIMEN": nombre,
                    "PARES": m.n_pares_evaluados,
                    "TP": m.tp,
                    "FP": m.fp,
                    "FN": m.fn,
                    "PRECISION": round(m.precision, 4),
                    "PRECISION_IC95": tuple(round(x, 4) for x in ic.get("precision", (0.0, 0.0))),
                    "RECALL": round(m.recall, 4),
                    "RECALL_IC95": tuple(round(x, 4) for x in ic.get("recall", (0.0, 0.0))),
                    "F1": round(m.f1, 4),
                }
            )
        return pd.DataFrame(filas)

    def como_dict(self) -> dict[str, Any]:
        """Serializable a JSON (para ``docs/evidencia`` sin datos reales: solo cifras)."""
        return {
            "archivo": self.archivo,
            "n_etiquetados": self.n_etiquetados,
            "n_descartados": self.n_descartados,
            "global": self.global_.como_dict(),
            "por_regimen": {k: v.como_dict() for k, v in self.por_regimen.items()},
            "intervalos": {
                k: {m: list(v) for m, v in d.items()} for k, d in self.intervalos.items()
            },
            "notas": list(self.notas),
        }


def _intervalos(m: MetricasParesEtiquetados) -> dict[str, tuple[float, float]]:
    return {
        "precision": intervalo_wilson(m.tp, m.tp + m.fp),
        "recall": intervalo_wilson(m.tp, m.tp + m.fn),
    }


def medir_verdad_real(
    etiquetas: pd.DataFrame,
    correlativa: pd.DataFrame,
    *,
    col_id: str = "ID_REGISTRO",
    col_grupo: str = "ID_GRUPO",
    col_nit: str | None = None,
    grupos: pd.Series | Mapping[Any, Any] | None = None,
) -> ResultadoVerdadReal:
    """Precisión y recall de una partición predicha contra pares etiquetados, global y por régimen.

    Args:
        etiquetas: lo que devuelve :func:`leer_etiquetas` (o un DataFrame con ``ID_1``, ``ID_2``, ``IS_MATCH`` en 1/0).
        correlativa: una fila por registro con ``col_id`` y ``col_grupo`` (la correlativa de ``linkage()``/``dedupe()``).
        col_nit: columna del identificador para separar regímenes; sin ella todo se reporta como GLOBAL.
        grupos: alternativa a ``correlativa``: serie ``id → grupo`` ya construida.

    Raises:
        ValueError: ``correlativa`` sin las columnas pedidas, ids repetidos, o ningún par evaluable.
    """
    if grupos is None:
        faltan = [c for c in (col_id, col_grupo) if c not in correlativa.columns]
        if faltan:
            raise ValueError(
                f"A la correlativa le faltan las columnas {faltan}; se necesitan {col_id!r} (id del registro) y "
                f"{col_grupo!r} (grupo predicho). Columnas: {list(correlativa.columns)}"
            )
        grupos = pd.Series(
            correlativa[col_grupo].to_numpy(), index=correlativa[col_id].astype(str).to_numpy()
        )
    global_ = evaluar_pares_etiquetados(etiquetas, grupos)
    por_regimen: dict[str, MetricasParesEtiquetados] = {}
    notas: list[str] = []
    if col_nit is not None and col_nit in correlativa.columns:
        ident = pd.Series(
            correlativa[col_nit].to_numpy(), index=correlativa[col_id].astype(str).to_numpy()
        )
        regimen = regimen_de_los_pares(etiquetas, ident)
        for r in REGIMENES:
            sub = etiquetas.loc[(regimen == r).to_numpy()]
            if len(sub) == 0:
                continue
            try:
                por_regimen[r] = evaluar_pares_etiquetados(sub, grupos)
            except ValueError as e:  # ningún par evaluable en ese régimen: se informa, no se oculta
                notas.append(f"{r}: {e}")
    elif col_nit is not None:
        notas.append(f"la correlativa no trae {col_nit!r}: no se separan regímenes")
    intervalos = {
        "GLOBAL": _intervalos(global_),
        **{k: _intervalos(v) for k, v in por_regimen.items()},
    }
    return ResultadoVerdadReal(
        global_=global_,
        por_regimen=por_regimen,
        intervalos=intervalos,
        n_etiquetados=len(etiquetas),
        n_descartados=int(etiquetas.attrs.get("descartados", 0)),
        archivo=str(etiquetas.attrs.get("archivo", "")),
        notas=notas,
    )


@dataclass(frozen=True)
class Acuerdo:
    """Acuerdo entre dos anotadores sobre los pares que ambos etiquetaron."""

    n_comunes: int
    acuerdo_observado: float
    kappa: float
    desacuerdos: pd.DataFrame

    @property
    def lectura(self) -> str:
        """Banda de Landis y Koch: la verdad es confiable con kappa > 0,80."""
        if self.n_comunes == 0:
            return "sin pares en común"
        if self.kappa > 0.80:
            return "casi perfecto: verdad confiable"
        if self.kappa >= 0.60:
            return "sustancial: aceptable, resolver los desacuerdos"
        return "bajo: el manual de etiquetado está mal definido, no el motor"


def acuerdo_entre_anotadores(a: pd.DataFrame, b: pd.DataFrame) -> Acuerdo:
    """Kappa de Cohen entre dos tablas de etiquetas (formato único), sobre los pares que ambas traen.

    Un par se identifica por ``(ID_1, ID_2)`` sin importar el orden. Los desacuerdos salen listados para la sesión
    de resolución que pide el protocolo.
    """

    def clave(df: pd.DataFrame) -> pd.Series:
        x, y = df["ID_1"].astype(str), df["ID_2"].astype(str)
        return pd.Series(np.where(x <= y, x + "|" + y, y + "|" + x), index=df.index)

    ea = pd.Series(a["IS_MATCH"].to_numpy(), index=clave(a).to_numpy()).groupby(level=0).first()
    eb = pd.Series(b["IS_MATCH"].to_numpy(), index=clave(b).to_numpy()).groupby(level=0).first()
    comunes = ea.index.intersection(eb.index)
    n = len(comunes)
    if n == 0:
        return Acuerdo(0, 0.0, 0.0, pd.DataFrame(columns=["PAR", "ANOTADOR_A", "ANOTADOR_B"]))
    va, vb = ea.loc[comunes].to_numpy().astype(int), eb.loc[comunes].to_numpy().astype(int)
    po = float((va == vb).mean())
    pe = float(va.mean() * vb.mean() + (1 - va.mean()) * (1 - vb.mean()))
    kappa = 0.0 if pe == 1.0 else (po - pe) / (1 - pe)
    dif = va != vb
    desacuerdos = pd.DataFrame(
        {"PAR": np.asarray(comunes)[dif], "ANOTADOR_A": va[dif], "ANOTADOR_B": vb[dif]}
    )
    return Acuerdo(n, po, float(kappa), desacuerdos.reset_index(drop=True))
