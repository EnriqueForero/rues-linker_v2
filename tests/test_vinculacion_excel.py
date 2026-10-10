"""Excel de entrega de la vinculación (``flujo.vinculacion_excel``).

Los valores esperados NO salen de razonar sobre el código: se midieron ejecutando el ``exportar_excel`` del
motor original (celda del notebook 10) sobre el mismo ``Resultado`` inventado que arma ``_armar`` y se
copiaron aquí como constantes, con la regla que ejerce cada una. Las empresas son inventadas.

Cambios de TEXTO declarados respecto del original (los únicos): tres ejemplos de la guía del LEEME que
nombraban empresas reales y el subtítulo del LEEME, que ahora dice ``motor flujo.vinculacion``.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from openpyxl import Workbook, load_workbook

import record_linkage
from record_linkage.flujo import vinculacion_excel
from record_linkage.flujo.vinculacion.config import ConfigVinculacion, Fuente
from record_linkage.flujo.vinculacion.constantes import NOMBRE_MOTOR
from record_linkage.flujo.vinculacion.resultado import Resultado
from record_linkage.flujo.vinculacion_excel import (
    estilos,
    exportar_excel,
    exportar_vinculacion_excel,
    hojas,
    leeme,
    tablas,
)

#: Las clases con las que ``_armar`` construye el ``Resultado``: las del paquete nuevo.
CLASES = SimpleNamespace(Fuente=Fuente, ConfigVinculacion=ConfigVinculacion, Resultado=Resultado)


def _fuentes(clases: SimpleNamespace, destinos: tuple[str, ...]) -> tuple[Fuente, list[Fuente]]:
    """La ancla y los destinos pedidos (``ORBIS`` y ``FDI``): bases inventadas con sus columnas."""
    ancla = clases.Fuente(
        clave="EXPORTADORES",
        archivo="ancla.xlsx",
        col_nombre="Empresa",
        col_pais="País de Empresa",
        cols_metricas=("Operaciones", "Valor U$S", "Fletes"),
        cols_contexto=("Descripción",),
        nombres_contexto={"Valor U$S": "VALOR_USD", "Descripción": "DESCRIPCION_PRODUCTO"},
    )
    todos = {
        "ORBIS": clases.Fuente(
            clave="ORBIS",
            archivo="descargas.xlsx",
            hoja="Orbis",
            col_nombre="Company name Latin alphabet",
            col_pais="Country",
            col_web="Website address",
        ),
        "FDI": clases.Fuente(
            clave="FDI",
            etiqueta="FDI MARKETS",
            archivo="descargas.xlsx",
            hoja="FDI",
            col_nombre="Company name",
            col_pais="País de consulta",
            cols_pais_extra=("HQ country",),
        ),
    }
    return ancla, [todos[d] for d in destinos]


def _tablas_base(destinos: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """Las tablas de salida, pequeñas y con cada caso que el Excel trata distinto."""
    exportadores = pd.DataFrame(
        {
            "ID_EXPORTADOR": [f"EXP-{i:05d}" for i in range(1, 7)],
            "NOMBRE_EXPORTADOR": [
                "Zelvora Alimentos S.A.",
                "Kramento Foods S.L.",
                "Orbélia Conservas",
                "Tavorín Conservas Marinas",
                "Quelmar Dulces",
                "Vantiko Chocolates",
            ],
            "RESULTADO": [
                "VINCULADO",
                "VINCULADO",
                "CANDIDATO POR REVISAR",
                "SIN COINCIDENCIA",
                "VINCULADO",
                "SIN COINCIDENCIA",
            ],
            "NIVEL_VINCULO": ["EMPRESA", "GRUPO", "", "", "EMPRESA", ""],
            "CONFIANZA": ["ALTA", "MEDIA", "", "", "ALTA", ""],
            "REQUIERE_REVISION": ["NO", "SÍ", "SÍ", "NO", "NO", "NO"],
            "FUENTES_VINCULADAS": ["ORBIS", "ORBIS + FDI MARKETS", "", "", "FDI MARKETS", ""],
            "OPERACIONES": [3, 8, 1, 4, 2, 6],
            "VALOR_USD": [12000.5, 8000.0, 500.25, 7300.75, 150.0, 99999.99],
            "FLETES": [0.0] * 6,
            "DESCRIPCION_PRODUCTO": ["Galletas"] * 6,
            "ORBIS_NIVEL": ["EMPRESA", "GRUPO", "", "", "", ""],
            "ORBIS_WEB": ["www.zelvora.es", "https://kramento.com", "", "", "", ""],
            "FDI_NIVEL": ["", "GRUPO", "", "", "EMPRESA", ""],
            "FDI_WEB": ["", "orbelia.org | otra.com", "sin web", "", "quelmar.fr", ""],
            "CONTACTO_WEB": ["zelvora.es | x.com", "", "", "", "quelmar.fr", ""],
        }
    )
    for base in ("ORBIS", "FDI"):
        if base not in destinos:
            exportadores = exportadores.drop(columns=[f"{base}_NIVEL", f"{base}_WEB"])
    enlaces = pd.DataFrame(
        {
            "ID_EXPORTADOR": ["EXP-00001", "EXP-00002", "EXP-00005"],
            "FUENTE": ["ORBIS", "ORBIS", "FDI"],
            "NOMBRE_B": ["ZELVORA ALIMENTOS SA", "KRAMENTO HOLDING", "QUELMAR DULCES SL"],
            "NIVEL": ["EMPRESA", "GRUPO", "EMPRESA"],
            "CONFIANZA": ["ALTA", "MEDIA", "ALTA"],
            "ES_PRINCIPAL": [True, True, False],
            "SCORE_RUES_LINKER": [1.0, 0.82, 0.9],
            "ANIO": [2026, 2026, 2025],
            "MOTIVO": ["Mismo nombre", "Marca\x07 compartida", "=Mismo nombre y país"],
        }
    )
    revision = pd.DataFrame(
        {
            "TIPO": ["VINCULO", "VINCULO", "DUPLICADO", "VINCULO"],
            "FUENTE": ["ORBIS", "ORBIS", "EXPORTADORES", "FDI"],
            "CLAVE_A": ["España|Kramento Foods S.L.", "España|Orbélia Conservas", "x", "y"],
            "CLAVE_B": ["ESB0000002", "ESB0000003", "z", "FDI-F0002"],
            "ESTADO_REVISION": ["PENDIENTE", "VALIDAR", "DECIDIDO", "PENDIENTE"],
            "DECISION": ["", "", "DISTINTA", ""],
            "AUTOR": ["", "", "Revisora Uno", ""],
            "FECHA": ["", "", "2026-09-29", ""],
            "RAZON": ["", "", "homónimos", ""],
        }
    )
    decisiones = pd.DataFrame(
        {
            "ARCHIVO": ["decisiones_1.csv", "decisiones_2.csv"],
            "TIPO": ["VINCULO", "VINCULO"],
            "DECISION": ["EMPRESA", "GRUPO"],
            "AUTOR": ["Revisora Uno", "Revisor Dos"],
        }
    )
    vacia = pd.DataFrame()
    validacion = pd.DataFrame(
        {
            "FUENTE": ["ORBIS", "FDI MARKETS"],
            "AUTOMATICOS": [10, 4],
            "PRECISION": [0.9, 0.75],
            "RECALL": [0.8, 0.6],
            "F1": [0.85, 0.667],
            "RECALL_CONTANDO_CANDIDATOS_C": [0.95, 0.8],
        }
    )
    tablas = {
        "EXPORTADORES": exportadores,
        "ENLACES": enlaces,
        "REVISION": revision,
        "DECISIONES": decisiones,
        "CONTACTOS": pd.DataFrame(
            {"ID_EXPORTADOR": ["EXP-00001"], "WEB": ["zelvora.es"], "TELEFONO": ["600111222"]}
        ),
        "DETALLE_EXPORTADORES": pd.DataFrame(
            {"FILA_EXCEL": [2, 3, 4], "ID_EXPORTADOR": ["EXP-00001", "EXP-00002", "EXP-00003"]}
        ),
        "DETALLE_ORBIS": pd.DataFrame(
            {"ID_ORBIS": ["ESB0000001"], "NOMBRE": ["ZELVORA ALIMENTOS SA"]}
        ),
        "DETALLE_FDI": pd.DataFrame({"ID_FDI": ["FDI-F0001"], "NOMBRE": ["Quelmar Dulces"]}),
        "DEDUPLICACION": pd.DataFrame({"BASE": ["ORBIS"], "MOTIVO": ["mismo nombre"]}),
        "VALIDACION": validacion,
        "VALIDACION_DETALLE": pd.DataFrame({"CLAVE_A": ["x"], "MOTOR": ["B"], "REFERENCIA": ["A"]}),
        "COMPARACION_REVISIONES": vacia,
        "CALIDAD_DATOS": pd.DataFrame(
            {
                "FUENTE": ["EXPORTADORES", "ORBIS"],
                "TEMA": ["Métrica en cero", "Nombres cortados"],
                "HALLAZGO": ["FLETES vale 0 en todas las filas.", "3 nombres de 35 caracteres."],
                "FILAS_AFECTADAS": [6, 3],
                "IMPACTO": ["No sirve para comparar.", "Pueden faltar palabras."],
                "QUE_HACER": ["Ignorarla.", "Revisar."],
                "EJEMPLOS": ["", "x"],
            }
        ),
        "CONTROL_QA": pd.DataFrame(
            {
                "CONTROL": ["Conservación de filas", "Recall del bloqueo", "Vínculos A alineados"],
                "ESTADO": ["OK", "ADVERTENCIA", "FALLA"],
                "CUMPLE": [True, True, False],
            }
        ),
        "PARAMETROS": pd.DataFrame(
            {"PARAMETRO": ["motor", "umbral_dedup"], "VALOR": ["flujo.vinculacion", "0.84"]}
        ),
    }
    for base in ("ORBIS", "FDI"):
        if base not in destinos:
            del tablas[f"DETALLE_{base}"]
    return tablas


def _resumen(destinos: tuple[str, ...]) -> pd.DataFrame:
    """Indicadores del RESUMEN: cada fila ejerce una de las fórmulas vivas (o ninguna)."""
    filas = [
        ("Entradas", "Empresas EXPORTADORES (tras deduplicar)", 6, "empresas", "x"),
        (
            "Resultado",
            "Empresas vinculadas a al menos una base",
            3,
            "empresas",
            "50.0% de las empresas.",
        ),
        ("Resultado", "  · con la MISMA EMPRESA en alguna base", 2, "empresas", ""),
        ("Resultado", "  · solo con su GRUPO empresarial", 1, "empresas", ""),
        *(
            [("Resultado", "  · vinculadas a ORBIS", 2, "empresas", "")]
            if "ORBIS" in destinos
            else []
        ),
        *(
            [("Resultado", "  · vinculadas a FDI MARKETS", 2, "empresas", "")]
            if "FDI" in destinos
            else []
        ),
        ("Resultado", "  · vinculadas a todas las bases", 1, "empresas", ""),
        ("Resultado", "OPERACIONES de las empresas vinculadas", 0.65, "%", "13 de 24."),
        (
            "Resultado",
            "INEXISTENTE de las empresas vinculadas",
            0.5,
            "%",
            "sin columna: sin fórmula",
        ),
        ("Confianza", "Empresas que requieren revisión", 2, "empresas", ""),
        ("Controles", "Invariantes que cumplen", "2 de 3", "", "FALLAN: x"),
    ]
    return pd.DataFrame(filas, columns=["SECCION", "INDICADOR", "VALOR", "UNIDAD", "LECTURA"])


def _armar(
    carpeta: Path,
    *,
    clases: SimpleNamespace | None = None,
    destinos: tuple[str, ...] = ("ORBIS", "FDI"),
    versionar: bool = False,
    tablas: dict[str, pd.DataFrame | None] | None = None,
    resumen: pd.DataFrame | None = None,
    por_pais: pd.DataFrame | None = None,
    calidad: pd.DataFrame | None = None,
) -> Resultado:
    """Un ``Resultado`` mínimo y completo; los argumentos opcionales reemplazan una tabla por su nombre."""
    clases = clases or CLASES
    ancla, bases = _fuentes(clases, destinos)
    cfg = clases.ConfigVinculacion(
        ancla=ancla,
        destinos=bases,
        carpeta_salida=str(carpeta / "salida"),
        prefijo_salida="VINCULACION_PRUEBA",
        versionar_salida=versionar,
        titulo="Vinculación de prueba (empresas inventadas)",
    )
    todas = {**_tablas_base(destinos), **(tablas or {})}
    if por_pais is None:
        por_pais = pd.DataFrame(
            {
                "PAIS": ["España", "Italia"],
                "EMPRESAS": [4, 2],
                "PCT_VINCULADAS": [0.75, 0.5],
                "SCORE_MEDIO": [0.93, 0.8],
            }
        )
    return clases.Resultado(
        cfg=cfg,
        tablas=todas,
        resumen=_resumen(destinos) if resumen is None else resumen,
        por_pais=por_pais,
        validacion=todas["VALIDACION"],
        invariantes=todas["CONTROL_QA"],
        calidad=todas["CALIDAD_DATOS"] if calidad is None else calidad,
        avisos=[],
        parametros=todas["PARAMETROS"],
        segundos=0.0,
    )


# ── Utilidades de lectura ────────────────────────────────────────────────────
def _abrir(res: Resultado, carpeta: Path) -> Workbook:
    """Exporta ``res`` y devuelve el libro leído de vuelta (lo que vería quien lo abre)."""
    return load_workbook(exportar_vinculacion_excel(res, carpeta / "libro.xlsx"))


@pytest.fixture
def libro(tmp_path: Path) -> Workbook:
    """El libro de la ``Resultado`` completa con ORBIS y FDI."""
    return _abrir(_armar(tmp_path), tmp_path)


def _comentarios(hoja) -> dict[str, tuple[str, str]]:
    return {
        c.coordinate: (c.comment.text, c.comment.author)
        for f in hoja.iter_rows()
        for c in f
        if c.comment
    }


def _hipervinculos(hoja) -> dict[str, str]:
    return {c.coordinate: c.hyperlink.target for f in hoja.iter_rows() for c in f if c.hyperlink}


def _semaforos(hoja) -> list[tuple[str, str, str]]:
    """(rango, fórmula, color de relleno) de cada regla de formato condicional, en orden."""
    return [
        (str(cf.sqref), regla.formula[0], regla.dxf.fill.fgColor.rgb)
        for cf in hoja.conditional_formatting
        for regla in cf.rules
    ]


def _relleno(celda) -> str | None:
    return celda.fill.fgColor.rgb if celda.fill.fill_type else None


def _columna_a(hoja, desde: int, hasta: int) -> list[object]:
    return [hoja.cell(row=i, column=1).value for i in range(desde, hasta + 1)]


HOJAS_DE_ENTREGA = {
    ("ORBIS", "FDI"): [
        "LEEME", "RESUMEN", "EXPORTADORES", "ENLACES", "REVISION", "DECISIONES", "CONTACTOS",
        "DETALLE_EXPORTADORES", "DETALLE_ORBIS", "DETALLE_FDI", "DEDUPLICACION", "VALIDACION",
        "CALIDAD_DATOS", "CONTROL_QA", "PARAMETROS", "METODOLOGIA",
    ],
    ("ORBIS",): [
        "LEEME", "RESUMEN", "EXPORTADORES", "ENLACES", "REVISION", "DECISIONES", "CONTACTOS",
        "DETALLE_EXPORTADORES", "DETALLE_ORBIS", "DEDUPLICACION", "VALIDACION",
        "CALIDAD_DATOS", "CONTROL_QA", "PARAMETROS", "METODOLOGIA",
    ],
    (): [
        "LEEME", "RESUMEN", "EXPORTADORES", "ENLACES", "REVISION", "DECISIONES", "CONTACTOS",
        "DETALLE_EXPORTADORES", "DEDUPLICACION", "VALIDACION",
        "CALIDAD_DATOS", "CONTROL_QA", "PARAMETROS", "METODOLOGIA",
    ],
}  # fmt: skip


# ── Contrato público y orden de las hojas ────────────────────────────────────
def test_exportar_excel_es_el_mismo_objeto_que_exportar_vinculacion_excel():
    """El nombre del motor original sigue valiendo: lo usan el arnés de paridad y los notebooks."""
    assert exportar_excel is exportar_vinculacion_excel
    assert sorted(vinculacion_excel.__all__) == ["exportar_excel", "exportar_vinculacion_excel"]


@pytest.mark.parametrize("destinos", list(HOJAS_DE_ENTREGA), ids=["dos", "uno", "ninguno"])
def test_orden_de_las_hojas_segun_los_destinos(tmp_path, destinos):
    """El orden de entrega es fijo; hay una hoja DETALLE_ por la ancla y por cada destino."""
    libro = _abrir(_armar(tmp_path, destinos=destinos), tmp_path)
    assert libro.sheetnames == HOJAS_DE_ENTREGA[destinos]


def test_las_hojas_detalle_siguen_el_orden_de_las_tablas(tmp_path):
    """Las DETALLE_* se crean en el orden de ``res.tablas``, no en el de la configuración."""
    res = _armar(tmp_path)
    res.tablas = {"DETALLE_FDI": res.tablas["DETALLE_FDI"], **res.tablas}
    libro = _abrir(res, tmp_path)
    assert [h for h in libro.sheetnames if h.startswith("DETALLE_")] == [
        "DETALLE_FDI",
        "DETALLE_EXPORTADORES",
        "DETALLE_ORBIS",
    ]


@pytest.mark.parametrize(
    ("color", "hojas_de_la_familia"),
    [
        ("00548235", ["LEEME", "RESUMEN", "EXPORTADORES", "ENLACES", "CONTACTOS"]),
        ("00C65911", ["REVISION", "DECISIONES"]),
        ("002F5597", ["DETALLE_EXPORTADORES", "DETALLE_ORBIS", "DETALLE_FDI", "DEDUPLICACION"]),
        ("007F7F7F", ["VALIDACION", "CALIDAD_DATOS", "CONTROL_QA", "PARAMETROS", "METODOLOGIA"]),
    ],
    ids=["negocio", "revision", "detalle", "auditoria"],
)
def test_color_de_pestana_segun_la_familia_de_cada_hoja(libro, color, hojas_de_la_familia):
    assert {
        h: libro[h].sheet_properties.tabColor.rgb for h in hojas_de_la_familia
    } == dict.fromkeys(hojas_de_la_familia, color)


def test_todas_las_hojas_abren_al_90_y_el_libro_recalcula_al_abrir(libro):
    """Las fórmulas del RESUMEN se recalculan solas al abrir (``fullCalcOnLoad``)."""
    assert {h.title: h.sheet_view.zoomScale for h in libro.worksheets} == dict.fromkeys(
        libro.sheetnames, 90
    )
    assert libro.calculation.fullCalcOnLoad is True


# ── LEEME ────────────────────────────────────────────────────────────────────
def test_leeme_subtitulo_nombra_el_motor_la_version_y_la_fecha(tmp_path):
    """A2 dice «motor <NOMBRE_MOTOR> sobre rues-linker <versión> · <fecha y hora>» (cambio declarado)."""
    antes = datetime.now().replace(second=0, microsecond=0)
    libro = _abrir(_armar(tmp_path), tmp_path)
    despues = datetime.now()
    subtitulo = libro["LEEME"]["A2"].value
    patron = re.compile(
        r"Vinculación de EXPORTADORES con ORBIS y FDI MARKETS · motor (?P<motor>\S+) "
        r"sobre rues-linker (?P<version>\S+) · (?P<fecha>\d{4}-\d{2}-\d{2} \d{2}:\d{2})"
    )
    m = patron.fullmatch(subtitulo)
    assert m, subtitulo
    assert m["motor"] == NOMBRE_MOTOR == "flujo.vinculacion"
    assert m["version"] == record_linkage.__version__
    assert antes <= datetime.strptime(m["fecha"], "%Y-%m-%d %H:%M") <= despues
    assert libro["LEEME"]["A1"].value == "Vinculación de prueba (empresas inventadas)"


def test_leeme_tiene_sus_bloques_en_orden(libro):
    """Cinco títulos de sección, cada uno en su fila; la tabla de hojas sigue al segundo."""
    hoja = libro["LEEME"]
    titulos = {
        "Cómo leer este archivo en tres pasos": 4,
        "Hojas": 9,
        "Glosario": 26,
        "Columnas de EXPORTADORES": 37,
        "Cómo llenar la revisión": 47,
    }
    for titulo, fila in titulos.items():
        celda = hoja.cell(row=fila, column=1)
        assert celda.value == titulo
        assert (celda.font.b, celda.font.sz, celda.font.color.rgb) == (True, 12.0, "001F3864")
    assert [hoja.cell(row=9 + 1, column=j).value for j in (1, 2, 3)] == [
        "HOJA",
        "QUE_CONTIENE",
        "COMO_USARLA",
    ]
    pasos = _columna_a(hoja, 5, 7)
    assert [p[:2] for p in pasos] == ["1.", "2.", "3."]
    assert hoja["A1"].font.sz == 14.0 and hoja["A2"].font.i is True


def test_leeme_lista_las_hojas_el_glosario_y_las_columnas(libro):
    hoja = libro["LEEME"]
    assert _columna_a(hoja, 11, 24) == [
        "RESUMEN", "EXPORTADORES", "ENLACES", "REVISION", "DECISIONES", "CONTACTOS",
        "DETALLE_EXPORTADORES", "DETALLE_ORBIS", "DETALLE_FDI", "DEDUPLICACION", "VALIDACION*",
        "CALIDAD_DATOS", "CONTROL_QA", "PARAMETROS · METODOLOGIA",
    ]  # fmt: skip
    assert _columna_a(hoja, 27, 35) == [
        "NIVEL EMPRESA", "NIVEL GRUPO", "RELACIÓN DE PROPIEDAD", "BANDA A", "BANDA B", "BANDA C",
        "CONFIANZA ALTA / MEDIA", "ESTADO_REVISION", "SCORE_RUES_LINKER",
    ]  # fmt: skip
    assert _columna_a(hoja, 38, 45) == [
        "ID_EXPORTADOR · NOMBRE_EXPORTADOR", "RESULTADO", "NIVEL_VINCULO · CONFIANZA",
        "REQUIERE_REVISION · QUE_REVISAR", "Métricas", "ORBIS_…", "FDI_…", "CONTACTO_*",
    ]  # fmt: skip
    assert _columna_a(hoja, 48, 52) == ["DECISION", "AUTOR · FECHA", "RAZON", "Ejemplo", "No edite"]


def test_leeme_describe_el_bloque_de_cada_destino(libro):
    """El prefijo es la primera palabra de la etiqueta; el país extra solo se nombra si el destino lo declara."""
    hoja = libro["LEEME"]
    orbis, fdi = hoja["B43"].value, hoja["B44"].value
    assert orbis.startswith("Registro principal de ORBIS y sus datos. Si ORBIS_NIVEL = GRUPO,")
    assert orbis.endswith(
        "ORBIS_N_VINCULOS cuenta todos sus vínculos en esa base; ORBIS_OTROS_VINCULOS los lista."
    )
    assert fdi.startswith("Registro principal de FDI MARKETS y sus datos. Si FDI_NIVEL = GRUPO,")
    assert fdi.endswith(
        " FDI_PAIS es 'País de consulta' tal como viene; también cuenta como mismo país HQ country."
    )
    assert "_PAIS es" not in orbis


def test_leeme_celdas_combinadas_alturas_y_anchos(libro):
    """Cada definición combina B:C y su alto crece con el largo del texto (14 por línea de 150, más 2)."""
    hoja = libro["LEEME"]
    filas = [*range(27, 36), *range(38, 46), *range(48, 53)]
    assert sorted(str(r) for r in hoja.merged_cells.ranges) == sorted(f"B{i}:C{i}" for i in filas)
    assert {k: v.height for k, v in hoja.row_dimensions.items() if v.height} == {
        11: 16.0, 12: 30.0, 13: 30.0, 14: 30.0, 15: 30.0, 16: 16.0, 17: 16.0, 18: 16.0, 19: 16.0,
        20: 16.0, 21: 16.0, 22: 30.0, 23: 30.0, 24: 16.0, 27: 44.0, 28: 30.0, 29: 16.0, 30: 16.0,
        31: 30.0, 32: 30.0, 33: 30.0, 34: 30.0, 35: 30.0, 38: 30.0, 39: 16.0, 40: 16.0, 41: 30.0,
        42: 30.0, 43: 30.0, 44: 44.0, 45: 16.0, 48: 16.0, 49: 30.0, 50: 16.0, 51: 30.0, 52: 16.0,
    }  # fmt: skip
    assert {k: v.width for k, v in hoja.column_dimensions.items()} == {
        "A": 26.0,
        "B": 95.0,
        "C": 60.0,
    }
    encabezado = hoja["A10"]
    assert (encabezado.font.b, encabezado.font.color.rgb, _relleno(encabezado)) == (
        True,
        "00FFFFFF",
        "001F3864",
    )
    termino, definicion = hoja["A27"], hoja["B27"]
    assert (termino.font.b, termino.alignment.vertical, termino.alignment.wrap_text) == (
        True,
        "top",
        None,
    )
    assert (definicion.alignment.vertical, definicion.alignment.wrap_text) == ("top", True)
    assert hoja["A38"].alignment.wrap_text is True  # solo los términos de EXPORTADORES envuelven


def test_leeme_textos_declarados_ya_no_nombran_empresas_reales(libro):
    """Los tres ejemplos de la guía de revisión cambiaron de marca; lo demás del texto sigue igual."""
    hoja = libro["LEEME"]
    assert hoja["B50"].value == (
        "La evidencia: 'misma web vantiko.es y misma ciudad', 'es la matriz según su web', "
        "'homónimo en Málaga'."
    )
    assert hoja["B51"].value == (
        "TIPO=VINCULO · FUENTE=ORBIS · CLAVE_A=España|VANTIKO CHOCOLATES, S.A., · CLAVE_B=ESB0000001 · "
        "DECISION=EMPRESA · AUTOR=M. Pérez · FECHA=2026-10-02 · RAZON=Mismo nombre y web vantiko.es"
    )
    todo = " ".join(str(c.value) for f in hoja.iter_rows() for c in f if c.value)
    assert "gullon" not in todo.lower() and "ESA34002501" not in todo


def test_leeme_sin_destinos_lo_dice(tmp_path):
    """Sin destinos el subtítulo queda con la lista vacía y EXPORTADORES habla de «(sin destinos)»."""
    hoja = _abrir(_armar(tmp_path, destinos=()), tmp_path)["LEEME"]
    assert hoja["A2"].value.startswith("Vinculación de EXPORTADORES con  · motor ")
    assert hoja["B12"].value.endswith("y el mejor registro de (sin destinos) con sus datos.")
    assert _columna_a(hoja, 38, 41)[-1] == "CONTACTO_*"


# ── RESUMEN ──────────────────────────────────────────────────────────────────
FORMULAS_DEL_RESUMEN = {
    "C4": "=COUNTA(EXPORTADORES!$A$2:$A$7)",
    "C5": '=COUNTIF(EXPORTADORES!$C$2:$C$7,"VINCULADO")',
    "C6": '=COUNTIFS(EXPORTADORES!$C$2:$C$7,"VINCULADO",EXPORTADORES!$D$2:$D$7,"EMPRESA")',
    "C7": '=COUNTIFS(EXPORTADORES!$C$2:$C$7,"VINCULADO",EXPORTADORES!$D$2:$D$7,"GRUPO")',
    "C8": '=COUNTIF(EXPORTADORES!$G$2:$G$7,"*ORBIS*")',
    "C9": '=COUNTIF(EXPORTADORES!$G$2:$G$7,"*FDI MARKETS*")',
    "C11": (
        '=IFERROR(SUMIF(EXPORTADORES!$C$2:$C$7,"VINCULADO",EXPORTADORES!$H$2:$H$7)'
        "/SUM(EXPORTADORES!$H$2:$H$7),0)"
    ),
    "C13": '=COUNTIF(EXPORTADORES!$F$2:$F$7,"SÍ")',
}


def test_resumen_formulas_vivas_sobre_exportadores(libro):
    """Cada indicador con fórmula apunta a su columna de EXPORTADORES (por NOMBRE, no por posición)."""
    hoja = libro["RESUMEN"]
    formulas = {c.coordinate: c.value for f in hoja["C4:C14"] for c in f if c.data_type == "f"}
    assert formulas == FORMULAS_DEL_RESUMEN


def test_resumen_los_indicadores_sin_fórmula_conservan_su_valor(libro):
    """«vinculadas a todas las bases», una columna inexistente y un texto no tienen fórmula."""
    hoja = libro["RESUMEN"]
    assert (hoja["B10"].value, hoja["C10"].value, hoja["C10"].data_type) == (
        "  · vinculadas a todas las bases",
        1,
        "n",
    )
    assert (hoja["B12"].value, hoja["C12"].value, hoja["C12"].data_type) == (
        "INEXISTENTE de las empresas vinculadas",
        0.5,
        "n",
    )
    assert (hoja["C14"].value, hoja["C14"].data_type) == ("2 de 3", "s")


def test_resumen_formato_alineacion_y_negrita_de_los_valores(libro):
    """Porcentaje si la unidad es «%», entero si no; valores a la derecha; negrita sin sangría."""
    hoja = libro["RESUMEN"]
    formatos = {f"C{i}": hoja[f"C{i}"].number_format for i in range(4, 15)}
    assert formatos == {
        **dict.fromkeys(["C4", "C5", "C6", "C7", "C8", "C9", "C10", "C13", "C14"], "#,##0"),
        "C11": "0.0%",
        "C12": "0.0%",
    }
    assert {hoja[f"C{i}"].alignment.horizontal for i in range(4, 15)} == {"right"}
    negritas = {i for i in range(4, 15) if hoja[f"B{i}"].font.b}
    assert negritas == {
        4,
        5,
        11,
        12,
        13,
        14,
    }  # los indicadores con sangría («  · …») no van en negrita


def test_resumen_anchos_encabezado_y_cobertura_por_pais(libro):
    hoja = libro["RESUMEN"]
    assert {k: v.width for k, v in hoja.column_dimensions.items()} == {
        "A": 18.0, "B": 58.0, "C": 14.0, "D": 12.0, "E": 90.0, "F": 3.0, "G": 16.0, "H": 12.0, "I": 12.0, "J": 12.0,
    }  # fmt: skip
    assert hoja.row_dimensions[3].height == 42.0
    assert (hoja["A1"].value, hoja["A1"].font.sz) == ("Resumen del cruce", 13.0)
    assert (hoja["G2"].value, hoja["G2"].font.sz) == ("Cobertura por país", 12.0)
    assert [hoja.cell(row=3, column=j).value for j in range(7, 11)] == [
        "PAIS", "EMPRESAS", "PCT_VINCULADAS", "SCORE_MEDIO",
    ]  # fmt: skip
    assert [hoja.cell(row=4, column=j).number_format for j in range(7, 11)] == [
        "General", "General", "0.0%", "General",
    ]  # fmt: skip
    assert hoja["H4"].value == 4 and hoja["I5"].value == 0.5
    assert (hoja["A3"].value, _relleno(hoja["A3"]), hoja["A3"].alignment.vertical) == (
        "SECCION",
        "001F3864",
        "center",
    )
    assert hoja.freeze_panes is None and hoja.auto_filter.ref is None


def test_resumen_advertencias_de_calidad_bajo_los_indicadores(libro):
    hoja = libro["RESUMEN"]
    assert hoja["A17"].value == "Advertencias de los datos (detalle en CALIDAD_DATOS)"
    assert [hoja.cell(row=18, column=j).value for j in (1, 2, 3)] == [
        "EXPORTADORES",
        "Métrica en cero",
        "FLETES vale 0 en todas las filas. No sirve para comparar.",
    ]
    assert sorted(str(r) for r in hoja.merged_cells.ranges) == ["C18:E18", "C19:E19"]
    assert hoja.row_dimensions[18].height == 16.0
    assert (hoja["A18"].font.b, hoja["C18"].font.b, hoja["C18"].alignment.wrap_text) == (
        True,
        False,
        True,
    )


def test_resumen_solo_repite_las_ocho_primeras_advertencias(tmp_path):
    """Con diez hallazgos en CALIDAD_DATOS el RESUMEN trae ocho; el libro conserva los diez."""
    calidad = pd.concat([_tablas_base(("ORBIS", "FDI"))["CALIDAD_DATOS"]] * 5, ignore_index=True)
    libro = _abrir(_armar(tmp_path, calidad=calidad, tablas={"CALIDAD_DATOS": calidad}), tmp_path)
    assert sorted(str(r) for r in libro["RESUMEN"].merged_cells.ranges) == [
        f"C{i}:E{i}" for i in range(18, 26)
    ]
    assert libro["RESUMEN"]["A26"].value is None
    assert libro["CALIDAD_DATOS"].max_row == 11


# ── EXPORTADORES ─────────────────────────────────────────────────────────────
def test_exportadores_hipervinculos_del_primer_sitio_web(libro):
    """Solo ``*_WEB``: se toma lo anterior a « | », con ``https://`` si falta; sin punto o con espacios no hay enlace."""
    hoja = libro["EXPORTADORES"]
    assert _hipervinculos(hoja) == {
        "M2": "https://www.zelvora.es",
        "P2": "https://zelvora.es",
        "M3": "https://kramento.com",
        "O3": "https://orbelia.org",
        "O6": "https://quelmar.fr",
        "P6": "https://quelmar.fr",
    }
    celda = hoja["M2"]
    assert (celda.font.color.rgb, celda.font.u, celda.font.sz, celda.font.b) == (
        "000563C1",
        "single",
        10.0,
        False,
    )
    assert hoja["O4"].value == "sin web" and hoja["O4"].hyperlink is None


