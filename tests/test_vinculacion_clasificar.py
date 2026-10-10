"""Clasificación de los pares candidatos del motor de vinculación (``flujo.vinculacion.clasificar``).

Cada valor esperado salió de correr la función ORIGINAL del motor (la celda ``%%writefile`` del notebook 10) con los
mismos pares y los mismos rasgos: la extracción no cambia el comportamiento. Junto a cada caso va la regla que ejerce.
Las empresas son inventadas. Los rasgos de cada par los fija a mano un COMPARADOR DE MENTIRA
(``apoyo_pares_vinculacion.ComparadorFalso``), así que cada rama y cada umbral se ejerce en su frontera sin depender
del puntaje real; el original acepta el mismo comparador (duck typing).
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest
from apoyo_comparacion_vinculacion import NormalizadorDoble, config_prueba
from apoyo_pares_vinculacion import (
    ComparadorFalso,
    Grafia,
    Par,
    Registro,
    destino_de,
    graf_de,
    pares_de,
    rasgos,
)

from record_linkage.flujo.vinculacion.clasificar import clasificar, evidencia_extra

CAMPOS = [
    "NIVEL_AUTO",
    "BANDA_AUTO",
    "MOTIVO",
    "MISMO_PAIS",
    "COMPARTIDAS",
    "SOBRA_EXPORTADOR",
    "SOBRA_DESTINO",
]


def _uno(
    r: dict[str, Any],
    *,
    score: float = 0.90,
    tipo_a: str = "NOMBRE",
    tipo_b: str = "NOMBRE",
    trunc: bool = False,
    inter: bool = False,
    iso: str = "ESP",
    evid: str = "ESP",
    matriz: str = "",
    comun: bool = False,
    **cfg: Any,
) -> dict[str, Any] | None:
    """Clasifica UN par con los rasgos ``r``; devuelve sus campos, o ``None`` si el par no se propone."""
    comp = ComparadorFalso({("A", "B"): r}, comunes={"A"} if comun else set())
    graf = graf_de([Grafia("España", "A", "A", iso=iso, truncado=trunc, intermediario=inter)])
    prep_b = destino_de([Registro("ESB0000001", "B", "B", paises=evid, grupo=matriz)])
    x = pares_de([Par(0, 0, "A", "B", tipo_a, tipo_b, score)])
    y = clasificar(x, graf, prep_b, NormalizadorDoble(), comp, config_prueba(**cfg))
    if y.empty:
        return None
    assert len(y) == 1
    return {c: y[c].iat[0] for c in CAMPOS}


def _veredicto(res: dict[str, Any] | None) -> tuple[str, str, str] | None:
    """``(nivel, banda, motivo)`` del resultado, o ``None`` si el par no se propone."""
    return None if res is None else (res["NIVEL_AUTO"], res["BANDA_AUTO"], res["MOTIVO"])


# Rasgos de «mismas palabras, sin sobras» (la base de EMPRESA) y de «misma marca, sobra genérico» (la de GRUPO).
EMPRESA = {"N_COMPARTIDAS": 1, "COMPARTIDAS": "ZELVORA", "SIN_SOBRAS": True, "COMPACTO_IGUAL": True}
GRUPO = {
    "N_COMPARTIDAS": 1,
    "COMPARTIDAS": "ZELVORA",
    "SOBRAS_GENERICAS": True,
    "SOBRA_B": "HOLDING",
}

MOTIVO_A = (
    "Mismo nombre tras normalizar (solo cambian forma legal, puntuación o tildes) y mismo país"
)
MOTIVO_MATRIZ = (
    "La matriz reportada del registro (Solenta Alimentos) coincide con el exportador; "
    "el registro es otra empresa del grupo"
)
MOTIVO_OTRO_PAIS_B = "Mismo nombre pero en otro país: probable filial o matriz del mismo grupo"
MOTIVO_OTRO_PAIS_C = "Nombre casi igual pero en otro país: ¿mismo grupo o empresa homónima?"


# ── RELACION_MATRIZ ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rasgos_",
    [
        pytest.param(rasgos(**EMPRESA), id="como_empresa"),
        pytest.param(rasgos(**GRUPO), id="como_grupo"),
    ],
)
def test_una_superficie_de_matriz_que_coincide_es_relacion_matriz_banda_c(rasgos_):
    """La matriz reportada coincide con el exportador (como EMPRESA o como GRUPO): el registro es otra empresa del grupo."""
    res = _uno(rasgos_, tipo_b="MATRIZ", matriz="Solenta Alimentos")
    assert _veredicto(res) == ("RELACION_MATRIZ", "C", MOTIVO_MATRIZ)


def test_una_matriz_que_no_coincide_no_cae_a_revisar_y_el_par_se_descarta():
    """Aunque habría sido REVISAR con otra superficie (palabra fuerte, score alto), la matriz sin coincidencia no se propone."""
    r = rasgos(
        N_COMPARTIDAS=1, N_FUERTES=1, COMPARTIDAS="ZELVORA", SOBRA_A="HIJOS", SOBRA_B="NORTE"
    )
    assert _uno(r, tipo_b="NOMBRE") is not None  # con superficie de nombre, sí es REVISAR
    assert _uno(r, tipo_b="MATRIZ", matriz="Solenta Alimentos") is None


# ── EMPRESA en el mismo país ──────────────────────────────────────────────────


def test_empresa_banda_a_mismo_nombre_mismo_pais_sin_reparos():
    """Rasgos de nombre idéntico, país coincidente y nada que mencionar: EMPRESA banda A."""
    res = _uno(rasgos(**EMPRESA))
    assert _veredicto(res) == ("EMPRESA", "A", MOTIVO_A)
    assert (
        res["MISMO_PAIS"],
        res["COMPARTIDAS"],
        res["SOBRA_EXPORTADOR"],
        res["SOBRA_DESTINO"],
    ) == ("SÍ", "ZELVORA", "", "")


def test_empresa_sin_nombre_identico_es_banda_b_y_cita_el_score():
    """Mismas palabras pero no el mismo texto compacto: EMPRESA banda B con «score 0.90» como único detalle."""
    res = _uno(rasgos(N_COMPARTIDAS=2, COMPARTIDAS="ALIMENTOS ZELVORA", SIN_SOBRAS=True))
    assert _veredicto(res) == ("EMPRESA", "B", "Mismas palabras y mismo país; score 0.90")


def test_empresa_comparte_tambien_si_solo_el_texto_compacto_coincide():
    """``comparte`` vale con ``COMPACTO_IGUAL`` aunque no haya palabras compartidas (KR FOODS ≡ KRFOODS)."""
    res = _uno(rasgos(SIN_SOBRAS=True, COMPACTO_IGUAL=True, COMPARTIDAS=""))
    assert _veredicto(res) == ("EMPRESA", "A", MOTIVO_A)


@pytest.mark.parametrize(
    ("cambios", "kwargs", "detalle"),
    [
        pytest.param({"ERRATA": True}, {}, "errata de una letra", id="errata"),
        pytest.param({"TRUNCADA": True}, {}, "nombre truncado en el origen", id="truncada"),
        pytest.param(
            {},
            {"tipo_a": "ALIAS"},
            "coincide por el nombre entre paréntesis",
            id="alias_de_la_ancla",
        ),
        pytest.param(
            {},
            {"tipo_b": "ALIAS"},
            "coincide por el nombre entre paréntesis",
            id="alias_del_destino",
        ),
        pytest.param(
            {},
            {"comun": True},
            "nombre de una sola palabra, frecuente en las bases: posible homónimo",
            id="nombre_comun",
        ),
        pytest.param({}, {"iso": ""}, "sin país para contrastar", id="ancla_sin_pais"),
        pytest.param({}, {"evid": ""}, "sin país para contrastar", id="destino_sin_pais"),
        pytest.param(
            {}, {"inter": True}, "el exportador es un agente de carga", id="intermediario"
        ),
    ],
)
def test_empresa_banda_b_por_cada_detalle_que_obliga_a_revisar(cambios, kwargs, detalle):
    """Un solo reparo (aunque el nombre sea idéntico) baja EMPRESA de A a B y lo nombra en el motivo."""
    res = _uno(rasgos(**EMPRESA, **cambios), **kwargs)
    assert _veredicto(res) == ("EMPRESA", "B", f"Mismas palabras y mismo país; {detalle}")


def test_empresa_banda_b_junta_los_detalles_en_el_orden_fijo():
    """Con todos los reparos a la vez salen en el orden: errata, truncada, alias, nombre común, sin país, agente de carga."""
    res = _uno(
        rasgos(**EMPRESA, ERRATA=True, TRUNCADA=True),
        tipo_a="ALIAS",
        comun=True,
        iso="",
        inter=True,
    )
    assert _veredicto(res) == (
        "EMPRESA",
        "B",
        "Mismas palabras y mismo país; errata de una letra; nombre truncado en el origen; "
        "coincide por el nombre entre paréntesis; nombre de una sola palabra, frecuente en las bases: "
        "posible homónimo; sin país para contrastar; el exportador es un agente de carga",
    )


def test_empresa_el_piso_del_score_es_umbral_piso_empresa():
    """Justo en ``umbral_piso_empresa`` el par es EMPRESA; un poco debajo ya no lo es (y, sin otra regla, se descarta)."""
    en_el_piso = rasgos(N_COMPARTIDAS=1, COMPARTIDAS="ZELVORA", SIN_SOBRAS=True)
    assert _veredicto(_uno(en_el_piso, score=0.70)) == (
        "EMPRESA",
        "B",
        "Mismas palabras y mismo país; score 0.70",
    )
    assert _uno(en_el_piso, score=0.6999) is None
    # El piso es configurable.
    assert _veredicto(_uno(en_el_piso, score=0.60, umbral_piso_empresa=0.60))[:2] == (
        "EMPRESA",
        "B",
    )
    assert _uno(en_el_piso, score=0.60) is None


def test_empresa_bajo_el_piso_con_palabra_fuerte_pasa_a_revisar():
    """Por debajo del piso no es EMPRESA, pero con una palabra fuerte y score ≥ 0,50 se propone a revisión."""
    r = rasgos(N_COMPARTIDAS=1, N_FUERTES=1, COMPARTIDAS="ZELVORA", SIN_SOBRAS=True)
    assert _veredicto(_uno(r, score=0.69)) == (
        "REVISAR",
        "C",
        "Comparte ZELVORA, pero difieren en: ",
    )


@pytest.mark.parametrize(
    "cambios",
    [
        pytest.param({"MARCAS_OK": False}, id="marcas_distintas"),
        pytest.param({"SIN_SOBRAS": False, "SOBRA_A": "HIJOS"}, id="con_sobras"),
        pytest.param({"N_COMPARTIDAS": 0, "COMPACTO_IGUAL": False}, id="sin_nada_en_comun"),
    ],
)
def test_empresa_exige_marcas_iguales_sin_sobras_y_algo_en_comun(cambios):
    """Cualquiera de las tres condiciones que falla impide EMPRESA (con score 0,60 no queda otra regla que aplique)."""
    assert _uno(rasgos(**{**EMPRESA, **cambios}), score=0.60, umbral_piso_empresa=0.50) is None


def test_empresa_gana_a_grupo_aunque_el_exportador_sea_agente_de_carga():
    """EMPRESA se evalúa antes que GRUPO; el agente de carga solo baja la banda, no cambia el camino."""
    res = _uno(rasgos(**EMPRESA, SOBRAS_GENERICAS=True), inter=True)
    assert _veredicto(res) == (
        "EMPRESA",
        "B",
        "Mismas palabras y mismo país; el exportador es un agente de carga",
    )


# ── EMPRESA en otro país ──────────────────────────────────────────────────────


def test_empresa_en_otro_pais_con_nombre_identico_y_marca_larga_es_grupo_banda_b():
    """Mismo nombre, sin errata, no común y con ≥ 5 letras de marca: probable filial o matriz (GRUPO B)."""
    res = _uno(rasgos(**EMPRESA), evid="DEU")
    assert _veredicto(res) == ("GRUPO", "B", MOTIVO_OTRO_PAIS_B)
    assert res["MISMO_PAIS"] == "NO"


@pytest.mark.parametrize(
    ("cambios", "kwargs"),
    [
        pytest.param({"COMPACTO_IGUAL": False}, {}, id="no_es_el_mismo_texto"),
        pytest.param({"ERRATA": True}, {}, id="con_errata"),
        pytest.param({}, {"comun": True}, id="nombre_comun"),
        pytest.param({"UTILES_A": 0}, {}, id="sin_palabras_utiles_en_la_ancla"),
        pytest.param({"COMPARTIDAS": "ABCD"}, {}, id="marca_de_cuatro_letras"),
    ],
)
def test_empresa_en_otro_pais_cualquier_reparo_la_deja_en_grupo_banda_c(cambios, kwargs):
    """Cada una de las cinco condiciones de la banda B, al fallar, deja el par en GRUPO C (¿mismo grupo u homónima?)."""
    res = _uno(rasgos(**{**EMPRESA, **cambios}), evid="DEU", **kwargs)
    assert _veredicto(res) == ("GRUPO", "C", MOTIVO_OTRO_PAIS_C)


def test_empresa_en_otro_pais_la_marca_se_mide_sin_espacios_y_cinco_letras_bastan():
    """La frontera de la marca: «AB CDE» suma 5 letras sin espacios (B); «AB CD» suma 4 (C)."""
    assert _veredicto(_uno(rasgos(**{**EMPRESA, "COMPARTIDAS": "AB CDE"}), evid="DEU"))[:2] == (
        "GRUPO",
        "B",
    )
    assert _veredicto(_uno(rasgos(**{**EMPRESA, "COMPARTIDAS": "AB CD"}), evid="DEU"))[:2] == (
        "GRUPO",
        "C",
    )


def test_empresa_en_otro_pais_no_pregunta_por_palabra_comun_si_hay_errata():
    """El orden de las condiciones corta la evaluación: con errata no se consulta ``palabra_comun``."""
    comp = ComparadorFalso({("A", "B"): rasgos(**EMPRESA, ERRATA=True)}, comunes={"A"})
    graf = graf_de([Grafia("España", "A", "A")])
    prep_b = destino_de([Registro("ESB0000001", "B", "B", paises="DEU")])
    clasificar(pares_de([Par(0, 0)]), graf, prep_b, NormalizadorDoble(), comp, config_prueba())
    assert comp.llamadas == [("A", "B", False)]


# ── GRUPO ─────────────────────────────────────────────────────────────────────


def test_grupo_banda_b_misma_marca_y_lo_que_sobra_es_generico():
    """Misma marca, sobrante genérico (HOLDING) y mismo país: otra sociedad del mismo grupo."""
    res = _uno(rasgos(**GRUPO))
    assert _veredicto(res) == (
        "GRUPO",
        "B",
        "Comparte la marca ZELVORA; lo que sobra es genérico (HOLDING): otra sociedad del mismo grupo",
    )
    assert (res["SOBRA_EXPORTADOR"], res["SOBRA_DESTINO"]) == ("", "HOLDING")


def test_grupo_los_sobrantes_de_los_dos_lados_se_unen_con_una_barra():
    """El texto de lo que sobra junta el sobrante de la ancla y el del destino con « / »."""
    res = _uno(rasgos(**GRUPO, SOBRA_A="IBERIA"))
    assert _veredicto(res)[2].endswith("(IBERIA / HOLDING): otra sociedad del mismo grupo")


def test_grupo_en_otro_pais_queda_en_banda_c():
    """La misma marca en otro país no se vincula sola: GRUPO C."""
    res = _uno(rasgos(**GRUPO), evid="DEU")
    assert _veredicto(res) == (
        "GRUPO",
        "C",
        "Comparte la marca ZELVORA pero está en otro país; lo que sobra es genérico (HOLDING)",
    )
    assert res["MISMO_PAIS"] == "NO"


def test_grupo_sin_pais_para_contrastar_se_trata_como_el_mismo_pais():
    """Sin país en algún lado ``MISMO_PAIS`` es «SIN DATO» y el par no se baja a C (solo ``False`` lo baja)."""
    res = _uno(rasgos(**GRUPO), iso="")
    assert _veredicto(res)[:2] == ("GRUPO", "B")
    assert res["MISMO_PAIS"] == "SIN DATO"


def test_grupo_con_exportador_agente_de_carga_cae_a_revisar():
    """Un agente de carga no entra a GRUPO; con palabra compartida y score ≥ 0,70 queda como REVISAR."""
    res = _uno(rasgos(**GRUPO), inter=True)
    assert _veredicto(res) == ("REVISAR", "C", "Comparte ZELVORA, pero difieren en: HOLDING")


@pytest.mark.parametrize(
    "cambios",
    [
        pytest.param({"SOLO_NOMBRE_PILA": True}, id="solo_nombre_de_pila"),
        pytest.param({"SIN_SOBRAS": True}, id="sin_sobras"),
        pytest.param({"SOBRAS_GENERICAS": False}, id="sobras_no_genericas"),
        pytest.param({"N_COMPARTIDAS": 0}, id="sin_palabra_compartida"),
    ],
)
def test_grupo_exige_marca_distintiva_y_sobras_genericas(cambios):
    """No basta compartir un nombre de pila, ni que no sobre nada (eso es EMPRESA), ni que lo sobrante informe algo."""
    res = _uno(rasgos(**{**GRUPO, **cambios}), score=0.40)
    assert res is None or res["NIVEL_AUTO"] != "GRUPO"


# ── REVISAR ───────────────────────────────────────────────────────────────────


def test_revisar_palabra_fuerte_exige_score_de_al_menos_050():
    """Una palabra exacta y distintiva (N_FUERTES ≥ 1) con score justo en 0,50 se propone; con 0,4999, no."""
    r = rasgos(
        N_COMPARTIDAS=1, N_FUERTES=1, COMPARTIDAS="KORIMBA", SOBRA_A="QUESOS", SOBRA_B="ACEITES"
    )
    assert _veredicto(_uno(r, score=0.50)) == (
        "REVISAR",
        "C",
        "Comparte KORIMBA, pero difieren en: QUESOS / ACEITES",
    )
    assert _uno(r, score=0.4999) is None


def test_revisar_palabra_compartida_exige_score_de_al_menos_070_y_no_ser_solo_nombre_de_pila():
    """Sin palabra fuerte, compartir una palabra pide score ≥ 0,70; con 0,6999 o solo un nombre de pila, no."""
    r = rasgos(N_COMPARTIDAS=1, COMPARTIDAS="KORIMBA", SOBRA_A="QUESOS", SOBRA_B="ACEITES")
    assert _veredicto(_uno(r, score=0.70))[:2] == ("REVISAR", "C")
    assert _uno(r, score=0.6999) is None
    assert _uno({**r, "SOLO_NOMBRE_PILA": True}, score=0.80) is None


def test_revisar_sin_marca_nombra_una_palabra_distintiva():
    """Si ``COMPARTIDAS`` viene vacía (palabra compartida por truncamiento) el motivo dice «palabra distintiva»."""
    r = rasgos(N_COMPARTIDAS=1, N_FUERTES=1, COMPARTIDAS="", SOBRA_A="QUESOS")
    assert _veredicto(_uno(r)) == (
        "REVISAR",
        "C",
        "Comparte palabra distintiva, pero difieren en: QUESOS",
    )


def test_revisar_sigla_o_abreviatura_se_propone_sin_importar_el_score():
    """Una abreviatura plausible (``ABREVIATURA``) se propone a revisión aunque casi nada más se parezca."""
    res = _uno(rasgos(ABREVIATURA=True), score=0.10)
    assert _veredicto(res) == ("REVISAR", "C", "Posible sigla o abreviatura del otro nombre")


def test_revisar_palabra_compartida_tiene_prioridad_sobre_la_sigla():
    """El orden de las ramas es la prioridad: si hay palabra compartida y score, gana su motivo."""
    res = _uno(
        rasgos(N_COMPARTIDAS=1, N_FUERTES=1, COMPARTIDAS="KORIMBA", ABREVIATURA=True), score=0.80
    )
    assert _veredicto(res)[2].startswith("Comparte KORIMBA")


def test_revisar_parecido_de_cadena_exige_umbral_parecido():
    """Sin nada en común, solo un score ≥ ``umbral_parecido`` (0,88 por defecto) propone el par."""
    assert _veredicto(_uno(rasgos(), score=0.88)) == (
        "REVISAR",
        "C",
        "Nombres muy parecidos como cadena (score 0.88)",
    )
    assert _uno(rasgos(), score=0.8799) is None
    assert (
        _veredicto(_uno(rasgos(), score=0.60, umbral_parecido=0.60))[2]
        == "Nombres muy parecidos como cadena (score 0.60)"
    )
    assert _uno(rasgos(), score=0.60) is None


def test_un_par_sin_ninguna_regla_se_descarta():
    """Rasgos neutros y score bajo: ninguna rama aplica y el par no se propone."""
    assert _uno(rasgos(), score=0.30) is None


# ── MISMO_PAIS ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("iso", "evid", "esperado"),
    [
        pytest.param("ESP", "ESP", "SÍ", id="mismo_pais"),
        pytest.param("FRA", "ESP|FRA", "SÍ", id="uno_de_varios_paises_de_evidencia"),
        pytest.param("ESP", "DEU", "NO", id="otro_pais"),
        pytest.param("ESP", "FRA|DEU", "NO", id="ninguno_de_varios"),
        pytest.param("", "ESP", "SIN DATO", id="la_ancla_no_trae_pais"),
        pytest.param("ESP", "", "SIN DATO", id="el_destino_no_trae_pais"),
        pytest.param("", "", "SIN DATO", id="ninguno_trae_pais"),
    ],
)
def test_mismo_pais_dice_si_el_pais_de_la_ancla_esta_entre_los_del_destino(iso, evid, esperado):
    """``MISMO_PAIS`` es «SÍ» / «NO» / «SIN DATO» según el ISO3 de la ancla y los países de evidencia del registro."""
    res = _uno(rasgos(ABREVIATURA=True), iso=iso, evid=evid)
    assert res["MISMO_PAIS"] == esperado


# ── la tabla de salida ────────────────────────────────────────────────────────


def test_clasificar_sin_pares_devuelve_la_misma_tabla():
    """Una tabla vacía de entrada vuelve tal cual (el mismo objeto), sin consultar nada."""
    x = pd.DataFrame()
    graf = graf_de([Grafia("España", "A", "A")])
    comp = ComparadorFalso({})
    assert clasificar(x, graf, destino_de([]), NormalizadorDoble(), comp, config_prueba()) is x
    assert comp.llamadas == []


def test_clasificar_pasa_a_los_rasgos_si_el_nombre_de_la_ancla_pudo_truncarse():
    """El comparador recibe la bandera ``TRUNCADO`` de la grafía del par, como ``bool``."""
    comp = ComparadorFalso({("A", "B"): rasgos(**EMPRESA)})
    graf = graf_de([Grafia("España", "A", "A", truncado=True)])
    prep_b = destino_de([Registro("ESB0000001", "B", "B")])
    clasificar(pares_de([Par(0, 0)]), graf, prep_b, NormalizadorDoble(), comp, config_prueba())
    assert comp.llamadas == [("A", "B", True)]


def test_clasificar_agrega_las_columnas_y_conserva_las_de_entrada():
    """Salen las columnas de los pares y, al final, las siete de la clasificación; el índice se reinicia."""
    comp = ComparadorFalso({("A", "B"): rasgos(**GRUPO)})
    x = pares_de([Par(0, 0)])
    y = clasificar(
        x,
        graf_de([Grafia("España", "A", "A")]),
        destino_de([Registro("ESB0000001", "B", "B")]),
        NormalizadorDoble(),
        comp,
        config_prueba(),
    )
    assert list(y.columns) == [*x.columns, *CAMPOS]
    assert y.index.tolist() == [0]
    assert list(x.columns) == list(pares_de([Par(0, 0)]).columns)  # la entrada no se modifica


def _varios(
    filas: list[Par], por_par: dict[tuple[str, str], dict[str, Any]], **cfg: Any
) -> pd.DataFrame:
    """Clasifica varios pares: una grafía y un registro por cada índice usado."""
    n_g = max(f.g for f in filas) + 1
    n_b = max(f.pos_b for f in filas) + 1
    graf = graf_de([Grafia("España", f"A{k}", f"A{k}") for k in range(n_g)])
    prep_b = destino_de([Registro(f"ESB{k:07d}", f"B{k}", f"B{k}") for k in range(n_b)])
    return clasificar(
        pares_de(filas),
        graf,
        prep_b,
        NormalizadorDoble(),
        ComparadorFalso(por_par),
        config_prueba(**cfg),
    )


def test_clasificar_deja_un_solo_par_por_grafia_y_registro_el_de_mejor_rango():
    """Un mismo (g, registro) por dos superficies: queda el de mejor nivel y banda, aunque su score sea menor."""
    filas = [
        Par(0, 0, "A0", "B0", "NOMBRE", "NOMBRE", 0.95),  # GRUPO B
        Par(0, 0, "A0", "B0x", "NOMBRE", "ALIAS", 0.80),  # EMPRESA B (por el alias)
    ]
    y = _varios(
        filas,
        {
            ("A0", "B0"): rasgos(**GRUPO),
            ("A0", "B0x"): rasgos(N_COMPARTIDAS=1, COMPARTIDAS="ZELVORA", SIN_SOBRAS=True),
        },
    )
    assert y[["SUP_B", "NIVEL_AUTO", "BANDA_AUTO"]].values.tolist() == [["B0x", "EMPRESA", "B"]]


def test_clasificar_a_igual_rango_gana_el_mayor_score_y_luego_el_primero():
    """Mismo nivel y banda: manda el score más alto; con score idéntico, el que venía primero (orden estable)."""
    mismo = rasgos(N_COMPARTIDAS=1, N_FUERTES=1, COMPARTIDAS="ZELVORA")
    y = _varios(
        [
            Par(0, 0, "A0", "B0", score=0.60),
            Par(0, 0, "A0", "B1", score=0.90),
            Par(0, 0, "A0", "B2", score=0.90),
        ],
        {("A0", "B0"): mismo, ("A0", "B1"): mismo, ("A0", "B2"): mismo},
    )
    assert y[["SUP_B", "SCORE_RUES_LINKER"]].values.tolist() == [["B1", 0.90]]


def test_clasificar_ordena_por_grafia_y_registro_y_descarta_lo_que_no_se_propone():
    """La salida va ordenada por (g, pos_b); los pares sin nivel desaparecen."""
    empresa = rasgos(**EMPRESA)
    y = _varios(
        [
            Par(1, 1, "A1", "B1"),
            Par(0, 1, "A0", "B1", score=0.20),  # no se propone
            Par(1, 0, "A1", "B0"),
            Par(0, 0, "A0", "B0"),
        ],
        {
            ("A1", "B1"): empresa,
            ("A0", "B1"): rasgos(),
            ("A1", "B0"): empresa,
            ("A0", "B0"): empresa,
        },
    )
    assert y[["g", "pos_b"]].values.tolist() == [[0, 0], [1, 0], [1, 1]]
    assert y.index.tolist() == [0, 1, 2]


# ── evidencia_extra ───────────────────────────────────────────────────────────


def _pares_clasificados(filas: list[tuple[str, str, str]]) -> pd.DataFrame:
    """Pares ya clasificados, el par ``k`` entre la grafía ``k`` y el registro ``k``: ``(nivel, banda, compartidas)``."""
    return pd.DataFrame(
        {
            "g": range(len(filas)),
            "pos_b": range(len(filas)),
            "NIVEL_AUTO": [f[0] for f in filas],
            "BANDA_AUTO": [f[1] for f in filas],
            "COMPARTIDAS": [f[2] for f in filas],
        }
    )


def _evidencia(
    filas: list[tuple[str, str, str]],
    graf_vars: list[dict[str, str]],
    dest_vars: list[dict[str, str]],
) -> pd.DataFrame:
    """Corre ``evidencia_extra`` con variables por grafía y por registro (las no dadas quedan vacías)."""
    graf = graf_de([Grafia("España", f"A{k}", f"A{k}", **v) for k, v in enumerate(graf_vars)])
    prep_b = destino_de(
        [Registro(f"ESB{k:07d}", f"B{k}", f"B{k}", **v) for k, v in enumerate(dest_vars)]
    )
    return evidencia_extra(_pares_clasificados(filas), graf, prep_b)


def test_evidencia_identificador_comparable_igual_decide_empresa_a():
    """Un identificador comparable igual sube cualquier candidato a EMPRESA A, aunque sea REVISAR C."""
    y = _evidencia(
        [("REVISAR", "C", "")],
        [{"id_comparable": "B12345678"}],
        [{"id_comparable": "B12345678"}],
    )
    assert y[["NIVEL_AUTO", "BANDA_AUTO", "EVIDENCIA_EXTRA"]].values.tolist() == [
        ["EMPRESA", "A", "mismo identificador"]
    ]


@pytest.mark.parametrize(
    ("variable", "nota"),
    [
        pytest.param("dominio_web", "mismo web", id="web"),
        pytest.param("dominio_email", "mismo correo", id="correo"),
        pytest.param("tel", "mismo teléfono", id="telefono"),
    ],
)
def test_evidencia_web_correo_o_telefono_igual_suben_un_candidato_c_con_palabra_compartida_a_grupo_b(
    variable, nota
):
    """Web, correo o teléfono iguales suben REVISAR C (que comparte palabra) a GRUPO B y dejan su nota."""
    y = _evidencia(
        [("REVISAR", "C", "ZELVORA")], [{variable: "zelvora.es"}], [{variable: "zelvora.es"}]
    )
    assert y[["NIVEL_AUTO", "BANDA_AUTO", "EVIDENCIA_EXTRA"]].values.tolist() == [
        ["GRUPO", "B", nota]
    ]


@pytest.mark.parametrize(
    "fila",
    [
        pytest.param(("REVISAR", "C", ""), id="sin_palabra_compartida"),
        pytest.param(("GRUPO", "B", "ZELVORA"), id="ya_en_banda_b"),
        pytest.param(("EMPRESA", "B", "ZELVORA"), id="empresa_en_banda_b"),
        pytest.param(("EMPRESA", "A", "ZELVORA"), id="ya_en_banda_a"),
    ],
)
def test_evidencia_web_igual_solo_sube_candidatos_c_que_comparten_palabra(fila):
    """Sin palabra compartida, o si el par ya está en banda A o B, el sitio web igual solo deja la nota."""
    y = _evidencia([fila], [{"dominio_web": "zelvora.es"}], [{"dominio_web": "zelvora.es"}])
    assert y[["NIVEL_AUTO", "BANDA_AUTO"]].values.tolist() == [[fila[0], fila[1]]]
    assert y["EVIDENCIA_EXTRA"].tolist() == ["mismo web"]


def test_evidencia_ciudad_igual_solo_se_informa():
    """La ciudad nunca sube un par: solo deja la nota «mismo ciudad»."""
    y = _evidencia(
        [("REVISAR", "C", "ZELVORA")], [{"ciudad": "VALENCIA"}], [{"ciudad": "VALENCIA"}]
    )
    assert y[["NIVEL_AUTO", "BANDA_AUTO", "EVIDENCIA_EXTRA"]].values.tolist() == [
        ["REVISAR", "C", "mismo ciudad"]
    ]


def test_evidencia_valores_distintos_o_vacios_no_cuentan_ni_vetan():
    """Valores distintos no restan; un valor vacío (p. ej. un dominio genérico ya anulado) nunca coincide con otro vacío."""
    y = _evidencia(
        [("REVISAR", "C", "ZELVORA"), ("REVISAR", "C", "ZELVORA")],
        [
            {"dominio_web": "zelvora.es", "dominio_email": ""},
            {"dominio_web": "zelvora.es", "dominio_email": ""},
        ],
        [
            {"dominio_web": "otra.es", "dominio_email": ""},
            {"dominio_web": "zelvora.es", "dominio_email": ""},
        ],
    )
    assert y["EVIDENCIA_EXTRA"].tolist() == ["", "mismo web"]
    assert y[["NIVEL_AUTO", "BANDA_AUTO"]].values.tolist() == [["REVISAR", "C"], ["GRUPO", "B"]]


def test_evidencia_dos_valores_vacios_no_coinciden_aunque_la_variable_exista_en_otros_pares():
    """La variable tiene valores en el par 1, pero en el par 0 está vacía de los dos lados: «vacío = vacío» no es evidencia."""
    y = _evidencia(
        [("REVISAR", "C", "ZELVORA"), ("REVISAR", "C", "ZELVORA")],
        [{"dominio_web": ""}, {"dominio_web": "zelvora.es"}],
        [{"dominio_web": ""}, {"dominio_web": "zelvora.es"}],
    )
    assert y["EVIDENCIA_EXTRA"].tolist() == ["", "mismo web"]
    assert y[["NIVEL_AUTO", "BANDA_AUTO"]].values.tolist() == [["REVISAR", "C"], ["GRUPO", "B"]]


def test_evidencia_las_notas_salen_en_el_orden_de_las_variables():
    """Identificador, web, correo, teléfono y ciudad, en ese orden, separadas por «; »."""
    v = {
        "id_comparable": "B1",
        "dominio_web": "zelvora.es",
        "dominio_email": "zelvora.es",
        "tel": "12345678",
        "ciudad": "VALENCIA",
    }
    y = _evidencia([("REVISAR", "C", "ZELVORA")], [v], [dict(v)])
    assert y["EVIDENCIA_EXTRA"].tolist() == [
        "mismo identificador; mismo web; mismo correo; mismo teléfono; mismo ciudad"
    ]
    assert y[["NIVEL_AUTO", "BANDA_AUTO"]].values.tolist() == [["EMPRESA", "A"]]


def test_evidencia_el_identificador_gana_y_el_sitio_web_ya_no_cambia_la_banda():
    """El identificador va primero: deja EMPRESA A y el web igual, que busca bandas C, ya no encuentra una."""
    v = {"id_comparable": "B1", "dominio_web": "zelvora.es"}
    y = _evidencia([("REVISAR", "C", "ZELVORA")], [v], [dict(v)])
    assert y[["NIVEL_AUTO", "BANDA_AUTO", "EVIDENCIA_EXTRA"]].values.tolist() == [
        ["EMPRESA", "A", "mismo identificador; mismo web"]
    ]


def test_evidencia_cada_par_se_compara_con_su_grafia_y_su_registro():
    """El par ``k`` toma las variables de la grafía ``g`` y del registro ``pos_b``; solo el que coincide cambia."""
    y = _evidencia(
        [("REVISAR", "C", ""), ("REVISAR", "C", "")],
        [{"id_comparable": "B1"}, {"id_comparable": "B2"}],
        [{"id_comparable": "B1"}, {"id_comparable": "B9"}],
    )
    assert y[["NIVEL_AUTO", "BANDA_AUTO"]].values.tolist() == [["EMPRESA", "A"], ["REVISAR", "C"]]
    assert y["EVIDENCIA_EXTRA"].tolist() == ["mismo identificador", ""]


def test_evidencia_una_variable_que_la_ancla_no_trae_no_se_considera():
    """Sin la columna en la ancla (o vacía en alguno de los lados) la variable se omite: sin notas y sin cambios."""
    graf = graf_de([Grafia("España", "A0", "A0", dominio_web="zelvora.es")]).drop(
        columns=["ID_COMPARABLE"]
    )
    prep_b = destino_de([Registro("ESB0000001", "B0", "B0", id_comparable="B1")])
    y = evidencia_extra(_pares_clasificados([("REVISAR", "C", "ZELVORA")]), graf, prep_b)
    assert y[["NIVEL_AUTO", "BANDA_AUTO", "EVIDENCIA_EXTRA"]].values.tolist() == [
        ["REVISAR", "C", ""]
    ]
    # Vacía del lado de la ancla: tampoco.
    vacia = _evidencia([("REVISAR", "C", "")], [{}], [{"id_comparable": "B1"}])
    assert vacia["EVIDENCIA_EXTRA"].tolist() == [""]


def test_evidencia_una_variable_vacia_en_la_ancla_se_omite_sin_leer_el_destino():
    """La omisión comprueba primero la ancla: con la variable vacía allí, al destino ni se le pide la columna."""
    graf = graf_de([Grafia("España", "A0", "A0")])
    prep_b = destino_de([Registro("ESB0000001", "B0", "B0")]).drop(columns=["ID_COMPARABLE"])
    y = evidencia_extra(_pares_clasificados([("REVISAR", "C", "")]), graf, prep_b)
    assert y[["NIVEL_AUTO", "BANDA_AUTO", "EVIDENCIA_EXTRA"]].values.tolist() == [
        ["REVISAR", "C", ""]
    ]


def test_evidencia_el_identificador_igual_no_mira_el_pais_ni_las_palabras_ni_el_motivo():
    """Hallazgo F3 (fijado tal cual está): un par entre países, con palabras sin alinear, sube a EMPRESA A.

    Queda un vínculo A automático que ``controles`` marca en FALLA (cruza países y tiene palabras sin alinear),
    y el MOTIVO sigue explicando el nivel anterior.
    """
    pares = _pares_clasificados([("GRUPO", "C", "ZELVORA")])
    pares["MISMO_PAIS"] = ["NO"]
    pares["SOBRA_EXPORTADOR"] = ["ALIMENTOS"]
    pares["SOBRA_DESTINO"] = ["INDUSTRIAS"]
    pares["MOTIVO"] = ["Comparte la marca ZELVORA pero está en otro país; lo que sobra es genérico"]
    graf = graf_de([Grafia("España", "A0", "A0", id_comparable="B12345678")])
    prep_b = destino_de(
        [Registro("ESB0000001", "B0", "B0", paises="DEU", id_comparable="B12345678")]
    )
    y = evidencia_extra(pares, graf, prep_b)
    assert y[["NIVEL_AUTO", "BANDA_AUTO", "MISMO_PAIS", "SOBRA_EXPORTADOR"]].values.tolist() == [
        ["EMPRESA", "A", "NO", "ALIMENTOS"]
    ]
    assert y["MOTIVO"].tolist() == pares["MOTIVO"].tolist()


def test_evidencia_sin_pares_devuelve_la_misma_tabla():
    """Una tabla vacía vuelve tal cual, sin agregar la columna de evidencia."""
    pares = _pares_clasificados([])
    graf = graf_de([Grafia("España", "A0", "A0")])
    assert evidencia_extra(pares, graf, destino_de([])) is pares


def test_evidencia_no_modifica_los_pares_de_entrada():
    """Se trabaja sobre una copia: los pares originales conservan su nivel y no ganan la columna."""
    pares = _pares_clasificados([("REVISAR", "C", "ZELVORA")])
    graf = graf_de([Grafia("España", "A0", "A0", id_comparable="B1")])
    prep_b = destino_de([Registro("ESB0000001", "B0", "B0", id_comparable="B1")])
    y = evidencia_extra(pares, graf, prep_b)
    assert y is not pares
    assert pares["NIVEL_AUTO"].tolist() == ["REVISAR"]
    assert "EVIDENCIA_EXTRA" not in pares.columns
