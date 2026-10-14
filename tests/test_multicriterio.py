"""``record_linkage.evaluation.multicriterio`` y ``.plantilla_base_real`` sobre ``data/ejemplos``.

Qué se vigila: que la lectura de la plantilla, el motor y la medición vivan UNA sola vez en el
paquete (los scripts son envoltorios), que ``MUNICIPIO`` se traduzca a ``CIUDAD`` sin tocar la
configuración, que la validación falle rápido con mensaje accionable, que las cifras de la guía
(``docs/PLANTILLA_BASE_REAL.md`` §5.1 y §8) se reproduzcan: base 281 grupos / P 0,9875 / 18 de 24 y
correo + teléfono firmados 283 / 0,9937 / 20 de 24, separando exactamente los dos falsos positivos
de borde; que la tabla de pares que cambian traiga los valores para auditar a ojo; que una base SIN
verdad se pueda comparar igual; que la exportación deje el Excel y el JSON; y que la partición sea
la misma en subprocesos con ``PYTHONHASHSEED`` distinto (CLAUDE.md §5). Tras la verificación del
2026-10-14 vigila además lo que se corrigió: ids repetidos entre hojas, VERDAD_GRUPOS mínima (sin
CASO) y fuentes sin NIT/MUNICIPIO fallan o avisan con mensaje; una columna extra que no llega al
scorer detiene la medición; el aporte cuenta el lado vacío aparte (``categorical``); el llenado se
redondea como se mide a mano (49,3 %); hay tabla de etiquetas por régimen. Todo es inventado
(vocabulario de prueba del repositorio: KRONOS, ZELVORA, TRIVENTA…).
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest
from cargar_script import cargar_script

from record_linkage.evaluation import multicriterio as mc, plantilla_base_real as pbr
from record_linkage.matching.comparadores_extra import tipos_disponibles

RAIZ = Path(__file__).resolve().parents[1]
DATOS = RAIZ / "data" / "ejemplos"
#: Lo que la guía documenta de la plantilla (§5 y §5.1); si el motor cambia, cambia la guía.
BASE_ESPERADA = {
    "grupos": 281,
    "precision": 0.9875,
    "recall": 0.9754,
    "f1": 0.9814,
    "fp": 4,
    "fn": 8,
}
CONTACTO_ESPERADA = {"grupos": 283, "precision": 0.9937, "recall": 0.9754, "f1": 0.9845, "fp": 2}
HUELLA_BASE = "d858130fd8a2"
HUELLA_CONTACTO = "714c1cc4e2c7"
PARES_QUE_SEPARA_EL_CONTACTO = {("CRM-000037", "RUES-000016"), ("CRM-000051", "CRM-000161")}


@pytest.fixture(scope="module")
def libro() -> pbr.LibroPlantilla:
    return pbr.leer_plantilla(DATOS)


@pytest.fixture(scope="module")
def etiquetas() -> pd.DataFrame:
    et = mc.leer_etiquetas_de(DATOS)
    assert et is not None
    return et


@pytest.fixture(scope="module")
def comparacion(libro, etiquetas, tmp_path_factory) -> mc.ComparacionOffOn:
    """OFF contra ON (correo + teléfono firmados) sobre la plantilla, una sola vez por módulo."""
    return mc.comparar_off_on(
        libro, etiquetas, list(mc.CONTACTO), tmp_path_factory.mktemp("offon"), nombre="B_contacto"
    )


# ── Una sola implementación ───────────────────────────────────────────────


def test_las_constantes_de_la_plantilla_coinciden_con_el_generador() -> None:
    """El generador no depende del paquete, así que repite dos constantes: tienen que coincidir."""
    generador = cargar_script("generar_plantilla_base_real")
    assert tuple(generador.FUENTES) == pbr.FUENTES_PLANTILLA
    assert generador.PREFIJO_HOJA_FUENTE == pbr.PREFIJO_HOJA_FUENTE


def test_los_scripts_son_envoltorios_delgados() -> None:
    """Los scripts importan la lógica del paquete; si la vuelven a definir, hay dos copias."""
    verificador = cargar_script("verificar_plantilla_base_real")
    for nombre in ("leer_plantilla", "correr_motor", "evaluar_casos", "metricas_pares"):
        assert getattr(verificador, nombre) is getattr(pbr, nombre), nombre
    script = cargar_script("multicriterio_plantilla")
    for nombre in ("medir_configuracion", "comparar_con_base", "elegir_recomendada"):
        assert getattr(script, nombre) is getattr(mc, nombre), nombre
    assert script.CONFIGURACIONES is mc.CONFIGURACIONES
    definidas = {
        nodo.name
        for ruta in ("verificar_plantilla_base_real.py", "multicriterio_plantilla.py")
        for nodo in ast.walk(ast.parse((RAIZ / "scripts" / ruta).read_text(encoding="utf-8")))
        if isinstance(nodo, ast.FunctionDef | ast.ClassDef)
    }
    repetidas = definidas & (set(mc.__all__) | set(pbr.__all__))
    assert not repetidas, f"los scripts vuelven a definir: {sorted(repetidas)}"


# ── Configuraciones: traducción y validación ──────────────────────────────


def test_a_columnas_del_motor_traduce_municipio_sin_tocar_la_entrada() -> None:
    entrada = (mc.variable("MUNICIPIO", 0.05, "categoria_tolerante_signed"), mc.CONTACTO[0])
    salida = mc.a_columnas_del_motor(entrada)
    assert [f["column"] for f in salida] == ["CIUDAD", "EMAIL"]
    assert entrada[0]["column"] == "MUNICIPIO", "la configuración no se modifica"
    assert mc.ajustes_del_motor({"llaves_bloqueo": ["MUNICIPIO", "EMAIL"]}) == {
        "llaves_bloqueo": ["CIUDAD", "EMAIL"]
    }
    assert mc.ajustes_del_motor({}) == {}


@pytest.mark.parametrize(
    ("config", "mensaje"),
    [
        (
            mc.Configuracion("x", "", (mc.variable("EMAIL", 0.1, "no_existe"),)),
            "no está registrado",
        ),
        (
            mc.Configuracion("x", "", (mc.variable("FAX", 0.1, "exact_signed"),)),
            "no está en las fuentes",
        ),
        (
            mc.Configuracion("x", "", (mc.variable("CIUDAD", 0.1, "exact_signed"),)),
            "MUNICIPIO, no CIUDAD",
        ),
        (mc.Configuracion("x", "", (("EMAIL", "email_signed", 0.1),)), "una tupla no sirve"),  # type: ignore[arg-type]
        (mc.Configuracion("x", "", ({"column": "EMAIL", "weight": 0.1},)), "le faltan"),
        (
            mc.Configuracion("x", "", (mc.variable("EMAIL", 0.0, "email_signed"),)),
            "debe ser un número > 0",
        ),
        (mc.Configuracion("x", "", (), {"llaves_bloqueo": ["FAX"]}), "llave de bloqueo"),
    ],
)
def test_validar_configuraciones_falla_rapido(config: mc.Configuracion, mensaje: str) -> None:
    columnas = {"ID_REGISTRO_FUENTE", "NIT", "RAZON_SOCIAL", "EMAIL", "MUNICIPIO"}
    with pytest.raises(ValueError, match=mensaje):
        mc.validar_configuraciones((mc.CONFIGURACION_BASE, config), columnas)


def test_validar_configuraciones_rechaza_nombres_repetidos() -> None:
    with pytest.raises(ValueError, match="mismo nombre"):
        mc.validar_configuraciones((mc.CONFIGURACION_BASE, mc.CONFIGURACION_BASE), {"EMAIL"})


def test_las_configuraciones_por_defecto_validan_contra_la_plantilla(libro) -> None:
    columnas = {c for df in libro.fuentes.values() for c in df.columns}
    mc.validar_configuraciones(mc.CONFIGURACIONES, columnas)
    assert mc.es_base(mc.CONFIGURACIONES[0]) and not any(
        mc.es_base(c) for c in mc.CONFIGURACIONES[1:]
    )


def test_leer_configuraciones_ida_y_vuelta(tmp_path: Path) -> None:
    ruta = tmp_path / "configs.json"
    ruta.write_text(json.dumps([c.como_dict() for c in mc.CONFIGURACIONES[:3]]), encoding="utf-8")
    leidas = mc.leer_configuraciones(ruta)
    assert leidas == mc.CONFIGURACIONES[:3]
    ruta.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="lista no vacía"):
        mc.leer_configuraciones(ruta)
    ruta.write_text(json.dumps([{"descripcion": "sin nombre"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="'nombre'"):
        mc.leer_configuraciones(ruta)


def test_descripcion_tipos_sale_del_registro() -> None:
    tabla = mc.descripcion_tipos()
    assert list(tabla.columns) == ["TIPO", "FIRMADO", "DESCRIPCION"]
    assert set(tabla["TIPO"]) == set(tipos_disponibles())
    assert (tabla["DESCRIPCION"].str.len() > 0).all()
    assert "tipo_direccion" in set(tabla["TIPO"]), (
        "los tipo_* del puente de campos están registrados"
    )
    firmado = tabla.set_index("TIPO")["FIRMADO"]
    assert firmado["email_signed"].startswith("sí") and firmado["categorical"] == "no"


# ── Dónde está la plantilla ───────────────────────────────────────────────


def test_localizar_plantilla_elige_la_primera_ruta_que_la_tiene(tmp_path: Path) -> None:
    assert mc.localizar_plantilla([tmp_path / "no_existe", DATOS]) == DATOS
    excel = tmp_path / "mi_base.xlsx"
    pd.DataFrame({"A": [1]}).to_excel(excel, index=False)
    assert mc.localizar_plantilla([tmp_path, excel]) == excel, "una carpeta sin CSV no cuenta"
    with pytest.raises(FileNotFoundError, match="data/ejemplos"):
        mc.localizar_plantilla([tmp_path, tmp_path / "x.csv"])


def test_localizar_plantilla_descarga_los_csv_si_no_hay_copia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin red: ``urlopen`` se sustituye por una lectura local; se verifica qué pide y qué deja."""
    pedidas: list[str] = []

    class _Respuesta:
        def __init__(self, nombre: str) -> None:
            self.nombre = nombre

        def __enter__(self) -> _Respuesta:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return (DATOS / self.nombre).read_bytes()

    def falso_urlopen(url: str, timeout: float) -> _Respuesta:
        assert url.startswith(mc.URL_RAW_PLANTILLA)
        pedidas.append(url[len(mc.URL_RAW_PLANTILLA) :])
        return _Respuesta(pedidas[-1])

    monkeypatch.setattr(mc.datos.urllib.request, "urlopen", falso_urlopen)
    destino = tmp_path / "descarga"
    assert mc.localizar_plantilla([tmp_path / "nada"], descargar_a=destino) == destino
    assert pedidas == list(mc.ARCHIVOS_PLANTILLA)
    assert pbr.leer_plantilla(destino).con_verdad