def test_exportadores_comentarios_de_encabezado(libro):
    """Un comentario por bloque de destino (en su ``*_NIVEL``) y otro en RESULTADO, todos del autor «motor»."""
    assert _comentarios(libro["EXPORTADORES"]) == {
        "C1": (
            "VINCULADO: al menos un vínculo EMPRESA o GRUPO (automático A/B o decidido). CANDIDATO POR REVISAR: "
            "solo candidatos C. SIN COINCIDENCIA: nada con evidencia.",
            "motor",
        ),
        "L1": (
            "Bloque ORBIS_*: registro principal de ORBIS. Si ORBIS_NIVEL = GRUPO, sus datos son de OTRA sociedad "
            "del grupo (contexto, no atribuibles al exportador).",
            "motor",
        ),
        "N1": (
            "Bloque FDI_*: registro principal de FDI MARKETS. Si FDI_NIVEL = GRUPO, sus datos son de OTRA sociedad "
            "del grupo (contexto, no atribuibles al exportador).",
            "motor",
        ),
    }


def test_exportadores_oculta_las_columnas_constantes_de_metricas_y_contexto(libro):
    """FLETES (J) y DESCRIPCION_PRODUCTO (K) valen lo mismo en todas las filas; OPERACIONES varía y se queda."""
    columnas = libro["EXPORTADORES"].column_dimensions
    assert {k: (v.width, v.hidden) for k, v in columnas.items() if v.hidden} == {
        "J": (8.0, True),
        "K": (22.0, True),
    }
    assert columnas["H"].hidden is False


