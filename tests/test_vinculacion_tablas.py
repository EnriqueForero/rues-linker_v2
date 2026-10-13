"""Tablas de salida del motor de vinculación (``flujo.vinculacion.tablas`` y las hojas que arma).

Los valores esperados NO salen de razonar sobre el código: se observaron ejecutando ``construir_tablas`` del
motor ORIGINAL (la celda ``%%writefile`` del notebook 10) sobre la MISMA corrida —el estado guardado en
``tests/data_sintetica/vinculacion_tablas/``, ver ``apoyo_tablas_vinculacion``— y, para las funciones que el
original solo tenía en línea, ejecutando ese bloque del original sobre las mismas entradas. Cada prueba dice
qué regla ejerce. Las empresas son inventadas.

Las pruebas marcadas «Hallazgo F3» fijan un defecto del original que se conservó a propósito (la extracción no
cambia el comportamiento); cuando se corrija, se corrige también la prueba, con su declaración.
"""

from __future__ import annotations

import apoyo_tablas_vinculacion as at
import numpy as np
import pandas as pd
import pytest
from apoyo_tablas_vinculacion import fila_de
from apoyo_vinculacion import huella_tabla

from record_linkage.flujo.vinculacion.compartido import (
    nombre_columna_ancla,
    prefijo_destino,
    unir_valores,
)
from record_linkage.flujo.vinculacion.config import Fuente
from record_linkage.flujo.vinculacion.empresas import (
    filas_de_la_ancla,
    numerar_empresas,
    representante,
)
from record_linkage.flujo.vinculacion.pares import (
    duplicados_pendientes,
    marcar_principales,
    reglas_de_union,
    vinculos_principales,
)
from record_linkage.flujo.vinculacion.tablas import EstadoTablas, info_destino

NAN = float("nan")

#: Las 15 hojas del Excel, en el orden de ``Resultado.tablas``.
HOJAS = [
    "EXPORTADORES",
    "ENLACES",
    "REVISION",
    "DECISIONES",
    "CONTACTOS",
    "DETALLE_EXPORTADORES",
    "DETALLE_ORBIS",
    "DETALLE_FDI",
    "DEDUPLICACION",
    "VALIDACION",
    "VALIDACION_DETALLE",
    "COMPARACION_REVISIONES",
    "CALIDAD_DATOS",
    "CONTROL_QA",
    "PARAMETROS",
]

#: Columnas de cada hoja que el Excel, ``invariantes`` y ``resumen`` leen por nombre y por posición.
COLUMNAS = {
    "EXPORTADORES": [
        "ID_EXPORTADOR", "NOMBRE_EXPORTADOR", "PAIS", "RESULTADO", "NIVEL_VINCULO", "CONFIANZA",
        "FUENTES_VINCULADAS", "REQUIERE_REVISION", "QUE_REVISAR", "OBSERVACIONES", "OPERACIONES",
        "VALOR_USD", "KILOS_SIN_REPETIDOS", "KILOS", "FLETES", "N_FILAS", "N_GRAFIAS", "GRAFIAS", "HS6",
        "DESCRIPCION_PRODUCTO", "RÉGIMEN", "ORBIS_NIVEL", "ORBIS_CONFIANZA", "ORBIS_NOMBRE", "ORBIS_ID",
        "ORBIS_PAIS", "ORBIS_CIUDAD", "ORBIS_WEB", "ORBIS_MATRIZ", "ORBIS_INGRESOS_USD_M",
        "ORBIS_EMPLEADOS", "ORBIS_N_VINCULOS", "ORBIS_OTROS_VINCULOS", "FDI_NIVEL", "FDI_CONFIANZA",
        "FDI_NOMBRE", "FDI_ID", "FDI_PAIS", "FDI_CIUDAD", "FDI_WEB", "FDI_MATRIZ", "FDI_SECTOR",
        "FDI_PROYECTOS_BUSQUEDA", "FDI_N_VINCULOS", "FDI_OTROS_VINCULOS", "CONTACTO_WEB",
        "CONTACTO_TELEFONO", "CONTACTO_EMAIL", "NOMBRE_ESTANDARIZADO",
    ],
    "ENLACES": [
        "ID_EXPORTADOR", "NOMBRE_EXPORTADOR", "PAIS_EXPORTADOR", "FUENTE", "ID_DESTINO", "NOMBRE_DESTINO",
        "PAIS_DESTINO", "NIVEL", "CONFIANZA", "ESTADO", "ORIGEN", "ES_PRINCIPAL", "MOTIVO",
        "EVIDENCIA_EXTRA", "MISMO_PAIS", "GRAFIA_EXPORTADOR", "NOMBRE_COMPARADO_EXPORTADOR",
        "NOMBRE_COMPARADO_DESTINO", "COMPARTIDAS", "SOBRA_EXPORTADOR", "SOBRA_DESTINO",
        "SCORE_RUES_LINKER", "SIM_TOKENS_IDF", "JARO_WINKLER", "NIVEL_AUTO", "BANDA_AUTO",
        "DECISION_REVISION", "REVISADO_POR", "RAZON_REVISION", "CIUDAD_DESTINO", "WEB_DESTINO",
        "MATRIZ_DESTINO", "ENTIDAD_DESTINO",
    ],
    "REVISION": [
        "PRIORIDAD", "ESTADO_REVISION", "PREGUNTA", "SUGERENCIA_MOTOR", "MOTIVO", "ESTADO_ACTUAL", "TIPO",
        "FUENTE", "CLAVE_A", "NOMBRE_A", "CLAVE_B", "NOMBRE_B", "DECISION", "AUTOR", "FECHA", "RAZON",
        "ID_EXPORTADOR", "OPERACIONES_EXPORTADOR", "PAIS_EXPORTADOR", "PAIS_DESTINO", "MISMO_PAIS",
        "CIUDAD_DESTINO", "WEB_DESTINO", "MATRIZ_DESTINO", "COMPARTIDAS", "SOBRA_EXPORTADOR",
        "SOBRA_DESTINO", "SCORE_RUES_LINKER", "EVIDENCIA_EXTRA",
    ],
    "DECISIONES": [
        "TIPO", "FUENTE", "CLAVE_A", "NOMBRE_A", "CLAVE_B", "NOMBRE_B", "DECISION", "AUTOR", "FECHA",
        "RAZON", "ARCHIVO_ORIGEN",
    ],
    "CONTACTOS": [
        "ID_EXPORTADOR", "NOMBRE_EXPORTADOR", "FUENTE", "ID_DESTINO", "NOMBRE_DESTINO", "NIVEL", "PAIS",
        "CIUDAD", "TIPO_CONTACTO", "VALOR_CONTACTO",
    ],
    "DETALLE_EXPORTADORES": [
        "ID_EXPORTADOR", "NOMBRE_EXPORTADOR", "FILA_EXCEL", "ID_REGISTRO", "Empresa", "País de Empresa",
        "Operaciones", "Valor U$S", "Kilos", "Fletes", "HS6", "Descripción", "Régimen", "NIF", "Teléfono",
        "Correo", "Sitio web", "Ciudad", "NOMBRE_NORMALIZADO", "TRUNCADO", "AGENTE_DE_CARGA", "RESULTADO",
        "NIVEL_VINCULO", "CONFIANZA", "FUENTES_VINCULADAS", "ORBIS_NOMBRE", "FDI_NOMBRE", "KILOS_REPETIDO",
    ],
    "DETALLE_ORBIS": [
        "ID_REGISTRO", "FILA_EXCEL", "NOMBRE", "NOMBRE_NORMALIZADO", "PAIS", "PAIS_ISO3", "IDS_EXPORTADOR",
        "EXPORTADORES_VINCULADOS", "NIVEL_MAS_ALTO", "ID_ENTIDAD", "CIUDAD", "WEB", "EMAIL", "TELEFONO",
        "DIRECCION", "MATRIZ", "INGRESOS_USD_M", "EMPLEADOS",
    ],
    "DETALLE_FDI": [
        "ID_REGISTRO", "FILA_EXCEL", "NOMBRE", "NOMBRE_NORMALIZADO", "PAIS", "PAIS_ISO3", "IDS_EXPORTADOR",
        "EXPORTADORES_VINCULADOS", "NIVEL_MAS_ALTO", "ID_ENTIDAD", "CIUDAD", "WEB", "EMAIL", "TELEFONO",
        "DIRECCION", "MATRIZ", "SECTOR", "PROYECTOS_BUSQUEDA",
    ],
    "DEDUPLICACION": [
        "FUENTE", "ENTIDAD", "NOMBRE_ENTIDAD", "REGISTRO", "NOMBRE_REGISTRO", "N_FILAS", "FORMA_LEGAL",
        "COMO_SE_UNIO",
    ],
    "VALIDACION": [
        "FUENTE_DESTINO", "VINCULOS_AUTOMATICOS_A_B", "VINCULOS_REFERENCIA", "ACIERTOS", "PRECISION",
        "PRECISION_IC95", "RECALL", "RECALL_IC95", "F1", "RECALL_CONTANDO_CANDIDATOS_C",
        "NIVEL_CORRECTO_EN_ACIERTOS", "AUTOMATICOS_ENTRE_PAISES", "DESCARTES_EN_REFERENCIA",
    ],
    "VALIDACION_DETALLE": [
        "FUENTE_DESTINO", "EXPORTADOR", "TIPO", "ID_DESTINO", "NOMBRE_DESTINO", "NIVEL_AUTO", "REFERENCIA",
    ],
    "CALIDAD_DATOS": ["FUENTE", "TEMA", "HALLAZGO", "FILAS_AFECTADAS", "IMPACTO", "QUE_HACER", "EJEMPLOS"],
    "CONTROL_QA": ["CONTROL", "CUMPLE", "VALOR", "DETALLE", "ESTADO"],
    "PARAMETROS": ["PARAMETRO", "VALOR"],
}  # fmt: skip

#: Huella de cada tabla de la corrida (``apoyo_tablas_vinculacion.huellas``) según el motor ORIGINAL, salvo los cambios
#: declarados: ``resumen`` lleva el texto declarado (Quelmar frente a Zelvora) en lugar de las dos marcas reales, y
#: ``CONTROL_QA`` y ``resumen`` ya no cuentan como FALLA los A justificados por identificador (F3.1b, M-a).
HUELLAS_MOTOR_ORIGINAL = {
    "EXPORTADORES": "0505435781ada4b5",
    "ENLACES": "35f6567cd34856e6",
    "REVISION": "df5d717a8a0338b0",
    "DECISIONES": "afbef6252ca7f7ed",
    "CONTACTOS": "8eb2f1cfd724890c",
    "DETALLE_EXPORTADORES": "0990a5666ba2d0da",
    "DETALLE_ORBIS": "5a1341cfc9749e19",
    "DETALLE_FDI": "c334634b96bbed33",
    "DEDUPLICACION": "e95cbd003fbfae56",
    "VALIDACION": "15dc09302273ef7e",
    "VALIDACION_DETALLE": "2b4a608d23f99c1b",
    "COMPARACION_REVISIONES": "9af061aa3114d889",
    "CALIDAD_DATOS": "12cd6a8697f461c6",
    "CONTROL_QA": "e888a538f0afa81a",
    "PARAMETROS": "79a7c469953adec8",
    "resumen": "1cb3b169c2ed733c",
    "por_pais": "803e0ea6df8cc314",
}


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def corrida(tmp_path_factory):
    """La corrida guardada (``tests/data_sintetica/vinculacion_tablas``); las pruebas la copian, no la tocan."""
    return at.cargar_corrida(tmp_path_factory.mktemp("vinculacion_tablas"))


@pytest.fixture(scope="module")
def base(corrida):
    """``construir_tablas`` sobre la corrida intacta."""
    return corrida.construir()


def exportador(res, nombre):
    """La fila de EXPORTADORES de la empresa que se llama ``nombre``."""
    return fila_de(res.tablas["EXPORTADORES"], "NOMBRE_EXPORTADOR", nombre)


def vacio(valor):
    """True si ``valor`` es un ausente de pandas (``NaN``/``None``/``NA``)."""
    return bool(pd.isna(valor))


# ── forma del resultado ───────────────────────────────────────────────────────


def test_las_hojas_salen_en_el_orden_del_excel(base):
    """Regla: ``Resultado.tablas`` trae las 15 hojas en el orden del Excel, con un DETALLE por destino."""
    assert list(base.tablas) == HOJAS


@pytest.mark.parametrize("hoja", sorted(COLUMNAS))
def test_cada_hoja_conserva_sus_columnas_y_su_orden(base, hoja):
    """Regla: el orden de las columnas ES contrato (el Excel y los controles las leen por nombre y posición)."""
    assert list(base.tablas[hoja].columns) == COLUMNAS[hoja]


def test_las_huellas_de_las_tablas_coinciden_con_el_motor_original(base):
    """Regla: las 15 tablas, el resumen y la cobertura por país dan lo mismo que el motor original."""
    assert at.huellas(base) == HUELLAS_MOTOR_ORIGINAL


def test_el_resultado_trae_la_configuracion_los_avisos_y_los_controles(corrida, base):
    """Regla: ``Resultado`` arrastra ``cfg``, los avisos y los segundos tal cual y deja las tablas en sus campos."""
    assert base.cfg is corrida.cfg
    assert base.avisos == corrida.avisos
    assert len(base.avisos) == 4
    assert base.segundos == at.SEGUNDOS
    assert base.tablas["CONTROL_QA"] is base.invariantes
    assert base.tablas["CALIDAD_DATOS"] is base.calidad
    assert base.tablas["PARAMETROS"] is base.parametros
    assert base.tablas["VALIDACION"] is base.validacion
    # El caso del identificador comparable (A con palabras que sobran) incumplía un control crítico en el motor
    # original; F3.1b (M-a) lo justifica por el identificador y ya no es FALLA.
    fallas = base.invariantes.loc[base.invariantes["ESTADO"] == "FALLA", "CONTROL"].tolist()
    assert fallas == []
    assert base.todo_ok is True


def test_deja_id_ent_y_vinc_en_el_estado_interno(corrida):
    """Regla: ``construir_tablas`` deja en ``interno`` dos claves que otros leen después: ``id_ent`` y ``vinc``."""
    c = corrida.copia()
    res = at.construir_tablas(c.cfg, c.interno, c.val, c.avisos, at.SEGUNDOS)
    assert res.internos is c.interno
    assert len(c.interno["id_ent"]) == 79
    assert sorted(c.interno["id_ent"].values())[:2] == ["EXP-0001", "EXP-0002"]
    assert c.interno["id_ent"][78] == "EXP-0001"
    vinc = c.interno["vinc"]
    assert len(vinc) == 35
    assert vinc["ES_PRINCIPAL"].dtype == bool
    assert int(vinc["ES_PRINCIPAL"].sum()) == 26


def test_no_modifica_las_tablas_de_entrada(corrida):
    """Regla: trabaja sobre copias; los pares, las fuentes y las grafías de entrada quedan intactos."""
    c = corrida.copia()
    antes = {k: c.interno[k].copy() for k in ("L", "graf", "dec", "pend_a", "un_a")}
    prep = {k: v.copy() for k, v in c.interno["prep"].items()}
    at.construir_tablas(c.cfg, c.interno, c.val, c.avisos, at.SEGUNDOS)
    for k, tabla in antes.items():
        pd.testing.assert_frame_equal(c.interno[k], tabla)
    assert "ES_PRINCIPAL" not in c.interno["L"].columns
    assert "ENT" not in c.interno["graf"].columns
    for k, tabla in prep.items():
        pd.testing.assert_frame_equal(c.interno["prep"][k], tabla)


# ── EXPORTADORES · diagnóstico de cada empresa ────────────────────────────────

#: (empresa, RESULTADO, NIVEL_VINCULO, CONFIANZA, FUENTES_VINCULADAS, REQUIERE_REVISION)
DIAGNOSTICO = [
    pytest.param("Kramento Foods S.L.", "VINCULADO", "EMPRESA", "ALTA", "ORBIS", "NO", id="vinculada_a"),
    pytest.param("Mirelda Chocolates", "VINCULADO", "EMPRESA", "MEDIA", "ORBIS", "SÍ", id="vinculada_b"),
    pytest.param("Holvexa Foods GmbH", "VINCULADO", "GRUPO", "MEDIA", "ORBIS", "SÍ", id="solo_grupo_b"),
    pytest.param("Korimba Quesos", "VINCULADO", "GRUPO", "ALTA", "ORBIS", "NO", id="grupo_decidido"),
    pytest.param("Zelvora Alimentos S.A.", "VINCULADO", "GRUPO", "ALTA", "ORBIS", "NO", id="nivel_corregido"),
    pytest.param("Nolmendo Cervezas", "VINCULADO", "EMPRESA", "ALTA", "FDI MARKETS", "NO", id="solo_fdi"),
    pytest.param(
        "Vantiko Chocolates S.A.", "VINCULADO", "EMPRESA", "MEDIA", "ORBIS + FDI MARKETS", "SÍ", id="dos_fuentes"
    ),
    pytest.param("Fervano Bebidas", "CANDIDATO POR REVISAR", "", "", "", "SÍ", id="candidato_c"),
    pytest.param("Dromanto Vinagres", "CANDIDATO POR REVISAR", "", "", "", "SÍ", id="candidato_y_duplicados"),
    pytest.param("RASPIDO FRUTAS SA", "SIN COINCIDENCIA", "", "", "", "NO", id="sin_coincidencia"),
    pytest.param("Cravinto Foods", "SIN COINCIDENCIA", "", "", "", "SÍ", id="sin_vinculo_pero_por_revisar"),
]  # fmt: skip


@pytest.mark.parametrize("nombre, resultado, nivel, confianza, fuentes, revisar", DIAGNOSTICO)
def test_diagnostico_de_la_empresa(base, nombre, resultado, nivel, confianza, fuentes, revisar):
    """Reglas: ``RESULTADO`` (VINCULADO / CANDIDATO POR REVISAR / SIN COINCIDENCIA), el nivel más alto de sus
    vínculos, ``CONFIANZA`` ALTA solo si TODOS los principales lo son (si no, MEDIA), las fuentes con vínculo y
    ``REQUIERE_REVISION``."""
    f = exportador(base, nombre)
    assert (f["RESULTADO"], f["NIVEL_VINCULO"], f["CONFIANZA"]) == (resultado, nivel, confianza)
    assert (f["FUENTES_VINCULADAS"], f["REQUIERE_REVISION"]) == (fuentes, revisar)


