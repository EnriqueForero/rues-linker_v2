"""evaluation.plantilla_base_real — leer la plantilla de base real, correr el motor y medirlo contra su verdad.

La plantilla (``docs/PLANTILLA_BASE_REAL.md``) es un libro con hojas ``FUENTE_*`` (una por fuente, con
las 15 columnas de la base real), ``VERDAD_GRUPOS`` (a qué grupo pertenece cada registro y de qué caso
es) y ``CASOS`` (qué debe hacer el motor con cada caso). Una base real recién estructurada trae solo
las hojas ``FUENTE_*``. Este módulo es la ÚNICA implementación de:

- :func:`leer_plantilla`: las hojas ``FUENTE_*`` por prefijo (del Excel o de los CSV ``plantilla_*.csv``
  de una carpeta) y, si existen, ``VERDAD_GRUPOS`` y ``CASOS``;
- :func:`correr_motor`: ``linkage()`` sobre las fuentes con ``col_nit="NIT"``, ``col_name="RAZON_SOCIAL"``
  y ``col_ciudad="MUNICIPIO"``, con o sin variables extra, devolviendo la correlativa con
  ``ID_REGISTRO_FUENTE`` en cada fila;
- :func:`evaluar_casos`, :func:`pares_verdaderos`, :func:`pares_por_particion`,
  :func:`pares_predichos` y :func:`metricas_pares`: la medición contra ``VERDAD_GRUPOS`` y ``CASOS``.

Lo mínimo que exige cada hoja (lo demás es opcional): ``FUENTE_*`` trae ``ID_REGISTRO_FUENTE`` (único
en TODO el libro: prefijo de la fuente + su clave, ``CRM-000017``) y ``RAZON_SOCIAL``; ``NIT`` y
``MUNICIPIO`` son las columnas que el motor usa como identificador y ciudad, y si faltan se avisa
(se corre igual, solo por nombre). ``VERDAD_GRUPOS`` trae ``ID_REGISTRO_FUENTE`` e ``ID_GRUPO``
(bastan para precisión y recall por pares); ``CASO`` es opcional y, con la hoja ``CASOS``, da la
tabla por caso.

Hasta 2026-10-14 vivía en ``scripts/verificar_plantilla_base_real.py``; el notebook 13 y
``evaluation.multicriterio`` lo necesitan desde el paquete instalado (``scripts/`` no se instala), así
que los scripts lo importan de aquí. Las constantes de la plantilla (``FUENTES_PLANTILLA``,
``PREFIJO_HOJA_FUENTE``) se repiten en ``scripts/generar_plantilla_base_real.py`` porque el generador no
depende del paquete; ``tests/test_multicriterio.py`` comprueba que coincidan.
"""

from __future__ import annotations

import contextlib
import io
import os
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

__all__ = [
    "COLUMNAS_TABLA",
    "COL_ID",
    "FUENTES_PLANTILLA",
    "PREFIJO_CSV_FUENTE",
    "PREFIJO_HOJA_FUENTE",
    "LibroPlantilla",
    "correr_motor",
    "en_silencio",
    "evaluar_casos",
    "leer_plantilla",
    "metricas_pares",
    "pares_por_particion",
    "pares_predichos",
    "pares_verdaderos",
    "tabla_markdown",
]

