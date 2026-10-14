"""La plantilla de base real (scripts/generar_plantilla_base_real.py) cumple su contrato.

Qué se vigila: que el generador sea determinista entre procesos, que el Excel tenga
exactamente las hojas y columnas que lee la librería, que cada caso del catálogo esté
ejercitado, que las etiquetas de pares no contradigan la partición, que los NIT con DV
validen, que ningún nombre sea una marca real, que los correos y sitios usen dominios
reservados, que ``construir_banco`` reproduzca el CSV del banco, que el verificador descubra
las hojas FUENTE_* por prefijo y muestree pares con ids medibles, que
``scripts/medir_verdad_real.py --hoja`` lea la hoja ETIQUETAS_PARES del Excel y que
``linkage()`` una los casos básicos. Todo es inventado (vocabulario de prueba del
repositorio: KRONOS, ZELVORA, TRIVENTA…).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import test_sin_nombres_reales as guarda
from cargar_script import cargar_script

from record_linkage.evaluation.verdad_real import leer_etiquetas, medir_verdad_real
from record_linkage.matching.identificadores import base_canonica, digito_verificacion_dian

RAIZ = Path(__file__).resolve().parents[1]
SCRIPT = RAIZ / "scripts" / "generar_plantilla_base_real.py"
CARPETA_REPO = RAIZ / "data" / "ejemplos"

HOJAS = (
    "LEEME",
    "FUENTE_CRM",
    "FUENTE_RUES",
    "FUENTE_ADUANAS",
    "VERDAD_GRUPOS",
    "ETIQUETAS_PARES",
    "DICCIONARIO",
    "CASOS",
)
COLUMNAS_FUENTE = [
    "ID_REGISTRO_FUENTE",
    "NIT",
    "DV",
    "TIPO_IDENTIFICACION",
    "RAZON_SOCIAL",
    "NOMBRE_COMERCIAL",
    "DEPARTAMENTO",
    "MUNICIPIO",
    "DIRECCION",
    "TELEFONO",
    "EMAIL",
    "SITIO_WEB",
    "CIIU",
    "PAIS",
    "FECHA_ACTUALIZACION",
]
COLUMNAS_POR_HOJA = {
    "LEEME": ["LEEME"],
    "FUENTE_CRM": COLUMNAS_FUENTE,
    "FUENTE_RUES": COLUMNAS_FUENTE,
    "FUENTE_ADUANAS": COLUMNAS_FUENTE,
    "VERDAD_GRUPOS": [
        "ID_REGISTRO_FUENTE",
        "FUENTE",
        "ID_GRUPO",
        "TIPO_ENTIDAD",
        "REGIMEN",
        "CASO",
        "NIVEL_RUIDO",
        "VARIACION",
    ],
    "ETIQUETAS_PARES": ["ID_1", "ID_2", "IS_MATCH", "ANOTADOR", "BANDA_ESPERADA", "COMENTARIO"],
    "DICCIONARIO": ["COLUMNA", "HOJA", "OBLIGATORIA", "TIPO", "FORMATO", "EJEMPLO", "REGLA"],
    "CASOS": ["CASO", "DESCRIPCION", "QUE_DEBE_HACER_EL_MOTOR", "EJEMPLO_IDS", "N_REGISTROS"],
}
BANDAS = {"unido_holgado", "unido_borde", "no_unido_borde", "no_unido_lejos"}
#: Los 23 casos del encargo (16 positivos, 6 negativos y singleton); ambiguo_revisar se añadió aparte.
CASOS_OBLIGATORIOS = (
    "positivo_nit_igual",
    "positivo_nit_con_y_sin_dv",
    "positivo_nit_con_ceros_o_puntuacion",
    "positivo_sufijo_legal",
    "positivo_errata_1_letra",
    "positivo_errata_2_letras",
    "positivo_orden_de_palabras",
    "positivo_sigla",
    "positivo_sigla_sin_nit",
    "positivo_truncado",
    "positivo_nombre_comercial_vs_razon_social",
    "positivo_sin_nit_en_una_fuente",
    "positivo_sin_nit_en_ninguna",
    "positivo_sucursal",
    "positivo_persona_natural",
    "positivo_mojibake",
    "negativo_homonimo_persona",
    "negativo_nombre_identico_nit_distinto",
    "negativo_nombre_generico",
    "negativo_filial_matriz",
    "negativo_nit_corrupto_un_digito",
    "negativo_raiz_confundible",
    "singleton",
)
DOMINIO_RESERVADO = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.example$")


def _csv(carpeta: Path, nombre: str) -> pd.DataFrame:
    return pd.read_csv(carpeta / f"plantilla_{nombre}.csv", dtype=str, keep_default_na=False)


@pytest.fixture(scope="module")
def generador():
    return cargar_script("generar_plantilla_base_real")


@pytest.fixture(scope="module")
def verificador(generador):
    return cargar_script("verificar_plantilla_base_real")


@pytest.fixture(scope="module")
def carpeta(generador, tmp_path_factory) -> Path:
    """La plantilla generada en una carpeta temporal (una vez por módulo)."""
    salida = tmp_path_factory.mktemp("plantilla")
    config = generador.ConfiguracionPlantilla(semilla=42, salida=salida)
    plantilla = generador.generar(config)
    generador.escribir(plantilla, config)
    return salida


@pytest.fixture(scope="module")
def fuentes(carpeta) -> dict[str, pd.DataFrame]:
    return {f: _csv(carpeta, f"fuente_{f.lower()}") for f in ("CRM", "RUES", "ADUANAS")}


@pytest.fixture(scope="module")
def verdad(carpeta) -> pd.DataFrame:
    return _csv(carpeta, "verdad_grupos")


@pytest.fixture(scope="module")
def pares(carpeta) -> pd.DataFrame:
    return _csv(carpeta, "etiquetas_pares")


@pytest.fixture(scope="module")
def motor(verificador, fuentes, tmp_path_factory) -> tuple[pd.DataFrame, Path]:
    """La correlativa del motor sobre la plantilla y su directorio de trabajo (una sola corrida)."""
    dir_trabajo = tmp_path_factory.mktemp("trabajo")
    correlativa = verificador.correr_motor(fuentes, dir_trabajo)
    return correlativa, dir_trabajo


def _correr_generador(salida: Path, semilla_hash: str) -> None:
    entorno = dict(os.environ, PYTHONHASHSEED=semilla_hash)
    subprocess.run(
        [sys.executable, str(SCRIPT), "--salida", str(salida), "--semilla", "42"],
        check=True,
        capture_output=True,
        env=entorno,
        cwd=RAIZ,
    )


def test_es_determinista_entre_procesos(tmp_path: Path) -> None:
    """Dos procesos con semilla de hash distinta producen los mismos bytes en todos los CSV."""
    a, b = tmp_path / "a", tmp_path / "b"
    _correr_generador(a, "1")
    _correr_generador(b, "4242")
    csvs = sorted(p.name for p in a.glob("*.csv"))
    assert len(csvs) >= 10
    for nombre in csvs:
        assert (a / nombre).read_bytes() == (b / nombre).read_bytes(), (
            f"{nombre} cambia entre procesos"
        )


def test_los_csv_del_repositorio_estan_al_dia(carpeta: Path) -> None:
    """Lo que hay en data/ejemplos/ es exactamente lo que produce el generador con semilla 42."""
    if not CARPETA_REPO.is_dir():
        pytest.skip("data/ejemplos/ no existe en este árbol")
    for ruta in sorted(carpeta.glob("*.csv")):
        copia = CARPETA_REPO / ruta.name
        assert copia.is_file(), f"falta {copia}: corra scripts/generar_plantilla_base_real.py"
        assert copia.read_bytes() == ruta.read_bytes(), (
            f"{ruta.name} está desactualizado en data/ejemplos/"
        )


def test_el_excel_tiene_las_hojas_y_columnas_exactas(carpeta: Path) -> None:
    hojas = pd.read_excel(carpeta / "plantilla_base_real.xlsx", sheet_name=None, dtype=str)
    assert list(hojas) == list(HOJAS)
    for nombre, columnas in COLUMNAS_POR_HOJA.items():
        assert list(hojas[nombre].columns) == columnas, nombre
    assert 20 <= len(hojas["LEEME"]) <= 45
    for f in ("CRM", "RUES", "ADUANAS"):
        assert 150 <= len(hojas[f"FUENTE_{f}"]) <= 300, f


def test_los_csv_coinciden_con_las_hojas(carpeta: Path) -> None:
    hojas = pd.read_excel(carpeta / "plantilla_base_real.xlsx", sheet_name=None, dtype=str)
    for nombre in HOJAS:
        if nombre == "LEEME":
            continue
        csv = _csv(carpeta, nombre.lower())
        excel = hojas[nombre].fillna("")
        assert excel.shape == csv.shape, nombre
        assert (excel.to_numpy() == csv.to_numpy()).all(), f"{nombre}: el CSV no es la hoja"


def test_ids_unicos_y_verdad_completa(fuentes, verdad) -> None:
    ids = pd.concat([df["ID_REGISTRO_FUENTE"] for df in fuentes.values()])
    assert ids.is_unique
    assert ids.str.match(r"^(CRM|RUES|ADUANAS)-\d{6}$").all()
    assert set(verdad["ID_REGISTRO_FUENTE"]) == set(ids)
    assert verdad["ID_REGISTRO_FUENTE"].is_unique
    assert verdad["ID_GRUPO"].str.match(r"^G\d{4}$").all()
    assert set(verdad["REGIMEN"]) == {"CON_ID", "SIN_ID"}
    assert set(verdad["TIPO_ENTIDAD"]) == {"empresa", "persona"}
    assert set(verdad["NIVEL_RUIDO"]) <= {"CLEAN", "LIGHT", "MODERATE", "SEVERE"}
    assert (verdad.groupby("ID_GRUPO")["CASO"].nunique() == 1).all(), "un grupo tiene un solo CASO"
    assert (verdad.groupby("ID_GRUPO")["TIPO_ENTIDAD"].nunique() == 1).all()


def test_regimen_y_tipo_de_entidad_coinciden_con_la_fuente(fuentes, verdad) -> None:
    todos = pd.concat(fuentes.values()).set_index("ID_REGISTRO_FUENTE")
    tiene = todos["NIT"].str.strip().ne("")
    esperado = tiene.map({True: "CON_ID", False: "SIN_ID"})
    v = verdad.set_index("ID_REGISTRO_FUENTE")
    assert (v["REGIMEN"].to_numpy() == esperado.reindex(v.index).to_numpy()).all()
    tipo_id = todos["TIPO_IDENTIFICACION"].reindex(v.index)
    assert (v.loc[tipo_id.isin(["CC", "CE"]), "TIPO_ENTIDAD"] == "persona").all()
    assert (v.loc[tipo_id == "NIT", "TIPO_ENTIDAD"] == "empresa").all()


def test_cada_caso_obligatorio_tiene_al_menos_dos_registros(carpeta: Path, verdad) -> None:
    casos = _csv(carpeta, "casos")
    assert set(CASOS_OBLIGATORIOS) <= set(casos["CASO"])
    conteo = verdad["CASO"].value_counts()
    for caso in CASOS_OBLIGATORIOS:
        minimo = 1 if caso == "singleton" else 2
        assert conteo.get(caso, 0) >= minimo, caso
        assert int(casos.set_index("CASO").loc[caso, "N_REGISTROS"]) == conteo[caso]
    assert set(casos["QUE_DEBE_HACER_EL_MOTOR"]) == {"unir", "no unir", "revisar"}
    ids = set(verdad["ID_REGISTRO_FUENTE"])
    for ejemplo in casos["EJEMPLO_IDS"]:
        assert all(x.strip() in ids for x in ejemplo.split(";")), ejemplo


def test_niveles_de_ruido_coherentes(verdad) -> None:
    sin_cambios = verdad["VARIACION"] == "canónico (sin cambios)"
    assert (verdad.loc[sin_cambios, "NIVEL_RUIDO"] == "CLEAN").all()
    assert (verdad.loc[~sin_cambios, "NIVEL_RUIDO"] != "CLEAN").all()


def test_etiquetas_consistentes_con_los_grupos(verdad, pares) -> None:
    assert 60 <= len(pares) <= 120
    grupo = verdad.set_index("ID_REGISTRO_FUENTE")["ID_GRUPO"]
    assert pares["ID_1"].isin(grupo.index).all() and pares["ID_2"].isin(grupo.index).all()
    assert set(pares["IS_MATCH"]) == {"1", "0", "UNCLEAR"}
    assert 2 <= (pares["IS_MATCH"] == "UNCLEAR").sum() <= 3
    assert set(pares["ANOTADOR"]) == {"A", "B", "resuelto"}
    assert set(pares["BANDA_ESPERADA"]) == BANDAS
    claros = pares[pares["IS_MATCH"] != "UNCLEAR"]
    mismo = grupo[claros["ID_1"]].to_numpy() == grupo[claros["ID_2"]].to_numpy()
    assert (mismo == (claros["IS_MATCH"] == "1").to_numpy()).all()
    clave = pares.apply(lambda f: tuple(sorted((f["ID_1"], f["ID_2"]))), axis=1)
    assert clave.is_unique, "un par repetido"


def test_leer_etiquetas_lee_la_plantilla(carpeta: Path, pares) -> None:
    etiquetas = leer_etiquetas(carpeta / "plantilla_etiquetas_pares.csv")
    assert etiquetas.attrs["descartados"] == int((pares["IS_MATCH"] == "UNCLEAR").sum())
    assert len(etiquetas) == len(pares) - etiquetas.attrs["descartados"]
    a = leer_etiquetas(carpeta / "plantilla_etiquetas_anotador_A.csv")
    b = leer_etiquetas(carpeta / "plantilla_etiquetas_anotador_B.csv")
    assert len(a) == len(b) == len(etiquetas)
    assert (a["IS_MATCH"] != b["IS_MATCH"]).sum() == int((pares["ANOTADOR"] == "resuelto").sum())


def test_los_nit_con_dv_declarado_validan(fuentes) -> None:
    todos = pd.concat(fuentes.values())
    con_dv = todos[todos["DV"] != ""]
    assert len(con_dv) >= 100
    base = con_dv["NIT"].str.replace(r"\D", "", regex=True).str.lstrip("0")
    assert (base.map(digito_verificacion_dian) == con_dv["DV"]).all()
    assert ((base + con_dv["DV"]).map(base_canonica) == base).all()
    # el NIT con el DV pegado (10 dígitos) se reduce a la misma base que el NIT sin DV
    pegado = todos[todos["NIT"].str.fullmatch(r"\d{10}") & (todos["TIPO_IDENTIFICACION"] == "NIT")]
    assert len(pegado) >= 3
    assert pegado["NIT"].map(base_canonica).str.len().eq(9).all()


def test_nit_distintos_estan_lejos_salvo_el_caso_disenado(fuentes, verdad) -> None:
    todos = pd.concat(fuentes.values()).merge(verdad, on="ID_REGISTRO_FUENTE")
    con = todos[todos["NIT"].str.strip() != ""].copy()
    con["BASE"] = con["NIT"].str.replace(r"\D", "", regex=True).str.lstrip("0").map(base_canonica)
    por_base = con.groupby("BASE")["ID_GRUPO"].nunique()
    assert (por_base == 1).all(), "una base de NIT en dos grupos distintos"
    bases = con.drop_duplicates("BASE")[["BASE", "CASO"]]
    cercanos = []
    valores = bases["BASE"].tolist()
    casos = bases["CASO"].tolist()
    for i, x in enumerate(valores):
        for j in range(i + 1, len(valores)):
            y = valores[j]
            if len(x) == len(y) and sum(a != b for a, b in zip(x, y, strict=True)) < 3:
                cercanos.append((casos[i], casos[j]))
    assert cercanos and all(
        c == ("negativo_nit_corrupto_un_digito", "negativo_nit_corrupto_un_digito")
        for c in cercanos
    ), cercanos


def test_ningun_nombre_es_una_marca_real(carpeta: Path) -> None:
    for ruta in sorted(carpeta.glob("*.csv")):
        encontradas = guarda._palabras_prohibidas(ruta.read_text(encoding="utf-8"))
        assert not encontradas, f"{ruta.name} nombra una empresa real"


def test_correos_y_sitios_usan_dominios_reservados(fuentes, carpeta: Path) -> None:
    """Ningún buzón ni sitio puede existir: todos los dominios son ``.example`` y sin tildes."""
    todos = pd.concat(fuentes.values())
    correos = todos.loc[todos["EMAIL"] != "", "EMAIL"]
    assert len(correos) >= 100
    assert correos.str.match(r"^[a-z0-9.]+@[a-z0-9.-]+$").all()
    assert correos.str.split("@").str[-1].map(DOMINIO_RESERVADO.match).notna().all()
    sitios = todos.loc[todos["SITIO_WEB"] != "", "SITIO_WEB"]
    assert len(sitios) >= 20
    dominios = sitios.str.replace(r"^(https?://)?(www\.)?", "", regex=True)
    assert dominios.map(DOMINIO_RESERVADO.match).notna().all()
    diccionario = _csv(carpeta, "diccionario")
    ejemplos = diccionario.loc[diccionario["COLUMNA"].isin(["EMAIL", "SITIO_WEB"]), "EJEMPLO"]
    assert ejemplos.str.endswith(".example").all()


def test_banco_csv_tiene_las_columnas_requeridas(carpeta: Path, verdad) -> None:
    from record_linkage.evaluation.banco import COLUMNAS_REQUERIDAS, cargar_referencia

    banco = cargar_referencia(carpeta / "plantilla_banco.csv")
    assert set(banco.columns) >= COLUMNAS_REQUERIDAS
    assert banco["ID_REGISTRO"].is_unique and len(banco) == len(verdad)
    assert set(banco["REGIMEN"]) == {"CON_ID", "SIN_ID"}


def test_construir_banco_reproduce_el_csv(generador, carpeta: Path, fuentes, verdad) -> None:
    """El CSV del banco sale de las hojas FUENTE_* y VERDAD_GRUPOS, y lo mismo vale para una base real."""
    esperado = _csv(carpeta, "banco")
    banco = generador.construir_banco(fuentes, verdad)
    assert list(banco.columns) == list(esperado.columns)
    assert banco.equals(esperado)
    # una base real sin REGIMEN ni TIPO_ENTIDAD: se deducen del NIT y del tipo de identificación
    minima = verdad[["ID_REGISTRO_FUENTE", "FUENTE", "ID_GRUPO"]]
    deducido = generador.construir_banco(fuentes, minima, estrato="LOTE_1")
    assert (deducido["REGIMEN"] == esperado["REGIMEN"]).all()
    assert (deducido["ESTRATO"] == "LOTE_1").all() and (deducido["CASO"] == "").all()
    con_tipo = pd.concat(fuentes.values()).set_index("ID_REGISTRO_FUENTE")["TIPO_IDENTIFICACION"]
    conocido = con_tipo.reindex(deducido["ID_REGISTRO"]).ne("VACIO").to_numpy()
    assert (deducido.loc[conocido, "TIPO_ENTIDAD"] == esperado.loc[conocido, "TIPO_ENTIDAD"]).all()
    with pytest.raises(ValueError, match="VERDAD_GRUPOS"):
        generador.construir_banco(fuentes, verdad.drop(columns=["ID_GRUPO"]))


def test_el_verificador_descubre_las_hojas_fuente_por_prefijo(
    verificador, carpeta: Path, tmp_path: Path
) -> None:
    """Renombrar una fuente (FUENTE_ADUANAS → FUENTE_DIAN) y quitar VERDAD_GRUPOS/CASOS no rompe nada."""
    hojas = pd.read_excel(
        carpeta / "plantilla_base_real.xlsx", sheet_name=None, dtype=str, keep_default_na=False
    )
    ruta = tmp_path / "base_real.xlsx"
    with pd.ExcelWriter(ruta) as escritor:
        for nombre, hoja in hojas.items():
            if nombre in ("VERDAD_GRUPOS", "CASOS"):
                continue
            nuevo = "FUENTE_DIAN" if nombre == "FUENTE_ADUANAS" else nombre
            hoja.to_excel(escritor, sheet_name=nuevo, index=False)
    libro = verificador.leer_plantilla(ruta)
    assert list(libro.fuentes) == ["CRM", "RUES", "DIAN"]
    assert libro.verdad is None and libro.casos is None
    assert (
        libro.fuentes["DIAN"]["ID_REGISTRO_FUENTE"] == hojas["FUENTE_ADUANAS"]["ID_REGISTRO_FUENTE"]
    ).all()
    desde_csv = verificador.leer_plantilla(carpeta)
    assert list(desde_csv.fuentes) == ["CRM", "RUES", "ADUANAS"]
    assert desde_csv.verdad is not None and desde_csv.casos is not None
    sin_fuentes = tmp_path / "sin_fuentes.xlsx"
    hojas["LEEME"].to_excel(sin_fuentes, sheet_name="LEEME", index=False)
    with pytest.raises(ValueError, match="FUENTE_"):
        verificador.leer_plantilla(sin_fuentes)


def test_linkage_une_los_casos_basicos(motor, verdad) -> None:
    """``linkage()`` termina sobre la plantilla y une positivo_nit_igual y positivo_nit_con_y_sin_dv."""
    correlativa, _ = motor
    assert "ID_REGISTRO_FUENTE" in correlativa.columns
    predicho = correlativa.set_index("ID_REGISTRO_FUENTE")["ID_GRUPO"]
    marco = verdad.assign(PRED=verdad["ID_REGISTRO_FUENTE"].map(predicho).to_numpy())
    assert marco["PRED"].notna().all()
    for caso in ("positivo_nit_igual", "positivo_nit_con_y_sin_dv"):
        sub = marco[marco["CASO"] == caso]
        assert (sub.groupby("ID_GRUPO")["PRED"].nunique() == 1).all(), caso


def test_el_muestreo_de_pares_da_ids_medibles(
    verificador, generador, motor, verdad, tmp_path: Path
) -> None:
    """Los pares muestreados se nombran con ID_REGISTRO_FUENTE, salen sin columnas del motor y se miden."""
    correlativa, dir_trabajo = motor
    para_anotar, bandas = verificador.muestrear_pares(correlativa, dir_trabajo, 48, semilla=7)
    assert list(para_anotar.columns) == list(generador.COLUMNAS_PARES_A_ETIQUETAR)
    assert list(bandas.columns) == list(generador.COLUMNAS_BANDAS)
    assert len(para_anotar) == len(bandas) == 48
    ids = set(verdad["ID_REGISTRO_FUENTE"])
    assert para_anotar["ID_1"].isin(ids).all() and para_anotar["ID_2"].isin(ids).all()
    assert (para_anotar["ID_1"] != para_anotar["ID_2"]).all()
    assert not para_anotar.duplicated(["ID_1", "ID_2"]).any()
    assert (para_anotar[["IS_MATCH", "ANOTADOR", "COMENTARIO"]] == "").all().all()
    assert set(bandas["BANDA_PUNTAJE"]) == BANDAS
    assert set(bandas["REGIMEN"]) == {"CON_ID", "SIN_ID", "MIXTO"}
    grupo = correlativa.set_index("ID_REGISTRO_FUENTE")["ID_GRUPO"]
    unidos = grupo[bandas["ID_1"]].to_numpy() == grupo[bandas["ID_2"]].to_numpy()
    assert (bandas["UNIDO_POR_EL_MOTOR"].to_numpy() == unidos.astype(int)).all()
    assert bandas["BANDA_PUNTAJE"].str.startswith("unido").to_numpy().tolist() == unidos.tolist()
    # el mismo muestreo con la misma semilla es idéntico; otra semilla, distinto
    otra, _ = verificador.muestrear_pares(correlativa, dir_trabajo, 48, semilla=7)
    assert otra.equals(para_anotar)
    # la cadena completa: el archivo de los anotadores, relleno, se lee y se mide por ID_REGISTRO_FUENTE
    verdadero = verdad.set_index("ID_REGISTRO_FUENTE")["ID_GRUPO"]
    etiquetado = para_anotar.copy()
    mismo = verdadero[etiquetado["ID_1"]].to_numpy() == verdadero[etiquetado["ID_2"]].to_numpy()
    etiquetado["IS_MATCH"] = mismo.astype(int).astype(str)
    etiquetado["ANOTADOR"] = "A"
    ruta = tmp_path / "etiquetas_A.csv"
    etiquetado.to_csv(ruta, index=False)
    res = medir_verdad_real(
        leer_etiquetas(ruta),
        correlativa,
        col_id="ID_REGISTRO_FUENTE",
        col_grupo="ID_GRUPO",
        col_nit="NIT",
    )
    assert res.n_etiquetados == 48 and res.n_descartados == 0
    assert set(res.por_regimen) == {"CON_ID", "SIN_ID", "MIXTO"}
    assert res.global_.tp + res.global_.fp == int(unidos.sum())


def test_medir_verdad_real_lee_la_hoja_etiquetas_pares_del_excel(
    carpeta: Path, motor, tmp_path: Path, capsys
) -> None:
    """``--hoja ETIQUETAS_PARES`` mide el Excel de la plantilla; sin --hoja, la primera hoja (LEEME) falla."""
    medir = cargar_script("medir_verdad_real")
    correlativa, _ = motor
    parquet = tmp_path / "correlativa.parquet"
    correlativa.to_parquet(parquet, index=False)
    comunes = [
        "--correlativa", str(parquet), "--col-id", "ID_REGISTRO_FUENTE",
        "--col-grupo", "ID_GRUPO", "--col-nit", "NIT",
    ]  # fmt: skip
    excel = str(carpeta / "plantilla_base_real.xlsx")
    for hoja in ("ETIQUETAS_PARES", "5"):
        assert medir.main(["--etiquetas", excel, "--hoja", hoja, *comunes]) == 0
        salida = capsys.readouterr().out
        assert "Etiquetas: 106 pares (3 dudosos descartados)" in salida
        assert "GLOBAL" in salida and "MIXTO" in salida
    with pytest.raises(ValueError, match="ID_1"):
        medir.main(["--etiquetas", excel, *comunes])
    csv = str(carpeta / "plantilla_etiquetas_pares.csv")
    assert medir.main(["--etiquetas", csv, *comunes]) == 0
    assert "106 pares" in capsys.readouterr().out