def test_exportadores_con_una_sola_fila_no_oculta_nada(tmp_path):
    """Con una fila todo es «constante»: la regla exige más de una."""
    una = _tablas_base(("ORBIS", "FDI"))["EXPORTADORES"].iloc[:1].reset_index(drop=True)
    hoja = _abrir(_armar(tmp_path, tablas={"EXPORTADORES": una}), tmp_path)["EXPORTADORES"]
    assert [k for k, v in hoja.column_dimensions.items() if v.hidden] == []
    assert hoja.auto_filter.ref == "A1:P2"
    assert _semaforos(hoja)[0] == ("C2", 'LEFT($C2,9)="VINCULADO"', "00E2EFDA")


def test_exportadores_semaforos_paneles_y_filtro(libro):
    hoja = libro["EXPORTADORES"]
    assert _semaforos(hoja) == [
        ("C2:C7", 'LEFT($C2,9)="VINCULADO"', "00E2EFDA"),
        ("C2:C7", 'LEFT($C2,9)="CANDIDATO"', "00FFF2CC"),
        ("E2:E7", 'LEFT($E2,5)="MEDIA"', "00FFF2CC"),
        ("F2:F7", 'LEFT($F2,2)="SÍ"', "00FFF2CC"),
    ]
    assert (hoja.freeze_panes, hoja.auto_filter.ref, hoja.dimensions) == ("C2", "A1:P7", "A1:P7")
    assert hoja.row_dimensions[1].height == 30.0
    assert hoja["B2"].alignment.wrap_text is None  # EXPORTADORES no envuelve texto
    assert (hoja["H2"].number_format, hoja["I2"].number_format) == ("#,##0", "#,##0.00")
    assert hoja["A1"].comment is None and hoja["C1"].comment is not None


