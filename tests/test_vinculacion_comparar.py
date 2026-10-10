"""Comparación de nombres del motor de vinculación (``flujo.vinculacion.comparar``).

Cada valor esperado salió de correr la función ORIGINAL del motor (la celda ``%%writefile`` del notebook 10)
con los mismos argumentos: la extracción no cambia el comportamiento. Junto a cada caso va la regla que ejerce.
Las empresas son inventadas y el ``Normalizador`` se sustituye por el doble de ``apoyo_comparacion_vinculacion``.
"""

from __future__ import annotations

import numpy as np
import pytest
from apoyo_comparacion_vinculacion import (
    CORPUS,
    GENERICOS,
    NormalizadorDoble,
    comparador,
    config_prueba,
)

from record_linkage.flujo.vinculacion.comparar import (
    COLUMNAS_PUNTAJES,
    Comparador,
    alinear,
    es_abreviatura,
    es_subsecuencia,
    fichas_abreviatura,
    misma_palabra,
)

# ── misma_palabra ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("max_ed", [0, 1, 2])
def test_misma_palabra_exacta_siempre_es_la_misma(max_ed):
    """Dos palabras iguales coinciden aunque no se admita ninguna errata."""
    assert misma_palabra("ZELVORA", "ZELVORA", max_ed) is True


@pytest.mark.parametrize(
    ("t", "u"),
    [
        pytest.param("VELSAER", "VELSAR", id="supresion"),
        pytest.param("TORDEN", "TORDENS", id="insercion"),
        pytest.param("LUNA", "LUNAS", id="insercion_en_palabra_de_4_letras"),
    ],
)
def test_misma_palabra_insercion_y_supresion_valen_en_palabras_cortas(t, u):
    """Una letra de más o de menos es errata aunque la palabra sea corta (≥ 4 letras)."""
    assert misma_palabra(t, u, 1) is True


def test_misma_palabra_sustitucion_en_palabra_corta_no_vale():
    """Medido en el piloto: una sustitución en una palabra corta unía marcas distintas (TORBAL ≠ TORGAL)."""
    assert misma_palabra("TORBAL", "TORGAL", 1) is False


@pytest.mark.parametrize(
    ("t", "u"),
    [
        pytest.param("NORDAVIA", "NORDAVIX", id="8_letras"),
        pytest.param("CONSERVERAS", "CONSERVERAZ", id="11_letras"),
        pytest.param("ALMACENAJE", "ALMACENAXE", id="10_letras"),
    ],
)
def test_misma_palabra_sustitucion_en_palabra_larga_si_vale(t, u):
    """Una sustitución solo cuenta en palabras de ≥ 8 letras (ambas de la misma longitud)."""
    assert misma_palabra(t, u, 1) is True


def test_misma_palabra_con_max_ed_cero_solo_vale_la_exacta():
    """``max_ed=0`` desactiva la errata: ni una inserción vale."""
    assert misma_palabra("MIRVANA", "MIRVANAS", 0) is False
    assert misma_palabra("MIRVANA", "MIRVANAS", 1) is True


@pytest.mark.parametrize(
    ("t", "u"),
    [
        pytest.param("ABC", "ABD", id="sustitucion_de_3_letras"),
        pytest.param("SOL", "SOLE", id="insercion_con_una_palabra_de_3_letras"),
    ],
)
def test_misma_palabra_menor_de_cuatro_letras_no_admite_errata(t, u):
    """Si alguna de las dos palabras tiene menos de 4 letras no hay errata que valga."""
    assert misma_palabra(t, u, 1) is False


def test_misma_palabra_distancia_mayor_que_max_ed_no_vale():
    """La distancia de edición no puede pasar de ``max_ed``; con 2 sí vale una sustitución doble larga."""
    assert misma_palabra("ZORMETTI", "ZOTTI", 1) is False
    assert misma_palabra("ALMACENAJE", "ALMACENAXX", 1) is False
    assert misma_palabra("ALMACENAJE", "ALMACENAXX", 2) is True


# ── alinear ───────────────────────────────────────────────────────────────────


def test_alinear_palabras_exactas():
    """Mismas palabras: no sobra nada y todos los pares son «exacta»."""
    assert alinear(["ZELVORA", "ALIMENTOS"], ["ZELVORA", "ALIMENTOS"], 1) == (
        [],
        [],
        [("ZELVORA", "ZELVORA", "exacta"), ("ALIMENTOS", "ALIMENTOS", "exacta")],
    )


def test_alinear_errata_de_una_letra():
    """Una letra de más en una palabra de ≥ 4 letras se empareja como «errata»."""
    assert alinear(
        ["TAVORIN", "CONSERVAS", "MARINAS"], ["TAVORIIN", "CONSERVAS", "MARINAS"], 1
    ) == (
        [],
        [],
        [
            ("TAVORIN", "TAVORIIN", "errata"),
            ("CONSERVAS", "CONSERVAS", "exacta"),
            ("MARINAS", "MARINAS", "exacta"),
        ],
    )


def test_alinear_con_max_ed_cero_la_errata_queda_como_sobrante():
    """Sin errata admitida, la palabra con la letra de más sobra de los dos lados."""
    assert alinear(
        ["TAVORIN", "CONSERVAS", "MARINAS"], ["TAVORIIN", "CONSERVAS", "MARINAS"], 0
    ) == (
        ["TAVORIN"],
        ["TAVORIIN"],
        [("CONSERVAS", "CONSERVAS", "exacta"), ("MARINAS", "MARINAS", "exacta")],
    )


def test_alinear_errata_con_dos_candidatas_elige_la_de_menor_distancia_y_luego_la_alfabetica():
    """Desempate: ``sorted((distancia, palabra))``; BRUMALIAB gana a BRUMALIAS y esta queda de sobra."""
    assert alinear(["BRUMALIA"], ["BRUMALIAS", "BRUMALIAB"], 1) == (
        [],
        ["BRUMALIAS"],
        [("BRUMALIA", "BRUMALIAB", "errata")],
    )


def test_alinear_cada_palabra_del_otro_lado_se_usa_una_sola_vez():
    """Una palabra repetida de un lado solo se empareja mientras quede otra libre del otro."""
    assert alinear(["LUNA", "LUNA"], ["LUNA"], 1) == (
        ["LUNA"],
        [],
        [("LUNA", "LUNA", "exacta")],
    )


