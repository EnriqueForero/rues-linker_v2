"""Dimensionado del universo y elección automática de motor (v0.17.3).

Qué se protege aquí, en una frase: que la decisión "RAM o disco" se pueda
tomar en la PRIMERA corrida —la que revienta la sesión— y que, cuando no se
pueda tomar con evidencia, se tome del lado que no se cae.

Regresión que motivó estas pruebas: con ``MOTOR="auto"`` y sin caché previa,
0.17.2 no podía medir el universo y caía a pandas, exactamente en el escenario
donde una base grande agota los ~12,7 GB de Colab Free.
"""

from __future__ import annotations

import gzip
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from record_linkage import ColumnType, SourceSpec
from record_linkage.flujo import ConfigCruce, filas_en_cache, resolver_motor
from record_linkage.flujo.insumos import escribir_cache
from record_linkage.ingestion import estimar_filas, resumir_universo
from record_linkage.ingestion.dimensionado import MARGEN_INCERTIDUMBRE, MUESTRA_BYTES

# ── Utilidades de construcción ────────────────────────────────────────────


def _linea(i: int) -> str:
    """Filas de ancho variable: un archivo de ancho constante haría trivial
    cualquier estimador y no probaría nada."""
    relleno = "X" * (i % 97)
    return f"{900_000_000 + i},EMPRESA {relleno} {i} SAS\n"


def _csv(ruta: Path, filas: int) -> Path:
    with ruta.open("w", encoding="utf-8") as fh:
        fh.write("NIT,RAZON_SOCIAL\n")
        for i in range(filas):
            fh.write(_linea(i))
    return ruta


def _spec(nombre: str, ruta: Path, **extra) -> SourceSpec:
    return SourceSpec(
        name=nombre,
        path=ruta,
        column_mapping={"NIT": "NIT", "RAZON_SOCIAL": "RAZON_SOCIAL"},
        delimiter=",",
        column_types={"NIT": ColumnType.IDENTIFIER},
        **extra,
    )


def _grande(tmp_path: Path, nombre: str = "grande.csv", filas: int = 120_000) -> Path:
    """CSV holgadamente por encima de la muestra, para forzar el muestreo."""
    ruta = _csv(tmp_path / nombre, filas)
    assert ruta.stat().st_size > MUESTRA_BYTES * 2
    return ruta


# ── Dimensionado: caminos exactos ─────────────────────────────────────────


def test_parquet_da_conteo_exacto_sin_leer_datos(tmp_path: Path) -> None:
    ruta = tmp_path / "x.parquet"
    pd.DataFrame({"NIT": range(4_321), "RAZON_SOCIAL": ["E"] * 4_321}).to_parquet(ruta)
    est = estimar_filas(_spec("P", ruta))
    assert (est.filas, est.exacta, est.metodo) == (4_321, True, "parquet_metadata")
    assert est.filas_con_margen == 4_321  # una cifra exacta no se infla


def test_archivo_chico_se_cuenta_entero_y_descuenta_la_cabecera(tmp_path: Path) -> None:
    ruta = _csv(tmp_path / "chico.csv", 500)
    est = estimar_filas(_spec("C", ruta))
    assert est.filas == 500
    assert est.metodo == "texto_conteo_completo"


def test_conteo_completo_es_exacto_si_no_hay_saltos_dentro_de_campos(tmp_path: Path) -> None:
    ruta = _csv(tmp_path / "chico.csv", 500)
    est = estimar_filas(_spec("C", ruta, newlines_in_values=False))
    assert (est.filas, est.exacta) == (500, True)


def test_ultima_linea_sin_salto_final_tambien_cuenta(tmp_path: Path) -> None:
    ruta = tmp_path / "sin_salto.csv"
    ruta.write_text("NIT,RAZON_SOCIAL\n1,A\n2,B", encoding="utf-8")
    assert estimar_filas(_spec("C", ruta)).filas == 2


def test_cabecera_multiple_se_descuenta(tmp_path: Path) -> None:
    ruta = tmp_path / "dos_cabeceras.csv"
    ruta.write_text("BASURA\nNIT,RAZON_SOCIAL\n1,A\n2,B\n", encoding="utf-8")
    assert estimar_filas(_spec("C", ruta, header=1)).filas == 2


# ── Dimensionado: caminos estimados ───────────────────────────────────────


@pytest.mark.parametrize("filas", [60_000, 200_000])
def test_texto_plano_grande_estima_con_error_bajo(tmp_path: Path, filas: int) -> None:
    ruta = _csv(tmp_path / f"g{filas}.csv", filas)
    est = estimar_filas(_spec("T", ruta))
    assert est.metodo == "texto_ancho_estratificado"
    assert est.exacta is False
    assert abs(est.filas - filas) / filas < 0.05