# ── Pares de prueba y aporte por variable ─────────────────────────────────


def test_pares_de_prueba_de_la_plantilla(libro, etiquetas) -> None:
    positivos, negativos = mc.pares_de_prueba(libro.verdad, libro.casos, etiquetas)
    assert len(positivos) == 325 and len(negativos) == 210
    assert (positivos["ID_1"] < positivos["ID_2"]).all() and (
        negativos["ID_1"] < negativos["ID_2"]
    ).all()
    pos = set(zip(positivos["ID_1"], positivos["ID_2"], strict=True))
    neg = set(zip(negativos["ID_1"], negativos["ID_2"], strict=True))
    assert not pos & neg
    assert pos == pbr.pares_verdaderos(libro.verdad), "una sola regla de «mismo grupo verdadero»"
    with pytest.raises(ValueError, match="columna CASO"):
        mc.pares_de_prueba(libro.verdad.drop(columns=["CASO"]), libro.casos, etiquetas)
    _, sin_etiquetas = mc.pares_de_prueba(libro.verdad, libro.casos, None)
    assert len(sin_etiquetas) < 210, "los IS_MATCH=0 de las etiquetas añaden negativos"


def test_aporte_por_variable_en_la_plantilla(libro, etiquetas) -> None:
    """Las cifras de la guía §5.1: el contacto firmado separa del todo; el exacto no ve el formato."""
    positivos, negativos = mc.pares_de_prueba(libro.verdad, libro.casos, etiquetas)
    aporte = {
        (a["columna"], a["tipo"]): a
        for a in mc.aporte_por_variable(mc.marco_registros(libro), positivos, negativos)
    }
    email = aporte[("EMAIL", "email_signed")]
    assert (email["positivos"]["ambos_llenos"], email["positivos"]["premia"]) == (59, 59)
    assert (email["negativos"]["premia"], email["negativos"]["castiga"]) == (0, 69)
    assert email["separacion_pp"] == 100.0
    assert aporte[("TELEFONO", "telefono_signed")]["positivos"]["premia"] == 114
    assert aporte[("TELEFONO", "categorical_signed")]["positivos"]["premia"] == 4, (
        "CLAUDE.md §5 medido: el comparador exacto ve el formato, no el número"
    )
    assert aporte[("DIRECCION", "categorical_signed")]["positivos"]["premia"] == 1
    assert aporte[("MUNICIPIO", "categoria_tolerante_signed")]["negativos"]["premia"] == 21
    tabla = mc.aporte_como_tabla(list(aporte.values()))
    assert len(tabla) == len(mc.VARIABLES_A_MEDIR) and tabla["LLENADO_%"].between(0, 100).all()
    por_tipo = tabla.set_index(["COLUMNA", "TIPO"])
    assert por_tipo.loc[("EMAIL", "email_signed"), "LLENADO_%"] == 49.3, (
        "264 de 536 = 49,25 %: se redondea el medio hacia arriba, como se mide a mano (no 49,2)"
    )
    assert (tabla["POSITIVOS_PREMIA_UN_LADO_VACIO"] == 0).all(), (
        "los firmados dan 0 con un lado vacío"
    )
    assert (tabla["POSITIVOS_PREMIA"] <= tabla["POSITIVOS_LLENOS"]).all()
    assert (
        email["positivos"]["premia"] + email["positivos"]["castiga"] + email["positivos"]["neutro"]
        == email["positivos"]["ambos_llenos"]
    ), "premia + castiga + neutro = pares con ambos lados"
    markdown = mc.tabla_aporte(list(aporte.values()))
    assert markdown.startswith("| Columna |") and "`email_signed`" in markdown
    assert "| EMAIL | `email_signed` | 49.3 % |" in markdown