def test_alinear_truncada_en_a_la_ultima_palabra_es_prefijo():
    """Con ``trunc_a`` la ÚLTIMA palabra de ``a`` puede ser prefijo de una de ``b`` («truncada»)."""
    assert alinear(
        ["COOPERATIVA", "CONSERVAS", "CANTAB"], ["COOPERATIVA", "CONSERVAS", "CANTABRICO"], 1, True
    ) == (
        [],
        [],
        [
            ("COOPERATIVA", "COOPERATIVA", "exacta"),
            ("CONSERVAS", "CONSERVAS", "exacta"),
            ("CANTAB", "CANTABRICO", "truncada"),
        ],
    )


def test_alinear_truncada_en_b_la_ultima_palabra_de_b_es_prefijo():
    """Con ``trunc_b`` la ÚLTIMA palabra de ``b`` puede ser prefijo de una de ``a``."""
    assert alinear(
        ["COOPERATIVA", "CONSERVAS", "CANTABRICO"],
        ["COOPERATIVA", "CONSERVAS", "CANTAB"],
        1,
        False,
        True,
    ) == (
        [],
        [],
        [
            ("COOPERATIVA", "COOPERATIVA", "exacta"),
            ("CONSERVAS", "CONSERVAS", "exacta"),
            ("CANTABRICO", "CANTAB", "truncada"),
        ],
    )


def test_alinear_sin_bandera_de_truncamiento_el_prefijo_es_una_diferencia():
    """El mismo par sin ``trunc_a``/``trunc_b``: CANTAB y CANTABRICO quedan sin emparejar."""
    nombres = (["COOPERATIVA", "CONSERVAS", "CANTAB"], ["COOPERATIVA", "CONSERVAS", "CANTABRICO"])
    pares = [("COOPERATIVA", "COOPERATIVA", "exacta"), ("CONSERVAS", "CONSERVAS", "exacta")]
    assert alinear(*nombres, 1) == (["CANTAB"], ["CANTABRICO"], pares)
    assert alinear(nombres[1], nombres[0], 1) == (["CANTABRICO"], ["CANTAB"], pares)


def test_alinear_palabras_distintas_sobran_de_cada_lado():
    """Lo que no se corresponde queda en ``sobra_a`` y ``sobra_b``, en el orden original."""
    assert alinear(["KORIMBA", "QUESOS"], ["KORIMBA", "ACEITES"], 1) == (
        ["QUESOS"],
        ["ACEITES"],
        [("KORIMBA", "KORIMBA", "exacta")],
    )


def test_alinear_lo_que_sigue_a_la_palabra_truncada_se_perdio_en_el_corte():
    """Con ``trunc_a`` y la última palabra emparejada, lo que a ``b`` le sobra DESPUÉS no es diferencia."""
    a = ["INDUSTRIAS", "ALIMENTARIAS", "TRIVENTOLA", "SU"]
    b = ["INDUSTRIAS", "ALIMENTARIAS", "TRIVENTOLA", "SUR", "EXTRA"]
    pares = [
        ("INDUSTRIAS", "INDUSTRIAS", "exacta"),
        ("ALIMENTARIAS", "ALIMENTARIAS", "exacta"),
        ("TRIVENTOLA", "TRIVENTOLA", "exacta"),
        ("SU", "SUR", "truncada"),
    ]
    assert alinear(a, b, 1, True) == ([], [], pares)
    # Sin la bandera, SU no es prefijo admitido y EXTRA es una diferencia más.
    assert alinear(a, b, 1) == (["SU"], ["SUR", "EXTRA"], pares[:3])


def test_alinear_la_perdida_por_truncamiento_tambien_vale_con_trunc_b():
    """Simétrico: con ``trunc_b``, lo que a ``a`` le sobra después de la palabra truncada se perdió."""
    a = ["INDUSTRIAS", "ALIMENTARIAS", "TRIVENTOLA", "SUR", "EXTRA"]
    b = ["INDUSTRIAS", "ALIMENTARIAS", "TRIVENTOLA", "SU"]
    assert alinear(a, b, 1, False, True) == (
        [],
        [],
        [
            ("INDUSTRIAS", "INDUSTRIAS", "exacta"),
            ("ALIMENTARIAS", "ALIMENTARIAS", "exacta"),
            ("TRIVENTOLA", "TRIVENTOLA", "exacta"),
            ("SUR", "SU", "truncada"),
        ],
    )


def test_alinear_solo_la_ultima_palabra_puede_ser_truncada():
    """Si la palabra corta no es la última de ``a``, no es truncamiento: sobra y lo del otro lado también."""
    assert alinear(
        ["TRIVENTOLA", "SU", "ZORVA"], ["TRIVENTOLA", "EXTRA", "SUR", "ZORVA"], 1, True
    ) == (
        ["SU"],
        ["EXTRA", "SUR"],
        [("TRIVENTOLA", "TRIVENTOLA", "exacta"), ("ZORVA", "ZORVA", "exacta")],
    )


@pytest.mark.parametrize(
    ("a", "b", "esperado"),
    [
        pytest.param([], [], ([], [], []), id="ambas_vacias"),
        pytest.param([], ["ZELVORA"], ([], ["ZELVORA"], []), id="a_vacia"),
        pytest.param(["ZELVORA"], [], (["ZELVORA"], [], []), id="b_vacia"),
    ],
)
def test_alinear_con_una_lista_vacia(a, b, esperado):
    """Con listas vacías, incluso con las banderas de truncamiento, no hay pares ni errores."""
    assert alinear(a, b, 1, True, True) == esperado


def test_alinear_errata_en_palabra_de_cuatro_letras():
    """Cuatro letras es el mínimo para admitir errata también al alinear: LUNA ↔ LUNAS."""
    assert alinear(["LUNA"], ["LUNAS"], 1) == ([], [], [("LUNA", "LUNAS", "errata")])


def test_alinear_trunc_b_solo_admite_el_prefijo_de_la_ultima_palabra_de_b():
    """Con ``trunc_b`` solo la ÚLTIMA palabra de ``b`` puede ser un corte; CANTAB, que no lo es, no se alinea."""
    assert alinear(["CANTABRICO", "ZELVORA"], ["CANTAB", "ZELVORA"], 1, False, True) == (
        ["CANTABRICO"],
        ["CANTAB"],
        [("ZELVORA", "ZELVORA", "exacta")],
    )