def test_zip_usa_el_tamano_declarado_y_estima_bien(tmp_path: Path) -> None:
    fuente = _grande(tmp_path)
    z = tmp_path / "g.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as arch:
        arch.write(fuente, arcname="g.csv")
    est = estimar_filas(_spec("Z", z))
    assert est.metodo == "zip_ancho_estratificado"
    assert est.bytes_datos == fuente.stat().st_size
    assert abs(est.filas - 120_000) / 120_000 < 0.05


def test_gzip_estima_bien_usando_el_trailer(tmp_path: Path) -> None:
    fuente = _grande(tmp_path)
    gz = tmp_path / "g.csv.gz"
    gz.write_bytes(gzip.compress(fuente.read_bytes()))
    est = estimar_filas(_spec("G", gz))
    assert est.metodo == "gzip_isize"
    assert abs(est.filas - 120_000) / 120_000 < 0.05


def test_estimacion_se_infla_por_el_margen_de_incertidumbre(tmp_path: Path) -> None:
    est = estimar_filas(_spec("T", _grande(tmp_path)))
    assert est.filas_con_margen == int(est.filas * MARGEN_INCERTIDUMBRE)


def test_muestreo_estratificado_supera_al_de_solo_cabecera(tmp_path: Path) -> None:
    """El sesgo que motivó el cambio: filas cortas al principio, largas al final.

    Con la base real de exportaciones medir solo la cabecera daba +21 % de
    error. Esta prueba reproduce el patrón en pequeño y exige que el estimador
    actual no caiga en él.
    """
    ruta = tmp_path / "creciente.csv"
    n = 150_000
    with ruta.open("w", encoding="utf-8") as fh:
        fh.write("NIT,RAZON_SOCIAL\n")
        for i in range(n):
            fh.write(f"{900_000_000 + i},{'X' * (10 + (i * 200) // n)}\n")
    est = estimar_filas(_spec("T", ruta))
    assert abs(est.filas - n) / n < 0.05


# ── Dimensionado: fallos que NO deben tumbar nada ─────────────────────────


def test_archivo_inexistente_no_lanza(tmp_path: Path) -> None:
    est = estimar_filas(_spec("X", tmp_path / "no_existe.csv"))
    assert (est.filas, est.metodo) == (None, "inexistente")
    assert est.filas_con_margen is None


def test_contenido_sin_saltos_de_linea_no_lanza(tmp_path: Path) -> None:
    ruta = tmp_path / "sin_lineas.csv"
    ruta.write_bytes(b"A" * (MUESTRA_BYTES * 3))
    est = estimar_filas(_spec("X", ruta))
    assert est.filas is None
    assert "sin_lineas" in est.metodo


