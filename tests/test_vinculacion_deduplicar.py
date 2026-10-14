"""Deduplicación interna del motor de vinculación (``flujo.vinculacion.deduplicar``).

Cada valor esperado salió de correr la función ORIGINAL del motor (la celda ``%%writefile`` del notebook 10)
sobre la misma tabla ``prep``, el mismo vocabulario y las mismas decisiones: la extracción no cambia el
comportamiento. Junto a cada caso va la regla que ejerce. Las empresas son inventadas; el ``Normalizador``
se sustituye por el doble de ``apoyo_comparacion_vinculacion`` y las tablas ``prep`` se arman a mano con lo
que ``preparar_fuente`` dejaría en las columnas que estas funciones leen.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from apoyo_comparacion_vinculacion import (
    CORPUS,
    FilaAncla,
    FilaDestino,
    NormalizadorDoble,
    comparador,
    config_prueba,
    escribir_decisiones,
    prep_ancla,
    prep_destino,
)

from record_linkage.flujo.vinculacion.config import Fuente
from record_linkage.flujo.vinculacion.decisiones import leer_decisiones
from record_linkage.flujo.vinculacion.deduplicar import (
    FAMILIAS_LEGALES,
    FAMILIAS_RE,
    componentes,
    deduplicar_ancla,
    deduplicar_destino,
    familias_compatibles,
    familias_legales,
    raiz_id,
)
from record_linkage.flujo.vinculacion.ejecutar import _avisos_de_decisiones_de_un_destino

A = FilaAncla
D = FilaDestino
DUP = "DUPLICADO"
EXP = "EXPORTADORES"


# ── componentes (union-find) ──────────────────────────────────────────────────


def test_componentes_se_numeran_por_orden_de_primera_aparicion():
    """Las componentes salen 0..k-1 según el primer nodo de cada una, no según la raíz que se elija."""
    assert componentes(5, [3, 1], [4, 0]).tolist() == [0, 0, 1, 2, 2]


def test_componentes_sin_aristas_cada_nodo_es_la_suya():
    """Sin uniones, n nodos son n componentes numeradas en orden."""
    assert componentes(4, [], []).tolist() == [0, 1, 2, 3]


def test_componentes_une_por_transitividad():
    """0–1 y 1–2 dejan a los tres en una componente; el resto queda aparte."""
    assert componentes(6, [0, 1, 3], [1, 2, 4]).tolist() == [0, 0, 0, 1, 1, 2]


def test_componentes_no_depende_del_orden_de_las_aristas():
    """Determinista: la misma lista de uniones, en cualquier orden o sentido, da la misma partición."""
    aristas = [(0, 1), (1, 2), (3, 4), (5, 2), (6, 7)]
    esperado = [0, 0, 0, 1, 1, 0, 2, 2]
    rng = np.random.default_rng(7)
    for _ in range(5):
        orden = rng.permutation(len(aristas))
        a = [aristas[i][0] for i in orden]
        b = [aristas[i][1] for i in orden]
        assert componentes(8, a, b).tolist() == esperado
        assert componentes(8, b, a).tolist() == esperado


def test_componentes_acepta_aristas_repetidas_y_bucles():
    """Una arista repetida o un nodo unido consigo mismo no cambia nada."""
    assert componentes(3, [0, 0, 1, 2], [1, 1, 0, 2]).tolist() == [0, 0, 1]


def test_componentes_sin_nodos_devuelve_un_arreglo_vacio():
    """``n = 0`` es válido y da un arreglo vacío."""
    assert len(componentes(0, [], [])) == 0


def test_componentes_devuelve_un_arreglo_de_enteros():
    """El resultado es un ``ndarray`` de enteros (se usa como índice y para ``groupby``)."""
    r = componentes(3, [0], [1])
    assert isinstance(r, np.ndarray)
    assert np.issubdtype(r.dtype, np.integer)


# ── familias legales ──────────────────────────────────────────────────────────

#: (nombre, familia): un caso por familia de ``FAMILIAS_LEGALES`` y por rama de la limpieza previa.
FAMILIAS = [
    pytest.param("Zelvora Stiftung & Co KG", "STIFTUNG_KG", id="stiftung_kg"),
    pytest.param("Zelvora SE & Co KG", "SE_KG", id="se_kg"),
    pytest.param("Kramento GmbH & Co KG", "GMBH_KG", id="gmbh_kg"),
    pytest.param("Orbelia AG & Co OHG", "GMBH_KG", id="ag_ohg"),
    pytest.param("Tavorin KGaA", "KGAA", id="kgaa"),
    pytest.param("Tavorin OHG", "OHG", id="ohg"),
    pytest.param("Tavorin KG", "KG", id="kg"),
    pytest.param("Tavorin GmbH", "GMBH", id="gmbh"),
    pytest.param("Tavorin Beschrankter Haftung", "GMBH", id="gmbh_en_palabras"),
    pytest.param("Tavorin AG", "AG", id="ag"),
    pytest.param("Tavorin Aktiengesellschaft", "AG", id="ag_en_palabras"),
    pytest.param("Tavorin SE", "SE", id="se"),
    pytest.param("Fribelia S. COOP", "COOP", id="coop_con_puntos"),
    pytest.param("Fribelia Sociedad Cooperativa", "COOP", id="cooperativa"),
    pytest.param("Fribelia eG", "COOP", id="eg"),
    pytest.param("Fribelia Genossenschaft", "COOP", id="genossenschaft"),
    pytest.param("Brandelia Fromages SAS", "SAS", id="sas"),
    pytest.param("Brandelia Fromages SASU", "SAS", id="sasu"),
    pytest.param("Fribelia SL", "SL", id="sl"),
    pytest.param("Fribelia S.L.U.", "SL", id="slu_con_puntos"),
    pytest.param("Fribelia SRL", "SL", id="srl"),
    pytest.param("Fribelia Sarl", "SL", id="sarl"),
    pytest.param("Fribelia Limitada", "SL", id="limitada"),
    pytest.param("Zelvora Alimentos SCA", "SCA", id="sca"),
    pytest.param("Zelvora Alimentos SA", "SA", id="sa"),
    pytest.param("Zelvora Alimentos S.A.", "SA", id="sa_con_puntos"),
    pytest.param("Zelvora Alimentos SAU", "SA", id="sau"),
    pytest.param("Zelvora Alimentos SpA", "SA", id="spa"),
    pytest.param("Zelvora Sociedad Anonima", "SA", id="anonima"),
    pytest.param("Brandelia BV", "BV", id="bv"),
    pytest.param("Brandelia NV", "NV", id="nv"),
    pytest.param("Brandelia Ltd", "LTD", id="ltd"),
    pytest.param("Brandelia Limited", "LTD", id="limited"),
    pytest.param("Nolmendo Inc", "INC", id="inc"),
    pytest.param("Nolmendo Corp", "INC", id="corp"),
    pytest.param("Nolmendo LLC", "LLC", id="llc"),
    pytest.param("Zelvora Alimentos", "", id="sin_forma_legal"),
    pytest.param("Pustorán Foods (Lamberto SL)", "", id="el_parentesis_no_cuenta"),
    pytest.param("Pustorán Foods (Lamberto SL", "", id="parentesis_sin_cerrar"),
    pytest.param("", "", id="vacio"),
]  # fmt: skip


@pytest.mark.parametrize(("nombre", "familia"), FAMILIAS)
def test_familias_legales_reconoce_cada_familia(nombre, familia):
    """La familia sale de la forma legal del nombre original, tras quitar paréntesis y puntuación."""
    assert familias_legales(pd.Series([nombre])).tolist() == [familia]


def test_familias_legales_gana_la_primera_familia_de_la_tabla():
    """Una forma compuesta (GMBH & CO KG) gana a las simples (GMBH, KG) porque va antes en la tabla."""
    assert familias_legales(pd.Series(["Kramento GmbH & Co KG"])).tolist() == ["GMBH_KG"]
    assert familias_legales(pd.Series(["Kramento KG GmbH"])).tolist() == ["KG"]


def test_familias_legales_trata_los_ausentes_como_vacios_y_conserva_el_orden():
    """``None`` y NaN no fallan: dan ``''``. La salida es un arreglo de objetos, una familia por nombre."""
    r = familias_legales(pd.Series(["Fribelia SL", None, "Zelvora SA", float("nan"), "Tavorin AG"]))
    assert isinstance(r, np.ndarray)
    assert r.dtype == object
    assert r.tolist() == ["SL", "", "SA", "", "AG"]


def test_tabla_de_familias_legales():
    """La tabla es pública (otras funciones la consultan): 21 familias (19 del original + SCS y EU, F3.1b) con su patrón compilado."""
    claves = [k for k, _ in FAMILIAS_LEGALES]
    assert len(FAMILIAS_LEGALES) == 21
    assert claves[:3] == ["STIFTUNG_KG", "SE_KG", "GMBH_KG"]
    assert claves[-3:] == ["INC", "LLC", "EU"]
    assert [k for k, _ in FAMILIAS_RE] == claves
    assert all(hasattr(rx, "search") for _, rx in FAMILIAS_RE)


@pytest.mark.parametrize(
    ("fa", "fb", "estricto", "esperado"),
    [
        pytest.param("SL", "SL", False, True, id="iguales"),
        pytest.param("SL", "COOP", False, False, id="distintas"),
        pytest.param("SL", "", False, True, id="una_desconocida"),
        pytest.param("", "COOP", False, True, id="otra_desconocida"),
        pytest.param("", "", False, True, id="ambas_desconocidas"),
        pytest.param("SL", "SL", True, True, id="estricto_iguales"),
        pytest.param("SL", "COOP", True, False, id="estricto_distintas"),
        pytest.param("SL", "", True, False, id="estricto_una_desconocida"),
        pytest.param("", "", True, True, id="estricto_ambas_desconocidas"),
    ],
)
def test_familias_compatibles(fa, fb, estricto, esperado):
    """Compatibles: iguales, o una desconocida; en modo estricto solo iguales."""
    assert familias_compatibles(fa, fb, estricto) is esperado


def test_familias_compatibles_por_defecto_no_es_estricto():
    """Sin el tercer argumento, una familia desconocida es compatible con cualquiera."""
    assert familias_compatibles("SL", "") is True
    assert familias_compatibles("", "SA") is True


# ── raiz_id ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("entrada", "raiz"),
    [
        pytest.param("ESB0000001", "ESB0000001", id="sin_sufijo"),
        pytest.param("ESB0000001-1003", "ESB0000001", id="sufijo_de_sucursal"),
        pytest.param("DE0000000001-1003", "DE0000000001", id="sucursal_alemana"),
        pytest.param("FR12345678900012", "FR123456789", id="siret_a_siren"),
        pytest.param("FR12345678900012-7", "FR123456789", id="siret_con_sufijo"),
        pytest.param("FR123456789", "FR123456789", id="siren_ya_es_la_raiz"),
        pytest.param("FR1234567890001", "FR1234567890001", id="siret_incompleto_no_se_toca"),
        pytest.param("FR123456789000123", "FR123456789000123", id="siret_largo_no_se_toca"),
        pytest.param("  ESB0000001 - 1  ", "ESB0000001", id="espacios"),
        pytest.param(12345, "12345", id="no_texto"),
        pytest.param("", "", id="vacio"),
    ],
)
def test_raiz_id(entrada, raiz):
    """Sin el sufijo de sucursal (tras el primer «-») y, en Francia, el SIREN (9 dígitos) de un SIRET."""
    assert raiz_id(entrada) == raiz


# ── deduplicar_ancla ──────────────────────────────────────────────────────────


def _leer(tmp_path, *archivos):
    """Escribe un CSV de decisiones por cada lista de filas y los lee con ``leer_decisiones``."""
    rutas = [
        escribir_decisiones(tmp_path / f"decisiones_{k}.csv", filas)
        for k, filas in enumerate(archivos, start=1)
    ]
    return leer_decisiones(rutas)


def _ancla(filas, dec=None, *, clave_ancla=EXP, **cambios):
    """``deduplicar_ancla`` con el vocabulario de prueba más los nombres de ``filas`` y el doble de normalizador."""
    cfg = config_prueba(clave_ancla=clave_ancla, **cambios)
    corpus = [*CORPUS, *(f.norm for f in filas), *(f.alias for f in filas)]
    dec = leer_decisiones([]) if dec is None else dec
    return deduplicar_ancla(
        prep_ancla(filas), NormalizadorDoble(), comparador(cfg, corpus), cfg, dec
    )


def _listas(tabla):
    """Filas de una tabla como listas de Python (sin depender del dtype de pandas)."""
    return tabla.values.tolist()


def _puntaje(filas, a, b, **cambios):
    """SCORE_RUES_LINKER de ``a`` contra ``b`` con el mismo vocabulario que usa ``_ancla`` para ``filas``."""
    cfg = config_prueba(**cambios)
    corpus = [*CORPUS, *(f.norm for f in filas), *(f.alias for f in filas)]
    p = comparador(cfg, corpus).puntajes(np.array([a], dtype=object), np.array([b], dtype=object))
    return float(p["SCORE_RUES_LINKER"].iat[0])


def test_ancla_devuelve_las_tablas_con_sus_columnas():
    """Contrato de salida: ``graf`` (una fila por grafía), ``un`` y ``pe`` con estas columnas exactas."""
    graf, un, pe = _ancla([A("España", "Zelvora Alimentos SA", "ZELVORA ALIMENTOS")])
    assert list(graf.columns) == [
        "CLAVE",
        "PAIS_ORIGINAL",
        "NOMBRE_ORIGINAL",
        "PAIS_ISO3",
        "NOMBRE_NORM",
        "ALIAS_NORM",
        "TRUNCADO",
        "INTERMEDIARIO",
        "N_FILAS",
        "FORMA_LEGAL",
        "_COMP",
    ]
    assert list(un.columns) == ["pos_a", "pos_b", "REGLA"]
    assert list(pe.columns) == ["pos_a", "pos_b", "MOTIVO"]
    assert _listas(graf[["CLAVE", "FORMA_LEGAL", "N_FILAS", "_COMP"]]) == [
        ["España|Zelvora Alimentos SA", "SA", 1, 0]
    ]
    assert len(un) == len(pe) == 0


def test_ancla_deja_vetos_y_desconocidas_en_attrs_incluso_sin_decisiones():
    """Otras funciones leen ``graf.attrs``: las tres claves existen siempre, vacías si no hubo decisiones."""
    graf, _, _ = _ancla([A("España", "Zelvora Alimentos SA", "ZELVORA ALIMENTOS")])
    assert set(graf.attrs) == {"vetos", "desconocidas", "autopares"}
    assert graf.attrs["vetos"] == set()
    assert graf.attrs["desconocidas"] == []


def test_ancla_mismo_nombre_normalizado_une_automaticamente():
    """Regla 1: el mismo nombre normalizado en el mismo país une. Tres grafías, una repetida (N_FILAS=2)."""
    graf, un, pe = _ancla(
        [
            A("España", "Brumalia Cereales SA", "BRUMALIA CEREALES"),
            A("España", "BRUMALIA CEREALES S.A.", "BRUMALIA CEREALES"),
            A("España", "Brumalia Cereales", "BRUMALIA CEREALES"),
            A("España", "Brumalia Cereales SA", "BRUMALIA CEREALES"),
        ]
    )
    # Las grafías salen ordenadas por CLAVE (las mayúsculas van antes) y la repetida cuenta dos filas.
    assert _listas(graf[["CLAVE", "FORMA_LEGAL", "N_FILAS", "_COMP"]]) == [
        ["España|BRUMALIA CEREALES S.A.", "SA", 1, 0],
        ["España|Brumalia Cereales", "", 1, 0],
        ["España|Brumalia Cereales SA", "SA", 2, 0],
    ]
    # Los pares salen en el orden de np.triu_indices (primero, segundo; primero, tercero; segundo, tercero).
    assert _listas(un) == [
        [0, 1, "mismo nombre normalizado"],
        [0, 2, "mismo nombre normalizado"],
        [1, 2, "mismo nombre normalizado"],
    ]
    assert len(pe) == 0


def test_ancla_mismas_letras_con_otros_espacios_une_automaticamente():
    """Regla 2: KRA MENTO FOODS y KRAMENTO FOODS tienen las mismas letras."""
    _, un, pe = _ancla(
        [
            A("España", "Kra Mento Foods", "KRA MENTO FOODS"),
            A("España", "Kramento Foods", "KRAMENTO FOODS"),
        ]
    )
    assert _listas(un) == [[0, 1, "mismas letras, otros espacios"]]
    assert len(pe) == 0


def test_ancla_errata_con_score_bajo_va_a_pendientes():
    """Misma palabra con una errata pero SCORE < umbral_dedup (0,84): no une sola, lo decide una persona."""
    graf, un, pe = _ancla(
        [
            A("España", "Dromanto Vinagres Artesanales", "DROMANTO VINAGRES ARTESANALES"),
            A("España", "Dromanto Vinagres Artesanalez", "DROMANTO VINAGRES ARTESANALEZ"),
        ]
    )
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "mismas palabras (score 0.83)"]]
    assert graf["_COMP"].tolist() == [0, 1]


def test_ancla_errata_con_score_alto_une_automaticamente():
    """Regla 3: mismas palabras con una errata y SCORE ≥ umbral_dedup; bajar el umbral la activa."""
    filas = [
        A("España", "Dromanto Vinagres Artesanales", "DROMANTO VINAGRES ARTESANALES"),
        A("España", "Dromanto Vinagres Artesanalez", "DROMANTO VINAGRES ARTESANALEZ"),
    ]
    graf, un, pe = _ancla(filas, umbral_dedup=0.80)
    assert _listas(un) == [[0, 1, "mismas palabras con errata"]]
    assert len(pe) == 0
    assert graf["_COMP"].tolist() == [0, 0]


def test_ancla_truncamiento_une_automaticamente_si_el_origen_corta_el_nombre():
    """La grafía cortada por el sistema de origen (TRUNCADO) une con la completa: «con truncamiento»."""
    graf, un, pe = _ancla(
        [
            A(
                "España",
                "INDUSTRIAS ALIMENTARIAS TRIVENTOLA DEL SU",
                "INDUSTRIAS ALIMENTARIAS TRIVENTOLA DEL SU",
                "",
                True,
            ),
            A(
                "España",
                "INDUSTRIAS ALIMENTARIAS TRIVENTOLA DEL SUR",
                "INDUSTRIAS ALIMENTARIAS TRIVENTOLA DEL SUR",
            ),
        ]
    )
    assert graf["TRUNCADO"].tolist() == [True, False]
    assert _listas(un) == [[0, 1, "mismas palabras con truncamiento"]]
    assert len(pe) == 0


def test_ancla_sin_la_marca_de_truncado_el_corte_no_une():
    """Sin TRUNCADO el mismo par ya no se alinea como truncamiento: no hay unión automática."""
    _, un, _ = _ancla(
        [
            A(
                "España",
                "INDUSTRIAS ALIMENTARIAS TRIVENTOLA DEL SU",
                "INDUSTRIAS ALIMENTARIAS TRIVENTOLA DEL SU",
            ),
            A(
                "España",
                "INDUSTRIAS ALIMENTARIAS TRIVENTOLA DEL SUR",
                "INDUSTRIAS ALIMENTARIAS TRIVENTOLA DEL SUR",
            ),
        ]
    )
    assert len(un) == 0


def test_ancla_un_nombre_truncado_en_cualquiera_de_sus_filas_marca_la_grafia():
    """TRUNCADO de la grafía es el máximo de sus filas: basta una fila cortada."""
    graf, _, _ = _ancla(
        [
            A("España", "Zelvora Alimentos SA", "ZELVORA ALIMENTOS", "", False),
            A("España", "Zelvora Alimentos SA", "ZELVORA ALIMENTOS", "", True),
        ]
    )
    assert graf["TRUNCADO"].tolist() == [True]
    assert graf["N_FILAS"].tolist() == [2]


def test_ancla_forma_legal_distinta_no_une_y_va_a_pendientes_con_las_familias():
    """SL ≠ S. COOP.: mismo nombre pero personas jurídicas distintas; el motivo nombra las dos familias."""
    graf, un, pe = _ancla(
        [A("España", "Fribelia SL", "FRIBELIA"), A("España", "Fribelia S. COOP", "FRIBELIA")]
    )
    assert graf["FORMA_LEGAL"].tolist() == ["COOP", "SL"]
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "mismo nombre, forma legal distinta (COOP ≠ SL)"]]


def test_ancla_familia_desconocida_es_compatible_con_cualquiera_pero_no_puentea():
    """Un nombre sin forma legal une con UNA de las formas; unir además la otra mezclaría SL y COOP (F3.1b, g3-O1)."""
    graf, un, pe = _ancla(
        [
            A("España", "Fribelia SL", "FRIBELIA"),
            A("España", "Fribelia S. COOP", "FRIBELIA"),
            A("España", "Fribelia", "FRIBELIA"),
        ]
    )
    assert graf["FORMA_LEGAL"].tolist() == ["", "COOP", "SL"]
    # Antes (original) las dos uniones se aplicaban y las tres grafías quedaban en una empresa con SL y COOP juntas.
    assert _listas(un) == [[0, 1, "mismo nombre normalizado"]]
    assert graf["_COMP"].tolist() == [0, 0, 1]
    assert _listas(pe) == [
        [1, 2, "mismo nombre, forma legal distinta (COOP ≠ SL)"],
        [
            0,
            2,
            "mismo nombre normalizado; no se une sola: la empresa mezclaría formas legales distintas (COOP ≠ SL)",
        ],
    ]


def test_ancla_sigla_va_a_pendientes():
    """GRDL es subsecuencia de GARANDEL: «posible sigla o abreviatura», nunca automática."""
    _, un, pe = _ancla(
        [A("España", "Garandel Foods", "GARANDEL FOODS"), A("España", "GRDL", "GRDL")]
    )
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "posible sigla o abreviatura"]]


def test_ancla_alias_entre_parentesis_va_a_pendientes():
    """El nombre entre paréntesis (¿marca?) coincide con otra grafía: se propone, no se une."""
    graf, un, pe = _ancla(
        [
            A("España", "Pustorán Foods (Lamberto)", "PUSTORAN FOODS", "LAMBERTO"),
            A("España", "Lamberto", "LAMBERTO"),
        ]
    )
    assert graf["CLAVE"].tolist() == ["España|Lamberto", "España|Pustorán Foods (Lamberto)"]
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "coincide con el nombre entre paréntesis (¿marca?)"]]


def test_ancla_un_nombre_que_contiene_al_otro_con_sobrantes_genericos_va_a_pendientes():
    """GALDIMAR contra GALDIMAR HOLDING IBERIA: lo que sobra es genérico, pero puede ser otra empresa."""
    _, un, pe = _ancla(
        [
            A("España", "Galdimar", "GALDIMAR"),
            A("España", "Galdimar Holding Iberia SA", "GALDIMAR HOLDING IBERIA"),
        ]
    )
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "un nombre contiene al otro; lo que sobra es genérico"]]


def test_ancla_palabra_distintiva_compartida_con_score_suficiente_va_a_pendientes():
    """Comparten KORIMBA QUESOS y sobra una palabra no genérica; SCORE ≥ 0,75 la propone a revisión."""
    _, un, pe = _ancla(
        [
            A("España", "Korimba Quesos", "KORIMBA QUESOS"),
            A("España", "Korimba Quesos Artesanos", "KORIMBA QUESOS ARTESANOS"),
        ]
    )
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "comparten palabra distintiva (KORIMBA QUESOS)"]]


def test_ancla_palabra_compartida_con_score_bajo_no_propone_nada():
    """KORIMBA QUESOS / KORIMBA ACEITES comparten una palabra pero su SCORE no llega a 0,75."""
    graf, un, pe = _ancla(
        [A("España", "Korimba Quesos", "KORIMBA QUESOS"), A("España", "Korimba Aceites", "KORIMBA ACEITES")]
    )  # fmt: skip
    assert (len(un), len(pe)) == (0, 0)
    assert graf["_COMP"].tolist() == [0, 1]


def test_ancla_palabra_distintiva_compartida_con_score_entre_075_y_080_tambien_se_propone():
    """El piso para proponer por palabra distintiva es 0,75 (no 0,80): KORIMBA ARTESANO / KORIMBA da 0,77."""
    filas = [A("España", "Korimba Artesano", "KORIMBA ARTESANO"), A("España", "Korimba", "KORIMBA")]
    assert 0.75 <= _puntaje(filas, "KORIMBA ARTESANO", "KORIMBA") < 0.80
    _, un, pe = _ancla(filas)
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "comparten palabra distintiva (KORIMBA)"]]


def test_ancla_solo_palabras_genericas_no_propone_nada_aunque_coincidan():
    """Nombres hechos solo de palabras genéricas no comparten nada distintivo: ni unión ni pendiente."""
    for filas in (
        [A("España", "Alimentos", "ALIMENTOS"), A("España", "Bebidas", "BEBIDAS")],
        [A("España", "Alimentos Bebidas", "ALIMENTOS BEBIDAS"), A("España", "Bebidas Alimentos", "BEBIDAS ALIMENTOS")],
    ):  # fmt: skip
        graf, un, pe = _ancla(filas)
        assert (len(un), len(pe)) == (0, 0)
        assert graf["_COMP"].tolist() == [0, 1]


def test_ancla_mismas_letras_con_forma_legal_distinta_va_a_pendientes():
    """KRA MENTO FOODS SL ≈ KRAMENTO FOODS S. COOP: mismas letras, pero personas jurídicas distintas."""
    _, un, pe = _ancla(
        [
            A("España", "Kra Mento Foods SL", "KRA MENTO FOODS"),
            A("España", "Kramento Foods S. COOP", "KRAMENTO FOODS"),
        ]
    )
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "mismas letras, forma legal distinta"]]


def test_ancla_errata_con_forma_legal_distinta_no_une_aunque_el_score_alcance():
    """Con el score por encima del umbral, la forma legal distinta la deja pendiente y nombra las familias."""
    _, un, pe = _ancla(
        [
            A("España", "Dromanto Vinagres Artesanales SL", "DROMANTO VINAGRES ARTESANALES"),
            A("España", "Dromanto Vinagres Artesanalez S. COOP", "DROMANTO VINAGRES ARTESANALEZ"),
        ],
        umbral_dedup=0.70,
    )
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "mismas palabras, forma legal distinta (SL ≠ COOP)"]]


def test_ancla_marcas_distintas_impiden_la_union_por_mismas_palabras():
    """Con iniciales o números distintos (KRAMENTO FOODS 2 ≠ FOODS KRAMENTO 3) las mismas palabras solo se proponen."""
    filas = [A("España", "Kramento Foods 2", "KRAMENTO FOODS 2"), A("España", "Foods Kramento 3", "FOODS KRAMENTO 3")]  # fmt: skip
    _, un, pe = _ancla(filas, umbral_dedup=0.50)
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "mismas palabras (score 0.75)"]]


def test_ancla_truncada_une_con_score_de_075_aunque_umbral_dedup_sea_alto():
    """Un nombre cortado por el origen une con score ≥ 0,75 (sin errata) aunque ``umbral_dedup`` pida 0,95."""
    cortada = A("España", "COOPERATIVA CONSERVAS CANT", "COOPERATIVA CONSERVAS CANT", "", True)
    completa = A("España", "COOPERATIVA CONSERVAS CANTABRICO", "COOPERATIVA CONSERVAS CANTABRICO")
    assert 0.75 <= _puntaje([cortada, completa], cortada.norm, completa.norm) < 0.85
    _, un, pe = _ancla([cortada, completa], umbral_dedup=0.95)
    assert _listas(un) == [[0, 1, "mismas palabras con truncamiento"]]
    assert len(pe) == 0
    # Sin la marca de truncado el mismo par ya no es un corte: solo queda como posible abreviatura.
    _, un, pe = _ancla([cortada._replace(truncado=False), completa], umbral_dedup=0.95)
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "posible sigla o abreviatura"]]


def test_ancla_truncada_con_errata_no_usa_el_piso_de_075():
    """Si además del corte hay una errata, vuelve a regir ``umbral_dedup``: con 0,95 queda pendiente."""
    filas = [
        A("España", "INDUSTRIAS ALIMENTARIAS TRIVENTOLA DEL SU", "INDUSTRIAS ALIMENTARIAS TRIVENTOLA DEL SU", "", True),
        A("España", "INDUSTRIAS ALIMENTARIAS TRIVENTOLAS DEL SUR", "INDUSTRIAS ALIMENTARIAS TRIVENTOLAS DEL SUR"),
    ]  # fmt: skip
    _, un, pe = _ancla(filas, umbral_dedup=0.95)
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "mismas palabras (score 0.78)"]]
    _, un, pe = _ancla(filas, umbral_dedup=0.50)
    assert _listas(un) == [[0, 1, "mismas palabras con truncamiento"]]


def test_ancla_el_nombre_contra_nombre_gana_a_los_pares_con_alias():
    """Un par de grafías puede compararse por varias superficies; manda la primera: nombre contra nombre."""
    _, un, pe = _ancla(
        [
            A(
                "España",
                "Korimba Quesos Artesanos (Korimba)",
                "KORIMBA QUESOS ARTESANOS",
                "KORIMBA",
            ),
            A("España", "Korimba Quesos", "KORIMBA QUESOS"),
        ]
    )
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "comparten palabra distintiva (KORIMBA QUESOS)"]]


def test_ancla_alias_con_las_mismas_letras_que_otra_grafia_va_a_pendientes():
    """El alias KLAMENTO FOODS tiene las mismas letras que KLAMENTOFOODS: pendiente, nunca unión automática."""
    _, un, pe = _ancla(
        [
            A("España", "Pustorán (Klamento Foods)", "PUSTORAN", "KLAMENTO FOODS"),
            A("España", "Klamentofoods", "KLAMENTOFOODS"),
        ]
    )
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "coincide con el nombre entre paréntesis (¿marca?)"]]


def test_ancla_alias_con_las_mismas_palabras_en_otro_orden_va_a_pendientes():
    """El alias KORIMBA QUESOS y el nombre QUESOS KORIMBA coinciden sin sobrantes: pendiente."""
    _, un, pe = _ancla(
        [
            A("España", "Pustorán (Korimba Quesos)", "PUSTORAN", "KORIMBA QUESOS"),
            A("España", "Quesos Korimba", "QUESOS KORIMBA"),
        ]
    )
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "coincide con el nombre entre paréntesis (¿marca?)"]]


def test_ancla_los_paises_se_recorren_en_el_orden_de_las_grafias():
    """Las grafías van ordenadas por clave (España antes que Suiza) y los países se recorren en ese orden."""
    graf, un, _ = _ancla(
        [
            A("Suiza", "Zelvora Alimentos SA", "ZELVORA ALIMENTOS"),
            A("Suiza", "Zelvora Alimentos", "ZELVORA ALIMENTOS"),
            A("España", "Kramento Foods SA", "KRAMENTO FOODS"),
            A("España", "Kramento Foods", "KRAMENTO FOODS"),
        ]
    )
    assert graf["PAIS_ISO3"].tolist() == ["ESP", "ESP", "CHE", "CHE"]
    assert _listas(un) == [[0, 1, "mismo nombre normalizado"], [2, 3, "mismo nombre normalizado"]]


def test_ancla_grafias_sin_nombre_normalizado_no_se_unen_entre_si():
    """S.L. y S.A. se normalizan a vacío: no hay nombre que comparar, así que no se unen ni se proponen."""
    graf, un, pe = _ancla([A("España", "S.L.", ""), A("España", "S.A.", "")])
    assert graf["FORMA_LEGAL"].tolist() == ["SA", "SL"]
    assert (len(un), len(pe)) == (0, 0)


def test_ancla_sin_palabras_utiles_solo_se_une_el_nombre_normalizado_identico():
    """Un nombre sin palabras útiles (solo números) se une únicamente si el normalizado es idéntico."""
    _, un, _ = _ancla([A("España", "2000 SA", "2000"), A("España", "2000", "2000")])
    assert _listas(un) == [[0, 1, "mismo nombre normalizado"]]
    _, un, pe = _ancla([A("España", "2000", "2000"), A("España", "2001", "2001")])
    assert (len(un), len(pe)) == (0, 0)
    # Basta que una de las dos grafías no tenga palabras útiles: 2000 ≈ 2000A (score 0,72) no se propone
    # aunque se baje ``umbral_parecido``.
    _, un, pe = _ancla(
        [A("España", "2000", "2000"), A("España", "2000A", "2000A")], umbral_parecido=0.5
    )
    assert (len(un), len(pe)) == (0, 0)


def test_ancla_los_pendientes_que_quedan_se_numeran_desde_cero(tmp_path):
    """Tras descartar los vetados, el índice de ``pendientes`` se reinicia (0, 1, …)."""
    filas = [
        A("España", "Fribelia SL", "FRIBELIA"),
        A("España", "Fribelia S. COOP", "FRIBELIA"),
        A("España", "Fribelia SA", "FRIBELIA"),
    ]
    assert _ancla(filas)[2].index.tolist() == [0, 1, 2]
    dec = _leer(
        tmp_path, [(DUP, EXP, "España|Fribelia S. COOP", "España|Fribelia SA", "DISTINTA", "x")]
    )
    _, _, pe = _ancla(filas, dec)
    assert pe.index.tolist() == [0, 1]
    assert _listas(pe) == [
        [0, 2, "mismo nombre, forma legal distinta (COOP ≠ SL)"],
        [1, 2, "mismo nombre, forma legal distinta (SA ≠ SL)"],
    ]


def _decisiones_a_mano(filas):
    """Tabla de decisiones como la deja ``leer_decisiones``, armada fila a fila: ``(DECISION, ORDEN, AUTOR)``."""
    par = ("España|Sementa Pastas", "España|Sementa Pastas SA")
    return pd.DataFrame(
        [
            {"TIPO": DUP, "FUENTE": EXP, "CLAVE_A": par[0], "NOMBRE_A": "", "CLAVE_B": par[1], "NOMBRE_B": "",
             "DECISION": d, "AUTOR": autor, "FECHA": "", "RAZON": "", "ARCHIVO": "x.csv", "ORDEN": orden}
            for d, orden, autor in filas
        ]
    )  # fmt: skip


SEMENTA = [
    A("España", "Sementa Pastas", "SEMENTA PASTAS"),
    A("España", "Sementa Pastas SA", "SEMENTA PASTAS"),
]


def test_ancla_ordena_las_decisiones_por_archivo_aunque_lleguen_desordenadas():
    """Las decisiones se aplican por ORDEN de archivo (el último manda), llegue como llegue la tabla."""
    dec = _decisiones_a_mano([("MISMA_ENTIDAD", 1, "Dos"), ("DISTINTA", 0, "Uno")])
    graf, un, _ = _ancla(SEMENTA, dec)
    assert _listas(un) == [[0, 1, "mismo nombre normalizado"], [0, 1, "decisión: Dos"]]
    assert graf.attrs["vetos"] == set()
    veto = _decisiones_a_mano([("DISTINTA", 1, "Dos"), ("MISMA_ENTIDAD", 0, "Uno")])
    assert _ancla(SEMENTA, veto)[0].attrs["vetos"] == {(0, 1)}


def test_ancla_el_orden_dentro_de_un_archivo_se_conserva_al_ordenar_por_archivo():
    """La ordenación por archivo es estable: filas del mismo ORDEN conservan su orden relativo."""
    filas = [
        ("MISMA_ENTIDAD" if (k // 2) % 2 == 1 else "DISTINTA", k % 2, f"a{k}") for k in range(40)
    ]
    _, un, _ = _ancla(SEMENTA, _decisiones_a_mano(filas))
    esperado = [
        f"decisión: a{k}" for orden in (0, 1) for k in range(orden, 40, 2) if (k // 2) % 2 == 1
    ]
    assert un["REGLA"].tolist() == ["mismo nombre normalizado", *esperado]


def test_ancla_nombres_muy_parecidos_sin_palabra_compartida_exigen_umbral_parecido():
    """Sin palabra alineada, solo el parecido de cadena ≥ umbral_parecido propone el par (por defecto 0,88)."""
    filas = [A("España", "Mirvana", "MIRVANA"), A("España", "Mirvano", "MIRVANO")]
    assert len(_ancla(filas)[2]) == 0
    _, un, pe = _ancla(filas, umbral_parecido=0.70)
    assert len(un) == 0
    assert _listas(pe) == [[0, 1, "nombres muy parecidos (score 0.71)"]]
    assert len(_ancla(filas, umbral_parecido=0.72)[2]) == 0


def test_ancla_los_umbrales_son_inclusivos():
    """Un SCORE exactamente igual al umbral cuenta: ``umbral_dedup`` une y ``umbral_parecido`` propone."""
    errata = [
        A("España", "Dromanto Vinagres Artesanales", "DROMANTO VINAGRES ARTESANALES"),
        A("España", "Dromanto Vinagres Artesanalez", "DROMANTO VINAGRES ARTESANALEZ"),
    ]
    sc = _puntaje(errata, errata[0].norm, errata[1].norm)
    assert sc == 0.8313
    assert _listas(_ancla(errata, umbral_dedup=sc)[1]) == [[0, 1, "mismas palabras con errata"]]
    assert len(_ancla(errata, umbral_dedup=round(sc + 0.0001, 4))[1]) == 0
    parecidos = [A("España", "Mirvana", "MIRVANA"), A("España", "Mirvano", "MIRVANO")]
    sp = _puntaje(parecidos, "MIRVANA", "MIRVANO")
    assert sp == 0.7071
    assert len(_ancla(parecidos, umbral_parecido=sp)[2]) == 1
    assert len(_ancla(parecidos, umbral_parecido=round(sp + 0.0001, 4))[2]) == 0


def test_ancla_paises_distintos_nunca_se_comparan():
    """Se compara dentro de cada país (ISO3): el mismo nombre en Alemania y Francia son dos empresas."""
    graf, un, pe = _ancla(
        [
            A("Alemania", "Holvexa Foods GmbH", "HOLVEXA FOODS"),
            A("Francia", "Holvexa Foods", "HOLVEXA FOODS"),
        ]
    )
    assert graf["PAIS_ISO3"].tolist() == ["DEU", "FRA"]
    assert (len(un), len(pe)) == (0, 0)
    assert graf["_COMP"].tolist() == [0, 1]


def test_ancla_grafia_unica_y_tabla_vacia():
    """Una sola grafía, o ninguna, no tiene con quién compararse y no falla."""
    for filas in ([A("España", "Zelvora Alimentos SA", "ZELVORA ALIMENTOS")], []):
        graf, un, pe = _ancla(filas)
        assert len(graf) == len(filas)
        assert (len(un), len(pe)) == (0, 0)
        assert graf.attrs["vetos"] == set()


def test_ancla_decision_misma_entidad_une_lo_que_las_reglas_no_unen(tmp_path):
    """TIPO=DUPLICADO / MISMA_ENTIDAD agrega la unión «decisión: autor» y retira el pendiente equivalente."""
    filas = [A("España", "Cravinto Salsas", "CRAVINTO SALSAS"), A("España", "Cravinto Foods", "CRAVINTO FOODS")]  # fmt: skip
    _, _, sin = _ancla(filas)
    assert _listas(sin) == [[0, 1, "un nombre contiene al otro; lo que sobra es genérico"]]
    dec = _leer(
        tmp_path,
        [(DUP, EXP, "España|Cravinto Salsas", "España|Cravinto Foods", "MISMA_ENTIDAD", "(verificar) paneles")],
    )  # fmt: skip
    graf, un, pe = _ancla(filas, dec)
    assert _listas(un) == [[0, 1, "decisión: (verificar) paneles"]]
    assert len(pe) == 0
    assert graf["_COMP"].tolist() == [0, 0]


def test_ancla_decision_sin_autor_usa_el_nombre_del_archivo(tmp_path):
    """Si AUTOR viene vacío, la regla de la unión nombra el archivo de decisiones."""
    filas = [A("España", "Cravinto Salsas", "CRAVINTO SALSAS"), A("España", "Cravinto Foods", "CRAVINTO FOODS")]  # fmt: skip
    dec = _leer(
        tmp_path,
        [(DUP, EXP, "España|Cravinto Foods", "España|Cravinto Salsas", "MISMA_ENTIDAD", "")],
    )
    _, un, _ = _ancla(filas, dec)
    assert _listas(un) == [[0, 1, "decisión: decisiones_1.csv"]]


def test_ancla_decision_distinta_veta_la_union_automatica(tmp_path):
    """DISTINTA es un veto: el par no se une aunque las reglas lo unan, y queda en ``attrs['vetos']``."""
    filas = [A("España", "Sementa Pastas", "SEMENTA PASTAS"), A("España", "Sementa Pastas SA", "SEMENTA PASTAS")]  # fmt: skip
    assert _listas(_ancla(filas)[1]) == [[0, 1, "mismo nombre normalizado"]]
    dec = _leer(tmp_path, [(DUP, EXP, "España|Sementa Pastas", "España|Sementa Pastas SA", "DISTINTA", "Revisora Uno")])  # fmt: skip
    graf, un, pe = _ancla(filas, dec)
    assert (len(un), len(pe)) == (0, 0)
    assert graf["_COMP"].tolist() == [0, 1]
    assert graf.attrs["vetos"] == {(0, 1)}
    assert graf.attrs["desconocidas"] == []


def test_ancla_decision_distinta_retira_tambien_el_pendiente(tmp_path):
    """Un par pendiente al que una persona dijo DISTINTA deja de proponerse."""
    filas = [A("España", "Fribelia SL", "FRIBELIA"), A("España", "Fribelia S. COOP", "FRIBELIA")]
    assert len(_ancla(filas)[2]) == 1
    dec = _leer(
        tmp_path, [(DUP, EXP, "España|Fribelia S. COOP", "España|Fribelia SL", "DISTINTA", "x")]
    )
    graf, _, pe = _ancla(filas, dec)
    assert len(pe) == 0
    assert graf.attrs["vetos"] == {(0, 1)}


def test_ancla_decision_misma_entidad_sobre_un_pendiente_lo_convierte_en_union(tmp_path):
    """La forma legal distinta no impide que una persona decida que es la misma entidad."""
    filas = [A("España", "Fribelia SL", "FRIBELIA"), A("España", "Fribelia S. COOP", "FRIBELIA")]
    dec = _leer(
        tmp_path,
        [(DUP, EXP, "España|Fribelia S. COOP", "España|Fribelia SL", "MISMA_ENTIDAD", "x")],
    )
    graf, un, pe = _ancla(filas, dec)
    assert _listas(un) == [[0, 1, "decisión: x"]]
    assert len(pe) == 0
    assert graf["_COMP"].tolist() == [0, 0]


def test_ancla_el_ultimo_archivo_manda_distinta_y_luego_misma_levanta_el_veto(tmp_path):
    """DISTINTA en el primer archivo y MISMA_ENTIDAD en el segundo: gana la última, el veto se levanta."""
    filas = [A("España", "Sementa Pastas", "SEMENTA PASTAS"), A("España", "Sementa Pastas SA", "SEMENTA PASTAS")]  # fmt: skip
    par = ("España|Sementa Pastas", "España|Sementa Pastas SA")
    dec = _leer(tmp_path, [(DUP, EXP, *par, "DISTINTA", "Revisora Uno")], [(DUP, EXP, *par, "MISMA_ENTIDAD", "Revisor Dos")])  # fmt: skip
    graf, un, _ = _ancla(filas, dec)
    # La unión automática sigue (ya no está vetada) y la decisión queda como segunda razón del mismo par.
    assert _listas(un) == [[0, 1, "mismo nombre normalizado"], [0, 1, "decisión: Revisor Dos"]]
    assert graf.attrs["vetos"] == set()
    assert graf["_COMP"].tolist() == [0, 0]


def test_ancla_el_ultimo_archivo_manda_misma_y_luego_distinta_pone_el_veto(tmp_path):
    """MISMA_ENTIDAD y luego DISTINTA: el veto quita TODAS las uniones del par, también la automática."""
    filas = [A("España", "Sementa Pastas", "SEMENTA PASTAS"), A("España", "Sementa Pastas SA", "SEMENTA PASTAS")]  # fmt: skip
    par = ("España|Sementa Pastas", "España|Sementa Pastas SA")
    dec = _leer(tmp_path, [(DUP, EXP, *par, "MISMA_ENTIDAD", "Revisora Uno")], [(DUP, EXP, *par, "DISTINTA", "Revisor Dos")])  # fmt: skip
    graf, un, _ = _ancla(filas, dec)
    assert len(un) == 0
    assert graf.attrs["vetos"] == {(0, 1)}
    assert graf["_COMP"].tolist() == [0, 1]


def test_ancla_claves_desconocidas_se_reportan_en_attrs_y_no_se_aplican(tmp_path):
    """Una clave que no es una grafía de la ancla no se aplica: se guarda en ``attrs['desconocidas']``."""
    filas = [A("España", "Sementa Pastas", "SEMENTA PASTAS"), A("España", "Sementa Pastas SA", "SEMENTA PASTAS")]  # fmt: skip
    dec = _leer(
        tmp_path,
        [
            (DUP, EXP, "España|NO EXISTE", "España|Sementa Pastas SA", "MISMA_ENTIDAD", "x"),
            (DUP, EXP, "España|Sementa Pastas", "España|OTRA", "DISTINTA", "y"),
        ],
    )  # fmt: skip
    graf, un, _ = _ancla(filas, dec)
    assert graf.attrs["desconocidas"] == [
        ("España|NO EXISTE", "España|Sementa Pastas SA"),
        ("España|Sementa Pastas", "España|OTRA"),
    ]
    assert graf.attrs["vetos"] == set()
    assert _listas(un) == [[0, 1, "mismo nombre normalizado"]]


def test_ancla_ignora_decisiones_de_otra_fuente_y_de_otro_tipo(tmp_path):
    """Solo cuentan las decisiones TIPO=DUPLICADO cuya FUENTE es la clave de la ancla."""
    filas = [A("España", "Sementa Pastas", "SEMENTA PASTAS"), A("España", "Sementa Pastas SA", "SEMENTA PASTAS")]  # fmt: skip
    par = ("España|Sementa Pastas", "España|Sementa Pastas SA")
    dec = _leer(
        tmp_path,
        [(DUP, "ORBIS", *par, "DISTINTA", "x"), ("VINCULO", EXP, *par, "NO_VINCULAR", "y")],
    )
    graf, un, _ = _ancla(filas, dec)
    assert _listas(un) == [[0, 1, "mismo nombre normalizado"]]
    assert graf.attrs["vetos"] == set() and graf.attrs["desconocidas"] == []


def test_ancla_las_claves_de_las_decisiones_toleran_espacios_de_mas(tmp_path):
    """La clave escrita con espacios raros (Excel) se normaliza al leer y encuentra su grafía."""
    filas = [A("España", "Cravinto Salsas", "CRAVINTO SALSAS"), A("España", "Cravinto Foods", "CRAVINTO FOODS")]  # fmt: skip
    dec = _leer(tmp_path, [(DUP, EXP, "España|Cravinto   Salsas ", " España|Cravinto Foods", "MISMA_ENTIDAD", "uno")])  # fmt: skip
    graf, un, _ = _ancla(filas, dec)
    assert _listas(un) == [[0, 1, "decisión: uno"]]
    assert graf.attrs["desconocidas"] == []


def test_ancla_usa_la_clave_de_la_fuente_ancla_de_la_configuracion(tmp_path):
    """La FUENTE de la decisión se compara con ``cfg.ancla.clave``: con otra clave deja de aplicarse."""
    filas = [A("España", "Sementa Pastas", "SEMENTA PASTAS"), A("España", "Sementa Pastas SA", "SEMENTA PASTAS")]  # fmt: skip
    dec = _leer(tmp_path, [(DUP, "OTRA_BASE", "España|Sementa Pastas", "España|Sementa Pastas SA", "DISTINTA", "x")])  # fmt: skip
    assert _ancla(filas, dec)[0].attrs["vetos"] == set()
    assert _ancla(filas, dec, clave_ancla="OTRA_BASE")[0].attrs["vetos"] == {(0, 1)}


def test_ancla_el_umbral_de_dedup_viene_de_la_configuracion():
    """``umbral_dedup`` decide entre unión automática y pendiente para la misma errata."""
    filas = [
        A("España", "Dromanto Vinagres Artesanales", "DROMANTO VINAGRES ARTESANALES"),
        A("España", "Dromanto Vinagres Artesanalez", "DROMANTO VINAGRES ARTESANALEZ"),
    ]
    assert (len(_ancla(filas, umbral_dedup=0.80)[1]), len(_ancla(filas, umbral_dedup=0.90)[1])) == (
        1,
        0,
    )


# ── deduplicar_destino ────────────────────────────────────────────────────────

ORBIS = Fuente(
    clave="ORBIS", archivo="x.xlsx", col_nombre="Nombre", col_id="BvD ID", prefijo_id="ORB"
)
FDI = Fuente(clave="FDI", archivo="x.xlsx", col_nombre="Nombre", prefijo_id="FDI")


def _destino(fuente, filas, dec=None):
    """``deduplicar_destino`` con el doble de normalizador."""
    dec = leer_decisiones([]) if dec is None else dec
    return deduplicar_destino(prep_destino(filas), fuente, NormalizadorDoble(), dec)


def _entidades(out):
    """``(ID_REGISTRO, ID_ENTIDAD, N_EN_ENTIDAD, FORMA_LEGAL)`` de cada fila."""
    return _listas(out[["ID_REGISTRO", "ID_ENTIDAD", "N_EN_ENTIDAD", "FORMA_LEGAL"]])


def test_destino_con_identificador_une_por_la_raiz_del_id():
    """Con ``col_id`` solo cuenta la raíz del ID: sucursal (-1003) y SIRET→SIREN comparten entidad."""
    out, un = _destino(
        ORBIS,
        [
            D("ESB0000001", "Triventa Bebidas SA", "TRIVENTA BEBIDAS"),
            D("ESB0000001-1003", "Triventa Bebidas SA - Sucursal", "TRIVENTA BEBIDAS"),
            D("ESB0000002", "Triventa Bebidas SA", "TRIVENTA BEBIDAS"),
            D("FR12345678900012", "Brandelia Fromages SAS", "BRANDELIA FROMAGES", "FRA"),
            D("FR12345678900034", "Brandelia Fromages SAS", "BRANDELIA FROMAGES", "FRA"),
            D("FR99999999900012", "Brandelia Fromages SAS", "BRANDELIA FROMAGES", "FRA"),
        ],
    )
    assert _entidades(out) == [
        ["ESB0000001", "ORB-E0001", 2, "SA"],
        ["ESB0000001-1003", "ORB-E0001", 2, "SA"],
        ["ESB0000002", "ORB-E0002", 1, "SA"],
        ["FR12345678900012", "ORB-E0003", 2, "SAS"],
        ["FR12345678900034", "ORB-E0003", 2, "SAS"],
        ["FR99999999900012", "ORB-E0004", 1, "SAS"],
    ]
    assert _listas(un) == [
        [0, 1, "mismo identificador raíz"],
        [3, 4, "mismo identificador raíz"],
    ]


def test_destino_con_identificador_ignora_el_nombre():
    """El mismo nombre con otro ID NO une: con identificador manda el ID, nunca el nombre."""
    out, un = _destino(
        ORBIS,
        [D("ESB0000001", "Triventa SA", "TRIVENTA"), D("ESB0000002", "Triventa SA", "TRIVENTA")],
    )
    assert out["ID_ENTIDAD"].tolist() == ["ORB-E0001", "ORB-E0002"]
    assert len(un) == 0


def test_destino_las_uniones_salen_por_raiz_ordenada_y_las_entidades_por_primera_aparicion():
    """Las uniones siguen el orden alfabético de las raíces; ID_ENTIDAD se numera por primera aparición."""
    out, un = _destino(
        ORBIS,
        [
            D("ESB0000001", "Quelmar SL", "QUELMAR"),
            D("DE0000000001-1003", "Holvexa GmbH", "HOLVEXA", "DEU"),
            D("DE0000000001", "Holvexa GmbH Zentrale", "HOLVEXA ZENTRALE", "DEU"),
            D("ESB0000001-7", "Quelmar SL", "QUELMAR"),
        ],
    )
    assert _listas(un) == [[1, 2, "mismo identificador raíz"], [0, 3, "mismo identificador raíz"]]
    assert out["ID_ENTIDAD"].tolist() == ["ORB-E0001", "ORB-E0002", "ORB-E0002", "ORB-E0001"]


def test_destino_sin_identificador_une_el_mismo_nombre_exacto_en_los_mismos_paises():
    """Sin ``col_id``: la misma cadena (salvo mayúsculas y espacios) y los mismos países de evidencia."""
    out, un = _destino(
        FDI,
        [
            D("FDI-F0002", "Nolmendo Cervezas", "NOLMENDO CERVEZAS", "ESP"),
            D("FDI-F0003", "NOLMENDO  CERVEZAS", "NOLMENDO CERVEZAS", "ESP"),
            D("FDI-F0004", "nolmendo cervezas", "NOLMENDO CERVEZAS", "ESP"),
            D("FDI-F0005", "Nolmendo Cervezas", "NOLMENDO CERVEZAS", "DEU"),
            D("FDI-F0006", "Zelvora", "ZELVORA", "ESP|FRA"),
            D("FDI-F0007", "Zelvora", "ZELVORA", "ESP|FRA"),
        ],
    )
    assert out["ID_ENTIDAD"].tolist() == [
        "FDI-E0001",
        "FDI-E0001",
        "FDI-E0001",
        "FDI-E0002",
        "FDI-E0003",
        "FDI-E0003",
    ]
    assert _listas(un) == [
        [0, 1, "mismo nombre exacto y mismo país"],
        [0, 2, "mismo nombre exacto y mismo país"],
        [4, 5, "mismo nombre exacto y mismo país"],
    ]


def test_destino_sin_identificador_otra_cadena_no_une_aunque_sea_matriz_y_filial():
    """«Nolmendo Cervezas» y «Nolmendo Cervezas Inc» (o «SA» y «S.A.») son matriz y filial: no se unen."""
    out, un = _destino(
        FDI,
        [
            D("FDI-F0002", "Nolmendo Cervezas", "NOLMENDO CERVEZAS", "ESP"),
            D("FDI-F0003", "Nolmendo Cervezas Inc", "NOLMENDO CERVEZAS", "ESP"),
            D("FDI-F0004", "Triventa SA", "TRIVENTA", "ESP"),
            D("FDI-F0005", "Triventa S.A.", "TRIVENTA", "ESP"),
            D("FDI-F0006", "TRIVENTA SA", "TRIVENTA", "ESP"),
        ],
    )
    assert out["ID_ENTIDAD"].tolist() == [
        "FDI-E0001",
        "FDI-E0002",
        "FDI-E0003",
        "FDI-E0004",
        "FDI-E0003",
    ]
    assert out["FORMA_LEGAL"].tolist() == ["", "INC", "SA", "SA", "SA"]
    assert _listas(un) == [[2, 4, "mismo nombre exacto y mismo país"]]


def test_destino_sin_identificador_no_une_nombres_vacios_ni_sin_palabras_utiles():
    """Un nombre vacío, o cuya versión normalizada no tiene palabras útiles (solo números), no se une."""
    out, un = _destino(
        FDI,
        [
            D("FDI-F0002", "", "", "ESP"),
            D("FDI-F0003", "", "", "ESP"),
            D("FDI-F0004", "S.L.", "2000", "ESP"),
            D("FDI-F0005", "S.L.", "2000", "ESP"),
            D("FDI-F0006", "SA", "", "ESP"),
            D("FDI-F0007", "SA", "", "ESP"),
        ],
    )
    assert out["ID_ENTIDAD"].tolist() == [f"FDI-E{k:04d}" for k in range(1, 7)]
    assert len(un) == 0


def test_destino_agrega_las_tres_columnas_en_orden_y_no_modifica_la_entrada():
    """La salida es ``prep`` más ID_ENTIDAD, N_EN_ENTIDAD y FORMA_LEGAL; ``prep`` queda intacta."""
    prep = prep_destino(
        [D("ESB0000001", "Quelmar SL", "QUELMAR"), D("ESB0000001-1", "Quelmar SL", "QUELMAR")]
    )
    columnas_antes = list(prep.columns)
    out, un = deduplicar_destino(prep, ORBIS, NormalizadorDoble(), leer_decisiones([]))
    assert list(out.columns) == [*columnas_antes, "ID_ENTIDAD", "N_EN_ENTIDAD", "FORMA_LEGAL"]
    assert list(prep.columns) == columnas_antes
    assert list(un.columns) == ["pos_a", "pos_b", "REGLA"]
    assert out["N_EN_ENTIDAD"].tolist() == [2, 2]


def test_destino_el_prefijo_de_la_fuente_forma_el_id_de_entidad():
    """ID_ENTIDAD = ``<prefijo_id>-E<n:04d>``; el prefijo es el de la ``Fuente``."""
    otra = Fuente(clave="REGISTRO", archivo="x.xlsx", col_nombre="N", col_id="ID", prefijo_id="REG")
    out, _ = _destino(otra, [D("ESB0000001", "Quelmar SL", "QUELMAR")])
    assert out["ID_ENTIDAD"].tolist() == ["REG-E0001"]


def test_destino_sin_filas_y_con_una_fila():
    """Tablas mínimas: vacía y de una fila no fallan y siguen el mismo contrato."""
    out, un = _destino(ORBIS, [])
    assert (len(out), len(un)) == (0, 0)
    assert list(out.columns)[-3:] == ["ID_ENTIDAD", "N_EN_ENTIDAD", "FORMA_LEGAL"]
    out, un = _destino(ORBIS, [D("ESB0000001", "Quelmar SL", "QUELMAR")])
    assert _entidades(out) == [["ESB0000001", "ORB-E0001", 1, "SL"]]
    assert len(un) == 0


def test_destino_decision_misma_entidad_une_por_id_de_registro(tmp_path):
    """Las claves de una decisión de destino son ``ID_REGISTRO``; MISMA_ENTIDAD une aunque el ID sea otro."""
    filas = [
        D("ESB0000001", "Quelmar SL", "QUELMAR"),
        D("ESB0000002", "Quelmar Lacteos SL", "QUELMAR LACTEOS"),
        D("ESB0000004", "Triventa SA", "TRIVENTA"),
        D("ESB0000004-1", "Triventa SA", "TRIVENTA"),
    ]
    dec = _leer(
        tmp_path, [(DUP, "ORBIS", "ESB0000002", "ESB0000001", "MISMA_ENTIDAD", "Revisora Uno")]
    )
    out, un = _destino(ORBIS, filas, dec)
    assert _listas(un) == [[2, 3, "mismo identificador raíz"], [0, 1, "decisión: Revisora Uno"]]
    assert out["ID_ENTIDAD"].tolist() == ["ORB-E0001", "ORB-E0001", "ORB-E0002", "ORB-E0002"]
    sin_autor = _leer(tmp_path, [(DUP, "ORBIS", "ESB0000001", "ESB0000002", "MISMA_ENTIDAD", "")])
    assert _listas(_destino(ORBIS, filas, sin_autor)[1])[-1] == [0, 1, "decisión: decisiones_1.csv"]


def test_destino_decision_distinta_veta_la_union_por_raiz(tmp_path):
    """Un DISTINTA entre un registro y su sucursal impide que se unan por el identificador."""
    filas = [
        D("ESB0000004", "Triventa SA", "TRIVENTA"),
        D("ESB0000004-1", "Triventa SA", "TRIVENTA"),
    ]
    dec = _leer(
        tmp_path, [(DUP, "ORBIS", "ESB0000004", "ESB0000004-1", "DISTINTA", "Revisora Uno")]
    )
    out, un = _destino(ORBIS, filas, dec)
    assert len(un) == 0
    assert out["ID_ENTIDAD"].tolist() == ["ORB-E0001", "ORB-E0002"]


def test_destino_ignora_claves_desconocidas_decisiones_de_otra_fuente_y_de_otro_tipo(tmp_path):
    """Lo que no es un ID_REGISTRO del destino, una FUENTE distinta o un TIPO distinto no se aplica."""
    filas = [
        D("ESB0000001", "Quelmar SL", "QUELMAR"),
        D("ESB0000002", "Quelmar Lacteos SL", "QUELMAR LACTEOS"),
    ]
    dec = _leer(
        tmp_path,
        [
            (DUP, "ORBIS", "ESB9999999", "ESB0000002", "MISMA_ENTIDAD", "x"),
            (DUP, "FDI", "ESB0000001", "ESB0000002", "MISMA_ENTIDAD", "y"),
            ("VINCULO", "ORBIS", "ESB0000001", "ESB0000002", "EMPRESA", "z"),
        ],
    )  # fmt: skip
    out, un = _destino(ORBIS, filas, dec)
    assert len(un) == 0
    assert out["ID_ENTIDAD"].tolist() == ["ORB-E0001", "ORB-E0002"]


def test_destino_las_decisiones_se_filtran_por_la_clave_de_la_fuente_del_destino(tmp_path):
    """La FUENTE de la decisión debe ser la clave del destino que se deduplica (ORBIS ≠ FDI)."""
    filas = [
        D("FDI-F0002", "Quelmar", "QUELMAR"),
        D("FDI-F0003", "Quelmar Lacteos", "QUELMAR LACTEOS"),
    ]
    dec = _leer(tmp_path, [(DUP, "FDI", "FDI-F0002", "FDI-F0003", "MISMA_ENTIDAD", "uno")])
    out, un = _destino(FDI, filas, dec)
    assert _listas(un) == [[0, 1, "decisión: uno"]]
    assert out["ID_ENTIDAD"].tolist() == ["FDI-E0001", "FDI-E0001"]
    otra = Fuente(clave="OTRA", archivo="x.xlsx", col_nombre="Nombre", prefijo_id="OTR")
    out, un = _destino(otra, filas, dec)  # la misma decisión no aplica a otro destino
    assert len(un) == 0
    assert out["ID_ENTIDAD"].tolist() == ["OTR-E0001", "OTR-E0002"]


# ── F3.1b (B2) · las decisiones del destino se tratan igual que las de la ancla ───────────────────
def _sucursales():
    return [
        D("ESB0000004", "Triventa SA", "TRIVENTA"),
        D("ESB0000004-1", "Triventa SA", "TRIVENTA"),
    ]


def test_destino_la_ultima_decision_manda_misma_despues_de_distinta_une(tmp_path):
    """B2: DISTINTA (archivo 1) y luego MISMA_ENTIDAD (archivo 2) sobre el mismo par: manda la última, como en la ancla.

    Antes el veto quedaba en pie y la decisión humana más reciente se descartaba sin aviso.
    """
    dec = _leer(
        tmp_path,
        [(DUP, "ORBIS", "ESB0000004", "ESB0000004-1", "DISTINTA", "Uno")],
        [(DUP, "ORBIS", "ESB0000004", "ESB0000004-1", "MISMA_ENTIDAD", "Dos")],
    )
    out, un = _destino(ORBIS, _sucursales(), dec)
    assert out["ID_ENTIDAD"].tolist() == ["ORB-E0001", "ORB-E0001"]
    assert ["decisión: Dos"] in [[r] for r in un["REGLA"]]


def test_destino_la_ultima_decision_manda_distinta_despues_de_misma_veta(tmp_path):
    dec = _leer(
        tmp_path,
        [(DUP, "ORBIS", "ESB0000004", "ESB0000004-1", "MISMA_ENTIDAD", "Uno")],
        [(DUP, "ORBIS", "ESB0000004", "ESB0000004-1", "DISTINTA", "Dos")],
    )
    out, un = _destino(ORBIS, _sucursales(), dec)
    assert out["ID_ENTIDAD"].tolist() == ["ORB-E0001", "ORB-E0002"]
    assert len(un) == 0


def test_destino_deja_en_attrs_las_decisiones_que_no_pudo_aplicar(tmp_path):
    """B2: las claves que no son un ID_REGISTRO del destino y las de un registro contra sí mismo se anotan en attrs."""
    dec = _leer(
        tmp_path,
        [
            (DUP, "ORBIS", "ESB9999999", "ESB0000004", "MISMA_ENTIDAD", "x"),
            (DUP, "ORBIS", "ESB0000004", "ESB0000004", "DISTINTA", "y"),
        ],
    )
    out, _ = _destino(ORBIS, _sucursales(), dec)
    assert out.attrs["desconocidas"] == [("ESB9999999", "ESB0000004")]
    assert out.attrs["autopares"] == [("ESB0000004", "ESB0000004")]
    limpio, _ = _destino(ORBIS, _sucursales())
    assert (limpio.attrs["desconocidas"], limpio.attrs["autopares"]) == ([], [])
    assert _avisos_de_decisiones_de_un_destino("ORBIS", out) == [
        "ORBIS: decisión DUPLICADO con registro inexistente ESB9999999 ↔ ESB0000004",
        "ORBIS: decisión DUPLICADO de un registro contra sí mismo (ESB0000004); se ignora",
    ]


# ── F3.1b · uniones con restricciones: puentes, vetos y autopares ───────────────────────────────────
def _comp_por_nombre(graf):
    return dict(zip(graf["NOMBRE_ORIGINAL"], graf["_COMP"], strict=True))


def test_ancla_una_grafia_sin_forma_legal_no_puentea_dos_formas_distintas():
    """F3.1b (g3-O1): 'Zelvora' une a 'Zelvora S. Coop.' pero no puede unir además a 'Zelvora SL' (SL ≠ COOP)."""
    filas = [
        A("España", "Zelvora", "ZELVORA"),
        A("España", "Zelvora S. Coop.", "ZELVORA"),
        A("España", "Zelvora SL", "ZELVORA"),
    ]
    graf, un, pe = _ancla(filas)
    comp = _comp_por_nombre(graf)
    assert comp["Zelvora S. Coop."] != comp["Zelvora SL"]
    assert graf["_COMP"].nunique() == 2
    assert _listas(un) == [[0, 1, "mismo nombre normalizado"]]
    motivos = {(a, b): m for a, b, m in _listas(pe)}
    assert motivos[(0, 2)] == (
        "mismo nombre normalizado; no se une sola: la empresa mezclaría formas legales distintas (COOP ≠ SL)"
    )
    assert motivos[(1, 2)] == "mismo nombre, forma legal distinta (COOP ≠ SL)"


def test_ancla_una_decision_distinta_se_respeta_aunque_haya_un_puente(tmp_path):
    """F3.1b (g3-O2): DISTINTA entre 'SL' y 'S.L.' no se anula porque 'Zelvora' una a los dos."""
    filas = [
        A("España", "Zelvora", "ZELVORA"),
        A("España", "Zelvora SL", "ZELVORA"),
        A("España", "Zelvora S.L.", "ZELVORA"),
    ]
    dec = _leer(
        tmp_path,
        [(DUP, EXP, "España|Zelvora SL", "España|Zelvora S.L.", "DISTINTA", "Revisora Uno")],
    )
    graf, _, pe = _ancla(filas, dec)
    comp = _comp_por_nombre(graf)
    assert comp["Zelvora SL"] != comp["Zelvora S.L."]
    assert graf["_COMP"].nunique() == 2
    assert any(
        "una decisión DISTINTA separa a miembros de ambas empresas" in m for *_, m in _listas(pe)
    )


def test_ancla_una_decision_misma_entidad_une_formas_legales_distintas_aunque_las_reglas_no(
    tmp_path,
):
    """Lo que una persona decide manda sobre la compatibilidad de formas legales (solo un veto lo frena)."""
    filas = [A("España", "Zelvora SL", "ZELVORA"), A("España", "Zelvora S. Coop.", "ZELVORA")]
    dec = _leer(
        tmp_path, [(DUP, EXP, "España|Zelvora S. Coop.", "España|Zelvora SL", "MISMA_ENTIDAD", "x")]
    )
    graf, _, pe = _ancla(filas, dec)
    assert graf["_COMP"].nunique() == 1 and len(pe) == 0


def test_ancla_el_orden_de_las_uniones_no_cambia_si_nada_las_restringe():
    """Sin vetos ni formas incompatibles el resultado es la partición de siempre (transitividad)."""
    filas = [A("España", "Kra Mento Foods", "KRAMENTO"), A("España", "Kramento Foods", "KRAMENTO"), A("España", "Kramento Foods SL", "KRAMENTO")]  # fmt: skip
    graf, un, _ = _ancla(filas)
    assert graf["_COMP"].nunique() == 1 and len(un) == 3


def test_ancla_los_pendientes_conservan_sus_columnas_aunque_una_decision_los_absorba(tmp_path):
    """F3.1b (g3-O4): si todos los pendientes quedan en la misma empresa, ``pe`` sigue teniendo sus tres columnas."""
    filas = [A("España", "Quelmar", "QUELMAR"), A("España", "Quelmar Lacteos", "QUELMAR LACTEOS")]
    assert len(_ancla(filas)[2]) == 1
    dec = _leer(
        tmp_path, [(DUP, EXP, "España|Quelmar", "España|Quelmar Lacteos", "MISMA_ENTIDAD", "x")]
    )
    _, _, pe = _ancla(filas, dec)
    assert len(pe) == 0
    assert list(pe.columns) == ["pos_a", "pos_b", "MOTIVO"]


def test_ancla_una_decision_de_una_grafia_con_ella_misma_se_avisa_y_no_pone_veto(tmp_path):
    """F3.1b (g3-O3): CLAVE_A == CLAVE_B no tiene sentido: no pone un veto (p, p) que rompe una invariante."""
    filas = [A("España", "Zelvora", "ZELVORA"), A("España", "Kramento", "KRAMENTO")]
    dec = _leer(tmp_path, [(DUP, EXP, "España|Zelvora", "España|Zelvora", "DISTINTA", "x")])
    graf, _, _ = _ancla(filas, dec)
    assert graf.attrs["vetos"] == set()
    assert graf.attrs["autopares"] == [("España|Zelvora", "España|Zelvora")]
