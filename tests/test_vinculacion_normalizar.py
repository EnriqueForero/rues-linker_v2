"""Pruebas de ``flujo.vinculacion.normalizar``: nombres, países, contacto y columnas estándar de cada fuente.

Los valores esperados NO salen de razonar sobre el código: se observaron ejecutando el motor original
(la celda ``%%writefile`` del notebook 10) con estas mismas entradas. Cada prueba dice qué regla ejerce.
Las empresas son inventadas.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from record_linkage.flujo.vinculacion.config import ConfigVinculacion, Fuente
from record_linkage.flujo.vinculacion.normalizar import (
    Normalizador,
    digitos_telefono,
    dominio_email,
    dominio_registrable,
    dominio_web,
    id_comparable,
    preparar_fuente,
    serie_numerica,
)
from record_linkage.matching.normalizadores import LOCALES
from record_linkage.paises import CATALOGO_PAISES

NAN = float("nan")

#: Las 26 columnas de ``preparar_fuente``, en el orden del contrato (las demás etapas y el Excel las leen).
COLUMNAS_PREPARADAS = [
    "FUENTE",
    "ID_REGISTRO",
    "FILA_EXCEL",
    "NOMBRE_ORIGINAL",
    "NOMBRE_NORM",
    "ALIAS_NORM",
    "GRUPO_ORIGINAL",
    "GRUPO_NORM",
    "PAIS_ORIGINAL",
    "PAIS_ISO3",
    "PAIS_CANONICO",
    "PAISES_EVIDENCIA",
    "WEB",
    "DOMINIO_WEB",
    "EMAIL",
    "DOMINIO_EMAIL",
    "TELEFONO",
    "TEL_DIGITOS",
    "CIUDAD",
    "CIUDAD_NORM",
    "DIRECCION",
    "ID_COMPARABLE",
    "TAMANO",
    "TRUNCADO",
    "INTERMEDIARIO",
    "SIN_NOMBRE_UTIL",
]


@pytest.fixture(autouse=True, scope="module")
def _locales_de_la_libreria_intactos():
    """El constructor de ``Normalizador`` registra un locale en ``LOCALES`` (global de la librería): se quita al terminar."""
    antes = dict(LOCALES)
    yield
    LOCALES.clear()
    LOCALES.update(antes)


def _config(**cambios) -> ConfigVinculacion:
    """Configuración de las pruebas: alias de país, formas legales y genéricos propios, un patrón de agente de carga."""
    ancla = Fuente(clave="ANCLA", archivo="no_se_lee.xlsx", col_nombre="Empresa", col_pais="Pais")
    parametros = {
        "ancla": ancla,
        "alias_paises": {"FRA": ("FRANCE",)},
        "formas_legales_extra": frozenset({"SOCIEDAD COOPERATIVA", "S COOP"}),
        "genericos_extra": frozenset({"HOLDING", "IBERIA"}),
        "patrones_intermediario": (r"\bCARGO\b",),
        "dominios_genericos": frozenset({"gmail.com"}),
    }
    return ConfigVinculacion(**{**parametros, **cambios})


@pytest.fixture
def nz() -> Normalizador:
    # Hallazgo F3: el constructor registra un locale GLOBAL en la librería; construirlo cuesta ~1 ms, así
    # que cada prueba trae el suyo y ninguna depende de qué Normalizador se creó antes.
    return Normalizador(_config())


def _fuente(**cambios) -> Fuente:
    """Una fuente con TODAS las variables declaradas (se sobreescribe lo que se pase)."""
    parametros = {
        "clave": "ANCLA",
        "archivo": "no_se_lee.xlsx",
        "col_nombre": "Empresa",
        "col_pais": "Pais",
        "cols_pais_extra": ("HQ",),
        "col_grupo": "Grupo",
        "col_web": "Web",
        "col_email": "Mail",
        "col_telefono": "Tel",
        "col_ciudad": "Ciudad",
        "col_direccion": "Dir",
        "col_id_comparable": "NIF",
        "col_tamano": "Tam",
        "largo_truncamiento": 20,
        "detectar_intermediarios": True,
    }
    return Fuente(**{**parametros, **cambios})


def _entrada(**columnas) -> pd.DataFrame:
    """Lo que entrega ``leer_fuente``: ``FILA_EXCEL``, ``ID_REGISTRO`` y las columnas declaradas, como object."""
    n = len(next(iter(columnas.values())))
    df = pd.DataFrame(columnas, dtype=object)
    df.insert(0, "FILA_EXCEL", np.arange(2, n + 2))
    df.insert(1, "ID_REGISTRO", [f"ANC-F{k:04d}" for k in range(2, n + 2)])
    return df


def _solo_nombre(nombres: list, **cambios) -> pd.DataFrame:
    """La salida de ``preparar_fuente`` para una fuente que declara solo ``col_nombre``."""
    f = Fuente(clave="ANCLA", archivo="no_se_lee.xlsx", col_nombre="Empresa", **cambios)
    return preparar_fuente(_entrada(Empresa=nombres), f, Normalizador(_config()))


# ── extractores sueltos ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        (
            "https://www.zelvora.es/inicio?x=1",
            "zelvora.es",
        ),  # esquema, www, ruta y consulta se quitan
        ("www3.kramento.com:8080/x", "kramento.com"),  # wwwN. y puerto
        (
            "http://tienda.zelvora.co.uk/a",
            "zelvora.co.uk",
        ),  # segundo nivel genérico + país de 2 letras
        ("zelvora.es", "zelvora.es"),
        ("ZELVORA.ES", "zelvora.es"),  # minúsculas
        ("WWW.Zelvora.COM/Inicio", "zelvora.com"),
        ("  https://zelvora.es; https://otro.es  ", "zelvora.es"),  # varias direcciones: la primera
        ("zelvora.es, otro.com", "zelvora.es"),
        ("zelvora.es|otro.com", "zelvora.es"),
        ("zelvora.es otro.com", "zelvora.es"),
        ("ftp://zelvora.es", "zelvora.es"),
        ("zelvora.es?x=1", "zelvora.es"),
        ("www.zelvora.es.", "zelvora.es"),  # punto final
        ("zelvora", ""),  # sin punto no es un dominio
        ("", ""),
        ("   ", ""),
        ("n.a.", ""),  # marcadores de ausente
        ("-", ""),
        ("nan", ""),
        ("None", ""),
        (None, ""),
        (NAN, ""),
        (12345, ""),
        ("tienda.zelvora.gob.es", "gob.es"),  # Hallazgo F3: gob.es no está en los segundos niveles
        (
            ";zelvora.es",
            "",
        ),  # Hallazgo F3: un separador al comienzo deja la primera dirección vacía
    ],
)
def test_dominio_web(entrada, esperado):
    assert dominio_web(entrada) == esperado


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("info@zelvora.es", "zelvora.es"),
        (
            "Info@MAIL.Zelvora.CO.UK",
            "zelvora.co.uk",
        ),  # minúsculas, subdominio fuera, co.uk completo
        ("a@b.com; c@d.com", "b.com"),  # varios correos: el primero
        ("correo: ventas@zelvora.es.", "zelvora.es"),  # texto alrededor y punto final
        ("@zelvora.es", "zelvora.es"),
        ("pepe@gmail.com", "gmail.com"),  # los genéricos se anulan después, en preparar_fuente
        ("sin correo", ""),
        ("x@zelvora", ""),  # sin dominio de nivel superior
        ("x@zelvora.e", ""),  # el nivel superior tiene al menos 2 letras
        ("", ""),
        (None, ""),
        (NAN, ""),
        (123, ""),
    ],
)
def test_dominio_email(entrada, esperado):
    assert dominio_email(entrada) == esperado


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("+34 961 234 567", "61234567"),  # los últimos 8 dígitos
        ("961234567", "61234567"),
        ("0034961999999", "61999999"),
        ("(961) 234-567", "61234567"),
        ("'+34 961 234 567", "61234567"),
        (96123456789, "23456789"),
        ("12345678", "12345678"),  # justo 8 dígitos
        ("1234567", ""),  # menos de 8 dígitos no sirve de evidencia
        ("ext. 12", ""),
        ("", ""),
        (None, ""),
        (NAN, ""),
    ],
)
def test_digitos_telefono(entrada, esperado):
    assert digitos_telefono(entrada) == esperado


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("B-1234 5678", "B12345678"),  # solo letras A-Z y dígitos, en mayúsculas
        ("b12345678", "B12345678"),
        ("x.y", "XY"),
        ("es-b1234", "ESB1234"),
        ("  ab 12  ", "AB12"),
        ("ñ-1", "1"),  # la ñ no es A-Z: se pierde
        (5, "5"),
        ("", ""),
        (None, ""),
        (NAN, ""),
    ],
)
def test_id_comparable(entrada, esperado):
    assert id_comparable(entrada) == esperado


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("zelvora.es", "zelvora.es"),
        ("www.kramento.es", "kramento.es"),  # dos últimos rótulos
        ("a.b.c.d.com", "d.com"),
        ("mail.zelvora.com", "zelvora.com"),  # .com tiene 3 letras: no es dominio de país
        ("tienda.zelvora.co.uk", "zelvora.co.uk"),  # tres rótulos: segundo nivel genérico + país
        ("tienda.zelvora.com.co", "zelvora.com.co"),
        ("tienda.zelvora.org.au", "zelvora.org.au"),
        ("tienda.zelvora.ac.uk", "zelvora.ac.uk"),
        ("tienda.zelvora.gov.co", "zelvora.gov.co"),
        ("x.edu.mx", "x.edu.mx"),
        ("x.co.uk", "x.co.uk"),
        ("co.uk", "co.uk"),  # con 2 rótulos o menos se devuelve tal cual
        ("zelvora", "zelvora"),
        ("..zelvora..es..", "zelvora.es"),  # rótulos vacíos se descartan
        ("", ""),
        ("x.gob.mx", "gob.mx"),  # Hallazgo F3: gob no está en la lista de segundos niveles
    ],
)
def test_dominio_registrable(entrada, esperado):
    assert dominio_registrable(entrada) == esperado


def test_serie_numerica_deja_en_nan_los_marcadores_de_ausente_y_lo_no_numerico():
    # Regla: 'n.a.', 'n.s.' y '-' son ausentes; to_numpy(errors='coerce') descarta lo demás (coma decimal incluida).
    s = pd.Series(
        ["12", "n.a.", "n.s.", "-", None, 3.5, "abc", "1,5", "  7 ", "1e3", 4, "N.A."],
        index=range(10, 22),
        dtype="object",
    )
    r = serie_numerica(s)
    esperado = [12.0, NAN, NAN, NAN, NAN, 3.5, NAN, NAN, 7.0, 1000.0, 4.0, NAN]
    assert r.dtype == "float64"
    assert r.index.equals(s.index)
    pd.testing.assert_series_equal(r, pd.Series(esperado, index=s.index), check_names=False)


# ── Normalizador: construcción ───────────────────────────────────────────────────────────────────
def test_el_constructor_registra_el_locale_en_la_libreria(nz):
    # Hallazgo F3: estado global de la librería (LOCALES); normalizar() lo pide por nombre.
    locale = LOCALES[Normalizador.LOCALE]
    assert {"SOCIEDAD COOPERATIVA", "S COOP", "GMBH", "LTDA"} <= locale["sufijos"]
    assert locale["genericos"] == nz.genericos
    # Seis formas de 2 letras que son palabras corrientes cuentan como legales (ver ``nz.legales``) pero
    # LEGALES_BASE no las registra como sufijo (``CO`` sí está, pero porque la librería ya la trae).
    assert not {"AB", "AS", "RL", "SC", "SE", "SP"} & locale["sufijos"]
    assert "CO" in locale["sufijos"]


def test_legales_son_palabras_sueltas_limpias_y_sin_las_palabras_de_las_frases(nz):
    # Regla: palabras de una sola palabra + las de las formas declaradas; de las FRASES de la librería no.
    assert {"GMBH", "LTDA", "COOPERATIVA", "SOCIEDAD", "COOP", "CO", "SE", "AS"} <= nz.legales
    assert "S" not in nz.legales  # de 'S COOP' solo queda COOP: se piden 2 o más caracteres
    assert "INTERNACIONAL" not in nz.legales  # parte de la frase «COMERCIALIZADORA INTERNACIONAL»
    assert all(re.fullmatch(r"[A-Z0-9]{2,}", t) for t in nz.legales)


def test_genericos_suman_los_de_la_libreria_y_los_declarados(nz):
    assert {"HOLDING", "IBERIA"} <= nz.genericos
    assert "ZELVORA" not in nz.genericos
    declarados = Normalizador(_config(genericos_extra=frozenset({" norte "}))).genericos
    assert "NORTE" in declarados  # ConfigVinculacion los pasa a mayúsculas y sin espacios
    # El constructor reescribió el locale con los genéricos de ESTA configuración.
    assert LOCALES[Normalizador.LOCALE]["genericos"] == declarados


def test_patron_de_intermediarios_solo_existe_si_se_declaran_patrones(nz):
    assert Normalizador(_config(patrones_intermediario=())).patron_inter is None
    assert nz.patron_inter is not None
    assert nz.patron_inter.search("quelmar cargo sl")  # sin distinguir mayúsculas
    assert not nz.patron_inter.search("CARGOLINA SA")  # \b: palabra completa


def test_catalogo_suma_los_alias_declarados_en_mayusculas(nz):
    por_iso = {iso: graf for iso, _, graf in nz.catalogo}
    assert len(nz.catalogo) == len(CATALOGO_PAISES)
    assert "FRANCE" in por_iso["FRA"]
    nz_ita = Normalizador(_config(alias_paises={"ITA": (" italy ",)}))
    assert "ITALY" in {iso: graf for iso, _, graf in nz_ita.catalogo}["ITA"]


def test_alias_con_iso3_inexistente_falla_en_el_constructor():
    cfg = _config(alias_paises={"XXX": ("UNO",), "YYY": ()})
    with pytest.raises(KeyError) as error:
        Normalizador(cfg)
    assert (
        error.value.args[0] == "ALIAS_PAISES usa ISO3 inexistentes en el catálogo: ['XXX', 'YYY']"
    )


# ── Normalizador: nombres ────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        (
            "Zelvora Alimentos (Dulcevent)",
            ("Zelvora Alimentos", "Dulcevent"),
        ),  # el paréntesis es el alias
        ("Zelvora (Dulcevent) Foods (Peluvika)", ("Zelvora Foods", "Dulcevent Peluvika")),  # varios
        ("Orbelia &AMP; Hijos (a) (b)", ("Orbelia & Hijos", "a b")),  # &AMP; → & y alias unidos
        ("Orbelia &amp; Hijos", ("Orbelia & Hijos", "")),
        ("Kramento Foods (", ("Kramento Foods", "")),  # un paréntesis sin cerrar se descarta
        ("A (B) C (D", ("A C", "B")),
        ("Tavorín (x) y (", ("Tavorín y", "x")),
        ("(solo alias)", ("", "solo alias")),  # sin nombre principal
        ("a (  ) b", ("a b", "")),  # paréntesis vacío: sin alias
        ("Sin paréntesis", ("Sin paréntesis", "")),
        ("a ) b (", ("a ) b", "")),
        ("((x))", ("( )", "x")),  # anidados: queda un paréntesis suelto en el principal
        ("", ("", "")),
        (None, ("", "")),
        (NAN, ("", "")),
        (123, ("123", "")),
    ],
)
def test_separar_alias(nz, entrada, esperado):
    assert nz.separar_alias(entrada) == esperado


#: (entrada, esperado) de ``Normalizador.normalizar``; se procesan en UNA llamada (ver ``normalizados``).
CASOS_NORMALIZAR = [
    ("Zelvora Alimentos S.A.", "ZELVORA ALIMENTOS"),  # forma legal fuera, mayúsculas
    ("Zelvora Alimentos S.A.S.", "ZELVORA ALIMENTOS"),
    ("Zelvora Foods S.R.L.", "ZELVORA FOODS"),
    ("Nordavia Industrias, Ltda.", "NORDAVIA INDUSTRIAS"),
    ("Vantiko GmbH & Co KG", "VANTIKO"),
    ("Kramento S COOP", "KRAMENTO"),  # forma legal declarada en la configuración
    ("Tavorín SOCIEDAD COOPERATIVA", "TAVORIN"),  # frase declarada
    ("Zelvora SA SUCURSAL COLOMBIA", "ZELVORA"),  # frase de la librería, completa y al final
    (
        "ZELVORA   INTERNACIONAL",
        "ZELVORA INTERNACIONAL",
    ),  # «INTERNACIONAL» suelta es parte del nombre
    ("SOCIEDAD ANONIMA", "SOCIEDAD ANONIMA"),  # si todo es forma legal, no se deja vacío
    ("DE CV", "DE"),
    ("TAVORÍN  conservas  marinas", "TAVORIN CONSERVAS MARINAS"),  # tildes y espacios
    ("Zélvora Münster GmbH", "ZELVORA MUNSTER"),
    ("Pustorán Foods", "PUSTORAN FOODS"),
    ("Orbélia &amp; Hijos SL", "ORBELIA HIJOS"),  # entidad HTML
    ("Orbelia &#38; Hijos", "ORBELIA HIJOS"),
    ("&#1234; Zelvora", "ZELVORA"),  # entidad numérica de la prelimpieza
    ("123 - Zelvora SA", "ZELVORA"),  # código inicial
    ("c/o Zelvora Foods", "ZELVORA FOODS"),  # «care of»
    ("Kramento's Foods S.L.", "KRAMENTOS FOODS"),  # apóstrofo-s: se pega
    ("O'Zelvora Foods", "O ZELVORA FOODS"),  # otro apóstrofo: espacio
    ("O Zelvora\N{ACUTE ACCENT}s", "O ZELVORAS"),
    ("Lumbreras d'Or", "LUMBRERAS D OR"),
    ("O.M.G Quimicos C.A", "OMG QUIMICOS"),  # iniciales unidas
    ("A B C Zelvora", "ABC ZELVORA"),
    ("Zelvora \N{NO-BREAK SPACE}Foods", "ZELVORA FOODS"),  # espacio duro
    ("Zelvora\tFoods", "ZELVORA FOODS"),
    (
        "Quelmar (Holding)",
        "QUELMAR HOLDING",
    ),  # los paréntesis pasan a espacio (los genéricos no se quitan)
    ("  ", ""),
    (None, ""),
]


@pytest.fixture(scope="module")
def normalizados() -> list[str]:
    serie = pd.Series([entrada for entrada, _ in CASOS_NORMALIZAR], dtype="object")
    return Normalizador(_config()).normalizar(serie).tolist()


@pytest.mark.parametrize(
    ("posicion", "entrada", "esperado"),
    [(k, entrada, esperado) for k, (entrada, esperado) in enumerate(CASOS_NORMALIZAR)],
)
def test_normalizar(normalizados, posicion, entrada, esperado):
    assert normalizados[posicion] == esperado


def test_normalizar_conserva_el_indice_y_trabaja_por_serie(nz):
    serie = pd.Series(["Zelvora S.A.", None, "Kramento's Foods"], index=[10, 20, 30])
    r = nz.normalizar(serie)
    assert r.index.tolist() == [10, 20, 30]
    assert r.tolist() == ["ZELVORA", "", "KRAMENTOS FOODS"]


@pytest.mark.parametrize(
    ("nombre", "utiles", "distintivas", "marcas", "marcas_truncado", "compacto"),
    [
        ("ZELVORA ALIMENTOS", ["ZELVORA", "ALIMENTOS"], ["ZELVORA"], (), (), "ZELVORAALIMENTOS"),
        (
            "ZELVORA HOLDING IBERIA",
            ["ZELVORA", "HOLDING", "IBERIA"],
            ["ZELVORA"],
            (),
            (),
            "ZELVORAHOLDINGIBERIA",
        ),
        ("ZELVORA SL", ["ZELVORA"], ["ZELVORA"], (), (), "ZELVORA"),  # forma legal fuera
        ("ZELVORA SOCIEDAD COOPERATIVA", ["ZELVORA"], ["ZELVORA"], (), (), "ZELVORA"),
        (
            "THE ZELVORA OF IBERIA",
            ["ZELVORA", "IBERIA"],
            ["ZELVORA"],
            (),
            (),
            "ZELVORAIBERIA",
        ),  # conectores
        (
            "ZELVORA Y KRAMENTO",
            ["ZELVORA", "KRAMENTO"],
            ["ZELVORA", "KRAMENTO"],
            (),
            (),
            "ZELVORAKRAMENTO",
        ),
        (
            "ZELVORA INTERNACIONAL",
            ["ZELVORA", "INTERNACIONAL"],
            ["ZELVORA"],
            (),
            (),
            "ZELVORAINTERNACIONAL",
        ),
        ("J ORBELIA", ["ORBELIA"], ["ORBELIA"], ("J",), ("J",), "ORBELIA"),  # inicial suelta: marca
        (
            "ZELVORA 2 SA",
            ["ZELVORA"],
            ["ZELVORA"],
            ("2",),
            (),
            "ZELVORA",
        ),  # número: marca (útil descarta dígitos)
        ("ZELVORA 3", ["ZELVORA"], ["ZELVORA"], ("3",), (), "ZELVORA"),
        (
            "ZELVORA K",
            ["ZELVORA"],
            ["ZELVORA"],
            ("K",),
            (),
            "ZELVORA",
        ),  # truncado: se descarta la última inicial
        ("K ZELVORA A", ["ZELVORA"], ["ZELVORA"], ("K",), ("K",), "ZELVORA"),
        ("DE LA X 12 Z", [], [], ("12", "X", "Z"), ("12", "X"), ""),  # marcas ordenadas
        (
            "KRA ZELVORA",
            ["KRA", "ZELVORA"],
            ["KRA", "ZELVORA"],
            (),
            (),
            "KRAZELVORA",
        ),  # 3 letras: ya distintiva
        ("QU 12", ["QU"], [], ("12",), ("12",), "QU"),  # 2 letras: útil pero no distintiva (< 3)
        ("ZE VO", ["ZE", "VO"], [], (), (), "ZEVO"),
        (
            "KR FOODS",
            ["KR", "FOODS"],
            [],
            (),
            (),
            "KRFOODS",
        ),  # «KR FOODS» y «KRFOODS» compactan igual
        ("KRFOODS", ["KRFOODS"], ["KRFOODS"], (), (), "KRFOODS"),
        ("AB", [], [], (), (), ""),  # AB es forma legal
        (
            "A ORBELIA",
            ["ORBELIA"],
            ["ORBELIA"],
            (),
            (),
            "ORBELIA",
        ),  # Hallazgo F3: 'A' es conector, no marca
        ("", [], [], (), (), ""),
    ],
)
def test_utiles_distintivas_marcas_y_compacto(
    nz, nombre, utiles, distintivas, marcas, marcas_truncado, compacto
):
    assert nz.utiles(nombre) == utiles
    assert nz.distintivas(nombre) == distintivas
    assert nz.marcas(nombre) == marcas
    assert nz.marcas(nombre, True) == marcas_truncado
    assert nz.compacto(nombre) == compacto


# ── Normalizador: países ─────────────────────────────────────────────────────────────────────────
def test_paises_resuelve_el_alias_declarado_y_deja_vacios_los_vacios(nz):
    # Regla: ISO3 + nombre canónico; los vacíos dan '' en las dos columnas y no son error.
    serie = pd.Series(["España", "FRANCE", "", None, " Spain ", "Francia"], index=list("abcdef"))
    tabla = nz.paises(serie, "ANCLA", "Pais")
    assert tabla.columns.tolist() == ["PAIS_ISO3", "PAIS_FINAL"]
    assert tabla.index.tolist() == list(range(6))  # se rehace el índice
    assert tabla["PAIS_ISO3"].tolist() == ["ESP", "FRA", "", "", "ESP", "FRA"]
    assert tabla["PAIS_FINAL"].tolist() == ["ESPANA", "FRANCIA", "", "", "ESPANA", "FRANCIA"]


def test_paises_solo_con_vacios_no_falla(nz):
    tabla = nz.paises(pd.Series(["", None, "  "]), "ANCLA", "Pais")
    assert tabla["PAIS_ISO3"].tolist() == ["", "", ""]
    assert tabla["PAIS_FINAL"].tolist() == ["", "", ""]


def test_paises_con_grafia_desconocida_detiene_la_corrida_con_sugerencia(nz):
    serie = pd.Series(["España", "Narnia", "Narnia", "Mordor"])
    with pytest.raises(ValueError) as error:
        nz.paises(serie, "ANCLA", "Pais")
    mensaje = str(error.value)
    assert mensaje.startswith(
        "Qué pasó: ANCLA.Pais trae grafías de país que el catálogo no reconoce:\n"
    )
    assert "NARNIA" in mensaje
    assert "MORDOR" in mensaje
    assert "ESPAÑA" not in mensaje
    assert (
        "Por qué importa: el país es evidencia del cruce; sin catalogar, 'France' y 'Francia' no coinciden."
        in mensaje
    )
    assert (
        "Qué hacer: agréguelas en ALIAS_PAISES con su ISO3, p. ej. 'ISO3': ('NARNIA',), 'ISO3': ('MORDOR',). "
        "La sugerencia es una ayuda, no una regla."
    ) in mensaje


def test_paises_limita_la_tabla_de_sugerencias_a_50_y_los_ejemplos_a_5(nz):
    # Regla: sugerir_alias_pais(maximo=50) y los ejemplos del «Qué hacer» son los 5 primeros (los más frecuentes).
    desconocidas = [
        f"PAISFALSO{k:02d}" for k in range(60) for _ in range(k + 1)
    ]  # la k-ésima aparece k+1 veces
    with pytest.raises(ValueError) as error:
        nz.paises(pd.Series(desconocidas), "ANCLA", "Pais")
    lineas = str(error.value).splitlines()
    assert (
        len(lineas) == 1 + 1 + 50 + 2
    )  # qué pasó, encabezado, 50 filas, por qué importa, qué hacer
    assert lineas[2].split()[0] == "PAISFALSO59"  # las más frecuentes primero
    assert lineas[-1].startswith(
        "Qué hacer: agréguelas en ALIAS_PAISES con su ISO3, p. ej. "
        "'ISO3': ('PAISFALSO59',), 'ISO3': ('PAISFALSO58',), 'ISO3': ('PAISFALSO57',), "
        "'ISO3': ('PAISFALSO56',), 'ISO3': ('PAISFALSO55',). La sugerencia"
    )


def test_paises_sin_el_alias_de_la_configuracion_pide_agregarlo():
    # Regla: 'FRANCE' no está en el catálogo de la librería: sin ALIAS_PAISES la corrida se detiene.
    sin_alias = Normalizador(_config(alias_paises={}))
    with pytest.raises(ValueError) as error:
        sin_alias.paises(pd.Series(["FRANCE"]), "ORBIS", "Country")
    assert "ORBIS.Country trae grafías de país que el catálogo no reconoce" in str(error.value)
    assert "'ISO3': ('FRANCE',)" in str(error.value)


def test_hallazgo_f3_el_marcador_sin_pais_sale_como_si_fuera_una_grafia_del_usuario(nz):
    # Hallazgo F3: los vacíos se reemplazan por «SIN PAIS» antes de canonizar; ese marcador tampoco se
    # clasifica y, si hay otra grafía desconocida, aparece en la tabla de sugerencias y en los ejemplos.
    with pytest.raises(ValueError) as error:
        nz.paises(pd.Series(["Narnia", ""]), "ANCLA", "Pais")
    mensaje = str(error.value)
    assert "SIN PAIS" in mensaje
    assert "'ISO3': ('SIN PAIS',)" in mensaje


# ── preparar_fuente ──────────────────────────────────────────────────────────────────────────────
def _entrada_completa() -> pd.DataFrame:
    return _entrada(
        Empresa=[
            "Zelvora Alimentos S.A. (Dulcevent)",
            "(Peluvika Dulces)",
            "Quelmar Cargo Logistica SL",
            None,
            "Tavorín Conservas Marinas Norte Ibérica",
            "Kramento Foods",
        ],
        Pais=["España", "Spain", "FRANCE", "", None, "Francia"],
        HQ=["Spain", "", "Alemania", "Germany", None, "France"],
        Grupo=[
            "Zelvora Alimentos SA",
            "Otro Grupo (x)",
            None,
            "",
            "Tavorín Conservas Marinas Norte Ibérica",
            NAN,
        ],
        Web=[
            "https://www.zelvora.es/inicio?x=1",
            "gmail.com",
            "http://tienda.quelmar.co.uk/a",
            None,
            "n.a.",
            "www3.kramento.com:8080/x",
        ],
        Mail=[
            "info@zelvora.es",
            "pepe@gmail.com",
            "x@mail.quelmar.co.uk",
            None,
            "sin correo",
            "A@B.COM",
        ],
        Tel=["'+34 961 234 567", "123", None, "0034961999999", "  ", "(961) 234-567"],
        Ciudad=["Valencia", "bilbao", None, "Zürich", "", "Málaga"],
        Dir=["Calle 1", None, "", "x", "y", "z"],
        NIF=["B-1234 5678", None, "", "x.y", "ab-12", 5],
        Tam=["12", "n.a.", "n.s.", "-", None, 3.5],
    )


#: Salida del motor original para ``_entrada_completa()`` y ``_fuente()``, columna por columna.
ESPERADO_COMPLETO = {
    "FUENTE": ["ANCLA", "ANCLA", "ANCLA", "ANCLA", "ANCLA", "ANCLA"],
    "ID_REGISTRO": ["ANC-F0002", "ANC-F0003", "ANC-F0004", "ANC-F0005", "ANC-F0006", "ANC-F0007"],
    "FILA_EXCEL": [2, 3, 4, 5, 6, 7],
    "NOMBRE_ORIGINAL": [
        "Zelvora Alimentos S.A. (Dulcevent)",
        "(Peluvika Dulces)",
        "Quelmar Cargo Logistica SL",
        "",
        "Tavorín Conservas Marinas Norte Ibérica",
        "Kramento Foods",
    ],
    "NOMBRE_NORM": [
        "ZELVORA ALIMENTOS",
        "PELUVIKA DULCES",
        "QUELMAR CARGO LOGISTICA",
        "",
        "TAVORIN CONSERVAS MARINAS NORTE IBERICA",
        "KRAMENTO FOODS",
    ],
    "ALIAS_NORM": ["DULCEVENT", "", "", "", "", ""],
    "GRUPO_ORIGINAL": [
        "Zelvora Alimentos SA",
        "Otro Grupo (x)",
        "",
        "",
        "Tavorín Conservas Marinas Norte Ibérica",
        "",
    ],
    "GRUPO_NORM": ["", "OTRO GRUPO", "", "", "", ""],
    "PAIS_ORIGINAL": ["España", "Spain", "FRANCE", "", "", "Francia"],
    "PAIS_ISO3": ["ESP", "ESP", "FRA", "", "", "FRA"],
    "PAIS_CANONICO": ["ESPANA", "ESPANA", "FRANCIA", "", "", "FRANCIA"],
    "PAISES_EVIDENCIA": ["ESP", "ESP", "DEU|FRA", "DEU", "", "FRA"],
    "WEB": [
        "https://www.zelvora.es/inicio?x=1",
        "gmail.com",
        "http://tienda.quelmar.co.uk/a",
        "",
        "n.a.",
        "www3.kramento.com:8080/x",
    ],
    "DOMINIO_WEB": ["zelvora.es", "", "quelmar.co.uk", "", "", "kramento.com"],
    "EMAIL": [
        "info@zelvora.es",
        "pepe@gmail.com",
        "x@mail.quelmar.co.uk",
        "",
        "sin correo",
        "A@B.COM",
    ],
    "DOMINIO_EMAIL": ["zelvora.es", "", "quelmar.co.uk", "", "", "b.com"],
    "TELEFONO": ["+34 961 234 567", "123", "", "0034961999999", "", "(961) 234-567"],
    "TEL_DIGITOS": ["61234567", "", "", "61999999", "", "61234567"],
    "CIUDAD": ["Valencia", "bilbao", "", "Zürich", "", "Málaga"],
    "CIUDAD_NORM": ["VALENCIA", "BILBAO", "", "ZURICH", "", "MALAGA"],
    "DIRECCION": ["Calle 1", "", "", "x", "y", "z"],
    "ID_COMPARABLE": ["B12345678", "", "", "XY", "AB12", "5"],
    "TAMANO": [12.0, -1.0, -1.0, -1.0, -1.0, 3.5],
    "TRUNCADO": [True, False, True, False, True, False],
    "INTERMEDIARIO": [False, False, True, False, False, False],
    "SIN_NOMBRE_UTIL": [False, False, False, True, False, False],
}


@pytest.fixture(scope="module")
def corrida_completa() -> SimpleNamespace:
    """UNA corrida de ``preparar_fuente`` con todas las variables declaradas (cada una cuesta ~0,75 s)."""
    entrada = _entrada_completa()
    copia = entrada.copy()
    salida = preparar_fuente(entrada, _fuente(), Normalizador(_config()))
    return SimpleNamespace(entrada=entrada, copia=copia, salida=salida)


def test_preparar_fuente_completa_coincide_con_el_motor_original(corrida_completa):
    # La instantánea ejerce a la vez nombre/alias, grupo, país y país extra, contacto, tamaño y banderas.
    out = corrida_completa.salida
    assert out.columns.tolist() == COLUMNAS_PREPARADAS
    assert {c: out[c].tolist() for c in out.columns} == ESPERADO_COMPLETO
    assert out["TAMANO"].dtype == "float64"
    assert [str(out[c].dtype) for c in ("TRUNCADO", "INTERMEDIARIO", "SIN_NOMBRE_UTIL")] == [
        "bool"
    ] * 3
    assert out.index.tolist() == list(range(6))


def test_preparar_fuente_no_modifica_la_entrada(corrida_completa):
    pd.testing.assert_frame_equal(corrida_completa.entrada, corrida_completa.copia)


def test_columnas_que_la_fuente_no_declara_quedan_vacias_o_con_su_valor_neutro():
    # Regla: np.full(len(df), None) para las columnas opcionales sin declarar; TAMANO = -1.0; banderas en False.
    out = _solo_nombre(["Zelvora Alimentos S.A.", "S.L.", "Kramento"])
    assert out.columns.tolist() == COLUMNAS_PREPARADAS
    con_valor_propio = {
        "FUENTE",
        "ID_REGISTRO",
        "FILA_EXCEL",
        "NOMBRE_ORIGINAL",
        "NOMBRE_NORM",
        "ALIAS_NORM",
        "TAMANO",
        "TRUNCADO",
        "INTERMEDIARIO",
        "SIN_NOMBRE_UTIL",
    }
    for columna in COLUMNAS_PREPARADAS:
        if columna not in con_valor_propio:
            assert out[columna].tolist() == ["", "", ""], columna
    assert out["TAMANO"].tolist() == [-1.0, -1.0, -1.0]
    assert out["TRUNCADO"].tolist() == [False, False, False]  # sin largo_truncamiento
    assert out["INTERMEDIARIO"].tolist() == [False, False, False]  # sin detectar_intermediarios
    assert out["NOMBRE_NORM"].tolist() == ["ZELVORA ALIMENTOS", "SL", "KRAMENTO"]


def test_alias_va_a_alias_norm_solo_si_aporta_una_palabra_distintiva():
    # Regla: ALIAS_NORM exige una palabra distintiva y que no sea igual al nombre; si el principal queda
    # vacío, el alias pasa a NOMBRE_NORM.
    out = _solo_nombre(
        [
            "Zelvora Alimentos S.A. (Dulcevent)",  # alias con palabra distintiva
            "Zelvora (Holding)",  # alias genérico: se descarta
            "Kramento (Kramento)",  # alias igual al nombre: se descarta
            "(Peluvika Dulces)",  # sin principal: el alias pasa a principal
            "Orbelia (Dulcevent",  # paréntesis sin cerrar: no hay alias
        ]
    )
    assert out["NOMBRE_NORM"].tolist() == [
        "ZELVORA ALIMENTOS",
        "ZELVORA",
        "KRAMENTO",
        "PELUVIKA DULCES",
        "ORBELIA",
    ]
    assert out["ALIAS_NORM"].tolist() == ["DULCEVENT", "", "", "", ""]


def test_hallazgo_f3_un_nombre_que_queda_solo_en_forma_legal_no_cede_el_lugar_al_alias():
    # Hallazgo F3: el alias solo pasa a principal si NOMBRE_NORM == ''; con «S.L. (Peluvika Dulces)» el
    # nombre queda en 'SL' (SIN_NOMBRE_UTIL=True) y el alias se queda como alias.
    out = _solo_nombre(["S.L. (Peluvika Dulces)"])
    assert out["NOMBRE_NORM"].tolist() == ["SL"]
    assert out["ALIAS_NORM"].tolist() == ["PELUVIKA DULCES"]
    assert out["SIN_NOMBRE_UTIL"].tolist() == [True]


def test_sin_nombre_util_marca_nombres_que_no_tienen_palabras_con_contenido():
    out = _solo_nombre(["Zelvora SA", "S.L.", "", None, "De La Y", "AB 12"])
    assert out["NOMBRE_NORM"].tolist() == ["ZELVORA", "SL", "", "", "DE LA Y", "AB 12"]
    assert out["SIN_NOMBRE_UTIL"].tolist() == [False, True, True, True, True, True]


def test_grupo_norm_queda_vacio_si_es_igual_al_nombre_y_solo_normaliza_el_principal():
    # Regla: GRUPO_NORM = grupo normalizado (sin su paréntesis) salvo que coincida con NOMBRE_NORM → ''.
    f = Fuente(clave="ORBIS", archivo="no_se_lee.xlsx", col_nombre="Empresa", col_grupo="Grupo")
    df = _entrada(
        Empresa=["Zelvora Alimentos SA", "Kramento Foods", "Orbelia", "Tavorín"],
        Grupo=["ZELVORA ALIMENTOS S.A.", "Otro Grupo (x)", None, "  "],
    )
    out = preparar_fuente(df, f, Normalizador(_config()))
    assert out["GRUPO_ORIGINAL"].tolist() == ["ZELVORA ALIMENTOS S.A.", "Otro Grupo (x)", "", ""]
    assert out["GRUPO_NORM"].tolist() == ["", "OTRO GRUPO", "", ""]


def test_paises_evidencia_junta_el_principal_y_los_extra_ordenados():
    # Regla: PAISES_EVIDENCIA = ISO3 del país principal y de cols_pais_extra, sin repetir, ordenados, con '|'.
    f = Fuente(
        clave="FDI",
        archivo="no_se_lee.xlsx",
        col_nombre="Empresa",
        col_pais="Pais",
        cols_pais_extra=("HQ", "HQ2"),
    )
    df = _entrada(
        Empresa=["A1 Zelvora", "A2 Zelvora", "A3 Zelvora", "A4 Zelvora", "A5 Zelvora"],
        Pais=["España", "Spain", "", None, "FRANCE"],
        HQ=["Spain", "Alemania", "Germany", None, ""],
        HQ2=["Francia", "", "", "", "Alemania"],
    )
    out = preparar_fuente(df, f, Normalizador(_config()))
    assert out["PAISES_EVIDENCIA"].tolist() == ["ESP|FRA", "DEU|ESP", "DEU", "", "DEU|FRA"]
    assert out["PAIS_ISO3"].tolist() == ["ESP", "ESP", "", "", "FRA"]


def test_paises_evidencia_sin_col_pais_usa_solo_los_extra():
    f = Fuente(clave="FDI", archivo="no_se_lee.xlsx", col_nombre="Empresa", cols_pais_extra=("HQ",))
    df = _entrada(Empresa=["A1 Zelvora", "A2 Zelvora"], HQ=["Spain", None])
    out = preparar_fuente(df, f, Normalizador(_config()))
    assert out["PAIS_ISO3"].tolist() == ["", ""]
    assert out["PAISES_EVIDENCIA"].tolist() == ["ESP", ""]


def test_pais_desconocido_en_una_columna_extra_nombra_la_fuente_y_la_columna():
    f = Fuente(clave="FDI", archivo="no_se_lee.xlsx", col_nombre="Empresa", cols_pais_extra=("HQ",))
    df = _entrada(Empresa=["A1 Zelvora"], HQ=["Narnia"])
    with pytest.raises(
        ValueError, match=r"FDI\.HQ trae grafías de país que el catálogo no reconoce"
    ):
        preparar_fuente(df, f, Normalizador(_config()))


def test_dominios_genericos_se_anulan_pero_web_y_correo_originales_se_conservan(corrida_completa):
    # Regla: DOMINIO_WEB / DOMINIO_EMAIL en ``dominios_genericos`` quedan en ''; WEB y EMAIL no se tocan.
    out = corrida_completa.salida
    assert out["WEB"].tolist()[1] == "gmail.com"
    assert out["DOMINIO_WEB"].tolist()[1] == ""
    assert out["EMAIL"].tolist()[1] == "pepe@gmail.com"
    assert out["DOMINIO_EMAIL"].tolist()[1] == ""
    sin_genericos = Normalizador(_config(dominios_genericos=frozenset()))
    out2 = preparar_fuente(corrida_completa.entrada, _fuente(), sin_genericos)
    assert out2["DOMINIO_WEB"].tolist()[1] == "gmail.com"
    assert out2["DOMINIO_EMAIL"].tolist()[1] == "gmail.com"


def test_telefono_pierde_el_apostrofo_inicial_y_tel_digitos_son_los_ultimos_8(corrida_completa):
    # Regla: TELEFONO = texto sin ``'`` al comienzo (Excel lo pone para forzar texto) y sin espacios.
    out = corrida_completa.salida
    assert out["TELEFONO"].tolist() == [
        "+34 961 234 567",
        "123",
        "",
        "0034961999999",
        "",
        "(961) 234-567",
    ]
    assert out["TEL_DIGITOS"].tolist() == ["61234567", "", "", "61999999", "", "61234567"]


def test_ciudad_se_pliega_a_ascii_mayusculas_y_la_direccion_y_el_id_se_limpian(corrida_completa):
    out = corrida_completa.salida
    assert out["CIUDAD"].tolist() == ["Valencia", "bilbao", "", "Zürich", "", "Málaga"]
    assert out["CIUDAD_NORM"].tolist() == ["VALENCIA", "BILBAO", "", "ZURICH", "", "MALAGA"]
    assert out["DIRECCION"].tolist() == ["Calle 1", "", "", "x", "y", "z"]
    assert out["ID_COMPARABLE"].tolist() == ["B12345678", "", "", "XY", "AB12", "5"]


def test_tamano_convierte_texto_y_deja_menos_uno_en_ausentes(corrida_completa):
    # Regla: serie_numerica(...).fillna(-1): 'n.a.', 'n.s.', '-' y vacíos → -1.0.
    assert corrida_completa.salida["TAMANO"].tolist() == [12.0, -1.0, -1.0, -1.0, -1.0, 3.5]


def test_truncado_es_largo_igual_o_mayor_al_declarado():
    # Regla: TRUNCADO = len(nombre original recortado) >= largo_truncamiento.
    nombres = ["A" * 19, "B" * 20, "C" * 21, "  " + "D" * 19 + "  "]
    out = _solo_nombre(nombres, largo_truncamiento=20)
    assert out["TRUNCADO"].tolist() == [False, True, True, False]  # el recorte de espacios va antes


def test_intermediario_exige_declarar_la_deteccion_y_un_patron(nz):
    # Regla: INTERMEDIARIO solo si detectar_intermediarios=True y la configuración trae patrones.
    nombres = ["Quelmar Cargo SL", "Quelmar Logistica SL", "CARGO Express"]
    entrada = _entrada(Empresa=nombres)
    activo = Fuente(
        clave="FUENTE", archivo="n.xlsx", col_nombre="Empresa", detectar_intermediarios=True
    )
    apagado = Fuente(clave="FUENTE", archivo="n.xlsx", col_nombre="Empresa")
    sin_patrones = Normalizador(_config(patrones_intermediario=()))
    assert preparar_fuente(entrada, activo, nz)["INTERMEDIARIO"].tolist() == [True, False, True]
    assert preparar_fuente(entrada, apagado, nz)["INTERMEDIARIO"].tolist() == [False] * 3
    assert preparar_fuente(entrada, activo, sin_patrones)["INTERMEDIARIO"].tolist() == [False] * 3


def test_hallazgo_f3_una_fuente_sin_filas_revienta_en_preparar_fuente(nz):
    # Hallazgo F3: con 0 filas np.array([]) es float64 y el `&` del alias lanza TypeError sin mensaje
    # accionable. leer_fuente sí entrega la tabla vacía; el fallo ocurre aquí.
    vacia = _entrada(Empresa=[])
    with pytest.raises(TypeError):
        preparar_fuente(vacia, Fuente(clave="FUENTE", archivo="n.xlsx", col_nombre="Empresa"), nz)
