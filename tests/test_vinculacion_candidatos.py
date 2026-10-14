"""Candidatos ancla × destino del motor de vinculación (``flujo.vinculacion.candidatos``).

Cada valor esperado salió de correr la función ORIGINAL del motor (la celda ``%%writefile`` del notebook 10) con las
mismas tablas y el mismo vocabulario: la extracción no cambia el comportamiento. Junto a cada caso va la regla que
ejerce. Las empresas son inventadas; el ``Normalizador`` se sustituye por el doble de ``apoyo_comparacion_vinculacion``
y las tablas ``graf`` y ``prep`` se arman a mano con las columnas que lee la generación de candidatos.
"""

from __future__ import annotations

import pandas as pd
import pytest
from apoyo_comparacion_vinculacion import NormalizadorDoble, comparador, config_prueba
from apoyo_pares_vinculacion import Grafia, Registro, destino_de, graf_de

from record_linkage.flujo.vinculacion.candidatos import (
    claves_borrosas,
    generar_candidatos,
    superficies_destino,
)
from record_linkage.flujo.vinculacion.clasificar import clasificar

COLUMNAS = [
    "g",
    "SUP_A",
    "TIPO_SUP_A",
    "SUP_B",
    "SCORE_RUES_LINKER",
    "SIM_TOKENS_IDF",
    "JARO_WINKLER",
    "DIF_INFORMATIVA",
    "USO_CONTENCION",
    "pos_b",
    "TIPO_SUP_B",
]


def _ancla(*nombres: str, alias: tuple[str, ...] = ()) -> pd.DataFrame:
    """Tabla ``graf``: una grafía por nombre normalizado (con su alias, si lo hay, en el mismo orden)."""
    alias = alias or ("",) * len(nombres)
    return graf_de([Grafia("España", n, n, alias=a) for n, a in zip(nombres, alias, strict=True)])


def _destino(
    *nombres: str, alias: tuple[str, ...] = (), matriz: tuple[str, ...] = ()
) -> pd.DataFrame:
    """Tabla ``prep`` de un destino: un registro por nombre normalizado, con su alias y su matriz si los hay."""
    alias = alias or ("",) * len(nombres)
    matriz = matriz or ("",) * len(nombres)
    return destino_de(
        [
            Registro(f"ESB{k:07d}", n, n, alias=a, grupo=m, grupo_norm=m)
            for k, (n, a, m) in enumerate(zip(nombres, alias, matriz, strict=True), start=1)
        ]
    )


def _generar(graf: pd.DataFrame, prep_b: pd.DataFrame) -> pd.DataFrame:
    """``generar_candidatos`` con el doble de normalizador y el comparador de la prueba."""
    cfg = config_prueba()
    return generar_candidatos(graf, prep_b, NormalizadorDoble(), comparador(cfg), cfg)


def _pares(x: pd.DataFrame) -> list[tuple[int, str, str, str, int, str]]:
    """Los pares como ``(g, SUP_A, TIPO_SUP_A, SUP_B, pos_b, TIPO_SUP_B)``."""
    return [
        (r.g, r.SUP_A, r.TIPO_SUP_A, r.SUP_B, r.pos_b, r.TIPO_SUP_B)
        for r in x.itertuples(index=False)
    ]


# ── claves_borrosas ───────────────────────────────────────────────────────────


def test_claves_borrosas_una_palabra_larga_trae_ella_y_cada_variante_con_una_letra_borrada():
    """Una palabra de ≥ 4 letras entra entera y con cada una de sus variantes sin una letra (SymSpell)."""
    assert claves_borrosas("TAVORIN", NormalizadorDoble()) == {
        "TAVORIN",
        "AVORIN",
        "TVORIN",
        "TAORIN",
        "TAVRIN",
        "TAVOIN",
        "TAVORN",
        "TAVORI",
    }


def test_claves_borrosas_una_palabra_de_tres_letras_entra_sin_variantes():
    """Con 3 letras solo cuenta la palabra misma: borrarle una letra dejaría una palabra casi sin información."""
    assert claves_borrosas("SOL", NormalizadorDoble()) == {"SOL"}


def test_claves_borrosas_las_palabras_de_menos_de_tres_letras_no_cuentan():
    """Ni la palabra de 2 letras ni la que no es útil (forma legal, conector, número) generan claves."""
    assert claves_borrosas("AB SA DE 2000", NormalizadorDoble()) == set()