# ── ENLACES ──────────────────────────────────────────────────────────────────
def test_enlaces_formatos_de_columna_y_valores_especiales(libro):
    """SCORE a dos decimales, ANIO sin separador; bool → SÍ/NO; control → espacio; «=» inicial → texto."""
    hoja = libro["ENLACES"]
    assert [hoja.cell(row=2, column=j).number_format for j in range(1, 10)] == [
        "General", "General", "General", "General", "General", "General", "0.00", "0", "General",
    ]  # fmt: skip
    assert [hoja.cell(row=i, column=6).value for i in (2, 3, 4)] == ["SÍ", "SÍ", "NO"]
    assert hoja["I3"].value == "Marca  compartida"
    assert (hoja["I4"].value, hoja["I4"].data_type) == ("=Mismo nombre y país", "s")
    assert (hoja["G2"].value, hoja["H2"].value) == (1, 2026)
    assert _semaforos(hoja) == [
        ("E2:E4", 'LEFT($E2,5)="MEDIA"', "00FFF2CC"),
        ("E2:E4", 'LEFT($E2,4)="ALTA"', "00E2EFDA"),
    ]
    assert (hoja.freeze_panes, hoja.auto_filter.ref) == ("C2", "A1:I4")


# ── REVISION y DECISIONES ────────────────────────────────────────────────────
def test_revision_lista_desplegable_de_decisiones(libro):
    """La lista vale para DECISION (F) desde la fila 2 hasta 200 filas después de la última."""
    validaciones = libro["REVISION"].data_validations.dataValidation
    assert len(validaciones) == 1
    v = validaciones[0]
    assert (str(v.sqref), v.type, v.allow_blank, v.showErrorMessage) == (
        "F2:F204",
        "list",
        True,
        True,
    )
    assert v.formula1 == '"EMPRESA,GRUPO,NO_VINCULAR,RELACION_PROPIEDAD,MISMA_ENTIDAD,DISTINTA"'
    assert v.errorTitle == "Decisión no válida"
    assert v.error == (
        "Use EMPRESA, GRUPO, NO_VINCULAR o RELACION_PROPIEDAD (vínculos) o MISMA_ENTIDAD / DISTINTA (duplicados)."
    )
    assert _comentarios(libro["REVISION"]) == {
        "F1": (
            "Llene esta columna (lista desplegable). Luego vuelva a correr el notebook con este Excel "
            "en ARCHIVOS_DECISIONES.",
            "motor",
        )
    }


