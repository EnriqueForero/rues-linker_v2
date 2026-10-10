"""Invariantes de cada corrida (hoja CONTROL_QA): conservación de filas y métricas, coherencia de vínculos.

Un control CRÍTICO que falla deja ``Resultado.todo_ok`` en ``False``; uno no crítico queda como ADVERTENCIA.
El orden de las filas de la hoja es el orden de los controles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

import record_linkage
from record_linkage.flujo.vinculacion.config import ConfigVinculacion

__all__ = ["comparar_fuentes_decision", "invariantes"]

_NIVELES_VALIDOS = {"", "EMPRESA", "GRUPO", "RELACION_PROPIEDAD"}
_BANDAS_VALIDAS = {"", "A", "B", "C"}
_VERSION_MINIMA = (0, 22, 4)


@dataclass
class _Controles:
    """Acumula las filas de CONTROL_QA en el orden en que se evalúan."""

    filas: list[dict] = field(default_factory=list)

    def agregar(
        self, control: str, cumple: object, valor: object, detalle: str, critico: bool = True
    ) -> None:
        estado = "OK" if cumple else ("FALLA" if critico else "ADVERTENCIA")
        self.filas.append(
            {
                "CONTROL": control,
                "CUMPLE": bool(cumple),
                "VALOR": valor,
                "DETALLE": detalle,
                "ESTADO": estado,
            }
        )


def _controles_de_la_ancla(
    c: _Controles,
    cfg: ConfigVinculacion,
    prep: dict,
    pa: pd.DataFrame,
    exportadores: pd.DataFrame,
    g: pd.DataFrame,
) -> None:
    ancla = cfg.ancla
    n = len(prep[ancla.clave])
    asignadas = pa["ENT"].notna()
    c.agregar(
        "Una fila de DETALLE por fila de entrada de la ancla",
        asignadas.all() and len(pa) == n,
        len(pa),
        f"entrada {n}",
    )
    c.agregar(
        "Cada fila de la ancla tiene empresa asignada", asignadas.all(), int(asignadas.sum()), ""
    )
    for m in ancla.cols_metricas:
        nombre = ancla.nombres_contexto.get(m, m).upper().replace(" ", "_")
        suma_e, suma_f = float(exportadores[nombre].sum()), float(pa[m].sum())
        c.agregar(
            f"'{m}' se conserva al consolidar por empresa",
            abs(suma_e - suma_f) < 1e-6 * max(1, abs(suma_f)),
            f"{suma_e:,.0f}",
            f"filas {suma_f:,.0f}",
        )
    paises_por_ent = g.groupby("ENT")["PAIS_ISO3"].nunique()
    c.agregar(
        "Cada empresa de la ancla tiene un solo país",
        (paises_por_ent <= 1).all(),
        int((paises_por_ent > 1).sum()),
        "empresas con >1 país",
    )


def _controles_de_enlaces(
    c: _Controles, destinos: list, prep: dict, vinc: pd.DataFrame, enlaces: pd.DataFrame
) -> None:
    ok_ids = (
        all(
            set(vinc.loc[vinc["FUENTE_DESTINO"] == d.clave, "ID_DESTINO"])
            <= set(prep[d.clave]["ID_REGISTRO"])
            for d in destinos
        )
        if len(vinc)
        else True
    )
    c.agregar("Todo ID vinculado existe en su base destino", ok_ids, "", "")
    dup = enlaces.duplicated(["ID_EXPORTADOR", "FUENTE", "ID_DESTINO"]).sum() if len(enlaces) else 0
    c.agregar("Sin pares repetidos en ENLACES", dup == 0, int(dup), "")


def _controles_de_pares(c: _Controles, pares: pd.DataFrame) -> None:
    if not len(pares):
        return
    emp_auto = pares[
        (pares["NIVEL_AUTO"] == "EMPRESA")
        & (pares["BANDA_AUTO"].isin(["A", "B"]))
        & (pares["DECISION"] == "")
    ]
    malos = emp_auto[(emp_auto["MISMO_PAIS"] == "NO")]
    c.agregar("Ningún vínculo EMPRESA automático cruza países", len(malos) == 0, len(malos), "")
    emp_a = pares[(pares["BANDA_AUTO"] == "A") & (pares["TIPO_SUP_B"] != "WEB")]
    sin_alinear = emp_a[(emp_a["SOBRA_EXPORTADOR"] != "") | (emp_a["SOBRA_DESTINO"] != "")]
    c.agregar(
        "Todo vínculo automático A tiene todas las palabras alineadas",
        len(sin_alinear) == 0,
        len(sin_alinear),
        "",
    )
    rech = pares[
        (pares["DECISION"] == "NO_VINCULAR") & pares["NIVEL_FINAL"].isin(["EMPRESA", "GRUPO"])
    ]
    c.agregar("Ningún par descartado por revisión aparece vinculado", len(rech) == 0, len(rech), "")
    niveles_ok = set(pares["NIVEL_FINAL"].fillna("")) <= _NIVELES_VALIDOS
    bandas_ok = set(pares["BANDA_AUTO"].fillna("")) <= _BANDAS_VALIDAS
    c.agregar("Niveles y bandas válidos", niveles_ok and bandas_ok, "", "")


def _controles_de_principales_y_vetos(
    c: _Controles, vinc: pd.DataFrame, g: pd.DataFrame, ent: object
) -> None:
    if len(vinc):
        por_destino = vinc.groupby(["ENT", "FUENTE_DESTINO"])["ES_PRINCIPAL"].sum()
        c.agregar(
            "Exactamente un vínculo principal por empresa y destino",
            (por_destino == 1).all(),
            int((por_destino != 1).sum()),
            "",
        )
    vetos = g.attrs.get("vetos", set())
    viol = [(a, b) for a, b in vetos if ent[a] == ent[b]]  # type: ignore[index]
    c.agregar(
        "Los pares separados por revisión (DISTINTA) no quedan en la misma empresa",
        not viol,
        len(viol),
        "; ".join(
            f"{g['NOMBRE_ORIGINAL'].iat[a]} ~ {g['NOMBRE_ORIGINAL'].iat[b]}" for a, b in viol[:5]
        ),
    )


def _controles_de_version_y_detalles(
    c: _Controles, destinos: list, prep: dict, detalles: dict, interno: dict
) -> None:
    version = tuple(int(x) for x in re.findall(r"\d+", record_linkage.__version__)[:3])
    c.agregar("rues-linker ≥ 0.22.4", version >= _VERSION_MINIMA, record_linkage.__version__, "")
    for d in destinos:
        c.agregar(
            f"DETALLE_{d.clave} conserva todas las filas",
            len(detalles[d.clave]) == len(prep[d.clave]),
            len(detalles[d.clave]),
            f"entrada {len(prep[d.clave])}",
        )
    if len(interno["ref"]):
        disc = interno["disc"]
        sin_candidato = (
            int(disc["TIPO"].str.contains("ni siquiera candidato").sum()) if len(disc) else 0
        )
        c.agregar(
            "Recall del bloqueo: todo vínculo de la referencia es al menos candidato",
            sin_candidato == 0,
            sin_candidato,
            "los que faltan solo se encuentran con conocimiento externo (ver VALIDACION_DETALLE)",
            critico=False,
        )


def invariantes(
    cfg: ConfigVinculacion,
    interno: dict,
    pa: pd.DataFrame,
    exportadores: pd.DataFrame,
    pares: pd.DataFrame,
    vinc: pd.DataFrame,
    enlaces: pd.DataFrame,
    g: pd.DataFrame,
    detalles: dict,
) -> pd.DataFrame:
    """Evalúa los controles de la corrida y devuelve la hoja CONTROL_QA.

    Args:
        cfg: configuración del cruce.
        interno: estado interno de la corrida (``prep``, ``ent``, ``ref``, ``disc``).
        pa: filas de la ancla con la empresa asignada (``ENT``) y sus métricas.
        exportadores: tabla EXPORTADORES (una fila por empresa).
        pares: todos los pares ancla × destino con su nivel final.
        vinc: pares que quedaron vinculados (EMPRESA o GRUPO).
        enlaces: tabla ENLACES.
        g: grafías de la ancla; ``g.attrs["vetos"]`` trae los pares separados por una decisión.
        detalles: tablas DETALLE_<destino>.
    """
    destinos = list(cfg.destinos)
    prep = interno["prep"]
    c = _Controles()
    _controles_de_la_ancla(c, cfg, prep, pa, exportadores, g)
    _controles_de_enlaces(c, destinos, prep, vinc, enlaces)
    _controles_de_pares(c, pares)
    _controles_de_principales_y_vetos(c, vinc, g, interno["ent"])
    _controles_de_version_y_detalles(c, destinos, prep, detalles, interno)
    return pd.DataFrame(c.filas)


_COLUMNAS_COMPARACION = [
    "TIPO",
    "FUENTE",
    "CLAVE_A",
    "CLAVE_B",
    "NOMBRE_B",
    "DECISIONES",
    "COINCIDEN",
]


def comparar_fuentes_decision(decisiones: pd.DataFrame) -> pd.DataFrame:
    """Pares decididos en más de un archivo: ¿coinciden las revisiones?

    Devuelve una fila por par (``TIPO``, ``FUENTE``, par de claves) que aparece en al menos dos archivos de
    decisiones, con lo que decidió cada archivo y si las decisiones coinciden. Sin decisiones, o con un solo
    archivo, devuelve la tabla vacía con sus siete columnas.
    """
    if decisiones.empty or decisiones["ARCHIVO"].nunique() < 2:
        return pd.DataFrame(columns=_COLUMNAS_COMPARACION)
    d = decisiones.copy()
    d["PAR"] = [
        " ↔ ".join(sorted((a, b))) if t == "DUPLICADO" else f"{a} → {b}"
        for t, a, b in zip(d.TIPO, d.CLAVE_A, d.CLAVE_B, strict=True)
    ]
    filas = []
    for (t, f, _par), grp in d.groupby(["TIPO", "FUENTE", "PAR"], sort=True):
        if grp["ARCHIVO"].nunique() < 2:
            continue
        decs = [f"{a}: {x}" for a, x in zip(grp["ARCHIVO"], grp["DECISION"], strict=True)]
        filas.append(
            {
                "TIPO": t,
                "FUENTE": f,
                "CLAVE_A": grp["CLAVE_A"].iat[0],
                "CLAVE_B": grp["CLAVE_B"].iat[0],
                "NOMBRE_B": grp["NOMBRE_B"].iat[-1],
                "DECISIONES": " | ".join(decs),
                "COINCIDEN": "SÍ" if grp["DECISION"].nunique() == 1 else "NO",
                "RAZON_ULTIMA": grp["RAZON"].iat[-1],
            }
        )
    return pd.DataFrame(filas)