#: La columna con la que se nombra cada registro y cada par (la misma que mide
#: ``scripts/medir_verdad_real.py --col-id``).
COL_ID = "ID_REGISTRO_FUENTE"
#: Las fuentes de la plantilla, en su orden; una base real puede traer otras.
FUENTES_PLANTILLA: tuple[str, ...] = ("CRM", "RUES", "ADUANAS")
#: Prefijo de las hojas de fuente del Excel (``FUENTE_CRM``, ``FUENTE_DIAN``…).
PREFIJO_HOJA_FUENTE = "FUENTE_"
#: Prefijo de los CSV de fuente de una carpeta (``plantilla_fuente_crm.csv``).
PREFIJO_CSV_FUENTE = "plantilla_fuente_"
#: Columnas que el motor usa además del nombre (``col_nit`` y ``col_ciudad`` de :func:`correr_motor`);
#: si una fuente no las trae, se avisa y se corre igual.
COLUMNAS_QUE_USA_EL_MOTOR: tuple[str, ...] = ("NIT", "MUNICIPIO")
#: Lo mínimo de ``VERDAD_GRUPOS`` para medir por pares; ``CASO`` es opcional.
COLUMNAS_MINIMAS_VERDAD: tuple[str, ...] = (COL_ID, "ID_GRUPO")
#: Columnas de la tabla por caso de :func:`evaluar_casos`.
COLUMNAS_TABLA: tuple[str, ...] = (
    "CASO",
    "ESPERADO",
    "ACERTO",
    "GRUPOS",
    "GRUPOS_UNIDOS",
    "PARES_ENTRE_GRUPOS_UNIDOS",
    "DETALLE",
)


@dataclass
class LibroPlantilla:
    """Las hojas que hacen falta para medir; ``verdad`` y ``casos`` faltan en una base real."""

    fuentes: dict[str, pd.DataFrame]
    verdad: pd.DataFrame | None
    casos: pd.DataFrame | None

    @property
    def con_verdad(self) -> bool:
        """True si trae ``VERDAD_GRUPOS``: hay precisión y recall por pares (no solo correr)."""
        return self.verdad is not None

    @property
    def con_casos(self) -> bool:
        """True si además trae la hoja ``CASOS`` y ``VERDAD_GRUPOS`` tiene la columna ``CASO``: hay
        tabla por caso y aporte por variable."""
        return self.verdad is not None and self.casos is not None and "CASO" in self.verdad.columns


def _orden_fuentes(nombres: list[str]) -> list[str]:
    """Primero las fuentes de la plantilla en su orden (CRM, RUES, ADUANAS), luego las demás."""
    conocidas = [f for f in FUENTES_PLANTILLA if f in nombres]
    return conocidas + sorted(f for f in nombres if f not in FUENTES_PLANTILLA)


def _leer_excel(
    datos: Path,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame | None, pd.DataFrame | None]:
    hojas: dict[str, pd.DataFrame] = {
        str(nombre): hoja
        for nombre, hoja in pd.read_excel(
            datos, sheet_name=None, dtype=str, keep_default_na=False
        ).items()
    }
    fuentes = {
        nombre[len(PREFIJO_HOJA_FUENTE) :]: hoja
        for nombre, hoja in hojas.items()
        if nombre.startswith(PREFIJO_HOJA_FUENTE)
    }
    return fuentes, hojas.get("VERDAD_GRUPOS"), hojas.get("CASOS")


def _leer_carpeta(
    datos: Path,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame | None, pd.DataFrame | None]:
    def csv(nombre: str) -> pd.DataFrame | None:
        ruta = datos / f"plantilla_{nombre}.csv"
        if not ruta.is_file():
            return None
        return pd.read_csv(ruta, dtype=str, keep_default_na=False)

    fuentes = {
        ruta.stem[len(PREFIJO_CSV_FUENTE) :].upper(): pd.read_csv(
            ruta, dtype=str, keep_default_na=False
        )
        for ruta in sorted(datos.glob(f"{PREFIJO_CSV_FUENTE}*.csv"))
    }
    return fuentes, csv("verdad_grupos"), csv("casos")


