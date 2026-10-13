"""Arreglos declarados del motor de vinculación (F3.1b): cada prueba fija el comportamiento NUEVO de un hallazgo.

El paquete nació con paridad exacta contra el motor original, defectos incluidos (``docs/vinculacion/``). Aquí cada
defecto se corrige con su prueba, y el cambio se declara (``tests/lineas_base_vinculacion_cambios.json`` cuando altera
las tablas de los escenarios inventados). El id de cada prueba es el del hallazgo en HALLAZGOS_MOTOR_ORIGINAL.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.flujo.vinculacion import controles
from record_linkage.flujo.vinculacion.normalizar import (
    digitos_telefono,
    dominio_email,
    dominio_web,
    id_comparable,
)

AUSENTES = [None, np.nan, np.float32("nan"), pd.NA, pd.NaT]


# ── g1-O1 · un ausente de pandas nunca es el texto «NA» ─────────────────────────────────────────────
@pytest.mark.parametrize("ausente", AUSENTES, ids=repr)
@pytest.mark.parametrize("extractor", [id_comparable, dominio_web, dominio_email, digitos_telefono])
def test_g1_o1_los_extractores_tratan_cualquier_ausente_de_pandas_como_vacio(extractor, ausente):
    # Antes: id_comparable(pd.NA) daba 'NA' y dos empresas sin NIF «compartían identificador» (EMPRESA/A).
    assert extractor(ausente) == ""


# ── g1-O2 · un número entero guardado como float conserva sus dígitos ───────────────────────────────
@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (900123456.0, "900123456"),
        (np.float64(1234567890.0), "1234567890"),
        (900123456, "900123456"),
    ],
)
def test_g1_o2_id_comparable_no_agrega_un_cero_a_un_entero_en_float(valor, esperado):
    # Antes: 900123456.0 → '9001234560' y no coincidía con el mismo NIT escrito como texto.
    assert id_comparable(valor) == esperado == id_comparable(str(int(valor)))


@pytest.mark.parametrize(
    "valor", [3001234567.0, np.float64(3001234567.0), 3001234567, "300 123 4567"]
)
def test_g1_o2_el_telefono_en_float_da_los_mismos_ocho_digitos_que_en_texto(valor):
    # Antes: 3001234567.0 → '12345670' (el '.0' aportaba un dígito) frente a '01234567' del texto.
    assert digitos_telefono(valor) == "01234567"


def test_g1_o2_un_float_con_decimales_se_conserva_como_viene():
    assert id_comparable(12.5) == "125"
    assert digitos_telefono(12.5) == ""


# ── C1 + M-a · el identificador igual no cruza países y justifica el A en la invariante ─────────────
def _pares_a(evidencia: str, sobra: str = "ALIMENTOS") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "NIVEL_AUTO": ["EMPRESA"], "BANDA_AUTO": ["A"], "DECISION": [""], "MISMO_PAIS": ["SÍ"],
            "TIPO_SUP_B": ["NOMBRE"], "SOBRA_EXPORTADOR": [sobra], "SOBRA_DESTINO": [""],
            "NIVEL_FINAL": ["EMPRESA"], "EVIDENCIA_EXTRA": [evidencia],
        }
    )  # fmt: skip


def _estados(pares: pd.DataFrame) -> dict[str, str]:
    c = controles._Controles()
    controles._controles_de_pares(c, pares)
    return {f["CONTROL"]: f["ESTADO"] for f in c.filas}


def test_m_a_un_a_promovido_por_identificador_no_incumple_la_invariante_de_palabras_alineadas():
    # Antes: toda subida por identificador con palabras que sobran dejaba esta invariante en FALLA.
    estados = _estados(_pares_a("mismo identificador"))
    assert estados["Todo vínculo automático A tiene todas las palabras alineadas"] == "OK"


def test_m_a_un_a_con_palabras_sin_alinear_y_sin_identificador_sigue_incumpliendo():
    estados = _estados(_pares_a(""))
    assert estados["Todo vínculo automático A tiene todas las palabras alineadas"] == "FALLA"


# ── B1 · mismas letras pero iniciales o números distintos: nunca se unen solas ─────────────────────
from apoyo_comparacion_vinculacion import (  # noqa: E402
    CORPUS,
    FilaAncla,
    NormalizadorDoble,
    comparador,
    config_prueba,
    prep_ancla,
)

from record_linkage.flujo.vinculacion.decisiones import leer_decisiones  # noqa: E402
from record_linkage.flujo.vinculacion.deduplicar import (  # noqa: E402
    deduplicar_ancla,
    familias_legales,
)


def _ancla(*filas: FilaAncla):
    cfg = config_prueba()
    corpus = [*CORPUS, *(f.norm for f in filas)]
    return deduplicar_ancla(
        prep_ancla(list(filas)),
        NormalizadorDoble(),
        comparador(cfg, corpus),
        cfg,
        leer_decisiones([]),
    )


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("J MARTINEZ", "A MARTINEZ"),
        ("KRAMENTO 2", "KRAMENTO 3"),
        ("PELUVIKA 1 SL", "PELUVIKA 2 SL"),
    ],
)
def test_b1_mismas_letras_con_iniciales_o_numeros_distintos_van_a_pendientes_no_a_union(a, b):
    # Antes: la rama «mismas letras, otros espacios» no miraba MARCAS_OK y 'J Martinez' = 'A Martinez' se unían solas.
    _, union, pendientes = _ancla(
        FilaAncla("España", a.title(), a), FilaAncla("España", b.title(), b)
    )
    assert union.empty
    assert pendientes.values.tolist() == [
        [
            0,
            1,
            "mismas letras, pero difieren las iniciales o los números (¿otra persona o sucursal?)",
        ]
    ]


def test_b1_mismas_letras_con_marcas_iguales_siguen_uniendose_solas():
    _, union, pendientes = _ancla(
        FilaAncla("España", "Kra Mento Foods", "KRA MENTO FOODS"),
        FilaAncla("España", "Kramento Foods", "KRAMENTO FOODS"),
    )
    assert union.values.tolist() == [[0, 1, "mismas letras, otros espacios"]] and pendientes.empty


# ── g3-O6 · formas legales latinoamericanas ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("nombre", "familia"),
    [
        ("ZELVORA LTDA", "SL"),
        ("ZELVORA LDA", "SL"),
        ("ZELVORA L T D A", "SL"),
        ("ZELVORA S EN C", "SCS"),
        ("ZELVORA EU", "EU"),
        ("ZELVORA EIRL", "EU"),
        ("ZELVORA SAS", "SAS"),
        ("UNLIMITED FOODS", ""),
        ("ILIMITADA FOODS", ""),
        ("ZELVORA LIMITADA", "SL"),
        ("ZELVORA LIMITED", "LTD"),
    ],
)
def test_g3_o6_las_formas_legales_latinoamericanas_tienen_familia(nombre, familia):
    # Antes: «Ltda», «Lda», «S. en C.», «E.U.» y «EIRL» no tenían familia (compatibles con todo) y «Unlimited» caía en LTD.
    assert familias_legales(pd.Series([nombre]))[0] == familia


# ── g3-O5 · la clave de una grafía no depende de la versión de pandas ──────────────────────────────
from record_linkage.flujo.vinculacion.decisiones import clave_ancla, clave_texto  # noqa: E402

ESPACIOS_RAROS = ["\u00a0", "\u2007", "\u202f", "\u3000", "\x0b", "\x85", "\t", "  "]


@pytest.mark.parametrize("espacio", ESPACIOS_RAROS, ids=lambda s: repr(s))
def test_g3_o5_los_espacios_unicode_se_normalizan_igual_en_pandas_2_y_3(espacio):
    # Antes, con pandas 3 el `\s` lo evaluaba Arrow/RE2 y no reconocía NBSP, U+2007, U+202F, U+3000, \x0b ni \x85:
    # 'Zelvora<NBSP>Alimentos' daba una grafía distinta de 'Zelvora Alimentos' solo en pandas 3, y un archivo de
    # decisiones hecho en pandas 2 no casaba con las claves de pandas 3.
    esperado = "España|Zelvora Alimentos"
    assert clave_ancla(["España"], [f"Zelvora{espacio}Alimentos"]).tolist() == [esperado]
    assert clave_ancla(["España"], [f"{espacio}Zelvora{espacio}Alimentos{espacio}"]).tolist() == [
        esperado
    ]


def test_g3_o5_la_clave_conserva_los_ausentes_como_vacio_y_el_orden():
    assert clave_texto([None, np.nan, pd.NA, " a  b "]).tolist() == ["", "", "", "a b"]


def test_g3_o3_el_aviso_de_una_decision_de_una_grafia_contra_si_misma():
    from record_linkage.flujo.vinculacion.ejecutar import _avisos_de_decisiones_de_la_ancla

    graf = pd.DataFrame({"x": [1]})
    graf.attrs["desconocidas"] = [("España|A", "España|B")]
    graf.attrs["autopares"] = [("España|Zelvora", "España|Zelvora")]
    assert _avisos_de_decisiones_de_la_ancla("EXPORTADORES", graf) == [
        "EXPORTADORES: decisión DUPLICADO con grafía inexistente España|A ↔ España|B",
        "EXPORTADORES: decisión DUPLICADO de una grafía contra sí misma (España|Zelvora); se ignora",
    ]


# ── g1-O3 / C1(g2) · un texto suelto donde va una colección ya no se recorre letra a letra ─────────
from record_linkage.flujo.vinculacion.config import ConfigVinculacion, Fuente  # noqa: E402


def _fuente(clave="EXPORTADORES", **cambios):
    return Fuente(clave=clave, archivo="x.csv", col_nombre="Empresa", **cambios)


@pytest.mark.parametrize(
    "campo",
    [
        "formas_legales_extra",
        "genericos_extra",
        "patrones_intermediario",
        "dominios_genericos",
        "nombres_pila",
    ],
)
def test_g1_o3_la_configuracion_rechaza_un_texto_donde_va_una_coleccion(campo):
    with pytest.raises(ValueError, match=rf"{campo}.*texto suelto"):
        ConfigVinculacion(ancla=_fuente(), **{campo: "CARGO"})


@pytest.mark.parametrize(
    "campo",
    [
        "cols_pais_extra",
        "cols_metricas",
        "cols_contexto",
        "metricas_repetidas",
        "claves_repeticion",
    ],
)
def test_g1_o3_la_fuente_rechaza_un_texto_donde_va_una_coleccion(campo):
    with pytest.raises(ValueError, match=rf"{campo}.*texto suelto"):
        _fuente(**{campo: "HQ"})


def test_g1_o3_los_alias_de_pais_deben_ser_colecciones_de_grafias():
    with pytest.raises(ValueError, match=r"alias_paises\['FRA'\].*texto suelto"):
        ConfigVinculacion(ancla=_fuente(), alias_paises={"FRA": "FRANCE"})
    cfg = ConfigVinculacion(ancla=_fuente(), alias_paises={"FRA": ("FRANCE",)})
    assert cfg.alias_paises == {"FRA": ("FRANCE",)}


def test_g1_o3_un_archivo_de_decisiones_suelto_es_un_solo_archivo():
    # Antes: "decisiones.xlsx" se iteraba letra a letra y se intentaban leer los archivos 'd', 'e', 'c'…
    assert ConfigVinculacion(
        ancla=_fuente(), archivos_decisiones="decisiones.xlsx"
    ).archivos_decisiones == ("decisiones.xlsx",)


def test_d_la_etiqueta_en_blanco_vale_la_clave_y_dos_destinos_no_comparten_primera_palabra():
    assert _fuente("ORBIS", etiqueta="   ").etiqueta == "ORBIS"
    with pytest.raises(ValueError, match="primera palabra de la etiqueta"):
        ConfigVinculacion(
            ancla=_fuente(),
            destinos=[
                _fuente("FDI", etiqueta="FDI MARKETS"),
                _fuente("FDI_NEWS", etiqueta="FDI NEWS"),
            ],
        )