def test_alinear_sin_la_ultima_palabra_emparejada_no_hay_perdida_por_truncamiento():
    """Si la última palabra del nombre truncado no se emparejó, lo que sobra del otro lado SÍ es diferencia."""
    assert alinear(["TRIVENTOLA", "QWERTYUIOP"], ["TRIVENTOLA", "EXTRA"], 1, True) == (
        ["QWERTYUIOP"],
        ["EXTRA"],
        [("TRIVENTOLA", "TRIVENTOLA", "exacta")],
    )


def test_alinear_sin_la_ultima_palabra_de_b_emparejada_no_hay_perdida_con_trunc_b():
    """Simétrico: si la última palabra de ``b`` quedó sin pareja, lo que sobra de ``a`` sigue siendo diferencia."""
    assert alinear(["ZELVORA", "EXTRA"], ["ZELVORA", "QWERTYUIOP"], 1, False, True) == (
        ["EXTRA"],
        ["QWERTYUIOP"],
        [("ZELVORA", "ZELVORA", "exacta")],
    )


def test_alinear_la_perdida_por_truncamiento_solo_cubre_lo_que_viene_despues_de_la_palabra_cortada():
    """Lo que está ANTES de la palabra cortada se conserva como diferencia; lo que viene DESPUÉS se perdió."""
    pares = [
        ("ZELVORA", "ZELVORA", "exacta"),
        ("TRIVENTOLA", "TRIVENTOLA", "exacta"),
    ]
    a = ["ZELVORA", "TRIVENTOLA", "SU"]
    b = ["ZELVORA", "EXTRA1", "TRIVENTOLA", "SUR", "EXTRA2"]
    assert alinear(a, b, 1, True) == ([], ["EXTRA1"], [*pares, ("SU", "SUR", "truncada")])
    a = ["ZELVORA", "EXTRA1", "TRIVENTOLA", "SUR", "EXTRA2"]
    b = ["ZELVORA", "TRIVENTOLA", "SU"]
    assert alinear(a, b, 1, False, True) == (["EXTRA1"], [], [*pares, ("SUR", "SU", "truncada")])


# ── es_subsecuencia ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("corta", "larga", "esperado"),
    [
        pytest.param("PLVK", "PELUVIKA", True, id="consonantes_en_orden"),
        pytest.param("GRDL", "GARANDEL", True, id="otra_abreviatura_de_aduana"),
        pytest.param("PLK", "PELUVIKA", True, id="tres_letras_es_el_minimo"),
        pytest.param("ABD", "ABCD", True, id="salta_una_letra_del_medio"),
        pytest.param("AB", "ABCD", False, id="menos_de_tres_letras"),
        pytest.param("ABC", "ABC", False, id="igual_de_larga"),
        pytest.param("ABCD", "ABC", False, id="mas_larga_que_la_otra"),
        pytest.param("BCD", "ABCDE", False, id="otra_primera_letra_aunque_esten_en_orden"),
        pytest.param("PKL", "PELUVIKA", False, id="letras_en_otro_orden"),
        pytest.param("ADC", "ABCD", False, id="orden_invertido"),
    ],
)
def test_es_subsecuencia(corta, larga, esperado):
    """La corta empieza como la larga, tiene ≥ 3 letras y sus letras aparecen en orden dentro de la larga."""
    assert es_subsecuencia(corta, larga) is esperado


# ── es_abreviatura ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("a", "b", "esperado"),
    [
        pytest.param(
            ["KR", "EMP", "VAL", "ALIMENTACION"],
            ["KRONOS", "EMPRESARIAL", "VALDORIA", "ALIMENTACION"],
            True,
            id="prefijos_en_orden",
        ),
        pytest.param(["KRO"], ["KRONOS"], True, id="una_palabra_de_tres_letras_prefijo"),
        pytest.param(
            ["KR", "VAL"],
            ["KRONOS", "EMPRESARIAL", "VALDORIA"],
            True,
            id="salta_una_palabra_del_otro_nombre",
        ),
        pytest.param(
            ["KRONOS", "EMPRESARIAL"],
            ["KRONOS", "EMPRESARIAL"],
            False,
            id="iguales_no_abrevian_nada",
        ),
        pytest.param(["AB"], ["ABCD"], False, id="una_palabra_de_dos_letras_no_basta"),
        pytest.param([], ["KRONOS"], False, id="a_vacia"),
        pytest.param(["KRONOS"], [], False, id="b_vacia"),
    ],
)
def test_es_abreviatura_por_prefijos(a, b, esperado):
    """Cada palabra de ``a`` es prefijo de una de ``b``, y al menos una se acorta de verdad."""
    assert es_abreviatura(a, b, GENERICOS) is esperado


@pytest.mark.parametrize(
    ("a", "b"),
    [
        pytest.param(["KEV"], ["KRONOS", "EMPRESARIAL", "VALDORIA"], id="sigla_de_tres_palabras"),
        pytest.param(["KE", "VAL"], ["KRONOS", "EMPRESARIAL", "VALDORIA"], id="sigla_y_prefijo"),
        pytest.param(
            ["KEVI"],
            ["KRONOS", "EMPRESARIAL", "VALDORIA", "IBERICA"],
            id="sigla_de_cuatro_palabras",
        ),
    ],
)
def test_es_abreviatura_acepta_la_sigla_de_varias_palabras_consecutivas(a, b):
    """Una palabra de ``a`` puede ser las iniciales de varias palabras seguidas de ``b``."""
    assert es_abreviatura(a, b, GENERICOS) is True


def test_es_abreviatura_subsecuencia_solo_con_subsecuencia_true():
    """PLVK ⊂ PELUVIKA se acepta únicamente si se pide la subsecuencia de consonantes."""
    assert es_abreviatura(["PLVK"], ["PELUVIKA"], GENERICOS) is False
    assert es_abreviatura(["PLVK"], ["PELUVIKA"], GENERICOS, subsecuencia=True) is True
    assert es_abreviatura(["KR", "VLDR"], ["KRONOS", "VALDORIA"], GENERICOS) is False
    assert es_abreviatura(["KR", "VLDR"], ["KRONOS", "VALDORIA"], GENERICOS, True) is True