def test_aporte_con_un_comparador_no_firmado_cuenta_el_lado_vacio_aparte(libro, etiquetas) -> None:
    """``categorical`` devuelve 0,5 con un lado vacío. Antes se contaba como «premia» sobre todos
    los pares y se dividía por los llenos: POSITIVOS_PREMIA 255 > POSITIVOS_LLENOS 59 y una
    separación de 287 pp. Ahora premia/castiga/neutro son solo sobre los llenos y la trampa se ve
    en su columna."""
    positivos, negativos = mc.pares_de_prueba(libro.verdad, libro.casos, etiquetas)
    tabla = mc.aporte_como_tabla(
        mc.aporte_por_variable(
            mc.marco_registros(libro), positivos, negativos, (("EMAIL", "categorical"),)
        )
    )
    fila = tabla.iloc[0]
    assert fila["POSITIVOS_PREMIA"] <= fila["POSITIVOS_LLENOS"] == 59
    assert fila["POSITIVOS_PREMIA_UN_LADO_VACIO"] > 0 and fila["NEGATIVOS_PREMIA_UN_LADO_VACIO"] > 0
    assert -100 <= fila["SEPARACION_PP"] <= 100


def test_aporte_omite_columnas_ausentes_y_acepta_tipos_propios(libro, etiquetas) -> None:
    positivos, negativos = mc.pares_de_prueba(libro.verdad, libro.casos, etiquetas)
    registros = mc.marco_registros(libro).drop(columns=["SITIO_WEB"])
    aporte = mc.aporte_por_variable(
        registros, positivos, negativos, (("SITIO_WEB", "exact_signed"), ("CIIU", "exact_signed"))
    )
    assert [(a["columna"], a["tipo"]) for a in aporte] == [("CIIU", "exact_signed")]
    with pytest.raises(ValueError, match="no está registrado"):
        mc.aporte_por_variable(registros, positivos, negativos, (("CIIU", "no_existe"),))


