"""Decisiones de revisión del motor de vinculación (``flujo.vinculacion.decisiones``).

Cada valor esperado salió de correr la función ORIGINAL del motor (la celda ``%%writefile`` del notebook 10)
sobre el mismo archivo. Lo único que cambia a propósito: el aviso de «archivo no encontrado» va al registro
(``logging``) en lugar de ``print``, y las dos filas de ejemplo de la plantilla son de empresas inventadas.
Los archivos se escriben en ``tmp_path``; las empresas son inventadas.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.flujo.vinculacion.decisiones import (
    COLUMNAS_DECISION,
    DECISIONES_DUPLICADO,
    DECISIONES_VINCULO,
    SINONIMOS_DECISION,
    clave_ancla,
    clave_texto,
    leer_decisiones,
    plantilla_decisiones,
)

LOGGER = "record_linkage.flujo.vinculacion.decisiones"
COLUMNAS_LEIDAS = [*COLUMNAS_DECISION, "ARCHIVO", "ORDEN"]
#: Una decisión de vínculo ancla ↔ destino, en el orden de ``COLUMNAS_DECISION``.
FILA = [
    "VINCULO",
    "ORBIS",
    "España|Zelvora Alimentos SA",
    "Zelvora Alimentos",
    "ESB0000001",
    "ZELVORA ALIMENTOS SA",
    "EMPRESA",
    "Revisora Uno",
    "2026-10-01",
    "mismo nombre",
]


def _fila(**cambios: str) -> list[str]:
    """``FILA`` con algunas columnas cambiadas por nombre."""
    fila = list(FILA)
    for columna, valor in cambios.items():
        fila[COLUMNAS_DECISION.index(columna)] = valor
    return fila


def _csv(ruta: Path, filas: list[list[str]], sep: str = ",", encoding: str = "utf-8-sig") -> Path:
    """Escribe un CSV de decisiones con ``COLUMNAS_DECISION`` como encabezado."""
    tabla = pd.DataFrame(filas, columns=COLUMNAS_DECISION)
    tabla.to_csv(ruta, index=False, sep=sep, encoding=encoding)
    return ruta


def _registro(archivo: str, orden: int = 0, **cambios: str) -> dict:
    """La fila de ``FILA`` tal como la deja ``leer_decisiones``."""
    fila = dict(zip(COLUMNAS_DECISION, _fila(**cambios), strict=True))
    return {**fila, "ARCHIVO": archivo, "ORDEN": orden}


# ── Formatos de archivo ───────────────────────────────────────────────────────


def test_csv_separado_por_comas(tmp_path):
    """CSV con ``,``: una fila con sus diez columnas más ARCHIVO (nombre) y ORDEN (posición)."""
    ruta = _csv(tmp_path / "decisiones.csv", [FILA])
    r = leer_decisiones([ruta])
    assert list(r.columns) == COLUMNAS_LEIDAS
    assert r.to_dict("records") == [_registro("decisiones.csv")]


def test_csv_separado_por_punto_y_coma(tmp_path):
    """Excel en español guarda con ``;``: se detecta por la primera línea y da lo mismo."""
    ruta = _csv(tmp_path / "decisiones.csv", [FILA], sep=";")
    assert leer_decisiones([ruta]).to_dict("records") == [_registro("decisiones.csv")]


def test_csv_en_cp1252_cae_a_la_segunda_codificacion(tmp_path):
    """Un CSV que no es UTF-8 (Excel en Windows) se relee como cp1252 y conserva las tildes."""
    ruta = _csv(
        tmp_path / "windows.csv", [_fila(AUTOR="Revisión Ñandú")], sep=";", encoding="cp1252"
    )
    with pytest.raises(UnicodeDecodeError):
        ruta.read_bytes().decode("utf-8")
    r = leer_decisiones([ruta])
    assert r["AUTOR"].tolist() == ["Revisión Ñandú"]
    assert r["CLAVE_A"].tolist() == ["España|Zelvora Alimentos SA"]


def test_csv_utf8_con_y_sin_marca_de_orden_de_bytes(tmp_path):
    """El BOM de ``utf-8-sig`` no se cuela en el nombre de la primera columna."""
    con_bom = _csv(tmp_path / "con_bom.csv", [FILA], encoding="utf-8-sig")
    sin_bom = _csv(tmp_path / "sin_bom.csv", [FILA], encoding="utf-8")
    assert con_bom.read_bytes().startswith(b"\xef\xbb\xbf")
    assert not sin_bom.read_bytes().startswith(b"\xef\xbb\xbf")
    for ruta in (con_bom, sin_bom):
        r = leer_decisiones([ruta])
        assert list(r.columns) == COLUMNAS_LEIDAS
        assert r["TIPO"].tolist() == ["VINCULO"]


def _libro(ruta: Path, hojas: dict[str, pd.DataFrame]) -> Path:
    """Escribe un Excel con las hojas dadas (nombre → tabla)."""
    with pd.ExcelWriter(ruta) as w:
        for nombre, tabla in hojas.items():
            tabla.to_excel(w, sheet_name=nombre, index=False)
    return ruta


def _tabla(*filas: list[str]) -> pd.DataFrame:
    return pd.DataFrame(list(filas), columns=COLUMNAS_DECISION)


def test_xlsx_con_las_hojas_decisiones_y_revision_la_ultima_gana(tmp_path):
    """DECISIONES (historial) + REVISION (lo llenado después): una decisión repetida queda la de REVISION."""
    decisiones = _tabla(
        _fila(),
        _fila(CLAVE_B="ESB0000002", DECISION="GRUPO", AUTOR="Revisora Uno"),
    )
    revision = _tabla(
        _fila(AUTOR="Revisor Dos", RAZON="reconfirma"),
        _fila(CLAVE_B="ESB0000002", DECISION="NO_VINCULAR", AUTOR="Revisor Dos"),
        _fila(CLAVE_B="ESB0000003", DECISION="", AUTOR="", RAZON="sin decidir"),
    )
    ruta = _libro(tmp_path / "resultados.xlsx", {"DECISIONES": decisiones, "REVISION": revision})
    r = leer_decisiones([ruta])
    # La fila repetida (EMPRESA sobre ESB0000001) conserva la de REVISION y queda DESPUÉS de la de GRUPO;
    # la fila sin DECISION no entra.
    assert r[["CLAVE_B", "DECISION", "AUTOR", "ARCHIVO", "ORDEN"]].values.tolist() == [
        ["ESB0000002", "GRUPO", "Revisora Uno", "resultados.xlsx", 0],
        ["ESB0000001", "EMPRESA", "Revisor Dos", "resultados.xlsx", 0],
        ["ESB0000002", "NO_VINCULAR", "Revisor Dos", "resultados.xlsx", 0],
    ]


@pytest.mark.parametrize("hoja", ["DECISIONES", "REVISION"])
def test_xlsx_con_una_sola_de_las_dos_hojas(tmp_path, hoja):
    """Basta con una de las dos hojas; las demás hojas del libro se ignoran."""
    ruta = _libro(
        tmp_path / "resultados.xlsx",
        {hoja: _tabla(_fila()), "RESUMEN": pd.DataFrame({"INDICADOR": ["x"], "VALOR": [1]})},
    )
    assert leer_decisiones([ruta]).to_dict("records") == [_registro("resultados.xlsx")]


def test_xlsx_sin_hoja_decisiones_ni_revision_falla_con_el_nombre_del_archivo(tmp_path):
    """Un Excel sin ninguna de las dos hojas no se puede leer: el error nombra el archivo."""
    ruta = _libro(tmp_path / "otro.xlsx", {"RESUMEN": pd.DataFrame({"a": [1]})})
    with pytest.raises(ValueError, match=r"otro\.xlsx: no tiene hoja DECISIONES ni REVISION"):
        leer_decisiones([ruta])


def test_xlsx_con_hoja_sin_columna_decision_no_aporta_filas(tmp_path):
    """Una hoja REVISION sin la columna DECISION se omite: el resultado sale vacío, con sus columnas."""
    ruta = _libro(
        tmp_path / "incompleto.xlsx",
        {"REVISION": pd.DataFrame({"TIPO": ["VINCULO"], "FUENTE": ["ORBIS"]})},
    )
    r = leer_decisiones([ruta])
    assert list(r.columns) == COLUMNAS_LEIDAS
    assert len(r) == 0


def test_xlsx_con_extension_en_mayusculas_o_xlsm(tmp_path):
    """La extensión se compara sin distinguir mayúsculas y ``.xlsm`` también es un libro de Excel."""
    base = _libro(tmp_path / "base.xlsx", {"REVISION": _tabla(_fila())})
    for nombre in ("MAYUS.XLSX", "macros.xlsm"):
        copia = tmp_path / nombre
        copia.write_bytes(base.read_bytes())
        assert leer_decisiones([copia]).to_dict("records") == [_registro(nombre)]


def test_acepta_rutas_como_texto_y_como_path(tmp_path):
    """``rutas`` puede mezclar ``str`` y ``Path``."""
    uno = _csv(tmp_path / "uno.csv", [FILA])
    dos = _csv(tmp_path / "dos.csv", [_fila(CLAVE_B="ESB0000002")])
    r = leer_decisiones([str(uno), dos])
    assert r[["CLAVE_B", "ARCHIVO", "ORDEN"]].values.tolist() == [
        ["ESB0000001", "uno.csv", 0],
        ["ESB0000002", "dos.csv", 1],
    ]


# ── Normalización de columnas y valores ───────────────────────────────────────

#: (TIPO escrito, DECISION escrita) → (TIPO, DECISION) que deja ``leer_decisiones``.
SINONIMOS = [
    pytest.param("CRUCE", "misma empresa", "VINCULO", "EMPRESA", id="cruce_misma_empresa"),
    pytest.param("vínculo", "mismo_grupo", "VINCULO", "GRUPO", id="vinculo_con_tilde_mismo_grupo"),
    pytest.param("Vinculo", "no vincular", "VINCULO", "NO_VINCULAR", id="espacio_por_guion_bajo"),
    pytest.param("VINCULO", "si", "VINCULO", "EMPRESA", id="si"),
    pytest.param("VINCULO", "no", "VINCULO", "NO_VINCULAR", id="no"),
    pytest.param("VINCULO", "No_Es", "VINCULO", "NO_VINCULAR", id="no_es"),
    pytest.param("vinculo", "rechazar", "VINCULO", "NO_VINCULAR", id="rechazar"),
    pytest.param("VINCULO", "distinta empresa", "VINCULO", "NO_VINCULAR", id="distinta_empresa"),
    pytest.param("VINCULO", "propiedad", "VINCULO", "RELACION_PROPIEDAD", id="propiedad"),
    pytest.param("VINCULO", "relacion propiedad", "VINCULO", "RELACION_PROPIEDAD", id="relacion_propiedad"),
    pytest.param("VINCULO", " grupo ", "VINCULO", "GRUPO", id="espacios_alrededor"),
    pytest.param("DUPLICADO", "misma", "DUPLICADO", "MISMA_ENTIDAD", id="duplicado_misma"),
    pytest.param("DUPLICADO", "duplicado", "DUPLICADO", "MISMA_ENTIDAD", id="duplicado_duplicado"),
    pytest.param("DUPLICADO", "distinta", "DUPLICADO", "DISTINTA", id="duplicado_distinta"),
]  # fmt: skip


@pytest.mark.parametrize(("tipo", "decision", "tipo_ok", "decision_ok"), SINONIMOS)
def test_tipo_y_decision_se_llevan_a_su_forma_canonica(
    tmp_path, tipo, decision, tipo_ok, decision_ok
):
    """TIPO en mayúsculas (CRUCE y VÍNCULO son VINCULO); DECISION en mayúsculas, con «_» y sinónimos."""
    ruta = _csv(tmp_path / "d.csv", [_fila(TIPO=tipo, DECISION=decision)])
    r = leer_decisiones([ruta])
    assert (r["TIPO"].tolist(), r["DECISION"].tolist()) == ([tipo_ok], [decision_ok])


def test_la_fuente_se_lleva_a_mayusculas(tmp_path):
    """FUENTE se compara con la clave de la fuente (``ORBIS``): se pasa a mayúsculas."""
    ruta = _csv(tmp_path / "d.csv", [_fila(FUENTE="orbis")])
    assert leer_decisiones([ruta])["FUENTE"].tolist() == ["ORBIS"]


def test_decision_humana_se_lee_como_decision(tmp_path):
    """Los archivos de revisión usan ``DECISION_HUMANA``: se renombra a ``DECISION``."""
    tabla = pd.DataFrame([FILA], columns=COLUMNAS_DECISION).rename(
        columns={"DECISION": "DECISION_HUMANA"}
    )
    ruta = tmp_path / "revision.csv"
    tabla.to_csv(ruta, index=False)
    assert leer_decisiones([ruta]).to_dict("records") == [_registro("revision.csv")]


def test_si_vienen_decision_y_decision_humana_gana_decision(tmp_path):
    """Con las dos columnas se usa ``DECISION`` y ``DECISION_HUMANA`` se descarta."""
    tabla = pd.DataFrame([[*FILA, "GRUPO"]], columns=[*COLUMNAS_DECISION, "DECISION_HUMANA"])
    ruta = tmp_path / "ambas.csv"
    tabla.to_csv(ruta, index=False)
    r = leer_decisiones([ruta])
    assert r["DECISION"].tolist() == ["EMPRESA"]
    assert "DECISION_HUMANA" not in r.columns


def test_encabezados_con_espacios_y_minusculas_y_columnas_que_faltan(tmp_path):
    """Los encabezados se normalizan; lo opcional que falta queda vacío y lo que sobra se descarta."""
    ruta = tmp_path / "minimo.csv"
    ruta.write_text(
        " tipo,Fuente ,clave_a,clave_b,decision,extra\n"
        "vinculo,orbis,España|Zelvora   Alimentos SA ,ESB1,empresa,otra\n",
        encoding="utf-8",
    )
    r = leer_decisiones([ruta])
    assert list(r.columns) == COLUMNAS_LEIDAS
    assert r.to_dict("records") == [
        {
            "TIPO": "VINCULO",
            "FUENTE": "ORBIS",
            "CLAVE_A": "España|Zelvora Alimentos SA",
            "NOMBRE_A": "",
            "CLAVE_B": "ESB1",
            "NOMBRE_B": "",
            "DECISION": "EMPRESA",
            "AUTOR": "",
            "FECHA": "",
            "RAZON": "",
            "ARCHIVO": "minimo.csv",
            "ORDEN": 0,
        }
    ]


def test_las_claves_pierden_los_espacios_de_mas_pero_los_nombres_solo_los_extremos(tmp_path):
    """CLAVE_A y CLAVE_B colapsan espacios (sobreviven a Excel); NOMBRE_A/B solo se recortan."""
    ruta = _csv(
        tmp_path / "d.csv",
        [_fila(CLAVE_A="  España|Zelvora   Alimentos  SA ", CLAVE_B=" ESB   1 ", NOMBRE_A="  Zelvora   Alimentos ")],
    )  # fmt: skip
    r = leer_decisiones([ruta])
    assert r["CLAVE_A"].tolist() == ["España|Zelvora Alimentos SA"]
    assert r["CLAVE_B"].tolist() == ["ESB 1"]
    assert r["NOMBRE_A"].tolist() == ["Zelvora   Alimentos"]


def test_las_filas_sin_decision_se_descartan(tmp_path):
    """Una fila sin DECISION (vacía o solo espacios) es una fila de revisión sin llenar: no es decisión."""
    ruta = _csv(
        tmp_path / "d.csv",
        [_fila(), _fila(CLAVE_B="ESB0000002", DECISION=""), _fila(CLAVE_B="ESB0000003", DECISION="  ")],
    )  # fmt: skip
    assert leer_decisiones([ruta])["CLAVE_B"].tolist() == ["ESB0000001"]


# ── Orden entre archivos ──────────────────────────────────────────────────────


def test_el_orden_de_los_archivos_se_guarda_en_la_columna_orden(tmp_path):
    """Dos archivos: las filas del segundo llevan ORDEN 1 y van después; así «el último manda»."""
    primero = _csv(tmp_path / "primero.csv", [_fila(DECISION="EMPRESA")])
    segundo = _csv(tmp_path / "segundo.csv", [_fila(DECISION="GRUPO")])
    r = leer_decisiones([primero, segundo])
    assert r[["DECISION", "ARCHIVO", "ORDEN"]].values.tolist() == [
        ["EMPRESA", "primero.csv", 0],
        ["GRUPO", "segundo.csv", 1],
    ]
    assert r.index.tolist() == [
        0,
        1,
    ]  # las filas de varios archivos se numeran de nuevo, sin repetir
    # Invertir los archivos invierte el orden: la posición en ``rutas`` es lo que cuenta, no el nombre.
    assert leer_decisiones([segundo, primero])["ORDEN"].tolist() == [0, 1]
    assert leer_decisiones([segundo, primero])["ARCHIVO"].tolist() == ["segundo.csv", "primero.csv"]


def test_un_xlsx_y_un_csv_se_numeran_juntos(tmp_path):
    """El orden es de archivos, sin importar el formato; las dos hojas de un libro comparten ORDEN."""
    libro = _libro(
        tmp_path / "resultados.xlsx",
        {"DECISIONES": _tabla(_fila(CLAVE_B="ESB0000010")), "REVISION": _tabla(_fila(CLAVE_B="ESB0000011"))},
    )  # fmt: skip
    csv = _csv(tmp_path / "despues.csv", [_fila(CLAVE_B="ESB0000012")])
    r = leer_decisiones([libro, csv])
    assert r[["CLAVE_B", "ARCHIVO", "ORDEN"]].values.tolist() == [
        ["ESB0000010", "resultados.xlsx", 0],
        ["ESB0000011", "resultados.xlsx", 0],
        ["ESB0000012", "despues.csv", 1],
    ]


# ── Archivos que no hay ───────────────────────────────────────────────────────


def test_archivo_inexistente_se_omite_con_una_advertencia_en_el_registro(tmp_path, caplog, capsys):
    """Un archivo que no existe no detiene la corrida: se avisa por ``logging`` (no por ``print``)."""
    ruta = tmp_path / "no_existe.csv"
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        r = leer_decisiones([ruta])
    avisos = [x for x in caplog.records if x.name == LOGGER and x.levelno == logging.WARNING]
    assert len(avisos) == 1
    assert avisos[0].getMessage() == f"archivo de decisiones no encontrado, se omite: {ruta}"
    assert capsys.readouterr().out == ""
    assert list(r.columns) == COLUMNAS_LEIDAS
    assert len(r) == 0


def test_el_orden_cuenta_tambien_los_archivos_que_no_existen(tmp_path):
    """``ORDEN`` es la posición en ``rutas``: el archivo que falta deja un hueco en la numeración."""
    uno = _csv(tmp_path / "uno.csv", [FILA])
    tres = _csv(tmp_path / "tres.csv", [FILA])
    r = leer_decisiones([uno, tmp_path / "falta.csv", tres])
    assert r[["ARCHIVO", "ORDEN"]].values.tolist() == [["uno.csv", 0], ["tres.csv", 2]]


@pytest.mark.parametrize("rutas", [None, [], ()])
def test_sin_archivos_devuelve_una_tabla_vacia_con_sus_columnas(rutas):
    """Sin archivos de decisiones (lo normal en la primera corrida) la tabla sale vacía pero con columnas."""
    r = leer_decisiones(rutas)
    assert list(r.columns) == COLUMNAS_LEIDAS
    assert len(r) == 0


def test_un_csv_solo_con_encabezado_devuelve_una_tabla_vacia(tmp_path):
    """Una plantilla sin llenar no aporta decisiones."""
    ruta = _csv(tmp_path / "vacio.csv", [])
    r = leer_decisiones([ruta])
    assert list(r.columns) == COLUMNAS_LEIDAS
    assert len(r) == 0


# ── Errores con mensaje accionable ────────────────────────────────────────────


def test_tipo_invalido_falla_y_muestra_las_filas_malas(tmp_path):
    """Un TIPO fuera de {VINCULO, DUPLICADO}: el error dice cuántas filas, de qué archivo y las muestra."""
    ruta = _csv(tmp_path / "malo.csv", [_fila(), _fila(TIPO="OTRO", CLAVE_B="ESB0000009")])
    with pytest.raises(
        ValueError, match=r"1 filas de malo\.csv con TIPO o DECISION no válidos"
    ) as e:
        leer_decisiones([ruta])
    mensaje = str(e.value)
    assert "Qué hacer: TIPO ∈ {VINCULO, DUPLICADO}" in mensaje
    assert "['EMPRESA', 'GRUPO', 'NO_VINCULAR', 'RELACION_PROPIEDAD']" in mensaje
    assert "['DISTINTA', 'MISMA_ENTIDAD']" in mensaje
    assert "OTRO" in mensaje and "ESB0000009" in mensaje
    assert "ESB0000001" not in mensaje  # la fila buena no aparece en la tabla de filas malas


@pytest.mark.parametrize(
    ("tipo", "decision"),
    [
        pytest.param("VINCULO", "MISMA_ENTIDAD", id="decision_de_duplicado_en_un_vinculo"),
        pytest.param("DUPLICADO", "EMPRESA", id="decision_de_vinculo_en_un_duplicado"),
        pytest.param("VINCULO", "TAL_VEZ", id="decision_desconocida"),
    ],
)
def test_decision_que_no_corresponde_al_tipo_falla(tmp_path, tipo, decision):
    """Cada TIPO admite su propio vocabulario de DECISION; mezclarlos es un error."""
    ruta = _csv(tmp_path / "malo.csv", [_fila(TIPO=tipo, DECISION=decision)])
    with pytest.raises(ValueError, match=r"1 filas de malo\.csv con TIPO o DECISION no válidos"):
        leer_decisiones([ruta])


def test_el_error_cuenta_todas_las_filas_malas(tmp_path):
    """Con dos filas malas el mensaje dice 2; el nombre del archivo es el del archivo que falla."""
    buena = _csv(tmp_path / "bueno.csv", [FILA])
    mala = _csv(tmp_path / "malo.csv", [_fila(TIPO="X"), _fila(TIPO="Y")])
    with pytest.raises(ValueError, match=r"2 filas de malo\.csv"):
        leer_decisiones([buena, mala])


def test_faltan_columnas_obligatorias_falla_con_la_lista_y_el_remedio(tmp_path):
    """Sin TIPO, FUENTE, CLAVE_A, CLAVE_B o DECISION no hay decisión: el error lista las que faltan."""
    ruta = tmp_path / "corto.csv"
    ruta.write_text("tipo,fuente\nVINCULO,ORBIS\n", encoding="utf-8")
    with pytest.raises(KeyError, match="no trae las columnas") as e:
        leer_decisiones([ruta])
    mensaje = e.value.args[0]
    assert mensaje.startswith(
        "Qué pasó: corto.csv no trae las columnas ['CLAVE_A', 'CLAVE_B', 'DECISION']."
    )
    assert f"use la plantilla (columnas {COLUMNAS_DECISION})" in mensaje
    assert "hoja REVISION del Excel de resultados" in mensaje


# ── Plantilla ─────────────────────────────────────────────────────────────────


def test_plantilla_escribe_las_columnas_esperadas_y_dos_filas_de_ejemplo(tmp_path):
    """Un CSV UTF-8 con BOM, el encabezado de ``COLUMNAS_DECISION`` y un ejemplo de cada TIPO."""
    ruta = plantilla_decisiones(tmp_path / "plantilla.csv")
    assert ruta == tmp_path / "plantilla.csv"
    assert isinstance(ruta, Path)
    assert ruta.read_bytes().startswith(b"\xef\xbb\xbf")
    tabla = pd.read_csv(ruta, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    assert list(tabla.columns) == COLUMNAS_DECISION
    assert tabla["TIPO"].tolist() == ["VINCULO", "DUPLICADO"]
    assert tabla["FUENTE"].tolist() == ["ORBIS", "EXPORTADORES"]
    assert tabla["DECISION"].tolist() == ["EMPRESA", "MISMA_ENTIDAD"]
    assert tabla["AUTOR"].tolist() == ["iniciales", "iniciales"]
    assert tabla["FECHA"].tolist() == ["2026-09-29", "2026-09-29"]


def test_plantilla_trae_estas_dos_filas_de_ejemplo(tmp_path):
    """Las dos filas de ejemplo (empresas inventadas): un vínculo con Orbis y una abreviatura de aduana."""
    ruta = plantilla_decisiones(tmp_path / "plantilla.csv")
    tabla = pd.read_csv(ruta, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    assert tabla.values.tolist() == [
        [
            "VINCULO",
            "ORBIS",
            "España|GALLETAS ZELVORA, S.A.,",
            "GALLETAS ZELVORA, S.A.,",
            "ESB9990001",
            "GALLETAS ZELVORA, SA",
            "EMPRESA",
            "iniciales",
            "2026-09-29",
            "Mismo nombre y web zelvora.es",
        ],
        [
            "DUPLICADO",
            "EXPORTADORES",
            "España|PLVK SLU",
            "PLVK SLU",
            "España|PELUVIKA SLU",
            "PELUVIKA SLU",
            "MISMA_ENTIDAD",
            "iniciales",
            "2026-09-29",
            "Abreviatura de aduana de PELUVIKA",
        ],
    ]


def test_plantilla_acepta_texto_como_ruta(tmp_path):
    """``ruta`` puede ser ``str``; se devuelve siempre un ``Path``."""
    ruta = plantilla_decisiones(str(tmp_path / "plantilla.csv"))
    assert ruta == tmp_path / "plantilla.csv"
    assert ruta.exists()


def test_plantilla_solo_trae_empresas_inventadas(tmp_path):
    """Las filas de ejemplo usan las marcas inventadas del resto de las pruebas, no empresas reales."""
    texto = plantilla_decisiones(tmp_path / "plantilla.csv").read_text(encoding="utf-8-sig").upper()
    for inventada in ("ZELVORA", "PELUVIKA", "PLVK"):
        assert inventada in texto
    # Todo el contenido está fijado en ``test_plantilla_trae_estas_dos_filas_de_ejemplo``.


def test_plantilla_se_puede_leer_de_vuelta_con_leer_decisiones(tmp_path):
    """La plantilla tiene el formato que ``leer_decisiones`` espera: se lee sin error y con sus columnas."""
    ruta = plantilla_decisiones(tmp_path / "plantilla.csv")
    r = leer_decisiones([ruta])
    assert list(r.columns) == COLUMNAS_LEIDAS
    assert set(r["TIPO"]) <= {"VINCULO", "DUPLICADO"}


# ── Claves ────────────────────────────────────────────────────────────────────


def test_clave_texto_recorta_colapsa_espacios_y_completa_los_ausentes():
    """Texto sin espacios de más; ``None``, NaN y vacío son ``''``; los números se vuelven texto."""
    r = clave_texto(["  Zelvora   Alimentos \t SA ", None, float("nan"), 12, "   ", "a\nb"])
    assert r.tolist() == ["Zelvora Alimentos SA", "", "", "12", "", "a b"]


def test_clave_texto_conserva_el_indice():
    """Devuelve una Serie con el mismo índice que recibe (``clave_ancla`` alinea por índice)."""
    entrada = pd.Series(["a ", " b"], index=[10, 20])
    assert clave_texto(entrada).index.tolist() == [10, 20]


def test_clave_ancla_une_pais_y_nombre_con_barra_vertical():
    """La clave de una grafía es ``país|nombre`` con los espacios normalizados en cada parte."""
    pais = pd.Series(["España", " Francia", ""])
    nombre = pd.Series(["Zelvora  Alimentos SA ", "Brandelia Fromages", "Kramento"])
    assert clave_ancla(pais, nombre).tolist() == [
        "España|Zelvora Alimentos SA",
        "Francia|Brandelia Fromages",
        "|Kramento",
    ]


def test_clave_ancla_coincide_con_la_clave_que_se_lee_de_las_decisiones(tmp_path):
    """Una decisión escrita a mano con espacios raros llega con la misma clave que ``clave_ancla``."""
    ruta = _csv(tmp_path / "d.csv", [_fila(CLAVE_A="España |Zelvora   Alimentos SA")])
    esperada = clave_ancla(pd.Series(["España "]), pd.Series(["Zelvora   Alimentos SA"])).iat[0]
    assert esperada == "España|Zelvora Alimentos SA"
    # El espacio antes de «|» sí es parte de la clave escrita a mano: no se quita.
    assert leer_decisiones([ruta])["CLAVE_A"].tolist() == ["España |Zelvora Alimentos SA"]


# ── Vocabulario ───────────────────────────────────────────────────────────────


def test_vocabulario_de_decisiones():
    """Las constantes que otras funciones leen: columnas, decisiones de cada tipo y sinónimos."""
    assert COLUMNAS_DECISION == [
        "TIPO",
        "FUENTE",
        "CLAVE_A",
        "NOMBRE_A",
        "CLAVE_B",
        "NOMBRE_B",
        "DECISION",
        "AUTOR",
        "FECHA",
        "RAZON",
    ]
    assert {"EMPRESA", "GRUPO", "NO_VINCULAR", "RELACION_PROPIEDAD"} == DECISIONES_VINCULO
    assert {"MISMA_ENTIDAD", "DISTINTA"} == DECISIONES_DUPLICADO


def test_cada_sinonimo_lleva_a_una_decision_valida():
    """Ningún sinónimo apunta a una decisión que ``leer_decisiones`` rechazaría."""
    assert set(SINONIMOS_DECISION.values()) <= DECISIONES_VINCULO | DECISIONES_DUPLICADO
    assert SINONIMOS_DECISION["SI"] == "EMPRESA"
    assert SINONIMOS_DECISION["DUPLICADO"] == "MISMA_ENTIDAD"
    assert len(SINONIMOS_DECISION) == 10