def test_claves_borrosas_las_palabras_genericas_no_cuentan():
    """Una palabra genérica del sector (ALIMENTOS) no identifica a nadie: solo se indexa la distintiva."""
    claves = claves_borrosas("ZELVORA ALIMENTOS", NormalizadorDoble())
    assert "ZELVORA" in claves
    assert not any(c.startswith("ALIM") or "LIMENTOS" in c for c in claves)
    assert len(claves) == 8  # ZELVORA + sus 7 variantes con una letra menos


def test_claves_borrosas_varias_palabras_se_unen_en_un_conjunto():
    """Las claves de todas las palabras distintivas van juntas (sin repetir las que coinciden)."""
    assert claves_borrosas("LUNA SOL", NormalizadorDoble()) == {
        "LUNA",
        "UNA",
        "LNA",
        "LUA",
        "LUN",
        "SOL",
    }


# ── superficies_destino ───────────────────────────────────────────────────────


def test_superficies_destino_van_por_bloques_nombre_alias_y_matriz():
    """Primero todos los nombres, luego todos los alias y luego todas las matrices; ``pos`` es el registro."""
    s = superficies_destino(
        _destino(
            "DULCEVENT",
            "ARDENTA",
            "RODALTA",
            alias=("", "", "RODALTA DULCES"),
            matriz=("", "SOLENTA", ""),
        )
    )
    assert list(s.columns) == ["pos", "SUP_B", "TIPO_SUP_B"]
    assert list(s.itertuples(index=False, name=None)) == [
        (0, "DULCEVENT", "NOMBRE"),
        (1, "ARDENTA", "NOMBRE"),
        (2, "RODALTA", "NOMBRE"),
        (2, "RODALTA DULCES", "ALIAS"),
        (1, "SOLENTA", "MATRIZ"),
    ]


def test_superficies_destino_descarta_las_vacias_y_reinicia_el_indice():
    """Los alias y matrices vacíos no son superficies; el índice de la salida es 0..n-1."""
    s = superficies_destino(_destino("ZELVORA", "", alias=("", "ZELVORA FOODS")))
    assert list(s.itertuples(index=False, name=None)) == [
        (0, "ZELVORA", "NOMBRE"),
        (1, "ZELVORA FOODS", "ALIAS"),
    ]
    assert s.index.tolist() == [0, 1]


def test_superficies_destino_sin_ninguna_superficie_da_una_tabla_vacia_con_sus_columnas():
    """Un destino sin nombres utilizables no tiene superficies, pero la tabla conserva sus tres columnas."""
    s = superficies_destino(_destino("", ""))
    assert s.empty
    assert list(s.columns) == ["pos", "SUP_B", "TIPO_SUP_B"]


# ── generar_candidatos: las tres vías por separado ────────────────────────────


def test_via_palabra_compartida_sola_un_nombre_con_la_marca_y_nada_mas_se_parece():
    """Vía 1: comparten ZELVORA aunque casi nada más se parezca (Jaro-Winkler ≈ 0,41)."""
    x = _generar(_ancla("ZELVORA"), _destino("GRUPO NACIONAL ZELVORA"))
    assert _pares(x) == [(0, "ZELVORA", "NOMBRE", "GRUPO NACIONAL ZELVORA", 0, "NOMBRE")]
    assert x["JARO_WINKLER"].tolist() == [0.4105]


def test_via_palabra_compartida_admite_una_errata_de_una_letra():
    """Vía 1 con tolerancia: QUELMAR y QUELMAAR (una letra de más) comparten alguna variante con una letra borrada."""
    x = _generar(_ancla("QUELMAR LACTEOS"), _destino("QUELMAAR LACTEOS"))
    assert _pares(x) == [(0, "QUELMAR LACTEOS", "NOMBRE", "QUELMAAR LACTEOS", 0, "NOMBRE")]


def test_via_cadena_sola_dos_nombres_parecidos_sin_ninguna_palabra_en_comun():
    """Vía 2: KORIMBA y KORIMBAAA tienen Jaro-Winkler 0,96 y dos letras de diferencia: ninguna clave en común."""
    x = _generar(_ancla("KORIMBA"), _destino("KORIMBAAA"))
    assert _pares(x) == [(0, "KORIMBA", "NOMBRE", "KORIMBAAA", 0, "NOMBRE")]
    assert x["JARO_WINKLER"].tolist() == [0.9556]
    assert x["SCORE_RUES_LINKER"].tolist() == [0.7167]


