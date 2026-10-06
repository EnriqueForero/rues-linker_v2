"""F1.8 — Las columnas de arrastre nunca se omiten en silencio en ``flujo.cruce``.

Hasta F1.8, si el parquet derramado de una fuente no cuadraba con la
correlativa, ``_adjuntar_columnas_extra`` escribía un WARNING, devolvía la
correlativa intacta y el manifiesto declaraba la unión PLANIFICADA
(``columnas_re_adjuntadas``) como si hubiera ocurrido. Aquí se exige:

* fallo ruidoso (``ColumnasArrastreError``, con qué pasó / por qué importa /
  qué hacer) cuando la alineación posicional no cuadra;
* manifiesto con la lista REAL en los TRES caminos (pandas con separación,
  pandas sin separación, DuckDB): ``columnas_arrastre = {adjuntadas,
  omitidas}``, donde ``omitidas`` dice, POR FUENTE, qué columna opcional pidió
  el usuario y la fuente no tiene (lo calcula el lector una sola vez:
  ``missing_optional``), más los choques con columnas que produce el motor.

Empresas inventadas; ningún dato real entra al repositorio.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
from apoyo_cruce import LogNulo, config_cruce

from record_linkage import SourceSpec
from record_linkage.flujo import cruce as modulo, ejecutar_cruce
from record_linkage.flujo.cruce import ColumnaOmitida, ReporteColumnasArrastre
from record_linkage.ingestion import DuckDBIngestionSettings
from record_linkage.pipeline.errores import ColumnasArrastreError


def _manifiesto(resultado) -> dict:
    return json.loads(resultado.rutas["metadatos"].read_text(encoding="utf-8"))


def _con_opcionales(spec: SourceSpec, **opcionales: str) -> SourceSpec:
    """Copia del contrato pidiendo columnas opcionales adicionales."""
    return replace(spec, optional_column_mapping={**spec.optional_column_mapping, **opcionales})


def _ajustes_duckdb(tmp_path: Path) -> DuckDBIngestionSettings:
    return DuckDBIngestionSettings(
        memory_limit="256MB", threads=2, temp_directory=tmp_path / "spill"
    )


def _con_columna_propia(spec: SourceSpec, columna: str, valor: str) -> SourceSpec:
    """Reescribe la fuente (CSV con coma) añadiendo una columna constante."""
    ruta = Path(spec.path)
    lineas = ruta.read_text(encoding="utf-8").splitlines()
    cabecera, filas = lineas[0], lineas[1:]
    ruta.write_text(
        "\n".join([f"{cabecera},{columna}", *(f"{fila},{valor}" for fila in filas)]) + "\n",
        encoding="utf-8",
    )
    return spec


# ── Camino feliz: el manifiesto dice lo que REALMENTE se adjuntó ──────


def test_manifiesto_declara_las_columnas_realmente_adjuntadas(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path
) -> None:
    resultado = ejecutar_cruce(config_cruce(fuentes_con_arrastre, tmp_path))
    corr = resultado.correlativa
    assert {"TELEFONO", "EMAIL", "DEPARTAMENTO"} <= set(corr.columns)

    parametros = _manifiesto(resultado)["parametros"]
    assert "columnas_re_adjuntadas" not in parametros, "declaraba la unión planificada, no la real"
    arrastre = parametros["columnas_arrastre"]
    assert set(arrastre["adjuntadas"]) == {"TELEFONO", "EMAIL", "DEPARTAMENTO"}
    assert arrastre["omitidas"] == []
    # La lista del manifiesto es la que de verdad está en la correlativa.
    assert set(arrastre["adjuntadas"]) <= set(corr.columns)


def test_columna_pedida_que_la_fuente_no_tiene_queda_en_omitidas_por_fuente(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path
) -> None:
    """Pedir una columna opcional inexistente no tumba la corrida, pero se DICE."""
    padron, clientes = fuentes_con_arrastre
    resultado = ejecutar_cruce(
        config_cruce([_con_opcionales(padron, SUCURSAL="sucursal"), clientes], tmp_path)
    )
    assert "SUCURSAL" not in resultado.correlativa.columns
    # El lector ya lo calculó una vez: el informe de ingesta lo expone tal cual.
    assert resultado.reportes_carga["PADRON"]["missing_optional"] == ["SUCURSAL"]
    assert resultado.reportes_carga["CLIENTES"]["missing_optional"] == []

    arrastre = _manifiesto(resultado)["parametros"]["columnas_arrastre"]
    assert set(arrastre["adjuntadas"]) == {"TELEFONO", "EMAIL", "DEPARTAMENTO"}
    assert arrastre["omitidas"] == [
        {
            "columna": "SUCURSAL",
            "fuente": "PADRON",
            "motivo": "la fuente no tiene la columna 'sucursal' pedida en optional_column_mapping",
        }
    ]


def test_columna_pedida_en_una_fuente_y_presente_en_otra_se_declara_en_ambas_listas(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path
) -> None:
    """DEPARTAMENTO llega desde CLIENTES (adjuntada) pero PADRON no la tiene:
    para PADRON la petición no se cumplió y eso también se dice."""
    padron, clientes = fuentes_con_arrastre
    resultado = ejecutar_cruce(
        config_cruce([_con_opcionales(padron, DEPARTAMENTO="depto"), clientes], tmp_path)
    )
    corr = resultado.correlativa
    assert corr.loc[corr["SRC"] == "PADRON", "DEPARTAMENTO"].isna().all()
    assert corr.loc[corr["SRC"] == "CLIENTES", "DEPARTAMENTO"].notna().all()

    arrastre = _manifiesto(resultado)["parametros"]["columnas_arrastre"]
    assert "DEPARTAMENTO" in arrastre["adjuntadas"]
    assert [(o["columna"], o["fuente"]) for o in arrastre["omitidas"]] == [
        ("DEPARTAMENTO", "PADRON")
    ]


def test_columna_pedida_con_nombre_del_motor_e_inexistente_no_se_salta(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path
) -> None:
    """Pedir ``SRC`` (que el motor produce) en una fuente que no la trae se
    saltaba en silencio porque el nombre ya estaba en la correlativa."""
    padron, clientes = fuentes_con_arrastre
    resultado = ejecutar_cruce(
        config_cruce([_con_opcionales(padron, SRC="src"), clientes], tmp_path)
    )
    assert "SRC" in resultado.correlativa.columns  # la del motor
    arrastre = _manifiesto(resultado)["parametros"]["columnas_arrastre"]
    assert "SRC" not in arrastre["adjuntadas"]
    assert [(o["columna"], o["fuente"]) for o in arrastre["omitidas"]] == [("SRC", "PADRON")]


# ── Los otros dos caminos también declaran lo real ────────────────────


def test_camino_duckdb_declara_adjuntadas_y_omitidas_reales(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path
) -> None:
    """Con DuckDB el re-adjunte lo hace ``preservar_payload``; el manifiesto
    se deriva de la correlativa ENTREGADA, no queda en ``[]``."""
    padron, clientes = fuentes_con_arrastre
    resultado = ejecutar_cruce(
        config_cruce(
            [_con_opcionales(padron, SUCURSAL="sucursal"), clientes],
            tmp_path,
            motor_ingesta="duckdb",
            duckdb_settings=_ajustes_duckdb(tmp_path),
        )
    )
    corr = resultado.correlativa
    assert {"TELEFONO", "EMAIL", "DEPARTAMENTO"} <= set(corr.columns)
    assert "SUCURSAL" not in corr.columns
    assert resultado.reportes_carga["PADRON"]["engine"] == "duckdb"
    assert resultado.reportes_carga["PADRON"]["missing_optional"] == ["SUCURSAL"]

    arrastre = _manifiesto(resultado)["parametros"]["columnas_arrastre"]
    assert set(arrastre["adjuntadas"]) == {"TELEFONO", "EMAIL", "DEPARTAMENTO"}
    assert [(o["columna"], o["fuente"]) for o in arrastre["omitidas"]] == [("SUCURSAL", "PADRON")]


def test_sin_separar_columnas_extra_el_manifiesto_sigue_siendo_real(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path
) -> None:
    padron, clientes = fuentes_con_arrastre
    resultado = ejecutar_cruce(
        config_cruce(
            [_con_opcionales(padron, SUCURSAL="sucursal"), clientes],
            tmp_path,
            separar_columnas_extra=False,
        )
    )
    corr = resultado.correlativa
    assert "TELEFONO" in corr.columns and "SUCURSAL" not in corr.columns
    arrastre = _manifiesto(resultado)["parametros"]["columnas_arrastre"]
    assert set(arrastre["adjuntadas"]) == {"TELEFONO", "EMAIL", "DEPARTAMENTO"}
    assert [(o["columna"], o["fuente"]) for o in arrastre["omitidas"]] == [("SUCURSAL", "PADRON")]


def test_sin_separar_una_columna_propia_con_nombre_del_motor_se_declara_choque_no_adjuntada(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path
) -> None:
    """Revisión r3 (media): con ``separar_columnas_extra=False`` la columna
    viaja por el motor y el motor la PISA (``SRC`` sale con PADRON/CLIENTES,
    no con el valor de la fuente); el manifiesto no puede decir «adjuntada»."""
    padron, clientes = fuentes_con_arrastre
    padron = _con_opcionales(_con_columna_propia(padron, "src", "ORIGEN_X"), SRC="src")
    resultado = ejecutar_cruce(
        config_cruce([padron, clientes], tmp_path, separar_columnas_extra=False)
    )
    corr = resultado.correlativa
    assert set(corr["SRC"]) == {"PADRON", "CLIENTES"}, "la del motor; la de la fuente se perdió"
    assert resultado.reportes_carga["PADRON"]["missing_optional"] == []

    arrastre = _manifiesto(resultado)["parametros"]["columnas_arrastre"]
    assert "SRC" not in arrastre["adjuntadas"]
    assert set(arrastre["adjuntadas"]) == {"TELEFONO", "EMAIL", "DEPARTAMENTO"}
    assert arrastre["omitidas"] == [
        {"columna": "SRC", "fuente": None, "motivo": modulo.MOTIVO_CHOQUE_CON_MOTOR}
    ]


def test_las_columnas_que_produce_el_motor_son_exactamente_las_declaradas(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path
) -> None:
    """El camino sin separación solo puede declarar un choque si SABE qué
    columnas escribe el motor; esta prueba ata la constante a lo observado:
    ni una de menos (volvería la mentira «adjuntada») ni una de más (se
    declararía un choque que no ocurrió)."""
    config = config_cruce(fuentes_con_arrastre, tmp_path)
    resultado = ejecutar_cruce(config)
    entradas_del_motor = modulo._columnas_de_motor(config)
    observadas = set(resultado.correlativa.columns) - {"TELEFONO", "EMAIL", "DEPARTAMENTO"}
    assert observadas - entradas_del_motor == set(modulo.COLUMNAS_QUE_PRODUCE_EL_MOTOR)


def test_camino_duckdb_sin_preservar_payload_declara_por_que_faltan_las_columnas(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path
) -> None:
    """Revisión r3 (baja): con ``preservar_payload=False`` las columnas pedidas
    y existentes no se entregan; ``columnas_arrastre`` lo dice, por fuente."""
    resultado = ejecutar_cruce(
        config_cruce(
            fuentes_con_arrastre,
            tmp_path,
            motor_ingesta="duckdb",
            duckdb_settings=_ajustes_duckdb(tmp_path),
            preservar_payload=False,
        )
    )
    assert not {"TELEFONO", "EMAIL", "DEPARTAMENTO"} & set(resultado.correlativa.columns)
    arrastre = _manifiesto(resultado)["parametros"]["columnas_arrastre"]
    assert arrastre["adjuntadas"] == []
    assert arrastre["omitidas"] == [
        {"columna": "TELEFONO", "fuente": "PADRON", "motivo": modulo.MOTIVO_SIN_PAYLOAD},
        {"columna": "EMAIL", "fuente": "PADRON", "motivo": modulo.MOTIVO_SIN_PAYLOAD},
        {"columna": "DEPARTAMENTO", "fuente": "CLIENTES", "motivo": modulo.MOTIVO_SIN_PAYLOAD},
    ]
    assert "preservar_payload=False" in modulo.MOTIVO_SIN_PAYLOAD


def test_cache_anterior_a_f1_8_se_relee_en_vez_de_callar_las_omitidas(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path
) -> None:
    """Una caché escrita antes de F1.8 no trae ``missing_optional``: se vuelve
    a leer el original (y se reescribe), en vez de declarar ``omitidas = []``."""
    padron, clientes = fuentes_con_arrastre
    fuentes = [_con_opcionales(padron, SUCURSAL="sucursal"), clientes]
    cache = tmp_path / "cache"
    ejecutar_cruce(config_cruce(fuentes, tmp_path, dir_procesados=cache))
    (json_padron,) = [r for r in cache.glob("PADRON__*.json")]
    viejo = json.loads(json_padron.read_text(encoding="utf-8"))
    assert viejo.pop("missing_optional") == ["SUCURSAL"]
    json_padron.write_text(json.dumps(viejo), encoding="utf-8")

    resultado = ejecutar_cruce(config_cruce(fuentes, tmp_path, dir_procesados=cache))
    assert resultado.reportes_carga["PADRON"]["missing_optional"] == ["SUCURSAL"]
    arrastre = _manifiesto(resultado)["parametros"]["columnas_arrastre"]
    assert [o["columna"] for o in arrastre["omitidas"]] == ["SUCURSAL"]
    assert "missing_optional" in json.loads(json_padron.read_text(encoding="utf-8"))


# ── Alineación que no cuadra: la corrida FALLA ────────────────────────


def test_parquet_de_arrastre_con_una_fila_de_menos_tumba_la_corrida(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Se simula el desalineamiento recortando el parquet derramado de PADRON."""
    separar_original = modulo._separar_columnas_extra

    def separar_y_recortar(marcos, config, log):
        rutas, union, longitudes = separar_original(marcos, config, log)
        ruta = rutas["PADRON"]
        pd.read_parquet(ruta).iloc[:-1].to_parquet(ruta, index=False)
        return rutas, union, longitudes

    monkeypatch.setattr(modulo, "_separar_columnas_extra", separar_y_recortar)

    with pytest.raises(ColumnasArrastreError) as info:
        ejecutar_cruce(config_cruce(fuentes_con_arrastre, tmp_path))

    texto = str(info.value)
    assert "Qué pasó" in texto and "Por qué importa" in texto and "Qué hacer" in texto
    assert "PADRON" in texto
    assert "2" in texto and "3" in texto, "debe citar filas observadas y esperadas"
    assert info.value.fuente == "PADRON"
    assert (info.value.observadas, info.value.esperadas) == (2, 3)
    # Y no hay manifiesto que diga que la unión ocurrió.
    assert not (tmp_path / "salida" / "metadatos_corrida.json").exists()