def leer_plantilla(datos: str | Path) -> LibroPlantilla:
    """Las hojas ``FUENTE_*`` (por prefijo) y, si existen, VERDAD_GRUPOS y CASOS.

    ``datos`` es el Excel (archivo) o la carpeta con los CSV ``plantilla_<hoja>.csv``. Todo se lee
    como texto (``dtype=str, keep_default_na=False``): así el NIT conserva los ceros y los vacíos.

    Raises:
        ValueError: no hay ninguna hoja de fuente; a una fuente le falta ``ID_REGISTRO_FUENTE`` o
            ``RAZON_SOCIAL``; un ``ID_REGISTRO_FUENTE`` se repite entre las hojas (cada hoja numerada
            1..N: la medición saldría falsa en silencio); o ``VERDAD_GRUPOS`` no trae
            ``ID_REGISTRO_FUENTE`` e ``ID_GRUPO``.

    Warns:
        UserWarning: a una fuente le falta ``NIT`` o ``MUNICIPIO``: el motor corre igual, pero sin
            identificador (todo por nombre) o sin ciudad; si la columna existe con otro nombre, hay
            que renombrarla.
    """
    datos = Path(datos)
    if datos.is_file():
        fuentes, verdad, casos = _leer_excel(datos)
        donde = f"hojas {PREFIJO_HOJA_FUENTE}*"
    else:
        fuentes, verdad, casos = _leer_carpeta(datos)
        donde = f"archivos {PREFIJO_CSV_FUENTE}*.csv"
    if not fuentes:
        raise ValueError(
            f"{datos} no tiene {donde}. Cada fuente va en una hoja cuyo nombre empieza por "
            f"{PREFIJO_HOJA_FUENTE} (FUENTE_CRM, FUENTE_DIAN…) con las columnas de la plantilla."
        )
    for nombre, hoja in fuentes.items():
        faltan = [c for c in (COL_ID, "RAZON_SOCIAL") if c not in hoja.columns]
        if faltan:
            raise ValueError(
                f"A la fuente {nombre} le faltan {faltan}: sin {COL_ID} no se puede nombrar un par y "
                "sin RAZON_SOCIAL el motor no tiene con qué vincular."
            )
        sin_usar = [c for c in COLUMNAS_QUE_USA_EL_MOTOR if c not in hoja.columns]
        if sin_usar:
            warnings.warn(
                f"La fuente {nombre} no trae {sin_usar}. El motor corre igual, pero "
                + (
                    "sin NIT todos sus registros se vinculan solo por nombre (régimen sin "
                    "identificador) "
                    if "NIT" in sin_usar
                    else ""
                )
                + ("y sin MUNICIPIO no hay ciudad que comparar " if "MUNICIPIO" in sin_usar else "")
                + "(docs/PLANTILLA_BASE_REAL.md §2). Si la columna existe con otro nombre "
                "(NIT_EXPORTADOR, CIUDAD…), renómbrela antes de cargar.",
                UserWarning,
                stacklevel=2,
            )
    _exigir_ids_unicos(fuentes)
    if verdad is not None:
        faltan = [c for c in COLUMNAS_MINIMAS_VERDAD if c not in verdad.columns]
        if faltan:
            raise ValueError(
                f"A VERDAD_GRUPOS le faltan {faltan}; mínimo {list(COLUMNAS_MINIMAS_VERDAD)} "
                "(CASO es opcional: sin ella hay precisión y recall por pares pero no tabla por "
                f"caso). Columnas: {list(verdad.columns)}."
            )
    fuentes = {f: fuentes[f] for f in _orden_fuentes(list(fuentes))}
    return LibroPlantilla(fuentes=fuentes, verdad=verdad, casos=casos)


def _exigir_ids_unicos(fuentes: dict[str, pd.DataFrame]) -> None:
    """Un ``ID_REGISTRO_FUENTE`` repetido entre hojas no rompe nada por sí solo: la tabla de pares
    muestra tres empresas en una celda, los pares salen mal y la huella cambia, todo en silencio.
    Por eso se exige aquí, con el remedio (CLAUDE.md §2.5)."""
    todos = pd.concat([df[COL_ID].astype(str) for df in fuentes.values()], ignore_index=True)
    repetidos = todos[todos.duplicated()].unique()
    if len(repetidos):
        raise ValueError(
            f"{len(repetidos)} {COL_ID} repetidos entre las hojas {PREFIJO_HOJA_FUENTE}* "
            f"(p. ej. {list(repetidos[:5])}). Cada registro necesita un id único en TODO el "
            "libro: anteponga el prefijo de la fuente a su clave (CRM-000017, RUES-000017), "
            "docs/PLANTILLA_BASE_REAL.md §3.3."
        )