# ── Huella ────────────────────────────────────────────────────────────────


def test_huella_particion_ignora_los_nombres_de_grupo() -> None:
    a = pd.DataFrame({"ID_REGISTRO_FUENTE": ["A-1", "A-2", "B-1"], "ID_GRUPO": [7, 7, 9]})
    b = pd.DataFrame({"ID_REGISTRO_FUENTE": ["B-1", "A-2", "A-1"], "ID_GRUPO": ["x", "y", "y"]})
    c = pd.DataFrame({"ID_REGISTRO_FUENTE": ["A-1", "A-2", "B-1"], "ID_GRUPO": [1, 2, 2]})
    assert mc.huella_particion(a) == mc.huella_particion(b)
    assert mc.huella_particion(a) != mc.huella_particion(c)


# ── El motor sobre la plantilla: OFF y ON ─────────────────────────────────


def test_la_base_reproduce_la_guia(comparacion) -> None:
    base = comparacion.base
    assert base.configuracion is mc.CONFIGURACION_BASE and base.con_verdad
    assert base.grupos_predichos == BASE_ESPERADA["grupos"]
    assert {k: base.pares[k] for k in ("precision", "recall", "f1", "fp", "fn")} == {
        k: v for k, v in BASE_ESPERADA.items() if k != "grupos"
    }
    assert base.casos_acertados == 18 and len(base.tabla_casos) == 24
    assert list(base.tabla_casos.columns) == list(pbr.COLUMNAS_TABLA)
    assert base.huella.startswith(HUELLA_BASE)
    assert base.columnas_extra_en_scorer == {}
    assert pbr.pares_predichos(base.correlativa) == base.predichos
    assert "**falló**" in pbr.tabla_markdown(base.tabla_casos)