def test_total_de_extras_distinto_de_la_correlativa_falla(tmp_path: Path) -> None:
    """Cada parte cuadra con su fuente, pero la suma no cuadra con la correlativa."""
    ruta = tmp_path / "A.parquet"
    pd.DataFrame({"TELEFONO": ["1", "2", "3"]}).to_parquet(ruta, index=False)
    correlativa = pd.DataFrame({"ORIGINAL_INDEX": [0, 1], "ID_GRUPO": [1, 2]})
    with pytest.raises(ColumnasArrastreError) as info:
        modulo._adjuntar_columnas_extra(
            correlativa, {"A": ruta}, ["TELEFONO"], {"A": 3}, ["A"], LogNulo()
        )
    assert info.value.fuente is None
    assert (info.value.observadas, info.value.esperadas) == (3, 2)
    assert "Qué hacer" in str(info.value)


def test_sin_original_index_no_se_puede_alinear_y_falla(tmp_path: Path) -> None:
    ruta = tmp_path / "A.parquet"
    pd.DataFrame({"TELEFONO": ["1"]}).to_parquet(ruta, index=False)
    correlativa = pd.DataFrame({"ID_GRUPO": [1]})
    with pytest.raises(ColumnasArrastreError, match="ORIGINAL_INDEX"):
        modulo._adjuntar_columnas_extra(
            correlativa, {"A": ruta}, ["TELEFONO"], {"A": 1}, ["A"], LogNulo()
        )


