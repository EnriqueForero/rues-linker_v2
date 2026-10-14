"""Medición: el aporte de cada variable y una corrida del motor con su medición.

:func:`aporte_por_variable` dice qué hace cada comparador sobre positivos y negativos
(:func:`aporte_como_tabla` y :func:`tabla_aporte` lo presentan). :func:`medir_configuracion` corre
el motor con una configuración y devuelve una :class:`Medicion`: partición, :func:`huella_particion`,
cifras contra ``VERDAD_GRUPOS`` y ``ETIQUETAS_PARES`` si las hay, y la comprobación por archivo de
que las columnas extra llegaron al scorer (:func:`columnas_que_ve_el_scorer`,
:func:`exigir_columnas_en_scorer`). :func:`comparar_con_base` lista qué casos y qué pares cambian,
:func:`medicion_como_dict` serializa y :func:`elegir_recomendada` aplica la regla de decisión del
proyecto. Parte del paquete ``evaluation.multicriterio``.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from record_linkage.evaluation.multicriterio.configuraciones import (
    VARIABLES_A_MEDIR,
    Configuracion,
    a_columnas_del_motor,
    ajustes_del_motor,
)
from record_linkage.evaluation.plantilla_base_real import (
    COL_ID,
    LibroPlantilla,
    correr_motor,
    evaluar_casos,
    metricas_pares,
    pares_por_particion,
    pares_predichos,
)
from record_linkage.evaluation.verdad_real import medir_verdad_real
from record_linkage.matching.comparadores_extra import obtener, tipos_disponibles

#: Dónde deja L1 el DataFrame que lee el scorer (la misma ruta que usa el orquestador).
RUTA_L1 = Path("L1_prep") / "data.parquet"
VACIOS = frozenset({"", "nan", "none", "null", "<na>"})


# ---------------------------------------------------------------------------
# Aporte de cada variable: qué hace el comparador sobre positivos y negativos
# ---------------------------------------------------------------------------


def _conteo_similitud(sim: np.ndarray, ambos_llenos: np.ndarray) -> dict[str, float | int]:
    """``premia``, ``castiga`` y ``neutro`` se cuentan SOLO sobre los pares con las dos celdas llenas
    (suman ``ambos_llenos``), que es el denominador de ``separacion_pp``. Lo que un comparador premia
    con un lado vacío (``categorical`` devuelve 0,5) va aparte, en ``premia_con_un_lado_vacio``: con
    los firmados es 0; con los no firmados es la trampa, y así se ve en vez de romper la tabla."""
    llenos = sim[ambos_llenos]
    return {
        "pares": len(sim),
        "ambos_llenos": int(ambos_llenos.sum()),
        "premia": int((llenos > 0).sum()),
        "castiga": int((llenos < 0).sum()),
        "neutro": int((llenos == 0).sum()),
        "premia_con_un_lado_vacio": int((sim[~ambos_llenos] > 0).sum()),
        "similitud_media_ambos_llenos": round(float(llenos.mean()), 4)
        if ambos_llenos.any()
        else 0.0,
    }


def _llenos(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    vacios = list(VACIOS)
    return ~np.isin(np.char.lower(a.astype(str)), vacios) & ~np.isin(
        np.char.lower(b.astype(str)), vacios
    )


def aporte_por_variable(
    registros: pd.DataFrame,
    positivos: pd.DataFrame,
    negativos: pd.DataFrame,
    variables: Sequence[tuple[str, str]] = VARIABLES_A_MEDIR,
) -> list[dict[str, object]]:
    """Qué hace cada comparador de ``variables`` (``(columna, tipo)``) sobre positivos y negativos.

    ``separacion_pp`` es la diferencia, en puntos porcentuales, entre la fracción de positivos y de
    negativos que el comparador premia, contando solo los pares con las dos celdas llenas: lo que
    de verdad separa, no la tasa de llenado. Lo que premia con un lado vacío se cuenta aparte
    (``premia_con_un_lado_vacio``). Una columna que no está en ``registros`` se omite.
    """
    filas = []
    for columna, tipo in variables:
        if columna not in registros.columns:
            continue
        comparador = obtener(tipo)
        if comparador is None:
            raise ValueError(f"El tipo {tipo!r} no está registrado: {tipos_disponibles()}")
        valores = registros[columna].astype(str).str.strip()
        lados = {}
        for nombre, pares in (("positivos", positivos), ("negativos", negativos)):
            a = valores.reindex(pares["ID_1"]).to_numpy(dtype=object)
            b = valores.reindex(pares["ID_2"]).to_numpy(dtype=object)
            sim = np.asarray(comparador(a, b), dtype=float)
            lados[nombre] = _conteo_similitud(sim, _llenos(a, b))
        pos, neg = lados["positivos"], lados["negativos"]
        frac_pos = pos["premia"] / pos["ambos_llenos"] if pos["ambos_llenos"] else 0.0
        frac_neg = neg["premia"] / neg["ambos_llenos"] if neg["ambos_llenos"] else 0.0
        filas.append(
            {
                "columna": columna,
                "tipo": tipo,
                "llenado": round(float((~valores.str.lower().isin(VACIOS)).mean()), 4),
                "positivos": pos,
                "negativos": neg,
                "separacion_pp": round(100 * (frac_pos - frac_neg), 1),
            }
        )
    return filas


def aporte_como_tabla(aporte: list[dict[str, object]]) -> pd.DataFrame:
    """El aporte de :func:`aporte_por_variable` como tabla plana (una fila por columna y tipo)."""
    filas = []
    for a in aporte:
        p, n = a["positivos"], a["negativos"]
        assert isinstance(p, dict) and isinstance(n, dict)
        filas.append(
            {
                "COLUMNA": a["columna"],
                "TIPO": a["tipo"],
                "LLENADO_%": _porcentaje(a["llenado"]),
                "POSITIVOS_LLENOS": p["ambos_llenos"],
                "POSITIVOS_PREMIA": p["premia"],
                "POSITIVOS_CASTIGA": p["castiga"],
                "NEGATIVOS_LLENOS": n["ambos_llenos"],
                "NEGATIVOS_PREMIA": n["premia"],
                "NEGATIVOS_CASTIGA": n["castiga"],
                "SEPARACION_PP": a["separacion_pp"],
                "POSITIVOS_PREMIA_UN_LADO_VACIO": p.get("premia_con_un_lado_vacio", 0),
                "NEGATIVOS_PREMIA_UN_LADO_VACIO": n.get("premia_con_un_lado_vacio", 0),
            }
        )
    return pd.DataFrame(
        filas,
        columns=[
            "COLUMNA",
            "TIPO",
            "LLENADO_%",
            "POSITIVOS_LLENOS",
            "POSITIVOS_PREMIA",
            "POSITIVOS_CASTIGA",
            "NEGATIVOS_LLENOS",
            "NEGATIVOS_PREMIA",
            "NEGATIVOS_CASTIGA",
            "SEPARACION_PP",
            "POSITIVOS_PREMIA_UN_LADO_VACIO",
            "NEGATIVOS_PREMIA_UN_LADO_VACIO",
        ],
    )


def _porcentaje(fraccion: object) -> float:
    """``0.4925`` → ``49.3``: un decimal redondeando el medio hacia arriba. ``round`` del lenguaje
    redondea al par (49,2) y la cifra no coincidía con la medida a mano (264/536 = 49,25 %)."""
    return float((Decimal(str(fraccion)) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def tabla_aporte(aporte: list[dict[str, object]]) -> str:
    """El aporte en Markdown (para la consola y la guía)."""
    lineas = [
        "| Columna | Comparador | Llenado | Positivos llenos / premia / castiga | "
        "Negativos llenos / premia / castiga | Separación (pp) |",
        "|---|---|---|---|---|---|",
    ]
    for fila in aporte_como_tabla(aporte).itertuples(index=False):
        lineas.append(
            f"| {fila.COLUMNA} | `{fila.TIPO}` | {fila[2]:.1f} % | "
            f"{fila.POSITIVOS_LLENOS} / {fila.POSITIVOS_PREMIA} / {fila.POSITIVOS_CASTIGA} | "
            f"{fila.NEGATIVOS_LLENOS} / {fila.NEGATIVOS_PREMIA} / {fila.NEGATIVOS_CASTIGA} | "
            f"{fila.SEPARACION_PP:+.1f} |"
        )
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Una corrida del motor y su medición
# ---------------------------------------------------------------------------


@dataclass
class Medicion:
    """Lo que se mide de una configuración. ``verdaderos`` y ``pares`` existen con VERDAD_GRUPOS
    (aunque sea mínima); ``tabla_casos`` solo con la columna CASO y la hoja CASOS; ``etiquetas`` y
    ``tabla_etiquetas`` (por régimen, con Wilson 95 %) solo con ETIQUETAS_PARES; ``predichos`` y
    ``huella`` existen siempre y bastan para comparar dos corridas."""

    configuracion: Configuracion
    correlativa: pd.DataFrame
    grupos_predichos: int
    predichos: set[tuple[str, str]]
    huella: str
    columnas_extra_en_scorer: dict[str, bool]
    verdaderos: set[tuple[str, str]] | None = None
    pares: dict[str, float | int] | None = None
    tabla_casos: pd.DataFrame | None = None
    etiquetas: dict[str, object] | None = None
    tabla_etiquetas: pd.DataFrame | None = None
    cambios_vs_base: dict[str, object] = field(default_factory=dict)

    @property
    def con_verdad(self) -> bool:
        """Hay precisión y recall por pares (VERDAD_GRUPOS)."""
        return self.pares is not None

    @property
    def con_casos(self) -> bool:
        """Hay tabla por caso (columna CASO y hoja CASOS)."""
        return self.tabla_casos is not None

    @property
    def casos_acertados(self) -> int | None:
        return None if self.tabla_casos is None else int(self.tabla_casos["ACERTO"].sum())


def huella_particion(correlativa: pd.DataFrame) -> str:
    """SHA-256 de la partición, independiente de cómo se llamen los grupos: cada registro se
    etiqueta con el menor id de su grupo y se ordena por id."""
    ids = correlativa[COL_ID].astype(str)
    grupos = correlativa["ID_GRUPO"].astype(str)
    representante = pd.Series(ids.to_numpy(), index=grupos.to_numpy()).groupby(level=0).min()
    marco = pd.DataFrame({"ID": ids.to_numpy(), "REP": grupos.map(representante).to_numpy()})
    marco = marco.sort_values("ID")
    # Una línea «ID,REP» por registro (el mismo texto que daba ``to_csv`` sin encabezado; se arma
    # con ``str.cat`` porque ``to_csv`` es un escritor y la compuerta de tests/test_escritor.py
    # solo se lo permite al escritor del estándar).
    texto = (marco["ID"].astype(str) + "," + marco["REP"].astype(str)).str.cat(sep="\n") + "\n"
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def columnas_que_ve_el_scorer(
    dir_trabajo: str | Path, extra_features: list[dict[str, object]]
) -> dict[str, bool]:
    """Si cada columna extra está en el parquet de L1, que es lo que el scorer recibe en L3. Se
    mira el archivo porque el aviso del scorer ante una columna ausente es solo un log."""
    if not extra_features:
        return {}
    ruta = Path(dir_trabajo) / RUTA_L1
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe {ruta}: la corrida no dejó el parquet de L1.")
    columnas = set(pd.read_parquet(ruta).columns)
    return {str(f["column"]): str(f["column"]) in columnas for f in extra_features}


def exigir_columnas_en_scorer(
    columnas_extra_en_scorer: dict[str, bool], dir_trabajo: str | Path
) -> None:
    """Fail-fast: si una columna extra no llegó al DataFrame del scorer, el motor la habría omitido
    con un aviso que la medición apaga y la comparación OFF/ON no mediría nada."""
    faltan = sorted(c for c, ok in columnas_extra_en_scorer.items() if not ok)
    if faltan:
        raise RuntimeError(
            f"Las columnas extra {faltan} no llegaron al DataFrame del scorer "
            f"({Path(dir_trabajo) / RUTA_L1}): el motor las omitiría con un aviso que esta medición "
            "apaga, y la comparación OFF/ON no mediría nada. Qué hacer: compruebe el nombre de la "
            "columna en sus hojas FUENTE_* (tal cual, mayúsculas incluidas) y que no sea una columna "
            "que el motor renombra (MUNICIPIO → CIUDAD lo traduce la librería; otras no)."
        )


def _pares_con_caso(
    pares: set[tuple[str, str]],
    caso_por_id: pd.Series | None,
    verdaderos: set[tuple[str, str]] | None,
) -> list[dict[str, object]]:
    def casos(a: str, b: str) -> list[str]:
        if caso_por_id is None:
            return []
        return sorted({str(caso_por_id.get(a, "")), str(caso_por_id.get(b, ""))})

    return [
        {
            "ID_1": a,
            "ID_2": b,
            "verdadero": None if verdaderos is None else (a, b) in verdaderos,
            "casos": casos(a, b),
        }
        for a, b in sorted(pares)
    ]


def medir_configuracion(
    libro: LibroPlantilla,
    etiquetas: pd.DataFrame | None,
    config: Configuracion,
    dir_trabajo: str | Path,
) -> Medicion:
    """Corre el motor con la configuración (en un directorio limpio) y mide contra lo que haya:
    pares contra VERDAD_GRUPOS si el libro la trae, tabla por caso si además trae CASO y CASOS,
    etiquetas si vienen; sin nada, solo la partición. Se detiene si una columna extra no llegó al
    scorer (:func:`exigir_columnas_en_scorer`)."""
    dir_trabajo = Path(dir_trabajo)
    shutil.rmtree(dir_trabajo, ignore_errors=True)
    extra = a_columnas_del_motor(config.extra_features)
    ajustes = ajustes_del_motor(config.ajustes_perfil)
    correlativa = correr_motor(
        libro.fuentes, dir_trabajo, extra_features=extra or None, ajustes_perfil=ajustes or None
    )
    medicion = Medicion(
        configuracion=config,
        correlativa=correlativa,
        grupos_predichos=int(correlativa["ID_GRUPO"].nunique()),
        predichos=pares_predichos(correlativa),
        huella=huella_particion(correlativa),
        columnas_extra_en_scorer=columnas_que_ve_el_scorer(dir_trabajo, extra),
    )
    exigir_columnas_en_scorer(medicion.columnas_extra_en_scorer, dir_trabajo)
    if libro.verdad is not None:
        medicion.verdaderos, medicion.predichos = pares_por_particion(correlativa, libro.verdad)
        medicion.pares = metricas_pares(correlativa, libro.verdad)
    if libro.con_casos:
        assert libro.verdad is not None and libro.casos is not None
        medicion.tabla_casos = evaluar_casos(correlativa, libro.verdad, libro.casos)
    if etiquetas is not None:
        resultado = medir_verdad_real(etiquetas, correlativa, col_id=COL_ID, col_nit="NIT")
        medicion.etiquetas = resultado.como_dict()
        medicion.tabla_etiquetas = resultado.tabla()
    return medicion


def comparar_con_base(
    base: Medicion, otra: Medicion, caso_por_id: pd.Series | None = None
) -> dict[str, object]:
    """Qué casos y qué pares cambian respecto a la base (ids y veredicto, nada más). Sin verdad,
    ``verdadero`` es ``None`` y las listas de casos quedan vacías."""
    mejoran: list[str] = []
    empeoran: list[str] = []
    if base.tabla_casos is not None and otra.tabla_casos is not None:
        acierto_otra = otra.tabla_casos.set_index("CASO")["ACERTO"].astype(bool)
        acierto_base = (
            base.tabla_casos.set_index("CASO")["ACERTO"].reindex(acierto_otra.index).astype(bool)
        )
        mejoran = sorted(str(c) for c in acierto_otra.index[(~acierto_base & acierto_otra)])
        empeoran = sorted(str(c) for c in acierto_otra.index[(acierto_base & ~acierto_otra)])
    return {
        "casos_que_mejoran": mejoran,
        "casos_que_empeoran": empeoran,
        "pares_ganados": _pares_con_caso(
            otra.predichos - base.predichos, caso_por_id, otra.verdaderos
        ),
        "pares_perdidos": _pares_con_caso(
            base.predichos - otra.predichos, caso_por_id, otra.verdaderos
        ),
    }


def medicion_como_dict(m: Medicion, caso_por_id: pd.Series | None = None) -> dict[str, object]:
    """Serializable a JSON: cifras, ids y la configuración exacta; ningún dato de registro."""
    salida: dict[str, object] = {
        **m.configuracion.como_dict(),
        "extra_features_para_el_motor": a_columnas_del_motor(m.configuracion.extra_features),
        "ajustes_perfil_para_el_motor": ajustes_del_motor(m.configuracion.ajustes_perfil),
        "columnas_extra_en_scorer": m.columnas_extra_en_scorer,
        "grupos_predichos": m.grupos_predichos,
        "pares_predichos": len(m.predichos),
        "pares_verdad_grupos": m.pares,
        "fp": None,
        "fn": None,
        "casos_acertados": m.casos_acertados,
        "casos_total": None if m.tabla_casos is None else len(m.tabla_casos),
        "por_caso": None if m.tabla_casos is None else m.tabla_casos.to_dict(orient="records"),
        "etiquetas_pares": m.etiquetas,
        "huella_particion": m.huella,
        "cambios_vs_base": m.cambios_vs_base,
    }
    if m.verdaderos is not None:
        salida["fp"] = _pares_con_caso(m.predichos - m.verdaderos, caso_por_id, m.verdaderos)
        salida["fn"] = _pares_con_caso(m.verdaderos - m.predichos, caso_por_id, m.verdaderos)
    return salida


def elegir_recomendada(mediciones: Sequence[Medicion]) -> Medicion:
    """Precisión antes que recall: mayor precisión por pares; empate → mayor recall; empate →
    menos variables extra. Es la regla del proyecto, no una opinión. Exige VERDAD_GRUPOS."""
    sin_verdad = [m.configuracion.nombre for m in mediciones if m.pares is None]
    if sin_verdad or not mediciones:
        raise ValueError(
            f"Sin VERDAD_GRUPOS no hay con qué elegir (configuraciones sin cifras: {sin_verdad})."
        )

    def clave(m: Medicion) -> tuple[float, float, int]:
        assert m.pares is not None
        return (
            float(m.pares["precision"]),
            float(m.pares["recall"]),
            -len(m.configuracion.extra_features),
        )

    return max(mediciones, key=clave)
