"""Registro de comparadores y ponderación IDF del nombre (v0.18.0).

Qué se protege aquí:

* **Paridad.** Los seis comparadores heredados dan exactamente el mismo
  resultado que la cadena de ``if`` que vivía dentro del scorer. Sin esta
  garantía, mover código a un registro sería una apuesta.
* **Capacidad nueva.** Los comparadores de teléfono y correo canonicalizan
  antes de comparar. Declarar TELEFONO con el comparador exacto no cambiaba
  ni un par, porque dentro de un mismo grupo el número aparece como
  ``312 1897799``, ``312-189-7799`` y ``+573121897799``.
* **Extensibilidad real.** Se puede registrar un comparador nuevo sin tocar
  el scorer, y la validación de configuración lo acepta de inmediato: antes
  había dos listas de tipos válidos que debían coincidir a mano.
* **IDF.** Los tokens frecuentes pesan menos que los raros, el cálculo es
  vectorizado y la similitud es simétrica y acotada.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.scorer import VectorizedScorer
from record_linkage.matching import comparadores_extra as ce
from record_linkage.matching.idf import construir_idf, similitud_idf

TIPOS_HEREDADOS = (
    "exact_or_zero",
    "categorical",
    "categorical_signed",
    "exact_signed",
    "token_set_ratio",
    "token_set_ratio_signed",
)


@pytest.fixture
def valores_ruidosos() -> tuple[np.ndarray, np.ndarray]:
    """Pares con nulos de todas las formas, espacios y diferencias de caja."""
    rng = np.random.default_rng(42)
    vocabulario = np.array(
        ["BOGOTA", "MEDELLIN", "CALI", "", "nan", None, "N/A", "  BOGOTA  ", "bogota", "-"],
        dtype=object,
    )
    return rng.choice(vocabulario, 3_000), rng.choice(vocabulario, 3_000)


# ── Paridad con la implementación anterior ────────────────────────────────


@pytest.mark.parametrize("tipo", TIPOS_HEREDADOS)
def test_comparador_heredado_conserva_paridad_bit_a_bit(
    tipo: str, valores_ruidosos: tuple[np.ndarray, np.ndarray]
) -> None:
    izquierda, derecha = valores_ruidosos
    delegado = VectorizedScorer._feature_similarity_vectorized(izquierda, derecha, tipo)
    directo = ce.obtener(tipo)(izquierda, derecha)
    np.testing.assert_allclose(delegado, directo, atol=0, rtol=0)


def test_tipo_desconocido_devuelve_ceros_sin_romper() -> None:
    salida = VectorizedScorer._feature_similarity_vectorized(
        np.array(["A"]), np.array(["A"]), "no_existe"
    )
    assert salida.tolist() == [0.0]


def test_entrada_vacia_no_falla() -> None:
    vacio = np.array([], dtype=object)
    assert VectorizedScorer._feature_similarity_vectorized(vacio, vacio, "categorical").size == 0


# ── Registro ──────────────────────────────────────────────────────────────


def test_todos_los_tipos_heredados_siguen_registrados() -> None:
    assert set(TIPOS_HEREDADOS) <= set(ce.tipos_disponibles())


def test_no_se_puede_pisar_un_comparador_registrado() -> None:
    with pytest.raises(ValueError, match="ya está registrado"):
        ce.registrar("categorical", firmado=True, descripcion="duplicado")(lambda a, b: a)


def test_la_validacion_de_configuracion_lee_el_registro() -> None:
    """Una sola lista de tipos válidos: la del registro.

    Antes existían dos —la del registro y una escrita a mano en la
    validación— y registrar un comparador nuevo lo dejaba funcionando en el
    scorer pero rechazado por la configuración.
    """
    from record_linkage.deduplication.unified import _tipos_de_comparador_validos

    assert _tipos_de_comparador_validos() == frozenset(ce.tipos_disponibles())


def test_un_comparador_nuevo_no_obliga_a_tocar_el_scorer() -> None:
    nombre = "prueba_ocp"
    if nombre not in ce.tipos_disponibles():

        @ce.registrar(nombre, firmado=False, descripcion="siempre 0,5")
        def _mitad(a: np.ndarray, b: np.ndarray) -> np.ndarray:
            return np.full(len(a), 0.5)

    salida = VectorizedScorer._feature_similarity_vectorized(
        np.array(["x", "y"]), np.array(["x", "z"]), nombre
    )
    assert salida.tolist() == [0.5, 0.5]


# ── Teléfono ──────────────────────────────────────────────────────────────


def test_el_telefono_ignora_formato_prefijo_e_indicativo() -> None:
    izquierda = np.array(["312 1897799", "312-189-7799", "+573121897799", "(031) 2345678"])
    derecha = np.array(["+573121897799", "3121897799", "312 189 7799", "2345678"])
    np.testing.assert_array_equal(ce.obtener("telefono_signed")(izquierda, derecha), 1.0)


def test_el_telefono_penaliza_numeros_distintos() -> None:
    salida = ce.obtener("telefono_signed")(np.array(["3121897799"]), np.array(["3009998877"]))
    assert salida.tolist() == [-1.0]


@pytest.mark.parametrize("centinela", ["", "N/A", "SIN DATO", "-", "123", None])
def test_el_telefono_sin_informacion_no_premia_ni_castiga(centinela) -> None:
    salida = ce.obtener("telefono_signed")(
        np.array([centinela], dtype=object), np.array(["3121897799"])
    )
    assert salida.tolist() == [0.0]


def test_canonicalizar_telefono_conserva_la_cola() -> None:
    salida = ce.canonicalizar_telefono(np.array(["+57 (312) 189-7799"]))
    assert salida.tolist() == ["1897799"]


# ── Correo ────────────────────────────────────────────────────────────────


def test_el_correo_ignora_la_caja() -> None:
    salida = ce.obtener("email_signed")(
        np.array(["SERVICIOSDEL@Hotmail.com"]), np.array(["serviciosdel@hotmail.com"])
    )
    assert salida.tolist() == [1.0]


def test_el_correo_gradua_variantes_del_mismo_dominio() -> None:
    """``serviciosdel`` y ``serviciosdel.co`` en el mismo dominio son la misma
    empresa con dos buzones, no dos empresas."""
    salida = ce.obtener("email_signed")(
        np.array(["serviciosdel@hotmail.com"]), np.array(["serviciosdel.co@hotmail.com"])
    )
    assert 0.5 < salida[0] < 1.0


def test_el_correo_penaliza_dominios_distintos() -> None:
    salida = ce.obtener("email_signed")(
        np.array(["contacto@acme.com"]), np.array(["contacto@beta.com"])
    )
    assert salida.tolist() == [-1.0]


def test_el_correo_sin_arroba_es_ausencia_de_evidencia() -> None:
    salida = ce.obtener("email_signed")(np.array(["no es un correo"]), np.array(["a@b.com"]))
    assert salida.tolist() == [0.0]


# ── Documento ─────────────────────────────────────────────────────────────


def test_el_documento_ignora_separadores_y_ceros_a_la_izquierda() -> None:
    salida = ce.obtener("documento_signed")(
        np.array(["000.123.456-7", "12.345.678"]), np.array(["1234567", "12345678"])
    )
    np.testing.assert_array_equal(salida, 1.0)


# ── Simetría y rango de todos los comparadores ────────────────────────────


@pytest.mark.parametrize("tipo", ce.tipos_disponibles())
def test_todo_comparador_es_simetrico_y_acotado(
    tipo: str, valores_ruidosos: tuple[np.ndarray, np.ndarray]
) -> None:
    if tipo == "prueba_ocp":
        pytest.skip("comparador de juguete registrado por otra prueba")
    izquierda, derecha = valores_ruidosos
    comparador = ce.obtener(tipo)
    ida, vuelta = comparador(izquierda, derecha), comparador(derecha, izquierda)
    np.testing.assert_allclose(ida, vuelta, atol=1e-12)
    minimo = -1.0 if comparador.firmado else 0.0
    assert ida.min() >= minimo - 1e-12 and ida.max() <= 1.0 + 1e-12


# ── IDF ───────────────────────────────────────────────────────────────────


@pytest.fixture
def corpus() -> pd.Series:
    return pd.Series(
        [
            "HWANGJUNG TECH LLC",
            "HANJUNG TECH LLC",
            "ACME TECH LLC",
            "BETA TECH LLC",
            "GAMMA TECH LLC",
            "OHJUNG FLOWER",
            "OHJUNG FLOWER CO LTD",
        ]
    )


def test_los_tokens_frecuentes_pesan_menos_que_los_raros(corpus: pd.Series) -> None:
    pesos = construir_idf(corpus)
    peso = {t: pesos.pesos[i] for t, i in pesos.vocabulario.items()}
    assert peso["TECH"] < peso["HWANGJUNG"]
    assert peso["LLC"] < peso["OHJUNG"]


def test_el_idf_separa_lo_que_el_comparador_por_tokens_confunde(corpus: pd.Series) -> None:
    """Dos empresas distintas que comparten genéricos deben quedar por debajo
    de una misma empresa con un sufijo de más."""
    pesos = construir_idf(corpus)
    distintas = similitud_idf(pesos, np.array([0]), np.array([1]))[0]
    misma = similitud_idf(pesos, np.array([5]), np.array([6]))[0]
    assert misma > distintas


def test_la_similitud_idf_es_simetrica_y_acotada(corpus: pd.Series) -> None:
    pesos = construir_idf(corpus)
    izq = np.array([0, 1, 5, 2])
    der = np.array([1, 0, 6, 3])
    ida = similitud_idf(pesos, izq, der)
    vuelta = similitud_idf(pesos, der, izq)
    np.testing.assert_allclose(ida, vuelta, atol=1e-12)
    assert ida.min() >= 0.0 and ida.max() <= 1.0


def test_un_nombre_consigo_mismo_da_uno(corpus: pd.Series) -> None:
    pesos = construir_idf(corpus)
    idx = np.arange(len(corpus))
    np.testing.assert_allclose(similitud_idf(pesos, idx, idx), 1.0, atol=1e-12)


def test_la_frecuencia_es_documental_no_por_ocurrencia() -> None:
    """Un token repetido dentro del MISMO nombre cuenta una sola vez.

    Sin esto, "SEGUROS SEGUROS DEL SUR" inflaría la frecuencia de SEGUROS y
    lo haría parecer genérico cuando no lo es.
    """
    con_repeticion = construir_idf(pd.Series(["SEGUROS SEGUROS DEL SUR", "BANCO DEL SUR"]))
    sin_repeticion = construir_idf(pd.Series(["SEGUROS DEL SUR", "BANCO DEL SUR"]))
    i, j = con_repeticion.vocabulario["SEGUROS"], sin_repeticion.vocabulario["SEGUROS"]
    assert con_repeticion.pesos[i] == pytest.approx(sin_repeticion.pesos[j])


def test_corpus_vacio_no_rompe() -> None:
    pesos = construir_idf(pd.Series(["", "  ", None]))
    assert pesos.vacio
    assert similitud_idf(pesos, np.array([0]), np.array([1])).tolist() == [0.0]


def test_largos_distintos_fallan_rapido(corpus: pd.Series) -> None:
    pesos = construir_idf(corpus)
    with pytest.raises(ValueError, match="largos distintos"):
        similitud_idf(pesos, np.array([0, 1]), np.array([0]))


def test_construir_idf_valida_su_parametro(corpus: pd.Series) -> None:
    with pytest.raises(ValueError, match="debe ser >= 1"):
        construir_idf(corpus, longitud_minima=0)
