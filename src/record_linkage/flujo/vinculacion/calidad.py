"""Hallazgos sobre los insumos que cambian la lectura del resultado (hoja CALIDAD_DATOS).

Cada función revisa UN aspecto de los datos de entrada (métricas vacías o repetidas, columnas constantes,
filas idénticas, nombres truncados, agentes de carga, países sin cobertura…) y, si lo encuentra, agrega una
fila con qué pasó, cuántas filas toca, su impacto y qué hacer. El orden de las revisiones es el orden de
las filas de la hoja.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from record_linkage.flujo.vinculacion.config import ConfigVinculacion, Fuente
from record_linkage.flujo.vinculacion.normalizar import serie_numerica

__all__ = ["calidad_datos"]

_TEXTO_SIN_ACCESO = "Your access does not allow"


@dataclass
class _Hallazgos:
    """Acumula las filas de la hoja CALIDAD_DATOS en el orden en que se encuentran."""

    filas: list[dict] = field(default_factory=list)

    def agregar(
        self,
        fuente: str,
        tema: str,
        hallazgo: str,
        filas: int,
        impacto: str,
        que_hacer: str,
        ejemplos: str = "",
    ) -> None:
        self.filas.append(
            {
                "FUENTE": fuente,
                "TEMA": tema,
                "HALLAZGO": hallazgo,
                "FILAS_AFECTADAS": filas,
                "IMPACTO": impacto,
                "QUE_HACER": que_hacer,
                "EJEMPLOS": ejemplos,
            }
        )


def _metricas_vacias(h: _Hallazgos, ancla: Fuente, pa: pd.DataFrame) -> None:
    for c in ancla.cols_metricas:
        v = pa[c]
        if len(v) and float(v.abs().sum()) == 0:
            h.agregar(
                ancla.etiqueta,
                "Métrica vacía",
                f"'{c}' vale 0 en las {len(v):,} filas.",
                len(v),
                "No sirve para priorizar ni ponderar.",
                "Pedir la base con la métrica diligenciada; mientras tanto no usarla.",
            )


def _metrica_repetida(h: _Hallazgos, ancla: Fuente, pa: pd.DataFrame, c: str) -> None:
    claves = ["ENT", *[k for k in ancla.claves_repeticion if k in pa.columns], c]
    rep = pa.duplicated(claves, keep=False) & (pa[c] != 0)
    extra = pa.loc[pa.duplicated(claves, keep="first"), c].sum()
    total = pa[c].sum()
    if not rep.any():
        return
    ej = pa.loc[rep].groupby(claves)["NOMBRE_ORIGINAL"].first().head(4).tolist()
    otras = [m for m in ancla.cols_metricas if m != c]
    con_dif = (
        int(pa.loc[rep].groupby(claves)[otras].transform("nunique").gt(1).any(axis=1).sum())
        if otras
        else 0
    )
    n_grupos = int(pa.loc[rep].groupby(claves).ngroups)
    sin_rep = total - extra
    h.agregar(
        ancla.etiqueta,
        "Métrica repetida",
        f"'{c}' se repite idéntico en {int(rep.sum())} filas ({n_grupos} grupos de misma "
        f"empresa y {'/'.join(ancla.claves_repeticion)}); en {con_dif} de ellas las demás métricas cambian (p. ej. distintas "
        f"operaciones con los mismos kilos): parece un total de un nivel superior copiado en cada fila.",
        int(rep.sum()),
        (
            f"Sumar todas las filas da {total:,.0f}; sin repetidos {sin_rep:,.0f}. Lo repetido es el {extra / total:.1%} del "
            f"total, es decir, la suma simple infla la cifra en {extra / sin_rep:.1%}."
        )
        if total and sin_rep
        else "",
        f"Usar {c.upper()}_SIN_REPETIDOS para comparar empresas y confirmar con quien generó la base cómo se agregó.",
        "; ".join(map(str, ej)),
    )


def _metricas_repetidas(h: _Hallazgos, ancla: Fuente, pa: pd.DataFrame) -> None:
    for c in ancla.metricas_repetidas:
        _metrica_repetida(h, ancla, pa, c)


def _columnas_constantes(h: _Hallazgos, ancla: Fuente, ca: pd.DataFrame) -> None:
    for c in [*ancla.cols_contexto]:
        vals = ca[c].astype(str).str.strip()
        if len(vals) > 1 and vals.nunique() == 1:
            h.agregar(
                ancla.etiqueta,
                "Columna constante",
                f"'{c}' tiene el mismo valor en todas las filas ({vals.iat[0]!r}).",
                len(vals),
                "No distingue a ninguna empresa (en EXPORTADORES se oculta).",
                "Nada; confirmar que el filtro de la base es el esperado.",
            )


def _filas_identicas(h: _Hallazgos, ancla: Fuente, ca: pd.DataFrame) -> None:
    cols_a = [c for c in ca.columns if c not in ("FILA_EXCEL", "ID_REGISTRO", "FUENTE")]
    dup = ca.duplicated(cols_a, keep=False)
    if not dup.any():
        return
    extra_ops = {
        m: float(serie_numerica(ca.loc[ca.duplicated(cols_a), m]).sum())
        for m in ancla.cols_metricas
    }
    h.agregar(
        ancla.etiqueta,
        "Filas idénticas",
        f"{int(dup.sum())} filas son idénticas en todas sus columnas ({int(ca.duplicated(cols_a).sum())} repetidas).",
        int(dup.sum()),
        "Pueden ser registros legítimos (sin la columna que los distingue) o duplicados; si son duplicados, inflan "
        + ", ".join(f"{m} en {v:,.0f}" for m, v in extra_ops.items() if v)
        + ".",
        "Confirmar con la fuente; el cruce no se ve afectado.",
        "; ".join(ca.loc[dup, ancla.col_nombre].astype(str).drop_duplicates().head(4)),
    )


def _nombres_truncados(h: _Hallazgos, ancla: Fuente, pa: pd.DataFrame) -> None:
    if not ancla.largo_truncamiento:
        return
    t = pa["TRUNCADO"]
    h.agregar(
        ancla.etiqueta,
        "Nombres truncados",
        f"{int(t.sum())} filas tienen el nombre cortado a {ancla.largo_truncamiento} caracteres.",
        int(t.sum()),
        "El final del nombre (a veces la forma legal o una palabra) se pierde; el motor lo tolera.",
        "Nada: el motor admite que la última palabra sea un prefijo.",
        "; ".join(pa.loc[t, "NOMBRE_ORIGINAL"].drop_duplicates().head(4)),
    )


def _agentes_de_carga(h: _Hallazgos, ancla: Fuente, pa: pd.DataFrame) -> None:
    inter = pa["INTERMEDIARIO"]
    if inter.any():
        h.agregar(
            ancla.etiqueta,
            "Agentes de carga",
            f"{int(inter.sum())} filas corresponden a agentes de carga o navieras, no al exportador real.",
            int(inter.sum()),
            "No representan a una empresa exportadora: solo se vinculan como EMPRESA exacta.",
            "Excluirlas de análisis de oferta exportable.",
            "; ".join(pa.loc[inter, "NOMBRE_ORIGINAL"].drop_duplicates().head(5)),
        )


def _nombres_sin_palabras_utiles(h: _Hallazgos, ancla: Fuente, pa: pd.DataFrame) -> None:
    sin = pa["SIN_NOMBRE_UTIL"]
    if sin.any():
        h.agregar(
            ancla.etiqueta,
            "Nombres sin palabras útiles",
            f"{int(sin.sum())} filas tienen un nombre que tras quitar la forma legal "
            "queda en letras sueltas (abreviaturas de aduana).",
            int(sin.sum()),
            "El motor no puede vincularlas por nombre; solo una decisión de revisión puede unirlas a otra grafía (ver DEDUPLICACION).",
            "Validar esas uniones en REVISION o completar el nombre en el origen.",
            "; ".join(pa.loc[sin, "NOMBRE_ORIGINAL"].drop_duplicates().head(6)),
        )


def _pais_sin_cobertura(
    h: _Hallazgos, ancla: Fuente, destinos: list[Fuente], pa: pd.DataFrame, prep: dict
) -> None:
    iso_dest: set[str] = set()
    for d in destinos:
        iso_dest |= set("|".join(prep[d.clave]["PAISES_EVIDENCIA"]).split("|")) - {""}
    sin_cob = pa[(pa["PAIS_ISO3"] != "") & ~pa["PAIS_ISO3"].isin(iso_dest)]
    if len(sin_cob) and destinos:
        paises = sin_cob["PAIS_ORIGINAL"].value_counts()
        h.agregar(
            ancla.etiqueta,
            "País sin cobertura",
            f"{len(sin_cob)} filas son de países que no aparecen en ninguna base destino: "
            + ", ".join(f"{k} ({v})" for k, v in paises.items())
            + ".",
            len(sin_cob),
            "Solo pueden vincularse por nombre a filiales/matrices en otros países (nivel GRUPO).",
            "Descargar Orbis/FDI también para esos países si se quieren cubrir.",
        )


def _entidades_html(h: _Hallazgos, ancla: Fuente, pa: pd.DataFrame) -> None:
    html = pa["NOMBRE_ORIGINAL"].str.contains(r"&[A-Za-z]+;|&#\d+;", regex=True)
    if html.any():
        h.agregar(
            ancla.etiqueta,
            "Entidades HTML",
            f"{int(html.sum())} nombres traen entidades HTML (&AMP;).",
            int(html.sum()),
            "La librería las decodifica antes de comparar.",
            "Nada.",
            "; ".join(pa.loc[html, "NOMBRE_ORIGINAL"].head(3)),
        )


def _ids_de_sucursal(h: _Hallazgos, d: Fuente, pr: pd.DataFrame) -> None:
    if not d.col_id:
        return
    suc = pr["ID_REGISTRO"].str.contains("-")
    if suc.any():
        h.agregar(
            d.etiqueta,
            "Identificadores de sucursal",
            f"{int(suc.sum())} IDs traen sufijo de sucursal (…-1003).",
            int(suc.sum()),
            "La sucursal y la casa matriz se tratan como la misma entidad (DEDUPLICACION).",
            "Nada.",
            "; ".join(pr.loc[suc, "ID_REGISTRO"].head(4)),
        )


def _columnas_sin_acceso(h: _Hallazgos, d: Fuente, cr: pd.DataFrame) -> None:
    bloqueadas = [
        c
        for c in cr.columns
        if cr[c].astype(str).str.contains(_TEXTO_SIN_ACCESO, na=False).mean() > 0.9
    ]
    if bloqueadas:
        h.agregar(
            d.etiqueta,
            "Columnas sin acceso",
            f"{len(bloqueadas)} columnas vienen con el texto 'Your access does not allow…'.",
            len(cr),
            "No aportan información.",
            "Pedir la licencia o excluirlas de la descarga.",
            "; ".join(bloqueadas[:5]),
        )


def _pais_de_consulta_distinto(h: _Hallazgos, d: Fuente, pr: pd.DataFrame) -> None:
    if not (d.cols_pais_extra and d.col_pais):
        return
    difiere = pr["PAISES_EVIDENCIA"].str.contains(r"\|")
    if difiere.any():
        h.agregar(
            d.etiqueta,
            "País de consulta ≠ sede",
            f"{int(difiere.sum())} registros tienen '{d.col_pais}' distinto de {', '.join(d.cols_pais_extra)}.",
            int(difiere.sum()),
            f"En fDi Markets '{d.col_pais}' es el país del inversionista consultado, no la sede de cada filial: ambos cuentan como 'mismo país'.",
            "Nada; leer MISMO_PAIS con esto en mente.",
            "; ".join(pr.loc[difiere, "NOMBRE_ORIGINAL"].head(4)),
        )


def _nombres_repetidos(h: _Hallazgos, d: Fuente, pr: pd.DataFrame) -> None:
    dn = pr[pr["NOMBRE_NORM"] != ""].duplicated(["NOMBRE_NORM"], keep=False)
    if dn.any():
        h.agregar(
            d.etiqueta,
            "Nombres repetidos",
            f"{int(dn.sum())} registros comparten el nombre normalizado con otro.",
            int(dn.sum()),
            "Pueden ser la misma entidad o filiales homónimas en países distintos (ver DEDUPLICACION).",
            "Nada.",
            "; ".join(
                pr.loc[pr["NOMBRE_NORM"] != ""].loc[dn, "NOMBRE_ORIGINAL"].drop_duplicates().head(4)
            ),
        )


def _hallazgos_de_destino(h: _Hallazgos, d: Fuente, pr: pd.DataFrame, cr: pd.DataFrame) -> None:
    _ids_de_sucursal(h, d, pr)
    _columnas_sin_acceso(h, d, cr)
    _pais_de_consulta_distinto(h, d, pr)
    _nombres_repetidos(h, d, pr)


def calidad_datos(
    cfg: ConfigVinculacion,
    interno: dict,
    pa: pd.DataFrame,
    ca: pd.DataFrame,
    EXP: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Hallazgos sobre los insumos que cambian la lectura del resultado.

    Args:
        cfg: configuración del cruce.
        interno: estado interno de la corrida (usa ``prep`` y ``crudos``).
        pa: filas de la ancla con sus columnas normalizadas y la empresa asignada (``ENT``).
        ca: la ancla tal como se leyó del archivo.
        EXP: no se usa; se conserva para no cambiar la firma del orquestador original.
    """
    ancla, destinos = cfg.ancla, list(cfg.destinos)
    prep, crudos = interno["prep"], interno["crudos"]
    h = _Hallazgos()
    _metricas_vacias(h, ancla, pa)
    _metricas_repetidas(h, ancla, pa)
    _columnas_constantes(h, ancla, ca)
    _filas_identicas(h, ancla, ca)
    _nombres_truncados(h, ancla, pa)
    _agentes_de_carga(h, ancla, pa)
    _nombres_sin_palabras_utiles(h, ancla, pa)
    _pais_sin_cobertura(h, ancla, destinos, pa, prep)
    _entidades_html(h, ancla, pa)
    for d in destinos:
        _hallazgos_de_destino(h, d, prep[d.clave], crudos[d.clave])
    return pd.DataFrame(h.filas)
