"""Fachada canónica (F1, v0.9.0): dedupe · ResultadoLinkage · preflight.

Contratos que congela:
1. PARIDAD: ``dedupe()`` produce una correlativa bit a bit idéntica a llamar
   ``deduplicate_auto`` directo con los mismos insumos (Nivel 3). La fachada
   NO transforma datos; este test lo garantiza contra regresiones futuras.
2. Preflight accionable (F1.4): todo error dice qué pasó / por qué importa /
   qué hacer.
3. Manifiesto (F1.5): trazabilidad total de la corrida.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pandas as pd
import pytest

from record_linkage import ResultadoLinkage, dedupe
from record_linkage.deduplication.auto import deduplicate_auto


def _dataset_mixto() -> pd.DataFrame:
    """~90 filas mixtas (mismo generador del contrato de determinismo)."""
    filas: list[dict[str, str]] = []
    variantes = ["{} S.A.S.", "{} SAS", "{}  S A S", "{} LTDA"]
    for i in range(15):
        nit = f"{900100000 + i}"
        base = f"COMERCIALIZADORA ANDINA {i:02d}"
        for j in range(3):
            filas.append({"NIT": nit, "RAZON_SOCIAL": variantes[j].format(base)})
    for i in range(15):
        base = f"DISTRIBUCIONES DEL ORIENTE {i:02d}"
        for j in range(3):
            filas.append({"NIT": "", "RAZON_SOCIAL": variantes[j].format(base)})
    return pd.DataFrame(filas, dtype=str)


def test_paridad_fachada_vs_ruta_directa(tmp_path: Path) -> None:
    """dedupe() no cambia ninguna decisión de deduplicate_auto() sobre el mismo insumo.

    Desde F1.9 la fachada COMPLETA la correlativa al contrato 1.0 (añade
    ID_REGISTRO/ID_ENTIDAD/METODO_UNION…, retira las técnicas y recodifica
    ID_GRUPO de texto a entero), así que ya no es bit a bit idéntica. La
    paridad que importa es la del motor: misma partición, misma identidad
    adoptada y mismas métricas por fila.
    """
    from record_linkage.contrato import COLUMNAS_TECNICAS

    df = _dataset_mixto()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = dedupe(df.copy(), output_dir=str(tmp_path / "fachada"))
        corr_directa, _ = deduplicate_auto(
            df_input=df.copy(),
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            output_dir=str(tmp_path / "directa"),
        )
    a = res.correlativa.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    b = corr_directa.sort_values("ORIGINAL_INDEX").reset_index(drop=True)
    comunes = [c for c in b.columns if c not in COLUMNAS_TECNICAS and c != "ID_GRUPO"]
    assert set(comunes) <= set(a.columns)
    pd.testing.assert_frame_equal(a[comunes], b[comunes])
    # Misma partición: el ID_GRUPO entero es una biyección de las etiquetas del motor.
    pares = pd.DataFrame({"a": a["ID_GRUPO"], "b": b["ID_GRUPO"]}).drop_duplicates()
    assert pares["a"].is_unique and pares["b"].is_unique
    assert pd.api.types.is_integer_dtype(a["ID_GRUPO"])


def test_resultado_tipado_metricas_y_manifiesto(tmp_path: Path) -> None:
    df = _dataset_mixto()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = dedupe(df, output_dir=str(tmp_path / "out"))

    assert isinstance(res, ResultadoLinkage)
    m = res.metricas
    assert m["n_registros"] == len(df)
    assert 0 < m["n_grupos"] <= len(df)
    assert m["n_registros_con_nit"] + m["n_registros_sin_nit"] == len(df)
    assert m["output_dir"].endswith("out")

    man = res.manifiesto
    assert man["funcion"] == "dedupe"
    assert man["seed"] == 42
    assert len(man["hash_parametros"]) == 16
    assert man["entradas"]["df"]["filas"] == len(df)
    assert len(man["entradas"]["df"]["huella"]) == 16
    assert man["versiones"]["rues-linker"] not in ("no-instalado", "")
    assert "datasketch" in man["versiones"]
    assert "grupos únicos" in res.resumen()


def test_manifiesto_huella_cambia_si_cambia_el_insumo(tmp_path: Path) -> None:
    """La huella del dataset distingue insumos distintos (trazabilidad real)."""
    from record_linkage.api import _huella_dataset

    df = _dataset_mixto()
    df2 = df.copy()
    df2.loc[0, "RAZON_SOCIAL"] = "OTRA EMPRESA SAS"
    assert _huella_dataset(df) != _huella_dataset(df2)
    assert _huella_dataset(df) == _huella_dataset(df.copy())


def test_preflight_no_dataframe() -> None:
    with pytest.raises(TypeError) as exc:
        dedupe([{"NIT": "1", "RAZON_SOCIAL": "X"}])  # type: ignore[arg-type]
    msg = str(exc.value)
    assert "Qué pasó" in msg and "Qué hacer" in msg and "pd.DataFrame" in msg


def test_preflight_vacio() -> None:
    with pytest.raises(ValueError, match="0 filas"):
        dedupe(pd.DataFrame(columns=["NIT", "RAZON_SOCIAL"]))


def test_preflight_columnas_faltantes_es_accionable() -> None:
    df = pd.DataFrame({"nit_empresa": ["1"], "nombre": ["ACME"]})
    with pytest.raises(ValueError) as exc:
        dedupe(df)
    msg = str(exc.value)
    assert "NIT" in msg and "col_nit=" in msg and "rename" in msg
    assert "nit_empresa" in msg  # muestra lo disponible


# ─────────────────────────────────────────────────────────────────────────────
# F2.7: dedupe(carpeta_salida=...) y link(carpeta_salida=...) escriben con el
# escritor único, como linkage() desde F1.10
# ─────────────────────────────────────────────────────────────────────────────


def _unica_carpeta(raiz: Path, sufijo: str) -> Path:
    carpetas = [p for p in raiz.iterdir() if p.is_dir()]
    assert len(carpetas) == 1 and carpetas[0].name.endswith(f"_{sufijo}"), carpetas
    assert not (raiz / f".{sufijo}.pendiente").exists()
    return carpetas[0]


def test_dedupe_con_carpeta_salida_escribe_el_estandar(tmp_path: Path) -> None:
    """``dedupe(carpeta_salida=...)``: la carpeta del estándar con ``_trabajo/`` dentro.

    Sin ``output_dir`` los golden y reportes por régimen van a ``_trabajo/``
    de la carpeta publicada (como ``linkage``), ninguna ruta del resultado ni
    del ``manifest.json`` apunta a la pendiente, y ``leer_resultado`` la lee.
    """
    import json

    from record_linkage import leer_resultado

    df = _dataset_mixto()
    with contextlib.redirect_stdout(io.StringIO()):
        res = dedupe(
            df,
            carpeta_salida=tmp_path / "salidas",
            nombre="dd",
            profile_sin_nit="deduplication_sin_nit_conservador",
        )
    carpeta = _unica_carpeta(tmp_path / "salidas", "dd")
    assert res.manifiesto["carpeta_salida"] == str(carpeta)
    assert res.dir_trabajo == carpeta / "_trabajo"
    assert res.metricas["output_dir"] == str(carpeta / "_trabajo")
    assert res.manifiesto["dir_trabajo"] == str(carpeta / "_trabajo")
    assert res.manifiesto["columnas_tecnicas"]["quedan_en"] == str(carpeta / "_trabajo")
    # Los reportes por régimen de deduplicate_auto quedaron dentro.
    assert (carpeta / "_trabajo" / "con_nit").is_dir()
    assert (carpeta / "_trabajo" / "sin_nit").is_dir()
    for archivo in ("correlativa.parquet", "entidades_ids.parquet", "revision.csv"):
        assert (carpeta / archivo).is_file()
    assert (carpeta / "diccionario.csv").is_file()
    assert (carpeta / "excel" / "correlativa.xlsx").is_file()
    assert not (carpeta / "golden.parquet").exists()  # esta ruta no produce golden

    texto = (carpeta / "manifest.json").read_text(encoding="utf-8")
    assert ".pendiente" not in texto
    man = json.loads(texto)
    assert man["nombre"] == "dd"
    assert man["corrida"]["funcion"] == "dedupe"
    assert man["corrida"]["dir_trabajo"] == "_trabajo"
    assert man["metricas"]["output_dir"] == "_trabajo"
    assert man["parametros"]["llamada"]["profile_sin_nit"] == "deduplication_sin_nit_conservador"
    # Dónde se escribe no es un parámetro del motor: no entra en hash_parametros
    # (igual que en linkage()); el nombre va en la cabecera del manifiesto.
    assert "carpeta_salida" not in man["parametros"]["llamada"]
    omitidos = {o["artefacto"] for o in man["omitidos"]}
    assert "golden.parquet" in omitidos

    leido = leer_resultado(carpeta)
    assert leido.validar().ok
    assert len(leido.correlativa) == len(df)
    assert leido.manifiesto["funcion"] == "dedupe"


def test_dedupe_con_carpeta_salida_y_output_dir_fuera(tmp_path: Path) -> None:
    """Con ``output_dir`` explícito el trabajo se queda donde se pidió: la
    carpeta publicada no lleva ``_trabajo/`` y el resultado apunta afuera."""
    df = _dataset_mixto()
    with contextlib.redirect_stdout(io.StringIO()):
        res = dedupe(
            df,
            output_dir=str(tmp_path / "trabajo"),
            carpeta_salida=tmp_path / "salidas",
            nombre="dd",
        )
    carpeta = _unica_carpeta(tmp_path / "salidas", "dd")
    assert not (carpeta / "_trabajo").exists()
    assert res.dir_trabajo == tmp_path / "trabajo"
    assert (tmp_path / "trabajo" / "con_nit").is_dir()
    assert (carpeta / "correlativa.parquet").is_file()


def test_dedupe_nombre_invalido_falla_antes_de_correr(tmp_path: Path) -> None:
    """Un ``nombre`` con separadores falla ANTES de deduplicar (nada en disco)."""
    df = _dataset_mixto()
    with pytest.raises(ValueError, match="nombre"):
        dedupe(df, carpeta_salida=tmp_path / "salidas", nombre="a/b")
    assert not (tmp_path / "salidas").exists()


def _dos_tablas() -> tuple[pd.DataFrame, pd.DataFrame]:
    df_a = pd.DataFrame(
        {
            "NIT": ["900111222", "900333444", ""],
            "RAZON_SOCIAL": ["ACME COLOMBIA SAS", "GLOBEX LTDA", "INICIATIVA VERDE SA"],
            "CIUDAD": ["BOGOTA", "MEDELLIN", "CALI"],
        }
    )
    df_b = pd.DataFrame(
        {
            "NIT": ["900111222", "", "900999888"],
            "RAZON_SOCIAL": ["ACME COLOMBIA S.A.S.", "INICIATIVA VERDE S A", "TITAN GROUP SAS"],
            "CIUDAD": ["BOGOTA", "CALI", "BARRANQUILLA"],
        }
    )
    return df_a, df_b


def test_link_con_carpeta_salida_escribe_el_estandar(tmp_path: Path) -> None:
    """``link(carpeta_salida=...)``: escribe DESPUÉS de poner las métricas de
    cruce y el manifiesto de ``link`` (no el de ``linkage``), con ``_trabajo/``
    dentro de la carpeta publicada."""
    import json

    from record_linkage import leer_resultado, link

    df_a, df_b = _dos_tablas()
    with contextlib.redirect_stdout(io.StringIO()):
        res = link(
            df_a,
            df_b,
            nombre_a="RUES",
            nombre_b="ADUANAS",
            trusted={"RUES"},
            skip_reporting=True,
            carpeta_salida=tmp_path / "salidas",
            nombre="cruce",
        )
    carpeta = _unica_carpeta(tmp_path / "salidas", "cruce")
    assert res.manifiesto["carpeta_salida"] == str(carpeta)
    assert res.dir_trabajo == carpeta / "_trabajo"
    assert res.manifiesto["dir_trabajo"] == str(carpeta / "_trabajo")
    assert res.manifiesto["columnas_tecnicas"]["quedan_en"] == str(carpeta / "_trabajo")
    assert (carpeta / "_trabajo" / "L3_scoring" / "scored.db").is_file()
    assert (carpeta / "golden.parquet").is_file()
    assert res.metricas["n_grupos_cruzados"] == 2

    texto = (carpeta / "manifest.json").read_text(encoding="utf-8")
    assert ".pendiente" not in texto
    man = json.loads(texto)
    assert man["corrida"]["funcion"] == "link"
    assert man["corrida"]["dir_trabajo"] == "_trabajo"
    assert man["metricas"]["n_grupos_cruzados"] == 2
    assert man["metricas"]["n_pares_a_b"] == 2
    assert man["parametros"]["llamada"]["nombre_a"] == "RUES"
    assert man["nombre"] == "cruce" and "carpeta_salida" not in man["parametros"]["llamada"]
    assert man["parametros"]["perfil"]  # los efectivos del motor vienen de linkage()
    assert "L3_scoring" in man["tiempos_por_fase"]
    assert set(man["insumos"]) == {"RUES", "ADUANAS"}

    leido = leer_resultado(carpeta)
    assert leido.validar().ok
    assert leido.manifiesto["funcion"] == "link"
    assert leido.dir_trabajo == carpeta / "_trabajo"


def test_link_nombre_invalido_falla_antes_de_correr(tmp_path: Path) -> None:
    from record_linkage import link

    df_a, df_b = _dos_tablas()
    with pytest.raises(ValueError, match="nombre"):
        link(df_a, df_b, carpeta_salida=tmp_path / "salidas", nombre="../x")
    assert not (tmp_path / "salidas").exists()