def test_es_abreviatura_ancla_en_la_primera_letra_de_la_primera_palabra():
    """Si la primera palabra de ``a`` no empieza como la primera de ``b`` no hay abreviatura."""
    assert es_abreviatura(["ZELVORA"], ["KRONOS", "ZELVORA"], GENERICOS) is False


@pytest.mark.parametrize(
    ("a", "b"),
    [
        pytest.param(["EMP", "ALIM"], ["EMPRESARIAL", "ALIMENTACION"], id="prefijos_genericos"),
        pytest.param(
            ["EAI"], ["EMPRESARIAL", "ALIMENTACION", "INDUSTRIAS"], id="sigla_de_genericas"
        ),
    ],
)
def test_es_abreviatura_exige_cubrir_una_palabra_distintiva(a, b):
    """Si todo lo abreviado son palabras genéricas, no es una sigla (EMPRESARIAL, ALIMENTACION… lo son)."""
    assert es_abreviatura(a, b, GENERICOS) is False
    assert es_abreviatura(a, b, frozenset()) is True


def test_es_abreviatura_una_palabra_de_dos_letras_sola_no_basta_ni_siquiera_como_sigla():
    """``len(a) >= 2 or len(a[0]) >= 3``: ['EA'] no se evalúa aunque sea la sigla de dos palabras."""
    assert es_abreviatura(["EA"], ["EMPRESARIAL", "ALIMENTACION"], frozenset()) is False


def test_es_abreviatura_la_subsecuencia_de_una_palabra_generica_no_es_distintiva():
    """EMPRL ⊂ EMPRESARIAL solo cuenta si EMPRESARIAL no es genérica."""
    assert es_abreviatura(["EMPRL"], ["EMPRESARIAL"], GENERICOS, True) is False
    assert es_abreviatura(["EMPRL"], ["EMPRESARIAL"], frozenset(), True) is True


def test_es_abreviatura_una_palabra_igual_no_se_cuenta_como_sigla_de_una_letra():
    """Una palabra de una letra que ya coincide entera no abrevia nada: iguales siguen sin ser abreviatura."""
    assert es_abreviatura(["KRONOS", "V"], ["KRONOS", "V"], GENERICOS) is False


def test_es_abreviatura_no_salta_palabras_de_b_antes_de_empezar():
    """Salvo en la primera palabra, se pueden saltar palabras de ``b``; al empezar, no: ancla en ``b[0]``."""
    assert es_abreviatura(["KR", "VAL"], ["KRONOS", "EMPRESARIAL", "VALDORIA"], GENERICOS) is True
    assert es_abreviatura(["KR", "VAL"], ["KANTAR", "KRONOS", "VALDORIA"], GENERICOS) is False


# ── fichas_abreviatura ────────────────────────────────────────────────────────


def test_fichas_abreviatura_quita_formas_legales_y_conectores():
    """La ficha es la lista de palabras sin formas legales ni conectores (DE, Y…)."""
    nz = NormalizadorDoble()
    assert fichas_abreviatura("KRONOS EMPRESARIAL VALDORIA SA", nz) == [
        ["KRONOS", "EMPRESARIAL", "VALDORIA"]
    ]
    assert fichas_abreviatura("KRONOS DE VALDORIA", nz) == [["KRONOS", "VALDORIA"]]


def test_fichas_abreviatura_quita_la_letra_suelta_del_final():
    """Una letra final suelta es el fragmento de un S.A./S.L. truncado y no cuenta."""
    nz = NormalizadorDoble()
    assert fichas_abreviatura("KRONOS DE VALDORIA S", nz) == [["KRONOS", "VALDORIA"]]
    assert fichas_abreviatura("KRONOS VALDORIA K", nz) == [["KRONOS", "VALDORIA"]]


def test_fichas_abreviatura_una_palabra_final_de_dos_letras_se_conserva():
    """Solo la letra suelta se recorta: una palabra final de dos letras que no es forma legal se queda."""
    assert fichas_abreviatura("KRONOS VALDORIA XY", NormalizadorDoble()) == [
        ["KRONOS", "VALDORIA", "XY"]
    ]


def test_fichas_abreviatura_una_sola_letra_se_conserva():
    """El recorte de la letra final se detiene en la primera palabra: ``S`` sola queda."""
    assert fichas_abreviatura("S", NormalizadorDoble()) == [["S"]]


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        pytest.param("KEVSAU", [["KEVSAU"], ["KEV"]], id="sau_pegada"),
        pytest.param("KEVSA", [["KEVSA"], ["KEV"]], id="sa_pegada"),
        pytest.param("XYSA", [["XYSA"], ["XY"]], id="quedan_dos_letras"),
        pytest.param("KEVSAS", [["KEVSAS"], ["KEV"]], id="la_forma_legal_mas_larga_primero"),
        pytest.param("ABSAU", [["ABSAU"], ["AB"]], id="sau_con_dos_letras_antes"),
        pytest.param("ASA", [["ASA"]], id="una_sola_letra_antes_no_alcanza"),
        pytest.param("KEVSCOOP", [["KEVSCOOP"], ["KEV"]], id="solo_la_mas_larga_y_una_variante"),
    ],
)
def test_fichas_abreviatura_forma_legal_pegada_a_la_ultima_palabra(nombre, esperado):
    """Si la última palabra trae la forma legal pegada (KEV+SAU) se agrega la variante sin ella."""
    assert fichas_abreviatura(nombre, NormalizadorDoble()) == esperado


@pytest.mark.parametrize("nombre", ["SA", "DE LA", ""])
def test_fichas_abreviatura_sin_palabras_utiles_devuelve_lista_vacia(nombre):
    """Un nombre que solo trae formas legales o conectores no tiene fichas."""
    assert fichas_abreviatura(nombre, NormalizadorDoble()) == []


def test_fichas_abreviatura_una_forma_legal_como_palabra_aparte_se_retira_sin_variante():
    """SAS o KG sueltas no pegan nada a la última palabra: se quitan y la ficha es una sola."""
    nz = NormalizadorDoble()
    assert fichas_abreviatura("ZELVORA", nz) == [["ZELVORA"]]
    assert fichas_abreviatura("KRONOS SAS", nz) == [["KRONOS"]]
    assert fichas_abreviatura("KEV KG", nz) == [["KEV"]]