@contextmanager
def en_silencio() -> Iterator[None]:
    """Apaga el progreso de tqdm, el logging INFO del pipeline y los DeprecationWarning durante una
    corrida de medición. Ojo: también calla los ``logger.warning`` del scorer (p. ej. «columna extra
    ausente»); por eso :func:`record_linkage.evaluation.multicriterio.columnas_que_ve_el_scorer`
    comprueba las columnas por archivo y no por log."""
    from record_linkage.evaluation.banco import silenciar_pipeline

    previo = os.environ.get("TQDM_DISABLE")
    os.environ["TQDM_DISABLE"] = "1"
    try:
        with silenciar_pipeline(), warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            yield
    finally:
        if previo is None:
            os.environ.pop("TQDM_DISABLE", None)
        else:
            os.environ["TQDM_DISABLE"] = previo


def correr_motor(
    fuentes: dict[str, pd.DataFrame],
    dir_trabajo: str | Path,
    extra_features: list[dict[str, object]] | None = None,
    ajustes_perfil: dict[str, object] | None = None,
) -> pd.DataFrame:
    """La correlativa de ``linkage()`` sobre las fuentes, con ``ID_REGISTRO_FUENTE`` en cada fila.

    No se pasa ``col_id``: el motor antepondría la fuente otra vez (``CRM-CRM-000001``); la
    correlativa conserva la columna de la fuente tal cual y es con ella con la que se mide.

    ``extra_features`` (opcional) son las variables adicionales del scorer, en la forma que acepta
    ``linkage()`` (``{"column", "weight", "type"}``), y ``ajustes_perfil`` (opcional) las
    sobrescrituras puntuales del perfil (p. ej. ``{"llaves_bloqueo": ["EMAIL"]}``); sin ellos el
    motor corre exactamente como antes. Ojo: como ``col_ciudad="MUNICIPIO"``, el motor renombra esa
    columna a ``CIUDAD`` antes de validar las variables extra, así que una variable sobre el
    municipio se declara como ``CIUDAD`` (``evaluation.multicriterio.a_columnas_del_motor`` traduce).
    """
    from record_linkage import linkage

    # ``linkage`` anota ``extra_features: list[str]`` pero acepta dicts (los normaliza
    # ``config/profiles.py``); la lista se pasa como ``Any`` para no mentirle al tipado.
    extras: list[Any] | None = list(extra_features) if extra_features else None
    with en_silencio(), contextlib.redirect_stderr(io.StringIO()):
        resultado = linkage(
            {f: df.copy() for f, df in fuentes.items()},
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            col_ciudad="MUNICIPIO",
            work_dir=str(dir_trabajo),
            skip_reporting=True,
            extra_features=extras,
            ajustes_perfil=dict(ajustes_perfil) if ajustes_perfil else None,
        )
    correlativa = resultado.correlativa
    if COL_ID not in correlativa.columns:
        raise RuntimeError(
            f"la correlativa no trae {COL_ID}; columnas: {list(correlativa.columns)}"
        )
    return correlativa


def _grupo_predicho(correlativa: pd.DataFrame) -> pd.Series:
    """Serie ``ID_REGISTRO_FUENTE → ID_GRUPO`` (ambos como texto)."""
    return pd.Series(
        correlativa["ID_GRUPO"].astype(str).to_numpy(),
        index=pd.Index(correlativa[COL_ID].astype(str)),
    )


def _marco_con_prediccion(correlativa: pd.DataFrame, verdad: pd.DataFrame) -> pd.DataFrame:
    predicho = _grupo_predicho(correlativa)
    faltan = sorted(set(verdad[COL_ID].astype(str)) - set(predicho.index))
    if faltan:
        raise RuntimeError(
            f"{len(faltan)} registros de VERDAD_GRUPOS no están en la correlativa: {faltan[:5]}"
        )
    columnas = [c for c in (COL_ID, "ID_GRUPO", "CASO") if c in verdad.columns]
    marco = verdad[columnas].astype(str)
    marco["PRED"] = marco[COL_ID].map(predicho).to_numpy()
    return marco