def test_el_contacto_firmado_sube_la_precision_sin_tocar_el_recall(comparacion) -> None:
    con, base = comparacion.con, comparacion.base
    assert con.grupos_predichos == CONTACTO_ESPERADA["grupos"]
    assert {k: con.pares[k] for k in ("precision", "recall", "f1", "fp")} == {
        k: v for k, v in CONTACTO_ESPERADA.items() if k != "grupos"
    }
    assert con.casos_acertados == 20
    assert con.huella.startswith(HUELLA_CONTACTO)
    assert con.columnas_extra_en_scorer == {"EMAIL": True, "TELEFONO": True}, (
        "las columnas extra tienen que llegar al DataFrame del scorer (L1_prep/data.parquet)"
    )
    cambios = con.cambios_vs_base
    assert cambios["casos_que_mejoran"] == ["ambiguo_revisar", "singleton"]
    assert cambios["casos_que_empeoran"] == [] and cambios["pares_ganados"] == []
    perdidos = {(p["ID_1"], p["ID_2"]) for p in cambios["pares_perdidos"]}
    assert perdidos == PARES_QUE_SEPARA_EL_CONTACTO
    assert all(p["verdadero"] is False for p in cambios["pares_perdidos"])
    # Los 8 FN no cambian: las extras se suman después de los filtros de nombre y de NIT.
    assert base.verdaderos - base.predichos == con.verdaderos - con.predichos
    assert con.etiquetas["global"]["precision"] == base.etiquetas["global"]["precision"], (
        "ETIQUETAS_PARES no ve el multicriterio: los pares movidos están UNCLEAR o sin etiquetar"
    )


def test_la_tabla_de_pares_que_cambian_trae_los_valores_para_auditar(comparacion) -> None:
    tabla = comparacion.pares_que_cambian
    assert set(zip(tabla["ID_1"], tabla["ID_2"], strict=True)) == PARES_QUE_SEPARA_EL_CONTACTO
    assert (tabla["CAMBIO"] == "se separa").all() and (tabla["VERDADERO"] == "no").all()
    for columna in (
        "CASO_1",
        "CASO_2",
        "RAZON_SOCIAL_1",
        "RAZON_SOCIAL_2",
        "EMAIL_2",
        "TELEFONO_1",
    ):
        assert columna in tabla.columns
    fila = tabla.set_index("ID_1").loc["CRM-000051"]
    assert fila["EMAIL_1"].split("@")[1] != fila["EMAIL_2"].split("@")[1], (
        "dominio de correo distinto"
    )
    fila = tabla.set_index("ID_1").loc["CRM-000037"]
    assert fila["TELEFONO_1"] and fila["TELEFONO_2"] and fila["TELEFONO_1"] != fila["TELEFONO_2"]
    con_columnas = mc.tabla_pares_que_cambian(
        comparacion.base, comparacion.con, mc.marco_registros(comparacion.libro), ["SITIO_WEB"]
    )
    assert list(con_columnas.columns) == [
        "CAMBIO",
        "ID_1",
        "ID_2",
        "VERDADERO",
        "SITIO_WEB_1",
        "SITIO_WEB_2",
    ]
    with pytest.raises(ValueError, match="no están en los registros"):
        mc.tabla_pares_que_cambian(
            comparacion.base, comparacion.con, mc.marco_registros(comparacion.libro), ["FAX"]
        )


def test_resumen_y_recomendada(comparacion) -> None:
    tabla = comparacion.resumen
    assert list(tabla["CONFIGURACION"]) == ["A_base", "B_contacto"]
    assert list(tabla["CASOS"]) == ["18/24", "20/24"]
    assert list(tabla["ETIQUETAS_P"]) == [0.9859, 0.9859], "4 decimales, como las cifras de pares"
    assert list(tabla["ETIQUETAS_R"]) == [0.9211, 0.9211]
    por_regimen = comparacion.con.tabla_etiquetas
    assert por_regimen is not None
    assert list(por_regimen["REGIMEN"]) == ["GLOBAL", "CON_ID", "SIN_ID", "MIXTO"]
    assert {"PRECISION_IC95", "RECALL_IC95", "PARES"} <= set(por_regimen.columns)
    assert por_regimen.set_index("REGIMEN").loc["GLOBAL", "PARES"] == 106
    texto = mc.describir_libro(comparacion.libro, mc.leer_etiquetas_de(DATOS))
    assert "CRM (187)" in texto and "con VERDAD_GRUPOS: sí" in texto and "con CASOS: sí" in texto
    assert "106 pares evaluables" in texto and "Sin" not in texto
    markdown = mc.tabla_resumen(comparacion.mediciones)
    assert "`B_contacto`" in markdown and "0.9937" in markdown
    assert mc.elegir_recomendada(comparacion.mediciones) is comparacion.con