def test_revision_columnas_de_entrada_en_amarillo(libro):
    """DECISION, AUTOR, FECHA y RAZON: encabezado ocre y celdas amarillas; el resto, el estilo común."""
    hoja = libro["REVISION"]
    assert [_relleno(hoja.cell(row=1, column=j)) for j in range(1, 10)] == (
        ["001F3864"] * 5 + ["00BF8F00"] * 4
    )
    for fila in range(2, 6):
        assert [_relleno(hoja.cell(row=fila, column=j)) for j in range(1, 10)] == [None] * 5 + [
            "00FFF2CC"
        ] * 4
    assert _semaforos(hoja) == [
        ("E2:E5", 'LEFT($E2,9)="PENDIENTE"', "00FFF2CC"),
        ("E2:E5", 'LEFT($E2,8)="DECIDIDO"', "00E2EFDA"),
    ]
    assert (hoja.freeze_panes, hoja.auto_filter.ref) == ("D2", "A1:I5")


def test_revision_sin_filas_no_trae_lista_ni_columnas_amarillas(tmp_path):
    """Solo el encabezado: sin validación, sin semáforos, sin comentario; el filtro cubre una fila de más."""
    vacia = _tablas_base(("ORBIS", "FDI"))["REVISION"].iloc[0:0]
    hoja = _abrir(_armar(tmp_path, tablas={"REVISION": vacia}), tmp_path)["REVISION"]
    assert hoja.data_validations.dataValidation == []
    assert _semaforos(hoja) == [] and _comentarios(hoja) == {}
    assert {_relleno(c) for c in hoja[1]} == {"001F3864"}
    assert (hoja.dimensions, hoja.auto_filter.ref, hoja.freeze_panes) == ("A1:I1", "A1:I2", "D2")