# ── Comparador.puntajes ───────────────────────────────────────────────────────


def test_puntajes_sin_pares_devuelve_las_cinco_columnas_vacias():
    """Sin pares la tabla sale vacía pero con sus columnas, en este orden."""
    p = comparador().puntajes(np.array([], dtype=object), np.array([], dtype=object))
    assert list(p.columns) == COLUMNAS_PUNTAJES
    assert COLUMNAS_PUNTAJES == [
        "SCORE_RUES_LINKER",
        "SIM_TOKENS_IDF",
        "JARO_WINKLER",
        "DIF_INFORMATIVA",
        "USO_CONTENCION",
    ]
    assert len(p) == 0


def test_puntajes_de_un_par_identico_es_uno():
    """Dos nombres iguales: score y similitudes 1,0, sin diferencia informativa y con contención."""
    a = np.array(["ZELVORA ALIMENTOS"], dtype=object)
    p = comparador().puntajes(a, a.copy())
    assert p.values.tolist() == [[1.0, 1.0, 1.0, 0.0, True]]


def test_puntajes_varios_pares_conservan_el_orden_de_entrada():
    """Una fila por par, en el orden en que llegan; los valores salen del comparador de la librería."""
    a = np.array(
        ["ZELVORA ALIMENTOS", "KORIMBA QUESOS", "TAVORIN CONSERVAS MARINAS", "PLVK", "GALDIMAR"],
        dtype=object,
    )
    b = np.array(
        [
            "ZELVORA ALIMENTOS",
            "KORIMBA ACEITES",
            "TAVORIIN CONSERVAS MARINAS",
            "PELUVIKA",
            "GALDIMAR HOLDING IBERIA",
        ],
        dtype=object,
    )
    p = comparador().puntajes(a, b)
    assert p.values.tolist() == [
        [1.0, 1.0, 1.0, 0.0, True],
        [0.7342, 0.3081, 0.8762, 2.0, False],
        [0.836, 0.3672, 0.9923, 2.0, False],
        [0.6375, 0.0, 0.85, 2.0, False],
        [0.8079, 0.6228, 0.8696, 1.0, False],
    ]


def test_puntajes_redondea_score_y_similitudes_a_cuatro_decimales():
    """SCORE_RUES_LINKER, SIM_TOKENS_IDF y JARO_WINKLER salen con 4 decimales; los otros dos no se tocan."""
    a = np.array(["KORIMBA QUESOS", "TAVORIN CONSERVAS MARINAS"], dtype=object)
    b = np.array(["KORIMBA ACEITES", "TAVORIIN CONSERVAS MARINAS"], dtype=object)
    p = comparador().puntajes(a, b)
    for columna in ("SCORE_RUES_LINKER", "SIM_TOKENS_IDF", "JARO_WINKLER"):
        assert (p[columna] == p[columna].round(4)).all()
    assert p["DIF_INFORMATIVA"].tolist() == [2.0, 2.0]
    assert p["USO_CONTENCION"].tolist() == [False, False]
    assert p["SCORE_RUES_LINKER"].dtype == np.float64
    assert p["USO_CONTENCION"].dtype == np.bool_


def test_puntajes_las_palabras_de_ruido_de_la_configuracion_cambian_la_contencion():
    """``genericos_extra`` de la configuración es ruido para el comparador: KORIMBA ⊂ KORIMBA SEMILLAS pasa a contención."""
    a = np.array(["KORIMBA", "KORIMBA SEMILLAS"], dtype=object)
    b = np.array(["KORIMBA SEMILLAS", "KORIMBA"], dtype=object)
    corpus = [*CORPUS, "KORIMBA SEMILLAS", "KORIMBA"]
    sin = comparador(corpus=corpus).puntajes(a, b)
    con = comparador(config_prueba(genericos_extra=frozenset({"SEMILLAS"})), corpus).puntajes(a, b)
    assert sin.values.tolist() == [[0.7733, 0.4309, 0.8875, 1.0, False]] * 2
    assert con.values.tolist() == [[0.9156, 1.0, 0.8875, 0.0, True]] * 2


# ── Comparador.rasgos ─────────────────────────────────────────────────────────

CLAVES_DE_RASGOS = [
    "UTILES_A",
    "UTILES_B",
    "SOBRA_A",
    "SOBRA_B",
    "COMPARTIDAS",
    "N_COMPARTIDAS",
    "N_FUERTES",
    "SOLO_NOMBRE_PILA",
    "SOBRAS_GENERICAS",
    "SIN_SOBRAS",
    "ERRATA",
    "TRUNCADA",
    "MARCAS_OK",
    "COMPACTO_IGUAL",
    "ABREVIATURA",
]


def test_rasgos_de_nombres_iguales_trae_las_quince_claves_en_orden():
    """El diccionario de rasgos tiene siempre estas 15 claves, en este orden (otras funciones lo leen)."""
    r = comparador().rasgos("ZELVORA ALIMENTOS", "ZELVORA ALIMENTOS", False)
    assert list(r) == CLAVES_DE_RASGOS
    assert r == {
        "UTILES_A": 2,
        "UTILES_B": 2,
        "SOBRA_A": "",
        "SOBRA_B": "",
        "COMPARTIDAS": "ZELVORA",
        "N_COMPARTIDAS": 1,
        "N_FUERTES": 1,
        "SOLO_NOMBRE_PILA": False,
        "SOBRAS_GENERICAS": True,
        "SIN_SOBRAS": True,
        "ERRATA": False,
        "TRUNCADA": False,
        "MARCAS_OK": True,
        "COMPACTO_IGUAL": True,
        "ABREVIATURA": False,
    }


