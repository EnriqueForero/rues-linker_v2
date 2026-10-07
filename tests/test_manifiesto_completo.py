"""Diccionario y manifiesto únicos (F1.12): ``config_auditoria_*`` se funde en ``manifest.json``.

Qué exige
---------
Tras ``linkage(carpeta_salida=...)`` sobre el conjunto sintético de
``test_contrato_salida`` (64 filas inventadas, dos fuentes) con L6 activo:

1. ``manifest.json`` tiene TODOS los campos del estándar (``CAMPOS_MANIFEST``,
   lista fija) y cada uno es coherente con la corrida:
   ``version`` es la versión real del paquete (``importlib.metadata``, no un
   «8.5» fijo); ``insumos`` trae huella SHA-256, filas (== N) y columnas por
   fuente; ``parametros`` trae la llamada y los parámetros EFECTIVOS del motor
   (perfil, LSH, scoring, pesos, prioridad de fuentes) leídos del perfil que
   corrió; ``prioridad_fuentes`` es la REAL del golden (la regla del
   ``Orchestrator``: claves de ``source_quality_weights`` o, si el perfil no
   las trae —``produccion_estandar`` no las trae—, el orden de las fuentes),
   no un ``{}`` leído de una clave que el perfil no usa; ``tiempos_por_fase``
   y ``rss_por_fase`` son los de ``_trabajo/manifest.json`` (incluido L6);
   ``metricas`` sale de la verdad en disco (``candidates.db``, ``scored.db``).
2. ``diccionario.csv`` se genera SOLO desde ``contrato.diccionario``: una fila
   por columna real de cada tabla, con ``alias_es``, y las columnas de la
   fuente con origen ``fuente`` y significado «de la fuente».
3. ``config_auditoria.json`` (nombre estable, sin marca de tiempo) queda como
   ALIAS de v1 en ``_trabajo/L6_reporting/``: ``vease: "manifest.json"``, el
   mismo bloque de parámetros que el manifiesto (una regla, una vez),
   ``DeprecationWarning`` una vez por proceso; el ``.txt`` desaparece.
4. El contrato de L6 ya no exige ``config_auditoria_*.json``: lo que
   reproduce la corrida vive en el manifiesto del estándar.

Cada corrida con L6 cuesta ≈ 20 s: una sola, de módulo. Las empresas son
inventadas; ningún dato licenciado entra aquí.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import warnings
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from test_contrato_salida import _silencio, conjunto_sintetico

import record_linkage as rl
from record_linkage import contrato
from record_linkage.api import _huella_dataset
from record_linkage.config.auditoria import (
    CLAVES_LSH,
    CLAVES_SCORING,
    ParametrosMotor,
    parametros_motor,
    prioridad_del_perfil,
)
from record_linkage.config.profiles import PERFILES_BASE
from record_linkage.evaluation.banco import (
    _contar_filas_sqlite,
    _fases_desde_manifiesto,
    _rss_por_fase,
)
from record_linkage.exporters.escritor import VERSION_RETIRO_ALIAS_V1, Manifiesto, leer_resultado
from record_linkage.pipeline.metricas import (
    CLAVES_METRICAS,
    RUTA_CANDIDATES_DB,
    RUTA_SCORED_DB,
    metricas_de_corrida,
)
from record_linkage.pipeline.orchestrator import Orchestrator
from record_linkage.reporting import contrato_l6, strategies

PERFIL = "produccion_estandar"
NOMBRE = "completo"

#: Los campos del estándar, lista fija (ESTANDAR_SALIDA: contrato, versión,
#: huellas de insumos y artefactos, parámetros, conteos, invariantes, tiempos
#: y RAM por fase, artefactos omitidos).
CAMPOS_MANIFEST: tuple[str, ...] = (
    "contrato",
    "version",
    "nombre",
    "marca_tiempo",
    "insumos",
    "artefactos",
    "parametros",
    "conteos",
    "invariantes",
    "metricas",
    "tiempos_por_fase",
    "rss_por_fase",
    "omitidos",
    "renombres",
)
CAMPOS_INSUMO: tuple[str, ...] = ("huella", "filas", "columnas")
CAMPOS_PARAMETROS: tuple[str, ...] = (
    "llamada",
    "perfil",
    "lsh",
    "scoring",
    "pesos",
    "prioridad_fuentes",
)
CAMPOS_MOTOR: tuple[str, ...] = CAMPOS_PARAMETROS[1:]
ALIAS_AUDITORIA = "config_auditoria.json"


def _exigir_l6() -> None:
    faltan = [m for m in ("matplotlib", "seaborn") if importlib.util.find_spec(m) is None]
    if faltan:
        pytest.fail(f"Faltan {faltan}: L6 omitiría sus PNG en silencio.", pytrace=False)


@pytest.fixture(scope="module")
def corrida(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    _exigir_l6()
    raiz = tmp_path_factory.mktemp("manifiesto")
    fuentes = conjunto_sintetico()
    strategies._AUDITORIA_V1_AVISADA = False
    with _silencio(), warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        res = rl.linkage(
            fuentes,
            carpeta_salida=raiz,
            nombre=NOMBRE,
            skip_reporting=False,
            col_ciudad="CIUDAD",
            col_id="CODIGO",
        )
    carpeta = next(p for p in raiz.iterdir() if p.name.endswith(f"_{NOMBRE}"))
    man = json.loads((carpeta / "manifest.json").read_text(encoding="utf-8"))
    trabajo = json.loads((carpeta / "_trabajo" / "manifest.json").read_text(encoding="utf-8"))
    return {
        "res": res,
        "fuentes": fuentes,
        "carpeta": carpeta,
        "man": man,
        "trabajo": trabajo,
        "avisos": list(avisos),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. manifest.json: todos los campos, coherentes con la corrida
# ─────────────────────────────────────────────────────────────────────────────


def test_manifest_tiene_todos_los_campos_del_estandar(corrida: dict[str, Any]) -> None:
    man = corrida["man"]
    faltan = [c for c in CAMPOS_MANIFEST if c not in man]
    assert faltan == [], f"faltan en manifest.json: {faltan}"
    for fuente, insumo in man["insumos"].items():
        assert set(CAMPOS_INSUMO) <= set(insumo), fuente
    faltan = [c for c in CAMPOS_PARAMETROS if c not in man["parametros"]]
    assert faltan == [], f"faltan en manifest.json → parametros: {faltan}"
    # La prioridad vive bajo parametros (es un parámetro del golden), no suelta.
    assert "prioridad_fuentes" not in man
    assert isinstance(Manifiesto.desde_dict(man), Manifiesto)


def test_version_es_la_real_del_paquete(corrida: dict[str, Any]) -> None:
    man = corrida["man"]
    assert man["version"] == rl.__version__ == version("rues-linker")
    assert man["version"] not in ("8.5", "0.0.0+sin.instalar")
    assert man["contrato"] == contrato.VERSION_CONTRATO


def test_insumos_traen_huella_filas_y_columnas_por_fuente(corrida: dict[str, Any]) -> None:
    man, fuentes = corrida["man"], corrida["fuentes"]
    assert set(man["insumos"]) == set(fuentes)
    for nombre, df in fuentes.items():
        insumo = man["insumos"][nombre]
        assert insumo["filas"] == len(df)
        assert insumo["columnas"] == list(df.columns)
        # SHA-256 (16 hex) del esquema y el contenido: la misma huella de F1.5.
        assert insumo["huella"] == _huella_dataset(df)
        assert len(insumo["huella"]) == 16 and int(insumo["huella"], 16) >= 0
    assert man["conteos"]["filas"] == sum(len(df) for df in fuentes.values())


def test_parametros_del_motor_son_los_del_perfil_que_corrio(corrida: dict[str, Any]) -> None:
    man, res = corrida["man"], corrida["res"]
    parametros = man["parametros"]
    perfil = PERFILES_BASE[PERFIL]
    # La llamada, tal cual (es lo que hash_parametros resume).
    assert parametros["llamada"] == res.manifiesto["parametros"]
    assert parametros["llamada"]["profile"] == PERFIL
    assert parametros["perfil"] == PERFIL
    assert parametros["lsh"] == {k: perfil[k] for k in CLAVES_LSH}
    assert parametros["scoring"] == {k: perfil[k] for k in CLAVES_SCORING}
    assert parametros["pesos"] == perfil["weights"]
    # Y en memoria, el mismo bloque (es lo que el escritor copia).
    assert res.manifiesto["configuracion"] == {k: parametros[k] for k in CAMPOS_MOTOR}


def test_prioridad_de_fuentes_es_la_real_del_golden(corrida: dict[str, Any]) -> None:
    man, fuentes, res = corrida["man"], corrida["fuentes"], corrida["res"]
    perfil = PERFILES_BASE[PERFIL]
    # produccion_estandar no trae source_quality_weights: la regla del
    # Orchestrator cae al orden de las fuentes. Antes la auditoría escribía
    # {} (leía una clave vacía) mientras el golden usaba ese orden.
    assert "source_quality_weights" not in perfil
    esperada = prioridad_del_perfil(perfil) or list(fuentes)
    assert man["parametros"]["prioridad_fuentes"] == esperada == list(fuentes)
    assert (
        man["parametros"]["prioridad_fuentes"] == res.manifiesto["completar"]["prioridad_fuentes"]
    )


def test_parametros_motor_lee_la_prioridad_de_la_clave_que_el_perfil_usa() -> None:
    config = {
        "profile": "produccion_calibrada",
        "profiles": {"produccion_calibrada": PERFILES_BASE["produccion_calibrada"]},
    }
    pm = parametros_motor(config)
    assert isinstance(pm, ParametrosMotor)
    assert pm.perfil == "produccion_calibrada"
    assert list(pm.prioridad_fuentes) == list(
        PERFILES_BASE["produccion_calibrada"]["source_quality_weights"]
    )
    assert pm.lsh["lsh_permutations"] == 252 and pm.scoring["score_threshold"] == 0.6
    # La real (la del Orchestrator) manda cuando se pasa.
    assert parametros_motor(config, prioridad_fuentes=["B", "A"]).prioridad_fuentes == ("B", "A")
    # Un perfil sin pesos de fuente no inventa prioridad.
    sin = {"profile": PERFIL, "profiles": {PERFIL: PERFILES_BASE[PERFIL]}}
    assert parametros_motor(sin).prioridad_fuentes == ()
    assert set(pm.a_dict()) == set(CAMPOS_MOTOR)


def test_tiempos_y_rss_por_fase_son_los_de_trabajo(corrida: dict[str, Any]) -> None:
    man, trabajo = corrida["man"], corrida["trabajo"]
    assert man["tiempos_por_fase"] == _fases_desde_manifiesto(trabajo)
    assert man["rss_por_fase"] == _rss_por_fase(trabajo)
    assert set(man["tiempos_por_fase"]) == {
        "L1_prep",
        "L2_lsh_candidates",
        "L3_scoring",
        "L4_clustering",
        "L5_golden",
        "L6_reporting",
    }


def test_metricas_salen_de_la_verdad_en_disco(corrida: dict[str, Any]) -> None:
    man, carpeta = corrida["man"], corrida["carpeta"]
    trabajo = carpeta / "_trabajo"
    metricas = man["metricas"]
    candidatos = _contar_filas_sqlite(trabajo / RUTA_CANDIDATES_DB, "candidate_pairs")
    pares = _contar_filas_sqlite(trabajo / RUTA_SCORED_DB, "scored_pairs")
    assert candidatos and pares
    assert metricas["candidatos"] == candidatos
    assert metricas["pares_puntuados"] == pares
    conteos = man["conteos"]
    assert metricas["tasa_reduccion"] == pytest.approx(1 - conteos["grupos"] / conteos["filas"])
    assert metricas["rss_pico_mib"] == max(man["rss_por_fase"].values())
    assert metricas["segundos_total"] == pytest.approx(sum(man["tiempos_por_fase"].values()))
    assert metricas["n_registros"] == conteos["filas"]
    assert metricas["grupos_multifuente"] >= 1  # CRM y ADUANAS comparten entidades
    assert 0.0 <= metricas["confianza_media"] <= 1.0


def test_metricas_del_alias_y_del_manifiesto_son_el_mismo_bloque(corrida: dict[str, Any]) -> None:
    """Una regla, una vez: ``config_auditoria.json → metricas`` (lo que
    ``Orchestrator._build_metrics`` entrega a L6) y ``manifest.json → metricas``
    (el escritor) salen de ``pipeline.metricas.metricas_de_corrida`` y dicen
    la misma cifra clave a clave. Solo ``segundos_total`` y ``rss_pico_mib``
    pueden diferir, porque L6 escribe el alias antes de cerrar su propia fase."""
    man, carpeta = corrida["man"], corrida["carpeta"]
    alias = json.loads(
        (carpeta / "_trabajo" / "L6_reporting" / ALIAS_AUDITORIA).read_text(encoding="utf-8")
    )
    metricas_alias, metricas_man = alias["metricas"], man["metricas"]
    assert set(CLAVES_METRICAS) <= set(metricas_alias)
    assert set(CLAVES_METRICAS) <= set(metricas_man)
    for clave in CLAVES_METRICAS:
        if clave in ("segundos_total", "rss_pico_mib"):
            continue
        assert metricas_alias[clave] == metricas_man[clave], clave
    sin_l6 = {f: s for f, s in man["tiempos_por_fase"].items() if f != "L6_reporting"}
    assert metricas_alias["segundos_total"] == pytest.approx(sum(sin_l6.values()), abs=0.05)
    assert metricas_alias["rss_pico_mib"] <= metricas_man["rss_pico_mib"] + 0.05
    # Y los alias en inglés de L6 se DERIVAN del bloque, no se recalculan.
    assert metricas_alias["reduction_rate"] == metricas_alias["tasa_reduccion"]
    assert metricas_alias["multi_source_groups"] == metricas_alias["grupos_multifuente"]
    assert metricas_alias["avg_confidence"] == metricas_alias["confianza_media"]
    assert metricas_alias["median_confidence"] == metricas_alias["confianza_mediana"]
    assert metricas_alias["candidates_found"] == metricas_alias["candidatos"]
    assert metricas_alias["pairs_scored"] == metricas_alias["pares_puntuados"]


def test_metricas_de_corrida_es_la_regla_unica_y_build_metrics_deriva_de_ella(
    tmp_path: Path,
) -> None:
    """La función pura acepta las dos grafías del conteo de fuentes
    (``SOURCES_COUNT`` del contrato y ``SOURCE_COUNT`` heredado), devuelve
    ``None`` —nunca 0— sin ``_trabajo/``, y ``_build_metrics`` (instancia
    parcial, como en los fixtures de L6) deriva sus claves en inglés de ella."""
    golden = pd.DataFrame({"SOURCE_COUNT": [1, 2, 3], "CONFIDENCE_SCORE": [0.5, 1.0, 0.9]})
    correl = pd.DataFrame({"ID_GRUPO": [1, 1, 2, 3, 3, 3]})
    tiempos = {"L1_prep": 0.1, "L2_lsh_candidates": 0.25}
    rss = {"L1_prep": 100.0, "L2_lsh_candidates": 120.5}
    bloque = metricas_de_corrida(golden, correl, None, tiempos, rss)
    assert tuple(bloque) == CLAVES_METRICAS
    assert bloque["candidatos"] is None and bloque["pares_puntuados"] is None
    assert bloque["tasa_reduccion"] == pytest.approx(0.5)
    assert bloque["grupos_multifuente"] == 2
    assert bloque["confianza_media"] == pytest.approx(0.8)
    assert bloque["confianza_mediana"] == pytest.approx(0.9)
    assert bloque["segundos_total"] == pytest.approx(0.35)
    assert bloque["rss_pico_mib"] == 120.5
    # Sin golden ni filas: nada se inventa.
    vacio = metricas_de_corrida(None, correl.iloc[0:0], None, {}, {})
    assert all(v is None for v in vacio.values())

    # Lo mínimo REAL de un orquestador para _build_metrics: work_dir (sin
    # bases: los conteos son None, como en `bloque`) y logger.
    orq = object.__new__(Orchestrator)
    orq.work_dir = tmp_path
    orq.log = logging.getLogger("prueba_manifiesto")
    orq._start_time = None
    orq._phase_times = dict(tiempos)
    orq._phase_peak_rss_mib = dict(rss)
    metricas = orq._build_metrics(golden, correl)
    assert {k: metricas[k] for k in CLAVES_METRICAS} == bloque
    assert metricas["reduction_rate"] == metricas["linkage_rate"] == bloque["tasa_reduccion"]
    assert metricas["multi_source_groups"] == bloque["grupos_multifuente"]
    assert metricas["avg_confidence"] == bloque["confianza_media"]
    assert metricas["median_confidence"] == bloque["confianza_mediana"]
    assert metricas["candidates_found"] == 0 and metricas["pairs_scored"] == 0
    assert metricas["max_memory_gb"] == pytest.approx(120.5 / 1024)
    # Sin la columna de confianza ni la de fuentes, las claves en inglés NO
    # aparecen (los consumidores de v1 hacen .get(clave, 0)).
    sin_columnas = orq._build_metrics(pd.DataFrame({"ID_GRUPO": [1]}), correl)
    assert sin_columnas["grupos_multifuente"] is None and sin_columnas["confianza_media"] is None
    assert not {"multi_source_groups", "avg_confidence", "median_confidence"} & set(sin_columnas)


def test_leer_resultado_reconstruye_llamada_configuracion_y_metricas(
    corrida: dict[str, Any],
) -> None:
    man, carpeta, res = corrida["man"], corrida["carpeta"], corrida["res"]
    leido = leer_resultado(carpeta)
    assert leido.manifiesto["parametros"] == res.manifiesto["parametros"]
    assert leido.manifiesto["configuracion"] == res.manifiesto["configuracion"]
    assert leido.metricas["candidatos"] == man["metricas"]["candidatos"]
    assert leido.metricas["n_registros"] == man["conteos"]["filas"]


def test_manifiesto_de_f1_10_con_parametros_planos_se_lee_igual() -> None:
    """Un ``manifest.json`` escrito antes de F1.12 trae ``parametros`` PLANOS
    (la llamada) y ``prioridad_fuentes`` suelta: se sigue leyendo."""
    viejo = {
        "contrato": "1.0",
        "version": "0.23.0",
        "nombre": "x",
        "marca_tiempo": "2026-10-06T00:00:00",
        "parametros": {"fuentes": ["A"], "profile": PERFIL},
        "prioridad_fuentes": ["A"],
    }
    man = Manifiesto.desde_dict(viejo)
    assert man.llamada() == {"fuentes": ["A"], "profile": PERFIL}
    assert man.configuracion() == {}
    assert man.metricas == {}


# ─────────────────────────────────────────────────────────────────────────────
# 2. diccionario.csv: SOLO desde contrato.diccionario
# ─────────────────────────────────────────────────────────────────────────────


def _diccionario_leido(carpeta: Path) -> pd.DataFrame:
    return pd.read_csv(carpeta / "diccionario.csv", dtype="string", keep_default_na=False)


def test_diccionario_tiene_una_fila_por_columna_real_con_alias(corrida: dict[str, Any]) -> None:
    carpeta: Path = corrida["carpeta"]
    dic = _diccionario_leido(carpeta)
    assert list(dic.columns) == list(contrato.COLUMNAS_DICCIONARIO)
    leido = leer_resultado(carpeta)
    tablas = {
        "correlativa": leido.correlativa,
        "golden": leido.golden,
        "revision": leido.revision,
        "entidades_ids": pd.read_parquet(carpeta / "entidades_ids.parquet"),
    }
    assert set(dic["tabla"]) == set(tablas)
    for tabla, df in tablas.items():
        assert df is not None
        filas = dic[dic["tabla"] == tabla]
        assert list(filas["columna"]) == [str(c) for c in df.columns], tabla
        assert filas["columna"].is_unique, tabla
        assert (filas["alias_es"] != "").all(), tabla
        assert (filas["significado"] != "").all(), tabla
    assert set(dic["origen"]) <= {"motor", "fuente", "revision"}
    alias = dict(zip(dic["columna"], dic["alias_es"], strict=True))
    assert alias["PRIMARY_SOURCE"] == "FUENTE_PRINCIPAL"


def test_diccionario_explica_las_columnas_de_la_fuente(corrida: dict[str, Any]) -> None:
    dic = _diccionario_leido(corrida["carpeta"])
    correl = dic[dic["tabla"] == "correlativa"].set_index("columna")
    completar = corrida["man"]["corrida"]["completar"]
    columnas_fuente = completar["columnas_fuente"]
    assert {"SECTOR", "CODIGO", "ID_REGISTRO_FUENTE", "NIT", "RAZON_SOCIAL"} <= set(columnas_fuente)
    for col in columnas_fuente:
        assert correl.loc[col, "origen"] == "fuente", col
        assert "de la fuente" in correl.loc[col, "significado"], col
    # La colisión con el contrato queda explicada, no escondida.
    assert "renombrada por colisión" in correl.loc["ID_REGISTRO_FUENTE", "significado"]
    assert corrida["man"]["renombres"] == {"ID_REGISTRO": "ID_REGISTRO_FUENTE"}
    for col in contrato.COLUMNAS_CORRELATIVA:
        assert correl.loc[col, "origen"] == "motor", col


def test_diccionario_es_exactamente_el_de_contrato(corrida: dict[str, Any]) -> None:
    """Ningún otro módulo lo construye: el CSV es ``contrato.diccionario`` sobre
    las tablas de la carpeta, con los renombres que el manifiesto declara."""
    carpeta: Path = corrida["carpeta"]
    completar = corrida["man"]["corrida"]["completar"]
    leido = leer_resultado(carpeta)
    esperado = contrato.diccionario(
        {
            "correlativa": leido.correlativa,
            "golden": leido.golden,
            "enlaces": leido.enlaces,
            "revision": leido.revision,
            "entidades_ids": pd.read_parquet(carpeta / "entidades_ids.parquet"),
        },
        columnas_fuente=completar["columnas_fuente"],
        renombres=completar["renombres"],
        renombres_canonicos=completar["renombres_canonicos"],
    ).astype("string")
    pd.testing.assert_frame_equal(_diccionario_leido(carpeta), esperado, check_dtype=False)


# ─────────────────────────────────────────────────────────────────────────────
# 3. config_auditoria.json: alias de v1 que remite al manifiesto
# ─────────────────────────────────────────────────────────────────────────────


def test_config_auditoria_es_un_alias_estable_con_vease(corrida: dict[str, Any]) -> None:
    carpeta, man = corrida["carpeta"], corrida["man"]
    l6 = carpeta / "_trabajo" / "L6_reporting"
    assert sorted(p.name for p in l6.glob("config_auditoria*")) == [ALIAS_AUDITORIA]
    alias = json.loads((l6 / ALIAS_AUDITORIA).read_text(encoding="utf-8"))
    assert alias["vease"] == "manifest.json"
    assert VERSION_RETIRO_ALIAS_V1 in alias["aviso"]
    assert alias["version"] == man["version"]
    # El mismo bloque que el manifiesto: una regla escrita una vez.
    assert alias["parametros"] == {k: man["parametros"][k] for k in CAMPOS_MOTOR}
    # L6 escribe el alias ANTES de cerrar su propia fase: trae L1…L5.
    assert set(alias["tiempos_por_fase"]) == set(man["tiempos_por_fase"]) - {"L6_reporting"}
    for fase, segundos in alias["tiempos_por_fase"].items():
        assert man["tiempos_por_fase"][fase] == pytest.approx(segundos, abs=0.01)
    assert alias["metricas"]["total_records"] == man["conteos"]["filas"]
    assert "orchestrator_version" not in json.dumps(alias)
    # El alias también está en el manifiesto de L6 y en report_files.
    assert l6 / ALIAS_AUDITORIA in [Path(f) for f in corrida["res"].metricas["report_files"]]


def test_config_auditoria_avisa_con_deprecation_una_vez(corrida: dict[str, Any]) -> None:
    de_alias = [
        a
        for a in corrida["avisos"]
        if issubclass(a.category, DeprecationWarning) and "config_auditoria" in str(a.message)
    ]
    assert len(de_alias) == 1, [str(a.message) for a in de_alias]
    mensaje = str(de_alias[0].message)
    assert "manifest.json" in mensaje and VERSION_RETIRO_ALIAS_V1 in mensaje


# ─────────────────────────────────────────────────────────────────────────────
# 4. El contrato de L6 ya no exige la auditoría: vive en el manifiesto
# ─────────────────────────────────────────────────────────────────────────────


def test_contrato_l6_no_exige_config_auditoria() -> None:
    obligatorios = {a.patron for a in contrato_l6.ARTEFACTOS_OBLIGATORIOS}
    assert obligatorios == {
        "tabla_correlativa.parquet",
        "tabla_correlativa.csv.gz",
        "golden_records.parquet",
        "golden_records.csv.gz",
    }
    assert {"DataExportStrategy"} == contrato_l6.ESTRATEGIAS_OBLIGATORIAS
    opcionales = {a.patron: a.estrategia for a in contrato_l6.ARTEFACTOS_OPCIONALES}
    assert opcionales[ALIAS_AUDITORIA] == "ConfigAuditStrategy"
    declarados = obligatorios | set(opcionales)
    assert not any("config_auditoria_" in p or p.endswith(".txt") for p in declarados)
    assert not strategies.ConfigAuditStrategy().obligatoria
    assert not hasattr(strategies.ConfigAuditStrategy, "_write_txt_audit")