def test_tabla_sin_columnas_deja_la_hoja_con_sin_datos(tmp_path):
    """Una tabla sin columnas (o ausente) deja ``(sin datos)`` en A1 y nada más: ni filtro, ni paneles, ni comentario."""
    sin = {
        "REVISION": pd.DataFrame(),
        "DECISIONES": pd.DataFrame(),
        "CONTACTOS": None,
        "DEDUPLICACION": None,
    }
    libro = _abrir(_armar(tmp_path, tablas=sin), tmp_path)
    for nombre in sin:
        hoja = libro[nombre]
        assert (hoja["A1"].value, hoja["A1"].font.name, hoja["A1"].font.sz) == (
            "(sin datos)",
            "Arial",
            10.0,
        )
        assert (hoja.dimensions, hoja.freeze_panes, hoja.auto_filter.ref) == ("A1:A1", None, None)
        assert _comentarios(hoja) == {} and hoja.data_validations.dataValidation == []


def test_decisiones_lleva_comentario_y_el_encabezado_sin_envolver(libro):
    hoja = libro["DECISIONES"]
    texto, autor = _comentarios(hoja)["A1"]
    assert texto.startswith("Historial de TODAS las decisiones aplicadas en esta corrida")
    assert texto.endswith("las decisiones nuevas de REVISION.") and autor == "motor"
    assert (hoja.dimensions, hoja.freeze_panes, hoja.auto_filter.ref) == ("A1:D3", "A2", "A1:D3")


# ── VALIDACION y hojas de auditoría ──────────────────────────────────────────
def test_validacion_escribe_tres_bloques_y_porcentajes_por_nombre_de_columna(libro):
    """Cada bloque: título, tabla con encabezado azul y, si la columna es PRECISION/RECALL/F1…, formato %."""
    hoja = libro["VALIDACION"]
    assert [hoja[f"A{i}"].value for i in (4, 9, 13)] == [
        "Métricas",
        "Discrepancias motor vs referencia",
        "Pares decididos en más de una revisión",
    ]
    assert all(hoja[f"A{i}"].font.sz == 11.0 for i in (4, 9, 13))
    assert [hoja.cell(row=5, column=j).value for j in range(1, 7)] == [
        "FUENTE", "AUTOMATICOS", "PRECISION", "RECALL", "F1", "RECALL_CONTANDO_CANDIDATOS_C",
    ]  # fmt: skip
    assert [hoja.cell(row=6, column=j).number_format for j in range(1, 7)] == [
        "General", "General", "0.0%", "0.0%", "0.0%", "0.0%",
    ]  # fmt: skip
    assert (
        _relleno(hoja["A5"]),
        hoja["A5"].alignment.wrap_text,
        hoja["A5"].alignment.vertical,
    ) == ("001F3864", True, None)
    assert [hoja.cell(row=7, column=j).value for j in range(1, 7)] == [
        "FDI MARKETS",
        4,
        0.75,
        0.6,
        0.667,
        0.8,
    ]
    assert hoja["A14"].value == "(sin datos: no se declaró referencia o no hay pares)"
    assert hoja["A1"].font.sz == 12.0 and hoja["A2"].font.sz == 9.0 and hoja["A2"].font.i is True
    assert {k: v.width for k, v in hoja.column_dimensions.items()} == {
        c: w for c, w in zip("ABCDEFGHIJKLMN", [16, 60, 40, 16, 40, 16, 16, 16, 16, 16, 16, 16, 16, 16], strict=True)
    }  # fmt: skip
    assert hoja.freeze_panes is None and hoja.auto_filter.ref is None


def test_validacion_sin_datos_en_los_tres_bloques(tmp_path):
    """Tabla ausente, vacía o sin columnas: el bloque queda con su título y el aviso, y cada uno ocupa tres filas."""
    vacios = {
        "VALIDACION": pd.DataFrame(),
        "VALIDACION_DETALLE": None,
        "COMPARACION_REVISIONES": pd.DataFrame(),
    }
    hoja = _abrir(_armar(tmp_path, tablas=vacios), tmp_path)["VALIDACION"]
    aviso = "(sin datos: no se declaró referencia o no hay pares)"
    assert _columna_a(hoja, 4, 12) == [
        "Métricas", aviso, None, "Discrepancias motor vs referencia", aviso, None,
        "Pares decididos en más de una revisión", aviso, None,
    ]  # fmt: skip
    assert (
        hoja["A5"].font.name == "Arial"
        and hoja["A5"].font.sz == 10.0
        and hoja["A5"].font.i is False
    )


def test_control_qa_semaforo_por_estado(libro):
    hoja = libro["CONTROL_QA"]
    assert _semaforos(hoja) == [
        ("B2:B4", 'LEFT($B2,2)="OK"', "00E2EFDA"),
        ("B2:B4", 'LEFT($B2,11)="ADVERTENCIA"', "00FFF2CC"),
        ("B2:B4", 'LEFT($B2,5)="FALLA"', "00F8CBAD"),
    ]
    assert [hoja.cell(row=i, column=3).value for i in (2, 3, 4)] == ["SÍ", "SÍ", "NO"]