def test_columna_esperada_que_no_llega_a_la_correlativa_falla_en_vez_de_callarse() -> None:
    """La regla observada es la misma para los tres caminos: si una columna
    que la fuente aportó fuera del motor no está en la entrega, no se declara
    como «omitida»: es un error."""
    with pytest.raises(ColumnasArrastreError, match="EMAIL") as info:
        modulo._reporte_arrastre_observado(
            columnas_finales=["ID_GRUPO", "TELEFONO"],
            esperadas=["TELEFONO", "EMAIL"],
            columnas_motor=["ID_GRUPO"],
            omitidas_ingesta=(),
        )
    # Revisión r3 (baja): el remedio es el de la ENTREGA (DuckDB o sin
    # separación), no el de la alineación de parquets derramados.
    texto = str(info.value)
    assert "Qué hacer: " + modulo.QUE_HACER_ENTREGA_INCOMPLETA in texto
    assert "fase L5" in texto and "payload_columns" in texto
    assert "columnas_extra" not in texto and "separar_columnas_extra" not in texto


def test_el_remedio_por_defecto_sigue_siendo_el_de_la_alineacion() -> None:
    por_defecto = ColumnasArrastreError("x")
    assert "Qué hacer: " + ColumnasArrastreError.QUE_HACER_POR_DEFECTO in str(por_defecto)
    assert "columnas_extra" in ColumnasArrastreError.QUE_HACER_POR_DEFECTO
    especifico = ColumnasArrastreError("x", que_hacer="haga tal cosa")
    assert str(especifico).endswith("Qué hacer: haga tal cosa")
    assert "columnas_extra" not in str(especifico)