def test_la_confianza_de_la_empresa_solo_mira_sus_vinculos_principales(corrida):
    """Regla: ``CONFIANZA`` es ALTA si TODOS los vínculos PRINCIPALES lo son; un vínculo no principal de confianza
    MEDIA no la baja (Nolmendo tiene dos registros en FDI y el principal sigue siendo el de confianza ALTA)."""
    c = at.con_par(corrida, "FDI", "FDI-F0007", CONFIANZA="MEDIA")
    f = exportador(c.construir(), "Nolmendo Cervezas")
    assert (f["CONFIANZA"], f["FDI_CONFIANZA"], f["FDI_N_VINCULOS"]) == ("ALTA", "ALTA", 2)


#: (empresa, QUE_REVISAR) · una por frase, y sus combinaciones en el orden en que salen.
QUE_REVISAR = [
    pytest.param("Mirelda Chocolates", "1 vínculo(s) automático(s) B por confirmar", id="vinculo_b"),
    pytest.param("Quelmar", "4 vínculo(s) automático(s) B por confirmar", id="varios_vinculos_b"),
    pytest.param(
        "PLVK SLU",
        "1 vínculo(s) de revisión asistida por validar; ¿duplicado de «Peluvika Dulces (Dulcevent)»?",
        id="revision_asistida",
    ),
    pytest.param("Fervano Bebidas", "1 candidato(s) C por decidir", id="candidato_c"),
    pytest.param("Tulvanto Aceites", "2 candidato(s) C por decidir; ¿duplicado de «Tulvanto Quesos»?", id="dos_c"),
    pytest.param(
        "Cravinto Foods", "validar la unión de sus grafías (revisión asistida)", id="union_asistida"
    ),
    pytest.param("Fribelia SL", "¿duplicado de «Fribelia S. COOP»?", id="duplicado_pendiente"),
    pytest.param(
        "Castelbro Vinos",
        "1 vínculo(s) automático(s) B por confirmar; 1 candidato(s) C por decidir",
        id="b_y_c",
    ),
    pytest.param(
        "Tulvanto Quesos",
        "1 vínculo(s) automático(s) B por confirmar; 1 candidato(s) C por decidir; ¿duplicado de «Tulvanto Aceites»?",
        id="b_c_y_duplicado",
    ),
    pytest.param("Kramento Foods S.L.", "", id="nada_que_revisar"),
]  # fmt: skip


@pytest.mark.parametrize("nombre, esperado", QUE_REVISAR)
def test_que_revisar_dice_cada_frase_en_su_orden(base, nombre, esperado):
    """Reglas: ``QUE_REVISAR`` junta con «; » las frases de vínculos B (y de revisión asistida), candidatos C,
    unión asistida de grafías y posibles duplicados, en ese orden."""
    assert exportador(base, nombre)["QUE_REVISAR"] == esperado


def test_que_revisar_nombra_a_lo_sumo_tres_duplicados_en_orden_alfabetico(corrida):
    """Regla: «¿duplicado de …?» lista hasta 3 nombres sin repetir, ordenados alfabéticamente (no en el orden en
    que se hallaron los pares): un cuarto nombre se queda fuera si es el último y entra si es el primero."""
    c = at.con_pendiente(corrida, "España|Dromanto Vinagres", "España|Fribelia SL")
    f = exportador(c.construir(), "Dromanto Vinagres")
    assert f["QUE_REVISAR"] == (
        "1 candidato(s) C por decidir; ¿duplicado de «Dromanto Vinagres Artesanales» / "
        "«Dromanto Vinagres Artesanalez» / «Dromanto Vinagress»?"
    )
    c = at.con_pendiente(c, "España|Dromanto Vinagres", "España|BRUMALIA CEREALES S.A.")
    f = exportador(c.construir(), "Dromanto Vinagres")
    assert f["QUE_REVISAR"] == (
        "1 candidato(s) C por decidir; ¿duplicado de «BRUMALIA CEREALES S.A.» / "
        "«Dromanto Vinagres Artesanales» / «Dromanto Vinagres Artesanalez»?"
    )


#: (empresa, OBSERVACIONES)
OBSERVACIONES = [
    pytest.param("Pelmonte Foods", "país sin registros en ORBIS ni FDI MARKETS", id="pais_sin_cobertura"),
    pytest.param(
        "Transportes Rapidos Freight SA",
        "agente de carga: la empresa exportadora real no está identificada",
        id="agente_de_carga",
    ),
    pytest.param(
        "COOPERATIVA DE CONSERVAS DEL CANTAB",
        "nombre truncado a 35 caracteres en el origen",
        id="nombre_truncado",
    ),
    pytest.param("S.L.", "nombre sin palabras identificables", id="sin_nombre_util"),
    pytest.param("Kramento Foods S.L.", "", id="sin_observaciones"),
    pytest.param(
        "Logistica Norte Cargo S.L.",
        "agente de carga: la empresa exportadora real no está identificada",
        id="agente_de_carga_vinculado",
    ),
]  # fmt: skip


@pytest.mark.parametrize("nombre, esperado", OBSERVACIONES)
def test_observaciones_advierten_de_los_datos_de_la_empresa(base, nombre, esperado):
    """Reglas: ``OBSERVACIONES`` avisa de país sin cobertura (en TODOS los destinos), agente de carga,
    nombre truncado y nombre sin palabras identificables."""
    assert exportador(base, nombre)["OBSERVACIONES"] == esperado


def test_el_pais_sin_cobertura_exige_que_ningun_destino_tenga_registros_de_ese_pais(corrida):
    """Regla: «país sin registros en …» solo sale si NINGÚN destino trae registros del país de la empresa: si FDI
    no tiene registros de Italia pero ORBIS sí, las cuatro empresas italianas quedan sin observación."""
    sin_fdi = at.sin_pais_en_destino(corrida, "FDI", "ITA")
    exportadores = sin_fdi.construir().tablas["EXPORTADORES"]
    italianas = exportadores[exportadores["PAIS"] == "Italia"]
    assert italianas["OBSERVACIONES"].tolist() == [""] * 4
    sin_ninguno = at.sin_pais_en_destino(sin_fdi, "ORBIS", "ITA")
    exportadores = sin_ninguno.construir().tablas["EXPORTADORES"]
    italianas = exportadores[exportadores["PAIS"] == "Italia"]
    assert italianas["OBSERVACIONES"].tolist() == ["país sin registros en ORBIS ni FDI MARKETS"] * 4


@pytest.mark.parametrize(
    "campo, observacion",
    [
        ("TRUNCADO", "nombre truncado a 35 caracteres en el origen"),
        ("INTERMEDIARIO", "agente de carga: la empresa exportadora real no está identificada"),
    ],
)
def test_basta_una_grafia_truncada_o_de_agente_de_carga_para_advertirlo(
    corrida, campo, observacion
):
    """Regla: el truncamiento y el agente de carga se advierten si ALGUNA grafía de la empresa lo es (aquí una
    de las tres de Brumalia)."""
    c = at.con_grafia(corrida, "España|Brumalia Cereales", **{campo: True})
    assert exportador(c.construir(), "Brumalia Cereales SA")["OBSERVACIONES"] == observacion


def test_el_nombre_sin_palabras_identificables_exige_que_ninguna_grafia_las_tenga(corrida):
    """Regla: «nombre sin palabras identificables» solo sale si TODAS las grafías de la empresa carecen de palabras
    útiles: con una sola grafía reducida a «SL» entre las tres de Brumalia no sale; con las tres, sí."""
    una = at.con_grafia(corrida, "España|Brumalia Cereales", NOMBRE_NORM="SL")
    assert exportador(una.construir(), "Brumalia Cereales SA")["OBSERVACIONES"] == ""
    todas = una
    for clave in ("España|BRUMALIA CEREALES S.A.", "España|Brumalia Cereales SA"):
        todas = at.con_grafia(todas, clave, NOMBRE_NORM="SL")
    f = exportador(todas.construir(), "Brumalia Cereales SA")
    assert f["OBSERVACIONES"] == "nombre sin palabras identificables"


def test_las_empresas_salen_ordenadas_por_resultado_y_luego_por_operaciones(base):
    """Regla: VINCULADO, CANDIDATO POR REVISAR y SIN COINCIDENCIA; dentro de cada uno, más operaciones primero
    y, a igualdad, el orden de ``ID_EXPORTADOR`` (el orden es estable)."""
    e = base.tablas["EXPORTADORES"]
    assert e["RESULTADO"].value_counts(sort=False).to_dict() == {
        "VINCULADO": 25,
        "CANDIDATO POR REVISAR": 11,
        "SIN COINCIDENCIA": 43,
    }
    assert e["RESULTADO"].tolist() == sorted(
        e["RESULTADO"], key=["VINCULADO", "CANDIDATO POR REVISAR", "SIN COINCIDENCIA"].index
    )
    for _, grupo in e.groupby("RESULTADO", sort=False):
        assert grupo["OPERACIONES"].is_monotonic_decreasing
    vinculadas = e[e["RESULTADO"] == "VINCULADO"]
    assert vinculadas["ID_EXPORTADOR"].tolist()[:4] == [
        "EXP-0001",
        "EXP-0007",
        "EXP-0010",
        "EXP-0014",
    ]
    assert e["ID_EXPORTADOR"].tolist()[25:28] == ["EXP-0020", "EXP-0022", "EXP-0005"]


# ── EXPORTADORES · columnas de la ancla ───────────────────────────────────────


def test_las_metricas_se_suman_por_empresa(base):
    """Regla: cada métrica de la ancla sale sumada sobre las filas de la empresa, como número."""
    f = exportador(base, "Balvarda Mieles SL")
    assert (f["OPERACIONES"], f["VALOR_USD"], f["KILOS"], f["FLETES"]) == (
        6.0,
        36000.0,
        1120.0,
        0.0,
    )
    assert (f["N_FILAS"], f["N_GRAFIAS"]) == (3, 1)
    assert pd.api.types.is_float_dtype(base.tablas["EXPORTADORES"]["OPERACIONES"])


def test_la_cifra_sin_repetidos_va_antes_que_la_suma(base):
    """Regla: si la fuente repite una métrica (Kilos en varias filas de la misma partida), sale primero
    ``<METRICA>_SIN_REPETIDOS``: 500 + 500 + 120 kilos son 620 sin repetidos (la misma empresa, partida y valor)."""
    columnas = list(base.tablas["EXPORTADORES"].columns)
    assert columnas.index("KILOS_SIN_REPETIDOS") == columnas.index("KILOS") - 1
    f = exportador(base, "Balvarda Mieles SL")
    assert (f["KILOS_SIN_REPETIDOS"], f["KILOS"]) == (620.0, 1120.0)
    assert "OPERACIONES_SIN_REPETIDOS" not in columnas


def test_grafias_van_de_la_mas_frecuente_a_la_menos_y_sin_repetir(base):
    """Regla: ``GRAFIAS`` une los nombres de la empresa, más filas primero; ``N_GRAFIAS`` las cuenta."""
    f = exportador(base, "Brumalia Cereales SA")
    assert (f["N_FILAS"], f["N_GRAFIAS"]) == (4, 3)
    assert f["GRAFIAS"] == "Brumalia Cereales SA | BRUMALIA CEREALES S.A. | Brumalia Cereales"
    assert f["KILOS_SIN_REPETIDOS"] == 800.0
    assert f["KILOS"] == 3200.0


def test_las_columnas_de_contexto_unen_los_valores_distintos(base):
    """Regla: cada columna de contexto de la ancla une sus valores distintos con « | » y lleva el nombre
    legible en mayúsculas y con guiones bajos."""
    f = exportador(base, "Balvarda Mieles SL")
    assert f["HS6"] == "170490 | 180632"
    assert f["DESCRIPCION_PRODUCTO"] == "Galletas"
    assert f["RÉGIMEN"] == "EXPORTACION DEFINITIVA"
    assert exportador(base, "Brumalia Cereales SA")["HS6"] == "190590"


def test_las_columnas_de_contexto_unen_hasta_ocho_valores_distintos(corrida):
    """Regla: una columna de contexto de la ancla une hasta 8 valores distintos (el corte con «… (+N)» es el de
    ``unir_valores``): las cuatro filas de Brumalia, con cuatro partidas distintas, salen completas."""
    partidas = zip((33, 34, 35, 36), ("110100", "120200", "130300", "140400"), strict=True)
    c = at.con_ancla(corrida, valores={("HS6", fila): hs for fila, hs in partidas})
    f = exportador(c.construir(), "Brumalia Cereales SA")
    assert f["HS6"] == "110100 | 120200 | 130300 | 140400"


def test_el_nombre_estandarizado_es_el_de_la_grafia_representante(base):
    """Regla: ``NOMBRE`` y ``NOMBRE_ESTANDARIZADO`` salen de la grafía que representa a la empresa."""
    f = exportador(base, "Brumalia Cereales SA")
    assert f["NOMBRE_ESTANDARIZADO"] == "BRUMALIA CEREALES"
    assert f["PAIS"] == "España"
    assert f["ID_EXPORTADOR"] == "EXP-0013"


# ── EXPORTADORES · bloque de cada destino ─────────────────────────────────────


def test_el_bloque_del_destino_trae_el_vinculo_principal(base):
    """Regla: por destino, el nivel, la confianza, el nombre, el id, el país, la ciudad, la web y la matriz del
    vínculo principal, más el contexto declarado (con su nombre legible) y cuántos vínculos tiene."""
    f = exportador(base, "Kramento Foods S.L.")
    assert f["ORBIS_NIVEL"] == "EMPRESA"
    assert f["ORBIS_CONFIANZA"] == "ALTA"
    assert f["ORBIS_NOMBRE"] == "Kramento Foods, S.L.U."
    assert f["ORBIS_ID"] == "ESB0000002"
    assert f["ORBIS_PAIS"] == "Spain"
    assert (f["ORBIS_CIUDAD"], f["ORBIS_WEB"], f["ORBIS_MATRIZ"]) == ("", "", "")
    assert (f["ORBIS_INGRESOS_USD_M"], f["ORBIS_EMPLEADOS"]) == (5.5, 40.0)
    assert (f["ORBIS_N_VINCULOS"], f["ORBIS_OTROS_VINCULOS"]) == (1, "")


def test_el_bloque_lleva_la_ciudad_y_la_web_del_registro(base):
    """Regla: ``<DESTINO>_CIUDAD`` y ``<DESTINO>_WEB`` salen del registro destino del vínculo principal."""
    f = exportador(base, "Vantiko Chocolates S.A.")
    assert (f["ORBIS_CIUDAD"], f["ORBIS_WEB"]) == ("Valencia", "www.vantiko.es")
    assert (f["FDI_CIUDAD"], f["FDI_WEB"]) == ("Valencia", "https://vantiko.es/inicio")


def test_sin_vinculo_el_bloque_queda_vacio_y_lo_numerico_en_nan(base):
    """Regla: sin vínculo con un destino, los textos del bloque son ``''``, las columnas numéricas de contexto
    ``NaN`` y ``N_VINCULOS`` cero."""
    f = exportador(base, "Balvarda Mieles SL")
    assert all(
        f[c] == ""
        for c in ("ORBIS_NIVEL", "ORBIS_CONFIANZA", "ORBIS_NOMBRE", "ORBIS_ID", "FDI_SECTOR")
    )
    assert vacio(f["ORBIS_INGRESOS_USD_M"])
    assert vacio(f["ORBIS_EMPLEADOS"])
    assert vacio(f["FDI_PROYECTOS_BUSQUEDA"])
    assert (f["ORBIS_N_VINCULOS"], f["FDI_N_VINCULOS"]) == (0, 0)
    assert (f["ORBIS_OTROS_VINCULOS"], f["FDI_OTROS_VINCULOS"]) == ("", "")


def test_el_prefijo_es_la_primera_palabra_de_la_etiqueta_del_destino(base):
    """Regla: las columnas de FDI MARKETS se llaman ``FDI_…`` y su contexto trae el sector y los proyectos."""
    f = exportador(base, "Nolmendo Cervezas")
    assert (f["FDI_NIVEL"], f["FDI_CONFIANZA"], f["FDI_ID"], f["FDI_PAIS"]) == (
        "EMPRESA",
        "ALTA",
        "FDI-F0006",
        "España",
    )
    assert (f["FDI_SECTOR"], f["FDI_PROYECTOS_BUSQUEDA"]) == ("Food", 2.0)
    assert not any(c.startswith("FDI_MARKETS") for c in base.tablas["EXPORTADORES"].columns)


def test_n_vinculos_y_otros_vinculos(base):
    """Regla: ``N_VINCULOS`` cuenta todos los vínculos con el destino; ``OTROS_VINCULOS`` lista los que no son
    el principal como «nombre [NIVEL]», hasta 10, unidos con « | »."""
    q = exportador(base, "Quelmar")
    assert (q["ORBIS_NOMBRE"], q["ORBIS_N_VINCULOS"]) == ("QUELMAR SL", 4)
    assert q["ORBIS_OTROS_VINCULOS"] == (
        "QUELMAR LACTEOS SL [GRUPO] | QUELMAR BEBIDAS SL [GRUPO] | QUELMAR CARNICOS SL [GRUPO]"
    )
    n = exportador(base, "Nolmendo Cervezas")
    assert (n["FDI_N_VINCULOS"], n["FDI_OTROS_VINCULOS"]) == (2, "Nolmendo Cervezas [EMPRESA]")
    v = exportador(base, "Vantiko Chocolates S.A.")
    assert (v["FDI_N_VINCULOS"], v["FDI_OTROS_VINCULOS"]) == (2, "Vantiko USA [GRUPO]")


def test_el_principal_gana_por_nivel_aunque_otro_puntue_igual(base):
    """Regla (vínculo principal): EMPRESA antes que GRUPO. Peluvika tiene DULCEVENT SA (EMPRESA) y PELUVIKA SLU (GRUPO)."""
    f = exportador(base, "Peluvika Dulces (Dulcevent)")
    assert (f["ORBIS_NIVEL"], f["ORBIS_NOMBRE"], f["ORBIS_N_VINCULOS"]) == (
        "EMPRESA",
        "DULCEVENT SA",
        2,
    )
    assert f["ORBIS_OTROS_VINCULOS"] == "PELUVIKA SLU [GRUPO]"


def test_el_principal_gana_por_tamano_antes_que_por_puntaje(base):
    """Regla (vínculo principal): el registro destino más GRANDE gana antes que el puntaje. PELVORTA HOLDING SL
    (500 millones) puntúa 0,9250 y PELVORTA IBERIA SA (50 millones) 0,9300: gana el más grande."""
    f = exportador(base, "Pelvorta")
    assert (f["ORBIS_NOMBRE"], f["ORBIS_ID"], f["ORBIS_INGRESOS_USD_M"]) == (
        "PELVORTA HOLDING SL",
        "ESB0000026",
        500.0,
    )
    assert f["ORBIS_OTROS_VINCULOS"] == "PELVORTA IBERIA SA [GRUPO]"