def test_metodologia_trae_los_once_pasos_del_metodo(libro):
    """METODOLOGIA sale de ``textos_leeme``: once pasos, el cuarto nombra la ancla; ancho de la descripción 120."""
    hoja = libro["METODOLOGIA"]
    assert [hoja.cell(row=1, column=j).value for j in (1, 2, 3)] == ["PASO", "TEMA", "DESCRIPCION"]
    assert _columna_a(hoja, 2, 12) == [str(i) for i in range(1, 12)]
    assert hoja["B2"].value == "Ingesta" and hoja["B12"].value == "Controles"
    assert hoja["B5"].value == "Deduplicación interna"
    assert hoja["C5"].value.startswith(
        "EXPORTADORES: mismo nombre normalizado, mismas letras con otros espacios"
    )
    assert (hoja.column_dimensions["C"].width, hoja.dimensions) == (120.0, "A1:C12")
    assert libro["PARAMETROS"].column_dimensions["A"].width == 13.0


# ── Nombre de hoja ───────────────────────────────────────────────────────────
def test_nombre_de_hoja_se_recorta_a_31_caracteres(tmp_path):
    """«DETALLE_» + una clave de 31 letras da 39 caracteres: Excel admite 31."""
    res = _armar(tmp_path, destinos=("ORBIS",))
    clave = "BASE_CON_NOMBRE_MUY_LARGO_DE_30"
    assert len(clave) == 31
    largo = Fuente(clave=clave, etiqueta="BASE LARGA", archivo="x.xlsx", col_nombre="n")
    res.cfg = ConfigVinculacion(
        ancla=res.cfg.ancla, destinos=[largo], carpeta_salida=res.cfg.carpeta_salida
    )
    res.tablas = {
        ("DETALLE_" + clave if k == "DETALLE_ORBIS" else k): v for k, v in res.tablas.items()
    }
    libro = _abrir(res, tmp_path)
    assert "DETALLE_BASE_CON_NOMBRE_MUY_LAR" in libro.sheetnames
    assert {len(n) for n in libro.sheetnames} <= set(range(1, 32))
    assert len("DETALLE_BASE_CON_NOMBRE_MUY_LAR") == 31
    assert libro["LEEME"]["A18"].value == "DETALLE_" + clave  # LEEME conserva el nombre completo


# ── Rutas de salida ──────────────────────────────────────────────────────────
def test_ruta_explicita_crea_las_carpetas_y_se_devuelve_como_path(tmp_path):
    res = _armar(tmp_path)
    destino = tmp_path / "uno" / "dos" / "mi_libro.xlsx"
    devuelta = exportar_vinculacion_excel(res, str(destino))  # también acepta texto
    assert devuelta == destino and isinstance(devuelta, Path)
    assert (
        destino.is_file() and not (tmp_path / "salida").exists()
    )  # no toca la carpeta de la configuración
    assert load_workbook(destino).sheetnames[0] == "LEEME"


def test_ruta_generada_sin_version_usa_carpeta_y_prefijo(tmp_path):
    res = _armar(tmp_path, versionar=False)
    assert not (tmp_path / "salida").exists()
    ruta = exportar_vinculacion_excel(res)
    assert ruta == tmp_path / "salida" / "VINCULACION_PRUEBA.xlsx" and ruta.is_file()


def test_ruta_generada_con_version_agrega_el_sello_de_minuto(tmp_path):
    """``versionar_salida`` agrega ``_AAAA-MM-DD_HHMM``; la carpeta anidada se crea."""
    res = _armar(tmp_path, versionar=True)
    res.cfg.carpeta_salida = str(tmp_path / "a" / "b")
    antes = datetime.now().replace(second=0, microsecond=0)
    ruta = exportar_vinculacion_excel(res)
    despues = datetime.now()
    m = re.fullmatch(r"VINCULACION_PRUEBA_(\d{4}-\d{2}-\d{2}_\d{4})\.xlsx", ruta.name)
    assert m, ruta.name
    assert antes <= datetime.strptime(m[1], "%Y-%m-%d_%H%M") <= despues
    assert ruta.parent == tmp_path / "a" / "b" and ruta.is_file()


def test_exportar_no_modifica_el_resultado(tmp_path):
    """Escribir el Excel no cambia ninguna tabla del ``Resultado`` (el RESUMEN se copia antes de usarlo)."""
    res = _armar(tmp_path)
    antes = {k: v.copy() for k, v in res.tablas.items()}
    resumen, por_pais = res.resumen.copy(), res.por_pais.copy()
    exportar_vinculacion_excel(res, tmp_path / "libro.xlsx")
    assert list(res.tablas) == list(antes)
    for nombre, tabla in antes.items():
        pd.testing.assert_frame_equal(res.tablas[nombre], tabla)
    pd.testing.assert_frame_equal(res.resumen, resumen)
    pd.testing.assert_frame_equal(res.por_pais, por_pais)


def test_ruta_cuyo_padre_es_un_archivo_falla_sin_escribir(tmp_path):
    ocupado = tmp_path / "ocupado"
    ocupado.write_text("no soy una carpeta", encoding="utf-8")
    with pytest.raises(FileExistsError, match="ocupado"):
        exportar_vinculacion_excel(_armar(tmp_path), ocupado / "libro.xlsx")
    assert ocupado.read_text(encoding="utf-8") == "no soy una carpeta"


def test_una_tabla_faltante_falla_con_su_nombre_y_no_deja_archivo(tmp_path):
    """El Excel exige todas las tablas del ``Resultado``; no escribe un libro a medias."""
    res = _armar(tmp_path)
    del res.tablas["CONTROL_QA"]
    destino = tmp_path / "libro.xlsx"
    with pytest.raises(KeyError, match="CONTROL_QA"):
        exportar_vinculacion_excel(res, destino)
    assert not destino.exists()


# ── Piezas internas (no existen en el motor original como funciones de módulo) ─
def test_interno_escribir_tabla_convierte_los_valores():
    """numpy → nativos; bool → SÍ/NO; ausente → vacía; control → espacio; «=» inicial → texto; desde (2, C)."""
    tabla = pd.DataFrame(
        {
            "A": [True, False, None],
            "B": pd.array([1, None, 3], dtype="Int64"),
            "C": [np.float64(1.5), np.float64("nan"), 2.0],
            "D": ["=1+1", "a\x00b\x1fc\td\ne", "x"],
            "E": [np.int64(5), np.int64(6), np.int64(7)],
            "F": [np.bool_(True), np.bool_(False), np.bool_(True)],
        }
    )
    hoja = Workbook().active
    tablas.escribir_tabla(hoja, tabla, fila0=2, col0=3)
    escrito = {
        c.coordinate: (c.value, c.data_type)
        for f in hoja.iter_rows()
        for c in f
        if c.value is not None
    }
    assert escrito == {
        "C2": ("A", "s"), "D2": ("B", "s"), "E2": ("C", "s"), "F2": ("D", "s"), "G2": ("E", "s"), "H2": ("F", "s"),
        "C3": ("SÍ", "s"), "D3": (1, "n"), "E3": (1.5, "n"), "F3": ("=1+1", "s"), "G3": (5, "n"), "H3": ("SÍ", "s"),
        "C4": ("NO", "s"), "F4": ("a b c\td\ne", "s"), "G4": (6, "n"), "H4": ("NO", "s"),
        "D5": (3, "n"), "E5": (2.0, "n"), "F5": ("x", "s"), "G5": (7, "n"), "H5": ("SÍ", "s"),
    }  # fmt: skip
    assert type(hoja["G3"].value) is int and type(hoja["E3"].value) is float
    assert hoja["A1"].value is None  # nada fuera de la tabla