def test_via_cadena_el_piso_de_jaro_winkler_es_085():
    """Con Jaro-Winkler ≈ 0,89 el par entra por la vía 2; con ≈ 0,83 y sin palabra en común, no."""
    assert _pares(_generar(_ancla("KORIMBA"), _destino("KORIMXX"))) == [
        (0, "KORIMBA", "NOMBRE", "KORIMXX", 0, "NOMBRE")
    ]
    assert _generar(_ancla("TAVORIN"), _destino("TAVOXXX")).empty


def test_via_sigla_sola_cinco_fichas_es_el_maximo_que_se_busca():
    """Vía 3: KR EMP VAL ALIM IB abrevia a KRONOS EMPRESARIAL VALDORIA ALIMENTACION IBERICA (5 fichas)."""
    x = _generar(
        _ancla("KR EMP VAL ALIM IB"), _destino("KRONOS EMPRESARIAL VALDORIA ALIMENTACION IBERICA")
    )
    assert _pares(x) == [
        (
            0,
            "KR EMP VAL ALIM IB",
            "NOMBRE",
            "KRONOS EMPRESARIAL VALDORIA ALIMENTACION IBERICA",
            0,
            "NOMBRE",
        )
    ]


def test_via_sigla_seis_fichas_ya_no_se_buscan():
    """Con una ficha más (6) la sigla no se busca y, sin otra vía, el par no es candidato."""
    x = _generar(
        _ancla("KR EMP VAL ALIM IB SUR"),
        _destino("KRONOS EMPRESARIAL VALDORIA ALIMENTACION IBERICA SURESTE"),
    )
    assert x.empty


def test_via_sigla_las_iniciales_de_un_nombre_de_varias_palabras():
    """Vía 3: KEV son las iniciales de KRONOS, EMPRESARIAL y VALDORIA."""
    x = _generar(_ancla("KEV"), _destino("KRONOS EMPRESARIAL VALDORIA"))
    assert _pares(x) == [(0, "KEV", "NOMBRE", "KRONOS EMPRESARIAL VALDORIA", 0, "NOMBRE")]


def test_via_sigla_con_la_forma_legal_pegada_a_la_ultima_ficha():
    """Vía 3: KEVSAU se prueba también sin el SAU pegado (K·E·V + SAU)."""
    x = _generar(_ancla("KEVSAU"), _destino("KRONOS EMPRESARIAL VALDORIA"))
    assert _pares(x) == [(0, "KEVSAU", "NOMBRE", "KRONOS EMPRESARIAL VALDORIA", 0, "NOMBRE")]


def test_via_sigla_la_ficha_corta_es_de_cuatro_letras_o_menos():
    """Frontera de la ficha corta: con una ficha de 4 letras la abreviatura se busca; con 5, no."""
    corta = _generar(_ancla("EMPR VALD"), _destino("EMPRESARIAL VALDORIA"))
    assert _pares(corta) == [(0, "EMPR VALD", "NOMBRE", "EMPRESARIAL VALDORIA", 0, "NOMBRE")]
    assert _generar(_ancla("ALIME MARIN"), _destino("ALIMENTACION MARINERAS")).empty


def test_via_sigla_una_ficha_larga_sola_no_se_busca():
    """Solo se buscan siglas con alguna ficha de ≤ 4 letras: KRONOS (6) no es sigla de un nombre largo."""
    x = _generar(_ancla("KRONOS"), _destino("KRONOSAMERICANAINTERNACIONALCOMERCIAL"))
    assert x.empty


def test_via_sigla_busca_la_sigla_tambien_del_lado_del_destino():
    """Arreglo C3 (recall): con la sigla en el destino y el nombre largo en la ancla el par también es candidato.

    El motor original solo miraba la sigla del lado de la ancla y perdía este par, aunque
    ``Comparador.rasgos`` reconoce la abreviatura en los dos sentidos. Es el espejo de
    ``test_via_sigla_las_iniciales_de_un_nombre_de_varias_palabras``.
    """
    assert _pares(_generar(_ancla("KRONOS EMPRESARIAL VALDORIA"), _destino("KEV"))) == [
        (0, "KRONOS EMPRESARIAL VALDORIA", "NOMBRE", "KEV", 0, "NOMBRE")
    ]


def test_via_sigla_del_destino_aplica_los_mismos_limites_que_la_de_la_ancla():
    """La sigla del destino tampoco se busca con 6 fichas, ni con una ficha larga sola, ni si solo abrevia genéricas."""
    assert _generar(
        _ancla("KRONOS EMPRESARIAL VALDORIA ALIMENTACION IBERICA SURESTE"),
        _destino("KR EMP VAL ALIM IB SUR"),
    ).empty
    assert _generar(_ancla("KRONOSAMERICANAINTERNACIONALCOMERCIAL"), _destino("KRONOS")).empty
    assert _generar(_ancla("ALIMENTOS INDUSTRIAS CONSERVAS"), _destino("ALIM IND CONS")).empty