def test_elegir_recomendada_prefiere_menos_variables_en_empate(comparacion) -> None:
    con = comparacion.con
    gemela = mc.Medicion(
        configuracion=mc.Configuracion("gemela", "", con.configuracion.extra_features + mc.GEO),
        correlativa=con.correlativa,
        grupos_predichos=con.grupos_predichos,
        predichos=con.predichos,
        huella=con.huella,
        columnas_extra_en_scorer={},
        verdaderos=con.verdaderos,
        pares=dict(con.pares),
        tabla_casos=con.tabla_casos,
    )
    assert mc.elegir_recomendada([gemela, con]) is con
    assert mc.elegir_recomendada([con, gemela]) is con


def test_medicion_como_dict_es_serializable_y_no_lleva_registros(comparacion) -> None:
    casos = mc.caso_por_id(comparacion.libro.verdad)
    d = mc.medicion_como_dict(comparacion.con, casos)
    texto = json.dumps(d, ensure_ascii=False)
    assert d["extra_features_para_el_motor"][0]["column"] == "EMAIL"
    assert {(p["ID_1"], p["ID_2"]) for p in d["fp"]} == {
        ("ADUANAS-000029", "CRM-000077"),
        ("ADUANAS-000029", "RUES-000057"),
    }, "los dos FP que ninguna configuración separa (TRUVEL/TRUVELA: ADUANAS sin contacto)"
    assert len(d["fn"]) == 8 and d["casos_total"] == 24
    assert "RAZON_SOCIAL" not in texto, "cifras e ids, nunca datos de registro"


def test_exportar_comparacion_escribe_excel_y_json(comparacion, tmp_path: Path) -> None:
    rutas = mc.exportar_comparacion(comparacion, tmp_path / "salida", "demo")
    assert [r.name for r in rutas] == ["demo.xlsx", "demo.json"]
    hojas = pd.read_excel(rutas[0], sheet_name=None)
    assert list(hojas) == [
        "RESUMEN",
        "PARES_QUE_CAMBIAN",
        "CORRELATIVA_ON",
        "CORRELATIVA_OFF",
        "CASOS",
        "APORTE",
    ]
    assert len(hojas["PARES_QUE_CAMBIAN"]) == 2 and len(hojas["CORRELATIVA_ON"]) == 536
    assert list(hojas["CASOS"]["ACERTO_CON_EXTRAS"]).count(True) == 20
    salida = json.loads(rutas[1].read_text(encoding="utf-8"))
    assert salida["con_verdad"] and salida["con_casos"] and salida["pares_que_cambian"] == 2
    assert [c["nombre"] for c in salida["configuraciones"]] == ["A_base", "B_contacto"]


# ── Sin verdad: una base real recién estructurada ─────────────────────────


def test_sin_verdad_solo_se_comparan_las_particiones(libro, tmp_path: Path) -> None:
    solo_fuentes = pbr.LibroPlantilla(fuentes=libro.fuentes, verdad=None, casos=None)
    assert not solo_fuentes.con_verdad and not solo_fuentes.con_casos
    assert mc.caso_por_id(None) is None
    texto = mc.describir_libro(solo_fuentes, None)
    assert "Sin VERDAD_GRUPOS" in texto and "Sin ETIQUETAS_PARES" in texto
    comparacion = mc.comparar_off_on(
        solo_fuentes, None, list(mc.CONTACTO), tmp_path / "trabajo", nombre="contacto"
    )
    con = comparacion.con
    assert con.pares is None and con.tabla_casos is None and con.etiquetas is None
    assert con.casos_acertados is None and comparacion.aporte is None
    assert con.huella.startswith(HUELLA_CONTACTO), "la partición no depende de la verdad"
    tabla = comparacion.pares_que_cambian
    assert set(zip(tabla["ID_1"], tabla["ID_2"], strict=True)) == PARES_QUE_SEPARA_EL_CONTACTO
    assert (tabla["VERDADERO"] == "").all() and "CASO_1" not in tabla.columns
    assert "PRECISION" not in comparacion.resumen.columns
    assert all(p["verdadero"] is None for p in con.cambios_vs_base["pares_perdidos"])
    with pytest.raises(ValueError, match="Sin VERDAD_GRUPOS"):
        mc.elegir_recomendada(comparacion.mediciones)
    rutas = mc.exportar_comparacion(comparacion, tmp_path / "salida", "sin_verdad")
    assert pd.ExcelFile(rutas[0]).sheet_names == [
        "RESUMEN",
        "PARES_QUE_CAMBIAN",
        "CORRELATIVA_ON",
        "CORRELATIVA_OFF",
    ]
    exportado = json.loads(rutas[1].read_text(encoding="utf-8"))
    assert exportado["con_verdad"] is False and exportado["con_casos"] is False