def test_interno_formato_de_numero_segun_el_nombre_de_la_columna():
    """Porcentaje, dos decimales, entero sin separador, miles o miles con decimales; texto y bool no llevan."""
    n = 6
    tabla = pd.DataFrame(
        {
            "PCT_X": np.linspace(0, 1, n), "PRECISION": np.linspace(0, 1, n), "RECALL": np.linspace(0, 1, n),
            "F1": np.linspace(0, 1, n), "RECALL_MAS": np.linspace(0, 1, n), "SCORE_A": np.linspace(0, 1, n),
            "SIM_B": np.linspace(0, 1, n), "JARO_C": np.linspace(0, 1, n), "ANIO": np.arange(2000, 2000 + n),
            "FILA_EXCEL": np.arange(n), "X_ID": np.arange(n), "ENTERO_FLOAT": np.arange(n, dtype=float),
            "DECIMAL": np.linspace(0, 1, n), "BOOL": [True, False] * (n // 2), "TEXTO": list("abcdef"),
            "ENTERO_NULO": pd.array([*range(n - 1), None], dtype="Int64"), "pct_minuscula": np.linspace(0, 1, n),
            "ENTERO": np.arange(n),
        }
    )  # fmt: skip
    hoja = Workbook().active
    tablas.escribir_tabla(hoja, tabla)
    tablas.formatear_tabla(hoja, tabla, estilos.Estilos())
    assert {c.value: hoja.cell(row=2, column=c.column).number_format for c in hoja[1]} == {
        "PCT_X": "0.0%", "PRECISION": "0.0%", "RECALL": "0.0%", "F1": "0.0%", "RECALL_MAS": "0.0%",
        "SCORE_A": "0.00", "SIM_B": "0.00", "JARO_C": "0.00", "ANIO": "0", "FILA_EXCEL": "0", "X_ID": "0",
        "ENTERO_FLOAT": "#,##0", "DECIMAL": "#,##0.00", "BOOL": "General", "TEXTO": "General",
        "ENTERO_NULO": "#,##0", "pct_minuscula": "0.0%", "ENTERO": "#,##0",
    }  # fmt: skip
    assert (hoja.freeze_panes, hoja.auto_filter.ref) == ("A2", "A1:R7")


def test_interno_ancho_tope_y_ajuste_de_texto():
    """Ancho = máx(8, largo + 2) con tope; solo envuelve la columna que llega al tope y si se pide envolver."""
    tabla = pd.DataFrame(
        {
            "CORTA": list("abc"),
            "LARGA": ["x" * 80, "y" * 90, "z" * 70],
            "MEDIA": ["m" * 30] * 3,
            "ENCABEZADO_MUY_LARGO_QUE_PASA_DEL_TOPE_DE_ANCHO_PERMITIDO_POR_LA_HOJA": [1, 2, 3],
        }
    )

    def medir(**opciones):
        hoja = Workbook().active
        tablas.escribir_tabla(hoja, tabla)
        tablas.formatear_tabla(hoja, tabla, estilos.Estilos(), **opciones)
        return [
            (
                hoja.column_dimensions[c.column_letter].width,
                bool(hoja.cell(row=2, column=c.column).alignment.wrap_text),
            )
            for c in hoja[1]
        ]

    assert medir(anchos_max=55, envolver=True) == [(8, False), (55, True), (32, False), (55, True)]
    assert medir(anchos_max=55, envolver=False) == [
        (8, False),
        (55, False),
        (32, False),
        (55, False),
    ]
    assert medir(anchos_max=120, envolver=True) == [
        (8, False),
        (90, False),
        (32, False),
        (71, False),
    ]


def test_interno_colorear_columna_ignora_columna_ausente_y_tabla_vacia():
    tabla = pd.DataFrame({"ESTADO": ["OK", "FALLA"]})
    relleno = estilos.Estilos().relleno_ok
    hoja = Workbook().active
    tablas.colorear_columna(hoja, tabla, "NO_EXISTE", [("OK", relleno)])
    tablas.colorear_columna(hoja, tabla.iloc[0:0], "ESTADO", [("OK", relleno)])
    assert _semaforos(hoja) == []
    tablas.colorear_columna(hoja, tabla, "ESTADO", [("OK", relleno)], fila0=5)
    assert _semaforos(hoja) == [("A6:A7", 'LEFT($A6,2)="OK"', "00E2EFDA")]


def test_interno_estilos_por_defecto_y_alturas():
    e = estilos.Estilos()
    assert [(f.name, f.sz, f.b) for f in (e.base, e.negrita, e.encabezado)] == [
        ("Arial", 10.0, False),
        ("Arial", 10.0, True),
        ("Arial", 10.0, True),
    ]
    assert e.encabezado.color.rgb == "00FFFFFF" and e.titulo(12).color.rgb == "001F3864"
    assert (e.cursiva(9).i, e.cursiva(9).sz, e.titulo(14).sz) == (True, 9.0, 14.0)
    assert [e.relleno_ok.fgColor.rgb, e.relleno_advertencia.fgColor.rgb, e.relleno_falla.fgColor.rgb] == [
        "00E2EFDA", "00FFF2CC", "00F8CBAD",
    ]  # fmt: skip
    assert (
        e.relleno_entrada == e.relleno_advertencia
        and e.relleno_encabezado_entrada.fgColor.rgb == "00BF8F00"
    )
    assert [estilos.altura_de_fila(n) for n in (0, 1, 2)] == [16, 30, 44]
    assert estilos.COLORES_PESTANA["revision"] == "C65911" and estilos.AUTOR_COMENTARIOS == "motor"


def test_interno_prefijo_de_bloque_es_la_primera_palabra_en_mayusculas():
    assert (
        leeme.prefijo_de_bloque(
            Fuente(clave="FDI", etiqueta="fdi markets", archivo="x", col_nombre="n")
        )
        == "FDI"
    )
    assert leeme.prefijo_de_bloque(Fuente(clave="ORBIS", archivo="x", col_nombre="n")) == "ORBIS"


def test_interno_textos_leeme_cuenta_hojas_glosario_y_metodo(tmp_path):
    cfg = _armar(tmp_path).cfg
    texto_hojas, glosario, metodo = leeme.textos_leeme(cfg)
    assert (len(texto_hojas), len(glosario), len(metodo)) == (14, 9, 11)
    assert [h[0] for h in texto_hojas][:2] == ["RESUMEN", "EXPORTADORES"]
    assert {"DETALLE_EXPORTADORES", "DETALLE_ORBIS", "DETALLE_FDI"} <= {h[0] for h in texto_hojas}
    assert [m[0] for m in metodo] == [str(i) for i in range(1, 12)]
    # el paso 4 nombra a la ancla; sin destinos la hoja EXPORTADORES lo dice
    assert metodo[3][2].startswith("EXPORTADORES: mismo nombre normalizado")
    assert "(sin destinos)" in leeme.textos_leeme(_armar(tmp_path, destinos=()).cfg)[0][1][1]


def test_interno_escribir_hojas_llena_un_libro_vacio_en_el_orden_de_entrega(tmp_path):
    """``escribir_hojas`` es el único sitio que fija el orden; no guarda nada ni toca el zoom."""
    libro = Workbook()
    libro.remove(libro.active)
    hojas.escribir_hojas(libro, estilos.Estilos(), _armar(tmp_path))
    assert libro.sheetnames == HOJAS_DE_ENTREGA[("ORBIS", "FDI")]
    assert libro["LEEME"].sheet_view.zoomScale is None
    assert not (tmp_path / "salida").exists()


def test_interno_una_tabla_que_no_cabe_en_la_hoja_falla_y_no_se_recorta():
    """Excel admite 1.048.576 filas: la fila siguiente falla con el aviso de openpyxl, sin recortar en silencio."""
    hoja = Workbook().active
    tabla = pd.DataFrame({"X": [1, 2]})
    with pytest.raises(ValueError, match="Row numbers must be between 1 and 1048576"):
        tablas.escribir_tabla(hoja, tabla, fila0=1_048_576)