# ── La función devuelve (correlativa, reporte) con la lista real ──────


def test_adjuntar_devuelve_reporte_con_adjuntadas_reales(tmp_path: Path) -> None:
    ruta = tmp_path / "A.parquet"
    pd.DataFrame({"TELEFONO": ["1", "2"], "EMAIL": ["a", "b"]}).to_parquet(ruta, index=False)
    correlativa = pd.DataFrame({"ORIGINAL_INDEX": [1, 0], "ID_GRUPO": [1, 2]})
    salida, reporte = modulo._adjuntar_columnas_extra(
        correlativa, {"A": ruta}, ["TELEFONO", "EMAIL"], {"A": 2}, ["A"], LogNulo()
    )
    assert reporte == ReporteColumnasArrastre(adjuntadas=("TELEFONO", "EMAIL"))
    assert list(salida["TELEFONO"]) == ["2", "1"], "take posicional por ORIGINAL_INDEX"
    assert reporte.como_manifiesto() == {
        "adjuntadas": ["TELEFONO", "EMAIL"],
        "omitidas": [],
    }


def test_choque_con_columna_del_motor_queda_declarado_no_callado(tmp_path: Path) -> None:
    """Si el motor ya produjo una columna con ese nombre, la de la fuente no se
    pisa — pero la omisión queda en el reporte con su motivo, no en un WARNING
    que nadie lee."""
    ruta = tmp_path / "A.parquet"
    pd.DataFrame({"TELEFONO": ["1"], "CONFIANZA": ["x"]}).to_parquet(ruta, index=False)
    correlativa = pd.DataFrame({"ORIGINAL_INDEX": [0], "ID_GRUPO": [1], "CONFIANZA": [0.9]})
    salida, reporte = modulo._adjuntar_columnas_extra(
        correlativa, {"A": ruta}, ["TELEFONO", "CONFIANZA"], {"A": 1}, ["A"], LogNulo()
    )
    assert reporte.adjuntadas == ("TELEFONO",)
    assert reporte.omitidas == (
        ColumnaOmitida(columna="CONFIANZA", motivo=modulo.MOTIVO_CHOQUE_CON_MOTOR),
    )
    assert "motor" in reporte.omitidas[0].motivo
    assert salida.loc[0, "CONFIANZA"] == 0.9, "la columna del motor no se pisa"
    assert reporte.como_manifiesto()["omitidas"] == [
        {"columna": "CONFIANZA", "fuente": None, "motivo": modulo.MOTIVO_CHOQUE_CON_MOTOR}
    ]