@pytest.mark.parametrize(
    "nombre, id_principal",
    [
        pytest.param("Nolmendo Cervezas", "FDI-F0006", id="empate_total_gana_el_id_menor_fdi"),
        pytest.param("Triventa Bebidas", "ESB9000001", id="empate_total_gana_el_id_menor_orbis"),
        pytest.param(
            "Brandelia Fromages", "FR12345678900012", id="empate_total_gana_el_id_menor_siret"
        ),
    ],
)
def test_si_todo_empata_el_principal_es_el_de_id_menor(base, nombre, id_principal):
    """Regla (vínculo principal): sin diferencia de nivel, confianza, país, tamaño, sobrantes ni puntaje, gana el
    ``ID_DESTINO`` menor."""
    enlaces = base.tablas["ENLACES"]
    mios = enlaces[enlaces["NOMBRE_EXPORTADOR"] == nombre]
    assert mios["ES_PRINCIPAL"].tolist().count("SÍ") == 1
    assert mios.loc[mios["ES_PRINCIPAL"] == "SÍ", "ID_DESTINO"].tolist() == [id_principal]


def test_el_contacto_consolidado_junta_web_telefono_y_correo_de_los_vinculos_empresa(base):
    """Regla: ``CONTACTO_*`` junta (hasta 3, sin repetir) lo de los registros de nivel EMPRESA, el primer destino
    primero: ORBIS antes que FDI."""
    f = exportador(base, "Vantiko Chocolates S.A.")
    assert f["CONTACTO_WEB"] == "www.vantiko.es | https://vantiko.es/inicio"
    assert (f["CONTACTO_TELEFONO"], f["CONTACTO_EMAIL"]) == ("", "")


def test_el_contacto_consolidado_ignora_los_vinculos_de_grupo(base):
    """Regla: un registro vinculado solo como GRUPO no aporta al contacto consolidado de la empresa (sí a la
    hoja CONTACTOS, si es el principal)."""
    f = exportador(base, "Castelbro Vinos")
    assert f["NIVEL_VINCULO"] == "GRUPO"
    assert (f["CONTACTO_WEB"], f["CONTACTO_TELEFONO"], f["CONTACTO_EMAIL"]) == ("", "", "")


def test_el_contacto_consolidado_pone_antes_el_vinculo_principal_de_cada_destino(corrida):
    """Regla: dentro de un mismo destino el contacto del vínculo PRINCIPAL va antes que el de los demás vínculos
    EMPRESA (Nolmendo tiene dos registros en FDI)."""
    c = at.con_registro(corrida, "FDI", "FDI-F0006", WEB="principal.example")
    c = at.con_registro(c, "FDI", "FDI-F0007", WEB="secundaria.example")
    f = exportador(c.construir(), "Nolmendo Cervezas")
    assert f["CONTACTO_WEB"] == "principal.example | secundaria.example"


def test_el_contacto_consolidado_junta_hasta_tres_valores_y_cuenta_los_que_sobran(corrida):
    """Regla: ``CONTACTO_*`` une hasta 3 valores distintos y agrega «… (+N)» con los que se quedan fuera: Quelmar
    con cuatro registros de nivel EMPRESA, cada uno con su sitio web y su teléfono."""
    c = corrida
    for k, id_registro in enumerate(("ESB0000010", "ESB0000011", "ESB0000012", "ESB0000013")):
        c = at.con_registro(
            c, "ORBIS", id_registro, WEB=f"quelmar{k}.example", TELEFONO=f"60000000{k}"
        )
        if k:
            c = at.con_par(c, "ORBIS", id_registro, NIVEL_FINAL="EMPRESA")
    f = exportador(c.construir(), "Quelmar")
    assert f["CONTACTO_WEB"] == "quelmar0.example | quelmar1.example | quelmar2.example | … (+1)"
    assert f["CONTACTO_TELEFONO"] == "600000000 | 600000001 | 600000002 | … (+1)"


# ── vínculo principal por (empresa, destino) ──────────────────────────────────


def par(ent, pos_b, nivel, conf, pais, sobra, score, id_destino, fuente="ORBIS"):
    """Un par ya enriquecido, con solo las columnas que lee ``vinculos_principales``."""
    return {
        "ENT": ent,
        "FUENTE_DESTINO": fuente,
        "pos_b": pos_b,
        "NIVEL_FINAL": nivel,
        "CONFIANZA": conf,
        "MISMO_PAIS": pais,
        "SOBRA_DESTINO": sobra,
        "SCORE_RUES_LINKER": score,
        "ID_DESTINO": id_destino,
    }


UNO = [1.0, 1.0, 1.0]  # tamaño de los tres registros destino de las pruebas: empatan

#: nombre → (pares, tamaños, ID_DESTINO en el orden de salida, ES_PRINCIPAL en ese orden, ES_PRINCIPAL en los
#: pares de entrada). La salida sale del bloque del vínculo principal del motor original sobre estos pares.
PRINCIPAL = {
    "nivel": (
        [par(0, 0, "GRUPO", "ALTA", "SÍ", "", 0.99, "A"), par(0, 1, "EMPRESA", "MEDIA", "NO", "X Y", 0.5, "B")],
        UNO, ["B", "A"], [True, False], [False, True],
    ),
    "confianza": (
        [par(0, 0, "EMPRESA", "MEDIA", "SÍ", "", 0.99, "A"), par(0, 1, "EMPRESA", "ALTA", "NO", "X Y", 0.5, "B")],
        UNO, ["B", "A"], [True, False], [False, True],
    ),
    "mismo_pais": (
        [
            par(0, 0, "EMPRESA", "ALTA", "NO", "", 0.9, "A"),
            par(0, 1, "EMPRESA", "ALTA", "SIN DATO", "", 0.9, "B"),
            par(0, 2, "EMPRESA", "ALTA", "SÍ", "X Y", 0.5, "C"),
        ],
        UNO, ["C", "B", "A"], [True, False, False], [False, False, True],
    ),
    "mismo_pais_desconocido": (
        [par(0, 0, "EMPRESA", "ALTA", "NO", "", 0.9, "A"), par(0, 1, "EMPRESA", "ALTA", "", "", 0.9, "B")],
        UNO, ["B", "A"], [True, False], [False, True],
    ),
    "mismo_pais_desconocido_pierde_con_si": (
        [par(0, 0, "EMPRESA", "ALTA", "", "", 0.9, "A"), par(0, 1, "EMPRESA", "ALTA", "SÍ", "", 0.9, "B")],
        UNO, ["B", "A"], [True, False], [False, True],
    ),
    "confianza_desconocida": (
        [par(0, 0, "EMPRESA", "", "SÍ", "", 0.9, "A"), par(0, 1, "EMPRESA", "MEDIA", "NO", "", 0.9, "B")],
        UNO, ["B", "A"], [True, False], [False, True],
    ),
    "tamano": (
        [par(0, 0, "EMPRESA", "ALTA", "SÍ", "", 0.99, "A"), par(0, 1, "EMPRESA", "ALTA", "SÍ", "X Y Z", 0.5, "B")],
        [10.0, 500.0, 1.0], ["B", "A"], [True, False], [False, True],
    ),
    "sobrantes": (
        [par(0, 0, "EMPRESA", "ALTA", "SÍ", "X Y", 0.99, "A"), par(0, 1, "EMPRESA", "ALTA", "SÍ", "", 0.5, "B")],
        UNO, ["B", "A"], [True, False], [False, True],
    ),
    "sobrantes_ausentes": (
        [par(0, 0, "EMPRESA", "ALTA", "SÍ", "A", 0.9, "A"), par(0, 1, "EMPRESA", "ALTA", "SÍ", np.nan, 0.9, "B")],
        UNO, ["B", "A"], [True, False], [False, True],
    ),
    "score": (
        [par(0, 0, "EMPRESA", "ALTA", "SÍ", "", 0.5, "A"), par(0, 1, "EMPRESA", "ALTA", "SÍ", "", 0.9, "B")],
        UNO, ["B", "A"], [True, False], [False, True],
    ),
    "id": (
        [par(0, 0, "EMPRESA", "ALTA", "SÍ", "", 0.9, "C"), par(0, 1, "EMPRESA", "ALTA", "SÍ", "", 0.9, "B")],
        UNO, ["B", "C"], [True, False], [False, True],
    ),
    "empate_total_gana_el_primero": (
        [par(0, 0, "EMPRESA", "ALTA", "SÍ", "", 0.9, "B"), par(0, 1, "EMPRESA", "ALTA", "SÍ", "", 0.9, "B")],
        UNO, ["B", "B"], [True, False], [True, False],
    ),
    "empresas_y_destinos_independientes": (
        [
            par(0, 0, "EMPRESA", "ALTA", "SÍ", "", 0.9, "A1", "ORBIS"),
            par(0, 1, "GRUPO", "MEDIA", "SÍ", "", 0.9, "A2", "ORBIS"),
            par(0, 0, "GRUPO", "MEDIA", "SÍ", "", 0.9, "F1", "FDI"),
            par(0, 1, "EMPRESA", "MEDIA", "SÍ", "", 0.9, "F2", "FDI"),
            par(1, 2, "GRUPO", "ALTA", "SÍ", "", 0.9, "A3", "ORBIS"),
        ],
        UNO, ["F2", "F1", "A1", "A2", "A3"], [True, False, True, False, True], [True, False, False, True, True],
    ),
    "solo_cuentan_empresa_y_grupo": (
        [
            par(0, 0, "RELACION_PROPIEDAD", "", "SÍ", "", 0.9, "A"),
            par(0, 1, "", "", "SÍ", "", 0.9, "B"),
            par(0, 2, "GRUPO", "MEDIA", "NO", "", 0.9, "C"),
        ],
        UNO, ["C"], [True], [False, False, True],
    ),
}  # fmt: skip


@pytest.mark.parametrize("caso", sorted(PRINCIPAL))
def test_vinculos_principales_aplica_los_desempates_en_su_orden(caso):
    """Reglas: entre los vínculos de una empresa con un destino gana, en este orden: el nivel (EMPRESA antes que
    GRUPO), la confianza (ALTA antes que MEDIA; lo desconocido, al final), el mismo país (SÍ, SIN DATO, NO; lo
    desconocido vale como SIN DATO), el MAYOR tamaño, los MENOS términos sobrantes, el mayor puntaje y el
    ``ID_DESTINO`` menor; con empate total, el primero. Solo cuentan EMPRESA y GRUPO."""
    filas, tamanos, ids, principal, en_pares = PRINCIPAL[caso]
    pares = pd.DataFrame(filas)
    prep = {f: pd.DataFrame({"TAMANO": tamanos}) for f in ("ORBIS", "FDI")}
    vinc = vinculos_principales(pares, prep)
    assert vinc["ID_DESTINO"].tolist() == ids
    assert vinc["ES_PRINCIPAL"].tolist() == principal
    assert not {"_k1", "_k2", "_k3", "_k4", "_k5"} & set(vinc.columns)
    marcar_principales(pares, vinc)
    assert pares["ES_PRINCIPAL"].tolist() == en_pares


def test_vinculos_principales_sin_tamano_en_la_fuente_desempata_por_lo_siguiente():
    """Regla: si ``prep`` no trae ``TAMANO`` el tamaño vale -1 para todos y decide lo que sigue (los sobrantes)."""
    pares = pd.DataFrame(
        [
            par(0, 0, "EMPRESA", "ALTA", "SÍ", "X", 0.9, "A"),
            par(0, 1, "EMPRESA", "ALTA", "SÍ", "", 0.5, "B"),
        ]
    )
    vinc = vinculos_principales(pares, {"ORBIS": pd.DataFrame({"OTRA": [1, 2]})})
    assert vinc["ID_DESTINO"].tolist() == ["B", "A"]
    assert vinc["ES_PRINCIPAL"].tolist() == [True, False]


def test_vinculos_principales_sin_vinculos_devuelve_la_tabla_vacia_con_su_columna():
    """Regla: sin pares vinculados (o sin pares) la salida es la tabla vacía con ``ES_PRINCIPAL``, y la tabla de
    pares queda con ``ES_PRINCIPAL`` en falso."""
    columnas = list(par(0, 0, "", "", "", "", 0.0, "").keys())
    pares = pd.DataFrame(columns=columnas)
    vinc = vinculos_principales(pares, {})
    assert list(vinc.columns) == [*columnas, "ES_PRINCIPAL"]
    assert len(vinc) == 0
    marcar_principales(pares, vinc)
    assert pares["ES_PRINCIPAL"].dtype == bool
    assert len(pares) == 0
    ninguno = pd.DataFrame([par(0, 0, "RELACION_PROPIEDAD", "", "SÍ", "", 0.9, "A")])
    assert len(vinculos_principales(ninguno, {"ORBIS": pd.DataFrame({"TAMANO": [1.0]})})) == 0


# ── pares enriquecidos ────────────────────────────────────────────────────────


def test_un_par_sin_puntaje_se_puntua_para_dejar_constancia(corrida):
    """Regla: los pares que solo vienen de una decisión (sin ``SCORE_RUES_LINKER``) se puntúan contra el nombre de
    la empresa, se rehacen sus rasgos y su motivo dice que lo aportó la revisión; el nivel y la banda
    automáticos no se tocan."""
    c = at.sin_puntaje(
        corrida, [("ORBIS", "ESB0000014"), ("ORBIS", "ESB0000016"), ("FDI", "FDI-F0002")]
    )
    enlaces = c.construir().tablas["ENLACES"]
    motivo = "Sin evidencia automática suficiente por nombre; vínculo aportado por revisión"
    m = fila_de(enlaces, "ID_DESTINO", "ESB0000014")
    assert (m["NIVEL"], m["CONFIANZA"], m["MISMO_PAIS"], m["MOTIVO"]) == (
        "EMPRESA",
        "MEDIA",
        "NO",
        motivo,
    )
    assert (m["NOMBRE_COMPARADO_EXPORTADOR"], m["NOMBRE_COMPARADO_DESTINO"]) == (
        "MIRELDA CHOCOLATES",
    ) * 2
    assert (m["COMPARTIDAS"], m["SOBRA_EXPORTADOR"], m["SOBRA_DESTINO"]) == ("MIRELDA", "", "")
    assert (m["SCORE_RUES_LINKER"], m["SIM_TOKENS_IDF"], m["JARO_WINKLER"]) == (1.0, 1.0, 1.0)
    assert (m["NIVEL_AUTO"], m["BANDA_AUTO"]) == ("EMPRESA", "B")
    h = fila_de(enlaces, "ID_DESTINO", "ESB0000016")
    assert (h["NIVEL"], h["MISMO_PAIS"], h["MOTIVO"]) == ("GRUPO", "NO", motivo)
    v = fila_de(enlaces, "ID_DESTINO", "FDI-F0002")
    assert v["MISMO_PAIS"] == "SÍ"
    assert (v["NOMBRE_COMPARADO_EXPORTADOR"], v["NOMBRE_COMPARADO_DESTINO"]) == (
        "VANTIKO CHOCOLATES",
        "VANTIKO SWEETS",
    )
    assert (v["COMPARTIDAS"], v["SOBRA_EXPORTADOR"], v["SOBRA_DESTINO"]) == (
        "VANTIKO",
        "CHOCOLATES",
        "SWEETS",
    )
    assert (v["SCORE_RUES_LINKER"], v["SIM_TOKENS_IDF"], v["JARO_WINKLER"]) == (
        0.7522,
        0.4253,
        0.8612,
    )


def test_un_par_sin_puntaje_no_supone_que_el_nombre_de_la_empresa_este_truncado(corrida):
    """Regla: los rasgos de un par sin puntaje se piden SIN suponer que el nombre de la empresa se cortó en el
    origen: «GALDIMAR» contra «GALDIMAR HOLDING IBERIA» deja «HOLDING IBERIA» como sobrante del destino (si se
    supusiera truncado, lo posterior a la última palabra emparejada no contaría y no sobraría nada)."""
    c = at.sin_puntaje(corrida, [("ORBIS", "ESB0000018")])
    revision = c.construir().tablas["REVISION"]
    f = fila_de(revision[revision["TIPO"] == "VINCULO"], "CLAVE_B", "ESB0000018")
    assert (f["COMPARTIDAS"], f["SOBRA_EXPORTADOR"], f["SOBRA_DESTINO"]) == (
        "GALDIMAR",
        "",
        "HOLDING IBERIA",
    )


def test_los_pares_con_puntaje_conservan_el_motivo_automatico(base):
    """Regla: los pares que sí traen puntaje conservan su motivo y su evidencia automáticos."""
    m = fila_de(base.tablas["ENLACES"], "ID_DESTINO", "ESB0000014")
    assert m["MOTIVO"] == "Mismas palabras y mismo país; sin país para contrastar"
    assert m["MISMO_PAIS"] == "SIN DATO"
    v = fila_de(base.tablas["ENLACES"], "ID_DESTINO", "FDI-F0002")
    assert (
        v["EVIDENCIA_EXTRA"]
        == "mismo sitio web (vantiko.es) que ORBIS «VANTIKO CHOCOLATES SA» y misma ciudad"
    )


def test_la_grafia_de_cada_par_sale_de_su_clave(base):
    """Regla: ``GRAFIA_EXPORTADOR`` es la grafía de la ancla del par (la parte de ``CLAVE`` tras el país)."""
    enlaces = base.tablas["ENLACES"]
    assert (
        fila_de(enlaces, "ID_DESTINO", "ESB0000030")["GRAFIA_EXPORTADOR"]
        == "Casa Lumbreras Hermanos"
    )
    assert (
        fila_de(enlaces, "ID_DESTINO", "ESB0000015")["GRAFIA_EXPORTADOR"]
        == "Logistica Norte Cargo S.L."
    )


def test_un_par_sin_clave_de_grafia_usa_la_de_la_decision_o_la_representante(corrida):
    """Regla: si el par no trae la clave de su grafía, se toma la de la decisión (``CLAVE_A_DEC``) y, si no hay
    decisión, la de la grafía que representa a la empresa (aquí, «Dromanto Vinagres Artesanales»)."""

    def revision_de_dromanto(c):
        r = c.construir().tablas["REVISION"]
        return fila_de(r[r["TIPO"] == "VINCULO"], "ID_EXPORTADOR", "EXP-0020")

    sin_decision = revision_de_dromanto(
        at.sin_clave_de_grafia(corrida, "Dromanto Vinagres Artesanales")
    )
    assert sin_decision["CLAVE_A"] == "España|Dromanto Vinagres Artesanales"
    assert sin_decision["NOMBRE_A"] == "Dromanto Vinagres Artesanales"
    clave = "España|Dromanto Vinagres Artesanalez"
    con_decision = revision_de_dromanto(
        at.sin_clave_de_grafia(corrida, "Dromanto Vinagres Artesanales", clave)
    )
    assert con_decision["CLAVE_A"] == clave
    assert con_decision["NOMBRE_A"] == "Dromanto Vinagres Artesanalez"