def test_rasgos_errata_marca_errata_y_cuenta_las_palabras_compartidas():
    """Una letra de más en TAVORIN: ERRATA, sin sobras, y la compartida es la palabra del OTRO nombre."""
    r = comparador().rasgos("TAVORIN CONSERVAS MARINAS", "TAVORIIN CONSERVAS MARINAS", False)
    assert r == {
        "UTILES_A": 3,
        "UTILES_B": 3,
        "SOBRA_A": "",
        "SOBRA_B": "",
        "COMPARTIDAS": "MARINAS TAVORIIN",
        "N_COMPARTIDAS": 2,
        "N_FUERTES": 1,
        "SOLO_NOMBRE_PILA": False,
        "SOBRAS_GENERICAS": True,
        "SIN_SOBRAS": True,
        "ERRATA": True,
        "TRUNCADA": False,
        "MARCAS_OK": True,
        "COMPACTO_IGUAL": False,
        "ABREVIATURA": False,
    }


def test_rasgos_truncada_se_activa_con_la_bandera_del_nombre_cortado():
    """CANTAB es el corte de CANTABRICO: TRUNCADA solo si se declara que ``a`` pudo cortarse."""
    comp = comparador()
    a, b = "COOPERATIVA CONSERVAS CANTAB", "COOPERATIVA CONSERVAS CANTABRICO"
    r = comp.rasgos(a, b, True)
    assert (r["TRUNCADA"], r["ERRATA"], r["SIN_SOBRAS"], r["COMPARTIDAS"], r["N_COMPARTIDAS"]) == (
        True,
        False,
        True,
        "CANTABRICO",
        1,
    )
    # La palabra truncada no es una coincidencia «fuerte»: solo cuentan las exactas.
    assert r["N_FUERTES"] == 0
    r = comp.rasgos(b, a, False, True)
    assert (r["TRUNCADA"], r["SIN_SOBRAS"], r["COMPARTIDAS"]) == (True, True, "CANTAB")
    r = comp.rasgos(a, b, False)
    assert (r["TRUNCADA"], r["SIN_SOBRAS"], r["SOBRA_A"], r["SOBRA_B"]) == (
        False,
        False,
        "CANTAB",
        "CANTABRICO",
    )


def test_rasgos_una_truncada_corta_cuenta_como_compartida_si_no_hay_otra():
    """TR (2 letras) no es «compartida» por sí sola, pero la truncada de una palabra no genérica cuenta."""
    comp = comparador(corpus=["TR", "TRIVENTOLA", "SU", "SUR"])
    r = comp.rasgos("TR", "TRIVENTOLA", True)
    assert (r["N_COMPARTIDAS"], r["COMPARTIDAS"], r["N_FUERTES"], r["TRUNCADA"]) == (1, "", 0, True)
    # Si la palabra del otro lado es genérica (SUR) tampoco cuenta como compartida.
    r = comp.rasgos("SU", "SUR", True)
    assert (r["N_COMPARTIDAS"], r["COMPARTIDAS"], r["TRUNCADA"], r["SIN_SOBRAS"]) == (
        0,
        "",
        True,
        True,
    )


def test_rasgos_solo_nombre_de_pila_no_es_evidencia():
    """Compartir ALFREDO (nombre de pila) y nada más: SOLO_NOMBRE_PILA y ninguna palabra «fuerte»."""
    r = comparador().rasgos("ALFREDO ZELVORA", "ALFREDO KRAMENTO", False)
    assert (r["SOLO_NOMBRE_PILA"], r["N_FUERTES"], r["N_COMPARTIDAS"], r["COMPARTIDAS"]) == (
        True,
        0,
        1,
        "ALFREDO",
    )
    assert (r["SOBRA_A"], r["SOBRA_B"], r["SOBRAS_GENERICAS"], r["SIN_SOBRAS"]) == (
        "ZELVORA",
        "KRAMENTO",
        False,
        False,
    )


def test_rasgos_nombres_de_pila_vienen_de_la_configuracion():
    """``cfg.nombres_pila`` decide qué palabra es de pila: con otra lista, ALFREDO ya es una marca."""
    comp = comparador(config_prueba(nombres_pila=frozenset({"KRAMENTO"})))
    r = comp.rasgos("ALFREDO ZELVORA", "ALFREDO KRAMENTO", False)
    assert (r["SOLO_NOMBRE_PILA"], r["N_FUERTES"]) == (False, 1)


def test_rasgos_marcas_distintas_aunque_las_palabras_coincidan():
    """Iniciales sueltas y números distinguen: KRAMENTO 2 ≠ KRAMENTO 3, J MARTINEZ ≠ A MARTINEZ."""
    comp = comparador()
    r = comp.rasgos("KRAMENTO 2", "KRAMENTO 3", False)
    assert (r["MARCAS_OK"], r["COMPACTO_IGUAL"], r["SIN_SOBRAS"], r["COMPARTIDAS"]) == (
        False,
        True,
        True,
        "KRAMENTO",
    )
    r = comp.rasgos("J MARTINEZ", "A MARTINEZ", False)
    assert (r["MARCAS_OK"], r["UTILES_A"], r["UTILES_B"]) == (False, 1, 1)
    assert comp.rasgos("KRAMENTO 2", "KRAMENTO 2", False)["MARCAS_OK"] is True


def test_rasgos_compacto_igual_ignora_los_espacios():
    """KL FOODS y KLFOODS tienen las mismas letras: COMPACTO_IGUAL aunque no se alineen palabra a palabra."""
    r = comparador().rasgos("KL FOODS", "KLFOODS", False)
    assert (r["COMPACTO_IGUAL"], r["N_COMPARTIDAS"], r["SOBRA_A"], r["SOBRA_B"]) == (
        True,
        0,
        "KL FOODS",
        "KLFOODS",
    )
    # Con COMPACTO_IGUAL no se busca sigla.
    assert r["ABREVIATURA"] is False


def test_rasgos_con_las_mismas_letras_compactas_no_se_busca_sigla():
    """VALDORLTD ↔ VALDORL TD: sin palabras compartidas, pero COMPACTO_IGUAL evita buscar una sigla."""
    comp = comparador()
    for a, b in (("VALDORLTD", "VALDORL TD"), ("VALDORL TD", "VALDORLTD")):
        r = comp.rasgos(a, b, False, False, True)
        assert (r["COMPACTO_IGUAL"], r["N_COMPARTIDAS"], r["ABREVIATURA"]) == (True, 0, False)


