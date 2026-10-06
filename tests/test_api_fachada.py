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