def test_si_todos_los_pares_traen_puntaje_ninguno_se_puntua_de_nuevo(corrida):
    """Regla: un par que ya trae ``SCORE_RUES_LINKER`` conserva lo suyo; solo se puntúan los que no lo traen. Con el
    único par sin puntaje de la corrida puntuado a 0.5, ya no se rehace: sin motivo de revisión ni nombres
    comparados (en la corrida intacta salen con puntaje 0.6375 y el motivo «aportado por revisión»)."""
    c = at.con_pares_puntuados(corrida, 0.5)
    enlaces = c.construir().tablas["ENLACES"]
    f = fila_de(enlaces, "ID_EXPORTADOR", "EXP-0044")
    assert (f["SCORE_RUES_LINKER"], f["MOTIVO"]) == (0.5, "")
    assert vacio(f["NOMBRE_COMPARADO_EXPORTADOR"])
    intacto = fila_de(corrida.construir().tablas["ENLACES"], "ID_EXPORTADOR", "EXP-0044")
    assert intacto["SCORE_RUES_LINKER"] == 0.6375
    assert intacto["MOTIVO"].startswith("Sin evidencia automática suficiente")


def test_si_la_tabla_de_pares_no_trae_puntaje_se_puntuan_todos(corrida):
    """Regla: sin la columna ``SCORE_RUES_LINKER`` todos los pares cuentan como sin puntaje y se puntúan contra el
    nombre de su empresa: todos llevan el motivo «aportado por revisión» y el país se contrasta de nuevo."""
    c = at.sin_columnas_de_pares(corrida, "SCORE_RUES_LINKER")
    enlaces = c.construir().tablas["ENLACES"]
    assert enlaces["MOTIVO"].value_counts().to_dict() == {
        "Sin evidencia automática suficiente por nombre; vínculo aportado por revisión": 36
    }
    f = fila_de(enlaces, "ID_EXPORTADOR", "EXP-0014")
    assert (f["SCORE_RUES_LINKER"], f["MISMO_PAIS"], f["ES_PRINCIPAL"]) == (0.5416, "SÍ", "SÍ")
    assert enlaces["ES_PRINCIPAL"].value_counts().to_dict() == {"SÍ": 26, "NO": 10}


def test_los_pares_sin_columnas_opcionales_las_reciben_vacias(corrida):
    """Regla: si la tabla de pares no trae la clave de la grafía ni las columnas de texto de la revisión (``AUTOR``,
    ``ARCHIVO``) se completan: la grafía sale de la decisión o de la empresa y los textos quedan vacíos."""
    c = at.sin_columnas_de_pares(corrida, "CLAVE_GRAFIA", "AUTOR", "ARCHIVO")
    enlaces = c.construir().tablas["ENLACES"]
    intactos = corrida.construir().tablas["ENLACES"]
    assert enlaces["REVISADO_POR"].unique().tolist() == [""]
    assert enlaces["GRAFIA_EXPORTADOR"].tolist() == intactos["GRAFIA_EXPORTADOR"].tolist()


def test_sin_pares_las_hojas_de_vinculos_salen_vacias_y_todo_queda_sin_coincidencia(corrida):
    """Regla: con la tabla de pares vacía, ENLACES y CONTACTOS salen vacías con sus columnas, EXPORTADORES
    deja a todas las empresas sin coincidencia y REVISION solo trae los duplicados de la ancla."""
    res = at.sin_pares(corrida).construir()
    assert list(res.tablas["ENLACES"].columns) == COLUMNAS["ENLACES"]
    assert (len(res.tablas["ENLACES"]), len(res.tablas["CONTACTOS"])) == (0, 0)
    assert res.tablas["EXPORTADORES"]["RESULTADO"].value_counts().to_dict() == {
        "SIN COINCIDENCIA": 79
    }
    assert res.tablas["REVISION"]["TIPO"].unique().tolist() == ["DUPLICADO"]
    assert len(res.tablas["REVISION"]) == 10
    assert len(res.internos["vinc"]) == 0
    assert "ES_PRINCIPAL" in res.internos["vinc"].columns


# ── ENLACES ───────────────────────────────────────────────────────────────────


def test_enlaces_trae_las_columnas_renombradas_de_cada_vinculo(base):
    """Regla: las columnas internas salen con su nombre de hoja (``FUENTE_DESTINO`` → ``FUENTE``, ``SUP_A`` →
    ``NOMBRE_COMPARADO_EXPORTADOR``, ``AUTOR`` → ``REVISADO_POR``, ``CIUDAD`` → ``CIUDAD_DESTINO``…)."""
    f = fila_de(base.tablas["ENLACES"], "ID_DESTINO", "ESB0000002")
    assert f["ID_EXPORTADOR"] == "EXP-0035"
    assert (f["NOMBRE_EXPORTADOR"], f["PAIS_EXPORTADOR"], f["FUENTE"]) == (
        "Kramento Foods S.L.",
        "España",
        "ORBIS",
    )
    assert (f["NOMBRE_DESTINO"], f["PAIS_DESTINO"]) == ("Kramento Foods, S.L.U.", "Spain")
    assert (f["NIVEL"], f["CONFIANZA"], f["ES_PRINCIPAL"]) == ("EMPRESA", "ALTA", "SÍ")
    assert f["ESTADO"] == "VINCULADO (automático A)"
    assert f["ORIGEN"] == "automático, confirmado por revisión"
    assert (
        f["MOTIVO"]
        == "Mismo nombre tras normalizar (solo cambian forma legal, puntuación o tildes) y mismo país"
    )
    assert f["GRAFIA_EXPORTADOR"] == "Kramento Foods S.L."
    assert (f["NOMBRE_COMPARADO_EXPORTADOR"], f["NOMBRE_COMPARADO_DESTINO"]) == (
        "KRAMENTO FOODS",
    ) * 2
    assert f["COMPARTIDAS"] == "KRAMENTO"
    assert (f["SCORE_RUES_LINKER"], f["SIM_TOKENS_IDF"], f["JARO_WINKLER"]) == (1.0, 1.0, 1.0)
    assert (f["NIVEL_AUTO"], f["BANDA_AUTO"]) == ("EMPRESA", "A")
    assert f["DECISION_REVISION"] == "EMPRESA"
    assert f["REVISADO_POR"] == "(verificar) paneles"
    assert f["RAZON_REVISION"] == "regla EMP_A_FORMA_LEGAL"
    assert (f["CIUDAD_DESTINO"], f["WEB_DESTINO"], f["MATRIZ_DESTINO"]) == ("", "", "")
    assert f["ENTIDAD_DESTINO"] == "ORB-E0002"


def test_enlaces_marca_el_principal_con_si_y_los_demas_con_no(base):
    """Regla: ``ES_PRINCIPAL`` sale como ``SÍ``/``NO``; hay un ``SÍ`` por empresa y destino con vínculos."""
    enlaces = base.tablas["ENLACES"]
    assert enlaces["ES_PRINCIPAL"].value_counts().to_dict() == {"SÍ": 26, "NO": 10}
    por_grupo = enlaces[enlaces["NIVEL"] != "RELACION_PROPIEDAD"].groupby(
        ["ID_EXPORTADOR", "FUENTE"]
    )
    assert (por_grupo["ES_PRINCIPAL"].apply(lambda s: (s == "SÍ").sum()) == 1).all()


def test_enlaces_muestra_la_etiqueta_del_destino_y_ordena_el_principal_primero(base):
    """Regla: ``FUENTE`` lleva la etiqueta (FDI MARKETS); se ordena por empresa, destino (por su clave: FDI antes
    que ORBIS), principal primero, nivel y puntaje de mayor a menor."""
    v = base.tablas["ENLACES"]
    v = v[v["ID_EXPORTADOR"] == "EXP-0059"]
    assert v["FUENTE"].tolist() == ["FDI MARKETS", "FDI MARKETS", "ORBIS"]
    assert v["ID_DESTINO"].tolist() == ["FDI-F0002", "FDI-F0003", "ESB0000029"]
    assert v["ES_PRINCIPAL"].tolist() == ["SÍ", "NO", "SÍ"]
    q = base.tablas["ENLACES"]
    q = q[q["ID_EXPORTADOR"] == "EXP-0047"]
    assert q["NIVEL"].tolist() == ["EMPRESA", "GRUPO", "GRUPO", "GRUPO"]
    assert q["SCORE_RUES_LINKER"].tolist() == [1.0, 0.92, 0.92, 0.9156]


def test_enlaces_ordena_por_nivel_antes_que_por_puntaje(corrida):
    """Regla: tras el principal, los vínculos de una empresa con un destino van por nivel (EMPRESA, GRUPO) y solo
    dentro de cada nivel por puntaje de mayor a menor: un EMPRESA de 0.5 va antes que un GRUPO de 0.92."""
    c = at.con_par(corrida, "ORBIS", "ESB0000013", NIVEL_FINAL="EMPRESA", SCORE_RUES_LINKER=0.5)
    enlaces = c.construir().tablas["ENLACES"]
    quelmar = enlaces[enlaces["NOMBRE_EXPORTADOR"] == "Quelmar"]
    assert quelmar[
        ["ID_DESTINO", "NIVEL", "ES_PRINCIPAL", "SCORE_RUES_LINKER"]
    ].values.tolist() == [
        ["ESB0000010", "EMPRESA", "SÍ", 1.0],
        ["ESB0000013", "EMPRESA", "NO", 0.5],
        ["ESB0000011", "GRUPO", "NO", 0.92],
        ["ESB0000012", "GRUPO", "NO", 0.92],
    ]


def test_enlaces_incluye_las_relaciones_de_propiedad_sin_confianza_ni_principal(base):
    """Regla: ENLACES trae también los pares ``RELACION_PROPIEDAD`` (decididos por revisión): sin confianza y
    nunca principales; no cuentan como vínculo."""
    f = fila_de(base.tablas["ENLACES"], "ID_DESTINO", "ESB0000027")
    assert (f["NIVEL"], f["CONFIANZA"], f["ES_PRINCIPAL"]) == ("RELACION_PROPIEDAD", "", "NO")
    assert f["ESTADO"] == "RELACIÓN DE PROPIEDAD (revisión)"
    assert f["ORIGEN"] == "agregado por revisión"
    assert f["MATRIZ_DESTINO"] == "Solenta Alimentos SA"
    assert (f["NIVEL_AUTO"], f["BANDA_AUTO"]) == ("RELACION_MATRIZ", "C")
    assert exportador(base, "Solenta Alimentos S.A.")["RESULTADO"] == "SIN COINCIDENCIA"


def test_enlaces_no_incluye_candidatos_ni_descartados(base):
    """Regla: solo entran los pares con nivel final EMPRESA, GRUPO o RELACION_PROPIEDAD; los candidatos C y los
    descartados por revisión no."""
    enlaces = base.tablas["ENLACES"]
    assert set(enlaces["NIVEL"]) == {"EMPRESA", "GRUPO", "RELACION_PROPIEDAD"}
    assert "ESB0000018" not in set(enlaces["ID_DESTINO"])  # GALDIMAR HOLDING IBERIA: NO_VINCULAR
    assert "ESB0000017" not in set(enlaces["ID_DESTINO"])  # FERVANNO BEBIDAS: candidato C


def test_enlaces_conserva_el_indice_de_la_tabla_de_pares(base):
    """Hallazgo F3: ENLACES no reinicia el índice (las demás hojas sí): conserva el de la tabla de pares."""
    assert base.tablas["ENLACES"].index.tolist()[:8] == [51, 1, 2, 5, 7, 3, 4, 12]
    assert base.tablas["EXPORTADORES"].index.tolist() == list(range(79))


# ── REVISION ──────────────────────────────────────────────────────────────────

P1 = "1 · confirmar vínculo automático B"
P2 = "2 · validar decisión de revisión asistida"
P3 = "3 · candidato para empresa sin vínculo"
P4 = "4 · candidato adicional"
P5 = "5 · ¿duplicado interno?"
P6 = "6 · decidido"

#: (NOMBRE_A, NOMBRE_B, PRIORIDAD, ESTADO_REVISION, SUGERENCIA_MOTOR, ESTADO_ACTUAL)
REVISION = [
    pytest.param(
        "Mirelda Chocolates", "MIRELDA CHOCOLATES SA", P1, "PENDIENTE", "EMPRESA (B)",
        "VINCULADO (automático B · revisar)", id="1_vinculo_automatico_b",
    ),
    pytest.param(
        "PLVK SLU", "PELUVIKA SLU", P2, "VALIDAR", "—", "VINCULADO (revisión asistida · validar)",
        id="2_asistida_que_agrega_un_vinculo",
    ),
    pytest.param(
        "Solenta Alimentos S.A.", "ARDENTA SA", P2, "VALIDAR", "RELACION_MATRIZ (C)",
        "RELACIÓN DE PROPIEDAD (revisión)", id="2_asistida_que_cambia_un_candidato",
    ),
    pytest.param(
        "Fervano Bebidas", "FERVANNO BEBIDAS", P3, "PENDIENTE", "GRUPO (C)",
        "CANDIDATO (pendiente de revisión)", id="3_candidato_de_empresa_sin_vinculo",
    ),
    pytest.param(
        "Castelbro Vinos", "Castelbro Spirits", P4, "PENDIENTE", "REVISAR (C)",
        "CANDIDATO (pendiente de revisión)", id="4_candidato_de_empresa_ya_vinculada",
    ),
    pytest.param(
        "Galdimar", "GALDIMAR HOLDING IBERIA SA", P6, "DECIDIDO", "GRUPO (B)", "DESCARTADO (revisión)",
        id="6_descartado_por_una_persona",
    ),
    pytest.param(
        "Korimba Quesos", "KORIMBA ACEITES SL", P6, "DECIDIDO", "REVISAR (C)", "VINCULADO (revisión)",
        id="6_vinculo_decidido_por_una_persona",
    ),
    pytest.param(
        "Kramento Foods S.L.", "Kramento Foods, S.L.U.", P6, "DECIDIDO", "EMPRESA (A)",
        "VINCULADO (automático A)", id="6_confirmar_un_a_no_pide_firma",
    ),
]  # fmt: skip


@pytest.mark.parametrize("nombre_a, nombre_b, prioridad, estado, sugerencia, actual", REVISION)
def test_revision_asigna_prioridad_y_estado_a_cada_vinculo(
    base, nombre_a, nombre_b, prioridad, estado, sugerencia, actual
):
    """Reglas: 1 = vínculo automático B por confirmar; 2 = decisión de revisión ASISTIDA que CAMBIA lo automático
    (VALIDAR); 3 = candidato de una empresa sin vínculo; 4 = candidato adicional de una vinculada; 6 = ya
    decidido. Confirmar un A (aun siendo asistida) no agrega riesgo: queda DECIDIDO."""
    r = base.tablas["REVISION"]
    f = r[(r["NOMBRE_A"] == nombre_a) & (r["NOMBRE_B"] == nombre_b)]
    assert len(f) == 1
    f = f.iloc[0]
    assert (f["PRIORIDAD"], f["ESTADO_REVISION"], f["TIPO"]) == (prioridad, estado, "VINCULO")
    assert (f["SUGERENCIA_MOTOR"], f["ESTADO_ACTUAL"]) == (sugerencia, actual)


def test_revision_cuenta_las_filas_por_prioridad_estado_y_tipo(base):
    """Regla: la hoja mezcla vínculos y duplicados de la ancla; estos son los conteos por prioridad y estado."""
    r = base.tablas["REVISION"]
    conteo = r.groupby(["PRIORIDAD", "ESTADO_REVISION", "TIPO"]).size().to_dict()
    assert conteo == {
        (P1, "PENDIENTE", "VINCULO"): 21,
        (P2, "VALIDAR", "DUPLICADO"): 1,
        (P2, "VALIDAR", "VINCULO"): 2,
        (P3, "PENDIENTE", "VINCULO"): 12,
        (P4, "PENDIENTE", "VINCULO"): 3,
        (P5, "PENDIENTE", "DUPLICADO"): 8,
        (P6, "DECIDIDO", "DUPLICADO"): 1,
        (P6, "DECIDIDO", "VINCULO"): 5,
    }
    assert len(r) == 53


def test_revision_ordena_por_prioridad_operaciones_y_empresa(base):
    """Regla: se ordena por prioridad (1 a 6), operaciones de la empresa de mayor a menor e ``ID_EXPORTADOR``."""
    r = base.tablas["REVISION"]
    assert r["PRIORIDAD"].tolist() == sorted(r["PRIORIDAD"])
    p3 = r[r["PRIORIDAD"] == P3]
    assert p3["OPERACIONES_EXPORTADOR"].tolist() == [
        6.0,
        5.0,
        3.0,
        3.0,
        3.0,
        3.0,
        3.0,
        3.0,
        3.0,
        3.0,
        2.0,
        1.0,
    ]
    p1 = r[r["PRIORIDAD"] == P1]
    assert p1["ID_EXPORTADOR"].tolist() == sorted(p1["ID_EXPORTADOR"])


def test_revision_pregunta_a_la_persona_que_revisa(base):
    """Regla: ``PREGUNTA`` nombra a la empresa y su país, el destino por su etiqueta y el registro; para un
    duplicado, las dos grafías."""
    r = base.tablas["REVISION"]
    f = fila_de(r[r["TIPO"] == "VINCULO"], "CLAVE_B", "ESB0000017")
    assert f["PREGUNTA"] == (
        "¿«Fervano Bebidas» (Alemania) es la misma empresa (EMPRESA), del mismo grupo (GRUPO) o no tiene "
        "relación (NO_VINCULAR) con ORBIS «FERVANNO BEBIDAS» (Spain)?"
    )
    g = fila_de(r[r["TIPO"] == "VINCULO"], "CLAVE_B", "FDI-F0005")
    assert "con FDI MARKETS «Castelbro Spirits» (España)?" in g["PREGUNTA"]
    d = fila_de(r[r["TIPO"] == "DUPLICADO"], "NOMBRE_B", "Dromanto Vinagress")
    assert d["PREGUNTA"] == (
        "¿«Dromanto Vinagres» y «Dromanto Vinagress» son el MISMO exportador (MISMA_ENTIDAD) o distintos (DISTINTA)?"
    )