def test_rasgos_compacto_vacio_nunca_es_igual():
    """Dos nombres sin palabras útiles tienen compacto vacío y NO se consideran iguales."""
    r = comparador().rasgos("SA", "SA", False)
    assert (r["COMPACTO_IGUAL"], r["UTILES_A"], r["UTILES_B"]) == (False, 0, 0)


def test_rasgos_abreviatura_con_subsecuencia_solo_si_subs():
    """PLVK ⊂ PELUVIKA es ABREVIATURA únicamente con ``subs=True``; la sigla de iniciales no lo necesita."""
    comp = comparador()
    assert comp.rasgos("PLVK", "PELUVIKA", False)["ABREVIATURA"] is False
    assert comp.rasgos("PLVK", "PELUVIKA", False, False, True)["ABREVIATURA"] is True
    assert comp.rasgos("KEV", "KRONOS EMPRESARIAL VALDORIA", False)["ABREVIATURA"] is True


def test_rasgos_abreviatura_en_cualquier_sentido():
    """Se prueba a→b y b→a: la sigla puede venir en el primer o en el segundo nombre."""
    comp = comparador()
    assert comp.rasgos("KRONOS EMPRESARIAL VALDORIA", "KEV", False)["ABREVIATURA"] is True
    largo, corto = "KRONOS EMPRESARIAL VALDORIA ALIMENTACION IBERICA", "KR EMP VAL ALIM IB"
    assert comp.rasgos(corto, largo, False)["ABREVIATURA"] is True
    assert comp.rasgos(largo, corto, False)["ABREVIATURA"] is True


def test_rasgos_abreviatura_no_se_busca_si_ya_comparten_una_palabra():
    """Con una palabra compartida no se calcula la sigla aunque exista: KEV ZELVORA ↔ KRONOS EMPRESARIAL VALDORIA ZELVORA."""
    comp = comparador()
    r = comp.rasgos("KEV ZELVORA", "KRONOS EMPRESARIAL VALDORIA ZELVORA", False, False, True)
    assert (r["N_COMPARTIDAS"], r["COMPARTIDAS"], r["ABREVIATURA"]) == (1, "ZELVORA", False)
    # Sin la palabra compartida la misma sigla sí se detecta.
    assert comp.rasgos("KEV", "KRONOS EMPRESARIAL VALDORIA", False)["ABREVIATURA"] is True


def test_rasgos_abreviatura_con_subsecuencia_tambien_en_el_segundo_sentido():
    """La subsecuencia (``subs``) vale en los dos sentidos: PELUVIKA ↔ PLVK se detecta en cualquier orden."""
    comp = comparador()
    assert comp.rasgos("PELUVIKA", "PLVK", False, False, True)["ABREVIATURA"] is True
    assert comp.rasgos("PLVK", "PELUVIKA", False, False, True)["ABREVIATURA"] is True
    assert comp.rasgos("PELUVIKA", "PLVK", False)["ABREVIATURA"] is False


def test_rasgos_una_palabra_generica_del_otro_lado_no_es_compartida():
    """ALIMENTO ↔ ALIMENTOS es una errata, pero ALIMENTOS es genérica: no cuenta como palabra compartida."""
    r = comparador().rasgos("ALIMENTO", "ALIMENTOS", False)
    assert (r["ERRATA"], r["SIN_SOBRAS"], r["N_COMPARTIDAS"], r["COMPARTIDAS"], r["N_FUERTES"]) == (
        True,
        True,
        0,
        "",
        0,
    )


def test_rasgos_una_truncada_de_dos_letras_en_b_cuenta_como_compartida_pero_no_se_lista():
    """ZE (2 letras) es el corte de ZELVORA: suma a N_COMPARTIDAS pero no aparece en COMPARTIDAS."""
    r = comparador().rasgos("ZELVORA", "ZE", False, True)
    assert (r["TRUNCADA"], r["N_COMPARTIDAS"], r["COMPARTIDAS"], r["N_FUERTES"]) == (True, 1, "", 0)


def test_rasgos_una_palabra_exacta_de_tres_letras_ya_es_fuerte():
    """El mínimo de una coincidencia «fuerte» son 3 letras exactas."""
    r = comparador().rasgos("KEV", "KEV", False)
    assert (r["N_FUERTES"], r["N_COMPARTIDAS"], r["COMPARTIDAS"]) == (1, 1, "KEV")


def test_rasgos_solo_nombre_de_pila_exige_que_todas_las_compartidas_lo_sean():
    """ALFREDO ZELVORA ↔ ALFREDO ZELVORA comparte un nombre de pila y una marca: ya no es «solo nombre de pila»."""
    r = comparador().rasgos("ALFREDO ZELVORA", "ALFREDO ZELVORA", False)
    assert (r["SOLO_NOMBRE_PILA"], r["N_COMPARTIDAS"], r["COMPARTIDAS"], r["N_FUERTES"]) == (
        False,
        2,
        "ALFREDO ZELVORA",
        1,
    )


def test_rasgos_la_bandera_de_truncado_de_b_se_usa_para_las_marcas_de_b():
    """Una letra final suelta de ``b`` no es una marca si ``b`` pudo cortarse: KRAMENTO ↔ KRAMENTO S."""
    comp = comparador()
    assert comp.rasgos("KRAMENTO", "KRAMENTO S", False, True)["MARCAS_OK"] is True
    assert comp.rasgos("KRAMENTO", "KRAMENTO S", False, False)["MARCAS_OK"] is False
    assert comp.rasgos("KRAMENTO S", "KRAMENTO", True, False)["MARCAS_OK"] is True


def test_rasgos_sobras_genericas_cuando_solo_sobran_palabras_de_relleno():
    """GALDIMAR contra GALDIMAR HOLDING IBERIA: sobra HOLDING IBERIA, todo genérico, pero hay sobras."""
    r = comparador().rasgos("GALDIMAR", "GALDIMAR HOLDING IBERIA", False)
    assert (r["SOBRAS_GENERICAS"], r["SIN_SOBRAS"], r["SOBRA_B"], r["N_COMPARTIDAS"]) == (
        True,
        False,
        "HOLDING IBERIA",
        1,
    )
    r = comparador().rasgos("KORIMBA QUESOS", "KORIMBA ACEITES", False)
    assert (r["SOBRAS_GENERICAS"], r["SOBRA_A"], r["SOBRA_B"]) == (False, "QUESOS", "ACEITES")


