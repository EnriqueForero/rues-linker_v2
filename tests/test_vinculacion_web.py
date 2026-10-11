"""Propagación por sitio web del motor de vinculación (``flujo.vinculacion.web``).

Cada valor esperado salió de correr la función ORIGINAL del motor (la celda ``%%writefile`` del notebook 10) con las
mismas tablas y el mismo vocabulario: la extracción no cambia el comportamiento. Junto a cada caso va la regla que
ejerce. Las empresas son inventadas; el ``Normalizador`` se sustituye por el doble de ``apoyo_comparacion_vinculacion``
y las tablas se arman a mano con las columnas que lee la propagación.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest
from apoyo_comparacion_vinculacion import CORPUS, NormalizadorDoble, comparador, config_prueba
from apoyo_pares_vinculacion import Grafia, Registro, destino_de, graf_de

from record_linkage.flujo.vinculacion.web import propagar_por_web

#: Nombres que la prueba agrega al vocabulario con el que se arma el comparador.
CORPUS_WEB = [
    *CORPUS,
    "VANTIKO CHOCOLATES",
    "VANTIKO SWEETS",
    "VANTIKO HOLDING",
    "VANTIKO USA",
    "VANTIKO",
    "MARCA NUEVA IBERICA",
    "CASTELBRO VINOS",
    "CASTELBRO LICORES",
]

#: Dos grafías de la empresa 0 (Vantiko) y una de la empresa 1 (Castelbro).
GRAF = graf_de(
    [
        Grafia("España", "Vantiko Chocolates S.A.", "VANTIKO CHOCOLATES"),
        Grafia("España", "Vantiko", "VANTIKO"),
        Grafia("España", "Castelbro Vinos", "CASTELBRO VINOS"),
    ]
)
ENT = np.array([0, 0, 1])

WEB = "vantiko.es"
ORBIS = [
    Registro(
        "ESB0000001",
        "VANTIKO CHOCOLATES SA",
        "VANTIKO CHOCOLATES",
        dominio_web=WEB,
        ciudad="VALENCIA",
    ),
    Registro(
        "ESB0000002",
        "CASTELBRO LICORES SL",
        "CASTELBRO LICORES",
        dominio_web="castelbro.com",
        ciudad="BILBAO",
    ),
]
FDI = [
    # 0: comparte VANTIKO y la ciudad coincide                         → EMPRESA B
    Registro("FDI-F0002", "Vantiko Sweets", "VANTIKO SWEETS", dominio_web=WEB, ciudad="VALENCIA"),
    # 1: sobrante genérico (HOLDING) en el registro nuevo               → GRUPO B aunque coincida la ciudad
    Registro("FDI-F0003", "Vantiko Holding", "VANTIKO HOLDING", dominio_web=WEB, ciudad="VALENCIA"),
    # 2: la marca en otro país                                         → GRUPO B
    Registro(
        "FDI-F0004", "Vantiko USA", "VANTIKO USA", paises="USA", dominio_web=WEB, ciudad="MIAMI"
    ),
    # 3: ninguna palabra del nombre en común                           → GRUPO C
    Registro(
        "FDI-F0005", "Marca Nueva Ibérica", "MARCA NUEVA IBERICA", dominio_web=WEB, ciudad="MADRID"
    ),
    # 4: palabras alineadas, ciudad distinta                           → EMPRESA B
    Registro(
        "FDI-F0006", "Vantiko Chocolates", "VANTIKO CHOCOLATES", dominio_web=WEB, ciudad="MADRID"
    ),
    # 5: sin sitio web                                                 → no se propone
    Registro("FDI-F0007", "Vantiko Dulces", "VANTIKO DULCES", dominio_web="", ciudad="VALENCIA"),
    # 6: sin país de evidencia                                         → GRUPO B (no es «mismo país»)
    Registro(
        "FDI-F0008",
        "Vantiko Sin Pais",
        "VANTIKO SIN PAIS",
        paises="",
        dominio_web=WEB,
        ciudad="MADRID",
    ),
    # 7: el nombre es la grafía corta de la empresa                     → EMPRESA B, leída contra «Vantiko»
    Registro("FDI-F0009", "Vantiko", "VANTIKO", dominio_web=WEB, ciudad="MADRID"),
    # 8: palabras alineadas pero en otro país                          → GRUPO B (EMPRESA exige el mismo país)
    Registro(
        "FDI-F0010",
        "Vantiko Chocolates",
        "VANTIKO CHOCOLATES",
        paises="DEU",
        dominio_web=WEB,
        ciudad="BERLIN",
    ),
    # 9: palabras alineadas pero sin país                              → GRUPO B (sin país no hay «mismo país»)
    Registro(
        "FDI-F0011",
        "Vantiko Chocolates",
        "VANTIKO CHOCOLATES",
        paises="",
        dominio_web=WEB,
        ciudad="BERLIN",
    ),
    # 10: mismas palabras pero otro número (marcas distintas)          → GRUPO B (no están «alineadas»)
    Registro("FDI-F0012", "Vantiko 3", "VANTIKO 3", dominio_web=WEB, ciudad="MADRID"),
]
PREP = {"ORBIS": destino_de(ORBIS), "FDI": destino_de(FDI, "FDI")}
DESTINOS = ["ORBIS", "FDI"]

COLUMNAS_NUEVAS = [
    "ENT",
    "FUENTE_DESTINO",
    "pos_b",
    "g",
    "GRAFIA_ANCLA",
    "CLAVE_GRAFIA",
    "SUP_A",
    "SUP_B",
    "TIPO_SUP_A",
    "TIPO_SUP_B",
    "NIVEL_AUTO",
    "BANDA_AUTO",
    "MOTIVO",
    "MISMO_PAIS",
    "COMPARTIDAS",
    "SOBRA_EXPORTADOR",
    "SOBRA_DESTINO",
    "EVIDENCIA_EXTRA",
    "SCORE_RUES_LINKER",
    "SIM_TOKENS_IDF",
    "JARO_WINKLER",
    "DIF_INFORMATIVA",
    "USO_CONTENCION",
]


def _L(filas: list[tuple[Any, ...]], evidencia: bool = True) -> pd.DataFrame:
    """Pares por empresa: ``(ENT, fuente, pos_b, NIVEL_FINAL, NIVEL_AUTO, BANDA_AUTO, MOTIVO[, EVIDENCIA_EXTRA])``."""
    L = pd.DataFrame(
        {
            "ENT": [f[0] for f in filas],
            "FUENTE_DESTINO": [f[1] for f in filas],
            "pos_b": [f[2] for f in filas],
            "NIVEL_FINAL": [f[3] for f in filas],
            "NIVEL_AUTO": [f[4] for f in filas],
            "BANDA_AUTO": [f[5] for f in filas],
            "MOTIVO": [f[6] for f in filas],
        }
    )
    if evidencia:
        L["EVIDENCIA_EXTRA"] = [f[7] if len(f) > 7 else "" for f in filas]
    return L


#: El vínculo del que se propaga: la empresa 0 es EMPRESA/A de ESB0000001 (con sitio web vantiko.es).
BASE = (0, "ORBIS", 0, "EMPRESA", "EMPRESA", "A", "Mismo nombre")


def _propagar(
    L: pd.DataFrame,
    graf: pd.DataFrame = GRAF,
    ent: np.ndarray = ENT,
    prep: dict = PREP,
    destinos=DESTINOS,
):
    cfg = config_prueba()
    return propagar_por_web(
        L, graf, ent, prep, list(destinos), NormalizadorDoble(), comparador(cfg, CORPUS_WEB)
    )


def _nuevas(r: pd.DataFrame) -> pd.DataFrame:
    """Las filas que creó la propagación (las únicas con ``TIPO_SUP_B == "WEB"``); ninguna si no hay esa columna."""
    if "TIPO_SUP_B" not in r.columns:
        return r.iloc[0:0]
    return r[r["TIPO_SUP_B"].fillna("") == "WEB"]


def _lectura(r: pd.DataFrame) -> dict[tuple[str, int], tuple[str, str, str]]:
    """``(fuente, pos_b) → (NIVEL_AUTO, BANDA_AUTO, MISMO_PAIS)`` de las filas nuevas."""
    return {
        (x.FUENTE_DESTINO, x.pos_b): (x.NIVEL_AUTO, x.BANDA_AUTO, x.MISMO_PAIS)
        for x in _nuevas(r).itertuples(index=False)
    }


# ── las cuatro reglas ─────────────────────────────────────────────────────────


def test_empresa_b_si_comparte_palabra_distintiva_mismo_pais_y_misma_ciudad():
    """Mismo dominio + comparte VANTIKO + mismo país + misma ciudad → EMPRESA B, y el motivo lo cuenta."""
    r = _propagar(_L([BASE]))
    fila = _nuevas(r).query("pos_b == 0 and FUENTE_DESTINO == 'FDI'").iloc[0]
    assert (fila["NIVEL_AUTO"], fila["BANDA_AUTO"], fila["MISMO_PAIS"]) == ("EMPRESA", "B", "SÍ")
    assert fila["MOTIVO"] == (
        "Mismo sitio web (vantiko.es) y ciudad que ORBIS «VANTIKO CHOCOLATES SA», "
        "vinculado como EMPRESA; comparte VANTIKO"
    )
    assert (
        fila["EVIDENCIA_EXTRA"]
        == "mismo sitio web (vantiko.es) que ORBIS «VANTIKO CHOCOLATES SA» y misma ciudad"
    )


def test_empresa_b_si_las_palabras_estan_alineadas_y_el_pais_coincide_aunque_la_ciudad_no():
    """Mismo dominio + palabras alineadas + mismo país → EMPRESA B, sin hablar de la ciudad."""
    r = _propagar(_L([BASE]))
    fila = _nuevas(r).query("pos_b == 4 and FUENTE_DESTINO == 'FDI'").iloc[0]
    assert (fila["NIVEL_AUTO"], fila["BANDA_AUTO"]) == ("EMPRESA", "B")
    assert fila["MOTIVO"] == (
        "Mismo sitio web (vantiko.es) que ORBIS «VANTIKO CHOCOLATES SA», vinculado como EMPRESA; "
        "comparte CHOCOLATES VANTIKO"
    )
    assert (
        fila["EVIDENCIA_EXTRA"] == "mismo sitio web (vantiko.es) que ORBIS «VANTIKO CHOCOLATES SA»"
    )


def test_un_sobrante_generico_en_el_registro_nuevo_lo_deja_en_grupo_aunque_coincida_la_ciudad():
    """HOLDING delata una filial: comparte la marca, el país y la ciudad, pero es GRUPO B y no EMPRESA."""
    assert _lectura(_propagar(_L([BASE])))[("FDI", 1)] == ("GRUPO", "B", "SÍ")


def test_grupo_b_si_comparte_palabra_pero_esta_en_otro_pais():
    """La marca con el mismo sitio web en otro país (MISMO_PAIS = NO) es GRUPO B."""
    assert _lectura(_propagar(_L([BASE])))[("FDI", 2)] == ("GRUPO", "B", "NO")


def test_grupo_b_si_falta_el_pais_porque_sin_pais_no_hay_mismo_pais():
    """Sin país de evidencia no se puede afirmar «mismo país»: GRUPO B (y ``MISMO_PAIS`` queda «SIN DATO»)."""
    assert _lectura(_propagar(_L([BASE])))[("FDI", 6)] == ("GRUPO", "B", "SIN DATO")


def test_empresa_exige_el_mismo_pais_y_las_marcas_iguales_aunque_las_palabras_esten_alineadas():
    """Palabras alineadas pero en otro país o sin país (8 y 9), o con otro número (10): solo GRUPO B, no EMPRESA."""
    lectura = _lectura(_propagar(_L([BASE])))
    assert lectura[("FDI", 8)] == ("GRUPO", "B", "NO")
    assert lectura[("FDI", 9)] == ("GRUPO", "B", "SIN DATO")
    assert lectura[("FDI", 10)] == ("GRUPO", "B", "SÍ")


def test_grupo_c_si_no_comparte_ninguna_palabra_del_nombre():
    """Mismo dominio sin palabra compartida: GRUPO C, a revisión, con el motivo «no comparte palabras del nombre»."""
    r = _propagar(_L([BASE]))
    assert _lectura(r)[("FDI", 3)] == ("GRUPO", "C", "SÍ")
    fila = _nuevas(r).query("pos_b == 3").iloc[0]
    assert fila["MOTIVO"].endswith("vinculado como EMPRESA; no comparte palabras del nombre")


def test_las_diez_lecturas_juntas_y_sin_proponer_lo_que_no_tiene_sitio_web():
    """Del dominio vantiko.es salen 10 filas nuevas: el registro sin sitio web (5) y el del propio vínculo no aparecen."""
    r = _propagar(_L([BASE]))
    assert _lectura(r) == {
        ("FDI", 0): ("EMPRESA", "B", "SÍ"),
        ("FDI", 1): ("GRUPO", "B", "SÍ"),
        ("FDI", 2): ("GRUPO", "B", "NO"),
        ("FDI", 3): ("GRUPO", "C", "SÍ"),
        ("FDI", 4): ("EMPRESA", "B", "SÍ"),
        ("FDI", 6): ("GRUPO", "B", "SIN DATO"),
        ("FDI", 7): ("EMPRESA", "B", "SÍ"),
        ("FDI", 8): ("GRUPO", "B", "NO"),
        ("FDI", 9): ("GRUPO", "B", "SIN DATO"),
        ("FDI", 10): ("GRUPO", "B", "SÍ"),
    }


def test_la_ciudad_vacia_nunca_cuenta_como_misma_ciudad():
    """Sin ciudad en el vínculo (o en el registro) ``misma_ciudad`` es falso: sin palabras alineadas queda GRUPO B."""
    orbis = [Registro("ESB0000001", "VANTIKO CHOCOLATES SA", "VANTIKO CHOCOLATES", dominio_web=WEB)]
    fdi = [Registro("FDI-F0002", "Vantiko Sweets", "VANTIKO SWEETS", dominio_web=WEB)]
    prep = {"ORBIS": destino_de(orbis), "FDI": destino_de(fdi, "FDI")}
    assert _lectura(_propagar(_L([BASE]), prep=prep))[("FDI", 0)] == ("GRUPO", "B", "SÍ")


# ── qué registros se proponen ─────────────────────────────────────────────────


def test_no_se_propone_el_registro_del_propio_vinculo_ni_los_de_otro_dominio():
    """Se salta el registro del que se parte (ORBIS 0) y los que tienen otro sitio web (ORBIS 1)."""
    r = _propagar(_L([BASE]))
    claves = set(zip(_nuevas(r)["FUENTE_DESTINO"], _nuevas(r)["pos_b"], strict=True))
    assert ("ORBIS", 0) not in claves
    assert ("ORBIS", 1) not in claves


def test_un_vinculo_sin_sitio_web_no_propaga():
    """Si el registro vinculado no tiene dominio no hay nada que propagar (aunque otros registros tengan uno)."""
    sin_web = Registro(
        "ESB0000001", "VANTIKO CHOCOLATES SA", "VANTIKO CHOCOLATES", ciudad="VALENCIA"
    )
    prep = {"ORBIS": destino_de([sin_web, ORBIS[1]]), "FDI": PREP["FDI"]}
    r = _propagar(_L([BASE]), prep=prep)
    assert _nuevas(r).empty
    assert len(r) == 1


def test_solo_los_vinculos_empresa_propagan():
    """Un vínculo GRUPO (nivel final) con sitio web no propaga; sin ningún EMPRESA se devuelve la MISMA tabla."""
    L = _L([(0, "ORBIS", 0, "GRUPO", "GRUPO", "B", "x")])
    assert _propagar(L) is L


def test_hallazgo_propaga_desde_un_vinculo_empresa_aunque_su_confianza_sea_media():
    """Hallazgo F3 (fijado tal cual está): solo cuenta ``NIVEL_FINAL == "EMPRESA"``; la ``CONFIANZA`` no se mira.

    Un EMPRESA automático B o de revisión asistida sin validar (confianza MEDIA) propaga igual que uno de confianza
    ALTA, mientras que ``fusionar_por_empresa_comun`` solo une con vínculos de confianza ALTA.
    """
    media = _L([BASE]).assign(CONFIANZA="MEDIA")
    alta = media.assign(CONFIANZA="ALTA")
    assert (
        _lectura(_propagar(media)) == _lectura(_propagar(alta)) == _lectura(_propagar(_L([BASE])))
    )
    assert _lectura(_propagar(media))


def test_la_propagacion_cruza_destinos_el_dominio_de_una_fuente_propone_registros_de_otra():
    """El vínculo está en FDI (``FDI-F0002``); los registros de ORBIS con el mismo dominio se proponen."""
    base = (0, "FDI", 0, "EMPRESA", "EMPRESA", "B", "x")
    r = _propagar(_L([base]))
    assert ("ORBIS", 0) in _lectura(r)
    assert ("FDI", 0) not in _lectura(r)


# ── subir sin bajar ───────────────────────────────────────────────────────────


def test_un_par_existente_se_sube_y_el_motivo_recuerda_el_anterior():
    """REVISAR C (FDI 0) pasa a EMPRESA B por el sitio web; el motivo nuevo cierra con «(antes: …)»."""
    L = _L(
        [
            BASE,
            (
                0,
                "FDI",
                0,
                "",
                "REVISAR",
                "C",
                "Comparte VANTIKO, pero difieren en: CHOCOLATES / SWEETS",
            ),
        ]
    )
    r = _propagar(L)
    fila = r[(r["FUENTE_DESTINO"] == "FDI") & (r["pos_b"] == 0)].iloc[0]
    assert (fila["NIVEL_AUTO"], fila["BANDA_AUTO"]) == ("EMPRESA", "B")
    assert fila["MOTIVO"] == (
        "Mismo sitio web (vantiko.es) y ciudad que ORBIS «VANTIKO CHOCOLATES SA», vinculado como EMPRESA; "
        "comparte VANTIKO (antes: Comparte VANTIKO, pero difieren en: CHOCOLATES / SWEETS)"
    )
    assert (
        fila["EVIDENCIA_EXTRA"]
        == "mismo sitio web (vantiko.es) que ORBIS «VANTIKO CHOCOLATES SA» y misma ciudad"
    )
    assert pd.isna(fila["g"]) or fila["g"] != -1  # no es una fila nueva: conserva su origen


def test_un_par_existente_sube_de_banda_dentro_del_mismo_nivel():
    """GRUPO C → GRUPO B: el rango es (nivel, banda) y B es mejor que C."""
    L = _L([BASE, (0, "FDI", 2, "GRUPO", "GRUPO", "C", "viejo")])
    r = _propagar(L)
    fila = r[(r["FUENTE_DESTINO"] == "FDI") & (r["pos_b"] == 2)].iloc[0]
    assert (fila["NIVEL_AUTO"], fila["BANDA_AUTO"]) == ("GRUPO", "B")
    assert fila["MOTIVO"].endswith("comparte VANTIKO (antes: viejo)")


def test_un_par_existente_nunca_baja_pero_recibe_la_nota():
    """EMPRESA A frente a EMPRESA B, GRUPO B frente a GRUPO C y EMPRESA B frente a EMPRESA B: no cambian; solo suman la nota."""
    L = _L(
        [
            BASE,
            (0, "FDI", 4, "EMPRESA", "EMPRESA", "A", "ya es A"),
            (0, "FDI", 3, "", "GRUPO", "B", "ya es grupo B"),
            (0, "FDI", 7, "", "EMPRESA", "B", "ya es empresa B"),
        ]
    )
    r = _propagar(L).set_index(["FUENTE_DESTINO", "pos_b"])
    # A igual rango no se reescribe el motivo: la propuesta tiene que ser ESTRICTAMENTE mejor.
    assert r.loc[("FDI", 7), ["NIVEL_AUTO", "BANDA_AUTO", "MOTIVO"]].tolist() == [
        "EMPRESA",
        "B",
        "ya es empresa B",
    ]
    assert r.loc[("FDI", 4), ["NIVEL_AUTO", "BANDA_AUTO", "MOTIVO"]].tolist() == [
        "EMPRESA",
        "A",
        "ya es A",
    ]
    assert r.loc[("FDI", 3), ["NIVEL_AUTO", "BANDA_AUTO", "MOTIVO"]].tolist() == [
        "GRUPO",
        "B",
        "ya es grupo B",
    ]
    # FDI 4 también es un vínculo EMPRESA, así que propaga a su vez: la nota de ORBIS 0 y la de FDI 4 se suman.
    assert r.loc[("FDI", 3), "EVIDENCIA_EXTRA"] == (
        "mismo sitio web (vantiko.es) que ORBIS «VANTIKO CHOCOLATES SA»; "
        "mismo sitio web (vantiko.es) que FDI «Vantiko Chocolates» y misma ciudad"
    )


def test_la_nota_no_se_repite_si_se_propaga_dos_veces():
    """Propagar sobre el resultado de una propagación no duplica notas ni filas."""
    una = _propagar(_L([BASE]))
    dos = _propagar(una)
    assert len(dos) == len(una)
    assert dos["EVIDENCIA_EXTRA"].tolist() == una["EVIDENCIA_EXTRA"].tolist()


# ── filas nuevas ──────────────────────────────────────────────────────────────


def test_las_filas_nuevas_tienen_g_menos_uno_tipo_web_y_las_columnas_del_contrato():
    """Las filas creadas se agregan al final, con ``g = -1``, ``TIPO_SUP_B = "WEB"`` y los datos de la mejor grafía."""
    r = _propagar(_L([BASE]))
    nuevas = _nuevas(r)
    assert len(r) == 1 + len(nuevas)
    assert list(r.columns) == [
        "ENT", "FUENTE_DESTINO", "pos_b", "NIVEL_FINAL", "NIVEL_AUTO", "BANDA_AUTO", "MOTIVO", "EVIDENCIA_EXTRA",
        *[c for c in COLUMNAS_NUEVAS if c not in {"ENT", "FUENTE_DESTINO", "pos_b", "NIVEL_AUTO", "BANDA_AUTO", "MOTIVO", "EVIDENCIA_EXTRA"}],
    ]  # fmt: skip
    assert set(nuevas["g"]) == {-1}
    assert set(nuevas["TIPO_SUP_A"]) == {"NOMBRE"}
    fila = nuevas.query("pos_b == 7").iloc[0]
    assert (fila["GRAFIA_ANCLA"], fila["CLAVE_GRAFIA"], fila["SUP_A"], fila["SUP_B"]) == (
        "Vantiko",
        "España|Vantiko",
        "VANTIKO",
        "VANTIKO",
    )
    assert fila["COMPARTIDAS"] == "VANTIKO"
    assert (fila["SOBRA_EXPORTADOR"], fila["SOBRA_DESTINO"]) == ("", "")


def test_las_filas_nuevas_siguen_el_orden_de_los_registros_y_van_despues_de_las_existentes():
    """El orden de creación es el de (vínculo base, destino, posición): los registros de FDI 0 a 4 y 6 a 10."""
    r = _propagar(_L([BASE]))
    assert r["pos_b"].tolist() == [0, 0, 1, 2, 3, 4, 6, 7, 8, 9, 10]
    assert r["FUENTE_DESTINO"].tolist() == ["ORBIS"] + ["FDI"] * 10


def test_un_nombre_truncado_en_el_origen_se_alinea_con_su_palabra_completa():
    """La grafía «Vantiko Choc» viene cortada por la aduana: contra «Vantiko Chocolates» está alineada (EMPRESA B)."""
    graf = graf_de([Grafia("España", "Vantiko Choc", "VANTIKO CHOC", truncado=True)])
    fdi = [
        Registro(
            "FDI-F0002",
            "Vantiko Chocolates",
            "VANTIKO CHOCOLATES",
            dominio_web=WEB,
            ciudad="MADRID",
        )
    ]
    prep = {"ORBIS": PREP["ORBIS"], "FDI": destino_de(fdi, "FDI")}
    r = _propagar(_L([BASE]), graf=graf, ent=np.array([0]), prep=prep)
    assert _lectura(r) == {("FDI", 0): ("EMPRESA", "B", "SÍ")}
    sin_truncar = graf_de([Grafia("España", "Vantiko Choc", "VANTIKO CHOC")])
    r = _propagar(_L([BASE]), graf=sin_truncar, ent=np.array([0]), prep=prep)
    assert _lectura(r) == {("FDI", 0): ("GRUPO", "B", "SÍ")}


def test_las_filas_nuevas_traen_el_puntaje_de_la_grafia_elegida_contra_el_registro():
    """Los cinco puntajes de la fila nueva son los de ``Comparador.puntajes(SUP_A, SUP_B)``."""
    fila = _nuevas(_propagar(_L([BASE]))).query("pos_b == 0").iloc[0]
    assert (
        fila["SCORE_RUES_LINKER"],
        fila["SIM_TOKENS_IDF"],
        fila["JARO_WINKLER"],
        fila["DIF_INFORMATIVA"],
    ) == (0.7122, 0.2651, 0.8612, 2.0)
    assert not fila["USO_CONTENCION"]


def test_con_varias_grafias_se_elige_la_lectura_de_mejor_rango():
    """«Vantiko» (FDI 7) contra las dos grafías de la empresa 0: con «VANTIKO» está alineada (EMPRESA B), con la otra no (GRUPO B)."""
    r = _propagar(_L([BASE]))
    fila = _nuevas(r).query("pos_b == 7").iloc[0]
    assert (fila["NIVEL_AUTO"], fila["BANDA_AUTO"], fila["SUP_A"]) == ("EMPRESA", "B", "VANTIKO")
    # Con la grafía de varias palabras la misma lectura habría sido solo GRUPO B (ciudad distinta y sobra CHOCOLATES).
    solo_larga = _propagar(
        _L([BASE]), graf=GRAF.iloc[[0]].reset_index(drop=True), ent=np.array([0])
    )
    assert _lectura(solo_larga)[("FDI", 7)] == ("GRUPO", "B", "SÍ")


def test_a_igual_rango_gana_la_primera_grafia():
    """El empate de rango lo gana la primera grafía de la empresa (comparación estricta)."""
    graf = graf_de(
        [Grafia("España", "Vantiko Uno", "VANTIKO"), Grafia("España", "Vantiko Dos", "VANTIKO")]
    )
    r = _propagar(_L([BASE]), graf=graf, ent=np.array([0, 0]))
    assert set(_nuevas(r)["GRAFIA_ANCLA"]) == {"Vantiko Uno"}


def test_si_dos_vinculos_proponen_el_mismo_registro_nuevo_se_queda_el_primero_en_empate():
    """Dos registros de ORBIS con el mismo dominio y ciudad proponen lo mismo: queda la primera propuesta."""
    orbis = [
        *ORBIS,
        Registro(
            "ESB0000003", "VANTIKO DULCES SA", "VANTIKO DULCES", dominio_web=WEB, ciudad="VALENCIA"
        ),
    ]
    prep = {"ORBIS": destino_de(orbis), "FDI": destino_de(FDI[:5], "FDI")}
    L = _L([BASE, (0, "ORBIS", 2, "EMPRESA", "EMPRESA", "A", "x")])
    fdi0 = _nuevas(_propagar(L, prep=prep)).query("FUENTE_DESTINO == 'FDI' and pos_b == 0")
    assert len(fdi0) == 1
    assert fdi0["EVIDENCIA_EXTRA"].tolist() == [
        "mismo sitio web (vantiko.es) que ORBIS «VANTIKO CHOCOLATES SA» y misma ciudad"
    ]


def test_si_otro_vinculo_propone_el_mismo_registro_con_mejor_rango_lo_reemplaza_sin_cambiar_su_lugar():
    """La segunda propuesta (EMPRESA B, por la ciudad) mejora la primera (GRUPO B); si solo empata, no la reemplaza."""
    orbis = [
        Registro(
            "ESB0000001",
            "VANTIKO CHOCOLATES SA",
            "VANTIKO CHOCOLATES",
            dominio_web=WEB,
            ciudad="BILBAO",
        ),
        Registro(
            "ESB0000002",
            "CASTELBRO LICORES SL",
            "CASTELBRO LICORES",
            dominio_web="castelbro.com",
            ciudad="BILBAO",
        ),
        Registro(
            "ESB0000003", "VANTIKO DULCES SA", "VANTIKO DULCES", dominio_web=WEB, ciudad="VALENCIA"
        ),
    ]
    prep = {"ORBIS": destino_de(orbis), "FDI": destino_de(FDI[:2], "FDI")}
    r = _propagar(_L([BASE, (0, "ORBIS", 2, "EMPRESA", "EMPRESA", "A", "x")]), prep=prep)
    nuevas = _nuevas(r)
    assert nuevas["pos_b"].tolist() == [0, 1]  # FDI 0 conserva el lugar de su primera propuesta
    assert nuevas[["NIVEL_AUTO", "BANDA_AUTO"]].values.tolist() == [
        ["EMPRESA", "B"],
        ["GRUPO", "B"],
    ]
    assert (
        nuevas["EVIDENCIA_EXTRA"].tolist()
        == [
            "mismo sitio web (vantiko.es) que ORBIS «VANTIKO DULCES SA» y misma ciudad",  # mejoró: la propuesta nueva
            "mismo sitio web (vantiko.es) que ORBIS «VANTIKO CHOCOLATES SA»",  # empató: se queda la primera
        ]
    )


# ── la tabla de salida ────────────────────────────────────────────────────────


def test_la_evidencia_se_crea_si_falta_y_los_nulos_pasan_a_vacios():
    """Sin ``EVIDENCIA_EXTRA`` la columna se crea vacía; con nulos, se rellenan con ``""``."""
    sin_columna = _propagar(_L([BASE], evidencia=False))
    assert "EVIDENCIA_EXTRA" in sin_columna.columns
    assert sin_columna.loc[
        (sin_columna["FUENTE_DESTINO"] == "ORBIS"), "EVIDENCIA_EXTRA"
    ].tolist() == [""]
    con_nulo = _L([BASE])
    con_nulo["EVIDENCIA_EXTRA"] = [None]
    assert _propagar(con_nulo).loc[0, "EVIDENCIA_EXTRA"] == ""


def test_la_tabla_de_entrada_no_se_modifica():
    """La propagación trabaja sobre una copia: ``L`` conserva sus filas, sus columnas y su evidencia."""
    L = _L([BASE, (0, "FDI", 0, "", "REVISAR", "C", "antes")])
    copia = L.copy()
    _propagar(L)
    assert L.equals(copia)
    assert list(L.columns) == list(copia.columns)
