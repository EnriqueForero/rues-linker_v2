"""Clasificación de los pares candidatos: NIVEL, BANDA y MOTIVO legible de cada uno.

``clasificar`` recorre los pares que dejó ``generar_candidatos`` y decide, con los rasgos que calcula el
``Comparador``, qué relación proponen entre la empresa ancla y el registro destino:

* ``RELACION_MATRIZ`` (banda C): la matriz que reporta el registro coincide con el exportador;
* ``EMPRESA`` (A o B): todas las palabras se corresponden. En otro país no es la misma empresa sino, a lo
  sumo, el mismo grupo (``GRUPO`` B o C);
* ``GRUPO`` (B, o C en otro país): comparten una marca y lo que sobra es genérico;
* ``REVISAR`` (C): comparten una palabra, es una sigla o se parecen como cadena; lo decide una persona.

**El orden de las ramas ES la prioridad de las reglas** y los umbrales y los textos de ``MOTIVO`` son contrato.
``evidencia_extra`` agrega después lo que dicen las variables adicionales (identificador, web, correo,
teléfono, ciudad).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple, cast

import numpy as np
import pandas as pd

from record_linkage.flujo.vinculacion.constantes import ORDEN_BANDA, ORDEN_NIVEL

if TYPE_CHECKING:
    from record_linkage.flujo.vinculacion.comparar import Comparador
    from record_linkage.flujo.vinculacion.config import ConfigVinculacion
    from record_linkage.flujo.vinculacion.normalizar import Normalizador

__all__ = ["clasificar", "etiqueta_mismo_pais", "evidencia_extra", "posicion_columna"]

#: ``(nivel, banda, motivo)`` de un par; los tres vacíos significan que el par no se propone.
_Veredicto = tuple[str, str, str]

#: SCORE mínimo para proponer a revisión un par con una palabra distintiva EXACTA y fuerte, o con una palabra
#: compartida que no es un nombre de pila.
_SCORE_PALABRA_FUERTE = 0.50
_SCORE_PALABRA_COMPARTIDA = 0.70

#: Letras (sin espacios) que deben sumar las palabras compartidas para que «mismo nombre en otro país» sea
#: banda B y no C.
_LETRAS_MARCA_MINIMAS = 5

#: (columna presente en ancla y destino, cómo se nombra en ``EVIDENCIA_EXTRA``). El orden es el de aplicación.
_VARIABLES_EVIDENCIA = (
    ("ID_COMPARABLE", "identificador"),
    ("DOMINIO_WEB", "web"),
    ("DOMINIO_EMAIL", "correo"),
    ("TEL_DIGITOS", "teléfono"),
    ("CIUDAD_NORM", "ciudad"),
)

#: Variables cuyo valor igual sube un candidato C que comparte palabra a GRUPO/B.
_VARIABLES_QUE_SUBEN_A_GRUPO = ("DOMINIO_WEB", "DOMINIO_EMAIL", "TEL_DIGITOS")


class _ColumnasPar(NamedTuple):
    """Columnas de ``graf`` y ``prep_b`` que lee la clasificación, alineadas fila a fila con los pares."""

    trunc_a: np.ndarray
    inter_a: np.ndarray
    iso_a: np.ndarray
    evid_b: np.ndarray
    grupo_b: np.ndarray


def etiqueta_mismo_pais(mismo: bool | None) -> str:
    """El texto de ``MISMO_PAIS``: ``SÍ``, ``NO`` o ``SIN DATO`` (``None``: falta el país de algún lado)."""
    return "SÍ" if mismo else ("NO" if mismo is False else "SIN DATO")


def posicion_columna(tabla: pd.DataFrame, nombre: str) -> int:
    """Posición de la columna ``nombre`` de ``tabla``: ``DataFrame.iat`` trabaja por posición, no por nombre."""
    return cast("int", tabla.columns.get_loc(nombre))


def _mismo_pais(iso: str, evidencia: str) -> bool | None:
    """¿El país de la ancla está entre los países de evidencia del registro (ISO3 unidos por ``|``)?

    ``None`` si la ancla no tiene país o el registro no tiene ninguno.
    """
    if not iso or not evidencia:
        return None
    return iso in set(evidencia.split("|"))


def _columnas_par(x: pd.DataFrame, graf: pd.DataFrame, prep_b: pd.DataFrame) -> _ColumnasPar:
    """Lleva a cada par las columnas de su grafía (``g``) y de su registro destino (``pos_b``)."""
    return _ColumnasPar(
        graf["TRUNCADO"].to_numpy()[x["g"].to_numpy()],
        graf["INTERMEDIARIO"].to_numpy()[x["g"].to_numpy()],
        graf["PAIS_ISO3"].to_numpy()[x["g"].to_numpy()],
        prep_b["PAISES_EVIDENCIA"].to_numpy()[x["pos_b"].to_numpy()],
        prep_b["GRUPO_ORIGINAL"].to_numpy()[x["pos_b"].to_numpy()],
    )


def _es_empresa(r: dict[str, Any], sc: float, cfg: ConfigVinculacion) -> bool:
    """Todas las palabras se corresponden, las marcas coinciden y el puntaje llega al piso de EMPRESA."""
    comparte = r["N_COMPARTIDAS"] >= 1 or r["COMPACTO_IGUAL"]
    return bool(comparte and r["SIN_SOBRAS"] and r["MARCAS_OK"] and sc >= cfg.umbral_piso_empresa)


def _es_grupo(r: dict[str, Any]) -> bool:
    """Comparten una palabra distintiva (que no es solo un nombre de pila) y lo que sobra es genérico."""
    return bool(
        r["N_COMPARTIDAS"] >= 1
        and r["SOBRAS_GENERICAS"]
        and not r["SIN_SOBRAS"]
        and not r["SOLO_NOMBRE_PILA"]
    )


def _por_relacion_matriz(vincula: bool, matriz: str) -> _Veredicto:
    """Una superficie de MATRIZ que coincide (como empresa o como grupo) es RELACION_MATRIZ; si no, nada."""
    if vincula:
        return (
            "RELACION_MATRIZ",
            "C",
            f"La matriz reportada del registro ({matriz}) coincide con el exportador; el registro es otra empresa del grupo",
        )
    return "", "", ""


def _empresa_otro_pais(sup_a: str, r: dict[str, Any], comp: Comparador) -> _Veredicto:
    """EMPRESA en otro país: es GRUPO. Solo un nombre IDÉNTICO y no común sugiere filial o matriz (B).

    Una errata entre países es con frecuencia otra empresa (la misma marca con una letra distinta en otro
    país), así que ese caso queda en C.
    """
    if (
        r["COMPACTO_IGUAL"]
        and not r["ERRATA"]
        and not comp.palabra_comun(sup_a)
        and r["UTILES_A"] >= 1
        and len(r["COMPARTIDAS"].replace(" ", "")) >= _LETRAS_MARCA_MINIMAS
    ):
        return (
            "GRUPO",
            "B",
            "Mismo nombre pero en otro país: probable filial o matriz del mismo grupo",
        )
    return "GRUPO", "C", "Nombre casi igual pero en otro país: ¿mismo grupo o empresa homónima?"


def _detalles_de_empresa(
    row: Any, r: dict[str, Any], mp: bool | None, inter: bool, comp: Comparador
) -> list[str]:
    """Lo que obliga a revisar rápido un vínculo EMPRESA del mismo país (banda B), en orden de aparición."""
    detalles = []
    if r["ERRATA"]:
        detalles.append("errata de una letra")
    if r["TRUNCADA"]:
        detalles.append("nombre truncado en el origen")
    if row.TIPO_SUP_A == "ALIAS" or row.TIPO_SUP_B == "ALIAS":
        detalles.append("coincide por el nombre entre paréntesis")
    if comp.palabra_comun(row.SUP_A):
        detalles.append("nombre de una sola palabra, frecuente en las bases: posible homónimo")
    if mp is None:
        detalles.append("sin país para contrastar")
    if inter:
        detalles.append("el exportador es un agente de carga")
    return detalles


def _empresa_mismo_pais(
    row: Any, r: dict[str, Any], sc: float, mp: bool | None, inter: bool, comp: Comparador
) -> _Veredicto:
    """EMPRESA del mismo país (o sin país): A si es el mismo nombre sin reparos, B en cualquier otro caso."""
    detalles = _detalles_de_empresa(row, r, mp, inter, comp)
    if not detalles and r["COMPACTO_IGUAL"]:
        return (
            "EMPRESA",
            "A",
            "Mismo nombre tras normalizar (solo cambian forma legal, puntuación o tildes) y mismo país",
        )
    return (
        "EMPRESA",
        "B",
        "Mismas palabras y mismo país; " + "; ".join(detalles or [f"score {sc:.2f}"]),
    )


def _por_empresa(
    row: Any, r: dict[str, Any], sc: float, mp: bool | None, inter: bool, comp: Comparador
) -> _Veredicto:
    """El camino EMPRESA: con país distinto es GRUPO (``_empresa_otro_pais``); si no, EMPRESA A o B."""
    if mp is False:
        return _empresa_otro_pais(row.SUP_A, r, comp)
    return _empresa_mismo_pais(row, r, sc, mp, inter, comp)


def _por_grupo(marca: str, sobras: str, mp: bool | None) -> _Veredicto:
    """El camino GRUPO: comparten la marca y lo que sobra es genérico; en otro país queda en C."""
    if mp is False:
        return (
            "GRUPO",
            "C",
            f"Comparte la marca {marca} pero está en otro país; lo que sobra es genérico ({sobras})",
        )
    return (
        "GRUPO",
        "B",
        f"Comparte la marca {marca}; lo que sobra es genérico ({sobras}): otra sociedad del mismo grupo",
    )


def _por_revision(
    r: dict[str, Any], sc: float, marca: str, sobras: str, cfg: ConfigVinculacion
) -> _Veredicto:
    """El camino REVISAR (siempre banda C): palabra compartida, sigla o nombres muy parecidos; si no, nada."""
    if (r["N_FUERTES"] >= 1 and sc >= _SCORE_PALABRA_FUERTE) or (
        r["N_COMPARTIDAS"] >= 1 and not r["SOLO_NOMBRE_PILA"] and sc >= _SCORE_PALABRA_COMPARTIDA
    ):
        return (
            "REVISAR",
            "C",
            f"Comparte {marca or 'palabra distintiva'}, pero difieren en: {sobras}",
        )
    if r["ABREVIATURA"]:
        return "REVISAR", "C", "Posible sigla o abreviatura del otro nombre"
    if sc >= cfg.umbral_parecido:
        return "REVISAR", "C", f"Nombres muy parecidos como cadena (score {sc:.2f})"
    return "", "", ""


def _clasificar_par(
    row: Any,
    r: dict[str, Any],
    k: int,
    cols: _ColumnasPar,
    comp: Comparador,
    cfg: ConfigVinculacion,
) -> tuple[str, str, str, str]:
    """``(nivel, banda, motivo, mismo_pais)`` del par ``k``, cuyos rasgos son ``r``.

    Es una cadena ``if/elif`` y el ORDEN de las ramas ES la prioridad: decide la primera que se cumple
    (MATRIZ, EMPRESA, GRUPO y, por último, REVISAR). Una superficie de matriz que no coincide no cae
    a REVISAR: queda sin proponer.
    """
    mp = _mismo_pais(cols.iso_a[k], cols.evid_b[k])
    sc = row.SCORE_RUES_LINKER
    empresa = _es_empresa(r, sc, cfg)
    grupo = _es_grupo(r)
    marca = r.get("COMPARTIDAS", "")
    sobras = " / ".join(s for s in (r["SOBRA_A"], r["SOBRA_B"]) if s)
    if row.TIPO_SUP_B == "MATRIZ":
        veredicto = _por_relacion_matriz(empresa or grupo, cols.grupo_b[k])
    elif empresa:
        veredicto = _por_empresa(row, r, sc, mp, cols.inter_a[k], comp)
    elif grupo and not cols.inter_a[k]:
        veredicto = _por_grupo(marca, sobras, mp)
    else:
        veredicto = _por_revision(r, sc, marca, sobras, cfg)
    return (*veredicto, etiqueta_mismo_pais(mp))


def _armar_salida(
    x: pd.DataFrame,
    niveles: list[str],
    bandas: list[str],
    motivos: list[str],
    mismo: list[str],
    rasgos: list[dict[str, Any]],
) -> pd.DataFrame:
    """Agrega las columnas de la clasificación, descarta lo no propuesto y deja un par por (g, pos_b).

    Orden de la salida: ``g``, ``pos_b``, luego el mejor nivel y banda (EMPRESA A primero) y, a igual
    rango, el mayor ``SCORE_RUES_LINKER``. El desempate es ESTABLE (``kind="stable"``).
    """
    out = x.copy()
    out["NIVEL_AUTO"], out["BANDA_AUTO"], out["MOTIVO"], out["MISMO_PAIS"] = (
        niveles,
        bandas,
        motivos,
        mismo,
    )
    rs = pd.DataFrame(rasgos)
    out["COMPARTIDAS"] = rs["COMPARTIDAS"].to_numpy()
    out["SOBRA_EXPORTADOR"], out["SOBRA_DESTINO"] = (
        rs["SOBRA_A"].to_numpy(),
        rs["SOBRA_B"].to_numpy(),
    )
    out = out[out["NIVEL_AUTO"] != ""].copy()
    out["_r"] = out["NIVEL_AUTO"].map(ORDEN_NIVEL) * 10 + out["BANDA_AUTO"].map(ORDEN_BANDA)
    out = out.sort_values(
        ["g", "pos_b", "_r", "SCORE_RUES_LINKER"],
        ascending=[True, True, True, False],
        kind="stable",
    )
    return out.drop_duplicates(["g", "pos_b"]).drop(columns="_r").reset_index(drop=True)


def clasificar(
    x: pd.DataFrame,
    graf: pd.DataFrame,
    prep_b: pd.DataFrame,
    nz: Normalizador,
    comp: Comparador,
    cfg: ConfigVinculacion,
) -> pd.DataFrame:
    """NIVEL (EMPRESA/GRUPO/RELACION_MATRIZ/REVISAR), BANDA (A/B/C) y MOTIVO legible por par.

    ``x`` son los pares de ``generar_candidatos``. Devuelve los mismos pares que se proponen, con las
    columnas ``NIVEL_AUTO``, ``BANDA_AUTO``, ``MOTIVO``, ``MISMO_PAIS``, ``COMPARTIDAS``,
    ``SOBRA_EXPORTADOR`` y ``SOBRA_DESTINO`` y un solo par por (grafía, registro): el de mejor rango.
    ``nz`` no se usa: se conserva en la firma del motor original.
    """
    if x.empty:
        return x
    cols = _columnas_par(x, graf, prep_b)
    niveles, bandas, motivos, rasgos, mismo = [], [], [], [], []
    row: Any  # fila de itertuples(): sus campos son las columnas de ``x``
    for k, row in enumerate(x.itertuples(index=False)):
        r = comp.rasgos(row.SUP_A, row.SUP_B, bool(cols.trunc_a[k]))
        rasgos.append(r)
        nivel, banda, motivo, mismo_pais = _clasificar_par(row, r, k, cols, comp, cfg)
        niveles.append(nivel)
        bandas.append(banda)
        motivos.append(motivo)
        mismo.append(mismo_pais)
    return _armar_salida(x, niveles, bandas, motivos, mismo, rasgos)


def _hay_variable(col: str, graf_prep: pd.DataFrame, prep_b: pd.DataFrame) -> bool:
    """¿La variable existe en la ancla y tiene algún valor en los dos lados?"""
    return bool(
        col in graf_prep.columns and (graf_prep[col] != "").any() and (prep_b[col] != "").any()
    )


def _subir_par(out: pd.DataFrame, k: int, col: str) -> None:
    """Sube el par ``k`` de ``out`` (por posición) según la variable cuyo valor coincide.

    Solo el identificador comparable lleva a EMPRESA/A. Web, correo y teléfono suben un candidato C que
    comparte palabra a GRUPO/B. La ciudad solo se informa.
    """
    if col == "ID_COMPARABLE":
        # Hallazgo F3: la subida a EMPRESA/A no mira las palabras, el país ni el MOTIVO del par. Queda un EMPRESA/A
        # con palabras sin alinear (la invariante «todo vínculo A tiene todas las palabras alineadas» de
        # ``controles`` marca FALLA); con identificadores iguales en países distintos, un EMPRESA/A entre países
        # (otra invariante en FALLA); y un MOTIVO que sigue explicando el nivel anterior (p. ej. «en otro país»).
        out.iat[k, posicion_columna(out, "NIVEL_AUTO")] = "EMPRESA"
        out.iat[k, posicion_columna(out, "BANDA_AUTO")] = "A"
    elif (
        col in _VARIABLES_QUE_SUBEN_A_GRUPO
        and out["BANDA_AUTO"].iat[k] == "C"
        and out["COMPARTIDAS"].iat[k]
    ):
        out.iat[k, posicion_columna(out, "NIVEL_AUTO")] = "GRUPO"
        out.iat[k, posicion_columna(out, "BANDA_AUTO")] = "B"


def evidencia_extra(
    pares: pd.DataFrame, graf_prep: pd.DataFrame, prep_b: pd.DataFrame
) -> pd.DataFrame:
    """Variables adicionales presentes en AMBOS lados: ID comparable, web, email, teléfono, ciudad.

    Un ID comparable igual decide EMPRESA (A). Web/email/teléfono igual suben un candidato
    que comparte palabra a B. Distintos no vetan (una empresa tiene varias webs); se informan.

    Agrega la columna ``EVIDENCIA_EXTRA`` (``mismo identificador; mismo web``…) y modifica por POSICIÓN
    ``NIVEL_AUTO`` y ``BANDA_AUTO`` de los pares que suben. Las variables se aplican en el orden de
    ``_VARIABLES_EVIDENCIA``; una variable que falta en la ancla, o vacía en algún lado, se omite.
    """
    # Hallazgo F3: la docstring original dice que los valores distintos «se informan», pero solo se
    # informan los iguales; y las notas salen con el género equivocado («mismo web», «mismo ciudad»).
    if pares.empty:
        return pares
    out = pares.copy()
    notas: list[list[str]] = [[] for _ in range(len(out))]
    for col, nombre in _VARIABLES_EVIDENCIA:
        if not _hay_variable(col, graf_prep, prep_b):
            continue
        va = graf_prep[col].to_numpy()[out["g"].to_numpy()]
        vb = prep_b[col].to_numpy()[out["pos_b"].to_numpy()]
        igual = (va != "") & (va == vb)
        for k in np.flatnonzero(igual):
            notas[k].append(f"mismo {nombre}")
            _subir_par(out, k, col)
    out["EVIDENCIA_EXTRA"] = ["; ".join(n) for n in notas]
    return out