def test_las_omitidas_de_ingesta_viajan_al_reporte_sin_recomputarse(tmp_path: Path) -> None:
    ruta = tmp_path / "A.parquet"
    pd.DataFrame({"TELEFONO": ["1"]}).to_parquet(ruta, index=False)
    correlativa = pd.DataFrame({"ORIGINAL_INDEX": [0], "ID_GRUPO": [1]})
    de_ingesta = (ColumnaOmitida("SUCURSAL", "la fuente no la tiene", fuente="A"),)
    _salida, reporte = modulo._adjuntar_columnas_extra(
        correlativa,
        {"A": ruta},
        ["TELEFONO"],
        {"A": 1},
        ["A"],
        LogNulo(),
        omitidas_ingesta=de_ingesta,
    )
    assert reporte == ReporteColumnasArrastre(adjuntadas=("TELEFONO",), omitidas=de_ingesta)


def test_sin_parquets_derramados_el_reporte_sigue_siendo_real() -> None:
    correlativa = pd.DataFrame({"ORIGINAL_INDEX": [0], "ID_GRUPO": [1]})
    de_ingesta = (ColumnaOmitida("SUCURSAL", "la fuente no la tiene", fuente="A"),)
    salida, reporte = modulo._adjuntar_columnas_extra(
        correlativa, {}, [], {"A": 1}, ["A"], LogNulo(), omitidas_ingesta=de_ingesta
    )
    assert salida is correlativa
    assert reporte == ReporteColumnasArrastre(omitidas=de_ingesta)


