"""record_linkage.resultado — ResultadoLinkage: lo que devuelven linkage(), dedupe() y link() (F1.9).

Un solo objeto para los tres caminos, con las tablas del estándar
(``contrato.py``), el manifiesto, las métricas y ``validar()``, que comprueba
el contrato contra lo que de verdad hay en los DataFrames.

Compatibilidad
--------------
Hasta 0.22.x ``linkage()`` devolvía un ``dict`` con ``"correlative"``,
``"golden"``, ``"report_files"``, ``"preprocessing"``… Para no romper a nadie,
``ResultadoLinkage`` acepta ``res["correlative"]``, ``res.get(...)``, ``in`` y
``.keys()`` con ``DeprecationWarning`` y los mapea a los campos nuevos (el
mismo patrón que ``pipeline/result.py::PipelineResult``). Las claves viejas
desaparecen en 1.0.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from . import contrato
from .pipeline.errores import ContratoSalidaError

__all__ = ["ReporteValidacion", "ResultadoLinkage"]


@dataclass
class ReporteValidacion:
    """Resultado de ``ResultadoLinkage.validar()``.

    Attributes:
        ok: True si no hubo ningún fallo.
        fallos: un texto por incumplimiento, legible y accionable.
    """

    ok: bool
    fallos: list[str] = field(default_factory=list)

    def resumen(self) -> str:
        if self.ok:
            return f"contrato {contrato.VERSION_CONTRATO}: cumple"
        return f"contrato {contrato.VERSION_CONTRATO}: {len(self.fallos)} fallo(s)\n" + "\n".join(
            f"  - {f}" for f in self.fallos
        )


def _diccionario_vacio() -> pd.DataFrame:
    return pd.DataFrame(columns=list(contrato.COLUMNAS_DICCIONARIO))


#: Claves del ``dict`` viejo y el campo nuevo al que mapean. Las que no están
#: aquí (``report_files``, ``preprocessing``, ``ingestion_reports``,
#: ``matcher_stats``, ``matcher_decisions``) viven en ``metricas``.
_CLAVES_VIEJAS: dict[str, str] = {
    "correlative": "correlativa",
    "golden": "golden",
    "work_dir": "dir_trabajo",
}
_CLAVES_EN_METRICAS: tuple[str, ...] = (
    "report_files",
    "preprocessing",
    "ingestion_reports",
    "matcher_stats",
    "matcher_decisions",
)


def _avisar(clave: str) -> None:
    nuevo = _CLAVES_VIEJAS.get(clave, f"metricas[{clave!r}]")
    warnings.warn(
        f"res[{clave!r}] está obsoleto desde 0.23.0: use res.{nuevo} (ResultadoLinkage, "
        f"contrato {contrato.VERSION_CONTRATO}). Las claves del dict viejo desaparecen en 1.0.",
        DeprecationWarning,
        stacklevel=3,
    )


@dataclass
class ResultadoLinkage:
    """Resultado tipado de la fachada: las tablas del estándar + trazabilidad.

    Attributes:
        correlativa: LA tabla, una fila por registro de entrada, con las 12
            columnas fijas del contrato primero y después todas las de la fuente.
        golden: una fila por entidad (13 columnas de v1 + ``ID_ENTIDAD``), o
            None si la ruta no lo produce en memoria (``dedupe`` escribe los
            golden por régimen en ``metricas['output_dir']``).
        enlaces: solo vinculación (F3); None si no aplica.
        revision: pares por decidir con la forma del archivo de decisiones;
            vacío con las columnas del contrato si no hay.
        diccionario: tabla · columna · tipo · significado · origen · alias_es.
        manifiesto: trazabilidad — función, timestamp UTC, seed, parámetros y su
            hash, huella de cada insumo, versiones, ``contrato`` y el reporte de
            ``completar`` (renombres, regla de ID_REGISTRO, origen de SCORE_PAR…).
        metricas: conteos de la corrida y estadísticas del pipeline.
        dir_trabajo: carpeta con L1…L5 (``_trabajo/``), donde quedan las
            columnas técnicas que salen del entregable.
    """

    correlativa: pd.DataFrame
    golden: pd.DataFrame | None = None
    metricas: dict[str, Any] = field(default_factory=dict)
    manifiesto: dict[str, Any] = field(default_factory=dict)
    enlaces: pd.DataFrame | None = None
    revision: pd.DataFrame = field(default_factory=contrato.revision_vacia)
    diccionario: pd.DataFrame = field(default_factory=_diccionario_vacio)
    dir_trabajo: Path | None = None

    # ── Resumen ──────────────────────────────────────────────────────────
    def resumen(self) -> str:
        """Resumen humano de una línea (para logs y actas)."""
        m, man = self.metricas, self.manifiesto
        return (
            f"{man.get('funcion', '?')}: {m.get('n_registros', '?')} registros "
            f"→ {m.get('n_grupos', '?')} grupos únicos "
            f"(rues-linker {man.get('versiones', {}).get('rues-linker', '?')}, "
            f"hash_parametros {man.get('hash_parametros', '?')})"
        )

    # ── Validación del contrato ──────────────────────────────────────────
    def validar(self, estricto: bool = False) -> ReporteValidacion:
        """Comprueba el contrato de salida sobre los DataFrames reales.

        Exige: orden y tipos de las columnas fijas, ``ID_REGISTRO`` único, N
        filas de la correlativa = N de entrada (``manifiesto['entradas']``),
        golden sin NaN en métricas y una fila por ``ID_GRUPO`` de la
        correlativa, ``ID_ENTIDAD`` no nulo y consistente (un ``ID_GRUPO`` ↔
        un ``ID_ENTIDAD``), ``METODO_UNION`` dentro del vocabulario y
        ``CONFIANZA`` (cuando no es nula) dentro de ALTA · MEDIA · BAJA.

        Args:
            estricto: si True y hay fallos, lanza ``ContratoSalidaError`` con
                la lista completa en vez de devolverla.
        """
        fallos: list[str] = []
        fallos += _validar_correlativa(self.correlativa, self.manifiesto)
        if self.golden is not None:
            fallos += _validar_golden(self.golden, self.correlativa)
        if self.enlaces is not None:
            fallos += _validar_columnas(self.enlaces, contrato.ENLACES, "enlaces")
        fallos += _validar_columnas(self.revision, contrato.REVISION, "revision", solo_orden=True)
        reporte = ReporteValidacion(ok=not fallos, fallos=fallos)
        if estricto and not reporte.ok:
            raise ContratoSalidaError(reporte.fallos)
        return reporte

    # ── Compatibilidad con el dict viejo (DeprecationWarning) ────────────
    def _valor_viejo(self, clave: str) -> Any:
        if clave in _CLAVES_VIEJAS:
            return getattr(self, _CLAVES_VIEJAS[clave])
        if clave in _CLAVES_EN_METRICAS and clave in self.metricas:
            return self.metricas[clave]
        raise KeyError(clave)

    def __getitem__(self, clave: str) -> Any:
        _avisar(clave)
        return self._valor_viejo(clave)

    def get(self, clave: str, default: Any = None) -> Any:
        _avisar(clave)
        try:
            valor = self._valor_viejo(clave)
        except KeyError:
            return default
        return default if valor is None else valor

    def __contains__(self, clave: object) -> bool:
        if not isinstance(clave, str):
            return False
        _avisar(clave)
        try:
            return self._valor_viejo(clave) is not None
        except KeyError:
            return False

    def keys(self) -> list[str]:
        """Las claves que tenía el ``dict`` viejo (``work_dir`` nunca fue una)."""
        _avisar("keys()")
        candidatas = ("correlative", "golden", *_CLAVES_EN_METRICAS)
        return [c for c in candidatas if self._presente(c)]

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    def _presente(self, clave: str) -> bool:
        try:
            return self._valor_viejo(clave) is not None
        except KeyError:
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Comprobaciones (funciones puras sobre DataFrames)
# ─────────────────────────────────────────────────────────────────────────────


def _cumple_familia(dtype: Any, familia: str) -> bool:
    tipos = pd.api.types
    if familia == "texto":
        return bool(tipos.is_string_dtype(dtype) or tipos.is_object_dtype(dtype))
    if familia == "entero":
        return bool(tipos.is_integer_dtype(dtype))
    if familia == "decimal":
        return bool(tipos.is_float_dtype(dtype))
    if familia == "booleano":
        return bool(tipos.is_bool_dtype(dtype))
    return True


def _validar_columnas(
    df: pd.DataFrame,
    columnas: tuple[contrato.ColumnaContrato, ...],
    tabla: str,
    *,
    solo_orden: bool = False,
) -> list[str]:
    """Orden de las columnas fijas al frente y familia de tipo de cada una."""
    fallos: list[str] = []
    esperadas = [c.nombre for c in columnas]
    actuales = [str(c) for c in df.columns[: len(esperadas)]]
    faltan = [c for c in esperadas if c not in df.columns]
    if faltan:
        fallos.append(f"{tabla}: faltan las columnas fijas {faltan}.")
    elif actuales != esperadas:
        fallos.append(
            f"{tabla}: las columnas fijas no van primero y en el orden del contrato "
            f"{contrato.VERSION_CONTRATO}. Esperado: {esperadas}. Actual: {actuales}."
        )
    if solo_orden:
        return fallos
    for col in columnas:
        if col.nombre not in df.columns:
            continue
        familia = contrato.familia_tipo(col.tipo)
        if not _cumple_familia(df[col.nombre].dtype, familia):
            fallos.append(
                f"{tabla}: la columna {col.nombre} debe ser {familia} ({col.tipo}) y es "
                f"{df[col.nombre].dtype}."
            )
    return fallos


def _validar_correlativa(c: pd.DataFrame, manifiesto: dict[str, Any]) -> list[str]:
    fallos = _validar_columnas(c, contrato.CORRELATIVA, "correlativa")
    if "ID_REGISTRO" in c.columns:
        nulos = int(c["ID_REGISTRO"].isna().sum())
        if nulos:
            fallos.append(f"correlativa: ID_REGISTRO tiene {nulos} nulo(s).")
        repetidos = int(c["ID_REGISTRO"].duplicated().sum())
        if repetidos:
            fallos.append(
                f"correlativa: ID_REGISTRO no es único ({repetidos} repetido(s)); la "
                f"identidad del registro deja de ser estable."
            )
    entradas = manifiesto.get("entradas")
    if isinstance(entradas, dict) and entradas:
        n_entrada = sum(int(e.get("filas", 0)) for e in entradas.values() if isinstance(e, dict))
        if n_entrada != len(c):
            fallos.append(
                f"correlativa: trae {len(c)} filas y la entrada tuvo {n_entrada} "
                f"(manifiesto['entradas']); debe haber una fila por registro de entrada."
            )
    if "ID_ENTIDAD" in c.columns:
        nulos = int(c["ID_ENTIDAD"].isna().sum())
        if nulos:
            fallos.append(f"correlativa: ID_ENTIDAD tiene {nulos} nulo(s).")
        if "ID_GRUPO" in c.columns and len(c):
            por_grupo = c.groupby("ID_GRUPO", sort=False)["ID_ENTIDAD"].nunique(dropna=False)
            malos = por_grupo[por_grupo != 1]
            if len(malos):
                fallos.append(
                    f"correlativa: {len(malos)} ID_GRUPO con más de un ID_ENTIDAD "
                    f"(p. ej. {malos.index[:5].tolist()}); un ID_GRUPO ↔ un ID_ENTIDAD."
                )
            por_entidad = c.groupby("ID_ENTIDAD", sort=False)["ID_GRUPO"].nunique()
            malos = por_entidad[por_entidad != 1]
            if len(malos):
                fallos.append(
                    f"correlativa: {len(malos)} ID_ENTIDAD repartidos en más de un ID_GRUPO "
                    f"(p. ej. {malos.index[:5].tolist()})."
                )
    if "METODO_UNION" in c.columns:
        fuera = sorted(set(c["METODO_UNION"].dropna().unique()) - set(contrato.METODOS_UNION))
        if fuera:
            fallos.append(
                f"correlativa: METODO_UNION trae valores fuera del vocabulario {fuera}; "
                f"válidos: {list(contrato.METODOS_UNION)}."
            )
    if "CONFIANZA" in c.columns:
        fuera = sorted(set(c["CONFIANZA"].dropna().unique()) - set(contrato.NIVELES_CONFIANZA))
        if fuera:
            fallos.append(
                f"correlativa: CONFIANZA trae valores fuera de {list(contrato.NIVELES_CONFIANZA)}: "
                f"{fuera}."
            )
    return fallos


def _validar_golden(g: pd.DataFrame, c: pd.DataFrame) -> list[str]:
    fallos = _validar_columnas(g, contrato.GOLDEN, "golden")
    metricas = [m for m in contrato.COLUMNAS_METRICAS_GOLDEN if m in g.columns]
    if metricas:
        con_nan = g[metricas].isna().sum()
        con_nan = con_nan[con_nan > 0]
        if len(con_nan):
            fallos.append(
                "golden: métricas con NaN: "
                + ", ".join(f"{k} ({v})" for k, v in con_nan.items())
                + "; un golden sin métricas no describe a su grupo."
            )
    if "ID_GRUPO" in g.columns and "ID_GRUPO" in c.columns:
        repetidos = int(g["ID_GRUPO"].duplicated().sum())
        if repetidos:
            fallos.append(f"golden: ID_GRUPO repetido {repetidos} vez/veces.")
        grupos_c = set(c["ID_GRUPO"].unique())
        grupos_g = set(g["ID_GRUPO"].unique())
        sin_golden = sorted(grupos_c - grupos_g)
        huerfanos = sorted(grupos_g - grupos_c)
        if sin_golden:
            fallos.append(
                f"golden: {len(sin_golden)} ID_GRUPO de la correlativa sin fila en el golden "
                f"(p. ej. {sin_golden[:5]})."
            )
        if huerfanos:
            fallos.append(
                f"golden: {len(huerfanos)} fila(s) huérfana(s) cuyo ID_GRUPO no está en la "
                f"correlativa (p. ej. {huerfanos[:5]})."
            )
    if "ID_ENTIDAD" in g.columns:
        nulos = int(g["ID_ENTIDAD"].isna().sum())
        if nulos:
            fallos.append(f"golden: ID_ENTIDAD tiene {nulos} nulo(s).")
        if "ID_GRUPO" in g.columns and {"ID_GRUPO", "ID_ENTIDAD"} <= set(c.columns) and len(c):
            esperado = c.drop_duplicates("ID_GRUPO").set_index("ID_GRUPO")["ID_ENTIDAD"]
            actual = g.set_index("ID_GRUPO")["ID_ENTIDAD"]
            comunes = actual.index.intersection(esperado.index)
            distintos = int((actual.loc[comunes] != esperado.loc[comunes]).sum())
            if distintos:
                fallos.append(
                    f"golden: {distintos} ID_GRUPO con ID_ENTIDAD distinto del de la correlativa."
                )
    return fallos
