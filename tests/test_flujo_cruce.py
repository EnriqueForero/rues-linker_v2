"""Flujo de alto nivel `record_linkage.flujo` (v0.14.0).

La lógica que antes vivía en celdas de notebook (preflight, copia anti-Drive,
smoke test, invariantes, exportes, metadatos) ahora es código con pruebas.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from record_linkage import ColumnType, SourceSpec
from record_linkage.flujo import (
    ConfigCruce,
    ejecutar_cruce,
    preparar_insumo_local,
    reportar_composicion,
)
from record_linkage.flujo.reportes import reportar_cruce_por_fuente
from record_linkage.ingestion import DuckDBIngestionSettings


@pytest.fixture
def fuentes_csv(tmp_path: Path) -> list[SourceSpec]:
    """Dos fuentes con encabezados, separadores y tipos DISTINTOS."""
    a = tmp_path / "padron.csv"
    a.write_text(
        "IDENT,NOMBRE_EMPRESA\n"
        "900111222,ACME COLOMBIA SAS\n"
        "800333444,BETA LTDA\n"
        "900555666,GAMA S.A.\n",
        encoding="utf-8",
    )
    b = tmp_path / "clientes.txt"
    b.write_text(
        "nit_cliente\trazon\n"
        # Mismo NIT que PADRON pero en formato 10 dígitos (base + DV correcto),
        # que es exactamente la diferencia RUES (9) vs DANE (10).
        "9001112221\tACME COLOMBIA S.A.S.\n"
        "700999888\tDELTA EU\n",
        encoding="utf-8",
    )
    return [
        SourceSpec(
            name="PADRON",
            path=a,
            column_mapping={"NIT": "IDENT", "RAZON_SOCIAL": "NOMBRE_EMPRESA"},
            delimiter=",",
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
        SourceSpec(
            name="CLIENTES",
            path=b,
            column_mapping={"NIT": "nit_cliente", "RAZON_SOCIAL": "razon"},
            delimiter="\t",
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
    ]


def _config(fuentes, tmp_path: Path, **extra) -> ConfigCruce:
    base = {
        "fuentes": fuentes,
        "workspace": tmp_path / "salida",
        "confiables": {"PADRON"},
        "dir_trabajo": tmp_path / "trabajo",
        "filas_smoke": 0,
        "exportar_excel": False,
    }
    base.update(extra)
    return ConfigCruce(**base)


# ── Validación de la configuración ────────────────────────────────────


def test_config_rechaza_fuentes_vacias(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fuentes"):
        ConfigCruce(fuentes=[], workspace=tmp_path)


def test_config_rechaza_nombres_duplicados(fuentes_csv, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicados"):
        ConfigCruce(fuentes=[fuentes_csv[0], fuentes_csv[0]], workspace=tmp_path)


def test_config_rechaza_confiable_inexistente(fuentes_csv, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inexistentes"):
        ConfigCruce(fuentes=fuentes_csv, workspace=tmp_path, confiables={"NO_EXISTE"})


def test_config_nunca_deja_el_trabajo_en_drive(fuentes_csv) -> None:
    """El work_dir jamás debe quedar sobre un montaje FUSE."""
    cfg = ConfigCruce(fuentes=fuentes_csv, workspace="/content/drive/MyDrive/salida")
    assert not str(cfg.dir_trabajo).startswith("/content/drive")


def test_config_respeta_dir_trabajo_explicito(fuentes_csv, tmp_path: Path) -> None:
    cfg = ConfigCruce(fuentes=fuentes_csv, workspace=tmp_path, dir_trabajo=tmp_path / "mio")
    assert cfg.dir_trabajo == tmp_path / "mio"


@pytest.mark.parametrize("objetivo_nombre", ["_smoke", "corrida"])
@pytest.mark.parametrize(
    ("protegida_clase", "relacion"),
    [
        (protegida_clase, relacion)
        for protegida_clase in ("workspace", "fuente", "cache", "spill")
        for relacion in ("igual", "objetivo_ancestro", "objetivo_descendiente")
        if not (protegida_clase == "workspace" and relacion == "objetivo_descendiente")
    ],
)
def test_config_rechaza_solapamientos_destructivos_de_checkpoints(
    fuentes_csv,
    tmp_path: Path,
    objetivo_nombre: str,
    protegida_clase: str,
    relacion: str,
) -> None:
    from dataclasses import replace

    trabajo = tmp_path / "trabajo_seguro"
    objetivo = trabajo / objetivo_nombre
    protegida = {
        "igual": objetivo,
        "objetivo_ancestro": objetivo / "anidada",
        "objetivo_descendiente": trabajo,
    }[relacion]
    kwargs = {
        "fuentes": fuentes_csv,
        "workspace": tmp_path / "salida_segura",
        "dir_trabajo": trabajo,
        "reusar_checkpoints": False,
    }
    if protegida_clase == "workspace":
        kwargs["workspace"] = protegida
    elif protegida_clase == "fuente":
        kwargs["fuentes"] = [replace(fuentes_csv[0], path=protegida), fuentes_csv[1]]
    elif protegida_clase == "cache":
        kwargs["dir_procesados"] = protegida
    else:
        kwargs["duckdb_settings"] = DuckDBIngestionSettings(temp_directory=protegida)

    with pytest.raises(ValueError, match="Limpieza de checkpoints insegura"):
        ConfigCruce(**kwargs)


def test_config_permite_checkpoints_default_descendientes_del_workspace(
    fuentes_csv, tmp_path: Path
) -> None:
    from record_linkage.flujo.cruce import _limpiar_checkpoints

    workspace = tmp_path / "salida"
    config = ConfigCruce(
        fuentes=fuentes_csv,
        workspace=workspace,
        reusar_checkpoints=False,
        exportar_excel=False,
    )

    assert config.ruta_trabajo == workspace / "_trabajo"
    resultado_existente = workspace / "resultado_existente.parquet"
    resultado_existente.parent.mkdir(parents=True)
    resultado_existente.write_bytes(b"resultado")
    for checkpoint in ("_smoke", "corrida"):
        ruta = config.ruta_trabajo / checkpoint
        ruta.mkdir(parents=True)
        (ruta / "obsoleto").write_text("x", encoding="utf-8")

    _limpiar_checkpoints(config)

    assert resultado_existente.read_bytes() == b"resultado"
    assert not (config.ruta_trabajo / "_smoke").exists()
    assert not (config.ruta_trabajo / "corrida").exists()


@pytest.mark.parametrize(
    ("extra", "mensaje"),
    [
        ({"forzar_relectura": True}, "forzar_relectura"),
        ({"dir_procesados": "cache"}, "dir_procesados"),
    ],
)
def test_config_duckdb_rechaza_parametros_exclusivos_de_pandas(
    fuentes_csv, tmp_path: Path, extra: dict[str, object], mensaje: str
) -> None:
    if "dir_procesados" in extra:
        extra = {"dir_procesados": tmp_path / str(extra["dir_procesados"])}
    with pytest.raises(ValueError, match=mensaje):
        ConfigCruce(
            fuentes=fuentes_csv,
            workspace=tmp_path / "salida",
            motor_ingesta="duckdb",
            exportar_excel=False,
            **extra,
        )


def test_config_rechaza_motor_ingesta_desconocido(fuentes_csv, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="motor_ingesta"):
        ConfigCruce(
            fuentes=fuentes_csv,
            workspace=tmp_path,
            motor_ingesta="spark",
        )


# ── Preflight ─────────────────────────────────────────────────────────


def test_preflight_falla_antes_de_procesar_si_falta_una_fuente(fuentes_csv, tmp_path: Path) -> None:
    from dataclasses import replace

    rotas = [fuentes_csv[0], replace(fuentes_csv[1], path=tmp_path / "no_existe.csv")]
    with pytest.raises(FileNotFoundError, match="no existe"):
        ejecutar_cruce(_config(rotas, tmp_path))


@pytest.mark.parametrize(
    ("nombre_destino", "exportar_excel"),
    [
        ("golden.parquet", False),
        ("correlativa.parquet", False),
        ("metadatos_corrida.json", False),
        ("golden.xlsx", True),
        ("correlativa.xlsx", True),
    ],
)
def test_preflight_pandas_rechaza_fuente_que_seria_sobrescrita(
    fuentes_csv,
    tmp_path: Path,
    nombre_destino: str,
    exportar_excel: bool,
) -> None:
    from dataclasses import replace

    destino = tmp_path / "salida" / nombre_destino
    destino.parent.mkdir(parents=True)
    contenido_original = b"fuente que debe conservarse"
    destino.write_bytes(contenido_original)
    fuentes = [replace(fuentes_csv[0], path=destino), fuentes_csv[1]]

    with pytest.raises(ValueError, match="colisiona"):
        ejecutar_cruce(
            _config(
                fuentes,
                tmp_path,
                exportar_excel=exportar_excel,
                motor_ingesta="pandas",
            )
        )

    assert destino.read_bytes() == contenido_original


@pytest.mark.parametrize(
    "ruta_relativa",
    [
        "resultados.manifest.json",
        ".resultados.manifest.json.lock",
        "resultados.generations/existente/golden.parquet",
        ".resultados.token.generation.pending",
        ".resultados.manifest.json.token.pending",
    ],
)
def test_preflight_disco_rechaza_fuentes_en_controles_de_publicacion(
    fuentes_csv, tmp_path: Path, ruta_relativa: str
) -> None:
    from dataclasses import replace

    destino = tmp_path / "salida" / Path(ruta_relativa)
    destino.parent.mkdir(parents=True)
    contenido_original = b"control reservado"
    destino.write_bytes(contenido_original)
    fuentes = [replace(fuentes_csv[0], path=destino), fuentes_csv[1]]

    with pytest.raises(ValueError, match=r"colisiona|solapa|nombre reservado"):
        ejecutar_cruce(
            _config(
                fuentes,
                tmp_path,
                motor_ingesta="duckdb",
                modo_resultado="disco",
            )
        )

    assert destino.read_bytes() == contenido_original


# ── Corrida completa ──────────────────────────────────────────────────


def test_cruce_completo_enlaza_y_conserva_todas_las_filas(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(_config(fuentes_csv, tmp_path))

    assert resultado.metricas["filas_entrada"] == 5
    assert len(resultado.correlativa) == 5
    # ACME aparece en ambas fuentes con NIT 9 y 10 dígitos: debe ser una entidad.
    acme = resultado.correlativa[resultado.correlativa["RAZON_SOCIAL"].str.startswith("ACME")]
    assert acme["ID_GRUPO"].nunique() == 1, "el mismo NIT base debe unir ambas fuentes"
    assert resultado.correlativa["ID_GRUPO"].nunique() == 4


def test_cruce_escribe_parquet_y_metadatos_auditables(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(_config(fuentes_csv, tmp_path))

    assert resultado.rutas["golden_parquet"].exists()
    assert resultado.rutas["correlativa_parquet"].exists()
    metadatos = json.loads(resultado.rutas["metadatos"].read_text(encoding="utf-8"))
    parametros = metadatos["parametros"]
    assert parametros["confiables"] == ["PADRON"]
    assert parametros["filas_smoke"] == 0
    assert parametros["reusar_checkpoints"] is True
    assert parametros["forzar_relectura"] is False
    assert parametros["exportar_excel"] is False
    assert parametros["dir_trabajo"] == str(tmp_path / "trabajo")
    assert parametros["perfil_multicampo"] is None
    assert metadatos["metricas"]["filas_entrada"] == 5
    assert {f["nombre"] for f in parametros["fuentes"]} == {"PADRON", "CLIENTES"}
    contratos = {spec["name"]: spec for spec in parametros["source_specs"]}
    assert contratos["PADRON"]["column_mapping"] == {
        "NIT": "IDENT",
        "RAZON_SOCIAL": "NOMBRE_EMPRESA",
    }
    assert contratos["PADRON"]["column_types"] == {"NIT": "identifier"}
    assert contratos["PADRON"]["format"] == "auto"
    assert contratos["PADRON"]["temp_dir"] is None
    assert not list((tmp_path / "salida").glob(".metadatos_corrida.json.*.pending"))


def test_normalizador_json_cubre_tipos_conocidos_y_matching_profile(tmp_path: Path) -> None:
    import numpy as np

    from record_linkage.flujo.cruce import _normalizar_json, _serializar_perfil_multicampo
    from record_linkage.matching import default_colombia_profile

    settings = DuckDBIngestionSettings(temp_directory=tmp_path / "spill")
    formato = SourceSpec(
        name="TEMP",
        path=tmp_path / "x.csv",
        column_mapping={"NIT": "id"},
    ).format
    normalizado = _normalizar_json(
        {
            "settings": settings,
            "enum": formato,
            "secuencia": (np.int64(7), np.float64(1.5), np.bool_(True)),
            "perfil": default_colombia_profile(),
        }
    )

    assert formato.value == "auto"
    assert normalizado["settings"]["temp_directory"] == str(tmp_path / "spill")
    assert normalizado["enum"] == "auto"
    assert normalizado["secuencia"] == [7, 1.5, True]
    assert normalizado["perfil"]["name"] == "default_colombia_balanced"
    assert normalizado["perfil"]["variables"]
    assert _serializar_perfil_multicampo(None) is None
    assert _serializar_perfil_multicampo("colombia") == "colombia"
    assert _serializar_perfil_multicampo(default_colombia_profile())["variables"]


def test_config_rechaza_metadato_opaco_antes_de_la_corrida(fuentes_csv, tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=r"Metadato no serializable.*objeto"):
        ConfigCruce(
            fuentes=fuentes_csv,
            workspace=tmp_path / "salida",
            variables_extra=[{"column": "CIUDAD", "objeto": object()}],
            exportar_excel=False,
        )


def test_metadata_pandas_preserva_version_anterior_si_falla_replace(
    tmp_path: Path, monkeypatch
) -> None:
    import record_linkage.flujo.cruce as modulo

    destino = tmp_path / "metadatos_corrida.json"
    destino.write_text('{"version": "anterior"}\n', encoding="utf-8")
    original = destino.read_bytes()

    def fallar_replace(_origen: Path, _destino: Path) -> None:
        raise OSError("fallo de commit simulado")

    monkeypatch.setattr(modulo.os, "replace", fallar_replace)
    with pytest.raises(OSError, match="fallo de commit simulado"):
        modulo._escribir_json_atomico(destino, {"version": "nueva"})

    assert destino.read_bytes() == original
    assert not list(tmp_path.glob(".metadatos_corrida.json.*.pending"))


def test_cruce_con_smoke_test_previo(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(_config(fuentes_csv, tmp_path, filas_smoke=2))
    assert len(resultado.correlativa) == 5


def test_smoke_propaga_multicampo_y_limpia_ambos_checkpoints(
    fuentes_csv, tmp_path: Path, monkeypatch
) -> None:
    import record_linkage.flujo.cruce as modulo

    trabajo = tmp_path / "trabajo"
    marcadores = []
    for nombre in ("_smoke", "corrida"):
        directorio = trabajo / nombre
        directorio.mkdir(parents=True)
        marcador = directorio / "estado_anterior.txt"
        marcador.write_text("obsoleto", encoding="utf-8")
        marcadores.append(marcador)

    llamadas = []

    def linkage_falso(sources, **kwargs):
        llamadas.append(kwargs)
        assert not any(marcador.exists() for marcador in marcadores)
        correlativa = pd.concat(sources.values(), ignore_index=True).copy()
        correlativa["ID_GRUPO"] = range(len(correlativa))
        # v0.21.0 — El `linkage` real SIEMPRE devuelve las cuatro columnas del
        # contrato de salida (ADR-0008). Un doble que no las devuelve no es un
        # doble fiel: la invariante del flujo lo destapó, y se corrige aquí en
        # vez de debilitar la invariante.
        correlativa["NIT_FINAL"] = correlativa.get("NIT", pd.Series("", index=correlativa.index))
        correlativa["RAZON_SOCIAL_FINAL"] = correlativa.get(
            "RAZON_SOCIAL", pd.Series("", index=correlativa.index)
        )
        correlativa["NAME_SIMILARITY_SCORE"] = 1.0
        correlativa["NIT_DISTANCE"] = 0
        return {"golden": correlativa.copy(), "correlative": correlativa}

    monkeypatch.setattr(modulo, "linkage", linkage_falso)
    resultado = ejecutar_cruce(
        _config(
            fuentes_csv,
            tmp_path,
            filas_smoke=1,
            reusar_checkpoints=False,
            perfil_multicampo="colombia",
        )
    )

    assert len(resultado.correlativa) == 5
    assert [llamada["matching_profile"] for llamada in llamadas] == [
        "colombia",
        "colombia",
    ]
    assert llamadas[0]["work_dir"] == str(trabajo / "_smoke")
    assert llamadas[1]["work_dir"] == str(trabajo / "corrida")


def test_preflight_revalida_rutas_si_config_fue_mutada(fuentes_csv, tmp_path: Path) -> None:
    config = _config(fuentes_csv, tmp_path, reusar_checkpoints=False)
    config.workspace = config.ruta_trabajo / "corrida"

    with pytest.raises(ValueError, match="Limpieza de checkpoints insegura"):
        ejecutar_cruce(config)


def test_error_al_limpiar_checkpoint_no_se_oculta(fuentes_csv, tmp_path: Path, monkeypatch) -> None:
    import record_linkage.flujo.cruce as modulo

    config = _config(fuentes_csv, tmp_path, reusar_checkpoints=False)
    (config.ruta_trabajo / "_smoke").mkdir(parents=True)

    def fallar(_ruta):
        raise OSError("fallo de limpieza simulado")

    monkeypatch.setattr(modulo.shutil, "rmtree", fallar)
    with pytest.raises(OSError, match="fallo de limpieza simulado"):
        ejecutar_cruce(config)


def test_excel_se_omite_por_encima_del_limite(fuentes_csv, tmp_path: Path, monkeypatch) -> None:
    """Un Excel de millones de filas no se intenta escribir: se avisa."""
    import record_linkage.flujo.cruce as modulo

    monkeypatch.setattr(modulo, "_LIMITE_EXCEL", 2)
    resultado = ejecutar_cruce(_config(fuentes_csv, tmp_path, exportar_excel=True))
    assert "correlativa_excel" not in resultado.rutas
    assert resultado.rutas["correlativa_parquet"].exists()


def test_invariante_detecta_perdida_de_filas(fuentes_csv, tmp_path: Path) -> None:
    from record_linkage.flujo.cruce import _verificar_invariantes

    correlativa = pd.DataFrame({"ID_GRUPO": [1, 2]})
    with pytest.raises(RuntimeError, match="conservar TODAS"):
        _verificar_invariantes(correlativa, pd.DataFrame({"ID_GRUPO": [1, 2]}), esperadas=3)


def test_invariante_detecta_grupo_nulo() -> None:
    from record_linkage.flujo.cruce import _verificar_invariantes

    correlativa = pd.DataFrame({"ID_GRUPO": [1, None]})
    with pytest.raises(RuntimeError, match="sin ID_GRUPO"):
        _verificar_invariantes(correlativa, pd.DataFrame({"ID_GRUPO": [1]}), esperadas=2)


# ── Insumos: copia anti-Drive ─────────────────────────────────────────


def test_insumo_local_no_se_copia(tmp_path: Path) -> None:
    """Si el origen no está en FUSE no se paga ninguna copia."""
    origen = tmp_path / "dato.csv"
    origen.write_text("a\n1\n", encoding="utf-8")
    assert preparar_insumo_local(origen, tmp_path / "cache") == origen
    assert not (tmp_path / "cache").exists()


def test_insumo_inexistente_falla_con_mensaje_util(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Drive"):
        preparar_insumo_local(tmp_path / "fantasma.zip", tmp_path)


def test_insumo_en_drive_se_copia_y_se_reutiliza(tmp_path: Path, monkeypatch) -> None:
    import record_linkage.flujo.insumos as modulo

    origen = tmp_path / "grande.zip"
    origen.write_bytes(b"contenido" * 100)
    monkeypatch.setattr(modulo, "es_ruta_fuse", lambda _ruta: True)

    destino_dir = tmp_path / "local"
    primera = preparar_insumo_local(origen, destino_dir)
    assert primera == destino_dir / "grande.zip"
    assert primera.read_bytes() == origen.read_bytes()

    marca = primera.stat().st_mtime_ns
    segunda = preparar_insumo_local(origen, destino_dir)
    assert segunda.stat().st_mtime_ns == marca, "una copia válida no debe rehacerse"

    tercera = preparar_insumo_local(origen, destino_dir, forzar=True)
    assert tercera.exists()
    assert not list(destino_dir.glob("*.parcial")), "no deben quedar copias truncadas"


# ── Reportes ──────────────────────────────────────────────────────────


def test_reporte_de_composicion_es_legible() -> None:
    correlativa = pd.DataFrame({"ID_GRUPO": [1, 1, 2], "SRC": ["A", "B", "A"]})
    texto = reportar_composicion(3, correlativa)
    assert "Entidades" in texto and "2" in texto
    assert "33.3%" in texto or "33.3 %" in texto or "66.7%" in texto


def test_reporte_de_cruce_por_fuente() -> None:
    correlativa = pd.DataFrame({"ID_GRUPO": [1, 1, 2, 3], "SRC": ["RUES", "EXPO", "EXPO", "RUES"]})
    tabla = reportar_cruce_por_fuente(correlativa)
    expo = tabla[tabla["SRC"] == "EXPO"].iloc[0]
    assert expo["registros"] == 2
    assert expo["enlazados"] == 1
    assert expo["pct_enlazado"] == pytest.approx(0.5)


def test_reporte_exige_columnas() -> None:
    with pytest.raises(KeyError, match="SRC"):
        reportar_cruce_por_fuente(pd.DataFrame({"ID_GRUPO": [1]}))


# ── Caché de fuentes proyectadas (v0.14.0) ────────────────────────────


def test_cache_reutiliza_la_segunda_corrida(fuentes_csv, tmp_path: Path) -> None:
    """La segunda corrida no vuelve a leer el original: lee el Parquet."""
    from record_linkage.flujo.insumos import leer_cache, ruta_en_cache

    cache = tmp_path / "procesados"
    primera = ejecutar_cruce(_config(fuentes_csv, tmp_path, dir_procesados=cache))
    assert ruta_en_cache(fuentes_csv[0], cache).is_file()
    assert leer_cache(fuentes_csv[0], cache) is not None

    # Se borra el archivo original: si la caché no sirviera, esto fallaría.
    Path(fuentes_csv[0].path).unlink()
    segunda = ejecutar_cruce(
        ConfigCruce(
            fuentes=fuentes_csv,
            workspace=tmp_path / "salida2",
            dir_trabajo=tmp_path / "trabajo2",
            dir_procesados=cache,
            filas_smoke=0,
            exportar_excel=False,
        )
    )
    assert len(segunda.correlativa) == len(primera.correlativa)


def test_cache_se_invalida_si_cambia_el_contrato(fuentes_csv, tmp_path: Path) -> None:
    """Otro mapeo de columnas es otro DataFrame: no puede reutilizar el anterior."""
    from dataclasses import replace

    from record_linkage.flujo.insumos import ruta_en_cache

    cache = tmp_path / "procesados"
    original = fuentes_csv[0]
    otro = replace(original, passthrough_columns=("NOMBRE_EMPRESA",))
    assert ruta_en_cache(original, cache) != ruta_en_cache(otro, cache)


def test_cache_se_invalida_si_cambia_el_archivo(fuentes_csv, tmp_path: Path) -> None:
    """Un archivo distinto (tamaño o fecha) descarta la caché por sí solo."""
    from record_linkage.flujo.insumos import cargar_fuente_con_cache, leer_cache

    cache = tmp_path / "procesados"
    cargar_fuente_con_cache(fuentes_csv[0], cache)
    assert leer_cache(fuentes_csv[0], cache) is not None

    Path(fuentes_csv[0].path).write_text(
        "IDENT,NOMBRE_EMPRESA\n900111222,ACME COLOMBIA SAS\n", encoding="utf-8"
    )
    assert leer_cache(fuentes_csv[0], cache) is None, "un archivo nuevo no puede reutilizarse"


def test_forzar_relectura_ignora_la_cache(fuentes_csv, tmp_path: Path) -> None:
    from record_linkage.flujo.insumos import cargar_fuente_con_cache

    cache = tmp_path / "procesados"
    _, _, primera = cargar_fuente_con_cache(fuentes_csv[0], cache)
    assert primera is False
    _, _, segunda = cargar_fuente_con_cache(fuentes_csv[0], cache)
    assert segunda is True
    _, _, tercera = cargar_fuente_con_cache(fuentes_csv[0], cache, forzar=True)
    assert tercera is False


def test_cache_corrupta_no_tumba_la_corrida(fuentes_csv, tmp_path: Path) -> None:
    from record_linkage.flujo.insumos import cargar_fuente_con_cache, ruta_en_cache

    cache = tmp_path / "procesados"
    cargar_fuente_con_cache(fuentes_csv[0], cache)
    ruta = ruta_en_cache(fuentes_csv[0], cache)
    ruta.write_bytes(b"esto no es un parquet")
    datos, _, desde_cache = cargar_fuente_con_cache(fuentes_csv[0], cache)
    assert desde_cache is False
    assert len(datos) == 3


def test_cache_desactivada_por_defecto(fuentes_csv, tmp_path: Path) -> None:
    cfg = ConfigCruce(fuentes=fuentes_csv, workspace=tmp_path)
    assert cfg.ruta_procesados is None


def test_cache_no_escribible_no_tumba_la_corrida(fuentes_csv, tmp_path: Path) -> None:
    """Sin permiso de escritura la caché se omite; el resultado sale igual."""
    from record_linkage.flujo.insumos import escribir_cache

    # Un archivo donde debería ir la carpeta: mkdir falla y la caché se omite.
    bloqueada = tmp_path / "bloqueada"
    bloqueada.write_text("no soy una carpeta", encoding="utf-8")
    assert escribir_cache(fuentes_csv[0], bloqueada, pd.DataFrame({"a": [1]}), {}) is None


# ── Recorte para ensayos (v0.14.0) ────────────────────────────────────


def test_limite_filas_recorta_y_queda_registrado(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(_config(fuentes_csv, tmp_path, limite_filas={"PADRON": 2}))
    assert resultado.metricas["filas_entrada"] == 4  # 2 de PADRON + 2 de CLIENTES
    assert len(resultado.correlativa) == 4
    metadatos = json.loads(resultado.rutas["metadatos"].read_text(encoding="utf-8"))
    assert metadatos["parametros"]["limite_filas"] == {"PADRON": 2}


def test_limite_filas_rechaza_fuente_inexistente(fuentes_csv, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inexistentes"):
        ConfigCruce(fuentes=fuentes_csv, workspace=tmp_path, limite_filas={"NO_EXISTE": 10})


def test_limite_filas_rechaza_valores_invalidos(fuentes_csv, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=">= 1"):
        ConfigCruce(fuentes=fuentes_csv, workspace=tmp_path, limite_filas={"PADRON": 0})


def test_limite_mayor_que_la_fuente_es_no_op(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(_config(fuentes_csv, tmp_path, limite_filas={"PADRON": 999}))
    assert resultado.metricas["filas_entrada"] == 5


def test_duckdb_limita_en_ingesta_y_expande_todas_las_filas(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(
        _config(
            fuentes_csv,
            tmp_path,
            motor_ingesta="duckdb",
            duckdb_settings=DuckDBIngestionSettings(
                memory_limit="256MB",
                threads=2,
                temp_directory=tmp_path / "spill",
            ),
            limite_filas={"PADRON": 2},
        )
    )

    assert resultado.metricas["filas_entrada"] == 4
    assert len(resultado.correlativa) == 4
    assert resultado.reportes_carga["PADRON"]["engine"] == "duckdb"
    assert resultado.reportes_carga["PADRON"]["rows"] == 2
    metadatos = json.loads(resultado.rutas["metadatos"].read_text(encoding="utf-8"))
    assert metadatos["parametros"]["motor_ingesta"] == "duckdb"
    assert metadatos["parametros"]["duckdb_settings"]["memory_limit"] == "256MB"
    assert resultado.rutas["manifest"].is_file()
    assert resultado.rutas["generation_dir"].is_dir()
    assert resultado.rutas["metadatos"].parent == resultado.rutas["generation_dir"]
    manifest = json.loads(resultado.rutas["manifest"].read_text(encoding="utf-8"))
    assert manifest["generation"] == resultado.rutas["generation_dir"].name
    assert (
        tmp_path / "salida" / Path(manifest["artifacts"]["metadata"])
        == resultado.rutas["metadatos"]
    )


# ── Flexibilidad: ajustes de perfil, tiempos y deduplicación (v0.14.0) ──


def test_ajustes_de_perfil_llegan_al_motor(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(
        _config(
            fuentes_csv,
            tmp_path,
            ajustes_perfil={"lsh_permutations": 64, "score_threshold": 0.5},
        )
    )
    metadatos = json.loads(resultado.rutas["metadatos"].read_text(encoding="utf-8"))
    assert metadatos["parametros"]["ajustes_perfil"]["lsh_permutations"] == 64


def test_ajuste_desconocido_falla_con_sugerencia(fuentes_csv, tmp_path: Path) -> None:
    """Un parámetro mal escrito no puede ignorarse en silencio."""
    with pytest.raises(ValueError, match="lsh_permutations"):
        ejecutar_cruce(_config(fuentes_csv, tmp_path, ajustes_perfil={"lsh_permutation": 64}))


def test_tiempos_por_fase_se_registran(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(_config(fuentes_csv, tmp_path))
    fases = resultado.metricas["segundos_por_fase"]
    assert {"preflight", "carga de fuentes", "cruce", "exportes"} <= set(fases)
    assert sum(fases.values()) <= resultado.metricas["segundos_total"] + 1
    texto = resultado.tiempos()
    # v0.17.0: el encabezado gana la columna de memoria cuando psutil está
    # disponible ("TIEMPO Y MEMORIA POR FASE"); sin él degrada al título
    # anterior. El contrato estable es que hay desglose y total.
    assert "POR FASE" in texto and "TOTAL" in texto


def test_una_sola_fuente_es_deduplicacion(fuentes_csv, tmp_path: Path) -> None:
    """El mismo flujo deduplica cuando se le pasa una sola fuente."""
    resultado = ejecutar_cruce(
        ConfigCruce(
            fuentes=[fuentes_csv[0]],
            workspace=tmp_path / "dedup",
            dir_trabajo=tmp_path / "trabajo_dedup",
            filas_smoke=0,
            exportar_excel=False,
        )
    )
    assert len(resultado.correlativa) == 3
    assert resultado.correlativa["ID_GRUPO"].nunique() == 3


# ═══════════════════════════════════════════════════════════════════
# Separación de columnas de arrastre (restituida en v0.17.0 desde 0.14.1)
# ═══════════════════════════════════════════════════════════════════
# Sin esto, una columna mapeada que no puntúa (DEPARTAMENTO, TELEFONO…)
# viaja por las cinco fases y degrada el colapso exacto: medido sobre
# RUES x Exportaciones, los representantes pasaron de 19.407 a 32.745.


@pytest.fixture
def fuentes_con_arrastre(tmp_path: Path) -> list[SourceSpec]:
    """Fuentes con columnas mapeadas que NO participan en la decisión."""
    a = tmp_path / "padron.csv"
    a.write_text(
        "IDENT,NOMBRE_EMPRESA,TEL,CORREO\n"
        "900111222,ACME COLOMBIA SAS,3001112233,acme@x.co\n"
        "800333444,BETA LTDA,3009998877,beta@x.co\n"
        "900555666,GAMA S.A.,3005554433,gama@x.co\n",
        encoding="utf-8",
    )
    b = tmp_path / "clientes.txt"
    b.write_text(
        "nit_cliente\trazon\tdepto\n"
        "9001112221\tACME COLOMBIA S.A.S.\tANTIOQUIA\n"
        "700999888\tDELTA EU\tBOGOTA\n",
        encoding="utf-8",
    )
    return [
        SourceSpec(
            name="PADRON",
            path=a,
            column_mapping={"NIT": "IDENT", "RAZON_SOCIAL": "NOMBRE_EMPRESA"},
            optional_column_mapping={"TELEFONO": "TEL", "EMAIL": "CORREO"},
            delimiter=",",
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
        SourceSpec(
            name="CLIENTES",
            path=b,
            column_mapping={"NIT": "nit_cliente", "RAZON_SOCIAL": "razon"},
            optional_column_mapping={"DEPARTAMENTO": "depto"},
            delimiter="\t",
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
    ]


def test_columnas_de_arrastre_no_entran_al_motor_pero_vuelven(
    fuentes_con_arrastre, tmp_path: Path
) -> None:
    """TELEFONO/EMAIL/DEPARTAMENTO no viajan por el pipeline; sí a la salida."""
    resultado = ejecutar_cruce(_config(fuentes_con_arrastre, tmp_path))
    corr = resultado.correlativa
    # Re-adjuntadas y con el valor EXACTO de la fila original.
    fila_acme = corr[(corr["SRC"] == "PADRON") & (corr["NIT"] == "900111222")].iloc[0]
    assert fila_acme["TELEFONO"] == "3001112233"
    assert fila_acme["EMAIL"] == "acme@x.co"
    fila_delta = corr[(corr["SRC"] == "CLIENTES") & (corr["RAZON_SOCIAL"] == "DELTA EU")].iloc[0]
    assert fila_delta["DEPARTAMENTO"] == "BOGOTA"
    # Cruzada: TELEFONO de la fila de CLIENTES es NA (no existe en esa fuente).
    assert pd.isna(fila_delta["TELEFONO"])
    metadatos = json.loads(resultado.rutas["metadatos"].read_text(encoding="utf-8"))
    # F1.8: el manifiesto declara lo que REALMENTE se adjuntó, no la unión planificada.
    arrastre = metadatos["parametros"]["columnas_arrastre"]
    assert set(arrastre["adjuntadas"]) == {"TELEFONO", "EMAIL", "DEPARTAMENTO"}
    assert arrastre["omitidas"] == []


def test_variable_extra_activa_se_queda_en_el_motor(fuentes_con_arrastre, tmp_path: Path) -> None:
    """Una columna declarada en variables_extra NO se separa: el motor la necesita."""
    from record_linkage.flujo.cruce import _columnas_de_motor, _separar_columnas_extra

    cfg = _config(
        fuentes_con_arrastre,
        tmp_path,
        variables_extra=[{"column": "TELEFONO", "weight": 0.1, "type": "exact_signed"}],
    )
    assert "TELEFONO" in _columnas_de_motor(cfg)
    marcos = {
        "PADRON": pd.DataFrame(
            {"NIT": ["1"], "RAZON_SOCIAL": ["A"], "TELEFONO": ["3"], "EMAIL": ["x"]}
        )
    }
    _rutas, union, _longitudes = _separar_columnas_extra(marcos, cfg, _LogNulo())
    assert "TELEFONO" in marcos["PADRON"].columns, "el motor la usa: no puede separarse"
    assert union == ["EMAIL"]


def test_separacion_desactivable(fuentes_con_arrastre, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(
        _config(fuentes_con_arrastre, tmp_path, separar_columnas_extra=False)
    )
    assert "TELEFONO" in resultado.correlativa.columns  # viajó por el pipeline


def test_el_colapso_mejora_sin_columnas_de_arrastre(tmp_path: Path) -> None:
    """Sin la columna transaccional, el colapso exacto agrupa mucho más."""
    a = tmp_path / "trans.csv"
    filas = ["IDENT,NOMBRE,ANIO"] + [
        f"90011122{i % 2},EMPRESA {i % 2} SAS,{2000 + i}" for i in range(40)
    ]
    a.write_text("\n".join(filas) + "\n", encoding="utf-8")
    spec = SourceSpec(
        name="T",
        path=a,
        column_mapping={"NIT": "IDENT", "RAZON_SOCIAL": "NOMBRE"},
        optional_column_mapping={"CIUDAD": "ANIO"},  # transaccional a propósito
        delimiter=",",
        column_types={"NIT": ColumnType.IDENTIFIER},
    )
    resultado = ejecutar_cruce(
        ConfigCruce(
            fuentes=[spec],
            workspace=tmp_path / "s",
            dir_trabajo=tmp_path / "t",
            filas_smoke=0,
            exportar_excel=False,
        )
    )
    assert len(resultado.correlativa) == 40  # todas las filas restituidas
    assert resultado.correlativa["CIUDAD"].nunique() == 40  # y su columna intacta


class _LogNulo:
    """Logger inerte para ejercitar helpers sin ruido en la salida."""

    def info(self, *_a, **_k): ...

    def warning(self, *_a, **_k): ...