def _veredicto_del_caso(
    caso: str,
    esperado: str,
    grupos_unidos: int,
    n_multi: int,
    mezclados: int,
    fuga_n: int,
    n_registros: int,
) -> tuple[bool, str]:
    """El veredicto y el texto DETALLE de un caso. El texto es parte del contrato: lo citan
    ``docs/evidencia/plantilla_base_real_casos.json`` y la guía §5, y ``tests/test_plantilla_base_real.py``
    lo compara con la evidencia."""
    if esperado == "unir":
        acerto = grupos_unidos == n_multi and fuga_n == 0
        detalle = f"{grupos_unidos}/{n_multi} grupos unidos completos"
    elif esperado == "revisar":
        acerto = grupos_unidos == 0 and mezclados == 0 and fuga_n == 0
        detalle = (
            f"{grupos_unidos}/{n_multi} grupos unidos y {mezclados} pares entre grupos unidos "
            "(se esperaba 0: sin evidencia suficiente, el motor no debe adivinar)"
        )
    elif caso == "singleton":
        acerto = fuga_n == 0 and mezclados == 0
        detalle = f"{fuga_n + mezclados} de {n_registros} singletons mezclados con otro registro"
    else:  # no unir
        acerto = mezclados == 0 and fuga_n == 0
        detalle = (
            f"{mezclados} pares de entidades distintas unidos; "
            f"{grupos_unidos}/{n_multi} entidades con varios registros unidas internamente"
        )
    if fuga_n and esperado != "no unir" and caso != "singleton":
        detalle += f"; {fuga_n} registro(s) mezclados con otro grupo ajeno al caso"
    elif fuga_n and esperado == "no unir":
        detalle += f"; {fuga_n} registro(s) mezclados con un grupo ajeno al caso"
    return bool(acerto), detalle


def evaluar_casos(
    correlativa: pd.DataFrame, verdad: pd.DataFrame, casos: pd.DataFrame
) -> pd.DataFrame:
    """Una fila por CASO: qué se esperaba, si el motor acertó y el detalle.

    ``unir`` acierta si todos los registros de cada grupo verdadero del caso quedan en un mismo
    grupo predicho; ``no unir`` si ningún par de grupos verdaderos distintos del caso queda junto;
    ``revisar`` si el motor no une solo; ``singleton`` si cada registro queda solo. Los fallos no se
    ocultan: la plantilla también sirve para mostrar lo que el motor no sabe hacer hoy.
    """
    if "CASO" not in verdad.columns:
        raise ValueError(
            "VERDAD_GRUPOS no trae la columna CASO: sin ella no se puede decir qué caso acertó. "
            "Añádala (un caso por grupo) o mida solo por pares con metricas_pares."
        )
    marco = _marco_con_prediccion(correlativa, verdad)
    filas = []
    for caso, esperado in zip(casos["CASO"], casos["QUE_DEBE_HACER_EL_MOTOR"], strict=True):
        sub = marco[marco["CASO"] == caso]
        por_grupo = sub.groupby("ID_GRUPO")["PRED"].agg(["nunique", "size"])
        multi = por_grupo[por_grupo["size"] >= 2]
        grupos_unidos = int((multi["nunique"] == 1).sum())
        # pares de registros del caso que caen en grupos verdaderos distintos y predicho igual
        cruce = sub.merge(sub, on="PRED", suffixes=("_1", "_2"))
        cruce = cruce[
            (cruce[f"{COL_ID}_1"] < cruce[f"{COL_ID}_2"])
            & (cruce["ID_GRUPO_1"] != cruce["ID_GRUPO_2"])
        ]
        mezclados = len(cruce)
        # registros del caso unidos con registros de CUALQUIER otro grupo verdadero
        otros = marco[~marco["ID_GRUPO"].isin(sub["ID_GRUPO"])]
        fuga = sub.merge(otros[["PRED", "ID_GRUPO"]], on="PRED", suffixes=("", "_otro"))
        fuga_n = int(fuga[COL_ID].nunique())
        acerto, detalle = _veredicto_del_caso(
            str(caso), str(esperado), grupos_unidos, len(multi), mezclados, fuga_n, len(sub)
        )
        filas.append(
            {
                "CASO": caso,
                "ESPERADO": esperado,
                "ACERTO": acerto,
                "GRUPOS": int(sub["ID_GRUPO"].nunique()),
                "GRUPOS_UNIDOS": grupos_unidos,
                "PARES_ENTRE_GRUPOS_UNIDOS": mezclados,
                "DETALLE": detalle,
            }
        )
    return pd.DataFrame(filas, columns=list(COLUMNAS_TABLA))