def test_via_sigla_del_destino_con_la_forma_legal_pegada_y_otro_destino_sin_relacion():
    """KEVSAU (destino) abrevia a KRONOS EMPRESARIAL VALDORIA (ancla); un destino que no empieza igual no entra."""
    x = _generar(_ancla("KRONOS EMPRESARIAL VALDORIA"), _destino("KEVSAU"))
    assert _pares(x) == [(0, "KRONOS EMPRESARIAL VALDORIA", "NOMBRE", "KEVSAU", 0, "NOMBRE")]
    assert _generar(_ancla("KRONOS EMPRESARIAL VALDORIA"), _destino("TEV")).empty


def test_via_sigla_una_superficie_del_destino_solo_con_formas_legales_no_tiene_fichas():
    """«SA SL» son solo formas legales: no tiene fichas con las que formar una sigla y no genera pares."""
    assert _generar(_ancla("KEV"), _destino("SA SL")).empty


@pytest.mark.parametrize(
    "destino",
    [
        pytest.param("LA KRONOS EMPRESARIAL VALDORIA", id="conector_al_inicio"),
        pytest.param("SA KRONOS EMPRESARIAL VALDORIA", id="forma_legal_al_inicio"),
    ],
)
def test_via_sigla_ignora_los_conectores_y_las_formas_legales_del_destino(destino):
    """Las fichas del destino no cuentan conectores ni formas legales: KEV sigue siendo K·E·V aunque haya «LA» o «SA» antes."""
    x = _generar(_ancla("KEV"), _destino(destino))
    assert _pares(x) == [(0, "KEV", "NOMBRE", destino, 0, "NOMBRE")]


def test_via_sigla_una_abreviatura_de_palabras_solo_genericas_no_cuenta():
    """ALIM IND CONS abrevia tres palabras genéricas del sector: sin ninguna distintiva no hay sigla que proponer."""
    assert _generar(_ancla("ALIM IND CONS"), _destino("ALIMENTOS INDUSTRIAS CONSERVAS")).empty


def test_la_sigla_por_subsecuencia_de_consonantes_no_es_candidata():
    """PLVK ⊂ PELUVIKA es sigla por subsecuencia, pero la generación no la activa: solo la revisión de la ancla."""
    assert _generar(_ancla("PLVK"), _destino("PELUVIKA")).empty


# ── generar_candidatos: forma de la salida ────────────────────────────────────


def test_un_par_hallado_por_varias_vias_aparece_una_sola_vez():
    """KORIMBA QUESOS ↔ KORIMBA ACEITES entra por palabra compartida y por parecido de cadena: una fila."""
    x = _generar(_ancla("KORIMBA QUESOS"), _destino("KORIMBA ACEITES"))
    assert _pares(x) == [(0, "KORIMBA QUESOS", "NOMBRE", "KORIMBA ACEITES", 0, "NOMBRE")]


def test_los_pares_de_una_misma_superficie_salen_ordenados_por_la_posicion_del_destino():
    """Una superficie de la ancla con dos parejas: salen en el orden de las superficies del destino, no el de un conjunto."""
    x = _generar(_ancla("KORIMBA QUESOS"), _destino("KORIMBA ACEITES", "KORIMBA VINOS"))
    assert [(r.SUP_B, r.pos_b) for r in x.itertuples(index=False)] == [
        ("KORIMBA ACEITES", 0),
        ("KORIMBA VINOS", 1),
    ]


def test_sin_pares_devuelve_un_dataframe_vacio_sin_columnas():
    """Si ningún par cumple las tres vías, el resultado es ``DataFrame()``: vacío y sin columnas."""
    x = _generar(_ancla("AAAA BBBB"), _destino("XXXX YYYY"))
    assert x.empty
    assert list(x.columns) == []
    assert isinstance(x.index, pd.RangeIndex)


@pytest.mark.parametrize(
    ("graf", "prep_b"),
    [
        pytest.param(_ancla(), _destino("ZELVORA"), id="ancla_sin_grafias"),
        pytest.param(_ancla("ZELVORA"), _destino(), id="destino_sin_registros"),
        pytest.param(_ancla(""), _destino("ZELVORA"), id="ancla_sin_nombre"),
        pytest.param(_ancla("ZELVORA"), _destino(""), id="destino_sin_nombre"),
    ],
)
def test_sin_superficies_de_algun_lado_no_hay_candidatos(graf, prep_b):
    """Una ancla o un destino sin superficies no vacías no produce pares, y no falla."""
    assert _generar(graf, prep_b).empty