def test_el_reporte_es_inmutable() -> None:
    reporte = ReporteColumnasArrastre(adjuntadas=("A",))
    with pytest.raises(AttributeError):
        reporte.adjuntadas = ("B",)  # type: ignore[misc]
    omitida = ColumnaOmitida("X", modulo.MOTIVO_CHOQUE_CON_MOTOR)
    with pytest.raises(AttributeError):
        omitida.motivo = "otro"  # type: ignore[misc]


def _es_motivo_documentado(motivo: str) -> bool:
    return motivo in {
        modulo.MOTIVO_CHOQUE_CON_MOTOR,
        modulo.MOTIVO_SIN_PAYLOAD,
    } or motivo.startswith(modulo.PREFIJO_MOTIVO_FUENTE_SIN_COLUMNA)


def test_los_motivos_que_se_producen_son_solo_los_documentados(
    fuentes_con_arrastre: list[SourceSpec], tmp_path: Path
) -> None:
    """Comportamiento, no docstring: cada productor de omisiones solo emite
    uno de los tres motivos declarados como constantes del módulo."""
    padron, clientes = fuentes_con_arrastre
    config = config_cruce([_con_opcionales(padron, SUCURSAL="sucursal"), clientes], tmp_path)
    reportes_carga = {
        "PADRON": {"missing_optional": ["SUCURSAL"]},
        "CLIENTES": {"missing_optional": []},
    }
    por_ingesta = modulo._omitidas_por_ingesta(config, reportes_carga)
    assert [o.columna for o in por_ingesta] == ["SUCURSAL"]

    reporte = modulo._reporte_arrastre_observado(
        columnas_finales=["ID_GRUPO", "SRC", "TELEFONO"],
        esperadas=["TELEFONO", "SRC"],
        columnas_motor=["ID_GRUPO", "SRC"],
        omitidas_ingesta=por_ingesta,
    )
    assert [o.columna for o in reporte.omitidas] == ["SUCURSAL", "SRC"]
    assert all(_es_motivo_documentado(o.motivo) for o in reporte.omitidas)
    assert len({o.motivo for o in reporte.omitidas}) == 2, "dos productores, dos motivos"