def _libro_minimo(libro: pbr.LibroPlantilla) -> pbr.LibroPlantilla:
    """Lo natural en una base real: VERDAD_GRUPOS con solo el id y el grupo, y sin hoja CASOS."""
    assert libro.verdad is not None
    return pbr.LibroPlantilla(
        fuentes=libro.fuentes, verdad=libro.verdad[[pbr.COL_ID, "ID_GRUPO"]], casos=None
    )


def test_verdad_minima_da_precision_y_recall_pero_no_casos(
    libro, comparacion, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Antes reventaba con ``KeyError: 'CASO'`` y, con CASO pero sin hoja CASOS, callaba las cifras.
    El motor no se vuelve a correr: se reutiliza la correlativa base de la comparación."""
    minimo = _libro_minimo(libro)
    assert minimo.con_verdad and not minimo.con_casos
    assert mc.caso_por_id(minimo.verdad) is None
    # Se sustituye en el submódulo donde medir_configuracion busca el nombre (es un paquete).
    monkeypatch.setattr(mc.medicion, "correr_motor", lambda *a, **k: comparacion.base.correlativa)
    m = mc.medir_configuracion(minimo, None, mc.CONFIGURACION_BASE, tmp_path / "base")
    assert m.con_verdad and not m.con_casos and m.tabla_casos is None
    assert {k: m.pares[k] for k in ("precision", "recall", "fp", "fn")} == {
        k: v for k, v in BASE_ESPERADA.items() if k not in ("grupos", "f1")
    }
    tabla = mc.resumen([m])
    assert "PRECISION" in tabla.columns and "CASOS" not in tabla.columns
    assert m.huella.startswith(HUELLA_BASE)
    texto = mc.describir_libro(minimo, None)
    assert "con VERDAD_GRUPOS: sí" in texto and "Sin hoja CASOS" in texto
    with pytest.raises(ValueError, match="columna CASO"):
        pbr.evaluar_casos(comparacion.base.correlativa, minimo.verdad, libro.casos)
    # tabla de pares que cambian: VERDADERO sí/no, sin columnas CASO_*
    cambian = mc.tabla_pares_que_cambian(m, comparacion.con, mc.marco_registros(minimo))
    assert set(cambian["VERDADERO"]) == {"no"} and "CASO_1" not in cambian.columns


def test_medir_configuracion_se_detiene_si_una_columna_extra_no_llega_al_scorer(
    libro, comparacion, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """El aviso del scorer es un log que la medición apaga: la comprobación por archivo tiene que
    detener la corrida, no solo dejar un ``false`` en el JSON."""
    with pytest.raises(FileNotFoundError, match="L1"):
        mc.columnas_que_ve_el_scorer(tmp_path / "vacio", list(mc.CONTACTO))
    mc.exigir_columnas_en_scorer({"EMAIL": True, "TELEFONO": True}, tmp_path)  # no lanza
    monkeypatch.setattr(mc.medicion, "correr_motor", lambda *a, **k: comparacion.base.correlativa)
    monkeypatch.setattr(
        mc.medicion,
        "columnas_que_ve_el_scorer",
        lambda *a, **k: {"EMAIL": False, "TELEFONO": True},
    )
    with pytest.raises(RuntimeError, match=r"\['EMAIL'\] no llegaron al DataFrame del scorer"):
        mc.medir_configuracion(libro, None, mc.CONFIGURACIONES[1], tmp_path / "con")


# ── Lectura: lo que una base real mal armada tiene que oír antes de correr ──


def _escribir_fuente(carpeta: Path, nombre: str, filas: pd.DataFrame) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)
    filas.to_csv(carpeta / f"plantilla_fuente_{nombre}.csv", index=False)


def _fuente(ids: list[str], **columnas: list[str]) -> pd.DataFrame:
    base = {
        "ID_REGISTRO_FUENTE": ids,
        "NIT": ["900000001"] * len(ids),
        "RAZON_SOCIAL": ["KRONOS SAS", "ZELVORA LTDA", "TRIVENTA SA"][: len(ids)],
        "MUNICIPIO": ["BOGOTA"] * len(ids),
    }
    base.update(columnas)
    return pd.DataFrame(base)


def test_leer_plantilla_rechaza_ids_repetidos_entre_hojas(tmp_path: Path) -> None:
    """Cada hoja numerada 1..N: antes corría «OK» con una tabla de pares falsa (tres empresas en
    una celda) y otra huella."""
    _escribir_fuente(tmp_path, "a", _fuente(["1", "2", "3"]))
    _escribir_fuente(tmp_path, "b", _fuente(["1", "2", "3"]))
    with pytest.raises(ValueError, match=r"3 ID_REGISTRO_FUENTE repetidos .*CRM-000017"):
        pbr.leer_plantilla(tmp_path)
    _escribir_fuente(tmp_path, "b", _fuente(["B-1", "B-2", "B-3"]))
    assert list(pbr.leer_plantilla(tmp_path).fuentes) == ["A", "B"]


def test_leer_plantilla_exige_id_y_grupo_en_verdad_grupos(tmp_path: Path) -> None:
    _escribir_fuente(tmp_path, "a", _fuente(["A-1", "A-2"]))
    pd.DataFrame({"ID_REGISTRO_FUENTE": ["A-1", "A-2"], "CASO": ["x", "x"]}).to_csv(
        tmp_path / "plantilla_verdad_grupos.csv", index=False
    )
    with pytest.raises(ValueError, match=r"VERDAD_GRUPOS le faltan \['ID_GRUPO'\]"):
        pbr.leer_plantilla(tmp_path)
    pd.DataFrame({"ID_REGISTRO_FUENTE": ["A-1", "A-2"], "ID_GRUPO": ["G1", "G1"]}).to_csv(
        tmp_path / "plantilla_verdad_grupos.csv", index=False
    )
    leido = pbr.leer_plantilla(tmp_path)
    assert leido.con_verdad and not leido.con_casos, "mínima: pares sí, casos no"


def test_leer_plantilla_avisa_si_una_fuente_no_trae_nit_o_municipio(tmp_path: Path) -> None:
    """Sin NIT el motor corre callado con todo en el régimen sin identificador (medido: 287 grupos
    en vez de 281 sobre la plantilla); ahora lo dice antes de correr."""
    _escribir_fuente(tmp_path, "a", _fuente(["A-1", "A-2"]).drop(columns=["NIT"]))
    with pytest.warns(UserWarning, match=r"no trae \['NIT'\].*solo por nombre.*ren"):
        pbr.leer_plantilla(tmp_path)
    _escribir_fuente(tmp_path, "a", _fuente(["A-1", "A-2"]).drop(columns=["MUNICIPIO"]))
    with pytest.warns(UserWarning, match=r"no trae \['MUNICIPIO'\].*no hay ciudad"):
        pbr.leer_plantilla(tmp_path)
    _escribir_fuente(tmp_path, "a", _fuente(["A-1", "A-2"]))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert list(pbr.leer_plantilla(tmp_path).fuentes) == ["A"]


def test_comparar_off_on_exige_al_menos_una_variable(libro) -> None:
    with pytest.raises(ValueError, match="extra_features está vacío"):
        mc.comparar_off_on(libro, None, [], "/no/se/usa")


# ── Determinismo entre procesos ───────────────────────────────────────────


def _huella_en_subproceso(semilla_hash: str, trabajo: Path) -> dict[str, object]:
    codigo = (
        "import json, sys\n"
        "from record_linkage.evaluation import multicriterio as mc, plantilla_base_real as pbr\n"
        f"libro = pbr.leer_plantilla({str(DATOS)!r})\n"
        f"m = mc.medir_configuracion(libro, None, mc.CONFIGURACIONES[1], {str(trabajo)!r})\n"
        "print(json.dumps({'huella': m.huella, 'grupos': m.grupos_predichos, "
        "'pares': m.pares, 'casos': m.casos_acertados}))\n"
    )
    salida = subprocess.run(
        [sys.executable, "-c", codigo],
        check=True,
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONHASHSEED=semilla_hash),
    )
    return json.loads(salida.stdout.strip().splitlines()[-1])


def test_la_particion_es_la_misma_en_subprocesos_con_semilla_de_hash_distinta(
    tmp_path: Path,
) -> None:
    """Dos llamadas en el mismo proceso no prueban determinismo (CLAUDE.md §5)."""
    a = _huella_en_subproceso("0", tmp_path / "a")
    b = _huella_en_subproceso("1", tmp_path / "b")
    assert a == b
    assert str(a["huella"]).startswith(HUELLA_CONTACTO) and a["casos"] == 20