def test_las_columnas_y_su_orden_son_el_contrato_con_clasificar():
    """Las once columnas, en este orden, con ``pos`` renombrada a ``pos_b`` y el índice limpio."""
    x = _generar(
        _ancla("ZELVORA ALIMENTOS", "KRAMENTO FOODS"),
        _destino("KRAMENTO FOODS", "ZELVORA ALIMENTOS"),
    )
    assert list(x.columns) == COLUMNAS
    assert x.index.tolist() == list(range(len(x)))
    assert x["USO_CONTENCION"].dtype == bool


def test_cada_par_se_expande_a_las_grafias_y_a_los_registros_que_comparten_la_superficie():
    """Dos grafías con el mismo nombre y dos registros con el mismo nombre dan 2 × 2 filas, ordenadas por grafía."""
    x = _generar(
        _ancla("ZELVORA ALIMENTOS", "ZELVORA ALIMENTOS"),
        _destino("ZELVORA ALIMENTOS", "ZELVORA ALIMENTOS", "OTRA"),
    )
    assert [(r.g, r.pos_b) for r in x.itertuples(index=False)] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert set(x["SCORE_RUES_LINKER"]) == {1.0}


def test_el_alias_de_la_ancla_y_el_alias_y_la_matriz_del_destino_son_superficies():
    """Un alias de la ancla (``ALIAS``) y la matriz reportada del destino (``MATRIZ``) también generan pares."""
    x = _generar(
        _ancla("PELUVIKA DULCES", "SOLENTA ALIMENTOS", alias=("DULCEVENT", "")),
        _destino(
            "DULCEVENT",
            "ARDENTA",
            "RODALTA",
            alias=("", "", "RODALTA DULCES"),
            matriz=("", "SOLENTA ALIMENTOS", ""),
        ),
    )
    assert _pares(x) == [
        (1, "SOLENTA ALIMENTOS", "NOMBRE", "SOLENTA ALIMENTOS", 1, "MATRIZ"),
        (0, "DULCEVENT", "ALIAS", "DULCEVENT", 0, "NOMBRE"),
    ]


def test_cada_via_aporta_su_par_cuando_van_juntas_y_lo_ajeno_no_entra():
    """Las tres vías en una sola corrida: tres pares, uno por vía, y el nombre ajeno queda fuera."""
    x = _generar(
        _ancla("ZELVORA", "KORIMBA", "KR EMP VAL ALIM IB", "TOTALMENTE AJENO"),
        _destino(
            "GRUPO NACIONAL ZELVORA",
            "KORIMBAAA",
            "KRONOS EMPRESARIAL VALDORIA ALIMENTACION IBERICA",
            "OTRA COSA DISTINTA",
        ),
    )
    assert [(r.g, r.pos_b) for r in x.itertuples(index=False)] == [(0, 0), (1, 1), (2, 2)]
    assert x["SCORE_RUES_LINKER"].tolist() == [0.3079, 0.7167, 0.5243]


# ── el parecido de cadena y umbral_parecido ───────────────────────────────────


def test_un_par_que_solo_entra_por_jaro_winkler_se_propone_solo_si_se_baja_umbral_parecido():
    """ZELVORAL ↔ ZELVORXX solo entra por la vía 2 (score 0,675): con el umbral por defecto no se propone."""
    graf = graf_de([Grafia("España", "Zelvoral", "ZELVORAL")])
    prep_b = destino_de([Registro("ESB0000001", "Zelvorxx", "ZELVORXX")])

    def propuesta(umbral: float | None) -> list[list[str]]:
        cfg = config_prueba() if umbral is None else config_prueba(umbral_parecido=umbral)
        nz, comp = NormalizadorDoble(), comparador(cfg)
        x = generar_candidatos(graf, prep_b, nz, comp, cfg)
        assert _pares(x) == [(0, "ZELVORAL", "NOMBRE", "ZELVORXX", 0, "NOMBRE")]
        y = clasificar(x, graf, prep_b, nz, comp, cfg)
        return y[["NIVEL_AUTO", "BANDA_AUTO", "MOTIVO"]].values.tolist()

    assert propuesta(None) == []
    assert propuesta(0.6) == [["REVISAR", "C", "Nombres muy parecidos como cadena (score 0.68)"]]