def test_rasgos_nombres_sin_nada_en_comun():
    """Sin palabras compartidas ni sigla: todo sobra y no hay compartidas."""
    r = comparador().rasgos("ZELVORA ALIMENTOS", "KRAMENTO FOODS", False)
    assert (r["N_COMPARTIDAS"], r["COMPARTIDAS"], r["SOBRA_A"], r["SOBRA_B"]) == (
        0,
        "",
        "ZELVORA ALIMENTOS",
        "KRAMENTO FOODS",
    )
    assert (r["SIN_SOBRAS"], r["SOBRAS_GENERICAS"], r["ABREVIATURA"]) == (False, False, False)
    assert r["SOLO_NOMBRE_PILA"] is False  # sin palabras compartidas no hay «solo nombre de pila»


def test_rasgos_el_maximo_de_ediciones_viene_de_la_configuracion():
    """Con ``max_ediciones_palabra=0`` la errata de TAVORIN ya no se alinea: sobra de cada lado."""
    comp = comparador(config_prueba(max_ediciones_palabra=0))
    r = comp.rasgos("TAVORIN CONSERVAS MARINAS", "TAVORIIN CONSERVAS MARINAS", False)
    assert (r["ERRATA"], r["SOBRA_A"], r["SOBRA_B"], r["N_COMPARTIDAS"]) == (
        False,
        "TAVORIN",
        "TAVORIIN",
        1,
    )


def test_rasgos_memoiza_y_devuelve_el_mismo_objeto():
    """Segunda llamada con los mismos argumentos: el mismo diccionario, sin recalcular."""
    comp = comparador()
    primero = comp.rasgos("ZELVORA ALIMENTOS", "ZELVORA ALIMENTOS", False)
    assert comp.rasgos("ZELVORA ALIMENTOS", "ZELVORA ALIMENTOS", False) is primero
    assert comp.rasgos("ZELVORA ALIMENTOS", "ZELVORA ALIMENTOS", False, False, False) is primero


def test_rasgos_la_clave_de_la_cache_incluye_las_tres_banderas():
    """``(a, b, trunc_a, trunc_b, subs)``: cambiar cualquiera de las banderas es otra entrada."""
    comp = comparador()
    comp.rasgos("PLVK", "PELUVIKA", False)
    comp.rasgos("PLVK", "PELUVIKA", True)
    comp.rasgos("PLVK", "PELUVIKA", False, True)
    comp.rasgos("PLVK", "PELUVIKA", False, False, True)
    assert list(comp._cache) == [
        ("PLVK", "PELUVIKA", False, False, False),
        ("PLVK", "PELUVIKA", True, False, False),
        ("PLVK", "PELUVIKA", False, True, False),
        ("PLVK", "PELUVIKA", False, False, True),
    ]


def test_rasgos_el_orden_de_los_argumentos_importa():
    """(a, b) y (b, a) son entradas distintas y pueden dar rasgos distintos (SOBRA_A ↔ SOBRA_B)."""
    comp = comparador()
    ab = comp.rasgos("KORIMBA QUESOS", "KORIMBA ACEITES", False)
    ba = comp.rasgos("KORIMBA ACEITES", "KORIMBA QUESOS", False)
    assert (ab["SOBRA_A"], ab["SOBRA_B"]) == ("QUESOS", "ACEITES")
    assert (ba["SOBRA_A"], ba["SOBRA_B"]) == ("ACEITES", "QUESOS")
    assert len(comp._cache) == 2


# ── Comparador.palabra_comun y construcción ───────────────────────────────────


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        pytest.param("QUELMAR", True, id="una_palabra_en_cuatro_nombres"),
        pytest.param("QUELMAR SA", True, id="la_forma_legal_no_cuenta"),
        pytest.param("CONSERVAS", True, id="tambien_las_genericas_si_son_frecuentes"),
        pytest.param("KORIMBA", False, id="solo_en_dos_nombres"),
        pytest.param("KORIMBA QUESOS", False, id="dos_palabras_no_es_nombre_de_una_palabra"),
        pytest.param("PELUVIKA", False, id="solo_en_uno"),
        pytest.param("SA", False, id="sin_palabras_utiles"),
        pytest.param("", False, id="vacio"),
    ],
)
def test_palabra_comun(nombre, esperado):
    """Nombre de UNA palabra útil que aparece en ≥ 3 nombres del corpus."""
    assert comparador().palabra_comun(nombre) is esperado


def test_palabra_comun_el_umbral_es_tres_nombres():
    """Con la palabra en 2 nombres no es común; con 3 sí."""
    assert comparador(corpus=["MIRELDA X", "MIRELDA Y"]).palabra_comun("MIRELDA") is False
    assert (
        comparador(corpus=["MIRELDA X", "MIRELDA Y", "MIRELDA Z"]).palabra_comun("MIRELDA") is True
    )


def test_comparador_cuenta_cada_palabra_una_vez_por_nombre():
    """La frecuencia documental cuenta nombres, no apariciones: una palabra repetida en un nombre vale 1."""
    comp = comparador(corpus=["MIRELDA MIRELDA", "MIRELDA X"])
    assert comp.df_tok["MIRELDA"] == 2
    assert comp.palabra_comun("MIRELDA") is False


def test_comparador_ignora_la_cadena_vacia_y_las_repetidas_del_corpus():
    """El vocabulario es el conjunto de nombres sin el vacío; un nombre repetido cuenta una vez."""
    comp = comparador(corpus=["", "ZELVORA ALIMENTOS", "ZELVORA ALIMENTOS", "KRAMENTO FOODS"])
    assert comp.vocab == {"ZELVORA ALIMENTOS", "KRAMENTO FOODS"}
    assert comp.df_tok == {"ZELVORA": 1, "ALIMENTOS": 1, "KRAMENTO": 1, "FOODS": 1}


def test_comparador_arranca_con_la_cache_vacia_y_guarda_nz_y_cfg():
    """El estado inicial: sin rasgos memoizados y con el normalizador y la configuración recibidos."""
    cfg = config_prueba()
    nz = NormalizadorDoble()
    comp = Comparador(nz, CORPUS, cfg)
    assert comp._cache == {}
    assert comp.nz is nz
    assert comp.cfg is cfg