def test_revision_de_un_vinculo_trae_el_detalle_del_par(base):
    """Regla: la fila de un vínculo lleva la clave y el nombre de la grafía, el registro, la decisión previa, la
    empresa, las operaciones, la evidencia y el puntaje."""
    r = base.tablas["REVISION"]
    f = fila_de(r[r["TIPO"] == "VINCULO"], "ID_EXPORTADOR", "EXP-0044")
    assert (f["FUENTE"], f["CLAVE_A"], f["NOMBRE_A"], f["NOMBRE_B"]) == (
        "ORBIS", "España|PLVK SLU", "PLVK SLU", "PELUVIKA SLU",
    )  # fmt: skip
    assert (f["DECISION"], f["AUTOR"], f["FECHA"], f["RAZON"]) == (
        "EMPRESA", "(verificar) paneles", "2026-09-29", "regla REV_C_SIGLA",
    )  # fmt: skip
    assert (f["ID_EXPORTADOR"], f["OPERACIONES_EXPORTADOR"], f["PAIS_EXPORTADOR"]) == (
        "EXP-0044",
        3.0,
        "España",
    )
    assert (f["PAIS_DESTINO"], f["MISMO_PAIS"], f["SOBRA_DESTINO"], f["SCORE_RUES_LINKER"]) == (
        "Spain", "SÍ", "PELUVIKA", 0.6375,
    )  # fmt: skip
    assert (
        f["MOTIVO"]
        == "Sin evidencia automática suficiente por nombre; vínculo aportado por revisión"
    )


def test_revision_duplicados_pendientes_y_decididos(base):
    """Reglas: un par de grafías sin decidir es prioridad 5 (PENDIENTE, «separadas»); el ya decidido por una
    persona, 6 (DECIDIDO); el unido por una decisión asistida MISMA_ENTIDAD, 2 (VALIDAR, «unidas»). El
    ``ID_EXPORTADOR`` lista las dos empresas, en el orden de las grafías."""
    r = base.tablas["REVISION"]
    d = r[r["TIPO"] == "DUPLICADO"]
    pendiente = fila_de(d, "NOMBRE_B", "Dromanto Vinagress")
    assert (pendiente["PRIORIDAD"], pendiente["ESTADO_REVISION"], pendiente["ESTADO_ACTUAL"]) == (
        P5,
        "PENDIENTE",
        "separadas",
    )
    assert (pendiente["ID_EXPORTADOR"], pendiente["MOTIVO"]) == (
        "EXP-0019 / EXP-0021",
        "mismas palabras (score 0.82)",
    )
    assert (pendiente["DECISION"], pendiente["AUTOR"], pendiente["FECHA"], pendiente["RAZON"]) == (
        "",
        "",
        "",
        "",
    )
    assert pendiente["SUGERENCIA_MOTOR"] == "¿MISMA_ENTIDAD?"
    decidido = fila_de(d, "NOMBRE_B", "Sementa Pastas SA")
    assert (decidido["PRIORIDAD"], decidido["ESTADO_REVISION"], decidido["DECISION"]) == (
        P6,
        "DECIDIDO",
        "DISTINTA",
    )
    assert (decidido["ESTADO_ACTUAL"], decidido["MOTIVO"], decidido["AUTOR"]) == (
        "separadas",
        "decisión de revisión",
        "Revisora Uno",
    )
    asistida = fila_de(d, "NOMBRE_B", "Cravinto Salsas")
    assert (asistida["PRIORIDAD"], asistida["ESTADO_REVISION"], asistida["ESTADO_ACTUAL"]) == (
        P2,
        "VALIDAR",
        "unidas",
    )
    assert (asistida["ID_EXPORTADOR"], asistida["DECISION"], asistida["AUTOR"]) == (
        "EXP-0017",
        "MISMA_ENTIDAD",
        "(verificar) paneles",
    )
    orden = fila_de(d, "NOMBRE_B", "Garandel Foods")
    assert (
        orden["ID_EXPORTADOR"] == "EXP-0029 / EXP-0027"
    )  # GRDL va antes que Garandel en las grafías
    assert d[["OPERACIONES_EXPORTADOR", "SCORE_RUES_LINKER"]].isna().all().all()


def test_revision_un_duplicado_unido_por_otra_via_y_sin_decision_no_aparece(base):
    """Regla: dos grafías que quedaron unidas por otra vía y nadie decidió no se listan como duplicado."""
    r = base.tablas["REVISION"]
    d = r[r["TIPO"] == "DUPLICADO"]
    assert len(d) == 10
    assert "Brumalia Cereales" not in set(d["NOMBRE_A"]) | set(d["NOMBRE_B"])
    assert "GBFoods" not in set(d["NOMBRE_A"]) | set(d["NOMBRE_B"])


def test_sin_la_marca_ninguna_decision_pide_validacion(corrida):
    """Regla: sin ``marca_sin_validar`` ninguna decisión es asistida: no hay filas VALIDAR en la hoja y la unión
    de grafías por una decisión no se pide validar en EXPORTADORES."""
    res = at.con_marca(corrida, "").construir()
    r = res.tablas["REVISION"]
    assert "VALIDAR" not in set(r["ESTADO_REVISION"])
    assert r.groupby(["PRIORIDAD", "ESTADO_REVISION", "TIPO"]).size().to_dict() == {
        (P1, "PENDIENTE", "VINCULO"): 21,
        (P3, "PENDIENTE", "VINCULO"): 12,
        (P4, "PENDIENTE", "VINCULO"): 3,
        (P5, "PENDIENTE", "DUPLICADO"): 8,
        (P6, "DECIDIDO", "DUPLICADO"): 2,
        (P6, "DECIDIDO", "VINCULO"): 7,
    }
    assert exportador(res, "Cravinto Foods")["QUE_REVISAR"] == ""


def test_revision_una_decision_asistida_que_descarta_un_vinculo_b_pide_validacion(corrida):
    """Regla: una decisión NO_VINCULAR de revisión asistida (``AUTOR`` con la marca) pide validar si descarta un
    vínculo automático de banda A o B; sin la marca queda «decidido»."""
    r = corrida.construir().tablas["REVISION"]
    f = fila_de(r[r["TIPO"] == "VINCULO"], "CLAVE_B", "ESB0000018")
    assert (f["DECISION"], f["SUGERENCIA_MOTOR"], f["PRIORIDAD"]) == (
        "NO_VINCULAR",
        "GRUPO (B)",
        "6 · decidido",
    )
    c = at.con_par(corrida, "ORBIS", "ESB0000018", AUTOR="Asistente (verificar)")
    r = c.construir().tablas["REVISION"]
    f = fila_de(r[r["TIPO"] == "VINCULO"], "CLAVE_B", "ESB0000018")
    assert (f["PRIORIDAD"], f["ESTADO_REVISION"]) == (
        "2 · validar decisión de revisión asistida",
        "VALIDAR",
    )


def test_revision_una_decision_asistida_que_separa_dos_grafias_no_pide_validacion(corrida):
    """Regla: en los duplicados, una decisión asistida solo pide validar si UNE las grafías (MISMA_ENTIDAD); una
    decisión DISTINTA queda «decidido» aunque su autor lleve la marca."""
    c = at.con_decision(
        corrida, "España|Sementa Pastas", "España|Sementa Pastas SA", AUTOR="(verificar) otra"
    )
    r = c.construir().tablas["REVISION"]
    f = fila_de(r[r["TIPO"] == "DUPLICADO"], "CLAVE_A", "España|Sementa Pastas")
    assert (f["PRIORIDAD"], f["ESTADO_REVISION"], f["DECISION"]) == (
        "6 · decidido",
        "DECIDIDO",
        "DISTINTA",
    )


def test_revision_un_duplicado_pendiente_y_decidido_conserva_el_motivo_del_pendiente(corrida):
    """Regla: un par de grafías que a la vez está pendiente y tiene decisión muestra el motivo de la
    deduplicación; el motivo «decisión de revisión» es solo para los decididos que no estaban pendientes."""
    r = corrida.construir().tablas["REVISION"]
    f = fila_de(r[r["TIPO"] == "DUPLICADO"], "CLAVE_A", "España|Sementa Pastas")
    assert f["MOTIVO"] == "decisión de revisión"
    c = at.con_pendiente(corrida, "España|Sementa Pastas", "España|Sementa Pastas SA")
    r = c.construir().tablas["REVISION"]
    f = fila_de(r[r["TIPO"] == "DUPLICADO"], "CLAVE_A", "España|Sementa Pastas")
    assert (f["MOTIVO"], f["ESTADO_ACTUAL"]) == ("nombres muy parecidos (score 0.90)", "separadas")


def test_revision_si_un_par_se_decidio_dos_veces_vale_la_ultima_decision(corrida):
    """Regla: si un mismo par de grafías tiene dos decisiones DUPLICADO, REVISION muestra la última leída."""
    c = at.con_decision_repetida(
        corrida,
        "España|Sementa Pastas",
        "España|Sementa Pastas SA",
        DECISION="MISMA_ENTIDAD",
        AUTOR="Revisora Dos",
    )
    r = c.construir().tablas["REVISION"]
    f = fila_de(r[r["TIPO"] == "DUPLICADO"], "CLAVE_A", "España|Sementa Pastas")
    assert (f["DECISION"], f["AUTOR"], f["ESTADO_REVISION"]) == (
        "MISMA_ENTIDAD",
        "Revisora Dos",
        "DECIDIDO",
    )


# ── CONTACTOS ─────────────────────────────────────────────────────────────────

TIPO_CONTACTO = [
    "ID_EXPORTADOR",
    "FUENTE",
    "ID_DESTINO",
    "NIVEL",
    "PAIS",
    "CIUDAD",
    "TIPO_CONTACTO",
    "VALOR_CONTACTO",
]


def test_contactos_saca_un_renglon_por_dato_de_contacto_del_registro_vinculado(base):
    """Regla: de cada registro destino que cuenta salen, en este orden, su sitio web, teléfono, correo y dirección
    si no están vacíos; cada renglón lleva la empresa, el destino por su etiqueta, el nivel, el país y la ciudad."""
    c = base.tablas["CONTACTOS"]
    assert c[TIPO_CONTACTO].values.tolist() == [
        ["EXP-0015", "ORBIS", "ESB0000031", "GRUPO", "Spain", "", "TELEFONO", "961234567"],
        ["EXP-0030", "ORBIS", "ESB0000032", "GRUPO", "Spain", "", "CORREO", "ventas@ibertona.es"],
        ["EXP-0057", "ORBIS", "ESB0000034", "GRUPO", "Spain", "Bilbao", "SITIO_WEB", "tulvanto.es"],
        ["EXP-0059", "FDI MARKETS", "FDI-F0002", "EMPRESA", "España", "Valencia", "SITIO_WEB", "https://vantiko.es/inicio"],
        ["EXP-0059", "ORBIS", "ESB0000029", "EMPRESA", "Spain", "Valencia", "SITIO_WEB", "www.vantiko.es"],
    ]  # fmt: skip
    assert c["NOMBRE_DESTINO"].tolist()[0] == "CASTELBRO LICORES SL"
    assert c["NOMBRE_EXPORTADOR"].tolist()[3] == "Vantiko Chocolates S.A."


def test_contactos_solo_cuenta_la_misma_empresa_y_el_principal_del_grupo(corrida):
    """Regla: se toman los contactos de los vínculos EMPRESA (principales o no) y del registro PRINCIPAL de los
    vínculos GRUPO; los de otras filiales (Zelvora Polska, Zelvora Austria…) no sirven para contactar. Los
    valores se recortan y se descarta el texto «nan» (no «None»)."""
    c = corrida
    c = at.con_registro(
        c,
        "ORBIS",
        "ESB0000002",
        WEB="kramento.es",
        TELEFONO=" 961 000 111 ",
        EMAIL="nan",
        DIRECCION="Calle Falsa 1",
    )
    c = at.con_registro(
        c, "ORBIS", "ESB0000011", WEB="lacteos.quelmar.es"
    )  # GRUPO no principal: no cuenta
    c = at.con_registro(
        c, "FDI", "FDI-F0007", TELEFONO="+34 600 111 222"
    )  # EMPRESA no principal: cuenta
    res = c.construir()
    contactos = res.tablas["CONTACTOS"]
    assert contactos[TIPO_CONTACTO].values.tolist() == [
        ["EXP-0015", "ORBIS", "ESB0000031", "GRUPO", "Spain", "", "TELEFONO", "961234567"],
        ["EXP-0030", "ORBIS", "ESB0000032", "GRUPO", "Spain", "", "CORREO", "ventas@ibertona.es"],
        ["EXP-0035", "ORBIS", "ESB0000002", "EMPRESA", "Spain", "", "SITIO_WEB", "kramento.es"],
        ["EXP-0035", "ORBIS", "ESB0000002", "EMPRESA", "Spain", "", "TELEFONO", "961 000 111"],
        ["EXP-0035", "ORBIS", "ESB0000002", "EMPRESA", "Spain", "", "DIRECCION", "Calle Falsa 1"],
        ["EXP-0040", "FDI MARKETS", "FDI-F0007", "EMPRESA", "España", "", "TELEFONO", "+34 600 111 222"],
        ["EXP-0057", "ORBIS", "ESB0000034", "GRUPO", "Spain", "Bilbao", "SITIO_WEB", "tulvanto.es"],
        ["EXP-0059", "FDI MARKETS", "FDI-F0002", "EMPRESA", "España", "Valencia", "SITIO_WEB", "https://vantiko.es/inicio"],
        ["EXP-0059", "ORBIS", "ESB0000029", "EMPRESA", "Spain", "Valencia", "SITIO_WEB", "www.vantiko.es"],
    ]  # fmt: skip
    k = exportador(res, "Kramento Foods S.L.")
    assert (k["CONTACTO_WEB"], k["CONTACTO_TELEFONO"], k["CONTACTO_EMAIL"]) == (
        "kramento.es",
        "961 000 111",
        "",
    )
    assert exportador(res, "Quelmar")["CONTACTO_WEB"] == ""
    assert exportador(res, "Nolmendo Cervezas")["CONTACTO_TELEFONO"] == "+34 600 111 222"


def test_contactos_deja_pasar_el_texto_none(corrida):
    """Hallazgo F3: CONTACTOS descarta el texto «nan» pero no «None»: un correo «None» llega a la hoja como si
    fuera un dato (mientras ``CONTACTO_EMAIL`` de EXPORTADORES sí lo descarta)."""
    res = at.con_registro(corrida, "ORBIS", "ESB0000010", EMAIL="None").construir()
    c = res.tablas["CONTACTOS"]
    fila = fila_de(c, "ID_DESTINO", "ESB0000010")
    assert (fila["TIPO_CONTACTO"], fila["VALOR_CONTACTO"]) == ("CORREO", "None")
    assert exportador(res, "Quelmar")["CONTACTO_EMAIL"] == ""


def test_los_datos_de_matriz_del_registro_llegan_a_exportadores_enlaces_y_detalle(corrida):
    """Regla: la matriz reportada (``GRUPO_ORIGINAL``) del registro sale en ``<DESTINO>_MATRIZ``,
    ``MATRIZ_DESTINO`` y ``MATRIZ`` del detalle."""
    res = at.con_registro(
        corrida, "ORBIS", "ESB0000002", GRUPO_ORIGINAL="Kramento Group"
    ).construir()
    assert exportador(res, "Kramento Foods S.L.")["ORBIS_MATRIZ"] == "Kramento Group"
    assert (
        fila_de(res.tablas["ENLACES"], "ID_DESTINO", "ESB0000002")["MATRIZ_DESTINO"]
        == "Kramento Group"
    )
    assert (
        fila_de(res.tablas["DETALLE_ORBIS"], "ID_REGISTRO", "ESB0000002")["MATRIZ"]
        == "Kramento Group"
    )


# ── DETALLE_EXPORTADORES ──────────────────────────────────────────────────────


def test_detalle_de_la_ancla_conserva_todas_las_filas_y_agrega_la_empresa(base):
    """Regla: una fila por fila de la ancla, con ``ID_EXPORTADOR`` y ``NOMBRE_EXPORTADOR`` al frente y las
    columnas originales tal como se leyeron (sin ``FUENTE``)."""
    d = base.tablas["DETALLE_EXPORTADORES"]
    assert len(d) == 88
    assert "FUENTE" not in d.columns
    k = fila_de(d, "ID_REGISTRO", "EXP-F0003")
    assert (k["ID_EXPORTADOR"], k["NOMBRE_EXPORTADOR"], k["FILA_EXCEL"]) == (
        "EXP-0035",
        "Kramento Foods S.L.",
        3,
    )
    assert (k["Empresa"], k["País de Empresa"], k["Operaciones"], k["Kilos"], k["HS6"]) == (
        "Kramento Foods S.L.", "España", 3, 800, "190590",
    )  # fmt: skip
    assert d["ID_EXPORTADOR"].nunique() == 79


def test_detalle_de_la_ancla_agrega_las_marcas_de_calidad_y_el_resultado(base):
    """Reglas: ``NOMBRE_NORMALIZADO``; ``TRUNCADO`` y ``AGENTE_DE_CARGA`` como SÍ/NO; el resultado, nivel,
    confianza y fuentes de la empresa; el nombre del vínculo principal de cada destino."""
    d = base.tablas["DETALLE_EXPORTADORES"]
    k = fila_de(d, "Empresa", "Kramento Foods S.L.")
    assert k["NOMBRE_NORMALIZADO"] == "KRAMENTO FOODS"
    assert (k["TRUNCADO"], k["AGENTE_DE_CARGA"]) == ("NO", "NO")
    assert (k["RESULTADO"], k["NIVEL_VINCULO"], k["CONFIANZA"], k["FUENTES_VINCULADAS"]) == (
        "VINCULADO", "EMPRESA", "ALTA", "ORBIS",
    )  # fmt: skip
    assert (k["ORBIS_NOMBRE"], k["FDI_NOMBRE"]) == ("Kramento Foods, S.L.U.", "")
    t = fila_de(d, "Empresa", "COOPERATIVA DE CONSERVAS DEL CANTAB")
    assert (t["TRUNCADO"], t["NOMBRE_NORMALIZADO"]) == ("SÍ", "DE CONSERVAS DEL CANTAB")
    a = fila_de(d, "Empresa", "Transportes Rapidos Freight SA")
    assert (a["AGENTE_DE_CARGA"], a["RESULTADO"], a["NIVEL_VINCULO"], a["ORBIS_NOMBRE"]) == (
        "SÍ",
        "SIN COINCIDENCIA",
        "",
        "",
    )
    v = fila_de(d, "Empresa", "Vantiko Chocolates S.A.")
    assert (v["ORBIS_NOMBRE"], v["FDI_NOMBRE"], v["FUENTES_VINCULADAS"]) == (
        "VANTIKO CHOCOLATES SA", "Vantiko Sweets", "ORBIS + FDI MARKETS",
    )  # fmt: skip
    assert (d["TRUNCADO"] == "SÍ").sum() == 4
    assert (d["AGENTE_DE_CARGA"] == "SÍ").sum() == 2


