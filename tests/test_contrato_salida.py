"""Contrato de salida 1.0 (F1.9): ``linkage()``, ``dedupe()`` y ``link()`` lo cumplen.

Qué congela
-----------
Sobre un conjunto sintético inventado (64 filas, dos fuentes, duplicados
CON_NIT y SIN_NIT, una fuente con su propia columna ``ID_REGISTRO`` y una
columna extra del usuario), para las tres fachadas:

* esquema y orden de las columnas fijas de la correlativa y del golden;
* tipos (enteros, booleanos, decimales, texto);
* ``ID_REGISTRO`` único; correlativa con N filas = N de entrada;
* golden sin NaN en métricas y una fila por ``ID_GRUPO``;
* ``ID_ENTIDAD`` ``NIT-…`` para grupos con identificador válido y ``ENT-…``
  estable entre dos corridas (determinista por contenido);
* ``METODO_UNION`` coherente (singleton → ``sin_pareja``; mismo NIT →
  ``identificador``) y ``SCORE_PAR`` > 0 en una unión por nombre;
* el diccionario cubre todas las columnas entregadas;
* ``validar()`` detecta un contrato roto (mutando un DataFrame);
* el shim de compatibilidad (``res["correlative"]`` …) avisa con
  ``DeprecationWarning`` y mapea a los campos nuevos.

Las empresas son inventadas. Ningún dato licenciado entra aquí.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

import record_linkage as rl
from record_linkage import contrato
from record_linkage.matching.identificadores import bases_validas
from record_linkage.pipeline.errores import ContratoSalidaError
from record_linkage.resultado import ReporteValidacion, ResultadoLinkage
from record_linkage.salida.completar import completar_correlativa

FUENTE_A = "CRM"
FUENTE_B = "ADUANAS"

# Palabras distintas por entidad SIN_NIT: evitan que el encadenamiento por
# nombre (single-linkage) una entidades ajenas que solo difieren en un número.
_NOMBRES_SIN_NIT = [
    "PANADERIA LA ESPIGA DORADA",
    "RESTAURANTE EL BUEN SABOR",
    "LAVANDERIA BURBUJAS AZULES",
    "CARPINTERIA ROBLE VIEJO",
    "FLORISTERIA JARDIN SECRETO",
    "PELUQUERIA TIJERAS MAGICAS",
    "ZAPATERIA PASO FIRME",
    "LIBRERIA PAGINAS ABIERTAS",
    "VETERINARIA PATAS FELICES",
    "OPTICA MIRADA CLARA",
]

# Singletons: ninguna palabra compartida entre sí ni con las demás entidades.
# Con «SOLITARIA 0/1 DEL SUR» y «UNICA EMPRESA 0..5 DEL NORTE» el
# encadenamiento por nombre los unía (solo difieren en un dígito) y la
# comprobación «singleton → sin_pareja» se evaluaba sobre selección vacía.
_NOMBRES_SINGLETON = [
    "ACUARIO CORAL ESMERALDA",
    "TORNERIA PISTON GIRATORIO",
    "HELADERIA COPO NEVADO",
    "IMPRENTA TINTA RAPIDA",
    "JOYERIA PERLA BRILLANTE",
    "MUEBLERIA NOGAL FINO",
    "RELOJERIA SEGUNDERO EXACTO",
    "TAPICERIA TELA SUAVE",
]


def conjunto_sintetico() -> dict[str, pd.DataFrame]:
    """64 filas inventadas en dos fuentes.

    * 12 entidades CON_NIT: dos filas en CRM (variantes SAS / S.A.S.) y una en
      ADUANAS → 36 filas, unión por identificador.
    * 10 entidades SIN_NIT: una fila en cada fuente con sufijo distinto
      (LTDA / LIMITADA) → 20 filas, unión por nombre.
    * 8 singletons (2 en CRM, 6 en ADUANAS) con nombres sin palabras en
      común: en los tres caminos quedan en grupos de uno.

    CRM trae ``CODIGO`` (único por fila: sirve de ``col_id``) y ``SECTOR``
    (columna extra del usuario). ADUANAS trae su propia ``ID_REGISTRO``, que
    choca con el contrato y debe conservarse renombrada ``ID_REGISTRO_FUENTE``.
    """
    a: list[dict[str, str]] = []
    b: list[dict[str, str]] = []
    for i in range(12):
        nit = f"{800200000 + i}"
        base = f"FERRETERIA EL TORNILLO {i:02d}"
        a.append(
            {
                "NIT": nit,
                "RAZON_SOCIAL": f"{base} SAS",
                "CIUDAD": "BOGOTA",
                "SECTOR": "COMERCIO",
                "CODIGO": f"A{i:03d}",
            }
        )
        a.append(
            {
                "NIT": nit,
                "RAZON_SOCIAL": f"{base} S.A.S.",
                "CIUDAD": "BOGOTA",
                "SECTOR": "COMERCIO",
                "CODIGO": f"A{i:03d}X",
            }
        )
        b.append(
            {
                "NIT": nit,
                "RAZON_SOCIAL": f"{base} S A S",
                "CIUDAD": "BOGOTA",
                "ID_REGISTRO": f"B-{i}",
                "SECTOR": "COMERCIO",
            }
        )
    for i, nombre in enumerate(_NOMBRES_SIN_NIT):
        a.append(
            {
                "NIT": "",
                "RAZON_SOCIAL": f"{nombre} LTDA",
                "CIUDAD": "CALI",
                "SECTOR": "SERVICIOS",
                "CODIGO": f"A9{i:02d}",
            }
        )
        b.append(
            {
                "NIT": "",
                "RAZON_SOCIAL": f"{nombre} LIMITADA",
                "CIUDAD": "CALI",
                "ID_REGISTRO": f"B-9{i}",
                "SECTOR": "SERVICIOS",
            }
        )
    for i, nombre in enumerate(_NOMBRES_SINGLETON[:2]):
        a.append(
            {
                "NIT": "",
                "RAZON_SOCIAL": nombre,
                "CIUDAD": "PASTO",
                "SECTOR": "OTRO",
                "CODIGO": f"A8{i:02d}",
            }
        )
    for i, nombre in enumerate(_NOMBRES_SINGLETON[2:]):
        b.append(
            {
                "NIT": "",
                "RAZON_SOCIAL": nombre,
                "CIUDAD": "CUCUTA",
                "ID_REGISTRO": f"B-U{i}",
                "SECTOR": "OTRO",
            }
        )
    return {FUENTE_A: pd.DataFrame(a, dtype=str), FUENTE_B: pd.DataFrame(b, dtype=str)}


def _silencio() -> contextlib.AbstractContextManager[Any]:
    return contextlib.redirect_stdout(io.StringIO())


def _correr_linkage(dir_trabajo: Path, **kwargs: Any) -> ResultadoLinkage:
    fuentes = conjunto_sintetico()
    with _silencio():
        return rl.linkage(
            fuentes,
            work_dir=str(dir_trabajo),
            skip_reporting=True,
            col_ciudad="CIUDAD",
            col_id="CODIGO",
            **kwargs,
        )


@pytest.fixture(scope="module")
def res_linkage(tmp_path_factory: pytest.TempPathFactory) -> ResultadoLinkage:
    return _correr_linkage(tmp_path_factory.mktemp("linkage"))


@pytest.fixture(scope="module")
def res_link(tmp_path_factory: pytest.TempPathFactory) -> ResultadoLinkage:
    fuentes = conjunto_sintetico()
    with _silencio():
        return rl.link(
            fuentes[FUENTE_A],
            fuentes[FUENTE_B],
            nombre_a=FUENTE_A,
            nombre_b=FUENTE_B,
            work_dir=str(tmp_path_factory.mktemp("link")),
            skip_reporting=True,
            col_id="CODIGO",
        )


@pytest.fixture(scope="module")
def res_dedupe(tmp_path_factory: pytest.TempPathFactory) -> ResultadoLinkage:
    fuentes = conjunto_sintetico()
    df = pd.concat([fuentes[FUENTE_A], fuentes[FUENTE_B]], ignore_index=True)
    with _silencio():
        return rl.dedupe(df, output_dir=str(tmp_path_factory.mktemp("dedupe")))


@pytest.fixture(params=["linkage", "link", "dedupe"])
def resultado(request: pytest.FixtureRequest) -> ResultadoLinkage:
    return request.getfixturevalue(f"res_{request.param}")


# ─────────────────────────────────────────────────────────────────────────────
# contrato.py
# ─────────────────────────────────────────────────────────────────────────────


def test_contrato_declara_las_tablas_del_estandar() -> None:
    assert contrato.VERSION_CONTRATO == "1.0"
    assert contrato.COLUMNAS_CORRELATIVA == (
        "ID_REGISTRO",
        "SRC",
        "ORIGINAL_INDEX",
        "ID_GRUPO",
        "ID_ENTIDAD",
        "NIT_FINAL",
        "RAZON_SOCIAL_FINAL",
        "NAME_SIMILARITY_SCORE",
        "NIT_DISTANCE",
        "SCORE_PAR",
        "METODO_UNION",
        "CONFIANZA",
    )
    assert len(contrato.COLUMNAS_GOLDEN) == 14 and contrato.COLUMNAS_GOLDEN[-1] == "ID_ENTIDAD"
    assert len(contrato.COLUMNAS_ENLACES) == 12
    assert contrato.COLUMNAS_REVISION == (
        "TIPO",
        "FUENTE",
        "CLAVE_A",
        "NOMBRE_A",
        "CLAVE_B",
        "NOMBRE_B",
        "DECISION",
        "AUTOR",
        "RAZON",
    )
    assert set(contrato.METODOS_UNION_F1) == {"identificador", "nombre", "sin_pareja"}
    assert set(contrato.METODOS_UNION_F1) < set(contrato.METODOS_UNION)
    for col in contrato.COLUMNAS_TECNICAS:
        assert col not in contrato.COLUMNAS_CORRELATIVA


def test_esquemas_pyarrow() -> None:
    esq = contrato.esquema_correlativa(["NIT", "RAZON_SOCIAL", "SECTOR"])
    assert esq.names[:12] == list(contrato.COLUMNAS_CORRELATIVA)
    assert esq.names[12:] == ["NIT", "RAZON_SOCIAL", "SECTOR"]
    assert esq.field("ID_GRUPO").type == pa.int64()
    assert esq.field("SCORE_PAR").type == pa.float64()
    esq_tipado = contrato.esquema_correlativa({"MONTO": pa.float64()})
    assert esq_tipado.field("MONTO").type == pa.float64()
    with pytest.raises(ValueError, match="ID_GRUPO"):
        contrato.esquema_correlativa(["ID_GRUPO"])
    golden = contrato.esquema_golden()
    assert golden.names == list(contrato.COLUMNAS_GOLDEN)
    assert golden.field("REQUIRES_REVIEW").type == pa.bool_()
    assert golden.field("RECORD_COUNT").type == pa.int64()
    assert contrato.esquema_enlaces().names == list(contrato.COLUMNAS_ENLACES)
    assert contrato.esquema_revision().names == list(contrato.COLUMNAS_REVISION)


def test_columnas_finales_se_importan_desde_el_contrato() -> None:
    from record_linkage.golden import columnas_finales

    assert columnas_finales.COLUMNAS_FINALES == contrato.COLUMNAS_FINALES
    assert columnas_finales.COLUMNAS_FINALES == (
        "NIT_FINAL",
        "RAZON_SOCIAL_FINAL",
        "NAME_SIMILARITY_SCORE",
        "NIT_DISTANCE",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Las tres fachadas cumplen el contrato
# ─────────────────────────────────────────────────────────────────────────────


def test_tipo_de_retorno_y_validacion(resultado: ResultadoLinkage) -> None:
    assert isinstance(resultado, ResultadoLinkage)
    reporte = resultado.validar()
    assert isinstance(reporte, ReporteValidacion)
    assert reporte.ok, reporte.fallos
    assert resultado.validar(estricto=True).ok
    assert resultado.manifiesto["contrato"]["version"] == contrato.VERSION_CONTRATO
    assert resultado.dir_trabajo is not None and Path(resultado.dir_trabajo).is_dir()


def test_correlativa_orden_y_tipos(resultado: ResultadoLinkage) -> None:
    c = resultado.correlativa
    assert list(c.columns[:12]) == list(contrato.COLUMNAS_CORRELATIVA)
    assert pd.api.types.is_integer_dtype(c["ORIGINAL_INDEX"])
    assert pd.api.types.is_integer_dtype(c["ID_GRUPO"])
    assert pd.api.types.is_integer_dtype(c["NIT_DISTANCE"])
    assert pd.api.types.is_float_dtype(c["NAME_SIMILARITY_SCORE"])
    assert pd.api.types.is_float_dtype(c["SCORE_PAR"])
    for col in ("ID_REGISTRO", "SRC", "ID_ENTIDAD", "METODO_UNION"):
        assert pd.api.types.is_string_dtype(c[col]) or pd.api.types.is_object_dtype(c[col])
    # Las técnicas salen del entregable.
    assert not set(contrato.COLUMNAS_TECNICAS) & set(c.columns)
    # Las columnas de la fuente se conservan (incluida la extra del usuario).
    assert "SECTOR" in c.columns and "RAZON_SOCIAL" in c.columns


def test_id_registro_unico_y_n_filas(resultado: ResultadoLinkage) -> None:
    c = resultado.correlativa
    assert c["ID_REGISTRO"].is_unique and c["ID_REGISTRO"].notna().all()
    n_entrada = sum(e["filas"] for e in resultado.manifiesto["entradas"].values())
    assert len(c) == n_entrada == 64


def test_id_registro_usa_col_id_cuando_es_unico(res_linkage: ResultadoLinkage) -> None:
    c = res_linkage.correlativa
    de_a = c.loc[c["SRC"] == FUENTE_A, "ID_REGISTRO"]
    assert de_a.str.startswith(f"{FUENTE_A}-A").all()  # <SRC>-<id nativo>
    de_b = c.loc[c["SRC"] == FUENTE_B, "ID_REGISTRO"]
    assert de_b.str.fullmatch(rf"{FUENTE_B}-F\d+").all()  # sin col_id → <SRC>-F<fila>
    regla = res_linkage.manifiesto["completar"]["id_registro"]
    assert regla[FUENTE_A]["regla"] == "col_id" and regla[FUENTE_B]["regla"] == "fila"


def test_colision_con_el_contrato_se_renombra(res_linkage: ResultadoLinkage) -> None:
    c = res_linkage.correlativa
    assert "ID_REGISTRO_FUENTE" in c.columns
    propia = c.loc[c["SRC"] == FUENTE_B, "ID_REGISTRO_FUENTE"]
    assert propia.str.startswith("B-").all()
    assert res_linkage.manifiesto["completar"]["renombres"] == {"ID_REGISTRO": "ID_REGISTRO_FUENTE"}
    dic = res_linkage.diccionario
    fila = dic[(dic["tabla"] == "correlativa") & (dic["columna"] == "ID_REGISTRO_FUENTE")]
    assert len(fila) == 1 and fila["origen"].iloc[0] == "fuente"
    assert "renombrada" in fila["significado"].iloc[0]


def test_golden_contrato(res_linkage: ResultadoLinkage, res_link: ResultadoLinkage) -> None:
    for res in (res_linkage, res_link):
        g = res.golden
        assert g is not None
        assert list(g.columns[:14]) == list(contrato.COLUMNAS_GOLDEN)
        metricas = ["SOURCES_COUNT", "RECORD_COUNT", "NAME_VARIATIONS", "NIT_VARIATIONS"]
        assert not g[[*metricas, "CONFIDENCE_SCORE"]].isna().any().any()
        for col in metricas:
            assert pd.api.types.is_integer_dtype(g[col]), col
        assert pd.api.types.is_bool_dtype(g["REQUIRES_REVIEW"])
        assert g["ID_GRUPO"].is_unique
        assert set(g["ID_GRUPO"]) == set(res.correlativa["ID_GRUPO"])
        assert g["ID_ENTIDAD"].notna().all()
        # Sin columnas de la correlativa pegadas.
        assert not {"RAZON_SOCIAL", "NIT", "SECTOR", "SRC", "ORIGINAL_INDEX"} & set(g.columns)


def test_dedupe_golden_none_documentado(res_dedupe: ResultadoLinkage) -> None:
    """``dedupe`` no produce golden en memoria: la correlativa cumple igual."""
    assert res_dedupe.golden is None
    assert res_dedupe.correlativa["CONFIANZA"].isna().all()
    assert "golden" in res_dedupe.manifiesto["completar"]["confianza"]["motivo"]
    assert res_dedupe.manifiesto["completar"]["score_par"]["origen"] is None
    assert res_dedupe.manifiesto["completar"]["id_grupo"]["recodificado"] is True


def test_id_entidad_nit_y_ent(resultado: ResultadoLinkage) -> None:
    c = resultado.correlativa
    con_nit = c[c["NIT"].fillna("").astype(str).str.len() > 0]
    assert con_nit["ID_ENTIDAD"].str.fullmatch(r"NIT-\d{7,}").all()
    sin_nit = c[c["NIT"].fillna("").astype(str).str.len() == 0]
    assert sin_nit["ID_ENTIDAD"].str.fullmatch(r"ENT-[0-9a-f]{16}").all()
    # Un ID_GRUPO ↔ un ID_ENTIDAD.
    assert (c.groupby("ID_GRUPO")["ID_ENTIDAD"].nunique() == 1).all()
    assert (c.groupby("ID_ENTIDAD")["ID_GRUPO"].nunique() == 1).all()
    # NIT-<base sin DV>: la base es la del NIT_FINAL del grupo.
    fila = con_nit.iloc[0]
    assert fila["ID_ENTIDAD"] == "NIT-" + str(fila["NIT"]).lstrip("0")


def test_ent_es_determinista_por_contenido(res_linkage: ResultadoLinkage, tmp_path: Path) -> None:
    otra = _correr_linkage(tmp_path / "segunda")
    a = res_linkage.correlativa.set_index("ID_REGISTRO")["ID_ENTIDAD"].sort_index()
    b = otra.correlativa.set_index("ID_REGISTRO")["ID_ENTIDAD"].sort_index()
    pd.testing.assert_series_equal(a, b)
    # Y es exactamente SHA-256 de los ID_REGISTRO ordenados del grupo.
    c = res_linkage.correlativa
    grupo = c[c["ID_ENTIDAD"].str.startswith("ENT-")].groupby("ID_GRUPO")["ID_REGISTRO"]
    for _, ids in grupo:
        esperado = "ENT-" + hashlib.sha256("|".join(sorted(ids)).encode()).hexdigest()[:16]
        assert c.loc[c["ID_REGISTRO"].isin(ids), "ID_ENTIDAD"].eq(esperado).all()


def test_metodo_union_coherente(resultado: ResultadoLinkage) -> None:
    c = resultado.correlativa
    assert set(c["METODO_UNION"]) <= set(contrato.METODOS_UNION_F1)
    tam = c.groupby("ID_GRUPO")["ID_REGISTRO"].transform("size")
    # Los 8 singletons del fixture lo son en los tres caminos: la
    # comprobación de abajo no se evalúa sobre una selección vacía.
    assert int((tam == 1).sum()) == 8
    assert (c.loc[tam == 1, "METODO_UNION"] == "sin_pareja").all()
    assert (c.loc[tam == 1, "SCORE_PAR"].isna()).all()
    con_nit = c[(c["NIT"].fillna("").astype(str).str.len() > 0) & (tam > 1)]
    assert len(con_nit) == 36
    assert (con_nit["METODO_UNION"] == "identificador").all()
    por_nombre = c[c["METODO_UNION"] == "nombre"]
    assert len(por_nombre) > 0


def test_score_par_positivo_en_union_por_nombre(
    res_linkage: ResultadoLinkage, res_link: ResultadoLinkage
) -> None:
    for res in (res_linkage, res_link):
        c = res.correlativa
        por_nombre = c[c["METODO_UNION"] == "nombre"]
        assert len(por_nombre) >= 2
        assert (por_nombre["SCORE_PAR"] > 0).all()
        assert (c["SCORE_PAR"].dropna() <= 1.0).all()
        assert res.manifiesto["completar"]["score_par"]["origen"].endswith("scored.db")


def test_confianza_viene_del_golden(res_linkage: ResultadoLinkage) -> None:
    c, g = res_linkage.correlativa, res_linkage.golden
    assert g is not None
    esperado = c["ID_GRUPO"].map(g.set_index("ID_GRUPO")["CONFIANZA"])
    assert (c["CONFIANZA"] == esperado).all()
    assert set(c["CONFIANZA"]) <= set(contrato.NIVELES_CONFIANZA)


def test_diccionario_cubre_todas_las_columnas(resultado: ResultadoLinkage) -> None:
    dic = resultado.diccionario
    assert list(dic.columns) == ["tabla", "columna", "tipo", "significado", "origen", "alias_es"]
    cubiertas = set(dic.loc[dic["tabla"] == "correlativa", "columna"])
    assert set(resultado.correlativa.columns) <= cubiertas
    if resultado.golden is not None:
        assert set(resultado.golden.columns) <= set(dic.loc[dic["tabla"] == "golden", "columna"])
        fila = dic[(dic["tabla"] == "golden") & (dic["columna"] == "PRIMARY_SOURCE")]
        assert fila["alias_es"].iloc[0] == "FUENTE_PRINCIPAL"
    else:  # una tabla que no se entrega no se lista
        assert not (dic["tabla"] == "golden").any()
    assert set(dic["origen"]) <= {"motor", "fuente", "revision"}
    assert (dic["significado"].str.len() > 0).all()
    assert set(dic.loc[dic["tabla"] == "revision", "columna"]) == set(contrato.COLUMNAS_REVISION)
    assert dic.loc[dic["columna"] == "SRC", "alias_es"].eq("FUENTE").all()


def test_revision_y_enlaces(resultado: ResultadoLinkage) -> None:
    assert resultado.enlaces is None
    assert list(resultado.revision.columns) == list(contrato.COLUMNAS_REVISION)
    assert resultado.revision.empty


# ─────────────────────────────────────────────────────────────────────────────
# validar() detecta un contrato roto
# ─────────────────────────────────────────────────────────────────────────────


def _copia(res: ResultadoLinkage) -> ResultadoLinkage:
    return ResultadoLinkage(
        correlativa=res.correlativa.copy(),
        golden=None if res.golden is None else res.golden.copy(),
        metricas=dict(res.metricas),
        manifiesto=dict(res.manifiesto),
        enlaces=res.enlaces,
        revision=res.revision.copy(),
        diccionario=res.diccionario.copy(),
        dir_trabajo=res.dir_trabajo,
    )


def test_validar_detecta_contrato_roto(res_linkage: ResultadoLinkage) -> None:
    roto = _copia(res_linkage)
    roto.correlativa.loc[0, "ID_REGISTRO"] = roto.correlativa.loc[1, "ID_REGISTRO"]
    roto.correlativa.loc[2, "METODO_UNION"] = "magia"
    roto.correlativa.loc[3, "ID_ENTIDAD"] = None
    roto.correlativa = roto.correlativa[list(reversed(roto.correlativa.columns))]
    assert roto.golden is not None
    roto.golden = roto.golden.iloc[1:]
    roto.golden.loc[roto.golden.index[0], "RECORD_COUNT"] = np.nan
    reporte = roto.validar()
    assert not reporte.ok
    texto = "\n".join(reporte.fallos)
    assert "ID_REGISTRO" in texto and "único" in texto
    assert "METODO_UNION" in texto and "magia" in texto
    assert "ID_ENTIDAD" in texto
    assert "orden" in texto
    assert "golden" in texto and "RECORD_COUNT" in texto
    with pytest.raises(ContratoSalidaError) as exc:
        roto.validar(estricto=True)
    assert exc.value.fallos == reporte.fallos
    assert "Qué hacer" in str(exc.value)


def test_validar_filas_y_consistencia_entidad(res_linkage: ResultadoLinkage) -> None:
    roto = _copia(res_linkage)
    roto.correlativa = roto.correlativa.iloc[:-1]
    primer_grupo = roto.correlativa["ID_GRUPO"].iloc[0]
    mascara = roto.correlativa["ID_GRUPO"] == primer_grupo
    roto.correlativa.loc[mascara.idxmax(), "ID_ENTIDAD"] = "ENT-0000000000000000"
    reporte = roto.validar()
    assert not reporte.ok
    texto = "\n".join(reporte.fallos)
    assert "64" in texto and "63" in texto  # N entrada vs N correlativa
    assert "ID_ENTIDAD" in texto and "ID_GRUPO" in texto


def test_validar_tipos(res_linkage: ResultadoLinkage) -> None:
    roto = _copia(res_linkage)
    roto.correlativa["ID_GRUPO"] = roto.correlativa["ID_GRUPO"].astype(str)
    assert roto.golden is not None
    roto.golden["REQUIRES_REVIEW"] = roto.golden["REQUIRES_REVIEW"].astype(int)
    fallos = roto.validar().fallos
    assert any("ID_GRUPO" in f and "entero" in f for f in fallos)
    assert any("REQUIRES_REVIEW" in f and "booleano" in f for f in fallos)


# ─────────────────────────────────────────────────────────────────────────────
# Compatibilidad con la API vieja (dict)
# ─────────────────────────────────────────────────────────────────────────────


def test_shim_claves_viejas_con_deprecation(res_linkage: ResultadoLinkage) -> None:
    with pytest.warns(DeprecationWarning, match="correlativa"):
        assert res_linkage["correlative"] is res_linkage.correlativa
    with pytest.warns(DeprecationWarning):
        assert res_linkage["golden"] is res_linkage.golden
    with pytest.warns(DeprecationWarning):
        assert res_linkage.get("report_files") is None  # skip_reporting=True
    with pytest.warns(DeprecationWarning):
        assert res_linkage.get("preprocessing", "x") == "x"
    with pytest.warns(DeprecationWarning):
        assert res_linkage["work_dir"] == res_linkage.dir_trabajo
    with pytest.warns(DeprecationWarning):
        assert "correlative" in res_linkage and "golden" in res_linkage
        assert "preprocessing" not in res_linkage
    with pytest.warns(
        DeprecationWarning, match=r"res\.keys\(\)/dict\(res\) está obsoleto.*res\.correlativa"
    ):
        assert set(res_linkage.keys()) == {"correlative", "golden"}
    with pytest.warns(DeprecationWarning, match=r"iter\(res\) está obsoleto"):
        assert list(iter(res_linkage)) == ["correlative", "golden"]
    with pytest.warns(DeprecationWarning), pytest.raises(KeyError):
        res_linkage["inexistente"]
    # Los campos nuevos no avisan.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _ = res_linkage.correlativa, res_linkage.golden, res_linkage.metricas


def test_col_id_desconocido_falla_rapido(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="col_id"), _silencio():
        rl.linkage(
            conjunto_sintetico(),
            work_dir=str(tmp_path / "w"),
            skip_reporting=True,
            col_id="NO_EXISTE",
        )


def test_col_id_no_unico_cae_a_fila_y_lo_dice(tmp_path: Path) -> None:
    fuentes = conjunto_sintetico()
    with _silencio():
        res = rl.linkage(
            fuentes,
            work_dir=str(tmp_path / "w"),
            skip_reporting=True,
            col_id="SECTOR",  # existe en ambas, no es único
        )
    assert res.correlativa["ID_REGISTRO"].str.contains("-F").all()
    for fuente in (FUENTE_A, FUENTE_B):
        nota = res.manifiesto["completar"]["id_registro"][fuente]
        assert nota["regla"] == "fila" and "único" in nota["motivo"]


# ─────────────────────────────────────────────────────────────────────────────
# r3/r5 · la regla de «base válida» es la del motor (NIT_BASE/NIT_VALID)
# ─────────────────────────────────────────────────────────────────────────────


def _conjunto_nit_flotante() -> pd.DataFrame:
    """Caso habitual: pandas lee un CSV con un NIT en blanco y la columna queda
    float64 (``900111222.0``). El motor lo resuelve (NitProcessor entiende el
    sufijo ``.0``); el contrato tiene que decir lo mismo que el motor."""
    df = pd.DataFrame(
        {
            "NIT": [900111222.0, 900111222.0, np.nan, 800333444.0],
            "RAZON_SOCIAL": [
                "COMERCIAL ANDINA SAS",
                "COMERCIAL ANDINA S.A.S.",
                "FERRETERIA EL TORNILLO LTDA",
                "TEXTILES DEL NORTE SA",
            ],
        }
    )
    assert pd.api.types.is_float_dtype(df["NIT"])
    return df


def _asegurar_union_por_identificador(res: ResultadoLinkage) -> None:
    c = res.correlativa
    mismo_nit = c[c["ID_ENTIDAD"] == "NIT-900111222"]
    assert len(mismo_nit) == 2 and mismo_nit["ID_GRUPO"].nunique() == 1
    assert list(mismo_nit["METODO_UNION"]) == ["identificador", "identificador"]
    assert list(c.loc[~c.index.isin(mismo_nit.index), "METODO_UNION"]) == [
        "sin_pareja",
        "sin_pareja",
    ]
    identificador = res.manifiesto["completar"]["identificador"]
    assert identificador["origen"].startswith("NIT_BASE/NIT_VALID del motor")
    assert identificador["grupos_con_bases_distintas"] == 0


def test_linkage_nit_flotante_une_por_identificador(tmp_path: Path) -> None:
    with _silencio():
        res = rl.linkage(
            {"A": _conjunto_nit_flotante()}, work_dir=str(tmp_path / "w"), skip_reporting=True
        )
    _asegurar_union_por_identificador(res)


def test_dedupe_nit_flotante_une_por_identificador(tmp_path: Path) -> None:
    with _silencio():
        res = rl.dedupe(_conjunto_nit_flotante(), output_dir=str(tmp_path / "dd"))
    _asegurar_union_por_identificador(res)


def test_completar_sin_tecnicas_recalcula_desde_nit_y_lo_declara(tmp_path: Path) -> None:
    """Sin ``NIT_OK``/``NIT_VALID`` (correlativa que no viene del motor) la base
    se recalcula desde la columna de identificador y el reporte lo dice."""
    correl = pd.DataFrame(
        {
            "NIT": ["900123456", "9001234568", ""],
            "RAZON_SOCIAL": ["ACME SAS", "ACME S.A.S.", "GLOBEX"],
            "SRC": ["X", "X", "X"],
            "ORIGINAL_INDEX": [0, 1, 2],
            "ID_GRUPO": [0, 0, 2],
            "NIT_FINAL": ["9001234568", "9001234568", ""],
            "RAZON_SOCIAL_FINAL": ["ACME SAS", "ACME SAS", "GLOBEX"],
            "NAME_SIMILARITY_SCORE": [1.0, 0.9, 1.0],
            "NIT_DISTANCE": [0, 0, 0],
        }
    )
    fuentes = {"X": correl[["NIT", "RAZON_SOCIAL"]]}
    correlativa, _, reporte = completar_correlativa(
        correl, None, tmp_path, None, fuentes, col_nit="NIT"
    )
    assert list(correlativa["METODO_UNION"]) == ["identificador", "identificador", "sin_pareja"]
    assert reporte.identificador["origen"] == "columna NIT de la fuente (sin NIT_BASE/NIT_VALID)"
    assert "NitProcessor" in reporte.identificador["motivo"]
    assert reporte.identificador["grupos_con_bases_distintas"] == 0


def test_completar_con_tecnicas_usa_nit_base_del_motor(tmp_path: Path) -> None:
    """r5: la regla de «base válida» es UNA y es la del motor. ``METODO_UNION``
    y ``grupos_con_bases_distintas`` salen de ``NIT_BASE`` donde ``NIT_VALID``
    (sin pasar por ``bases_validas``), y la base del grupo es el ``NIT_BASE``
    de la fila cuyo ``NIT_OK == NIT_FINAL``. Casos medidos en el banco: una
    cédula de 8 dígitos frente al NIT de 10 que la contiene (el motor vio dos
    bases y unió por nombre), un identificador de 6 dígitos que el motor sí
    validó (``bases_validas`` lo descartaría por corto) y un NIT con y sin
    DV. ``ID_ENTIDAD`` sigue la base canónica del preámbulo."""
    correl = pd.DataFrame(
        {
            "NIT": [
                "10282948",
                "1028294826",
                "456866",
                "456866",
                "9001112221",
                "900111222",
                "8003334448",
            ],
            "RAZON_SOCIAL": [
                "JUAN PEREZ",
                "JUAN PEREZ",
                "LA ESQUINA",
                "LA ESQUINA LTDA",
                "ACME SAS",
                "ACME S.A.S.",
                "GLOBEX",
            ],
            "SRC": ["X"] * 7,
            "ORIGINAL_INDEX": list(range(7)),
            "NIT_OK": [
                "10282948",
                "1028294826",
                "456866",
                "456866",
                "9001112221",
                "900111222",
                "8003334448",
            ],
            "NIT_BASE": [
                "10282948",
                "102829482",
                "456866",
                "456866",
                "900111222",
                "900111222",
                "800333444",
            ],
            "NIT_VALID": ["1", 1, True, "true", "1", "1", "1"],
            "ID_GRUPO": [0, 0, 1, 1, 2, 2, 3],
            "NIT_FINAL": [
                "1028294826",
                "1028294826",
                "456866",
                "456866",
                "9001112221",
                "9001112221",
                "8003334448",
            ],
            "RAZON_SOCIAL_FINAL": [
                "JUAN PEREZ",
                "JUAN PEREZ",
                "LA ESQUINA",
                "LA ESQUINA",
                "ACME SAS",
                "ACME SAS",
                "GLOBEX",
            ],
            "NAME_SIMILARITY_SCORE": [1.0, 1.0, 0.9, 0.9, 1.0, 0.95, 1.0],
            "NIT_DISTANCE": [0] * 7,
        }
    )
    fuentes = {"X": correl[["NIT", "RAZON_SOCIAL"]]}
    correlativa, _, reporte = completar_correlativa(
        correl, None, tmp_path, None, fuentes, col_nit="NIT"
    )
    # La cédula 10282948 no es la base del grupo (102829482): el motor la unió
    # por nombre. La fila que aportó NIT_FINAL tampoco tiene con quién compartir
    # su base dentro del grupo: ``identificador`` exige pareja de base, así que
    # también queda ``nombre`` (decisión del coordinador, F1.9) y el reporte la
    # cuenta en ``identificador_sin_pareja_de_base``.
    assert list(correlativa["METODO_UNION"]) == [
        "nombre",
        "nombre",
        "identificador",
        "identificador",
        "identificador",
        "identificador",
        "sin_pareja",
    ]
    identificador = reporte.identificador
    assert identificador["origen"].startswith("NIT_BASE/NIT_VALID del motor")
    assert "NIT_OK" in identificador["base_del_grupo"]
    assert identificador["grupos_con_bases_distintas"] == 1
    assert identificador["grupos_sin_fila_de_nit_final"] == 0
    assert identificador["identificador_sin_pareja_de_base"] == 1
    # ID_ENTIDAD es NIT-<base canónica de NIT_FINAL> (decisión del preámbulo), no
    # el NIT_BASE del motor: el de 6 dígitos no tiene base canónica y recibe ENT-.
    assert correlativa.loc[0, "ID_ENTIDAD"] == "NIT-" + bases_validas(np.array(["1028294826"]))[0]
    assert correlativa.loc[2, "ID_ENTIDAD"].startswith("ENT-")
    assert correlativa.loc[4, "ID_ENTIDAD"] == "NIT-900111222"
    assert correlativa.loc[6, "ID_ENTIDAD"] == "NIT-800333444"
    # Las técnicas no viajan en el entregable.
    assert not {"NIT_OK", "NIT_BASE", "NIT_VALID"} & set(correlativa.columns)


def test_completar_con_tecnicas_sin_fila_de_nit_final_lo_declara(tmp_path: Path) -> None:
    """Si ningún miembro del grupo aporta ``NIT_FINAL`` (no debería pasar con el
    motor), el grupo no tiene base: nadie se une por identificador y el
    reporte cuenta el grupo en vez de inventar una base."""
    correl = pd.DataFrame(
        {
            "NIT": ["900111222", "900111222"],
            "RAZON_SOCIAL": ["ACME SAS", "ACME S.A.S."],
            "SRC": ["X", "X"],
            "ORIGINAL_INDEX": [0, 1],
            "NIT_OK": ["900111222", "900111222"],
            "NIT_BASE": ["900111222", "900111222"],
            "NIT_VALID": [True, True],
            "ID_GRUPO": [0, 0],
            "NIT_FINAL": ["9001112221", "9001112221"],
            "RAZON_SOCIAL_FINAL": ["ACME SAS", "ACME SAS"],
            "NAME_SIMILARITY_SCORE": [1.0, 1.0],
            "NIT_DISTANCE": [0, 0],
        }
    )
    fuentes = {"X": correl[["NIT", "RAZON_SOCIAL"]]}
    correlativa, _, reporte = completar_correlativa(
        correl, None, tmp_path, None, fuentes, col_nit="NIT"
    )
    assert list(correlativa["METODO_UNION"]) == ["nombre", "nombre"]
    assert reporte.identificador["grupos_sin_fila_de_nit_final"] == 1
    assert reporte.identificador["grupos_con_bases_distintas"] == 0


def test_iter_avisa_desde_la_linea_del_usuario(res_linkage: ResultadoLinkage) -> None:
    """El aviso de ``iter(res)`` se atribuye al archivo del usuario (no a
    resultado.py) y habla de iter, no de keys()."""
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        claves = list(iter(res_linkage))
    assert claves == ["correlative", "golden"]
    assert len(avisos) == 1
    assert Path(avisos[0].filename) == Path(__file__)
    assert "iter(res)" in str(avisos[0].message)
    assert "res.keys()" not in str(avisos[0].message)

    # dict(res) pasa por keys() y luego por __getitem__ de cada clave: todos
    # los avisos apuntan a esta línea y el primero nombra dict(res).
    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        assert set(dict(res_linkage)) == {"correlative", "golden"}
    assert avisos and all(Path(a.filename) == Path(__file__) for a in avisos)
    assert "dict(res)" in str(avisos[0].message)


# ─────────────────────────────────────────────────────────────────────────────
# completar_correlativa como función (sin correr el motor)
# ─────────────────────────────────────────────────────────────────────────────


def test_completar_sin_scored_db_deja_score_par_nulo(tmp_path: Path) -> None:
    correl = pd.DataFrame(
        {
            "NIT": ["900123456", "900123456", ""],
            "RAZON_SOCIAL": ["ACME SAS", "ACME S.A.S.", "GLOBEX"],
            "SRC": ["X", "X", "X"],
            "ORIGINAL_INDEX": [0, 1, 2],
            "NOMBRE_LIMPIO": ["ACME", "ACME", "GLOBEX"],
            "ID_GRUPO": [0, 0, 2],
            "NIT_FINAL": ["9001234568", "9001234568", ""],  # 8 = DV DIAN de 900123456
            "RAZON_SOCIAL_FINAL": ["ACME SAS", "ACME SAS", "GLOBEX"],
            "NAME_SIMILARITY_SCORE": [1.0, 0.9, 1.0],
            "NIT_DISTANCE": [0, 0, 0],
        }
    )
    fuentes = {"X": correl[["NIT", "RAZON_SOCIAL"]]}
    correlativa, golden, reporte = completar_correlativa(
        correl, None, tmp_path, None, fuentes, col_nit="NIT"
    )
    assert golden is None
    assert list(correlativa.columns) == [
        *list(contrato.COLUMNAS_CORRELATIVA),
        "NIT",
        "RAZON_SOCIAL",
    ]
    assert correlativa["SCORE_PAR"].isna().all()
    assert reporte.score_par["origen"] is None and "scored.db" in reporte.score_par["motivo"]
    assert list(correlativa["METODO_UNION"]) == ["identificador", "identificador", "sin_pareja"]
    assert list(correlativa["ID_REGISTRO"]) == ["X-F0", "X-F1", "X-F2"]
    assert correlativa["ID_ENTIDAD"].iloc[0] == "NIT-900123456"
    assert reporte.columnas_tecnicas_retiradas == ["NOMBRE_LIMPIO"]


def _correl_dos_grupos() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "NIT": ["900123456", "9001234568", "", "900123456"],
            "RAZON_SOCIAL": ["ACME SAS", "ACME S.A.S.", "GLOBEX", "ACME"],
            "SRC": ["RUES", "CRM", "CRM", "CRM"],
            "ORIGINAL_INDEX": [0, 1, 2, 3],
            "NIT_OK": ["9001234568", "9001234568", "", "9001234568"],
            "ID_GRUPO": [0, 0, 2, 0],
            "NIT_FINAL": ["9001234568", "9001234568", "", "9001234568"],
            "RAZON_SOCIAL_FINAL": ["ACME SAS", "ACME SAS", "GLOBEX", "ACME SAS"],
            "NAME_SIMILARITY_SCORE": [1.0, 0.9, 1.0, 0.7],
            "NIT_DISTANCE": [0, 0, 0, 0],
        }
    )


def test_completar_repara_golden_sin_metricas_y_lo_declara(tmp_path: Path) -> None:
    """La fila fusionada por NIT llega sin métricas y con columnas pegadas (F1.1)."""
    correl = _correl_dos_grupos()
    golden = pd.DataFrame(
        {
            "ID_GRUPO": [2, 0],
            "NIT_FINAL": ["", "9001234568"],
            "RAZON_SOCIAL_FINAL": ["GLOBEX", "ACME SAS"],
            "PRIMARY_SOURCE": ["CRM", None],
            "SOURCES_LIST": ["CRM", None],
            "SOURCES_COUNT": [1.0, np.nan],
            "RECORD_COUNT": [1.0, np.nan],
            "NAME_VARIATIONS": [1.0, np.nan],
            "NIT_VARIATIONS": [1.0, np.nan],
            "CONFIDENCE_SCORE": [1.0, np.nan],
            "CONFIANZA": ["MEDIA", None],
            "REQUIRES_REVIEW": [0.0, np.nan],
            "CREATED_AT": ["2026-10-06 00:00:00", None],
            "RAZON_SOCIAL": ["GLOBEX", "ACME SAS"],  # pegada de la correlativa
            "SRC": ["CRM", "RUES"],
        }
    )
    fuentes = {"RUES": ["NIT", "RAZON_SOCIAL"], "CRM": ["NIT", "RAZON_SOCIAL"]}
    _, g, reporte = completar_correlativa(
        correl, golden, tmp_path, None, fuentes, prioridad_fuentes=["RUES", "CRM"]
    )
    assert g is not None
    assert list(g.columns) == list(contrato.COLUMNAS_GOLDEN)
    assert not g[list(contrato.COLUMNAS_METRICAS_GOLDEN)].isna().any().any()
    fila = g.set_index("ID_GRUPO").loc[0]
    assert fila["RECORD_COUNT"] == 3 and fila["SOURCES_COUNT"] == 2
    assert fila["SOURCES_LIST"] == "CRM|RUES" and fila["PRIMARY_SOURCE"] == "RUES"
    assert fila["NAME_VARIATIONS"] == 3 and fila["NIT_VARIATIONS"] == 1
    assert fila["CONFIANZA"] == "ALTA"  # NIT único y dos fuentes
    assert fila["CONFIDENCE_SCORE"] == pytest.approx(0.5 + 0.4 / 3 + 0.1 * (2 / 3), abs=1e-4)
    assert bool(fila["REQUIRES_REVIEW"]) is False
    assert pd.api.types.is_bool_dtype(g["REQUIRES_REVIEW"])
    for col in ("SOURCES_COUNT", "RECORD_COUNT", "NAME_VARIATIONS", "NIT_VARIATIONS"):
        assert pd.api.types.is_integer_dtype(g[col]), col
    assert reporte.golden["metricas_reparadas"]["n"] == 1
    assert reporte.golden["metricas_reparadas"]["grupos"] == [0]
    assert set(reporte.golden["columnas_pegadas_retiradas"]) == {"RAZON_SOCIAL", "SRC"}


def test_completar_golden_sin_grupo_de_la_correlativa_falla(tmp_path: Path) -> None:
    correl = _correl_dos_grupos()
    golden = pd.DataFrame(
        {
            "ID_GRUPO": [2, 99],  # falta el 0; el 99 es huérfana
            "NIT_FINAL": ["", ""],
            "RAZON_SOCIAL_FINAL": ["GLOBEX", "NADIE"],
            "CONFIANZA": ["MEDIA", "MEDIA"],
        }
    )
    with pytest.raises(ContratoSalidaError, match="sin fila en el golden"):
        completar_correlativa(correl, golden, tmp_path, None, {"RUES": [], "CRM": []})


def test_metricas_de_grupo_y_calidad_misma_regla() -> None:
    """Las funciones puras reproducen la regla del generador (vectorizada y SQL)."""
    from record_linkage.golden.metricas import metricas_de_calidad, metricas_de_grupo

    correl = _correl_dos_grupos()
    m = metricas_de_grupo(correl, ["CRM", "RUES"])
    assert list(m.index) == [0, 2]
    assert m.loc[0, "PRIMARY_SOURCE"] == "CRM" and m.loc[2, "CONFIANZA"] == "MEDIA"
    golden = pd.DataFrame(
        {
            "NIT_FINAL": ["9001234568", "9001234568", "12345", "9001234568"],
            "NIT_VARIATIONS": [1, 1, 1, 4],
            "NAME_VARIATIONS": [1, 3, 1, 1],
            "SOURCES_COUNT": [2, 1, 1, 2],
            "RECORD_COUNT": [2, 3, 1, 25],
        }
    )
    calidad = metricas_de_calidad(golden)
    assert list(calidad["CONFIDENCE_SCORE"]) == [
        1.0,
        0.85,
        1.0,
        round(0.1 * (2 / 3) + 0.5 / 4 + 0.4, 4),
    ]
    assert list(calidad["REQUIRES_REVIEW"]) == [False, False, True, True]


# ─────────────────────────────────────────────────────────────────────────────
# Corrección de la revisión (F1.9, segundo commit)
# ─────────────────────────────────────────────────────────────────────────────


def test_col_id_renombrado_por_colision_se_resuelve(tmp_path: Path) -> None:
    """``col_id="ID_REGISTRO"`` apunta a la columna propia de ADUANAS, que el
    contrato renombra ``ID_REGISTRO_FUENTE``: debe usarse, no fallar."""
    with _silencio():
        res = rl.linkage(
            conjunto_sintetico(),
            work_dir=str(tmp_path / "w"),
            skip_reporting=True,
            col_id="ID_REGISTRO",
        )
    regla = res.manifiesto["completar"]["id_registro"]
    assert regla[FUENTE_B]["regla"] == "col_id"
    assert regla[FUENTE_B]["columna"] == "ID_REGISTRO_FUENTE"
    assert regla[FUENTE_A]["regla"] == "fila"  # CRM no trae esa columna
    c = res.correlativa
    de_b = c[c["SRC"] == FUENTE_B]
    assert (de_b["ID_REGISTRO"] == FUENTE_B + "-" + de_b["ID_REGISTRO_FUENTE"]).all()


def test_col_id_renombrado_por_el_motor_se_resuelve(tmp_path: Path) -> None:
    """``col_nit="IDENT", col_id="IDENT"``: el motor renombra ``IDENT`` → ``NIT``.

    Se resuelve al nombre efectivo (nunca ``ValueError``) y, como el sintético
    repite el NIT en CRM (2 filas por entidad CON_NIT) y lo deja vacío en las
    SIN_NIT y singletons, ambas fuentes caen a la regla ``fila`` con el motivo
    exacto que lo explica."""
    fuentes = {
        n: f.rename(columns={"NIT": "IDENT", "RAZON_SOCIAL": "NOMBRE"})
        for n, f in conjunto_sintetico().items()
    }
    with _silencio():
        res = rl.linkage(
            fuentes,
            work_dir=str(tmp_path / "w"),
            skip_reporting=True,
            col_nit="IDENT",
            col_name="NOMBRE",
            col_id="IDENT",
        )
    assert res.validar().ok
    notas = res.manifiesto["completar"]["id_registro"]
    assert notas[FUENTE_A] == {
        "regla": "fila",
        "columna": "NIT",
        "motivo": f"col_id='NIT' en la fuente '{FUENTE_A}': 12 vacío(s)/ausente(s); "
        "no es único por fila (24 repetido(s))",
    }
    assert notas[FUENTE_B] == {
        "regla": "fila",
        "columna": "NIT",
        "motivo": f"col_id='NIT' en la fuente '{FUENTE_B}': 16 vacío(s)/ausente(s)",
    }
    assert res.correlativa["ID_REGISTRO"].str.match(r"^(CRM|ADUANAS)-F\d+$").all()


def test_validar_reporta_golden_con_id_grupo_repetido(res_linkage: ResultadoLinkage) -> None:
    roto = _copia(res_linkage)
    assert roto.golden is not None
    roto.golden = pd.concat([roto.golden, roto.golden.iloc[:1]])
    reporte = roto.validar()  # no debe lanzar nada que no sea ContratoSalidaError
    assert not reporte.ok
    assert any("ID_GRUPO repetido" in f for f in reporte.fallos)
    with pytest.raises(ContratoSalidaError):
        roto.validar(estricto=True)


def test_validar_texto_rechaza_enteros_en_object(res_linkage: ResultadoLinkage) -> None:
    roto = _copia(res_linkage)
    roto.correlativa["ID_REGISTRO"] = pd.Series(
        range(len(roto.correlativa)), index=roto.correlativa.index, dtype=object
    )
    fallos = roto.validar().fallos
    assert any("ID_REGISTRO" in f and "texto" in f for f in fallos), fallos


def test_dedupe_con_nombres_no_canonicos_documenta_las_columnas_del_usuario(
    tmp_path: Path,
) -> None:
    """``dedupe`` NO renombra: ``IDENT``/``NOMBRE`` son de la fuente y
    ``NIT``/``RAZON_SOCIAL`` son copias canónicas del motor."""
    fuentes = conjunto_sintetico()
    df = pd.concat([fuentes[FUENTE_A], fuentes[FUENTE_B]], ignore_index=True).rename(
        columns={"NIT": "IDENT", "RAZON_SOCIAL": "NOMBRE"}
    )
    with _silencio():
        res = rl.dedupe(df, col_nit="IDENT", col_name="NOMBRE", output_dir=str(tmp_path / "d"))
    c = res.correlativa
    columnas_fuente = res.manifiesto["completar"]["columnas_fuente"]
    assert columnas_fuente == [
        "IDENT",
        "NOMBRE",
        "CIUDAD",
        "SECTOR",
        "CODIGO",
        "ID_REGISTRO_FUENTE",
    ]
    assert list(c.columns[12 : 12 + len(columnas_fuente)]) == columnas_fuente
    dic = res.diccionario.set_index("columna")
    assert dic.loc["IDENT", "origen"] == "fuente" and dic.loc["NOMBRE", "origen"] == "fuente"
    assert dic.loc["NIT", "origen"] == "motor" and "IDENT" in dic.loc["NIT", "significado"]
    assert dic.loc["RAZON_SOCIAL", "origen"] == "motor"
    assert "NOMBRE" in dic.loc["RAZON_SOCIAL", "significado"]


def _paridad_golden_motor_vs_metricas(res: ResultadoLinkage) -> None:
    """El golden que dejó L5 (antes de completar) coincide, columna a columna,
    con ``metricas_de_grupo`` + ``metricas_de_calidad`` sobre su correlativa."""
    from record_linkage.golden.metricas import (
        COLUMNAS_METRICAS_GRUPO,
        metricas_de_calidad,
        metricas_de_grupo,
    )

    assert res.dir_trabajo is not None
    g5 = pd.read_parquet(Path(res.dir_trabajo) / "L5_golden" / "golden.parquet")
    c5 = pd.read_parquet(Path(res.dir_trabajo) / "L5_golden" / "correlative.parquet")
    assert res.manifiesto["completar"]["golden"]["metricas_reparadas"]["n"] == 0
    prioridad = res.manifiesto["completar"]["prioridad_fuentes"]
    assert prioridad  # viene del Orchestrator, no se recalcula en api.py
    esperado = g5.set_index("ID_GRUPO").sort_index()
    grupo = metricas_de_grupo(c5, prioridad).sort_index()
    for col in COLUMNAS_METRICAS_GRUPO:
        pd.testing.assert_series_equal(
            esperado[col].astype(grupo[col].dtype), grupo[col], check_names=False, obj=col
        )
    calidad = metricas_de_calidad(esperado)
    pd.testing.assert_series_equal(
        esperado["CONFIDENCE_SCORE"], calidad["CONFIDENCE_SCORE"], obj="CONFIDENCE_SCORE"
    )
    pd.testing.assert_series_equal(
        esperado["REQUIRES_REVIEW"].astype(bool),
        calidad["REQUIRES_REVIEW"],
        check_dtype=False,
        obj="REQUIRES_REVIEW",
    )


def test_paridad_metricas_con_el_motor_sintetico(res_linkage: ResultadoLinkage) -> None:
    _paridad_golden_motor_vs_metricas(res_linkage)


def test_paridad_metricas_con_el_motor_p2(tmp_path: Path) -> None:
    ruta = Path(__file__).parent / "data_sintetica" / "dataset_sintetico_p2_extra_features.csv"
    df = pd.read_csv(ruta, dtype=str, keep_default_na=False)
    with _silencio():
        res = rl.linkage(
            {"P2": df}, work_dir=str(tmp_path / "w"), skip_reporting=True, col_ciudad="CIUDAD"
        )
    _paridad_golden_motor_vs_metricas(res)


def test_definicion_de_confianza_es_la_regla_aplicada(res_linkage: ResultadoLinkage) -> None:
    from record_linkage.golden.metricas import confianza_de_grupo

    casos = pd.DataFrame(
        {
            "NIT_VARIATIONS": [0, 1, 3],  # singleton sin NIT · NIT único 2 fuentes · 3 NITs
            "SOURCES_COUNT": [1, 2, 2],
            "RECORD_COUNT": [1, 2, 3],
        }
    )
    assert list(confianza_de_grupo(casos)) == ["MEDIA", "ALTA", "BAJA"]
    texto = contrato._DEFINICION_CONFIANZA
    assert "NIT_VARIATIONS = 1" in texto and "SOURCES_COUNT" in texto and "RECORD_COUNT" in texto
    assert "NIT_VARIATIONS <= 2" in texto and "RECORD_COUNT <= 5" in texto and "F2.12" in texto
    assert "un solo registro sin" not in texto
    dic = res_linkage.diccionario
    fila = dic[(dic["tabla"] == "golden") & (dic["columna"] == "SOURCES_LIST")]
    assert "'|'" in fila["significado"].iloc[0] and "coma" not in fila["significado"].iloc[0]
    assert res_linkage.golden is not None
    varias = res_linkage.golden.loc[res_linkage.golden["SOURCES_COUNT"] > 1, "SOURCES_LIST"]
    assert len(varias) and varias.str.contains("|", regex=False).all()


def test_colision_no_recuperable_avisa(tmp_path: Path) -> None:
    correl = _correl_dos_grupos()
    fuentes = {"RUES": ["NIT", "RAZON_SOCIAL", "SRC"], "CRM": ["NIT", "RAZON_SOCIAL"]}
    with pytest.warns(UserWarning, match="SRC") as avisos:
        _, _, reporte = completar_correlativa(correl, None, tmp_path, None, fuentes)
    assert reporte.colisiones_no_recuperables == ["SRC"]
    assert "SRC_FUENTE" in str(avisos[0].message)


def test_manifiesto_registra_el_costo_de_la_huella(resultado: ResultadoLinkage) -> None:
    """En las tres rutas, ``segundos_huellas`` es la suma redondeada de las
    huellas por entrada y las métricas la copian tal cual."""
    entradas = resultado.manifiesto["entradas"]
    assert entradas, "el manifiesto debe listar las entradas"
    suma = round(sum(float(e["segundos_huella"]) for e in entradas.values()), 3)
    assert resultado.manifiesto["segundos_huellas"] == suma
    assert resultado.metricas["segundos_manifiesto"] == resultado.manifiesto["segundos_huellas"]


def test_completar_correlativa_no_muta_la_entrada(tmp_path: Path) -> None:
    """``completar_correlativa`` es pública: el DataFrame del llamador queda intacto."""
    correl = _correl_dos_grupos()
    correl["ID_GRUPO"] = ["C0", "C0", "S1", "C0"]  # texto: obliga a recodificar
    columnas_antes = list(correl.columns)
    copia = correl.copy()
    fuentes = {"RUES": ["NIT", "RAZON_SOCIAL"], "CRM": ["NIT", "RAZON_SOCIAL"]}
    completada, _, reporte = completar_correlativa(correl, None, tmp_path, None, fuentes)
    assert list(correl.columns) == columnas_antes
    pd.testing.assert_frame_equal(correl, copia)
    assert reporte.id_grupo["recodificado"] is True
    assert correl["ID_GRUPO"].tolist() == ["C0", "C0", "S1", "C0"]
    assert pd.api.types.is_integer_dtype(completada["ID_GRUPO"])
    assert "ID_REGISTRO" in completada.columns and "ID_REGISTRO" not in correl.columns


def test_diccionario_declara_renombre_canonico(tmp_path: Path) -> None:
    """``col_name="NOMBRE"``, ``col_nit="IDENT"``: el motor entrega ``RAZON_SOCIAL`` y
    ``NIT``; el diccionario y el manifiesto lo dicen, no «sin cambios»."""
    fuentes = {
        n: f.rename(columns={"NIT": "IDENT", "RAZON_SOCIAL": "NOMBRE"})
        for n, f in conjunto_sintetico().items()
    }
    with _silencio():
        res = rl.linkage(
            fuentes,
            work_dir=str(tmp_path / "w"),
            skip_reporting=True,
            col_nit="IDENT",
            col_name="NOMBRE",
        )
    assert res.manifiesto["completar"]["renombres_canonicos"] == {
        "NOMBRE": "RAZON_SOCIAL",
        "IDENT": "NIT",
    }
    assert "NOMBRE" not in res.correlativa.columns and "IDENT" not in res.correlativa.columns
    dic = res.diccionario
    correl = dic[dic["tabla"] == "correlativa"].set_index("columna")
    assert correl.loc["RAZON_SOCIAL", "origen"] == "fuente"
    assert correl.loc["RAZON_SOCIAL", "significado"] == (
        "Columna 'NOMBRE' de la fuente, renombrada a la canónica RAZON_SOCIAL por el motor "
        "(col_name='NOMBRE')."
    )
    assert correl.loc["NIT", "significado"] == (
        "Columna 'IDENT' de la fuente, renombrada a la canónica NIT por el motor (col_nit='IDENT')."
    )
    assert "sin cambios" not in correl.loc["RAZON_SOCIAL", "significado"]
    # Sin renombre, el texto sigue siendo el de siempre.
    res_sin = _correr_linkage(tmp_path / "sin")
    assert res_sin.manifiesto["completar"]["renombres_canonicos"] == {}
    dic_sin = res_sin.diccionario.set_index(["tabla", "columna"])
    assert dic_sin.loc[("correlativa", "RAZON_SOCIAL"), "significado"] == (
        "Columna de la fuente, sin cambios."
    )


@pytest.mark.parametrize("dtype", ["object", "string", "string[pyarrow]", None])
def test_el_diccionario_nombra_el_tipo_logico_no_la_representacion(dtype: str | None) -> None:
    """``string`` y ``large_string`` son el mismo tipo lógico: el diccionario dice
    ``string`` sea cual sea la representación (pandas 2 `object`, pandas 3 `str`,
    `string[pyarrow]`). Antes el ``diccionario.csv`` escrito desde memoria y el
    recalculado tras ``leer_resultado`` discrepaban según la versión de pandas."""
    from record_linkage.contrato import _tipo_de_serie

    serie = (
        pd.Series(["ACME SAS", "GLOBEX"], dtype=dtype)
        if dtype
        else pd.Series(["ACME SAS", "GLOBEX"])
    )
    assert _tipo_de_serie(serie) == "string"
    assert _tipo_de_serie(pd.Series([1, 2])) == "int64"
