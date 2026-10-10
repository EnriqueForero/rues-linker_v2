"""Flujo de alto nivel `record_linkage.flujo` (v0.14.0).

La lógica que antes vivía en celdas de notebook (preflight, copia anti-Drive,
smoke test, invariantes, exportes, metadatos) ahora es código con pruebas.

F2.5: ``ejecutar_cruce`` devuelve un ``ResultadoLinkage`` (``ResultadoCruce``
lo extiende con el QA del cruce) y escribe la carpeta del estándar con
``escribir_resultado``; las invariantes del cruce son ``validar()`` más la
comprobación de filas de entrada del flujo; el modo disco conserva las
generaciones, cada una con la forma del estándar.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from apoyo_cruce import LogNulo, config_cruce

from record_linkage import ColumnType, SourceSpec, contrato
from record_linkage.exporters import escritor
from record_linkage.exporters.escritor import leer_resultado
from record_linkage.flujo import (
    ConfigCruce,
    ResultadoCruce,
    ResultadoCruceDisco,
    ejecutar_cruce,
    preparar_insumo_local,
    reportar_composicion,
)
from record_linkage.flujo.reportes import reportar_cruce_por_fuente
from record_linkage.flujo.resultados_disco import DIVERGENCIA_METODO_UNION
from record_linkage.ingestion import DuckDBIngestionSettings
from record_linkage.pipeline.errores import ContratoSalidaError, EscrituraSalidaError
from record_linkage.resultado import ResultadoLinkage
from record_linkage.salida.completar import bases_del_motor, grupos_con_bases_distintas


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
        ejecutar_cruce(config_cruce(rotas, tmp_path))


@pytest.mark.parametrize(
    "ruta_relativa",
    [".cruce.pendiente/correlativa.parquet", ".cruce.pendiente/_trabajo/manifest.json"],
)
def test_preflight_pandas_rechaza_fuente_en_la_carpeta_pendiente(
    fuentes_csv, tmp_path: Path, ruta_relativa: str
) -> None:
    """F2.5: el escritor del estándar limpia ``<workspace>/.<nombre>.pendiente/``
    antes de escribir y la renombra al final; una fuente ahí se perdería. La
    carpeta definitiva (``<AAAA-MM-DD_HHMM>_<nombre>``) no se reserva: si ya
    existe, el escritor falla sin tocarla (nunca sobrescribe)."""
    from dataclasses import replace

    destino = tmp_path / "salida" / Path(ruta_relativa)
    destino.parent.mkdir(parents=True)
    contenido_original = b"fuente que debe conservarse"
    destino.write_bytes(contenido_original)
    fuentes = [replace(fuentes_csv[0], path=destino), fuentes_csv[1]]

    with pytest.raises(ValueError, match="solapa"):
        ejecutar_cruce(config_cruce(fuentes, tmp_path, motor_ingesta="pandas"))

    assert destino.read_bytes() == contenido_original


def test_config_rechaza_nombre_con_separadores(fuentes_csv, tmp_path: Path) -> None:
    """El ``nombre`` cierra el nombre de la carpeta del estándar: se valida al
    construir la configuración, no después de L1…L5."""
    with pytest.raises(ValueError, match="nombre"):
        config_cruce(fuentes_csv, tmp_path, nombre="a/b")


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
            config_cruce(
                fuentes,
                tmp_path,
                motor_ingesta="duckdb",
                modo_resultado="disco",
            )
        )

    assert destino.read_bytes() == contenido_original


# ── Corrida completa ──────────────────────────────────────────────────


#: Lo que toda carpeta del estándar trae sin Excel ni figuras.
ARTEFACTOS_ESTANDAR = {
    "correlativa.parquet",
    "golden.parquet",
    "entidades_ids.parquet",
    "revision.csv",
    "diccionario.csv",
    "manifest.json",
}


def _archivos(carpeta: Path) -> set[str]:
    return {p.relative_to(carpeta).as_posix() for p in carpeta.rglob("*") if p.is_file()}


def test_cruce_devuelve_resultado_linkage_y_escribe_el_estandar(
    fuentes_csv, tmp_path: Path
) -> None:
    """F2.5: en modo dataframe ``ejecutar_cruce`` devuelve el ``ResultadoLinkage``
    del contrato (ampliado con el QA del cruce) y deja UNA carpeta con la forma
    del estándar, escrita por ``escribir_resultado``."""
    resultado = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path))

    assert isinstance(resultado, ResultadoLinkage)
    assert isinstance(resultado, ResultadoCruce)
    assert resultado.validar().ok
    assert list(resultado.correlativa.columns[:12]) == list(contrato.COLUMNAS_CORRELATIVA)
    carpeta = resultado.carpeta
    assert carpeta == resultado.rutas["carpeta"]
    assert carpeta.parent == tmp_path / "salida" and carpeta.name.endswith("_cruce")
    assert _archivos(carpeta) == ARTEFACTOS_ESTANDAR
    assert not (tmp_path / "salida" / ".cruce.pendiente").exists()
    assert resultado.rutas["manifest"] == carpeta / "manifest.json"
    assert resultado.rutas["correlativa_parquet"] == carpeta / "correlativa.parquet"
    assert resultado.rutas["golden_parquet"] == carpeta / "golden.parquet"
    assert resultado.rutas["entidades_ids_parquet"] == carpeta / "entidades_ids.parquet"
    assert resultado.rutas["dir_trabajo"] == resultado.dir_trabajo
    assert resultado.dir_trabajo == tmp_path / "trabajo" / "corrida"
    assert resultado.manifiesto["carpeta_salida"] == str(carpeta)
    # Lo escrito es lo devuelto, y se lee de vuelta como un resultado válido.
    leido = leer_resultado(carpeta)
    assert leido.validar().ok
    pd.testing.assert_frame_equal(
        leido.correlativa.reset_index(drop=True),
        resultado.correlativa.reset_index(drop=True),
        check_dtype=False,
    )
    # _trabajo/ está fuera del workspace: la carpeta no lo trae, el manifiesto lo declara.
    assert leido.dir_trabajo is None
    assert leido.manifiesto["manifest"]["corrida"]["dir_trabajo"] == str(resultado.dir_trabajo)
    # El diccionario cubre las columnas de arrastre re-adjuntadas.
    dic = resultado.diccionario
    assert set(resultado.correlativa.columns) <= set(
        dic.loc[dic["tabla"] == "correlativa", "columna"]
    )


def test_cruce_con_excel_lo_escribe_el_escritor(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path, exportar_excel=True))
    carpeta = resultado.carpeta
    esperados = ARTEFACTOS_ESTANDAR | {"excel/correlativa.xlsx", "excel/golden.xlsx"}
    assert _archivos(carpeta) == esperados
    assert resultado.rutas["correlativa_excel"] == carpeta / "excel" / "correlativa.xlsx"
    assert resultado.rutas["golden_excel"] == carpeta / "excel" / "golden.xlsx"


def test_dos_corridas_en_el_mismo_minuto_exigen_otro_nombre(
    fuentes_csv, tmp_path: Path, monkeypatch
) -> None:
    """El escritor nunca sobrescribe una corrida publicada (resolución de minuto).

    El reloj se fija: con ``datetime.now()`` real, una corrida en HH:MM:59 y la
    siguiente en HH:MM+1:00 no chocan y la prueba fallaría al azar."""

    class Reloj(datetime):
        @classmethod
        def now(cls, tz=None):  # firma de datetime.now
            return cls(2026, 10, 6, 12, 0)

    monkeypatch.setattr(escritor, "datetime", Reloj)
    ejecutar_cruce(config_cruce(fuentes_csv, tmp_path))
    with pytest.raises(EscrituraSalidaError, match="ya existe"):
        ejecutar_cruce(config_cruce(fuentes_csv, tmp_path))
    segunda = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path, nombre="segunda"))
    assert segunda.carpeta.name.endswith("_segunda")
    assert len([p for p in (tmp_path / "salida").iterdir() if p.is_dir()]) == 2


def test_cruce_completo_enlaza_y_conserva_todas_las_filas(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path))

    assert resultado.metricas["filas_entrada"] == 5
    assert len(resultado.correlativa) == 5
    # ACME aparece en ambas fuentes con NIT 9 y 10 dígitos: debe ser una entidad.
    acme = resultado.correlativa[resultado.correlativa["RAZON_SOCIAL"].str.startswith("ACME")]
    assert acme["ID_GRUPO"].nunique() == 1, "el mismo NIT base debe unir ambas fuentes"
    assert resultado.correlativa["ID_GRUPO"].nunique() == 4


def test_cruce_en_memoria_responde_el_qa_sin_columnas_tecnicas(fuentes_csv, tmp_path: Path) -> None:
    """F1.9: el entregable ya no trae NIT_BASE/NIT_VALID; el QA toma el conteo de
    conflictos del manifiesto del contrato (calculado con las técnicas del motor)."""
    resultado = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path))

    assert not {"NIT_BASE", "NIT_VALID"} & set(resultado.correlativa.columns)
    assert {"ID_REGISTRO", "ID_ENTIDAD", "METODO_UNION"} <= set(resultado.correlativa.columns)
    assert resultado.conflictos_identificador() == 0
    distribucion = resultado.distribucion_grupos()
    assert distribucion["grupos_1_fila"] == 3 and distribucion["grupos_2a5_filas"] == 1
    assert distribucion["max_filas_por_grupo"] == 2
    por_fuente = resultado.identificadores_por_fuente()
    assert set(por_fuente) == {"PADRON", "CLIENTES"}
    assert por_fuente["PADRON"].filas == 3 and por_fuente["CLIENTES"].filas == 2
    assert resultado.entidades_multifuente() == 1
    assert len(resultado.grupos_sospechosos()) == 0


def test_conflictos_identificador_parten_de_las_tecnicas_del_motor() -> None:
    """La regla única del QA (r3/r5): la base de cada registro es ``NIT_BASE``
    del motor (NitProcessor) cuando ``NIT_VALID`` lo acepta, sin ninguna
    reducción propia; dos bases distintas en un grupo = 1 conflicto; un NIT
    con DV y otro sin DV traen el mismo ``NIT_BASE``; lo que el motor no
    valida no cuenta, aunque traiga base."""
    id_grupo = pd.Series([0, 0, 1, 1, 2, 2, 3, 3])
    nit_base = pd.Series(
        [
            "900111222",  # NIT_OK 9001112221 (con DV)
            "900111222",  # NIT_OK 900111222 (sin DV): misma base, no es conflicto
            "800333444",
            "700999888",  # otra base válida en el grupo → conflicto
            "123",  # el motor no lo valida (NIT_VALID=0): no cuenta
            "",
            "900555666",
            "700999888",  # válido solo el primero: no es conflicto
        ]
    )
    nit_valid = pd.Series(["1", 1, True, "true", "0", "", "1", False])
    bases = bases_del_motor(nit_base.to_numpy(), nit_valid.to_numpy())
    assert list(bases) == [
        "900111222",
        "900111222",
        "800333444",
        "700999888",
        "",
        "",
        "900555666",
        "",
    ]
    assert grupos_con_bases_distintas(id_grupo, bases) == 1
    assert grupos_con_bases_distintas(id_grupo.iloc[:2], bases[:2]) == 0
    assert grupos_con_bases_distintas(id_grupo.iloc[4:6], bases[4:6]) == 0
    assert grupos_con_bases_distintas(id_grupo.iloc[6:], bases[6:]) == 0


def _fuentes_nit_flotante(tmp_path: Path) -> list[SourceSpec]:
    """Una fuente exportada desde pandas/Excel con el NIT como flotante
    (``900111222.0``, porque otra fila lo trae en blanco) y otra con el mismo
    NIT en 9 dígitos. El motor las une; el QA y METODO_UNION deben decirlo."""
    a = tmp_path / "padron_flotante.csv"
    a.write_text(
        "IDENT,NOMBRE_EMPRESA\n900111222.0,ACME COLOMBIA SAS\n,SIN IDENTIFICADOR SAS\n",
        encoding="utf-8",
    )
    b = tmp_path / "clientes_flotante.csv"
    b.write_text("nit_cliente,razon\n900111222,ACME COLOMBIA S.A.S.\n", encoding="utf-8")
    return [
        SourceSpec(
            name="PADRON",
            path=a,
            column_mapping={"NIT": "IDENT", "RAZON_SOCIAL": "NOMBRE_EMPRESA"},
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
        SourceSpec(
            name="CLIENTES",
            path=b,
            column_mapping={"NIT": "nit_cliente", "RAZON_SOCIAL": "razon"},
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
    ]


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"motor_ingesta": "duckdb", "modo_resultado": "disco"},
    ],
    ids=["memoria", "disco"],
)
def test_cruce_con_nit_flotante_no_inventa_conflictos(tmp_path: Path, extra: dict) -> None:
    resultado = ejecutar_cruce(config_cruce(_fuentes_nit_flotante(tmp_path), tmp_path, **extra))

    assert resultado.conflictos_identificador() == 0
    assert resultado.metricas["conflictos_identificador"] == 0
    correlativa = resultado.correlativa
    if not isinstance(correlativa, pd.DataFrame):
        correlativa = correlativa.to_pandas()
    acme = correlativa[correlativa["RAZON_SOCIAL"].str.startswith("ACME")]
    assert len(acme) == 2 and acme["ID_GRUPO"].nunique() == 1
    assert set(acme["METODO_UNION"]) == {"identificador"}


def test_cruce_falla_claro_si_linkage_no_trae_golden(
    fuentes_csv, tmp_path: Path, monkeypatch
) -> None:
    """Sin golden no hay cruce publicable: error accionable, no un AttributeError
    más abajo (ni un ``assert`` que ``python -O`` borra)."""
    import record_linkage.flujo.cruce as modulo

    def linkage_falso(sources, **kwargs):
        correlativa = pd.concat(sources.values(), ignore_index=True).copy()
        correlativa["ID_GRUPO"] = range(len(correlativa))
        return ResultadoLinkage(correlativa=correlativa, golden=None)

    monkeypatch.setattr(modulo, "linkage", linkage_falso)
    with pytest.raises(ContratoSalidaError, match="sin golden"):
        ejecutar_cruce(config_cruce(fuentes_csv, tmp_path))


def _manifiesto_cruce(resultado) -> dict:
    """El bloque del cruce dentro de ``manifest.json`` (un solo manifiesto, F1.12)."""
    manifest = json.loads(resultado.rutas["manifest"].read_text(encoding="utf-8"))
    return manifest["corrida"]["cruce"]


def test_cruce_escribe_parquet_y_metadatos_auditables(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path))

    assert resultado.rutas["golden_parquet"].exists()
    assert resultado.rutas["correlativa_parquet"].exists()
    manifest = json.loads(resultado.rutas["manifest"].read_text(encoding="utf-8"))
    assert manifest["contrato"] == contrato.VERSION_CONTRATO
    assert manifest["invariantes"]["ok"] is True
    assert manifest["corrida"]["funcion"] == "ejecutar_cruce"
    assert manifest["corrida"]["dir_trabajo"] == str(tmp_path / "trabajo" / "corrida")
    assert manifest["conteos"]["filas"] == 5 and manifest["conteos"]["grupos"] == 4
    assert manifest["parametros"]["perfil"] == "produccion_estandar"
    metadatos = manifest["corrida"]["cruce"]
    parametros = metadatos["parametros"]
    assert parametros["confiables"] == ["PADRON"]
    assert parametros["filas_smoke"] == 0
    assert parametros["reusar_checkpoints"] is True
    assert parametros["forzar_relectura"] is False
    assert parametros["exportar_excel"] is False
    assert parametros["dir_trabajo"] == str(tmp_path / "trabajo")
    # F1: las técnicas salieron del entregable; el resultado dice dónde quedan.
    assert resultado.rutas["dir_trabajo"] == tmp_path / "trabajo" / "corrida"
    assert (resultado.rutas["dir_trabajo"] / "L5_golden" / "correlative.parquet").is_file()
    assert parametros["perfil_multicampo"] is None
    assert metadatos["metricas"]["filas_entrada"] == 5
    assert set(metadatos["ingesta"]) == {"PADRON", "CLIENTES"}
    assert {f["nombre"] for f in parametros["fuentes"]} == {"PADRON", "CLIENTES"}
    contratos = {spec["name"]: spec for spec in parametros["source_specs"]}
    assert contratos["PADRON"]["column_mapping"] == {
        "NIT": "IDENT",
        "RAZON_SOCIAL": "NOMBRE_EMPRESA",
    }
    assert contratos["PADRON"]["column_types"] == {"NIT": "identifier"}
    assert contratos["PADRON"]["format"] == "auto"
    assert contratos["PADRON"]["temp_dir"] is None
    assert not list((tmp_path / "salida").glob(".*"))


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


def test_fallo_al_escribir_no_deja_carpeta_ni_pendiente(
    fuentes_csv, tmp_path: Path, monkeypatch
) -> None:
    """La atomicidad es la del escritor único: si falla a mitad, ni carpeta
    definitiva ni pendiente; los checkpoints de ``_trabajo`` (fuera del
    workspace) sobreviven para reanudar."""

    def fallar(*_a, **_k):
        raise OSError("disco lleno simulado")

    monkeypatch.setattr(escritor, "_escribir_parquet", fallar)
    with pytest.raises(OSError, match="disco lleno simulado"):
        ejecutar_cruce(config_cruce(fuentes_csv, tmp_path))
    assert not list((tmp_path / "salida").iterdir())
    assert (tmp_path / "trabajo" / "corrida" / "manifest.json").is_file()


def test_cruce_con_smoke_test_previo(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path, filas_smoke=2))
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
    linkage_real = modulo.linkage

    def linkage_espia(sources, **kwargs):
        # F2.5 — el flujo valida el contrato completo y escribe el estándar: un
        # doble a medias ya no pasa. El espía registra la llamada, comprueba la
        # limpieza y delega en el linkage real (ADR-0008: un doble infiel no
        # sirve para debilitar una invariante).
        llamadas.append(kwargs)
        assert not any(marcador.exists() for marcador in marcadores)
        return linkage_real(sources, **kwargs)

    monkeypatch.setattr(modulo, "linkage", linkage_espia)
    resultado = ejecutar_cruce(
        config_cruce(
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
    config = config_cruce(fuentes_csv, tmp_path, reusar_checkpoints=False)
    config.workspace = config.ruta_trabajo / "corrida"

    with pytest.raises(ValueError, match="Limpieza de checkpoints insegura"):
        ejecutar_cruce(config)


def test_error_al_limpiar_checkpoint_no_se_oculta(fuentes_csv, tmp_path: Path, monkeypatch) -> None:
    import record_linkage.flujo.cruce as modulo

    config = config_cruce(fuentes_csv, tmp_path, reusar_checkpoints=False)
    (config.ruta_trabajo / "_smoke").mkdir(parents=True)

    def fallar(_ruta):
        raise OSError("fallo de limpieza simulado")

    monkeypatch.setattr(modulo.shutil, "rmtree", fallar)
    with pytest.raises(OSError, match="fallo de limpieza simulado"):
        ejecutar_cruce(config)


def test_excel_que_no_cabe_deja_leeme_y_no_recorta(
    fuentes_csv, tmp_path: Path, monkeypatch
) -> None:
    """La regla es la del escritor (F1.11): completo hasta el límite o un
    ``<tabla>_LEEME.xlsx`` que dice cómo abrir el parquet; nunca un recorte."""
    monkeypatch.setattr(escritor, "LIMITE_FILAS_EXCEL", 2)
    resultado = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path, exportar_excel=True))
    assert "correlativa_excel" not in resultado.rutas
    assert resultado.rutas["correlativa_leeme_excel"].name == "correlativa_LEEME.xlsx"
    assert resultado.rutas["correlativa_parquet"].exists()
    manifest = json.loads(resultado.rutas["manifest"].read_text(encoding="utf-8"))
    assert any(o["artefacto"] == "excel/correlativa.xlsx" for o in manifest["omitidos"])


def _resultado_minimo(
    correlativa: pd.DataFrame, golden: pd.DataFrame, filas: int
) -> ResultadoLinkage:
    return ResultadoLinkage(
        correlativa=correlativa,
        golden=golden,
        manifiesto={"entradas": {"A": {"filas": filas}}},
    )


def test_invariante_detecta_perdida_de_filas() -> None:
    """F2.5: la invariante «una fila por registro de entrada» es ``validar()``;
    el flujo la cruza además con las filas que ÉL contó al cargar."""
    from record_linkage.flujo.cruce import _validar_cruce

    correlativa = pd.DataFrame({"ID_GRUPO": [1, 2]})
    res = _resultado_minimo(correlativa, pd.DataFrame({"ID_GRUPO": [1, 2]}), filas=3)
    fallos = res.validar().fallos
    assert any("una fila por registro de entrada" in f for f in fallos)
    with pytest.raises(ContratoSalidaError, match="una fila por registro de entrada"):
        _validar_cruce(res, filas_entrada=3)
    # Manifiesto cuadrado pero el conteo del flujo no: también falla.
    res_cuadrado = _resultado_minimo(correlativa, pd.DataFrame({"ID_GRUPO": [1, 2]}), filas=2)
    with pytest.raises(ContratoSalidaError, match=r"entraron 3"):
        _validar_cruce(res_cuadrado, filas_entrada=3)


def test_invariante_detecta_grupo_nulo() -> None:
    correlativa = pd.DataFrame({"ID_GRUPO": [1, None]})
    res = _resultado_minimo(correlativa, pd.DataFrame({"ID_GRUPO": [1]}), filas=2)
    fallos = res.validar().fallos
    assert any("ID_GRUPO" in f and "nulo" in f for f in fallos)


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
    primera = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path, dir_procesados=cache))
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
    resultado = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path, limite_filas={"PADRON": 2}))
    assert resultado.metricas["filas_entrada"] == 4  # 2 de PADRON + 2 de CLIENTES
    assert len(resultado.correlativa) == 4
    assert _manifiesto_cruce(resultado)["parametros"]["limite_filas"] == {"PADRON": 2}


def test_limite_filas_rechaza_fuente_inexistente(fuentes_csv, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inexistentes"):
        ConfigCruce(fuentes=fuentes_csv, workspace=tmp_path, limite_filas={"NO_EXISTE": 10})


def test_limite_filas_rechaza_valores_invalidos(fuentes_csv, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=">= 1"):
        ConfigCruce(fuentes=fuentes_csv, workspace=tmp_path, limite_filas={"PADRON": 0})


def test_limite_mayor_que_la_fuente_es_no_op(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path, limite_filas={"PADRON": 999}))
    assert resultado.metricas["filas_entrada"] == 5


def test_duckdb_limita_en_ingesta_y_expande_todas_las_filas(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(
        config_cruce(
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
    # F2.5: motor DuckDB en modo dataframe → la misma carpeta del estándar
    # que el camino pandas (la expansión queda en _trabajo/, borrable).
    assert isinstance(resultado, ResultadoCruce)
    assert resultado.validar().ok
    assert _archivos(resultado.carpeta) == ARTEFACTOS_ESTANDAR
    assert "generation_dir" not in resultado.rutas
    assert (tmp_path / "trabajo" / "corrida" / "expansion" / "correlativa.parquet").is_file()
    metadatos = _manifiesto_cruce(resultado)
    assert metadatos["parametros"]["motor_ingesta"] == "duckdb"
    assert metadatos["parametros"]["duckdb_settings"]["memory_limit"] == "256MB"
    manifest = json.loads(resultado.rutas["manifest"].read_text(encoding="utf-8"))
    # Los insumos del manifiesto son las filas reales, no los representantes.
    assert manifest["insumos"]["PADRON"]["filas"] == 2
    assert manifest["insumos"]["PADRON"]["representantes"] == 2
    assert manifest["conteos"]["filas"] == 4


def test_modo_disco_conserva_generaciones_con_la_forma_del_estandar(
    fuentes_csv, tmp_path: Path
) -> None:
    """F2.5: en modo disco nada se materializa, pero la generación publicada es
    una carpeta del estándar (los mismos nombres y el mismo manifest.json) y
    ``leer_resultado`` la lee como cualquier otra."""
    config = config_cruce(
        fuentes_csv,
        tmp_path,
        motor_ingesta="duckdb",
        modo_resultado="disco",
        duckdb_settings=DuckDBIngestionSettings(
            memory_limit="256MB", threads=2, temp_directory=tmp_path / "spill"
        ),
    )
    resultado = ejecutar_cruce(config)

    assert isinstance(resultado, ResultadoCruceDisco)
    generacion = resultado.carpeta
    assert generacion == resultado.rutas["generation_dir"]
    assert generacion.parent == tmp_path / "salida" / "resultados.generations"
    assert _archivos(generacion) == ARTEFACTOS_ESTANDAR
    assert resultado.rutas["manifest"] == generacion / "manifest.json"
    assert resultado.rutas["correlativa_parquet"] == resultado.correlativa.path
    assert resultado.rutas["entidades_ids_parquet"].is_file()
    puntero = json.loads(resultado.rutas["manifest_generaciones"].read_text(encoding="utf-8"))
    assert puntero["generation"] == generacion.name
    assert tmp_path / "salida" / puntero["artifacts"]["manifest"] == generacion / "manifest.json"
    assert "metadata" not in puntero["artifacts"]
    assert not (generacion / "metadatos_corrida.json").exists()

    manifest = json.loads(resultado.rutas["manifest"].read_text(encoding="utf-8"))
    assert manifest["contrato"] == contrato.VERSION_CONTRATO
    assert manifest["invariantes"]["ok"] is True
    # Lo que el modo disco calcula distinto del dataframe se declara, no se calla.
    assert manifest["invariantes"]["divergencia_declarada"] == DIVERGENCIA_METODO_UNION
    assert manifest["conteos"]["filas"] == 5 and manifest["conteos"]["grupos"] == 4
    assert manifest["insumos"]["PADRON"]["filas"] == 3
    assert manifest["corrida"]["cruce"]["parametros"]["modo_resultado"] == "disco"
    assert any(o["artefacto"] == "excel/correlativa.xlsx" for o in manifest["omitidos"])

    leido = resultado.leer()
    assert isinstance(leido, ResultadoLinkage)
    assert leido.validar().ok, leido.validar().fallos
    assert len(leido.correlativa) == 5
    assert leido.correlativa["ID_REGISTRO"].is_unique
    assert leido.dir_trabajo == resultado.rutas["dir_trabajo"]
    dic = leido.diccionario
    assert set(leido.correlativa.columns) <= set(dic.loc[dic["tabla"] == "correlativa", "columna"])
    assert (leido.golden["INPUT_ROW_COUNT"] >= 1).all()


def _particion(correlativa: pd.DataFrame) -> set[frozenset[str]]:
    return {frozenset(g["ID_REGISTRO"]) for _, g in correlativa.groupby("ID_GRUPO")}


def _entidad_por_grupo(correlativa: pd.DataFrame) -> dict[frozenset[str], str]:
    """``{registros del grupo: ID_ENTIDAD}``: compara la identidad sin depender del ID_GRUPO."""
    return {
        frozenset(g["ID_REGISTRO"]): str(g["ID_ENTIDAD"].iloc[0])
        for _, g in correlativa.groupby("ID_GRUPO")
    }


def test_los_dos_motores_entregan_la_misma_correlativa(fuentes_csv, tmp_path: Path) -> None:
    """ID_REGISTRO, la partición en grupos y METODO_UNION son los mismos por
    pandas y por DuckDB (en DuckDB el ``<SRC>-F<fila>`` se reconstruye en SQL
    tras expandir, y un representante ``sin_pareja`` cuyas copias exactas lo
    acompañan pasa a ``identificador``/``nombre`` como en pandas)."""
    # Copias exactas: con NIT (grupo de 3 → identificador) y sin NIT (2 → nombre).
    with Path(fuentes_csv[0].path).open("a", encoding="utf-8") as f:
        f.write("900111222,ACME COLOMBIA SAS\n,SIN IDENTIFICADOR SAS\n,SIN IDENTIFICADOR SAS\n")
    pandas_ = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path / "p"))
    disco = ejecutar_cruce(
        config_cruce(fuentes_csv, tmp_path / "d", motor_ingesta="duckdb", modo_resultado="disco")
    ).leer()
    a = pandas_.correlativa.sort_values("ID_REGISTRO").reset_index(drop=True)
    b = disco.correlativa.sort_values("ID_REGISTRO").reset_index(drop=True)
    assert list(a["ID_REGISTRO"]) == list(b["ID_REGISTRO"])
    assert list(a["ORIGINAL_INDEX"]) == list(b["ORIGINAL_INDEX"])
    for col in ("SRC", "NIT_FINAL", "RAZON_SOCIAL_FINAL", "METODO_UNION", "CONFIANZA"):
        assert list(a[col].astype(str)) == list(b[col].astype(str)), col
    assert len(b) == 8
    acme = b[b["RAZON_SOCIAL"].str.startswith("ACME")]
    assert len(acme) == 3 and set(acme["METODO_UNION"]) == {"identificador"}
    sin_nit = b[b["RAZON_SOCIAL"].str.startswith("SIN IDENTIFICADOR")]
    assert len(sin_nit) == 2 and set(sin_nit["METODO_UNION"]) == {"nombre"}
    assert sin_nit["ID_GRUPO"].nunique() == 1
    # Misma partición (los ID_GRUPO pueden numerarse distinto; F2.3 los fija)
    # y el MISMO ID_ENTIDAD por grupo, también los ENT-: el hash es el de los
    # ID_REGISTRO publicados, no el de los representantes que vio el motor.
    assert _particion(a) == _particion(b)
    assert _entidad_por_grupo(a) == _entidad_por_grupo(b)


# ── Flexibilidad: ajustes de perfil, tiempos y deduplicación (v0.14.0) ──


def test_ajustes_de_perfil_llegan_al_motor(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(
        config_cruce(
            fuentes_csv,
            tmp_path,
            ajustes_perfil={"lsh_permutations": 64, "score_threshold": 0.5},
        )
    )
    metadatos = _manifiesto_cruce(resultado)
    assert metadatos["parametros"]["ajustes_perfil"]["lsh_permutations"] == 64


def test_ajuste_desconocido_falla_con_sugerencia(fuentes_csv, tmp_path: Path) -> None:
    """Un parámetro mal escrito no puede ignorarse en silencio."""
    with pytest.raises(ValueError, match="lsh_permutations"):
        ejecutar_cruce(config_cruce(fuentes_csv, tmp_path, ajustes_perfil={"lsh_permutation": 64}))


def test_tiempos_por_fase_se_registran(fuentes_csv, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path))
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


def test_columnas_de_arrastre_no_entran_al_motor_pero_vuelven(
    fuentes_con_arrastre, tmp_path: Path
) -> None:
    """TELEFONO/EMAIL/DEPARTAMENTO no viajan por el pipeline; sí a la salida."""
    resultado = ejecutar_cruce(config_cruce(fuentes_con_arrastre, tmp_path))
    corr = resultado.correlativa
    # Re-adjuntadas y con el valor EXACTO de la fila original.
    fila_acme = corr[(corr["SRC"] == "PADRON") & (corr["NIT"] == "900111222")].iloc[0]
    assert fila_acme["TELEFONO"] == "3001112233"
    assert fila_acme["EMAIL"] == "acme@x.co"
    fila_delta = corr[(corr["SRC"] == "CLIENTES") & (corr["RAZON_SOCIAL"] == "DELTA EU")].iloc[0]
    assert fila_delta["DEPARTAMENTO"] == "BOGOTA"
    # Cruzada: TELEFONO de la fila de CLIENTES es NA (no existe en esa fuente).
    assert pd.isna(fila_delta["TELEFONO"])
    metadatos = _manifiesto_cruce(resultado)
    # F1.8: el manifiesto declara lo que REALMENTE se adjuntó, no la unión planificada.
    arrastre = metadatos["parametros"]["columnas_arrastre"]
    assert set(arrastre["adjuntadas"]) == {"TELEFONO", "EMAIL", "DEPARTAMENTO"}
    assert arrastre["omitidas"] == []


def test_variable_extra_activa_se_queda_en_el_motor(fuentes_con_arrastre, tmp_path: Path) -> None:
    """Una columna declarada en variables_extra NO se separa: el motor la necesita."""
    from record_linkage.flujo.cruce import _columnas_de_motor, _separar_columnas_extra

    cfg = config_cruce(
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
    _rutas, union, _longitudes = _separar_columnas_extra(marcos, cfg, LogNulo())
    assert "TELEFONO" in marcos["PADRON"].columns, "el motor la usa: no puede separarse"
    assert union == ["EMAIL"]


def test_separacion_desactivable(fuentes_con_arrastre, tmp_path: Path) -> None:
    resultado = ejecutar_cruce(
        config_cruce(fuentes_con_arrastre, tmp_path, separar_columnas_extra=False)
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


def test_golden_de_y_conflictos_identificador_de_exigen_el_contrato() -> None:
    """Los dos guardas que ``_ejecutar_cruce_medido`` aplica a lo que devuelve
    ``linkage()``: sin golden no hay entregable, y el QA de identificador sale
    del manifiesto de ``completar``, no se recalcula. Un fallo es accionable."""
    from record_linkage.flujo.cruce import _conflictos_identificador_de, _golden_de

    df = pd.DataFrame({"ID_GRUPO": [0]})
    with pytest.raises(ContratoSalidaError, match="sin golden"):
        _golden_de(ResultadoLinkage(correlativa=df, golden=None))
    assert _golden_de(ResultadoLinkage(correlativa=df, golden=df)) is df

    with pytest.raises(ContratoSalidaError, match=r"sin completar\.identificador"):
        _conflictos_identificador_de(ResultadoLinkage(correlativa=df, golden=df))
    con_qa = ResultadoLinkage(
        correlativa=df,
        golden=df,
        manifiesto={"completar": {"identificador": {"grupos_con_bases_distintas": 2}}},
    )
    assert _conflictos_identificador_de(con_qa) == 2


# ── F2.18: el crosswalk de F2.4 en los dos motores ─────────────────────


def _con_filas_sin_nit(fuentes_csv) -> None:
    """Dos copias exactas sin identificador en PADRON: un grupo ``ENT-…`` de 2."""
    with Path(fuentes_csv[0].path).open("a", encoding="utf-8") as f:
        f.write(",SIN IDENTIFICADOR SAS\n,SIN IDENTIFICADOR SAS\n")


def test_corrida_anterior_exige_el_motor_pandas_y_lo_dice(fuentes_csv, tmp_path: Path) -> None:
    """Con DuckDB la expansión recalcula los ``ENT-`` por contenido de las filas
    publicadas (los representantes del motor no son las filas), así que una
    herencia desde ``corrida_anterior`` no sobreviviría: en vez de perderse en
    silencio, ``ConfigCruce`` la rechaza al recibirla (modo disco y modo
    dataframe) y ``motor_ingesta='auto'`` la rechaza al resolverse a DuckDB,
    antes de cargar nada."""
    primera = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path / "p1", nombre="primera"))
    for modo in ("disco", "dataframe"):
        with pytest.raises(ValueError, match=r"(?s)corrida_anterior.*motor_ingesta='pandas'"):
            config_cruce(
                fuentes_csv,
                tmp_path / modo,
                motor_ingesta="duckdb",
                modo_resultado=modo,
                corrida_anterior=primera.carpeta,
            )
    auto = config_cruce(
        fuentes_csv,
        tmp_path / "auto",
        motor_ingesta="auto",
        umbral_filas_disco=1,  # fuerza DuckDB al resolver
        corrida_anterior=primera.carpeta,
    )
    with pytest.raises(ValueError, match=r"(?s)corrida_anterior.*motor_ingesta='pandas'"):
        ejecutar_cruce(auto)
    assert not (tmp_path / "auto" / "trabajo").exists() or not any(
        (tmp_path / "auto" / "trabajo").rglob("*.parquet")
    )


def test_cruce_pandas_publica_el_crosswalk_con_sus_retiros(fuentes_csv, tmp_path: Path) -> None:
    """El crosswalk que ``identidad.estable`` calculó dentro de ``linkage()``
    (con ``REGISTROS`` y los retiros) es el que el cruce entrega y escribe; no
    se rederiva de la correlativa perdiendo las entidades retiradas."""
    from record_linkage.exporters.escritor import escribir_parquet
    from record_linkage.identidad.estable import COLUMNA_REGISTROS, NOMBRE_CROSSWALK

    _con_filas_sin_nit(fuentes_csv)
    primera = ejecutar_cruce(config_cruce(fuentes_csv, tmp_path / "p1", nombre="primera"))
    x1 = pd.read_parquet(primera.carpeta / NOMBRE_CROSSWALK)
    ent = x1[x1["ID_ENTIDAD"].str.startswith("ENT-") & (x1["N_REGISTROS"] == 2)]
    assert len(ent) == 1, x1
    r1, r2 = str(ent[COLUMNA_REGISTROS].iloc[0]).split("|")
    # Corrida anterior «a mano»: ese grupo era DOS entidades. En la segunda
    # corrida se fusionan → una hereda y la otra se retira hacia ella.
    partida = pd.concat(
        [
            x1[~x1.index.isin(ent.index)],
            pd.DataFrame(
                {
                    "ID_ENTIDAD": pd.Series(["ENT-aaaaaaaaaaaaaaaa", "ENT-bbbbbbbbbbbbbbbb"]),
                    "ID_GRUPO": pd.Series([900, 901], dtype="Int64"),
                    "N_REGISTROS": pd.Series([1, 1], dtype="int64"),
                    "RETIRADO_EN": pd.Series([pd.NA, pd.NA], dtype="string"),
                    COLUMNA_REGISTROS: pd.Series([r1, r2], dtype="string"),
                }
            ),
        ],
        ignore_index=True,
    )
    anterior = tmp_path / "anterior"
    anterior.mkdir()
    escribir_parquet(partida, anterior / NOMBRE_CROSSWALK, contrato.ENTIDADES_IDS)

    segunda = ejecutar_cruce(
        config_cruce(fuentes_csv, tmp_path / "p2", nombre="segunda", corrida_anterior=anterior)
    )
    assert segunda.validar().ok, segunda.validar().fallos
    assert segunda.entidades_ids is not None
    retiradas = segunda.entidades_ids[segunda.entidades_ids["RETIRADO_EN"].notna()]
    assert len(retiradas) == 1, segunda.entidades_ids
    assert retiradas["RETIRADO_EN"].iloc[0] in set(segunda.correlativa["ID_ENTIDAD"])
    herencia = segunda.manifiesto["completar"]["id_entidad"]["herencia"]
    assert herencia["heredadas"] == 1 and herencia["retiradas"] == 1
    assert segunda.manifiesto["cruce"]["parametros"]["corrida_anterior"] == str(anterior)
    leido = leer_resultado(segunda.carpeta)
    # leer_resultado devuelve texto con nan (parquet → pandas); el contenido es el mismo.
    pd.testing.assert_frame_equal(leido.entidades_ids, segunda.entidades_ids, check_dtype=False)
    assert leido.validar().ok, leido.validar().fallos


def test_una_generacion_de_disco_sirve_como_corrida_anterior(fuentes_csv, tmp_path: Path) -> None:
    """La generación del modo disco lleva ``REGISTROS`` (la misma regla que en
    pandas), así que una corrida posterior con motor pandas hereda de ella."""
    from record_linkage.identidad.estable import COLUMNA_REGISTROS, NOMBRE_CROSSWALK

    _con_filas_sin_nit(fuentes_csv)
    disco = ejecutar_cruce(
        config_cruce(fuentes_csv, tmp_path / "d", motor_ingesta="duckdb", modo_resultado="disco")
    )
    x = pd.read_parquet(disco.carpeta / NOMBRE_CROSSWALK)
    assert list(x.columns) == list(contrato.COLUMNAS_ENTIDADES_IDS)
    assert x[COLUMNA_REGISTROS].notna().all() and x["RETIRADO_EN"].isna().all()
    assert x.loc[x["ID_ENTIDAD"].str.startswith("ENT-"), "N_REGISTROS"].tolist() == [2]

    despues = ejecutar_cruce(
        config_cruce(fuentes_csv, tmp_path / "p", nombre="despues", corrida_anterior=disco.carpeta)
    )
    herencia = despues.manifiesto["completar"]["id_entidad"]["herencia"]
    assert herencia["corrida_anterior"] == str(disco.carpeta / NOMBRE_CROSSWALK)
    assert herencia["heredadas"] == 1 and herencia["retiradas"] == 0
    assert set(despues.correlativa["ID_ENTIDAD"]) == set(disco.leer().correlativa["ID_ENTIDAD"])