def test_detalle_de_la_ancla_marca_las_filas_con_la_metrica_repetida(base):
    """Regla: ``<METRICA>_REPETIDO`` es SÍ en las filas cuyo valor ya apareció antes para la misma empresa y
    partida (la primera aparición es NO)."""
    d = base.tablas["DETALLE_EXPORTADORES"]
    assert d.loc[d["Empresa"] == "Balvarda Mieles SL", "KILOS_REPETIDO"].tolist() == [
        "NO",
        "SÍ",
        "NO",
    ]
    assert (d["KILOS_REPETIDO"] == "SÍ").sum() == 8


def test_sin_metricas_repetidas_no_hay_columnas_de_repetidos(corrida):
    """Regla: sin ``metricas_repetidas`` ni EXPORTADORES trae columnas ``_SIN_REPETIDOS`` ni DETALLE_EXPORTADORES
    columnas ``_REPETIDO``: esta última termina con los nombres de los vínculos principales."""
    res = at.con_ancla(corrida, metricas_repetidas=()).construir()
    assert not [c for c in res.tablas["EXPORTADORES"].columns if "SIN_REPETIDOS" in c]
    detalle = res.tablas["DETALLE_EXPORTADORES"]
    assert not [c for c in detalle.columns if "REPETIDO" in c]
    assert list(detalle.columns)[-2:] == ["ORBIS_NOMBRE", "FDI_NOMBRE"]


# ── DETALLE_<destino> ─────────────────────────────────────────────────────────


def test_detalle_del_destino_conserva_todos_sus_registros(base):
    """Regla: una hoja por destino con TODOS sus registros, en el orden de la fuente, con las columnas
    renombradas (``ID_REGISTRO``, ``NOMBRE``, ``PAIS``) y las de contexto al final."""
    o = base.tablas["DETALLE_ORBIS"]
    f = base.tablas["DETALLE_FDI"]
    assert (len(o), len(f)) == (79, 36)
    assert o.iloc[0].to_dict() == {
        "ID_REGISTRO": "ESB0000001", "FILA_EXCEL": 2, "NOMBRE": "ZELVORA ALIMENTOS SA",
        "NOMBRE_NORMALIZADO": "ZELVORA ALIMENTOS", "PAIS": "Spain", "PAIS_ISO3": "ESP",
        "IDS_EXPORTADOR": "EXP-0060", "EXPORTADORES_VINCULADOS": "Zelvora Alimentos S.A.",
        "NIVEL_MAS_ALTO": "GRUPO", "ID_ENTIDAD": "ORB-E0001", "CIUDAD": "", "WEB": "", "EMAIL": "",
        "TELEFONO": "", "DIRECCION": "", "MATRIZ": "", "INGRESOS_USD_M": 5.5, "EMPLEADOS": 40,
    }  # fmt: skip
    assert f.iloc[0].to_dict() == {
        "ID_REGISTRO": "FDI-F0002", "FILA_EXCEL": 2, "NOMBRE": "Vantiko Sweets",
        "NOMBRE_NORMALIZADO": "VANTIKO SWEETS", "PAIS": "España", "PAIS_ISO3": "ESP",
        "IDS_EXPORTADOR": "EXP-0059", "EXPORTADORES_VINCULADOS": "Vantiko Chocolates S.A.",
        "NIVEL_MAS_ALTO": "EMPRESA", "ID_ENTIDAD": "FDI-E0001", "CIUDAD": "Valencia",
        "WEB": "https://vantiko.es/inicio", "EMAIL": "", "TELEFONO": "", "DIRECCION": "", "MATRIZ": "",
        "SECTOR": "Food", "PROYECTOS_BUSQUEDA": 2,
    }  # fmt: skip


def test_detalle_del_destino_dice_con_que_exportadores_se_vinculo_cada_registro(base):
    """Reglas: ``IDS_EXPORTADOR`` y ``EXPORTADORES_VINCULADOS`` unen las empresas que se vincularon al registro
    (hasta 6, en el orden de los vínculos principales primero); ``NIVEL_MAS_ALTO`` es EMPRESA si alguna lo es y
    GRUPO si no; sin vínculos, los tres quedan en ``''``."""
    o = base.tablas["DETALLE_ORBIS"]
    p = fila_de(o, "ID_REGISTRO", "ESB0000021")
    assert p["IDS_EXPORTADOR"] == "EXP-0044 | EXP-0042"
    assert p["EXPORTADORES_VINCULADOS"] == "PLVK SLU | Peluvika Dulces (Dulcevent)"
    assert p["NIVEL_MAS_ALTO"] == "EMPRESA"
    sin = fila_de(o, "ID_REGISTRO", "ESB0000006")
    assert (sin["IDS_EXPORTADOR"], sin["EXPORTADORES_VINCULADOS"], sin["NIVEL_MAS_ALTO"]) == (
        "",
        "",
        "",
    )
    assert o["NIVEL_MAS_ALTO"].value_counts().to_dict() == {"": 49, "EMPRESA": 18, "GRUPO": 12}
    assert base.tablas["DETALLE_FDI"]["NIVEL_MAS_ALTO"].value_counts().to_dict() == {
        "": 32,
        "EMPRESA": 3,
        "GRUPO": 1,
    }


# ── DEDUPLICACION ─────────────────────────────────────────────────────────────


def test_deduplicacion_explica_como_se_unieron_las_grafias_de_la_ancla(base):
    """Regla: por cada empresa de la ancla con más de una grafía, una fila por grafía con su nombre, sus filas, su
    forma legal y las reglas que las unieron (sin repetir y en orden)."""
    d = base.tablas["DEDUPLICACION"]
    assert d.groupby("FUENTE").size().to_dict() == {
        "EXPORTADORES": 11,
        "FDI MARKETS": 2,
        "ORBIS": 4,
    }
    b = d[d["ENTIDAD"] == "EXP-0013"]
    assert b[["REGISTRO", "NOMBRE_REGISTRO", "N_FILAS", "FORMA_LEGAL", "COMO_SE_UNIO"]].values.tolist() == [
        ["España|BRUMALIA CEREALES S.A.", "BRUMALIA CEREALES S.A.", 1, "SA", "mismo nombre normalizado"],
        ["España|Brumalia Cereales", "Brumalia Cereales", 1, "", "mismo nombre normalizado"],
        ["España|Brumalia Cereales SA", "Brumalia Cereales SA", 2, "SA", "mismo nombre normalizado"],
    ]  # fmt: skip
    assert set(b["NOMBRE_ENTIDAD"]) == {"Brumalia Cereales SA"}
    assert set(d.loc[d["ENTIDAD"] == "EXP-0028", "COMO_SE_UNIO"]) == {
        "mismas letras, otros espacios"
    }
    assert set(d.loc[d["ENTIDAD"] == "EXP-0031", "COMO_SE_UNIO"]) == {
        "mismas palabras con truncamiento"
    }


def test_deduplicacion_trae_las_uniones_por_decision(base):
    """Regla: una unión decidida por una persona dice «decisión: <autor>» como regla."""
    d = base.tablas["DEDUPLICACION"]
    c = d[d["ENTIDAD"] == "EXP-0017"]
    assert c["REGISTRO"].tolist() == ["España|Cravinto Foods", "España|Cravinto Salsas"]
    assert set(c["COMO_SE_UNIO"]) == {"decisión: (verificar) paneles"}


def test_deduplicacion_trae_las_entidades_de_cada_destino_con_el_nombre_del_primer_registro(base):
    """Reglas: en los destinos entran las entidades con más de un registro (misma raíz de identificador, o el
    mismo nombre exacto y país); ``NOMBRE_ENTIDAD`` es el nombre del primer registro de la entidad y
    ``N_FILAS`` siempre 1."""
    d = base.tablas["DEDUPLICACION"]
    orbis = d[d["FUENTE"] == "ORBIS"]
    assert orbis[["ENTIDAD", "NOMBRE_ENTIDAD", "REGISTRO", "N_FILAS", "FORMA_LEGAL", "COMO_SE_UNIO"]].values.tolist()[:2] == [
        ["ORB-E0036", "TRIVENTA BEBIDAS SA", "ESB9000001", 1, "SA", "mismo identificador raíz"],
        ["ORB-E0036", "TRIVENTA BEBIDAS SA", "ESB9000001-1003", 1, "SA", "mismo identificador raíz"],
    ]  # fmt: skip
    fdi = d[d["FUENTE"] == "FDI MARKETS"]
    assert fdi["REGISTRO"].tolist() == ["FDI-F0006", "FDI-F0007"]
    assert set(fdi["COMO_SE_UNIO"]) == {"mismo nombre exacto y mismo país"}
    assert set(fdi["NOMBRE_ENTIDAD"]) == {"Nolmendo Cervezas"}
    assert d["N_FILAS"].tolist()[11:] == [1, 1, 1, 1, 1, 1]


def test_deduplicacion_junta_sin_repetir_y_en_orden_las_reglas_de_cada_registro_destino(corrida):
    """Regla: ``COMO_SE_UNIO`` de un registro destino junta sus reglas sin repetir y en orden alfabético, no en el
    orden en que se aplicaron (aquí «mismo dominio», aplicada dos veces, va antes que «mismo identificador raíz»)."""
    c = corrida
    for _ in range(2):
        c = at.con_union_de_destino(c, "ORBIS", "ESB9000001", "ESB9000001-1003", "mismo dominio")
    dd = c.construir().tablas["DEDUPLICACION"]
    orbis = dd[(dd["FUENTE"] == "ORBIS") & (dd["ENTIDAD"] == "ORB-E0036")]
    assert orbis["REGISTRO"].tolist() == ["ESB9000001", "ESB9000001-1003"]
    assert orbis["COMO_SE_UNIO"].tolist() == ["mismo dominio; mismo identificador raíz"] * 2


def test_deduplicacion_de_la_ancla_incluye_la_fusion_por_empresa_comun(corrida):
    """Regla: además de las reglas que unieron a cada grafía, ``COMO_SE_UNIO`` trae las de las fusiones de su
    empresa (dos grafías que son la misma empresa que un mismo registro destino)."""
    c = at.con_empresas_unidas(
        corrida,
        "España|Fribelia SL",
        "España|Fribelia S. COOP",
        "misma empresa que ORBIS ESB0000099",
    )
    dd = c.construir().tablas["DEDUPLICACION"]
    fribelia = dd[dd["NOMBRE_REGISTRO"].str.contains("Fribelia")]
    assert fribelia[["ENTIDAD", "REGISTRO", "COMO_SE_UNIO"]].values.tolist() == [
        ["EXP-0023", "España|Fribelia S. COOP", "misma empresa que ORBIS ESB0000099"],
        ["EXP-0023", "España|Fribelia SL", "misma empresa que ORBIS ESB0000099"],
    ]


# ── hojas de validación y decisiones ──────────────────────────────────────────


def test_las_hojas_de_validacion_muestran_el_destino_por_su_etiqueta(base):
    """Regla: VALIDACION y VALIDACION_DETALLE llevan la etiqueta del destino (FDI MARKETS) en lugar de su clave,
    y el detalle nombra a la empresa por todas sus grafías automáticas."""
    v = base.tablas["VALIDACION"]
    assert v["FUENTE_DESTINO"].tolist() == ["ORBIS", "FDI MARKETS"]
    assert (v["VINCULOS_AUTOMATICOS_A_B"].tolist(), v["ACIERTOS"].tolist()) == ([30, 4], [3, 0])
    assert base.validacion is base.tablas["VALIDACION"]
    d = base.tablas["VALIDACION_DETALLE"]
    assert d["FUENTE_DESTINO"].value_counts().to_dict() == {"ORBIS": 29, "FDI MARKETS": 4}
    assert "ENT" not in d.columns
    assert d.iloc[0].to_dict() == {
        "FUENTE_DESTINO": "ORBIS", "EXPORTADOR": "Holvexa Foods GmbH",
        "TIPO": "Falso positivo (el motor vincula, la referencia no)", "ID_DESTINO": "ESB0000016",
        "NOMBRE_DESTINO": "HOLVEXA FOODS", "NIVEL_AUTO": "GRUPO", "REFERENCIA": "no incluido",
    }  # fmt: skip


def test_decisiones_trae_lo_leido_con_el_archivo_de_origen(base):
    """Regla: DECISIONES es la tabla de decisiones leída, con ``ARCHIVO`` renombrada ``ARCHIVO_ORIGEN``; la
    comparación entre archivos marca las que se contradicen."""
    d = base.tablas["DECISIONES"]
    assert d["ARCHIVO_ORIGEN"].value_counts().to_dict() == {
        "decisiones_1.csv": 9,
        "decisiones_2.csv": 4,
    }
    assert "ORDEN" not in d.columns
    c = base.tablas["COMPARACION_REVISIONES"]
    assert c.to_dict("records") == [
        {
            "TIPO": "VINCULO", "FUENTE": "ORBIS", "CLAVE_A": "España|Zelvora Alimentos S.A.",
            "CLAVE_B": "ESB0000001", "NOMBRE_B": "ZELVORA ALIMENTOS SA",
            "DECISIONES": "decisiones_1.csv: EMPRESA | decisiones_2.csv: GRUPO", "COINCIDEN": "NO",
            "RAZON_ULTIMA": "contradice al archivo anterior",
        }
    ]  # fmt: skip


def test_sin_decisiones_leidas_los_duplicados_decididos_dejan_de_listarse(corrida):
    """Regla: sin decisiones leídas, DECISIONES y COMPARACION_REVISIONES salen vacías con sus columnas y en
    REVISION solo quedan los duplicados pendientes (los decididos vienen de las decisiones)."""
    res = at.sin_decisiones(corrida).construir()
    decisiones = res.tablas["DECISIONES"]
    assert (len(decisiones), list(decisiones.columns)) == (0, COLUMNAS["DECISIONES"])
    comparacion = res.tablas["COMPARACION_REVISIONES"]
    assert (len(comparacion), len(comparacion.columns)) == (
        0,
        8,
    )  # M-b: el mismo esquema que la llena
    r = res.tablas["REVISION"]
    assert r.groupby(["PRIORIDAD", "ESTADO_REVISION", "TIPO"]).size().to_dict() == {
        (P1, "PENDIENTE", "VINCULO"): 21,
        (P2, "VALIDAR", "VINCULO"): 2,
        (P3, "PENDIENTE", "VINCULO"): 12,
        (P4, "PENDIENTE", "VINCULO"): 3,
        (P5, "PENDIENTE", "DUPLICADO"): 8,
        (P6, "DECIDIDO", "VINCULO"): 5,
    }
    assert len(r) == 51


def test_un_cruce_sin_destinos_solo_deduplica(corrida):
    """Regla: sin destinos no hay hojas DETALLE_<destino> ni bloques de destino en EXPORTADORES; las empresas
    quedan sin coincidencia y REVISION trae solo duplicados."""
    res = at.sin_destinos(corrida).construir()
    assert list(res.tablas) == [h for h in HOJAS if h not in ("DETALLE_ORBIS", "DETALLE_FDI")]
    e = res.tablas["EXPORTADORES"]
    assert not any(c.startswith(("ORBIS_", "FDI_")) for c in e.columns)
    assert e["RESULTADO"].value_counts().to_dict() == {"SIN COINCIDENCIA": 79}
    assert (e["FUENTES_VINCULADAS"] == "").all()
    assert (
        len(res.tablas["ENLACES"]),
        len(res.tablas["CONTACTOS"]),
        len(res.tablas["VALIDACION"]),
    ) == (0, 0, 0)
    assert (
        exportador(res, "Pelmonte Foods")["OBSERVACIONES"] == ""
    )  # sin destinos, ningún país queda sin cobertura


def test_revision_sin_destinos_pierde_las_columnas_de_los_vinculos(corrida):
    """Hallazgo F3: las columnas de REVISION salen de las claves de las filas; sin ningún vínculo solo quedan las
    19 de los duplicados (con vínculos son 29) y sin filas no queda ninguna."""
    r = at.sin_destinos(corrida).construir().tablas["REVISION"]
    assert list(r.columns) == COLUMNAS["REVISION"][:19]
    assert len(r) == 10
    assert "MISMO_PAIS" not in r.columns


def test_sin_nada_que_deduplicar_ni_revisar_revision_sale_sin_columnas(corrida):
    """Hallazgo F3: sin vínculos ni duplicados pendientes o decididos, REVISION sale sin una sola columna (las
    columnas salen de las claves de las filas); DEDUPLICACION, en cambio, sale vacía pero con sus 8 columnas."""
    res = at.sin_nada_que_deduplicar_ni_revisar(corrida).construir()
    assert res.tablas["REVISION"].shape == (0, 0)
    assert list(res.tablas["DEDUPLICACION"].columns) == COLUMNAS["DEDUPLICACION"]
    assert len(res.tablas["DEDUPLICACION"]) == 0
    assert res.tablas["EXPORTADORES"]["RESULTADO"].value_counts().to_dict() == {
        "SIN COINCIDENCIA": 85
    }


@pytest.mark.parametrize(
    "campos",
    [
        pytest.param({"CLAVE_B": "España|Grafia inexistente SL"}, id="grafia_que_no_existe"),
        pytest.param({"CLAVE_B": "España|Sementa Pastas"}, id="el_par_es_una_sola_grafia"),
    ],
)
def test_revision_ignora_la_decision_de_duplicado_que_no_es_un_par_de_grafias(
    base, corrida, campos
):
    """Regla: una decisión DUPLICADO cuyas claves no son dos grafías de la ancla (una no existe, o las dos son la
    misma) no agrega ninguna fila a REVISION."""
    c = at.con_decision_repetida(
        corrida, "España|Sementa Pastas", "España|Sementa Pastas SA", **campos
    )
    revision = c.construir().tablas["REVISION"]
    assert revision.equals(base.tablas["REVISION"])


# ── unir_valores ──────────────────────────────────────────────────────────────

UNIR = [
    pytest.param(["b", "a", "b", " a ", "c"], {}, "b | a | c", id="sin_repetidos_y_recortado"),
    pytest.param(
        [None, NAN, "nan", "NaN", "None", "none", "", "  ", "x"],
        {},
        "x",
        id="descarta_ausentes_y_vacios",
    ),
    pytest.param(list("abcde"), {"maximo": 3}, "a | b | c | … (+2)", id="corta_en_el_maximo"),
    pytest.param(list("abc"), {"maximo": 3}, "a | b | c", id="justo_en_el_maximo_no_corta"),
    pytest.param(
        list("abcd"), {"maximo": 3, "sep": "; "}, "a; b; c; … (+1)", id="separador_propio"
    ),
    pytest.param([1, 2.0, 2, "2.0"], {}, "1 | 2.0 | 2", id="numeros_como_texto"),
    pytest.param(pd.Series(["x", np.nan, "x", "y"]), {}, "x | y", id="acepta_una_serie"),
    pytest.param([], {}, "", id="vacio"),
    pytest.param(["Nan", "NONE", "nanas"], {}, "nanas", id="solo_descarta_nan_y_none_enteros"),
]