def _pares_de(marco: pd.DataFrame, col: str) -> set[tuple[str, str]]:
    m = marco[[COL_ID, col]].merge(marco[[COL_ID, col]], on=col, suffixes=("_1", "_2"))
    m = m[m[f"{COL_ID}_1"] < m[f"{COL_ID}_2"]]
    return set(zip(m[f"{COL_ID}_1"], m[f"{COL_ID}_2"], strict=True))


def pares_verdaderos(verdad: pd.DataFrame) -> set[tuple[str, str]]:
    """Los pares ``(ID_1 < ID_2)`` de un mismo ``ID_GRUPO`` de VERDAD_GRUPOS. Es la única
    implementación de «mismo grupo verdadero»: la usan :func:`pares_por_particion` y
    ``evaluation.multicriterio.pares_de_prueba``."""
    return _pares_de(verdad[[COL_ID, "ID_GRUPO"]].astype(str), "ID_GRUPO")


def pares_predichos(correlativa: pd.DataFrame) -> set[tuple[str, str]]:
    """Los pares ``(ID_1 < ID_2)`` que la partición predicha une, sobre todos los registros de la
    correlativa (no hace falta verdad: es lo que se compara entre dos corridas de una base real)."""
    marco = pd.DataFrame(
        {COL_ID: correlativa[COL_ID].astype(str), "PRED": correlativa["ID_GRUPO"].astype(str)}
    )
    return _pares_de(marco, "PRED")


def pares_por_particion(
    correlativa: pd.DataFrame, verdad: pd.DataFrame
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """``(verdaderos, predichos)``: los pares ``(ID_1 < ID_2)`` de la partición de VERDAD_GRUPOS y
    de la predicha restringida a sus registros, como conjuntos de ids. Es la base de
    :func:`metricas_pares` y de lo que ``evaluation.multicriterio`` usa para decir qué pares gana o
    pierde cada configuración."""
    marco = _marco_con_prediccion(correlativa, verdad)
    return pares_verdaderos(verdad), _pares_de(marco, "PRED")


def metricas_pares(correlativa: pd.DataFrame, verdad: pd.DataFrame) -> dict[str, float | int]:
    """Precisión, recall y F1 por pares contra la partición completa de VERDAD_GRUPOS."""
    verdaderos, predichos = pares_por_particion(correlativa, verdad)
    tp = len(verdaderos & predichos)
    fp = len(predichos - verdaderos)
    fn = len(verdaderos - predichos)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "pares_verdaderos": len(verdaderos),
        "pares_predichos": len(predichos),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def tabla_markdown(tabla: pd.DataFrame) -> str:
    """La tabla por caso de :func:`evaluar_casos` en Markdown (para la consola y la guía)."""
    lineas = [
        "| CASO | Esperado | Motor | Detalle |",
        "|---|---|---|---|",
    ]
    for fila in tabla.itertuples(index=False):
        veredicto = "acertó" if fila.ACERTO else "**falló**"
        lineas.append(f"| `{fila.CASO}` | {fila.ESPERADO} | {veredicto} | {fila.DETALLE} |")
    return "\n".join(lineas)