def test_xlsx_se_declara_no_dimensionable(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    ruta = tmp_path / "x.xlsx"
    libro = openpyxl.Workbook()
    libro.active.append(["NIT", "RAZON_SOCIAL"])
    libro.save(ruta)
    est = estimar_filas(_spec("X", ruta))
    assert (est.filas, est.metodo) == (None, "xlsx_no_dimensionable")


def test_zip_con_parquet_dentro_se_declara_no_dimensionable(tmp_path: Path) -> None:
    interno = tmp_path / "x.parquet"
    pd.DataFrame({"NIT": range(10), "RAZON_SOCIAL": ["E"] * 10}).to_parquet(interno)
    z = tmp_path / "p.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as arch:
        arch.write(interno, arcname="x.parquet")
    est = estimar_filas(_spec("Z", z))
    assert (est.filas, est.metodo) == (None, "zip_no_textual")


# ── Agregación del universo ───────────────────────────────────────────────


def test_universo_suma_y_reporta_exactitud(tmp_path: Path) -> None:
    pequeno = _csv(tmp_path / "a.csv", 300)
    grande = _grande(tmp_path, "b.csv", 80_000)
    resumen = resumir_universo([_spec("A", pequeno), _spec("B", grande)])
    assert resumen.completo is True
    assert resumen.exacto is False  # B es estimada
    assert abs(resumen.filas - 80_300) / 80_300 < 0.05
    assert resumen.filas_con_margen > resumen.filas


def test_universo_incompleto_no_da_total(tmp_path: Path) -> None:
    resumen = resumir_universo(
        [_spec("A", _csv(tmp_path / "a.csv", 10)), _spec("B", tmp_path / "no.csv")]
    )
    assert resumen.completo is False
    assert resumen.filas is None and resumen.filas_con_margen is None


def test_conteo_conocido_tiene_prioridad_sobre_la_estimacion(tmp_path: Path) -> None:
    resumen = resumir_universo([_spec("A", _grande(tmp_path))], exactas={"A": 999_999})
    (est,) = resumen.por_fuente
    assert (est.filas, est.exacta, est.metodo) == (999_999, True, "cache")
    assert resumen.exacto is True
    assert resumen.filas_con_margen == 999_999


def test_filas_en_cache_lee_solo_el_pie_del_parquet(tmp_path: Path) -> None:
    spec = _spec("A", _csv(tmp_path / "a.csv", 20))
    cache = tmp_path / "cache"
    assert filas_en_cache(spec, cache) is None
    escribir_cache(spec, cache, pd.DataFrame({"NIT": ["1"] * 77}), {"filas": 77})
    assert filas_en_cache(spec, cache) == 77
    assert filas_en_cache(spec, None) is None


def test_filas_en_cache_con_parquet_corrupto_devuelve_none(tmp_path: Path) -> None:
    spec = _spec("A", _csv(tmp_path / "a.csv", 20))
    cache = tmp_path / "cache"
    escribir_cache(spec, cache, pd.DataFrame({"NIT": ["1"] * 5}), {"filas": 5})
    from record_linkage.flujo.insumos import ruta_en_cache

    ruta_en_cache(spec, cache).write_bytes(b"no soy parquet")
    assert filas_en_cache(spec, cache) is None


# ── Resolución del motor ──────────────────────────────────────────────────


def _config(tmp_path: Path, fuentes: list[SourceSpec], **extra) -> ConfigCruce:
    base = {
        "fuentes": fuentes,
        "workspace": tmp_path / "salida",
        "dir_trabajo": tmp_path / "trabajo",
        "exportar_excel": False,
        "motor_ingesta": "auto",
        "modo_resultado": "auto",
    }
    base.update(extra)
    return ConfigCruce(**base)


def test_universo_chico_resuelve_a_pandas(tmp_path: Path) -> None:
    cfg = _config(tmp_path, [_spec("A", _csv(tmp_path / "a.csv", 300))])
    resolver_motor(cfg)
    assert (cfg.motor_ingesta, cfg.modo_resultado) == ("pandas", "dataframe")


def test_universo_grande_resuelve_a_duckdb_y_disco(tmp_path: Path) -> None:
    cfg = _config(tmp_path, [_spec("A", _grande(tmp_path))], umbral_filas_disco=10_000)
    resolver_motor(cfg)
    assert (cfg.motor_ingesta, cfg.modo_resultado) == ("duckdb", "disco")
    assert cfg.duckdb_settings is not None


def test_universo_indimensionable_resuelve_a_duckdb(tmp_path: Path) -> None:
    """La regresión concreta: sin poder medir, ANTES caía a pandas."""
    sin_lineas = tmp_path / "opaco.csv"
    sin_lineas.write_bytes(b"A" * (MUESTRA_BYTES * 3))
    cfg = _config(tmp_path, [_spec("A", sin_lineas)])
    resolver_motor(cfg)
    assert cfg.motor_ingesta == "duckdb"


def test_el_margen_puede_inclinar_la_decision_hacia_disco(tmp_path: Path) -> None:
    """Una estimación por debajo del umbral, pero no con su margen, va a disco."""
    ruta = _grande(tmp_path, filas=120_000)
    estimacion = estimar_filas(_spec("A", ruta))
    umbral = estimacion.filas + 1  # la cifra cruda NO supera el umbral
    assert estimacion.filas_con_margen > umbral
    cfg = _config(tmp_path, [_spec("A", ruta)], umbral_filas_disco=umbral)
    resolver_motor(cfg)
    assert cfg.motor_ingesta == "duckdb"


def test_modo_disco_explicito_fuerza_duckdb_aunque_sea_chico(tmp_path: Path) -> None:
    cfg = _config(tmp_path, [_spec("A", _csv(tmp_path / "a.csv", 50))], modo_resultado="disco")
    resolver_motor(cfg)
    assert (cfg.motor_ingesta, cfg.modo_resultado) == ("duckdb", "disco")


def test_excel_pedido_mantiene_dataframe_con_duckdb(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        [_spec("A", _grande(tmp_path))],
        umbral_filas_disco=10_000,
        exportar_excel=True,
    )
    resolver_motor(cfg)
    assert (cfg.motor_ingesta, cfg.modo_resultado) == ("duckdb", "dataframe")


def test_sin_colapso_se_respeta_pandas_pese_al_tamano(tmp_path: Path) -> None:
    """No se cambia la semántica de salida a espaldas del usuario."""
    cfg = _config(
        tmp_path,
        [_spec("A", _grande(tmp_path))],
        umbral_filas_disco=10_000,
        colapsar_duplicados_exactos=False,
    )
    resolver_motor(cfg)
    assert (cfg.motor_ingesta, cfg.modo_resultado) == ("pandas", "dataframe")


def test_opciones_solo_de_pandas_se_desactivan_al_ir_a_duckdb(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        [_spec("A", _grande(tmp_path))],
        umbral_filas_disco=10_000,
        dir_procesados=tmp_path / "cache",
        forzar_relectura=True,
    )
    resolver_motor(cfg)
    assert cfg.motor_ingesta == "duckdb"
    assert cfg.dir_procesados is None and cfg.forzar_relectura is False


def test_motor_explicito_no_se_toca(tmp_path: Path) -> None:
    fuentes = [_spec("A", _grande(tmp_path))]
    cfg = ConfigCruce(
        fuentes=fuentes,
        workspace=tmp_path / "s",
        motor_ingesta="pandas",
        dir_procesados=tmp_path / "cache",
    )
    resolver_motor(cfg)
    assert (cfg.motor_ingesta, cfg.modo_resultado) == ("pandas", "dataframe")
    assert cfg.dir_procesados is not None


def test_resolver_es_idempotente(tmp_path: Path) -> None:
    cfg = _config(tmp_path, [_spec("A", _grande(tmp_path))], umbral_filas_disco=10_000)
    resolver_motor(cfg)
    antes = (cfg.motor_ingesta, cfg.modo_resultado)
    resolver_motor(cfg)
    assert (cfg.motor_ingesta, cfg.modo_resultado) == antes


def test_resolver_devuelve_el_mismo_config(tmp_path: Path) -> None:
    cfg = _config(tmp_path, [_spec("A", _csv(tmp_path / "a.csv", 10))])
    assert resolver_motor(cfg) is cfg


# ── Validación de la configuración ────────────────────────────────────────


def test_motor_desconocido_falla_nombrando_las_tres_opciones(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'pandas', 'duckdb' o 'auto'"):
        _config(tmp_path, [_spec("A", _csv(tmp_path / "a.csv", 5))], motor_ingesta="dask")


def test_modo_disco_con_pandas_explicito_sigue_prohibido(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requiere motor_ingesta='duckdb'"):
        _config(
            tmp_path,
            [_spec("A", _csv(tmp_path / "a.csv", 5))],
            motor_ingesta="pandas",
            modo_resultado="disco",
        )


def test_modo_auto_con_pandas_explicito_es_dataframe(tmp_path: Path) -> None:
    cfg = _config(tmp_path, [_spec("A", _csv(tmp_path / "a.csv", 5))], motor_ingesta="pandas")
    assert cfg.modo_resultado == "dataframe"


def test_modo_auto_con_duckdb_explicito_es_disco(tmp_path: Path) -> None:
    cfg = _config(tmp_path, [_spec("A", _csv(tmp_path / "a.csv", 5))], motor_ingesta="duckdb")
    assert cfg.modo_resultado == "disco"


@pytest.mark.parametrize("valor", [0, -1, True, 1.5, "muchas"])
def test_umbral_invalido_falla_al_construir(tmp_path: Path, valor) -> None:
    with pytest.raises((ValueError, TypeError)):
        _config(
            tmp_path,
            [_spec("A", _csv(tmp_path / "a.csv", 5))],
            umbral_filas_disco=valor,
        )


# ── Casos de borde del trailer de gzip ────────────────────────────────────


def test_gzip_multimiembro_no_se_cree_el_trailer(tmp_path: Path) -> None:
    """Concatenar gzips deja un trailer que describe el ÚLTIMO miembro.

    Tomarlo al pie de la letra daría un tamaño ridículamente pequeño; el
    control de banda contra el tamaño comprimido lo detecta y manda a la razón
    medida.
    """
    from record_linkage.ingestion.dimensionado import _tamano_descomprimido_gzip

    grande = _grande(tmp_path).read_bytes()
    gz = tmp_path / "multi.csv.gz"
    gz.write_bytes(gzip.compress(grande) + gzip.compress(b"cola\n" * 10))
    total, exacto, metodo = _tamano_descomprimido_gzip(gz)
    assert (metodo, exacto) == ("gzip_razon", False)
    assert abs(total - (len(grande) + 50)) / len(grande) < 0.25


def test_razon_de_compresion_no_se_sesga_por_lectura_anticipada(tmp_path: Path) -> None:
    """La razón medida debe parecerse a la real; con GzipFile se iba 6x.

    Ese sesgo no rompía el tamaño (el trailer manda) pero sí habría elegido mal
    la vuelta del contador en archivos de más de 4 GiB.
    """
    from record_linkage.ingestion.dimensionado import MUESTRA_BYTES, _razon_compresion_gzip

    fuente = _grande(tmp_path)
    gz = tmp_path / "g.csv.gz"
    gz.write_bytes(gzip.compress(fuente.read_bytes()))
    real = fuente.stat().st_size / gz.stat().st_size
    medida = _razon_compresion_gzip(gz, MUESTRA_BYTES)
    assert 0.7 * real < medida < 1.3 * real


def test_vuelta_del_contador_se_elige_con_la_razon() -> None:
    """Aritmética del trailer: ISIZE es el tamaño módulo 2^32.

    Se prueba con números en vez de con un archivo de 8 GiB. La pista solo
    tiene que acertar la vuelta, no el tamaño: por eso se le da un valor
    deliberadamente impreciso (±300 MiB).
    """
    from record_linkage.ingestion.dimensionado import _desenrollar_isize

    real = 2 * 2**32 + 123_456_789
    comprimido = real // 10
    total, del_trailer = _desenrollar_isize(
        isize=real % 2**32, estimado=real - 300 * 1024**2, comprimido=comprimido
    )
    assert (total, del_trailer) == (real, True)


def test_trailer_fuera_de_banda_cede_ante_el_estimado() -> None:
    """Un trailer que implicaría 3000:1 de compresión no es creíble."""
    from record_linkage.ingestion.dimensionado import _desenrollar_isize

    total, del_trailer = _desenrollar_isize(isize=50, estimado=9_000_000, comprimido=3_000)
    assert (total, del_trailer) == (9_000_000, False)


def test_archivo_pequeno_sin_vueltas_usa_el_trailer_tal_cual() -> None:
    from record_linkage.ingestion.dimensionado import _desenrollar_isize

    total, del_trailer = _desenrollar_isize(isize=5_000_000, estimado=4_900_000, comprimido=400_000)
    assert (total, del_trailer) == (5_000_000, True)


# ── Extremo a extremo: la corrida completa honra "auto" ───────────────────


def _fuentes_cruzables(tmp_path: Path) -> list[SourceSpec]:
    a = tmp_path / "padron.csv"
    a.write_text(
        "IDENT,NOMBRE\n900111222,ACME COLOMBIA SAS\n800333444,BETA LTDA\n",
        encoding="utf-8",
    )
    b = tmp_path / "clientes.csv"
    b.write_text(
        "nit,razon\n9001112221,ACME COLOMBIA S.A.S.\n700999888,DELTA EU\n",
        encoding="utf-8",
    )
    return [
        SourceSpec(
            name="PADRON",
            path=a,
            column_mapping={"NIT": "IDENT", "RAZON_SOCIAL": "NOMBRE"},
            delimiter=",",
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
        SourceSpec(
            name="CLIENTES",
            path=b,
            column_mapping={"NIT": "nit", "RAZON_SOCIAL": "razon"},
            delimiter=",",
            column_types={"NIT": ColumnType.IDENTIFIER},
        ),
    ]


def test_corrida_completa_con_auto_elige_pandas_y_produce_resultado(tmp_path: Path) -> None:
    from record_linkage.flujo import ejecutar_cruce

    cfg = _config(
        tmp_path,
        _fuentes_cruzables(tmp_path),
        confiables={"PADRON"},
        filas_smoke=0,
        perfil="produccion_estandar",
    )
    resultado = ejecutar_cruce(cfg)
    assert cfg.motor_ingesta == "pandas"
    assert len(resultado.correlativa) == 4


def test_corrida_completa_con_auto_baja_a_disco_si_supera_el_umbral(tmp_path: Path) -> None:
    """Mismo insumo, mismo resultado, otro motor: la elección no cambia el qué."""
    from record_linkage.flujo import ejecutar_cruce

    cfg = _config(
        tmp_path,
        _fuentes_cruzables(tmp_path),
        confiables={"PADRON"},
        filas_smoke=0,
        perfil="produccion_estandar",
        umbral_filas_disco=1,
    )
    resultado = ejecutar_cruce(cfg)
    assert (cfg.motor_ingesta, cfg.modo_resultado) == ("duckdb", "disco")
    assert resultado.correlativa.rows == 4