@pytest.mark.parametrize("valores, kwargs, esperado", UNIR)
def test_unir_valores(valores, kwargs, esperado):
    """Reglas: une los valores distintos y no vacíos en el orden en que llegan, recortados; descarta ``None``,
    ``NaN`` y los textos «nan»/«none»; con más de ``maximo`` deja los primeros y cuenta los que sobran."""
    assert unir_valores(valores, **kwargs) == esperado


def test_unir_valores_con_maximo_cero_empieza_con_el_separador():
    """Regla (borde): con ``maximo=0`` no queda ningún valor y la salida es el separador y «… (+N)»."""
    assert unir_valores(["a", "b"], maximo=0) == " | … (+2)"


def test_unir_valores_no_descarta_na_ni_nat():
    """Hallazgo F3: ``pd.NA`` y ``pd.NaT`` no se tratan como vacíos: entran a la celda como «<NA>» y «NaT»."""
    assert unir_valores([pd.NA, "a", pd.NaT]) == "<NA> | a | NaT"


# ── nombres de columnas derivados de la configuración ─────────────────────────


def test_nombre_columna_ancla_usa_el_nombre_legible_en_mayusculas_y_con_guiones_bajos(corrida):
    """Regla: ``'Valor U$S'`` se llama VALOR_USD (su nombre legible); sin nombre legible, la columna misma en
    mayúsculas y con guiones bajos en lugar de espacios."""
    ancla = corrida.cfg.ancla
    assert nombre_columna_ancla(ancla, "Valor U$S") == "VALOR_USD"
    assert nombre_columna_ancla(ancla, "Kilos") == "KILOS"
    assert nombre_columna_ancla(ancla, "Régimen") == "RÉGIMEN"
    otra = Fuente(
        clave="EXPORTADORES",
        archivo="x.xlsx",
        col_nombre="N",
        nombres_contexto={"a b": "valor fob"},
    )
    assert nombre_columna_ancla(otra, "a b") == "VALOR_FOB"
    assert nombre_columna_ancla(otra, "peso neto") == "PESO_NETO"


@pytest.mark.parametrize(
    "etiqueta, prefijo",
    [("FDI MARKETS", "FDI"), ("orbis", "ORBIS"), ("  Base Uno  ", "BASE"), ("RUES", "RUES")],
)
def test_prefijo_destino_es_la_primera_palabra_de_la_etiqueta_en_mayusculas(etiqueta, prefijo):
    """Regla: las columnas de un destino en EXPORTADORES llevan la primera palabra de su etiqueta en MAYÚSCULAS."""
    destino = Fuente(clave="DEST", archivo="x.xlsx", col_nombre="N", etiqueta=etiqueta)
    assert prefijo_destino(destino) == prefijo


# ── info_destino ──────────────────────────────────────────────────────────────


def _info(valores, **fuente):
    """``info_destino`` de un destino de 5 registros con una columna de contexto ``C`` con esos ``valores``."""
    n = len(valores)
    prep = pd.DataFrame(
        {
            "ID_REGISTRO": [f"ORB-F{k + 2:04d}" for k in range(n)],
            "FILA_EXCEL": list(range(2, n + 2)),
            "NOMBRE_ORIGINAL": [f"Orbelia {k}" for k in range(n)],
            "PAIS_ORIGINAL": ["España"] * n,
            "CIUDAD": (["Valencia", "", "Lyon", "", "Bilbao"] * 2)[:n],
            "WEB": ["orbelia.es"] * n,
            "EMAIL": [""] * n,
            "TELEFONO": [""] * n,
            "DIRECCION": [""] * n,
            "GRUPO_ORIGINAL": [""] * n,
        }
    )
    ent = pd.DataFrame({"ID_ENTIDAD": [f"ORB-E{k + 1:04d}" for k in range(n)]})
    f = Fuente(
        clave="ORBIS", archivo="x.xlsx", col_nombre="N", cols_contexto=("C",),
        nombres_contexto={"C": "CONTEXTO"}, **fuente,
    )  # fmt: skip
    crudo = pd.DataFrame({"N": [f"o{k}" for k in range(n)], "C": pd.Series(valores, dtype=object)})
    return info_destino(f, prep, crudo, ent)


def test_info_destino_trae_una_fila_por_registro_con_lo_que_se_muestra():
    """Regla: columnas ``ID_DESTINO``, ``FILA_EXCEL``, ``NOMBRE_DESTINO``, ``PAIS_DESTINO``, ``CIUDAD``, ``WEB``,
    ``EMAIL``, ``TELEFONO``, ``DIRECCION``, ``MATRIZ``, ``ID_ENTIDAD`` y luego el contexto, en el orden de ``prep``."""
    out = _info([1.0] * 5)
    assert list(out.columns) == [
        "ID_DESTINO", "FILA_EXCEL", "NOMBRE_DESTINO", "PAIS_DESTINO", "CIUDAD", "WEB", "EMAIL", "TELEFONO",
        "DIRECCION", "MATRIZ", "ID_ENTIDAD", "CONTEXTO",
    ]  # fmt: skip
    assert out.iloc[0].to_dict() == {
        "ID_DESTINO": "ORB-F0002", "FILA_EXCEL": 2, "NOMBRE_DESTINO": "Orbelia 0", "PAIS_DESTINO": "España",
        "CIUDAD": "Valencia", "WEB": "orbelia.es", "EMAIL": "", "TELEFONO": "", "DIRECCION": "", "MATRIZ": "",
        "ID_ENTIDAD": "ORB-E0001", "CONTEXTO": 1.0,
    }  # fmt: skip


@pytest.mark.parametrize(
    "valores, esperado",
    [
        pytest.param(
            [1.23456, 2.0, np.nan, 4, 5.5],
            [1.2346, 2.0, NAN, 4.0, 5.5],
            id="numerica_a_4_decimales",
        ),
        pytest.param(
            ["a", "b", np.nan, "d", None], ["a", "b", "", "d", ""], id="texto_con_ausentes_vacios"
        ),
        pytest.param(
            [1, 2, 3, "x", "y"], ["1", "2", "3", "x", "y"], id="menos_del_80_por_ciento_es_texto"
        ),
        pytest.param(
            [1, 2, 3, "x", None],
            ["1", "2", "3", "x", ""],
            id="tres_de_cuatro_con_contenido_es_texto",
        ),
        pytest.param(
            ["n.a.", "n.s.", "-", "", None],
            ["n.a.", "n.s.", "-", "", ""],
            id="sin_contenido_queda_como_texto",
        ),
        pytest.param(
            [10, "n.a.", "-", 20, 30],
            [10.0, NAN, NAN, 20.0, 30.0],
            id="n_a_y_guion_no_cuentan_como_valores",
        ),
    ],
)
def test_info_destino_decide_si_una_columna_de_contexto_es_numerica_o_texto(valores, esperado):
    """Reglas: la columna es numérica (redondeada a 4 decimales) si al menos el 80 % de sus valores CON contenido
    (no vacíos, ni «n.a.», «n.s.» ni «-») son números; si no, es texto con ``''`` en los ausentes."""
    columna = _info(valores)["CONTEXTO"]
    esperado = pd.Series(esperado)
    assert pd.api.types.is_numeric_dtype(columna) == pd.api.types.is_numeric_dtype(esperado)
    assert [
        x if not (isinstance(x, float) and np.isnan(x)) else None for x in columna.tolist()
    ] == [x if not (isinstance(x, float) and np.isnan(x)) else None for x in esperado.tolist()]


def test_info_destino_pierde_los_valores_que_no_son_numeros_cuando_casi_todo_lo_es():
    """Hallazgo F3: con 80 % o más de números, los demás valores («ND», «1,5» con coma decimal) se vuelven NaN
    sin aviso: el dato se pierde en DETALLE_<destino> y en EXPORTADORES."""
    assert _info([1, 2, 3, 4, "ND"])["CONTEXTO"].tolist()[:4] == [1.0, 2.0, 3.0, 4.0]
    assert np.isnan(_info([1, 2, 3, 4, "ND"])["CONTEXTO"].iloc[4])
    assert np.isnan(_info(["1,5", "2", "3", "4", "5"])["CONTEXTO"].iloc[0])


def test_info_destino_renombra_el_contexto_que_choca_con_una_columna_anterior():
    """Regla: si el nombre legible de una columna de contexto ya existe en la tabla, se agrega la clave del
    destino: ``CIUDAD`` → ``CIUDAD_ORBIS``."""
    n = 3
    prep = _info([1.0] * n).iloc[:0]
    f = Fuente(
        clave="ORBIS", archivo="x.xlsx", col_nombre="N", cols_contexto=("C", "D", "E"),
        nombres_contexto={"C": "CIUDAD", "D": "X", "E": "X"},
    )  # fmt: skip
    base = pd.DataFrame(
        {
            "ID_REGISTRO": ["a", "b", "c"], "FILA_EXCEL": [2, 3, 4], "NOMBRE_ORIGINAL": ["A", "B", "C"],
            "PAIS_ORIGINAL": ["x"] * n, "CIUDAD": ["Lyon"] * n, "WEB": [""] * n, "EMAIL": [""] * n,
            "TELEFONO": [""] * n, "DIRECCION": [""] * n, "GRUPO_ORIGINAL": [""] * n,
        }
    )  # fmt: skip
    crudo = pd.DataFrame({"N": ["a"] * n, "C": [1] * n, "D": [1.5] * n, "E": ["u"] * n})
    out = info_destino(f, base, crudo, pd.DataFrame({"ID_ENTIDAD": ["e1", "e2", "e3"]}))
    assert prep.empty
    assert list(out.columns)[-4:] == ["ID_ENTIDAD", "CIUDAD_ORBIS", "X", "X_ORBIS"]
    assert out["CIUDAD"].tolist() == ["Lyon"] * n
    assert out["CIUDAD_ORBIS"].tolist() == [1] * n
    assert (out["X"].tolist(), out["X_ORBIS"].tolist()) == ([1.5] * n, ["u"] * n)


# ── representante de una empresa ──────────────────────────────────────────────


def _grafias(*filas):
    """Las grafías de UNA empresa: ``(etiqueta, nombre_norm, forma_legal, truncado, n_filas, nombre_original)``."""
    return pd.DataFrame(
        {
            "NOMBRE_NORM": [f[1] for f in filas],
            "FORMA_LEGAL": [f[2] for f in filas],
            "TRUNCADO": [f[3] for f in filas],
            "N_FILAS": [f[4] for f in filas],
            "NOMBRE_ORIGINAL": [f[5] for f in filas],
        },
        index=[f[0] for f in filas],
    )


@pytest.fixture(scope="module")
def nz_y_comp(corrida):
    return corrida.interno["nz"], corrida.interno["comp"]


def test_representante_de_una_sola_grafia_es_ella_misma(nz_y_comp):
    """Regla: con una sola grafía no hay nada que elegir; devuelve la etiqueta de su fila."""
    nz, comp = nz_y_comp
    assert representante(_grafias((7, "ZELVORA", "", False, 1, "Zelvora")), nz, comp, set()) == 7


def test_representante_prefiere_la_que_coincide_con_un_registro_destino(nz_y_comp):
    """Regla 1: la grafía con un candidato EMPRESA automático (``con_empresa``) gana aunque tenga menos filas."""
    nz, comp = nz_y_comp
    g = _grafias(
        (10, "KRAMENTO FOODS", "", False, 9, "Kramento Foods"),
        (11, "KRAMENTO FOODS SPAIN", "", False, 1, "Kramento Foods Spain"),
    )
    assert representante(g, nz, comp, {11}) == 11
    assert representante(g, nz, comp, set()) == 10


def test_representante_no_elige_una_abreviatura_de_otra_grafia(nz_y_comp):
    """Regla 2: PLVK es abreviatura de PELUVIKA: no da nombre a la empresa aunque tenga más filas, y tampoco una
    grafía sin palabras útiles («SL»). Pero la que coincide con un registro destino (regla 1) va antes."""
    nz, comp = nz_y_comp
    sigla = _grafias((20, "PLVK", "", False, 5, "PLVK"), (21, "PELUVIKA", "", False, 1, "Peluvika"))
    assert representante(sigla, nz, comp, set()) == 21
    assert representante(sigla.iloc[::-1], nz, comp, set()) == 21
    assert representante(sigla, nz, comp, {20}) == 20
    vacia = _grafias((30, "SL", "SL", False, 9, "SL"), (31, "ZELVORA", "", False, 1, "Zelvora"))
    assert representante(vacia, nz, comp, set()) == 31


def test_representante_prefiere_la_mas_central_antes_que_la_mas_frecuente(nz_y_comp):
    """Regla 3: la grafía más parecida, en promedio, a las demás (puntaje de la librería a 2 decimales) gana
    antes que la más frecuente: ZELVORA FOODS comparte palabra con las otras dos."""
    nz, comp = nz_y_comp
    for filas in ([1, 1, 1], [9, 1, 1], [1, 1, 9], [1, 9, 1]):
        g = _grafias(
            (100, "ZELVORA ALIMENTOS", "", False, filas[0], "Zelvora Alimentos"),
            (101, "ZELVORA FOODS", "", False, filas[1], "Zelvora Foods"),
            (102, "KRAMENTO FOODS", "", False, filas[2], "Kramento Foods"),
        )
        assert representante(g, nz, comp, set()) == 101


def test_representante_empata_los_parecidos_que_coinciden_a_dos_decimales(nz_y_comp):
    """Regla 3: el parecido medio se compara a 2 decimales: BRUMALIA CEREALES (0.5585) y BALVARDA MIELES (0.5552)
    empatan en 0.56 y decide la frecuencia, aunque la primera sea un poco más central."""
    nz, comp = nz_y_comp
    g = _grafias(
        (200, "BRUMALIA CEREALES", "", False, 1, "Brumalia Cereales"),
        (201, "BALVARDA MIELES", "", False, 4, "Balvarda Mieles"),
        (202, "TRALVORA ALIMENTOS", "", False, 1, "Tralvora Alimentos"),
    )
    assert representante(g, nz, comp, set()) == 201


def test_representante_no_empata_los_parecidos_que_difieren_a_dos_decimales(nz_y_comp):
    """Regla 3: BARDELIA VINOS (0.5188 → 0.52) gana a BALVARDA MIELES (0.5124 → 0.51) aunque tenga menos filas;
    a un solo decimal los dos valdrían 0.5 y ganaría la más frecuente."""
    nz, comp = nz_y_comp
    g = _grafias(
        (210, "BALVARDA MIELES", "", False, 4, "Balvarda Mieles"),
        (211, "BARDELIA VINOS", "", False, 1, "Bardelia Vinos"),
        (212, "CALVARIA CARNICOS", "", False, 1, "Calvaria Carnicos"),
    )
    assert representante(g, nz, comp, set()) == 211


def test_representante_prefiere_forma_legal_sin_truncar_luego_frecuencia_y_luego_alfabeto(
    nz_y_comp,
):
    """Reglas 4 a 6, con dos grafías del mismo nombre normalizado (el parecido empata): la que trae forma legal
    gana a la que no; entre iguales, la más frecuente; entre iguales en todo, el nombre original menor."""
    nz, comp = nz_y_comp
    con_forma = _grafias(
        (50, "KRAMENTO FOODS", "", False, 9, "Kramento Foods"),
        (51, "KRAMENTO FOODS", "SL", False, 1, "Kramento Foods SL"),
    )
    assert representante(con_forma, nz, comp, set()) == 51
    truncada = _grafias(
        (60, "KRAMENTO FOODS", "SL", True, 9, "Kramento Foods SL"),
        (61, "KRAMENTO FOODS", "SL", False, 1, "Kramento Foods SL"),
    )
    assert representante(truncada, nz, comp, set()) == 61
    frecuente = _grafias(
        (70, "KRAMENTO FOODS", "SL", False, 1, "Kramento Foods SL"),
        (71, "KRAMENTO FOODS", "SL", False, 4, "KRAMENTO FOODS SL"),
    )
    assert representante(frecuente, nz, comp, set()) == 71
    iguales = _grafias(
        (80, "KRAMENTO FOODS", "SL", False, 2, "Kramento Foods SL"),
        (81, "KRAMENTO FOODS", "SL", False, 2, "KRAMENTO FOODS SL"),
    )
    assert representante(iguales, nz, comp, set()) == 81
    assert representante(iguales.iloc[::-1], nz, comp, set()) == 81


# ── las empresas de la ancla ──────────────────────────────────────────────────


def test_numerar_empresas_ordena_por_pais_nombre_y_numera_desde_uno(corrida):
    """Reglas: las empresas se numeran ``<prefijo>-0001``… en orden de ISO3 del país, nombre normalizado y nombre
    original de su grafía representante (sin país va primero); cada empresa trae los datos de esa grafía."""
    emp = numerar_empresas(corrida.cfg, corrida.interno)
    assert len(emp.orden) == 79
    assert emp.orden[:3] == [78, 8, 0]
    assert [emp.id_ent[e] for e in emp.orden[:3]] == ["EXP-0001", "EXP-0002", "EXP-0003"]
    assert emp.id_ent[emp.orden[-1]] == "EXP-0079"
    assert (emp.nombre_ent[78], emp.norm_ent[78], emp.pais_ent[78], emp.iso_ent[78]) == (
        "Mirelda Chocolates", "MIRELDA CHOCOLATES", "", "",
    )  # fmt: skip
    assert (emp.nombre_ent[8], emp.pais_ent[8], emp.iso_ent[8]) == (
        "Pelmonte Foods",
        "Colombia",
        "COL",
    )
    assert emp.nombre_ent[17] == "Dromanto Vinagres"


def test_numerar_empresas_une_las_grafias_de_cada_empresa(corrida):
    """Reglas: ``grafias`` es ``graf`` con la empresa (``ENT``) y ``ent_de_clave`` da la empresa de cada grafía;
    la representante de una empresa con varias grafías sale de ``representante``."""
    emp = numerar_empresas(corrida.cfg, corrida.interno)
    assert len(emp.grafias) == 85
    assert emp.grafias["ENT"].tolist() == corrida.interno["ent"].tolist()
    assert "ENT" not in corrida.interno["graf"].columns
    assert emp.ent_de_clave["España|Kramento Foods S.L."] == 33
    assert emp.ent_de_clave["Alemania|BRUMBONIA CHOCOLATES SL"] == 0
    brumalia = [k for k in emp.ent_de_clave if k.startswith("España|") and "umalia" in k.lower()]
    assert len({emp.ent_de_clave[k] for k in brumalia}) == 1
    e = emp.ent_de_clave[brumalia[0]]
    assert emp.nombre_ent[e] == "Brumalia Cereales SA"
    assert emp.grafias["NOMBRE_ORIGINAL"].iat[emp.posicion[e]] == "Brumalia Cereales SA"


@pytest.mark.parametrize(
    "nivel, banda, da_nombre",
    [
        pytest.param("EMPRESA", "A", True, id="empresa_a"),
        pytest.param("EMPRESA", "B", True, id="empresa_b"),
        pytest.param("EMPRESA", "C", False, id="empresa_c"),
        pytest.param("GRUPO", "A", False, id="grupo_a"),
        pytest.param("GRUPO", "B", False, id="grupo_b"),
        pytest.param("REVISAR", "C", False, id="revisar_c"),
    ],
)
def test_numerar_empresas_solo_prefiere_un_candidato_empresa_automatico(
    corrida, nivel, banda, da_nombre
):
    """Regla 1 del representante: solo cuenta un candidato de nivel EMPRESA y banda A o B (vínculo automático).
    De las tres grafías de Brumalia, «BRUMALIA CEREALES S.A.» (1 fila) da nombre a la empresa solo si lo tiene;
    si no, lo da «Brumalia Cereales SA» (2 filas)."""
    candidatos = pd.DataFrame({"g": [10], "NIVEL_AUTO": [nivel], "BANDA_AUTO": [banda]})
    emp = numerar_empresas(corrida.cfg, {**corrida.interno, "P": candidatos})
    assert emp.nombre_ent[10] == ("BRUMALIA CEREALES S.A." if da_nombre else "Brumalia Cereales SA")


def test_numerar_empresas_sin_candidatos_no_prefiere_ninguna_grafia(corrida):
    """Regla: sin tabla de candidatos no hay grafías con un registro destino y la representante es la habitual."""
    emp = numerar_empresas(corrida.cfg, {**corrida.interno, "P": pd.DataFrame()})
    assert emp.nombre_ent[10] == "Brumalia Cereales SA"


def test_filas_de_la_ancla_agrega_la_empresa_las_metricas_y_el_contexto(corrida):
    """Reglas: a ``prep`` de la ancla se le agregan ``CLAVE`` (país|nombre), ``ENT``, cada métrica como número
    (cero donde no lo es) y cada columna de contexto tal cual; la ancla cruda sale con índice 0..n-1."""
    emp = numerar_empresas(corrida.cfg, corrida.interno)
    c = at.con_ancla(corrida, valores={("Kilos", 63): "n.a."})
    pa, ca = filas_de_la_ancla(c.cfg, c.interno, emp.ent_de_clave)
    assert (len(pa), len(ca)) == (88, 88)
    assert list(pa.columns)[-12:] == [
        "TRUNCADO", "INTERMEDIARIO", "SIN_NOMBRE_UTIL", "CLAVE", "ENT", "Operaciones", "Valor U$S", "Kilos",
        "Fletes", "HS6", "Descripción", "Régimen",
    ]  # fmt: skip
    assert pa["CLAVE"].iat[1] == "España|Kramento Foods S.L."
    assert pa["ENT"].iat[1] == 33
    assert pa["ENT"].tolist() == [emp.ent_de_clave[k] for k in pa["CLAVE"]]
    assert pa["Kilos"].iat[1] == 800
    balvarda = pa[pa["FILA_EXCEL"] == 63]
    assert balvarda["Kilos"].tolist() == [0]  # «n.a.» no es un número
    assert ca["Kilos"].iat[61] == "n.a."  # la ancla cruda no se toca
    assert pa["HS6"].iat[1] == "190590"
    assert ca.index.tolist() == list(range(88))


# ── posibles duplicados y reglas de unión ─────────────────────────────────────

DUP_PEND = {
    17: ["Dromanto Vinagres Artesanales", "Dromanto Vinagres Artesanalez", "Dromanto Vinagress"],
    18: ["Dromanto Vinagres", "Dromanto Vinagres"],
    19: ["Dromanto Vinagres"],
    21: ["Fribelia SL"],
    22: ["Fribelia S. COOP"],
    24: ["Garandel Foods"],
    27: ["GRDL"],
    34: ["Pustorán Foods (Lamberto)"],
    40: ["Peluvika Dulces (Dulcevent)"],
    42: ["PLVK SLU"],
    44: ["Lamberto"],
    54: ["Tulvanto Quesos"],
    55: ["Tulvanto Aceites"],
}


def test_duplicados_pendientes_anota_a_cada_empresa_las_grafias_de_la_otra(corrida):
    """Regla: por cada par pendiente cuyas grafías quedaron en empresas DISTINTAS, cada empresa anota el nombre
    de la grafía de la otra (con repeticiones: una por par)."""
    emp = numerar_empresas(corrida.cfg, corrida.interno)
    assert duplicados_pendientes(corrida.cfg, corrida.interno, emp.grafias) == DUP_PEND


def test_duplicados_pendientes_omite_los_pares_que_ya_tienen_una_decision(corrida):
    """Regla: un par pendiente con una decisión DUPLICADO (aunque sea DISTINTA) no se señala."""
    c = at.con_pendiente(corrida, "España|Sementa Pastas", "España|Sementa Pastas SA")
    emp = numerar_empresas(c.cfg, c.interno)
    assert duplicados_pendientes(c.cfg, c.interno, emp.grafias) == DUP_PEND
    sin = at.sin_decisiones(c)
    emp = numerar_empresas(sin.cfg, sin.interno)
    pendientes = duplicados_pendientes(sin.cfg, sin.interno, emp.grafias)
    assert sorted(pendientes[e] for e in pendientes if e not in DUP_PEND) == [
        ["Sementa Pastas"],
        ["Sementa Pastas SA"],
    ]


def test_duplicados_pendientes_no_cuenta_los_pares_de_una_misma_empresa(corrida):
    """Regla: un par pendiente cuyas dos grafías quedaron en la MISMA empresa ya no es un posible duplicado."""
    c = at.con_pendiente(corrida, "España|Brumalia Cereales", "España|Brumalia Cereales SA")
    emp = numerar_empresas(c.cfg, c.interno)
    assert duplicados_pendientes(c.cfg, c.interno, emp.grafias) == DUP_PEND


def test_duplicados_pendientes_ignora_las_decisiones_de_otras_fuentes_y_tipos(corrida):
    """Regla: solo cuentan las decisiones DUPLICADO de la ANCLA; una decisión VINCULO no oculta un pendiente."""
    c = at.con_pendiente(corrida, "España|Sementa Pastas", "España|Sementa Pastas SA")
    dec = c.interno["dec"]
    dec.loc[dec["TIPO"] == "DUPLICADO", "FUENTE"] = "OTRA"
    emp = numerar_empresas(c.cfg, c.interno)
    pendientes = duplicados_pendientes(c.cfg, c.interno, emp.grafias)
    assert sorted(pendientes[e] for e in pendientes if e not in DUP_PEND) == [
        ["Sementa Pastas"],
        ["Sementa Pastas SA"],
    ]


def test_reglas_de_union_agrupa_las_reglas_por_posicion():
    """Regla: ``{posición: [REGLA, …]}`` con cada unión anotada en sus dos extremos, en el orden de las filas."""
    uniones = pd.DataFrame({"pos_a": [0, 1, 0], "pos_b": [1, 2, 2], "REGLA": ["r1", "r2", "r3"]})
    assert dict(reglas_de_union(uniones)) == {0: ["r1", "r3"], 1: ["r1", "r2"], 2: ["r2", "r3"]}
    assert dict(reglas_de_union(uniones.iloc[:0])) == {}


def test_reglas_de_union_de_la_ancla(corrida):
    """Regla: sobre las uniones de la ancla del conjunto de prueba."""
    esperado = {
        10: ["mismo nombre normalizado"] * 2,
        12: ["mismo nombre normalizado"] * 2,
        13: ["mismo nombre normalizado"] * 2,
        18: ["decisión: (verificar) paneles"],
        19: ["decisión: (verificar) paneles"],
        21: ["mismas palabras con errata"],
        22: ["mismas palabras con errata"],
        27: ["mismas letras, otros espacios"],
        28: ["mismas letras, otros espacios"],
        33: ["mismas palabras con truncamiento"],
        34: ["mismas palabras con truncamiento"],
    }
    assert dict(reglas_de_union(corrida.interno["un_a"])) == esperado


# ── estado común de las secciones ─────────────────────────────────────────────


def test_el_estado_expone_los_destinos_y_sus_etiquetas(corrida):
    """Regla: ``EstadoTablas.destinos`` y ``etiquetas`` salen de la configuración, en el orden de los destinos."""
    estado = EstadoTablas(
        cfg=corrida.cfg, interno=corrida.interno, empresas=None, filas_ancla=None, crudo_ancla=None,
        info={}, pares=None, vinculos=None, dup_pend={}, reglas_union={},
    )  # fmt: skip
    assert [d.clave for d in estado.destinos] == ["ORBIS", "FDI"]
    assert estado.etiquetas == {"ORBIS": "ORBIS", "FDI": "FDI MARKETS"}
    assert estado.destinos is not estado.cfg.destinos


# ── entradas incompletas ──────────────────────────────────────────────────────
# El original no valida el estado que recibe: un dato que falta sale como el error de pandas, que al menos nombra
# la columna o la clave. Se fijan para que una validación futura no cambie el tipo de error sin declararlo.


@pytest.mark.parametrize("columna", ["Kilos", "HS6"])
def test_una_columna_configurada_que_falta_en_la_ancla_leida_se_nombra_en_el_error(
    corrida, columna
):
    """Camino de error: una métrica (Kilos) o una columna de contexto (HS6) declarada en la configuración que no
    está en la ancla leída revienta con ``KeyError`` que nombra la columna."""
    c = corrida.copia()
    ancla = c.interno["crudos"][c.cfg.ancla.clave]
    c.interno["crudos"][c.cfg.ancla.clave] = ancla.drop(columns=[columna])
    with pytest.raises(KeyError, match=f"'{columna}'"):
        c.construir()


def test_un_par_de_una_fuente_que_no_es_destino_revienta_nombrandola(corrida):
    """Camino de error: un par cuya ``FUENTE_DESTINO`` no es un destino de la configuración revienta con
    ``KeyError`` que nombra la fuente."""
    c = corrida.copia()
    c.interno["L"]["FUENTE_DESTINO"] = "NOEXISTE"
    with pytest.raises(KeyError, match="'NOEXISTE'"):
        c.construir()


def test_una_asignacion_de_empresas_de_otro_largo_que_las_grafias_revienta(corrida):
    """Camino de error: si ``ent`` (la empresa de cada grafía) no tiene tantos elementos como grafías hay,
    ``ValueError`` con los dos largos."""
    c = corrida.copia()
    c.interno["ent"] = c.interno["ent"][:-1]
    with pytest.raises(
        ValueError, match=r"Length of values \(84\) does not match length of index \(85\)"
    ):
        c.construir()


@pytest.mark.parametrize("pieza", ["graf", "prep", "dec"])
def test_un_estado_al_que_le_falta_una_pieza_revienta_nombrandola(corrida, pieza):
    """Camino de error: si el estado interno no trae una de las piezas (``graf``, ``prep``, ``dec``…) revienta con
    ``KeyError`` que nombra la pieza."""
    c = corrida.copia()
    del c.interno[pieza]
    with pytest.raises(KeyError, match=f"'{pieza}'"):
        c.construir()


# ── hallazgos del original que se conservaron ─────────────────────────────────


def test_una_etiqueta_en_blanco_revienta_al_final_de_la_corrida(corrida):
    """Hallazgo F3: ``etiqueta.split()[0]`` falla con una etiqueta en blanco (``IndexError``), terminado todo el
    cruce y sin decir cuál es el destino."""
    c = at.con_destino(corrida, "FDI", etiqueta="  ")
    with pytest.raises(IndexError, match="list index out of range"):
        c.construir()


def test_dos_destinos_con_el_mismo_prefijo_se_pisan_en_exportadores(corrida):
    """Hallazgo F3: si las etiquetas de dos destinos empiezan por la misma palabra («ORBIS» y «ORBIS NORTE»), el
    bloque del segundo PISA al del primero en EXPORTADORES, sin aviso: Kramento queda sin su vínculo de ORBIS."""
    res = at.con_destino(corrida, "FDI", etiqueta="ORBIS NORTE").construir()
    e = res.tablas["EXPORTADORES"]
    assert len(e.columns) == 39  # 49 - 10: el bloque de FDI no tiene columnas propias
    v = exportador(res, "Vantiko Chocolates S.A.")
    assert v["FUENTES_VINCULADAS"] == "ORBIS + ORBIS NORTE"
    assert (v["ORBIS_NIVEL"], v["ORBIS_NOMBRE"], v["ORBIS_ID"], v["ORBIS_N_VINCULOS"]) == (
        "EMPRESA", "Vantiko Sweets", "FDI-F0002", 2,
    )  # fmt: skip
    k = exportador(res, "Kramento Foods S.L.")
    assert k["FUENTES_VINCULADAS"] == "ORBIS"
    assert (k["ORBIS_NIVEL"], k["ORBIS_NOMBRE"], k["ORBIS_N_VINCULOS"]) == ("", "", 0)


def test_un_contexto_que_choca_con_una_columna_base_muestra_la_base(corrida):
    """Hallazgo F3: ``info_destino`` renombra a ``<nombre>_<clave>`` un contexto cuyo nombre choca con una columna
    base (CIUDAD), pero EXPORTADORES pregunta si ``nombre`` está en la tabla YA completa: la columna base lo está
    y se muestra ella en lugar del contexto declarado."""
    ingresos = "Operating revenue (Turnover) m USD Last avail. yr"
    c = at.con_destino(
        corrida,
        "ORBIS",
        nombres_contexto={ingresos: "CIUDAD", "Number of employees Last avail. yr": "EMPLEADOS"},
    )
    res = c.construir()
    v = exportador(res, "Vantiko Chocolates S.A.")
    assert (v["ORBIS_CIUDAD"], v["ORBIS_EMPLEADOS"]) == ("Valencia", 40.0)
    assert "ORBIS_INGRESOS_USD_M" not in res.tablas["EXPORTADORES"].columns
    detalle = fila_de(res.tablas["DETALLE_ORBIS"], "ID_REGISTRO", "ESB0000029")
    assert (detalle["CIUDAD"], detalle["CIUDAD_ORBIS"]) == ("Valencia", 5.5)


def test_una_clave_de_repeticion_con_errata_se_ignora_sin_aviso(corrida):
    """Hallazgo F3: una clave de ``claves_repeticion`` que no es columna de la ancla se ignora sin aviso. Con las
    mismas Kilos (500) en dos partidas distintas, ``('HS6',)`` suma 1000 sin repetidos pero la errata ``('HS',)``
    suma 500, y el DETALLE marca como repetida una fila que no lo es."""
    valores = {("Kilos", 63): 500}

    def balvarda(**atributos):
        res = at.con_ancla(corrida, valores, **atributos).construir()
        f = exportador(res, "Balvarda Mieles SL")
        detalle = res.tablas["DETALLE_EXPORTADORES"]
        return f["KILOS_SIN_REPETIDOS"], detalle.loc[
            detalle["Empresa"] == "Balvarda Mieles SL", "KILOS_REPETIDO"
        ].tolist()

    assert balvarda() == (1000.0, ["NO", "SÍ", "NO"])
    assert balvarda(claves_repeticion=("HS",)) == (500.0, ["NO", "SÍ", "SÍ"])
    assert balvarda(claves_repeticion=()) == (500.0, ["NO", "SÍ", "SÍ"])


def test_el_detalle_nombra_la_columna_repetida_con_la_columna_de_origen(corrida):
    """Hallazgo F3: DETALLE_EXPORTADORES usa ``columna.upper()`` y EXPORTADORES el nombre legible: una métrica
    renombrada («Valor U$S» → VALOR_USD) sale como «VALOR U$S_REPETIDO» en una hoja y «VALOR_USD_SIN_REPETIDOS»
    en la otra."""
    res = at.con_ancla(corrida, metricas_repetidas=("Valor U$S", "Kilos")).construir()
    assert [c for c in res.tablas["EXPORTADORES"].columns if "SIN_REP" in c] == [
        "VALOR_USD_SIN_REPETIDOS",
        "KILOS_SIN_REPETIDOS",
    ]
    assert [c for c in res.tablas["DETALLE_EXPORTADORES"].columns if "REPETIDO" in c] == [
        "VALOR U$S_REPETIDO",
        "KILOS_REPETIDO",
    ]


def test_la_pregunta_de_revision_muestra_parentesis_vacios_sin_pais(base):
    """Hallazgo F3: si la empresa no trae país, la pregunta de REVISION dice «( )» vacío: «¿«Mirelda Chocolates» ()…»."""
    r = base.tablas["REVISION"]
    f = fila_de(r[r["TIPO"] == "VINCULO"], "CLAVE_B", "ESB0000014")
    assert f["PREGUNTA"].startswith("¿«Mirelda Chocolates» () es la misma empresa")


def test_un_par_de_decision_se_puntua_con_el_nombre_de_la_grafia_representante(corrida):
    """Hallazgo F3: el puntaje y los rasgos de un par sin puntaje se calculan con el nombre normalizado de la
    grafía REPRESENTANTE de la empresa, no con el de la grafía del par: «Dromanto Vinagres Artesanalez» sale
    comparada como «DROMANTO VINAGRES ARTESANALES»."""
    c = at.par_de_decision_de_otra_grafia(
        corrida, "Dromanto Vinagres Artesanales", "España|Dromanto Vinagres Artesanalez"
    )
    enlaces = c.construir().tablas["ENLACES"]
    f = fila_de(enlaces, "ID_EXPORTADOR", "EXP-0020")
    assert f["GRAFIA_EXPORTADOR"] == "Dromanto Vinagres Artesanalez"
    assert f["NOMBRE_COMPARADO_EXPORTADOR"] == "DROMANTO VINAGRES ARTESANALES"
    assert f["NOMBRE_COMPARADO_DESTINO"] == "TULVANTO VINAGRES"
    assert f["COMPARTIDAS"] == "VINAGRES"
    assert (
        f["MOTIVO"]
        == "Sin evidencia automática suficiente por nombre; vínculo aportado por revisión"
    )
